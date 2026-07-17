from __future__ import annotations

from typing import Any, Dict, Iterable, List

from scannermodels import CandidateSignal, PublishedSignal, ScanResult


def _merge_payload(candidate: CandidateSignal) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "candidate_id": candidate.candidate_id,
        "pair": candidate.pair,
        "setup_type": candidate.setup_type,
        "side": candidate.side,
        "specialist": candidate.specialist,
        "confidence": candidate.confidence,
        "score": candidate.score,
        "entry_idea": candidate.entry_idea,
        "stop_idea": candidate.stop_idea,
        "target_idea": candidate.target_idea,
        "evidence": dict(candidate.evidence),
        "context": dict(candidate.context),
    }

    if candidate.review is not None:
        payload["review"] = {
            "decision": candidate.review.decision,
            "adjusted_score": candidate.review.adjusted_score,
            "confidence_delta": candidate.review.confidence_delta,
            "rationale": candidate.review.rationale,
            "caution_flags": list(candidate.review.caution_flags),
            "evidence_notes": list(candidate.review.evidence_notes),
        }

    if candidate.council is not None:
        payload["council"] = {
            "decision": candidate.council.decision,
            "battlefield_ok": candidate.council.battlefield_ok,
            "veto_reasons": list(candidate.council.veto_reasons),
            "route": candidate.council.route,
            "execution_ready": candidate.council.execution_ready,
        }

    return payload


def publish_candidate(candidate: CandidateSignal) -> PublishedSignal:
    route = "killed_signals"
    execution_ready = False
    bucket = "killed"

    reviewed_score = candidate.score
    if candidate.review is not None:
        reviewed_score = candidate.review.adjusted_score

    if candidate.council is not None:
        route = candidate.council.route
        execution_ready = candidate.council.execution_ready

    if route == "live_signals":
        bucket = "live"
    elif route == "caution_signals":
        bucket = "caution"

    warnings = list(candidate.warnings)
    if candidate.review is not None:
        warnings.extend(candidate.review.caution_flags)
    if candidate.council is not None:
        warnings.extend(candidate.council.veto_reasons)

    return PublishedSignal(
        bucket=bucket,
        pair=candidate.pair,
        candidate_id=candidate.candidate_id,
        setup_type=candidate.setup_type,
        side=candidate.side,
        score=round(float(reviewed_score), 2),
        specialist=candidate.specialist,
        thesis=candidate.thesis,
        route=route,
        execution_ready=execution_ready,
        warnings=warnings,
        tags=list(candidate.tags),
        payload=_merge_payload(candidate),
    )


def publish_all(
    live_candidates: Iterable[CandidateSignal],
    caution_candidates: Iterable[CandidateSignal],
    killed_candidates: Iterable[CandidateSignal],
    positions: List[Dict[str, Any]] | None = None,
    audit: Dict[str, Any] | None = None,
) -> ScanResult:
    result = ScanResult(
        live_signals=[publish_candidate(c) for c in live_candidates],
        caution_signals=[publish_candidate(c) for c in caution_candidates],
        killed_signals=[publish_candidate(c) for c in killed_candidates],
        positions=list(positions or []),
        audit=dict(audit or {}),
    )
    return result
