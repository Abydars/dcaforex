from flask import Flask, jsonify, render_template, request, session, redirect, url_for
import logging
import signal_state
import config
import os
import db
import time

# Suppress noisy flask logs
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

import dotenv

app = Flask(__name__)
app.secret_key = os.urandom(24)

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
    if password: dotenv.set_key(env_file, "MT5_PASS", password)
    if server: dotenv.set_key(env_file, "MT5_SERVER", server)
    
    # Update running config
    if login: config.MT5_LOGIN = int(login)
    if password: config.MT5_PASS = password
    if server: config.MT5_SERVER = server
    
    # We could attempt to re-initialize MT5 here if needed
    # from mt5_connector import initialize_mt5
    # initialize_mt5()
    
    return jsonify({"success": True})

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        pwd = request.form.get('password')
        if pwd == config.DASHBOARD_PASSWORD:
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

@app.route('/api/signals')
def get_signals():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    return jsonify({
        "signals": signal_state.latest_signal_status,
        "total_pnl": signal_state.total_pnl,
        "balance": signal_state.current_balance,
        "is_active": signal_state.is_bot_active,
        "session_active": signal_state.session_active,
        "session_pnl": signal_state.session_current_pnl,
        "session_realized_pnl": signal_state.session_realized_pnl,
        "session_tp": signal_state.session_target_profit,
        "session_sl": signal_state.session_stop_loss,
        "session_max_symbols": signal_state.session_max_symbols,
        "session_symbols": signal_state.session_symbols,
        "session_schedule": signal_state.session_schedule,
        "session_auto_restart": signal_state.session_auto_restart,
        "session_smart_flush": signal_state.session_smart_flush,
        "session_flush_minutes": signal_state.session_flush_minutes,
        "session_flush_tolerance_pct": signal_state.session_flush_tolerance_pct,
        "session_auto_pause_minutes": signal_state.session_auto_pause_minutes,
        "master_symbols": config.SYMBOLS,
        "correlation_groups": config.CORRELATION_GROUPS
    })

@app.route('/api/session/history')
def get_session_history():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    records = db.get_latest_sessions(50)
    return jsonify({"history": records})

@app.route('/api/session/start', methods=['POST'])
def start_session():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    tp = float(data.get("tp", 0))
    sl = float(data.get("sl", 0))
    max_symbols = int(data.get("max_symbols", 2))
    session_syms = data.get("symbols", [])
    schedule = data.get("schedule", {str(i): {"enabled": i < 5, "ranges": []} for i in range(7)})
    auto_restart = bool(data.get("auto_restart", False))
    smart_flush = bool(data.get("smart_flush", False))
    flush_mins = int(data.get("flush_minutes", 5))
    flush_tol = int(data.get("flush_tolerance_pct", 10))
    auto_pause_mins = int(data.get("auto_pause_minutes", 15))
    bot_active = data.get("bot_active")
    
    if tp > 0 and sl > 0 and max_symbols > 0 and len(session_syms) > 0:
        signal_state.session_target_profit = tp
        signal_state.session_stop_loss = sl
        signal_state.session_max_symbols = max_symbols
        signal_state.session_symbols = session_syms
        signal_state.session_schedule = schedule
        signal_state.session_auto_restart = auto_restart
        signal_state.session_smart_flush = smart_flush
        signal_state.session_flush_minutes = flush_mins
        signal_state.session_flush_tolerance_pct = flush_tol
        signal_state.session_auto_pause_minutes = auto_pause_mins
        signal_state.session_start_time_stamp = time.time()
        
        signal_state.session_start_equity = signal_state.current_balance
        signal_state.session_start_balance = signal_state.current_balance
        signal_state.session_active = True
        signal_state.session_waiting_for_next_range = False
        signal_state.session_last_ended_range_id = ""
        signal_state.is_bot_active = bool(bot_active) if bot_active is not None else True
        
        # Clear out UI for symbols not in the session!
        signal_state.latest_signal_status = {
            s: {"status": "Session Started... Scanning", "color": "gray", "time": None} 
            for s in session_syms
        }
        
        signal_state.save_session()
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid Config or No Symbols Selected"})

@app.route('/api/session/update', methods=['POST'])
def update_session():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    tp = float(data.get("tp", 0))
    sl = float(data.get("sl", 0))
    max_symbols = int(data.get("max_symbols", 2))
    session_syms = data.get("symbols", [])
    schedule = data.get("schedule", {str(i): {"enabled": i < 5, "ranges": []} for i in range(7)})
    auto_restart = bool(data.get("auto_restart", False))
    smart_flush = bool(data.get("smart_flush", False))
    flush_mins = int(data.get("flush_minutes", 5))
    flush_tol = int(data.get("flush_tolerance_pct", 10))
    auto_pause_mins = int(data.get("auto_pause_minutes", 15))
    bot_active = data.get("bot_active")
    
    if tp > 0 and sl > 0 and max_symbols > 0 and len(session_syms) > 0 and signal_state.session_active:
        signal_state.session_target_profit = tp
        signal_state.session_stop_loss = sl
        signal_state.session_max_symbols = max_symbols
        signal_state.session_symbols = session_syms
        signal_state.session_schedule = schedule
        signal_state.session_auto_restart = auto_restart
        signal_state.session_smart_flush = smart_flush
        signal_state.session_flush_minutes = flush_mins
        signal_state.session_flush_tolerance_pct = flush_tol
        signal_state.session_auto_pause_minutes = auto_pause_mins
        if bot_active is not None:
            signal_state.is_bot_active = bool(bot_active)
        signal_state.save_session()
        
    return jsonify({"success": True})

@app.route('/api/session/stop', methods=['POST'])
def stop_session():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    signal_state.session_active = False
    signal_state.manual_close_requests.add("ALL")
    signal_state.save_session()
    return jsonify({"success": True})

@app.route('/api/toggle', methods=['POST'])
def toggle_bot():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    data = request.json or {}
    if "is_active" in data:
        signal_state.is_bot_active = bool(data["is_active"])
    return jsonify({"success": True})

@app.route('/api/close/<symbol>', methods=['POST'])
def close_symbol(symbol):
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    signal_state.manual_close_requests.add(symbol)
    return jsonify({"success": True})

@app.route('/api/close_all', methods=['POST'])
def close_all():
    if not session.get('authenticated'): return jsonify({"error": "Unauthorized"}), 401
    signal_state.manual_close_requests.add("ALL")
    return jsonify({"success": True})

def run_dashboard_server():
    print("\n" + "="*50)
    print("🚀 Premium Dashboard LIVE at http://localhost:5000")
    print("="*50 + "\n")
    # Run server (threaded and blocking within its own thread)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
