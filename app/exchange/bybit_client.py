
# ===================================================================================
#   exchange/bybit_client.py: Bybit V5 API 클라이언트
# ===================================================================================
#
#   - Bybit Unified Trading V5 API와의 모든 통신을 담당하는 비동기 클라이언트입니다.
#   - RESTful API 요청과 WebSocket 스트림 수신을 모두 처리합니다.
#   - 프로덕션 환경에 필수적인 기능들을 내장하고 있습니다:
#     - **자동 재시도 및 백오프**: 네트워크 오류나 API 리밋 도달 시 지수 백오프 전략으로 자동 재시도합니다.
#     - **비율 제한(Rate Limit) 관리**: API 응답 헤더를 분석하여 다음 요청까지 필요한 대기 시간을 계산합니다.
#     - **강력한 오류 처리**: API에서 반환하는 모든 오류 코드를 분석하고 적절한 예외를 발생시킵니다.
#     - **WebSocket 자동 재접속**: WebSocket 연결이 끊어지면 자동으로 재접속을 시도합니다.
#     - **비동기 설계**: `httpx.AsyncClient`와 `websockets`를 사용하여 모든 I/O 작업을 논블로킹으로 처리합니다.
#
#   **주요 기능:**
#   - **REST API**: 계정 정보 조회, 주문 실행, 포지션 조회 등
#   - **WebSocket API**:
#     - **Public Topics**: 실시간 오더북, 거래 체결 데이터 수신
#     - **Private Topics**: 내 주문 상태 업데이트, 포지션 변경, 지갑 잔고 변동 실시간 수신
#   - **상태 관리 연동**: 수신된 데이터를 `state_store`에 업데이트하여 애플리케이션의 다른 부분과 공유합니다.
#
#
import asyncio
import hashlib
import hmac
import json
import time
import math # Added for ceil/floor
from typing import Any, Dict, List, Optional
from decimal import Decimal # Added for precise price_scale calculation

import httpx
import websockets
from loguru import logger
from pydantic import SecretStr

from app.exchange.models import Side
from app.state.store import state_store
from app.utils.retry import async_retry

