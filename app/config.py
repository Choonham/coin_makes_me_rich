# ===================================================================================
#   config.py: 환경 변수 및 설정 관리
# ===================================================================================
#
#   - Pydantic-Settings를 사용하여 .env 파일 또는 환경 변수에서 설정을 로드합니다.
#   - 애플리케이션 전반에서 사용되는 모든 설정 값을 중앙에서 관리합니다.
#   - 각 설정 값에 대한 타입 힌트, 기본값, 설명을 제공하여 명확성을 높입니다.
#
#   **주요 기능:**
#   - **타입 안전성**: Pydantic 모델을 사용하여 설정 값의 타입을 강제하고 유효성을 검사합니다.
#   - **중앙 관리**: 모든 환경 변수를 한 곳에서 정의하고 관리하여 유지보수성을 향상시킵니다.
#   - **기본값 제공**: 필수적이지 않은 설정에 대해서는 합리적인 기본값을 제공하여 설정 과정을 단순화합니다.
#   - **.env 파일 지원**: `python-dotenv`와 통합되어 프로젝트 루트의 `.env` 파일을 자동으로 로드합니다.
#
#   **사용법:**
#   - 다른 모듈에서는 `from app.config import settings`와 같이 임포트하여 사용합니다.
#   - 예: `settings.BYBIT_API_KEY`, `settings.DAY_LOSS_LIMIT_USD`
#
#
from pathlib import Path
from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# 프로젝트 루트 디렉토리 경로를 계산
# 이 파일(config.py)은 app/ 안에 있으므로, parent.parent는 프로젝트 루트를 가리킵니다.
PROJECT_ROOT = Path(__file__).parent.parent

class AppSettings(BaseSettings):
    """
    애플리케이션의 모든 설정을 담는 Pydantic 모델입니다.
    환경 변수 이름은 대소문자를 구분하지 않습니다. (e.g., bybit_api_key == BYBIT_API_KEY)
    """

    # .env 파일의 경로와 인코딩을 지정합니다.
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8")

    # --- Bybit API 설정 ---
    # 실제 거래 및 데이터 조회를 위한 Bybit API 키와 시크릿
    # .env 파일에 반드시 설정해야 하는 필수 값입니다.
    BYBIT_API_KEY: SecretStr
    BYBIT_API_SECRET: SecretStr
    # 테스트넷 사용 여부를 결정합니다. True일 경우 테스트넷으로 접속합니다.
    BYBIT_TESTNET: bool = True

    # --- JWT 및 인증 설정 ---
    # API 보안을 위한 JWT(JSON Web Token) 시크릿 키
    JWT_SECRET: SecretStr = "a_very_secret_key_that_should_be_changed"
    # JWT 만료 시간(분 단위)
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    # JWT 알고리즘
    JWT_ALGORITHM: str = "HS256"
    # 시스템 관리를 위한 초기 관리자 계정 정보
    # 보안을 위해 비밀번호는 해시된 형태로 저장하고 사용하는 것이 좋습니다.
    ADMIN_USER: str = "admin"
    ADMIN_PASS_HASH: str = "$2b$12$EixZAxWfBCS.xvmj2dIeA.L4SgPp2jP1mAnGzKi/fB.5P8S.J5.3u" # default: "admin"

    # --- 리스크 관리 설정 ---
    # 일일 최대 손실 한도 (USD 기준). 이 금액 도달 시 모든 거래가 중지됩니다.
    DAY_LOSS_LIMIT_USD: float = 200.0
    # 일일 목표 수익률 (총 자산 대비 %). 이 수익률 도달 시 모든 신규 거래가 중지됩니다.
    DAY_PROFIT_TARGET_PCT: float = 1.0 # 1.0 = 1%
    # 단일 거래 당 리스크 비율 (전체 자산 대비). 주문 수량 계산에 사용됩니다.
    RISK_PER_TRADE: float = 0.95  # 95%
    # 최대 동시 보유 가능한 포지션(심볼)의 수
    MAX_ACTIVE_SYMBOLS: int = 5
    # 지정가 대비 시장가 주문의 최대 슬리피지 허용 범위 (단위: BPS, 1BPS = 0.01%)
    MAX_SLIPPAGE_BPS: int = 100 # 1.0%
    # 기본 익절(Take Profit) BPS
    DEFAULT_TP_BPS: int = 50 # 0.5%
    # 기본 손절(Stop Loss) BPS
    DEFAULT_SL_BPS: int = 25 # 0.25%
    # 추적 손절(Trailing Stop) 시작 BPS
    TRAILING_SL_BPS: int = 30 # 0.3%

    # --- 트렌드 분석 커넥터 API 키 (선택 사항) ---
    # 키가 제공되지 않으면 해당 커넥터는 모의(mock) 모드로 동작합니다.
    NEWS_API_KEY: SecretStr | None = None
    X_BEARER_TOKEN: SecretStr | None = None # Twitter API v2 Bearer Token
    FB_APP_ID: str | None = None
    FB_APP_SECRET: SecretStr | None = None

    # --- 데이터베이스 및 이벤트 큐 설정 (선택 사항) ---
    # 프로젝트 루트에 `trading_bot.db`라는 SQLite 파일을 생성하도록 절대 경로를 사용합니다.
    DB_URL: str = f"sqlite+aiosqlite:///{PROJECT_ROOT.joinpath('trading_bot.db')}"
    # Redis 사용 예: "redis://localhost:6379/0"
    REDIS_URL: AnyHttpUrl | None = None
    # Kafka 사용 예: "kafka://localhost:9092"
    KAFKA_BROKER_URL: str | None = None

    # --- 로깅 설정 ---
    LOG_LEVEL: str = "DEBUG"
    LOG_TO_FILE: bool = True
    LOG_FILE_PATH: str = str(PROJECT_ROOT / "logs/app.log")
    LOG_JSON_FORMAT: bool = False # JSON 형식으로 로그를 남길지 여부
    MAX_HOLDING_TIME_SECONDS: int

# 설정 객체 인스턴스 생성
# 이 `settings` 객체를 다른 모듈에서 임포트하여 사용합니다.
settings = AppSettings()