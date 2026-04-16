from flask import Flask, jsonify, render_template
import logging
import signal_state

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
        "max_dd": signal_state.max_drawdown_usd
    })

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
