"""
============================================================
 DCA Forex Bot — Main Entry Point (Parallel Edition)
============================================================
Fully automated:
  1. Connect to MT5
  2. Auto-calculate lot, steps, max orders per symbol
  3. Wait for candle signal → enter
  4. Rapid-fire DCA and Smart Trail Exit
  5. Multi-Basket concurrency (if PARALLEL_TRADING=True)
  6. Global Equity Drawdown Guard
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
    _get_close_price,
)
from mt5_connector import get_mt5_timeframe, initialize_mt5, shutdown_mt5
from signal_engine import get_entry_signal
import dashboard
import threading
import signal_state

logger = logging.getLogger("DCA_Bot")

# ─── State ──────────────────────────────────────────────────
_running = True

class BasketState:
    def __init__(self):
        self.direction = None
        self.dca_layer = 0
        self.pyramid_layer = 0
        self.last_dca_price = 0.0
        self.last_pyramid_price = 0.0
        self.trailing_active = False
        self.trailing_extreme_price = 0.0
        self.initial_surge = 0.0
        self.params = {}

basket_states: dict[str, BasketState] = {}
_last_candle_times: dict[str, int] = {}
global_sweep_trigger_time: float = 0.0
_last_log_time = 0.0

def _adopt_orphan_baskets():
    """
    Search MT5 for existing open positions that match our MAGIC_NUMBER.
    If found, reconstruct the BasketState so the bot can continue managing them.
    """
    from auto_params import calculate_params
    
    positions = mt5.positions_get(magic=config.MAGIC_NUMBER)
    if not positions:
        return

    # Group by symbol
    grouped = {}
    for pos in positions:
        if pos.symbol not in grouped:
            grouped[pos.symbol] = []
        grouped[pos.symbol].append(pos)

    adoptions = 0
    for sym, pos_list in grouped.items():
        if sym not in config.SYMBOLS:
            continue
            
        direction = "BUY" if pos_list[0].type == mt5.ORDER_TYPE_BUY else "SELL"
        
        # Sort positions chronologically to identify the most recent / worst entry
        sorted_pos = sorted(pos_list, key=lambda x: x.ticket)
        last_pos = sorted_pos[-1]
        
        state = BasketState()
        state.direction = direction
        state.dca_layer = len(pos_list) - 1 # e.g. 1 position = layer 0, 3 positions = 2 layers
        state.last_dca_price = last_pos.price_open
        state.params = calculate_params(sym)
        
        basket_states[sym] = state
        adoptions += 1
        
        logger.info(
            f"♻️ [State Recovery] Adopted {sym} {direction} basket | "
            f"DCA Layers: {state.dca_layer} | Total Positions: {len(pos_list)}"
        )
        
    if adoptions > 0:
        logger.info(f"✅ Successfully reclaimed {adoptions} active baskets from previous sessions.")


def _signal_handler(sig, frame):
    global _running
    logger.info("Interrupt received. Shutting down...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def _parse_time(raw: str):
    from datetime import time as dt_time
    parts = raw.split(":")
    return dt_time(hour=int(parts[0]), minute=int(parts[1]))

def _get_active_time_range_index() -> int:
    if not signal_state.session_time_ranges:
        return 0  # Dummy index for 24/7 if empty

    now_time = datetime.now(tz=timezone.utc).time()

    for idx, tr in enumerate(signal_state.session_time_ranges):
        s_t = _parse_time(tr["start"])
        e_t = _parse_time(tr["end"])
        
        if s_t <= e_t:
            if s_t <= now_time <= e_t:
                return idx
        else:
            if now_time >= s_t or now_time <= e_t:
                return idx
                
    return -1

def _is_within_trading_hours(symbol: str) -> bool:
    return _get_active_time_range_index() >= 0


# ─── Session Guard ───────────────────────────────────────
def _check_session_limits() -> str:
    """Checks if the dynamic User-Session Hit TP or SL."""
    if not signal_state.session_active:
        return ""
        
    account = mt5.account_info()
    if account is None:
        return ""

    current_pnl = account.equity - signal_state.session_start_equity
    realized_pnl = account.balance - signal_state.session_start_balance
    signal_state.session_current_pnl = current_pnl
    signal_state.session_realized_pnl = realized_pnl

    if current_pnl >= signal_state.session_target_profit:
        return "TAKE_PROFIT_HIT"
    if current_pnl <= -signal_state.session_stop_loss:
        return "STOP_LOSS_HIT"
        
    return ""


# ─── Smart Exit Check ─────────────────────────────────────
def _check_smart_exit(symbol: str, state: BasketState) -> bool:
    """Close basket with a Trailing Stop for a specific symbol."""
    if state.direction is None:
        return False

    positions = get_basket_positions(symbol)
    if not positions:
        return False

    breakeven = get_breakeven_price(symbol)
    if breakeven <= 0:
        return False

    current_price = _get_close_price(symbol, state.direction)
    if current_price <= 0:
        return False

    pip_size = state.params.get("PIP_SIZE", config.PIP_SIZE)
    exit_pips = state.params.get("EXIT_PIPS", config.EXIT_PIPS)
    trail_pips = state.params.get("TRAIL_PIPS", getattr(config, 'TRAIL_PIPS', 1.5))

    exit_distance = exit_pips * pip_size
    trail_distance = trail_pips * pip_size

    if state.direction == "BUY":
        # 1. Check if we reached activation point
        if not state.trailing_active and current_price >= breakeven + exit_distance:
            state.trailing_active = True
            state.trailing_extreme_price = current_price
            logger.info(f"🟢 [{symbol}] Trailing Stop ACTIVATED! Peak: {current_price:.5f}")

        # 2. Trail the stop
        if state.trailing_active:
            if current_price > state.trailing_extreme_price:
                state.trailing_extreme_price = current_price  # Update peak

            if current_price <= state.trailing_extreme_price - trail_distance:
                profit = get_basket_profit(symbol)
                logger.info(
                    f"🎯 [{symbol}] TRAILING EXIT! Pulled back from {state.trailing_extreme_price:.5f} "
                    f"to {current_price:.5f} | P/L: ${profit:+.2f}"
                )
                close_all_positions(symbol, reason="TRAIL_EXIT")
                return True

    else:
        # For SELL
        if not state.trailing_active and current_price <= breakeven - exit_distance:
            state.trailing_active = True
            state.trailing_extreme_price = current_price
            logger.info(f"🔴 [{symbol}] Trailing Stop ACTIVATED! Peak: {current_price:.5f}")

        if state.trailing_active:
            if current_price < state.trailing_extreme_price:
                state.trailing_extreme_price = current_price  # Update peak

            if current_price >= state.trailing_extreme_price + trail_distance:
                profit = get_basket_profit(symbol)
                logger.info(
                    f"🎯 [{symbol}] TRAILING EXIT! Pulled back from {state.trailing_extreme_price:.5f} "
                    f"to {current_price:.5f} | P/L: ${profit:+.2f}"
                )
                close_all_positions(symbol, reason="TRAIL_EXIT")
                return True

    return False


# ─── Basket Stop Loss ─────────────────────────────────────
def _check_basket_stop_loss(symbol: str, state: BasketState) -> bool:
    """When all DCA layers exhausted and basket in deep loss."""
    if state.direction is None:
        return False

    positions = get_basket_positions(symbol)
    max_orders = state.params.get("MAX_ORDERS", config.MAX_ORDERS)
    
    if len(positions) < max_orders:
        return False

    profit = get_basket_profit(symbol)
    # Max basket loss constraint = 30% of capital assigned to this basket theoretically
    max_basket_loss = config.CAPITAL * 0.30

    if profit <= -max_basket_loss:
        logger.warning(
            f"🛑 [{symbol}] BASKET STOP LOSS! All {max_orders} DCA layers used. "
            f"Loss: ${profit:.2f} exceeds -${max_basket_loss:.2f} limit."
        )
        close_all_positions(symbol, reason="BASKET_SL")
        return True

    return False


# ─── DCA & Pyramid Trigger ─────────────────────────────────
def _check_order_triggers(symbol: str, state: BasketState):
    if state.direction is None:
        return

    positions = get_basket_positions(symbol)
    max_orders = state.params.get("MAX_ORDERS", config.MAX_ORDERS)
    if not positions or len(positions) >= max_orders:
        return

    current_price = _get_price(symbol, state.direction)
    if current_price <= 0:
        return

    pip_size = state.params.get("PIP_SIZE", config.PIP_SIZE)
    step_pips = state.params.get("STEP_PIPS", config.STEP_PIPS)
    lot_size = state.params.get("LOT_SIZE", config.LOT_SIZE)
    step_distance = step_pips * pip_size

    if state.direction == "BUY":
        dca_delta = state.last_dca_price - current_price
        pyr_delta = current_price - state.last_pyramid_price
    else:
        dca_delta = current_price - state.last_dca_price
        pyr_delta = state.last_pyramid_price - current_price

    # 1. DCA (Against us)
    if dca_delta >= step_distance:
        # Arm the extreme tracker
        if symbol not in signal_state.dca_armed_extremes:
            signal_state.dca_armed_extremes[symbol] = current_price
        else:
            if state.direction == "BUY":
                # Market dropping -> track lowest pain point
                signal_state.dca_armed_extremes[symbol] = min(signal_state.dca_armed_extremes[symbol], current_price)
            else:
                # Market pumping -> track highest pain point
                signal_state.dca_armed_extremes[symbol] = max(signal_state.dca_armed_extremes[symbol], current_price)
                
        # ─── Dynamic Trailing Wick Reversal Filter ───
        reversal_dist = step_distance * 0.15 # 15% pullback from extreme
        min_reversal = 2.0 * pip_size
        actual_reversal = max(reversal_dist, min_reversal)
        
        trigger_dca = False
        extreme = signal_state.dca_armed_extremes[symbol]
        
        if state.direction == "BUY":
            # Reversal up from the lowest low
            if current_price - extreme >= actual_reversal:
                trigger_dca = True
        else:
            # Reversal down from the highest high
            if extreme - current_price >= actual_reversal:
                trigger_dca = True
                
        if not trigger_dca:
            distance_from_extreme = (current_price - extreme) if state.direction == "BUY" else (extreme - current_price)
            req_pips = actual_reversal / pip_size
            curr_pips = distance_from_extreme / pip_size
            signal_state.dca_rejection_statuses[symbol] = f"Armed (Wick: {abs(curr_pips):.1f}/{req_pips:.1f} pips)"
            return
            
        # Firing triggered! Clean up caches
        del signal_state.dca_armed_extremes[symbol]
        if symbol in signal_state.dca_rejection_statuses:
            del signal_state.dca_rejection_statuses[symbol]

        state.dca_layer += 1
        pips_moved = dca_delta / pip_size
        logger.info(
            f"📉 [{symbol}] Trailing DCA Trigger! Wick Caught ({actual_reversal/pip_size:.1f} pip pullback) "
            f"at distance {pips_moved:.1f} pips. (Layer {state.dca_layer} / Total {len(positions) + 1})."
        )
        if place_dca_order(symbol, state.direction, state.dca_layer, lot_size):
            state.last_dca_price = current_price
        else:
            state.dca_layer -= 1
            
    else:
        # Disarm if price fully recovers out of the DCA target zone
        if symbol in signal_state.dca_armed_extremes:
            del signal_state.dca_armed_extremes[symbol]
        if symbol in signal_state.dca_rejection_statuses:
            del signal_state.dca_rejection_statuses[symbol]

    # 2. Pyramid (In our favor) - DISABLED to prevent halving profit right before trail
    # elif pyr_delta >= step_distance:
    #     state.pyramid_layer += 1
    #     pips_moved = pyr_delta / pip_size
    #     logger.info(
    #         f"🚀 [{symbol}] PYRAMID Trigger! {pips_moved:.1f} pips in favor "
    #         f"(Layer {state.pyramid_layer} / Total {len(positions) + 1})"
    #     )
    #     if place_dca_order(symbol, state.direction, state.dca_layer + state.pyramid_layer, lot_size):
    #         state.last_pyramid_price = current_price
    #     else:
    #         state.pyramid_layer -= 1


def _is_new_candle(symbol: str) -> bool:
    global _last_candle_times
    tf = get_mt5_timeframe()
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, 1)
    if rates is None or len(rates) == 0:
        return False
        
    current_time = rates[0]['time']
    last_time = _last_candle_times.get(symbol, None)
    
    if last_time is None:
        _last_candle_times[symbol] = current_time
        return False # Skip initial startup candle so it waits for the next one
        
    if current_time > last_time:
        _last_candle_times[symbol] = current_time
        return True
        
    return False


# ─── Main Bot Loop ──────────────────────────────────────────
def main():
    global basket_states, _running, _last_log_time, global_sweep_trigger_time

    if not initialize_mt5():
        sys.exit(1)

    logger.info("═" * 60)
    logger.info(f"  DCA FOREX BOT STARTED ")
    logger.info("═" * 60)
    
    # Start Dashboard Server in background
    threading.Thread(target=dashboard.run_dashboard_server, daemon=True).start()
    
    for sym in config.SYMBOLS:
        signal_state.latest_signal_status[sym] = {
            "status": "Bot Started... Waiting for next candle.",
            "color": "gray",
            "time": None
        }
        
    # ── State Recovery ──
    _adopt_orphan_baskets()

    account = mt5.account_info()
    if account:
        total_adopted_pnl = sum(get_basket_profit(sym) for sym in basket_states.keys())
        logger.info(f"🔄 Adopted existing positions. Floating PnL: {total_adopted_pnl:+.2f}")
    else:
        logger.error("Could not fetch account equity. Exiting.")
        sys.exit(1)

    logger.info("Bot is active and scanning...")
    last_log_time = time.time()

    try:
        while _running:
            # ── 0. Manual Closures from Dashboard ──
            if "ALL" in signal_state.manual_close_requests:
                logger.warning("🚨 MANUAL UI TRIGGER: Closing ALL active baskets!")
                for sym in list(basket_states.keys()):
                    close_all_positions(sym, reason="MANUAL_UI_CLOSE")
                    del basket_states[sym]
                    signal_state.latest_signal_status[sym] = {
                        "status": "Closed Manually. Waiting for next candle...",
                        "color": "gray",
                        "time": None
                    }
                
                # Write history if session was deliberately ended by UI or "Close All"
                import db
                act_account = mt5.account_info()
                final_pnl = (act_account.balance - signal_state.session_start_balance) if act_account else signal_state.session_realized_pnl
                db.insert_session(
                    start_time=signal_state.session_start_time_stamp or time.time(),
                    realized_pnl=final_pnl,
                    reason="MANUAL_CLOSE",
                    symbols=signal_state.session_symbols
                )
                    
                signal_state.manual_close_requests.clear()
            else:
                for req_sym in list(signal_state.manual_close_requests):
                    if req_sym in basket_states:
                        logger.warning(f"🚨 MANUAL UI TRIGGER: Closing basket {req_sym}!")
                        close_all_positions(req_sym, reason="MANUAL_UI_CLOSE")
                        del basket_states[req_sym]
                        
                        signal_state.latest_signal_status[req_sym] = {
                            "status": "Closed Manually. Waiting for next candle...",
                            "color": "gray",
                            "time": None
                        }
                signal_state.manual_close_requests.clear()

            # ── 1. Session Guard (Dynamic TP/SL) ──
            limit_hit = _check_session_limits()
            if limit_hit:
                for sym in list(basket_states.keys()):
                    close_all_positions(sym, reason=limit_hit)
                    signal_state.latest_signal_status[sym] = {
                        "status": f"Session Ended ({limit_hit})",
                        "color": "gray",
                        "time": None
                    }
                basket_states.clear()
                
                logger.critical(f"🏆 SESSION ENDED ({limit_hit}). All trades closed.")
                
                # Save into History DB
                import db
                db.insert_session(
                    start_time=signal_state.session_start_time_stamp or time.time(),
                    realized_pnl=signal_state.session_realized_pnl,
                    reason=limit_hit,
                    symbols=signal_state.session_symbols
                )
                
                signal_state.session_active = False
                
                # Handle Auto Restart mechanism
                if signal_state.session_auto_restart:
                    signal_state.session_waiting_for_next_range = True
                    signal_state.session_last_ended_range_idx = _get_active_time_range_index()
                    logger.warning("⏳ Auto-Restart STANDBY: Waiting for next Schedule Range...")
                else:
                    signal_state.is_bot_active = False
                    
                signal_state.save_session()
                
            # ── 1.b. Auto Restart Guard ──
            if not signal_state.session_active and signal_state.session_auto_restart and signal_state.session_waiting_for_next_range:
                curr_idx = _get_active_time_range_index()
                
                # Drop tracking memory if time exits all schedules boundary entirely
                if curr_idx == -1:
                    signal_state.session_last_ended_range_idx = -1
                
                if curr_idx >= 0 and curr_idx != signal_state.session_last_ended_range_idx:
                    # Time has entered a completely new valid block
                    acc = mt5.account_info()
                    if acc:
                        signal_state.session_active = True
                        signal_state.is_bot_active = True
                        signal_state.session_waiting_for_next_range = False
                        signal_state.session_last_ended_range_idx = -1
                        signal_state.session_start_time_stamp = time.time()
                        
                        signal_state.session_start_equity = acc.equity
                        signal_state.session_start_balance = acc.balance
                        signal_state.session_current_pnl = 0.0
                        signal_state.session_realized_pnl = 0.0
                        signal_state.save_session()
                        logger.critical("🚀 AUTO RESTART TRIGGERED! Next time range hit. New Session Active.")

            # ── 2. Active Basket Management ──
            symbols_to_remove = []
            active_baskets_count = 0
            
            for sym, state in basket_states.items():
                if state.direction is not None:
                    active_baskets_count += 1
                    positions = get_basket_positions(sym)
                    if not positions:
                        # Basket closed manually or naturally
                        symbols_to_remove.append(sym)
                        continue

                    if _check_smart_exit(sym, state):
                        symbols_to_remove.append(sym)
                        continue

                    if _check_basket_stop_loss(sym, state):
                        symbols_to_remove.append(sym)
                        continue

                    _check_order_triggers(sym, state)

            for sym in symbols_to_remove:
                del basket_states[sym]
                signal_state.latest_signal_status[sym] = {
                    "status": "Basket Closed. Waiting for next candle...",
                    "color": "gray",
                    "time": None
                }
                
            active_baskets_count -= len(symbols_to_remove)

            # Logging active states every 1 second for real-time feel
            current_time = time.time()
            if current_time - last_log_time >= 1.0:
                last_log_time = current_time
                total_bot_profit = 0.0
                
                if active_baskets_count > 0:
                    for sym, state in basket_states.items():
                        positions = get_basket_positions(sym)
                        if positions:
                            profit = get_basket_profit(sym)
                            total_bot_profit += profit
                            
                            total_vol = get_basket_volume(sym)
                            num_pos = len(positions)
                            breakeven = get_breakeven_price(sym)
                            current = _get_close_price(sym, state.direction)
                            pip_size = state.params.get('PIP_SIZE', config.PIP_SIZE)
                            exit_pips = state.params.get('EXIT_PIPS', config.EXIT_PIPS)

                            if state.direction == "BUY":
                                pips_from_be = (current - breakeven) / pip_size if pip_size > 0 else 0
                            else:
                                pips_from_be = (breakeven - current) / pip_size if pip_size > 0 else 0

                            trail_text = f"TRAIL Peak: {state.trailing_extreme_price:.5f}" if state.trailing_active else f"Exit at: +{exit_pips:.1f}p"
                            logger.info(
                                f"📊 [{sym}] {num_pos} orders ({total_vol:.2f} lots) | "
                                f"P/L: ${profit:+.2f} | "
                                f"BE: {breakeven:.5f} ({pips_from_be:+.1f} p) | "
                                f"{trail_text}"
                            )
                            
                            max_orders = state.params.get('MAX_ORDERS', config.MAX_ORDERS)
                            dca_reason = signal_state.dca_rejection_statuses.get(sym, "")
                            reason_html = f"<br><small style='color: #f59e0b; font-weight: 500;'>{dca_reason}</small>" if dca_reason else ""
                            
                            entry_time = min([p.time for p in positions]) if positions else time.time()
                            
                            signal_state.latest_signal_status[sym] = {
                                "status": f"Holding {state.direction} | Layers: {num_pos}/{max_orders}<br><small style='color:#94a3b8'>BE: {breakeven:.5f} ({pips_from_be:+.1f} pips)</small>{reason_html}",
                                "pnl": profit,
                                "color": "green" if profit >= 0 else "orange",
                                "time": entry_time
                            }
                    
                    logger.info(f"💰 Bot Portfolio PnL: ${total_bot_profit:+.2f}")
                
                # Fetch global account metrics every second independently
                account = mt5.account_info()
                if account:
                    signal_state.current_balance = account.balance

                # Push true floating PnL of all bot baskets to the UI
                signal_state.total_pnl = total_bot_profit

            # ── 3. Scan for New Entries (Synchronized Leaderboard) ──
            if signal_state.is_bot_active and signal_state.session_active and active_baskets_count < signal_state.session_max_symbols:
                
                # Check for candle boundary crossing
                sweep_triggered = False
                for sym in signal_state.session_symbols:
                    if _is_new_candle(sym):
                        sweep_triggered = True
                
                if sweep_triggered and global_sweep_trigger_time == 0.0:
                    global_sweep_trigger_time = time.time()
                    logger.info("⏱️ New candle detected across network. Entering 3s grace period for ticks to settle...")
                
                # Execute Leaderboard Sweep after dynamic grace period!
                if global_sweep_trigger_time > 0 and (time.time() - global_sweep_trigger_time) >= config.SYNC_DELAY_SECONDS:
                    valid_signals = []
                    
                    # 1. Gather all signals concurrently
                    for sym in signal_state.session_symbols:
                        if sym in basket_states:
                            continue # Already trading this!
                            
                        if not _is_within_trading_hours(sym):
                            continue

                        signal_data = get_entry_signal(target_symbol=sym)
                        if signal_data is not None:
                            surge = signal_data.get("surge_ratio", 1.0)
                            
                            valid_signals.append({
                                "symbol": sym,
                                "direction": signal_data.get("direction"),
                                "surge": surge
                            })

                    # 2. Sort Leaderboard by strongest momentum
                    valid_signals.sort(key=lambda x: x["surge"], reverse=True)
                    
                    # 3. Execute Top Trades
                    for candidate in valid_signals:
                        sym = candidate["symbol"]
                        
                        if active_baskets_count >= signal_state.session_max_symbols:
                            signal_state.latest_signal_status[sym] = {
                                "status": "Skipped (Portfolio Limit Full)",
                                "color": "red",
                                "time": None
                            }
                            continue
                            
                        best_symbol = sym
                        best_direction = candidate["direction"]
                        surge = candidate["surge"]

                        params = recalculate(best_symbol)
                        if params:
                            logger.info(f"🏆 LEADERBOARD WINNER: [{best_symbol}] {best_direction} Surge Score: {surge:.2f}x")
                            lot_size = params.get("LOT_SIZE", config.LOT_SIZE)
                            if place_entry_order(best_symbol, best_direction, lot_size):
                                new_state = BasketState()
                                new_state.direction = best_direction
                                new_state.params = params
                                new_state.initial_surge = surge
                                
                                entry_price = _get_price(best_symbol, best_direction)
                                new_state.last_dca_price = entry_price
                                new_state.last_pyramid_price = entry_price
                                
                                basket_states[best_symbol] = new_state
                                active_baskets_count += 1
                                
                                step_pips = params.get("STEP_PIPS", config.STEP_PIPS)
                                exit_pips = params.get("EXIT_PIPS", config.EXIT_PIPS)
                                logger.info(
                                    f"✅ [{best_symbol}] Sync-Basket started: {best_direction} @ {entry_price} | "
                                    f"Gap: {step_pips} pips | "
                                    f"Target: +{exit_pips} pips"
                                )
                            else:
                                logger.error(f"[{best_symbol}] Leaderboard Entry FAILED.")
                                signal_state.latest_signal_status[best_symbol] = {
                                    "status": f"Blocked: MT5 Entry Order Failed",
                                    "color": "red",
                                    "time": None
                                }
                        else:
                            signal_state.latest_signal_status[best_symbol] = {
                                "status": f"Blocked: Spread/Risk Limit Exceeded",
                                "color": "red",
                                "time": None
                            }
                    
                    # Reset timer for next candle period
                    global_sweep_trigger_time = 0.0

            # Ultra-fast loop constraint
            time.sleep(0.01)

    except Exception as e:
        logger.exception(f"Unhandled exception: {e}")
        try:
            for sym in list(basket_states.keys()):
                close_all_positions(sym, reason="BOT_CRASH")
        except Exception:
            logger.exception("Failed emergency close during crash.")
    finally:
        shutdown_mt5()
        logger.info("DCA Bot terminated.")

if __name__ == "__main__":
    main()
