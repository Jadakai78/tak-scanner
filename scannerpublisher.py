from __future__ import annotations

from typing import Dict, List

from scannermodels import CandidateSignal, PublishedSignal, ScanResult


class ScannerPublisher:
    def _build_payload(self, candidate: CandidateSignal) -> Dict:
        review = candidate.review
        council = candidate.council

        return {
            "candidate_id": candidate.candidate_id,
            "pair": candidate.pair,
            "setup_type": candidate.setup_type,
            "side": candidate.side,
            "specialist": candidate.specialist,
            "score": candidate.score,
            "confidence": candidate.confidence,
            "thesis": candidate.thesis,
            "entry_idea": candidate.entry_idea,
            "stop_idea": candidate.stop_idea,
            "target_idea": candidate.target_idea,
            "evidence": dict(candidate.evidence),
            "warnings": list(candidate.warnings),
            "tags": list(candidate.tags),
            "context": dict(candidate.context),
            "review": {
                "decision": None if review is None else review.decision,
                "adjusted_score": None if review is None else review.adjusted_score,
                "confidence_delta": None if review is None else review.confidence_delta,
                "rationale": None if review is None else review.rationale,
                "caution_flags": [] if review is None else list(review.caution_flags),
                "evidence_notes": [] if review is None else list(review.evidence_notes),
            },
            "council": {
                "decision": None if council is None else council.decision,
                "battlefield_ok": None if council is None else council.battlefield_ok,
                "veto_reasons": [] if council is None else list(council.veto_reasons),
                "route": None if council is None else council.route,
                "execution_ready": False if council is None else council.execution_ready,
            },
            "final_status": candidate.final_status,
        }

    def _publish_one(self, bucket: str, candidate: CandidateSignal) -> PublishedSignal:
        adjusted_score = candidate.score
        route = bucket
        execution_ready = False

        if candidate.review is not None:
            adjusted_score = candidate.review.adjusted_score
        if candidate.council is not None:
            route = candidate.council.route
            execution_ready = candidate.council.execution_ready

        return PublishedSignal(
            bucket=bucket,
            pair=candidate.pair,
            candidate_id=candidate.candidate_id,
            setup_type=candidate.setup_type,
            side=candidate.side,
            score=round(float(adjusted_score), 2),
            specialist=candidate.specialist,
            thesis=candidate.thesis,
            route=route,
            execution_ready=execution_ready,
            warnings=list(candidate.warnings),
            tags=list(candidate.tags),
            payload=self._build_payload(candidate),
        )

    def publish(
        self,
        live_candidates: List[CandidateSignal],
        caution_candidates: List[CandidateSignal],
        killed_candidates: List[CandidateSignal],
        positions: List[Dict] | None = None,
        audit: Dict | None = None,
    ) -> ScanResult:
        result = ScanResult(
            live_signals=[self._publish_one("live_signals", c) for c in live_candidates],
            caution_signals=[self._publish_one("caution_signals", c) for c in caution_candidates],
            killed_signals=[self._publish_one("killed_signals", c) for c in killed_candidates],
            positions=list(positions or []),
            audit=dict(audit or {}),
        )
        return result
