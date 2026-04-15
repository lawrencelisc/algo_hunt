# APEX — Mock Data Layer (Phase 1 Simulation)
# Replaces: Twitter API + Helius RPC
# Produces realistic signal distributions for architecture validation

import asyncio
import math
import random
from datetime import datetime
from typing import Optional
from config.settings import Pipeline


# ─────────────────────────────────────────────
#  MOCK TOKEN REGISTRY
# ─────────────────────────────────────────────

MOCK_NEW_TOKENS = [
    {"symbol": "PEPE",    "mint": "mock_mint_pepe",    "launch_hour": 0},
    {"symbol": "BOME",    "mint": "mock_mint_bome",    "launch_hour": 2},
    {"symbol": "MOODENG", "mint": "mock_mint_moodeng", "launch_hour": 5},
    {"symbol": "GOAT",    "mint": "mock_mint_goat",    "launch_hour": 8},
    {"symbol": "PNUT",    "mint": "mock_mint_pnut",    "launch_hour": 12},
    {"symbol": "FWOG",    "mint": "mock_mint_fwog",    "launch_hour": 16},
]

MOCK_EVENT_TOKENS = [
    {"symbol": "DOGE",  "mint": "mock_mint_doge",  "event": "kol_mention"},
    {"symbol": "SHIB",  "mint": "mock_mint_shib",  "event": "tech_upgrade"},
    {"symbol": "XRP",   "mint": "mock_mint_xrp",   "event": "regulatory"},
    {"symbol": "WIF",   "mint": "mock_mint_wif",   "event": "kol_mention"},
]

# Pre-defined scenario archetypes
SCENARIOS = {
    "clean_signal": {
        # Real organic breakout — all gates should pass
        "sentiment_base": 0.82,
        "sentiment_std":  0.05,
        "velocity_base":  4.5,
        "velocity_std":   0.8,
        "bot_pct_base":   0.22,
        "bot_pct_std":    0.08,
        "volume_sigma":   7.2,
        "netflow_usd":    85_000,
        "expected_signal": "BUY",
    },
    "bot_pump": {
        # High sentiment but bot-driven — should be filtered
        "sentiment_base": 0.88,
        "sentiment_std":  0.04,
        "velocity_base":  6.1,
        "velocity_std":   1.2,
        "bot_pct_base":   0.68,
        "bot_pct_std":    0.05,
        "volume_sigma":   3.1,
        "netflow_usd":    12_000,
        "expected_signal": "SKIP",
    },
    "low_velocity": {
        # Genuine sentiment but slow build — event-driven acceptable, new token skip
        "sentiment_base": 0.74,
        "sentiment_std":  0.06,
        "velocity_base":  1.8,
        "velocity_std":   0.4,
        "bot_pct_base":   0.18,
        "bot_pct_std":    0.06,
        "volume_sigma":   4.1,
        "netflow_usd":    62_000,
        "expected_signal": "SKIP",  # for new token; HOLD for event
    },
    "rug_risk": {
        # Dev wallet dump signal
        "sentiment_base": 0.91,
        "sentiment_std":  0.03,
        "velocity_base":  8.0,
        "velocity_std":   1.5,
        "bot_pct_base":   0.55,
        "bot_pct_std":    0.10,
        "volume_sigma":   9.5,
        "netflow_usd":    200_000,
        "expected_signal": "SKIP",  # blocked by bot filter
    },
    "cold_market": {
        # Dead token, nothing happening
        "sentiment_base": 0.45,
        "sentiment_std":  0.08,
        "velocity_base":  0.3,
        "velocity_std":   0.2,
        "bot_pct_base":   0.12,
        "bot_pct_std":    0.05,
        "volume_sigma":   0.8,
        "netflow_usd":    2_000,
        "expected_signal": "IGNORE",
    },
}

SCENARIO_WEIGHTS = {
    "clean_signal": 0.15,   # 15% — real alpha is rare
    "bot_pump":     0.25,   # 25% — bots are everywhere
    "low_velocity": 0.30,   # 30% — most signals are borderline
    "rug_risk":     0.10,   # 10% — rug attempts
    "cold_market":  0.20,   # 20% — dead coins
}


# ─────────────────────────────────────────────
#  MOCK TWITTER FETCHER
# ─────────────────────────────────────────────

