"""
============================================================
 DCA Forex Bot — Main Entry Point
============================================================
Fully automated:
  1. Connect to MT5
  2. Auto-calculate lot, steps, max orders from CAPITAL
  3. Wait for candle signal → enter
  4. Rapid-fire DCA when price moves against
  5. Smart exit: close when price crosses break-even + EXIT_PIPS
  6. Safety: drawdown guard + basket stop loss
============================================================
"""

import logging
import signal
import sys
import time
from datetime import datetime, timezone

import MetaTrader5 as mt5

import config
from auto_params import calculate_params, recalculate
from execution import (
    close_all_positions,
    get_basket_positions,
    get_basket_profit,
    get_basket_volume,
    get_breakeven_price,
    place_dca_order,
    place_entry_order,
    _get_price,
)
from mt5_connector import get_mt5_timeframe, initialize_mt5, shutdown_mt5
from signal_engine import get_entry_signal

logger = logging.getLogger("DCA_Bot")

# ─── State ──────────────────────────────────────────────────
_running = True
_current_direction: str | None = None
_dca_layer: int = 0
_pyramid_layer: int = 0
_last_dca_price: float = 0.0
_last_pyramid_price: float = 0.0
_last_candle_time: int = 0
_trailing_active: bool = False
_trailing_extreme_price: float = 0.0


