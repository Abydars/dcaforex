"""
============================================================
 XAUUSD SMC Scalping Bot — Main Entry Point
============================================================
Single-position, single-symbol bot.

Flow each loop:
  1. Check daily risk limits → skip if breached
  2. Manage open position (log, detect SL/TP hit for journaling)
  3. If no open position:
     a. Run filters (session, spread, vol, news)
     b. Generate signal (bias → sweep → FVG)
     c. Size position from risk %
     d. Place market order with SL/TP
     e. Log to DB
============================================================
"""

import logging
import signal
import sys
import time
import threading

import MetaTrader5 as mt5

import config
import signal_state
import trade_log
from execution import (
    close_position,
    get_open_position,
    get_realized_pnl_since,
    place_market_order,
)
from filters import all_filters_pass
from mt5_connector import get_tick, initialize_mt5, shutdown_mt5
from risk import RiskManager, calculate_lot_size
from signal_engine import generate_signal
from dashboard import run_dashboard_server

logger = logging.getLogger("Bot")

_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("Interrupt received. Shutting down...")
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ─── Track open trade state so we can journal exits ────────
_current_ticket: int = 0


def _adopt_orphan_position():
    """If a position from a previous run exists, adopt its ticket for exit journaling."""
    global _current_ticket
    pos = get_open_position(config.SYMBOL)
    if pos is not None:
        _current_ticket = pos.ticket
        logger.info(
            f"♻️ Adopted existing position #{pos.ticket} {config.SYMBOL} "
            f"{'BUY' if pos.type == mt5.ORDER_TYPE_BUY else 'SELL'} "
            f"{pos.volume} lots @ {pos.price_open:.2f} | Current P/L: ${pos.profit:+.2f}"
        )


def _journal_closed_trade(ticket: int, risk_mgr: RiskManager):
    """Look up history for a ticket that just closed, log exit, update risk stats."""
    deals = mt5.history_deals_get(position=ticket)
    if not deals:
        logger.warning(f"No deal history for ticket {ticket}")
        return

    # Net PnL (sum of all deals attached to this position)
    pnl = sum(d.profit + d.swap + d.commission for d in deals)
    # Find the closing deal (last one, typically OUT or IN_OUT)
    close_deal = deals[-1]
    exit_price = close_deal.price
    exit_reason = close_deal.comment or "UNKNOWN"

    # Map common reasons
    if "sl" in exit_reason.lower():
        exit_reason = "SL_HIT"
    elif "tp" in exit_reason.lower():
        exit_reason = "TP_HIT"

    trade_log.log_exit(ticket, exit_price, exit_reason, pnl)
    risk_mgr.record_trade_closed(pnl)


