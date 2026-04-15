# APEX — Phase 2: Telegram Channel Scanner
# Scans TG channels for mint addresses and KOL mentions
# Uses Bot Token (no Telethon required)

import asyncio
import json
import logging
import re
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger("apex.tg_scanner")

# Solana mint address pattern (base58, 32-44 chars)
MINT_PATTERN = re.compile(r'\b[1-9A-HJ-NP-Za-km-z]{32,44}\b')

# Bullish keywords
BULLISH_KEYWORDS = [
    "gem", "early", "100x", "1000x", "moon", "ape in",
    "just launched", "new launch", "pump", "bullish",
    "accumulate", "load up", "send it", "lfg",
]


class TelegramScanner:
    """
    Scans Telegram channels via Bot API for:
    1. New mint addresses (auto-add to watchlist)
    2. KOL sentiment signals
    3. Keyword velocity tracking
    """

    def __init__(self, bot_token: str, channels: list[str]):
        self.bot_token  = bot_token
        self.channels   = channels   # e.g. ["@solana_gems", "@pump_alerts"]
        self.base_url   = f"https://api.telegram.org/bot{bot_token}"
        self._last_ids  = {}         # channel → last message_id processed
        self._keyword_counts = {}    # symbol → count in last window

    # ─────────────────────────────────────────
    #  MAIN SCAN LOOP
    # ─────────────────────────────────────────

    async def scan_loop(self, on_new_mint: Callable, interval: int = 60):
        """
        Poll channels every N seconds.
        Calls on_new_mint(symbol, mint, reason) when mint address found.
        """
        logger.info(f"[TG] Scanner started | channels={self.channels}")

        while True:
            for channel in self.channels:
                try:
                    await self._scan_channel(channel, on_new_mint)
                except Exception as e:
                    logger.error(f"[TG] Error scanning {channel}: {e}")

            await asyncio.sleep(interval)

    async def _scan_channel(self, channel: str, on_new_mint: Callable):
        messages = self._get_updates(channel)
        if not messages:
            return

        new_mints   = set()
        sentiment   = 0.0
        msg_count   = 0

        for msg in messages:
            text = msg.get("text", "") or msg.get("caption", "")
            if not text:
                continue

            msg_count += 1

            # Extract mint addresses
            mints = MINT_PATTERN.findall(text)
            for mint in mints:
                if mint not in new_mints:
                    new_mints.add(mint)
                    symbol = self._extract_symbol(text, mint)
                    logger.info(f"[TG] 🆕 Mint found in {channel}: {symbol} | {mint[:16]}...")
                    on_new_mint(
                        symbol = symbol,
                        mint   = mint,
                        reason = f"tg_mention:{channel}",
                    )

            # Sentiment scoring
            text_lower = text.lower()
            bull_hits  = sum(1 for kw in BULLISH_KEYWORDS if kw in text_lower)
            if bull_hits > 0:
                sentiment += min(bull_hits / 3, 1.0)

        if msg_count > 0:
            avg_sentiment = sentiment / msg_count
            logger.debug(f"[TG] {channel}: {msg_count} msgs | sentiment={avg_sentiment:.2f} | mints={len(new_mints)}")

    # ─────────────────────────────────────────
    #  TELEGRAM BOT API
    # ─────────────────────────────────────────

    def _get_updates(self, channel: str) -> list[dict]:
        """Get recent messages from channel via getUpdates or forwardMessage."""
        last_id = self._last_ids.get(channel, 0)

        params = {"offset": last_id + 1, "limit": 50, "timeout": 5}
        url    = self.base_url + "/getUpdates?" + urllib.parse.urlencode(params)

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())

            if not data.get("ok"):
                return []

            messages = []
            for update in data.get("result", []):
                uid = update.get("update_id", 0)
                if uid > last_id:
                    self._last_ids[channel] = uid

                # Extract message from various update types
                msg = (update.get("message") or
                       update.get("channel_post") or
                       update.get("edited_message"))
                if msg:
                    messages.append(msg)

            return messages

        except Exception as e:
            logger.error(f"[TG] getUpdates failed for {channel}: {e}")
            return []

    def _extract_symbol(self, text: str, mint: str) -> str:
        """Try to extract token symbol from message text."""
        # Look for $SYMBOL pattern
        dollar_match = re.search(r'\$([A-Z]{2,10})', text.upper())
        if dollar_match:
            return dollar_match.group(1)

        # Look for #SYMBOL pattern
        hash_match = re.search(r'#([A-Z]{2,10})', text.upper())
        if hash_match:
            return hash_match.group(1)

        # Fallback: first 6 chars of mint
        return mint[:6].upper()

    # ─────────────────────────────────────────
    #  CONNECTION TEST
    # ─────────────────────────────────────────

    def test_connection(self) -> bool:
        url = self.base_url + "/getMe"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read())
                ok   = data.get("ok", False)
                name = data.get("result", {}).get("username", "")
                logger.info(f"[TG] Bot connected: @{name} | ok={ok}")
                return ok
        except Exception as e:
            logger.error(f"[TG] Connection test failed: {e}")
            return False
