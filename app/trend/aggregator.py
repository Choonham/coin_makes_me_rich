
# ===================================================================================
#   trend/aggregator.py: 트렌드 이벤트 집계기
# ===================================================================================
#
#   - 전체 트렌드 분석 파이프라인을 총괄하는 오케스트레이터입니다.
#   - 여러 커넥터(X, News 등)로부터 원시 데이터를 수집하고, 이를 정제, 매핑, 점수화하여
#     최종적인 거래 신호로 변환하는 과정을 관리합니다.
#
#   **주요 기능:**
#   - **커넥터 관리**: `create_x_connector`, `create_news_connector` 등 팩토리 함수를
#     사용하여 모든 활성 커넥터 인스턴스를 생성하고 관리합니다.
#   - **중앙 이벤트 큐**: 모든 커넥터는 `asyncio.Queue`를 공유하며, 수집한 원시
#     `TrendEvent`를 이 큐에 넣습니다.
#   - **이벤트 처리 파이프라인**: 집계기는 큐에서 이벤트를 꺼내 다음 순서로 처리합니다:
#     1. `SymbolMapper`: 이벤트 텍스트를 분석하여 공식 거래 심볼(`symbol_final`)에 매핑합니다.
#     2. `TrendScorer`: 매핑된 이벤트의 텍스트를 분석하여 감성 점수(`score`)와 신뢰도(`confidence`)를 부여합니다.
#   - **신호 생성 및 저장**: 점수화된 이벤트가 특정 임계값(예: `score * confidence > 0.5`)
#     을 통과하면, 이를 `Signal` 객체로 변환합니다. 처리된 모든 이벤트와 생성된 신호는
#     `state_store`에 저장되어 다른 모듈(API, StrategyRouter)에서 사용할 수 있게 됩니다.
#
#   **데이터 흐름:**
#   1. `main.py`에서 `TrendAggregator` 인스턴스 생성 및 `run_connectors()` 호출.
#   2. 각 커넥터가 백그라운드에서 실행되며 원시 이벤트를 `self.raw_event_queue`에 추가.
#   3. `_process_events()` 루프가 큐에서 이벤트를 가져와 `mapper`와 `scorer`로 처리.
#   4. 처리된 이벤트(`processed_event`)를 `state_store.add_trend_event()`로 저장.
#   5. `processed_event`가 임계값을 넘으면 `Signal`로 변환하여 `self.latest_signals`에 저장.
#   6. `StrategyRouter`는 `get_latest_signal()`을 호출하여 이 신호를 가져가 거래 결정에 사용.
#
#
import asyncio
from typing import Dict, List

from loguru import logger

from app.connectors.base import BaseFeedConnector
from app.connectors.facebook_connector import create_facebook_connector
from app.connectors.news_connector import create_news_connector

from app.state.store import state_store
from app.trend.mapper import symbol_mapper
from app.trend.scorer import trend_scorer
from app.utils.typing import Side, Signal, TrendEvent

class TrendAggregator:
    """
    다양한 소스로부터 트렌드 이벤트를 수집, 처리, 집계하여 중앙 신호 큐로 보냅니다.
    """
    def __init__(self, signal_threshold: float = 0.3):
        self.raw_event_queue: asyncio.Queue[TrendEvent] = asyncio.Queue()
        self.connectors: List[BaseFeedConnector] = self._create_connectors()
        self.signal_queue: Optional[asyncio.Queue[Signal]] = None # 외부에서 주입받을 신호 큐
        self.signal_threshold = signal_threshold
        self._processing_task: Optional[asyncio.Task] = None
        logger.info(f"TrendAggregator initialized with {len(self.connectors)} connectors.")

    def set_signal_queue(self, queue: asyncio.Queue[Signal]):
        """StrategyRouter로부터 중앙 신호 큐를 주입받습니다."""
        self.signal_queue = queue

    def _create_connectors(self) -> List[BaseFeedConnector]:
        """활성화된 모든 커넥터를 생성하고 리스트로 반환합니다."""
        return [
            create_news_connector(self.raw_event_queue),
            create_facebook_connector(self.raw_event_queue),
        ]

    async def run_connectors(self):
        """모든 커넥터를 시작하고 이벤트 처리 루프를 실행합니다."""
        if not self.connectors:
            logger.warning("No connectors configured.")
            return
        if not self.signal_queue:
            raise RuntimeError("Signal queue has not been set in TrendAggregator.")

        logger.info("Starting all trend connectors and event processing loop...")
        self._processing_task = asyncio.create_task(self._process_events())
        
        connector_tasks = [asyncio.create_task(conn.run()) for conn in self.connectors]
        await asyncio.gather(self._processing_task, *connector_tasks)

    async def _process_events(self):
        """큐에서 원시 이벤트를 가져와 처리하는 메인 루프."""
        while True:
            try:
                raw_event = await self.raw_event_queue.get()
                
                final_symbol = symbol_mapper.map_event_to_symbol(raw_event)
                if not final_symbol:
                    self.raw_event_queue.task_done()
                    continue
                raw_event.symbol_final = final_symbol

                processed_event = trend_scorer.score_event(raw_event)
                await state_store.add_trend_event(processed_event)
                self._generate_and_send_signal(processed_event)

                self.raw_event_queue.task_done()

            except asyncio.CancelledError:
                logger.info("Event processing loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error processing trend event: {e}", exc_info=True)

    def _generate_and_send_signal(self, event: TrendEvent):
        """점수화된 이벤트가 임계값을 넘으면 신호로 변환하여 중앙 큐로 보냅니다."""
        if not event.symbol_final or event.score is None or event.confidence is None or not self.signal_queue:
            return

        signal_strength = event.score * event.confidence
        if abs(signal_strength) >= self.signal_threshold:
            side = Side.BUY if signal_strength > 0 else Side.SELL
            
            signal = Signal(
                symbol=event.symbol_final,
                side=side,
                price=0,
                reason=f"[{event.source}] {event.text[:100]}...",
                strength=abs(signal_strength),
                signal_type="trend"
            )
            
            self.signal_queue.put_nowait(signal)
            logger.info(f"[TREND SIGNAL] Sent new signal to queue for {event.symbol_final}: {side.value}")
