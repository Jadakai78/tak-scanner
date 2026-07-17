from __future__ import annotations

from typing import Any, Dict, List

from scannermodels import CandidateSignal, PublishedSignal, ScanResult


class ScannerPublisher:
    def publish_candidate(self, candidate: CandidateSignal) -> PublishedSignal:
        review = candidate.review
        council = candidate.council

        adjusted_score = candidate.score
        warnings: List[str] = list(candidate.warnings)

        if review is not None:
            adjusted_score = review.adjusted_score
            warnings.extend(review.caution_flags)

        route = "killed_signals"
        execution_ready = False
        bucket = "killed"

        if council is not None:
            route = council.route
            execution_ready = council.execution_ready
            if council.route == "live_signals":
                bucket = "live"
            elif council.route == "caution_signals":
                bucket = "caution"

        payload: Dict[str, Any] = {
            "entry_idea": candidate.entry_idea,
            "stop_idea": candidate.stop_idea,
            "target_idea": candidate.target_idea,
            "evidence": dict(candidate.evidence),
            "context": dict(candidate.context),
            "review": None
            if review is None
            else {
                "decision": review.decision,
                "adjusted_score": review.adjusted_score,
                "confidence_delta": review.confidence_delta,
                "rationale": review.rationale,
                "caution_flags": list(review.caution_flags),
                "evidence_notes": list(review.evidence_notes),
            },
            "council": None
            if council is None
            else {
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
            score=round(float(adjusted_score), 2),
            specialist=candidate.specialist,
            thesis=candidate.thesis,
            route=route,
            execution_ready=execution_ready,
            warnings=warnings,
            tags=list(candidate.tags),
            payload=payload,
        )

    def build_scan_result(
        self,
        live_candidates: List[CandidateSignal],
        caution_candidates: List[CandidateSignal],
        killed_candidates: List[CandidateSignal],
        positions: List[Dict[str, Any]] | None = None,
        audit: Dict[str, Any] | None = None,
    ) -> ScanResult:
        return ScanResult(
            live_signals=[self.publish_candidate(x) for x in live_candidates],
            caution_signals=[self.publish_candidate(x) for x in caution_candidates],
            killed_signals=[self.publish_candidate(x) for x in killed_candidates],
            positions=list(positions or []),
            audit=dict(audit or {}),
        )
