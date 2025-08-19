
# ===================================================================================
#   assets/__init__.py: 애셋 로딩 유틸리티
# ===================================================================================
#
#   - `assets` 디렉토리에 있는 YAML 설정 파일들을 파이썬 객체로 로드하는
#     유틸리티 함수들을 제공합니다.
#   - 애플리케이션 시작 시 한 번만 파일을 읽어 메모리에 캐싱하여, 반복적인
#     파일 I/O를 방지하고 성능을 향상시킵니다.
#
#
import yaml
from pathlib import Path
from functools import lru_cache
from typing import Dict, List

# 애셋 파일이 위치한 디렉토리 경로
ASSETS_DIR = Path(__file__).parent

@lru_cache(maxsize=1)
def _load_yaml_file(filename: str) -> Dict | List:
    """YAML 파일을 안전하게 로드하고 그 내용을 캐시합니다."""
    file_path = ASSETS_DIR / filename
    with open(file_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def load_symbol_map() -> Dict[str, List[str]]:
    """
    `symbol_map.yml` 파일을 로드하여 심볼과 관련 키워드 맵을 반환합니다.
    
    예시 `symbol_map.yml`:
    ```yaml
    BTCUSDT:
      - $BTC
      - Bitcoin
      - #Bitcoin
    SOLUSDT:
      - $SOL
      - Solana
      - #Solana
    ```
    
    반환값: `{"BTCUSDT": ["$BTC", ...], "SOLUSDT": ["$SOL", ...]}`
    """
    return _load_yaml_file("symbol_map.yml")

@lru_cache(maxsize=1)
def load_universe() -> List[str]:
    """
    `universe.yml` 파일을 로드하여 거래 허용 심볼 목록을 반환합니다.
    
    예시 `universe.yml`:
    ```yaml
    - BTCUSDT
    - ETHUSDT
    - SOLUSDT
    ```
    
    반환값: `["BTCUSDT", "ETHUSDT", "SOLUSDT"]`
    """
    return _load_yaml_file("universe.yml")

# 모듈 로드 시점에 데이터를 캐싱
symbol_map = load_symbol_map()
universe = load_universe()
