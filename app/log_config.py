
# ===================================================================================
#   logging.py: 애플리케이션 로깅 설정
# ===================================================================================
#
#   - Loguru 라이브러리를 사용하여 애플리케이션 전반의 로깅을 구성합니다.
#   - 콘솔(stdout) 및 파일 로깅을 모두 지원합니다.
#   - 설정(config.py)에 따라 로그 레벨, 파일 경로, JSON 포맷 등을 동적으로 설정합니다.
#
#   **주요 기능:**
#   - **간결한 설정**: `configure_logging` 함수 하나로 모든 로깅 설정을 완료합니다.
#   - **포맷 커스터마이징**: 로그 메시지에 시간, 레벨, 모듈, 함수, 라인 번호 등 상세 정보를 포함합니다.
#   - **파일 로테이션**: 로그 파일이 너무 커지는 것을 방지하기 위해 파일 크기나 시간에 따라 자동으로 새 파일을 생성합니다. (예: `rotation="10 MB"`)
#   - **JSON 로깅**: `LOG_JSON_FORMAT=True`로 설정 시, 구조화된 로깅(JSON)을 활성화하여 로그 분석 시스템(예: ELK, Datadog)과의 연동을 용이하게 합니다.
#   - **예외 추적**: 예외 발생 시 스택 트레이스를 자동으로 포함하여 디버깅을 돕습니다.
#
#   **사용법:**
#   - `main.py`의 `startup` 이벤트에서 `configure_logging()`을 호출하여 초기화합니다.
#   - 다른 모듈에서는 `from loguru import logger`를 임포트하여 `logger.info(...)`, `logger.error(...)` 등으로 사용합니다.
#
#
import sys
from pathlib import Path
from loguru import logger
from app.config import settings

def configure_logging():
    """
    Loguru를 사용하여 애플리케이션의 로거를 설정합니다.
    
    설정 파일(`config.py`)의 값을 기반으로 다음을 구성합니다:
    - 기존 로거 핸들러 제거 및 재설정
    - 콘솔(stderr) 로깅 핸들러 추가
    - 파일 로깅 핸들러 추가 (설정이 활성화된 경우)
    - JSON 형식 출력 옵션
    """
    # 1. 기본 로거 핸들러를 제거하여 중복 출력을 방지합니다.
    logger.remove()

    # 2. 콘솔(stderr)에 출력할 로거를 추가합니다.
    #    - 색상(colorize)을 사용하여 로그 레벨을 시각적으로 구분합니다.
    #    - `settings.LOG_LEVEL`에 따라 최소 로그 레벨을 설정합니다.
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL.upper(),
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
               "<level>{level: <8}</level> | "
               "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        colorize=True,
    )

    # 3. 파일에 로그를 기록하도록 설정합니다 (LOG_TO_FILE=True인 경우).
    if settings.LOG_TO_FILE:
        log_file = Path(settings.LOG_FILE_PATH)
        # 로그 파일을 저장할 디렉토리가 없으면 생성합니다.
        log_file.parent.mkdir(parents=True, exist_ok=True)

        logger.add(
            log_file,
            level=settings.LOG_LEVEL.upper(),
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
            rotation="10 MB",  # 파일 크기가 10MB에 도달하면 새 파일 생성
            retention="7 days", # 최대 7일간의 로그 파일 보관
            compression="zip", # 오래된 로그 파일은 zip으로 압축
            serialize=settings.LOG_JSON_FORMAT, # True일 경우 JSON 형식으로 저장
            enqueue=True,      # 비동기 및 다중 프로세스 환경에서 안전하게 로깅
            backtrace=True,    # 예외 발생 시 전체 스택 트레이스 기록
            diagnose=True,     # 예외 진단 정보 추가
        )

    logger.info("Logger configured.")
    logger.info(f"Log level set to {settings.LOG_LEVEL.upper()}")
    if settings.LOG_TO_FILE:
        logger.info(f"Logging to file: {settings.LOG_FILE_PATH}")
    if settings.LOG_JSON_FORMAT:
        logger.info("JSON log format enabled.")

# 초기 설정 (애플리케이션 시작 시 명시적으로 호출하는 것을 권장)
# configure_logging()
