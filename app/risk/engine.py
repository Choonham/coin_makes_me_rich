# ===================================================================================
#   risk/engine.py: 리스크 관리 엔진
# ===================================================================================
#
#   - 거래 시스템의 안정성을 보장하기 위한 모든 리스크 관리 규칙을 중앙에서
#     처리하고 강제하는 핵심 컴포넌트입니다.
#   - 거래 실행 전, 모든 거래는 이 엔진의 검증을 통과해야 합니다.
#
#   **주요 기능:**
#   - **설정 기반 초기화**: `config.py`에서 정의된 리스크 파라미터로 초기화됩니다.
#   - **동적 설정 변경**: API를 통해 실시간으로 리스크 설정을 업데이트할 수 있습니다 (`update_config`).
#   - **글로벌 리스크 관리**: 
#     - **일일 손실 한도 (Day Loss Cut)**: `state_store`에서 일일 PnL을 감시하여, 설정된 한도를 초과하면 모든 신규 거래를 차단합니다.
#   - **개별 거래 검증 (`validate_trade`)**:
#     - **거래 허용 심볼(Universe) 검사**: 거래하려는 심볼이 화이트리스트에 있는지 확인합니다.
#     - **최대 포지션 수 검사**: 현재 보유한 포지션 수가 최대치를 초과하지 않는지 확인합니다.
#     - **거래 수량 계산**: 계좌 잔고, 레버리지, `risk_per_trade` 설정을 바탕으로 진입할 포지션의 규모(qty)를 자동으로 계산합니다.
#     - **슬리피지 검사**: 신호 발생 시점의 가격과 현재 시장가(오더북 기준)를 비교하여 슬리피지가 허용 범위를 초과하는지 확인합니다.
#
#   **데이터 흐름:**
#   1. `StrategyRouter`가 거래 신호를 감지하면, `validate_trade(signal)`를 호출합니다.
#   2. `validate_trade`는 순차적으로 모든 리스크 규칙을 검사합니다.
#   3. 모든 검사를 통과하면, 계산된 주문 수량(qty)과 함께 거래 허용(`allow=True`)을 반환합니다.
#   4. 하나라도 실패하면, 거래 불허(`allow=False`)와 함께 거절 사유를 반환합니다.
#
#
import math
from decimal import Decimal, ROUND_DOWN
from typing import Dict

from pydantic import BaseModel, Field
from loguru import logger

from app.config import settings
from app.state.models import RiskConfig
from app.state.store import state_store
from app.utils.typing import Signal, Side
from app.assets import load_universe
from app.exchange.bybit_client import BybitClient

# --------------------------------------------------------------------------
# Pydantic 모델 정의
# --------------------------------------------------------------------------

class TradeDecision(BaseModel):
    """거래 검증 결과 모델"""
    allow: bool = Field(..., description="거래 허용 여부")
    reason: str | None = Field(None, description="거래 거절 사유")
    qty: float = Field(0.0, description="계산된 주문 수량")
    tp_price: float | None = Field(None, description="계산된 익절 가격")
    sl_price: float | None = Field(None, description="계산된 손절 가격")


# --------------------------------------------------------------------------
# 리스크 엔진 클래스
# --------------------------------------------------------------------------

