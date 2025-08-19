
# ===================================================================================
#   connectors/base.py: 데이터 피드 커넥터 추상 베이스 클래스
# ===================================================================================
#
#   - 모든 데이터 소스(X, News, Facebook 등) 커넥터가 상속받아야 할 추상 베이스 클래스(ABC)를 정의합니다.
#   - `Template Method` 패턴을 사용하여 커넥터의 기본 구조와 동작을 통일합니다.
#
#   **주요 기능:**
#   - **통일된 인터페이스**: 모든 커넥터는 `run()` 메서드를 통해 실행되고, `_connect_and_stream()` 이라는 내부 메서드를 구현해야 합니다.
#   - **이벤트 큐 주입**: 생성자에서 `asyncio.Queue`를 주입받아, 모든 커넥터가 수집한 데이터를 동일한 큐에 넣도록 강제합니다. 이 큐는 `TrendAggregator`가 소비합니다.
#   - **상태 관리**: `is_running` 플래그를 통해 커넥터의 실행 상태를 관리합니다.
#   - **자동 재시도 로직**: `run()` 메서드 내에 기본적인 재시도 및 백오프 로직을 포함하여, 하위 클래스에서 발생할 수 있는 일시적인 연결 문제를 처리합니다.
#
#   **구현 가이드:**
#   - 새로운 데이터 소스를 추가하려면, 이 `BaseFeedConnector`를 상속받고 다음을 구현해야 합니다:
#     1. `_connect_and_stream()`: 실제 데이터 소스에 연결하고, 데이터를 스트리밍하며, 수신된 데이터를 파싱하여 `TrendEvent`로 변환한 뒤 `self.queue.put()`을 호출하는 로직.
#     2. `stop()`: 스트림 연결을 정상적으로 종료하는 로직.
#
#
import asyncio
from abc import ABC, abstractmethod
from loguru import logger

from app.utils.typing import TrendEvent

class BaseFeedConnector(ABC):
    """
    모든 데이터 피드 커넥터의 추상 베이스 클래스.
    """
    def __init__(self, queue: asyncio.Queue[TrendEvent], source_name: str):
        """
        커넥터를 초기화합니다.

        :param queue: 수집된 TrendEvent를 넣을 비동기 큐.
        :param source_name: 데이터 소스의 이름 (예: 'X', 'NewsAPI').
        """
        self.queue = queue
        self.source_name = source_name
        self._is_running = False
        self._task: asyncio.Task | None = None

    @abstractmethod
    async def _connect_and_stream(self):
        """
        [구현 필요] 데이터 소스에 연결하고 스트리밍하는 핵심 로직.
        이 메서드는 데이터를 수신하고 `TrendEvent`로 변환한 뒤,
        `await self.queue.put(event)`를 호출해야 합니다.
        """
        pass

    async def run(self):
        """
        커넥터를 실행하고, 연결이 끊어지면 지수 백오프와 함께 재연결을 시도합니다.
        """
        self._is_running = True
        logger.info(f"Starting {self.source_name} connector...")
        backoff_delay = 5  # 초기 백오프 딜레이 (초)
        while self._is_running:
            try:
                await self._connect_and_stream()
                # 정상적으로 스트림이 끝나면 (그럴 경우는 거의 없지만) 재연결 시도
                logger.warning(f"{self.source_name} stream ended unexpectedly. Reconnecting...")
                backoff_delay = 5 # 딜레이 초기화
            except asyncio.CancelledError:
                logger.info(f"{self.source_name} connector run task cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in {self.source_name} connector: {e}. Retrying in {backoff_delay} seconds.", exc_info=True)
                await asyncio.sleep(backoff_delay)
                backoff_delay = min(backoff_delay * 2, 300) # 최대 5분까지 딜레이 증가
        logger.info(f"{self.source_name} connector stopped.")

    def start(self):
        """백그라운드에서 커넥터 실행 태스크를 시작합니다."""
        if not self._is_running:
            self._task = asyncio.create_task(self.run())

    def stop(self):
        """
        커넥터 실행을 중지합니다.
        """
        self._is_running = False
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info(f"Stopping {self.source_name} connector...")

class MockFeedConnector(BaseFeedConnector):
    """
    API 키가 없을 때 사용되는 모의(Mock) 커넥터.
    주기적으로 가짜 트렌드 이벤트를 생성하여 시스템의 다른 부분이
    정상적으로 동작하는지 테스트할 수 있게 합니다.
    """
    async def _connect_and_stream(self):
        """
        가짜 트렌드 이벤트를 10초마다 생성하여 큐에 넣습니다.
        """
        logger.warning(f"Running {self.source_name} in MOCK mode. No real data will be fetched.")
        from app.utils.time import now
        import random

        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
        texts = [
            "is about to pump! To the moon!",
            "looks bearish, might dump soon.",
            "just announced a new partnership, looks very promising.",
            "is showing some weakness, be careful."
        ]

        while True:
            await asyncio.sleep(random.uniform(10, 20)) # 10-20초 간격으로 이벤트 생성
            symbol = random.choice(symbols)
            text = f"${symbol.replace('USDT','')} {random.choice(texts)}"
            event = TrendEvent(
                source=f"{self.source_name}-Mock",
                symbol_raw=symbol,
                text=text,
                url=f"https://mock.event/{random.randint(1000,9999)}",
                timestamp=now(),
            )
            await self.queue.put(event)
            logger.info(f"[MOCK] Generated trend event: {event.text}")
