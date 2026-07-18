from __future__ import annotations

from typing import Any, Dict, Iterable, List

from scannermodels import (
    CandidateSignal,
    PublishedSignal,
    ScanResult,
    CommonIndicatorContext,
    ExecutionContext,
    ClaimContext,
    SignalDiagnostics,
    ClaimScore,
    ToolCheck,
)


class ScannerPublisher:
    def publish(
        self,
        candidates: Iterable[CandidateSignal],
        positions: List[Dict[str, object]] | None = None,
        audit: Dict[str, object] | None = None,
    ) -> ScanResult:
        """
        Convert reviewed + adjudicated CandidateSignal objects into a ScanResult,
        grouping into live / caution / killed buckets and attaching positions + audit.
        """
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

        # Basic counts for downstream diagnostics and website summary
        counts = result.audit.setdefault("counts", {})
        counts["live_signals"] = len(result.live_signals)
        counts["caution_signals"] = len(result.caution_signals)
        counts["killed_signals"] = len(result.killed_signals)
        counts["positions"] = len(result.positions)

        return result

    def _to_published(self, candidate: CandidateSignal) -> PublishedSignal:
        """
        Merge candidate + review + council into a PublishedSignal.

        - Uses Remi-adjusted score when available.
        - Uses Council route + execution_ready when available.
        - Builds both:
          - canonical payload dict (for schema/website), and
          - typed helper contexts (indicators, execution, claims, diagnostics).
        """
        review = candidate.review
        council = candidate.council

        adjusted_score = review.adjusted_score if review else candidate.score
        route = council.route if council else "killed_signals"
        execution_ready = council.execution_ready if council else False

        merged_context = self._build_context(candidate)
        payload = self._build_payload(candidate, merged_context)

        # Typed helpers for internal use, tests, and future modules
        indicators_ctx = self._build_indicators_context(merged_context)
        execution_ctx = self._build_execution_context(candidate, merged_context)
        claims_ctx = self._build_claims_context(candidate, merged_context)
        diagnostics_ctx = self._build_diagnostics_context(candidate, merged_context, payload)

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
            confidence=candidate.confidence,
            final_status=candidate.final_status,
            warnings=list(candidate.warnings),
            tags=list(candidate.tags),
            payload=payload,
            review=review,
            council=council,
            indicators=indicators_ctx,
            execution=execution_ctx,
            claims=claims_ctx,
            diagnostics=diagnostics_ctx,
        )

    # --- Payload + context builders (canonical contract) ---

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

        # Core identity / state
        base.setdefault("timeframe", "1h")
        base.setdefault("status", candidate.final_status)
        base.setdefault("confidence", candidate.confidence)
        base.setdefault("market_regime", base.get("regime", "unknown"))

        # Common indicator layer
        base.setdefault("trend_context", self._extract_trend_context(base))
        base.setdefault("st_context", self._extract_st_context(base))
        base.setdefault("volume_context", self._extract_volume_context(base))
        base.setdefault("volatility_context", self._extract_volatility_context(base))
        base.setdefault("structure_context", self._extract_structure_context(base))

        base.setdefault("mtf_verdict", base.get("mtf_verdict"))
        base.setdefault("mtf_score", base.get("mtf_score"))
        base.setdefault("mtf_alignment", base.get("mtf_alignment"))

        # Execution layer
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

        # Claim layer
        base.setdefault("attached_bots", [candidate.specialist])
        base.setdefault("lead_bot", candidate.specialist)
        base.setdefault("co_claims", [])
        base.setdefault("claim_status", self._infer_claim_status(candidate))
        base.setdefault("claim_scores", [])
        base.setdefault("tool_checks", [])
        base.setdefault("common_indicator_ok", base.get("common_indicator_ok"))
        base.setdefault("mission_fit", base.get("mission_fit"))
        base.setdefault("survival_ok", base.get("survival_ok"))

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
            "mission_fit": context.get("mission_fit"),
            "survival_ok": context.get("survival_ok"),
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

    # --- Typed helper context builders ---

    def _build_indicators_context(self, context: Dict[str, Any]) -> CommonIndicatorContext:
        return CommonIndicatorContext(
            market_regime=context.get("market_regime"),
            timeframe=context.get("timeframe"),
            mtf_verdict=context.get("mtf_verdict"),
            mtf_score=context.get("mtf_score"),
            mtf_alignment=context.get("mtf_alignment"),
            trend_context=self._build_trend_context(context),
            st_context=self._build_st_context(context),
            volume_context=self._build_volume_context(context),
            volatility_context=self._build_volatility_context(context),
            structure_context=self._build_structure_context(context),
            extra={},
        )

    def _build_execution_context(
        self,
        candidate: CandidateSignal,
        context: Dict[str, Any],
    ) -> ExecutionContext:
        return ExecutionContext(
            entry_idea=candidate.entry_idea,
            stop_idea=candidate.stop_idea,
            target_idea=candidate.target_idea,
            rr_estimate=context.get("rr_estimate"),
            offensive_score=context.get("offensive_score"),
            defensive_score=context.get("defensive_score"),
            trap_risk=context.get("trap_risk"),
            survivability=context.get("survivability"),
            liquidity_proximity=context.get("liquidity_proximity"),
            execution_intent=context.get("execution_intent"),
            invalidation_basis=context.get("invalidation_basis"),
            target_basis=context.get("target_basis"),
            cut_now=context.get("cut_now", False),
        )

    def _build_claims_context(
        self,
        candidate: CandidateSignal,
        context: Dict[str, Any],
    ) -> ClaimContext:
        # Convert any dict-like score/check objects into typed ClaimScore / ToolCheck if present
        raw_scores = context.get("claim_scores") or []
        claim_scores: List[ClaimScore] = []
        for item in raw_scores:
            if isinstance(item, dict):
                claim_scores.append(
                    ClaimScore(
                        bot=item.get("bot", candidate.specialist),
                        score=float(item.get("score", 0.0)),
                        threshold=item.get("threshold"),
                        lead_threshold=item.get("lead_threshold"),
                        outcome=item.get("outcome"),
                    )
                )

        raw_checks = context.get("tool_checks") or []
        tool_checks: List[ToolCheck] = []
        for item in raw_checks:
            if isinstance(item, dict):
                tool_checks.append(
                    ToolCheck(
                        name=item.get("name", ""),
                        required=bool(item.get("required", True)),
                        available=bool(item.get("available", False)),
                        note=item.get("note", ""),
                    )
                )

        return ClaimContext(
            lead_bot=context.get("lead_bot", candidate.specialist),
            attached_bots=list(context.get("attached_bots", [candidate.specialist])),
            co_claims=list(context.get("co_claims", [])),
            claim_status=context.get("claim_status"),
            claim_scores=claim_scores,
            tool_checks=tool_checks,
            common_indicator_ok=context.get("common_indicator_ok"),
            mission_fit=context.get("mission_fit"),
            survival_ok=context.get("survival_ok"),
        )

    def _build_diagnostics_context(
        self,
        candidate: CandidateSignal,
        context: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> SignalDiagnostics:
        return SignalDiagnostics(
            warnings=list(candidate.warnings),
            tags=list(candidate.tags),
            raw_context=context,
            raw_evidence=dict(candidate.evidence),
            legacy_payload=payload,
        )

    # --- Context extraction helpers (shared by payload + typed contexts) ---

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

    # Typed variants built from the same dicts

    def _build_trend_context(self, context: Dict[str, Any]):
        tc = self._extract_trend_context(context)
        return TrendContext(
            ribbon_state=tc.get("ribbon_state"),
            ribbon_order=list(tc.get("ribbon_order") or []),
            ribbon_slope=tc.get("ribbon_slope"),
            compression_state=tc.get("compression_state"),
            expansion_state=tc.get("expansion_state"),
            reclaim_status=tc.get("reclaim_status"),
        )

    def _build_st_context(self, context: Dict[str, Any]):
        sc = self._extract_st_context(context)
        return SupertrendContext(
            direction=sc.get("direction"),
            line_distance=sc.get("line_distance"),
            strength=sc.get("strength"),
            phase=sc.get("phase"),
            flip_risk=sc.get("flip_risk"),
        )

    def _build_volume_context(self, context: Dict[str, Any]):
        vc = self._extract_volume_context(context)
        return VolumeContext(
            relative_volume=vc.get("relative_volume"),
            participation_grade=vc.get("participation_grade"),
            spike_state=vc.get("spike_state"),
            quiet_pullback=vc.get("quiet_pullback"),
            delta_state=vc.get("delta_state"),
            cvd_state=vc.get("cvd_state"),
        )

    def _build_volatility_context(self, context: Dict[str, Any]):
        vc = self._extract_volatility_context(context)
        return VolatilityContext(
            atr_level=vc.get("atr_level"),
            atr_expansion=vc.get("atr_expansion"),
            compression_release=vc.get("compression_release"),
        )

    def _build_structure_context(self, context: Dict[str, Any]):
        sc = self._extract_structure_context(context)
        return StructureContext(
            nearest_swing_high=sc.get("nearest_swing_high"),
            nearest_swing_low=sc.get("nearest_swing_low"),
            bos_level=sc.get("bos_level"),
            choch_level=sc.get("choch_level"),
            zone_ref=sc.get("zone_ref"),
            target_path=list(sc.get("target_path") or []),
            liquidity_map=list(sc.get("liquidity_map") or []),
        )

    # --- Misc helpers ---

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
