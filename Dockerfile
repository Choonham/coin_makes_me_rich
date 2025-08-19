
# ====================================================================
# Dockerfile for Production
# ====================================================================
#
# - 이 Dockerfile은 프로덕션 환경을 위한 최적화된 Docker 이미지를 생성합니다.
# - 멀티스테이지 빌드(Multi-stage build)를 사용하여 최종 이미지의 크기를 줄이고
#   보안을 강화합니다.

# --- 1. Build Stage --- #
# 이 단계에서는 의존성을 설치하고 빌드합니다.
FROM python:3.11-slim as builder

# 작업 디렉토리 설정
WORKDIR /usr/src/app

# 시스템 패키지 업데이트 및 빌드에 필요한 도구 설치
# (특정 라이브러리가 C 확장을 빌드해야 할 경우 필요)
RUN apt-get update && apt-get install -y --no-install-recommends     build-essential     && rm -rf /var/lib/apt/lists/*

# 가상 환경 생성
ENV VIRTUAL_ENV=/opt/venv
RUN python3 -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# 의존성 파일 복사 및 설치
# requirements.txt만 먼저 복사하여 Docker의 레이어 캐시를 활용합니다.
# requirements.txt가 변경되지 않으면, 이 레이어는 재사용됩니다.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- 2. Final Stage --- #
# 이 단계에서는 빌드된 의존성과 애플리케이션 코드를 가져와 최종 이미지를 만듭니다.
FROM python:3.11-slim

# 보안을 위해 non-root 유저 생성 및 사용
RUN groupadd -r appuser && useradd --no-log-init -r -g appuser appuser

# 작업 디렉토리 설정
WORKDIR /home/appuser/app

# Build stage에서 생성된 가상 환경(설치된 패키지)을 복사
COPY --from=builder /opt/venv /opt/venv

# 애플리케이션 코드 복사
COPY ./app ./app

# 파일 소유권을 non-root 유저에게 부여
RUN chown -R appuser:appuser /home/appuser/app

# non-root 유저로 전환
USER appuser

# 가상 환경 경로 설정
ENV PATH="/opt/venv/bin:$PATH"

# 컨테이너 실행 시 실행될 기본 명령어
# Uvicorn을 사용하여 FastAPI 앱을 실행합니다.
# --host 0.0.0.0: 모든 네트워크 인터페이스에서 접속을 허용 (Docker 외부에서 접근 가능)
# --port 8000: 8000번 포트 사용
# --workers 2: 워커 프로세스 수 (CPU 코어 수에 맞게 조정)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
