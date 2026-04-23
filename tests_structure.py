"""
============================================================
 Unit Tests — SMC Structure & Liquidity
============================================================
Run with:   python -m pytest tests_structure.py -v
Or:         python tests_structure.py
============================================================
"""

import numpy as np

from liquidity import detect_fvgs, find_recent_sweep
from structure import (
    analyze_structure,
    detect_swings,
    get_premium_discount,
    is_in_discount,
    is_in_premium,
)


def _mk_candles(data):
    """Build a numpy structured array from list of (open, high, low, close) tuples."""
    dtype = [
        ('time', 'i8'),
        ('open', 'f8'),
        ('high', 'f8'),
        ('low', 'f8'),
        ('close', 'f8'),
        ('tick_volume', 'i8'),
        ('spread', 'i4'),
        ('real_volume', 'i8'),
    ]
    arr = np.zeros(len(data), dtype=dtype)
    for i, (o, h, l, c) in enumerate(data):
        arr[i] = (i * 300, o, h, l, c, 100, 5, 0)
    return arr


# ─── Swing Detection ───────────────────────────────────────
def test_swing_high_detected():
    # A clear peak at index 3
    candles = _mk_candles([
        (10, 11, 9, 10),   # 0
        (10, 12, 9, 11),   # 1
        (11, 13, 10, 12),  # 2
        (12, 15, 11, 14),  # 3 ← peak
        (14, 14, 12, 13),  # 4
        (13, 13, 11, 12),  # 5
        (12, 12, 10, 11),  # 6
    ])
    swings = detect_swings(candles, left=3, right=3)
    highs = [s for s in swings if s.kind == 'HIGH']
    assert any(s.index == 3 and s.price == 15 for s in highs), f"Expected swing high at 3, got {swings}"


def test_swing_low_detected():
    candles = _mk_candles([
        (15, 16, 14, 15),  # 0
        (15, 15, 13, 14),  # 1
        (14, 14, 12, 13),  # 2
        (13, 13, 10, 11),  # 3 ← trough
        (11, 14, 11, 13),  # 4
        (13, 15, 12, 14),  # 5
        (14, 16, 13, 15),  # 6
    ])
    swings = detect_swings(candles, left=3, right=3)
    lows = [s for s in swings if s.kind == 'LOW']
    assert any(s.index == 3 and s.price == 10 for s in lows), f"Expected swing low at 3, got {swings}"


# ─── BOS Detection ─────────────────────────────────────────
def test_bullish_bos():
    # Clean swing low at index 3 (price 15), swing high at index 9 (price 30),
    # then candle at index 16 closes above 30 → BOS up
    candles = _mk_candles([
        (20, 21, 19, 20),   # 0
        (20, 20, 18, 19),   # 1
        (19, 19, 17, 18),   # 2
        (18, 18, 15, 16),   # 3 ← swing low at 15 (strict)
        (16, 17, 16, 17),   # 4
        (17, 19, 17, 18),   # 5
        (18, 20, 18, 19),   # 6
        (19, 22, 19, 21),   # 7
        (21, 25, 21, 24),   # 8
        (24, 30, 24, 28),   # 9 ← swing high at 30 (strict)
        (28, 28, 25, 26),   # 10
        (26, 27, 24, 25),   # 11
        (25, 26, 23, 24),   # 12
        (24, 25, 22, 23),   # 13
        (23, 24, 21, 22),   # 14
        (22, 23, 20, 22),   # 15
        (22, 31, 22, 31),   # 16 ← close 31 > 30 = BOS
        (31, 32, 30, 32),   # 17
        (32, 33, 31, 32),   # 18
        (32, 33, 31, 32),   # 19
    ])
    state = analyze_structure(candles)
    assert state.trend == 'BULLISH', f"Expected BULLISH trend, got {state.trend}"
    assert state.last_bos is not None, "Expected BOS detected"
    assert state.last_bos.direction == 'BULLISH'


