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
import time
from dataclasses import dataclass
from typing import Optional

import config
import ui_state
from bias import BiasResult, get_bias
from filters import _compute_atr
from liquidity import (
    FVG,
    LiquiditySweep,
    find_entry_fvg_after_sweep,
    find_next_liquidity_target,
    find_recent_sweep,
)
from mt5_connector import get_rates, get_tick, TF_M5, TF_M15

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
    ui_state.last_scan_time = time.time()

    # ── Step 1: Current price ──
    tick = get_tick(symbol)
    if tick is None:
        logger.debug("No tick available")
        ui_state.log_rejection("EXECUTION", "No tick data available")
        ui_state.last_scan_result = "REJECTED"
        return None
    current_price = (tick.bid + tick.ask) / 2.0

    # ── Step 2: Bias check (H1 + M15) ──
    bias = get_bias(symbol, current_price)
    logger.debug(f"Bias: {bias.reason}")

    if not bias.is_tradeable:
        if bias.h1_trend == 'UNKNOWN':
            msg = "AWAITING H1 TREND"
        elif not getattr(bias, 'm15_aligned', False):
            msg = "AWAITING M15 ALIGNMENT"
        elif not getattr(bias, 'in_valid_zone', False):
            msg = "AWAITING P/D ZONE"
        else:
            msg = "AWAITING CONDITIONS"
            
        ui_state.last_market_context["sweep"] = msg
        ui_state.last_market_context["fvg"] = msg
        logger.debug(f"❌ Bias not tradeable: {bias.reason}")
        ui_state.log_rejection(
            "BIAS",
            bias.reason,
            details={
                "h1_trend": bias.h1_trend,
                "m15_aligned": getattr(bias, 'm15_aligned', False),
                "in_valid_zone": getattr(bias, 'in_valid_zone', False),
                "price": current_price,
            },
        )
        ui_state.last_scan_result = "REJECTED"
        return None

    direction_smc = _bias_direction_to_smc(bias.h1_trend)
    direction_order = 'BUY' if bias.h1_trend == 'BULLISH' else 'SELL'

    # ── Step 3: M5 candles + ATR ──
    m5 = get_rates(symbol, TF_M5, config.M5_LOOKBACK)
    if m5 is None:
        logger.debug("M5 data unavailable")
        ui_state.log_rejection("EXECUTION", "M5 data unavailable")
        ui_state.last_scan_result = "REJECTED"
        return None
    m5_atr = _compute_atr(m5, period=14)

    # ── Step 4: Find recent liquidity sweep on M5 ──
    sweep = find_recent_sweep(m5, direction_smc, atr=m5_atr)
    if sweep is None:
        ui_state.last_market_context["sweep"] = "NO RECENT SWEEP"
        logger.debug(f"❌ No recent {direction_smc} sweep on M5")
        ui_state.log_rejection(
            "SWEEP",
            f"No recent {direction_smc} liquidity sweep on M5",
            details={
                "direction": direction_smc,
                "m5_atr": round(m5_atr, 2),
                "lookback_bars": config.SWEEP_LOOKBACK_BARS,
            },
        )
        ui_state.last_scan_result = "REJECTED"
        return None

    ui_state.last_market_context["sweep"] = f"SWEPT {sweep.swing.kind} @ {sweep.swing.price:.2f}"
    logger.debug(f"Sweep found: {sweep}")

    # ── Step 5: Find unmitigated FVG after sweep ──
    fvg = find_entry_fvg_after_sweep(m5, direction_smc, sweep, atr=m5_atr)
    if fvg is None:
        ui_state.last_market_context["fvg"] = "WAITING FOR FVG"
        logger.debug(f"❌ No unmitigated FVG after sweep")
        ui_state.log_rejection(
            "FVG",
            f"No unmitigated {direction_smc} FVG after sweep",
            details={
                "sweep_index": sweep.sweep_index,
                "sweep_price": sweep.sweep_extreme,
                "m5_atr": round(m5_atr, 2),
            },
        )
        ui_state.last_scan_result = "REJECTED"
        return None

    ui_state.last_market_context["fvg"] = f"FVG {fvg.bottom:.2f}-{fvg.top:.2f}"
    logger.debug(f"FVG found: {fvg}")

    # ── Step 6: Validate FVG is still reachable ──
    # For a BUY setup, price must still be above the FVG top (FVG is below)
    # or within the FVG (retest in progress)
    if direction_smc == 'BULLISH':
        if current_price < fvg.bottom:
            logger.debug(f"❌ Price ${current_price:.2f} already below FVG bottom ${fvg.bottom:.2f}")
            ui_state.log_rejection(
                "FVG",
                f"Price ${current_price:.2f} already below FVG bottom ${fvg.bottom:.2f}",
                details={"price": current_price, "fvg_bottom": fvg.bottom},
            )
            ui_state.last_scan_result = "REJECTED"
            return None
    else:
        if current_price > fvg.top:
            logger.debug(f"❌ Price ${current_price:.2f} already above FVG top ${fvg.top:.2f}")
            ui_state.log_rejection(
                "FVG",
                f"Price ${current_price:.2f} already above FVG top ${fvg.top:.2f}",
                details={"price": current_price, "fvg_top": fvg.top},
            )
            ui_state.last_scan_result = "REJECTED"
            return None

    # ── Step 7: Calculate entry, SL, TP ──
    atr_buffer = m5_atr * config.SL_BUFFER_ATR_FRAC if m5_atr > 0 else 0.0
    sl_buffer = max(config.SL_BUFFER_FLOOR_USD, atr_buffer)

    if direction_smc == 'BULLISH':
        # Entry at FVG mid (we'll use market if we're already in/below it)
        # but for this scalping setup, we enter at current price to avoid missing it
        entry = current_price
        sl = sweep.sweep_extreme - sl_buffer
        # TP: next liquidity target (swing high above)
        target = find_next_liquidity_target(m5, direction_smc, entry)
        if target is None:
            # Fall back to min RR target
            target = entry + (entry - sl) * config.MIN_RR
        tp = target
    else:
        entry = current_price
        sl = sweep.sweep_extreme + sl_buffer
        target = find_next_liquidity_target(m5, direction_smc, entry)
        if target is None:
            target = entry - (sl - entry) * config.MIN_RR
        tp = target

    # ── Step 7.5: Validate SL/TP directionality ──
    # Confirm SL and TP are on the correct sides of entry.
    # If not, the sweep likely failed (price continued past sweep extreme)
    # and the setup is invalidated.
    if direction_smc == 'BULLISH':
        if sl >= entry:
            ui_state.log_rejection(
                "STRUCTURE",
                f"BUY setup invalid: SL ({sl:.2f}) >= entry ({entry:.2f}). "
                f"Price moved past sweep extreme — failed sweep.",
                details={
                    "direction": "BUY",
                    "entry": round(entry, 2),
                    "sl": round(sl, 2),
                    "sweep_extreme": round(sweep.sweep_extreme, 2),
                },
            )
            ui_state.last_scan_result = "REJECTED"
            return None
        if tp <= entry:
            ui_state.log_rejection(
                "STRUCTURE",
                f"BUY setup invalid: TP ({tp:.2f}) <= entry ({entry:.2f}). "
                f"No valid liquidity target above entry.",
                details={
                    "direction": "BUY",
                    "entry": round(entry, 2),
                    "tp": round(tp, 2),
                },
            )
            ui_state.last_scan_result = "REJECTED"
            return None
    else:  # BEARISH
        if sl <= entry:
            ui_state.log_rejection(
                "STRUCTURE",
                f"SELL setup invalid: SL ({sl:.2f}) <= entry ({entry:.2f}). "
                f"Price moved past sweep extreme — failed sweep.",
                details={
                    "direction": "SELL",
                    "entry": round(entry, 2),
                    "sl": round(sl, 2),
                    "sweep_extreme": round(sweep.sweep_extreme, 2),
                },
            )
            ui_state.last_scan_result = "REJECTED"
            return None
        if tp >= entry:
            ui_state.log_rejection(
                "STRUCTURE",
                f"SELL setup invalid: TP ({tp:.2f}) >= entry ({entry:.2f}). "
                f"No valid liquidity target below entry.",
                details={
                    "direction": "SELL",
                    "entry": round(entry, 2),
                    "tp": round(tp, 2),
                },
            )
            ui_state.last_scan_result = "REJECTED"
            return None

    # ── Step 8: RR validation ──
    risk_dist = abs(entry - sl)
    reward_dist = abs(tp - entry)
    if risk_dist <= 0:
        logger.debug("Zero risk distance — rejecting")
        ui_state.log_rejection("RR", "Zero risk distance — rejecting", details={"entry": entry, "sl": sl})
        ui_state.last_scan_result = "REJECTED"
        return None
    rr = reward_dist / risk_dist

    if rr < config.MIN_RR:
        logger.debug(f"❌ RR {rr:.2f} < min {config.MIN_RR}")
        ui_state.log_rejection(
            "RR",
            f"RR {rr:.2f} below minimum {config.MIN_RR}",
            details={
                "rr": round(rr, 2),
                "min_rr": config.MIN_RR,
                "entry": round(entry, 2),
                "sl": round(sl, 2),
                "tp": round(tp, 2),
            },
        )
        ui_state.last_scan_result = "REJECTED"
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
            f"| FVG {fvg.bottom:.2f}-{fvg.top:.2f} | RR {rr:.2f} "
            f"| M5_ATR=${m5_atr:.2f} | SL buf ${sl_buffer:.2f}"
        ),
        sweep=sweep,
        fvg=fvg,
    )
    logger.info(f"🎯 SIGNAL: {signal} | {signal.setup_note}")
    ui_state.last_scan_result = "SIGNAL"
    return signal
