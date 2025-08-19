
# ===================================================================================
#   state/store.py: 인메모리 상태 저장소
# ===================================================================================
#
#   - 애플리케이션의 실시간 휘발성 상태를 메모리에 저장하고 관리하는 중앙 저장소입니다.
#   - "단일 진실 공급원(Single Source of Truth)" 역할을 하여, 여러 비동기 태스크가
#     일관된 상태 데이터를 공유하고 업데이트할 수 있도록 합니다.
#   - 데이터베이스 I/O 없이 빠르게 상태를 읽고 쓸 수 있어, 실시간 모니터링 및
#     빠른 의사결정이 필요한 로직에 필수적입니다.
#
#   **주요 기능:**
#   - **싱글톤 인스턴스**: `state_store`라는 전역 인스턴스를 통해 애플리케이션 어디서든
#     동일한 상태 객체에 접근할 수 있습니다.
#   - **원자적 업데이트**: `asyncio.Lock`을 사용하여 여러 비동기 태스크가 동시에 상태를
#     수정하려 할 때 발생할 수 있는 경쟁 상태(race condition)를 방지하고 데이터의
#     무결성을 보장합니다.
#   - **실시간 데이터 관리**:
#     - `SystemState` 모델을 사용하여 시스템의 모든 실시간 상태(운영 상태, PnL, 포지션 등)를 관리합니다.
#     - 최신 오더북, 포지션, 주문 정보를 심볼별로 딕셔너리에 저장합니다.
#     - `collections.deque`를 사용하여 최근 거래, 에러, 트렌드 이벤트 목록의 크기를
#       일정하게 유지합니다.
#   - **편리한 인터페이스**: `update_position`, `add_trade`, `get_orderbook` 등 직관적인
#     메서드를 제공하여 상태를 쉽게 읽고 수정할 수 있습니다.
#
#   **데이터 흐름:**
#   - **BybitClient (WS)**: 웹소켓으로 수신한 포지션, 주문, 오더북 데이터를 `update_*` 메서드를 호출하여 스토어에 반영합니다.
#   - **StrategyRouter**: 거래 실행 후 `add_trade`를 호출하고, 에러 발생 시 `add_error`를 호출합니다.
#   - **RiskEngine**: `get_system_state`나 `get_position`을 호출하여 리스크 검증에 필요한 현재 상태를 조회합니다.
#   - **API/WebSocket 매니저**: `get_system_state`를 호출하여 RN 대시보드에 보낼 최신 데이터를 가져옵니다.
#
#
import asyncio
from collections import deque
from typing import Deque, Dict, List

from loguru import logger # Added import

from app.exchange.models import Order, Side, CoinBalance
from app.state.models import RiskConfig
from app.state.models import SystemState
from app.utils.typing import TrendEvent
from app.utils.time import now
from app.assets import universe

