"""
============================================================
 Filters — Session, News, Volatility, Spread
============================================================
All filters return (passed: bool, reason: str).
A False result means DO NOT TRADE right now.
============================================================
"""

import logging
import time as time_mod
from datetime import datetime, time, timezone, timedelta
from typing import List, Tuple

import requests

import config
from mt5_connector import get_rates, get_spread_usd, TF_M15

logger = logging.getLogger("Filters")


# ─── Session Filter ────────────────────────────────────────
def check_session() -> Tuple[bool, str]:
    """Are we inside an allowed trading session (UTC)?"""
    now = datetime.now(tz=timezone.utc).time()

    for name, sh, sm, eh, em in config.SESSIONS_UTC:
        start = time(sh, sm)
        end = time(eh, em)
        if start <= now <= end:
            return True, f"Session: {name}"

    return False, "Outside trading sessions"


# ─── Spread Filter ─────────────────────────────────────────
def check_spread(symbol: str) -> Tuple[bool, str]:
    spread = get_spread_usd(symbol)
    if spread > config.MAX_SPREAD_USD:
        return False, f"Spread too wide: ${spread:.2f} > ${config.MAX_SPREAD_USD:.2f}"
    return True, f"Spread OK: ${spread:.2f}"


# ─── Volatility Filter (ATR on M15) ────────────────────────
def _compute_atr(candles, period: int = 14) -> float:
    if candles is None or len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h = float(candles[i]['high'])
        l = float(candles[i]['low'])
        pc = float(candles[i - 1]['close'])
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    # Simple average of last `period` TRs
    return sum(trs[-period:]) / period


def check_volatility(symbol: str) -> Tuple[bool, str]:
    m15 = get_rates(symbol, TF_M15, 20)
    atr = _compute_atr(m15, period=14)
    if atr < config.MIN_ATR_M15_USD:
        return False, f"ATR(M15)=${atr:.2f} < min ${config.MIN_ATR_M15_USD:.2f} (market flat)"
    return True, f"ATR(M15)=${atr:.2f} OK"


# ─── News Filter ───────────────────────────────────────────
_news_cache: List[dict] = []
_news_cache_time: float = 0.0
_NEWS_CACHE_TTL = 3600  # Refresh hourly


def _fetch_news() -> List[dict]:
    global _news_cache, _news_cache_time
    now = time_mod.time()
    if _news_cache and (now - _news_cache_time) < _NEWS_CACHE_TTL:
        return _news_cache
    try:
        resp = requests.get(config.NEWS_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        _news_cache = data
        _news_cache_time = now
        logger.info(f"News calendar refreshed: {len(data)} events this week")
        return data
    except Exception as e:
        logger.warning(f"Failed to fetch news calendar: {e} — continuing without news filter")
        return _news_cache  # Return stale cache rather than empty


def check_news() -> Tuple[bool, str]:
    """Block trading around high-impact news events."""
    if not config.NEWS_ENABLED:
        return True, "News filter disabled"

    events = _fetch_news()
    if not events:
        return True, "News data unavailable (permissive)"

    now_utc = datetime.now(tz=timezone.utc)
    buffer_before = timedelta(minutes=config.NEWS_BUFFER_BEFORE_MIN)
    buffer_after = timedelta(minutes=config.NEWS_BUFFER_AFTER_MIN)

    for ev in events:
        impact = ev.get('impact', '')
        currency = ev.get('country', '') or ev.get('currency', '')
        if impact not in config.NEWS_IMPACT_FILTER:
            continue
        if currency not in config.NEWS_CURRENCIES:
            continue

        # Parse event time — ForexFactory uses ISO-like formats
        date_str = ev.get('date', '')
        if not date_str:
            continue
        try:
            # Handle both with/without timezone info
            if date_str.endswith('Z'):
                ev_time = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            else:
                ev_time = datetime.fromisoformat(date_str)
            if ev_time.tzinfo is None:
                ev_time = ev_time.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        if (ev_time - buffer_before) <= now_utc <= (ev_time + buffer_after):
            title = ev.get('title', 'Unknown')
            return False, f"News block: {currency} {title} @ {ev_time.strftime('%H:%M UTC')}"

    return True, "No imminent high-impact news"


# ─── Aggregated Gate ───────────────────────────────────────
def all_filters_pass(symbol: str) -> Tuple[bool, str]:
    """Run every filter; return the first failure or collective OK."""
    for check_fn, label in [
        (check_session, "session"),
        (lambda: check_spread(symbol), "spread"),
        (lambda: check_volatility(symbol), "volatility"),
        (check_news, "news"),
    ]:
        passed, reason = check_fn()
        if not passed:
            return False, f"[{label}] {reason}"
    return True, "All filters passed"
