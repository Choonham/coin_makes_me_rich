
# ===================================================================================
#   ws/manager.py: WebSocket 대시보드 관리자
# ===================================================================================
#
#   - React Native 대시보드로 실시간 데이터를 브로드캐스트하는 WebSocket 서버 로직을 관리합니다.
#   - 클라이언트 연결 수립, 유지, 종료를 처리합니다.
#   - 주기적으로 시스템의 최신 상태(`SystemState`)를 모든 연결된 클라이언트에게 전송합니다.
#
#   **주요 기능:**
#   - **연결 관리**: 여러 클라이언트의 동시 접속을 관리하고, 연결이 끊긴 클라이언트를 정리합니다.
#   - **상태 브로드캐스팅**: `broadcast_loop` 태스크를 통해 1초마다 `state_store`에서 최신 시스템 상태를 가져와 JSON 형태로 모든 클라이언트에게 전송합니다.
#   - **비동기 처리**: `asyncio`를 사용하여 다수의 WebSocket 연결을 효율적으로 처리합니다.
#
#   **데이터 흐름:**
#   1. RN 앱이 `/ws/dashboard` 엔드포인트로 WebSocket 연결을 요청합니다.
#   2. `handle_connection`이 호출되어 연결을 수락하고, `self.active_connections` 리스트에 추가합니다.
#   3. `broadcast_loop` (백그라운드 태스크)는 주기적으로 `state_store.get_system_state()`를 호출합니다.
#   4. 획득한 상태 정보를 JSON으로 직렬화하여 `active_connections`에 있는 모든 클라이언트에게 전송합니다.
#   5. 클라이언트 연결이 끊어지면, 해당 클라이언트는 리스트에서 제거됩니다.
#
#
import asyncio
from typing import List, Callable, Awaitable, Any

from fastapi import WebSocket
from loguru import logger
from starlette.websockets import WebSocketState

from app.state.store import state_store

class WebSocketManager:
    """
    WebSocket 연결을 관리하고, 연결된 모든 클라이언트에게 메시지를 브로드캐스트합니다.
    """
    def __init__(self):
        # 현재 활성화된 WebSocket 연결 목록
        self.active_connections: List[WebSocket] = []
        logger.info("WebSocketManager initialized.")

    async def handle_connection(self, websocket: WebSocket):
        """
        새로운 WebSocket 연결 요청을 처리합니다.
        FastAPI의 @app.websocket 데코레이터에서 이 메서드를 호출합니다.
        """
        await self.connect(websocket)
        try:
            # 클라이언트로부터 메시지를 수신 대기하는 루프
            # 현재는 클라이언트로부터 메시지를 받는 기능은 없지만, 향후 확장을 위해 구조를 유지합니다.
            while True:
                # 클라이언트의 연결 상태를 확인하기 위해 receive()를 호출합니다.
                # 연결이 끊어지면 `WebSocketDisconnect` 예외가 발생합니다.
                await websocket.receive_text()
        except Exception:
            # WebSocketDisconnect 예외 또는 다른 에러 발생 시 연결을 정리합니다.
            self.disconnect(websocket)

    async def connect(self, websocket: WebSocket):
        """
        클라이언트의 WebSocket 연결을 수락하고 관리 목록에 추가합니다.
        """
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"New WebSocket connection: {websocket.client}. Total clients: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """
        클라이언트의 WebSocket 연결을 관리 목록에서 제거합니다.
        """
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"WebSocket connection closed: {websocket.client}. Total clients: {len(self.active_connections)}")

    async def broadcast_loop(self, interval_seconds: float = 1.0):
        """
        주기적으로 모든 연결된 클라이언트에게 시스템 상태를 브로드캐스트하는 백그라운드 태스크입니다.
        `main.py`의 `startup` 이벤트에서 `asyncio.create_task`로 실행됩니다.
        """
        logger.info(f"Starting WebSocket broadcast loop with {interval_seconds}s interval.")
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                
                # 브로드캐스트 중 연결이 끊어지는 경우를 대비해 리스트를 복사해서 사용합니다.
                connections = self.active_connections[:]
                if not connections:
                    continue

                # state_store에서 최신 시스템 상태를 가져옵니다.
                system_state = state_store.get_system_state()
                # Pydantic 모델을 JSON 문자열로 변환합니다.
                message = system_state.model_dump_json()

                # 모든 활성 클라이언트에게 메시지를 전송합니다.
                for websocket in connections:
                    # 전송 전에 웹소켓 연결 상태를 확인합니다.
                    if websocket.client_state == WebSocketState.CONNECTED:
                        await websocket.send_text(message)
                    else:
                        # 연결이 끊긴 경우 리스트에서 제거합니다.
                        self.disconnect(websocket)

            except asyncio.CancelledError:
                logger.info("WebSocket broadcast loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in WebSocket broadcast loop: {e}", exc_info=True)
                # 루프가 중단되지 않도록 잠시 대기 후 계속합니다.
                await asyncio.sleep(5)
