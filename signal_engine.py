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


def _calc_ema(rates, period: int) -> float:
    if not rates or len(rates) < period:
        return 0.0
    closes = [r['close'] for r in rates]
    sma = sum(closes[:period]) / period
    ema = sma
    multiplier = 2 / (period + 1)
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def _calc_adx(rates, period: int = 14) -> float:
    if not rates or len(rates) < period * 2:
        return 0.0
    trs, pos_dm, neg_dm = [], [], []
    for i in range(1, len(rates)):
        h, l, pc = rates[i]['high'], rates[i]['low'], rates[i-1]['close']
        ph, pl = rates[i-1]['high'], rates[i-1]['low']
        tr = max(h - l, abs(h - pc), abs(l - pc))
        up_m, down_m = h - ph, pl - l
        pdm = up_m if (up_m > down_m and up_m > 0) else 0
        ndm = down_m if (down_m > up_m and down_m > 0) else 0
        trs.append(tr)
        pos_dm.append(pdm)
        neg_dm.append(ndm)

    def smooth(data, length):
        res = [sum(data[:length])]
        for val in data[length:]:
            res.append(res[-1] - (res[-1] / length) + val)
        return res

    smoothed_tr = smooth(trs, period)
    smoothed_pdm = smooth(pos_dm, period)
    smoothed_ndm = smooth(neg_dm, period)

    dx_values = []
    for i in range(len(smoothed_tr)):
        if smoothed_tr[i] == 0:
            dx_values.append(0)
            continue
        pdi = 100 * smoothed_pdm[i] / smoothed_tr[i]
        ndi = 100 * smoothed_ndm[i] / smoothed_tr[i]
        dx_values.append(100 * abs(pdi - ndi) / (pdi + ndi) if (pdi + ndi) > 0 else 0)

    if len(dx_values) < period:
        return 0.0
    adx = sum(dx_values[:period]) / period
    for val in dx_values[period:]:
        adx = ((adx * (period - 1)) + val) / period
    return adx


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
    # Fetch 250 candles for EMA200 and ADX computation
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 250)

    if rates is None or len(rates) < 200:
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

    # ─── 3. Strong Trend / Momentum Filters (EMA 200 + ADX) ───
    # We pass up to -1 to evaluate based on all closed candles
    ema200 = _calc_ema(rates[:-1], period=200)
    adx_value = _calc_adx(rates[:-1], period=14)
    
    # We define an aggressive uptrend if price is > EMA 200 and ADX > 25
    is_strong_uptrend = (curr_close > ema200) and (adx_value > 25.0)
    # We define an aggressive downtrend if price is < EMA 200 and ADX > 25
    is_strong_downtrend = (curr_close < ema200) and (adx_value > 25.0)

    if is_strong_uptrend:
        logger.debug(f"[{symbol}] Strong UPTREND (Price > EMA200 & ADX={adx_value:.1f} > 25)")
    if is_strong_downtrend:
        logger.debug(f"[{symbol}] Strong DOWNTREND (Price < EMA200 & ADX={adx_value:.1f} > 25)")

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
            # Strict momentum continuation: candle MUST break previous high
            if curr_close <= prev["high"]:
                logger.info(f"🟢 GREEN candle, but failed to break previous High ({prev['high']}). Weak bullish momentum. Skipping BUY.")
                return None
            # Reject if there is noticeable selling pressure at the top
            if upper_wick >= body * 0.5:
                logger.info(f"🟢 GREEN candle, but noticeable upper wick rejection. Buyers lost control at the top. Skipping BUY.")
                return None
            if is_strong_downtrend:
                logger.info(f"🚫 BUY Signal BLOCKED by Momentum Filter! Strong Downtrend detected (ADX={adx_value:.1f}, Price < EMA200).")
                return None
            logger.info(f"🟢 GREEN candle (Trend: {trend}, Momentum: Strong Breakout) → BUY signal on {symbol} (Surge: {surge_ratio:.2f}x)")
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
            # Strict momentum continuation: candle MUST break previous low
            if curr_close >= prev["low"]:
                logger.info(f"🔴 RED candle, but failed to break previous Low ({prev['low']}). Weak bearish momentum. Skipping SELL.")
                return None
            # Reject if there is noticeable buying pressure at the bottom
            if lower_wick >= body * 0.5:
                logger.info(f"🔴 RED candle, but noticeable lower wick rejection. Sellers lost control at the bottom. Skipping SELL.")
                return None
            if is_strong_uptrend:
                logger.info(f"🚫 SELL Signal BLOCKED by Momentum Filter! Strong Uptrend detected (ADX={adx_value:.1f}, Price > EMA200).")
                return None
            logger.info(f"🔴 RED candle (Trend: {trend}, Momentum: Strong Breakout) → SELL signal on {symbol} (Surge: {surge_ratio:.2f}x)")
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
        if is_strong_downtrend:
            logger.info(f"🚫 BULLISH ENGULFING BLOCKED! Strong Downtrend (ADX={adx_value:.1f}, Price < EMA200).")
            return None
        logger.info(f"🟢 BULLISH ENGULFING detected on {symbol} (Surge: {surge_ratio:.2f}x)")
        return {"direction": "BUY", "surge_ratio": surge_ratio}

    # Bearish Engulfing
    if (
        prev_close > prev_open
        and curr_close < curr_open
        and curr_close < prev_open
        and curr_open >= prev_close
    ):
        if is_strong_uptrend:
            logger.info(f"🚫 BEARISH ENGULFING BLOCKED! Strong Uptrend (ADX={adx_value:.1f}, Price > EMA200).")
            return None
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
        if is_strong_downtrend:
            logger.info(f"🚫 HAMMER BLOCKED! Strong Downtrend (ADX={adx_value:.1f}, Price < EMA200).")
            return None
        logger.info(f"🟢 HAMMER detected on {symbol} (Surge: {surge_ratio:.2f}x)")
        return {"direction": "BUY", "surge_ratio": surge_ratio}

    # Shooting Star (Bearish)
    if (
        body_ratio < 0.35
        and upper_wick >= 2.0 * body
        and lower_wick <= body * 0.5
    ):
        if is_strong_uptrend:
            logger.info(f"🚫 SHOOTING STAR BLOCKED! Strong Uptrend (ADX={adx_value:.1f}, Price > EMA200).")
            return None
        logger.info(f"🔴 SHOOTING STAR detected on {symbol} (Surge: {surge_ratio:.2f}x)")
        return {"direction": "SELL", "surge_ratio": surge_ratio}

    logger.info(f"No pattern matched on {symbol}. Waiting for next candle...")
    return None