class BybitClient:
    _instrument_info_cache: Dict[str, Any] = {} # Added for caching instrument info
    """Bybit V5 API와 통신하는 비동기 클라이언트"""

    def __init__(self, api_key: SecretStr, api_secret: SecretStr, testnet: bool = True):
        self.testnet = testnet
        self.base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
        self.ws_url = "wss://stream-testnet.bybit.com/v5/public/spot" if testnet else "wss://stream.bybit.com/v5/public/spot" # Changed for spot market
        self.ws_private_url = "wss://stream-testnet.bybit.com/v5/private" if testnet else "wss://stream.bybit.com/v5/private"
        
        self.api_key = api_key.get_secret_value()
        self.api_secret = api_secret.get_secret_value()
        
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)
        self.rate_limit_remaining = 120
        self.rate_limit_reset_at = time.time() + 60

        logger.info(f"BybitClient initialized for {'Testnet' if testnet else 'Mainnet'}.")

    # --- REST API 관련 메서드 ---

    def _generate_signature(self, data_to_sign: str) -> str:
        """Generates the HMAC-SHA256 signature."""
        return hmac.new(self.api_secret.encode('utf-8'), data_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

    def _get_auth_headers(self, method: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> Dict[str, str]:
        """Generates authentication headers for a request."""
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"

        if method == "GET":
            # GET 요청 시, query parameter를 알파벳순으로 정렬하여 서명
            param_str = "&".join([f"{k}={v}" for k, v in sorted(params.items())]) if params else ""
        else:  # POST
            # POST 요청 시, request body를 JSON 문자열로 만들어 서명
            param_str = json.dumps(data, separators=(',', ':')) if data else ""

        signature_payload = timestamp + self.api_key + recv_window + param_str
        signature = self._generate_signature(signature_payload)

        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-SIGN": signature,
            "X-BAPI-RECV-WINDOW": recv_window,
        }
        if method == "POST":
            headers["Content-Type"] = "application/json"
        return headers

    @async_retry(attempts=3, delay=2)
    async def _request(self, method: str, endpoint: str, params: Optional[Dict] = None, data: Optional[Dict] = None) -> Dict:
        print(f"_request called with endpoint: {endpoint}")  # Debug print

        # Rate limit 확인
        if self.rate_limit_remaining < 5:
            sleep_time = self.rate_limit_reset_at - time.time()
            if sleep_time > 0:
                logger.warning(f"Rate limit approaching. Sleeping for {sleep_time:.2f} seconds.")
                await asyncio.sleep(sleep_time)

        # private 엔드포인트 체크 및 헤더 생성
        is_private = any(k in endpoint for k in ["/v5/order", "/v5/position", "/v5/account"])
        headers = self._get_auth_headers(method, params=params, data=data) if is_private else {}

        try:
            url = self.base_url + endpoint
            # httpx는 GET 요청 시 params 딕셔너리의 순서를 보장하지 않으므로, 직접 query string을 만들어 전달
            if method == "GET" and params:
                sorted_params = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
                url = f"{self.base_url}{endpoint}?{sorted_params}"
                response = await self.client.request(method, url, json=data, headers=headers)
            else:
                response = await self.client.request(method, url, params=params, json=data, headers=headers)

            # Rate limit 업데이트
            if 'X-Bapi-Limit-Status' in response.headers:
                self.rate_limit_remaining = int(response.headers['X-Bapi-Limit-Status'])
            if 'X-Bapi-Limit-Reset-Timestamp' in response.headers:
                self.rate_limit_reset_at = int(response.headers['X-Bapi-Limit-Reset-Timestamp']) / 1000

            response.raise_for_status()
            response_data = response.json()

            if response_data.get("retCode") != 0:
                logger.error(f"Bybit API Error: {response_data}")
                if response_data.get("retCode") == 10006:
                    await asyncio.sleep(5)
                raise Exception(f"Bybit API Error: {response_data.get('retMsg')}")

            return response_data

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP Error: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Request failed: {e}")
            raise

    async def get_kline(self, symbol: str, interval: str, limit: int = 200) -> List[Dict]:
        """K-line (캔들) 데이터를 가져옵니다."""
        params = {"category": "spot", "symbol": symbol, "interval": interval, "limit": limit}
        logger.debug(f"Requesting kline with params: {params}")
        data = await self._request("GET", "/v5/market/kline", params=params)
        logger.debug(f"Received raw kline response for {symbol}: {data}")
        return data.get("result", {}).get("list", [])

    async def get_instruments_info(self, category: str = 'spot') -> List[Dict]:
        """특정 카테고리의 모든 상품(instrument) 정보를 가져옵니다."""
        params = {"category": category}
        data = await self._request("GET", "/v5/market/instruments-info", params=params)
        return data.get("result", {}).get("list", [])

    async def get_wallet_balance(self, **params):
        """
        Bybit API v5의 지갑 잔고 엔드포인트를 호출하여 전체 응답을 반환합니다.
        """
        # 이 함수는 API의 응답 딕셔너리 전체를 그대로 반환해야 합니다.
        return await self._request(
            endpoint="/v5/account/wallet-balance",
            method="GET",
            params=params,
            auth=True
        )

    async def get_closed_pnl(self, category: str = "spot", limit: int = 50) -> List[Dict]:
        """Closed PnL 기록을 가져옵니다."""
        params = {"category": category, "limit": limit}
        data = await self._request("GET", "/v5/position/closed-pnl", params=params)
        return data.get("result", {}).get("list", [])

    async def get_order_history(self, category: str = "spot", symbol: str = None, order_id: str = None, limit: int = 50) -> List[Dict]:
        """주문 내역을 가져옵니다."""
        params = {"category": category, "limit": limit}
        if symbol:
            params["symbol"] = symbol
        if order_id:
            params["orderId"] = order_id
        data = await self._request("GET", "/v5/order/history", params=params)
        return data.get("result", {}).get("list", [])

    async def _get_latest_price(self, symbol: str) -> float:
        """
        심볼의 최신 시장 가격을 조회합니다.
        """
        try:
            response = await self._request("GET", "/v5/market/tickers", params={"category": "spot", "symbol": symbol})
            data = response.get('result', {}).get('list', [])
            if data:
                return float(data[0].get('lastPrice'))
            else:
                logger.warning(f"[{symbol}] 최신 가격 정보를 가져올 수 없습니다.")
                return 0.0
        except Exception as e:
            logger.error(f"[{symbol}] 최신 가격 조회 중 오류 발생: {e}")
            return 0.0

    async def _get_instrument_info(self, symbol: str) -> dict:
        """
        심볼의 거래 규칙 (tickSize, minPrice 등)을 조회하고 캐시합니다.
        """
        if symbol in self._instrument_info_cache:
            return self._instrument_info_cache[symbol]

        try:
            response = await self._request("GET", "/v5/market/instruments-info", params={"category": "spot", "symbol": symbol})
            data = response.get('result', {}).get('list', [])
            if data:
                instrument_info = data[0]
                self._instrument_info_cache[symbol] = instrument_info
                return instrument_info
            else:
                logger.warning(f"[{symbol}] 거래 규칙 정보를 가져올 수 없습니다.")
                return {}
        except Exception as e:
            logger.error(f"[{symbol}] 거래 규칙 조회 중 오류 발생: {e}")
            return {}

    async def place_market_order(self, symbol: str, side: Side, qty: float, notional: float = 0) -> Dict:
        """
        현물 시장가 주문을 제출합니다.
        qty와 notional 중 하나를 사용합니다. notional이 우선됩니다.
        - notional > 0: USDT 금액으로 시장가 매수/매도 (marketUnit: quoteCoin)
        - qty > 0: 코인 수량으로 시장가 매수/매도
        """
        order_data = {
            "category": "spot",
            "symbol": symbol,
            "side": side.value,
            "orderType": "Market",
        }

        if notional > 0:
            # 금액(USDT)으로 주문
            order_data["qty"] = str(notional)
            order_data["marketUnit"] = "quoteCoin"
            logger.info(f"Submitting SPOT market order by NOTIONAL value: {order_data}")
        
        elif qty > 0:
            # 수량(e.g. BTC)으로 주문 (주로 매도 시 사용)
            instrument_info = await self._get_instrument_info(symbol)
            if not instrument_info:
                logger.error(f"[{symbol}] Instrument info not found. Cannot place order.")
                return {}

            lot_size_filter = instrument_info.get('lotSizeFilter', {})
            min_order_qty_str = lot_size_filter.get('minOrderQty', '0')
            qty_step_str = lot_size_filter.get('qtyStep', '0')
            
            logger.debug(f"[{symbol}] Adjusting qty. Original: {qty}, Step: {qty_step_str}, MinQty: {min_order_qty_str}")

            try:
                # 1. 정밀도(소수점 자릿수) 결정
                precision = 0
                # qtyStep이 유효한 소수일 경우, 해당 값의 소수점 자릿수 사용
                if '.' in qty_step_str and float(qty_step_str) > 0:
                    precision = len(qty_step_str.split('.')[1])
                # 그렇지 않으면, minOrderQty가 유효한 소수일 경우 사용
                elif '.' in min_order_qty_str and float(min_order_qty_str) > 0:
                    precision = len(min_order_qty_str.split('.')[1])
                
                # 2. 결정된 정밀도를 사용하여 수량 내림 처리
                factor = 10 ** precision
                adjusted_qty = math.floor(qty * factor) / factor
                
                # 3. 최종 문자열 포맷팅
                qty_str = f"{adjusted_qty:.{precision}f}"

            except Exception as e:
                logger.error(f"CRITICAL: Could not adjust quantity for {symbol}. Error: {e}. Aborting order.")
                return {}

            logger.info(f"Original sell qty: {qty}, Adjusted sell qty: {qty_str} for symbol {symbol}")

            if float(qty_str) < float(min_order_qty_str):
                logger.warning(f"[{symbol}] Adjusted qty ({qty_str}) is below min order qty ({min_order_qty_str}). Skipping trade.")
                return {}
            
            order_data["qty"] = qty_str
            logger.info(f"Submitting SPOT market order by QTY: {order_data}")
        
        else:
            logger.error("Order failed: both qty and notional are zero.")
            return {}

        response = await self._request("POST", "/v5/order/create", data=order_data)
        return response.get("result", {})

    async def close_position(self, symbol: str) -> Dict:
        """(현물 거래에서는 사용되지 않음) 지정된 심볼의 포지션을 시장가로 종료합니다."""
        logger.warning("close_position is not applicable for spot trading. Use place_market_order to sell assets.")
        return {}

    async def close(self):
        """HTTP 클라이언트를 종료합니다."""
        await self.client.aclose()
        logger.info("BybitClient HTTP client closed.")

    # --- WebSocket 관련 메서드 ---

    async def _ws_handler(self, url: str, subscriptions: List[str], is_private: bool):
        """WebSocket 연결 및 데이터 처리를 위한 내부 핸들러"""
        while True:
            try:
                async with websockets.connect(url) as ws:
                    logger.info(f"WebSocket connected to {url}")
                    if is_private:
                        # Private 채널 인증
                        expires = int((time.time() + 10) * 1000)
                        signature = hmac.new(self.api_secret.encode('utf-8'), f"GET/realtime{expires}".encode('utf-8'), hashlib.sha256).hexdigest()
                        await ws.send(json.dumps({"op": "auth", "args": [self.api_key, expires, signature]}))
                    
                    # Split subscriptions into chunks of 10 or less
                    chunk_size = 10
                    for i in range(0, len(subscriptions), chunk_size):
                        chunk = subscriptions[i:i + chunk_size]
                        await ws.send(json.dumps({"op": "subscribe", "args": chunk}))
                        logger.debug(f"Sent subscription request for chunk: {chunk}")

                    while True:
                        message = await ws.recv()
                        data = json.loads(message)
                        
                        if "op" in data and data["op"] == "subscribe":
                            logger.info(f"Subscribed to {data.get('args', 'N/A')}: Success={data.get('success')}")
                        elif "topic" in data:
                            await self._process_ws_message(data)
                        else:
                            logger.debug(f"Received WS message: {data}")

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning(f"WebSocket connection closed to {url}. Reconnecting in 5 seconds... Error: {e}")
            except Exception as e:
                logger.error(f"Error in WebSocket handler for {url}: {e}", exc_info=True)
            
            await asyncio.sleep(5) # 재접속 전 대기

    async def _process_ws_message(self, data: Dict):
        """수신된 WebSocket 메시지를 처리하고 state_store를 업데이트합니다."""
        topic = data.get("topic", "")
        
        # 예: 오더북 데이터 처리
        if topic.startswith("orderbook.50"): # 50-level orderbook
            symbol = topic.split('.')[-1]
            await state_store.update_orderbook(symbol, data['data'])
            # logger.debug(f"Updated orderbook for {symbol}")

        # 예: 개인 주문 업데이트 처리
        elif topic == "order":
            for order_data in data.get("data", []):
                await state_store.update_order(order_data)
                logger.info(f"Order update: {order_data}")
        
        # 다른 토픽(trade, tickers, execution 등)에 대한 처리 로직 추가 가능

    async def run_websockets(self):
        """Public 및 Private WebSocket 연결을 모두 실행합니다."""
        symbols = state_store.get_universe()
        
        public_subs = [f"orderbook.50.{symbol}" for symbol in symbols]
        public_subs += [f"publicTrade.{symbol}" for symbol in symbols]
        
        # Private 채널 구독 (주문, 체결) - position 제거
        private_subs = ["order", "execution"]

        public_task = asyncio.create_task(self._ws_handler(self.ws_url, public_subs, is_private=False))
        private_task = asyncio.create_task(self._ws_handler(self.ws_private_url, private_subs, is_private=True))

        await asyncio.gather(public_task, private_task)
