# ===================================================================================
#   strategy/scalping.py: 스캘핑 신호 생성 로직 (최종 수정본)
# ===================================================================================
#
#   **핵심 변경사항:**
#   - `check_and_send_signal` 함수를 수정하여, 이제 이 모듈은 오직 '매수(진입)' 신호만
#     생성하도록 역할이 명확해졌습니다.
#   - 청산(매도) 신호 생성 로직은 제거되었으며, 모든 청산 결정은 StrategyRouter의
#     포지션 모니터링 루프(익절/손절/타임아웃)가 전담하게 됩니다.
#
#
import asyncio
from typing import Optional

from loguru import logger

from app.state.store import state_store
from app.utils.typing import Signal, Side


class ScalpingSignalGenerator:
    """
    오더북 데이터를 기반으로 스캘핑 '매수(진입)' 신호를 생성하여 중앙 큐로 보냅니다.
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
        logger.info(
            f"ScalpingSignalGenerator initialized with imbalance threshold: {imbalance_threshold}, depth: {depth}")

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
                await asyncio.sleep(5)  # 에러 발생 시 잠시 대기

    def check_and_send_signal(self, symbol: str):
        """
        [수정됨] 이제 이 함수는 오직 '매수(진입)' 신호만 생성합니다.
        청산(매도)은 StrategyRouter의 포지션 모니터링 루프가 전담합니다.
        """
        base_currency = symbol.replace("USDT", "")
        balance = state_store.get_balance(base_currency)

        # 10달러 이상의 의미있는 자산을 보유하고 있다면, 새로운 매수 신호를 생성하지 않음
        if balance and balance.wallet_balance > 0:
            orderbook = state_store.get_orderbook(symbol)
            if orderbook and orderbook.get('b') and orderbook['b']:
                current_price = float(orderbook['b'][0][0])
                asset_value_usd = balance.wallet_balance * current_price
                if asset_value_usd >= 10.0:
                    return  # 자산 보유 중이므로 진입 신호 생성 안함

        # 자산을 보유하고 있지 않을 때만 매수 신호를 확인
        buy_signal = self._check_for_buy_signal(symbol)
        if buy_signal:
            self.signal_queue.put_nowait(buy_signal)

    def _check_for_buy_signal(self, symbol: str) -> Optional[Signal]:
        """
        진입(매수) 신호만 확인합니다. (기존 코드와 동일)
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

    # _check_for_exit_signal 메서드는 이제 호출되지 않으므로, 삭제하거나 비활성화합니다.
    # def _check_for_exit_signal(self, symbol: str) -> Optional[Signal]:
    #     """
    #     [비활성화됨] 이 메서드는 더 이상 사용되지 않습니다.
    #     """
    #     base_currency = symbol.replace("USDT", "")
    #     balance = state_store.get_balance(base_currency)

    #     if not balance or balance.wallet_balance <= 0:
    #         return None

    #     orderbook = state_store.get_orderbook(symbol)
    #     if not orderbook or not orderbook.get('b') or not orderbook.get('a'):
    #         return None

    #     bids = orderbook.get('b', [])
    #     asks = orderbook.get('a', [])
    #     if len(bids) < self.depth or len(asks) < self.depth:
    #         return None

    #     total_bid_size = sum(float(bid[1]) for bid in bids[:self.depth])
    #     total_ask_size = sum(float(ask[1]) for ask in asks[:self.depth])
    #     total_liquidity = total_bid_size + total_ask_size
    #     if total_liquidity == 0:
    #         return None

    #     bid_pressure_ratio = total_bid_size / total_liquidity
    #     best_bid_price = float(bids[0][0])

    #     if bid_pressure_ratio < 0.5: # 기존의 완화된 조건
    #         logger.info(f"[EXIT SIGNAL] Reversal for owned {symbol}. Creating SELL signal.")
    #         return Signal(
    #             symbol=symbol, side=Side.SELL, price=best_bid_price,
    #             reason="Orderbook imbalance reversal (strong ask pressure)",
    #             signal_type="scalping_exit"
    #         )
    #     return None