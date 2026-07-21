# server.py — JHL Holdings live signal API + terminal feed server (Railway-only)
from flask import Flask, jsonify, send_file
from flask_cors import CORS
import json
import threading
from pathlib import Path
from datetime import datetime, timezone

app = Flask(__name__)
CORS(app)

BASE = Path(__file__).resolve().parent
SIGNAL_BUS = Path("/app/data/signal_bus.json")
SIGNAL_BUS.parent.mkdir(parents=True, exist_ok=True)

# ── Launch scheduler as a background daemon thread ──────────────────────────
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
_kraken_cycle_log: list = []
_kraken_open_trades: dict = {}
_kraken_skip_stats: dict = {
    "verdict": 0,
    "conviction": 0,
    "trap": 0,
    "age": 0,
    "duplicate": 0,
    "missing_fields": 0,
    "unknown": 0,
}
_kraken_skip_samples: list = []  # last 20 skip samples


def _record_skip(reason: str, pair: str, detail: str = ""):
    """Track skip reasons across cycles so '4 skipped' can be diagnosed quickly."""
    with _kraken_lock:
        if reason not in _kraken_skip_stats:
            _kraken_skip_stats[reason] = 0
        _kraken_skip_stats[reason] += 1
        _kraken_skip_samples.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "pair": pair,
            "reason": reason,
            "detail": detail,
        })
        if len(_kraken_skip_samples) > 20:
            _kraken_skip_samples.pop(0)


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
                summary["ts"] = datetime.now(timezone.utc).isoformat()

                # Attempt to capture skip reason details if bot exposes them
                # (non-breaking: only reads optional attributes)
                try:
                    skipped = int(summary.get("skipped", 0) or 0)
                except Exception:
                    skipped = 0

                if skipped > 0:
                    # Optional fields the bot may set; we consume if present
                    skip_reasons = summary.get("skipped_by_reason") or {}
                    if isinstance(skip_reasons, dict):
                        for k, v in skip_reasons.items():
                            try:
                                count = int(v or 0)
                            except Exception:
                                count = 0
                            if count > 0:
                                with _kraken_lock:
                                    _kraken_skip_stats[k] = _kraken_skip_stats.get(k, 0) + count

                with _kraken_lock:
                    _kraken_open_trades = dict(getattr(bot, "_open_trades", {}))
                    _kraken_cycle_log.append(summary)
                    if len(_kraken_cycle_log) > 20:
                        _kraken_cycle_log.pop(0)
            except Exception as exc:
                _klog.error("Kraken bot cycle error: %s", exc)

            __import__("time").sleep(bot.poll_interval)
    except Exception as exc:
        import logging as _log
        _log.getLogger("server").warning("Kraken bot failed to start: %s", exc)

_kraken_thread = threading.Thread(target=_start_kraken_bot, daemon=True, name="kraken-bot")
_kraken_thread.start()

# ── Position monitor ─────────────────────────────────────────────────────────
def _start_position_monitor():
    try:
        from position_monitor import run_monitor  # type: ignore
        run_monitor()
    except Exception as exc:
        import logging as _log
        _log.getLogger("server").error("Position monitor failed to start: %s", exc)

_monitor_thread = threading.Thread(target=_start_position_monitor, daemon=True, name="position-monitor")
_monitor_thread.start()

# ── Signal aging ─────────────────────────────────────────────────────────────
import logging as _logging
_aging_logger = _logging.getLogger("signal_aging")

AGING_ENABLED    = True
AGING_INTERVAL   = 300    # 5 min
AGING_CONV_FLOOR = 68.0
AGING_TRAP_GATE  = 0.65
AGING_MAX_AGE    = 3600   # was 600; now 60 min for stability

def _norm_conviction_pct(sig: dict) -> float:
    raw = None
    for k in ("score", "conviction", "final_conviction"):
        if sig.get(k) is not None:
            raw = sig.get(k)
            break
    if raw is None and sig.get("confidence") is not None:
        raw = sig.get("confidence")
    try:
        x = float(raw) if raw is not None else 100.0
    except Exception:
        return 100.0
    return x * 100.0 if x <= 1.0 else x

