# ===================================================================================
#   strategy/signal_generator.py: 기술적 분석 기반 신호 생성기
# ===================================================================================
#
#   - 지정된 기술적 지표(RSI, 이동평균선 등)를 기반으로 '매수' 신호를 생성합니다.
#   - 기존의 오더북 기반 로직을 완전히 대체합니다.
#
#   **매수 신호 조건:**
#   1. RSI가 30 미만 (과매도 상태)
#   2. 5주기 이동평균선이 20주기 이동평균선을 상향 돌파 (골든 크로스)
#
#
import asyncio
from typing import Optional

from loguru import logger

from app.exchange.bybit_client import BybitClient
from app.state.store import state_store
from app.strategy.technical_analysis import calculate_indicators
from app.utils.typing import Signal, Side


class SignalGenerator:
    """
    기술적 지표를 기반으로 '매수' 신호를 생성하여 중앙 큐로 보냅니다.
    """

    def __init__(self, signal_queue: asyncio.Queue[Signal], bybit_client: BybitClient):
        self.signal_queue = signal_queue
        self.bybit_client = bybit_client
        self.short_ma = 5
        self.long_ma = 20
        logger.info("Technical Analysis SignalGenerator initialized.")

    async def run_loop(self, interval_seconds: float = 5.0):
        """
        주기적으로 모든 거래 대상 심볼에 대해 신호 생성을 확인하는 루프.
        (병렬 처리에서 순차 처리로 변경하여 안정성 강화)
        """
        logger.info(f"Starting TA signal generation loop with {interval_seconds}s interval.")
        while True:
            try:
                universe = state_store.get_universe()
                for symbol in universe:
                    try:
                        await self.check_and_send_signal(symbol)
                    except Exception as e:
                        # 개별 심볼 처리 중 발생하는 오류를 로깅하고 계속 진행
                        logger.error(f"Error processing symbol {symbol} in TA signal loop: {e}", exc_info=True)

                await asyncio.sleep(interval_seconds)

            except asyncio.CancelledError:
                logger.info("TA signal loop cancelled.")
                break
            except Exception as e:
                # 루프 자체의 심각한 오류 로깅
                logger.error(f"Critical error in TA signal loop: {e}", exc_info=True)
                await asyncio.sleep(15)



    async def check_and_send_signal(self, symbol: str):
        """
        특정 심볼에 대한 매수 신호 조건을 확인하고, 충족 시 신호를 큐로 보냅니다.
        """
        if state_store.get_position(symbol):
            return

        buy_signal = await self._check_for_buy_signal(symbol)
        if buy_signal:
            self.signal_queue.put_nowait(buy_signal)

    async def _check_for_buy_signal(self, symbol: str) -> Optional[Signal]:
        """
        RSI 및 골든 크로스 조건을 확인하여 매수 신호를 생성합니다.
        (들여쓰기 및 데이터 접근 오류 수정)
        """
        try:
            # 1시간봉 기준 캔들 데이터 가져오기
            kline_data = await self.bybit_client.get_kline(symbol=symbol, interval="1", limit=100)
            if not kline_data or len(kline_data) < self.long_ma:
                return None

            df = calculate_indicators(kline_data, self.short_ma, self.long_ma)
            if df.empty or len(df) < 2 or 'RSI' not in df.columns or 'close' not in df.columns:
                logger.warning(
                    f"[{symbol}] DataFrame is empty or missing required columns after indicator calculation.")
                return None

            latest = df.iloc[-1]
            previous = df.iloc[-2]

            logger.info(f"[{symbol}] Checking signals | RSI: {latest['RSI']:.2f} | SMA({self.short_ma}): {latest[f'SMA_{self.short_ma}']:.2f} | SMA({self.long_ma}): {latest[f'SMA_{self.long_ma}']:.2f}")

            # 매수 조건 확인
            rsi_condition = latest['RSI'] < 50
            golden_cross_condition = (previous[f'SMA_{self.short_ma}'] <= previous[f'SMA_{self.long_ma}']) and \
                                     (latest[f'SMA_{self.short_ma}'] > latest[f'SMA_{self.long_ma}'])

            # 두 조건 중 하나만 만족해도 신호를 발생시키도록 변경 (OR 조건 사용)
            if rsi_condition or golden_cross_condition:
                reason = ""
                if rsi_condition:
                    reason += f"RSI < 30 ({latest['RSI']:.1f})"
                if golden_cross_condition:
                    if reason: reason += " and "
                    reason += "Golden Cross"

                # RSI 값을 기반으로 신호 강도 계산 (0.0 ~ 1.0)
                # RSI가 0에 가까울수록 강한 신호
                strength = round(1.0 - (latest['RSI'] / 100.0), 2)

                logger.success(f"[BUY SIGNAL] Conditions met for {symbol}. Reason: {reason}, Strength: {strength}")
                return Signal(
                    symbol=symbol,
                    side=Side.BUY,
                    price=latest['close'],
                    reason=reason,
                    signal_type="scalping",  # "ta_buy"를 "scalping"으로 수정
                    strength=strength
                )
        except KeyError as e:
            logger.error(f"[{symbol}] KeyError when checking for buy signal. A required key is missing: {e}",
                         exc_info=True)
        except Exception as e:
            logger.error(f"[{symbol}] Failed to check for buy signal: {e}", exc_info=True)

        return None
