# APEX — PortfolioManager + HunterGate
# Unified position control and final entry decision

import logging
import time
from datetime import datetime
from typing import Optional

from config.settings import CONFIG, Pipeline, Signal
from core.models import WatchTarget, TradeRecord, SignalSnapshot
from core.watchlist import WatchlistManager
from exchange.bybit_client import BybitTestnetClient
from data.mock_feeds import MockPriceFeed

logger = logging.getLogger("apex.portfolio")


class HunterGate:
    """
    Final entry decision point.
    Called only when WatchlistManager promotes token to TRIGGERED.
    Double-checks all conditions before instructing execution.
    """

    def evaluate(self, target: WatchTarget, snap: SignalSnapshot) -> tuple[bool, str]:
        cfg = CONFIG.gate
        risk = CONFIG.risk

        # Exposure cap
        current_exposure = target.entry_usd if target.entry_usd else 0.0
        # (passed in from PortfolioManager — checked there)

        # Re-verify gates with latest snapshot
        if not snap.gate1_pass:
            return False, f"Gate1 fail: sentiment {snap.sentiment_eff:.2f}"

        if not snap.gate2_pass:
            return False, f"Gate2 fail: velocity {snap.velocity:.1%}"

        if not snap.gate3_pass:
            return False, f"Gate3 fail: on-chain insufficient"

        if snap.bot_pct > cfg.bot_pct_hard:
            return False, f"Hard bot block: {snap.bot_pct:.0%}"

        if snap.signal != Signal.BUY:
            return False, f"Signal is {snap.signal.value}, not BUY"

        return True, "HunterGate approved"


