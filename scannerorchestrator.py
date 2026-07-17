from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from scannercandidate_factory import build_candidate
from scannercouncil import ScannerCouncil
from scannerpair_intake import ScannerPairIntake
from scannerreviewer_remi import RemiReviewer
from scannermodels import CandidateSignal, PublishedSignal, ScanResult
from scannerspecialist_registry import ScannerSpecialistRegistry

logger = logging.getLogger(__name__)


class ScannerOrchestrator:
    def __init__(
        self,
        intake: Optional[ScannerPairIntake] = None,
        registry: Optional[ScannerSpecialistRegistry] = None,
        remi: Optional[RemiReviewer] = None,
        council: Optional[ScannerCouncil] = None,
    ) -> None:
        self.intake = intake or ScannerPairIntake()
        self.registry = registry or ScannerSpecialistRegistry()
        self.remi = remi or RemiReviewer()
        self.council = council or ScannerCouncil()

    def _publishable(self, candidate: CandidateSignal, bucket: str) -> PublishedSignal:
        reviewed_score = candidate.review.adjusted_score if candidate.review else candidate.score
        route = candidate.council.route if candidate.council else bucket
        execution_ready = bool(candidate.council.execution_ready) if candidate.council else False

        payload: Dict[str, Any] = {
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
            bucket=bucket,
            pair=candidate.pair,
            candidate_id=candidate.candidate_id,
            setup_type=candidate.setup_type,
            side=candidate.side,
            score=round(reviewed_score, 2),
            specialist=candidate.specialist,
            thesis=candidate.thesis,
            route=route,
            execution_ready=execution_ready,
            warnings=list(candidate.warnings),
            tags=list(candidate.tags),
            payload=payload,
        )

    def process_pair(self, pair_payload: Dict[str, Any]) -> List[CandidateSignal]:
        observations = self.registry.observe_pair(pair_payload)
        candidates: List[CandidateSignal] = []

        for observation in observations:
            candidate = build_candidate(observation)
            candidate.review = self.remi.review(candidate)
            candidate.council = self.council.adjudicate(candidate)

            if candidate.council.decision == "live":
                candidate.final_status = "live"
            elif candidate.council.decision == "caution":
                candidate.final_status = "caution"
            else:
                candidate.final_status = "killed"

            candidates.append(candidate)

        logger.info(
            "V4 orchestrator pair=%s candidates=%s",
            pair_payload.get("pair"),
            len(candidates),
        )
        return candidates

    def run(self, universe: Iterable[Dict[str, Any]]) -> ScanResult:
        prepared_pairs = self.intake.prepare(universe)

        live_signals: List[PublishedSignal] = []
        caution_signals: List[PublishedSignal] = []
        killed_signals: List[PublishedSignal] = []
        audit_pairs: List[Dict[str, Any]] = []

        for pair_payload in prepared_pairs:
            pair = pair_payload.get("pair")
            regime = pair_payload.get("market_regime")
            logger.info("V4 orchestrator run pair=%s regime=%s", pair, regime)

            candidates = self.process_pair(pair_payload)

            for candidate in candidates:
                if candidate.final_status == "live":
                    live_signals.append(self._publishable(candidate, "live_signals"))
                elif candidate.final_status == "caution":
                    caution_signals.append(self._publishable(candidate, "caution_signals"))
                else:
                    killed_signals.append(self._publishable(candidate, "killed_signals"))

            audit_pairs.append(
                {
                    "pair": pair,
                    "regime": regime,
                    "candidate_count": len(candidates),
                }
            )

        audit = {
            "pairs_seen": len(prepared_pairs),
            "live_count": len(live_signals),
            "caution_count": len(caution_signals),
            "killed_count": len(killed_signals),
            "pairs": audit_pairs,
        }

        return ScanResult(
            live_signals=live_signals,
            caution_signals=caution_signals,
            killed_signals=killed_signals,
            positions=[],
            audit=audit,
        )
