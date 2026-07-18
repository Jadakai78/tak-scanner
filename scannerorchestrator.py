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

    def run(
        self,
        contexts: Iterable[PairContext],
        shared_state: Dict[str, Any] | None = None,
    ) -> List[CandidateSignal]:
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

            try:
                pair_candidates = self._run_pair(context, shared_state)
            except Exception:
                logger.exception(
                    "V4 orchestrator pair_failed pair=%s regime=%s",
                    context.pair,
                    context.market_regime,
                )
                continue

            logger.info(
                "V4 orchestrator pair=%s candidates=%s",
                context.pair,
                len(pair_candidates),
            )
            finalized.extend(pair_candidates)

        return finalized

    def _run_pair(
        self,
        context: PairContext,
        shared_state: Dict[str, Any],
    ) -> List[CandidateSignal]:
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

            try:
                observation = self._invoke_specialist(specialist, context, shared_state)
            except Exception:
                logger.exception(
                    "V4 specialist_failed pair=%s engine=%s regime=%s",
                    context.pair,
                    name,
                    context.market_regime,
                )
                continue

            logger.info(
                "V4 TAK pair=%s engine=%s rawnone=%s",
                context.pair,
                name,
                observation is None,
            )

            if observation is None:
                continue

            candidate = build_candidate(observation)
            self._merge_pair_context(candidate, context, name, shared_state)

            review = self.remi_reviewer.review(candidate)
            candidate.review = review
            candidate.confidence = round(
                max(0.0, min(1.0, candidate.confidence + review.confidence_delta)),
                4,
            )
            candidate.score = round(float(review.adjusted_score), 2)

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

    def _merge_pair_context(
        self,
        candidate: CandidateSignal,
        pair_context: PairContext,
        specialist_name: str,
        shared_state: Dict[str, Any],
    ) -> None:
        merged = dict(pair_context.context)
        merged.update(candidate.context)

        merged.setdefault("pair", pair_context.pair)
        merged.setdefault("timeframe", pair_context.timeframe)
        merged.setdefault("market_regime", pair_context.market_regime)
        merged.setdefault("regime", pair_context.market_regime)
        merged.setdefault("session", pair_context.session)
        merged.setdefault("fear_greed", pair_context.fear_greed)

        if pair_context.indicators:
            merged.setdefault("pair_indicators", dict(pair_context.indicators))
        if pair_context.market_state:
            merged.setdefault("pair_market_state", dict(pair_context.market_state))
        if pair_context.diagnostics:
            merged.setdefault("pair_diagnostics", dict(pair_context.diagnostics))

        merged.setdefault("source_engine", specialist_name)
        merged.setdefault("fgscore", shared_state.get("fgscore"))
        merged.setdefault("shared_market_phase", shared_state.get("market_phase"))
        merged.setdefault("shared_scan_timeframe", shared_state.get("timeframe"))

        candidate.context = merged

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
            raw_context = dict(result.get("context", {}))
            raw_context.setdefault("pair", context.pair)
            raw_context.setdefault("timeframe", context.timeframe)
            raw_context.setdefault("market_regime", context.market_regime)
            raw_context.setdefault("regime", context.market_regime)
            raw_context.setdefault("session", context.session)
            raw_context.setdefault("fear_greed", context.fear_greed)

            return SpecialistObservation(
                specialist=str(
                    result.get(
                        "specialist",
                        getattr(specialist, "name", specialist.__class__.__name__),
                    )
                ),
                pair=str(result.get("pair", context.pair)),
                setup_type=str(result.get("setup_type", "unclassified")),
                side=str(result.get("side", result.get("bias", "NEUTRAL"))),
                confidence=float(result.get("confidence", 0.0)),
                score=float(result.get("score", 0.0)),
                thesis=str(result.get("thesis", result.get("summary", ""))),
                evidence=dict(result.get("evidence", {})),
                warnings=list(result.get("warnings", [])),
                tags=list(result.get("tags", [])),
                context=raw_context,
            )

        return None
