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
from typing import Any, Dict, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("takscannerv4")

MODULE_DIR = Path(__file__).resolve().parent
SIGNAL_BUS_PATH = MODULE_DIR / "signal_bus.json"

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
        ("S1", "s1_sniper",        "S1Sniper"),
        ("S2", "s2_trend_rider",   "S2TrendRider"),
        ("S3", "s3_gimba_volatile","S3GimbaVolatile"),
        ("S4", "s4_mean_reversion","S4MeanReversion"),
        ("S5", "s5_ema_cross",     "S5EMACross"),
        ("S6", "s6_reversal",      "S6Reversal"),
        ("S7", "s7_range_scalper", "S7RangeScalper"),
        ("S9", "s9_capitulation",  "S9Capitulation"),
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
            p = ps.payload if hasattr(ps, "payload") else {}
            identity = p.get("identity", {})
            summary  = p.get("summary", {})
            evidence = p.get("evidence", {})
            score    = float(summary.get("score", 0))
            out.append({
                "pair":       identity.get("pair", ""),
                "bias":       identity.get("side", "LONG"),
                "engine":     identity.get("setup_type", ""),
                "grade":      _score_to_grade(score),
                "conviction": round(score / 100.0, 3),
                "entry":      evidence.get("entry_idea", 0),
                "sl":         evidence.get("stop_idea", 0),
                "tp":         evidence.get("target_idea", 0),
                "rr":         evidence.get("rr", 0),
                "regime":     regime,
                "intent":     "EXECUTE" if summary.get("execution_ready") else "WATCH",
                "prop":       True,
                "mtf_verdict": p.get("context", {}).get("mtf_verdict", "unknown"),
            })
        return out

    all_signals = flatten(result.live_signals) + flatten(result.caution_signals)
    killed = [
        {"pair": getattr(ps, "pair", ""), "reason": "killed"}
        for ps in result.killed_signals
    ]
    prop_pairs = [r.get("pair", "") for r in pair_rows if r.get("is_prop")]

    return {
        "last_scan":        scan_completed,
        "next_scan":        None,
        "f_g":              fg,
        "active_pairs":     len(pair_rows),
        "dead_pairs":       0,
        "pair_universe":    {"count": len(pair_rows), "prop_count": len(prop_pairs)},
        "signals":          all_signals,
        "killed_signals":   killed,
        "regime_map":       {r.get("pair", ""): regime for r in pair_rows},
        "sprint_mode":      False,
        "worker_push_ok":   push_ok,
        "bus_write_ok":     True,
        "environment":      "railway",
        "generated_at":     scan_completed,
        "session_stats": {
            "signals_fired": len(all_signals),
            "killed":        len(killed),
            "s_grade":       sum(1 for s in all_signals if s.get("grade") == "S"),
            "a_grade":       sum(1 for s in all_signals if s.get("grade") == "A"),
        },
        "audit": result.audit,
    }


def run():
    scan_started = utc_now()
    logger.info("=== TAK Scanner v4 start fg=%s label=%s ===", "?", "?")

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

    # 3. Add fgscore to each row so intake/adapter can use it
    for row in pair_rows:
        row.setdefault("fgscore", fg_score)
        row.setdefault("market_regime", "unknown")

    # 4. Build PairContext objects via ScannerPairIntake
    from scannerpair_intake import ScannerPairIntake
    intake = ScannerPairIntake()
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

    live    = len(result.live_signals)
    caution = len(result.caution_signals)
    killed  = len(result.killed_signals)
    logger.info("V4 bus write ok live=%d caution=%d killed=%d", live, caution, killed)

    # 7. Build payload
    payload       = build_bus_payload(result, fg, "FEAR", pair_rows,
                                      scan_started, scan_completed, False)
    payload_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    SIGNAL_BUS_PATH.write_bytes(payload_bytes)

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
