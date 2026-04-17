"""
============================================================
 DCA Forex Bot — Configuration
============================================================
Simplified config: user provides CAPITAL + SYMBOL.
Everything else is auto-calculated at runtime.
"""

import logging
import os
import sys
from datetime import time as dtime

from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
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
MT5_LOGIN: int = int(_get_env("MT5_LOGIN"))
MT5_PASS: str = _get_env("MT5_PASS")
MT5_SERVER: str = _get_env("MT5_SERVER")

# ─── User Inputs (only these matter) ────────────────────────
SYMBOLS_RAW: str = _get_env("SYMBOLS", default="EURUSDm")
SYMBOLS: list = [s.strip() for s in SYMBOLS_RAW.split(",") if s.strip()]
SYMBOL: str = SYMBOLS[0] if len(SYMBOLS) > 0 else "EURUSDm"  # Dynamic active tracker (legacy support context)
TIMEFRAME_STR: str = _get_env("TIMEFRAME", default="M5").upper()
SIGNAL_MODE: str = _get_env("SIGNAL_MODE", default="candle").lower()
SYNC_DELAY_SECONDS: float = float(_get_env("SYNC_DELAY_SECONDS", default="3.0"))



def _parse_time(raw: str) -> dtime:
    parts = raw.split(":")
    return dtime(hour=int(parts[0]), minute=int(parts[1]))

TRADING_START: dtime = _parse_time(_get_env("TRADING_START_HOUR", default="08:00"))
TRADING_END: dtime = _parse_time(_get_env("TRADING_END_HOUR", default="20:00"))
MAGIC_NUMBER: int = int(_get_env("MAGIC_NUMBER", default="550055"))

# ─── Auto-Calculated (filled at runtime by auto_params) ─────
CAPITAL: float = 0.0

LOT_SIZE: float = 0.0
STEP_PIPS: float = 0.0
MAX_ORDERS: int = 0
EXIT_PIPS: float = 0.0
PIP_SIZE: float = 0.0
PIP_VALUE: float = 0.0
SPREAD_PIPS: float = 0.0
