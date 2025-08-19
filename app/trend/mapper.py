
# ===================================================================================
#   trend/mapper.py: 심볼 매퍼
# ===================================================================================
#
#   - 트렌드 이벤트에 포함된 텍스트(트윗, 뉴스 제목 등)를 분석하여, 어떤 공식적인
#     거래 심볼(예: `BTCUSDT`)과 관련이 있는지 식별하는 역할을 합니다.
#
#   **주요 기능:**
#   - **키워드 기반 매핑**: `assets/symbol_map.yml`에 정의된 키워드 목록을 사용합니다.
#     이벤트 텍스트에 특정 심볼과 연관된 키워드(예: `$SOL`, `Solana`, `#Solana`)가
#     포함되어 있는지 검사합니다.
#   - **효율적인 검색**: `symbol_map`을 미리 로드하고 재구성하여, 매번 파일을 읽지 않고
#     메모리 상에서 빠르게 검색을 수행합니다.
#   - **다중 매핑 처리**: 하나의 텍스트가 여러 심볼의 키워드를 포함할 경우, 가장 먼저
#     발견된 심볼을 반환하는 간단한 전략을 사용합니다. (향후 가중치 부여 등 확장 가능)
#
#   **데이터 흐름:**
#   1. `TrendAggregator`가 `map_event_to_symbol()` 함수를 호출합니다.
#   2. 이 함수는 `TrendEvent`의 `text`와 `symbol_raw` 필드를 검사합니다.
#   3. `_reversed_symbol_map`을 순회하며 텍스트에 키워드가 포함되어 있는지 확인합니다.
#   4. 일치하는 키워드를 찾으면, 해당 키워드가 속한 공식 심볼을 `TrendEvent`의
#      `symbol_final` 필드에 할당하여 반환합니다.
#
#
from typing import Dict, List, Optional
from loguru import logger

from app.assets import symbol_map
from app.utils.typing import TrendEvent

class SymbolMapper:
    """
    텍스트 내용을 기반으로 TrendEvent를 특정 거래 심볼에 매핑합니다.
    """
    def __init__(self):
        # 검색 효율성을 위해 symbol_map을 역으로 구성합니다.
        # {'$btc': 'BTCUSDT', 'bitcoin': 'BTCUSDT', ...}
        self._reversed_symbol_map: Dict[str, str] = {}
        for symbol, keywords in symbol_map.items():
            if not keywords:
                continue
            for keyword in keywords:
                if keyword:  # 키워드가 None이 아닌지 확인
                    self._reversed_symbol_map[keyword.lower()] = symbol
        logger.info(f"SymbolMapper initialized with {len(self._reversed_symbol_map)} keywords.")

    def map_event_to_symbol(self, event: TrendEvent) -> Optional[str]:
        """
        주어진 TrendEvent를 `symbol_map`을 기반으로 최종 심볼에 매핑합니다.

        :param event: 처리할 TrendEvent 객체.
        :return: 매핑된 심볼 문자열 (예: "BTCUSDT") 또는 None.
        """
        # 1. 커넥터에서 이미 심볼을 태그한 경우, 해당 심볼을 우선적으로 사용합니다.
        if event.symbol_raw and event.symbol_raw in symbol_map:
            return event.symbol_raw

        # 2. 텍스트 내용을 소문자로 변환하여 검색 준비
        text_lower = event.text.lower()

        # 3. 역매핑된 딕셔너리를 순회하며 키워드가 텍스트에 포함되어 있는지 확인
        #    긴 키워드를 먼저 체크하여 "sol"이 "solana"보다 먼저 매칭되는 것을 방지
        for keyword, symbol in sorted(self._reversed_symbol_map.items(), key=lambda item: len(item[0]), reverse=True):
            # 단어 경계를 확인하여 "sol"이 "absolve"에 매칭되는 경우 등을 방지 (간단한 방식)
            if f' {keyword} ' in f' {text_lower} ':
                logger.debug(f"Mapped text '{event.text[:30]}...' to {symbol} with keyword '{keyword}'")
                return symbol

        logger.warning(f"Could not map event to any symbol: {event.text[:50]}...")
        return None

# 전역 인스턴스 생성
symbol_mapper = SymbolMapper()
