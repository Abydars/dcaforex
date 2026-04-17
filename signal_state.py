"""
Shared memory for the real-time dashboard.
Holds the latest signal parsing state for each symbol.
"""

# Dictionary to hold the state of each symbol
# Format:
# "EURUSD": {
#     "status": "Green Candle - Strong Uptrend Block",
#     "color": "red", # UI color mapping (e.g. green, red, gray)
#     "time": "...timestamp..."
# }
latest_signal_status = {}
import json
import os

SESSION_FILE = "session.json"

# Global states
total_pnl = 0.0
current_balance = 0.0
is_bot_active = True
manual_close_requests = set()
dca_rejection_statuses = {}
dca_armed_extremes = {}

# Session States
session_active = False
session_start_equity = 0.0
session_start_balance = 0.0
session_target_profit = 0.0
session_stop_loss = 0.0
session_current_pnl = 0.0
session_realized_pnl = 0.0
session_max_symbols = 2
session_symbols = []
session_schedule = {str(i): {"enabled": i < 5, "ranges": []} for i in range(7)}
session_start_time_stamp = 0.0
session_auto_restart = False
session_smart_flush = False
session_flush_minutes = 5
session_flush_tolerance_pct = 10
session_auto_pause_minutes = 15
session_waiting_for_next_range = False
session_last_ended_range_id = ""

def save_session():
    try:
        with open(SESSION_FILE, "w") as f:
            json.dump({
                "session_active": session_active,
                "session_start_equity": session_start_equity,
                "session_start_balance": session_start_balance,
                "session_target_profit": session_target_profit,
                "session_stop_loss": session_stop_loss,
                "session_max_symbols": session_max_symbols,
                "session_symbols": session_symbols,
                "session_schedule": session_schedule,
                "session_start_time_stamp": session_start_time_stamp,
                "session_auto_restart": session_auto_restart,
                "session_smart_flush": session_smart_flush,
                "session_flush_minutes": session_flush_minutes,
                "session_flush_tolerance_pct": session_flush_tolerance_pct,
                "session_auto_pause_minutes": session_auto_pause_minutes,
                "session_waiting_for_next_range": session_waiting_for_next_range,
                "session_last_ended_range_id": session_last_ended_range_id
            }, f)
    except Exception as e:
        print(f"Error saving session: {e}")

def load_session():
    global session_active, session_start_equity, session_start_balance, session_target_profit, session_stop_loss, session_max_symbols, session_symbols, session_schedule, session_start_time_stamp, session_auto_restart, session_smart_flush, session_flush_minutes, session_flush_tolerance_pct, session_auto_pause_minutes, session_waiting_for_next_range, session_last_ended_range_id
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                data = json.load(f)
            session_active = data.get("session_active", False)
            session_start_equity = data.get("session_start_equity", 0.0)
            session_start_balance = data.get("session_start_balance", 0.0)
            session_target_profit = data.get("session_target_profit", 0.0)
            session_stop_loss = data.get("session_stop_loss", 0.0)
            session_max_symbols = data.get("session_max_symbols", 2)
            session_symbols = data.get("session_symbols", [])
            session_schedule = data.get("session_schedule", {str(i): {"enabled": i < 5, "ranges": []} for i in range(7)})
            session_start_time_stamp = data.get("session_start_time_stamp", 0.0)
            session_auto_restart = data.get("session_auto_restart", False)
            session_smart_flush = data.get("session_smart_flush", False)
            session_flush_minutes = data.get("session_flush_minutes", 5)
            session_flush_tolerance_pct = data.get("session_flush_tolerance_pct", 10)
            session_auto_pause_minutes = data.get("session_auto_pause_minutes", 15)
            session_waiting_for_next_range = data.get("session_waiting_for_next_range", False)
            session_last_ended_range_id = data.get("session_last_ended_range_id", "")
        except Exception as e:
            print(f"Error loading session: {e}")

# Call load_session on import
load_session()
