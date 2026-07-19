"""signal_bus_schema.py — Canonical snapshot contract.

This module defines the ONE canonical snapshot object that scanner, worker, Railway API, and UI all consume.
No more mixed legacy/new fields. This is the single source of truth.

Top-level sections:
  - meta: schema version, timestamps, universe counts, fear/greed
  - session: signals fired/killed, S-grade count, scan duration, quiet-hours flag
  - health: worker/publisher success, last push status, scan runtime sanity
  - regimes: per-pair regime classification (TRENDUP/TRENDDOWN/RANGE/VOLATILE/DEAD)
  - signals: S-engine outputs with full context objects and governance fields
  - alerts: derived human-facing notices (e.g., "Extreme Fear with 0 signals in 27.97s")
  - diagnostics: MTF errors, unexpected kwargs, HTTP fetch timeouts, dev-only detail
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Canonical Signal Object
# ---------------------------------------------------------------------------
def build_canonical_signal(
    *,
    pair: str,
    bias: str,
    engine: str,
    regime: str,
    entry: float,
    sl: float,
    tp: float,
    rr: float,
    score: float,
    grade: str,
    # Context objects (from _common.py context builders)
    trend_context: Dict[str, Any],
    st_context: Dict[str, Any],
    volume_context: Dict[str, Any],
    volatility_context: Dict[str, Any],
    structure_context: Dict[str, Any],
    # Execution layer (RTS backend)
    defensive_score: float,
    offensive_score: float,
    trap_risk: float,
    survivability: str,
    execution_intent: str,  # "execute", "wait", "cut", "ignore"
    invalidation_basis: str,
    target_basis: str,
    # Governance fields (Remi + Council + Tak)
    remi_review: str,  # "approved", "caution", "cut", "kill"
    council_claim: str,  # "no_claim", "claim", "lead_claim", "co_claim", "yield", "removed"
    tak_publish_auth: bool,
    route_reason: str,
    veto_reasons: Optional[List[str]] = None,
    # Bot attachments
    claimants: Optional[List[str]] = None,
    required_tools_satisfied: Optional[Dict[str, bool]] = None,
    common_indicator_validity: Optional[bool] = None,
    weighted_bot_scores: Optional[Dict[str, float]] = None,
    lead_claimant: Optional[str] = None,
    # Trade-safe vs diagnostics-only notes
    trade_safe_notes: Optional[str] = None,
    diagnostics_notes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Assemble a fully-formed canonical signal object with all governance, context, and execution fields.
    This is the outward signal model that the bus publishes.
    """
    signal = {
        # Identity
        "pair": pair,
        "bias": bias.upper(),
        "engine": engine,
        "regime": regime,
        # Execution
        "entry": round(entry, 8),
        "sl": round(sl, 8),
        "tp": round(tp, 8),
        "rr": round(rr, 2),
        "score": round(score, 2),
        "grade": grade,
        # Context objects (common indicators describe context)
        "trend_context": trend_context,
        "st_context": st_context,
        "volume_context": volume_context,
        "volatility_context": volatility_context,
        "structure_context": structure_context,
        # Execution layer (RTS owns execution intelligence)
        "defensive_score": round(defensive_score, 2),
        "offensive_score": round(offensive_score, 2),
        "trap_risk": round(trap_risk, 2),
        "survivability": survivability,
        "execution_intent": execution_intent,
        "invalidation_basis": invalidation_basis,
        "target_basis": target_basis,
        # Governance (Remi/Council/Tak)
        "remi_review": remi_review,
        "council_claim": council_claim,
        "tak_publish_auth": tak_publish_auth,
        "route_reason": route_reason,
        "veto_reasons": veto_reasons or [],
        # Bot attachments & claim logic
        "claimants": claimants or [],
        "required_tools_satisfied": required_tools_satisfied or {},
        "common_indicator_validity": common_indicator_validity,
        "weighted_bot_scores": weighted_bot_scores or {},
        "lead_claimant": lead_claimant,
        # Notes (separate trade-safe vs diagnostics-only)
        "trade_safe_notes": trade_safe_notes,
        "diagnostics_notes": diagnostics_notes,
    }
    return signal


