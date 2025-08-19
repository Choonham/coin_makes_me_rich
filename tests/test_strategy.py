
# ===================================================================================
#   tests/test_strategy.py: 전략 및 리스크 엔진 단위 테스트
# ===================================================================================
#
#   - 거래 시스템의 핵심 두뇌인 `StrategyRouter`와 안전장치인 `RiskEngine`의
#     핵심 로직을 단위 테스트합니다.
#   - 이 테스트들은 외부 상태나 서비스(Bybit, state_store)로부터 완전히 격리되어야 하며,
#     이를 위해 모의(mock) 객체를 적극적으로 사용합니다.
#
#   **테스트 대상:**
#   - **RiskEngine**:
#     - `validate_trade`: 주어진 신호와 현재 상태(모의)에 대해 정확한 거래 결정을 내리는지 테스트합니다.
#       - **허용 케이스**: 모든 조건(유니버스, 포지션 수, 슬리피지 등)을 만족할 때 `allow=True`와 함께 정확한 주문 수량을 반환하는지 확인합니다.
#       - **거절 케이스**: 각 리스크 규칙(일일 손실 한도, 최대 포지션, 슬리피지 초과 등)이 위반되었을 때 `allow=False`와 올바른 거절 사유를 반환하는지 확인합니다.
#   - **StrategyRouter**:
#     - `_evaluate_symbol`: 스캘핑 신호와 트렌드 신호가 주어졌을 때, 이를 올바르게 결합하여 최종 신호를 생성하는지 테스트합니다.
#     - `_execute_trade`: `RiskEngine`이 거래를 허용했을 때, `BybitClient`의 `place_market_order` 메서드를 정확한 파라미터로 호출하는지 확인합니다.
#
#   **테스트 방법:**
#   - `pytest.fixture`를 사용하여 테스트에 필요한 `RiskEngine`과 `StrategyRouter`의 모의 버전 인스턴스를 생성합니다.
#   - `unittest.mock.patch`를 사용하여 `state_store`, `BybitClient` 등 외부 의존성을 모의 객체로 대체합니다.
#   - 다양한 시나리오(예: 계좌 잔고, 현재 포지션, PnL)를 가정한 모의 상태를 설정하고, 각 시나리오에서 로직이 예상대로 동작하는지 검증합니다.
#
#
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from app.risk.engine import RiskEngine, TradeDecision
from app.strategy.router import StrategyRouter
from app.utils.typing import Signal, Side

@pytest.fixture
def risk_engine() -> RiskEngine:
    """테스트용 RiskEngine 인스턴스를 생성합니다."""
    # state_store의 의존성을 제거하기 위해 patch 사용
    with patch("app.risk.engine.state_store"), patch("app.risk.engine.load_universe", return_value=["BTCUSDT"]):
        engine = RiskEngine()
        # 테스트를 위해 리스크 설정을 간단하게 고정
        engine.config.risk_per_trade = 0.01 # 1%
        engine.config.default_sl_bps = 100 # 1%
        engine.config.max_slippage_bps = 50
        engine.config.max_active_symbols = 1
        return engine

# --- RiskEngine 테스트 --- 

def test_risk_engine_allow_trade(risk_engine: RiskEngine):
    """RiskEngine이 정상적인 거래를 허용하는지 테스트"""
    signal = Signal(symbol="BTCUSDT", side=Side.BUY, price=50000, reason="test", strength=0.8, signal_type="combined")
    
    # state_store와 get_orderbook을 모의 처리
    with patch("app.risk.engine.state_store") as mock_store:
        # 모의 상태 설정
        mock_store.get_system_state.return_value.pnl_day = 0
        mock_store.get_system_state.return_value.active_positions = []
        mock_store.has_position.return_value = False
        mock_store.get_orderbook.return_value = {'a': [["50001", "10"]], 'b': [["50000", "10"]]} # no slippage

        decision = risk_engine.validate_trade(signal)

        assert decision.allow is True
        assert decision.reason == "All risk checks passed"
        # 수량 계산 검증: 10000(가상 잔고) * 0.01(risk) / (50001 * 0.01(sl)) = 100 / 500.01 ~= 0.1999
        assert decision.qty == pytest.approx(0.1999, abs=1e-4)

def test_risk_engine_reject_daily_loss_limit(risk_engine: RiskEngine):
    """일일 손실 한도 도달 시 거래를 거절하는지 테스트"""
    signal = Signal(symbol="BTCUSDT", side=Side.BUY, price=50000, reason="test", strength=0.8, signal_type="combined")
    risk_engine.config.day_loss_limit_usd = 100

    with patch("app.risk.engine.state_store") as mock_store:
        mock_store.get_system_state.return_value.pnl_day = -101
        decision = risk_engine.validate_trade(signal)
        assert decision.allow is False
        assert decision.reason == "Daily loss limit reached"

def test_risk_engine_reject_max_symbols(risk_engine: RiskEngine):
    """최대 포지션 수 도달 시 거래를 거절하는지 테스트"""
    signal = Signal(symbol="BTCUSDT", side=Side.BUY, price=50000, reason="test", strength=0.8, signal_type="combined")
    
    with patch("app.risk.engine.state_store") as mock_store:
        mock_store.get_system_state.return_value.pnl_day = 0
        mock_store.get_system_state.return_value.active_positions = [MagicMock()] # 이미 1개 포지션 보유
        mock_store.has_position.return_value = False

        decision = risk_engine.validate_trade(signal)
        assert decision.allow is False
        assert decision.reason == f"Max active symbols ({risk_engine.config.max_active_symbols}) reached"

# --- StrategyRouter 테스트 --- 

@pytest.fixture
def strategy_router() -> StrategyRouter:
    """테스트용 StrategyRouter 인스턴스를 생성합니다."""
    mock_bybit_client = AsyncMock()
    mock_trend_aggregator = MagicMock()
    router = StrategyRouter(bybit_client=mock_bybit_client, trend_aggregator=mock_trend_aggregator)
    return router

@pytest.mark.asyncio
async def test_strategy_router_execute_trade(strategy_router: StrategyRouter):
    """StrategyRouter가 리스크 검증 통과 후 주문을 실행하는지 테스트"""
    signal = Signal(symbol="BTCUSDT", side=Side.BUY, price=50000, reason="test", strength=0.8, signal_type="combined")
    trade_decision = TradeDecision(allow=True, reason="test", qty=0.2)

    # RiskEngine과 BybitClient를 모의 객체로 설정
    strategy_router.risk_engine = MagicMock()
    strategy_router.risk_engine.validate_trade.return_value = trade_decision
    strategy_router.bybit_client.place_market_order = AsyncMock(return_value={"orderId": "123"})

    with patch("app.strategy.router.state_store", new_callable=AsyncMock) as mock_store:
        await strategy_router._execute_trade(signal)

        # RiskEngine의 validate_trade가 신호와 함께 호출되었는지 확인
        strategy_router.risk_engine.validate_trade.assert_called_once_with(signal)
        
        # BybitClient의 place_market_order가 올바른 인자와 함께 호출되었는지 확인
        strategy_router.bybit_client.place_market_order.assert_awaited_once_with(
            symbol="BTCUSDT",
            side=Side.BUY,
            qty=0.2,
            notional=0,
            sl_bps=strategy_router.risk_engine.config.default_sl_bps,
            tp_bps=strategy_router.risk_engine.config.default_tp_bps,
            reduce_only=False
        )
        # state_store에 거래가 기록되었는지 확인
        mock_store.add_trade.assert_awaited_once()
