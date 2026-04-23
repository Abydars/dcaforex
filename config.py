"""
============================================================
 XAUUSD SMC Scalping Bot — Configuration
============================================================
Strategy:
  H1 Bias → M15 Zone Validation → M5 Liquidity Sweep + FVG Entry
Rules (non-negotiable):
  - Fixed risk per trade (no DCA, no martingale, no averaging)
  - Single open position at a time
  - Hard SL on every trade
  - Daily loss limits enforce capital protection
============================================================
"""

import logging
import os
import json
import sys

from dotenv import load_dotenv

# ─── Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-16s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("Config")

load_dotenv()


def _get_env(key: str, default=None, required: bool = True) -> str:
    value = os.getenv(key, default)
    if required and (value is None or not str(value).strip()):
        logger.critical(f"Missing required environment variable: {key}")
        logger.critical("Copy .env.example → .env and fill in your values.")
        sys.exit(1)
    return str(value).strip()


# ─── MT5 Credentials ────────────────────────────────────────
MT5_LOGIN: int = int(_get_env("MT5_LOGIN", default="0", required=False))
MT5_PASS: str = _get_env("MT5_PASS", default="", required=False)
MT5_SERVER: str = _get_env("MT5_SERVER", default="", required=False)

DASHBOARD_PASSWORD: str = _get_env("DASHBOARD_PASSWORD", default="admin123", required=False)

# ─── Trading Symbol ─────────────────────────────────────────
# Note: Exness typically uses "XAUUSDm" (mini), "XAUUSD", or "XAUUSD.c"
# The bot will auto-detect the correct suffix at startup.
SYMBOL_BASE: str = _get_env("SYMBOL_BASE", default="XAUUSD")
SYMBOL: str = ""  # Resolved at runtime by mt5_connector

# ─── Risk Management ────────────────────────────────────────
RISK_PCT_PER_TRADE: float = float(_get_env("RISK_PCT_PER_TRADE", default="0.5"))  # 0.5% of equity
MAX_DAILY_LOSS_PCT: float = float(_get_env("MAX_DAILY_LOSS_PCT", default="2.0"))  # -2% daily → stop
MAX_TRADES_PER_DAY: int = int(_get_env("MAX_TRADES_PER_DAY", default="3"))
MAX_CONSECUTIVE_LOSSES: int = int(_get_env("MAX_CONSECUTIVE_LOSSES", default="2"))

# ─── Strategy Parameters ────────────────────────────────────
MIN_RR: float = float(_get_env("MIN_RR", default="2.0"))  # Minimum risk:reward ratio
MAX_RR: float = float(_get_env("MAX_RR", default="5.0"))  # Cap unrealistic targets

# Swing detection (fractal-based)
FRACTAL_LEFT: int = 3
FRACTAL_RIGHT: int = 3

# Lookback bars per timeframe
H1_LOOKBACK: int = 100   # ~4 days of H1 for bias
M15_LOOKBACK: int = 80   # ~20 hours of M15 for structure
M5_LOOKBACK: int = 60    # ~5 hours of M5 for entries

# ─── FVG (M5) ───────────────────────────────────────────────
# An FVG qualifies if its size ≥ max(FVG_MIN_ATR_FRAC * M5_ATR, FVG_FLOOR_USD)
FVG_MIN_ATR_FRAC: float = 0.10        # FVG must be ≥ 10% of M5 ATR
FVG_FLOOR_USD: float = 0.10           # But never smaller than $0.10
FVG_MAX_AGE_BARS: int = 20            # Keep as-is

# ─── Liquidity Sweep (M5) ───────────────────────────────────
# Wick must extend past swing by ≥ max(SWEEP_WICK_ATR_FRAC * M5_ATR, SWEEP_WICK_FLOOR_USD)
SWEEP_WICK_ATR_FRAC: float = 0.08     # 8% of M5 ATR
SWEEP_WICK_FLOOR_USD: float = 0.10    # Absolute floor
SWEEP_MAX_SWINGS_BACK: int = 3        # Keep as-is
SWEEP_LOOKBACK_BARS: int = 30         # Keep as-is

# ─── Stop Loss Buffer (M5) ──────────────────────────────────
# SL placed at sweep extreme ± max(SL_BUFFER_ATR_FRAC * M5_ATR, SL_BUFFER_FLOOR_USD)
SL_BUFFER_ATR_FRAC: float = 0.15      # 15% of M5 ATR
SL_BUFFER_FLOOR_USD: float = 0.15     # Absolute floor

# ─── Filters ────────────────────────────────────────────────
# ─── Volatility Floor (M15) ─────────────────────────────────
# Skip trading if M15 ATR is less than MIN_ATR_M15_PCT % of current price.
# e.g., 0.06% of $2800 = $1.68; at $1800 = $1.08. Auto-scales across price levels.
MIN_ATR_M15_PCT: float = float(_get_env("MIN_ATR_M15_PCT", default="0.06"))
MIN_ATR_M15_FLOOR_USD: float = 0.80   # Sanity floor

# ─── Spread Filter ──────────────────────────────────────────
# Skip if spread > MAX_SPREAD_PCT % of current price.
# 0.02% of $2800 = $0.56; naturally widens threshold when gold rallies.
MAX_SPREAD_PCT: float = float(_get_env("MAX_SPREAD_PCT", default="0.02"))
MAX_SPREAD_FLOOR_USD: float = 0.30    # Always reject if spread > this

# Sessions (UTC).
# Expected format in .env: SESSIONS_UTC='[["LONDON", 7, 0, 11, 0], ["NY", 12, 30, 16, 0]]'
_raw_sessions = os.getenv("SESSIONS_UTC")
if _raw_sessions:
    try:
        # JSON parse expects lists
        parsed = json.loads(_raw_sessions)
        # Convert lists to tuples
        SESSIONS_UTC = [tuple(s) for s in parsed]
    except Exception as e:
        print(f"Error parsing SESSIONS_UTC from env: {e}")
        SESSIONS_UTC = [
            ("LONDON", 7, 0, 11, 0),
            ("NY",     12, 30, 16, 0),
        ]
else:
    SESSIONS_UTC = [
        ("LONDON", 7, 0, 11, 0),
        ("NY",     12, 30, 16, 0),
    ]

# News filter
NEWS_ENABLED: bool = _get_env("NEWS_ENABLED", default="true").lower() == "true"
NEWS_BUFFER_BEFORE_MIN: int = 15
NEWS_BUFFER_AFTER_MIN: int = 30
NEWS_URL: str = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
NEWS_IMPACT_FILTER = ["High"]  # Only skip around High-impact news
NEWS_CURRENCIES = ["USD"]       # Gold is USD-denominated; XAU news is rare

# ─── Order Execution ────────────────────────────────────────
MAGIC_NUMBER: int = int(_get_env("MAGIC_NUMBER", default="770077"))
MAX_SLIPPAGE_POINTS: int = int(_get_env("MAX_SLIPPAGE_POINTS", default="30"))

# ─── Loop Timing ────────────────────────────────────────────
LOOP_INTERVAL_SEC: float = 1.0  # Main loop tick
M5_CANDLE_GRACE_SEC: int = 3    # Wait N seconds after M5 close before evaluating

# ─── Trade Log ──────────────────────────────────────────────
DB_PATH: str = _get_env("DB_PATH", default="trades.db", required=False)