class RiskEngine:
    """거래 리스크를 관리하고 검증하는 엔진"""

    def __init__(self, bybit_client: BybitClient):
        self.bybit_client = bybit_client
        self.config = self._load_config_from_settings()
        self.instrument_info: Dict[str, Dict] = {}
        self._min_notional_map: Dict[str, float] = {
            "BTCUSDT": 100.0,
            "ETHUSDT": 20.0,
            # Add other symbols as needed, or a default for others
        }
        logger.info(f"RiskEngine initialized with config: {self.config.model_dump_json()}")
        # self.universe will be loaded and filtered in load_instrument_info
        self.universe: List[str] = [] # Initialize as empty list
        logger.info("RiskEngine initialized. Universe will be loaded.")

    async def load_instrument_info(self):
        """Bybit에서 거래 상품 정보를 로드하여 리스크 엔진에 저장합니다."""
        logger.info("Loading instrument info from Bybit...")
        try:
            initial_universe = load_universe() # Load initial universe here
            info_list = await self.bybit_client.get_instruments_info(category='spot')
            logger.info(f"Received {len(info_list)} instruments from Bybit API.")
            
            # Filter out illiquid symbols based on kline data
            liquid_symbols = []
            for item in info_list:
                symbol = item['symbol']
                # Only check symbols that are in our trading universe initially
                if symbol in initial_universe: # Check against initial_universe
                    kline_data = await self.bybit_client.get_kline(symbol=symbol, interval="60", limit=15)
                    
                    if not kline_data or len(kline_data) < 15: # Not enough data
                        logger.warning(f"[{symbol}] Insufficient kline data for liquidity check. Skipping.")
                        continue
                    
                    # Check for zero volume or unchanging prices
                    total_volume = sum(float(k[5]) for k in kline_data) # k[5] is volume
                    prices = [float(k[4]) for k in kline_data] # k[4] is close price
                    
                    if total_volume == 0:
                        logger.warning(f"[{symbol}] Zero volume in kline data. Skipping due to illiquidity.")
                        continue
                    
                    if all(p == prices[0] for p in prices):
                        logger.warning(f"[{symbol}] Unchanging prices in kline data. Skipping due to illiquidity.")
                        continue
                        
                    liquid_symbols.append(symbol)
                    self.instrument_info[symbol] = item['lotSizeFilter']

            self.universe = liquid_symbols # Update universe to only include liquid symbols
            logger.success(f"Successfully loaded instrument info for {len(self.instrument_info)} symbols. Liquid symbols in universe: {len(self.universe)}")
            logger.debug(f"Loaded liquid symbols: {self.universe}")
        except Exception as e:
            logger.error(f"Failed to load instrument info: {e}", exc_info=True)
            raise

    def _load_config_from_settings(self) -> RiskConfig:
        """`settings` 객체로부터 리스크 설정을 로드합니다."""
        return RiskConfig(
            day_loss_limit_usd=settings.DAY_LOSS_LIMIT_USD,
            day_profit_target_pct=settings.DAY_PROFIT_TARGET_PCT,
            risk_per_trade=settings.RISK_PER_TRADE,
            max_active_symbols=settings.MAX_ACTIVE_SYMBOLS,
            max_slippage_bps=settings.MAX_SLIPPAGE_BPS,
            default_tp_bps=settings.DEFAULT_TP_BPS,
            default_sl_bps=settings.DEFAULT_SL_BPS,
            trailing_sl_bps=settings.TRAILING_SL_BPS,
        )

    def get_config(self) -> RiskConfig:
        """현재 리스크 설정을 반환합니다."""
        return self.config

    def update_config(self, new_config: RiskConfig):
        """API를 통해 리스크 설정을 동적으로 업데이트합니다."""
        self.config = new_config
        logger.warning(f"Risk configuration updated: {self.config.model_dump_json()}")
        state_store.set_risk_config(self.config)

    def update_universe(self, new_universe: list[str]):
        """거래 대상 심볼 목록을 업데이트합니다."""
        self.universe = new_universe
        logger.warning(f"Trading universe updated: {self.universe}")
        # TODO: Universe 변경 시 Bybit WS 구독도 변경해주는 로직 필요

    def get_price_scale(self, symbol: str) -> int:
        """심볼에 대한 가격 소수점 자릿수를 반환합니다."""
        instrument_info = self.instrument_info.get(symbol)
        if not instrument_info:
            logger.warning(f"No instrument info for {symbol}, defaulting price scale to 5.")
            return 5
        return int(instrument_info.get('priceScale', 5))

    def is_globally_ok_to_trade(self) -> bool:
        """시스템 전체의 거래 가능 여부를 확인합니다 (손실 한도, 수익 목표 등)."""
        system_state = state_store.get_system_state()
        pnl_day = system_state.pnl_day
        total_equity = system_state.total_equity

        # 1. 일일 손실 한도 확인
        if pnl_day <= -self.config.day_loss_limit_usd:
            if system_state.status == "running":
                logger.critical(f"DAILY LOSS LIMIT REACHED! PnL: ${pnl_day:.2f}. Halting all new trades.")
                state_store.add_error("DAILY LOSS LIMIT REACHED!")
            return False

        # 2. 일일 목표 수익 확인
        if total_equity > 0:
            profit_target_usd = total_equity * (self.config.day_profit_target_pct / 100)
            if pnl_day >= profit_target_usd:
                if system_state.status == "running":
                    logger.success(f"DAILY PROFIT TARGET REACHED! PnL: ${pnl_day:.2f}. Halting all new trades for the day.")
                    state_store.add_error("DAILY PROFIT TARGET REACHED!")
                return False
        
        return True