
# ===================================================================================
#   api/routes_control.py: 시스템 제어 API 엔드포인트
# ===================================================================================
#
#   - JWT 인증이 필요한 시스템 제어 관련 API 라우트를 정의합니다.
#   - React Native 앱에서 관리자가 거래 봇을 원격으로 제어하는 데 사용됩니다.
#
#   **주요 엔드포인트:**
#   - `POST /control/start`: 거래 전략 실행을 시작합니다.
#   - `POST /control/stop`: 실행 중인 거래 전략을 중지합니다.
#   - `POST /config/risk`: 리스크 관리 파라미터를 동적으로 변경합니다.
#   - `POST /config/universe`: 거래 대상 심볼 목록(universe)을 동적으로 변경합니다.
#
#   **보안:**
#   - 모든 엔드포인트는 `get_current_user` 의존성을 통해 JWT 토큰을 검증하며,
#     유효한 토큰이 없는 경우 `401 Unauthorized` 에러를 반환합니다.
#
#
from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger

from app.api.routes_auth import get_current_user
from app.state.models import RiskConfig
from app.strategy.router import StrategyRouter

router = APIRouter()

# --------------------------------------------------------------------------
# 의존성 주입 함수 (Dependency Injection)
# --------------------------------------------------------------------------

def get_strategy_router(request: Request) -> StrategyRouter:
    """
    Request 객체에서 StrategyRouter 인스턴스를 가져옵니다.
    """
    return request.app.state.strategy_router

# --------------------------------------------------------------------------
# 제어 API 엔드포인트 정의
# --------------------------------------------------------------------------

@router.post("/start", status_code=status.HTTP_200_OK)
async def start_strategy(
    current_user: str = Depends(get_current_user),
    strategy_router: StrategyRouter = Depends(get_strategy_router)
):
    """
    ## 거래 전략 시작 (JWT 필요)
    
    시스템의 자동 거래 로직을 활성화합니다.
    이미 실행 중인 경우, 성공 메시지를 반환합니다.
    
    **RN 앱 연동 가이드:**
    - '시작' 버튼에 이 API 호출을 연결합니다.
    - 호출 시 `Authorization: Bearer <token>` 헤더를 반드시 포함해야 합니다.
    """
    logger.info(f"Attempting to start strategy by user '{current_user}'...")
    try:
        await strategy_router.start()
        return {"message": "Strategy started successfully."}
    except Exception as e:
        logger.error(f"Failed to start strategy: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not start strategy: {e}"
        )

@router.post("/stop", status_code=status.HTTP_200_OK)
async def stop_strategy(
    current_user: str = Depends(get_current_user),
    strategy_router: StrategyRouter = Depends(get_strategy_router)
):
    """
    ## 거래 전략 정지 (JWT 필요)
    
    실행 중인 자동 거래 로직을 안전하게 중지합니다.
    이미 중지된 상태인 경우, 성공 메시지를 반환합니다.
    
    **RN 앱 연동 가이드:**
    - '정지' 버튼에 이 API 호출을 연결합니다.
    - 호출 시 `Authorization: Bearer <token>` 헤더를 반드시 포함해야 합니다.
    """
    logger.info(f"Attempting to stop strategy by user '{current_user}'...")
    try:
        await strategy_router.stop()
        return {"message": "Strategy stopped successfully."}
    except Exception as e:
        logger.error(f"Failed to stop strategy: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not stop strategy: {e}"
        )

@router.post("/config/risk", status_code=status.HTTP_200_OK)
async def update_risk_config(
    risk_config: RiskConfig,
    current_user: str = Depends(get_current_user),
    strategy_router: StrategyRouter = Depends(get_strategy_router)
):
    """
    ## 리스크 설정 업데이트 (JWT 필요)
    
    시스템의 리스크 관리 파라미터를 실시간으로 변경합니다.
    
    **요청 본문 (Request Body):** `RiskConfig` 모델
    ```json
    {
      "day_loss_limit_usd": 250.0,
      "risk_per_trade": 0.005,
      "max_active_symbols": 3,
      "max_slippage_bps": 15,
      "default_tp_bps": 60,
      "default_sl_bps": 30,
      "trailing_sl_bps": 40,
       "default_tp_bps": 60,
      "default_sl_bps": 30,
      "trailing_sl_bps": 20,
      "max_holding_time_seconds": 300
    }
    ```
    
    **RN 앱 연동 가이드:**
    - 설정 화면에서 각 리스크 파라미터 값을 입력받아 이 API를 호출합니다.
    - 호출 시 `Authorization: Bearer <token>` 헤더를 반드시 포함해야 합니다.
    """
    logger.info(f"User '{current_user}' updating risk config to: {risk_config.model_dump_json()}")
    try:
        strategy_router.risk_engine.update_config(risk_config)
        return {"message": "Risk configuration updated successfully.", "new_config": risk_config}
    except Exception as e:
        logger.error(f"Failed to update risk config: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid risk configuration: {e}"
        )

@router.post("/config/universe", status_code=status.HTTP_200_OK)
async def update_universe(
    symbols: list[str],
    current_user: str = Depends(get_current_user),
    strategy_router: StrategyRouter = Depends(get_strategy_router)
):
    """
    ## 거래 대상 심볼(universe) 업데이트 (JWT 필요)
    
    자동 거래를 수행할 심볼의 화이트리스트를 변경합니다.
    
    **요청 본문 (Request Body):** 심볼 문자열의 배열
    ```json
    [
      "BTCUSDT",
      "ETHUSDT",
      "SOLUSDT"
    ]
    ```
    
    **RN 앱 연동 가이드:**
    - 거래를 허용할 코인 목록을 선택하는 UI를 통해 이 API를 호출합니다.
    - 호출 시 `Authorization: Bearer <token>` 헤더를 반드시 포함해야 합니다.
    """
    logger.info(f"User '{current_user}' updating universe to: {symbols}")
    try:
        # TODO: 입력된 심볼들이 Bybit에서 거래 가능한지 유효성 검사 로직 추가 필요
        strategy_router.risk_engine.update_universe(symbols)
        return {"message": "Universe updated successfully.", "new_universe": symbols}
    except Exception as e:
        logger.error(f"Failed to update universe: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid universe configuration: {e}"
        )
