"""
============================================================
 Structure Inspector — Verify SMC detection visually
============================================================
Run this to print the current market state without trading:
  - H1 trend, last BOS/CHoCH, protected levels
  - M15 zone (premium/discount)
  - M5 recent sweep + FVG
  - All filter states
Use this for forward-testing the strategy before going live.
============================================================
"""

import logging
import sys

import config
from bias import get_bias
from filters import (
    check_news,
    check_session,
    check_spread,
    check_volatility,
)
from liquidity import (
    detect_fvgs,
    find_entry_fvg_after_sweep,
    find_next_liquidity_target,
    find_recent_sweep,
)
from mt5_connector import (
    TF_H1,
    TF_M5,
    TF_M15,
    get_rates,
    get_tick,
    initialize_mt5,
    shutdown_mt5,
)
from signal_engine import generate_signal
from structure import analyze_structure, detect_swings

logger = logging.getLogger("Inspector")


def inspect():
    tick = get_tick(config.SYMBOL)
    if tick is None:
        logger.error("No tick")
        return
    price = (tick.bid + tick.ask) / 2.0

    print("\n" + "=" * 70)
    print(f" XAUUSD SMC INSPECTOR | Current price: ${price:.2f}")
    print("=" * 70)

    # ── H1 ──
    h1 = get_rates(config.SYMBOL, TF_H1, config.H1_LOOKBACK)
    h1_swings = detect_swings(h1)
    h1_state = analyze_structure(h1, h1_swings)
    print(f"\n[H1] Trend: {h1_state.trend}")
    print(f"     Swings detected: {len(h1_swings)}")
    if h1_state.last_bos:
        print(f"     Last BOS:   {h1_state.last_bos}")
    if h1_state.last_choch:
        print(f"     Last CHoCH: {h1_state.last_choch}")
    if h1_state.protected_high:
        print(f"     Protected HIGH: ${h1_state.protected_high.price:.2f}")
    if h1_state.protected_low:
        print(f"     Protected LOW:  ${h1_state.protected_low.price:.2f}")

    # ── M15 ──
    m15 = get_rates(config.SYMBOL, TF_M15, config.M15_LOOKBACK)
    m15_swings = detect_swings(m15)
    m15_state = analyze_structure(m15, m15_swings)
    print(f"\n[M15] Trend: {m15_state.trend}")
    print(f"      Swings detected: {len(m15_swings)}")
    if m15_state.last_bos:
        print(f"      Last BOS:   {m15_state.last_bos}")
    if m15_state.last_choch:
        print(f"      Last CHoCH: {m15_state.last_choch}")

    bias = get_bias(config.SYMBOL, price)
    print(f"\n[Bias] Tradeable: {bias.is_tradeable}")
    print(f"       Reason: {bias.reason}")

    # ── M5 Liquidity ──
    m5 = get_rates(config.SYMBOL, TF_M5, config.M5_LOOKBACK)
    print(f"\n[M5] Lookback: {len(m5) if m5 is not None else 0} bars")
    for direction in ('BULLISH', 'BEARISH'):
        sweep = find_recent_sweep(m5, direction)
        if sweep:
            print(f"     Recent {direction} sweep: {sweep}")
            fvg = find_entry_fvg_after_sweep(m5, direction, sweep)
            if fvg:
                print(f"       ↳ FVG: {fvg}")

    # All unmitigated FVGs
    for d in ('BULLISH', 'BEARISH'):
        fvgs = [f for f in detect_fvgs(m5) if f.direction == d]
        if fvgs:
            print(f"     Unmitigated {d} FVGs: {len(fvgs)}")
            for f in fvgs[-3:]:  # last 3
                print(f"       {f}")

    # ── Filters ──
    print("\n[Filters]")
    for name, fn in [
        ("Session", check_session),
        ("Spread", lambda: check_spread(config.SYMBOL)),
        ("Volatility", lambda: check_volatility(config.SYMBOL)),
        ("News", check_news),
    ]:
        ok, reason = fn()
        mark = "✓" if ok else "✗"
        print(f"     {mark} {name:12s} — {reason}")

    # ── Full signal check ──
    print("\n[Signal]")
    sig = generate_signal(config.SYMBOL)
    if sig:
        print(f"  🎯 TRADEABLE: {sig}")
        print(f"     {sig.setup_note}")
    else:
        print("  — No setup right now")

    print("\n" + "=" * 70 + "\n")


if __name__ == "__main__":
    if not initialize_mt5():
        sys.exit(1)
    try:
        inspect()
    finally:
        shutdown_mt5()
