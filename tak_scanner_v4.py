"""tak_scanner_v4.py — JHL Holdings main scanner entry point.

Wires all specialists → orchestrator → publisher → signal bus → CF KV push.
Called by scheduler.py every 20 minutes.
"""
from __future__ import annotations

import json
import logging
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("tak_scanner_v4")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)

MODULE_DIR = Path(__file__).resolve().parent
SIGNAL_BUS_PATH = MODULE_DIR / "signal_bus.json"

# CF KV direct-write credentials
CF_ACCOUNT_ID = "ea17be7c9b13c5f9c1fec378a44e9e39"
CF_KV_NS_ID   = "e93558412bde4922828325e714bc44d8"
CF_API_TOKEN  = "cfut_mlCYHlnsJWOJb4KUU22dSiaUVu8Qk0KhMMHopHeq2fb3cef8"
CF_KV_URL     = (
    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
    f"/storage/kv/namespaces/{CF_KV_NS_ID}/values/signal_bus"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_prop_pairs() -> List[str]:
    """Load the 54 prop pairs from pair_universe or config."""
    try:
        from pair_universe import PairUniverse
        pu = PairUniverse()
        pairs = pu.get_prop_pairs()
        if pairs:
            return [p.upper() for p in pairs]
    except Exception as exc:
        logger.warning("PairUniverse.get_prop_pairs failed: %s", exc)

    # Hard fallback — known prop pairs
    return [
        "BTC","ETH","SOL","XRP","ADA","AVAX","MATIC","DOT","LINK","ATOM",
        "UNI","LTC","BCH","NEAR","ICP","FIL","ALGO","VET","SAND","MANA",
        "APE","AAVE","CRV","SNX","MKR","COMP","YFI","SUSHI","1INCH","BAL",
        "DOGE","SHIB","PEPE","FLOKI","BONK","WIF","POPCAT","MOODENG",
        "SUI","APT","ARB","OP","INJ","SEI","TIA","PYTH","JUP","W",
        "TON","NOT","HMSTR","CATI","DOGS",
    ]


def build_registry():
    """Instantiate all specialists and register them."""
    from scannerspecialist_registry import SpecialistRegistry
    from engineadapter_v4 import EngineSpecialistAdapter

    registry = SpecialistRegistry()

    specialists_to_load = [
        ("S1", "s1_sniper", "S1Sniper"),
        ("S2", "s2_trend_rider", "S2TrendRider"),
        ("S3", "s3_gimba_volatile", "S3GimbaVolatile"),
        ("S4", "s4_mean_reversion", "S4MeanReversion"),
        ("S5", "s5_ema_cross", "S5EMACross"),
        ("S6", "s6_reversal", "S6Reversal"),
        ("S7", "s7_range_scalper", "S7RangeScalper"),
        ("S9", "s9_capitulation", "S9Capitulation"),
        ("S10", "s10_gimba_range", "S10GimbaRange"),
    ]

    for name, module_name, class_name in specialists_to_load:
        try:
            mod = __import__(module_name)
            cls = getattr(mod, class_name)
            instance = cls()
            adapter = EngineSpecialistAdapter(name=name, engine=instance)
            registry.register(name, adapter)
            logger.info("Registered specialist: %s (%s)", name, class_name)
        except Exception as exc:
            logger.error("Failed to load specialist %s: %s", name, exc)

    # S8 is an overlay — wired via ConvictionScorer, not as a standalone specialist
    logger.info("Registry built: %s specialists", len(registry.names()))
    return registry


def get_fear_greed() -> Dict[str, Any]:
    """Fetch Fear & Greed from Alternative.me API."""
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


def classify_regime(fg_score: int) -> str:
    """Simple regime from Fear & Greed score."""
    if fg_score >= 75:
        return "TREND_UP"
    if fg_score >= 55:
        return "TREND_UP"
    if fg_score >= 45:
        return "RANGE"
    if fg_score >= 25:
        return "TREND_DOWN"
    return "FEAR"


def build_pair_contexts(pairs: List[str], regime: str, fg_score: int, ohlc_map: Dict):
    """Build PairContext objects for each pair."""
    from scannermodels import PairContext

    contexts = []
    for pair in pairs:
        ohlc_df = ohlc_map.get(pair)
        ctx = PairContext(
            pair=pair,
            market_regime=regime,
            timeframe="4h",
            fear_greed=float(fg_score),
            indicators={"ohlc_df": ohlc_df},
        )
        contexts.append(ctx)
    return contexts


def fetch_ohlc_map(pairs: List[str]) -> Dict[str, Any]:
    """Fetch 4H OHLC for all pairs via PairUniverse."""
    ohlc_map: Dict[str, Any] = {}
    try:
        from pair_universe import PairUniverse
        pu = PairUniverse()
        for pair in pairs:
            try:
                df = pu.fetch_ohlc(f"{pair}USD", interval=240)
                if df is not None and len(df) > 0:
                    ohlc_map[pair] = df
            except Exception as exc:
                logger.debug("OHLC fetch failed %s: %s", pair, exc)
    except Exception as exc:
        logger.error("PairUniverse unavailable: %s", exc)
    logger.info("OHLC fetched for %d / %d pairs", len(ohlc_map), len(pairs))
    return ohlc_map


def push_to_cf(payload_bytes: bytes) -> bool:
    """Write payload directly to CF KV via REST API."""
    try:
        req = urllib.request.Request(
            CF_KV_URL,
            data=payload_bytes,
            method="PUT",
            headers={
                "Authorization": f"Bearer {CF_API_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            logger.info("CF KV push OK — HTTP %s", resp.status)
        return True
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")[:200]
        logger.error("CF KV push HTTP error: %s %s — %s", e.code, e.reason, body)
    except Exception as exc:
        logger.error("CF KV push failed: %s", exc)
    return False


def build_bus_payload(
    result: Any,
    fg: Dict[str, Any],
    regime: str,
    pairs: List[str],
    scan_started: str,
    scan_completed: str,
    worker_push_ok: bool,
) -> Dict[str, Any]:
    """Map ScanResult → canonical bus schema the feed adapter expects."""
    from signalbusschema import build_signal_bus_payload

    # Get base payload from schema builder
    base = build_signal_bus_payload(result)

    # Flatten signals for the adapter (legacy shape)
    def flatten(published_list):
        out = []
        for ps in published_list:
            p = ps.payload if hasattr(ps, "payload") else ps
            if isinstance(p, dict):
                identity = p.get("identity", {})
                summary = p.get("summary", {})
                evidence = p.get("evidence", {})
                out.append({
                    "pair": identity.get("pair", ""),
                    "bias": identity.get("side", "LONG"),
                    "engine": identity.get("setup_type", ""),
                    "grade": _score_to_grade(float(summary.get("score", 0))),
                    "conviction": round(float(summary.get("score", 0)) / 100.0, 3),
                    "entry": evidence.get("entry_idea", 0),
                    "sl": evidence.get("stop_idea", 0),
                    "tp": evidence.get("target_idea", 0),
                    "rr": evidence.get("rr", 0),
                    "regime": regime,
                    "intent": "EXECUTE" if summary.get("execution_ready") else "WATCH",
                    "prop": True,
                    "mtf_verdict": p.get("context", {}).get("mtf_verdict", "unknown"),
                })
        return out

    all_signals = flatten(result.live_signals) + flatten(result.caution_signals)
    killed = [
        {"pair": ps.pair if hasattr(ps, "pair") else ps.get("pair",""), "reason": "killed"}
        for ps in result.killed_signals
    ]

    regime_map = {p: regime for p in pairs}

    payload = {
        # Canonical top-level fields (feed adapter reads these)
        "last_scan": scan_completed,
        "next_scan": None,
        "f_g": fg,
        "active_pairs": len(pairs),
        "dead_pairs": 0,
        "pair_universe": {"count": len(pairs)},
        "signals": all_signals,
        "killed_signals": killed,
        "regime_map": regime_map,
        "sprint_mode": False,
        "worker_push_ok": worker_push_ok,
        "bus_write_ok": True,
        "scan_duration_sec": None,
        "session_stats": {
            "signals_fired": len(all_signals),
            "killed": len(killed),
            "s_grade": sum(1 for s in all_signals if s.get("grade") == "S"),
            "a_grade": sum(1 for s in all_signals if s.get("grade") == "A"),
        },
        "environment": "railway",
        "generated_at": scan_completed,
        # Also keep raw buckets for schema compatibility
        "live_signals": base.get("live_signals", []),
        "caution_signals": base.get("caution_signals", []),
        "audit": base.get("audit", {}),
    }
    return payload


def _score_to_grade(score: float) -> str:
    if score >= 88:
        return "S"
    if score >= 75:
        return "A"
    if score >= 62:
        return "B"
    return "C"


def run():
    scan_started = utc_now()
    logger.info("=== TAK Scanner v4 starting — %s ===", scan_started)

    # 1. Fear & Greed
    fg = get_fear_greed()
    fg_score = fg["score"]
    regime = classify_regime(fg_score)
    logger.info("F&G: %s (%s) → regime: %s", fg_score, fg["label"], regime)

    # 2. Pairs
    pairs = load_prop_pairs()
    logger.info("Pairs loaded: %d", len(pairs))

    # 3. OHLC
    ohlc_map = fetch_ohlc_map(pairs)

    # 4. Registry
    registry = build_registry()
    if not registry.names():
        logger.error("No specialists loaded — aborting scan")
        return

    # 5. Contexts
    contexts = build_pair_contexts(pairs, regime, fg_score, ohlc_map)

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
        "fgscore": fg_score,
        "market_phase": fg["label"],
        "timeframe": "4h",
        "regime": regime,
    }

    candidates = orchestrator.run(contexts, shared_state=shared_state)
    scan_completed = utc_now()

    publisher = ScannerPublisher()
    result = publisher.publish(candidates)

    live = len(result.live_signals)
    caution = len(result.caution_signals)
    killed = len(result.killed_signals)
    logger.info("Scan complete — live=%d caution=%d killed=%d", live, caution, killed)

    # 7. Build payload
    payload = build_bus_payload(
        result=result,
        fg=fg,
        regime=regime,
        pairs=pairs,
        scan_started=scan_started,
        scan_completed=scan_completed,
        worker_push_ok=False,  # will update after push
    )

    # 8. Write to disk
    payload_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    SIGNAL_BUS_PATH.write_bytes(payload_bytes)
    logger.info("signal_bus.json written — %d bytes", len(payload_bytes))

    # 9. Push to CF KV
    ok = push_to_cf(payload_bytes)
    payload["worker_push_ok"] = ok
    if ok:
        # Re-write with correct push status
        SIGNAL_BUS_PATH.write_bytes(
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        )

    logger.info("=== TAK Scanner v4 done — push_ok=%s ===", ok)
    return result


if __name__ == "__main__":
    run()
