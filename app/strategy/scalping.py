
# ===================================================================================
#   strategy/scalping.py: 스캘핑 신호 생성 로직
# ===================================================================================
#
#   - 시장 데이터(주로 오더북)를 분석하여 단기적인 거래 기회를 포착하고 신호를 생성합니다.
#   - 이 파일의 로직은 순수하게 시장 미세구조에 기반하며, 트렌드 분석과는 독립적으로 동작합니다.
#   - 생성된 신호는 `StrategyRouter`로 전달되어 최종 거래 결정에 사용됩니다.
#
#   **구현된 데모 전략:**
#   - **오더북 불균형 (Orderbook Imbalance):**
#     - 오더북의 최상위 레벨(1단 호가)에서 매수(bid)와 매도(ask) 주문량의 비율을 계산합니다.
#     - 이 비율이 설정된 임계값을 초과하면, 강한 매수 또는 매도 압력이 있다고 판단하여 신호를 생성합니다.
#     - 예: `(매수 총량) / (매수 총량 + 매도 총량) > 0.7` 이면 매수 신호.
#
#   **확장 방향:**
#   - **거래량 가속**: 최근 체결된 거래의 양과 빈도가 급증하는 것을 감지합니다.
#   - **스프레드 분석**: 매수-매도 호가 차이(spread)가 비정상적으로 좁혀지거나 넓혀지는 것을 분석합니다.
#   - **다중 레벨 불균형**: 오더북의 여러 레벨에 걸쳐 가중치를 부여하여 불균형을 계산합니다.
#
#
import asyncio
from loguru import logger
from typing import Optional

from app.state.store import state_store
from app.utils.typing import Signal, Side


