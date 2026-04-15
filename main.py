"""
============================================================
 DCA Forex Bot — Main Entry Point
============================================================
Orchestrates the full trading lifecycle:

  1. Connect to MT5 / Exness
  2. Wait for a candlestick entry signal
  3. Open the initial position
  4. Monitor price and layer DCA orders when step distance is hit
  5. Monitor basket profit → close all when target is reached
  6. Monitor equity → emergency close on max drawdown

The main loop is designed for speed: profit checks run every
100ms, while signal checks only happen once per new candle.
============================================================
"""

import logging
import signal
import sys
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5

import config
from execution import (
    close_all_positions,
    get_basket_positions,
    get_basket_profit,
    place_dca_order,
    place_entry_order,
    _get_price,
    _pip_size,
)
from mt5_connector import get_mt5_timeframe, initialize_mt5, shutdown_mt5
from signal_engine import get_entry_signal

logger = logging.getLogger("DCA_Bot")

# ─── Global State ───────────────────────────────────────────
_running = True
_current_direction: str | None = None  # "BUY" or "SELL" while in a basket
_dca_layer: int = 0                     # Number of DCA layers placed
_last_dca_price: float = 0.0           # Price at which last DCA was triggered
_last_candle_time: int = 0             # Timestamp of the last processed candle


