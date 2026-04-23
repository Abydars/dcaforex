"""
============================================================
 Execution — Market Orders with SL/TP
============================================================
Single-position system. No DCA, no averaging, no hedging.
Every order has a hard SL and TP attached at placement time.
============================================================
"""

import logging
import time
from dataclasses import dataclass
from typing import Optional

import MetaTrader5 as mt5

import config
import ui_state

logger = logging.getLogger("Execution")


@dataclass
class OrderResult:
    success: bool
    ticket: Optional[int] = None
    fill_price: Optional[float] = None
    error: str = ""


def _normalize_price(symbol: str, price: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return price
    return round(price, info.digits)


def _normalize_volume(symbol: str, volume: float) -> float:
    info = mt5.symbol_info(symbol)
    if info is None:
        return volume
    step = info.volume_step
    volume = max(info.volume_min, min(volume, info.volume_max))
    # Floor to step
    factor = round(1.0 / step) if step < 1 else 1
    return round(int(volume * factor) / factor, 8) if factor > 1 else volume


def _validate_stops(symbol: str, direction: str, entry: float, sl: float, tp: float) -> bool:
    """Ensure SL/TP respect the broker's minimum stops distance."""
    info = mt5.symbol_info(symbol)
    if info is None:
        return False
    point = info.point
    min_stops = info.trade_stops_level * point  # minimum distance from current price

    if direction == "BUY":
        if entry - sl < min_stops:
            logger.error(f"SL too close: {entry - sl} < min {min_stops}")
            ui_state.log_rejection("EXECUTION", "Stops too tight for broker minimum distance", details={"entry": entry, "sl": sl, "tp": tp})
            return False
        if tp - entry < min_stops:
            logger.error(f"TP too close: {tp - entry} < min {min_stops}")
            ui_state.log_rejection("EXECUTION", "Stops too tight for broker minimum distance", details={"entry": entry, "sl": sl, "tp": tp})
            return False
    else:
        if sl - entry < min_stops:
            logger.error(f"SL too close: {sl - entry} < min {min_stops}")
            ui_state.log_rejection("EXECUTION", "Stops too tight for broker minimum distance", details={"entry": entry, "sl": sl, "tp": tp})
            return False
        if entry - tp < min_stops:
            logger.error(f"TP too close: {entry - tp} < min {min_stops}")
            ui_state.log_rejection("EXECUTION", "Stops too tight for broker minimum distance", details={"entry": entry, "sl": sl, "tp": tp})
            return False
    return True


def place_market_order(
    symbol: str,
    direction: str,
    volume: float,
    sl: float,
    tp: float,
    comment: str = "SMC_ENTRY",
    max_retries: int = 3,
) -> OrderResult:
    """
    Place a market order with attached SL/TP.
    Handles IOC → FOK fallback and up to `max_retries` retries on requotes.
    """
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL

    volume = _normalize_volume(symbol, volume)
    sl = _normalize_price(symbol, sl)
    tp = _normalize_price(symbol, tp)

    for attempt in range(1, max_retries + 1):
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.error(f"No tick for {symbol}")
            time.sleep(0.1)
            continue

        price = tick.ask if direction == "BUY" else tick.bid

        if not _validate_stops(symbol, direction, price, sl, tp):
            return OrderResult(success=False, error="Stops too tight for broker limits")

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume),
            "type": order_type,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": config.MAX_SLIPPAGE_POINTS,
            "magic": config.MAGIC_NUMBER,
            "comment": comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        logger.info(
            f"[Attempt {attempt}/{max_retries}] {direction} {symbol} {volume} lots "
            f"@ {price:.2f} | SL {sl:.2f} | TP {tp:.2f}"
        )

        result = mt5.order_send(request)

        if result is None:
            logger.error(f"order_send returned None. Error: {mt5.last_error()}")
            time.sleep(0.2)
            continue

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                f"✅ FILLED. Ticket: {result.order} | Price: {result.price:.2f} | "
                f"Vol: {result.volume}"
            )
            return OrderResult(
                success=True,
                ticket=result.order,
                fill_price=result.price,
            )

        if result.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
            logger.warning("IOC rejected, retrying FOK...")
            request["type_filling"] = mt5.ORDER_FILLING_FOK
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"✅ FOK FILLED. Ticket: {result.order}")
                return OrderResult(
                    success=True,
                    ticket=result.order,
                    fill_price=result.price,
                )

        if result.retcode == mt5.TRADE_RETCODE_REQUOTE:
            logger.warning(f"Requote on attempt {attempt}. Retrying...")
            time.sleep(0.1)
            continue

        logger.error(f"Order rejected. RetCode: {result.retcode} | Comment: {result.comment}")
        time.sleep(0.2)

    ui_state.log_rejection(
        "EXECUTION",
        f"Order placement failed: all {max_retries} attempts exhausted",
        details={"direction": direction, "volume": volume},
    )
    return OrderResult(success=False, error=f"All {max_retries} attempts exhausted")


def get_open_position(symbol: str) -> Optional[object]:
    """Return the single bot-managed position on this symbol, if any."""
    positions = mt5.positions_get(symbol=symbol)
    if positions is None:
        return None
    for p in positions:
        if p.magic == config.MAGIC_NUMBER:
            return p
    return None


def close_position(position, reason: str = "MANUAL_CLOSE") -> float:
    """Force-close a position at market. Returns realized PnL."""
    symbol = position.symbol
    volume = position.volume

    close_type = (
        mt5.ORDER_TYPE_SELL if position.type == mt5.ORDER_TYPE_BUY
        else mt5.ORDER_TYPE_BUY
    )
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"No tick to close {symbol}")
        return 0.0

    close_price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": close_type,
        "position": position.ticket,
        "price": close_price,
        "deviation": config.MAX_SLIPPAGE_POINTS,
        "magic": config.MAGIC_NUMBER,
        "comment": reason,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    result = mt5.order_send(request)
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        # Try FOK fallback
        request["type_filling"] = mt5.ORDER_FILLING_FOK
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            request["price"] = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
        result = mt5.order_send(request)

    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        pnl = position.profit
        logger.info(f"🔒 Closed #{position.ticket} | P/L: ${pnl:+.2f} | {reason}")
        return pnl

    logger.error(f"Failed to close #{position.ticket}. Code: {result.retcode if result else 'None'}")
    return 0.0


def get_realized_pnl_since(ticket: int) -> Optional[float]:
    """Look up the deal history for the given position ticket and compute net PnL."""
    # Fetch recent history deals
    deals = mt5.history_deals_get(position=ticket)
    if deals is None or len(deals) == 0:
        return None
    return sum(d.profit + d.swap + d.commission for d in deals)