def test_bearish_bos():
    # Single-BOS-down scenario (simplest valid test):
    # Form a clear swing low, then a later candle closes below it.
    # Using left=2, right=2 for compact test data.
    candles = _mk_candles([
        (20, 22, 18, 20),   # 0  H=22, L=18
        (20, 21, 18, 19),   # 1  H=21, L=18
        (19, 20, 17, 18),   # 2  H=20, L=17
        (18, 19, 15, 17),   # 3  H=19, L=15 ← swing low (L=15 < 17,18 before; < 16,17 after)
        (17, 18, 16, 17),   # 4  H=18, L=16
        (17, 19, 17, 18),   # 5  H=19, L=17 (confirms swing low at 3)
        (18, 20, 17, 19),   # 6
        (19, 20, 18, 19),   # 7
        (19, 19, 14, 14),   # 8  ← close=14 < 15 (swing low) → BOS DOWN
        (14, 15, 12, 13),   # 9
    ])
    swings = detect_swings(candles, left=2, right=2)
    state = analyze_structure(candles, swings)
    assert any(s.kind == 'LOW' and s.price == 15 for s in swings), \
        f"Expected swing low at price 15, got swings: {swings}"
    assert state.trend == 'BEARISH', f"Expected BEARISH, got {state.trend} | events={state.events}"
    assert state.last_bos is not None, "Expected BOS recorded"
    assert state.last_bos.direction == 'BEARISH'


def test_choch_reversal():
    # BOS up first (establishes BULLISH trend), then later break below
    # a new swing low → should register as CHoCH down (not BOS).
    candles = _mk_candles([
        (9, 10, 8, 9),       # 0
        (10, 12, 9, 11),     # 1
        (12, 15, 10, 14),    # 2  H=15 ← first swing high
        (13, 13, 11, 12),    # 3
        (10, 11, 7, 8),      # 4  L=7 ← swing low
        (8, 9, 8, 9),        # 5
        (9, 10, 8, 10),      # 6
        (13, 17, 12, 16),    # 7  close=16 > 15 → BOS UP (trend=BULLISH)
        (16, 18, 15, 17),    # 8
        (17, 19, 16, 18),    # 9
        (18, 20, 16, 19),    # 10 H=20 ← new swing high after BOS
        (17, 18, 13, 14),    # 11
        (14, 15, 12, 13),    # 12 L=12 ← new swing low
        (13, 14, 13, 13),    # 13
        (14, 14, 13, 14),    # 14 (confirms swing low at 12)
        (12, 13, 5, 5),      # 15 close=5 < 12 → CHoCH DOWN (trend was BULLISH)
    ])
    swings = detect_swings(candles, left=2, right=2)
    state = analyze_structure(candles, swings)
    assert state.last_bos is not None, f"Expected BOS recorded | events={state.events}"
    assert state.last_choch is not None, f"Expected CHoCH recorded | events={state.events}"
    assert state.last_choch.direction == 'BEARISH', f"Expected BEARISH CHoCH, got {state.last_choch}"
    assert state.trend == 'BEARISH', f"Trend should have flipped, got {state.trend}"


def test_empty_data():
    # Insufficient candles should produce UNKNOWN trend
    candles = _mk_candles([(10, 11, 9, 10)] * 3)
    state = analyze_structure(candles)
    assert state.trend == 'UNKNOWN'
    assert state.last_bos is None
    assert state.last_choch is None


# ─── FVG Detection ─────────────────────────────────────────
def test_bullish_fvg():
    # candle[i-1].high < candle[i+1].low → bullish FVG at index i
    candles = _mk_candles([
        (10, 11, 9, 10),    # 0 (prev)
        (11, 14, 11, 13),   # 1 (middle — impulsive)
        (13, 14, 12, 13),   # 2 (next — low=12, prev high=11, gap 11-12)
        (13, 14, 12, 13),   # 3
        (13, 14, 12, 13),   # 4
    ])
    fvgs = detect_fvgs(candles, atr=5.0, only_unmitigated=False)
    bull = [f for f in fvgs if f.direction == 'BULLISH']
    assert len(bull) >= 1, f"Expected at least 1 bullish FVG, got {fvgs}"
    assert bull[0].bottom == 11
    assert bull[0].top == 12


