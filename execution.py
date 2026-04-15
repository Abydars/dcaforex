"""
============================================================
 DCA Forex Bot — Execution Engine
============================================================
Handles all order management:
  • Initial entry orders
  • DCA (Dollar Cost Averaging) layer orders
  • Basket close (close all positions by magic number)
  • Emergency close (global stop loss)

Includes retry logic for requotes and fill-mode fallback
for Exness symbol compatibility.
"""

import logging
import time

import MetaTrader5 as mt5

import config

logger = logging.getLogger("Execution")


# ─── Pip Value Helper ───────────────────────────────────────
def _pip_size(symbol: str) -> float:
    """
    Returns the pip size for a symbol.
    • 5-digit pairs (e.g., EURUSD): pip = 0.0001
    • 3-digit pairs (e.g., USDJPY): pip = 0.01
    • Metals / exotics: derived from trade_tick_size × 10
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        return 0.0001  # fallback

    if info.digits == 5 or info.digits == 4:
        return 10 ** -(info.digits - 1)
    elif info.digits == 3 or info.digits == 2:
        return 10 ** -(info.digits - 1)
    else:
        # For non-standard symbols, use tick_size × 10
        return info.trade_tick_size * 10


def _get_price(symbol: str, direction: str) -> float:
    """Get the entry price based on direction (BUY→ask, SELL→bid)."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        logger.error(f"Cannot get tick for {symbol}. Error: {mt5.last_error()}")
        return 0.0
    return tick.ask if direction == "BUY" else tick.bid


# ─── Send Order with Retry ──────────────────────────────────
def _send_order(
    symbol: str,
    direction: str,
    volume: float,
    comment: str = "DCA_BOT",
    max_retries: int = 3,
) -> bool:
    """
    Send a market order with retry logic for requotes and
    fill-mode fallback (IOC → FOK) for Exness compatibility.

    Returns True if the order was filled successfully.
    """
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol info unavailable for {symbol}")
        return False

    # Clamp volume to broker limits
    volume = max(info.volume_min, min(volume, info.volume_max))
    # Snap to lot step
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
        "deviation": 30,  # 3 pips slippage tolerance on 5-digit pairs
        "magic": config.MAGIC_NUMBER,
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    for attempt in range(1, max_retries + 1):
        # Refresh price on each retry to avoid stale quotes
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

        # ── Success ──
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                f"✅ Order filled. Ticket: {result.order} | "
                f"Vol: {result.volume} @ {result.price}"
            )
            return True

        # ── Fill-mode fallback (IOC → FOK) ──
        if result.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
            logger.warning(
                f"IOC fill rejected (retcode={result.retcode}). "
                f"Retrying with FOK..."
            )
            request["type_filling"] = mt5.ORDER_FILLING_FOK
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"✅ FOK fill succeeded. Ticket: {result.order}")
                return True

        # ── Requote ──
        if result.retcode == mt5.TRADE_RETCODE_REQUOTE:
            logger.warning(
                f"Requote on attempt {attempt}. Re-fetching price..."
            )
            time.sleep(0.05)  # Brief cooldown
            continue

        # ── Other errors ──
        logger.error(
            f"Order rejected. RetCode: {result.retcode} | "
            f"Comment: {result.comment} | MT5 Error: {mt5.last_error()}"
        )
        time.sleep(0.1)

    logger.error(f"All {max_retries} attempts exhausted for {direction} {symbol}")
    return False


# ─── Place Initial Entry ────────────────────────────────────
def place_entry_order(direction: str) -> bool:
    """
    Place the initial entry order using the configured lot size.
    """
    logger.info(
        f"📍 Placing INITIAL {direction} entry on {config.SYMBOL} "
        f"with {config.INITIAL_LOT} lots"
    )
    return _send_order(
        symbol=config.SYMBOL,
        direction=direction,
        volume=config.INITIAL_LOT,
        comment="DCA_ENTRY",
    )


# ─── Place DCA Layer ────────────────────────────────────────
def place_dca_order(direction: str, layer: int) -> bool:
    """
    Place a DCA (averaging) order.
    Layer index starts at 1 (first DCA after entry).
    Lot size = INITIAL_LOT × LOT_MULTIPLIER^layer

    Args:
        direction: "BUY" or "SELL"
        layer:     The DCA layer number (1, 2, 3, ...)
    """
    volume = config.INITIAL_LOT * (config.LOT_MULTIPLIER ** layer)

    logger.info(
        f"📍 Placing DCA Layer {layer} — {direction} {config.SYMBOL} "
        f"with {volume:.2f} lots (multiplier: {config.LOT_MULTIPLIER}^{layer})"
    )
    return _send_order(
        symbol=config.SYMBOL,
        direction=direction,
        volume=volume,
        comment=f"DCA_L{layer}",
    )


