"""
============================================================
 DCA Forex Bot — Candlestick Signal Engine
============================================================
Detects entry signals using classic candlestick patterns:
  • Bullish/Bearish Engulfing
  • Hammer / Inverted Hammer (Shooting Star)

Returns a direction string: "BUY", "SELL", or None.
"""

import logging

import MetaTrader5 as mt5
import numpy as np

import config
from mt5_connector import get_mt5_timeframe

logger = logging.getLogger("SignalEngine")


def get_entry_signal() -> str | None:
    """
    Fetch the last few candles on the configured timeframe and
    check for a candlestick entry pattern on the MOST RECENTLY
    CLOSED candle (index -2; index -1 is the live/forming bar).

    Returns:
        "BUY"  — Bullish engulfing or hammer detected
        "SELL" — Bearish engulfing or shooting star detected
        None   — No actionable pattern
    """
    symbol = config.SYMBOL
    tf = get_mt5_timeframe()

    # Fetch 10 candles: enough context for pattern checks
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 10)

    if rates is None or len(rates) < 3:
        logger.warning(f"Insufficient candle data for {symbol}. Received: {rates}")
        return None

    # Work with the two most recently CLOSED candles
    prev = rates[-3]  # candle before the signal bar
    curr = rates[-2]  # the signal bar (fully closed)

    prev_open, prev_close = prev["open"], prev["close"]
    curr_open, curr_close, curr_high, curr_low = (
        curr["open"],
        curr["close"],
        curr["high"],
        curr["low"],
    )

    body = abs(curr_close - curr_open)
    full_range = curr_high - curr_low

    # Avoid zero-range candles (doji / no-volume ticks)
    if full_range == 0:
        return None

    body_ratio = body / full_range

    # ─── Bullish Engulfing ───────────────────────────────────
    if (
        prev_close < prev_open                # Previous candle is bearish
        and curr_close > curr_open             # Current candle is bullish
        and curr_close > prev_open             # Current body engulfs previous body
        and curr_open <= prev_close            # Current open at/below previous close
    ):
        logger.info(f"🟢 BULLISH ENGULFING detected on {symbol}")
        return "BUY"

    # ─── Bearish Engulfing ───────────────────────────────────
    if (
        prev_close > prev_open                # Previous candle is bullish
        and curr_close < curr_open             # Current candle is bearish
        and curr_close < prev_open             # Current body engulfs previous body
        and curr_open >= prev_close            # Current open at/above previous close
    ):
        logger.info(f"🔴 BEARISH ENGULFING detected on {symbol}")
        return "SELL"

    # ─── Hammer (Bullish Reversal) ───────────────────────────
    # Small body at the top, long lower wick (≥2x body), tiny upper wick
    upper_wick = curr_high - max(curr_open, curr_close)
    lower_wick = min(curr_open, curr_close) - curr_low

    if (
        body_ratio < 0.35                     # Small body relative to range
        and lower_wick >= 2.0 * body           # Long lower shadow
        and upper_wick <= body * 0.5           # Tiny upper shadow
    ):
        logger.info(f"🟢 HAMMER detected on {symbol}")
        return "BUY"

    # ─── Shooting Star / Inverted Hammer (Bearish) ───────────
    if (
        body_ratio < 0.35
        and upper_wick >= 2.0 * body
        and lower_wick <= body * 0.5
    ):
        logger.info(f"🔴 SHOOTING STAR detected on {symbol}")
        return "SELL"

    return None
