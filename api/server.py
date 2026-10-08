import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from flask import Flask, jsonify, request, send_from_directory
try:
    from flask_cors import CORS
    _has_cors = True
except ImportError:
    _has_cors = False

app = Flask(__name__)

if _has_cors:
    CORS(app)

_scanner_ref = None
_last_scan_time = None
_next_scan_time = None
_live_state = []


def set_scanner(scanner):
    global _scanner_ref
    _scanner_ref = scanner


def update_scan_times(last, next_):
    global _last_scan_time, _next_scan_time
    _last_scan_time = last
    _next_scan_time = next_


def set_live_state(state: list):
    global _live_state
    _live_state = state


DASHBOARD_DIR = Path(__file__).parent.parent / "dashboard"


@app.route("/")
def dashboard():
    return send_from_directory(str(DASHBOARD_DIR), "index.html")


@app.route("/api/live")
def api_live():
    """Returns the full current scan state for all pairs — including filtered ones."""
    return jsonify(_live_state)


@app.route("/api/signals")
def api_signals():
    from core.signals import get_recent_signals
    signals = get_recent_signals(50)
    return jsonify(signals)


@app.route("/api/scan")
def api_scan():
    if _scanner_ref is None:
        return jsonify({"error": "Scanner not initialised"}), 503
    try:
        results = _scanner_ref.run_scan()
        return jsonify([
            {
                "pair": r.pair,
                "strategy": r.strategy_name,
                "stage": r.stage,
                "buy_stop_price": r.buy_stop_price,
                "context_passed": r.context_passed,
                "entry_passed": r.entry_passed,
                "timestamp": r.timestamp.isoformat(),
            }
            for r in results
        ])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pairs")
def api_pairs():
    if _scanner_ref is None:
        return jsonify([])
    pairs = _scanner_ref.config.get("watchlist", [])
    return jsonify(pairs)


@app.route("/api/status")
def api_status():
    connected = False
    if _scanner_ref:
        try:
            connected = _scanner_ref.fetcher.check_connectivity()
        except Exception:
            pass
    return jsonify({
        "last_scan": _last_scan_time.isoformat() if _last_scan_time else None,
        "next_scan": _next_scan_time.isoformat() if _next_scan_time else None,
        "api_connected": connected,
        "server_time_utc": datetime.now(timezone.utc).isoformat(),
    })


def run_server(host="0.0.0.0", port=5001, debug=False):
    app.run(host=host, port=port, debug=debug, use_reloader=False)
