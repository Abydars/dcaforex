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

# Session States
session_active = False
session_start_equity = 0.0
session_target_profit = 0.0
session_stop_loss = 0.0
session_current_pnl = 0.0

def save_session():
    try:
        with open(SESSION_FILE, "w") as f:
            json.dump({
                "session_active": session_active,
                "session_start_equity": session_start_equity,
                "session_target_profit": session_target_profit,
                "session_stop_loss": session_stop_loss
            }, f)
    except Exception as e:
        print(f"Error saving session: {e}")

def load_session():
    global session_active, session_start_equity, session_target_profit, session_stop_loss
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, "r") as f:
                data = json.load(f)
            session_active = data.get("session_active", False)
            session_start_equity = data.get("session_start_equity", 0.0)
            session_target_profit = data.get("session_target_profit", 0.0)
            session_stop_loss = data.get("session_stop_loss", 0.0)
        except Exception as e:
            print(f"Error loading session: {e}")

# Call load_session on import
load_session()
