"""
============================================================
 DCA Forex Bot — Signal Engine
============================================================
Entry signal detection with two modes:

  MODE = "pattern"  → Strict candlestick patterns (Engulfing, Hammer)
  MODE = "candle"   → Simple candle color (green = BUY, red = SELL)

For fast DCA strategies, "candle" mode is recommended since
the goal is to get in quickly and let DCA handle the rest.
============================================================
"""

import logging

import MetaTrader5 as mt5

import config
from mt5_connector import get_mt5_timeframe

logger = logging.getLogger("SignalEngine")

# ─── Signal Mode ────────────────────────────────────────────
# "candle"  = Simple: green candle = BUY, red = SELL (fast entry)
# "pattern" = Strict: only engulfing / hammer patterns (selective)
SIGNAL_MODE = getattr(config, "SIGNAL_MODE", "candle")


def get_entry_signal() -> str | None:
    """
    Fetch the last few candles and detect an entry signal
    on the most recently CLOSED candle (index -2).

    Returns: "BUY", "SELL", or None
    """
    symbol = config.SYMBOL
    tf = get_mt5_timeframe()

    # Fetch 10 candles
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 10)

    if rates is None or len(rates) < 3:
        logger.warning(f"Insufficient candle data for {symbol}. Received: {rates}")
        return None

    # The signal bar = last CLOSED candle (index -2)
    # Index -1 is the current live/forming bar
    prev = rates[-3]
    curr = rates[-2]

    curr_open = curr["open"]
    curr_close = curr["close"]
    curr_high = curr["high"]
    curr_low = curr["low"]

    body = abs(curr_close - curr_open)
    full_range = curr_high - curr_low

    # Log candle details for debugging
    candle_type = "GREEN 🟢" if curr_close > curr_open else "RED 🔴" if curr_close < curr_open else "DOJI ⚪"
    logger.info(
        f"📊 Closed Candle: {candle_type} | "
        f"O:{curr_open} H:{curr_high} L:{curr_low} C:{curr_close} | "
        f"Body:{body:.5f} Range:{full_range:.5f}"
    )

    if full_range == 0:
        logger.debug("Zero-range candle (doji), skipping.")
        return None

    # ─── MODE: Simple Candle Color ───────────────────────
    if SIGNAL_MODE == "candle":
        if curr_close > curr_open:
            logger.info(f"🟢 GREEN candle → BUY signal on {symbol}")
            return "BUY"
        elif curr_close < curr_open:
            logger.info(f"🔴 RED candle → SELL signal on {symbol}")
            return "SELL"
        else:
            logger.debug("Doji candle, no signal.")
            return None

    # ─── MODE: Candlestick Patterns ──────────────────────
    prev_open, prev_close = prev["open"], prev["close"]
    body_ratio = body / full_range

    # Bullish Engulfing
    if (
        prev_close < prev_open
        and curr_close > curr_open
        and curr_close > prev_open
        and curr_open <= prev_close
    ):
        logger.info(f"🟢 BULLISH ENGULFING detected on {symbol}")
        return "BUY"

    # Bearish Engulfing
    if (
        prev_close > prev_open
        and curr_close < curr_open
        and curr_close < prev_open
        and curr_open >= prev_close
    ):
        logger.info(f"🔴 BEARISH ENGULFING detected on {symbol}")
        return "SELL"

    # Hammer (Bullish)
    upper_wick = curr_high - max(curr_open, curr_close)
    lower_wick = min(curr_open, curr_close) - curr_low

    if (
        body_ratio < 0.35
        and lower_wick >= 2.0 * body
        and upper_wick <= body * 0.5
    ):
        logger.info(f"🟢 HAMMER detected on {symbol}")
        return "BUY"

    # Shooting Star (Bearish)
    if (
        body_ratio < 0.35
        and upper_wick >= 2.0 * body
        and lower_wick <= body * 0.5
    ):
        logger.info(f"🔴 SHOOTING STAR detected on {symbol}")
        return "SELL"

    logger.info(f"No pattern matched on {symbol}. Waiting for next candle...")
    return None
