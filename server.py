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

# ── Kraken bot state (shared across requests) ───────────────────────────────
import threading as _threading
_kraken_bot = None
_kraken_lock = _threading.Lock()
_kraken_cycle_log: list = []   # last 20 cycle summaries
_kraken_open_trades: dict = {} # pair -> {opened_at, signal, status}

def _start_kraken_bot():
    global _kraken_bot, _kraken_cycle_log, _kraken_open_trades
    try:
        from kraken_bot_v4 import kraken_bot_v4
        bot = kraken_bot_v4()
        with _kraken_lock:
            _kraken_bot = bot
        import logging as _log
        _klog = _log.getLogger("kraken_bot_daemon")
        _klog.info("Kraken bot daemon started — dryrun=%s", bot.executor.dryrun)
        while True:
            try:
                summary = bot.process_cycle()
                summary["ts"] = __import__('datetime').datetime.now(
                    __import__('datetime').timezone.utc).isoformat()
                with _kraken_lock:
                    _kraken_open_trades = dict(bot._open_trades)
                    _kraken_cycle_log.append(summary)
                    if len(_kraken_cycle_log) > 20:
                        _kraken_cycle_log.pop(0)
            except Exception as exc:
                _klog.error("Kraken bot cycle error: %s", exc)
            __import__('time').sleep(bot.poll_interval)
    except Exception as exc:
        import logging as _log
        _log.getLogger("server").warning("Kraken bot failed to start: %s", exc)

_kraken_thread = threading.Thread(target=_start_kraken_bot, daemon=True, name="kraken-bot")
_kraken_thread.start()

# ── Position monitor — council eyes on live trades ───────────────────────────
def _start_position_monitor():
    try:
        from position_monitor import run_monitor  # type: ignore
        run_monitor()
    except Exception as exc:
        import logging as _log
        _log.getLogger("server").error("Position monitor failed to start: %s", exc)

_monitor_thread = threading.Thread(target=_start_position_monitor, daemon=True, name="position-monitor")
_monitor_thread.start()

# ── Signal aging daemon ───────────────────────────────────────────────────────
# ── Signal aging — auto-expire stale PENDING signals ────────────────────────
# Runs every 5 minutes. Removes PENDING signals whose conviction dropped
# below 68 OR whose trap_risk crept to ≥0.65 since they were written.
# CONFIRM signals are never touched here — position_monitor owns those.

import logging as _logging
_aging_logger = _logging.getLogger("signal_aging")

AGING_INTERVAL   = 300   # seconds between aging passes
AGING_CONV_FLOOR = 68.0  # conviction floor for PENDING signals
AGING_TRAP_GATE  = 0.65  # trap ceiling for PENDING signals
AGING_MAX_AGE    = 600   # 10 min max lifetime for an unactioned PENDING signal

def _signal_aging_loop():
    """Background thread — prunes stale PENDING signals from the bus."""
    import time as _time
    _aging_logger.info("Signal aging loop started — interval=%ds", AGING_INTERVAL)
    while True:
        try:
            _time.sleep(AGING_INTERVAL)
            _run_signal_aging()
        except Exception as exc:
            _aging_logger.warning("Signal aging error: %s", exc)

def _run_signal_aging():
    """Single aging pass — remove PENDING signals that have gone stale."""
    try:
        bus = json.loads(SIGNAL_BUS.read_text())
    except Exception:
        return

    now = datetime.now(timezone.utc)
    signals_in  = bus.get("signals", [])
    signals_out = []
    removed = []

    for sig in signals_in:
        verdict = sig.get("december_verdict", "PENDING")

        # Never touch CONFIRM or WAIT — position_monitor owns CONFIRM, human owns WAIT
        if verdict in ("CONFIRM", "WAIT"):
            signals_out.append(sig)
            continue

        # REJECT — already dead, keep for history but don't prune here
        if verdict == "REJECT":
            signals_out.append(sig)
            continue

        # PENDING — check conviction + trap + age
        raw_conv  = float(sig.get("conviction", sig.get("final_conviction", 100)) or 100)
        conv      = raw_conv * 100 if raw_conv <= 1.0 else raw_conv
        trap_risk = float(sig.get("trap_risk", sig.get("trap_score", 0)) or 0)

        # Age check
        fired_at_str = sig.get("fired_at", "")
        age_seconds  = 9999
        if fired_at_str:
            try:
                fired_at = datetime.fromisoformat(fired_at_str.replace("Z", "+00:00"))
                age_seconds = (now - fired_at).total_seconds()
            except Exception:
                pass

        reasons = []
        if conv < AGING_CONV_FLOOR:
            reasons.append(f"conviction {conv:.1f}<{AGING_CONV_FLOOR}")
        if trap_risk >= AGING_TRAP_GATE:
            reasons.append(f"trap {trap_risk:.2f}≥{AGING_TRAP_GATE}")
        if age_seconds > AGING_MAX_AGE:
            reasons.append(f"age {int(age_seconds)}s>{AGING_MAX_AGE}s")

        if reasons:
            pair = sig.get("pair","?")
            _aging_logger.info("SIGNAL AGED OUT %s — %s", pair, " | ".join(reasons))
            removed.append(pair)
            sig["december_verdict"] = "EXPIRED"
            sig["expired_at"]       = now.isoformat()
            sig["expiry_reason"]    = " | ".join(reasons)
            signals_out.append(sig)
        else:
            signals_out.append(sig)

    if removed:
        bus["signals"] = signals_out
        SIGNAL_BUS.write_bytes(json.dumps(bus, ensure_ascii=False, indent=2).encode())
        _aging_logger.info("Aging pass complete — expired %d signal(s): %s", len(removed), removed)

