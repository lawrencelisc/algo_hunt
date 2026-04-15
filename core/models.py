# APEX — Core Data Models

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from config.settings import Pipeline, WatchStatus, Signal


# ─────────────────────────────────────────────
#  SIGNAL SNAPSHOT  (output of SentimentEngine)
# ─────────────────────────────────────────────

@dataclass
class SignalSnapshot:
    symbol:         str
    timestamp:      datetime

    # Sentiment layer
    sentiment_raw:      float   # before bot adjustment
    sentiment_eff:      float   # after bot adjustment
    bot_pct:            float   # 0.0 - 1.0
    velocity:           float   # growth rate, e.g. 5.29 = 529%

    # On-chain layer
    volume_sigma:       float   # standard deviations above mean
    netflow_usd:        float   # net inflow in USD

    # Gate results
    gate1_pass:         bool    # sentiment
    gate2_pass:         bool    # velocity
    gate3_pass:         bool    # on-chain
    signal:             Signal

    # Diagnostics
    reason:             str = ""
    tweet_count:        int = 0
    kol_count:          int = 0


# ─────────────────────────────────────────────
#  WATCH TARGET  (entry in WatchlistManager)
# ─────────────────────────────────────────────

@dataclass
class WatchTarget:
    # Identity
    symbol:       str
    mint_address: str
    pipeline:     Pipeline
    added_at:     datetime
    added_reason: str

    # Latest signal values
    sentiment:    float = 0.0
    velocity:     float = 0.0
    bot_pct:      float = 0.0
    volume_sigma: float = 0.0
    netflow_usd:  float = 0.0

    # State
    status:       WatchStatus = WatchStatus.SCANNING
    last_updated: datetime    = field(default_factory=datetime.utcnow)
    signal_count: int         = 0

    # Position tracking (when ACTIVE)
    entry_price:    float = 0.0
    entry_qty:      float = 0.0
    entry_usd:      float = 0.0
    trailing_stop:  float = 0.0
    hard_stop:      float = 0.0
    tp1_triggered:  bool  = False
    bybit_order_id: str   = ""

    # Safety flags
    is_honeypot:  bool = False
    is_rug_risk:  bool = False
    is_blacklist: bool = False

    # Pipeline-specific threshold overrides
    velocity_override:  Optional[float] = None
    sentiment_override: Optional[float] = None

    @property
    def hours_idle(self) -> float:
        return (datetime.utcnow() - self.last_updated).total_seconds() / 3600

    @property
    def effective_velocity_threshold(self) -> float:
        if self.velocity_override:
            return self.velocity_override
        from config.settings import CONFIG
        return (CONFIG.gate.velocity_min_new
                if self.pipeline == Pipeline.NEW_TOKEN
                else CONFIG.gate.velocity_min_event)

    @property
    def effective_sentiment_threshold(self) -> float:
        if self.sentiment_override:
            return self.sentiment_override
        from config.settings import CONFIG
        return (CONFIG.gate.sentiment_min_new
                if self.pipeline == Pipeline.NEW_TOKEN
                else CONFIG.gate.sentiment_min_event)


# ─────────────────────────────────────────────
#  TRADE RECORD  (closed or open position log)
# ─────────────────────────────────────────────

@dataclass
class TradeRecord:
    trade_id:     str
    symbol:       str
    pipeline:     Pipeline

    entry_time:   datetime
    entry_price:  float
    entry_qty:    float
    entry_usd:    float

    exit_time:    Optional[datetime] = None
    exit_price:   Optional[float]    = None
    exit_usd:     Optional[float]    = None
    exit_reason:  str                = ""   # trailing_stop | hard_stop | tp1 | tp2 | rug | manual

    pnl_usd:      float = 0.0
    pnl_pct:      float = 0.0
    is_open:      bool  = True

    # Signal context at entry
    sentiment_at_entry: float = 0.0
    velocity_at_entry:  float = 0.0
    bot_pct_at_entry:   float = 0.0


# ─────────────────────────────────────────────
#  PORTFOLIO SNAPSHOT  (for TG digest)
# ─────────────────────────────────────────────

@dataclass
class PortfolioSnapshot:
    timestamp:        datetime
    portfolio_value:  float
    open_exposure:    float        # USD
    open_exposure_pct: float       # %
    open_positions:   list[WatchTarget]
    closed_today:     list[TradeRecord]
    signals_today:    int
    buys_today:       int
    skips_today:      int
    watchlist_count:  int
    triggered_count:  int
    daily_pnl:        float
    daily_pnl_pct:    float
