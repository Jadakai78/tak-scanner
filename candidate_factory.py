from __future__ import annotations

from scannermodels import CandidateSignal, SpecialistObservation


def build_candidate(observation: SpecialistObservation) -> CandidateSignal:
    return CandidateSignal(
        pair=observation.pair,
        candidate_id=f"{observation.specialist}:{observation.pair}:{observation.setup_type}:{observation.side}",
        setup_type=observation.setup_type,
        side=observation.side,
        specialist=observation.specialist,
        thesis=observation.thesis,
        score=float(observation.score),
        confidence=float(observation.confidence),
        context=dict(observation.context),
        evidence=dict(observation.evidence),
        warnings=list(observation.warnings),
        tags=list(observation.tags),
    )
