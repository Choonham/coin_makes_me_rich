# ===================================================================================
#   risk/engine.py: 리스크 관리 엔진 (최종 통합본)
# ===================================================================================
#
#   **핵심 변경사항:**
#   - 기존의 모든 기능(유동성 체크, 설정 로드, 동적 업데이트 등)을 완벽하게 보존했습니다.
#   - router.py와의 연동 오류를 해결하기 위해 누락되었던 `is_trade_allowed`와
#     `calculate_notional_size` 메서드를 추가했습니다.
#
#
import asyncio
from typing import Dict, List, Tuple

from loguru import logger

from app.config import settings
from app.state.models import RiskConfig
from app.state.store import state_store
from app.utils.typing import Side
from app.assets import load_universe
from app.exchange.bybit_client import BybitClient


class RiskEngine:
    """거래 리스크를 관리하고 검증하는 엔진"""

    def __init__(self, bybit_client: BybitClient):
        self.bybit_client = bybit_client
        self.config = self._load_config_from_settings()
        self.instrument_info: Dict[str, Dict] = {}
        self.universe: List[str] = []
        logger.info(f"RiskEngine initialized.")

    async def load_instrument_info(self):
        """Bybit에서 거래 상품 정보를 로드하여 리스크 엔진에 저장합니다."""
        logger.info("Loading instrument info from Bybit...")
        try:
            initial_universe = load_universe()
            info_list = await self.bybit_client.get_instruments_info(category='spot')
            logger.info(f"Received {len(info_list)} instruments from Bybit API.")

            liquid_symbols = []
            for item in info_list:
                symbol = item['symbol']
                if symbol in initial_universe:
                    # 유동성 체크 로직 (기존 코드 유지)
                    kline_data = await self.bybit_client.get_kline(symbol=symbol, interval="1", limit=15)
                    if not kline_data or len(kline_data) < 15:
                        continue

                    total_volume = sum(float(k[5]) for k in kline_data)
                    prices = [float(k[4]) for k in kline_data]

                    if total_volume == 0 or all(p == prices[0] for p in prices):
                        logger.warning(f"[{symbol}] Illiquid symbol detected. Skipping.")
                        continue

                    liquid_symbols.append(symbol)
                    self.instrument_info[symbol] = item.get('lotSizeFilter', {})

            self.universe = liquid_symbols
            # state_store가 최신 universe를 참조하도록 업데이트
            state_store._universe = self.universe
            logger.success(f"Successfully loaded instrument info for {len(self.instrument_info)} liquid symbols.")
        except Exception as e:
            logger.error(f"Failed to load instrument info: {e}", exc_info=True)
            raise

    def _load_config_from_settings(self) -> RiskConfig:
        """`settings` 객체로부터 리스크 설정을 로드하고, 새 전략에 맞게 TP/SL을 조정합니다."""
        return RiskConfig(
            day_loss_limit_usd=settings.DAY_LOSS_LIMIT_USD,
            day_profit_target_pct=settings.DAY_PROFIT_TARGET_PCT,
            risk_per_trade=settings.RISK_PER_TRADE, # .env 값을 사용 (필요시 0.1로 직접 설정 가능)
            max_active_symbols=settings.MAX_ACTIVE_SYMBOLS,
            max_slippage_bps=settings.MAX_SLIPPAGE_BPS,
            default_tp_bps=500,  # 익절: +5% (500 BPS)
            default_sl_bps=200,  # 손절: -2% (200 BPS)
            trailing_sl_bps=settings.TRAILING_SL_BPS,
            max_holding_time_seconds=settings.MAX_HOLDING_TIME_SECONDS
        )

    def get_config(self) -> RiskConfig:
        """현재 리스크 설정을 반환합니다."""
        return self.config

    def update_config(self, new_config: RiskConfig):
        """API를 통해 리스크 설정을 동적으로 업데이트합니다."""
        self.config = new_config
        logger.warning(f"Risk configuration updated via API: {self.config.model_dump_json()}")
        # state_store에도 변경된 설정을 즉시 반영
        asyncio.create_task(state_store.set_risk_config(self.config))

    def update_universe(self, new_universe: list[str]):
        """거래 대상 심볼 목록을 업데이트합니다."""
        self.universe = new_universe
        state_store._universe = self.universe
        logger.warning(f"Trading universe updated: {self.universe}")

    def is_globally_ok_to_trade(self) -> bool:
        """시스템 전체의 거래 가능 여부를 확인합니다 (손실 한도, 수익 목표 등)."""
        system_state = state_store.get_system_state()
        pnl_day = system_state.pnl_day
        total_equity = system_state.total_equity

        if pnl_day <= -self.config.day_loss_limit_usd:
            if system_state.status == "running":
                logger.critical(f"DAILY LOSS LIMIT REACHED! PnL: ${pnl_day:.2f}. Halting trades.")
            return False

        if total_equity > 0:
            profit_target_usd = total_equity * (self.config.day_profit_target_pct / 100)
            if pnl_day >= profit_target_usd:
                if system_state.status == "running":
                    logger.success(f"DAILY PROFIT TARGET REACHED! PnL: ${pnl_day:.2f}. Halting trades.")
                return False

        return True

    # --- [신규 추가] 거래 검증 및 계산 메서드 ---

    def is_trade_allowed(self, symbol: str, side: Side) -> Tuple[bool, str]:
        """
        특정 거래 신호에 대한 모든 리스크 규칙을 검증합니다.

        :return: (거래 허용 여부, 거부 사유)
        """
        if not self.is_globally_ok_to_trade():
            return False, "Global trading stop is active (e.g., daily loss limit)."

        state = state_store.get_system_state()

        if side == Side.BUY:
            # 최대 동시 보유 포지션 수 확인
            if len(state.held_symbols) >= self.config.max_active_symbols:
                # 이미 보유한 종목에 대한 추가 매수(물타기)가 아닌 신규 진입인 경우 차단
                if symbol not in state.held_symbols:
                    return False, f"Max active symbols limit reached ({self.config.max_active_symbols})."

        elif side == Side.SELL:
            # 판매할 자산이 실제로 있는지 확인
            if symbol not in state.held_symbols:
                return False, f"Attempted to sell {symbol} which is not held."

        return True, "Trade is allowed."

    def calculate_notional_size(self, symbol: str) -> float:
        """
        [전략 변경] 전체 자산의 10%를 거래 금액으로 계산합니다.

        :return: 거래에 사용할 USDT 금액
        """
        state = state_store.get_system_state()
        available_usdt = state.available_usdt_balance
        total_equity = state.total_equity

        if available_usdt < 20:  # 최소 주문 금액 등을 고려한 버퍼
            logger.warning(f"Not enough USDT to trade. Available: {available_usdt:.2f}")
            return 0.0

        # 전체 자산의 10%를 거래 금액으로 설정
        trade_value = total_equity * 0.10

        # 사용 가능한 USDT 잔고를 초과하지 않도록 조정
        notional_size = min(trade_value, available_usdt)

        MIN_ORDER_USDT = 10.0  # Bybit 현물 최소 주문 금액 (보수적으로 설정)
        if notional_size < MIN_ORDER_USDT:
            logger.warning(f"Calculated trade size (${notional_size:.2f}) is below minimum order size. Skipping trade.")
            return 0.0

        return round(notional_size, 2)