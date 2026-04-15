# APEX — WatchlistManager
# Dynamic token watchlist with auto-prune and state machine

import logging
from datetime import datetime
from typing import Optional

from config.settings import CONFIG, Pipeline, WatchStatus, Signal
from core.models import WatchTarget, SignalSnapshot

logger = logging.getLogger("apex.watchlist")


class WatchlistManager:

    def __init__(self):
        self.targets: dict[str, WatchTarget] = {}
        self.blacklist: set[str] = set()

    # ─────────────────────────────────────────
    #  ADD / REMOVE
    # ─────────────────────────────────────────

    def add(self, symbol: str, mint: str, pipeline: Pipeline, reason: str) -> bool:
        """Add token to watchlist. Returns False if already tracked or blacklisted."""
        if symbol in self.blacklist:
            logger.warning(f"[WATCHLIST] {symbol} is blacklisted, skipping")
            return False

        if symbol in self.targets:
            logger.debug(f"[WATCHLIST] {symbol} already tracked")
            return False

        if len(self.targets) >= CONFIG.watchlist.max_tokens:
            logger.warning(f"[WATCHLIST] Max tokens reached ({CONFIG.watchlist.max_tokens})")
            return False

        self.targets[symbol] = WatchTarget(
            symbol       = symbol,
            mint_address = mint,
            pipeline     = pipeline,
            added_at     = datetime.utcnow(),
            added_reason = reason,
        )
        logger.info(f"[WATCHLIST] Added {symbol} | pipeline={pipeline.value} | reason={reason}")
        return True

    def remove(self, symbol: str, reason: str = ""):
        if symbol in self.targets:
            del self.targets[symbol]
            logger.info(f"[WATCHLIST] Removed {symbol} | reason={reason}")

    def blacklist_token(self, symbol: str, reason: str):
        self.blacklist.add(symbol)
        self.remove(symbol, reason=f"blacklisted: {reason}")
        logger.warning(f"[WATCHLIST] Blacklisted {symbol} | {reason}")

    # ─────────────────────────────────────────
    #  STATE TRANSITIONS
    # ─────────────────────────────────────────

    def set_triggered(self, symbol: str):
        t = self.targets.get(symbol)
        if t:
            t.status = WatchStatus.TRIGGERED
            t.last_updated = datetime.utcnow()

    def set_active(self, symbol: str, entry_price: float, entry_qty: float,
                   entry_usd: float, order_id: str):
        t = self.targets.get(symbol)
        if t:
            t.status        = WatchStatus.ACTIVE
            t.entry_price   = entry_price
            t.entry_qty     = entry_qty
            t.entry_usd     = entry_usd
            t.bybit_order_id = order_id
            t.hard_stop     = entry_price * (1 - CONFIG.risk.hard_stop_pct)
            trail_pct       = (CONFIG.risk.trail_stop_new
                               if t.pipeline == Pipeline.NEW_TOKEN
                               else CONFIG.risk.trail_stop_event)
            t.trailing_stop = entry_price * (1 - trail_pct)
            t.last_updated  = datetime.utcnow()
            logger.info(f"[WATCHLIST] {symbol} ACTIVE | entry={entry_price:.8f} "
                        f"| hard_stop={t.hard_stop:.8f} | trail={t.trailing_stop:.8f}")

    def set_cooling(self, symbol: str):
        t = self.targets.get(symbol)
        if t:
            t.status = WatchStatus.COOLING
            t.last_updated = datetime.utcnow()

    def update_trailing_stop(self, symbol: str, current_price: float):
        """Ratchet trailing stop up as price rises. Never moves down."""
        t = self.targets.get(symbol)
        if not t or t.status != WatchStatus.ACTIVE:
            return

        trail_pct = (CONFIG.risk.trail_stop_new
                     if t.pipeline == Pipeline.NEW_TOKEN
                     else CONFIG.risk.trail_stop_event)
        new_stop = current_price * (1 - trail_pct)

        if new_stop > t.trailing_stop:
            t.trailing_stop = new_stop
            logger.debug(f"[WATCHLIST] {symbol} trailing stop → {new_stop:.8f}")

    # ─────────────────────────────────────────
    #  SIGNAL UPDATE
    # ─────────────────────────────────────────

    def update_signal(self, snap: SignalSnapshot):
        t = self.targets.get(snap.symbol)
        if not t:
            return

        t.sentiment    = snap.sentiment_eff
        t.velocity     = snap.velocity
        t.bot_pct      = snap.bot_pct
        t.volume_sigma = snap.volume_sigma
        t.netflow_usd  = snap.netflow_usd
        t.last_updated = datetime.utcnow()
        t.signal_count += 1

        # Auto-promote to TRIGGERED if signal is BUY but not yet active
        if snap.signal == Signal.BUY and t.status == WatchStatus.SCANNING:
            self.set_triggered(snap.symbol)

    # ─────────────────────────────────────────
    #  QUERIES
    # ─────────────────────────────────────────

    def get_ready(self) -> list[WatchTarget]:
        """Tokens with TRIGGERED status — ready for HunterGate final check."""
        return [t for t in self.targets.values()
                if t.status == WatchStatus.TRIGGERED]

    def get_active(self) -> list[WatchTarget]:
        return [t for t in self.targets.values()
                if t.status == WatchStatus.ACTIVE]

    def get_scanning(self) -> list[WatchTarget]:
        return [t for t in self.targets.values()
                if t.status == WatchStatus.SCANNING]

    def total_exposure_usd(self) -> float:
        return sum(t.entry_usd for t in self.get_active())

    def total_exposure_pct(self) -> float:
        return self.total_exposure_usd() / CONFIG.risk.portfolio_value

    # ─────────────────────────────────────────
    #  AUTO-PRUNE
    # ─────────────────────────────────────────

    def prune(self):
        """Remove stale, honeypot, or cooled-down tokens."""
        to_remove = []
        wcfg = CONFIG.watchlist

        for sym, t in self.targets.items():
            if t.is_honeypot or t.is_blacklist:
                to_remove.append((sym, "honeypot/blacklist"))
                continue

            if t.status == WatchStatus.SCANNING:
                if t.hours_idle > wcfg.stale_hours_scanning:
                    to_remove.append((sym, f"stale: {t.hours_idle:.1f}h idle"))

            if t.status == WatchStatus.COOLING:
                if t.hours_idle > wcfg.cooling_hours:
                    to_remove.append((sym, "cooling period complete"))

        for sym, reason in to_remove:
            self.remove(sym, reason)

        if to_remove:
            logger.info(f"[WATCHLIST] Pruned {len(to_remove)} tokens")

    # ─────────────────────────────────────────
    #  STATUS SUMMARY
    # ─────────────────────────────────────────

    def summary(self) -> dict:
        counts = {s: 0 for s in WatchStatus}
        for t in self.targets.values():
            counts[t.status] += 1
        return {
            "total":     len(self.targets),
            "scanning":  counts[WatchStatus.SCANNING],
            "triggered": counts[WatchStatus.TRIGGERED],
            "active":    counts[WatchStatus.ACTIVE],
            "cooling":   counts[WatchStatus.COOLING],
            "exposure_usd": round(self.total_exposure_usd(), 2),
            "exposure_pct": round(self.total_exposure_pct() * 100, 2),
            "blacklisted": len(self.blacklist),
        }