class MockTwitterFetcher:
    """
    Simulates Twitter API v2 Streaming output.
    Returns posts with follower, likes, retweets, views.
    Phase 2: replace with real tweepy StreamingClient.
    """

    KOL_PROFILES = [
        {"handle": "@CryptoWhale",   "followers": 890_000, "authority": "high"},
        {"handle": "@SolanaKing",    "followers": 340_000, "authority": "high"},
        {"handle": "@MemeTrader99",  "followers": 52_000,  "authority": "mid"},
        {"handle": "@DeFiDegen",     "followers": 28_000,  "authority": "mid"},
        {"handle": "@CryptoNewbie",  "followers": 1_200,   "authority": "low"},
        {"handle": "@Bot_Array_001", "followers": 45,      "authority": "bot"},
    ]

    BULLISH_TEXTS = [
        "This is the one. Early.",
        "Accumulating hard. Don't miss this.",
        "On-chain data looks insane right now.",
        "KOL rotation incoming. Watch this.",
        "Liquidity building. Chart looks perfect.",
    ]

    BEARISH_TEXTS = [
        "Dev wallet moving. Be careful.",
        "Seen this before. Exit plan ready.",
        "Volume faking. Stay out.",
        "Rug incoming. Don't touch.",
    ]

    NEUTRAL_TEXTS = [
        "Watching this one closely.",
        "Interesting chart pattern forming.",
        "Could go either way from here.",
    ]

    async def fetch(self, symbol: str, scenario: str, n_posts: int = 40) -> list[dict]:
        await asyncio.sleep(0.05)   # simulate network latency

        sc = SCENARIOS[scenario]
        posts = []

        for _ in range(n_posts):
            is_bot  = random.random() < sc["bot_pct_base"]
            profile = (
                random.choice([p for p in self.KOL_PROFILES if p["authority"] == "bot"])
                if is_bot else
                random.choice([p for p in self.KOL_PROFILES if p["authority"] != "bot"])
            )

            # Sentiment direction based on scenario
            if sc["sentiment_base"] > 0.7:
                texts = self.BULLISH_TEXTS
            elif sc["sentiment_base"] < 0.5:
                texts = self.BEARISH_TEXTS
            else:
                texts = self.NEUTRAL_TEXTS

            followers = profile["followers"]
            views     = max(1, int(followers * random.uniform(0.05, 0.3)))
            likes     = int(views * random.uniform(0.001 if is_bot else 0.02, 0.005 if is_bot else 0.06))
            retweets  = int(likes * random.uniform(0.1, 0.4))

            posts.append({
                "text":      random.choice(texts) + f" ${symbol}",
                "followers": followers,
                "likes":     likes,
                "retweets":  retweets,
                "views":     views,
                "is_bot":    is_bot,
                "handle":    profile["handle"],
            })

        return posts


# ─────────────────────────────────────────────
#  MOCK HELIUS / ON-CHAIN FETCHER
# ─────────────────────────────────────────────

class MockOnChainFetcher:
    """
    Simulates Helius RPC responses.
    Phase 2: replace with real aiohttp calls to Helius API.
    """

    async def get_volume_data(self, mint_address: str, scenario: str) -> dict:
        await asyncio.sleep(0.03)

        sc = SCENARIOS[scenario]
        volume_sigma = sc["volume_sigma"] + random.gauss(0, 0.5)
        netflow      = sc["netflow_usd"]  * random.uniform(0.85, 1.15)

        return {
            "mint":         mint_address,
            "volume_sigma": round(volume_sigma, 2),
            "netflow_usd":  round(netflow, 0),
            "buy_count":    random.randint(50, 800),
            "sell_count":   random.randint(10, 200),
            "unique_wallets": random.randint(20, 500),
        }

    async def check_honeypot(self, mint_address: str) -> bool:
        """Returns True if honeypot detected."""
        await asyncio.sleep(0.02)
        # Simulate 8% chance of honeypot on new tokens
        return random.random() < 0.08

    async def get_dev_wallet_pct(self, mint_address: str) -> float:
        """Returns fraction of supply held by dev wallet."""
        await asyncio.sleep(0.02)
        return random.uniform(0.02, 0.25)


# ─────────────────────────────────────────────
#  MOCK PRICE FEED
# ─────────────────────────────────────────────

class MockPriceFeed:
    """
    Simulates live price data for open position tracking.
    Phase 2: replace with Bybit WebSocket ticker.
    """

    def __init__(self):
        self._prices: dict[str, float] = {}
        self._base_prices = {
            "PEPE":    0.000012,
            "BOME":    0.0082,
            "MOODENG": 0.25,
            "GOAT":    0.42,
            "PNUT":    0.18,
            "FWOG":    0.0034,
            "DOGE":    0.182,
            "SHIB":    0.0000245,
            "XRP":     0.61,
            "WIF":     1.85,
        }

    def get_price(self, symbol: str) -> float:
        base = self._base_prices.get(symbol, 1.0)
        if symbol not in self._prices:
            self._prices[symbol] = base
        # Random walk ±3% per tick
        change = random.gauss(0, 0.015)
        self._prices[symbol] *= (1 + change)
        return round(self._prices[symbol], 8)

    async def get_price_async(self, symbol: str) -> float:
        await asyncio.sleep(0.01)
        return self.get_price(symbol)


# ─────────────────────────────────────────────
#  MOCK PUMP.FUN LAUNCHER
# ─────────────────────────────────────────────

class MockPumpFunWatcher:
    """
    Simulates Pump.fun new token launch events.
    Phase 2: replace with real Pump.fun WebSocket or Helius webhook.
    """

    async def stream_launches(self):
        """
        Async generator — yields new token launch events.
        Simulates ~1 new token every 30-120 seconds.
        """
        for token in MOCK_NEW_TOKENS:
            await asyncio.sleep(random.uniform(30, 90))
            yield {
                "event":    "new_launch",
                "symbol":   token["symbol"],
                "mint":     token["mint"],
                "timestamp": datetime.utcnow(),
                "scenario": random.choices(
                    list(SCENARIO_WEIGHTS.keys()),
                    weights=list(SCENARIO_WEIGHTS.values())
                )[0]
            }


# ─────────────────────────────────────────────
#  SCENARIO SELECTOR  (for existing watchlist tokens)
# ─────────────────────────────────────────────

def assign_scenario(symbol: str, hour: Optional[int] = None) -> str:
    """
    Deterministically assigns scenario based on symbol + hour.
    Allows reproducible backtest replay.
    """
    h = hour if hour is not None else datetime.utcnow().hour
    seed_val = sum(ord(c) for c in symbol) + h
    random.seed(seed_val)
    scenario = random.choices(
        list(SCENARIO_WEIGHTS.keys()),
        weights=list(SCENARIO_WEIGHTS.values())
    )[0]
    random.seed()   # reset to non-deterministic
    return scenario
