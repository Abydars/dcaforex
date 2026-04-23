from flask import Flask, jsonify, render_template, request, session, redirect, url_for
import logging
import os
import time
import json

import ui_state
import config
from execution import get_open_position
import MetaTrader5 as mt5
from risk import RiskManager
import trade_log
import mt5_connector
import dotenv

# Suppress noisy flask logs
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)
app.secret_key = os.urandom(24)

risk_mgr = RiskManager()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pwd = request.form.get('password')
        if pwd == getattr(config, 'DASHBOARD_PASSWORD', 'admin123'):
            session['authenticated'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="Invalid Password")
    return render_template('login.html', error=None)

@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect(url_for('login'))

@app.route('/')
def index():
    if not session.get('authenticated'):
        return redirect(url_for('login'))
    return render_template('index.html')

@app.route('/api/state')
def get_state():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    
    # Account Info
    account_info = {}
    acc = mt5.account_info()
    if acc:
        account_info = {
            "login": acc.login,
            "server": acc.server,
            "balance": acc.balance,
            "equity": acc.equity,
            "currency": acc.currency
        }

    # Current Position
    position_data = None
    pos = get_open_position(config.SYMBOL)
    if pos:
        tick = mt5.symbol_info_tick(config.SYMBOL)
        current_price = 0.0
        if tick:
            current_price = tick.bid if pos.type == mt5.ORDER_TYPE_SELL else tick.ask
        
        position_data = {
            "ticket": pos.ticket,
            "direction": "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL",
            "volume": pos.volume,
            "entry_price": pos.price_open,
            "sl": pos.sl,
            "tp": pos.tp,
            "current_price": current_price,
            "pnl": pos.profit,
            "time": pos.time,
        }

    # Today's stats
    stats = risk_mgr.stats

    # Calculate current effective thresholds
    import filters
    m5 = mt5_connector.get_rates(config.SYMBOL, mt5_connector.TF_M5, 20)
    m15 = mt5_connector.get_rates(config.SYMBOL, mt5_connector.TF_M15, 20)
    m5_atr = filters._compute_atr(m5, period=14)
    m15_atr = filters._compute_atr(m15, period=14)
    
    tick = mt5.symbol_info_tick(config.SYMBOL)
    price = (tick.bid + tick.ask) / 2.0 if tick else 0.0

    fvg_eff = max(config.FVG_FLOOR_USD, m5_atr * config.FVG_MIN_ATR_FRAC) if m5_atr else config.FVG_FLOOR_USD
    sweep_eff = max(config.SWEEP_WICK_FLOOR_USD, m5_atr * config.SWEEP_WICK_ATR_FRAC) if m5_atr else config.SWEEP_WICK_FLOOR_USD
    sl_eff = max(config.SL_BUFFER_FLOOR_USD, m5_atr * config.SL_BUFFER_ATR_FRAC) if m5_atr else config.SL_BUFFER_FLOOR_USD
    
    atr_m15_eff = max(config.MIN_ATR_M15_FLOOR_USD, price * (config.MIN_ATR_M15_PCT / 100.0))
    spread_eff = min(max(price * (config.MAX_SPREAD_PCT / 100.0), 0.0), config.MAX_SPREAD_FLOOR_USD)

    # Configuration summary
    config_summary = {
        "RISK_PCT_PER_TRADE": config.RISK_PCT_PER_TRADE,
        "MAX_DAILY_LOSS_PCT": config.MAX_DAILY_LOSS_PCT,
        "MAX_TRADES_PER_DAY": config.MAX_TRADES_PER_DAY,
        "MAX_CONSECUTIVE_LOSSES": config.MAX_CONSECUTIVE_LOSSES,
        "MIN_RR": config.MIN_RR,
        "MAX_RR": config.MAX_RR,
        "FVG_MIN_ATR_FRAC": config.FVG_MIN_ATR_FRAC,
        "SWEEP_WICK_ATR_FRAC": config.SWEEP_WICK_ATR_FRAC,
        "SL_BUFFER_ATR_FRAC": config.SL_BUFFER_ATR_FRAC,
        "MIN_ATR_M15_PCT": config.MIN_ATR_M15_PCT,
        "MAX_SPREAD_PCT": config.MAX_SPREAD_PCT,
        "NEWS_ENABLED": config.NEWS_ENABLED,
        "SESSIONS_UTC": config.SESSIONS_UTC,
        "effective": {
            "m5_atr": m5_atr,
            "fvg_usd": fvg_eff,
            "sweep_usd": sweep_eff,
            "sl_usd": sl_eff,
            "price": price,
            "atr_m15_usd": atr_m15_eff,
            "spread_usd": spread_eff
        }
    }

    return jsonify({
        "bot_enabled": ui_state.bot_enabled,
        "account": account_info,
        "position": position_data,
        "today_stats": stats,
        "market_context": ui_state.last_market_context,
        "config": config_summary
    })

