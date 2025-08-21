# ===================================================================================
#   strategy/router.py: 전략 라우터 및 실행 엔진 (최종 통합본)
# ===================================================================================
#
#   **핵심 변경사항:**
#   - `_evaluate_signal` 함수에서 존재하지 않는 `balance.usd_value` 속성 접근 오류를 수정했습니다.
#   - 자산의 USD 가치를 현재 가격을 기준으로 직접 계산하여, 최소 판매 금액 및
#     먼지 자산(dust) 여부를 정확히 체크하도록 로직을 변경했습니다.
#
#
import asyncio
import time
from datetime import datetime, timedelta
from typing import Dict, Optional

from loguru import logger

from app.exchange.bybit_client import BybitClient
from app.risk.engine import RiskEngine
from app.state.store import state_store
from app.strategy.scalping import SignalGenerator
from app.trend.aggregator import TrendAggregator
from app.utils.typing import Signal, Side
from app.state.models import Position
from app.strategy.technical_analysis import calculate_indicators


class StrategyRouter:
    """
    거래 신호를 라우팅하고 리스크를 검증하며 주문을 실행하는 핵심 엔진. (이벤트 기반)
    """

    def __init__(self, bybit_client: BybitClient, trend_aggregator: Optional[TrendAggregator], risk_engine: RiskEngine):
        self.bybit_client = bybit_client
        self.risk_engine = risk_engine

        self._signal_queue: asyncio.Queue[Signal] = asyncio.Queue()

        self.trend_aggregator = trend_aggregator
        if self.trend_aggregator:
            self.trend_aggregator.set_signal_queue(self._signal_queue)

        self.signal_generator = SignalGenerator(
            signal_queue=self._signal_queue,
            bybit_client=self.bybit_client
        )

        self._strategy_task: Optional[asyncio.Task] = None
        self._signal_gen_task: Optional[asyncio.Task] = None
        self._position_monitor_task: Optional[asyncio.Task] = None

        self._last_trade_times: Dict[str, datetime] = {}
        self.trade_cooldown = timedelta(minutes=1)

        self._trade_in_progress: bool = False
        self._pending_symbol: Optional[str] = None

        logger.info("Event-Driven StrategyRouter initialized with Position Monitor.")

    async def start(self):
        """전략 실행, 신호 생성, 포지션 모니터링 루프를 시작합니다."""
        if self.is_running():
            logger.warning("Strategy is already running.")
            return

        self._trade_in_progress = False
        self._pending_symbol = None

        await state_store.set_status("running")
        self._strategy_task = asyncio.create_task(self._strategy_loop())
        self._signal_gen_task = asyncio.create_task(self.signal_generator.run_loop())
        self._position_monitor_task = asyncio.create_task(self._position_monitor_loop())
        logger.info("StrategyRouter, SignalGenerator, and PositionMonitor loops started.")

    async def stop(self):
        """모든 관련 루프를 중지합니다."""
        if not self.is_running():
            logger.warning("Strategy is not running.")
            return

        tasks = [self._strategy_task, self._signal_gen_task, self._position_monitor_task]
        for task in tasks:
            if task:
                task.cancel()
        await asyncio.gather(*[t for t in tasks if t], return_exceptions=True)

        self._strategy_task, self._signal_gen_task, self._position_monitor_task = None, None, None
        await state_store.set_status("stopped")
        logger.info("StrategyRouter, SignalGenerator, and PositionMonitor loops stopped.")

    def is_running(self) -> bool:
        """전략이 현재 실행 중인지 확인합니다."""
        return self._strategy_task is not None and not self._strategy_task.done()

    async def _strategy_loop(self):
        """메인 전략 실행 루프. 중앙 신호 큐에서 신호를 기다립니다."""
        logger.info("Starting event-driven strategy loop...")
        while True:
            try:
                signal = await self._signal_queue.get()
                logger.debug(f"Received signal from queue: {signal.signal_type} for {signal.symbol}")

                if not self.risk_engine.is_globally_ok_to_trade():
                    continue

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
        주기적으로 활성 포지션을 확인하여 자동 청산 신호를 생성합니다.
        (익절/손절 + 새로운 TA 기반 매도 조건 추가)
        """
        logger.info("Position monitor loop started.")
        while True:
            try:
                await asyncio.sleep(3) # TA 계산을 위해 약간의 여유를 둠
                active_positions = state_store.get_system_state().active_positions
                if not active_positions:
                    continue

                config = self.risk_engine.get_config()
                for position in active_positions:
                    # --- 1. 익절/손절/타임아웃 확인 (기존 로직) ---
                    orderbook = state_store.get_orderbook(position.symbol)
                    if not (orderbook and orderbook.get('b') and orderbook['b']):
                        continue
                    
                    current_price = float(orderbook['b'][0][0])
                    entry_price = position.entry_price
                    if entry_price == 0: continue

                    pnl_bps = ((current_price / entry_price) - 1) * 10000

                    if pnl_bps >= config.default_tp_bps:
                        self._create_exit_signal(position, f"Take Profit at {pnl_bps:.1f} BPS")
                        continue # 신호 생성 후 다음 포지션으로
                    elif pnl_bps <= -config.default_sl_bps:
                        self._create_exit_signal(position, f"Stop Loss at {pnl_bps:.1f} BPS")
                        continue
                    elif time.time() - position.entry_timestamp > config.max_holding_time_seconds:
                        self._create_exit_signal(position, f"Timeout after {time.time() - position.entry_timestamp:.0f}s")
                        continue

                    # --- 2. TA 기반 매도 조건 확인 (새로운 로직) ---
                    try:
                        kline_data = await self.bybit_client.get_kline(symbol=position.symbol, interval="1", limit=100)
                        if not kline_data: continue

                        df = calculate_indicators(kline_data, self.signal_generator.short_ma, self.signal_generator.long_ma)
                        if df.empty or len(df) < 2: continue

                        latest = df.iloc[-1]
                        previous = df.iloc[-2]

                        # 매도 조건: RSI > 70 또는 데드 크로스
                        rsi_condition = latest['RSI'] > 70
                        dead_cross_condition = (previous[f'SMA_{self.signal_generator.short_ma}'] >= previous[f'SMA_{self.signal_generator.long_ma}']) and \
                                                 (latest[f'SMA_{self.signal_generator.short_ma}'] < latest[f'SMA_{self.signal_generator.long_ma}'])

                        if rsi_condition:
                            self._create_exit_signal(position, f"RSI > 70 ({latest['RSI']:.1f})")
                        elif dead_cross_condition:
                            self._create_exit_signal(position, "Dead Cross detected")

                    except Exception as e:
                        logger.error(f"Error checking TA exit conditions for {position.symbol}: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in position monitor loop: {e}", exc_info=True)

    def _create_exit_signal(self, position: Position, reason: str):
        """모니터링에 의해 발견된 청산 신호를 중앙 큐에 넣습니다."""
        logger.info(f"[EXIT SIGNAL CREATED] Symbol: {position.symbol}, Reason: {reason}")
        signal = Signal(symbol=position.symbol, side=Side.SELL, price=position.average_price, reason=reason,
                        signal_type="exit_monitor")
        self._signal_queue.put_nowait(signal)

    async def _evaluate_signal(self, signal: Signal):
        """큐에서 받은 신호를 평가하여 거래를 실행합니다."""
        symbol = signal.symbol
        if self._is_in_cooldown(symbol) and signal.signal_type != "exit_monitor":
            return

        # --- [오류 수정] ---
        # USD 가치 계산을 위한 현재 가격 정보부터 가져옵니다.
        orderbook = state_store.get_orderbook(symbol)
        if not (orderbook and orderbook.get('b') and orderbook['b']):
            logger.warning(f"Cannot evaluate signal for {symbol}, no orderbook data to get current price.")
            return
        current_price = float(orderbook['b'][0][0])
        # --- [수정 완료] ---

        if signal.side == Side.SELL:
            base_currency = symbol.replace("USDT", "")
            balance = state_store.get_balance(base_currency)

            if balance and balance.wallet_balance > 0:
                asset_value_usd = balance.wallet_balance * current_price
                MIN_SELL_VALUE_USD = 5.0

                if asset_value_usd < MIN_SELL_VALUE_USD:
                    logger.debug(
                        f"Sell signal for {symbol} ignored. Asset value (${asset_value_usd:.4f}) is below minimum.")
                    return

                logger.info(f"[EVALUATION] SELL signal for {symbol}. Proceeding to sell.")
                await self._execute_sell_trade(signal, balance.wallet_balance)

        elif signal.side == Side.BUY:
            base_currency = symbol.replace("USDT", "")
            balance = state_store.get_balance(base_currency)

            # 이미 자산을 보유하고 있는지 확인
            if balance and balance.wallet_balance > 0:
                asset_value_usd = balance.wallet_balance * current_price
                DUST_THRESHOLD_USD = 10.0
                if asset_value_usd < DUST_THRESHOLD_USD:
                    logger.info(f"[EVALUATION] Topping up dust asset {symbol} (value: ${asset_value_usd:.2f}).")
                    # 먼지 자산 추가 매수 로직으로 바로 진행
                else:
                    # 이미 충분한 양의 자산을 보유하고 있으면 추가 매수 안함
                    logger.debug(
                        f"Ignoring BUY signal for {symbol}. Asset already held with sufficient value (${asset_value_usd:.2f}).")
                    return

            # 리스크 엔진을 통해 최종 거래 가능 여부와 규모를 결정
            is_allowed, reason = self.risk_engine.is_trade_allowed(symbol, Side.BUY)
            if is_allowed:
                logger.info(f"[EVALUATION] BUY signal for {symbol} approved by RiskEngine.")
                await self._execute_buy_trade(signal)
            else:
                logger.debug(f"Ignoring BUY signal for {symbol}. Reason: {reason}")

    def _is_in_cooldown(self, symbol: str) -> bool:
        """해당 심볼이 현재 거래 쿨다운 상태인지 확인합니다."""
        last_trade_time = self._last_trade_times.get(symbol)
        if last_trade_time and datetime.utcnow() - last_trade_time < self.trade_cooldown:
            return True
        return False

        # app/strategy/router.py

    async def _execute_buy_trade(self, signal: Signal):
        """현물 매수 주문을 실행하고, 체결 완료 후 정확한 진입 가격을 기록합니다."""
        try:
            notional_size = self.risk_engine.calculate_notional_size(signal.symbol)
            if notional_size <= 0:
                return

            self._trade_in_progress = True
            self._pending_symbol = signal.symbol
            logger.critical(
                f"LOCKING TRADES: Submitting BUY order for {signal.symbol} with notional {notional_size}.")

            order_result = await self.bybit_client.place_market_order(
                symbol=signal.symbol,
                side=Side.BUY,
                qty=0,
                notional=notional_size
            )

            if order_result and order_result.get('orderId'):
                order_id = order_result['orderId']
                logger.success(f"SPOT BUY order submitted for {signal.symbol}: {order_result}")
                self._last_trade_times[signal.symbol] = datetime.utcnow()

                # --- 주문 체결 내역을 조회하여 실제 진입 가격을 가져옵니다 ---
                await asyncio.sleep(2.0)  # Bybit 서버가 주문을 처리하고 내역에 기록할 시간을 줍니다.
                try:
                    history = await self.bybit_client.get_order_history(symbol=signal.symbol, order_id=order_id,
                                                                        limit=1)
                    if history and history[0].get('avgPrice') and float(history[0]['avgPrice']) > 0:
                        actual_entry_price = float(history[0]['avgPrice'])

                        await state_store.add_trade(history[0])

                        # 새로운 Position 객체를 생성하여 active_positions에 추가
                        position = Position(
                            symbol=signal.symbol,
                            quantity=float(history[0]['execQty']),
                            average_price=actual_entry_price,
                            entry_price=actual_entry_price,
                            entry_timestamp=time.time(),
                            highest_price_since_entry=actual_entry_price
                        )
                        await state_store.add_or_update_position(position)

                        logger.success(
                            f"SUCCESS: New position for {signal.symbol} created with actual entry price: {actual_entry_price}")
                    else:
                        logger.error(f"Could not fetch valid avgPrice for order {order_id}. History: {history}")

                except Exception as e:
                    logger.error(f"Failed to fetch and record trade for order {order_id}: {e}", exc_info=True)

                asyncio.create_task(self._unlock_after_delay(5))

            else:
                logger.error(f"SPOT BUY order submission failed for {signal.symbol}. Result: {order_result}")
                await self._unlock_trade()

        except Exception as e:
            logger.error(f"Failed to execute BUY trade for {signal.symbol}: {e}", exc_info=True)
            await self._unlock_trade()

    async def _execute_sell_trade(self, signal: Signal, qty_to_sell: float):
        """보유한 현물 자산 전체를 매도하는 주문을 실행합니다."""
        try:
            self._trade_in_progress = True
            self._pending_symbol = signal.symbol
            logger.critical(f"LOCKING TRADES: Submitting SELL order for {signal.symbol}.")

            order_result = await self.bybit_client.place_market_order(symbol=signal.symbol, side=Side.SELL,
                                                                      qty=qty_to_sell, notional=0)

            if order_result and order_result.get('orderId'):
                logger.success(f"SPOT SELL order submitted for {signal.symbol}: {order_result}")
                self._last_trade_times[signal.symbol] = datetime.utcnow()
                asyncio.create_task(self._fetch_and_record_trade(signal.symbol, order_result['orderId']))
                asyncio.create_task(self._unlock_after_delay(5))
            else:
                logger.error(f"SPOT SELL order submission failed for {signal.symbol}. Result: {order_result}")
                await self._unlock_trade()

        except Exception as e:
            logger.error(f"Failed to execute SELL trade for {signal.symbol}: {e}", exc_info=True)
            await self._unlock_trade()

    async def _fetch_and_record_trade(self, symbol: str, order_id: str):
        """주문 ID를 사용하여 거래 내역을 조회하고 저장합니다."""
        await asyncio.sleep(1.5)
        try:
            history = await self.bybit_client.get_order_history(symbol=symbol, order_id=order_id)
            if history:
                await state_store.add_trade(history[0])
        except Exception as e:
            logger.error(f"Error fetching trade history for order {order_id}: {e}")

    async def _unlock_trade(self):
        """거래 잠금을 해제합니다."""
        if self._trade_in_progress:
            logger.critical(f"UNLOCKING TRADES: Trade for {self._pending_symbol} is considered complete.")
            self._trade_in_progress = False
            self._pending_symbol = None

    async def _unlock_after_delay(self, delay_seconds: int):
        """지정된 시간(초)만큼 대기한 후 거래 잠금을 해제합니다."""
        await asyncio.sleep(delay_seconds)
        await self._unlock_trade()