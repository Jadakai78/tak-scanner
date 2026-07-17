from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List

from scannercandidate_factory import build_candidate
from scannermodels import CandidateSignal, PairContext, SpecialistObservation
from scannercouncil import ScannerCouncil
from scannerreviewer_remi import RemiReviewer

logger = logging.getLogger("scannerorchestrator")


class ScannerOrchestrator:
    def __init__(
        self,
        specialist_registry: Any,
        remi_reviewer: RemiReviewer | None = None,
        council: ScannerCouncil | None = None,
    ) -> None:
        self.specialist_registry = specialist_registry
        self.remi_reviewer = remi_reviewer or RemiReviewer()
        self.council = council or ScannerCouncil()

    def run(self, contexts: Iterable[PairContext], shared_state: Dict[str, Any] | None = None) -> List[CandidateSignal]:
        shared_state = dict(shared_state or {})
        contexts = list(contexts)

        logger.info("V4 orchestrator contextcount=%s", len(contexts))

        finalized: List[CandidateSignal] = []

        for context in contexts:
            logger.info(
                "V4 orchestrator run pair=%s regime=%s",
                context.pair,
                context.market_regime,
            )

            pair_candidates = self._run_pair(context, shared_state)
            logger.info(
                "V4 orchestrator pair=%s candidates=%s",
                context.pair,
                len(pair_candidates),
            )
            finalized.extend(pair_candidates)

        return finalized

    def _run_pair(self, context: PairContext, shared_state: Dict[str, Any]) -> List[CandidateSignal]:
        specialists = self.specialist_registry.resolve_for_regime(context.market_regime)
        completed: List[CandidateSignal] = []

        for specialist in specialists:
            name = getattr(specialist, "name", specialist.__class__.__name__)
            fgscore = shared_state.get("fgscore", 50)

            logger.info(
                "V4 adapter calling engine=%s pair=%s regime=%s fg=%s",
                name,
                context.pair,
                context.market_regime,
                fgscore,
            )

            observation = self._invoke_specialist(specialist, context, shared_state)

            logger.info(
                "V4 TAK pair=%s engine=%s rawnone=%s",
                context.pair,
                name,
                observation is None,
            )

            if observation is None:
                continue

            candidate = build_candidate(observation)
            review = self.remi_reviewer.review(candidate)
            candidate.review = review
            candidate.confidence = round(max(0.0, min(1.0, candidate.confidence + review.confidence_delta)), 4)
            candidate.score = review.adjusted_score

            council_result = self.council.adjudicate(candidate)
            candidate.council = council_result
            candidate.final_status = council_result.decision

            logger.info(
                "V4 OBS pair=%s engine=%s bias=%s conf=%.3f score=%.2f route=%s",
                context.pair,
                name,
                candidate.side,
                candidate.confidence,
                candidate.score,
                council_result.route,
            )

            completed.append(candidate)

        return completed

    def _invoke_specialist(
        self,
        specialist: Any,
        context: PairContext,
        shared_state: Dict[str, Any],
    ) -> SpecialistObservation | None:
        for method_name in ("observe", "scan", "generate", "run"):
            method = getattr(specialist, method_name, None)
            if callable(method):
                result = method(context=context, shared_state=shared_state)
                return self._normalize_observation(result, specialist, context)
        return None

    def _normalize_observation(
        self,
        result: Any,
        specialist: Any,
        context: PairContext,
    ) -> SpecialistObservation | None:
        if result is None:
            return None

        if isinstance(result, SpecialistObservation):
            return result

        if isinstance(result, dict):
            return SpecialistObservation(
                specialist=str(result.get("specialist", getattr(specialist, "name", specialist.__class__.__name__))),
                pair=str(result.get("pair", context.pair)),
                setup_type=str(result.get("setup_type", "unclassified")),
                side=str(result.get("side", result.get("bias", "NEUTRAL"))),
                confidence=float(result.get("confidence", 0.0)),
                score=float(result.get("score", 0.0)),
                thesis=str(result.get("thesis", result.get("summary", ""))),
                evidence=dict(result.get("evidence", {})),
                warnings=list(result.get("warnings", [])),
                tags=list(result.get("tags", [])),
                context=dict(result.get("context", {})),
            )

        return None