def main():
    global _current_ticket

    if not initialize_mt5():
        sys.exit(1)

    logger.info("═" * 60)
    logger.info(" XAUUSD SMC SCALPING BOT — STARTED")
    logger.info("═" * 60)
    logger.info(f" Symbol:           {config.SYMBOL}")
    logger.info(f" Risk/trade:       {config.RISK_PCT_PER_TRADE}%")
    logger.info(f" Max trades/day:   {config.MAX_TRADES_PER_DAY}")
    logger.info(f" Max daily DD:     {config.MAX_DAILY_LOSS_PCT}%")
    logger.info(f" Min RR:           {config.MIN_RR}")
    logger.info(f" News filter:      {'ON' if config.NEWS_ENABLED else 'OFF'}")
    logger.info("═" * 60)

    trade_log.init_db()
    risk_mgr = RiskManager()
    _adopt_orphan_position()

    # Start the Dashboard in a background thread
    logger.info("Starting Dashboard UI on port 5000...")
    dashboard_thread = threading.Thread(target=run_dashboard_server, daemon=True)
    dashboard_thread.start()

    last_m5_ts = 0
    last_status_log = 0.0

    try:
        while _running:
            loop_start = time.time()

            # ── Detect new M5 candle close (add grace period for ticks to settle) ──
            m5_data = mt5.copy_rates_from_pos(config.SYMBOL, mt5.TIMEFRAME_M5, 0, 2)
            new_m5_closed = False
            if m5_data is not None and len(m5_data) >= 2:
                current_m5 = m5_data[-1]['time']
                if last_m5_ts and current_m5 > last_m5_ts:
                    new_m5_closed = True
                last_m5_ts = current_m5

            # ── 1. Manage open position ──
            position = get_open_position(config.SYMBOL)

            if position is not None:
                # We have an open trade — update ticket memory
                _current_ticket = position.ticket

                # Log status once per minute
                if loop_start - last_status_log >= 60.0:
                    last_status_log = loop_start
                    logger.info(
                        f"📌 OPEN #{position.ticket} "
                        f"{'BUY' if position.type == mt5.ORDER_TYPE_BUY else 'SELL'} "
                        f"{position.volume} @ {position.price_open:.2f} | "
                        f"SL {position.sl:.2f} TP {position.tp:.2f} | "
                        f"P/L ${position.profit:+.2f}"
                    )
                
                tick = get_tick(config.SYMBOL)
                current_price = 0.0
                if tick:
                    current_price = tick.bid if position.type == mt5.ORDER_TYPE_SELL else tick.ask
                
                signal_state.latest_signal_status[config.SYMBOL] = {
                    "status": f"Holding {'BUY' if position.type == mt5.ORDER_TYPE_BUY else 'SELL'}",
                    "color": "green" if position.profit >= 0 else "red",
                    "time": position.time,
                    "pnl": position.profit,
                    "tp": position.tp,
                    "sl": position.sl,
                    "entry_price": position.price_open,
                    "current_price": current_price
                }

                # Nothing else to do — SL/TP are on the broker side
                time.sleep(config.LOOP_INTERVAL_SEC)
                continue

            # ── Position just closed? Journal it ──
            if _current_ticket:
                logger.info(f"Position #{_current_ticket} closed externally (SL/TP hit).")
                _journal_closed_trade(_current_ticket, risk_mgr)
                _current_ticket = 0

            # ── 2. Check daily limits ──
            allowed, reason = risk_mgr.can_trade()
            if not allowed:
                if loop_start - last_status_log >= 300.0:  # Log every 5 min
                    last_status_log = loop_start
                    logger.info(f"⏸️  Trading paused: {reason} | {risk_mgr.status_line()}")
                signal_state.latest_signal_status[config.SYMBOL] = {
                    "status": f"Risk Blocked: {reason}",
                    "color": "gray",
                    "time": time.time()
                }
                time.sleep(config.LOOP_INTERVAL_SEC)
                continue

            # ── 3. Only scan on new M5 candle + grace period ──
            if not new_m5_closed:
                time.sleep(config.LOOP_INTERVAL_SEC)
                continue

            # Grace period after candle close
            time.sleep(config.M5_CANDLE_GRACE_SEC)

            # ── 4. Run filters ──
            passed, reason = all_filters_pass(config.SYMBOL)
            if not passed:
                logger.info(f"⏸️  Filters blocked: {reason}")
                signal_state.latest_signal_status[config.SYMBOL] = {
                    "status": f"Filters Blocked: {reason}",
                    "color": "gray",
                    "time": time.time()
                }
                continue

            # ── 5. Generate signal ──
            sig = generate_signal(config.SYMBOL)
            if sig is None:
                logger.debug("No valid setup this candle")
                continue

            # ── 6. Size position ──
            lot = calculate_lot_size(config.SYMBOL, sig.entry, sig.sl)
            if lot <= 0:
                logger.warning("Lot size calc failed — skipping")
                continue

            # ── 7. Place order ──
            result = place_market_order(
                symbol=config.SYMBOL,
                direction=sig.direction,
                volume=lot,
                sl=sig.sl,
                tp=sig.tp,
                comment=f"SMC_{sig.direction}",
            )

            if not result.success:
                logger.error(f"Entry failed: {result.error}")
                continue

            # ── 8. Journal ──
            trade_log.log_entry(
                ticket=result.ticket,
                symbol=config.SYMBOL,
                direction=sig.direction,
                entry_price=result.fill_price,
                sl=sig.sl,
                tp=sig.tp,
                lot_size=lot,
                rr_planned=sig.rr,
                setup_note=sig.setup_note,
            )
            risk_mgr.record_trade_opened()
            _current_ticket = result.ticket

            # ── Loop pacing ──
            elapsed = time.time() - loop_start
            sleep_for = max(0.1, config.LOOP_INTERVAL_SEC - elapsed)
            time.sleep(sleep_for)

    except Exception as e:
        logger.exception(f"Unhandled exception: {e}")
    finally:
        shutdown_mt5()
        logger.info("Bot terminated.")


if __name__ == "__main__":
    main()
