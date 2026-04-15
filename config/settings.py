# APEX Hunter System — Phase 1 Simulation Config
# Exchange: Bybit Testnet | Data: Mock sentiment + Mock on-chain

from dataclasses import dataclass, field
from enum import Enum


# ─────────────────────────────────────────────
#  ENUMS
# ─────────────────────────────────────────────

class Pipeline(Enum):
    NEW_TOKEN    = "new_token"
    EVENT_DRIVEN = "event_driven"

class WatchStatus(Enum):
    SCANNING  = "scanning"
    TRIGGERED = "triggered"
    ACTIVE    = "active"
    COOLING   = "cooling"
    REMOVED   = "removed"

class Signal(Enum):
    BUY    = "BUY"
    SKIP   = "SKIP"
    HOLD   = "HOLD"
    IGNORE = "IGNORE"


# ─────────────────────────────────────────────
#  GATE THRESHOLDS
# ─────────────────────────────────────────────

@dataclass
class GateConfig:
    # Sentiment
    sentiment_min_new:   float = 0.75
    sentiment_min_event: float = 0.70

    # Velocity (as multiplier, 3.0 = 300%)
    velocity_min_new:    float = 3.0
    velocity_min_event:  float = 1.5

    # Bot filter
    bot_pct_max:         float = 0.40   # above this → sentiment halved
    bot_pct_hard:        float = 0.70   # above this → always SKIP

    # On-chain
    volume_sigma_min:    float = 5.0
    netflow_min_usd:     float = 50_000


# ─────────────────────────────────────────────
#  POSITION SIZING & RISK
# ─────────────────────────────────────────────

@dataclass
class RiskConfig:
    portfolio_value:     float = 10_000.0   # USD, update before live

    pos_size_new:        float = 0.008      # 0.8% per new token trade
    pos_size_event:      float = 0.012      # 1.2% per event-driven trade
    pos_size_max:        float = 0.015      # hard cap per position

    total_exposure_cap:  float = 0.05       # 5% total open exposure

    hard_stop_pct:       float = 0.08       # -8% hard stop
    trail_stop_new:      float = 0.08       # 8% trailing
    trail_stop_event:    float = 0.10       # 10% trailing

    tp1_multiplier:      float = 2.0        # take 50% off at 2x
    tp1_sell_pct:        float = 0.50

    # Circuit breaker
    circuit_breaker_stops:  int   = 3       # stops within window
    circuit_breaker_window: int   = 3600    # seconds (1 hour)
    circuit_breaker_pause:  int   = 14400   # seconds (4 hours)

# ─────────────────────────────────────────────
#  HELIUS
# ─────────────────────────────────────────────

@dataclass
class HeliusConfig:
    api_key: str = "d9be6ae5-bafe-4c2c-ba10-211c6e2dabfa"


# ─────────────────────────────────────────────
#  WATCHLIST
# ─────────────────────────────────────────────

@dataclass
class WatchlistConfig:
    max_tokens:            int = 50
    stale_hours_scanning:  int = 48
    cooling_hours:         int = 24

    # Scan intervals (seconds)
    interval_cold:         int = 300    # 5 min — cold scanning
    interval_warm:         int = 60     # 1 min — velocity detected
    interval_triggered:    int = 30     # 30s  — near entry
    interval_active:       int = 60     # 1 min — position open


# ─────────────────────────────────────────────
#  EXCHANGE (Bybit Testnet)
# ─────────────────────────────────────────────

@dataclass
class ExchangeConfig:
    bybit_testnet:    bool  = True
    bybit_api_key:    str   = "ouBb9a9Bzqn5CzzWGc"
    bybit_api_secret: str   = "dsiWib28P1GYC1zcebmS516u2ULdcJLiVbb2"
    bybit_base_url:   str   = "https://api-demo.bybit.com"

    max_slippage_pct: float = 0.02      # 2% max slippage tolerance
    order_timeout_s:  int   = 10        # seconds before cancel retry


# ─────────────────────────────────────────────
#  REPORTING (Telegram)
# ─────────────────────────────────────────────

@dataclass
class TelegramConfig:
    bot_token:       str = "8426024795:AAEPR36otLKRNmSAwkwwuWHR_cyDsvOU83k"
    chat_id:         str = "8394621040"
    digest_hour_hkt: int = 8            # 08:00 HKT = 00:00 UTC
    digest_hour_utc: int = 0


# ─────────────────────────────────────────────
#  MASTER CONFIG
# ─────────────────────────────────────────────

@dataclass
class APEXConfig:
    gate:      GateConfig      = field(default_factory=GateConfig)
    risk:      RiskConfig      = field(default_factory=RiskConfig)
    watchlist: WatchlistConfig = field(default_factory=WatchlistConfig)
    exchange:  ExchangeConfig  = field(default_factory=ExchangeConfig)
    telegram:  TelegramConfig  = field(default_factory=TelegramConfig)

    # Phase control
    phase:          str  = "simulation"   # simulation | pilot | live
    dry_run:        bool = True           # True = never send real orders
    log_level:      str  = "INFO"


CONFIG = APEXConfig()
