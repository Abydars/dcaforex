"""
============================================================
 Liquidity & FVG Detection
============================================================
- detect_fvgs():            Fair Value Gaps (3-candle imbalances)
- find_recent_sweep():      Liquidity sweep (wick beyond swing, close back)
- find_entry_fvg_after_sweep(): Combined setup validator
============================================================
"""

from dataclasses import dataclass
from typing import List, Optional

import config
from structure import Swing, detect_swings


@dataclass
class FVG:
    """Fair Value Gap — a 3-candle imbalance."""
    direction: str     # 'BULLISH' (gap up) or 'BEARISH' (gap down)
    top: float         # Upper boundary of the gap
    bottom: float      # Lower boundary of the gap
    mid: float         # Midpoint (common entry target)
    formed_at: int     # Index of the middle candle of the 3-candle pattern
    time: int          # Timestamp of middle candle

    @property
    def size(self) -> float:
        return self.top - self.bottom

    def __repr__(self):
        return f"FVG({self.direction} {self.bottom:.2f}-{self.top:.2f} @{self.formed_at})"


@dataclass
class LiquiditySweep:
    """A liquidity sweep event — wick past swing, close back inside."""
    direction: str      # 'BULLISH' (swept lows, reversing up) or 'BEARISH' (swept highs, reversing down)
    swing: Swing        # The swing that got swept
    sweep_index: int    # Candle index where sweep occurred
    sweep_extreme: float  # The extreme price reached (wick high/low)
    close_price: float  # Close of the sweep candle

    def __repr__(self):
        return f"Sweep({self.direction}@{self.sweep_index} {self.swing.kind}={self.swing.price:.2f})"


# ─── FVG Detection ─────────────────────────────────────────
def detect_fvgs(
    candles,
    atr: float = 0.0,
    max_age: int = None,
    only_unmitigated: bool = True,
) -> List[FVG]:
    """
    Detect Fair Value Gaps in the candle array.

    A Bullish FVG exists when:
        candles[i-1].high < candles[i+1].low
        (there's a gap between candle i-1's high and candle i+1's low)

    A Bearish FVG exists when:
        candles[i-1].low > candles[i+1].high

    If only_unmitigated=True, filters out FVGs where later price action
    has already filled the gap.
    """
    # Compute effective min size
    atr_based = atr * config.FVG_MIN_ATR_FRAC if atr > 0 else 0.0
    min_size = max(config.FVG_FLOOR_USD, atr_based)
    if max_age is None:
        max_age = config.FVG_MAX_AGE_BARS

    fvgs: List[FVG] = []
    n = len(candles)
    if n < 3:
        return fvgs

    latest_index = n - 1

    for i in range(1, n - 1):
        prev_h = float(candles[i - 1]['high'])
        prev_l = float(candles[i - 1]['low'])
        next_h = float(candles[i + 1]['high'])
        next_l = float(candles[i + 1]['low'])

        # Age check — only keep recent FVGs
        age = latest_index - i
        if age > max_age:
            continue

        # Bullish FVG
        if prev_h < next_l:
            size = next_l - prev_h
            if size < min_size:
                continue
            fvg = FVG(
                direction='BULLISH',
                top=next_l,
                bottom=prev_h,
                mid=(prev_h + next_l) / 2.0,
                formed_at=i,
                time=int(candles[i]['time']),
            )
            if only_unmitigated:
                # Has any later candle's low traded below the FVG top?
                mitigated = any(
                    float(candles[j]['low']) <= prev_h
                    for j in range(i + 2, n)
                )
                if mitigated:
                    continue
            fvgs.append(fvg)

        # Bearish FVG
        elif prev_l > next_h:
            size = prev_l - next_h
            if size < min_size:
                continue
            fvg = FVG(
                direction='BEARISH',
                top=prev_l,
                bottom=next_h,
                mid=(prev_l + next_h) / 2.0,
                formed_at=i,
                time=int(candles[i]['time']),
            )
            if only_unmitigated:
                mitigated = any(
                    float(candles[j]['high']) >= next_h
                    for j in range(i + 2, n)
                )
                if mitigated:
                    continue
            fvgs.append(fvg)

    return fvgs