class StateStore:
    """애플리케이션의 실시간 상태를 관리하는 인메모리 저장소"""

    def __init__(self, max_events: int = 50):
        self._lock = asyncio.Lock()
        self._max_events = max_events
        
        # 핵심 상태 객체
        self._system_state = SystemState()
        self._initial_total_equity: float = 0.0 # 하루 시작 시점의 총 자산
        
        # 심볼별 데이터 저장소
        self._orderbooks: Dict[str, Dict] = {}
        self._orders: Dict[str, Dict] = {}
        self._balances: Dict[str, CoinBalance] = {}
        self._universe: List[str] = universe

        # 상태 초기화
        self._system_state.recent_trades = []
        self._system_state.recent_errors = []
        self._system_state.trend_summary = []
        self._recent_trades_deque: Deque[dict] = deque(maxlen=max_events)
        self._recent_errors_deque: Deque[str] = deque(maxlen=max_events)
        self._trend_summary_deque: Deque[TrendEvent] = deque(maxlen=max_events)

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
        return self._system_state

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
            # 거래/에러 기록은 초기화하지 않음
            logger.critical(f"Daily state has been reset. New initial equity: {current_equity:.2f} USDT")
        await self._update_state()

    # --- Public Setters (atomic operations) ---

    async def update_wallet_balance(self, bybit_client: "BybitClient"):
        """Bybit에서 지갑 잔고를 가져와 시스템 상태를 업데이트합니다."""
        try:
            balance_data = await bybit_client.get_wallet_balance()
            if not balance_data:
                logger.warning("Received empty balance data.")
                return

            async with self._lock:
                self._system_state.total_equity = float(balance_data.get('totalEquity', 0.0))
                self._system_state.available_balance = float(balance_data.get('totalAvailableBalance', 0.0))
                self._system_state.unrealised_pnl = 0.0
                
                # PnL 계산
                if self._initial_total_equity > 0:
                    pnl = self._system_state.total_equity - self._initial_total_equity
                    self._system_state.pnl_day = pnl
                    self._system_state.realized_pnl = pnl # 현물에서는 realized_pnl을 pnl_day와 동일하게 취급
                    
                    # 수익률(%) 계산
                    pnl_pct = (pnl / self._initial_total_equity) * 100
                    self._system_state.pnl_day_pct = pnl_pct

                coin_balances = balance_data.get('coin', [])
                self._balances.clear()
                
                new_active_positions = []
                held_symbols_set = set()
                
                for coin_data in coin_balances:
                    try:
                        balance = CoinBalance(**coin_data)
                        self._balances[balance.coin] = balance
                        
                        # 보유 자산 목록 생성 (0 이상, USDT 제외)
                        if balance.coin != "USDT" and balance.wallet_balance > 0:
                            held_symbols_set.add(balance.coin)
                            position_data = {
                                "symbol": f"{balance.coin}USDT",
                                "side": "BUY", # 보유 자산은 매수 포지션으로 간주
                                "size": balance.wallet_balance,
                                "unrealised_pnl": 0 # 현물에서는 미실현 손익을 0으로 표시
                            }
                            new_active_positions.append(position_data)
                            
                    except Exception as e:
                        logger.warning(f"Failed to parse coin balance data: {coin_data}. Error: {e}")
                
                self._system_state.held_symbols = sorted(list(held_symbols_set))
                self._system_state.active_positions = new_active_positions

            logger.info(f"Wallet balance updated. Equity: {self._system_state.total_equity:.2f}, PnL Day: {self._system_state.pnl_day:.2f}, Coins: {list(self._balances.keys())}")
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
                order = Order(**order_data)
                order.trade_type = f"{order.side.value}"
                self._orders[order_id] = order.model_dump()

    async def add_trade(self, trade_data: dict):
        async with self._lock:
            # API 응답을 Order 모델로 파싱하여 일관성 유지
            try:
                trade = Order(**trade_data)
                self._recent_trades_deque.appendleft(trade.model_dump())
                logger.info(f"Trade history updated with order {trade.order_id}")
            except Exception as e:
                logger.error(f"Failed to parse and add trade data to history: {trade_data}. Error: {e}")
        await self._update_state()

    async def add_error(self, error_message: str):
        async with self._lock:
            self._recent_errors_deque.appendleft(error_message)
        await self._update_state()

    async def add_trend_event(self, trend_event: TrendEvent):
        async with self._lock:
            self._trend_summary_deque.appendleft(trend_event)
        await self._update_state()

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

    async def update_realized_pnl(self, bybit_client: "BybitClient"):
        """Bybit에서 실현 PnL을 가져와 시스템 상태를 업데이트합니다."""
        try:
            # TODO: 현물 거래 PnL은 거래 내역을 기반으로 직접 계산해야 합니다.
            # 현재 Bybit API v5는 현물에 대한 closedPnl 엔드포인트를 지원하지 않습니다.
            # 이 기능은 향후 구현될 때까지 비활성화합니다.
            if self._system_state.realized_pnl == 0.0: # 불필요한 로그 출력을 막기 위해 한 번만 경고
                logger.warning("Spot PnL calculation is temporarily disabled as the Bybit API does not support it directly.")
            
            # 임시로 PnL을 0으로 유지
            async with self._lock:
                self._system_state.realized_pnl = 0.0
                self._system_state.pnl_day = 0.0
            # logger.info(f"Realized PnL updated: {self._system_state.realized_pnl}")
        except Exception as e:
            logger.error(f"Failed to update realized PnL: {e}", exc_info=True)
        await self._update_state()

    async def update_realized_pnl_loop(self, bybit_client: "BybitClient", interval_seconds: int = 60):
        """주기적으로 실현 PnL을 업데이트하는 백그라운드 루프"""
        logger.info(f"Starting realized PnL update loop with {interval_seconds}s interval.")
        while True:
            try:
                await self.update_realized_pnl(bybit_client)
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                logger.info("Realized PnL update loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in realized PnL update loop: {e}", exc_info=True)
                await asyncio.sleep(interval_seconds)

    async def update_order_history(self, bybit_client: "BybitClient"):
        """Bybit에서 주문 내역을 가져와 시스템 상태를 업데이트합니다."""
        try:
            order_history_data = await bybit_client.get_order_history(category="spot")
            
            processed_orders = []
            for data in order_history_data:
                order = Order(**data)
                order.trade_type = f"{order.side.value}"
                processed_orders.append(order)

            async with self._lock:
                self._system_state.order_history = [o.model_dump() for o in processed_orders]
            logger.info(f"Order history updated. Total orders: {len(self._system_state.order_history)}")
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
