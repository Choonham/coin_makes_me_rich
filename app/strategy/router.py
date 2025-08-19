
# ===================================================================================
#   strategy/router.py: 전략 라우터 및 실행 엔진
# ===================================================================================
#
#   - 시스템의 핵심 두뇌 역할을 하는 중앙 제어 장치입니다.
#   - 여러 소스(스캘핑, 트렌드)로부터 들어오는 거래 신호를 종합하고, 리스크 엔진의
#     검증을 거쳐 최종 거래 실행 여부를 결정합니다.
#
#   **주요 기능:**
#   - **중앙 루프**: `start()` 메서드 호출 시, 비동기 루프를 실행하여 지속적으로 거래 기회를 탐색합니다.
#   - **신호 통합**: 스캘핑 신호와 트렌드 신호를 결합하는 로직을 포함합니다. (예: AND/OR 조건)
#   - **리스크 검증**: 거래 실행 전, `RiskEngine`에 문의하여 모든 리스크 규칙(일일 손실 한도, 거래당 리스크 등)을 통과하는지 확인합니다.
#   - **주문 실행**: 모든 조건이 충족되면 `BybitClient`를 통해 실제 주문을 제출합니다.
#   - **상태 관리**: `start()`/`stop()` 메서드를 통해 API로부터 거래 실행을 제어받고, 현재 상태를 `state_store`에 반영합니다.
#   - **쿨다운 관리**: 동일 심볼에 대한 반복적인 거래를 방지하기 위해 마지막 거래 후 일정 시간 대기합니다.
#
#   **데이터 흐름:**
#   1. `POST /control/start` API 호출 -> `self.start()` 실행.
#   2. `_strategy_loop()` 태스크가 백그라운드에서 시작됨.
#   3. 루프 내에서 `universe`의 모든 심볼에 대해 다음을 반복:
#      a. `ScalpingSignalGenerator`에서 시장 미세구조 신호 확인.
#      b. `TrendAggregator`에서 소셜 트렌드 신호 확인.
#      c. 두 신호가 모두 존재하고 방향이 일치하면 (AND 조건) -> 최종 신호로 간주.
#      d. `RiskEngine.validate_trade()`로 리스크 검증.
#      e. 통과 시, `BybitClient.place_market_order()`로 주문 실행.
#   4. `POST /control/stop` API 호출 -> `self.stop()` 실행 -> `_strategy_loop()` 태스크 취소.
#
#
import asyncio
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

