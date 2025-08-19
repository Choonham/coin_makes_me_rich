
# ===================================================================================
#   utils/math.py: 수학 관련 유틸리티 함수
# ===================================================================================
#
#   - 애플리케이션에서 공통적으로 사용되는 수학 계산 함수들을 모아놓은 모듈입니다.
#
#   **주요 기능:**
#   - `calculate_percentage_change`: 두 값 사이의 백분율 변화를 계산합니다.
#   - `apply_bps`: 주어진 값에 BPS(Basis Points)를 적용하여 새로운 값을 계산합니다.
#     (예: 가격에 손절/익절 BPS를 적용하여 실제 가격을 계산)
#
#
from app.utils.typing import Side

def calculate_percentage_change(initial_value: float, final_value: float) -> float:
    """
    초기값과 최종값 사이의 백분율(%) 변화를 계산합니다.
    """
    if initial_value == 0:
        return float('inf') if final_value > 0 else 0.0
    return ((final_value - initial_value) / initial_value) * 100

def apply_bps(value: float, bps: int, side: Side) -> float:
    """
    주어진 값에 BPS를 적용합니다. 손절/익절 가격 계산에 사용됩니다.
    1 BPS = 0.01% = 0.0001

    :param value: 기준 가격
    :param bps: 적용할 BPS 값
    :param side: 거래 방향. 'Buy' 포지션의 경우 TP는 가격이 오르고 SL은 내립니다.
                 'Sell' 포지션의 경우 그 반대입니다.
    :return: BPS가 적용된 새로운 가격
    """
    bps_multiplier = bps / 10000.0
    if side == Side.BUY:
        # 롱 포지션: TP는 기준가보다 높고, SL은 낮다.
        # 이 함수는 TP/SL 가격을 계산하므로, TP를 계산할 땐 더하고, SL을 계산할 땐 뺀다.
        # 하지만 보통 TP와 SL bps는 양수로 들어오므로, 부호는 외부에서 결정해야 한다.
        # 여기서는 bps가 항상 양수라고 가정하고, TP/SL 방향을 결정한다.
        # TP: value * (1 + bps_multiplier), SL: value * (1 - bps_multiplier)
        # 이 함수는 단순히 변화량을 적용하는 것으로 단순화한다.
        # TP/SL 결정은 호출하는 쪽에서 bps에 부호를 붙여 전달하는 것으로 가정한다.
        return value * (1 + bps_multiplier)
    else: # side == Side.SELL
        # 숏 포지션: TP는 기준가보다 낮고, SL은 높다.
        # TP: value * (1 - bps_multiplier), SL: value * (1 + bps_multiplier)
        return value * (1 - bps_multiplier)

def safe_division(numerator: float, denominator: float) -> float:
    """
    0으로 나누는 것을 방지하는 안전한 나눗셈 함수.
    """
    if denominator == 0:
        return 0.0
    return numerator / denominator