# ─── Liquidity Sweep Detection ─────────────────────────────
def find_recent_sweep(
    candles,
    direction: str,
    atr: float = 0.0,
    lookback: int = None,
) -> Optional[LiquiditySweep]:
    """
    Find the most recent liquidity sweep in the given direction.

    direction='BULLISH' → looking for a sweep of SWING LOWS (wick below, close above).
        This indicates buy-side liquidity grab, bullish reversal likely.
    direction='BEARISH' → looking for a sweep of SWING HIGHS (wick above, close below).
        This indicates sell-side liquidity grab, bearish reversal likely.

    Valid sweep:
      - Candle's low (or high) extends past a prior swing point by >= min_wick
      - Candle's close is back ON THE RIGHT SIDE of that swing level
    """
    if lookback is None:
        lookback = config.SWEEP_LOOKBACK_BARS

    atr_based = atr * config.SWEEP_WICK_ATR_FRAC if atr > 0 else 0.0
    min_wick = max(config.SWEEP_WICK_FLOOR_USD, atr_based)

    n = len(candles)
    if n < 5:
        return None

    swings = detect_swings(candles)
    if not swings:
        return None

    # Examine recent candles for sweep patterns (skip the last unconfirmed candle)
    start = max(0, n - lookback - 1)
    end = n - 1  # Exclude forming candle

    best_sweep: Optional[LiquiditySweep] = None

    for i in range(end - 1, start, -1):  # Walk backwards — most recent first
        c_high = float(candles[i]['high'])
        c_low = float(candles[i]['low'])
        c_close = float(candles[i]['close'])

        if direction == 'BULLISH':
            # Find swing lows that formed BEFORE this candle
            checked = 0
            for s in reversed(swings):
                if s.index >= i:
                    continue
                if s.kind != 'LOW':
                    continue
                checked += 1
                if c_low < s.price - min_wick and c_close > s.price:
                    # Sweep confirmed
                    return LiquiditySweep(
                        direction='BULLISH',
                        swing=s,
                        sweep_index=i,
                        sweep_extreme=c_low,
                        close_price=c_close,
                    )
                if checked >= config.SWEEP_MAX_SWINGS_BACK:
                    break

        elif direction == 'BEARISH':
            checked = 0
            for s in reversed(swings):
                if s.index >= i:
                    continue
                if s.kind != 'HIGH':
                    continue
                checked += 1
                if c_high > s.price + min_wick and c_close < s.price:
                    return LiquiditySweep(
                        direction='BEARISH',
                        swing=s,
                        sweep_index=i,
                        sweep_extreme=c_high,
                        close_price=c_close,
                    )
                if checked >= config.SWEEP_MAX_SWINGS_BACK:
                    break

    return best_sweep


# ─── Combined Entry Setup Detection ────────────────────────
def find_entry_fvg_after_sweep(
    candles,
    direction: str,
    sweep: LiquiditySweep,
    atr: float = 0.0,
) -> Optional[FVG]:
    """
    After a sweep, look for an unmitigated FVG in the same direction
    that formed AT OR AFTER the sweep candle.

    Returns the highest-quality FVG (closest to current price, unmitigated)
    or None.
    """
    if sweep is None:
        return None

    fvgs = detect_fvgs(candles, only_unmitigated=True, atr=atr)
    # Filter: same direction, formed after sweep
    aligned = [
        f for f in fvgs
        if f.direction == direction and f.formed_at >= sweep.sweep_index
    ]
    if not aligned:
        return None

    # Pick the most recent FVG (freshest setup)
    aligned.sort(key=lambda f: f.formed_at, reverse=True)
    return aligned[0]


# ─── Nearest Liquidity Target ──────────────────────────────
def find_next_liquidity_target(
    candles,
    direction: str,
    entry_price: float,
) -> Optional[float]:
    """
    Find the next opposing liquidity pool to use as TP.

    For a BUY (direction='BULLISH'):
        Look for the nearest unbroken swing HIGH above entry.
    For a SELL (direction='BEARISH'):
        Look for the nearest unbroken swing LOW below entry.
    """
    swings = detect_swings(candles)
    if not swings:
        return None

    if direction == 'BULLISH':
        candidates = [
            s.price for s in swings
            if s.kind == 'HIGH' and s.price > entry_price
        ]
        if not candidates:
            return None
        # Nearest = lowest high above entry
        return min(candidates)
    else:
        candidates = [
            s.price for s in swings
            if s.kind == 'LOW' and s.price < entry_price
        ]
        if not candidates:
            return None
        return max(candidates)