def test_bearish_fvg():
    candles = _mk_candles([
        (13, 14, 12, 13),   # 0
        (13, 13, 10, 11),   # 1 (middle)
        (11, 11, 9, 10),    # 2 (next — high=11, prev low=12, gap 11-12)
        (10, 11, 9, 10),    # 3
        (10, 11, 9, 10),    # 4
    ])
    fvgs = detect_fvgs(candles, atr=5.0, only_unmitigated=False)
    bear = [f for f in fvgs if f.direction == 'BEARISH']
    assert len(bear) >= 1, f"Expected at least 1 bearish FVG, got {fvgs}"


def test_mitigated_fvg_excluded():
    # FVG gets filled by later price action
    candles = _mk_candles([
        (10, 11, 9, 10),    # 0
        (11, 14, 11, 13),   # 1
        (13, 14, 12, 13),   # 2 ← FVG formed (11-12)
        (12, 13, 10, 11),   # 3 ← fills the FVG (low 10 dips into 11-12 zone)
        (11, 12, 10, 11),   # 4
    ])
    fvgs = detect_fvgs(candles, atr=5.0, only_unmitigated=True)
    # Should be empty because mitigated
    assert not any(f.direction == 'BULLISH' for f in fvgs)


# ─── Sweep Detection ───────────────────────────────────────
def test_bullish_sweep():
    # Price forms a clean swing low at 15 (index 3), then a later candle
    # wicks well below 15 but closes back above → bullish sweep.
    candles = _mk_candles([
        (20, 21, 19, 20),   # 0
        (20, 20, 18, 19),   # 1
        (19, 19, 17, 18),   # 2
        (18, 18, 15, 16),   # 3 ← swing low at 15
        (16, 17, 16, 17),   # 4
        (17, 19, 17, 18),   # 5
        (18, 20, 18, 19),   # 6 (swing low confirmed)
        (19, 21, 18, 20),   # 7
        (20, 22, 19, 21),   # 8
        (21, 21, 13, 20),   # 9 ← wick to 13 (below 15), close 20 > 15 = SWEEP
        (20, 22, 19, 21),   # 10
        (21, 23, 20, 22),   # 11
    ])
    sweep = find_recent_sweep(candles, direction='BULLISH', atr=6.25)
    assert sweep is not None, "Expected a sweep"
    assert sweep.direction == 'BULLISH'
    assert sweep.sweep_index == 9


# ─── Premium/Discount ──────────────────────────────────────
def test_discount_zone():
    # Leg from low 10 to high 20 → mid = 15
    # Discount = [10, 15], Premium = [15, 20]
    zone = (10.0, 15.0, 20.0)
    assert is_in_discount(12.0, zone) is True
    assert is_in_discount(18.0, zone) is False
    assert is_in_premium(18.0, zone) is True
    assert is_in_premium(12.0, zone) is False


def test_sweep_checks_multiple_prior_swings():
    """A sweep should be detectable even when the nearest prior swing
    wasn't the one swept — as long as it's within SWEEP_MAX_SWINGS_BACK."""
    candles = _mk_candles([
        (20, 21, 19, 20),   # 0
        (20, 20, 18, 19),   # 1
        (19, 19, 17, 18),   # 2
        # Swing Low A (price = 10)
        (18, 18, 10, 16),   # 3 <- A
        (16, 17, 15, 17),   # 4
        (17, 19, 16, 18),   # 5
        (18, 20, 18, 19),   # 6 (A confirmed)
        
        # Swing Low B (price = 15)
        (19, 21, 18, 20),   # 7
        (20, 22, 15, 19),   # 8 <- B
        (19, 20, 18, 20),   # 9
        (20, 22, 19, 21),   # 10
        (21, 23, 20, 22),   # 11 (B confirmed)
        
        # Sweep candle: wicks below A (10) and B (15).
        # Wick = 8, Close = 12.
        # Since close (12) > 10, it sweeps A.
        # But close (12) is NOT > 15, so it fails the sweep check for B.
        (22, 22, 8, 12),    # 12
        (12, 14, 11, 13),   # 13
        (13, 15, 12, 14),   # 14
    ])
    sweep = find_recent_sweep(candles, direction='BULLISH', atr=6.25)
    assert sweep is not None, "Expected sweep to be detected from older swing A"
    assert sweep.swing.price == 10, f"Expected to sweep A (10), got {sweep.swing.price}"


