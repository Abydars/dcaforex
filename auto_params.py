"""
============================================================
 DCA Forex Bot — Auto Parameter Engine
============================================================
Calculates ALL trading parameters dynamically from:
  • CAPITAL (user input)
  • Live spread (from MT5 tick)
  • Live volatility (from recent candles ATR)

Called once at startup AND on every new candle close so
the bot adapts to changing market conditions in real time.
============================================================
"""

import logging

import MetaTrader5 as mt5
import numpy as np

import config
from mt5_connector import get_mt5_timeframe

logger = logging.getLogger("AutoParams")


def _get_pip_size(symbol_info) -> float:
    """Calculate pip size from symbol digits."""
    if symbol_info.digits in (4, 5):
        return 10 ** -(symbol_info.digits - 1)
    elif symbol_info.digits in (2, 3):
        return 10 ** -(symbol_info.digits - 1)
    else:
        return symbol_info.trade_tick_size * 10


def _get_pip_value(symbol_info, pip_size: float, lot: float) -> float:
    """Dollar value of 1 pip for given lot size."""
    if symbol_info.trade_tick_size == 0:
        return 0.0
    return symbol_info.trade_tick_value * (pip_size / symbol_info.trade_tick_size) * lot


def _calc_atr(rates, period: int = 10) -> float:
    """
    Simple Average True Range over recent candles.
    Returns ATR in raw price units.
    """
    if rates is None or len(rates) < period + 1:
        return 0.0

    trs = []
    for i in range(1, period + 1):
        idx = -(i)
        high = rates[idx]["high"]
        low  = rates[idx]["low"]
        prev_close = rates[idx - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    return float(np.mean(trs))


def recalculate() -> bool:
    """
    Recalculate all trading parameters from live market data.
    Called at startup AND on every new candle.
    Writes results directly into config module globals.

    Parameters auto-derived:
      PIP_SIZE    — symbol property
      PIP_VALUE   — symbol property × lot
      SPREAD_PIPS — live bid/ask spread
      STEP_PIPS   — max(spread×5, ATR×0.3 in pips, 2 pips min)
      MAX_ORDERS  — how many orders CAPITAL can safely support
      EXIT_PIPS   — pips above break-even to close (spread×3 or ATR×0.2)
    """
    symbol = config.SYMBOL
    capital = config.CAPITAL

    info = mt5.symbol_info(symbol)
    if info is None:
        logger.warning(f"Cannot get symbol info for {symbol}. Skipping recalculate.")
        return False

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.warning(f"Cannot get tick for {symbol}. Skipping recalculate.")
        return False

    # ── Base Properties ──────────────────────────────────
    pip_size  = _get_pip_size(info)
    base_lot  = info.volume_min
    pip_value = _get_pip_value(info, pip_size, base_lot)

    if pip_size <= 0 or pip_value <= 0:
        logger.warning("Invalid pip size/value. Skipping recalculate.")
        return False

    # ── Live Spread ───────────────────────────────────────
    spread_price = tick.ask - tick.bid
    spread_pips  = spread_price / pip_size

    # ── Volatility: ATR on last 10 candles ───────────────
    tf = get_mt5_timeframe()
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 20)
    atr_price = _calc_atr(rates, period=10)
    atr_pips  = atr_price / pip_size if pip_size > 0 else 0.0

    # ── Step Distance ─────────────────────────────────────
    # Must clear spread. Scales with ATR so wide-ranging
    # markets get wider steps, quiet markets get tight steps.
    #   • Minimum: max(spread×5, 2 pips)
    #   • Volatility component: ATR × 0.25 (25% of a typical candle)
    step_from_spread = max(spread_pips * 5.0, 2.0)
    step_from_atr    = atr_pips * 0.25 if atr_pips > 0 else step_from_spread
    step_pips        = max(step_from_spread, step_from_atr)
    step_pips        = round(max(step_pips, 2.0), 1)

    # ── Margin per order ─────────────────────────────────
    margin_per_lot = mt5.order_calc_margin(
        mt5.ORDER_TYPE_BUY, symbol, base_lot, tick.ask
    )
    if margin_per_lot is None or margin_per_lot <= 0:
        logger.warning("Cannot calculate margin. Skipping recalculate.")
        return False

    # ── Max Orders Calculation ────────────────────────────
    # Budget:
    #   40% → margin for all orders
    #   40% → worst-case floating loss buffer
    #   20% → safety reserve
    margin_budget = capital * 0.40
    max_from_margin = int(margin_budget / margin_per_lot) if margin_per_lot > 0 else 10
    max_orders = max(3, min(max_from_margin, 30))

    # Shrink until worst-case exposure fits in 80% of capital
    while max_orders > 3:
        # Worst case: n orders × step × n(n-1)/2 pips of floating loss
        worst_float = pip_value * step_pips * max_orders * (max_orders - 1) / 2
        total_margin = max_orders * margin_per_lot
        if total_margin + worst_float <= capital * 0.80:
            break
        max_orders -= 1

    # ── Exit Distance (pips above break-even) ─────────────
    # Must clear spread + a little profit
    # Scales with ATR: volatile market = wider exit
    exit_from_spread = max(spread_pips * 3.0, 2.0)
    exit_from_atr    = atr_pips * 0.10 if atr_pips > 0 else exit_from_spread
    exit_pips        = max(exit_from_spread, exit_from_atr)
    exit_pips        = round(max(exit_pips, 2.0), 1)

    # ── Trailing Stop (activated after exit_pips) ─────────
    # Allows pyramiding to ride the trend until a pullback
    trail_pips = round(max(spread_pips * 2.0, 1.5), 1)

    # ── Check if anything changed significantly ───────────
    prev_step = getattr(config, "STEP_PIPS", 0)
    prev_exit = getattr(config, "EXIT_PIPS", 0)
    prev_max  = getattr(config, "MAX_ORDERS", 0)

    params_changed = (
        abs(step_pips - prev_step) >= 0.5
        or abs(exit_pips - prev_exit) >= 0.5
        or max_orders != prev_max
    )

    # ── Write to Config ───────────────────────────────────
    config.LOT_SIZE    = base_lot
    config.STEP_PIPS   = step_pips
    config.MAX_ORDERS  = max_orders
    config.EXIT_PIPS   = exit_pips
    config.TRAIL_PIPS  = trail_pips
    config.PIP_SIZE    = pip_size
    config.PIP_VALUE   = pip_value
    config.SPREAD_PIPS = round(spread_pips, 1)

    # ── Log (always on first run, then only if changed) ───
    if prev_step == 0 or params_changed:
        logger.info(
            f"⚙️  Params updated | "
            f"Spread: {spread_pips:.1f}p | "
            f"ATR: {atr_pips:.1f}p | "
            f"Step: {step_pips:.1f}p | "
            f"Max: {max_orders} orders | "
            f"Exit: +{exit_pips:.1f}p from BE (Trail: {trail_pips:.1f}p)"
        )
    else:
        logger.debug(
            f"Params stable | Spread: {spread_pips:.1f}p | ATR: {atr_pips:.1f}p"
        )

    return True


