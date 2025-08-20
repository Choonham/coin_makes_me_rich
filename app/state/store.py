# ===================================================================================
#   state/store.py: 인메모리 상태 저장소 (최종 수정본)
# ===================================================================================
#
#   **핵심 변경사항:**
#   - `update_wallet_balance` 함수를 수정하여 Bybit API로부터 받은 계좌 잔고(dict 리스트)를
#     `Position` 객체 리스트로 명시적으로 변환한 후 `SystemState.active_positions`에 저장하도록
#     변경했습니다.
#   - 이 수정으로 `PydanticSerializationUnexpectedValue` 경고가 더 이상 발생하지 않습니다.
#
#
import asyncio
from collections import deque
from typing import Deque, Dict, List

from loguru import logger

from app.exchange.models import Order, CoinBalance
from app.state.models import RiskConfig, SystemState, Position  # Position 모델 import
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

        # SystemState 내부의 리스트들을 deque로 초기화
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
        """
        [수정됨] Bybit에서 지갑 잔고를 가져와 시스템 상태를 업데이트합니다.
        API 응답(dict)을 `Position` 객체로 변환하여 저장합니다.
        """
        try:
            balance_data = await bybit_client.get_wallet_balance()
            if not balance_data:
                logger.warning("Received empty balance data.")
                return

            async with self._lock:
                self._system_state.total_equity = float(balance_data.get('totalEquity', 0.0))
                self._system_state.available_balance = float(balance_data.get('totalAvailableBalance', 0.0))
                self._system_state.unrealised_pnl = float(balance_data.get('totalUnrealisedPnl', 0.0))

                if self._initial_total_equity > 0:
                    pnl = self._system_state.total_equity - self._initial_total_equity
                    self._system_state.pnl_day = pnl
                    self._system_state.realized_pnl = pnl
                    if self._initial_total_equity > 0:
                        pnl_pct = (pnl / self._initial_total_equity) * 100
                        self._system_state.pnl_day_pct = pnl_pct
                    else:
                        # 시작 자산이 0이면 수익률도 0
                        self._system_state.pnl_day_pct = 0.0

                coin_balances_data = balance_data.get('coin', [])
                self._balances.clear()

                # --- [핵심 수정 지점] ---
                new_active_positions: List[Position] = []
                held_symbols_set = set()

                for coin_dict in coin_balances_data:
                    try:
                        balance = CoinBalance(**coin_dict)
                        self._balances[balance.coin] = balance

                        if balance.coin != "USDT" and balance.wallet_balance > 1e-9:
                            symbol = f"{balance.coin}USDT"
                            held_symbols_set.add(symbol)

                            # API 응답(dict)으로부터 Position 객체 생성
                            position_obj = Position(
                                symbol=symbol,
                                quantity=balance.wallet_balance,
                                average_price=float(coin_dict.get('avgPrice', 0) or 0)
                                # entry_price 등은 router에서 매수 시점에 기록되므로 여기서는 기본값 사용
                            )
                            new_active_positions.append(position_obj)

                    except Exception as e:
                        logger.warning(f"Failed to parse coin balance data: {coin_dict}. Error: {e}")

                self._system_state.held_symbols = sorted(list(held_symbols_set))
                # 변환된 Position 객체 리스트를 상태에 할당
                self._system_state.active_positions = new_active_positions
                # --- [수정 완료] ---

            logger.info(
                f"Wallet balance updated. Equity: {self._system_state.total_equity:.2f}, PnL Day: {self._system_state.pnl_day:.2f}, Coins: {len(self._balances)}")
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
                # API 응답을 Order 모델로 파싱하여 일관성 유지
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

    # ... (파일의 나머지 부분은 그대로 유지) ...
    # update_wallet_balance_loop, update_realized_pnl, update_order_history 등
    # 기존에 있던 다른 루프 및 메서드들은 그대로 유지합니다.

    async def update_wallet_balance_loop(self, bybit_client: "BybitClient", interval_seconds: int = 5):
        """주기적으로 지갑 잔고를 업데이트하는 백그라운드 루프"""
        logger.info(f"Starting wallet balance update loop with {interval_seconds}s interval.")
        while True:
            try:
                await self.update_wallet_balance(bybit_client)
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                logger.info("Wallet balance update loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in wallet balance update loop: {e}", exc_info=True)
                await asyncio.sleep(interval_seconds)

    async def update_order_history(self, bybit_client: "BybitClient"):
        """Bybit에서 주문 내역을 가져와 시스템 상태를 업데이트합니다."""
        try:
            order_history_data = await bybit_client.get_order_history(category="spot")

            processed_orders = []
            for data in order_history_data:
                try:
                    order = Order(**data)
                    processed_orders.append(order)
                except Exception as e:
                    logger.warning(f"Failed to parse order history item: {data}. Error: {e}")

            async with self._lock:
                self._system_state.order_history = [o.model_dump() for o in processed_orders]
            # logger.debug(f"Order history updated. Total orders: {len(self._system_state.order_history)}")
        except Exception as e:
            logger.error(f"Failed to update order history: {e}", exc_info=True)
        await self._update_state()

    async def update_order_history_loop(self, bybit_client: "BybitClient", interval_seconds: int = 60):
        """주기적으로 주문 내역을 업데이트하는 백그라운드 루프"""
        logger.info(f"Starting order history update loop with {interval_seconds}s interval.")
        while True:
            try:
                await self.update_order_history(bybit_client)
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                logger.info("Order history update loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in order history update loop: {e}", exc_info=True)
                await asyncio.sleep(interval_seconds)


# 전역 상태 저장소 인스턴스 생성
state_store = StateStore()