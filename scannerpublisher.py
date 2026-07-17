from __future__ import annotations

from typing import Any, Dict

from scannermodels import CandidateSignal, PublishedSignal


def publish_candidate(candidate: CandidateSignal, bucket: str) -> PublishedSignal:
    review = candidate.review
    council = candidate.council

    score = candidate.score
    warnings = list(candidate.warnings)

    if review is not None:
        score = review.adjusted_score
        warnings.extend(review.caution_flags)

    route = bucket
    execution_ready = False
    if council is not None:
        route = council.route
        execution_ready = council.execution_ready
        warnings.extend(council.veto_reasons)

    payload: Dict[str, Any] = {
        "pair": candidate.pair,
        "setup_type": candidate.setup_type,
        "side": candidate.side,
        "specialist": candidate.specialist,
        "confidence": candidate.confidence,
        "entry_idea": candidate.entry_idea,
        "stop_idea": candidate.stop_idea,
        "target_idea": candidate.target_idea,
        "evidence": dict(candidate.evidence),
        "context": dict(candidate.context),
        "review": None if review is None else {
            "decision": review.decision,
            "adjusted_score": review.adjusted_score,
            "confidence_delta": review.confidence_delta,
            "rationale": review.rationale,
            "caution_flags": list(review.caution_flags),
            "evidence_notes": list(review.evidence_notes),
        },
        "council": None if council is None else {
            "decision": council.decision,
            "battlefield_ok": council.battlefield_ok,
            "veto_reasons": list(council.veto_reasons),
            "route": council.route,
            "execution_ready": council.execution_ready,
        },
    }

    return PublishedSignal(
        bucket=bucket,
        pair=candidate.pair,
        candidate_id=candidate.candidate_id,
        setup_type=candidate.setup_type,
        side=candidate.side,
        score=round(float(score), 2),
        specialist=candidate.specialist,
        thesis=candidate.thesis,
        route=route,
        execution_ready=execution_ready,
        warnings=warnings,
        tags=list(candidate.tags),
        payload=payload,
    )
