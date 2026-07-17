from __future__ import annotations

from typing import Dict, List

from scannermodels import CandidateSignal, PublishedSignal, ScanResult


class ScannerPublisher:
    def publish(self, candidates: List[CandidateSignal]) -> ScanResult:
        result = ScanResult()

        for candidate in candidates:
            published = self._to_published_signal(candidate)

            if candidate.council is None:
                result.killed_signals.append(published)
                continue

            route = candidate.council.route
            if route == "live_signals":
                result.live_signals.append(published)
            elif route == "caution_signals":
                result.caution_signals.append(published)
            else:
                result.killed_signals.append(published)

        result.audit = {
            "candidate_count": len(candidates),
            "live_count": len(result.live_signals),
            "caution_count": len(result.caution_signals),
            "killed_count": len(result.killed_signals),
        }
        return result

    def _to_published_signal(self, candidate: CandidateSignal) -> PublishedSignal:
        review_payload: Dict[str, object] = {}
        council_payload: Dict[str, object] = {}

        if candidate.review is not None:
            review_payload = {
                "decision": candidate.review.decision,
                "adjusted_score": candidate.review.adjusted_score,
                "confidence_delta": candidate.review.confidence_delta,
                "rationale": candidate.review.rationale,
                "caution_flags": list(candidate.review.caution_flags),
                "evidence_notes": list(candidate.review.evidence_notes),
            }

        if candidate.council is not None:
            council_payload = {
                "decision": candidate.council.decision,
                "battlefield_ok": candidate.council.battlefield_ok,
                "veto_reasons": list(candidate.council.veto_reasons),
                "route": candidate.council.route,
                "execution_ready": candidate.council.execution_ready,
            }

        route = candidate.council.route if candidate.council else "killed_signals"
        execution_ready = candidate.council.execution_ready if candidate.council else False

        return PublishedSignal(
            bucket=route,
            pair=candidate.pair,
            candidate_id=candidate.candidate_id,
            setup_type=candidate.setup_type,
            side=candidate.side,
            score=(candidate.review.adjusted_score if candidate.review else candidate.score),
            specialist=candidate.specialist,
            thesis=candidate.thesis,
            route=route,
            execution_ready=execution_ready,
            warnings=list(candidate.warnings),
            tags=list(candidate.tags),
            payload={
                "entry_idea": candidate.entry_idea,
                "stop_idea": candidate.stop_idea,
                "target_idea": candidate.target_idea,
                "evidence": dict(candidate.evidence),
                "context": dict(candidate.context),
                "review": review_payload,
                "council": council_payload,
                "final_status": candidate.final_status,
            },
        )