def _signal_handler(sig, frame):
    global _running
    logger.info("Interrupt received. Shutting down...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ─── Trading Hours Check ───────────────────────────────────
def _is_within_trading_hours() -> bool:
    tick = mt5.symbol_info_tick(config.SYMBOL)
    if tick and tick.time:
        server_dt = datetime.fromtimestamp(tick.time, tz=timezone.utc)
    else:
        server_dt = datetime.now(tz=timezone.utc)

    now_time = server_dt.time()

    if config.TRADING_START <= config.TRADING_END:
        return config.TRADING_START <= now_time <= config.TRADING_END
    else:
        return now_time >= config.TRADING_START or now_time <= config.TRADING_END


# ─── Drawdown Guard ───────────────────────────────────────
def _check_drawdown_guard() -> bool:
    """Emergency stop if loss exceeds MAX_DRAWDOWN_PCT of CAPITAL."""
    account = mt5.account_info()
    if account is None:
        return False

    loss = config.SESSION_START_EQUITY - account.equity
    max_loss = config.CAPITAL * (config.MAX_DRAWDOWN_PCT / 100.0)

    if loss >= max_loss:
        logger.critical(
            f"🚨 DRAWDOWN GUARD! Loss: ${loss:.2f} ≥ ${max_loss:.2f} "
            f"({config.MAX_DRAWDOWN_PCT}% of ${config.CAPITAL} capital)"
        )
        return True
    return False


# ─── Smart Exit Check ─────────────────────────────────────
def _check_smart_exit() -> bool:
    """
    Close basket with a Trailing Stop.
    First, wait for price to cross break-even + EXIT_PIPS.
    Once crossed, activate trailing mode and track peak price.
    If price pulls back by TRAIL_PIPS from peak, close all.
    """
    global _trailing_active, _trailing_extreme_price

    if _current_direction is None:
        return False

    positions = get_basket_positions()
    if not positions:
        return False

    breakeven = get_breakeven_price()
    if breakeven <= 0:
        return False

    current_price = _get_price(config.SYMBOL, _current_direction)
    if current_price <= 0:
        return False

    exit_distance = config.EXIT_PIPS * config.PIP_SIZE
    trail_distance = getattr(config, 'TRAIL_PIPS', 1.5) * config.PIP_SIZE

    if _current_direction == "BUY":
        # 1. Check if we reached activation point
        if not _trailing_active and current_price >= breakeven + exit_distance:
            _trailing_active = True
            _trailing_extreme_price = current_price
            logger.info(f"🟢 Trailing Stop ACTIVATED! Peak: {current_price:.5f}")

        # 2. Trail the stop
        if _trailing_active:
            if current_price > _trailing_extreme_price:
                _trailing_extreme_price = current_price  # Update peak

            if current_price <= _trailing_extreme_price - trail_distance:
                profit = get_basket_profit()
                logger.info(
                    f"🎯 TRAILING EXIT! Pulled back from {_trailing_extreme_price:.5f} "
                    f"to {current_price:.5f} | P/L: ${profit:+.2f}"
                )
                close_all_positions(reason="TRAIL_EXIT")
                return True

    else:
        # For SELL
        if not _trailing_active and current_price <= breakeven - exit_distance:
            _trailing_active = True
            _trailing_extreme_price = current_price
            logger.info(f"🔴 Trailing Stop ACTIVATED! Peak: {current_price:.5f}")

        if _trailing_active:
            if current_price < _trailing_extreme_price:
                _trailing_extreme_price = current_price  # Update peak

            if current_price >= _trailing_extreme_price + trail_distance:
                profit = get_basket_profit()
                logger.info(
                    f"🎯 TRAILING EXIT! Pulled back from {_trailing_extreme_price:.5f} "
                    f"to {current_price:.5f} | P/L: ${profit:+.2f}"
                )
                close_all_positions(reason="TRAIL_EXIT")
                return True

    return False


# ─── Basket Stop Loss ─────────────────────────────────────
def _check_basket_stop_loss() -> bool:
    """
    When all DCA layers are exhausted and basket is in loss,
    cut losses at 50% of remaining capital safety buffer.
    """
    if _current_direction is None:
        return False

    positions = get_basket_positions()
    if len(positions) < config.MAX_ORDERS:
        return False  # Still have DCA layers available

    profit = get_basket_profit()
    # Max basket loss = 30% of capital
    max_basket_loss = config.CAPITAL * 0.30

    if profit <= -max_basket_loss:
        logger.warning(
            f"🛑 BASKET STOP LOSS! All {config.MAX_ORDERS} DCA layers used. "
            f"Loss: ${profit:.2f} exceeds -${max_basket_loss:.2f} limit."
        )
        close_all_positions(reason="BASKET_SL")
        return True

    return False


# ─── DCA & Pyramid Trigger ─────────────────────────────────
def _check_order_triggers():
    """
    Check if price moved STEP_PIPS against us (DCA)
    or STEP_PIPS in our favor (Pyramid).
    """
    global _dca_layer, _pyramid_layer, _last_dca_price, _last_pyramid_price

    if _current_direction is None:
        return

    positions = get_basket_positions()
    if not positions or len(positions) >= config.MAX_ORDERS:
        return

    current_price = _get_price(config.SYMBOL, _current_direction)
    if current_price <= 0:
        return

    step_distance = config.STEP_PIPS * config.PIP_SIZE

    if _current_direction == "BUY":
        dca_delta = _last_dca_price - current_price
        pyr_delta = current_price - _last_pyramid_price
    else:
        dca_delta = current_price - _last_dca_price
        pyr_delta = _last_pyramid_price - current_price

    # 1. DCA (Against us)
    if dca_delta >= step_distance:
        _dca_layer += 1
        pips_moved = dca_delta / config.PIP_SIZE
        logger.info(
            f"📉 DCA Trigger! {pips_moved:.1f} pips against "
            f"(Layer {_dca_layer} / Total {len(positions) + 1})"
        )
        if place_dca_order(_current_direction, _dca_layer):
            _last_dca_price = current_price
        else:
            _dca_layer -= 1

    # 2. Pyramid (In our favor)
    elif pyr_delta >= step_distance:
        _pyramid_layer += 1
        pips_moved = pyr_delta / config.PIP_SIZE
        logger.info(
            f"🚀 PYRAMID Trigger! {pips_moved:.1f} pips in favor "
            f"(Layer {_pyramid_layer} / Total {len(positions) + 1})"
        )
        if place_dca_order(_current_direction, _dca_layer + _pyramid_layer):
            _last_pyramid_price = current_price
        else:
            _pyramid_layer -= 1


# ─── State Reset ─────────────────────────────────────────
def _reset_state():
    global _current_direction, _dca_layer, _pyramid_layer
    global _last_dca_price, _last_pyramid_price, _last_candle_time
    global _trailing_active, _trailing_extreme_price

    _current_direction = None
    _dca_layer = 0
    _pyramid_layer = 0
    _last_dca_price = 0.0
    _last_pyramid_price = 0.0
    _last_candle_time = 0
    _trailing_active = False
    _trailing_extreme_price = 0.0
    logger.info("State reset — ready for next signal.")


# ─── New Candle Check ────────────────────────────────────
def _is_new_candle() -> bool:
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


# ─── Main Loop ───────────────────────────────────────────
def main():
    global _current_direction, _last_dca_price

    # ── Startup ──
    initialize_mt5()

    # Auto-calculate all parameters
    if not calculate_params():
        logger.critical("Auto-parameter calculation failed. Exiting.")
        shutdown_mt5()
        sys.exit(1)

    logger.info("═" * 60)
    logger.info("  DCA FOREX BOT — LIVE (AUTO MODE)")
    logger.info("═" * 60)
    logger.info(f"  Capital: ${config.CAPITAL} | Symbol: {config.SYMBOL}")
    logger.info(f"  Lot: {config.LOT_SIZE} | Step: {config.STEP_PIPS} pips | Max: {config.MAX_ORDERS} orders")
    logger.info(f"  Exit: {config.EXIT_PIPS} pips above break-even")
    logger.info(f"  Session Equity: ${config.SESSION_START_EQUITY:.2f}")
    logger.info("═" * 60)

    profit_log_counter = 0

    try:
        while _running:
            # ── Drawdown Guard (every cycle) ──
            if _check_drawdown_guard():
                close_all_positions(reason="DRAWDOWN_GUARD")
                _reset_state()
                logger.critical("🛑 Bot halted — max drawdown hit.")
                break

            # ── Active Basket Management ──
            if _current_direction is not None:
                positions = get_basket_positions()
                if not positions:
                    _reset_state()
                    time.sleep(0.01)
                    continue

                # Smart exit (break-even + EXIT_PIPS)
                if _check_smart_exit():
                    _reset_state()
                    time.sleep(0.1)
                    continue

                # Basket stop loss (all DCA used + deep loss)
                if _check_basket_stop_loss():
                    _reset_state()
                    time.sleep(0.1)
                    continue

                # DCA and Pyramid trigger
                _check_order_triggers()

                # Status log every ~5 seconds
                profit_log_counter += 1
                if profit_log_counter >= 500:
                    profit = get_basket_profit()
                    num_pos = len(positions)
                    total_vol = get_basket_volume()
                    breakeven = get_breakeven_price()
                    current = _get_price(config.SYMBOL, _current_direction)
                    account = mt5.account_info()
                    eq = account.equity if account else 0

                    if _current_direction == "BUY":
                        pips_from_be = (current - breakeven) / config.PIP_SIZE if config.PIP_SIZE > 0 else 0
                    else:
                        pips_from_be = (breakeven - current) / config.PIP_SIZE if config.PIP_SIZE > 0 else 0

                    logger.info(
                        f"📊 {num_pos} orders ({total_vol:.2f} lots) | "
                        f"P/L: ${profit:+.2f} | "
                        f"BE: {breakeven:.5f} ({pips_from_be:+.1f} pips) | "
                        f"{'TRAIL ACTIVE Peak: ' + f'{_trailing_extreme_price:.5f}' if _trailing_active else f'Exit at: +{config.EXIT_PIPS:.1f} pips'} | "
                        f"Eq: ${eq:.2f}"
                    )
                    profit_log_counter = 0

                # 10ms ultra-fast loop
                time.sleep(0.01)
                continue

            # ── Wait for Entry Signal ──
            if not _is_new_candle():
                time.sleep(1.0)
                continue

            if not _is_within_trading_hours():
                time.sleep(5.0)
                continue

            # Scan all symbols for an entry signal
            best_direction = None
            best_symbol = None
            highest_surge = 0.0

            for sym in config.SYMBOLS:
                signal_data = get_entry_signal(target_symbol=sym)
                if signal_data is not None:
                    surge = signal_data.get("surge_ratio", 1.0)
                    if surge > highest_surge:
                        highest_surge = surge
                        best_direction = signal_data.get("direction")
                        best_symbol = sym

            if best_direction is None:
                continue

            # ── Pre-Entry Preparation ──
            # Lock the bot to this newly found symbol
            config.SYMBOL = best_symbol

            # Recalculate lot sizes, steps, margins dynamically for THIS exact symbol
            recalculate()

            # ── Execute Entry ──
            logger.info(f"🚀 ENTRY: {best_direction} {config.SYMBOL}")

            if place_entry_order(best_direction):
                _current_direction = best_direction
                _last_dca_price = _get_price(config.SYMBOL, best_direction)
                _last_pyramid_price = _last_dca_price
                _dca_layer = 0
                _pyramid_layer = 0
                _trailing_active = False
                profit_log_counter = 0
                logger.info(
                    f"✅ Basket started: {best_direction} @ {_last_dca_price} | "
                    f"Layer gap: {config.STEP_PIPS} pips | "
                    f"Trail mode: +{config.EXIT_PIPS} pips from BE"
                )
            else:
                logger.error("Entry FAILED. Waiting for next signal.")

    except Exception as e:
        logger.exception(f"Unhandled exception: {e}")
        try:
            close_all_positions(reason="BOT_CRASH")
        except Exception:
            logger.exception("Failed emergency close during crash.")
    finally:
        shutdown_mt5()
        logger.info("DCA Bot terminated.")


if __name__ == "__main__":
    main()
