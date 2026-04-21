"""
============================================================
 DCA Forex Bot — MT5 Connector
============================================================
Handles initialization, authentication, and graceful shutdown
of the MetaTrader5 terminal connection for Exness accounts.
"""

import logging
import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    logging.critical("MetaTrader5 package missing. Install: pip install MetaTrader5")
    sys.exit(1)

import config

logger = logging.getLogger("MT5_Connector")

# ─── Timeframe Mapping ──────────────────────────────────────
_TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

_HTF_MAP = {
    mt5.TIMEFRAME_M1: mt5.TIMEFRAME_M5,
    mt5.TIMEFRAME_M5: mt5.TIMEFRAME_M15,
    mt5.TIMEFRAME_M15: mt5.TIMEFRAME_H1,
    mt5.TIMEFRAME_M30: mt5.TIMEFRAME_H4,
    mt5.TIMEFRAME_H1: mt5.TIMEFRAME_H4,
    mt5.TIMEFRAME_H4: mt5.TIMEFRAME_D1,
    mt5.TIMEFRAME_D1: mt5.TIMEFRAME_W1,
}


def get_mt5_timeframe() -> int:
    """Resolve the string timeframe from config to an MT5 constant."""
    tf = _TF_MAP.get(config.TIMEFRAME_STR)
    if tf is None:
        logger.critical(
            f"Unsupported TIMEFRAME '{config.TIMEFRAME_STR}'. "
            f"Use one of: {list(_TF_MAP.keys())}"
        )
        sys.exit(1)
    return tf


def get_mt5_htf(current_tf: int) -> int:
    """Get the higher timeframe mapping for trend context."""
    return _HTF_MAP.get(current_tf, mt5.TIMEFRAME_H1)


def initialize_mt5(exit_on_fail: bool = True):
    """
    Starts the MT5 terminal and authenticates headlessly
    against the configured Exness account.
    """
    logger.info("Initializing MetaTrader5 connection...")

    if not config.MT5_LOGIN or not config.MT5_PASS or not config.MT5_SERVER:
        logger.warning("MT5 Credentials missing. Please configure via Dashboard.")
        if exit_on_fail:
            sys.exit(1)
        return False

    authorized = mt5.initialize(
        login=config.MT5_LOGIN,
        password=config.MT5_PASS,
        server=config.MT5_SERVER,
    )

    if not authorized:
        logger.critical(
            f"MT5 initialization / authorization failed. Error: {mt5.last_error()}"
        )
        mt5.shutdown()
        if exit_on_fail:
            sys.exit(1)
        return False

    logger.info("MT5 initialized ✓")

    # ── Account Verification ──
    account = mt5.account_info()
    if account is None:
        logger.critical(f"Cannot retrieve account info. Error: {mt5.last_error()}")
        mt5.shutdown()
        if exit_on_fail:
            sys.exit(1)
        return False

    logger.info(
        f"Account {account.login} @ {account.company} | "
        f"Balance: {account.balance:.2f} {account.currency} | "
        f"Equity: {account.equity:.2f}"
    )

    # ── Algo Trading Check ──
    terminal = mt5.terminal_info()
    if terminal is None or not terminal.trade_allowed:
        logger.critical(
            "Algo trading is disabled in MT5 Terminal. "
            "Enable it via Tools → Options → Expert Advisors."
        )
        mt5.shutdown()
        if exit_on_fail:
            sys.exit(1)
        return False

    # ─── Symbol Registration ──
    valid_symbols = []
    # Use MASTER_SYMBOLS so we always scan from the full list on re-initialization
    for sym in getattr(config, "MASTER_SYMBOLS", config.SYMBOLS):
        if _register_symbol(sym):
            valid_symbols.append(sym)
            
    config.SYMBOLS = valid_symbols

    logger.info(f"MT5 connection fully established ✓ ({len(valid_symbols)} Valid Symbols Registered)")
    return True


def _register_symbol(symbol: str) -> bool:
    """Ensure the trading symbol is visible in Market Watch."""
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.warning(
            f"Symbol '{symbol}' not found on broker server. "
            f"Skipping this symbol from the active pool."
        )
        return False

    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            logger.error(
                f"Cannot add '{symbol}' to Market Watch. Error: {mt5.last_error()}"
            )
            return False
        logger.info(f"Symbol '{symbol}' added to Market Watch.")
    else:
        # logger.debug(f"Symbol '{symbol}' already visible.")
        pass
        
    return True


def shutdown_mt5():
    """Gracefully close the MT5 connection."""
    logger.info("Shutting down MT5 connection...")
    mt5.shutdown()
    logger.info("MT5 shutdown complete ✓")
