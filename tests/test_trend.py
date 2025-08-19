
# ===================================================================================
#   tests/test_trend.py: 트렌드 분석 파이프라인 단위 테스트
# ===================================================================================
#
#   - 트렌드 분석의 각 단계를(Mapper, Scorer) 독립적으로 테스트합니다.
#   - 외부 API나 파일 시스템에 의존하지 않고, 정해진 입력에 대해 예측 가능한 출력을
#     반환하는지 확인하는 데 중점을 둡니다.
#
#   **테스트 대상:**
#   - **SymbolMapper**: 
#     - 트윗 텍스트에 포함된 캐시태그(예: `$SOL`)나 키워드(예: `Solana`)를 정확히
#       `SOLUSDT`와 같은 공식 심볼로 매핑하는지 테스트합니다.
#     - 관련 없는 텍스트는 `None`을 반환하는지 확인합니다.
#   - **TrendScorer**:
#     - 명확히 긍정적인 텍스트(예: `BTC to the moon!`)에 대해 긍정적인 점수를 반환하는지 테스트합니다.
#     - 명확히 부정적인 텍스트(예: `ETH is dumping hard`)에 대해 부정적인 점수를 반환하는지 테스트합니다.
#     - 직접 정의한 커스텀 사전에 있는 단어(예: `partnership`, `hack`)가 점수에 영향을 주는지 확인합니다.
#
#   **테스트 방법:**
#   - 각 컴포넌트(Mapper, Scorer)의 인스턴스를 직접 생성합니다.
#   - 테스트 케이스별로 `TrendEvent` 객체를 생성하여 입력값으로 사용합니다.
#   - 각 컴포넌트의 메서드를 호출하고, 반환된 객체의 필드(`symbol_final`, `score` 등)가
#     예상과 일치하는지 `assert` 문으로 검증합니다.
#
#
import pytest
from app.trend.mapper import SymbolMapper
from app.trend.scorer import TrendScorer
from app.utils.typing import TrendEvent
from app.utils.time import now

@pytest.fixture(scope="module")
def symbol_mapper() -> SymbolMapper:
    """테스트를 위한 SymbolMapper 인스턴스를 생성합니다."""
    return SymbolMapper()

@pytest.fixture(scope="module")
def trend_scorer() -> TrendScorer:
    """테스트를 위한 TrendScorer 인스턴스를 생성합니다."""
    return TrendScorer()

# --- SymbolMapper 테스트 --- 

def test_symbol_mapper_cashtag(symbol_mapper: SymbolMapper):
    """캐시태그($)가 포함된 텍스트 매핑 테스트"""
    event = TrendEvent(source="test", text="I think $SOL is going to pump soon.", timestamp=now())
    mapped_symbol = symbol_mapper.map_event_to_symbol(event)
    assert mapped_symbol == "SOLUSDT"

def test_symbol_mapper_keyword(symbol_mapper: SymbolMapper):
    """일반 키워드가 포함된 텍스트 매핑 테스트"""
    event = TrendEvent(source="test", text="Big news for Ethereum today!", timestamp=now())
    mapped_symbol = symbol_mapper.map_event_to_symbol(event)
    assert mapped_symbol == "ETHUSDT"

def test_symbol_mapper_no_match(symbol_mapper: SymbolMapper):
    """관련 없는 텍스트 매핑 테스트"""
    event = TrendEvent(source="test", text="Just a regular day.", timestamp=now())
    mapped_symbol = symbol_mapper.map_event_to_symbol(event)
    assert mapped_symbol is None

# --- TrendScorer 테스트 --- 

def test_trend_scorer_positive(trend_scorer: TrendScorer):
    """긍정적인 텍스트 점수화 테스트"""
    event = TrendEvent(source="test", text="Wow, BTC is amazing, awesome, and wonderful! To the moon!", timestamp=now(), symbol_final="BTCUSDT")
    scored_event = trend_scorer.score_event(event)
    assert scored_event.score > 0.5
    assert scored_event.confidence > 0.5

def test_trend_scorer_negative(trend_scorer: TrendScorer):
    """부정적인 텍스트 점수화 테스트"""
    event = TrendEvent(source="test", text="This is a terrible, awful, disastrous dump for ETH. Total scam.", timestamp=now(), symbol_final="ETHUSDT")
    scored_event = trend_scorer.score_event(event)
    assert scored_event.score < -0.5
    assert scored_event.confidence > 0.5

def test_trend_scorer_custom_lexicon_positive(trend_scorer: TrendScorer):
    """커스텀 긍정 키워드 테스트"""
    event = TrendEvent(source="test", text="SOL just announced a huge partnership.", timestamp=now(), symbol_final="SOLUSDT")
    scored_event = trend_scorer.score_event(event)
    # "partnership"이라는 긍정 단어 때문에 점수가 높아야 함
    assert scored_event.score > 0.3

def test_trend_scorer_custom_lexicon_negative(trend_scorer: TrendScorer):
    """커스텀 부정 키워드 테스트"""
    event = TrendEvent(source="test", text="There was a major hack on the Ripple network.", timestamp=now(), symbol_final="RIPPLEUSDT")
    scored_event = trend_scorer.score_event(event)
    # "hack"이라는 부정 단어 때문에 점수가 낮아야 함
    assert scored_event.score < -0.3

def test_trend_scorer_neutral(trend_scorer: TrendScorer):
    """중립적인 텍스트 점수화 테스트"""
    event = TrendEvent(source="test", text="The price of Bitcoin is currently stable.", timestamp=now(), symbol_final="BTCUSDT")
    scored_event = trend_scorer.score_event(event)
    # 중립에 가까운 점수가 나와야 함
    assert -0.2 < scored_event.score < 0.2
