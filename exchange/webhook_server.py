# APEX — Phase 2: Helius Webhook Server
# Receives real Pump.fun new token events from Helius
# Runs as async HTTP server on Port 8080

import asyncio
import json
import logging
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Callable

logger = logging.getLogger("apex.webhook")

PUMP_FUN_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


class WebhookHandler(BaseHTTPRequestHandler):

    # Callback injected by main.py
    on_new_token: Callable = None

    def do_POST(self):
        try:
            length  = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))

            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

            # Process events
            events = payload if isinstance(payload, list) else [payload]
            for event in events:
                self._handle_event(event)

        except Exception as e:
            logger.error(f"[WEBHOOK] Handler error: {e}")
            self.send_response(500)
            self.end_headers()

    def do_GET(self):
        # Health check endpoint
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"APEX Webhook Server OK")

    def _handle_event(self, event: dict):
        event_type = event.get("type", "")
        logger.info(f"[WEBHOOK] Event received: {event_type}")

        # New token mint on Pump.fun
        if event_type in ("TOKEN_MINT", "SWAP"):
            mint = self._extract_mint(event)
            if mint and self.on_new_token:
                symbol = event.get("description", mint[:6]).upper()
                logger.info(f"[WEBHOOK] 🆕 New token: {symbol} | {mint[:16]}...")
                self.on_new_token(symbol=symbol, mint=mint, reason="helius_webhook")

    def _extract_mint(self, event: dict) -> str:
        # Try tokenTransfers first
        for transfer in event.get("tokenTransfers", []):
            mint = transfer.get("mint", "")
            if mint and len(mint) >= 32:
                return mint
        # Try accountData
        for acct in event.get("accountData", []):
            token_balances = acct.get("tokenBalanceChanges", [])
            for tb in token_balances:
                mint = tb.get("mint", "")
                if mint and len(mint) >= 32:
                    return mint
        return ""

    def log_message(self, format, *args):
        logger.debug(f"[WEBHOOK] {format % args}")


class WebhookServer:
    """
    Lightweight HTTP server for Helius webhook events.
    Runs in background thread, callbacks fire into async main loop.
    """

    def __init__(self, port: int = 8080):
        self.port    = port
        self._server = None
        self._thread = None

    def start(self, on_new_token: Callable):
        WebhookHandler.on_new_token = on_new_token
        self._server = HTTPServer(("0.0.0.0", self.port), WebhookHandler)
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info(f"[WEBHOOK] Server started on port {self.port}")

    def stop(self):
        if self._server:
            self._server.shutdown()
            logger.info("[WEBHOOK] Server stopped")
