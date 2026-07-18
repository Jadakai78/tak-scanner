# server.py — JHL Holdings live signal API + terminal feed server
from flask import Flask, jsonify, send_file, redirect
from flask_cors import CORS
import json
import threading
import importlib
import sys
from pathlib import Path
from datetime import datetime, timezone

app = Flask(__name__)
CORS(app)

# ── Launch scheduler as a background daemon thread ──────────────────────────
# This is what makes the scanner run entirely on Railway with no local machine
# involvement. gunicorn starts server.py, server.py starts the scan loop.
def _start_scheduler():
    try:
        import scheduler as sched
        sched.run()   # blocking loop — runs in daemon thread
    except Exception as exc:
        import logging
        logging.getLogger("server").error("Scheduler failed to start: %s", exc)

_sched_thread = threading.Thread(target=_start_scheduler, daemon=True, name="scheduler")
_sched_thread.start()

BASE = Path(__file__).resolve().parent
SIGNAL_BUS = BASE / "signal_bus.json"
CF_WORKER_URL = "https://jhl-signal-bus.blazing0478.workers.dev"

ACCOUNTS = [
    {"account_id": "eval_4_25k",    "name": "Eval 4 $25K DRAGON",  "recommended_risk_per_trade": 177.0},
    {"account_id": "starter_3_10k", "name": "Starter 3 $10K",      "recommended_risk_per_trade": 130.0},
    {"account_id": "starter_2_10k", "name": "Starter 2 $10K",      "recommended_risk_per_trade":  66.0},
    {"account_id": "eval_1_5k",     "name": "Eval 1 $5K",          "recommended_risk_per_trade":  13.0},
]


def load_signal_bus():
    """Load bus from local file first, fall back to CF worker KV endpoint."""
    try:
        data = json.loads(SIGNAL_BUS.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        import urllib.request
        with urllib.request.urlopen(f"{CF_WORKER_URL}/api/signals", timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

    # Inject account data
    baselines = data.get("session_baselines", {})
    accounts = []
    for acct in ACCOUNTS:
        aid = acct["account_id"]
        baseline = baselines.get(aid, acct["recommended_risk_per_trade"])
        accounts.append({
            "account_id": aid,
            "name": acct["name"],
            "baseline": baseline,
            "current_equity": baseline,
            "recommended_risk_per_trade": acct["recommended_risk_per_trade"],
            "mode": "FULL_AGGRESSION",
        })
    data["accounts"] = accounts
    return data


# ── API routes ──────────────────────────────────────────────────────────────

@app.route("/api/signals")
def signals():
    try:
        return jsonify(load_signal_bus())
    except Exception as e:
        return jsonify({"error": str(e), "last_scan": None}), 500


@app.route("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "publish_mode": "direct-writer",
        "server_dependency": "optional"
    })


# ── Static / feed serving ───────────────────────────────────────────────────

@app.route("/")
@app.route("/index.html")
def feed():
    terminal = BASE / "jhl-live-terminal.html"
    if terminal.exists():
        return send_file(terminal)
    # fallback to old worker feed
    return redirect(CF_WORKER_URL)


@app.route("/jhl-snapshot-adapter.module.js")
def adapter():
    f = BASE / "jhl-snapshot-adapter.module.js"
    if f.exists():
        return send_file(f, mimetype="application/javascript")
    return "Not found", 404


@app.route("/manifest.webmanifest")
def manifest():
    f = BASE / "manifest.webmanifest"
    if f.exists():
        return send_file(f, mimetype="application/manifest+json")
    return "Not found", 404


@app.route("/sw.js")
def sw():
    f = BASE / "sw.js"
    if f.exists():
        return send_file(f, mimetype="application/javascript")
    return "Not found", 404


@app.route("/icon-192.png")
def icon192():
    return send_file(BASE / "icon-192.png") if (BASE / "icon-192.png").exists() else ("", 404)


@app.route("/icon-512.png")
def icon512():
    return send_file(BASE / "icon-512.png") if (BASE / "icon-512.png").exists() else ("", 404)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
