
# ===================================================================================
#   utils/typing.py: 전역 타입 정의
# ===================================================================================
#
#   - 애플리케이션 전반에서 공통적으로 사용되는 Pydantic 모델 및 Enum 타입을 정의합니다.
#   - 타입 정의를 별도 파일로 분리함으로써 모듈 간의 순환 참조(circular import) 문제를
#     방지하고 코드의 명확성을 높입니다.
#
#   **주요 정의:**
#   - `Side (Enum)`: `exchange.models`에서 정의된 것을 재노출하여 다른 모듈에서 쉽게
#     가져다 쓸 수 있도록 합니다.
#   - `TrendEvent (Pydantic)`: 커넥터에서 수집되어 집계기(Aggregator)로 전달되는
#     원시 트렌드 데이터의 구조를 정의합니다. 파이프라인을 거치며 필드가 채워집니다.
#   - `Signal (Pydantic)`: 스캘핑 로직이나 트렌드 분석 파이프라인을 통해 생성된 최종
#     거래 신호의 구조를 정의합니다. `StrategyRouter`의 핵심 입력값입니다.
#
#
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# exchange.models에 정의된 Side를 여기서 다시 export하여 순환참조를 피합니다.
from app.exchange.models import Side

class TrendEvent(BaseModel):
    """
    커넥터가 수집하여 트렌드 분석 파이프라인을 통과하는 데이터 모델.
    단계별로 필드가 채워집니다.
    """
    # --- 초기 필드 (커넥터에서 생성) ---
    source: str = Field(..., description="데이터 소스 (예: X, NewsAPI)")
    text: str = Field(..., description="이벤트의 원본 텍스트 (트윗, 뉴스 제목 등)")
    url: Optional[str] = Field(None, description="원본 콘텐츠 링크")
    timestamp: datetime = Field(..., description="이벤트 발생 시간")
    symbol_raw: Optional[str] = Field(None, description="소스에서 태그된 원시 심볼 (예: $BTC)")
    author: Optional[str] = Field(None, description="작성자 정보")
    lang: Optional[str] = Field(None, description="언어 코드 (예: en, ko)")

    # --- 처리 후 채워지는 필드 ---
    # Mapper에 의해 채워짐
    symbol_final: Optional[str] = Field(None, description="시스템 공식 심볼 (예: BTCUSDT)")
    # Scorer에 의해 채워짐
    score: Optional[float] = Field(None, description="감성 점수 (-1.0 ~ 1.0)")
    confidence: Optional[float] = Field(None, description="점수의 신뢰도 (0.0 ~ 1.0)")

class Signal(BaseModel):
    """
    거래 실행을 위해 StrategyRouter로 전달되는 최종 신호 모델.
    """
    symbol: str = Field(..., description="거래 대상 심볼 (예: BTCUSDT)")
    side: Side = Field(..., description="거래 방향 (Buy/Sell)")
    price: float = Field(..., description="신호 발생 시점의 기준 가격")
    reason: str = Field(..., description="신호 발생 근거")
    strength: float = Field(..., description="신호 강도 (0.0 ~ 1.0)")
    signal_type: Literal["scalping", "trend", "combined"] = Field(..., description="신호 종류")
