from __future__ import annotations

from typing import Dict, Iterable, List

from scannercandidate_factory import build_candidate
from scannercouncil import ScannerCouncil
from scannerpair_intake import ScannerPairIntake
from scannerpublisher import ScannerPublisher
from scannerreviewer_remi import RemiReviewer
from scannerspecialist_registry import SpecialistRegistry
from scannermodels import CandidateSignal, PairContext, ScanResult, SpecialistObservation


class ScannerOrchestrator:
    def __init__(
        self,
        intake: ScannerPairIntake,
        registry: SpecialistRegistry,
        remi: RemiReviewer,
        council: ScannerCouncil,
        publisher: ScannerPublisher,
    ) -> None:
        self.intake = intake
        self.registry = registry
        self.remi = remi
        self.council = council
        self.publisher = publisher

    def run(self, raw_pairs: Iterable[Dict]) -> ScanResult:
        pair_contexts = self.intake.normalize_pairs(raw_pairs)
        candidates: List[CandidateSignal] = []
        pair_audit: Dict[str, Dict[str, int]] = {}

        for context in pair_contexts:
            observations = self.registry.run_for_pair(context)
            pair_audit[context.pair] = {
                "observations": len(observations),
                "candidates": 0,
            }

            for observation in observations:
                candidate = build_candidate(observation)
                candidate.review = self.remi.review(candidate)
                candidate.score = candidate.review.adjusted_score
                candidate.confidence = max(0.0, min(1.0, candidate.confidence + candidate.review.confidence_delta))
                candidate.council = self.council.adjudicate(candidate)
                candidate.final_status = candidate.council.decision
                candidates.append(candidate)

            pair_audit[context.pair]["candidates"] = len(observations)

        result = self.publisher.publish(candidates)
        result.audit.update(
            {
                "pair_count": len(pair_contexts),
                "registry_size": len(self.registry),
                "pair_audit": pair_audit,
            }
        )
        return result


def build_default_orchestrator() -> ScannerOrchestrator:
    return ScannerOrchestrator(
        intake=ScannerPairIntake(),
        registry=SpecialistRegistry(),
        remi=RemiReviewer(),
        council=ScannerCouncil(),
        publisher=ScannerPublisher(),
    )
