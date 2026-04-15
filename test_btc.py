import config
config.SYMBOL = "BTCUSDm"
import MetaTrader5 as mt5
from mt5_connector import initialize_mt5
from auto_params import recalculate
if initialize_mt5():
    res = recalculate()
    print("LOT:", config.LOT_SIZE, "STEP:", config.STEP_PIPS)
