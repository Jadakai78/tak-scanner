from __future__ import annotations

from typing import Any, Dict, List, Optional


class TakScannerV3SchemaMixin:
    SCORE_VERSION = "v1_8factor_weighted_bootstrap"
    ST_AI_CONFIG_VERSION = "knn_bootstrap_v1"
    ST_AI_MODEL = "knn"

    @staticmethod
    def default_score_weights() -> Dict[str, float]:
        return {
            "STRUCTURE_BREAK": 0.20,
            "OB_PROXIMITY": 0.17,
            "MOMENTUM_ALIGN": 0.15,
            "CANDLE_QUALITY": 0.11,
            "HTF_BIAS": 0.16,
            "VOLUME_EXPANSION": 0.06,
            "FUNDING_EDGE": 0.05,
            "FEAR_GREED_ALIGN": 0.10,
        }

    @staticmethod
    def clamp01(v: Any) -> Optional[float]:
        try:
            x = float(v)
        except (TypeError, ValueError):
            return None
        return max(0.0, min(1.0, x))

    def bootstrap_score_components(
        self,
        raw_signal: Dict[str, Any],
        ai_st: Optional[Dict[str, Any]],
        mtf_payload: Optional[Dict[str, Any]],
        fg_payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Optional[float]]:
        bias = str(raw_signal.get("bias", "")).upper()
        st_dir = str((ai_st or {}).get("direction", "")).upper()
        momentum_align = 1.0 if bias and st_dir and bias in st_dir else 0.0 if st_dir else None

        fg_score = None
        if isinstance(fg_payload, dict):
            fg_score = fg_payload.get("score")
        try:
            fg_num = float(fg_score) if fg_score is not None else None
        except (TypeError, ValueError):
            fg_num = None
        fear_greed_align = None
        if fg_num is not None:
            if bias == "SHORT":
                fear_greed_align = 1.0 if fg_num <= 35 else 0.55 if fg_num <= 55 else 0.25
            elif bias == "LONG":
                fear_greed_align = 1.0 if fg_num >= 65 else 0.55 if fg_num >= 45 else 0.25

        mtf_score = None
        if isinstance(mtf_payload, dict):
            mtf_score = mtf_payload.get("score") or mtf_payload.get("mtf_score")

        return {
            "STRUCTURE_BREAK": self.clamp01(raw_signal.get("structure_quality") or raw_signal.get("signal_quality") or raw_signal.get("setup_quality")),
            "OB_PROXIMITY": self.clamp01(raw_signal.get("ob_proximity")),
            "MOMENTUM_ALIGN": self.clamp01(momentum_align),
            "CANDLE_QUALITY": self.clamp01(raw_signal.get("candle_quality") or raw_signal.get("structure_quality")),
            "HTF_BIAS": self.clamp01(mtf_score),
            "VOLUME_EXPANSION": self.clamp01(raw_signal.get("volume_expansion")),
            "FUNDING_EDGE": self.clamp01(raw_signal.get("funding_edge")),
            "FEAR_GREED_ALIGN": self.clamp01(fear_greed_align),
        }

    def compute_score_base(self, components: Dict[str, Optional[float]], weights: Dict[str, float]) -> Optional[float]:
        numerator = 0.0
        denom = 0.0
        for key, weight in weights.items():
            value = components.get(key)
            if value is None:
                continue
            numerator += float(value) * float(weight)
            denom += float(weight)
        if denom == 0:
            return None
        return round((numerator / denom) * 100.0, 2)

    def bootstrap_edge_multiplier(self, raw_signal: Dict[str, Any], ai_st: Optional[Dict[str, Any]] = None) -> float:
        multiplier = 1.0
        if raw_signal.get("edge_confirmed"):
            multiplier += 0.15
        if raw_signal.get("tier") == "TIER_A":
            multiplier += 0.05
        st_strength = (ai_st or {}).get("strength")
        try:
            if st_strength is not None and float(st_strength) >= 0.75:
                multiplier += 0.03
        except (TypeError, ValueError):
            pass
        return round(multiplier, 4)

    def bootstrap_delta_fields(self, raw_signal: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "delta_score": raw_signal.get("delta_score"),
            "delta_align": raw_signal.get("delta_align"),
        }

    def bootstrap_score_reasons(
        self,
        raw_signal: Dict[str, Any],
        components: Dict[str, Optional[float]],
        action_state: str,
        wait_reason: Optional[str] = None,
    ) -> List[str]:
        reasons: List[str] = []
        if components.get("MOMENTUM_ALIGN") == 1.0:
            reasons.append("SuperTrend AI aligned with trade bias")
        if (components.get("HTF_BIAS") or 0) >= 0.6:
            reasons.append("HTF bias improved under feature-quality scoring")
        if (components.get("STRUCTURE_BREAK") or 0) >= 0.7:
            reasons.append("Structure quality strongly supports setup")
        if (components.get("OB_PROXIMITY") or 0) >= 0.6:
            reasons.append("Order-block proximity is favorable")
        if raw_signal.get("edge_confirmed"):
            reasons.append("Edge confirmed bonus applied")
        if action_state == "WAIT" and wait_reason:
            reasons.append(wait_reason)
        return reasons

    def bootstrap_action_state(
        self,
        raw_signal: Dict[str, Any],
        remi_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Optional[str]]:
        remi_status = str((remi_payload or {}).get("status") or raw_signal.get("remi_status") or "").upper()
        tier = str(raw_signal.get("tier") or "").upper()
        grade = str(raw_signal.get("grade") or "").upper()

        if remi_status in {"KILLED", "REJECT", "BLOCKED"}:
            reason = (remi_payload or {}).get("reason") or raw_signal.get("kill_reason") or "Rejected by Remi"
            return {
                "action_state": "REJECT",
                "action_state_reason": reason,
                "wait_reason": None,
            }

        if tier == "TIER_B":
            wait_reason = "Await Tier A confirmation"
            return {
                "action_state": "WAIT",
                "action_state_reason": "Tier B waits on Tier A confirmation",
                "wait_reason": wait_reason,
            }

        if grade in {"S", "A", "B"}:
            return {
                "action_state": "CLICK",
                "action_state_reason": "Signal is currently actionable under bootstrap lane rules",
                "wait_reason": None,
            }

        return {
            "action_state": "WAIT",
            "action_state_reason": "Signal is valid but not yet actionable",
            "wait_reason": "Await stronger qualification",
        }

    def finalize_signal_v3(
        self,
        raw_signal: Dict[str, Any],
        ai_st: Optional[Dict[str, Any]] = None,
        mtf_payload: Optional[Dict[str, Any]] = None,
        fg_payload: Optional[Dict[str, Any]] = None,
        remi_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        signal = dict(raw_signal)
        weights = self.default_score_weights()
        components = self.bootstrap_score_components(signal, ai_st, mtf_payload, fg_payload)
        score_base = self.compute_score_base(components, weights)
        edge_multiplier = self.bootstrap_edge_multiplier(signal, ai_st)
        delta_fields = self.bootstrap_delta_fields(signal)
        action_fields = self.bootstrap_action_state(signal, remi_payload)

        legacy_conviction = signal.get("conviction")
        try:
            legacy_conviction_num = float(legacy_conviction) if legacy_conviction is not None else None
        except (TypeError, ValueError):
            legacy_conviction_num = None

        final_conviction = legacy_conviction_num
        if final_conviction is None and score_base is not None:
            final_conviction = round(score_base * edge_multiplier, 2)

        score_reasons = self.bootstrap_score_reasons(
            signal,
            components,
            action_fields["action_state"],
            action_fields.get("wait_reason"),
        )

        signal.update({
            "score_version": self.SCORE_VERSION,
            "score_components": components,
            "score_weights": weights,
            "score_base": score_base,
            "edge_multiplier": edge_multiplier,
            "score_reasons": score_reasons,
            "final_conviction": final_conviction,
            "delta_score": delta_fields["delta_score"],
            "delta_align": delta_fields["delta_align"],
            "action_state": action_fields["action_state"],
            "action_state_reason": action_fields["action_state_reason"],
            "wait_reason": action_fields["wait_reason"],
            "st_ai_direction": (ai_st or {}).get("direction"),
            "st_ai_strength": (ai_st or {}).get("strength"),
            "st_ai_multiplier": (ai_st or {}).get("multiplier"),
            "st_ai_config_version": self.ST_AI_CONFIG_VERSION,
            "st_ai_model": self.ST_AI_MODEL,
        })
        return signal


# Integration notes:
# 1. Mix TakScannerV3SchemaMixin into the existing tak_scanner_v3 scanner class.
# 2. Replace or wrap the current finalize_signal(...) call with finalize_signal_v3(...).
# 3. Keep existing legacy fields untouched so downstream consumers do not break.
# 4. Leave self.bus.update(...) intact for Step 1 unless top-level schema expansion is intentionally approved.
