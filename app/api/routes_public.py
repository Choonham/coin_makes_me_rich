
# ===================================================================================
#   api/routes_public.py: 공개 API 엔드포인트
# ===================================================================================
#
#   - 인증이 필요 없는 공개적으로 접근 가능한 API 라우트를 정의합니다.
#   - 주로 시스템의 현재 상태, 거래 가능한 심볼 목록, 기본 정보 등을 제공합니다.
#   - React Native 모니터링 대시보드의 초기 데이터 로딩에 사용됩니다.
#
#   **주요 엔드포인트:**
#   - `GET /status`: 시스템의 전반적인 상태를 반환합니다.
#     - 현재 실행 상태 (running, stopped)
#     - 일일 손익 (pnl_day)
#     - 현재 보유 포지션 (positions)
#     - 최근 거래 내역 (last_trades)
#     - 발생한 에러 (errors)
#     - 트렌드 요약 (trend_summary)
#
#   - `GET /symbols`: 거래가 허용된 심볼(universe) 목록을 반환합니다.
#
#   - `GET /config/risk`: 현재 적용된 리스크 관리 설정을 반환합니다.
#
#
from fastapi import APIRouter, Depends, Request
from app.state.store import state_store, SystemState
from app.state.models import RiskConfig
from app.strategy.router import StrategyRouter

router = APIRouter()

# --------------------------------------------------------------------------
# 의존성 주입 함수 (Dependency Injection)
# --------------------------------------------------------------------------

def get_strategy_router(request: Request) -> StrategyRouter:
    """
    Request 객체에서 StrategyRouter 인스턴스를 가져옵니다.
    main.py의 startup에서 app.state에 저장된 인스턴스를 사용합니다.
    """
    return request.app.state.strategy_router

# --------------------------------------------------------------------------
# 공개 API 엔드포인트 정의
# --------------------------------------------------------------------------

@router.get("/status", response_model=SystemState)
async def get_system_status():
    """
    ## 시스템 전체 상태 조회
    
    React Native 대시보드에서 폴링(polling)하여 시스템의 주요 상태 정보를
    실시간으로 업데이트하는 데 사용됩니다. WebSocket으로도 동일한 정보가
    스트리밍되지만, 초기 로딩이나 연결 재설정 시 이 엔드포인트를 사용할 수 있습니다.
    
    **반환 모델:** `SystemState`
    - `status`: 현재 시스템 운영 상태 (`running` | `stopped` | `error`)
    - `pnl_day`: 당일 실현 손익 (USD)
    - `active_positions`: 현재 보유 중인 포지션 목록
    - `recent_trades`: 최근 체결된 거래 목록
    - `recent_errors`: 최근 발생한 오류 메시지 목록
    - `trend_summary`: 최근 감지된 트렌드 이벤트 요약
    - `risk_config`: 현재 적용 중인 리스크 설정
    """
    return state_store.get_system_state()

@router.get("/symbols", response_model=list[str])
async def get_universe_symbols(strategy_router: StrategyRouter = Depends(get_strategy_router)):
    """
    ## 거래 가능 심볼 목록 조회
    
    현재 시스템에서 거래 대상으로 설정된 심볼(universe)의 전체 목록을 반환합니다.
    RN 앱에서 수동 주문 등을 위한 심볼 선택 UI에 사용될 수 있습니다.
    """
    return strategy_router.risk_engine.universe

@router.get("/config/risk", response_model=RiskConfig)
async def get_risk_config(strategy_router: StrategyRouter = Depends(get_strategy_router)):
    """
    ## 현재 리스크 설정 조회
    
    현재 시스템에 적용되고 있는 리스크 관리 파라미터를 반환합니다.
    
    **반환 모델:** `RiskConfig`
    - `day_loss_limit_usd`: 일일 손실 한도 (USD)
    - `risk_per_trade`: 거래당 리스크 비율
    - `max_active_symbols`: 최대 동시 포지션 수
    - `max_slippage_bps`: 최대 허용 슬리피지 (BPS)
    - `default_tp_bps`: 기본 익절 BPS
    - `default_sl_bps`: 기본 손절 BPS
    - `trailing_sl_bps`: 추적 손절 BPS
    """
    return strategy_router.risk_engine.get_config()
