from __future__ import annotations

from typing import List

from scannermodels import CandidateSignal, CouncilResult, PublishedSignal, ScanResult


class ScannerPublisher:
    def __init__(self) -> None:
        ...

    def _build_published(self, candidate: CandidateSignal, council: CouncilResult) -> PublishedSignal:
        bucket = council.route
        warnings: List[str] = list(candidate.warnings)

        if candidate.review and candidate.review.caution_flags:
            warnings.extend(candidate.review.caution_flags)

        tags = list(candidate.tags)

        payload = {
            "candidate_id": candidate.candidate_id,
            "pair": candidate.pair,
            "setup_type": candidate.setup_type,
            "side": candidate.side,
            "specialist": candidate.specialist,
            "score": candidate.review.adjusted_score if candidate.review else candidate.score,
            "confidence": candidate.confidence,
            "entry_idea": candidate.entry_idea,
            "stop_idea": candidate.stop_idea,
            "target_idea": candidate.target_idea,
            "evidence": candidate.evidence,
            "context": candidate.context,
            "council_decision": council.decision,
            "battlefield_ok": council.battlefield_ok,
            "veto_reasons": council.veto_reasons,
        }

        return PublishedSignal(
            bucket=bucket,
            pair=candidate.pair,
            candidate_id=candidate.candidate_id,
            setup_type=candidate.setup_type,
            side=candidate.side,
            score=payload["score"],
            specialist=candidate.specialist,
            thesis=candidate.thesis,
            route=council.route,
            execution_ready=council.execution_ready,
            warnings=warnings,
            tags=tags,
            payload=payload,
        )

    def publish(self, candidates: List[CandidateSignal]) -> ScanResult:
        result = ScanResult()

        for candidate in candidates:
            if candidate.council is None:
                continue

            published = self._build_published(candidate, candidate.council)

            if published.bucket == "live_signals":
                result.live_signals.append(published)
            elif published.bucket == "caution_signals":
                result.caution_signals.append(published)
            elif published.bucket == "killed_signals":
                result.killed_signals.append(published)

        result.audit["total_candidates"] = len(candidates)
        result.audit["live_count"] = len(result.live_signals)
        result.audit["caution_count"] = len(result.caution_signals)
        result.audit["killed_count"] = len(result.killed_signals)

        return result
