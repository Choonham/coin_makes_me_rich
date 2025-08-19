
# Crypto Scalping Bot - FastAPI Backend

[![Python 3.11](https://img.shields.io/badge/python-3.11-blue.svg)](https://www.python.org/downloads/release/python-3110/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.109.0-blueviolet)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

실시간 SNS 트렌드와 시장 데이터를 결합하여 Bybit V5 USDT-Perp 마켓에서 스캘핑 거래를 수행하는 고성능 파이썬 백엔드 시스템입니다. React Native 앱에서의 모니터링 및 제어를 위한 REST API와 WebSocket 엔드포인트를 제공합니다.

---

## 주요 기능 (Features)

- **Bybit V5 API 통합**: Unified Trading 계정을 위한 REST 및 WebSocket v5 API 완벽 지원 (테스트넷/메인넷).
- **하이브리드 신호 전략**: 오더북 불균형을 분석하는 스캘핑 신호와 실시간 SNS/뉴스 트렌드 신호를 결합하여 거래 결정.
- **실시간 트렌드 분석**: X(Twitter), News API 등에서 키워드 기반으로 데이터를 수집, 감성 분석 및 점수화하여 거래 신호로 활용. (API 키 없을 시 모의 모드 지원)
- **강력한 리스크 관리**: 일일 손실 한도, 거래당 리스크, 최대 동시 포지션 수, 슬리피지 제어 등 다층 리스크 엔진 내장.
- **React Native 연동**: RN 앱에서 거래 봇을 제어(시작/정지/설정 변경)하고 실시간 상태(PnL, 포지션)를 모니터링하기 위한 API 및 WebSocket 엔드포인트 제공.
- **프로덕션 레디**: 비동기 처리, 자동 재시도/백오프, 비율 제한 관리, 상세 로깅(Loguru), 메트릭(Prometheus) 등 프로덕션 환경에 필수적인 기능 내장.
- **컨테이너화**: Docker 및 Docker Compose를 지원하여 일관된 개발 및 배포 환경 제공.

## 아키텍처 (Architecture)

시스템은 기능별로 분리된 모듈식 구조를 따릅니다.

```
+-----------------------+      +-------------------------+      +-----------------------+
|   React Native App    |----->|    FastAPI Backend      |<---->|      Bybit V5 API     |
+-----------------------+      | (This Project)          |      +-----------------------+
  (Control/Monitor)            |                         |        (Market/Trade Data)
                               |   +-----------------+   |                                
                               |   | API (REST/WS)   |   |                                
                               |   +-------+---------+   |                                
                               |           |             |                                
                               |   +-------v---------+   |      +-----------------------+
                               |   | Strategy Router |<------>|      Risk Engine      |
                               |   +-------+---------+   |      +-----------------------+
                               |           |             |                                
           +-------------------+-----------+-------------------+                        
           |                                   |                   |                        
+----------v----------+             +----------v----------+      +-----------------------+
| Scalping Generator  |             |   Trend Aggregator  |<---->|   Social/News Feeds   |
| (Orderbook Data)    |             | (Sentiment Signals) |      | (X, News, Facebook)   |
+---------------------+             +---------------------+      +-----------------------+
```

## 시작하기 (Getting Started)

### 1. 사전 요구사항

- Python 3.11+
- Docker & Docker Compose (권장)
- Bybit API Key (V5 Unified Trading)
- (선택) X, News API 등 트렌드 분석용 API 키

### 2. 설치 및 설정

**a. 로컬 개발 환경 (가상환경 사용)**

1.  **저장소 클론**:
    ```bash
    git clone <repository_url>
    cd <repository_directory>
    ```

2.  **`.env` 파일 생성**:
    `.env.example` 파일을 복사하여 `.env` 파일을 만들고, Bybit API 키 등 필요한 환경 변수를 채워넣습니다.
    ```bash
    cp .env.example .env
    nano .env
    ```

3.  **가상환경 설정 및 의존성 설치 (Makefile 사용)**:
    ```bash
    make setup
    ```

4.  **가상환경 활성화**:
    ```bash
    source venv/bin/activate
    ```

**b. Docker 환경**

1.  **`.env` 파일 생성**: 위와 동일하게 `.env` 파일을 생성하고 내용을 채웁니다.
2.  **Docker Compose 실행**:
    ```bash
    make compose-up
    ```
    이 명령어는 Docker 이미지를 빌드하고 백그라운드에서 컨테이너를 실행합니다.

### 3. 서버 실행

- **로컬 환경**:
  ```bash
  make serve
  ```
- **Docker 환경**: `make compose-up`으로 이미 실행 중입니다. 로그를 확인하려면:
  ```bash
  make compose-logs
  ```

서버가 정상적으로 실행되면 `http://localhost:8000` 에서 API가, `http://localhost:8000/docs` 에서 Swagger UI 문서를 확인할 수 있습니다.

## API 및 WebSocket 사용법 (for React Native)

### 인증 (Authentication)

먼저, 로그인하여 JWT 토큰을 발급받아야 합니다. 이 토큰은 이후 모든 보호된 API 요청의 `Authorization` 헤더에 포함되어야 합니다.

**요청: `POST /api/v1/auth/login`**

```bash
cURL 예시
curl -X POST "http://localhost:8000/api/v1/auth/login" \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -d "username=admin&password=admin"
```

**응답:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```
RN 앱에서는 이 `access_token`을 SecureStore와 같은 안전한 곳에 저장하세요.

### 실시간 상태 모니터링 (WebSocket)

`SystemState` 객체가 1초마다 JSON 형태로 브로드캐스트됩니다.

**엔드포인트: `ws://localhost:8000/ws/dashboard`**

```javascript
// React Native WebSocket 클라이언트 예시
const ws = new WebSocket('ws://localhost:8000/ws/dashboard');

ws.onopen = () => {
  console.log('Dashboard WebSocket connected');
};

ws.onmessage = (e) => {
  // e.data는 SystemState 모델의 JSON 문자열입니다.
  const systemState = JSON.parse(e.data);
  console.log('Received state update:', systemState);
  // 이 데이터를 사용하여 RN 앱의 UI를 업데이트합니다.
  // 예: setPnl(systemState.pnl_day), setPositions(systemState.active_positions)
};

ws.onerror = (e) => {
  console.log('WebSocket error:', e.message);
};

ws.onclose = (e) => {
  console.log('WebSocket closed', e.code, e.reason);
};
```

### 시스템 제어 (Control)

**거래 시작: `POST /api/v1/control/start`**
```javascript
// RN fetch 예시
const startBot = async (token) => {
  const response = await fetch('http://localhost:8000/api/v1/control/start', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
    },
  });
  const data = await response.json();
  console.log(data.message); // "Strategy started successfully."
};
```

**거래 정지: `POST /api/v1/control/stop`**

**리스크 설정 변경: `POST /api/v1/control/config/risk`**
- **Body**: `RiskConfig` 모델 (JSON)

## 코드 품질 및 테스트

- **Linter 실행**:
  ```bash
  make lint
  ```
- **코드 포맷팅**:
  ```bash
  make fmt
  ```
- **단위 테스트 실행**:
  ```bash
  make test
  ```

## Postman Collection

API를 쉽게 테스트할 수 있도록 아래 JSON을 Postman으로 가져오세요.

```json
{
	"info": {
		"_postman_id": "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
		"name": "Crypto Scalping Bot API",
		"schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
	},
	"item": [
		{
			"name": "Authentication",
			"item": [
				{
					"name": "Login",
					"request": {
						"method": "POST",
						"header": [],
						"body": {
							"mode": "urlencoded",
							"urlencoded": [
								{"key": "username", "value": "admin", "type": "text"},
								{"key": "password", "value": "admin", "type": "text"}
							]
						},
						"url": {
							"raw": "http://localhost:8000/api/v1/auth/login",
							"protocol": "http",
							"host": ["localhost"],
							"port": "8000",
							"path": ["api", "v1", "auth", "login"]
						}
					},
					"response": []
				}
			]
		},
		{
			"name": "Public",
			"item": [
				{
					"name": "Get Status",
					"request": {
						"method": "GET",
						"header": [],
						"url": {
							"raw": "http://localhost:8000/api/v1/public/status",
							"protocol": "http",
							"host": ["localhost"],
							"port": "8000",
							"path": ["api", "v1", "public", "status"]
						}
					},
					"response": []
				}
			]
		},
		{
			"name": "Control (Auth Required)",
			"item": [
				{
					"name": "Start Strategy",
					"request": {
						"auth": {"type": "bearer", "bearer": [{"key": "token", "value": "YOUR_JWT_TOKEN", "type": "string"}]},
						"method": "POST",
						"header": [],
						"url": {
							"raw": "http://localhost:8000/api/v1/control/start",
							"protocol": "http",
							"host": ["localhost"],
							"port": "8000",
							"path": ["api", "v1", "control", "start"]
						}
					},
					"response": []
				}
			]
		}
	]
}
```
```