def _signal_handler(sig, frame):
    """Handle Ctrl+C gracefully."""
    global _running
    logger.info("Interrupt received. Shutting down after current cycle...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ─── Time Filter ────────────────────────────────────────────
def _is_within_trading_hours() -> bool:
    """
    Check if the current server time falls within the configured
    trading window. Uses MT5 server time to avoid timezone issues.
    """
    # Prefer MT5 server time; fallback to local UTC
    tick = mt5.symbol_info_tick(config.SYMBOL)
    if tick and tick.time:
        server_dt = datetime.fromtimestamp(tick.time, tz=timezone.utc)
    else:
        server_dt = datetime.now(tz=timezone.utc)

    now_time = server_dt.time()

    # Handle overnight windows (e.g., 22:00 → 06:00)
    if config.TRADING_START <= config.TRADING_END:
        return config.TRADING_START <= now_time <= config.TRADING_END
    else:
        return now_time >= config.TRADING_START or now_time <= config.TRADING_END


# ─── Drawdown Guard ────────────────────────────────────────
def _check_drawdown_guard() -> bool:
    """
    Returns True if drawdown exceeds MAX_DRAWDOWN_PCT → trigger emergency close.
    """
    if config.SESSION_START_EQUITY <= 0:
        return False

    account = mt5.account_info()
    if account is None:
        return False

    drawdown_pct = (
        (config.SESSION_START_EQUITY - account.equity)
        / config.SESSION_START_EQUITY
        * 100.0
    )

    if drawdown_pct >= config.MAX_DRAWDOWN_PCT:
        logger.critical(
            f"🚨 DRAWDOWN GUARD TRIGGERED! "
            f"Drawdown: {drawdown_pct:.2f}% ≥ {config.MAX_DRAWDOWN_PCT}% limit. "
            f"Equity: {account.equity:.2f} (Session Start: {config.SESSION_START_EQUITY:.2f})"
        )
        return True

    return False


# ─── DCA Monitor ───────────────────────────────────────────
def _check_dca_trigger():
    """
    If we have an active basket and price has moved STEP_DISTANCE_PIPS
    against us since the last entry, place the next DCA layer.
    """
    global _dca_layer, _last_dca_price, _current_direction

    if _current_direction is None:
        return

    positions = get_basket_positions()
    if not positions:
        # Basket was externally closed or lost
        _reset_state()
        return

    # Check if we've hit the max order cap
    total_orders = len(positions)
    if total_orders >= config.MAX_ORDERS:
        return

    # Get current price
    current_price = _get_price(config.SYMBOL, _current_direction)
    if current_price <= 0:
        return

    pip = _pip_size(config.SYMBOL)
    step_distance = config.STEP_DISTANCE_PIPS * pip

    # Determine if price moved against us by step distance
    if _current_direction == "BUY":
        # For BUY basket, price must DROP by step_distance from last DCA entry
        price_delta = _last_dca_price - current_price
    else:
        # For SELL basket, price must RISE by step_distance from last DCA entry
        price_delta = current_price - _last_dca_price

    if price_delta >= step_distance:
        _dca_layer += 1
        logger.info(
            f"📉 DCA Trigger! Price moved {price_delta / pip:.1f} pips against us. "
            f"Placing Layer {_dca_layer} (total orders will be {total_orders + 1})"
        )

        if place_dca_order(_current_direction, _dca_layer):
            _last_dca_price = current_price
            logger.info(f"DCA Layer {_dca_layer} placed ✓ | New anchor: {current_price}")
        else:
            _dca_layer -= 1  # Rollback on failure
            logger.error(f"DCA Layer {_dca_layer + 1} FAILED to place.")


# ─── Basket Profit Monitor ─────────────────────────────────
def _check_basket_target() -> bool:
    """
    Check if the aggregate basket profit has reached the target.
    Returns True if basket was closed.
    """
    if _current_direction is None:
        return False

    profit = get_basket_profit()

    # Log periodically (every ~5 seconds, based on 100ms loop = every 50 cycles)
    # This is handled in the main loop via a counter

    if profit >= config.TARGET_PROFIT_USD:
        logger.info(
            f"🎯 TARGET PROFIT REACHED! "
            f"Basket P/L: ${profit:+.2f} ≥ ${config.TARGET_PROFIT_USD}"
        )
        close_all_positions(reason="TARGET_HIT")
        return True

    return False


# ─── State Management ──────────────────────────────────────
def _reset_state():
    """Clear all basket state after positions are closed."""
    global _current_direction, _dca_layer, _last_dca_price, _last_candle_time
    _current_direction = None
    _dca_layer = 0
    _last_dca_price = 0.0
    _last_candle_time = 0
    logger.info("Bot state reset — ready for next signal.")


# ─── New Candle Detector ───────────────────────────────────
def _is_new_candle() -> bool:
    """
    Check if a new candle has formed since the last check.
    Prevents scanning the same bar multiple times.
    """
    global _last_candle_time

    tf = get_mt5_timeframe()
    rates = mt5.copy_rates_from_pos(config.SYMBOL, tf, 0, 1)

    if rates is None or len(rates) == 0:
        return False

    candle_time = int(rates[0]["time"])

    if candle_time != _last_candle_time:
        _last_candle_time = candle_time
        return True

    return False


# ─── Main Loop ─────────────────────────────────────────────
def main():
    global _current_direction, _last_dca_price

    # ── Startup ──
    config.print_config()
    initialize_mt5()

    logger.info("═" * 60)
    logger.info("  DCA FOREX BOT — LIVE")
    logger.info("═" * 60)
    logger.info(f"Session Start Equity: ${config.SESSION_START_EQUITY:.2f}")
    logger.info(
        f"Emergency Stop: ${config.SESSION_START_EQUITY * (1 - config.MAX_DRAWDOWN_PCT / 100):.2f} "
        f"({config.MAX_DRAWDOWN_PCT}% drawdown)"
    )

    profit_log_counter = 0

    try:
        while _running:
            # ──────────────────────────────────────────────
            # PHASE 0: Drawdown Guard (runs EVERY cycle)
            # ──────────────────────────────────────────────
            if _check_drawdown_guard():
                close_all_positions(reason="DRAWDOWN_GUARD")
                _reset_state()
                logger.critical(
                    "🛑 Bot halted due to max drawdown. "
                    "Manual restart required."
                )
                break

            # ──────────────────────────────────────────────
            # PHASE 1: Active Basket Management
            # ──────────────────────────────────────────────
            if _current_direction is not None:
                # Check basket positions still exist
                positions = get_basket_positions()
                if not positions:
                    logger.info("Basket positions no longer exist (closed externally?).")
                    _reset_state()
                    time.sleep(0.1)
                    continue

                # Fast profit check (every 100ms)
                if _check_basket_target():
                    _reset_state()
                    time.sleep(0.5)  # Brief pause before next cycle
                    continue

                # DCA trigger check
                _check_dca_trigger()

                # Periodic status log (every ~5 seconds)
                profit_log_counter += 1
                if profit_log_counter >= 50:
                    profit = get_basket_profit()
                    num_pos = len(positions)
                    account = mt5.account_info()
                    eq = account.equity if account else 0
                    logger.info(
                        f"📊 Basket: {num_pos} orders | "
                        f"P/L: ${profit:+.2f} / ${config.TARGET_PROFIT_USD} target | "
                        f"Equity: ${eq:.2f}"
                    )
                    profit_log_counter = 0

                # Fast loop: 100ms for responsive profit monitoring
                time.sleep(0.1)
                continue

            # ──────────────────────────────────────────────
            # PHASE 2: Wait for Entry Signal (no active basket)
            # ──────────────────────────────────────────────

            # Only scan on new candle formation
            if not _is_new_candle():
                time.sleep(1.0)  # Slower loop when waiting for signals
                continue

            # Trading hours filter
            if not _is_within_trading_hours():
                logger.debug(
                    f"Outside trading hours ({config.TRADING_START}-{config.TRADING_END}). Waiting..."
                )
                time.sleep(5.0)
                continue

            # Scan for candlestick pattern
            direction = get_entry_signal()
            if direction is None:
                logger.debug("No entry pattern on latest closed candle.")
                continue

            # ──────────────────────────────────────────────
            # PHASE 3: Execute Initial Entry
            # ──────────────────────────────────────────────
            logger.info(f"🚀 ENTRY SIGNAL: {direction} on {config.SYMBOL}")

            if place_entry_order(direction):
                _current_direction = direction
                _last_dca_price = _get_price(config.SYMBOL, direction)
                _dca_layer = 0
                profit_log_counter = 0
                logger.info(
                    f"✅ Basket initiated: {direction} @ {_last_dca_price} | "
                    f"DCA step: {config.STEP_DISTANCE_PIPS} pips | "
                    f"Target: ${config.TARGET_PROFIT_USD}"
                )
            else:
                logger.error("Entry order FAILED. Will retry on next signal.")

    except Exception as e:
        logger.exception(f"Unhandled exception in main loop: {e}")
        # Emergency close on crash
        try:
            close_all_positions(reason="BOT_CRASH")
        except Exception:
            logger.exception("Failed to emergency-close positions during crash handler.")

    finally:
        shutdown_mt5()
        logger.info("DCA Forex Bot terminated.")


if __name__ == "__main__":
    main()
