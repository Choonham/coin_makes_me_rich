
# ====================================================================
# Makefile for Development
# ====================================================================
#
# - 개발 과정에서 자주 사용되는 명령어들을 단순화하기 위한 Makefile입니다.
# - `make <target>` 형태로 간편하게 명령을 실행할 수 있습니다.

# --- 변수 정의 ---
PYTHON = python3
# 가상 환경 디렉토리
VENV_DIR = venv
# 가상 환경의 파이썬 실행 파일
VENV_PYTHON = $(VENV_DIR)/bin/python
# Docker Compose 명령어
COMPOSE = docker-compose

# 기본 목표 (make만 입력 시 실행)
.DEFAULT_GOAL := help

# --- 가상 환경 및 의존성 관리 ---
.PHONY: setup
setup:
	@echo "--> Setting up Python virtual environment..."
	$(PYTHON) -m venv $(VENV_DIR)
	@echo "--> Installing dependencies from requirements.txt..."
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements.txt
	@echo "
Setup complete. Activate the virtual environment with:
source $(VENV_DIR)/bin/activate
"

.PHONY: install
install:
	@echo "--> Installing/updating dependencies..."
	$(VENV_PYTHON) -m pip install -r requirements.txt

# --- 개발 서버 실행 ---
.PHONY: serve
serve: 
	@echo "--> Starting FastAPI development server with Uvicorn..."
	$(VENV_PYTHON) -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# --- 코드 품질 및 테스트 ---
.PHONY: lint
lint:
	@echo "--> Running linter (ruff)..."
	$(VENV_PYTHON) -m ruff check .

.PHONY: fmt
fmt:
	@echo "--> Formatting code with (ruff format)..."
	$(VENV_PYTHON) -m ruff format .

.PHONY: test
test:
	@echo "--> Running tests with pytest..."
	$(VENV_PYTHON) -m pytest

# --- Docker Compose 관리 ---
.PHONY: compose-up
compose-up:
	@echo "--> Starting services with Docker Compose..."
	$(COMPOSE) up -d --build

.PHONY: compose-down
compose-down:
	@echo "--> Stopping services with Docker Compose..."
	$(COMPOSE) down

.PHONY: compose-logs
compose-logs:
	@echo "--> Tailing logs from Docker Compose..."
	$(COMPOSE) logs -f api

# --- 도움말 ---
.PHONY: help
help:
	@echo "Available commands:"
	@echo "  setup          - Set up the Python virtual environment and install dependencies."
	@echo "  install        - Install/update dependencies from requirements.txt."
	@echo "  serve          - Start the FastAPI development server."
	@echo "  lint           - Check code for linting errors with ruff."
	@echo "  fmt            - Format code with ruff."
	@echo "  test           - Run unit tests with pytest."
	@echo "  compose-up     - Build and start all services with Docker Compose in detached mode."
	@echo "  compose-down   - Stop and remove all services started with Docker Compose."
	@echo "  compose-logs   - Follow logs from the running api service."

