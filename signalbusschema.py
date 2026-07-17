from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from scannermodels import PublishedSignal, ScanResult


SCHEMA_VERSION = "v1"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _safe_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _payload(signal: PublishedSignal) -> Dict[str, Any]:
    return _safe_dict(signal.payload)


def _review(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_dict(payload.get("review"))


def _council(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_dict(payload.get("council"))


def _context(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_dict(payload.get("context"))


def _evidence(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _safe_dict(payload.get("evidence"))


def serialize_signal(signal: PublishedSignal) -> Dict[str, Any]:
    payload = _payload(signal)
    review = _review(payload)
    council = _council(payload)
    context = _context(payload)
    evidence = _evidence(payload)

    trend_context = _safe_dict(context.get("trend_context"))
    st_context = _safe_dict(context.get("st_context"))
    volume_context = _safe_dict(context.get("volume_context"))
    volatility_context = _safe_dict(context.get("volatility_context"))
    structure_context = _safe_dict(context.get("structure_context"))
    mtf_context = {
        "verdict": context.get("mtf_verdict"),
        "score": context.get("mtf_score"),
        "alignment": context.get("mtf_alignment"),
    }

    return {
        "identity": {
            "signal_id": signal.candidate_id,
            "pair": signal.pair,
            "timeframe": context.get("timeframe"),
            "setup_type": signal.setup_type,
            "side": signal.side,
            "bucket": signal.bucket,
            "route": signal.route,
        },
        "summary": {
            "thesis": signal.thesis,
            "specialist": signal.specialist,
            "score": signal.score,
            "confidence": context.get("confidence"),
            "execution_ready": signal.execution_ready,
            "status": context.get("status", signal.bucket),
        },
        "market_context": {
            "regime": context.get("market_regime") or context.get("regime"),
            "trend_context": trend_context,
            "st_context": st_context,
            "volume_context": volume_context,
            "volatility_context": volatility_context,
            "structure_context": structure_context,
            "mtf_context": mtf_context,
        },
        "execution": {
            "entry_idea": payload.get("entry_idea"),
            "stop_idea": payload.get("stop_idea"),
            "target_idea": payload.get("target_idea"),
            "rr_estimate": context.get("rr_estimate"),
            "offensive_score": context.get("offensive_score"),
            "defensive_score": context.get("defensive_score"),
            "trap_risk": context.get("trap_risk"),
            "survivability": context.get("survivability"),
            "liquidity_proximity": context.get("liquidity_proximity"),
            "execution_intent": context.get("execution_intent"),
            "invalidation_basis": context.get("invalidation_basis"),
            "target_basis": context.get("target_basis"),
            "cut_now": context.get("cut_now", False),
        },
        "claims": {
            "lead_bot": context.get("lead_bot") or signal.specialist,
            "attached_bots": _safe_list(context.get("attached_bots")) or [signal.specialist],
            "co_claims": _safe_list(context.get("co_claims")),
            "claim_status": context.get("claim_status"),
            "claim_scores": _safe_list(context.get("claim_scores")),
            "tool_checks": _safe_list(context.get("tool_checks")),
            "common_indicator_ok": context.get("common_indicator_ok"),
        },
        "governance": {
            "remi_decision": review.get("decision"),
            "remi_rationale": review.get("rationale"),
            "remi_caution_flags": _safe_list(review.get("caution_flags")),
            "remi_evidence_notes": _safe_list(review.get("evidence_notes")),
            "council_decision": council.get("decision"),
            "battlefield_ok": council.get("battlefield_ok"),
            "veto_reasons": _safe_list(council.get("veto_reasons")),
            "publish_allowed": signal.bucket != "killed_signals",
        },
        "diagnostics": {
            "warnings": _safe_list(signal.warnings),
            "tags": _safe_list(signal.tags),
            "raw_context": context,
            "raw_evidence": evidence,
            "legacy_payload": payload,
        },
    }


def build_canonical_snapshot(result: ScanResult) -> Dict[str, Any]:
    live = [serialize_signal(x) for x in result.live_signals]
    caution = [serialize_signal(x) for x in result.caution_signals]
    killed = [serialize_signal(x) for x in result.killed_signals]

    counts = {
        "live": len(live),
        "caution": len(caution),
        "killed": len(killed),
        "positions": len(result.positions),
    }

    snapshot = {
        "meta": {
            "generated_at": utc_now_iso(),
            "schema_version": SCHEMA_VERSION,
            "producer": "tak_scanner_v4",
            "source": "scanner",
            "snapshot_id": None,
        },
        "session": {
            "scan_started_at": result.audit.get("scan_started_at"),
            "scan_completed_at": result.audit.get("scan_completed_at"),
            "timeframe": result.audit.get("timeframe"),
            "active_pairs": _safe_list(result.audit.get("active_pairs")),
            "pair_count": result.audit.get("pair_count", 0),
            "market_phase": result.audit.get("market_phase"),
        },
        "health": {
            "scanner_ok": result.audit.get("scanner_ok", True),
            "worker_push_ok": result.audit.get("worker_push_ok"),
            "data_freshness_sec": result.audit.get("data_freshness_sec"),
            "degraded": result.audit.get("degraded", False),
            "warnings": _safe_list(result.audit.get("health_warnings")),
        },
        "regimes": {
            "global": _safe_dict(result.audit.get("global_regime")),
            "by_pair": _safe_dict(result.audit.get("regime_map")),
        },
        "signals": {
            "live": live,
            "caution": caution,
            "killed": killed,
            "positions": _safe_list(result.positions),
            "summary": counts,
        },
        "alerts": {
            "top_signals": live[:3],
            "external_ready": [x for x in live if x["summary"]["execution_ready"]],
            "props": [],
            "suppressed": [
                {
                    "signal_id": x["identity"]["signal_id"],
                    "reason": x["governance"]["remi_decision"] or "not_live",
                    "stage": x["identity"]["bucket"],
                }
                for x in caution + killed
            ],
            "summary": {
                "top_signal_count": min(len(live), 3),
                "external_ready_count": len([x for x in live if x["summary"]["execution_ready"]]),
                "suppressed_count": len(caution) + len(killed),
            },
        },
        "diagnostics": {
            "audit": _safe_dict(result.audit),
            "route_stats": _safe_dict(result.audit.get("route_stats")),
            "field_general": _safe_dict(result.audit.get("field_general")),
            "legacy_mapping": {
                "used_legacy_keys": [
                    "generated_at",
                    "live_signals",
                    "caution_signals",
                    "killed_signals",
                    "positions",
                    "audit",
                ],
                "mapped_fields": {
                    "generated_at": "meta.generated_at",
                    "live_signals": "signals.live",
                    "caution_signals": "signals.caution",
                    "killed_signals": "signals.killed",
                    "positions": "signals.positions",
                    "audit": "diagnostics.audit",
                },
                "unmapped_fields": [],
            },
        },
    }
    return snapshot


def build_signal_bus_payload(result: ScanResult) -> Dict[str, Any]:
    snapshot = build_canonical_snapshot(result)
    return {
        "meta": snapshot["meta"],
        "session": snapshot["session"],
        "health": snapshot["health"],
        "regimes": snapshot["regimes"],
        "signals": snapshot["signals"],
        "alerts": snapshot["alerts"],
        "diagnostics": snapshot["diagnostics"],
    }


def empty_signal_bus() -> Dict[str, Any]:
    empty = ScanResult()
    return build_signal_bus_payload(empty)
