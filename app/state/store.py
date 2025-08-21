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

    async def update_wallet_balance(self, bybit_client: "BybitClient"):
        """Bybit에서 지갑 잔고를 가져와 시스템 상태를 업데이트합니다. (수정된 최종본)"""
        try:
            balance_data = await bybit_client.get_wallet_balance()
            if not balance_data or 'coin' not in balance_data.get('list', [{}])[0]:
                logger.warning("Wallet balance data is empty or malformed.")
                return

            account_info = balance_data['list'][0]

            async with self._lock:
                # --- 기본 지갑 정보 업데이트 ---
                self._system_state.total_equity = float(account_info.get('totalEquity', 0.0))
                self._system_state.available_balance = float(account_info.get('totalAvailableBalance', 0.0))
                self._system_state.unrealised_pnl = float(account_info.get('totalUnrealisedPnl', 0.0))

                # --- 일일 손익 계산 ---
                if self._initial_total_equity > 0:
                    pnl = self._system_state.total_equity - self._initial_total_equity
                    self._system_state.pnl_day = pnl
                    self._system_state.pnl_day_pct = (
                                                                 pnl / self._initial_total_equity) * 100 if self._initial_total_equity > 0 else 0.0

                # --- 보유 코인 및 포지션 정보 업데이트 ---
                coin_balances_data = account_info.get('coin', [])
                self._balances.clear()

                new_active_positions: Dict[str, Position] = {}
                held_symbols_set = set()

                for coin_dict in coin_balances_data:
                    try:
                        coin_name = coin_dict.get('coin')
                        if not coin_name or coin_name == "USDT":
                            continue

                        wallet_balance = float(coin_dict.get('walletBalance', 0.0))
                        if wallet_balance <= 1e-9:  # 먼지 수량 무시
                            continue

                        # CoinBalance 모델로 유효성 검사 및 데이터 변환
                        balance = CoinBalance(**coin_dict)
                        self._balances[balance.coin] = balance

                        symbol = f"{balance.coin}USDT"
                        if symbol not in self._universe:
                            continue

                        held_symbols_set.add(symbol)

                        avg_price = float(balance.avg_price or 0)

                        # 기존 포지션 정보를 가져옴
                        existing_pos = self._system_state.active_positions.get(symbol)

                        if existing_pos:
                            # 기존 포지션이 있으면 최신 정보로 업데이트
                            existing_pos.quantity = balance.wallet_balance
                            existing_pos.average_price = avg_price
                            existing_pos.last_update_timestamp = time.time()
                            new_active_positions[symbol] = existing_pos
                        else:
                            # 기존 포지션이 없으면 새로 생성 (모든 필수 필드 포함)
                            logger.info(f"Creating new position for existing asset: {symbol}")
                            new_position = Position(
                                symbol=symbol,
                                quantity=balance.wallet_balance,
                                average_price=avg_price,
                                entry_price=avg_price,  # 최초 생성 시점에는 진입가 = 평균가
                                entry_timestamp=time.time(),
                                highest_price_since_entry=avg_price
                            )
                            new_active_positions[symbol] = new_position
                            logger.warning(
                                f"Initial entry_price for {symbol} set to avgPrice: {avg_price}"
                            )

                    except Exception as e:
                        logger.error(f"Failed to parse coin balance data: {coin_dict}. Error: {e}", exc_info=True)

                self._system_state.held_symbols = sorted(list(held_symbols_set))
                self._system_state.active_positions = new_active_positions

            logger.info(
                f"Wallet balance updated. Equity: {self._system_state.total_equity:.2f}, "
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