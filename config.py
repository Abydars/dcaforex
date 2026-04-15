"""
============================================================
 DCA Forex Bot — Configuration Manager
============================================================
Loads all configurable parameters from .env and exposes them
as typed module-level constants with validation.
"""

import logging
import os
import sys
from datetime import time as dtime

from dotenv import load_dotenv

# Configure logging first
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("Config")

# Load .env
load_dotenv()


def _get_env(key: str, default=None, required: bool = True) -> str:
    """Retrieve an env var, exit if required and missing."""
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

# ─── Symbol & Timeframe ─────────────────────────────────────
SYMBOL: str = _get_env("SYMBOL", default="EURUSDm")

# Map string timeframe name → MT5 constant (resolved at runtime in connector)
TIMEFRAME_STR: str = _get_env("TIMEFRAME", default="M5").upper()

# ─── Signal Mode ────────────────────────────────────────────
# "candle"  = Simple green/red candle direction (fast entry, good for DCA)
# "pattern" = Strict candlestick patterns like Engulfing/Hammer (selective)
SIGNAL_MODE: str = _get_env("SIGNAL_MODE", default="candle").lower()

# ─── Lot Sizing ─────────────────────────────────────────────
# Initial lot size for the first entry order
INITIAL_LOT: float = float(_get_env("INITIAL_LOT", default="0.01"))

# ─── DCA Parameters ─────────────────────────────────────────
# Distance in pips before opening the next DCA order
STEP_DISTANCE_PIPS: float = float(_get_env("STEP_DISTANCE_PIPS", default="2"))

# Multiplier applied to each successive DCA order's lot size
LOT_MULTIPLIER: float = float(_get_env("LOT_MULTIPLIER", default="1.0"))

# Maximum total orders (initial + DCA layers) to cap margin exposure
MAX_ORDERS: int = int(_get_env("MAX_ORDERS", default="15"))

# ─── Basket Close ───────────────────────────────────────────
# Close all positions when combined floating profit ≥ this value (account currency)
TARGET_PROFIT_USD: float = float(_get_env("TARGET_PROFIT_USD", default="2.0"))

# Max loss per basket when all DCA layers exhausted (0 = disabled)
BASKET_STOP_LOSS_USD: float = float(_get_env("BASKET_STOP_LOSS_USD", default="10.0"))

# ─── Safety ─────────────────────────────────────────────────
# Emergency close if drawdown from session equity exceeds this %
MAX_DRAWDOWN_PCT: float = float(_get_env("MAX_DRAWDOWN_PCT", default="10.0"))

# ─── Trading Hours (Server/UTC) ─────────────────────────────
def _parse_time(raw: str) -> dtime:
    parts = raw.split(":")
    return dtime(hour=int(parts[0]), minute=int(parts[1]))


TRADING_START: dtime = _parse_time(_get_env("TRADING_START_HOUR", default="08:00"))
TRADING_END: dtime = _parse_time(_get_env("TRADING_END_HOUR", default="20:00"))

# ─── Magic Number ───────────────────────────────────────────
MAGIC_NUMBER: int = int(_get_env("MAGIC_NUMBER", default="550055"))

# ─── Runtime State ──────────────────────────────────────────
SESSION_START_EQUITY: float = 0.0  # Set once at bot startup


def print_config():
    """Dump active configuration to the log for audit trail."""
    logger.info("═" * 60)
    logger.info("  DCA FOREX BOT — ACTIVE CONFIGURATION")
    logger.info("═" * 60)
    logger.info(f"  Account Login  : {MT5_LOGIN}")
    logger.info(f"  Server         : {MT5_SERVER}")
    logger.info(f"  Symbol         : {SYMBOL}")
    logger.info(f"  Timeframe      : {TIMEFRAME_STR}")
    logger.info(f"  Signal Mode    : {SIGNAL_MODE}")
    logger.info(f"  Initial Lot    : {INITIAL_LOT}")
    logger.info(f"  Step Distance  : {STEP_DISTANCE_PIPS} pips")
    logger.info(f"  Lot Multiplier : {LOT_MULTIPLIER}x")
    logger.info(f"  Max Orders     : {MAX_ORDERS}")
    logger.info(f"  Target Profit  : ${TARGET_PROFIT_USD}")
    logger.info(f"  Basket SL      : ${BASKET_STOP_LOSS_USD} (0=disabled)")
    logger.info(f"  Max Drawdown   : {MAX_DRAWDOWN_PCT}%")
    logger.info(f"  Trading Window : {TRADING_START} → {TRADING_END}")
    logger.info(f"  Magic Number   : {MAGIC_NUMBER}")
    logger.info("═" * 60)
