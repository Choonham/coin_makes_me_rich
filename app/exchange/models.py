
# ===================================================================================
#   exchange/models.py: 거래소 데이터 모델
# ===================================================================================
#
#   - Bybit API와 통신하며 사용되는 데이터 객체들을 Pydantic 모델로 정의합니다.
#   - API 응답을 파싱하고, 애플리케이션 내부에서 데이터를 일관된 형식으로 다루기 위해 사용됩니다.
#   - 타입 안정성을 보장하고, 데이터 유효성 검사를 용이하게 합니다.
#
#   **주요 모델:**
#   - `Side`: 매수(Buy), 매도(Sell)를 나타내는 Enum.
#   - `OrderStatus`: 주문의 상태(신규, 체결, 취소 등)를 나타내는 Enum.
#   - `Order`: 주문 정보를 담는 모델.
#   - `Fill`: 체결 정보를 담는 모델.
#   - `Position`: 현재 보유 포지션 정보를 담는 모델.
#   - `PnL`: 손익 정보를 담는 모델.
#   - `BybitAPIResponse`: Bybit API의 표준 응답 형식을 감싸는 제네릭 모델.
#
#
from pydantic import BaseModel, Field, field_validator
from enum import Enum
from datetime import datetime
from typing import TypeVar, Generic, List, Optional

# --------------------------------------------------------------------------
# 열거형 (Enums)
# --------------------------------------------------------------------------

class Side(str, Enum):
    """주문 방향 (매수/매도)"""
    BUY = "Buy"
    SELL = "Sell"

class OrderStatus(str, Enum):
    """Bybit 주문 상태"""
    CREATED = "Created"                 # 생성됨 (API 수신)
    NEW = "New"                         # 신규 주문 (주문서 등록)
    PARTIALLY_FILLED = "PartiallyFilled"  # 부분 체결
    FILLED = "Filled"                   # 전체 체결
    CANCELLED = "Cancelled"               # 취소됨
    REJECTED = "Rejected"                 # 거절됨
    UNTRIGGERED = "Untriggered"           # 트리거 대기 중 (조건부 주문)
    DEACTIVATED = "Deactivated"           # 비활성화됨
    PARTIALLY_FILLED_CANCELED = "PartiallyFilledCanceled" # 부분 체결 후 취소


# --------------------------------------------------------------------------
# 핵심 데이터 모델 (Core Data Models)
# --------------------------------------------------------------------------

class Order(BaseModel):
    """주문 정보 모델"""
    order_id: str = Field(..., alias="orderId", description="주문 ID")
    symbol: str = Field(..., description="심볼 (예: BTCUSDT)")
    side: Side
    order_type: str = Field(..., alias="orderType", description="주문 유형 (예: Market, Limit)")
    price: float = Field(0.0, description="주문 가격 (시장가는 0)")
    qty: float = Field(..., description="주문 수량")
    status: OrderStatus = Field(..., alias="orderStatus", description="주문 상태")
    created_time: str = Field(..., alias="createdTime", description="생성 시간 (ms timestamp)")
    updated_time: str = Field(..., alias="updatedTime", description="업데이트 시간 (ms timestamp)")
    # 추가: 주문이 포지션 개시(open)인지 청산(close)인지 나타내는 필드
    trade_type: Optional[str] = Field(None, description="거래 유형 (Open Buy, Open Sell, Close Buy, Close Sell)")
    # Bybit API 응답에서 reduceOnly 필드를 가져오기 위해 추가
    reduce_only: Optional[bool] = Field(None, alias="reduceOnly", description="포지션 축소 전용 주문 여부")

class Fill(BaseModel):
    """체결 정보 모델"""
    exec_id: str = Field(..., alias="execId", description="체결 ID")
    order_id: str = Field(..., alias="orderId", description="주문 ID")
    symbol: str
    side: Side
    exec_price: float = Field(..., alias="execPrice", description="체결 가격")
    exec_qty: float = Field(..., alias="execQty", description="체결 수량")
    exec_fee: float = Field(..., alias="execFee", description="거래 수수료")
    exec_time: datetime = Field(..., alias="execTime", description="체결 시간")

class PnL(BaseModel):
    """손익 정보 모델"""
    symbol: str
    realised_pnl: float = Field(..., alias="realisedPnl", description="실현 손익")
    unrealised_pnl: float = Field(..., alias="unrealisedPnl", description="미실현 손익")

class CoinBalance(BaseModel):
    """개별 코인 잔고 정보 모델"""
    coin: str = Field(..., description="코인 이름 (e.g., BTC, USDT)")
    equity: float = Field(..., description="자산 가치")
    usd_value: float = Field(..., alias="usdValue", description="USD 환산 가치")
    wallet_balance: float = Field(..., alias="walletBalance", description="지갑 잔고 (총 수량)")
    available_to_borrow: Optional[float] = Field(0.0, alias="availableToBorrow", description="차입 가능 금액")
    available_to_withdraw: Optional[float] = Field(0.0, alias="availableToWithdraw", description="출금 가능 수량")
    accrued_interest: Optional[float] = Field(None, alias="accruedInterest", description="누적 이자")

    @field_validator('available_to_borrow', 'available_to_withdraw', 'accrued_interest', mode='before')
    @classmethod
    def empty_str_to_none(cls, v):
        if v == '':
            return None
        return v

# --------------------------------------------------------------------------
# API 응답 래퍼 모델 (API Response Wrapper)
# --------------------------------------------------------------------------

# 제네릭 타입을 위한 TypeVar 정의
T = TypeVar('T')

class BybitAPIResponse(BaseModel, Generic[T]):
    """Bybit V5 API의 표준 응답 형식을 위한 제네릭 모델"""
    ret_code: int = Field(..., alias="retCode", description="결과 코드 (0이면 성공)")
    ret_msg: str = Field(..., alias="retMsg", description="결과 메시지")
    result: Optional[T] = None
    ret_ext_info: dict = Field({}, alias="retExtInfo", description="추가 정보")
    time: datetime
