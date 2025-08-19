# ===================================================================================
#   state/models.py: 상태 및 데이터베이스 모델
# ===================================================================================
#
#   - 애플리케이션의 상태와 데이터베이스에 저장될 데이터를 위한 모델을 정의합니다.
#   - Pydantic 모델은 API 응답, 내부 데이터 구조, 실시간 상태 표현에 사용됩니다.
#   - SQLModel (또는 SQLAlchemy) 모델은 데이터베이스 테이블 스키마를 정의하고,
#     ORM(Object-Relational Mapping)을 통해 데이터베이스 작업을 객체 지향적으로
#     처리할 수 있게 합니다.
#
#   **주요 모델:**
#   - **SystemState (Pydantic)**: RN 대시보드에 전송될 실시간 시스템 상태를 나타내는 모델.
#     현재 PnL, 포지션, 에러 등 모든 휘발성 상태를 포함합니다.
#   - **TradeLog (SQLModel)**: 체결된 모든 거래를 데이터베이스에 기록하기 위한 테이블 모델.
#   - **EventLog (SQLModel)**: 중요한 시스템 이벤트(시작, 중지, 에러)를 기록하기 위한 모델.
#   - **TrendEventLog (SQLModel)**: 수집된 모든 트렌드 이벤트를 영구 저장하기 위한 모델.
#
#   **SQLModel 사용 이유:**
#   - Pydantic과 SQLAlchemy의 장점을 결합한 라이브러리입니다.
#   - 하나의 모델 정의로 데이터 유효성 검사(Pydantic)와 데이터베이스 ORM(SQLAlchemy)을
#     동시에 처리할 수 있어 코드 중복을 줄이고 생산성을 높입니다.
#
#
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
    day_loss_limit_usd: float = Field(..., gt=0, description="일일 손실 한도 (USD)")
    day_profit_target_pct: float = Field(..., gt=0, description="일일 목표 수익률 (%)")
    risk_per_trade: float = Field(..., gt=0, lt=1, description="거래당 리스크 비율 (0.01 = 1%)")
    max_active_symbols: int = Field(..., gt=0, description="최대 동시 보유 포지션 수")
    max_slippage_bps: int = Field(..., ge=0, description="최대 허용 슬리피지 (BPS)")
    default_tp_bps: int = Field(..., gt=0, description="기본 익절 BPS")
    default_sl_bps: int = Field(..., gt=0, description="기본 손절 BPS")
    trailing_sl_bps: int = Field(..., gt=0, description="추적 손절 BPS")

class SystemState(BaseModel):
    """
    시스템의 현재 상태를 나타내는 모델. WebSocket을 통해 대시보드로 전송됩니다.
    """
    status: str = "stopped"
    pnl_day: float = 0.0
    pnl_day_pct: float = 0.0 # 일일 수익률
    total_equity: float = 0.0
    available_balance: float = 0.0
    available_usdt_balance: float = 0.0
    unrealised_pnl: float = 0.0
    realized_pnl: float = 0.0
    active_positions: List[Dict] = [] # 선물 포지션 대신 보유 자산 목록을 넣을 수 있음 (향후 확장)
    held_symbols: List[str] = [] # 현재 보유 중인 코인 심볼 목록
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
# SQLModel은 Pydantic 모델처럼 사용하면서 데이터베이스 테이블 스키마를 정의할 수 있습니다.

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
    event_type: str = SQLField(index=True) # e.g., "START", "STOP", "ERROR"
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