from __future__ import annotations

from typing import Iterable, List

from scannercandidate_factory import build_candidate
from scannerpublisher import ScannerPublisher
from scannerreviewer_remi import RemiReviewer
from scannercouncil import ScannerCouncil
from scannerspecialist_registry import SpecialistRegistry
from scannerpair_intake import PairIntake
from scannermodels import CandidateSignal, PairContext, ScanResult


class ScannerOrchestrator:
    def __init__(
        self,
        registry: SpecialistRegistry,
        reviewer: RemiReviewer | None = None,
        council: ScannerCouncil | None = None,
        publisher: ScannerPublisher | None = None,
    ) -> None:
        self.registry = registry
        self.reviewer = reviewer or RemiReviewer()
        self.council = council or ScannerCouncil()
        self.publisher = publisher or ScannerPublisher()
        self.intake = PairIntake()

    def _build_candidates_for_pair(self, context: PairContext) -> List[CandidateSignal]:
        observations = self.registry.scan_pair(context)
        candidates: List[CandidateSignal] = []

        for obs in observations:
            candidate = build_candidate(obs)
            review = self.reviewer.review(candidate)
            candidate.review = review
            council_result = self.council.adjudicate(candidate)
            candidate.council = council_result
            candidate.final_status = council_result.route
            candidates.append(candidate)

        return candidates

    def run_scan(self, pairs: Iterable[str]) -> ScanResult:
        contexts = self.intake.build_contexts(pairs)
        all_candidates: List[CandidateSignal] = []

        for context in contexts:
            all_candidates.extend(self._build_candidates_for_pair(context))

        scan_result = self.publisher.publish(all_candidates)
        scan_result.audit["pairs_scanned"] = [ctx.pair for ctx in contexts]
        scan_result.audit["specialists"] = self.registry.list_specialists()

        return scan_result
