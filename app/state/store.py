# ===================================================================================
#   state/store.py: 인메모리 상태 저장소 (최종 통합본)
# ===================================================================================
#
#   **핵심 변경사항:**
#   - router.py에서 호출하는 `get_position` 메서드를 추가하여, 매수 직후 포지션의
#     상세 정보(entry_price 등)를 기록할 수 있도록 수정했습니다.
#   - PydanticSerializationWarning을 해결하기 위해 `update_wallet_balance`에서
#     API 응답(dict)을 `Position` 객체로 변환하는 로직을 포함합니다.
#
#
import asyncio
import time
from collections import deque
from typing import Deque, Dict, List, Optional

from loguru import logger

from app.exchange.models import Order, CoinBalance
from app.state.models import RiskConfig, SystemState, Position
from app.utils.typing import TrendEvent
from app.utils.time import now
from app.assets import universe


class StateStore:
    """애플리케이션의 실시간 상태를 관리하는 인메모리 저장소"""

    def __init__(self, max_events: int = 50):
        self._lock = asyncio.Lock()
        self._max_events = max_events

        self._system_state = SystemState()
        self._initial_total_equity: float = 0.0

        self._orderbooks: Dict[str, Dict] = {}
        self._orders: Dict[str, Dict] = {}
        self._balances: Dict[str, CoinBalance] = {}
        self._universe: List[str] = universe

        self._recent_trades_deque: Deque[dict] = deque(maxlen=max_events)
        self._recent_errors_deque: Deque[str] = deque(maxlen=max_events)
        self._trend_summary_deque: Deque[TrendEvent] = deque(maxlen=max_events)

        self._system_state.recent_trades = list(self._recent_trades_deque)
        self._system_state.recent_errors = list(self._recent_errors_deque)
        self._system_state.trend_summary = list(self._trend_summary_deque)

    async def _update_state(self):
        """내부 상태를 SystemState 객체에 동기화합니다."""
        async with self._lock:
            self._system_state.orders = list(self._orders.values())
            self._system_state.recent_trades = list(self._recent_trades_deque)
            self._system_state.recent_errors = list(self._recent_errors_deque)
            self._system_state.trend_summary = list(self._trend_summary_deque)

            usdt_balance = self._balances.get("USDT")
            self._system_state.available_usdt_balance = usdt_balance.wallet_balance if usdt_balance else 0.0

            self._system_state.timestamp = now()

    # --- Public Getters ---
    def get_system_state(self) -> SystemState:
        return self._system_state.model_copy(deep=True)

    def get_orderbook(self, symbol: str) -> Dict | None:
        return self._orderbooks.get(symbol)

    def get_balance(self, coin: str) -> CoinBalance | None:
        return self._balances.get(coin)

    def get_all_balances(self) -> Dict[str, CoinBalance]:
        return self._balances

    def get_universe(self) -> List[str]:
        return self._universe

    def get_position(self, symbol: str) -> Optional[Position]:
        """
        [신규 추가] 특정 심볼의 포지션 객체를 반환합니다.
        """
        # self._system_state는 Pydantic 모델이므로 직접 순회하여 객체를 찾습니다.
        for pos in self._system_state.active_positions:
            if pos.symbol == symbol:
                return pos
        return None

    async def set_initial_equity(self, equity: float):
        """하루 시작 시점의 총 자산을 설정합니다."""
        async with self._lock:
            self._initial_total_equity = equity
            logger.info(f"Initial equity for the day set to: {equity:.2f} USDT")

    async def reset_daily_state(self, current_equity: float):
        """일일 PnL을 초기화하고, 새로운 시작 자산을 설정합니다."""
        async with self._lock:
            self._initial_total_equity = current_equity
            self._system_state.pnl_day = 0.0
            self._system_state.realized_pnl = 0.0
            logger.critical(f"Daily state has been reset. New initial equity: {current_equity:.2f} USDT")
        await self._update_state()

    # --- Public Setters (atomic operations) ---

    # app/state/store.py

    async def update_wallet_balance(self, bybit_client: "BybitClient"):
        """Bybit에서 지갑 잔고를 가져와 시스템 상태를 업데이트합니다. (API v5 호환 최종 수정본)"""
        try:
            # 1. Bybit 클라이언트를 통해 전체 잔고 데이터를 딕셔너리 형태로 받아옵니다.
            response_data = await bybit_client.get_wallet_balance(accountType="UNIFIED")

            # 2. 응답의 'result' 키에서 실제 데이터를 추출합니다.
            balance_data = response_data.get('result', {})
            if not balance_data:
                logger.warning("Wallet balance data is missing 'result' field.")
                return

            account_info_list = balance_data.get('list', [])
            if not account_info_list:
                logger.warning("Wallet balance data list is empty.")
                return

            account_info = account_info_list[0]
            coin_list = account_info.get('coin', [])
            if not coin_list:
                logger.warning("Wallet balance data is malformed (missing 'coin' list).")
                return

            async with self._lock:
                # --- 기본 지갑 정보 업데이트 ---
                self._system_state.total_equity = float(account_info.get('totalEquity', 0) or 0)
                self._system_state.available_balance = float(account_info.get('totalAvailableBalance', 0) or 0)
                self._system_state.unrealised_pnl = float(account_info.get('totalUnrealisedPnl', 0) or 0)

                # --- 사용 가능한 USDT 잔액을 명시적으로 찾아 저장 (v5 호환) ---
                for coin in coin_list:
                    if coin.get('coin') == 'USDT':
                        # v5 API에서는 'walletBalance' 대신 'equity'를 사용합니다.
                        self._system_state.available_usdt_balance = float(coin.get('equity', 0) or 0)
                        break
                else:
                    self._system_state.available_usdt_balance = 0.0

                # --- 일일 손익 계산 ---
                if self._initial_total_equity > 0:
                    pnl = self._system_state.total_equity - self._initial_total_equity
                    self._system_state.pnl_day = pnl
                    self._system_state.pnl_day_pct = (
                                                                 pnl / self._initial_total_equity) * 100 if self._initial_total_equity > 0 else 0.0

                # --- 현재 보유 코인 목록 업데이트 (v5 호환) ---
                self._balances.clear()
                held_symbols_set = set()

                for coin_dict in coin_list:
                    # 'walletBalance' 키를 'equity'로 변경하여 CoinBalance 모델에 전달
                    # **중요**: CoinBalance 모델의 필드명도 'walletBalance'에서 'equity'로 변경해야 합니다.

                    # 임시로 'walletBalance' 키를 추가하여 기존 모델과 호환되도록 처리
                    coin_dict_compatible = coin_dict.copy()
                    coin_dict_compatible['wallet_balance'] = coin_dict.get('equity', '0')

                    balance = CoinBalance(**coin_dict_compatible)
                    self._balances[balance.coin] = balance

                    if balance.coin != "USDT" and float(balance.wallet_balance or 0) > 1e-9:
                        symbol = f"{balance.coin}USDT"
                        held_symbols_set.add(symbol)

                self._system_state.held_symbols = sorted(list(held_symbols_set))

            logger.info(
                f"Wallet balance updated. Equity: {self._system_state.total_equity:.2f}, "
                f"Available USDT: {self._system_state.available_usdt_balance:.2f}, "
                f"PnL Day: {self._system_state.pnl_day:.2f}, "
                f"Held Coins: {len(self._system_state.held_symbols)}"
            )

        except Exception as e:
            logger.error(f"Failed to update wallet balance: {e}", exc_info=True)

        await self._update_state()

    async def set_status(self, status: str):
        async with self._lock:
            self._system_state.status = status
        await self._update_state()

    async def set_risk_config(self, config: RiskConfig):
        async with self._lock:
            self._system_state.risk_config = config
        await self._update_state()

    async def update_orderbook(self, symbol: str, data: Dict):
        self._orderbooks[symbol] = data

    async def update_order(self, order_data: dict):
        async with self._lock:
            order_id = order_data.get('orderId')
            if order_id:
                try:
                    order = Order(**order_data)
                    self._orders[order_id] = order.model_dump()
                except Exception as e:
                    logger.error(f"Failed to parse order data: {order_data}. Error: {e}")

    async def add_trade(self, trade_data: dict):
        async with self._lock:
            try:
                trade = Order(**trade_data)
                self._recent_trades_deque.appendleft(trade.model_dump())
                logger.info(f"Trade history updated with order {trade.order_id}")
            except Exception as e:
                logger.error(f"Failed to parse and add trade data: {trade_data}. Error: {e}")
        await self._update_state()

    async def add_error(self, error_message: str):
        async with self._lock:
            self._recent_errors_deque.appendleft(error_message)
        await self._update_state()

    async def add_trend_event(self, trend_event: TrendEvent):
        async with self._lock:
            self._trend_summary_deque.appendleft(trend_event)
        await self._update_state()

    # ... 이하 루프 메서드들은 그대로 유지 ...
    async def update_wallet_balance_loop(self, bybit_client: "BybitClient", interval_seconds: int = 5):
        logger.info(f"Starting wallet balance update loop with {interval_seconds}s interval.")
        while True:
            try:
                await self.update_wallet_balance(bybit_client)
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in wallet balance update loop: {e}", exc_info=True)
                await asyncio.sleep(interval_seconds)

    async def update_order_history_loop(self, bybit_client: "BybitClient", interval_seconds: int = 60):
        logger.info(f"Starting order history update loop with {interval_seconds}s interval.")
        while True:
            try:
                # 주문 내역 업데이트 로직
                order_history_data = await bybit_client.get_order_history(category="spot")
                processed_orders = [Order(**data).model_dump() for data in order_history_data]
                async with self._lock:
                    self._system_state.order_history = processed_orders
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in order history update loop: {e}", exc_info=True)
            await asyncio.sleep(interval_seconds)


# 전역 상태 저장소 인스턴스 생성
state_store = StateStore()