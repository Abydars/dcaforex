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
total_pnl = 0.0
current_balance = 0.0
max_drawdown_usd = 0.0
is_bot_active = True
manual_close_requests = set()
dca_rejection_statuses = {}