def test_fvg_atr_adaptive_low_vol():
    """When ATR is low, small FVGs above floor should be detected."""
    candles = _mk_candles([
        (10, 11.0, 9, 10),    # 0 (prev) -> prev high = 11.0
        (11, 14, 11, 13),     # 1 (middle)
        (13, 14, 11.2, 13),   # 2 (next) -> next low = 11.2, Gap = 0.20
        (13, 14, 12, 13),     # 3
        (13, 14, 12, 13),     # 4
    ])
    
    # With atr=0.5: threshold = max(0.10, 0.5*0.10) = 0.10. FVG 0.20 passes.
    fvgs_low = detect_fvgs(candles, atr=0.5, only_unmitigated=False)
    assert len(fvgs_low) == 1, "Expected FVG to be detected with low ATR"
    
    # With atr=3.0: threshold = max(0.10, 3.0*0.10) = 0.30. FVG 0.20 rejected.
    fvgs_high = detect_fvgs(candles, atr=3.0, only_unmitigated=False)
    assert len(fvgs_high) == 0, "Expected FVG to be ignored with high ATR"


def test_fvg_floor_enforced():
    """Even when ATR=0, the absolute floor applies."""
    # FVG of 0.05
    candles = _mk_candles([
        (10, 11.0, 9, 10),
        (11, 14, 11, 13),
        (13, 14, 11.05, 13), # gap = 11.05 - 11.0 = 0.05
        (13, 14, 12, 13),
    ])
    fvgs_below = detect_fvgs(candles, atr=0.0, only_unmitigated=False)
    assert len(fvgs_below) == 0

    # FVG of 0.15
    candles_above = _mk_candles([
        (10, 11.0, 9, 10),
        (11, 14, 11, 13),
        (13, 14, 11.15, 13), # gap = 11.15 - 11.0 = 0.15
        (13, 14, 12, 13),
    ])
    fvgs_above = detect_fvgs(candles_above, atr=0.0, only_unmitigated=False)
    assert len(fvgs_above) == 1


def test_sweep_wick_atr_adaptive():
    """Sweep wick threshold scales with ATR."""
    # Wick = 8, Swing = 10, Sweep extent = 2.0
    candles = _mk_candles([
        (20, 21, 19, 20),   # 0 (Padding)
        (20, 21, 19, 20),   # 1 (Padding)
        (20, 21, 19, 20),   # 2 (Padding)
        (18, 18, 10, 16),   # 3 Swing low A = 10
        (16, 17, 15, 17),   # 4
        (17, 19, 16, 18),   # 5
        (18, 20, 18, 19),   # 6 A confirmed
        (22, 22, 8, 12),    # 7 Sweep: wick 8 (extends by 2.0 below 10), close 12 > 10
        (12, 14, 11, 13),   # 8
        (12, 14, 11, 13),   # 9
    ])
    # atr=10.0 => threshold = max(0.10, 10 * 0.08) = 0.80. 2.0 > 0.80 -> passes
    sweep_low = find_recent_sweep(candles, direction='BULLISH', atr=10.0)
    assert sweep_low is not None

    # atr=40.0 => threshold = max(0.10, 40 * 0.08) = 3.20. 2.0 < 3.20 -> fails
    sweep_high = find_recent_sweep(candles, direction='BULLISH', atr=40.0)
    assert sweep_high is None


# ─── Run All ───────────────────────────────────────────────
if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"✓ {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"✗ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {t.__name__}: UNEXPECTED {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    raise SystemExit(0 if failed == 0 else 1)
