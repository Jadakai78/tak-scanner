from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from scannercouncil import ScannerCouncil
from scannerpair_intake import ScannerPairIntake
from scannerpublisher import ScannerPublisher
from scannerreviewer_remi import RemiReviewer
from scannerspecialist_registry import SpecialistRegistry
from scannermodels import CandidateSignal, ScanResult

logger = logging.getLogger("takscannerv4")


class ScannerOrchestrator:
    def __init__(
        self,
        intake: Optional[ScannerPairIntake] = None,
        specialists: Optional[SpecialistRegistry] = None,
        remi: Optional[RemiReviewer] = None,
        council: Optional[ScannerCouncil] = None,
        publisher: Optional[ScannerPublisher] = None,
    ) -> None:
        self.intake = intake or ScannerPairIntake()
        self.specialists = specialists or SpecialistRegistry()
        self.remi = remi or RemiReviewer()
        self.council = council or ScannerCouncil()
        self.publisher = publisher or ScannerPublisher()

    def review_candidate(self, candidate: CandidateSignal) -> CandidateSignal:
        candidate.review = self.remi.review(candidate)
        candidate.council = self.council.adjudicate(candidate)

        if candidate.council.route == "live_signals":
            candidate.final_status = "live"
        elif candidate.council.route == "caution_signals":
            candidate.final_status = "caution"
        else:
            candidate.final_status = "killed"
        return candidate

    def run(
        self,
        fg_score: int = 50,
        extras: Optional[Dict[str, Any]] = None,
    ) -> ScanResult:
        live_candidates: List[CandidateSignal] = []
        caution_candidates: List[CandidateSignal] = []
        killed_candidates: List[CandidateSignal] = []

        pair_records = self.intake.build_pair_records()

        for record in pair_records:
            pair = record["pair"]
            regime = record["regime"]
            df = record["dataframe"]

            logger.info("V4 orchestrator run pair=%s regime=%s", pair, regime)

            candidates = self.specialists.collect_candidates_for_pair(
                pair=pair,
                df=df,
                regime=regime,
                fg_score=fg_score,
                extras=extras,
            )

            for candidate in candidates:
                reviewed = self.review_candidate(candidate)

                if reviewed.final_status == "live":
                    live_candidates.append(reviewed)
                elif reviewed.final_status == "caution":
                    caution_candidates.append(reviewed)
                else:
                    killed_candidates.append(reviewed)

        audit = {
            "pair_count": len(pair_records),
            "live_count": len(live_candidates),
            "caution_count": len(caution_candidates),
            "killed_count": len(killed_candidates),
        }

        return self.publisher.build_scan_result(
            live_candidates=live_candidates,
            caution_candidates=caution_candidates,
            killed_candidates=killed_candidates,
            positions=[],
            audit=audit,
        )