class StrategyRouter:
    """
    거래 신호를 라우팅하고 리스크를 검증하며 주문을 실행하는 핵심 엔진. (이벤트 기반)
    """
    def __init__(self, bybit_client: BybitClient, trend_aggregator: TrendAggregator, risk_engine: RiskEngine):
        self.bybit_client = bybit_client
        self.risk_engine = risk_engine
        
        # 중앙 신호 큐(Queue) 생성
        self._signal_queue: asyncio.Queue[Signal] = asyncio.Queue()

        # 각 신호 생성기는 이제 중앙 큐에 직접 신호를 보냄
        self.trend_aggregator = trend_aggregator
        self.trend_aggregator.set_signal_queue(self._signal_queue)
        
        self.scalping_generator = ScalpingSignalGenerator(
            signal_queue=self._signal_queue,
            imbalance_threshold=0.52
        )
        
        self._strategy_task: Optional[asyncio.Task] = None
        self._scalping_loop_task: Optional[asyncio.Task] = None
        self._last_trade_times: Dict[str, datetime] = {}
        self.trade_cooldown = timedelta(minutes=1)

        self._trade_in_progress: bool = False
        self._pending_symbol: Optional[str] = None

        logger.info("Event-Driven StrategyRouter initialized.")

    async def start(self):
        """전략 실행 루프 및 스캘핑 신호 생성 루프를 시작합니다."""
        if self.is_running():
            logger.warning("Strategy is already running.")
            return
        
        self._trade_in_progress = False
        self._pending_symbol = None
        
        await state_store.set_status("running")
        self._strategy_task = asyncio.create_task(self._strategy_loop())
        self._scalping_loop_task = asyncio.create_task(self.scalping_generator.run_loop())
        logger.info("StrategyRouter and ScalpingGenerator loops started.")

    async def stop(self):
        """모든 관련 루프를 중지합니다."""
        if not self.is_running():
            logger.warning("Strategy is not running.")
            return

        if self._strategy_task:
            self._strategy_task.cancel()
        if self._scalping_loop_task:
            self._scalping_loop_task.cancel()
            
        await asyncio.gather(self._strategy_task, self._scalping_loop_task, return_exceptions=True)

        self._strategy_task = None
        self._scalping_loop_task = None
        await state_store.set_status("stopped")
        logger.info("StrategyRouter and ScalpingGenerator loops stopped.")

    def is_running(self) -> bool:
        """전략이 현재 실행 중인지 확인합니다."""
        return self._strategy_task is not None and not self._strategy_task.done()

    async def _strategy_loop(self):
        """
        메인 전략 실행 루프. 중앙 신호 큐에서 신호를 기다립니다.
        """
        logger.info("Starting event-driven strategy loop...")
        while True:
            try:
                # 큐에 신호가 도착할 때까지 대기
                signal = await self._signal_queue.get()
                
                logger.debug(f"Received signal from queue: {signal.signal_type} for {signal.symbol}")

                # 1. 글로벌 거래 중지 조건 확인
                if not self.risk_engine.is_globally_ok_to_trade():
                    logger.critical("Global trading stop triggered. Stopping all strategy tasks.")
                    if self._scalping_loop_task:
                        self._scalping_loop_task.cancel()
                    break  # 루프를 탈출하여 현재 태스크를 정상적으로 종료

                # 2. 거래 잠금 상태 확인
                if self._trade_in_progress:
                    logger.warning(f"Trade in progress for {self._pending_symbol}. Ignoring new signal for {signal.symbol}.")
                    continue

                # 3. 신호 평가 및 실행
                await self._evaluate_signal(signal)
                
                self._signal_queue.task_done()

            except asyncio.CancelledError:
                logger.info("Strategy loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in strategy loop: {e}", exc_info=True)
                await state_store.add_error(f"Strategy loop error: {e}")
                await asyncio.sleep(5)

    async def _evaluate_signal(self, signal: Signal):
        """
        큐에서 받은 신호를 평가하여 거래를 실행합니다.
        신호 생성 단계에서 이미 잔고 확인 등 1차 필터링이 완료된 상태입니다.
        """
        symbol = signal.symbol
        if self._is_in_cooldown(symbol):
            logger.debug(f"Ignoring signal for {symbol} due to cooldown.")
            return

        # 매도 신호 처리 (신호를 100% 신뢰하고 즉시 실행)
        if signal.side == Side.SELL:
            base_currency = signal.symbol.replace("USDT", "")
            balance = state_store.get_balance(base_currency)
            
            if balance and balance.wallet_balance > 0:
                # 먼지(dust) 판매 시도 방지
                MIN_SELL_VALUE_USD = 1.0
                if balance.usd_value < MIN_SELL_VALUE_USD:
                    logger.warning(f"Sell signal for {signal.symbol} ignored. Asset value (${balance.usd_value:.4f}) is below minimum threshold of ${MIN_SELL_VALUE_USD}.")
                    return

                logger.info(f"[EVALUATION] SELL signal for owned asset {signal.symbol}. Proceeding to sell.")
                await self._execute_sell_trade(signal, balance.wallet_balance)
            else:
                logger.warning(f"Received SELL signal for {signal.symbol}, but no balance found. Ignoring.")
        
        # 매수 신호 처리
        elif signal.side == Side.BUY:
            base_currency = signal.symbol.replace("USDT", "")
            balances = state_store.get_all_balances()
            
            # 이미 보유한 자산인지, 그리고 그 가치가 10달러 미만(dust)인지 확인
            if base_currency in balances and balances[base_currency].wallet_balance > 0:
                asset_value_usd = balances[base_currency].usd_value
                DUST_THRESHOLD_USD = 10.0
                if asset_value_usd < DUST_THRESHOLD_USD:
                    logger.info(f"[EVALUATION] Topping up dust asset {signal.symbol} (value: ${asset_value_usd:.2f}). Proceeding to buy.")
                    await self._execute_buy_trade(signal)
                    return # 추가 매수 후 로직 종료

            # Dust 추가 매수가 아닌 경우, 최대 보유 자산 개수 규칙 적용
            owned_assets = {coin for coin, bal in balances.items() if coin != "USDT" and bal.wallet_balance > 0}
            max_symbols = self.risk_engine.get_config().max_active_symbols

            if len(owned_assets) < max_symbols:
                logger.info(f"[EVALUATION] BUY signal for new asset {signal.symbol}. Proceeding to buy (current assets: {len(owned_assets)}/{max_symbols}).")
                await self._execute_buy_trade(signal)
            else:
                logger.debug(f"Ignoring BUY signal for {signal.symbol}. Already holding max active symbols ({len(owned_assets)}/{max_symbols}): {owned_assets}")

    def _is_in_cooldown(self, symbol: str) -> bool:
        """해당 심볼이 현재 거래 쿨다운 상태인지 확인합니다."""
        last_trade_time = self._last_trade_times.get(symbol)
        if last_trade_time and datetime.utcnow() - last_trade_time < self.trade_cooldown:
            return True
        return False

    async def _execute_buy_trade(self, signal: Signal):
        """현물 매수 주문을 실행하고 거래 잠금을 설정합니다."""
        order_id = None
        try:
            usdt_balance = state_store.get_balance("USDT")
            if not usdt_balance or usdt_balance.wallet_balance <= 10:
                logger.warning(f"Not enough USDT to buy {signal.symbol}. Balance: {usdt_balance.wallet_balance if usdt_balance else 0}")
                return

            risk_per_trade = self.risk_engine.get_config().risk_per_trade
            usdt_to_spend = usdt_balance.wallet_balance * risk_per_trade
            usdt_to_spend = round(usdt_to_spend, 4)

            MIN_ORDER_VALUE_USDT = 10.0
            if usdt_to_spend < MIN_ORDER_VALUE_USDT:
                logger.warning(f"Order value ({usdt_to_spend:.2f} USDT) is below minimum. Skipping.")
                return
            
            self._trade_in_progress = True
            self._pending_symbol = signal.symbol
            logger.critical(f"LOCKING TRADES: Submitting BUY order for {signal.symbol}.")

            order_result = await self.bybit_client.place_market_order(
                symbol=signal.symbol, side=Side.BUY, qty=0, notional=usdt_to_spend
            )
            
            if order_result and order_result.get('orderId'):
                order_id = order_result.get('orderId')
                logger.success(f"SPOT BUY order submitted for {signal.symbol}: {order_result}")
                self._last_trade_times[signal.symbol] = datetime.utcnow()
                # 주문 성공 후, 최종 체결 내역을 가져와서 기록
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
        """보유한 현물 자산 전체를 매도하는 주문을 실행합니다."""
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
                # 주문 성공 후, 최종 체결 내역을 가져와서 기록
                asyncio.create_task(self._fetch_and_record_trade(signal.symbol, order_id))

                asyncio.create_task(self._unlock_after_delay(5))
            else:
                logger.error(f"SPOT SELL order submission failed for {signal.symbol}. Result: {order_result}")
                await self._unlock_trade()

        except Exception as e:
            logger.error(f"Failed to execute SELL trade for {signal.symbol}: {e}", exc_info=True)
            await state_store.add_error(f"Execution failed for {signal.symbol}: {e}")
            await self._unlock_trade()

    async def _fetch_and_record_trade(self, symbol: str, order_id: str):
        """주문 ID를 사용하여 거래 내역을 조회하고 저장합니다."""
        await asyncio.sleep(1.5) # 거래소 처리 시간 대기
        try:
            history = await self.bybit_client.get_order_history(symbol=symbol, order_id=order_id)
            if history:
                await state_store.add_trade(history[0])
                logger.success(f"Successfully fetched and recorded trade history for order {order_id}.")
            else:
                logger.warning(f"Could not fetch trade history for order {order_id}.")
        except Exception as e:
            logger.error(f"Error fetching trade history for order {order_id}: {e}")

    async def _unlock_trade(self):
        """거래 잠금을 해제합니다."""
        logger.critical(f"UNLOCKING TRADES: Trade for {self._pending_symbol} is considered complete.")
        self._trade_in_progress = False
        self._pending_symbol = None

    async def _unlock_after_delay(self, delay_seconds: int):
        """지정된 시간(초)만큼 대기한 후 거래 잠금을 해제합니다."""
        await asyncio.sleep(delay_seconds)
        await self._unlock_trade()

