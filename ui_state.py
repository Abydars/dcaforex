"""
Lightweight state module for UI.
"""

bot_enabled: bool = True
emergency_close_request: bool = False
last_market_context: dict = {}
last_signal_scan_time: float = 0.0
