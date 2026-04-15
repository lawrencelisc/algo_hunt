# APEX — SentimentEngine v7.5.2
# Authority-Weighted Scoring + Bot Suppression + Three-Layer Gate

import asyncio
import math
import random
from datetime import datetime

from config.settings import CONFIG, Signal, Pipeline
from core.models import SignalSnapshot
from data.onchain_router import OnChainRouter
from data.mock_feeds import (
    MockTwitterFetcher,
    SCENARIOS, assign_scenario
)


class SentimentEngine:
    """
    Core signal engine.
    Inputs:  Mock Twitter posts + Mock on-chain data
    Output:  SignalSnapshot with gate results and final Signal
    """

    def __init__(self):
        self.twitter   = MockTwitterFetcher()
        self.onchain   = OnChainRouter()
        self._velocity_history: dict[str, list[float]] = {}

    # ─────────────────────────────────────────
    #  MAIN ENTRY POINT
    # ─────────────────────────────────────────

    async def evaluate(self, symbol: str, mint: str, pipeline: Pipeline) -> SignalSnapshot:
        """
        Full evaluation pipeline for one token.
        Returns SignalSnapshot with all gate results.
        """
        scenario = assign_scenario(symbol)

        # Parallel fetch: social + on-chain
        posts, onchain_data = await asyncio.gather(
            self.twitter.fetch(symbol, scenario),
            self.onchain.get_volume_data(mint, scenario)
        )

        # Step 1: Authority-weighted sentiment score
        sentiment_raw, kol_count = self._authority_weighted_score(posts)

        # Step 2: Bot percentage
        bot_pct = self._calculate_bot_pct(posts)

        # Step 3: Apply bot penalty
        sentiment_eff = (
            sentiment_raw * 0.5
            if bot_pct > CONFIG.gate.bot_pct_max
            else sentiment_raw
        )

        # Step 4: Sentiment velocity
        velocity = self._calculate_velocity(symbol, sentiment_eff)

        # Step 5: Three-layer gate
        gate1, gate2, gate3, signal, reason = self._evaluate_gates(
            symbol, sentiment_eff, velocity, bot_pct,
            onchain_data["volume_sigma"], onchain_data["netflow_usd"],
            pipeline
        )

        return SignalSnapshot(
            symbol        = symbol,
            timestamp     = datetime.utcnow(),
            sentiment_raw = round(sentiment_raw, 3),
            sentiment_eff = round(sentiment_eff, 3),
            bot_pct       = round(bot_pct, 3),
            velocity      = round(velocity, 3),
            volume_sigma  = onchain_data["volume_sigma"],
            netflow_usd   = onchain_data["netflow_usd"],
            gate1_pass    = gate1,
            gate2_pass    = gate2,
            gate3_pass    = gate3,
            signal        = signal,
            reason        = reason,
            tweet_count   = len(posts),
            kol_count     = kol_count,
        )

    # ─────────────────────────────────────────
    #  STEP 1: AUTHORITY-WEIGHTED SCORE
    # ─────────────────────────────────────────

    def _authority_weighted_score(self, posts: list[dict]) -> tuple[float, int]:
        """
        Formula: Σ(sᵢ · ln(fᵢ+1) · (1+eᵢ)) / Σ(ln(fᵢ+1))

        sᵢ = base sentiment (-1 to +1)   — mocked via scenario
        fᵢ = follower count               — from mock KOL profiles
        eᵢ = engagement ratio             — (likes+RT) / views
        """
        if not posts:
            return 0.5, 0

        numerator   = 0.0
        denominator = 0.0
        kol_count   = 0

        for post in posts:
            # Mock sentiment: positive text → +0.7~1.0, negative → -0.7~-1.0
            s_i = self._mock_sentiment_score(post["text"])

            # Log-follower weight
            f_i = math.log(post["followers"] + 1)

            # Engagement ratio (bots score near 0)
            views = max(post["views"], 1)
            e_i   = (post["likes"] + post["retweets"]) / views

            numerator   += s_i * f_i * (1 + e_i)
            denominator += f_i

            if post["followers"] > 10_000:
                kol_count += 1

        if denominator == 0:
            return 0.5, 0

        raw_score      = numerator / denominator        # -1 to +1
        normalized     = (raw_score + 1) / 2           # 0 to 1
        return min(1.0, max(0.0, normalized)), kol_count

    def _mock_sentiment_score(self, text: str) -> float:
        """
        Phase 1: rule-based mock.
        Phase 2: replace with CryptoBERT inference.
        """
        bullish_words = ["moon", "early", "accumulate", "insane", "perfect", "building", "incoming"]
        bearish_words = ["rug", "careful", "exit", "fake", "dump", "scam"]

        text_lower = text.lower()
        bull = sum(1 for w in bullish_words if w in text_lower)
        bear = sum(1 for w in bearish_words if w in text_lower)

        if bull > bear:
            return random.uniform(0.55, 0.95)
        elif bear > bull:
            return random.uniform(-0.95, -0.3)
        else:
            return random.uniform(-0.15, 0.15)

    # ─────────────────────────────────────────
    #  STEP 2: BOT PERCENTAGE
    # ─────────────────────────────────────────

    def _calculate_bot_pct(self, posts: list[dict]) -> float:
        """
        Engagement ratio method:
        ratio < 0.005 → classified as bot
        Phase 2: augment with account age + tweet frequency signals.
        """
        if not posts:
            return 0.0

        bot_count = 0
        for post in posts:
            views = max(post["views"], 1)
            ratio = (post["likes"] + post["retweets"]) / views
            if ratio < 0.005:
                bot_count += 1

        return bot_count / len(posts)

    # ─────────────────────────────────────────
    #  STEP 3: VELOCITY
    # ─────────────────────────────────────────

    def _calculate_velocity(self, symbol: str, current_score: float) -> float:
        """
        Velocity = (current / baseline) - 1
        Baseline = rolling mean of last 59 scores.
        Window: 60 ticks (approx 60 seconds in live mode).
        """
        if symbol not in self._velocity_history:
            self._velocity_history[symbol] = []

        history = self._velocity_history[symbol]
        history.append(current_score)

        if len(history) > 60:
            history.pop(0)

        if len(history) < 5:
            return 0.0

        baseline = sum(history[:-1]) / len(history[:-1])
        if baseline <= 0:
            return 0.0

        return (current_score / baseline) - 1

    # ─────────────────────────────────────────
    #  STEP 4: THREE-LAYER GATE
    # ─────────────────────────────────────────

    def _evaluate_gates(
        self, symbol: str,
        sentiment_eff: float, velocity: float, bot_pct: float,
        volume_sigma: float, netflow_usd: float,
        pipeline: Pipeline
    ) -> tuple[bool, bool, bool, Signal, str]:

        cfg = CONFIG.gate

        # Hard bot block
        if bot_pct > cfg.bot_pct_hard:
            return False, False, False, Signal.SKIP, f"Hard bot block: {bot_pct:.0%} bot traffic"

        # Gate 1 — Sentiment
        thresh_s = (cfg.sentiment_min_new
                    if pipeline == Pipeline.NEW_TOKEN
                    else cfg.sentiment_min_event)
        gate1 = sentiment_eff >= thresh_s

        # Gate 2 — Velocity
        thresh_v = (cfg.velocity_min_new
                    if pipeline == Pipeline.NEW_TOKEN
                    else cfg.velocity_min_event)
        gate2 = velocity >= thresh_v

        # Gate 3 — On-chain
        gate3 = (volume_sigma >= cfg.volume_sigma_min and
                 netflow_usd  >= cfg.netflow_min_usd)

        # Signal decision
        if gate1 and gate2 and gate3:
            return gate1, gate2, gate3, Signal.BUY, "All gates passed"

        if not gate1:
            return gate1, gate2, gate3, Signal.SKIP, \
                f"Gate1 fail: sentiment {sentiment_eff:.2f} < {thresh_s}"

        if not gate2:
            return gate1, gate2, gate3, Signal.SKIP, \
                f"Gate2 fail: velocity {velocity:.1%} < {thresh_v:.0%}"

        if not gate3:
            return gate1, gate2, gate3, Signal.HOLD, \
                f"Gate3 fail: σ={volume_sigma:.1f}, netflow=${netflow_usd:,.0f}"

        return gate1, gate2, gate3, Signal.IGNORE, "No signal"
