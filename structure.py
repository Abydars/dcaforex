"""
============================================================
 SMC Structure Detection
============================================================
Core primitives:
  - detect_swings():        fractal-based swing high/low detection
  - analyze_structure():    returns trend state + last BOS/CHoCH
  - get_protected_levels(): most recent protected high/low

Terminology (ICT/SMC):
  BOS (Break of Structure): price breaks a prior swing in the
      direction of trend → trend continuation confirmed
  CHoCH (Change of Character): first break AGAINST the current
      trend → early reversal signal
============================================================
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import config


@dataclass
class Swing:
    """A confirmed swing point (fractal)."""
    index: int       # Index in the candles array
    time: int        # UNIX timestamp
    price: float     # High or low price
    kind: str        # 'HIGH' or 'LOW'

    def __repr__(self):
        return f"Swing({self.kind}@{self.index} ${self.price:.2f})"


@dataclass
class StructureEvent:
    """A BOS or CHoCH event."""
    kind: str              # 'BOS' or 'CHoCH'
    direction: str         # 'BULLISH' or 'BEARISH'
    broken_swing: Swing    # The swing that was broken
    break_index: int       # Candle index where break occurred
    break_price: float     # Price at which break happened

    def __repr__(self):
        return f"{self.kind}({self.direction}@{self.break_index} broke {self.broken_swing})"


@dataclass
class StructureState:
    """Current market structure snapshot."""
    trend: str                              # 'BULLISH', 'BEARISH', or 'UNKNOWN'
    last_bos: Optional[StructureEvent]      # Most recent BOS in current trend
    last_choch: Optional[StructureEvent]    # Most recent CHoCH
    protected_high: Optional[Swing]         # Most recent confirmed swing high
    protected_low: Optional[Swing]          # Most recent confirmed swing low
    events: List[StructureEvent]            # All BOS/CHoCH events chronologically


# ─── Swing Detection ───────────────────────────────────────
def detect_swings(
    candles,
    left: int = None,
    right: int = None,
) -> List[Swing]:
    """
    Fractal-based swing detection.

    A candle at index i is a swing HIGH if its high is strictly greater than
    the highs of `left` candles before it AND `right` candles after it.
    Swing LOW is the mirror.

    Returns swings sorted by index. The last `right` candles can never be
    swings (not yet confirmed).
    """
    if left is None:
        left = config.FRACTAL_LEFT
    if right is None:
        right = config.FRACTAL_RIGHT

    swings: List[Swing] = []
    n = len(candles)
    if n < left + right + 1:
        return swings

    for i in range(left, n - right):
        h = float(candles[i]['high'])
        l = float(candles[i]['low'])
        t = int(candles[i]['time'])

        is_high = all(h > float(candles[i - j]['high']) for j in range(1, left + 1)) \
              and all(h > float(candles[i + j]['high']) for j in range(1, right + 1))
        is_low = all(l < float(candles[i - j]['low']) for j in range(1, left + 1)) \
             and all(l < float(candles[i + j]['low']) for j in range(1, right + 1))

        if is_high:
            swings.append(Swing(index=i, time=t, price=h, kind='HIGH'))
        if is_low:
            swings.append(Swing(index=i, time=t, price=l, kind='LOW'))

    # Sort by index; if same index, both kinds can coexist on small ranges
    swings.sort(key=lambda s: s.index)
    return swings


# ─── Structure Analysis ────────────────────────────────────
def analyze_structure(candles, swings: List[Swing] = None) -> StructureState:
    """
    Walk through swings chronologically, track BOS/CHoCH events.

    Algorithm:
      Start UNKNOWN.
      Track the most recent UNBROKEN swing high (H_ref) and swing low (L_ref).
      When price CLOSES beyond H_ref:
          - If trend was BULLISH or UNKNOWN → BOS up → trend = BULLISH
          - If trend was BEARISH            → CHoCH up → trend = BULLISH
          Then H_ref resets to the next unbroken swing high after break.
      Symmetric for breaks of L_ref.

    Uses candle closes for break confirmation (standard SMC).
    """
    if swings is None:
        swings = detect_swings(candles)

    state = StructureState(
        trend='UNKNOWN',
        last_bos=None,
        last_choch=None,
        protected_high=None,
        protected_low=None,
        events=[],
    )

    if not swings:
        return state

    # Separate into highs/lows, each sorted by index
    highs = [s for s in swings if s.kind == 'HIGH']
    lows = [s for s in swings if s.kind == 'LOW']

    # Walk candles forward from the first swing onward
    start_idx = swings[0].index

    trend = 'UNKNOWN'

    for i in range(start_idx, len(candles)):
        close = float(candles[i]['close'])

        # Find the most recent confirmed high that is before this candle
        ref_high = None
        for h in reversed(highs):
            if h.index < i:
                ref_high = h
                break

        ref_low = None
        for l in reversed(lows):
            if l.index < i:
                ref_low = l
                break

        # BOS / CHoCH up?
        if ref_high and close > ref_high.price:
            # Only fire once per reference — we mark the high as "broken"
            # by checking if we've already logged it
            already_broken = any(
                e.broken_swing.index == ref_high.index for e in state.events
            )
            if not already_broken:
                event_kind = 'BOS' if trend == 'BULLISH' else \
                             ('CHoCH' if trend == 'BEARISH' else 'BOS')
                ev = StructureEvent(
                    kind=event_kind,
                    direction='BULLISH',
                    broken_swing=ref_high,
                    break_index=i,
                    break_price=close,
                )
                state.events.append(ev)
                trend = 'BULLISH'
                if ev.kind == 'BOS':
                    state.last_bos = ev
                else:
                    state.last_choch = ev

        # BOS / CHoCH down?
        if ref_low and close < ref_low.price:
            already_broken = any(
                e.broken_swing.index == ref_low.index for e in state.events
            )
            if not already_broken:
                event_kind = 'BOS' if trend == 'BEARISH' else \
                             ('CHoCH' if trend == 'BULLISH' else 'BOS')
                ev = StructureEvent(
                    kind=event_kind,
                    direction='BEARISH',
                    broken_swing=ref_low,
                    break_index=i,
                    break_price=close,
                )
                state.events.append(ev)
                trend = 'BEARISH'
                if ev.kind == 'BOS':
                    state.last_bos = ev
                else:
                    state.last_choch = ev

    state.trend = trend

    # Protected levels = most recent UNBROKEN swing extremes
    broken_indices = {e.broken_swing.index for e in state.events}
    unbroken_highs = [h for h in highs if h.index not in broken_indices]
    unbroken_lows = [l for l in lows if l.index not in broken_indices]
    state.protected_high = unbroken_highs[-1] if unbroken_highs else None
    state.protected_low = unbroken_lows[-1] if unbroken_lows else None

    return state


# ─── Premium / Discount Zones ──────────────────────────────
def get_premium_discount(
    candles,
    direction: str,
) -> Optional[Tuple[float, float, float]]:
    """
    For the most recent impulsive leg in `direction`, return:
        (leg_low, leg_mid, leg_high)

    direction='BULLISH' → leg is the last up-move (low→high)
        Discount zone = [leg_low, leg_mid]   (good for BUY entries)
        Premium zone  = [leg_mid, leg_high]  (avoid BUY here)
    direction='BEARISH' → leg is the last down-move (high→low)
        Premium zone  = [leg_mid, leg_high]  (good for SELL entries)
        Discount zone = [leg_low, leg_mid]   (avoid SELL here)

    Returns None if insufficient data.
    """
    swings = detect_swings(candles)
    if len(swings) < 2:
        return None

    if direction == 'BULLISH':
        # Find last swing low followed by a swing high, where high > low
        last_high = None
        last_low = None
        for s in reversed(swings):
            if last_high is None and s.kind == 'HIGH':
                last_high = s
                continue
            if last_high is not None and s.kind == 'LOW' and s.index < last_high.index:
                last_low = s
                break
        if last_high is None or last_low is None:
            return None
        leg_low = last_low.price
        leg_high = last_high.price
    elif direction == 'BEARISH':
        last_low = None
        last_high = None
        for s in reversed(swings):
            if last_low is None and s.kind == 'LOW':
                last_low = s
                continue
            if last_low is not None and s.kind == 'HIGH' and s.index < last_low.index:
                last_high = s
                break
        if last_low is None or last_high is None:
            return None
        leg_low = last_low.price
        leg_high = last_high.price
    else:
        return None

    leg_mid = (leg_low + leg_high) / 2.0
    return (leg_low, leg_mid, leg_high)


def is_in_discount(price: float, zone: Tuple[float, float, float]) -> bool:
    """zone = (leg_low, leg_mid, leg_high). Discount = price <= leg_mid."""
    if zone is None:
        return False
    return price <= zone[1]


def is_in_premium(price: float, zone: Tuple[float, float, float]) -> bool:
    """zone = (leg_low, leg_mid, leg_high). Premium = price >= leg_mid."""
    if zone is None:
        return False
    return price >= zone[1]

def get_last_impulsive_leg(candles, direction: str) -> Optional[Tuple[float, float, float]]:
    """
    Find the last clean impulsive leg in `direction` based on analyzed structure.

    For direction='BULLISH': returns (low, mid, high) of the most recent
        low→high swing in an uptrend context (either the current impulsive
        push or the leg that preceded the current pullback).

    For direction='BEARISH': returns (high, mid, low) conceptually, returned
        as (leg_low, leg_mid, leg_high) for consistency with existing code
        (leg_low = the swing low, leg_high = the swing high, mid = average).

    This differs from get_premium_discount() in that it specifically looks
    for a leg matching a given direction, even if the most recent leg is
    the opposite direction (the pullback).

    Returns None if no suitable leg is found.
    """
    swings = detect_swings(candles)
    if len(swings) < 2:
        return None

    # Walk backwards through swings; find the last pair that forms a leg
    # in the requested direction.
    # For BULLISH leg: a swing LOW followed (in time) by a swing HIGH.
    # For BEARISH leg: a swing HIGH followed (in time) by a swing LOW.
    #
    # IMPORTANT: We want the leg that corresponds to the PARENT trend, not
    # the pullback. So for direction='BULLISH', we iterate from most recent
    # backwards and find the first LOW→HIGH pair where HIGH came after LOW
    # chronologically AND high > low.

    if direction == 'BULLISH':
        # Find most recent (swing_low, swing_high) pair where high.index > low.index
        for hi in reversed([s for s in swings if s.kind == 'HIGH']):
            # Find the most recent swing LOW before this high
            low_before = None
            for lo in reversed([s for s in swings if s.kind == 'LOW' and s.index < hi.index]):
                low_before = lo
                break
            if low_before and hi.price > low_before.price:
                leg_low = low_before.price
                leg_high = hi.price
                return (leg_low, (leg_low + leg_high) / 2.0, leg_high)
        return None

    elif direction == 'BEARISH':
        for lo in reversed([s for s in swings if s.kind == 'LOW']):
            high_before = None
            for hi in reversed([s for s in swings if s.kind == 'HIGH' and s.index < lo.index]):
                high_before = hi
                break
            if high_before and high_before.price > lo.price:
                leg_low = lo.price
                leg_high = high_before.price
                return (leg_low, (leg_low + leg_high) / 2.0, leg_high)
        return None

    return None
