from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from scannercandidate_factory import build_candidate
from scannercouncil import ScannerCouncil
from scannermodels import PublishedSignal, ScanResult
from scannerreviewer_remi import RemiReviewer


logger = logging.getLogger("scannerorchestrator")


class ScannerOrchestrator:
    """
    One-shot orchestration:
    1. iterate all pair contexts
    2. ask registry for specialist observations
    3. normalize observations into candidates
    4. run REMI review
    5. run council adjudication
    6. publish only after the full scan is complete

    This avoids partial bus states during in-flight pair processing.
    """

    def __init__(
        self,
        specialist_registry: Any,
        publisher: Any,
        remi_reviewer: Optional[RemiReviewer] = None,
        council: Optional[ScannerCouncil] = None,
    ) -> None:
        self.specialist_registry = specialist_registry
        self.publisher = publisher
        self.remi_reviewer = remi_reviewer or RemiReviewer()
        self.council = council or ScannerCouncil()

    def run(
        self,
        pair_contexts: Iterable[Any],
        positions: Optional[List[Dict[str, Any]]] = None,
        audit: Optional[Dict[str, Any]] = None,
    ) -> ScanResult:
        contexts = list(pair_contexts)
        positions = list(positions or [])
        audit = dict(audit or {})

        logger.info("V4 orchestrator contextcount=%d", len(contexts))

        live_signals: List[PublishedSignal] = []
        caution_signals: List[PublishedSignal] = []
        killed_signals: List[PublishedSignal] = []

        audit.setdefault("context_count", len(contexts))
        audit.setdefault("pairs_processed", 0)
        audit.setdefault("observations_total", 0)
        audit.setdefault("candidates_total", 0)
        audit.setdefault("reviewed_total", 0)
        audit.setdefault("live_total", 0)
        audit.setdefault("caution_total", 0)
        audit.setdefault("killed_total", 0)
        audit.setdefault("pair_breakdown", [])

        for context in contexts:
            pair = getattr(context, "pair", None) or str(getattr(context, "symbol", "UNKNOWN"))
            regime = getattr(context, "market_regime", "unknown")

            logger.info("V4 orchestrator run pair=%s regime=%s", pair, regime)

            observations = self._safe_collect_observations(context)
            candidate_count = 0

            for observation in observations:
                audit["observations_total"] += 1

                try:
                    candidate = build_candidate(observation)
                except Exception as exc:
                    logger.exception("V4 candidate build failed pair=%s err=%s", pair, exc)
                    continue

                candidate_count += 1
                audit["candidates_total"] += 1

                review = self.remi_reviewer.review(candidate)
                candidate.review = review
                candidate.score = review.adjusted_score
                candidate.confidence = max(0.0, min(1.0, candidate.confidence + review.confidence_delta))
                audit["reviewed_total"] += 1

                council_result = self.council.adjudicate(candidate)
                candidate.council = council_result
                candidate.final_status = council_result.route

                published = self._to_published_signal(candidate)

                if council_result.route == "live_signals":
                    live_signals.append(published)
                    audit["live_total"] += 1
                elif council_result.route == "caution_signals":
                    caution_signals.append(published)
                    audit["caution_total"] += 1
                else:
                    killed_signals.append(published)
                    audit["killed_total"] += 1

            logger.info("V4 orchestrator pair=%s candidates=%d", pair, candidate_count)

            audit["pairs_processed"] += 1
            audit["pair_breakdown"].append(
                {
                    "pair": pair,
                    "regime": regime,
                    "candidate_count": candidate_count,
                }
            )

        result = ScanResult(
            live_signals=live_signals,
            caution_signals=caution_signals,
            killed_signals=killed_signals,
            positions=positions,
            audit=audit,
        )

        logger.info(
            "V4 orchestrator complete pairs=%d live=%d caution=%d killed=%d",
            audit["pairs_processed"],
            len(result.live_signals),
            len(result.caution_signals),
            len(result.killed_signals),
        )

        return self.publisher.publish(result)

    def _safe_collect_observations(self, context: Any) -> List[Any]:
        try:
            observations = self.specialist_registry.collect(context)
            if not observations:
                return []
            return list(observations)
        except Exception as exc:
            pair = getattr(context, "pair", "UNKNOWN")
            logger.exception("V4 collect failed pair=%s err=%s", pair, exc)
            return []

    @staticmethod
    def _to_published_signal(candidate: Any) -> PublishedSignal:
        review = candidate.review
        council = candidate.council

        warnings = list(candidate.warnings)
        if review is not None:
            warnings.extend(review.caution_flags)
        if council is not None:
            warnings.extend(council.veto_reasons)

        payload: Dict[str, Any] = {
            "pair": candidate.pair,
            "setup_type": candidate.setup_type,
            "specialist": candidate.specialist,
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
                "evidence_notes": list(review.evidence_notes),
            },
            "council": None
            if council is None
            else {
                "decision": council.decision,
                "battlefield_ok": council.battlefield_ok,
                "route": council.route,
                "execution_ready": council.execution_ready,
            },
        }

        return PublishedSignal(
            bucket="live"
            if council and council.route == "live_signals"
            else "caution"
            if council and council.route == "caution_signals"
            else "killed",
            pair=candidate.pair,
            candidate_id=candidate.candidate_id,
            setup_type=candidate.setup_type,
            side=candidate.side,
            score=float(candidate.score),
            specialist=candidate.specialist,
            thesis=candidate.thesis,
            route="killed_signals" if council is None else council.route,
            execution_ready=False if council is None else council.execution_ready,
            warnings=warnings,
            tags=list(candidate.tags),
            payload=payload,
        )
