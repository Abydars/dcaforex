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
from mt5_connector import get_mt5_timeframe, get_mt5_htf

logger = logging.getLogger("SignalEngine")

# ─── Signal Mode ────────────────────────────────────────────
# "candle"  = Simple: green candle = BUY, red = SELL (fast entry)
# "pattern" = Strict: only engulfing / hammer patterns (selective)
SIGNAL_MODE = getattr(config, "SIGNAL_MODE", "candle")


def get_entry_signal(target_symbol: str = None) -> dict | None:
    """
    Fetch the last few candles and detect an entry signal
    on the most recently CLOSED candle (index -2).

    Returns: {"direction": "BUY"|"SELL", "surge_ratio": float} or None
    """
    symbol = target_symbol if target_symbol else config.SYMBOL
    tf = get_mt5_timeframe()

    # ─── 1. Fetch Higher Timeframe Context ───
    htf = get_mt5_htf(tf)
    htf_rates = mt5.copy_rates_from_pos(symbol, htf, 0, 50)
    
    trend = "NONE"
    if htf_rates is not None and len(htf_rates) == 50:
        htf_closes = [float(x['close']) for x in htf_rates]
        htf_sma50 = sum(htf_closes) / len(htf_closes)
        current_htf_close = htf_closes[-1]
        trend = "UP" if current_htf_close > htf_sma50 else "DOWN"
    else:
        logger.warning(f"Could not fetch enough HTF candles for {symbol}. Proceeding without trend filter.")

    # ─── 2. Fetch Lower Timeframe Context ───
    # Fetch 20 candles for ATR computation
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 20)

    if rates is None or len(rates) < 18:
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

    # Calculate Tick Volume Surge globally
    recent_vols = [r['tick_volume'] for r in rates[-12:-2]]
    avg_vol = sum(recent_vols) / len(recent_vols) if len(recent_vols) > 0 else 1
    curr_vol = curr['tick_volume']
    surge_ratio = curr_vol / avg_vol if avg_vol > 0 else 1.0

    # ─── MODE: Smart Candle Color (Trend + Momentum + Wick Rejection) ─
    upper_wick = curr_high - max(curr_open, curr_close)
    lower_wick = min(curr_open, curr_close) - curr_low

    if SIGNAL_MODE == "candle":
        # Calculate ATR (Average True Range) for recent 14 candles to detect FLAT markets
        trs = []
        for i in range(2, 16):
            idx = -i
            h = rates[idx]["high"]
            l = rates[idx]["low"]
            pc = rates[idx - 1]["close"]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        atr = sum(trs) / len(trs) if len(trs) > 0 else 0

        # Calculate recent average body (momentum filter)
        recent_bodies = [abs(r['close'] - r['open']) for r in rates[-7:-2]]
        avg_body = sum(recent_bodies) / len(recent_bodies) if len(recent_bodies) > 0 else 0

        if curr_close > curr_open:
            if body < (atr * 0.5):
                logger.info(f"🟢 GREEN candle, but market is FLAT (Body {body:.5f} < 50% ATR {atr:.5f}). Skipping.")
                return None
            if trend == "DOWN":
                logger.info("🟢 GREEN candle, but HTF trend is DOWN. Skipping BUY to align with trend.")
                return None
            if body <= avg_body:
                logger.info(f"🟢 GREEN candle, but low momentum (Body: {body:.5f} <= Avg: {avg_body:.5f}). Skipping BUY.")
                return None
            # Check for massive upper wick (Bearish rejection trap)
            if upper_wick >= body * 1.5:
                logger.info(f"🟢 GREEN candle, but HUGE upper wick (rejection). Skipping BUY.")
                return None
            logger.info(f"🟢 GREEN candle (Trend: {trend}, Momentum: High) → BUY signal on {symbol} (Surge: {surge_ratio:.2f}x)")
            return {"direction": "BUY", "surge_ratio": surge_ratio}
        elif curr_close < curr_open:
            if body < (atr * 0.5):
                logger.info(f"🔴 RED candle, but market is FLAT (Body {body:.5f} < 50% ATR {atr:.5f}). Skipping.")
                return None
            if trend == "UP":
                logger.info("🔴 RED candle, but HTF trend is UP. Skipping SELL to align with trend.")
                return None
            if body <= avg_body:
                logger.info(f"🔴 RED candle, but low momentum (Body: {body:.5f} <= Avg: {avg_body:.5f}). Skipping SELL.")
                return None
            # Check for massive lower wick (Bullish rejection trap)
            if lower_wick >= body * 1.5:
                logger.info(f"🔴 RED candle, but HUGE lower wick (rejection). Skipping SELL.")
                return None
            logger.info(f"🔴 RED candle (Trend: {trend}, Momentum: High) → SELL signal on {symbol} (Surge: {surge_ratio:.2f}x)")
            return {"direction": "SELL", "surge_ratio": surge_ratio}
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
        logger.info(f"🟢 BULLISH ENGULFING detected on {symbol} (Surge: {surge_ratio:.2f}x)")
        return {"direction": "BUY", "surge_ratio": surge_ratio}

    # Bearish Engulfing
    if (
        prev_close > prev_open
        and curr_close < curr_open
        and curr_close < prev_open
        and curr_open >= prev_close
    ):
        logger.info(f"🔴 BEARISH ENGULFING detected on {symbol} (Surge: {surge_ratio:.2f}x)")
        return {"direction": "SELL", "surge_ratio": surge_ratio}

    # Hammer (Bullish)
    upper_wick = curr_high - max(curr_open, curr_close)
    lower_wick = min(curr_open, curr_close) - curr_low

    if (
        body_ratio < 0.35
        and lower_wick >= 2.0 * body
        and upper_wick <= body * 0.5
    ):
        logger.info(f"🟢 HAMMER detected on {symbol} (Surge: {surge_ratio:.2f}x)")
        return {"direction": "BUY", "surge_ratio": surge_ratio}

    # Shooting Star (Bearish)
    if (
        body_ratio < 0.35
        and upper_wick >= 2.0 * body
        and lower_wick <= body * 0.5
    ):
        logger.info(f"🔴 SHOOTING STAR detected on {symbol} (Surge: {surge_ratio:.2f}x)")
        return {"direction": "SELL", "surge_ratio": surge_ratio}

    logger.info(f"No pattern matched on {symbol}. Waiting for next candle...")
    return None