def _norm_verdict(sig: dict) -> str:
    raw = (
        sig.get("december_verdict")
        or sig.get("verdict")
        or sig.get("status")
        or sig.get("state")
        or "PENDING"
    )
    v = str(raw).upper().strip()
    aliases = {
        "EXECUTED": "CONFIRM",
        "APPROVED": "CONFIRM",
        "HOLD": "WAIT",
        "DENY": "REJECT",
        "KILLED": "REJECT"
    }
    return aliases.get(v, v if v in {"PENDING", "CONFIRM", "WAIT", "REJECT", "EXPIRED"} else "PENDING")

def _norm_fired_at(sig: dict) -> str:
    return sig.get("fired_at") or sig.get("created_at") or sig.get("ts") or sig.get("timestamp") or ""

def _norm_trap_risk(sig: dict) -> float:
    try:
        return float(sig.get("trap_risk", sig.get("trap_score", 0)) or 0)
    except Exception:
        return 0.0

def _signal_age_seconds(sig: dict, now: datetime) -> int:
    fired_at_str = _norm_fired_at(sig)
    if not fired_at_str:
        return 9999
    try:
        fired_at = datetime.fromisoformat(str(fired_at_str).replace("Z", "+00:00"))
        return int((now - fired_at).total_seconds())
    except Exception:
        return 9999

def _normalize_signal_inplace(sig: dict) -> dict:
    sig["_conviction_pct"] = _norm_conviction_pct(sig)
    sig["_verdict"] = _norm_verdict(sig)
    sig["_fired_at"] = _norm_fired_at(sig)
    sig["_trap_risk"] = _norm_trap_risk(sig)
    return sig

def _read_bus() -> dict:
    try:
        return json.loads(SIGNAL_BUS.read_text())
    except Exception:
        return {"signals": [], "rts_signals": []}

def _write_bus(bus: dict):
    SIGNAL_BUS.write_bytes(json.dumps(bus, ensure_ascii=False, indent=2).encode("utf-8"))

def _signal_aging_loop():
    import time as _time
    _aging_logger.info("Signal aging loop started — enabled=%s interval=%ds max_age=%ss", AGING_ENABLED, AGING_INTERVAL, AGING_MAX_AGE)
    while True:
        try:
            _time.sleep(AGING_INTERVAL)
            if AGING_ENABLED:
                _run_signal_aging()
        except Exception as exc:
            _aging_logger.warning("Signal aging error: %s", exc)

def _run_signal_aging():
    bus = _read_bus()
    now = datetime.now(timezone.utc)
    signals_in = bus.get("signals", [])
    signals_out = []
    removed = []

    for sig in signals_in:
        _normalize_signal_inplace(sig)
        verdict = sig["_verdict"]

        # Keep already finalized signals
        if verdict in ("CONFIRM", "WAIT", "REJECT", "EXPIRED"):
            signals_out.append(sig)
            continue

        conv = sig["_conviction_pct"]
        trap_risk = sig["_trap_risk"]
        age_seconds = _signal_age_seconds(sig, now)

        reasons = []
        if conv < AGING_CONV_FLOOR:
            reasons.append(f"conviction {conv:.1f}<{AGING_CONV_FLOOR}")
        if trap_risk >= AGING_TRAP_GATE:
            reasons.append(f"trap {trap_risk:.2f}>={AGING_TRAP_GATE}")
        if age_seconds > AGING_MAX_AGE:
            reasons.append(f"age {int(age_seconds)}s>{AGING_MAX_AGE}s")

        if reasons:
            pair = sig.get("pair", "?")
            _aging_logger.info("SIGNAL AGED OUT %s — %s", pair, " | ".join(reasons))
            removed.append(pair)
            sig["december_verdict"] = "EXPIRED"
            sig["verdict"] = "EXPIRED"
            sig["expired_at"] = now.isoformat()
            sig["expiry_reason"] = " | ".join(reasons)
            sig["_verdict"] = "EXPIRED"

            # diagnostic feed for skipped root-cause
            if age_seconds > AGING_MAX_AGE:
                _record_skip("age", pair, f"{age_seconds}s>{AGING_MAX_AGE}s")
            if conv < AGING_CONV_FLOOR:
                _record_skip("conviction", pair, f"{conv:.1f}<{AGING_CONV_FLOOR}")
            if trap_risk >= AGING_TRAP_GATE:
                _record_skip("trap", pair, f"{trap_risk:.2f}>={AGING_TRAP_GATE}")

        signals_out.append(sig)

    if removed:
        bus["signals"] = signals_out
        _write_bus(bus)
        _aging_logger.info("Aging pass complete — expired %d signal(s): %s", len(removed), removed)

