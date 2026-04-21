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
MT5_LOGIN: int = int(_get_env("MT5_LOGIN", default="0", required=False))
MT5_PASS: str = _get_env("MT5_PASS", default="", required=False)
MT5_SERVER: str = _get_env("MT5_SERVER", default="", required=False)

# ─── User Inputs (only these matter) ────────────────────────
TIMEFRAME_STR: str = _get_env("TIMEFRAME", default="M5").upper()
SIGNAL_MODE: str = _get_env("SIGNAL_MODE", default="candle").lower()
SYNC_DELAY_SECONDS: float = float(_get_env("SYNC_DELAY_SECONDS", default="3.0"))


MAGIC_NUMBER: int = int(_get_env("MAGIC_NUMBER", default="550055"))
# ─── Dashboard Protection ───────────────────────────────────
DASHBOARD_PASSWORD: str = _get_env("DASHBOARD_PASSWORD", default="*&*&*&", required=False)

# ─── Anti-Correlation Module ────────────────────────────────
CORRELATION_GROUPS = {
    # Major Pairs (USD Quote vs USD Base)
    "USD_DIRECT": ["EURUSD", "GBPUSD", "AUDUSD", "NZDUSD", "EURUSDm", "GBPUSDm", "AUDUSDm", "NZDUSDm"],
    "USD_INDIRECT": ["USDJPY", "USDCAD", "USDCHF", "USDJPYm", "USDCADm", "USDCHFm"],
    
    # Currency Crosses
    "EUR_CROSS": ["EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURNZD", "EURCAD", "EURGBPm", "EURJPYm", "EURCHFm", "EURAUDm", "EURNZDm", "EURCADm"],
    "GBP_CROSS": ["GBPJPY", "GBPCHF", "GBPAUD", "GBPNZD", "GBPCAD", "GBPJPYm", "GBPCHFm", "GBPAUDm", "GBPNZDm", "GBPCADm"],
    "JPY_CROSS": ["AUDJPY", "NZDJPY", "CADJPY", "CHFJPY", "AUDJPYm", "NZDJPYm", "CADJPYm", "CHFJPYm"],
    
    # Commodities / Metals
    "METALS": ["XAUUSD", "XAGUSD", "XAUUSDm", "XAGUSDm", "GOLD", "SILVER"],
    "OIL": ["USOIL", "UKOIL", "WTI", "BRENT", "USOILm", "UKOILm"],
    
    # Indices
    "US_INDICES": ["US30", "USTEC", "US100", "SPX500", "NAS100", "DJI"],
    "EU_INDICES": ["GER30", "GER40", "UK100", "FRA40", "DAX30", "DAX40"],
    
    # Crypto
    "CRYPTO": ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD", "BTCUSDm", "ETHUSDm"]
}

# Auto-compute Master Available Symbols explicitly from our Correlation Groups dictionary
SYMBOLS: list = list({sym for group in CORRELATION_GROUPS.values() for sym in group})
SYMBOLS.sort()
SYMBOL: str = SYMBOLS[0] if len(SYMBOLS) > 0 else "EURUSDm"

# ─── Auto-Calculated (filled at runtime by auto_params) ─────
CAPITAL: float = 0.0

LOT_SIZE: float = 0.0
STEP_PIPS: float = 0.0
MAX_ORDERS: int = 0
EXIT_PIPS: float = 0.0
PIP_SIZE: float = 0.0
PIP_VALUE: float = 0.0
SPREAD_PIPS: float = 0.0
