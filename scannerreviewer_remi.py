from __future__ import annotations

from typing import List

from scannermodels import CandidateSignal, RemiReview


class RemiReviewer:
    def __init__(
        self,
        min_score: float = 60.0,
        caution_score: float = 72.0,
        strong_score: float = 85.0,
    ) -> None:
        self.min_score = min_score
        self.caution_score = caution_score
        self.strong_score = strong_score

    def review(self, candidate: CandidateSignal) -> RemiReview:
        score = float(candidate.score)
        confidence_delta = 0.0
        caution_flags: List[str] = []
        evidence_notes: List[str] = []

        if candidate.confidence < 0.55:
            score -= 8.0
            confidence_delta -= 0.05
            caution_flags.append("low_confidence")
            evidence_notes.append("Base confidence under 0.55.")

        if not candidate.thesis or len(candidate.thesis.strip()) < 20:
            score -= 6.0
            caution_flags.append("thin_thesis")
            evidence_notes.append("Thesis detail is too weak.")

        if candidate.entry_idea is None:
            score -= 4.0
            caution_flags.append("missing_entry_idea")
            evidence_notes.append("Entry idea missing.")

        if candidate.stop_idea is None:
            score -= 5.0
            caution_flags.append("missing_stop_idea")
            evidence_notes.append("Stop idea missing.")

        if candidate.target_idea is None:
            score -= 3.0
            caution_flags.append("missing_target_idea")
            evidence_notes.append("Target idea missing.")

        if candidate.warnings:
            score -= min(len(candidate.warnings) * 2.0, 8.0)
            caution_flags.extend([f"warning:{w}" for w in candidate.warnings[:3]])
            evidence_notes.append("Specialist emitted warnings.")

        if "countertrend" in candidate.tags:
            score -= 5.0
            caution_flags.append("countertrend")
            evidence_notes.append("Setup is tagged countertrend.")

        if "breakout" in candidate.tags and candidate.confidence >= 0.70:
            score += 4.0
            confidence_delta += 0.03
            evidence_notes.append("Breakout with adequate confidence.")

        if score >= self.strong_score:
            decision = "approve"
            rationale = "High-quality candidate survived REMI review."
        elif score >= self.caution_score:
            decision = "caution"
            rationale = "Candidate is viable but needs guarded routing."
        elif score >= self.min_score:
            decision = "watchlist"
            rationale = "Candidate is informative but not execution-ready."
        else:
            decision = "reject"
            rationale = "Candidate failed minimum REMI quality bar."

        return RemiReview(
            decision=decision,
            adjusted_score=round(score, 2),
            confidence_delta=round(confidence_delta, 4),
            rationale=rationale,
            caution_flags=caution_flags,
            evidence_notes=evidence_notes,
        )
