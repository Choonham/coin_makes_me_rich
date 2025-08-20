# ===================================================================================
#   strategy/router.py: 전략 라우터 및 실행 엔진 (최종 통합본)
# ===================================================================================
#
#   - 기존 이벤트 기반 아키텍처와 거래 잠금 메커니즘을 유지하면서,
#     다양한 자동 청산 전략(익절, 손절, 타임아웃, 추적 손절)을 통합한 최종 버전입니다.
#
#   **핵심 변경사항:**
#   - `_position_monitor_loop`: 보유 포지션을 주기적으로 감시하여 청산 조건을 확인하는
#     별도의 백그라운드 태스크가 추가되었습니다.
#   - 청산 조건 충족 시: 모니터링 루프는 직접 주문을 실행하지 않고, SELL 신호를
#     중앙 `_signal_queue`에 넣어 모든 거래가 동일한 잠금 로직을 통과하도록 합니다.
#   - `_execute_buy_trade`: 매수 주문 성공 시, Position 객체에 익절/손절 계산에
#     필요한 진입 가격(`entry_price`)과 진입 후 최고가(`highest_price_since_entry`)를
#     기록하는 로직이 추가되었습니다.
#
#
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

from loguru import logger

from app.config import settings
from app.exchange.bybit_client import BybitClient
from app.risk.engine import RiskEngine
from app.state.store import state_store
from app.strategy.scalping import ScalpingSignalGenerator
from app.trend.aggregator import TrendAggregator
from app.utils.typing import Signal, Side
from app.state.models import Position  # Position 모델 import


