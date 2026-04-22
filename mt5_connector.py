"""
============================================================
 MT5 Connector — XAUUSD SMC Bot
============================================================
- Initializes the MT5 terminal connection
- Auto-detects broker suffix (XAUUSD, XAUUSDm, XAUUSD.c, etc.)
- Provides timeframe mapping + rate fetchers
============================================================
"""

import logging
import sys
from typing import Optional

try:
    import MetaTrader5 as mt5
except ImportError:
    logging.critical("MetaTrader5 package missing. Install: pip install MetaTrader5")
    sys.exit(1)

import config

logger = logging.getLogger("MT5_Connector")

# ─── Timeframe Mapping ──────────────────────────────────────
TF_M5 = mt5.TIMEFRAME_M5
TF_M15 = mt5.TIMEFRAME_M15
TF_H1 = mt5.TIMEFRAME_H1

# Common broker suffixes to probe when the exact symbol isn't found
_SYMBOL_SUFFIXES = [
    "", "m", "c", "z", "i", "pro", "ecn", "raw", "x",
    ".a", ".r", ".ecn", ".pro", ".x", "_x", "_raw", "_i", "-i",
    "b", "k", "s", "#",
]


def _resolve_symbol(base: str) -> Optional[str]:
    """Try every common broker suffix and return the first one that exists."""
    for suf in _SYMBOL_SUFFIXES:
        candidate = f"{base}{suf}"
        info = mt5.symbol_info(candidate)
        if info is not None:
            return candidate
    return None


def _register_symbol(symbol: str) -> bool:
    """Ensure the trading symbol is visible in Market Watch."""
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol '{symbol}' not found on broker server.")
        return False

    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            logger.error(f"Cannot add '{symbol}' to Market Watch. Error: {mt5.last_error()}")
            return False
        logger.info(f"Symbol '{symbol}' added to Market Watch.")
    return True


def initialize_mt5(exit_on_fail: bool = True) -> bool:
    """Start MT5 terminal, authenticate, and register the XAUUSD symbol."""
    logger.info("Initializing MetaTrader5 connection...")

    if not config.MT5_LOGIN or not config.MT5_PASS or not config.MT5_SERVER:
        logger.critical("MT5 credentials missing. Fill in MT5_LOGIN / MT5_PASS / MT5_SERVER in .env")
        if exit_on_fail:
            sys.exit(1)
        return False

    authorized = mt5.initialize(
        login=config.MT5_LOGIN,
        password=config.MT5_PASS,
        server=config.MT5_SERVER,
    )
    if not authorized:
        logger.critical(f"MT5 init failed. Error: {mt5.last_error()}")
        mt5.shutdown()
        if exit_on_fail:
            sys.exit(1)
        return False

    logger.info("MT5 initialized ✓")

    # ── Account info ──
    account = mt5.account_info()
    if account is None:
        logger.critical(f"Cannot retrieve account info. Error: {mt5.last_error()}")
        mt5.shutdown()
        if exit_on_fail:
            sys.exit(1)
        return False

    logger.info(
        f"Account {account.login} @ {account.server} | "
        f"Balance: {account.balance:.2f} {account.currency} | "
        f"Equity: {account.equity:.2f} | Leverage: 1:{account.leverage}"
    )

    # ── Algo trading check ──
    terminal = mt5.terminal_info()
    if terminal is None or not terminal.trade_allowed:
        logger.critical("Algo trading disabled in MT5 Terminal. Tools → Options → Expert Advisors.")
        mt5.shutdown()
        if exit_on_fail:
            sys.exit(1)
        return False

    # ── Resolve XAUUSD symbol with broker suffix ──
    resolved = _resolve_symbol(config.SYMBOL_BASE)
    if resolved is None:
        logger.critical(
            f"Could not find '{config.SYMBOL_BASE}' on broker with any common suffix. "
            f"Check Market Watch or set SYMBOL_BASE explicitly."
        )
        mt5.shutdown()
        if exit_on_fail:
            sys.exit(1)
        return False

    if not _register_symbol(resolved):
        mt5.shutdown()
        if exit_on_fail:
            sys.exit(1)
        return False

    config.SYMBOL = resolved
    info = mt5.symbol_info(resolved)
    logger.info(
        f"✓ Symbol resolved: {resolved} | Digits: {info.digits} | "
        f"Point: {info.point} | Contract: {info.trade_contract_size} | "
        f"Stops level: {info.trade_stops_level} pts"
    )

    return True


def shutdown_mt5():
    """Gracefully close the MT5 connection."""
    logger.info("Shutting down MT5 connection...")
    mt5.shutdown()
    logger.info("MT5 shutdown complete ✓")


# ─── Rate Fetchers ──────────────────────────────────────────
def get_rates(symbol: str, timeframe: int, count: int):
    """Fetch N most recent candles. Returns numpy structured array or None."""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None or len(rates) < count:
        return None
    return rates


def get_tick(symbol: str):
    """Current bid/ask tick."""
    return mt5.symbol_info_tick(symbol)


def get_spread_usd(symbol: str) -> float:
    """Live spread in USD (dollars for gold)."""
    tick = get_tick(symbol)
    if tick is None:
        return 999.0
    return tick.ask - tick.bid
