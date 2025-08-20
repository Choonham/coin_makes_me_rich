
# ===================================================================================
#   main.py: 애플리케이션 진입점 (Application Entrypoint)
# ===================================================================================
#
#   - FastAPI 애플리케이션 인스턴스 생성 및 설정
#   - API 라우터 등록
#   - 전역 의존성 주입 (Global Dependencies)
#   - 애플리케이션 생명주기 이벤트(startup/shutdown) 관리
#   - 핵심 서비스(Bybit 클라이언트, 전략 라우터 등) 초기화 및 관리
#
#   **주요 흐름:**
#   1.  애플리케이션 시작 시 (`startup` 이벤트):
#       - 설정 로드 및 로깅 초기화
#       - 데이터베이스 연결 및 테이블 생성
#       - Bybit REST/WebSocket 클라이언트, 트렌드 집계기 등 핵심 서비스 초기화
#       - WebSocket 대시보드 매니저 시작
#       - 백그라운드에서 주기적으로 상태 브로드캐스트하는 태스크 실행
#
#   2.  애플리케이션 실행 중:
#       - Uvicorn을 통해 HTTP/WebSocket 요청 처리
#       - API 엔드포인트를 통해 거래 시스템 제어 및 모니터링
#
#   3.  애플리케이션 종료 시 (`shutdown` 이벤트):
#       - 실행 중인 모든 백그라운드 태스크 정상 종료
#       - WebSocket 연결 등 리소스 정리
#
#
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from app.api import (routes_auth, routes_control, routes_orders, routes_public,
                   routes_signals)
from app.config import settings
from app.exchange.bybit_client import BybitClient
from app.log_config import configure_logging
from app.state.repo import db
from app.state.store import state_store
from app.strategy.router import StrategyRouter
from app.trend.aggregator import TrendAggregator
from app.ws.manager import WebSocketManager
from app.utils.time import get_seconds_until_next_day_utc

# --------------------------------------------------------------------------
# 백그라운드 태스크 (Background Tasks)
# --------------------------------------------------------------------------
async def daily_reset_task(bybit_client: BybitClient):
    """매일 자정(UTC)에 일일 상태를 리셋하는 백그라운드 태스크"""
    while True:
        seconds_until_midnight = get_seconds_until_next_day_utc()
        logger.info(f"Daily reset task sleeping for {seconds_until_midnight / 3600:.2f} hours until next UTC midnight.")
        await asyncio.sleep(seconds_until_midnight + 5) # 자정이 지난 후 5초 더 대기
        
        # 리셋 직전의 최신 자산 정보를 가져옴
        await state_store.update_wallet_balance(bybit_client)
        current_equity = state_store.get_system_state().total_equity
        
        # 새로운 시작 자산으로 리셋
        await state_store.reset_daily_state(current_equity)
        
        # 만약 봇이 멈춰있었다면 다시 시작
        router = app.state.strategy_router
        if not router.is_running():
            logger.info("Bot is not running. Restarting strategy after daily reset.")
            await router.start()

