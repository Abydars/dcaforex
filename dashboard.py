from flask import Flask, jsonify, render_template, request
import logging
import signal_state
import config

# Suppress noisy flask logs
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/signals')
def get_signals():
    return jsonify({
        "signals": signal_state.latest_signal_status,
        "total_pnl": signal_state.total_pnl,
        "balance": signal_state.current_balance,
        "is_active": signal_state.is_bot_active,
        "session_active": signal_state.session_active,
        "session_pnl": signal_state.session_current_pnl,
        "session_tp": signal_state.session_target_profit,
        "session_sl": signal_state.session_stop_loss,
        "session_max_symbols": signal_state.session_max_symbols,
        "session_symbols": signal_state.session_symbols,
        "master_symbols": config.SYMBOLS
    })

@app.route('/api/session/start', methods=['POST'])
def start_session():
    data = request.json or {}
    tp = float(data.get("tp", 0))
    sl = float(data.get("sl", 0))
    max_symbols = int(data.get("max_symbols", 2))
    session_syms = data.get("symbols", [])
    
    if tp > 0 and sl > 0 and max_symbols > 0 and len(session_syms) > 0:
        signal_state.session_target_profit = tp
        signal_state.session_stop_loss = sl
        signal_state.session_max_symbols = max_symbols
        signal_state.session_symbols = session_syms
        signal_state.session_start_equity = signal_state.current_balance
        signal_state.session_active = True
        signal_state.is_bot_active = True
        
        # Clear out UI for symbols not in the session!
        signal_state.latest_signal_status = {
            s: {"status": "Session Started... Scanning", "color": "gray", "time": None} 
            for s in session_syms
        }
        
        signal_state.save_session()
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Invalid Config or No Symbols Selected"})

@app.route('/api/session/stop', methods=['POST'])
def stop_session():
    signal_state.session_active = False
    signal_state.save_session()
    return jsonify({"success": True})

@app.route('/api/toggle', methods=['POST'])
def toggle_bot():
    data = request.json or {}
    if "is_active" in data:
        signal_state.is_bot_active = bool(data["is_active"])
    return jsonify({"success": True})

@app.route('/api/close/<symbol>', methods=['POST'])
def close_symbol(symbol):
    signal_state.manual_close_requests.add(symbol)
    return jsonify({"success": True})

@app.route('/api/close_all', methods=['POST'])
def close_all():
    signal_state.manual_close_requests.add("ALL")
    return jsonify({"success": True})

def run_dashboard_server():
    print("\n" + "="*50)
    print("🚀 Premium Dashboard LIVE at http://localhost:5000")
    print("="*50 + "\n")
    # Run server (threaded and blocking within its own thread)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
