
# ===================================================================================
#   tests/conftest.py: Pytest 설정 및 Fixture
# ===================================================================================
#
#   - Pytest 테스트 실행 시 공통적으로 사용될 설정, Fixture, Hook 등을 정의하는 파일입니다.
#   - 이 파일에 정의된 Fixture는 별도의 import 없이 테스트 함수에서 이름으로 바로 사용할 수 있습니다.
#
#   **주요 Fixture:**
#   - `test_app()`: 테스트용 FastAPI 애플리케이션 인스턴스를 생성합니다. 실제 앱의 `lifespan`
#     (startup/shutdown 이벤트)을 비활성화하고, 테스트에 필요한 모의(mock) 의존성을
#     주입하기 위해 사용됩니다.
#   - `client()`: `httpx.AsyncClient`를 사용하여 테스트용 앱과 통신할 수 있는 비동기
#     HTTP 클라이언트를 제공합니다. API 엔드포인트를 테스트하는 데 사용됩니다.
#
#   **테스트 환경 설정:**
#   - 테스트 실행 전, 환경 변수를 테스트에 맞게 오버라이드할 수 있습니다.
#     (예: `BYBIT_TESTNET=True`, `LOG_LEVEL="DEBUG"`)
#   - 실제 DB나 외부 API에 연결하는 대신, 모의 객체(MagicMock)를 사용하여 의존성을
#     격리시키는 것이 중요합니다. 이를 통해 테스트는 빠르고, 안정적이며, 독립적으로
#     실행될 수 있습니다.
#
#
import asyncio
from unittest.mock import MagicMock, AsyncMock

import pytest
from httpx import AsyncClient
from fastapi import FastAPI

# 테스트 세션 동안 이벤트 루프를 하나로 고정 (asyncio_mode = auto)
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop()
    yield loop
    loop.close()

@pytest.fixture
def test_app() -> FastAPI:
    """테스트용 FastAPI 앱 인스턴스를 생성합니다."""
    from app.main import app

    # 실제 startup/shutdown 로직(DB연결, Bybit 클라이언트 등)을 비활성화합니다.
    app.router.lifespan_context = MagicMock()

    # 의존성을 모의 객체로 대체합니다.
    app.state.bybit_client = AsyncMock()
    app.state.strategy_router = AsyncMock()
    app.state.ws_manager = AsyncMock()
    app.state.trend_aggregator = AsyncMock()

    return app

@pytest.fixture
async def client(test_app: FastAPI) -> AsyncClient:
    """
    테스트 앱과 통신하기 위한 비동기 HTTP 클라이언트를 생성합니다.
    """
    async with AsyncClient(app=test_app, base_url="http://test") as async_client:
        yield async_client
