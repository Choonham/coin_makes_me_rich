

# ===================================================================================
#   connectors/x_connector.py: X (Twitter) v2 Filtered Stream 커넥터
# ===================================================================================
#
#   - X API v2의 Filtered Stream 엔드포인트를 사용하여 실시간으로 특정 키워드나
#     캐시태그($)가 포함된 트윗을 수집합니다.
#   - `symbol_map.yml`에 정의된 키워드를 기반으로 필터링 규칙을 동적으로 생성하고 적용합니다.
#   - API 정책 및 연결 안정성을 위한 여러 기능을 포함합니다.
#
#   **주요 기능:**
#   - **동적 필터링 규칙**: `assets/symbol_map.yml` 파일을 읽어, 거래 대상 심볼과 관련된
#     키워드, 캐시태그, 해시태그를 포함하는 필터링 규칙을 생성합니다.
#     (예: `($BTC OR #Bitcoin OR "Bitcoin price")`)
#   - **실시간 스트리밍**: `httpx.AsyncClient`의 스트리밍 요청을 사용하여 X 서버와의 연결을
#     유지하고, 실시간으로 들어오는 트윗 데이터를 처리합니다.
#   - **자동 재연결 및 규칙 업데이트**: 스트림 연결이 끊어지면 자동으로 재연결을 시도하며,
#     시작 시 기존 규칙을 삭제하고 새로운 규칙을 설정하여 항상 최신 상태를 유지합니다.
#   - **하트비트(Heartbeat) 처리**: X API는 연결 유지를 위해 주기적으로 빈 줄(하트비트)을
#     보내는데, 이를 정상적인 신호로 처리하고 타임아웃을 방지합니다.
#   - **모의 모드(Mock Mode)**: `X_BEARER_TOKEN`이 제공되지 않으면, `MockFeedConnector`로
#     대체되어 실제 API 호출 없이 가상의 데이터 스트림을 생성합니다.
#
#   **API 정책 및 주의사항:**
#   - X API v2 Filtered Stream을 사용하려면 Academic Research 접근 권한 또는 그 이상의
#     프로젝트 레벨이 필요할 수 있습니다. (Standard 레벨에서는 제한적일 수 있음)
#   - API 키와 토큰은 절대로 코드에 하드코딩하지 않고, `.env` 파일을 통해 관리해야 합니다.
#
#
import asyncio
import json
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from app.assets import symbol_map
from app.config import settings
from app.connectors.base import BaseFeedConnector, MockFeedConnector
from app.utils.time import now
from app.utils.typing import TrendEvent


def create_x_connector(queue: asyncio.Queue[TrendEvent]) -> BaseFeedConnector:
    """
    X API Bearer Token의 존재 여부에 따라 실제 커넥터 또는 모의 커넥터를 생성하는 팩토리 함수.
    """
    if settings.X_BEARER_TOKEN and settings.X_BEARER_TOKEN.get_secret_value() != "":
        logger.info("X_BEARER_TOKEN found, creating real XConnector.")
        return XConnector(queue)
    else:
        logger.warning("X_BEARER_TOKEN not found. Creating MockFeedConnector for X.")
        return MockFeedConnector(queue, source_name="X")


class XConnector(BaseFeedConnector):
    """
    X API v2 Filtered Stream에 연결하여 실시간 트윗을 수집합니다.
    """
    API_URL = "https://api.twitter.com/2/tweets/search/stream"

    def __init__(self, queue: asyncio.Queue[TrendEvent]):
        super().__init__(queue, source_name="X")
        self.client = httpx.AsyncClient(timeout=30.0)
        self.bearer_token = settings.X_BEARER_TOKEN.get_secret_value()

    def _get_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.bearer_token}"}

    async def _get_rules(self) -> List[Dict[str, Any]]:
        """현재 설정된 필터링 규칙을 가져옵니다."""
        response = await self.client.get(f"{self.API_URL}/rules", headers=self._get_headers())
        response.raise_for_status()
        return response.json().get("data", [])

    async def _delete_rules(self, rules: List[Dict[str, Any]]):
        """기존 필터링 규칙을 삭제합니다."""
        if not rules:
            return
        rule_ids = [rule["id"] for rule in rules]
        payload = {"delete": {"ids": rule_ids}}
        response = await self.client.post(f"{self.API_URL}/rules", headers=self._get_headers(), json=payload)
        response.raise_for_status()
        logger.info(f"Deleted {len(rule_ids)} old X stream rules.")

    async def _add_rules(self):
        """`symbol_map`을 기반으로 새로운 필터링 규칙을 추가합니다."""
        rules = []
        for symbol, keywords in symbol_map.items():
            # X API 규칙은 공백으로 구분된 키워드를 OR 조건으로 처리합니다.
            # 따옴표로 묶인 구문은 정확히 일치해야 합니다.
            # 예: "$BTC OR #Bitcoin OR \"Bitcoin price\""
            query_parts = [f'"{k}"' if ' ' in k else k for k in keywords]
            rule_value = " OR ".join(query_parts)
            rules.append({"value": f"({rule_value}) lang:en", "tag": symbol})
        
        payload = {"add": rules}
        response = await self.client.post(f"{self.API_URL}/rules", headers=self._get_headers(), json=payload)
        response.raise_for_status()
        logger.info(f"Added {len(rules)} new X stream rules.")

    async def _setup_rules(self):
        """기존 규칙을 모두 삭제하고 새로운 규칙을 설정합니다."""
        try:
            logger.info("Setting up X stream rules...")
            current_rules = await self._get_rules()
            await self._delete_rules(current_rules)
            await self._add_rules()
            logger.info("X stream rules setup complete.")
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to set up X rules: {e.response.status_code} - {e.response.text}")
            raise

    async def _connect_and_stream(self):
        """Filtered Stream에 연결하고 데이터를 처리합니다."""
        await self._setup_rules()
        
        # 스트림에 요청할 때 받을 데이터 필드를 지정합니다.
        params = {
            "tweet.fields": "created_at,author_id,lang,text",
            "expansions": "author_id",
            "user.fields": "username,name"
        }
        
        async with self.client.stream("GET", self.API_URL, headers=self._get_headers(), params=params) as response:
            logger.info("Connected to X Filtered Stream.")
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not self._is_running:
                    break
                if line.strip(): # 하트비트(빈 줄) 무시
                    try:
                        data = json.loads(line)
                        if "data" in data:
                            tweet = data["data"]
                            matching_rules = data.get("matching_rules", [])
                            symbol = matching_rules[0]["tag"] if matching_rules else None

                            if symbol:
                                event = TrendEvent(
                                    source=self.source_name,
                                    symbol_raw=symbol,
                                    text=tweet["text"],
                                    url=f"https://twitter.com/{tweet['author_id']}/status/{tweet['id']}",
                                    timestamp=now(),
                                    lang=tweet.get("lang"),
                                    author=data.get("includes", {}).get("users", [{}])[0].get("username")
                                )
                                await self.queue.put(event)
                        else:
                            # API 에러 메시지 처리
                            logger.warning(f"Received non-data message from X stream: {data}")
                    except json.JSONDecodeError:
                        logger.warning(f"Could not decode JSON from X stream: {line}")
