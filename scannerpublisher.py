from __future__ import annotations

from typing import Dict, Iterable, List

from scannermodels import CandidateSignal, PublishedSignal, ScanResult


class ScannerPublisher:
    def publish(
        self,
        candidates: Iterable[CandidateSignal],
        positions: List[Dict[str, object]] | None = None,
        audit: Dict[str, object] | None = None,
    ) -> ScanResult:
        result = ScanResult(
            positions=list(positions or []),
            audit=dict(audit or {}),
        )

        for candidate in candidates:
            published = self._to_published(candidate)

            route = "killed_signals"
            if candidate.council is not None:
                route = candidate.council.route

            if route == "live_signals":
                result.live_signals.append(published)
            elif route == "caution_signals":
                result.caution_signals.append(published)
            else:
                result.killed_signals.append(published)

        result.audit.setdefault("counts", {})
        result.audit["counts"]["live_signals"] = len(result.live_signals)
        result.audit["counts"]["caution_signals"] = len(result.caution_signals)
        result.audit["counts"]["killed_signals"] = len(result.killed_signals)
        result.audit["counts"]["positions"] = len(result.positions)
        return result

    def _to_published(self, candidate: CandidateSignal) -> PublishedSignal:
        review_score = candidate.review.adjusted_score if candidate.review else candidate.score
        route = candidate.council.route if candidate.council else "killed_signals"
        execution_ready = candidate.council.execution_ready if candidate.council else False

        payload = {
            "entry_idea": candidate.entry_idea,
            "stop_idea": candidate.stop_idea,
            "target_idea": candidate.target_idea,
            "evidence": dict(candidate.evidence),
            "context": dict(candidate.context),
            "review": None if candidate.review is None else {
                "decision": candidate.review.decision,
                "adjusted_score": candidate.review.adjusted_score,
                "confidence_delta": candidate.review.confidence_delta,
                "rationale": candidate.review.rationale,
                "caution_flags": list(candidate.review.caution_flags),
                "evidence_notes": list(candidate.review.evidence_notes),
            },
            "council": None if candidate.council is None else {
                "decision": candidate.council.decision,
                "battlefield_ok": candidate.council.battlefield_ok,
                "veto_reasons": list(candidate.council.veto_reasons),
                "route": candidate.council.route,
                "execution_ready": candidate.council.execution_ready,
            },
        }

        return PublishedSignal(
            bucket=route,
            pair=candidate.pair,
            candidate_id=candidate.candidate_id,
            setup_type=candidate.setup_type,
            side=candidate.side,
            score=round(float(review_score), 2),
            specialist=candidate.specialist,
            thesis=candidate.thesis,
            route=route,
            execution_ready=execution_ready,
            warnings=list(candidate.warnings),
            tags=list(candidate.tags),
            payload=payload,
        )
