"""
============================================================
 Bias Engine — Multi-timeframe Directional Bias
============================================================
- H1:  Determines primary directional bias from structure
- M15: Validates that price is in a valid entry zone
       (discount for longs, premium for shorts)
       and confirms local structure alignment
============================================================
"""

import logging
from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5

import config
from mt5_connector import get_rates, TF_H1, TF_M15
from structure import (
    analyze_structure,
    detect_swings,
    get_premium_discount,
    get_last_impulsive_leg,
    is_in_discount,
    is_in_premium,
)

logger = logging.getLogger("Bias")


@dataclass
class BiasResult:
    """Complete bias snapshot for decision-making."""
    h1_trend: str                  # 'BULLISH' | 'BEARISH' | 'UNKNOWN'
    setup_type: str                # 'CONTINUATION' | 'PULLBACK_END' | 'NONE'
    m15_aligned: bool              # True if bias state is tradeable
    in_valid_zone: bool            # True if price is in discount (for longs) or premium (for shorts)
    current_price: float
    zone_low: Optional[float] = None
    zone_mid: Optional[float] = None
    zone_high: Optional[float] = None
    reason: str = ""               # Human-readable status

    @property
    def is_tradeable(self) -> bool:
        return (
            self.h1_trend in ('BULLISH', 'BEARISH')
            and self.m15_aligned
            and self.in_valid_zone
        )

    @property
    def direction(self) -> str:
        return self.h1_trend


def get_bias(symbol: str, current_price: float) -> BiasResult:
    # ── H1 bias (unchanged) ──
    h1_candles = get_rates(symbol, TF_H1, config.H1_LOOKBACK)
    if h1_candles is None:
        return BiasResult(
            h1_trend='UNKNOWN', setup_type='NONE',
            m15_aligned=False, in_valid_zone=False,
            current_price=current_price,
            reason="H1 data unavailable",
        )

    h1_state = analyze_structure(h1_candles)
    h1_trend = h1_state.trend

    if h1_trend == 'UNKNOWN':
        return BiasResult(
            h1_trend='UNKNOWN', setup_type='NONE',
            m15_aligned=False, in_valid_zone=False,
            current_price=current_price,
            reason="H1 structure undefined (no BOS detected)",
        )

    # ── M15 structure ──
    m15_candles = get_rates(symbol, TF_M15, config.M15_LOOKBACK)
    if m15_candles is None:
        return BiasResult(
            h1_trend=h1_trend, setup_type='NONE',
            m15_aligned=False, in_valid_zone=False,
            current_price=current_price,
            reason="M15 data unavailable",
        )

    m15_state = analyze_structure(m15_candles)

    # ── Determine setup type ──
    if m15_state.trend == h1_trend:
        setup_type = 'CONTINUATION'
    elif m15_state.trend in ('BULLISH', 'BEARISH'):
        # M15 trend opposes H1 — this is a PULLBACK setup
        setup_type = 'PULLBACK_END'
    else:
        return BiasResult(
            h1_trend=h1_trend, setup_type='NONE',
            m15_aligned=False, in_valid_zone=False,
            current_price=current_price,
            reason=f"M15 trend UNKNOWN, cannot classify",
        )

    # ── Zone check: differs by setup type ──
    if setup_type == 'CONTINUATION':
        # Use M15's own last impulsive leg (existing behavior)
        zone = get_premium_discount(m15_candles, h1_trend)
        zone_source = "M15 impulsive leg"
    else:
        # PULLBACK_END: use M15 swings to find the last leg that was IN THE H1
        # direction (i.e., the leg that got retraced by current pullback)
        zone = get_last_impulsive_leg(m15_candles, h1_trend)
        zone_source = "M15 parent leg (pre-pullback)"

    if zone is None:
        return BiasResult(
            h1_trend=h1_trend, setup_type=setup_type,
            m15_aligned=False, in_valid_zone=False,
            current_price=current_price,
            reason=f"Could not determine zone from {zone_source}",
        )

    leg_low, leg_mid, leg_high = zone

    if h1_trend == 'BULLISH':
        in_zone = is_in_discount(current_price, zone)
        zone_desc = "DISCOUNT" if in_zone else "PREMIUM"
    else:  # BEARISH
        in_zone = is_in_premium(current_price, zone)
        zone_desc = "PREMIUM" if in_zone else "DISCOUNT"

    return BiasResult(
        h1_trend=h1_trend,
        setup_type=setup_type,
        m15_aligned=True,
        in_valid_zone=in_zone,
        current_price=current_price,
        zone_low=leg_low,
        zone_mid=leg_mid,
        zone_high=leg_high,
        reason=(
            f"H1={h1_trend} {setup_type}, price in {zone_desc} of {zone_source} "
            f"(leg {leg_low:.2f}-{leg_high:.2f}, mid {leg_mid:.2f})"
        ),
    )
