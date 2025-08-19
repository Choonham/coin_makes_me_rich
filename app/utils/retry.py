
# ===================================================================================
#   utils/retry.py: 비동기 재시도 데코레이터
# ===================================================================================
#
#   - 비동기(async) 함수가 실패했을 때 자동으로 재시도하는 로직을 제공하는
#     데코레이터입니다.
#   - 네트워크 오류나 일시적인 API 문제 등 예측 불가능한 외부 요인에 대한
#     프로그램의 회복탄력성(resilience)을 높이기 위해 사용됩니다.
#
#   **주요 기능:**
#   - **데코레이터 방식**: `@async_retry()` 형태로 어떤 비동기 함수에든 쉽게 적용할 수 있습니다.
#   - **지수 백오프(Exponential Backoff)**: 재시도할 때마다 대기 시간을 점진적으로
#     늘려(예: 2초, 4초, 8초...), 실패가 반복될 경우 시스템에 가해지는 부하를 줄입니다.
#   - **설정 가능**: 재시도 횟수(`attempts`), 초기 대기 시간(`delay`), 백오프 계수(`backoff`)
#     등을 데코레이터의 인자로 전달하여 유연하게 설정할 수 있습니다.
#
#   **사용 예시:**
#   ```python
#   from app.utils.retry import async_retry
#
#   @async_retry(attempts=5, delay=1, backoff=2)
#   async def fetch_data_from_unreliable_api():
#       # ... API 호출 로직 ...
#       pass
#   ```
#   위 예시는 `fetch_data_from_unreliable_api` 함수가 실패하면, 1초, 2초, 4초, 8초,
#   16초의 대기 시간을 가지며 최대 5번까지 재시도합니다.
#
#
import asyncio
import functools
from typing import Callable, Any
from loguru import logger

def async_retry(attempts: int = 3, delay: float = 1.0, backoff: float = 2.0):
    """
    비동기 함수에 대한 재시도 로직을 적용하는 데코레이터.

    :param attempts: 최대 재시도 횟수.
    :param delay: 초기 대기 시간 (초).
    :param backoff: 다음 대기 시간을 계산하기 위한 배수.
    """
    def decorator(func: Callable[..., Any]):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            current_delay = delay
            for i in range(attempts):
                try:
                    return await func(*args, **kwargs)
                except asyncio.CancelledError:
                    # 태스크 취소 요청은 재시도하지 않고 즉시 전파합니다.
                    raise
                except Exception as e:
                    logger.warning(
                        f"Function '{func.__name__}' failed (attempt {i + 1}/{attempts}). "
                        f"Retrying in {current_delay:.2f}s. Error: {e}"
                    )
                    if i == attempts - 1:
                        logger.error(f"Function '{func.__name__}' failed after {attempts} attempts.")
                        raise
                    
                    await asyncio.sleep(current_delay)
                    current_delay *= backoff
        return wrapper
    return decorator
