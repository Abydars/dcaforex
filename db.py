import sqlite3
import os
import logging
from datetime import datetime, timezone

logger = logging.getLogger("Database")

DB_FILE = "dcaforex.db"

def _get_connection():
    return sqlite3.connect(DB_FILE)

def initialize_database():
    """Create the sessions table if it does not exist."""
    try:
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                realized_pnl REAL NOT NULL,
                reason TEXT NOT NULL,
                traded_symbols TEXT NOT NULL
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")

def insert_session(start_time: float, realized_pnl: float, reason: str, symbols: list):
    """Insert a new completed session record."""
    try:
        # Format timestamps nicely as strict ISO UTC
        start_str = datetime.fromtimestamp(start_time, tz=timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        end_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        
        symbols_str = ",".join(symbols) if symbols else "NONE"
        pnl = round(realized_pnl, 2)
        
        conn = _get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO sessions (start_time, end_time, realized_pnl, reason, traded_symbols)
            VALUES (?, ?, ?, ?, ?)
        ''', (start_str, end_str, pnl, reason, symbols_str))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Database insertion failed: {e}")

def get_latest_sessions(limit: int = 100):
    """Fetch the latest session records for the dashboard."""
    try:
        conn = _get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM sessions
            ORDER BY id DESC
            LIMIT ?
        ''', (limit,))
        rows = cursor.fetchall()
        
        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "realized_pnl": r["realized_pnl"],
                "reason": r["reason"],
                "traded_symbols": r["traded_symbols"]
            })
        conn.close()
        return results
    except Exception as e:
        logger.error(f"Failed to retrieve sessions: {e}")
        return []

# Initialize immediately when imported
initialize_database()
