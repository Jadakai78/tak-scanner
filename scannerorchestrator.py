from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, Iterable, List, Optional

from scannermodels import PairContext, SpecialistObservation

logger = logging.getLogger(__name__)
logger.propagate = False


@dataclass
class WeaponClaim:
    weapon: str
    setup_type: str
    side: str
    confidence: float
    score: float
    thesis: str
    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PanelStateRecord:
    pair: str
    timeframe: str
    market_regime: str
    board_state: str
    side: str = "NEUTRAL"
    confidence: float = 0.0
    score: float = 0.0
    primary_weapon: Optional[str] = None
    reason: str = ""
    oracle_bias: Optional[str] = None
    oracle_rsi_trend: Optional[str] = None
    oracle_structure_trend: Optional[str] = None
    trap_state: Optional[str] = None
    pressure_state: Optional[str] = None
    weapon_claims: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    oracle_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ScannerOrchestrator:
    """
    Oracle-native panel-state orchestrator.

    Input:
      - PairContext objects produced by Oracle
    Output:
      - one PanelStateRecord per pair

    Design goals:
      - no pair disappears from the board
      - specialists produce claims, not mandatory final candidates
      - forward-compatible with richer Oracle truth packets later
    """

    def __init__(
        self,
        specialist_registry: Any,
        remi_reviewer: Any | None = None,
        council: Any | None = None,
    ) -> None:
        self.specialist_registry = specialist_registry
        self.remi_reviewer = remi_reviewer
        self.council = council

    def run(
        self,
        contexts: Iterable[PairContext],
        shared_state: Dict[str, Any] | None = None,
    ) -> List[PanelStateRecord]:
        shared_state = dict(shared_state or {})
        contexts = list(contexts)

        logger.info("Oracle panel orchestrator contextcount=%s", len(contexts))

        finalized: List[PanelStateRecord] = []

        for context in contexts:
            logger.info(
                "Oracle panel orchestrator pair=%s regime=%s",
                context.pair,
                context.market_regime,
            )
            try:
                panel_record = self._run_pair(context, shared_state)
            except Exception:
                logger.exception(
                    "Oracle panel orchestrator pair_failed pair=%s regime=%s",
                    context.pair,
                    context.market_regime,
                )
                panel_record = self._build_fallback_record(
                    context,
                    board_state="wait",
                    reason="Pair failed during orchestration; held in wait.",
                )

            finalized.append(panel_record)

        return finalized

    def _run_pair(
        self,
        context: PairContext,
        shared_state: Dict[str, Any],
    ) -> PanelStateRecord:
        specialists = self._resolve_specialists(context)
        claims: List[WeaponClaim] = []

        for specialist in specialists:
            name = getattr(specialist, "name", specialist.__class__.__name__)
            fgscore = shared_state.get("fgscore", 50)

            logger.info(
                "Oracle panel adapter calling engine=%s pair=%s regime=%s fg=%s",
                name,
                context.pair,
                context.market_regime,
                fgscore,
            )

            try:
                observation = self._invoke_specialist(specialist, context, shared_state)
            except Exception:
                logger.exception(
                    "Oracle panel specialist_failed pair=%s engine=%s regime=%s",
                    context.pair,
                    name,
                    context.market_regime,
                )
                continue

            logger.info(
                "Oracle panel pair=%s engine=%s rawnone=%s",
                context.pair,
                name,
                observation is None,
            )

            if observation is None:
                continue

            claim = self._observation_to_claim(observation, context, name)
            claims.append(claim)

        return self._build_panel_state(context, claims, shared_state)

    def _resolve_specialists(self, context: PairContext) -> List[Any]:
        resolver = getattr(self.specialist_registry, "resolve_for_regime", None)
        if callable(resolver):
            specialists = resolver(context.market_regime)
            return list(specialists or [])
        return []

    def _build_panel_state(
        self,
        context: PairContext,
        claims: List[WeaponClaim],
        shared_state: Dict[str, Any],
    ) -> PanelStateRecord:
        oracle_bias = self._extract_oracle_bias(context)
        oracle_rsi_trend = self._extract_nested_value(
            context,
            ("market_state", "rsi_trend"),
            ("market_state", "oracle_rsi_trend"),
            ("indicators", "rsi_trend"),
            ("context", "rsi_trend"),
        )
        oracle_structure_trend = self._extract_nested_value(
            context,
            ("market_state", "structure_trend"),
            ("market_state", "oracle_structure_trend"),
            ("indicators", "structure_trend"),
            ("context", "structure_trend"),
        )
        trap_state = self._extract_nested_value(
            context,
            ("market_state", "trap_state"),
            ("diagnostics", "trap_state"),
            ("context", "trap_state"),
        )
        pressure_state = self._extract_nested_value(
            context,
            ("market_state", "pressure_state"),
            ("indicators", "pressure_state"),
            ("context", "pressure_state"),
        )

        if not claims:
            return self._build_no_claim_record(
                context=context,
                oracle_bias=oracle_bias,
                oracle_rsi_trend=oracle_rsi_trend,
                oracle_structure_trend=oracle_structure_trend,
                trap_state=trap_state,
                pressure_state=pressure_state,
            )

        ranked_claims = sorted(
            claims,
            key=lambda c: (float(c.score), float(c.confidence)),
            reverse=True,
        )
        lead = ranked_claims[0]

        board_state = self._assign_board_state(
            context=context,
            lead=lead,
            claims=ranked_claims,
            oracle_bias=oracle_bias,
            trap_state=trap_state,
        )

        reason = self._build_reason(
            context=context,
            lead=lead,
            board_state=board_state,
            oracle_bias=oracle_bias,
            trap_state=trap_state,
            oracle_rsi_trend=oracle_rsi_trend,
            oracle_structure_trend=oracle_structure_trend,
        )

        warnings = self._merge_lists(*(claim.warnings for claim in ranked_claims))
        tags = self._merge_lists(*(claim.tags for claim in ranked_claims))

        return PanelStateRecord(
            pair=context.pair,
            timeframe=context.timeframe,
            market_regime=context.market_regime,
            board_state=board_state,
            side=lead.side or "NEUTRAL",
            confidence=round(float(lead.confidence), 4),
            score=round(float(lead.score), 2),
            primary_weapon=lead.weapon,
            reason=reason,
            oracle_bias=oracle_bias,
            oracle_rsi_trend=oracle_rsi_trend,
            oracle_structure_trend=oracle_structure_trend,
            trap_state=trap_state,
            pressure_state=pressure_state,
            weapon_claims=[asdict(c) for c in ranked_claims],
            warnings=warnings,
            tags=tags,
            oracle_context=self._serialize_pair_context(context),
        )

    def _build_no_claim_record(
        self,
        context: PairContext,
        oracle_bias: Optional[str],
        oracle_rsi_trend: Optional[str],
        oracle_structure_trend: Optional[str],
        trap_state: Optional[str],
        pressure_state: Optional[str],
    ) -> PanelStateRecord:
        board_state = "dead" if self._is_dead_pair(context, trap_state) else "wait"

        if board_state == "dead":
            reason = "No weapon claimed the pair and Oracle marks it as dead or trapped."
        else:
            reason = "No weapon claim yet; pair remains visible in wait."

        return PanelStateRecord(
            pair=context.pair,
            timeframe=context.timeframe,
            market_regime=context.market_regime,
            board_state=board_state,
            side="NEUTRAL",
            confidence=0.0,
            score=0.0,
            primary_weapon=None,
            reason=reason,
            oracle_bias=oracle_bias,
            oracle_rsi_trend=oracle_rsi_trend,
            oracle_structure_trend=oracle_structure_trend,
            trap_state=trap_state,
            pressure_state=pressure_state,
            weapon_claims=[],
            warnings=[],
            tags=[],
            oracle_context=self._serialize_pair_context(context),
        )

    def _build_fallback_record(
        self,
        context: PairContext,
        board_state: str,
        reason: str,
    ) -> PanelStateRecord:
        return PanelStateRecord(
            pair=context.pair,
            timeframe=context.timeframe,
            market_regime=context.market_regime,
            board_state=board_state,
            side="NEUTRAL",
            confidence=0.0,
            score=0.0,
            primary_weapon=None,
            reason=reason,
            oracle_bias=self._extract_oracle_bias(context),
            oracle_rsi_trend=None,
            oracle_structure_trend=None,
            trap_state=None,
            pressure_state=None,
            weapon_claims=[],
            warnings=["orchestrator_fallback"],
            tags=[],
            oracle_context=self._serialize_pair_context(context),
        )

    def _assign_board_state(
        self,
        context: PairContext,
        lead: WeaponClaim,
        claims: List[WeaponClaim],
        oracle_bias: Optional[str],
        trap_state: Optional[str],
    ) -> str:
        if self._is_dead_pair(context, trap_state):
            return "dead"

        if self._is_trap_heavy(trap_state) and float(lead.confidence) < 0.8:
            return "caution"

        if self._bias_conflicts(lead.side, oracle_bias) and float(lead.confidence) < 0.85:
            return "caution"

        if float(lead.score) >= 6.5 and float(lead.confidence) >= 0.60:
            return "execute"

        if float(lead.score) >= 4.5 and float(lead.confidence) >= 0.40:
            return "caution"

        return "wait"

    def _build_reason(
        self,
        context: PairContext,
        lead: WeaponClaim,
        board_state: str,
        oracle_bias: Optional[str],
        trap_state: Optional[str],
        oracle_rsi_trend: Optional[str],
        oracle_structure_trend: Optional[str],
    ) -> str:
        parts: List[str] = []

        if lead.weapon:
            parts.append(f"{lead.weapon} leads")

        if lead.setup_type:
            parts.append(f"setup={lead.setup_type}")

        if oracle_bias:
            parts.append(f"bias={oracle_bias}")

        if oracle_rsi_trend:
            parts.append(f"rsi_trend={oracle_rsi_trend}")

        if oracle_structure_trend:
            parts.append(f"struct_trend={oracle_structure_trend}")

        if trap_state:
            parts.append(f"trap={trap_state}")

        parts.append(f"state={board_state}")
        return " | ".join(parts)

    def _observation_to_claim(
        self,
        observation: SpecialistObservation,
        context: PairContext,
        specialist_name: str,
    ) -> WeaponClaim:
        merged_context = dict(context.context)
        merged_context.update(observation.context or {})

        merged_context.setdefault("pair", context.pair)
        merged_context.setdefault("timeframe", context.timeframe)
        merged_context.setdefault("market_regime", context.market_regime)
        merged_context.setdefault("regime", context.market_regime)
        merged_context.setdefault("session", context.session)
        merged_context.setdefault("fear_greed", context.fear_greed)

        if context.indicators:
            merged_context.setdefault("pair_indicators", dict(context.indicators))
        if context.market_state:
            merged_context.setdefault("pair_market_state", dict(context.market_state))
        if context.diagnostics:
            merged_context.setdefault("pair_diagnostics", dict(context.diagnostics))

        return WeaponClaim(
            weapon=observation.specialist or specialist_name,
            setup_type=observation.setup_type or "unclassified",
            side=observation.side or "NEUTRAL",
            confidence=float(observation.confidence or 0.0),
            score=float(observation.score or 0.0),
            thesis=observation.thesis or "",
            warnings=list(observation.warnings or []),
            tags=list(observation.tags or []),
            evidence=dict(observation.evidence or {}),
            context=merged_context,
        )

    def _invoke_specialist(
        self,
        specialist: Any,
        context: PairContext,
        shared_state: Dict[str, Any],
    ) -> SpecialistObservation | None:
        for method_name in ("observe", "scan", "generate", "run"):
            method = getattr(specialist, method_name, None)
            if callable(method):
                if hasattr(context, "to_dict") and callable(getattr(context, "to_dict")):
                    context_payload = context.to_dict()
                elif hasattr(context, "__dict__"):
                    context_payload = dict(vars(context))
                else:
                    context_payload = context

                result = method(context=context_payload, shared_state=shared_state)
                return self._normalize_observation(result, specialist, context)
        return None

    def _normalize_observation(
        self,
        result: Any,
        specialist: Any,
        context: PairContext,
    ) -> SpecialistObservation | None:
        if result is None:
            return None

        if isinstance(result, SpecialistObservation):
            return result

        if isinstance(result, dict):
            raw_context = dict(result.get("context") or {})
            raw_context.setdefault("pair", context.pair)
            raw_context.setdefault("timeframe", context.timeframe)
            raw_context.setdefault("market_regime", context.market_regime)
            raw_context.setdefault("regime", context.market_regime)
            raw_context.setdefault("session", context.session)
            raw_context.setdefault("fear_greed", context.fear_greed)

            return SpecialistObservation(
                specialist=str(
                    result.get(
                        "specialist",
                        getattr(specialist, "name", specialist.__class__.__name__),
                    )
                ),
                pair=str(result.get("pair", context.pair)),
                setup_type=str(result.get("setup_type", "unclassified")),
                side=str(result.get("side", result.get("bias", "NEUTRAL"))),
                confidence=float(result.get("confidence", 0.0)),
                score=float(result.get("score", 0.0)),
                thesis=str(result.get("thesis", result.get("summary", ""))),
                evidence=dict(result.get("evidence", {})),
                warnings=list(result.get("warnings", [])),
                tags=list(result.get("tags", [])),
                context=raw_context,
            )

        return None

    def _extract_oracle_bias(self, context: PairContext) -> Optional[str]:
        return self._extract_nested_value(
            context,
            ("market_state", "oracle_bias"),
            ("market_state", "bias"),
            ("market_state", "mtf_verdict"),
            ("context", "oracle_bias"),
            ("context", "bias"),
            ("indicators", "mtf_verdict"),
        )

    def _extract_nested_value(
        self,
        context: PairContext,
        *paths: tuple[str, str],
    ) -> Optional[str]:
        for container_name, key in paths:
            container = getattr(context, container_name, None)
            if isinstance(container, dict):
                value = container.get(key)
                if value is not None and value != "":
                    return str(value)
        return None

    def _serialize_pair_context(self, context: PairContext) -> Dict[str, Any]:
        if hasattr(context, "to_dict") and callable(getattr(context, "to_dict")):
            return context.to_dict()

        if is_dataclass(context):
            return asdict(context)

        if hasattr(context, "__dict__"):
            return dict(vars(context))

        return {"pair": getattr(context, "pair", None)}

    def _is_dead_pair(self, context: PairContext, trap_state: Optional[str]) -> bool:
        regime = str(context.market_regime or "").upper()
        if regime == "DEAD":
            return True

        trap_text = str(trap_state or "").lower()
        if trap_text in {"dead", "invalid", "blocked", "do_not_trade"}:
            return True

        return False

    def _is_trap_heavy(self, trap_state: Optional[str]) -> bool:
        text = str(trap_state or "").lower()
        return text in {
            "trap",
            "high_trap_risk",
            "retail_trap",
            "squeeze_risk",
            "fake_break_risk",
        }

    def _bias_conflicts(self, side: str, oracle_bias: Optional[str]) -> bool:
        side_text = str(side or "").upper()
        bias_text = str(oracle_bias or "").upper()

        if not side_text or not bias_text:
            return False

        bullish = {"LONG", "BUY", "BULL", "BULLISH", "UP"}
        bearish = {"SHORT", "SELL", "BEAR", "BEARISH", "DOWN"}

        if side_text in bullish and bias_text in bearish:
            return True
        if side_text in bearish and bias_text in bullish:
            return True
        return False

    def _merge_lists(self, *iterables: Iterable[str]) -> List[str]:
        seen = set()
        merged: List[str] = []
        for iterable in iterables:
            for item in iterable:
                if item not in seen:
                    seen.add(item)
                    merged.append(item)
        return merged
