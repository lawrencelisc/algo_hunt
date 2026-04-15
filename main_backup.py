#!/usr/bin/env python3
# APEX — Phase 1 Simulation Runner
# Bybit Testnet | Mock sentiment + on-chain | Console TG output

import asyncio
import logging
import sys
import os
import random
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config.settings import CONFIG, Pipeline
from core.models import SignalSnapshot
from core.sentiment_engine import SentimentEngine
from core.watchlist import WatchlistManager
from core.portfolio import PortfolioManager
from exchange.bybit_client import BybitTestnetClient
from reporting.telegram_bot import TelegramReporter
from data.mock_feeds import (
    MOCK_NEW_TOKENS, MOCK_EVENT_TOKENS,
    MockPumpFunWatcher, assign_scenario
)

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s  %(name)-20s  %(levelname)-7s  %(message)s",
    datefmt = "%H:%M:%S",
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/apex_simulation.log"),
    ]
)
logger = logging.getLogger("apex.main")


# ─────────────────────────────────────────────
#  COMPONENT INIT
# ─────────────────────────────────────────────

sentiment_engine = SentimentEngine()
watchlist        = WatchlistManager()
exchange         = BybitTestnetClient()
portfolio        = PortfolioManager(watchlist, exchange)
reporter         = TelegramReporter(watchlist)
pump_watcher     = MockPumpFunWatcher()


# ─────────────────────────────────────────────
#  STARTUP — seed watchlist with event tokens
# ─────────────────────────────────────────────

def seed_event_tokens():
    for token in MOCK_EVENT_TOKENS:
        watchlist.add(
            symbol   = token["symbol"],
            mint     = token["mint"],
            pipeline = Pipeline.EVENT_DRIVEN,
            reason   = f"event_driven:{token['event']}",
        )
    logger.info(f"[MAIN] Seeded {len(MOCK_EVENT_TOKENS)} event-driven tokens")


# ─────────────────────────────────────────────
#  PUMP.FUN LISTENER  (new token pipeline)
# ─────────────────────────────────────────────

async def pump_fun_listener():
    """Listen for new token launches and add to watchlist."""
    logger.info("[MAIN] Pump.fun listener started")
    async for launch in pump_watcher.stream_launches():
        symbol = launch["symbol"]
        mint   = launch["mint"]

        logger.info(f"[PUMP] 🆕 New launch: {symbol} | scenario={launch['scenario']}")

        # Check honeypot before adding
        from data.mock_feeds import MockOnChainFetcher
        fetcher  = MockOnChainFetcher()
        honeypot = await fetcher.check_honeypot(mint)

        if honeypot:
            reporter.alert_honeypot(symbol)
            watchlist.blacklist_token(symbol, "honeypot on launch")
            continue

        added = watchlist.add(symbol, mint, Pipeline.NEW_TOKEN, "pump_fun_launch")
        if added:
            reporter.alert_new_token(symbol, mint, "new_token")


# ─────────────────────────────────────────────
#  SCAN LOOP  (evaluate all watchlist tokens)
# ─────────────────────────────────────────────

async def scan_loop():
    """Main scanning loop — evaluates sentiment for all watchlist tokens."""
    logger.info("[MAIN] Scan loop started")

    iteration = 0
    while True:
        iteration += 1
        targets = list(watchlist.targets.values())

        if not targets:
            await asyncio.sleep(10)
            continue

        logger.info(f"[SCAN] Iteration {iteration} | "
                    f"Tokens: {len(targets)} | "
                    f"Active: {len(watchlist.get_active())}")

        for target in targets:
            try:
                # Evaluate signal
                snap = await sentiment_engine.evaluate(
                    target.symbol, target.mint_address, target.pipeline
                )
                watchlist.update_signal(snap)

                # Log signal decision
                gate_str = (
                    f"G1={'✅' if snap.gate1_pass else '❌'} "
                    f"G2={'✅' if snap.gate2_pass else '❌'} "
                    f"G3={'✅' if snap.gate3_pass else '❌'}"
                )
                logger.info(
                    f"[SCAN] {target.symbol:8s} | "
                    f"s={snap.sentiment_eff:.2f} v={snap.velocity:+.0%} "
                    f"b={snap.bot_pct:.0%} σ={snap.volume_sigma:.1f} | "
                    f"{gate_str} → {snap.signal.value}"
                )

                # Attempt entry if triggered
                if target.status.value == "triggered":
                    success = await portfolio.attempt_entry(target, snap)
                    if success:
                        t = watchlist.targets.get(target.symbol)
                        if t:
                            reporter.alert_buy(
                                symbol      = target.symbol,
                                entry_price = t.entry_price,
                                usd_amount  = t.entry_usd,
                                sentiment   = snap.sentiment_eff,
                                velocity    = snap.velocity,
                                bot_pct     = snap.bot_pct,
                                pipeline    = target.pipeline.value,
                            )

            except Exception as e:
                logger.error(f"[SCAN] Error evaluating {target.symbol}: {e}", exc_info=True)

        # Monitor exits for active positions
        await portfolio.monitor_exits()

        # Prune stale tokens
        watchlist.prune()

        # Scan interval: 60s in simulation (faster for testing)
        await asyncio.sleep(15)


# ─────────────────────────────────────────────
#  DAILY DIGEST SCHEDULER
# ─────────────────────────────────────────────

async def digest_scheduler():
    """Send daily digest. In simulation: sends every 5 minutes for testing."""
    logger.info("[MAIN] Digest scheduler started")
    await asyncio.sleep(30)     # initial delay

    while True:
        stats = portfolio.daily_stats()
        reporter.send_daily_digest(stats)
        portfolio.reset_daily_counters()

        # Simulation: every 5 min. Live: every 24h
        interval = 300 if CONFIG.phase == "simulation" else 86400
        await asyncio.sleep(interval)


# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

async def main():
    os.makedirs("logs", exist_ok=True)

    logger.info("=" * 55)
    logger.info("  APEX Hunter System — Phase 1 Simulation")
    logger.info(f"  Exchange: Bybit Testnet  |  Dry-run: {CONFIG.dry_run}")
    logger.info(f"  Portfolio: ${CONFIG.risk.portfolio_value:,.0f}")
    logger.info(f"  Max exposure: {CONFIG.risk.total_exposure_cap:.0%}")
    logger.info("=" * 55)

    # Seed event-driven tokens
    seed_event_tokens()

    # Run all tasks concurrently
    await asyncio.gather(
        pump_fun_listener(),
        scan_loop(),
        digest_scheduler(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("\n[MAIN] Simulation stopped by user")
        stats = portfolio.daily_stats()
        logger.info(f"[MAIN] Final stats: {stats}")
