from __future__ import annotations

import logging
from typing import List

from scannercandidate_factory import build_candidate
from scannercouncil import ScannerCouncil
from scannerpublisher import publish_candidate
from scannerreviewer_remi import RemiReviewer
from scannermodels import PairContext, ScanResult
from scannerspecialist_registry import SpecialistRegistry

logger = logging.getLogger("takscannerv4")


class ScannerOrchestrator:
    def __init__(
        self,
        specialist_registry: SpecialistRegistry,
        remi_reviewer: RemiReviewer | None = None,
        council: ScannerCouncil | None = None,
    ) -> None:
        self.specialist_registry = specialist_registry
        self.remi_reviewer = remi_reviewer or RemiReviewer()
        self.council = council or ScannerCouncil()

    def run(self, contexts: List[PairContext], fear_greed_score: int = 50) -> ScanResult:
        logger.info("V4 orchestrator contextcount%s", len(contexts))

        result = ScanResult()
        audit = {
            "context_count": len(contexts),
            "processed_pairs": 0,
            "observations": 0,
            "candidates": 0,
            "live": 0,
            "caution": 0,
            "killed": 0,
        }

        for context in contexts:
            audit["processed_pairs"] += 1
            logger.info("V4 orchestrator run pair%s regime%s", context.pair, context.market_regime)

            adapters = self.specialist_registry.engines_for_regime(context.market_regime)
            pair_candidates = []

            for adapter in adapters:
                observations = adapter.evaluate(context, fear_greed_score=fear_greed_score)
                audit["observations"] += len(observations)

                for obs in observations:
                    candidate = build_candidate(obs)
                    candidate.review = self.remi_reviewer.review(candidate)
                    candidate.council = self.council.adjudicate(candidate)

                    if candidate.review is not None:
                        candidate.confidence = max(0.0, min(1.0, candidate.confidence + candidate.review.confidence_delta))
                        candidate.score = candidate.review.adjusted_score

                    route = candidate.council.route if candidate.council else "killed_signals"
                    if route == "live_signals":
                        candidate.final_status = "live"
                        result.live_signals.append(publish_candidate(candidate, "live_signals"))
                        audit["live"] += 1
                    elif route == "caution_signals":
                        candidate.final_status = "caution"
                        result.caution_signals.append(publish_candidate(candidate, "caution_signals"))
                        audit["caution"] += 1
                    else:
                        candidate.final_status = "killed"
                        result.killed_signals.append(publish_candidate(candidate, "killed_signals"))
                        audit["killed"] += 1

                    pair_candidates.append(candidate)

            logger.info("V4 orchestrator pair%s candidates%s", context.pair, len(pair_candidates))
            audit["candidates"] += len(pair_candidates)

        result.audit = audit
        return result
