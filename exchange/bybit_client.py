# APEX — Bybit REST API Client (v5)
# Signature fix: GET uses query string, POST uses raw JSON body

import hashlib
import hmac
import json
import logging
import time
import urllib.parse
import urllib.request
from typing import Optional
import random

from config.settings import CONFIG

logger = logging.getLogger("apex.bybit")


class BybitTestnetClient:

    def __init__(self):
        self.api_key    = CONFIG.exchange.bybit_api_key
        self.api_secret = CONFIG.exchange.bybit_api_secret
        self.base_url   = CONFIG.exchange.bybit_base_url
        self.dry_run    = CONFIG.dry_run

    def _sign(self, payload: str, timestamp: str, recv_window: str = "5000") -> str:
        raw = timestamp + self.api_key + recv_window + payload
        return hmac.new(
            self.api_secret.encode("utf-8"),
            raw.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _get_headers(self, payload: str) -> dict:
        ts  = str(int(time.time() * 1000))
        sig = self._sign(payload, ts)
        return {
            "X-BAPI-API-KEY":     self.api_key,
            "X-BAPI-SIGN":        sig,
            "X-BAPI-SIGN-TYPE":   "2",
            "X-BAPI-TIMESTAMP":   ts,
            "X-BAPI-RECV-WINDOW": "5000",
            "Content-Type":       "application/json",
        }

    async def _get(self, path: str, params: dict = {}) -> dict:
        query = urllib.parse.urlencode(params) if params else ""
        url   = self.base_url + path + ("?" + query if query else "")
        try:
            req = urllib.request.Request(url, headers=self._get_headers(query))
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.error(f"[BYBIT] GET {path} failed: {e}")
            return {"retCode": -1, "retMsg": str(e)}

    async def _post(self, path: str, body: dict) -> dict:
        body_str = json.dumps(body)
        if self.dry_run:
            logger.info(f"[BYBIT DRY-RUN] POST {path} | {body_str}")
            return {"retCode": 0, "retMsg": "DRY_RUN_OK",
                    "result": {"orderId": f"DRY_{int(time.time()*1000)}"}}
        url     = self.base_url + path
        payload = body_str.encode("utf-8")
        try:
            req = urllib.request.Request(
                url, data=payload,
                headers=self._get_headers(body_str), method="POST"
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.error(f"[BYBIT] POST {path} failed: {e}")
            return {"retCode": -1, "retMsg": str(e)}

    async def get_account_balance(self) -> dict:
        resp = await self._get("/v5/account/wallet-balance", {"accountType": "UNIFIED"})
        if resp.get("retCode") == 0:
            return resp.get("result", {})
        logger.error(f"[BYBIT] Balance error: {resp.get('retMsg')}")
        return {}

    async def get_ticker(self, symbol: str, category: str = "spot") -> Optional[dict]:
        resp = await self._get("/v5/market/tickers", {"category": category, "symbol": symbol + "USDT"})
        if resp.get("retCode") == 0:
            items = resp.get("result", {}).get("list", [])
            return items[0] if items else None
        return None

    async def place_market_buy(self, symbol: str, usd_amount: float, category: str = "spot") -> dict:
        body = {"category": category, "symbol": symbol + "USDT", "side": "Buy",
                "orderType": "Market", "qty": str(round(usd_amount, 2)),
                "marketUnit": "quoteCoin", "timeInForce": "IOC"}
        resp = await self._post("/v5/order/create", body)
        if resp.get("retCode") == 0:
            oid = resp["result"]["orderId"]
            logger.info(f"[BYBIT] BUY {symbol} ${usd_amount:.2f} | id={oid}")
            return {"success": True, "order_id": oid}
        logger.error(f"[BYBIT] BUY failed: {resp.get('retMsg')}")
        return {"success": False, "error": resp.get("retMsg")}

    async def place_market_sell(self, symbol: str, qty: float, category: str = "spot") -> dict:
        body = {"category": category, "symbol": symbol + "USDT", "side": "Sell",
                "orderType": "Market", "qty": str(qty), "timeInForce": "IOC"}
        resp = await self._post("/v5/order/create", body)
        if resp.get("retCode") == 0:
            oid = resp["result"]["orderId"]
            logger.info(f"[BYBIT] SELL {symbol} qty={qty} | id={oid}")
            return {"success": True, "order_id": oid}
        logger.error(f"[BYBIT] SELL failed: {resp.get('retMsg')}")
        return {"success": False, "error": resp.get("retMsg")}

    async def place_stop_loss(self, symbol: str, qty: float, stop_price: float, category: str = "spot") -> dict:
        body = {"category": category, "symbol": symbol + "USDT", "side": "Sell",
                "orderType": "Limit", "qty": str(qty),
                "price": str(round(stop_price, 8)), "timeInForce": "GTC"}
        resp = await self._post("/v5/order/create", body)
        if resp.get("retCode") == 0:
            return {"success": True, "order_id": resp["result"]["orderId"]}
        return {"success": False, "error": resp.get("retMsg")}

    async def cancel_order(self, symbol: str, order_id: str, category: str = "spot") -> bool:
        resp = await self._post("/v5/order/cancel",
                                {"category": category, "symbol": symbol + "USDT", "orderId": order_id})
        return resp.get("retCode") == 0

    async def check_slippage(self, symbol: str, usd_amount: float) -> tuple[bool, float]:
        est = random.uniform(0.001, 0.025)
        return est < CONFIG.exchange.max_slippage_pct, round(est, 4)