# Keep backward-compat alias
def calculate_params() -> bool:
    """Alias for first-run calculation."""
    logger.info("═" * 60)
    logger.info("  INITIAL PARAMETER CALCULATION")
    logger.info("═" * 60)
    result = recalculate()
    if result:
        logger.info(f"  Capital      : ${config.CAPITAL}")
        logger.info(f"  Lot Size     : {config.LOT_SIZE}")
        logger.info(f"  Step Pips    : {config.STEP_PIPS}")
        logger.info(f"  Max Orders   : {config.MAX_ORDERS}")
        logger.info(f"  Exit Pips    : {config.EXIT_PIPS}")
        logger.info(f"  Pip Value    : ${config.PIP_VALUE:.4f}/pip")
        worst = config.PIP_VALUE * config.STEP_PIPS * config.MAX_ORDERS * (config.MAX_ORDERS - 1) / 2
        margin_total = config.MAX_ORDERS * (mt5.order_calc_margin(
            mt5.ORDER_TYPE_BUY, config.SYMBOL, config.LOT_SIZE,
            mt5.symbol_info_tick(config.SYMBOL).ask
        ) or 0)
        logger.info(f"  Worst Exposure: ${worst + margin_total:.2f} / ${config.CAPITAL}")
    logger.info("═" * 60)
    return result