_aging_thread = threading.Thread(target=_signal_aging_loop, daemon=True, name="signal-aging")
_aging_thread.start()

ACCOUNTS = [
    {"account_id": "eval_4_25k",    "name": "Eval 4 $25K DRAGON",  "recommended_risk_per_trade": 177.0},
    {"account_id": "starter_3_10k", "name": "Starter 3 $10K",      "recommended_risk_per_trade": 130.0},
    {"account_id": "starter_2_10k", "name": "Starter 2 $10K",      "recommended_risk_per_trade":  66.0},
    {"account_id": "eval_1_5k",     "name": "Eval 1 $5K",          "recommended_risk_per_trade":  13.0},
]

def load_signal_bus():
    data = _read_bus()
    now = datetime.now(timezone.utc)

    data["last_scan"] = (
        data.get("lastscan")
        or data.get("last_scan")
        or (data.get("tak") or {}).get("lastscan")
    )

    signals = data.get("signals") or []
    for s in signals:
        _normalize_signal_inplace(s)

    data["active_pairs"] = sum(1 for s in signals if s.get("_verdict", "PENDING") == "PENDING")
    data["market_active_pairs"] = data.get("activepairs") or (data.get("oracle") or {}).get("activepairs") or 0

    counts = {"PENDING": 0, "CONFIRM": 0, "WAIT": 0, "REJECT": 0, "EXPIRED": 0}
    reason_counts = {"age": 0, "conviction": 0, "trap": 0, "verdict": 0, "missing_fields": 0}
    for s in signals:
        v = s.get("_verdict", "PENDING")
        counts[v] = counts.get(v, 0) + 1

        # derive coarse skip reasons for debugging visibility
        conv = s.get("_conviction_pct", 100.0)
        trap = s.get("_trap_risk", 0.0)
        age = _signal_age_seconds(s, now)

        if v != "PENDING":
            reason_counts["verdict"] += 1
        if conv < AGING_CONV_FLOOR:
            reason_counts["conviction"] += 1
        if trap >= AGING_TRAP_GATE:
            reason_counts["trap"] += 1
        if age > AGING_MAX_AGE:
            reason_counts["age"] += 1
        if not s.get("pair") or (not s.get("engine") and not s.get("bot")):
            reason_counts["missing_fields"] += 1

    data["_signal_verdict_counts"] = counts
    data["_signal_skip_reason_estimate"] = reason_counts
    data["_server_normalizer"] = "railway_only_v2"
    data["_aging"] = {
        "enabled": AGING_ENABLED,
        "interval_sec": AGING_INTERVAL,
        "conv_floor": AGING_CONV_FLOOR,
        "trap_gate": AGING_TRAP_GATE,
        "max_age_sec": AGING_MAX_AGE,
    }

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

# ── API routes ───────────────────────────────────────────────────────────────
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
        "publish_mode": "railway-local-file",
        "server_dependency": "none"
    })

# ── Static / feed serving ────────────────────────────────────────────────────
@app.route("/")
@app.route("/index.html")
def feed():
    terminal = BASE / "jhl-live-terminal.html"
    if terminal.exists():
        return send_file(terminal)
    return "Terminal file missing", 404

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

