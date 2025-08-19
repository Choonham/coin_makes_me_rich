
# ===================================================================================
#   tests/test_api.py: API 엔드포인트 단위 테스트
# ===================================================================================
#
#   - `httpx.AsyncClient`를 사용하여 FastAPI 애플리케이션의 API 엔드포인트를 테스트합니다.
#   - 각 테스트는 독립적으로 실행되어야 하며, 외부 서비스(DB, Bybit API)에 의존하지
#     않도록 모의(mock) 객체를 사용합니다.
#
#   **테스트 대상:**
#   - 공개 엔드포인트 (`/api/v1/public/...`):
#     - `GET /status`: 정상적인 상태 응답(200 OK)과 `SystemState` 모델에 맞는 JSON 본문을 반환하는지 확인합니다.
#     - `GET /symbols`: 심볼 목록을 정상적으로 반환하는지 확인합니다.
#   - 인증 및 제어 엔드포인트 (`/api/v1/auth/...`, `/api/v1/control/...`):
#     - `POST /auth/login`: 유효한 자격증명으로 JWT 토큰을 발급하는지, 유효하지 않은 자격증명은 거부하는지 테스트합니다.
#     - `POST /control/start`: 인증된 요청에 대해 200 OK를 반환하는지, 인증되지 않은 요청은 401 Unauthorized를 반환하는지 테스트합니다.
#
#   **테스트 방법:**
#   - `client` Fixture를 사용하여 테스트 클라이언트를 주입받습니다.
#   - `client.get()`, `client.post()` 등으로 API를 호출합니다.
#   - `assert response.status_code == ...`로 HTTP 상태 코드를 검증합니다.
#   - `assert response.json() == ...`로 응답 본문의 내용을 검증합니다.
#   - `monkeypatch`나 `unittest.mock`을 사용하여 의존성(서비스, 함수)을 모의 객체로 대체합니다.
#
#
import pytest
from httpx import AsyncClient
from unittest.mock import patch, MagicMock

from app.state.models import SystemState
from app.config import settings

# Pytest가 비동기 함수를 테스트하도록 마킹
pytestmark = pytest.mark.asyncio

async def test_read_root(client: AsyncClient):
    """루트 엔드포인트 GET / 테스트"""
    response = await client.get("/")
    assert response.status_code == 200
    assert response.json() == {"message": "Crypto Scalping Bot API is running."}

async def test_get_system_status(client: AsyncClient):
    """공개 상태 엔드포인트 GET /api/v1/public/status 테스트"""
    # state_store.get_system_state가 반환할 모의 데이터 생성
    mock_state = SystemState(status="testing", pnl_day=123.45)
    
    # state_store.get_system_state 함수를 모의 객체로 패치
    with patch("app.api.routes_public.state_store.get_system_state", return_value=mock_state) as mock_get:
        response = await client.get("/api/v1/public/status")
        assert response.status_code == 200
        # 반환된 JSON이 Pydantic 모델을 통해 직렬화된 결과와 일치하는지 확인
        assert response.json() == mock_state.model_dump(mode='json')
        mock_get.assert_called_once()

async def test_login_for_access_token(client: AsyncClient):
    """인증 엔드포인트 POST /api/v1/auth/login 테스트"""
    # passlib의 verify 함수를 항상 True를 반환하도록 패치
    with patch("app.api.routes_auth.verify_password", return_value=True):
        login_data = {"username": settings.ADMIN_USER, "password": "admin"}
        response = await client.post("/api/v1/auth/login", data=login_data)
        
        assert response.status_code == 200
        json_response = response.json()
        assert "access_token" in json_response
        assert json_response["token_type"] == "bearer"

async def test_login_invalid_credentials(client: AsyncClient):
    """잘못된 자격증명으로 로그인 시 401 에러가 발생하는지 테스트"""
    with patch("app.api.routes_auth.verify_password", return_value=False):
        login_data = {"username": settings.ADMIN_USER, "password": "wrong_password"}
        response = await client.post("/api/v1/auth/login", data=login_data)
        
        assert response.status_code == 401
        assert response.json() == {"detail": "Incorrect username or password"}

async def test_start_strategy_unauthorized(client: AsyncClient):
    """인증 없이 제어 엔드포인트 호출 시 401 에러가 발생하는지 테스트"""
    response = await client.post("/api/v1/control/start")
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}

async def test_start_strategy_authorized(client: AsyncClient):
    """인증 후 제어 엔드포인트 호출 테스트"""
    # 먼저 로그인하여 토큰을 얻습니다.
    with patch("app.api.routes_auth.verify_password", return_value=True):
        login_data = {"username": settings.ADMIN_USER, "password": "admin"}
        login_response = await client.post("/api/v1/auth/login", data=login_data)
        token = login_response.json()["access_token"]

    headers = {"Authorization": f"Bearer {token}"}
    
    # StrategyRouter의 start 메서드가 호출되는지 확인하기 위해 모의 객체 설정
    mock_strategy_router = MagicMock()
    mock_strategy_router.start = MagicMock()

    # 의존성 주입을 통해 모의 객체를 사용하도록 설정
    # test_app Fixture에서 이미 모의 객체로 대체되었으므로, 해당 객체를 가져와 설정합니다.
    with patch("app.api.routes_control.get_strategy_router", return_value=mock_strategy_router):
        response = await client.post("/api/v1/control/start", headers=headers)
        assert response.status_code == 200
        assert response.json() == {"message": "Strategy started successfully."}
        # start 메서드가 한 번 호출되었는지 확인
        mock_strategy_router.start.assert_called_once()
