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
    return jsonify(signal_state.latest_signal_status)

def run_dashboard_server():
    print("\n" + "="*50)
    print("🚀 Premium Dashboard LIVE at http://localhost:5000")
    print("="*50 + "\n")
    # Run server (threaded and blocking within its own thread)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