class StrategyRouter:
    """
    거래 신호를 라우팅하고 리스크를 검증하며 주문을 실행하는 핵심 엔진. (이벤트 기반)
    """

    def __init__(self, bybit_client: BybitClient, trend_aggregator: TrendAggregator, risk_engine: RiskEngine):
        self.bybit_client = bybit_client
        self.risk_engine = risk_engine

        self._signal_queue: asyncio.Queue[Signal] = asyncio.Queue()

        self.trend_aggregator = trend_aggregator
        # trend_aggregator에 큐를 설정하는 부분이 있다면 유지합니다.
        # self.trend_aggregator.set_signal_queue(self._signal_queue)

        self.scalping_generator = ScalpingSignalGenerator(
            signal_queue=self._signal_queue,
            imbalance_threshold=0.52  # 필요시 .env에서 가져오도록 수정 가능
        )

        self._strategy_task: Optional[asyncio.Task] = None
        self._scalping_loop_task: Optional[asyncio.Task] = None
        # [추가됨] 포지션 모니터링 태스크
        self._position_monitor_task: Optional[asyncio.Task] = None

        self._last_trade_times: Dict[str, datetime] = {}
        self.trade_cooldown = timedelta(minutes=1)

        self._trade_in_progress: bool = False
        self._pending_symbol: Optional[str] = None

        logger.info("Event-Driven StrategyRouter initialized with Position Monitor.")

    async def start(self):
        """전략 실행, 스캘핑, 포지션 모니터링 루프를 시작합니다."""
        if self.is_running():
            logger.warning("Strategy is already running.")
            return

        self._trade_in_progress = False
        self._pending_symbol = None

        await state_store.set_status("running")
        self._strategy_task = asyncio.create_task(self._strategy_loop())
        self._scalping_loop_task = asyncio.create_task(self.scalping_generator.run_loop())
        # [추가됨] 포지션 모니터링 루프 시작
        self._position_monitor_task = asyncio.create_task(self._position_monitor_loop())
        logger.info("StrategyRouter, ScalpingGenerator, and PositionMonitor loops started.")

    async def stop(self):
        """모든 관련 루프를 중지합니다."""
        if not self.is_running():
            logger.warning("Strategy is not running.")
            return

        tasks_to_cancel = [self._strategy_task, self._scalping_loop_task, self._position_monitor_task]
        for task in tasks_to_cancel:
            if task:
                task.cancel()

        await asyncio.gather(*[t for t in tasks_to_cancel if t], return_exceptions=True)

        self._strategy_task = None
        self._scalping_loop_task = None
        self._position_monitor_task = None
        await state_store.set_status("stopped")
        logger.info("StrategyRouter, ScalpingGenerator, and PositionMonitor loops stopped.")

    def is_running(self) -> bool:
        """전략이 현재 실행 중인지 확인합니다."""
        return self._strategy_task is not None and not self._strategy_task.done()

    async def _strategy_loop(self):
        # 이 메서드는 기존 코드와 동일합니다.
        logger.info("Starting event-driven strategy loop...")
        while True:
            try:
                signal = await self._signal_queue.get()
                logger.debug(f"Received signal from queue: {signal.signal_type} for {signal.symbol}")

                if not self.risk_engine.is_globally_ok_to_trade():
                    logger.critical("Global trading stop triggered. Stopping all strategy tasks.")
                    if self._scalping_loop_task: self._scalping_loop_task.cancel()
                    if self._position_monitor_task: self._position_monitor_task.cancel()
                    break

                if self._trade_in_progress:
                    logger.warning(
                        f"Trade in progress for {self._pending_symbol}. Ignoring new signal for {signal.symbol}.")
                    continue

                await self._evaluate_signal(signal)
                self._signal_queue.task_done()

            except asyncio.CancelledError:
                logger.info("Strategy loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in strategy loop: {e}", exc_info=True)
                await state_store.add_error(f"Strategy loop error: {e}")
                await asyncio.sleep(5)

    async def _position_monitor_loop(self):
        """
        [신규] 주기적으로 활성 포지션을 확인하여 익절, 손절, 타임아웃, 추적 손절 조건을 검사하고
        필요 시 청산(SELL) 신호를 중앙 큐에 생성합니다.
        """
        logger.info("Position monitor loop started.")
        while True:
            try:
                await asyncio.sleep(2)  # 2초마다 모든 포지션 검사

                # state_store에서 직접 active_positions를 가져오도록 수정
                active_positions = state_store.get_system_state().active_positions
                if not active_positions:
                    continue

                config = self.risk_engine.get_config()

                for position in active_positions:
                    orderbook = state_store.get_orderbook(position.symbol)
                    if not (orderbook and orderbook.get('b') and orderbook['b']):
                        continue

                    current_price = float(orderbook['b'][0][0])
                    entry_price = position.entry_price

                    if entry_price == 0: continue

                    pnl_bps = ((current_price / entry_price) - 1) * 10000

                    # 1. 익절 (Take Profit)
                    if pnl_bps >= config.default_tp_bps:
                        self._create_exit_signal(position, f"Take Profit at {pnl_bps:.1f} BPS")
                        continue

                    # 2. 손절 (Stop Loss)
                    if pnl_bps <= -config.default_sl_bps:
                        self._create_exit_signal(position, f"Stop Loss at {pnl_bps:.1f} BPS")
                        continue

                    # 3. 추적 손절 (Trailing Stop)
                    position.highest_price_since_entry = max(position.highest_price_since_entry, current_price)
                    trailing_sl_price = position.highest_price_since_entry * (1 - config.trailing_sl_bps / 10000)

                    if current_price < trailing_sl_price:
                        self._create_exit_signal(position, "Trailing Stop triggered")
                        continue

                    # 4. 시간 기반 청산 (Time-out)
                    holding_time = time.time() - position.entry_timestamp
                    if holding_time > config.max_holding_time_seconds:
                        self._create_exit_signal(position, f"Timeout after {holding_time:.0f}s")
                        continue
            except asyncio.CancelledError:
                logger.info("Position monitor loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in position monitor loop: {e}", exc_info=True)

    def _create_exit_signal(self, position: Position, reason: str):
        """모니터링에 의해 발견된 청산 신호를 중앙 큐에 넣습니다."""
        logger.info(f"[EXIT SIGNAL CREATED] Symbol: {position.symbol}, Reason: {reason}")
        signal = Signal(
            symbol=position.symbol,
            side=Side.SELL,
            price=position.average_price,  # 가격은 참고용, 실제 주문은 시장가
            reason=reason,
            signal_type="exit_monitor"
        )
        self._signal_queue.put_nowait(signal)

    async def _evaluate_signal(self, signal: Signal):
        # 이 메서드는 기존 코드와 거의 동일합니다.
        symbol = signal.symbol
        if self._is_in_cooldown(symbol) and signal.signal_type != "exit_monitor":
            logger.debug(f"Ignoring signal for {symbol} due to cooldown.")
            return

        if signal.side == Side.SELL:
            # `state_store`에서 최신 잔고 정보를 직접 조회하도록 수정
            base_currency = symbol.replace("USDT", "")
            balance = state_store.get_balance(base_currency)

            if balance and balance.wallet_balance > 0:
                asset_value = balance.wallet_balance * (await self.bybit_client._get_latest_price(symbol))
                MIN_SELL_VALUE_USD = 1.0
                if asset_value < MIN_SELL_VALUE_USD:
                    logger.warning(
                        f"Sell signal for {symbol} ignored. Asset value (${asset_value:.4f}) is below minimum.")
                    return
                await self._execute_sell_trade(signal, balance.wallet_balance)
            else:
                logger.warning(f"Received SELL signal for {symbol}, but no balance found. Ignoring.")

        elif signal.side == Side.BUY:
            # 최대 보유 자산 개수 규칙 적용 로직을 risk_engine으로 위임하는 것이 좋습니다.
            # 여기서는 기존 로직을 유지합니다.
            is_allowed, reason = self.risk_engine.is_trade_allowed(symbol, Side.BUY)
            if is_allowed:
                logger.info(f"[EVALUATION] BUY signal for {signal.symbol} approved by RiskEngine.")
                await self._execute_buy_trade(signal)
            else:
                logger.debug(f"Ignoring BUY signal for {signal.symbol}. Reason: {reason}")

    def _is_in_cooldown(self, symbol: str) -> bool:
        # 이 메서드는 기존 코드와 동일합니다.
        last_trade_time = self._last_trade_times.get(symbol)
        if last_trade_time and datetime.utcnow() - last_trade_time < self.trade_cooldown:
            return True
        return False

    async def _execute_buy_trade(self, signal: Signal):
        # [수정됨] 주문 성공 후 Position 객체에 진입 가격 정보 기록
        order_id = None
        try:
            notional_size = self.risk_engine.calculate_notional_size(signal.symbol)
            MIN_ORDER_VALUE_USDT = 10.0
            if notional_size < MIN_ORDER_VALUE_USDT:
                logger.warning(f"Order value ({notional_size:.2f} USDT) is below minimum. Skipping.")
                return

            self._trade_in_progress = True
            self._pending_symbol = signal.symbol
            logger.critical(f"LOCKING TRADES: Submitting BUY order for {signal.symbol}.")

            order_result = await self.bybit_client.place_market_order(
                symbol=signal.symbol, side=Side.BUY, qty=0, notional=notional_size
            )

            if order_result and order_result.get('orderId'):
                order_id = order_result.get('orderId')
                logger.success(f"SPOT BUY order submitted for {signal.symbol}: {order_result}")
                self._last_trade_times[signal.symbol] = datetime.utcnow()

                # 주문 성공 후, 포지션 정보 업데이트
                await asyncio.sleep(1.5)  # WS 업데이트 대기
                position = state_store.get_position(signal.symbol)
                if position:
                    position.entry_price = position.average_price
                    position.entry_timestamp = time.time()
                    position.highest_price_since_entry = position.average_price
                    logger.info(
                        f"Updated position entry data for {signal.symbol}: price={position.entry_price}, timestamp={position.entry_timestamp}")

                asyncio.create_task(self._fetch_and_record_trade(signal.symbol, order_id))
                asyncio.create_task(self._unlock_after_delay(5))
            else:
                logger.error(f"SPOT BUY order submission failed for {signal.symbol}. Result: {order_result}")
                await self._unlock_trade()

        except Exception as e:
            logger.error(f"Failed to execute BUY trade for {signal.symbol}: {e}", exc_info=True)
            await state_store.add_error(f"Execution failed for {signal.symbol}: {e}")
            await self._unlock_trade()

    async def _execute_sell_trade(self, signal: Signal, qty_to_sell: float):
        # 이 메서드는 기존 코드와 동일합니다.
        order_id = None
        try:
            logger.info(f"Executing SPOT SELL trade for {signal.symbol} with qty {qty_to_sell}")

            self._trade_in_progress = True
            self._pending_symbol = signal.symbol
            logger.critical(f"LOCKING TRADES: Submitting SELL order for {signal.symbol}.")

            order_result = await self.bybit_client.place_market_order(
                symbol=signal.symbol, side=Side.SELL, qty=qty_to_sell, notional=0
            )

            if order_result and order_result.get('orderId'):
                order_id = order_result.get('orderId')
                logger.success(f"SPOT SELL order submitted for {signal.symbol}: {order_result}")
                self._last_trade_times[signal.symbol] = datetime.utcnow()
                asyncio.create_task(self._fetch_and_record_trade(signal.symbol, order_id))
                asyncio.create_task(self._unlock_after_delay(5))
            else:
                logger.error(f"SPOT SELL order submission failed for {signal.symbol}. Result: {order_result}")
                await self._unlock_trade()

        except Exception as e:
            logger.error(f"Failed to execute SELL trade for {signal.symbol}: {e}", exc_info=True)
            await state_store.add_error(f"Execution failed for {signal.symbol}: {e}")
            await self._unlock_trade()

    # 아래 헬퍼 함수들은 기존 코드와 동일합니다.
    async def _fetch_and_record_trade(self, symbol: str, order_id: str):
        await asyncio.sleep(1.5)
        try:
            history = await self.bybit_client.get_order_history(symbol=symbol, order_id=order_id)
            if history:
                # state_store에 거래 기록을 추가하는 로직이 필요하다면 여기에 구현합니다.
                # 예: await state_store.add_trade(history[0])
                logger.success(f"Successfully fetched and recorded trade history for order {order_id}.")
            else:
                logger.warning(f"Could not fetch trade history for order {order_id}.")
        except Exception as e:
            logger.error(f"Error fetching trade history for order {order_id}: {e}")

    async def _unlock_trade(self):
        if self._trade_in_progress:
            logger.critical(f"UNLOCKING TRADES: Trade for {self._pending_symbol} is complete.")
            self._trade_in_progress = False
            self._pending_symbol = None

    async def _unlock_after_delay(self, delay_seconds: int):
        await asyncio.sleep(delay_seconds)
        await self._unlock_trade()