@app.route('/api/mt5_config', methods=['GET', 'POST'])
def mt5_config():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    
    env_file = ".env"
    
    if request.method == 'GET':
        return jsonify({
            "MT5_LOGIN": os.getenv("MT5_LOGIN", ""),
            "MT5_SERVER": os.getenv("MT5_SERVER", "")
        })
        
    data = request.json or {}
    login = data.get("MT5_LOGIN")
    password = data.get("MT5_PASS")
    server = data.get("MT5_SERVER")
    
    if login: dotenv.set_key(env_file, "MT5_LOGIN", str(login))
    if password: dotenv.set_key(env_file, "MT5_PASS", str(password))
    if server: dotenv.set_key(env_file, "MT5_SERVER", str(server))
    
    # Update running config
    if login: config.MT5_LOGIN = int(login)
    if password: config.MT5_PASS = password
    if server: config.MT5_SERVER = server
    
    # Re-initialize MT5 dynamically
    mt5_connector.shutdown_mt5()
    # give it a moment to release handles
    time.sleep(1)
    success = mt5_connector.initialize_mt5(exit_on_fail=False)
    
    if success:
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Failed to connect to MT5 with these credentials."})

@app.route('/api/config', methods=['POST'])
def update_config():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json or {}
    env_file = ".env"
    
    # Define mapping from key to (type_func, config_attr)
    mappings = {
        "RISK_PCT_PER_TRADE": (float, "RISK_PCT_PER_TRADE"),
        "MAX_DAILY_LOSS_PCT": (float, "MAX_DAILY_LOSS_PCT"),
        "MAX_TRADES_PER_DAY": (int, "MAX_TRADES_PER_DAY"),
        "MAX_CONSECUTIVE_LOSSES": (int, "MAX_CONSECUTIVE_LOSSES"),
        "MIN_RR": (float, "MIN_RR"),
        "MAX_RR": (float, "MAX_RR"),
        "FVG_MIN_ATR_FRAC": (float, "FVG_MIN_ATR_FRAC"),
        "SWEEP_WICK_ATR_FRAC": (float, "SWEEP_WICK_ATR_FRAC"),
        "SL_BUFFER_ATR_FRAC": (float, "SL_BUFFER_ATR_FRAC"),
        "MIN_ATR_M15_PCT": (float, "MIN_ATR_M15_PCT"),
        "MAX_SPREAD_PCT": (float, "MAX_SPREAD_PCT"),
        "NEWS_ENABLED": (lambda x: str(x).lower() == "true", "NEWS_ENABLED")
    }
    
    for key, (type_func, attr_name) in mappings.items():
        if key in data:
            try:
                # 1. parse
                val = type_func(data[key])
                # 2. update in memory
                setattr(config, attr_name, val)
                # 3. update in .env
                # Convert boolean to string "true" / "false" for dotenv
                env_val = str(val).lower() if isinstance(val, bool) else str(val)
                dotenv.set_key(env_file, key, env_val)
            except ValueError:
                pass # skip invalid numbers
                
    if "SESSIONS_UTC" in data:
        try:
            sessions_data = data["SESSIONS_UTC"]
            if isinstance(sessions_data, list):
                # Ensure it's a list of tuples for config
                val = [tuple(s) for s in sessions_data]
                setattr(config, "SESSIONS_UTC", val)
                # Save as JSON string
                dotenv.set_key(env_file, "SESSIONS_UTC", json.dumps(sessions_data))
        except Exception as e:
            print(f"Failed to update SESSIONS_UTC: {e}")
                
    return jsonify({"success": True})

@app.route('/api/toggle', methods=['POST'])
def toggle_bot():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    if "enabled" in data:
        ui_state.bot_enabled = bool(data["enabled"])
    return jsonify({"success": True, "bot_enabled": ui_state.bot_enabled})

@app.route('/api/close', methods=['POST'])
def emergency_close():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    ui_state.emergency_close_request = True
    return jsonify({"success": True})

@app.route('/api/history')
def get_history():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    days = int(request.args.get('days', 30))
    
    # Get all trades
    trades = trade_log.get_recent_trades(limit=1000)
    
    # Filter by days (Unix timestamp math)
    cutoff = time.time() - (days * 24 * 3600)
    filtered_trades = [t for t in trades if t['exit_time'] and t['exit_time'] >= cutoff]
    
    total_trades = len(filtered_trades)
    wins = len([t for t in filtered_trades if t['pnl'] > 0])
    winrate = (wins / total_trades * 100) if total_trades > 0 else 0
    total_pnl = sum(t['pnl'] for t in filtered_trades)
    
    # Simple equity curve
    equity_curve = []
    cumulative_pnl = 0
    for t in reversed(filtered_trades):  # Ascending order
        cumulative_pnl += t['pnl']
        equity_curve.append({
            "time": t['exit_time'],
            "pnl": cumulative_pnl
        })
    
    return jsonify({
        "summary": {
            "total_trades": total_trades,
            "winrate": winrate,
            "total_pnl": total_pnl
        },
        "equity_curve": equity_curve,
        "recent_trades": trades[:10]  # Just the last 10
    })

def run_dashboard_server():
    print("\n" + "="*50)
    print("🚀 XAUUSD SMC Dashboard LIVE at http://localhost:5000")
    print("="*50 + "\n")
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

if __name__ == '__main__':
    run_dashboard_server()
