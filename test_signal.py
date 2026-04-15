import config
config.SYMBOLS = ["US30m"]
from mt5_connector import initialize_mt5, shutdown_mt5
from signal_engine import get_entry_signal
import logging
logging.getLogger().setLevel(logging.DEBUG)

initialize_mt5()
print("Signal:", get_entry_signal("US30m"))
shutdown_mt5()