class PortfolioManager:
    """
    Unified risk and position manager.
    Knows about ALL open positions across Bybit + Hyperliquid.
    Enforces exposure cap before any order.
    Manages trailing stops, TP, and circuit breaker.
    """

    def __init__(self, watchlist: WatchlistManager, exchange: BybitTestnetClient):
        self.watchlist  = watchlist
        self.exchange   = exchange
        self.gate       = HunterGate()
        self.price_feed = MockPriceFeed()

        self.trade_log: list[TradeRecord] = []

        # Circuit breaker state
        self._stop_timestamps: list[float] = []
        self._circuit_open_until: float = 0.0

        # Daily counters
        self._signals_today: int = 0
        self._buys_today:    int = 0
        self._skips_today:   int = 0
        self._daily_pnl:     float = 0.0

    # ─────────────────────────────────────────
    #  ENTRY
    # ─────────────────────────────────────────

    async def attempt_entry(self, target: WatchTarget, snap: SignalSnapshot) -> bool:
        """
        Full entry flow:
        1. Circuit breaker check
        2. Exposure cap check
        3. HunterGate final evaluation
        4. Slippage check
        5. Order placement
        6. State update
        """
        self._signals_today += 1

        # 1. Circuit breaker
        if self._is_circuit_open():
            logger.warning(f"[PM] Circuit breaker open — skipping {target.symbol}")
            self._skips_today += 1
            return False

        # 2. Exposure cap
        pos_size = (CONFIG.risk.pos_size_new
                    if target.pipeline == Pipeline.NEW_TOKEN
                    else CONFIG.risk.pos_size_event)
        usd_amount = CONFIG.risk.portfolio_value * pos_size

        current_exposure_pct = self.watchlist.total_exposure_pct()
        if current_exposure_pct + pos_size > CONFIG.risk.total_exposure_cap:
            logger.warning(
                f"[PM] Exposure cap reached: "
                f"{current_exposure_pct:.1%} + {pos_size:.1%} > {CONFIG.risk.total_exposure_cap:.1%}"
            )
            self._skips_today += 1
            return False

        # 3. HunterGate
        approved, reason = self.gate.evaluate(target, snap)
        if not approved:
            logger.info(f"[PM] HunterGate SKIP {target.symbol}: {reason}")
            self._skips_today += 1
            return False

        # 4. Slippage check
        acceptable, slippage = await self.exchange.check_slippage(target.symbol, usd_amount)
        if not acceptable:
            logger.warning(
                f"[PM] Slippage too high for {target.symbol}: {slippage:.1%}"
            )
            self._skips_today += 1
            return False

        # 5. Place order (dry-run in Phase 1)
        result = await self.exchange.place_market_buy(target.symbol, usd_amount)
        if not result["success"]:
            logger.error(f"[PM] Order failed for {target.symbol}: {result.get('error')}")
            self._skips_today += 1
            return False

        # 6. Mock fill price
        fill_price = self.price_feed.get_price(target.symbol)
        fill_qty   = usd_amount / fill_price if fill_price > 0 else 0

        # 7. Update watchlist state
        self.watchlist.set_active(
            symbol      = target.symbol,
            entry_price = fill_price,
            entry_qty   = fill_qty,
            entry_usd   = usd_amount,
            order_id    = result["order_id"],
        )

        # 8. Log trade
        trade = TradeRecord(
            trade_id    = result["order_id"],
            symbol      = target.symbol,
            pipeline    = target.pipeline,
            entry_time  = datetime.utcnow(),
            entry_price = fill_price,
            entry_qty   = fill_qty,
            entry_usd   = usd_amount,
            sentiment_at_entry = snap.sentiment_eff,
            velocity_at_entry  = snap.velocity,
            bot_pct_at_entry   = snap.bot_pct,
        )
        self.trade_log.append(trade)
        self._buys_today += 1

        logger.info(
            f"[PM] ✅ BUY {target.symbol} | "
            f"${usd_amount:.2f} @ {fill_price:.8f} | "
            f"sentiment={snap.sentiment_eff:.2f} vel={snap.velocity:.0%} bot={snap.bot_pct:.0%}"
        )
        return True

    # ─────────────────────────────────────────
    #  EXIT MONITORING
    # ─────────────────────────────────────────

    async def monitor_exits(self):
        """Check all active positions for exit conditions."""
        for target in self.watchlist.get_active():
            current_price = self.price_feed.get_price(target.symbol)
            await self._check_exit(target, current_price)

    async def _check_exit(self, target: WatchTarget, current_price: float):
        exit_reason = None

        # Update trailing stop
        self.watchlist.update_trailing_stop(target.symbol, current_price)

        # Hard stop-loss
        if current_price <= target.hard_stop:
            exit_reason = "hard_stop"

        # Trailing stop
        elif current_price <= target.trailing_stop:
            exit_reason = "trailing_stop"

        # TP1: 2x — sell 50%
        elif not target.tp1_triggered:
            if target.entry_price > 0:
                gain = (current_price / target.entry_price) - 1
                if gain >= (CONFIG.risk.tp1_multiplier - 1):
                    await self._execute_partial_exit(target, current_price, 0.5)
                    target.tp1_triggered = True
                    logger.info(f"[PM] 🟡 TP1 {target.symbol} @ {current_price:.8f} (+{gain:.0%})")
                    return

        # Sentiment reversal (from update_signal — checked in scan loop)
        elif target.sentiment < 0.40 and target.entry_price > 0:
            gain = (current_price / target.entry_price) - 1
            if gain < 0:  # only exit if in loss territory
                exit_reason = "sentiment_reversal"

        if exit_reason:
            await self._execute_full_exit(target, current_price, exit_reason)

    async def _execute_partial_exit(self, target: WatchTarget, price: float, sell_fraction: float):
        qty = target.entry_qty * sell_fraction
        await self.exchange.place_market_sell(target.symbol, qty)
        target.entry_qty -= qty
        logger.info(f"[PM] Partial exit {target.symbol}: sold {sell_fraction:.0%} @ {price:.8f}")

    async def _execute_full_exit(self, target: WatchTarget, price: float, reason: str):
        await self.exchange.place_market_sell(target.symbol, target.entry_qty)

        pnl_usd = (price - target.entry_price) * target.entry_qty
        pnl_pct = ((price / target.entry_price) - 1) if target.entry_price > 0 else 0

        # Update trade log
        for trade in self.trade_log:
            if trade.symbol == target.symbol and trade.is_open:
                trade.exit_time   = datetime.utcnow()
                trade.exit_price  = price
                trade.exit_usd    = target.entry_qty * price
                trade.exit_reason = reason
                trade.pnl_usd     = round(pnl_usd, 4)
                trade.pnl_pct     = round(pnl_pct, 4)
                trade.is_open     = False
                break

        self._daily_pnl += pnl_usd
        self.watchlist.set_cooling(target.symbol)

        emoji = "🔴" if pnl_usd < 0 else "🟢"
        logger.info(
            f"[PM] {emoji} EXIT {target.symbol} | reason={reason} | "
            f"pnl={pnl_usd:+.2f} USD ({pnl_pct:+.1%})"
        )

        # Circuit breaker tracking
        if reason in ("hard_stop", "trailing_stop") and pnl_usd < 0:
            self._record_stop()

    # ─────────────────────────────────────────
    #  CIRCUIT BREAKER
    # ─────────────────────────────────────────

    def _record_stop(self):
        now = time.time()
        self._stop_timestamps.append(now)
        # Keep only stops within the window
        window = CONFIG.risk.circuit_breaker_window
        self._stop_timestamps = [t for t in self._stop_timestamps if now - t < window]

        if len(self._stop_timestamps) >= CONFIG.risk.circuit_breaker_stops:
            self._circuit_open_until = now + CONFIG.risk.circuit_breaker_pause
            logger.warning(
                f"[PM] ⛔ CIRCUIT BREAKER TRIGGERED — "
                f"pausing {CONFIG.risk.circuit_breaker_pause // 3600}h"
            )

    def _is_circuit_open(self) -> bool:
        return time.time() < self._circuit_open_until

    # ─────────────────────────────────────────
    #  STATS FOR TG REPORT
    # ─────────────────────────────────────────

    def daily_stats(self) -> dict:
        return {
            "signals":   self._signals_today,
            "buys":      self._buys_today,
            "skips":     self._skips_today,
            "daily_pnl": round(self._daily_pnl, 2),
            "open_trades": len(self.watchlist.get_active()),
            "circuit_open": self._is_circuit_open(),
        }

    def reset_daily_counters(self):
        self._signals_today = 0
        self._buys_today    = 0
        self._skips_today   = 0
        self._daily_pnl     = 0.0