class ScalpingSignalGenerator:
    """
    오더북 데이터를 기반으로 스캘핑 신호를 생성하여 중앙 큐로 보냅니다.
    - Sell 신호 로직 개선
    - 10달러 미만 '먼지' 자산 보유 예외 처리
    - 매수/매도 신호 생성 로직 분리 (버그 수정)
    """
    def __init__(self, signal_queue: asyncio.Queue[Signal], imbalance_threshold: float = 0.6, depth: int = 5):
        """
        스캘핑 신호 생성기를 초기화합니다.
        :param signal_queue: 생성된 신호를 보낼 중앙 큐
        """
        if not 0.5 < imbalance_threshold < 1.0:
            raise ValueError("imbalance_threshold must be between 0.5 and 1.0")
        if not depth > 0:
            raise ValueError("depth must be a positive integer")

        self.signal_queue = signal_queue
        self.imbalance_threshold = imbalance_threshold
        self.depth = depth
        logger.info(f"ScalpingSignalGenerator initialized with imbalance threshold: {imbalance_threshold}, depth: {depth}")

    async def run_loop(self, interval_seconds: float = 1.0):
        """
        주기적으로 모든 거래 대상 심볼에 대해 신호 생성을 확인하는 루프.
        """
        logger.info(f"Starting scalping signal generation loop with {interval_seconds}s interval.")
        while True:
            try:
                universe = state_store.get_universe()
                for symbol in universe:
                    self.check_and_send_signal(symbol)

                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                logger.info("Scalping signal loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in scalping signal loop: {e}", exc_info=True)
                await asyncio.sleep(5) # 에러 발생 시 잠시 대기

    def check_and_send_signal(self, symbol: str):
        """
        지정된 심볼의 자산 가치를 평가하여 신호를 확인하고 큐로 보냅니다.
        10달러 미만의 자산은 보유하지 않은 것으로 간주합니다.
        """
        base_currency = symbol.replace("USDT", "")
        balance_info = state_store.get_balance(base_currency)
        is_asset_held = False
        MIN_ASSET_VALUE_USD = 10.0 # 자산 보유로 판단할 최소 USD 가치

        if balance_info and balance_info.wallet_balance > 0:
            orderbook = state_store.get_orderbook(symbol)
            if orderbook and orderbook.get('b') and len(orderbook['b']) > 0:
                try:
                    current_price = float(orderbook['b'][0][0])
                    asset_value_usd = balance_info.wallet_balance * current_price

                    if asset_value_usd >= MIN_ASSET_VALUE_USD:
                        is_asset_held = True
                    else:
                        logger.trace(f"Ignoring dust asset for {symbol}. Value: ${asset_value_usd:.4f}")
                except (ValueError, IndexError) as e:
                    logger.warning(f"Could not calculate asset value for {symbol} due to orderbook data issue: {e}")
                    is_asset_held = True # 안전을 위해 보유한 것으로 간주

        if is_asset_held:
            # 자산을 의미있게 보유 중일 때는 '매도 신호'만 확인
            exit_signal = self._check_for_exit_signal(symbol)
            if exit_signal:
                self.signal_queue.put_nowait(exit_signal)
        else:
            # 자산을 보유하지 않았거나, 10달러 미만일 때는 '매수 신호'만 확인
            buy_signal = self._check_for_buy_signal(symbol)
            if buy_signal:
                self.signal_queue.put_nowait(buy_signal)

    def _check_for_buy_signal(self, symbol: str) -> Optional[Signal]:
        """
        진입(매수) 신호를 확인합니다.
        """
        orderbook = state_store.get_orderbook(symbol)
        if not orderbook or not orderbook.get('b') or not orderbook.get('a'):
            return None

        bids = orderbook.get('b', [])
        asks = orderbook.get('a', [])

        if len(bids) < self.depth or len(asks) < self.depth:
            return None

        total_bid_size = sum(float(bid[1]) for bid in bids[:self.depth])
        total_ask_size = sum(float(ask[1]) for ask in asks[:self.depth])
        total_liquidity = total_bid_size + total_ask_size
        if total_liquidity == 0:
            return None

        bid_pressure_ratio = total_bid_size / total_liquidity
        best_ask_price = float(asks[0][0])

        if bid_pressure_ratio > self.imbalance_threshold:
            strength = (bid_pressure_ratio - 0.5) * 2
            logger.debug(f"[SCALPING SIGNAL] BUY pressure for {symbol}. Ratio: {bid_pressure_ratio:.2f}")
            return Signal(
                symbol=symbol, side=Side.BUY, price=best_ask_price,
                reason=f"Orderbook imbalance (bid ratio: {bid_pressure_ratio:.2f})",
                strength=strength, signal_type="scalping"
            )
        return None

    def _check_for_exit_signal(self, symbol: str) -> Optional[Signal]:
        """
        보유 자산 청산을 위한 신호를 확인합니다. (매도 조건 완화)
        """
        orderbook = state_store.get_orderbook(symbol)
        if not orderbook or not orderbook.get('b') or not orderbook.get('a'):
            return None

        bids = orderbook.get('b', [])
        asks = orderbook.get('a', [])
        if len(bids) < self.depth or len(asks) < self.depth:
            return None

        total_bid_size = sum(float(bid[1]) for bid in bids[:self.depth])
        total_ask_size = sum(float(ask[1]) for ask in asks[:self.depth])
        total_liquidity = total_bid_size + total_ask_size
        if total_liquidity == 0:
            return None

        bid_pressure_ratio = total_bid_size / total_liquidity
        best_bid_price = float(bids[0][0])

        # [수정됨] 매수 압력이 50% 미만으로 떨어지면(중립화되면) 매도 신호 생성
        if bid_pressure_ratio < 0.5:
            logger.info(f"[EXIT SIGNAL] Pressure neutralized for {symbol}. Creating SELL signal. Ratio: {bid_pressure_ratio:.2f}")
            return Signal(
                symbol=symbol, side=Side.SELL, price=best_bid_price,
                reason=f"Orderbook pressure neutralized (bid ratio: {bid_pressure_ratio:.2f})",
                signal_type="scalping_exit"
            )
        return None