_aging_thread = threading.Thread(target=_signal_aging_loop, daemon=True, name="signal-aging")
_aging_thread.start()

BASE = Path(__file__).resolve().parent
SIGNAL_BUS = Path("/app/data/signal_bus.json")
# Ensure volume dir exists
SIGNAL_BUS.parent.mkdir(parents=True, exist_ok=True)
# Seed from CF KV on cold start if volume is empty
if not SIGNAL_BUS.exists():
    try:
        import urllib.request as _ur
        with _ur.urlopen(f"https://jhl-signal-bus.blazing0478.workers.dev/api/signals", timeout=8) as _r:
            SIGNAL_BUS.write_bytes(_r.read())
    except Exception:
        pass
CF_WORKER_URL = "https://jhl-signal-bus.blazing0478.workers.dev"

ACCOUNTS = [
    {"account_id": "eval_4_25k",    "name": "Eval 4 $25K DRAGON",  "recommended_risk_per_trade": 177.0},
    {"account_id": "starter_3_10k", "name": "Starter 3 $10K",      "recommended_risk_per_trade": 130.0},
    {"account_id": "starter_2_10k", "name": "Starter 2 $10K",      "recommended_risk_per_trade":  66.0},
    {"account_id": "eval_1_5k",     "name": "Eval 1 $5K",          "recommended_risk_per_trade":  13.0},
]