# ─── Get Basket Positions ───────────────────────────────────
def get_basket_positions() -> list:
    """
    Retrieve all open positions tagged with our magic number.
    Returns a list of MT5 TradePosition named tuples.
    """
    positions = mt5.positions_get(symbol=config.SYMBOL)
    if positions is None:
        return []
    return [p for p in positions if p.magic == config.MAGIC_NUMBER]


# ─── Get Basket Profit ──────────────────────────────────────
def get_basket_profit() -> float:
    """
    Calculate the total floating profit of our basket.
    Uses the native .profit field which includes swap & commission
    on Exness accounts.
    """
    return sum(p.profit for p in get_basket_positions())


# ─── Get Basket Entry Average ───────────────────────────────
def get_basket_avg_price() -> float:
    """
    Volume-weighted average entry price of all basket positions.
    Used to calculate the break-even level.
    """
    positions = get_basket_positions()
    if not positions:
        return 0.0

    total_volume = sum(p.volume for p in positions)
    if total_volume == 0:
        return 0.0

    weighted_price = sum(p.price_open * p.volume for p in positions)
    return weighted_price / total_volume


# ─── Close All Basket Positions ─────────────────────────────
def close_all_positions(reason: str = "BASKET_CLOSE") -> int:
    """
    Immediately close every open position in our basket.
    Uses market orders with aggressive deviation for speed.

    Returns the number of successfully closed positions.
    """
    positions = get_basket_positions()
    if not positions:
        logger.info("No basket positions to close.")
        return 0

    logger.info(
        f"🔒 CLOSING {len(positions)} positions — Reason: {reason}"
    )

    closed = 0
    for pos in positions:
        # Determine close direction (opposite of open)
        close_type = (
            mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY
            else mt5.ORDER_TYPE_BUY
        )
        # Use real-time tick for close price
        tick = mt5.symbol_info_tick(pos.symbol)
        if tick is None:
            logger.error(f"Cannot get tick for {pos.symbol} to close ticket {pos.ticket}")
            continue

        close_price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": pos.symbol,
            "volume": pos.volume,
            "type": close_type,
            "position": pos.ticket,
            "price": close_price,
            "deviation": 50,  # Aggressive deviation for speed
            "magic": config.MAGIC_NUMBER,
            "comment": reason,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }

        result = mt5.order_send(request)

        if result is None:
            logger.error(
                f"Close order returned None for ticket {pos.ticket}. "
                f"Error: {mt5.last_error()}"
            )
            # Retry with FOK
            request["type_filling"] = mt5.ORDER_FILLING_FOK
            request["price"] = (
                mt5.symbol_info_tick(pos.symbol).bid
                if close_type == mt5.ORDER_TYPE_SELL
                else mt5.symbol_info_tick(pos.symbol).ask
            )
            result = mt5.order_send(request)

        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(
                f"  ✅ Closed ticket {pos.ticket} | "
                f"P/L: {pos.profit:+.2f}"
            )
            closed += 1
        elif result and result.retcode == mt5.TRADE_RETCODE_INVALID_FILL:
            # Final fallback
            request["type_filling"] = mt5.ORDER_FILLING_FOK
            tick = mt5.symbol_info_tick(pos.symbol)
            request["price"] = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
            result = mt5.order_send(request)
            if result and result.retcode == mt5.TRADE_RETCODE_DONE:
                logger.info(f"  ✅ Closed ticket {pos.ticket} (FOK fallback)")
                closed += 1
            else:
                retcode = result.retcode if result else "None"
                logger.error(
                    f"  ❌ Failed to close ticket {pos.ticket}. "
                    f"RetCode: {retcode}"
                )
        else:
            retcode = result.retcode if result else "None"
            logger.error(
                f"  ❌ Failed to close ticket {pos.ticket}. "
                f"RetCode: {retcode}"
            )

    logger.info(f"Basket close complete: {closed}/{len(positions)} closed.")
    return closed
