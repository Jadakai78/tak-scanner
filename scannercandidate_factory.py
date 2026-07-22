from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from scannermodels import CandidateSignal, SpecialistObservation



def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_candidate(observation: SpecialistObservation) -> CandidateSignal:
    candidate_id = f"{observation.pair}-{observation.specialist}-{uuid4().hex[:10]}"

    evidence: Dict[str, Any] = dict(observation.evidence)
    context: Dict[str, Any] = dict(observation.context)

    entry_idea = evidence.get("entry_idea")
    stop_idea = evidence.get("stop_idea")
    target_idea = evidence.get("target_idea")

    return CandidateSignal(
        candidate_id=candidate_id,
        pair=observation.pair,
        setup_type=observation.setup_type,
        side=observation.side,
        specialist=observation.specialist,
        confidence=_clamp(float(observation.confidence), 0.0, 1.0),
        score=float(observation.score),
        thesis=observation.thesis,
        entry_idea=entry_idea,
        stop_idea=stop_idea,
        target_idea=target_idea,
        evidence=evidence,
        warnings=list(observation.warnings),
        tags=list(observation.tags),
        context=context,
        final_status="candidate",
    )


def build_candidate_from_dict(payload: Dict[str, Any]) -> CandidateSignal:
    observation = SpecialistObservation(
        specialist=str(payload.get("specialist", "unknown")),
        pair=str(payload.get("pair", "UNKNOWN")),
        setup_type=str(payload.get("setup_type", "unclassified")),
        side=str(payload.get("side", "NEUTRAL")),
        confidence=float(payload.get("confidence", 0.0)),
        score=float(payload.get("score", 0.0)),
        thesis=str(payload.get("thesis", "")),
        evidence=dict(payload.get("evidence", {})),
        warnings=list(payload.get("warnings", [])),
        tags=list(payload.get("tags", [])),
        context=dict(payload.get("context", {})),
    )
    return build_candidate(observation)