def _seed_from_kv():
    """On cold start, pull CF KV into local volume so feed isn't empty."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{CF_WORKER_URL}/api/signals", timeout=8) as resp:
            data = resp.read()
            SIGNAL_BUS.write_bytes(data)
    except Exception:
        pass


def load_signal_bus():
    """Load bus from local disk — fall back to CF KV seed if file missing."""
    if not SIGNAL_BUS.exists():
        _seed_from_kv()
    try:
        data = json.loads(SIGNAL_BUS.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {"signals": [], "rts_signals": []}

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


# ── Execution endpoints ─────────────────────────────────────────────────────

def _fetch_bus_from_kv() -> dict:
    """Read bus from local disk — one service, scanner and server share same filesystem."""
    try:
        return json.loads(SIGNAL_BUS.read_text())
    except Exception:
        return {"signals": []}


def _push_verdict_to_kv(bus: dict):
    """Push updated bus (with verdict changes) back to CF KV so all services see it."""
    import urllib.request, urllib.error
    try:
        payload = json.dumps(bus, ensure_ascii=False, indent=2).encode("utf-8")
        req = urllib.request.Request(
            f"{CF_WORKER_URL}/update",
            data=payload,
            headers={"Content-Type": "application/json", "X-JHL-Secret": "jhl2026dragon"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.status == 200
    except Exception as _e:
        _aging_logger.warning("KV verdict push failed: %s", _e)
        return False


@app.route("/api/position/execute", methods=["POST"])
def position_execute():
    """Human confirms an A-grade signal — flips december_verdict to CONFIRM."""
    from flask import request as freq
    body = freq.get_json(silent=True) or {}
    pair      = body.get("pair", "")
    bias      = body.get("bias", "")
    engine    = body.get("engine", "")
    fired_at  = body.get("fired_at", "")
    if not pair:
        return jsonify({"ok": False, "error": "pair required"}), 400
    try:
        bus = _fetch_bus_from_kv()
        updated = False
        for sig in bus.get("signals", []):
            if (sig.get("pair") == pair and
                    (not bias   or sig.get("bias")     == bias) and
                    (not engine or sig.get("engine")   == engine)):
                sig["december_verdict"] = "CONFIRM"
                sig["executed_at"] = datetime.now(timezone.utc).isoformat()
                updated = True
        if updated:
            SIGNAL_BUS.write_bytes(json.dumps(bus, ensure_ascii=False, indent=2).encode())
            _push_verdict_to_kv(bus)
        # Also log to open trades state
        with _kraken_lock:
            _kraken_open_trades[pair] = {
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "pair": pair, "bias": bias, "engine": engine,
                "fired_at": fired_at, "status": "EXECUTED",
            }
        return jsonify({"ok": True, "pair": pair, "verdict": "CONFIRM"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/position/wait", methods=["POST"])
def position_wait():
    """Human marks a signal WAIT — valid setup, holding for better entry timing."""
    from flask import request as freq
    body = freq.get_json(silent=True) or {}
    pair = body.get("pair", "")
    if not pair:
        return jsonify({"ok": False, "error": "pair required"}), 400
    try:
        bus = _fetch_bus_from_kv()
        for sig in bus.get("signals", []):
            if sig.get("pair") == pair:
                sig["december_verdict"] = "WAIT"
                sig["wait_at"] = datetime.now(timezone.utc).isoformat()
        SIGNAL_BUS.write_bytes(json.dumps(bus, ensure_ascii=False, indent=2).encode())
        _push_verdict_to_kv(bus)
        return jsonify({"ok": True, "pair": pair, "verdict": "WAIT"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.route("/api/position/reject", methods=["POST"])
def position_reject():
    """Human rejects a signal — flips december_verdict to REJECT."""
    from flask import request as freq
    body = freq.get_json(silent=True) or {}
    pair   = body.get("pair", "")
    if not pair:
        return jsonify({"ok": False, "error": "pair required"}), 400
    try:
        bus = _fetch_bus_from_kv()
        for sig in bus.get("signals", []):
            if sig.get("pair") == pair:
                sig["december_verdict"] = "REJECT"
                sig["rejected_at"] = datetime.now(timezone.utc).isoformat()
        SIGNAL_BUS.write_bytes(json.dumps(bus, ensure_ascii=False, indent=2).encode())
        with _kraken_lock:
            _kraken_open_trades.pop(pair, None)
        return jsonify({"ok": True, "pair": pair, "verdict": "REJECT"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── Kraken bot status endpoints ──────────────────────────────────────────────

@app.route("/api/kraken/status")
def kraken_status():
    """Live Kraken bot status — equity, mode, cycle log."""
    try:
        with _kraken_lock:
            bot = _kraken_bot
            cycles = list(_kraken_cycle_log)
            trades = dict(_kraken_open_trades)
        if bot is None:
            return jsonify({"running": False, "reason": "bot not started"})

        # Try to fetch live balance from Kraken
        balance = {}
        try:
            bal_raw = bot.executor._kraken_request(
                "/0/private/Balance", {}) if hasattr(bot.executor, '_kraken_request') else {}
            balance = bal_raw.get("result", {})
        except Exception:
            pass

        return jsonify({
            "running": True,
            "dryrun":  bot.executor.dryrun,
            "seat":    bot.executor.seat,
            "poll_interval_sec": bot.poll_interval,
            "auto_grade":   bot.auto_grade,
            "manual_grade": bot.manual_grade,
            "min_conviction": bot.min_conviction,
            "open_trades":  trades,
            "cycle_log":    cycles[-5:],  # last 5 cycles
            "balance":      balance,
            "daily_loss":   getattr(bot.executor, 'daily_loss', 0.0),
        })
    except Exception as exc:
        return jsonify({"running": False, "error": str(exc)}), 500


@app.route("/api/kraken/positions")
def kraken_positions():
    """Live open positions from Kraken API + internal trade tracker."""
    try:
        with _kraken_lock:
            bot = _kraken_bot
            trades = dict(_kraken_open_trades)
        live_positions = []
        if bot and not bot.executor.dryrun:
            try:
                raw = bot.executor.get_open_positions()
                live_positions = raw if isinstance(raw, list) else []
            except Exception:
                pass
        return jsonify({
            "live_positions": live_positions,
            "tracked_trades": list(trades.values()),
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500



@app.route("/api/seed", methods=["POST", "GET"])
def seed_from_kv():
    """Force-pull CF KV data into local volume. Call after cold start."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"{CF_WORKER_URL}/api/signals", timeout=10) as resp:
            data = resp.read()
            SIGNAL_BUS.write_bytes(data)
            bus = json.loads(data)
            sigs = len(bus.get("signals", []))
            return jsonify({"ok": True, "signals_seeded": sigs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
