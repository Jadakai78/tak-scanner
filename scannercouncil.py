from __future__ import annotations

from scannermodels import CandidateSignal, CouncilDecision

from scannercandidate_factory import build_candidate
from scannermodels import CandidateSignal, PairContext, SpecialistObservation
from scannercouncil import ScannerCouncil
from scannerreviewer_remi import RemiReviewer


class ScannerCouncil:
    def __init__(
        self,
        live_threshold: float = 80.0,
        caution_threshold: float = 68.0,
    ) -> None:
        self.live_threshold = live_threshold
        self.caution_threshold = caution_threshold

    def adjudicate(self, candidate: CandidateSignal) -> CouncilDecision:
        veto_reasons = []

        if candidate.review is None:
            veto_reasons.append("missing_remi_review")
            return CouncilDecision(
                decision="kill",
                battlefield_ok=False,
                veto_reasons=veto_reasons,
                route="killed_signals",
                execution_ready=False,
            )

        reviewed_score = candidate.review.adjusted_score

        if candidate.review.decision == "reject":
            veto_reasons.append("remi_rejected")
            return CouncilDecision(
                decision="kill",
                battlefield_ok=False,
                veto_reasons=veto_reasons,
                route="killed_signals",
                execution_ready=False,
            )

        if "news_risk" in candidate.tags:
            veto_reasons.append("news_risk")
        if "illiquid" in candidate.tags:
            veto_reasons.append("illiquid")
        if "blocked" in candidate.tags:
            veto_reasons.append("blocked")

        battlefield_ok = len(veto_reasons) == 0

        if not battlefield_ok:
            return CouncilDecision(
                decision="kill",
                battlefield_ok=False,
                veto_reasons=veto_reasons,
                route="killed_signals",
                execution_ready=False,
            )

        if reviewed_score >= self.live_threshold and candidate.review.decision == "approve":
            return CouncilDecision(
                decision="live",
                battlefield_ok=True,
                veto_reasons=[],
                route="live_signals",
                execution_ready=True,
            )

        if reviewed_score >= self.caution_threshold:
            return CouncilDecision(
                decision="caution",
                battlefield_ok=True,
                veto_reasons=[],
                route="caution_signals",
                execution_ready=False,
            )

        return CouncilDecision(
            decision="kill",
            battlefield_ok=False,
            veto_reasons=["score_below_caution_band"],
            route="killed_signals",
            execution_ready=False,
        )