# ---------------------------------------------------------------------------
# Canonical Snapshot Builder
# ---------------------------------------------------------------------------
def build_canonical_snapshot(
    *,
    # Meta
    schema_version: str = "1.0.0",
    last_scan: str,
    next_scan: str,
    fg_score: int,
    fg_label: str,
    active_pairs: int,
    dead_pairs: int,
    universe_count: int,
    # Session
    signals_fired: int,
    signals_killed: int,
    s_grade_count: int,
    scan_duration_sec: float,
    quiet_hours: bool,
    # Health
    worker_push_success: bool,
    last_push_status: str,
    scan_runtime_summary: str,
    # Regimes
    regime_map: Dict[str, str],
    regime_counts: Optional[Dict[str, int]] = None,
    # Signals
    signals: List[Dict[str, Any]],
    # Alerts
    alerts: List[str],
    # Diagnostics
    diagnostics: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Build the canonical snapshot object that everything downstream consumes.
    This is the single source of truth for worker, Railway API, and UI.
    """
    snapshot = {
        "meta": {
            "schema_version": schema_version,
            "last_scan": last_scan,
            "next_scan": next_scan,
            "fg_score": fg_score,
            "fg_label": fg_label,
            "active_pairs": active_pairs,
            "dead_pairs": dead_pairs,
            "universe_count": universe_count,
        },
        "session": {
            "signals_fired": signals_fired,
            "signals_killed": signals_killed,
            "s_grade_count": s_grade_count,
            "scan_duration_sec": round(scan_duration_sec, 2),
            "quiet_hours": quiet_hours,
        },
        "health": {
            "worker_push_success": worker_push_success,
            "last_push_status": last_push_status,
            "scan_runtime_summary": scan_runtime_summary,
        },
        "regimes": {
            "map": regime_map,
            "counts": regime_counts or {
                "TRENDUP": 0,
                "TRENDDOWN": 0,
                "RANGE": 0,
                "VOLATILE": 0,
                "DEAD": 0,
            },
        },
        "signals": signals,
        "alerts": alerts,
        "diagnostics": diagnostics or [],
    }
    return snapshot


# ---------------------------------------------------------------------------
# Legacy Field Mapping (for transition period)
# ---------------------------------------------------------------------------
def map_legacy_to_canonical(legacy_bus: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map legacy bus payload (lastscan, nextscan, activepairs, etc.) to canonical schema.
    Use this during transition to avoid breaking existing consumers.
    Once all consumers are migrated, remove this function.
    """
    # Extract legacy fields
    last_scan = legacy_bus.get("last_scan", "")
    next_scan = legacy_bus.get("next_scan", "")
    fg = legacy_bus.get("feargreed", {})
    fg_score = fg.get("score", 50)
    fg_label = fg.get("label", "Neutral")
    active_pairs = legacy_bus.get("active_pairs", 0)
    dead_pairs = legacy_bus.get("dead_pairs", 0)
    universe_count = legacy_bus.get("pair_universe_count", 0)

    session_stats = legacy_bus.get("session_stats", {})
    signals_fired = session_stats.get("signals_fired", 0)
    signals_killed = session_stats.get("signals_killed", 0)
    s_grade_count = session_stats.get("s_grade_count", 0)
    scan_duration_sec = session_stats.get("scan_duration_sec", 0.0)
    quiet_hours = legacy_bus.get("quiet_hours", False)

    # Health (worker push status)
    worker_push_success = True  # Assume success if no explicit field
    last_push_status = "OK 200" if worker_push_success else "ERROR"
    scan_runtime_summary = f"Scan completed in {scan_duration_sec:.2f}s with {signals_fired} signals"

    # Regimes
    regime_map = legacy_bus.get("regime_map", {})
    regime_counts = {}
    for regime in ["TRENDUP", "TRENDDOWN", "RANGE", "VOLATILE", "DEAD"]:
        regime_counts[regime] = sum(1 for r in regime_map.values() if r == regime)

    # Signals (legacy bucket structure: live_signals, caution_signals, killed_signals)
    live_signals = legacy_bus.get("live_signals", [])
    caution_signals = legacy_bus.get("caution_signals", [])
    killed_signals = legacy_bus.get("killed_signals", [])

    # Map legacy signals to canonical format (basic mapping — engines will provide full context)
    signals = []
    for sig in live_signals + caution_signals:
        signals.append({
            "pair": sig.get("pair", ""),
            "bias": sig.get("bias", ""),
            "engine": sig.get("engine", ""),
            "regime": sig.get("regime", ""),
            "entry": sig.get("entry", 0.0),
            "sl": sig.get("sl", 0.0),
            "tp": sig.get("tp", 0.0),
            "rr": sig.get("rr", 0.0),
            "score": sig.get("score", 0.0),
            "grade": sig.get("grade", "C"),
            # Context objects — fill with defaults if missing
            "trend_context": sig.get("trend_context", {}),
            "st_context": sig.get("st_context", {}),
            "volume_context": sig.get("volume_context", {}),
            "volatility_context": sig.get("volatility_context", {}),
            "structure_context": sig.get("structure_context", {}),
            # Execution layer — fill with defaults
            "defensive_score": sig.get("defensive_score", 0.0),
            "offensive_score": sig.get("offensive_score", 0.0),
            "trap_risk": sig.get("trap_risk", 0.0),
            "survivability": sig.get("survivability", "unknown"),
            "execution_intent": sig.get("execution_intent", "wait"),
            "invalidation_basis": sig.get("invalidation_basis", ""),
            "target_basis": sig.get("target_basis", ""),
            # Governance — fill with defaults
            "remi_review": sig.get("remi_review", "approved"),
            "council_claim": sig.get("council_claim", "no_claim"),
            "tak_publish_auth": sig.get("tak_publish_auth", True),
            "route_reason": sig.get("route_reason", ""),
            "veto_reasons": sig.get("veto_reasons", []),
            # Bot attachments — fill with defaults
            "claimants": sig.get("claimants", []),
            "required_tools_satisfied": sig.get("required_tools_satisfied", {}),
            "common_indicator_validity": sig.get("common_indicator_validity", True),
            "weighted_bot_scores": sig.get("weighted_bot_scores", {}),
            "lead_claimant": sig.get("lead_claimant", None),
            # Notes
            "trade_safe_notes": sig.get("trade_safe_notes", ""),
            "diagnostics_notes": sig.get("diagnostics_notes", ""),
        })

    # Alerts (derived from session stats and fg)
    alerts = []
    if signals_fired == 0:
        alerts.append(f"{fg_label} with 0 signals in {scan_duration_sec:.2f}s")
    elif s_grade_count > 0:
        alerts.append(f"{s_grade_count} S-grade signals detected")

    # Diagnostics (legacy audit log)
    diagnostics = legacy_bus.get("audit", [])

    # Build canonical snapshot
    return build_canonical_snapshot(
        last_scan=last_scan,
        next_scan=next_scan,
        fg_score=fg_score,
        fg_label=fg_label,
        active_pairs=active_pairs,
        dead_pairs=dead_pairs,
        universe_count=universe_count,
        signals_fired=signals_fired,
        signals_killed=signals_killed,
        s_grade_count=s_grade_count,
        scan_duration_sec=scan_duration_sec,
        quiet_hours=quiet_hours,
        worker_push_success=worker_push_success,
        last_push_status=last_push_status,
        scan_runtime_summary=scan_runtime_summary,
        regime_map=regime_map,
        regime_counts=regime_counts,
        signals=signals,
        alerts=alerts,
        diagnostics=diagnostics,
    )
