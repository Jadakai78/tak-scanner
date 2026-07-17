from __future__ import annotations

from typing import Any, Dict, Iterable, List

from scannermodels import CandidateSignal, PublishedSignal, ScanResult


class ScannerPublisher:
    def publish(
        self,
        candidates: Iterable[CandidateSignal],
        positions: List[Dict[str, object]] | None = None,
        audit: Dict[str, object] | None = None,
    ) -> ScanResult:
        result = ScanResult(
            positions=list(positions or []),
            audit=dict(audit or {}),
        )

        for candidate in candidates:
            published = self._to_published(candidate)
            route = published.route

            if route == "live_signals":
                result.live_signals.append(published)
            elif route == "caution_signals":
                result.caution_signals.append(published)
            else:
                result.killed_signals.append(published)

        result.audit.setdefault("counts", {})
        result.audit["counts"]["live_signals"] = len(result.live_signals)
        result.audit["counts"]["caution_signals"] = len(result.caution_signals)
        result.audit["counts"]["killed_signals"] = len(result.killed_signals)
        result.audit["counts"]["positions"] = len(result.positions)
        return result

    def _to_published(self, candidate: CandidateSignal) -> PublishedSignal:
        review = candidate.review
        council = candidate.council

        adjusted_score = review.adjusted_score if review else candidate.score
        route = council.route if council else "killed_signals"
        execution_ready = council.execution_ready if council else False

        merged_context = self._build_context(candidate)
        payload = self._build_payload(candidate, merged_context)

        return PublishedSignal(
            bucket=route,
            pair=candidate.pair,
            candidate_id=candidate.candidate_id,
            setup_type=candidate.setup_type,
            side=candidate.side,
            score=round(float(adjusted_score), 2),
            specialist=candidate.specialist,
            thesis=candidate.thesis,
            route=route,
            execution_ready=execution_ready,
            warnings=list(candidate.warnings),
            tags=list(candidate.tags),
            payload=payload,
        )

    def _build_payload(
        self,
        candidate: CandidateSignal,
        merged_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "entry_idea": candidate.entry_idea,
            "stop_idea": candidate.stop_idea,
            "target_idea": candidate.target_idea,
            "evidence": dict(candidate.evidence),
            "context": merged_context,
            "review": self._serialize_review(candidate),
            "council": self._serialize_council(candidate),
            "claims": self._build_claims(candidate, merged_context),
            "execution": self._build_execution(candidate, merged_context),
        }

    def _build_context(self, candidate: CandidateSignal) -> Dict[str, Any]:
        base = dict(candidate.context)

        base.setdefault("timeframe", "1h")
        base.setdefault("status", candidate.final_status)
        base.setdefault("confidence", candidate.confidence)
        base.setdefault("market_regime", base.get("regime", "unknown"))

        base.setdefault("trend_context", self._extract_trend_context(base))
        base.setdefault("st_context", self._extract_st_context(base))
        base.setdefault("volume_context", self._extract_volume_context(base))
        base.setdefault("volatility_context", self._extract_volatility_context(base))
        base.setdefault("structure_context", self._extract_structure_context(base))

        base.setdefault("rr_estimate", self._estimate_rr(candidate))
        base.setdefault("offensive_score", base.get("offensive_score"))
        base.setdefault("defensive_score", base.get("defensive_score"))
        base.setdefault("trap_risk", base.get("trap_risk"))
        base.setdefault("survivability", base.get("survivability"))
        base.setdefault("liquidity_proximity", base.get("liquidity_proximity"))
        base.setdefault("execution_intent", base.get("execution_intent"))
        base.setdefault("invalidation_basis", base.get("invalidation_basis"))
        base.setdefault("target_basis", base.get("target_basis"))
        base.setdefault("cut_now", base.get("cut_now", False))

        base.setdefault("attached_bots", [candidate.specialist])
        base.setdefault("lead_bot", candidate.specialist)
        base.setdefault("co_claims", [])
        base.setdefault("claim_status", self._infer_claim_status(candidate))
        base.setdefault("claim_scores", [])
        base.setdefault("tool_checks", [])
        base.setdefault("common_indicator_ok", base.get("common_indicator_ok"))

        return base

    def _serialize_review(self, candidate: CandidateSignal) -> Dict[str, Any] | None:
        review = candidate.review
        if review is None:
            return None

        return {
            "decision": review.decision,
            "adjusted_score": review.adjusted_score,
            "confidence_delta": review.confidence_delta,
            "rationale": review.rationale,
            "caution_flags": list(review.caution_flags),
            "evidence_notes": list(review.evidence_notes),
        }

    def _serialize_council(self, candidate: CandidateSignal) -> Dict[str, Any] | None:
        council = candidate.council
        if council is None:
            return None

        return {
            "decision": council.decision,
            "battlefield_ok": council.battlefield_ok,
            "veto_reasons": list(council.veto_reasons),
            "route": council.route,
            "execution_ready": council.execution_ready,
        }

    def _build_claims(
        self,
        candidate: CandidateSignal,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "lead_bot": context.get("lead_bot", candidate.specialist),
            "attached_bots": list(context.get("attached_bots", [candidate.specialist])),
            "co_claims": list(context.get("co_claims", [])),
            "claim_status": context.get("claim_status"),
            "claim_scores": list(context.get("claim_scores", [])),
            "tool_checks": list(context.get("tool_checks", [])),
            "common_indicator_ok": context.get("common_indicator_ok"),
        }

    def _build_execution(
        self,
        candidate: CandidateSignal,
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "entry_idea": candidate.entry_idea,
            "stop_idea": candidate.stop_idea,
            "target_idea": candidate.target_idea,
            "rr_estimate": context.get("rr_estimate"),
            "offensive_score": context.get("offensive_score"),
            "defensive_score": context.get("defensive_score"),
            "trap_risk": context.get("trap_risk"),
            "survivability": context.get("survivability"),
            "liquidity_proximity": context.get("liquidity_proximity"),
            "execution_intent": context.get("execution_intent"),
            "invalidation_basis": context.get("invalidation_basis"),
            "target_basis": context.get("target_basis"),
            "cut_now": context.get("cut_now", False),
        }

    def _extract_trend_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "ribbon_state": context.get("ribbon_state"),
            "ribbon_order": context.get("ribbon_order"),
            "ribbon_slope": context.get("ribbon_slope"),
            "compression_state": context.get("compression_state"),
            "expansion_state": context.get("expansion_state"),
            "reclaim_status": context.get("reclaim_status"),
        }

    def _extract_st_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "direction": context.get("st_direction"),
            "line_distance": context.get("st_line_distance"),
            "strength": context.get("st_strength"),
            "phase": context.get("st_phase"),
            "flip_risk": context.get("st_flip_risk"),
        }

    def _extract_volume_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "relative_volume": context.get("volume_ratio"),
            "participation_grade": context.get("participation_grade"),
            "spike_state": context.get("volume_spike"),
            "quiet_pullback": context.get("quiet_pullback"),
            "delta_state": context.get("delta_state"),
            "cvd_state": context.get("cvd_state"),
        }

    def _extract_volatility_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "atr_level": context.get("atr"),
            "atr_expansion": context.get("atr_expansion"),
            "compression_release": context.get("compression_release"),
        }

    def _extract_structure_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "nearest_swing_high": context.get("nearest_swing_high"),
            "nearest_swing_low": context.get("nearest_swing_low"),
            "bos_level": context.get("bos_level"),
            "choch_level": context.get("choch_level"),
            "zone_ref": context.get("zone_ref"),
            "target_path": list(context.get("target_path", [])),
            "liquidity_map": list(context.get("liquidity_map", [])),
        }

    def _estimate_rr(self, candidate: CandidateSignal) -> float | None:
        entry = candidate.entry_idea
        stop = candidate.stop_idea
        target = candidate.target_idea

        if entry is None or stop is None or target is None:
            return None

        risk = abs(entry - stop)
        reward = abs(target - entry)

        if risk <= 0:
            return None

        return round(reward / risk, 3)

    def _infer_claim_status(self, candidate: CandidateSignal) -> str:
        if candidate.council is None:
            return "removed"
        if candidate.council.route == "live_signals":
            return "lead_claim"
        if candidate.council.route == "caution_signals":
            return "claim"
        return "removed"