# ── Execution endpoints ──────────────────────────────────────────────────────
@app.route("/api/position/execute", methods=["POST"])
def position_execute():
    from flask import request as freq
    body = freq.get_json(silent=True) or {}
    pair = body.get("pair", "")
    bias = body.get("bias", "")
    engine = body.get("engine", "")
    fired_at = body.get("fired_at", "")
    if not pair:
        return jsonify({"ok": False, "error": "pair required"}), 400
    try:
        bus = _read_bus()
        updated = False
        for sig in bus.get("signals", []):
            if (sig.get("pair") == pair and
                (not bias or sig.get("bias") == bias) and
                (not engine or sig.get("engine") == engine)):
                sig["december_verdict"] = "CONFIRM"
                sig["verdict"] = "CONFIRM"
                sig["executed_at"] = datetime.now(timezone.utc).isoformat()
                updated = True
        if updated:
            _write_bus(bus)
        else:
            _record_skip("missing_fields", pair, "execute called but no matching signal found")
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
    from flask import request as freq
    body = freq.get_json(silent=True) or {}
    pair = body.get("pair", "")
    if not pair:
        return jsonify({"ok": False, "error": "pair required"}), 400
    try:
        bus = _read_bus()
        hit = False
        for sig in bus.get("signals", []):
            if sig.get("pair") == pair:
                sig["december_verdict"] = "WAIT"
                sig["verdict"] = "WAIT"
                sig["wait_at"] = datetime.now(timezone.utc).isoformat()
                hit = True
        _write_bus(bus)
        if not hit:
            _record_skip("missing_fields", pair, "wait called but no matching signal found")
        return jsonify({"ok": True, "pair": pair, "verdict": "WAIT"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

@app.route("/api/position/reject", methods=["POST"])
def position_reject():
    from flask import request as freq
    body = freq.get_json(silent=True) or {}
    pair = body.get("pair", "")
    if not pair:
        return jsonify({"ok": False, "error": "pair required"}), 400
    try:
        bus = _read_bus()
        hit = False
        for sig in bus.get("signals", []):
            if sig.get("pair") == pair:
                sig["december_verdict"] = "REJECT"
                sig["verdict"] = "REJECT"
                sig["rejected_at"] = datetime.now(timezone.utc).isoformat()
                hit = True
        _write_bus(bus)
        if not hit:
            _record_skip("missing_fields", pair, "reject called but no matching signal found")
        with _kraken_lock:
            _kraken_open_trades.pop(pair, None)
        return jsonify({"ok": True, "pair": pair, "verdict": "REJECT"})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500

# ── Kraken bot status endpoints ──────────────────────────────────────────────
@app.route("/api/kraken/status")
def kraken_status():
    try:
        with _kraken_lock:
            bot = _kraken_bot
            cycles = list(_kraken_cycle_log)
            trades = dict(_kraken_open_trades)
            skip_stats = dict(_kraken_skip_stats)
            skip_samples = list(_kraken_skip_samples)

        if bot is None:
            return jsonify({"running": False, "reason": "bot not started"})

        balance = {}
        try:
            bal_raw = bot.executor._kraken_request("/0/private/Balance", {}) if hasattr(bot.executor, "_kraken_request") else {}
            balance = bal_raw.get("result", {})
        except Exception:
            pass

        return jsonify({
            "running": True,
            "dryrun": bot.executor.dryrun,
            "seat": bot.executor.seat,
            "poll_interval_sec": bot.poll_interval,
            "auto_grade": bot.auto_grade,
            "manual_grade": bot.manual_grade,
            "min_conviction": bot.min_conviction,
            "open_trades": trades,
            "cycle_log": cycles[-5:],
            "balance": balance,
            "daily_loss": getattr(bot.executor, "daily_loss", 0.0),
            "skip_stats": skip_stats,
            "skip_samples": skip_samples[-10:],
        })
    except Exception as exc:
        return jsonify({"running": False, "error": str(exc)}), 500

@app.route("/api/kraken/positions")
def kraken_positions():
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
def seed_local():
    """POST: write provided bus directly. GET: report current signal count."""
    from flask import request as freq
    if freq.method == "POST" and freq.data:
        try:
            bus = json.loads(freq.data)
            _write_bus(bus)
            return jsonify({"ok": True, "signals_seeded": len(bus.get("signals", []))})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    bus = _read_bus()
    return jsonify({"ok": True, "signals_seeded": len(bus.get("signals", []))})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
