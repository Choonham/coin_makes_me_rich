# ===================================================================================
#   state/models.py: 상태 및 데이터베이스 모델 (최종 수정본)
# ===================================================================================
import time
from datetime import datetime
from typing import List, Optional, Dict

from pydantic import BaseModel, Field
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel

from app.utils.typing import TrendEvent
from app.utils.time import now


# --------------------------------------------------------------------------
# Pydantic 모델 (API 응답 및 인메모리 상태용)
# --------------------------------------------------------------------------

class RiskConfig(BaseModel):
    """리스크 관리 설정 모델"""
    # [정리됨] Field 안에 기본값을 넣어 중복 정의를 제거했습니다.
    day_loss_limit_usd: float = Field(250.0, gt=0, description="일일 손실 한도 (USD)")
    day_profit_target_pct: float = Field(5.0, gt=0, description="일일 목표 수익률 (%)")
    risk_per_trade: float = Field(0.005, gt=0, lt=1, description="거래당 리스크 비율 (0.005 = 0.5%)")
    max_active_symbols: int = Field(3, gt=0, description="최대 동시 보유 포지션 수")
    max_slippage_bps: int = Field(15, ge=0, description="최대 허용 슬리피지 (BPS)")
    default_tp_bps: int = Field(60, gt=0, description="기본 익절 BPS")
    default_sl_bps: int = Field(30, gt=0, description="기본 손절 BPS")
    trailing_sl_bps: int = Field(20, gt=0, description="추적 손절 BPS")
    max_holding_time_seconds: int = Field(300, gt=0, description="최대 포지션 보유 시간(초)")


class Position(BaseModel):
    """단일 포지션(자산) 상태 모델"""
    symbol: str
    quantity: float
    average_price: float

    # 상세 포지션 정보 (익절/손절/타임아웃 계산용)
    entry_price: float = 0.0
    entry_timestamp: float = Field(default_factory=time.time)
    last_update_timestamp: float = Field(default_factory=time.time)
    highest_price_since_entry: float = 0.0  # 진입 후 최고가 (추적 손절용)


class SystemState(BaseModel):
    """
    시스템의 현재 상태를 나타내는 모델. WebSocket을 통해 대시보드로 전송됩니다.
    """
    status: str = "stopped"
    pnl_day: float = 0.0
    pnl_day_pct: float = 0.0
    total_equity: float = 0.0
    available_balance: float = 0.0
    available_usdt_balance: float = 0.0
    unrealised_pnl: float = 0.0
    realized_pnl: float = 0.0

    active_positions: List[Position] = []
    held_symbols: List[str] = []
    orders: List[Dict] = []
    order_history: List[Dict] = []
    recent_trades: List[dict] = []
    recent_errors: List[str] = []
    trend_summary: List[TrendEvent] = []
    risk_config: Optional[RiskConfig] = None
    timestamp: datetime = Field(default_factory=now)


# --------------------------------------------------------------------------
# SQLModel 모델 (데이터베이스 테이블 정의용)
# --------------------------------------------------------------------------

class TradeLog(SQLModel, table=True):
    """체결된 거래 기록을 위한 DB 테이블"""
    id: Optional[int] = SQLField(default=None, primary_key=True)
    order_id: str = SQLField(index=True, alias="orderId")
    symbol: str = SQLField(index=True)
    side: str
    qty: float
    price: float
    fee: float
    timestamp: datetime = SQLField(default_factory=now, index=True)


class EventLog(SQLModel, table=True):
    """중요 시스템 이벤트 로그를 위한 DB 테이블"""
    id: Optional[int] = SQLField(default=None, primary_key=True)
    event_type: str = SQLField(index=True)
    details: str
    timestamp: datetime = SQLField(default_factory=now, index=True)


class TrendEventLog(SQLModel, table=True):
    """수집된 트렌드 이벤트 저장을 위한 DB 테이블"""
    id: Optional[int] = SQLField(default=None, primary_key=True)
    source: str
    symbol_raw: Optional[str] = None
    symbol_final: Optional[str] = SQLField(default=None, index=True)
    text: str
    url: Optional[str] = None
    author: Optional[str] = None
    score: Optional[float] = None
    confidence: Optional[float] = None
    timestamp: datetime = SQLField(index=True)