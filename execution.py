"""
============================================================
 DCA Forex Bot — Execution Engine
============================================================
Handles all order management using auto-calculated parameters:
  • Initial entry orders
  • DCA layer orders (same lot size, rapid-fire)
  • Basket close (by magic number)
  • Break-even tracking

Includes retry logic for requotes and fill-mode fallback.
============================================================
"""

import logging
import time

import MetaTrader5 as mt5

import config

logger = logging.getLogger("Execution")


def _get_price(symbol: str, direction: str) -> float:
    """Get entry price (BUY→ask, SELL→bid)."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Cannot get tick for {symbol}. Error: {mt5.last_error()}")
        return 0.0
    return tick.ask if direction == "BUY" else tick.bid


def _send_order(
    symbol: str,
    direction: str,
    volume: float,
    comment: str = "DCA_BOT",
    max_retries: int = 3,
) -> bool:
    """
    Send a market order with retry + IOC→FOK fallback.
    Returns True if filled.
    """
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol info unavailable for {symbol}")
        return False

    # Clamp and snap volume
    volume = max(info.volume_min, min(volume, info.volume_max))
    step = info.volume_step
    volume = round(volume - (volume % step), 8)

    price = _get_price(symbol, direction)
    if price <= 0:
        return False

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": float(volume),
        "type": order_type,
        "price": price,
        "deviation": 30,
        "magic": config.MAGIC_NUMBER,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    for attempt in range(1, max_retries + 1):
        request["price"] = _get_price(symbol, direction)
        if request["price"] <= 0:
            continue

        logger.info(
            f"[Attempt {attempt}/{max_retries}] {direction} {symbol} "
            f"Vol={volume:.2f} @ {request['price']}"
        )
        result = mt5.order_send(request)

        if result is None:
            logger.error(f"order_send returned None. MT5 error: {mt5.last_error()}")
            time.sleep(0.1)
            continue

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"✅ Filled. Ticket: {result.order} | Vol: {result.volume} @ {result.price}")
            return True

        if result.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
            logger.warning(f"IOC rejected. Retrying FOK...")
            request["type_filling"] = mt5.ORDER_FILLING_FOK
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"✅ FOK filled. Ticket: {result.order}")
                return True

        if result.retcode == mt5.TRADE_RETCODE_REQUOTE:
            logger.warning(f"Requote on attempt {attempt}. Refreshing...")
            time.sleep(0.05)
            continue

        logger.error(
            f"Order rejected. RetCode: {result.retcode} | "
            f"Comment: {result.comment}"
        )
        time.sleep(0.1)

    logger.error(f"All {max_retries} attempts exhausted for {direction} {symbol}")
    return False


# ─── Place Entry Order ──────────────────────────────────────
def place_entry_order(direction: str) -> bool:
    """Place initial entry with auto-calculated lot size."""
    logger.info(f"📍 ENTRY {direction} {config.SYMBOL} × {config.LOT_SIZE} lots")
    return _send_order(
        symbol=config.SYMBOL,
        direction=direction,
        volume=config.LOT_SIZE,
        comment="DCA_ENTRY",
    )


# ─── Place DCA Order ────────────────────────────────────────
def place_dca_order(direction: str, layer: int) -> bool:
    """Place DCA layer (same lot size every time — rapid fire)."""
    logger.info(f"📍 DCA Layer {layer} — {direction} {config.SYMBOL} × {config.LOT_SIZE} lots")
    return _send_order(
        symbol=config.SYMBOL,
        direction=direction,
        volume=config.LOT_SIZE,
        comment=f"DCA_L{layer}",
    )


# ─── Basket Helpers ─────────────────────────────────────────
def get_basket_positions() -> list:
    """Get all open positions with our magic number."""
    positions = mt5.positions_get(symbol=config.SYMBOL)
    if positions is None:
        return []
    return [p for p in positions if p.magic == config.MAGIC_NUMBER]


def get_basket_profit() -> float:
    """Total floating profit of our basket."""
    return sum(p.profit for p in get_basket_positions())


def get_basket_volume() -> float:
    """Total volume of all basket positions."""
    return sum(p.volume for p in get_basket_positions())


def get_breakeven_price() -> float:
    """Volume-weighted average entry price (break-even level)."""
    positions = get_basket_positions()
    if not positions:
        return 0.0
    total_vol = sum(p.volume for p in positions)
    if total_vol == 0:
        return 0.0
    return sum(p.price_open * p.volume for p in positions) / total_vol


# ─── Close All ──────────────────────────────────────────────
def close_all_positions(reason: str = "BASKET_CLOSE") -> int:
    """Close every position in the basket. Returns count closed."""
    positions = get_basket_positions()
    if not positions:
        return 0

    logger.info(f"🔒 CLOSING {len(positions)} positions — {reason}")

    closed = 0
    for pos in positions:
        close_type = (
            mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            continue

        close_price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": close_price,
            "deviation": 50,
            "magic": config.MAGIC_NUMBER,
            "comment": reason,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        # Fallback to FOK if IOC fails
        if result is None or result.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
            request["type_filling"] = mt5.ORDER_FILLING_FOK
            tick = mt5.symbol_info_tick(pos.symbol)
            if tick:
                request["price"] = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
            result = mt5.order_send(request)

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"  ✅ Closed #{pos.ticket} | P/L: {pos.profit:+.2f}")
            closed += 1
        else:
            retcode = result.retcode if result else "None"
            logger.error(f"  ❌ Failed #{pos.ticket} | RetCode: {retcode}")

    logger.info(f"Closed {closed}/{len(positions)}")
    return closed
