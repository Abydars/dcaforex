"""
============================================================
 Trade Log — SQLite journaling of every trade
============================================================
Used for post-hoc analysis and forward-test statistics.
============================================================
"""

import logging
import sqlite3
import time
from typing import Optional

import config

logger = logging.getLogger("TradeLog")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket          INTEGER,
    symbol          TEXT,
    direction       TEXT,
    entry_time      REAL,
    entry_price     REAL,
    sl              REAL,
    tp              REAL,
    lot_size        REAL,
    rr_planned      REAL,
    setup_note      TEXT,
    exit_time       REAL,
    exit_price      REAL,
    exit_reason     TEXT,
    pnl             REAL,
    status          TEXT  -- 'OPEN' | 'CLOSED'
);

CREATE INDEX IF NOT EXISTS idx_trades_ticket ON trades(ticket);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
"""


def _conn():
    c = sqlite3.connect(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript(_SCHEMA)
    logger.info(f"Trade DB ready at {config.DB_PATH}")


def log_entry(
    ticket: int,
    symbol: str,
    direction: str,
    entry_price: float,
    sl: float,
    tp: float,
    lot_size: float,
    rr_planned: float,
    setup_note: str,
) -> int:
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO trades (
                ticket, symbol, direction, entry_time, entry_price,
                sl, tp, lot_size, rr_planned, setup_note, status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
            """,
            (
                ticket, symbol, direction, time.time(), entry_price,
                sl, tp, lot_size, rr_planned, setup_note,
            ),
        )
        return cur.lastrowid


def log_exit(
    ticket: int,
    exit_price: float,
    exit_reason: str,
    pnl: float,
):
    with _conn() as c:
        c.execute(
            """
            UPDATE trades
            SET exit_time=?, exit_price=?, exit_reason=?, pnl=?, status='CLOSED'
            WHERE ticket=? AND status='OPEN'
            """,
            (time.time(), exit_price, exit_reason, pnl, ticket),
        )


def get_open_trades():
    with _conn() as c:
        return c.execute("SELECT * FROM trades WHERE status='OPEN'").fetchall()


def get_summary(last_n_days: int = 30) -> dict:
    cutoff = time.time() - (last_n_days * 86400)
    with _conn() as c:
        rows = c.execute(
            """
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) AS losses,
                   COALESCE(SUM(pnl), 0) AS total_pnl
            FROM trades
            WHERE status='CLOSED' AND exit_time >= ?
            """,
            (cutoff,),
        ).fetchone()
    return dict(rows) if rows else {}
