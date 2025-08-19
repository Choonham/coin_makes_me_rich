
# ===================================================================================
#   trend/scorer.py: 트렌드 이벤트 점수화기
# ===================================================================================
#
#   - 트렌드 이벤트의 텍스트 내용을 분석하여 감성(긍정/부정) 점수와 신뢰도 점수를
#     부여하는 역할을 합니다.
#   - 이 점수는 `StrategyRouter`에서 거래 신호의 강도를 판단하는 데 사용됩니다.
#
#   **주요 기능:**
#   - **감성 분석 (Sentiment Analysis)**:
#     - `vaderSentiment` 라이브러리를 사용하여 텍스트의 감성을 분석합니다. VADER는
#       소셜 미디어 텍스트에 특화되어 있으며, 이모티콘이나 약어 등도 잘 처리합니다.
#     - 분석 결과로 복합 점수(compound score)를 생성하며, 이 값은 -1(매우 부정)에서
#       +1(매우 긍정) 사이의 범위를 가집니다.
#   - **규칙 기반 점수 조정 (Rule-based Adjustment)**:
#     - VADER 점수만으로는 부족할 수 있는 금융 도메인의 뉘앙스를 보강하기 위해,
#       특정 키워드(예: `pump`, `moon`, `partnership` vs `dump`, `scam`, `hack`)의
#       존재 여부에 따라 점수를 가감하는 규칙을 추가합니다.
#   - **신뢰도 계산**: 감성 점수의 절대값(강도)을 기반으로 신뢰도 점수를 계산합니다.
#     매우 긍정적이거나 매우 부정적인 텍스트가 중립적인 텍스트보다 높은 신뢰도를 가집니다.
#
#   **데이터 흐름:**
#   1. `TrendAggregator`가 `score_event()` 함수를 호출합니다.
#   2. `score_event()`는 `TrendEvent`의 텍스트를 `vader`와 규칙 기반 로직으로 처리합니다.
#   3. 계산된 `score`와 `confidence`를 `TrendEvent`의 해당 필드에 채워넣어 반환합니다.
#
#
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from loguru import logger

from app.utils.typing import TrendEvent

class TrendScorer:
    """
    텍스트 기반 트렌드 이벤트의 감성 점수와 신뢰도를 계산합니다.
    """
    def __init__(self):
        self.analyzer = SentimentIntensityAnalyzer()
        # 금융/암호화폐 도메인에 특화된 키워드와 점수를 추가하여 VADER를 확장합니다.
        self.analyzer.lexicon.update(self._get_custom_lexicon())
        logger.info("TrendScorer initialized with custom lexicon.")

    def _get_custom_lexicon(self) -> dict:
        """암호화폐 도메인에 특화된 감성 사전을 정의합니다."""
        return {
            # Positive keywords
            "moon": 3.0, "mooning": 3.0, "pump": 2.5, "pumping": 2.5,
            "bullish": 2.5, "long": 2.0, "buy": 2.0, "undervalued": 2.5,
            "breakout": 2.5, "partnership": 2.8, "listing": 2.5, "upgrade": 2.0,
            "diamond hands": 3.5, "hodl": 2.0, "to the moon": 3.5,
            
            # Negative keywords
            "dump": -2.5, "dumping": -2.5, "bearish": -2.5, "short": -2.0,
            "sell": -2.0, "overvalued": -2.5, "scam": -3.5, "hack": -3.0,
            "rug pull": -4.0, "bubble": -2.5, "correction": -2.0,
            "paper hands": -3.0, "fud": -2.0
        }

    def score_event(self, event: TrendEvent) -> TrendEvent:
        """
        TrendEvent의 텍스트를 분석하여 감성 점수와 신뢰도를 부여합니다.

        :param event: 점수를 매길 TrendEvent 객체.
        :return: `score`와 `confidence`가 채워진 TrendEvent 객체.
        """
        if not event.text:
            event.score = 0.0
            event.confidence = 0.0
            return event

        # VADER를 사용하여 감성 점수 계산
        # compound 점수는 -1(부정) ~ +1(긍정) 범위의 정규화된 점수입니다.
        vader_scores = self.analyzer.polarity_scores(event.text)
        compound_score = vader_scores['compound']

        # 신뢰도 계산: 감성의 강도(중립에서 얼마나 먼지)를 신뢰도로 사용합니다.
        # compound 점수의 절대값을 신뢰도로 사용할 수 있습니다.
        confidence = abs(compound_score)

        # 최종 점수와 신뢰도를 이벤트 객체에 할당
        event.score = compound_score
        event.confidence = confidence

        logger.debug(f"Scored event for symbol '{event.symbol_final}': "
                     f"Score={event.score:.2f}, Conf={event.confidence:.2f}, Text='{event.text[:50]}...'")
        
        return event

# 전역 인스턴스 생성
trend_scorer = TrendScorer()
