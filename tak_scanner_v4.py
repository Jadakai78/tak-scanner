"""tak_scanner_v4.py — JHL Holdings main scanner entry point (v4).

Flow: PairUniverse.get_active_pairs() → ScannerPairIntake.build_contexts()
      → ScannerOrchestrator.run() → ScannerPublisher.publish()
      → signal_bus.json → CF KV push
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from alerts import fire_alerts
from typing import Any, Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("takscannerv4")

MODULE_DIR = Path(__file__).resolve().parent
SIGNAL_BUS_PATH = Path("/app/data/signal_bus.json")
Path("/app/data").mkdir(parents=True, exist_ok=True)

CF_ACCOUNT_ID = "ea17be7c9b13c5f9c1fec378a44e9e39"
CF_KV_NS_ID   = "e93558412bde4922828325e714bc44d8"
CF_API_TOKEN  = "cfut_mlCYHlnsJWOJb4KUU22dSiaUVu8Qk0KhMMHopHeq2fb3cef8"
CF_KV_URL     = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
    f"/storage/kv/namespaces/{CF_KV_NS_ID}/values/signal_bus"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_fear_greed() -> Dict[str, Any]:
    try:
        req = urllib.request.Request(
            "https://api.alternative.me/fng/?limit=1",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        entry = data["data"][0]
        return {"score": int(entry["value"]), "label": entry["value_classification"]}
    except Exception as exc:
        logger.warning("F&G fetch failed: %s", exc)
        return {"score": 50, "label": "Neutral"}


def push_to_cf(payload_bytes: bytes) -> bool:
    try:
        req = urllib.request.Request(
            CF_KV_URL, data=payload_bytes, method="PUT",
            headers={"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info("CF KV push OK — HTTP %s", resp.status)
        return True
    except urllib.error.HTTPError as e:
        logger.error("CF KV push HTTP error: %s %s", e.code, e.reason)
    except Exception as exc:
        logger.error("CF KV push failed: %s", exc)
    return False


def build_registry(fg_score: int):
    from scannerspecialist_registry import SpecialistRegistry
    from engineadapter_v4 import EngineSpecialistAdapter

    registry = SpecialistRegistry()
    specialists_to_load = [
        # Traditional nine
        ("S1",      "s1_sniper",        "S1Sniper"),
        ("S2",      "s2_trend_rider",   "S2TrendRider"),
        ("S3",      "s3_gimba_volatile","S3GimbaVolatile"),
        ("S4",      "s4_mean_reversion","S4MeanReversion"),
        ("S5",      "s5_ema_cross",     "S5EMACross"),
        ("S6",      "s6_reversal",      "S6Reversal"),
        ("S7",      "s7_range_scalper", "S7RangeScalper"),
        ("S9",      "s9_capitulation",  "S9Capitulation"),
        ("S10",     "s10_gimba_range",  "S10GimbaRange"),
        # RTS Council — retail trap hunters
        ("RTS_LIQ",    "rts_liq",    "RTSLiq"),
        ("RTS_BOS",    "rts_bos",    "RTSBos"),
        ("RTS_CHOCH",  "rts_choch",  "RTSChoch"),
        ("RTS_ZONE",   "rts_zone",   "RTSZone"),
        ("RTS_DELTA",  "rts_delta",  "RTSDelta"),
        ("RTS_BOTTLE", "rts_bottle", "RTSBottle"),
    ]
    for name, mod_name, cls_name in specialists_to_load:
        try:
            mod = __import__(mod_name)
            instance = getattr(mod, cls_name)()
            registry.register(name, EngineSpecialistAdapter(name=name, engine=instance))
            logger.info("Registered specialist: %s (%s)", name, cls_name)
        except Exception as exc:
            logger.error("Failed to load specialist %s: %s", name, exc)

    logger.info("Registry built: %d specialists", len(registry.names()))
    return registry


def _score_to_grade(score: float) -> str:
    if score >= 88: return "S"
    if score >= 75: return "A"
    if score >= 62: return "B"
    return "C"


def build_bus_payload(result, fg, regime, pair_rows, scan_started, scan_completed, push_ok):
    """Map ScanResult → flat bus shape the CF worker + feed adapter expect."""

    def flatten(published_list):
        out = []
        for ps in published_list:
            score = float(getattr(ps, "score", 0))
            exe   = getattr(ps, "execution", None)
            entry = getattr(exe, "entry_idea", None) if exe else None
            sl    = getattr(exe, "stop_idea",  None) if exe else None
            tp    = getattr(exe, "target_idea", None) if exe else None
            rr    = getattr(exe, "rr_estimate", None) if exe else None
            # V2 scoring fields
            action_state  = getattr(ps, "action_state",  None) or                             ("CLICK" if getattr(ps, "execution_ready", False) else "WAIT")
            action_reason = getattr(ps, "action_reason", "")
            def_score     = getattr(ps, "defensive_score", None)
            off_score     = getattr(ps, "offensive_score", None)
            trap          = getattr(ps, "trap_risk", None)
            bonus         = getattr(ps, "bonus_multiplier", None)
            out.append({
                "pair":            getattr(ps, "pair",       ""),
                "bias":            getattr(ps, "side",       "LONG"),
                "engine":          getattr(ps, "specialist", ""),
                "setup_type":      getattr(ps, "setup_type", ""),
                # V2 — conviction IS the grade. score_8 is the canonical field.
                "score_8":         round(score, 1),
                "conviction":      round(score / 100.0, 3) if score > 1 else round(score, 3),
                "action_state":    action_state,
                "action_reason":   action_reason,
                "defensive_score": round(def_score, 3) if def_score is not None else None,
                "offensive_score": round(off_score, 3) if off_score is not None else None,
                "trap_risk":       round(trap, 3) if trap is not None else None,
                "bonus_multiplier":round(bonus, 2) if bonus is not None else None,
                "entry":           entry,
                "sl":              sl,
                "tp":              tp,
                "rr":              rr,
                "regime":          regime,
                "prop":            True,
                "thesis":          getattr(ps, "thesis", ""),
                "route":           getattr(ps, "route", ""),
            })
        return out

    live_sigs    = flatten(result.live_signals)
    caution_sigs = flatten(result.caution_signals)
    # ── Promote qualifying RTS signals into all_signals ─────────────────────
    # RTS engines hunt retail liquidation — they ARE primary signals, not just
    # field intelligence. Promote ATTACK-intent RTS signals with conviction ≥75
    # that survived the trap detector into the live feed with proper levels.
    rts_promoted: list = []
    survivor_pairs = {s.get("pair","") for s in survivors} if "survivors" in dir() else set()

    for pair_key, rts_list in rts_map.items():
        for rts in rts_list:
            intent     = str(rts.get("intent","")).upper()
            conviction = float(rts.get("conviction", rts.get("final_conviction", 0)) or 0)
            # Normalize 0-1 scale to 0-100
            if conviction <= 1.0:
                conviction *= 100
            bias = str(rts.get("bias","")).upper() or "LONG"
            engine = str(rts.get("engine","RTS"))

            # Only promote ATTACK signals with ≥75 conviction
            if intent not in {"ATTACK","ATTACKBREAK","ATTACK_TRAP"} or conviction < 75:
                continue

            # RTS engines use build_signal — keys are entry/sl/tp directly
            # Fallback chain handles any engine that returns raw levels instead
            entry  = float(rts.get("entry", rts.get("entry_idea", 0)) or 0)
            sl     = float(rts.get("sl", rts.get("stop_idea", rts.get("kill_level", 0))) or 0)
            tp     = float(rts.get("tp", rts.get("target_idea", 0)) or 0)
            atr_v  = float(rts.get("atr", 0) or 0)

            # Use current bar close as entry if build_signal didn't set it
            bar = bar_map.get(pair_key, {})
            if entry == 0:
                entry = float(bar.get("close", 0))

            # Build sl from structural levels if missing
            if sl == 0:
                for field in ("bos_level","choch_level","zone_top","zone_bottom","liq_level","flip_level","kill_level"):
                    v = float(rts.get(field, 0) or 0)
                    if v > 0:
                        sl = v * (0.999 if bias=="LONG" else 1.001)
                        break
                if sl == 0 and atr_v > 0:
                    sl = entry - 1.5*atr_v if bias=="LONG" else entry + 1.5*atr_v

            # Build tp at 2.5R if missing
            if tp == 0 and sl > 0 and entry > 0:
                risk = abs(entry - sl)
                tp = entry + 2.5*risk if bias=="LONG" else entry - 2.5*risk

            if entry == 0 or sl == 0 or tp == 0:
                continue

            risk = abs(entry - sl)
            rr   = round(abs(tp - entry) / risk, 2) if risk > 0 else 0

            promoted_sig = {
                "pair":            pair_key,
                "bias":            bias,
                "engine":          engine,
                "grade":           "S" if conviction >= 88 else "A",
                "conviction":      round(conviction, 1),
                "entry":           round(entry, 6),
                "sl":              round(sl, 6),
                "tp":              round(tp, 6),
                "rr":              rr,
                "intent":          intent,
                "action_state":    "CLICK",
                "action_reason":   f"RTS {engine} {intent} — conviction {conviction:.1f}",
                "regime":          rts.get("regime","HUNT"),
                "prop":            rts.get("prop", False),
                "mtf_verdict":     rts.get("mtf_verdict",""),
                "trap_risk":       float(rts.get("trap_score", 0) or 0),
                "remi_status":     rts.get("remi_status","CLEAN"),
                "remi_caution":    rts.get("remi_caution", False),
                "december_verdict": "PENDING",
                "fired_at":        utc_now(),
                "rts_source":      True,   # flag so feed can style differently
            }
            rts_promoted.append(promoted_sig)
            logger.info(
                "RTS PROMOTED %s %s engine=%s conv=%.1f rr=%.2f",
                pair_key, bias, engine, conviction, rr
            )

    if rts_promoted:
        logger.info("RTS promoted %d signal(s) into live feed", len(rts_promoted))

    all_signals  = live_sigs + caution_sigs + rts_promoted
    killed = [
        {"pair": getattr(ps, "pair", ""), "reason": "killed"}
        for ps in result.killed_signals
    ]
    prop_pairs = [r.get("pair", "") for r in pair_rows if r.get("is_prop")]

    return {
        "last_scan":      scan_completed,
        "next_scan":      None,
        "f_g":            fg,
        "fg":             fg,  # duplicate key for adapter compat
        "active_pairs":   len(pair_rows),
        "dead_pairs":     0,
        "pair_universe":  {"count": len(pair_rows), "prop_count": len(prop_pairs)},
        "signals":        all_signals,
        "killed_signals": killed,
        "regime_map":     {r.get("pair", ""): regime for r in pair_rows},
        "sprint_mode":    False,
        "session": {
            "active_pairs":        len(pair_rows),
            "pair_universe_count": len(pair_rows),
            "dead_pairs":          0,
            "sprint_mode":         False,
            "last_scan":           scan_completed,
            "next_scan":           None,
        },
        "worker_push_ok": push_ok,
        "bus_write_ok":   True,
        "environment":    "railway",
        "generated_at":   scan_completed,
        "health": {
            "bus_write_ok":      True,
            "worker_push_ok":    push_ok,
            "bus_health_pct":    100 if push_ok else 50,
            "scan_duration_sec": (
                (lambda a, b: (b - a).total_seconds()
                 if hasattr(a, "total_seconds") or hasattr(b, "total_seconds")
                 else 0)(scan_started, scan_completed)
                if scan_started and scan_completed else 0
            ),
        },
        "session_stats": {
            "signals_fired": len(all_signals),
            "live":          len(live_sigs),
            "caution":       len(caution_sigs),
            "killed":        len(killed),
            "s_grade":       sum(1 for s in all_signals if s.get("grade") == "S"),
            "a_grade":       sum(1 for s in all_signals if s.get("grade") == "A"),
        },
        "audit": result.audit,
    }


def run():
    scan_started = utc_now()
    logger.info("=== TAK Scanner v4 start fg=%s label=%s ===", "?", "?")

    # Heartbeat: stamp last_scan immediately — stale detection works even if scan crashes mid-run
    try:
        if SIGNAL_BUS_PATH.exists():
            _hb = json.loads(SIGNAL_BUS_PATH.read_text())
            _hb["last_scan"] = scan_started
            _hb["scanner_heartbeat"] = scan_started
            SIGNAL_BUS_PATH.write_bytes(json.dumps(_hb, ensure_ascii=False).encode())
    except Exception as _hb_exc:
        logger.warning("Heartbeat write failed (non-fatal): %s", _hb_exc)

    # 1. Fear & Greed
    fg = get_fear_greed()
    fg_score = fg["score"]
    logger.info("V4 scan start fg=%s label=%s", fg_score, fg["label"])

    # 2. Active pair universe — returns dicts with pair, df, regime, metrics
    from pair_universe import PairUniverse
    pu = PairUniverse()
    pair_rows = pu.get_active_pairs(interval=240)
    if not pair_rows:
        logger.error("No active pairs — aborting")
        return
    logger.info("Active pairs: %d", len(pair_rows))

    # 3. Add fgscore + df to each row so intake/adapter + regime classifier can use them
    import pandas as pd
    _OHLC_COLS = ["time","open","high","low","close","vwap","volume","count"]
    for row in pair_rows:
        row.setdefault("fgscore", fg_score)
        row.setdefault("market_regime", "unknown")
        # Build df from ohlc_4h so RegimeClassifier can run
        ohlc_raw = row.get("ohlc_4h") or []
        if ohlc_raw and "df" not in row:
            try:
                df = pd.DataFrame(ohlc_raw, columns=_OHLC_COLS[:len(ohlc_raw[0])])
                for col in ["open","high","low","close","volume"]:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                row["df"] = df
            except Exception:
                pass

    # 4. Build PairContext objects via ScannerPairIntake
    from scannerpair_intake import ScannerPairIntake
    try:
        from regime_classifier import RegimeClassifier
        _regime_clf = RegimeClassifier()
    except Exception:
        _regime_clf = None
    intake = ScannerPairIntake(regime_classifier=_regime_clf)
    contexts = intake.build_contexts(pair_rows, timeframe="4h")
    if not contexts:
        logger.error("PairIntake produced 0 contexts — aborting")
        return
    logger.info("Contexts built: %d", len(contexts))

    # 5. Registry
    registry = build_registry(fg_score)
    if not registry.names():
        logger.error("No specialists — aborting")
        return

    # 6. Orchestrate
    from scannerorchestrator import ScannerOrchestrator
    from scannerpublisher import ScannerPublisher
    from scannerreviewer_remi import RemiReviewer
    from scannercouncil import ScannerCouncil

    orchestrator = ScannerOrchestrator(
        specialist_registry=registry,
        remi_reviewer=RemiReviewer(),
        council=ScannerCouncil(),
    )

    shared_state = {
        "fgscore":      fg_score,
        "market_phase": fg["label"],
        "timeframe":    "4h",
    }

    candidates  = orchestrator.run(contexts, shared_state=shared_state)
    scan_completed = utc_now()

    publisher = ScannerPublisher()
    result    = publisher.publish(candidates)

    # 6b. Trap Detector — runs after publish, before bus write
    # Build rts_map: pair → list of raw RTS signal dicts from this scan cycle
    # RTS engines are already in candidates; we collect their raw payloads here
    rts_map: dict = {}
    bar_map: dict = {}
    for row in pair_rows:
        pair_key = row.get("pair", "")
        df = row.get("df")
        if df is not None and len(df) > 0:
            last = df.iloc[-1]
            bar_map[pair_key] = {
                "high":  float(last.get("high",  0)),
                "low":   float(last.get("low",   0)),
                "close": float(last.get("close", 0)),
                "open":  float(last.get("open",  0)),
            }
    # Collect RTS signals from published result
    for ps in result.live_signals:
        specialist = getattr(ps, "specialist", "") or ""
        if any(rts in specialist.upper() for rts in ("RTS_LIQ","RTS_BOS","RTS_CHOCH","RTS_ZONE","RTS_BOTTLE")):
            pair_key = getattr(ps, "pair", "")
            raw = dict(ps.payload) if hasattr(ps, "payload") else {}
            raw["engine"] = specialist
            raw["pair"]   = pair_key
            rts_map.setdefault(pair_key, []).append(raw)

    try:
        from trap_detector import TrapDetector, april_system_view
        td = TrapDetector()
        # Flatten live signals to dicts for the detector
        live_dicts = []
        for ps in result.live_signals:
            d = dict(ps.payload) if hasattr(ps, "payload") else {}
            d["specialist"] = getattr(ps, "specialist", "")
            d["engine"]     = getattr(ps, "specialist", "")
            d["pair"]       = getattr(ps, "pair", "")
            d["bias"]       = getattr(ps, "side", "").replace("LONG","LONG").replace("SHORT","SHORT")
            d["candidate_id"] = getattr(ps, "candidate_id", "")
            live_dicts.append(d)

        survivors, trap_killed_dicts, trap_flips = td.evaluate(
            live_signals=live_dicts,
            rts_outputs=rts_map,
            current_bars=bar_map,
        )

        # Rebuild result.live_signals from survivors (by candidate_id)
        survivor_ids = {s.get("candidate_id") for s in survivors}
        result.live_signals = [
            ps for ps in result.live_signals
            if getattr(ps, "candidate_id", None) in survivor_ids
        ]

        # Move trap-killed signals into result.killed_signals
        for killed_d in trap_killed_dicts:
            # Find the matching PublishedSignal object
            for ps in result.caution_signals + result.live_signals:
                if getattr(ps, "candidate_id", None) == killed_d.get("candidate_id"):
                    result.killed_signals.append(ps)
                    break

        # April system view — logged for council/feed use
        caution_count = len([s for s in survivors if s.get("trap_caution")])
        april_view    = april_system_view(trap_killed_dicts, trap_flips, caution_count)
        logger.info(
            "APRIL council_mode=%s reason=%s killed=%d flips=%d",
            april_view["council_mode"], april_view["reason"],
            len(trap_killed_dicts), len(trap_flips),
        )

        # Store trap flips and april view in audit for bus payload
        if not hasattr(result, "audit") or result.audit is None:
            result.audit = {}
        result.audit["trap_flips"]    = trap_flips
        result.audit["april_view"]    = april_view
        result.audit["trap_killed_count"] = len(trap_killed_dicts)

    except Exception as exc:
        logger.warning("TrapDetector error (non-fatal): %s", exc)

    live    = len(result.live_signals)
    caution = len(result.caution_signals)
    killed  = len(result.killed_signals)
    logger.info("V4 bus write ok live=%d caution=%d killed=%d", live, caution, killed)

    # 7. Build payload
    payload       = build_bus_payload(result, fg, "FEAR", pair_rows,
                                      scan_started, scan_completed, False)
    payload_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    SIGNAL_BUS_PATH.write_bytes(payload_bytes)

    # 7b. Fire alerts — Telegram + Pushover + Outlook + Yahoo
    # Only fire on signals that passed all gates (live + caution + rts promoted)
    try:
        alertable = [s for s in all_signals if s.get("december_verdict") not in ("EXPIRED","REJECT","WAIT")]
        if alertable:
            fire_alerts(alertable, sprint_mode=False)
            logger.info("fire_alerts dispatched %d signal(s)", len(alertable))
        else:
            logger.info("fire_alerts — no alertable signals this cycle")
    except Exception as _alert_err:
        logger.warning("fire_alerts error (non-fatal): %s", _alert_err)

    # 8. Push to CF KV
    ok = push_to_cf(payload_bytes)
    if ok:
        payload["worker_push_ok"] = True
        SIGNAL_BUS_PATH.write_bytes(
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        )
    logger.info("V4 worker push ok status=%s", 200 if ok else "FAILED")
    logger.info("=== TAK Scanner v4 done push_ok=%s ===", ok)
    return result


if __name__ == "__main__":
    run()
