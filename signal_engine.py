"""
============================================================
 Signal Engine — SMC Setup Orchestrator
============================================================
Combines bias + structure + liquidity + FVG into a final
tradeable signal (or None).

Flow:
  1. Get H1/M15 bias (is trend clear? are we in P/D zone?)
  2. On M5: find recent liquidity sweep in bias direction
  3. On M5: find unmitigated FVG formed after sweep
  4. Calculate entry / SL / TP
  5. Validate RR meets minimum threshold
  6. Return signal or None
============================================================
"""

import logging
from dataclasses import dataclass
from typing import Optional

import config
from bias import BiasResult, get_bias
from liquidity import (
    FVG,
    LiquiditySweep,
    find_entry_fvg_after_sweep,
    find_next_liquidity_target,
    find_recent_sweep,
)
from mt5_connector import get_rates, get_tick, TF_M5

logger = logging.getLogger("SignalEngine")


@dataclass
class TradeSignal:
    direction: str         # 'BUY' or 'SELL'
    entry: float           # Entry price (current market or FVG)
    sl: float              # Stop loss
    tp: float              # Take profit
    rr: float              # Actual RR of this setup
    setup_note: str        # Human-readable rationale
    sweep: LiquiditySweep
    fvg: FVG

    def __repr__(self):
        return (
            f"Signal({self.direction} @{self.entry:.2f} "
            f"SL={self.sl:.2f} TP={self.tp:.2f} RR={self.rr:.2f})"
        )


def _bias_direction_to_smc(bias: str) -> str:
    """Map H1 bias to entry direction and SMC sweep direction."""
    return 'BULLISH' if bias == 'BULLISH' else 'BEARISH'


def generate_signal(symbol: str) -> Optional[TradeSignal]:
    """
    End-to-end signal generation. Returns a TradeSignal if all conditions
    are met, else None with detailed logging of why.
    """
    # ── Step 1: Current price ──
    tick = get_tick(symbol)
    if tick is None:
        logger.debug("No tick available")
        return None
    current_price = (tick.bid + tick.ask) / 2.0

    # ── Step 2: Bias check (H1 + M15) ──
    bias = get_bias(symbol, current_price)
    logger.debug(f"Bias: {bias.reason}")

    if not bias.is_tradeable:
        logger.debug(f"❌ Bias not tradeable: {bias.reason}")
        return None

    direction_smc = _bias_direction_to_smc(bias.h1_trend)
    direction_order = 'BUY' if bias.h1_trend == 'BULLISH' else 'SELL'

    # ── Step 3: M5 candles ──
    m5 = get_rates(symbol, TF_M5, config.M5_LOOKBACK)
    if m5 is None:
        logger.debug("M5 data unavailable")
        return None

    # ── Step 4: Find recent liquidity sweep on M5 ──
    sweep = find_recent_sweep(m5, direction_smc)
    if sweep is None:
        logger.debug(f"❌ No recent {direction_smc} sweep on M5")
        return None

    logger.debug(f"Sweep found: {sweep}")

    # ── Step 5: Find unmitigated FVG after sweep ──
    fvg = find_entry_fvg_after_sweep(m5, direction_smc, sweep)
    if fvg is None:
        logger.debug(f"❌ No unmitigated FVG after sweep")
        return None

    logger.debug(f"FVG found: {fvg}")

    # ── Step 6: Validate FVG is still reachable ──
    # For a BUY setup, price must still be above the FVG top (FVG is below)
    # or within the FVG (retest in progress)
    if direction_smc == 'BULLISH':
        if current_price < fvg.bottom:
            logger.debug(f"❌ Price ${current_price:.2f} already below FVG bottom ${fvg.bottom:.2f}")
            return None
    else:
        if current_price > fvg.top:
            logger.debug(f"❌ Price ${current_price:.2f} already above FVG top ${fvg.top:.2f}")
            return None

    # ── Step 7: Calculate entry, SL, TP ──
    if direction_smc == 'BULLISH':
        # Entry at FVG mid (we'll use market if we're already in/below it)
        # but for this scalping setup, we enter at current price to avoid missing it
        entry = current_price
        sl = sweep.sweep_extreme - config.SL_BUFFER_USD
        # TP: next liquidity target (swing high above)
        target = find_next_liquidity_target(m5, direction_smc, entry)
        if target is None:
            # Fall back to min RR target
            target = entry + (entry - sl) * config.MIN_RR
        tp = target
    else:
        entry = current_price
        sl = sweep.sweep_extreme + config.SL_BUFFER_USD
        target = find_next_liquidity_target(m5, direction_smc, entry)
        if target is None:
            target = entry - (sl - entry) * config.MIN_RR
        tp = target

    # ── Step 8: RR validation ──
    risk_dist = abs(entry - sl)
    reward_dist = abs(tp - entry)
    if risk_dist <= 0:
        logger.debug("Zero risk distance — rejecting")
        return None
    rr = reward_dist / risk_dist

    if rr < config.MIN_RR:
        logger.debug(f"❌ RR {rr:.2f} < min {config.MIN_RR}")
        return None

    # Cap unrealistic RR — target might be too far, use cap
    if rr > config.MAX_RR:
        if direction_smc == 'BULLISH':
            tp = entry + risk_dist * config.MAX_RR
        else:
            tp = entry - risk_dist * config.MAX_RR
        rr = config.MAX_RR

    signal = TradeSignal(
        direction=direction_order,
        entry=entry,
        sl=sl,
        tp=tp,
        rr=rr,
        setup_note=(
            f"H1 {bias.h1_trend} | Swept {sweep.swing.kind} @ ${sweep.swing.price:.2f} "
            f"| FVG {fvg.bottom:.2f}-{fvg.top:.2f} | RR {rr:.2f}"
        ),
        sweep=sweep,
        fvg=fvg,
    )
    logger.info(f"🎯 SIGNAL: {signal} | {signal.setup_note}")
    return signal
