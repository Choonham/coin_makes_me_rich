
# ===================================================================================
#   connectors/news_connector.py: 뉴스 API 커넥터
# ===================================================================================
#
#   - NewsAPI.org와 같은 외부 뉴스 제공업체 API를 사용하여 암호화폐 관련 최신 뉴스를
#     수집합니다.
#   - X 커넥터와 달리, 대부분의 뉴스 API는 스트리밍을 지원하지 않으므로, 주기적으로
#     폴링(polling)하는 방식으로 동작합니다.
#
#   **주요 기능:**
#   - **주기적 폴링**: 설정된 시간(예: 5분)마다 뉴스 API에 새로운 기사를 요청합니다.
#   - **키워드 검색**: `symbol_map.yml`의 키워드를 조합하여 관련성 높은 뉴스를 검색합니다.
#     (예: `(Bitcoin OR BTC) AND (crypto OR currency)`)
#   - **중복 방지**: 한 번 처리한 뉴스를 다시 처리하지 않도록 기사의 URL이나 제목을
#     기억하는 메커니즘을 포함합니다. (`self.seen_articles`)
#   - **API 호출 관리**: 단시간에 너무 많은 요청을 보내지 않도록 폴링 주기를 관리합니다.
#   - **모의 모드(Mock Mode)**: `NEWS_API_KEY`가 없으면 `MockFeedConnector`로 대체되어
#     가상의 뉴스 이벤트를 생성합니다.
#
#   **사용 가능한 다른 뉴스 소스:**
#   - **GDELT Project**: 방대한 양의 전 세계 뉴스 데이터를 제공하는 무료 소스. 데이터 처리 복잡도가 높음.
#   - **CryptoCompare (News Feed)**: 암호화폐 전문 뉴스 피드를 제공하는 API.
#
#
import asyncio
import json
from typing import Set

import httpx
from loguru import logger

from app.assets import symbol_map
from app.config import settings
from app.connectors.base import BaseFeedConnector, MockFeedConnector
from app.utils.time import now
from app.utils.typing import TrendEvent

def create_news_connector(queue: asyncio.Queue[TrendEvent]) -> BaseFeedConnector:
    """
    News API 키의 존재 여부에 따라 실제 커넥터 또는 모의 커넥터를 생성하는 팩토리 함수.
    """
    if settings.NEWS_API_KEY and settings.NEWS_API_KEY.get_secret_value() != "":
        logger.info("NEWS_API_KEY found, creating real NewsConnector.")
        return NewsConnector(queue)
    else:
        logger.warning("NEWS_API_KEY not found. Creating MockFeedConnector for News.")
        return MockFeedConnector(queue, source_name="News")

class NewsConnector(BaseFeedConnector):
    """
    NewsAPI.org를 폴링하여 암호화폐 관련 뉴스를 수집합니다.
    """
    API_URL = "https://newsapi.org/v2/everything"

    def __init__(self, queue: asyncio.Queue[TrendEvent], poll_interval_minutes: int = 10):
        super().__init__(queue, source_name="NewsAPI")
        self.api_key = settings.NEWS_API_KEY.get_secret_value()
        self.poll_interval_seconds = poll_interval_minutes * 60
        self.client = httpx.AsyncClient(timeout=20.0)
        self.seen_articles: Set[str] = set() # 중복 처리를 막기 위해 기사 URL을 저장

    def _build_query(self) -> str:
        """`symbol_map`을 기반으로 검색 쿼리를 생성합니다."""
        # 모든 심볼의 키워드를 OR로 묶어 하나의 긴 쿼리로 만듭니다.
        all_keywords = []
        for keywords in symbol_map.values():
            if keywords:
                all_keywords.extend([f'"{k}"' for k in keywords if k and len(k) > 2])
        
        # 중복 제거 후 쿼리 생성
        unique_keywords = list(set(all_keywords))
        # NewsAPI 쿼리 길이 제한(500자)을 고려해야 함
        query = " OR ".join(unique_keywords[:30]) # 예시로 30개 키워드만 사용
        return f"({query}) AND (crypto OR cryptocurrency OR blockchain)"

    async def _fetch_news(self):
        """뉴스 API를 호출하여 최신 기사를 가져옵니다."""
        query = self._build_query()
        params = {
            "q": query,
            "apiKey": self.api_key,
            "sortBy": "publishedAt", # 최신순으로 정렬
            "pageSize": 20, # 한 번에 가져올 기사 수
            "language": "en"
        }
        try:
            response = await self.client.get(self.API_URL, params=params)
            response.raise_for_status()
            data = response.json()

            if data.get("status") == "ok":
                articles = data.get("articles", [])
                logger.info(f"Fetched {len(articles)} articles from NewsAPI.")
                for article in articles:
                    article_url = article.get("url")
                    if article_url and article_url not in self.seen_articles:
                        self.seen_articles.add(article_url)
                        
                        # 기사 내용에서 어떤 심볼과 관련있는지 찾아야 함 (간단한 버전)
                        title_desc = f'{article.get("title", "")} {article.get("description", "")}'.lower()
                        matched_symbol = None
                        for symbol, keywords in symbol_map.items():
                            if keywords and any(k and k.lower() in title_desc for k in keywords):
                                matched_symbol = symbol
                                break
                        
                        if matched_symbol:
                            event = TrendEvent(
                                source=self.source_name,
                                symbol_raw=matched_symbol,
                                text=f'{article["title"]}: {article.get("description", "")}',
                                url=article_url,
                                timestamp=now(),
                                author=article.get("source", {}).get("name")
                            )
                            await self.queue.put(event)
            else:
                logger.error(f"NewsAPI error: {data.get('message')}")

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error while fetching news: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            logger.error(f"Failed to fetch or process news: {e}", exc_info=True)

    async def _connect_and_stream(self):
        """주기적으로 `_fetch_news`를 호출하는 폴링 루프입니다."""
        logger.info(f"Starting NewsConnector polling every {self.poll_interval_seconds} seconds.")
        while True:
            await self._fetch_news()
            await asyncio.sleep(self.poll_interval_seconds)
