"""
Lightweight state module for UI.
"""

bot_enabled: bool = True
emergency_close_request: bool = False
last_market_context: dict = {}
last_signal_scan_time: float = 0.0

from collections import deque
from dataclasses import dataclass
from threading import Lock
import time

@dataclass
class RejectionEvent:
    timestamp: float        # UNIX time
    stage: str              # 'FILTER' | 'BIAS' | 'SWEEP' | 'FVG' | 'RR' | 'RISK' | 'EXECUTION'
    reason: str             # Human-readable: "Session: outside windows"
    details: dict = None    # Optional: structured context (atr, price, etc.)

    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "time_str": time.strftime("%H:%M:%S", time.localtime(self.timestamp)),
            "stage": self.stage,
            "reason": self.reason,
            "details": self.details or {},
        }

# Bounded log — keep last 100 rejections
_rejection_log: deque = deque(maxlen=100)
_rejection_lock = Lock()

def log_rejection(stage: str, reason: str, details: dict = None):
    """Thread-safe append to the rejection log."""
    evt = RejectionEvent(
        timestamp=time.time(),
        stage=stage,
        reason=reason,
        details=details or {},
    )
    with _rejection_lock:
        _rejection_log.appendleft(evt)

def get_rejections(limit: int = 20) -> list:
    """Return last N rejections as dicts, newest first."""
    with _rejection_lock:
        items = list(_rejection_log)[:limit]
    return [e.to_dict() for e in items]

def get_rejection_stats(last_n_minutes: int = 60) -> dict:
    """Aggregate rejection counts by stage over a recent window."""
    cutoff = time.time() - (last_n_minutes * 60)
    with _rejection_lock:
        items = [e for e in _rejection_log if e.timestamp >= cutoff]
    stats = {}
    for e in items:
        stats[e.stage] = stats.get(e.stage, 0) + 1
    return {
        "total": len(items),
        "by_stage": stats,
        "window_minutes": last_n_minutes,
    }

# Also track the last scan timestamp and outcome
last_scan_time: float = 0.0
last_scan_result: str = "PENDING"  # 'PENDING' | 'SIGNAL' | 'REJECTED'
