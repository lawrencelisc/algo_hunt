# APEX — Phase 2: On-chain Volume Anomaly Scanner
# Monitors known tokens for sudden volume spikes via Helius
# Auto-adds to watchlist when sigma > threshold

import asyncio
import logging
import statistics
from datetime import datetime
from typing import Callable

from data.helius_client import HeliusClient

logger = logging.getLogger("apex.volume_scanner")

# Known token registry with real Solana mint addresses
KNOWN_TOKENS = {
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF":  "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "PEPE": "2b1kV6DkPAnxd5ixfnxCpjxmKwqjjaYmCZfHsFu24GXo",
    "JUP":  "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "DOGE": "So11111111111111111111111111111111111111112",   # SOL as proxy
}

SIGMA_TRIGGER = 3.0   # Add to watchlist at 3σ
SIGMA_SIGNAL  = 5.0   # Full BUY gate requires 5σ


class VolumeScanner:
    """
    Scans known tokens for on-chain volume anomalies.
    Maintains rolling baseline per token.
    Auto-adds to watchlist when volume spikes detected.
    """

    def __init__(self, helius_api_key: str):
        self.helius   = HeliusClient(helius_api_key)
        self._history = {}   # symbol → list of recent volume readings

    async def scan_loop(self, on_volume_spike: Callable, interval: int = 300):
        """
        Scan all known tokens every N seconds (default 5 min).
        Calls on_volume_spike(symbol, mint, sigma) on anomaly.
        """
        logger.info(f"[VOL] Volume scanner started | tokens={list(KNOWN_TOKENS.keys())}")

        while True:
            for symbol, mint in KNOWN_TOKENS.items():
                try:
                    await self._check_token(symbol, mint, on_volume_spike)
                except Exception as e:
                    logger.error(f"[VOL] Error checking {symbol}: {e}")

            await asyncio.sleep(interval)

    async def _check_token(self, symbol: str, mint: str, on_volume_spike: Callable):
        data  = await self.helius.get_volume_data(mint)
        sigma = data.get("volume_sigma", 0.0)

        # Maintain rolling history
        if symbol not in self._history:
            self._history[symbol] = []
        self._history[symbol].append(sigma)
        if len(self._history[symbol]) > 288:   # 24h at 5min intervals
            self._history[symbol].pop(0)

        logger.debug(f"[VOL] {symbol}: σ={sigma:.1f} | history={len(self._history[symbol])}")

        # Trigger if above threshold
        if sigma >= SIGMA_TRIGGER:
            netflow = data.get("netflow_usd", 0)
            logger.info(f"[VOL] 🚨 Spike detected: {symbol} σ={sigma:.1f} netflow=${netflow:,.0f}")
            on_volume_spike(
                symbol  = symbol,
                mint    = mint,
                sigma   = sigma,
                netflow = netflow,
                reason  = f"volume_anomaly:{sigma:.1f}σ",
            )

    def get_history(self, symbol: str) -> list:
        return self._history.get(symbol, [])
