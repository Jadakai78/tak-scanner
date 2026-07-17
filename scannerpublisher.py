from __future__ import annotations

import logging
from typing import List

from scannermodels import CandidateSignal, PublishedSignal

logger = logging.getLogger("scannerpublisher")


class ScannerPublisher:
    def publish(
        self,
        bucket: str,
        candidate: CandidateSignal,
    ) -> PublishedSignal:
        route = candidate.council.route if candidate.council else bucket
        execution_ready = bool(candidate.council.execution_ready) if candidate.council else False

        published = PublishedSignal(
            bucket=bucket,
            pair=candidate.pair,
            candidate_id=candidate.candidate_id,
            setup_type=candidate.setup_type,
            side=candidate.side,
            score=float(candidate.review.adjusted_score if candidate.review else candidate.score),
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
                "review": candidate.review.__dict__ if candidate.review else None,
                "council": candidate.council.__dict__ if candidate.council else None,
                "evidence": dict(candidate.evidence),
                "context": dict(candidate.context),
            },
        )

        logger.info(
            "V4 PUBLISH pair=%s candidate=%s bucket=%s execution_ready=%s",
            candidate.pair,
            candidate.candidate_id,
            bucket,
            execution_ready,
        )
        return published

    @staticmethod
    def bucket_name(candidate: CandidateSignal) -> str:
        if candidate.council is None:
            return "killed_signals"
        if candidate.council.route == "live_signals":
            return "live_signals"
        if candidate.council.route == "caution_signals":
            return "caution_signals"
        return "killed_signals"

    def publish_many(self, candidates: List[CandidateSignal]) -> List[PublishedSignal]:
        out: List[PublishedSignal] = []
        for candidate in candidates:
            out.append(self.publish(self.bucket_name(candidate), candidate))
        return out
