
# ===================================================================================
#   api/routes_signals.py: 트렌드 신호 조회 API 엔드포인트
# ===================================================================================
#
#   - 시스템이 수집하고 처리한 트렌드 관련 데이터를 조회하는 API 라우트를 정의합니다.
#   - React Native 앱에서 어떤 트렌드가 감지되고 있는지 시각화하거나, 디버깅 및
#     분석 목적으로 사용됩니다.
#
#   **주요 엔드포인트:**
#   - `GET /signals/trend`: 최근에 수집된 트렌드 이벤트 목록을 페이지네이션하여 반환합니다.
#
#
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.state.store import state_store
from app.trend.aggregator import TrendEvent

router = APIRouter()

# --------------------------------------------------------------------------
# Pydantic 모델 정의
# --------------------------------------------------------------------------

class PaginatedTrendEvents(BaseModel):
    """페이지네이션된 트렌드 이벤트 응답 모델"""
    total: int
    page: int
    page_size: int
    events: list[TrendEvent]

# --------------------------------------------------------------------------
# 신호 조회 API 엔드포인트 정의
# --------------------------------------------------------------------------

@router.get("/trend", response_model=PaginatedTrendEvents)
async def get_trend_signals(
    page: int = Query(1, ge=1, description="페이지 번호"),
    page_size: int = Query(20, ge=1, le=100, description="페이지 당 항목 수")
):
    """
    ## 최근 트렌드 이벤트 목록 조회
    
    시스템이 다양한 커넥터(X, News 등)를 통해 수집하고 점수화한 트렌드 이벤트의
    목록을 최신순으로 반환합니다.
    
    **RN 앱 연동 가이드:**
    - 트렌드 피드 화면에서 이 API를 호출하여 최신 소셜/뉴스 동향을 표시합니다.
    - 스크롤到底(scroll-to-bottom) 시 다음 페이지를 요청하는 방식으로 무한 스크롤을 구현할 수 있습니다.
    
    **쿼리 파라미터:**
    - `page`: 조회할 페이지 번호 (기본값: 1)
    - `page_size`: 한 페이지에 포함할 이벤트 수 (기본값: 20, 최대: 100)
    """
    events = state_store.get_recent_trend_events()
    
    # 페이지네이션 로직
    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    paginated_events = events[start_index:end_index]
    
    return {
        "total": len(events),
        "page": page,
        "page_size": page_size,
        "events": paginated_events
    }
