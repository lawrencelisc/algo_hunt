# APEX — Telegram Reporter
# Daily digest + real-time alerts
# Phase 1: prints to console (stdout). Set bot_token + chat_id to enable real TG.

import json
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

from config.settings import CONFIG
from core.models import WatchTarget, TradeRecord, PortfolioSnapshot
from core.watchlist import WatchlistManager
from data.mock_feeds import MockPriceFeed

logger = logging.getLogger("apex.telegram")


class TelegramReporter:

    def __init__(self, watchlist: WatchlistManager):
        self.watchlist  = watchlist
        self.price_feed = MockPriceFeed()
        self._enabled   = (
            CONFIG.telegram.bot_token != "YOUR_BOT_TOKEN" and
            CONFIG.telegram.chat_id   != "YOUR_CHAT_ID"
        )
        if not self._enabled:
            logger.info("[TG] Bot token not set — printing to console only")

    # ─────────────────────────────────────────
    #  SEND HELPERS
    # ─────────────────────────────────────────

    def _send(self, text: str):
        """Send message to Telegram or print to console."""
        if self._enabled:
            self._tg_send(text)
        else:
            # Console output with separator
            print("\n" + "─" * 55)
            print(text)
            print("─" * 55 + "\n")

    def _tg_send(self, text: str):
        """Real Telegram API call."""
        url     = f"https://api.telegram.org/bot{CONFIG.telegram.bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id":    CONFIG.telegram.chat_id,
            "text":       text,
            "parse_mode": "HTML",
        }).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=payload,
                                          headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            logger.error(f"[TG] Send failed: {e}")

    # ─────────────────────────────────────────
    #  DAILY DIGEST
    # ─────────────────────────────────────────

    def send_daily_digest(self, stats: dict):
        """Full portfolio status report — sent at 08:00 HKT."""
        now      = datetime.now(timezone.utc)
        active   = self.watchlist.get_active()
        wl_stats = self.watchlist.summary()

        portfolio_val = CONFIG.risk.portfolio_value
        exposure_usd  = wl_stats["exposure_usd"]
        exposure_pct  = wl_stats["exposure_pct"]
        daily_pnl     = stats.get("daily_pnl", 0.0)
        daily_pnl_pct = (daily_pnl / portfolio_val * 100) if portfolio_val else 0

        pnl_sign  = "+" if daily_pnl >= 0 else ""
        pnl_emoji = "📈" if daily_pnl >= 0 else "📉"

        lines = [
            f"🤖  <b>APEX Daily Report</b>",
            f"📅  {now.strftime('%d %b %Y  %H:%M UTC')}",
            "─" * 40,
            f"💼  Portfolio:   <b>${portfolio_val:,.2f}</b>",
            f"{pnl_emoji}  Daily P&L:   <b>{pnl_sign}{daily_pnl:.2f} USD ({pnl_sign}{daily_pnl_pct:.1f}%)</b>",
            f"📊  Exposure:    {exposure_usd:.2f} USD ({exposure_pct:.1f}%)",
            "─" * 40,
        ]

        if active:
            lines.append("📌  <b>OPEN POSITIONS</b>")
            for t in active:
                current = self.price_feed.get_price(t.symbol)
                pnl_usd = (current - t.entry_price) * t.entry_qty if t.entry_price else 0
                pnl_pct = ((current / t.entry_price) - 1) * 100 if t.entry_price else 0
                p_sign  = "+" if pnl_usd >= 0 else ""
                p_emoji = "🟢" if pnl_usd >= 0 else "🔴"

                lines += [
                    f"\n  {p_emoji} <b>{t.symbol}/USDT</b>  [{t.pipeline.value}]",
                    f"     Entry:  ${t.entry_price:.8f}   Qty: {t.entry_qty:,.2f}",
                    f"     Now:    ${current:.8f}   P&L: {p_sign}{pnl_usd:.2f} ({p_sign}{pnl_pct:.1f}%)",
                    f"     Stop:   ${t.trailing_stop:.8f}  (trailing)",
                    f"     Hard:   ${t.hard_stop:.8f}  (-8%)",
                ]
        else:
            lines.append("📌  No open positions")

        lines += [
            "─" * 40,
            f"👁  Watchlist:   {wl_stats['total']} tokens",
            f"     Scanning:  {wl_stats['scanning']}   |   Triggered: {wl_stats['triggered']}",
            f"     Active:    {wl_stats['active']}   |   Cooling:   {wl_stats['cooling']}",
            "─" * 40,
            f"📡  Signals today:  {stats.get('signals', 0)} evaluated",
            f"     ✅ BUY: {stats.get('buys', 0)}   |   ⏭ SKIP: {stats.get('skips', 0)}",
            "─" * 40,
            f"⚙️  Mode: <b>{CONFIG.phase.upper()}</b>  |  Dry-run: {CONFIG.dry_run}",
        ]

        self._send("\n".join(lines))

    # ─────────────────────────────────────────
    #  REAL-TIME ALERTS
    # ─────────────────────────────────────────

    def alert_buy(self, symbol: str, entry_price: float, usd_amount: float,
                  sentiment: float, velocity: float, bot_pct: float, pipeline: str):
        self._send(
            f"🟢  <b>BUY SIGNAL — {symbol}</b>\n"
            f"Pipeline:   {pipeline}\n"
            f"Entry:      ${entry_price:.8f}\n"
            f"Size:       ${usd_amount:.2f}\n"
            f"Sentiment:  {sentiment:.2f}   Velocity: {velocity:.0%}   Bot: {bot_pct:.0%}\n"
            f"Stop:       -8% hard  |  trailing active"
        )

    def alert_stop_loss(self, symbol: str, entry: float, exit_price: float,
                        loss_usd: float, reason: str):
        self._send(
            f"🔴  <b>STOPPED — {symbol}</b>\n"
            f"Entry:   ${entry:.8f}\n"
            f"Exit:    ${exit_price:.8f}   ({reason})\n"
            f"Loss:    -${abs(loss_usd):.2f} USD"
        )

    def alert_take_profit(self, symbol: str, entry: float, current: float,
                          profit_usd: float, tp_level: str):
        self._send(
            f"🟡  <b>TAKE PROFIT — {symbol}</b>  [{tp_level}]\n"
            f"Entry:   ${entry:.8f}\n"
            f"Exit:    ${current:.8f}\n"
            f"Profit:  +${profit_usd:.2f} USD\n"
            f"Remaining position: trailing stop active"
        )

    def alert_rug_risk(self, symbol: str, dev_pct: float):
        self._send(
            f"🚨  <b>RUG RISK — {symbol}</b>\n"
            f"Dev wallet moved {dev_pct:.0%} of supply\n"
            f"AUTO-EXIT triggered immediately"
        )

    def alert_sentiment_flip(self, symbol: str, old_score: float, new_score: float):
        self._send(
            f"⚠️  <b>SENTIMENT FLIP — {symbol}</b>\n"
            f"Score: {old_score:.2f} → {new_score:.2f}\n"
            f"Monitor exit conditions"
        )

    def alert_circuit_breaker(self, pause_hours: int):
        self._send(
            f"⛔  <b>CIRCUIT BREAKER TRIGGERED</b>\n"
            f"3 stop-losses within 1 hour\n"
            f"System paused for {pause_hours} hours\n"
            f"Manual review required"
        )

    def alert_new_token(self, symbol: str, mint: str, pipeline: str):
        self._send(
            f"🆕  <b>NEW TOKEN DETECTED — {symbol}</b>\n"
            f"Pipeline: {pipeline}\n"
            f"Mint:     {mint[:12]}...\n"
            f"Added to watchlist — scanning"
        )

    def alert_honeypot(self, symbol: str):
        self._send(
            f"☠️  <b>HONEYPOT DETECTED — {symbol}</b>\n"
            f"Contract scan failed\n"
            f"Token blacklisted — no entry"
        )

    def alert_signal_skip(self, symbol: str, reason: str, sentiment: float,
                          velocity: float, bot_pct: float):
        """Logged but typically not sent to TG to avoid noise. Enable if needed."""
        logger.info(
            f"[TG] SKIP {symbol}: {reason} | "
            f"s={sentiment:.2f} v={velocity:.0%} b={bot_pct:.0%}"
        )
