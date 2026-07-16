# server.py — JHL Holdings live signal API + optional feed redirect
from flask import Flask, jsonify, redirect
from flask_cors import CORS
import json
from pathlib import Path
from datetime import datetime, timezone

app = Flask(__name__)
CORS(app)

SIGNAL_BUS = Path(__file__).resolve().parent / "signal_bus.json"
FEED_URL = "https://jhl-signal-bus.blazing0478.workers.dev"

ACCOUNTS = [
    {"account_id": "eval_4_25k",    "name": "Eval 4 $25K DRAGON",  "recommended_risk_per_trade": 177.0},
    {"account_id": "starter_3_10k", "name": "Starter 3 $10K",      "recommended_risk_per_trade": 130.0},
    {"account_id": "starter_2_10k", "name": "Starter 2 $10K",      "recommended_risk_per_trade":  66.0},
    {"account_id": "eval_1_5k",     "name": "Eval 1 $5K",          "recommended_risk_per_trade":  13.0},
]


def load_signal_bus():
    try:
        data = json.loads(SIGNAL_BUS.read_text())
    except FileNotFoundError:
        import urllib.request
        with urllib.request.urlopen(FEED_URL, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

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


@app.route("/")
@app.route("/index-2.html")
@app.route("/index.html")
def feed():
    return redirect(FEED_URL)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
