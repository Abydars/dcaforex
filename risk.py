"""
============================================================
 Risk Manager — Position Sizing + Daily Limits
============================================================
- calculate_lot_size():  converts % risk + SL distance → lot size
- DailyLimits:           tracks trade count, consecutive losses, daily DD
============================================================
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import MetaTrader5 as mt5

import config

logger = logging.getLogger("Risk")


# ─── Position Sizing ───────────────────────────────────────
def calculate_lot_size(
    symbol: str,
    entry: float,
    sl: float,
    risk_pct: float = None,
) -> float:
    """
    Convert desired USD risk into a valid MT5 lot size.

    Formula:
        risk_usd = equity * (risk_pct / 100)
        sl_distance_usd = abs(entry - sl)
        loss_per_lot_usd = sl_distance_usd * contract_size
        lot_size = risk_usd / loss_per_lot_usd
    Then snap to volume_step and clamp to volume_min/volume_max.
    """
    if risk_pct is None:
        risk_pct = config.RISK_PCT_PER_TRADE

    account = mt5.account_info()
    if account is None:
        logger.error("Cannot fetch account for lot calc")
        return 0.0

    info = mt5.symbol_info(symbol)
    if info is None:
        logger.error(f"Symbol info missing for {symbol}")
        return 0.0

    equity = account.equity
    risk_usd = equity * (risk_pct / 100.0)

    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        logger.error(f"Invalid SL distance: {sl_distance}")
        return 0.0

    # For XAUUSD: contract_size is typically 100 (100 oz per 1 lot)
    # Profit in account currency per unit price move = sl_distance * contract_size
    # This assumes account currency = USD (true for most Exness accounts).
    contract_size = info.trade_contract_size
    loss_per_lot = sl_distance * contract_size

    if loss_per_lot <= 0:
        return 0.0

    raw_lot = risk_usd / loss_per_lot

    # Snap to volume step
    step = info.volume_step
    vol_min = info.volume_min
    vol_max = info.volume_max

    # Floor-snap so we never exceed intended risk
    snapped = (int(raw_lot / step)) * step
    snapped = max(vol_min, min(snapped, vol_max))

    # If the minimum lot exceeds our budget, reject
    min_loss = vol_min * loss_per_lot
    if min_loss > risk_usd * 1.5:  # 50% tolerance
        logger.warning(
            f"Min lot ({vol_min}) exceeds risk budget. "
            f"Min loss ${min_loss:.2f} vs risk ${risk_usd:.2f}. Skipping."
        )
        return 0.0

    logger.info(
        f"Lot calc: equity=${equity:.2f} risk={risk_pct}% (${risk_usd:.2f}) "
        f"SL_dist=${sl_distance:.2f} → {snapped} lots"
    )
    return snapped


# ─── Daily Limits Tracking ─────────────────────────────────
@dataclass
class DailyStats:
    date: str                           # "YYYY-MM-DD" (UTC)
    trades_today: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    starting_equity: float = 0.0
    realized_pnl: float = 0.0
    stopped_by_limit: bool = False
    stop_reason: str = ""


class RiskManager:
    """Tracks daily stats and enforces limits."""

    def __init__(self):
        self.stats = DailyStats(date=self._today_utc())
        self._init_equity()

    @staticmethod
    def _today_utc() -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    def _init_equity(self):
        account = mt5.account_info()
        if account:
            self.stats.starting_equity = account.equity

    def _roll_over_if_new_day(self):
        today = self._today_utc()
        if today != self.stats.date:
            logger.info(f"📅 New trading day {today}. Resetting daily stats.")
            self.stats = DailyStats(date=today)
            self._init_equity()

    def can_trade(self) -> tuple:
        """Check all daily limits. Returns (allowed: bool, reason: str)."""
        self._roll_over_if_new_day()

        if self.stats.stopped_by_limit:
            return False, f"Daily stop active: {self.stats.stop_reason}"

        if self.stats.trades_today >= config.MAX_TRADES_PER_DAY:
            return False, f"Daily trade cap reached ({self.stats.trades_today}/{config.MAX_TRADES_PER_DAY})"

        if self.stats.consecutive_losses >= config.MAX_CONSECUTIVE_LOSSES:
            self.stats.stopped_by_limit = True
            self.stats.stop_reason = f"{self.stats.consecutive_losses} consecutive losses"
            return False, self.stats.stop_reason

        # Live drawdown check
        account = mt5.account_info()
        if account and self.stats.starting_equity > 0:
            dd_pct = ((self.stats.starting_equity - account.equity) / self.stats.starting_equity) * 100
            if dd_pct >= config.MAX_DAILY_LOSS_PCT:
                self.stats.stopped_by_limit = True
                self.stats.stop_reason = f"Daily DD hit: -{dd_pct:.2f}%"
                return False, self.stats.stop_reason

        return True, "Limits OK"

    def record_trade_opened(self):
        self._roll_over_if_new_day()
        self.stats.trades_today += 1
        logger.info(f"Trade #{self.stats.trades_today} opened today.")

    def record_trade_closed(self, pnl: float):
        self._roll_over_if_new_day()
        self.stats.realized_pnl += pnl
        if pnl > 0:
            self.stats.wins += 1
            self.stats.consecutive_losses = 0
            logger.info(f"✅ WIN ${pnl:+.2f} | Today: {self.stats.wins}W/{self.stats.losses}L")
        else:
            self.stats.losses += 1
            self.stats.consecutive_losses += 1
            logger.info(
                f"❌ LOSS ${pnl:+.2f} | Today: {self.stats.wins}W/{self.stats.losses}L | "
                f"Consecutive losses: {self.stats.consecutive_losses}"
            )

    def status_line(self) -> str:
        return (
            f"📊 Today: {self.stats.trades_today} trades | "
            f"{self.stats.wins}W/{self.stats.losses}L | "
            f"PnL ${self.stats.realized_pnl:+.2f} | "
            f"Consec losses: {self.stats.consecutive_losses}"
        )
