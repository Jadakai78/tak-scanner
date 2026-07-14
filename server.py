# server.py — JHL Holdings live signal API + feed server
from flask import Flask, jsonify, send_file, redirect
from flask_cors import CORS
import json
import threading
import requests as _requests
from pathlib import Path
from datetime import datetime, timezone

# Cloudflare Worker KV push — keeps the published PWA in sync
CF_WORKER_URL = "https://jhl-signal-bus.blazing0478.workers.dev/update"
CF_SECRET     = "jhl2026dragon"

def _push_to_cf(payload: str):
    """Fire-and-forget push to Cloudflare KV. Runs in background thread."""
    try:
        _requests.put(
            CF_WORKER_URL,
            headers={"X-JHL-Secret": CF_SECRET, "Content-Type": "application/json"},
            data=payload,
            timeout=10,
        )
    except Exception:
        pass  # Never block the local server

app = Flask(__name__)
CORS(app)

SIGNAL_BUS = Path(__file__).resolve().parent / "signal_bus.json"
# Feed is served by CF worker — no local HTML needed
FEED_URL = "https://jhl-signal-bus.blazing0478.workers.dev"


# Prop account metadata — matches casino_counter SESSION_BASELINES
ACCOUNTS = [
    {"account_id": "eval_4_25k",    "name": "Eval 4 $25K DRAGON",  "recommended_risk_per_trade": 177.0},
    {"account_id": "starter_3_10k", "name": "Starter 3 $10K",      "recommended_risk_per_trade": 130.0},
    {"account_id": "starter_2_10k", "name": "Starter 2 $10K",      "recommended_risk_per_trade":  66.0},
    {"account_id": "eval_1_5k",     "name": "Eval 1 $5K",          "recommended_risk_per_trade":  13.0},
]


@app.route("/api/signals")
def signals():
    try:
        data = json.loads(SIGNAL_BUS.read_text())

        # Build accounts[] array from session_baselines for PWA account panel
        baselines = data.get("session_baselines", {})
        accounts = []
        for acct in ACCOUNTS:
            aid = acct["account_id"]
            baseline = baselines.get(aid, acct["recommended_risk_per_trade"])
            accounts.append({
                "account_id": aid,
                "name": acct["name"],
                "baseline": baseline,
                "current_equity": baseline,   # static — no live API on prop firms
                "recommended_risk_per_trade": acct["recommended_risk_per_trade"],
                "mode": "FULL_AGGRESSION",     # dynamic mode lives in casino_state.json
            })
        data["accounts"] = accounts

        # Push to Cloudflare KV in background (keeps published PWA in sync)
        threading.Thread(target=_push_to_cf, args=(json.dumps(data),), daemon=True).start()

        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "last_scan": None}), 500


@app.route("/api/health")
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()})


@app.route("/")
@app.route("/index-2.html")
@app.route("/index.html")
def feed():
    return redirect(FEED_URL)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