# --------------------------------------------------------------------------
# 애플리케이션 생명주기 관리 (Lifecycle Management)
# --------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI 애플리케이션의 시작과 종료 시점에 실행될 로직을 관리합니다.
    """
    # --- Startup ---
    print("Initializing application components...")
    configure_logging()
    await db.connect()
    
    bybit_client = BybitClient(
        api_key=settings.BYBIT_API_KEY,
        api_secret=settings.BYBIT_API_SECRET,
        testnet=settings.BYBIT_TESTNET
    )
    # trend_aggregator = TrendAggregator() # 뉴스/소셜 미디어 기반 트렌드 분석 비활성화
    from app.risk.engine import RiskEngine
    risk_engine = RiskEngine(bybit_client=bybit_client)
    await risk_engine.load_instrument_info()
    strategy_router = StrategyRouter(
        bybit_client=bybit_client, 
        trend_aggregator=None, # 비활성화
        risk_engine=risk_engine
    )
    ws_manager = WebSocketManager()

    app.state.bybit_client = bybit_client
    # app.state.trend_aggregator = trend_aggregator # 비활성화
    app.state.strategy_router = strategy_router
    app.state.ws_manager = ws_manager

    # 시작 시점의 자산 가치를 초기화
    await state_store.update_wallet_balance(bybit_client)
    initial_equity = state_store.get_system_state().total_equity
    await state_store.set_initial_equity(initial_equity)

    # 백그라운드 태스크 시작
    app.state.bybit_ws_task = asyncio.create_task(bybit_client.run_websockets())
    # app.state.trend_task = asyncio.create_task(trend_aggregator.run_connectors()) # 비활성화
    app.state.ws_broadcast_task = asyncio.create_task(ws_manager.broadcast_loop())
    app.state.wallet_balance_task = asyncio.create_task(state_store.update_wallet_balance_loop(bybit_client))
    app.state.order_history_task = asyncio.create_task(state_store.update_order_history_loop(bybit_client))
    app.state.daily_reset_task = asyncio.create_task(daily_reset_task(bybit_client))

    print("Application startup complete.")
    
    yield

    # --- Shutdown ---
    print("Shutting down application components...")
    
    await app.state.strategy_router.stop()
    tasks = [
        app.state.ws_broadcast_task, app.state.trend_task, app.state.bybit_ws_task,
        app.state.wallet_balance_task, app.state.order_history_task, app.state.daily_reset_task
    ]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    await bybit_client.close()
    await db.disconnect()
    print("Application shutdown complete.")

# --------------------------------------------------------------------------
# FastAPI 앱 생성 및 설정
# --------------------------------------------------------------------------
app = FastAPI(
    title="Crypto Scalping Bot API",
    description="Bybit V5 API를 활용한 실시간 트렌드 기반 스캘핑 봇",
    version="1.0.0",
    lifespan=lifespan,
    default_response_class=ORJSONResponse,  # 고성능 JSON 라이브러리 사용
)

# --------------------------------------------------------------------------
# 미들웨어 설정 (Middleware Configuration)
# --------------------------------------------------------------------------
# CORS (Cross-Origin Resource Sharing) 미들웨어: React Native 앱 등 다른 도메인에서의 요청을 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 프로덕션에서는 실제 앱의 도메인으로 제한하는 것이 안전합니다.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# Prometheus 메트릭 설정
# --------------------------------------------------------------------------
# /metrics 엔드포인트를 통해 애플리케이션의 주요 지표를 Prometheus가 수집할 수 있도록 노출합니다.
Instrumentator().instrument(app).expose(app)

# --------------------------------------------------------------------------
# API 라우터 등록 (Register API Routers)
# --------------------------------------------------------------------------
# 각 기능별로 분리된 라우터 파일을 포함시켜 API를 모듈화합니다.
app.include_router(routes_public.router, tags=["Public"], prefix="/api/v1/public")
app.include_router(routes_auth.router, tags=["Authentication"], prefix="/api/v1/auth")
app.include_router(routes_control.router, tags=["Control"], prefix="/api/v1/control")
app.include_router(routes_orders.router, tags=["Orders"], prefix="/api/v1/orders")
app.include_router(routes_signals.router, tags=["Signals"], prefix="/api/v1/signals")

# WebSocket 라우트를 직접 추가합니다.
@app.websocket("/ws/dashboard")
async def websocket_dashboard_endpoint(websocket: WebSocket):
    # app.state에 저장된 ws_manager를 통해 WebSocket 연결을 처리합니다.
    await websocket.app.state.ws_manager.handle_connection(websocket)

# --------------------------------------------------------------------------
# 최상위 엔드포인트 (Root Endpoint)
# --------------------------------------------------------------------------
@app.get("/", tags=["Root"])
async def read_root():
    """
    서버의 상태를 확인하기 위한 기본 엔드포인트입니다.
    """
    return {"message": "Crypto Scalping Bot API is running."}

# --------------------------------------------------------------------------
# Uvicorn으로 실행하기 위한 설정 (if __name__ == "__main__")
# --------------------------------------------------------------------------
# 이 파일을 직접 실행할 경우 Uvicorn 개발 서버를 구동합니다.
# 프로덕션 환경에서는 Gunicorn + Uvicorn 워커를 사용하는 것이 일반적입니다.
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True, # 개발 중 코드 변경 시 자동 재시작
        log_level="info"
    )
