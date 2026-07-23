from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActionState(str, Enum):
    HUNT = "hunt"
    WAIT = "wait"
    EXECUTE = "execute"
    STAND_DOWN = "stand_down"


class Direction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    NONE = "none"


class Alignment(str, Enum):
    ALIGNED = "aligned"
    NEUTRAL = "neutral"
    CONFLICTING = "conflicting"
    HOSTILE = "hostile"


class TimingState(str, Enum):
    EARLY = "early"
    RIPE = "ripe"
    LATE = "late"
    NOT_READY = "not_ready"


class HealthState(str, Enum):
    CLEAN = "clean"
    FRAGILE = "fragile"
    DEGRADED = "degraded"
    HOSTILE = "hostile"
    INVALID = "invalid"


class RemiVerdict(str, Enum):
    SURVIVE = "survive"
    CAUTION = "caution"
    KILL = "kill"
    BLOCK = "block"


class AprilAction(str, Enum):
    MANAGE = "manage"
    MONITOR = "monitor"
    DEFEND = "defend"
    PRESS = "press"
    NONE = "none"


class FacultyName(str, Enum):
    INSTITUTION_FOLLOWER = "institution_follower"
    RETAIL_TRADER_SPECIALIST = "retail_trader_specialist"
    CHOPPY_VOLATILE_HUNTER = "choppy_volatile_hunter"
    STRUCTURE_GODDESS = "structure_goddess"


@dataclass
class LiquidityLevel:
    label: str
    price: Optional[float] = None
    side: Optional[str] = None
    timeframe: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TrapZone:
    label: str
    zone_low: Optional[float] = None
    zone_high: Optional[float] = None
    trigger: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OracleState:
    symbol: str
    timestamp: str
    timeframe_stack: List[str] = field(default_factory=list)

    institutional_bias: Optional[str] = None
    retail_condition: Optional[str] = None
    volatility_state: Optional[str] = None
    chop_state: Optional[str] = None
    structure_state: Optional[str] = None
    session_context: Optional[str] = None

    liquidity_map: List[LiquidityLevel] = field(default_factory=list)
    trap_map: List[TrapZone] = field(default_factory=list)

    ownership_state: Optional[str] = None
    health_state: HealthState = HealthState.CLEAN

    market_notes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["health_state"] = self.health_state.value
        return data


@dataclass
class FacultyRead:
    faculty_name: FacultyName
    observation: str
    alignment: Alignment = Alignment.NEUTRAL
    opportunity: Optional[str] = None
    threat: Optional[str] = None
    timing_state: TimingState = TimingState.NOT_READY
    execution_note: Optional[str] = None
    kill_flag: bool = False
    confidence_note: Optional[str] = None
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["faculty_name"] = self.faculty_name.value
        data["alignment"] = self.alignment.value
        data["timing_state"] = self.timing_state.value
        return data


@dataclass
class OracleAction:
    action_state: ActionState
    direction: Direction = Direction.NONE

    thesis: Optional[str] = None
    entry_model: Optional[str] = None
    entry_zone_low: Optional[float] = None
    entry_zone_high: Optional[float] = None

    sl: Optional[float] = None
    tp: Optional[float] = None

    invalidation: Optional[str] = None
    kill_condition: Optional[str] = None

    execution_timeframe: Optional[str] = None
    timeframe_owner: Optional[str] = None

    remi_verdict: RemiVerdict = RemiVerdict.SURVIVE
    april_action: AprilAction = AprilAction.NONE

    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    confidence_internal: Optional[float] = None
    score_internal: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["action_state"] = self.action_state.value
        data["direction"] = self.direction.value
        data["remi_verdict"] = self.remi_verdict.value
        data["april_action"] = self.april_action.value
        return data


@dataclass
class OracleEnvelope:
    oracle_state: OracleState
    faculties: List[FacultyRead]
    oracle_action: OracleAction
    created_at: str
    source: str = "oracle"
    schema_version: str = "1.0.0"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "oracle_state": self.oracle_state.to_dict(),
            "faculties": [f.to_dict() for f in self.faculties],
            "oracle_action": self.oracle_action.to_dict(),
            "created_at": self.created_at,
            "source": self.source,
            "schema_version": self.schema_version,
        }


# -------------------------------------------------------------------
# Legacy panel / bus compatibility layer
# Keep this while old scanner/panel/feed code is still being migrated.
# -------------------------------------------------------------------


@dataclass
class OracleHealth:
    scheduler_ok: bool = True
    bus_ok: bool = True
    publish_ok: bool = True
    last_error: Optional[str] = None
    source_path: Optional[str] = None
    heartbeat: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scheduler_ok": self.scheduler_ok,
            "bus_ok": self.bus_ok,
            "publish_ok": self.publish_ok,
            "last_error": self.last_error,
            "source_path": self.source_path,
            "heartbeat": self.heartbeat,
        }


@dataclass
class OracleSummary:
    fg: int = 50
    fg_label: str = "Neutral"
    market_phase: str = "NEUTRAL"
    session: Optional[str] = None
    regime_summary: Optional[str] = None
    active_pairs: int = 0
    dead_pairs: int = 0
    scan_mode: str = "scheduled"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OraclePanelRow:
    pair: str
    action: str
    side: Optional[str] = None
    thesis: Optional[str] = None
    entry_idea: Optional[str] = None
    stop_idea: Optional[str] = None
    target_idea: Optional[str] = None
    specialist: Optional[str] = None
    intent: Optional[str] = None
    grade: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    score: Optional[float] = None
    confidence: Optional[float] = None
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OraclePanelPayload:
    last_scan: str
    next_scan: Optional[str]
    oracle_summary: OracleSummary
    actions: List[OraclePanelRow] = field(default_factory=list)
    positions: List[Dict[str, Any]] = field(default_factory=list)
    health: OracleHealth = field(default_factory=OracleHealth)

    def to_dict(self) -> Dict[str, Any]:
        actions = [a.to_dict() for a in self.actions]
        signals = [a.to_dict() for a in self.actions if a.action == "execute"]
        killed = [a.to_dict() for a in self.actions if a.action in {"kill", "stand_down"}]

        return {
            "last_scan": self.last_scan,
            "next_scan": self.next_scan,
            "oracle": self.oracle_summary.to_dict(),
            "actions": actions,
            "signals": signals,
            "killedsignals": killed,
            "positions": self.positions,
            "health": self.health.to_dict(),
        }


# -------------------------------------------------------------------
# Builders
# -------------------------------------------------------------------


def build_oracle_state(
    symbol: str,
    timeframe_stack: Optional[List[str]] = None,
    institutional_bias: Optional[str] = None,
    retail_condition: Optional[str] = None,
    volatility_state: Optional[str] = None,
    chop_state: Optional[str] = None,
    structure_state: Optional[str] = None,
    session_context: Optional[str] = None,
    ownership_state: Optional[str] = None,
    health_state: HealthState = HealthState.CLEAN,
    liquidity_map: Optional[List[LiquidityLevel]] = None,
    trap_map: Optional[List[TrapZone]] = None,
    market_notes: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> OracleState:
    return OracleState(
        symbol=symbol,
        timestamp=utc_now_iso(),
        timeframe_stack=timeframe_stack or [],
        institutional_bias=institutional_bias,
        retail_condition=retail_condition,
        volatility_state=volatility_state,
        chop_state=chop_state,
        structure_state=structure_state,
        session_context=session_context,
        liquidity_map=liquidity_map or [],
        trap_map=trap_map or [],
        ownership_state=ownership_state,
        health_state=health_state,
        market_notes=market_notes or [],
        metadata=metadata or {},
    )


def build_oracle_envelope(
    oracle_state: OracleState,
    faculties: Optional[List[FacultyRead]] = None,
    oracle_action: Optional[OracleAction] = None,
) -> OracleEnvelope:
    return OracleEnvelope(
        oracle_state=oracle_state,
        faculties=faculties or [],
        oracle_action=oracle_action or OracleAction(action_state=ActionState.WAIT),
        created_at=utc_now_iso(),
    )


# -------------------------------------------------------------------
# Legacy adapters for current scanner code
# -------------------------------------------------------------------


def _legacy_action_from_candidate_action(action: Optional[str]) -> ActionState:
    value = (action or "").lower().strip()
    if value in {"signal", "execute", "entry", "go"}:
        return ActionState.EXECUTE
    if value in {"caution", "watch", "wait"}:
        return ActionState.WAIT
    if value in {"kill", "reject", "drop", "block", "stand_down"}:
        return ActionState.STAND_DOWN
    return ActionState.HUNT


def _legacy_direction_from_side(side: Optional[str]) -> Direction:
    value = (side or "").lower().strip()
    if value in {"buy", "long", "bull", "bullish"}:
        return Direction.BUY
    if value in {"sell", "short", "bear", "bearish"}:
        return Direction.SELL
    return Direction.NONE


def make_oracle_action(
    pair: str,
    action: str,
    timestamp: Optional[str] = None,
    setup_family: Optional[str] = None,
    side: Optional[str] = None,
    confidence: Optional[float] = None,
    score: Optional[float] = None,
    why_now: Optional[str] = None,
    entry_idea: Optional[str] = None,
    stop_idea: Optional[str] = None,
    target_idea: Optional[str] = None,
    tags: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
    context_regime: Optional[str] = None,
    fg: Optional[int] = None,
    specialist: Optional[str] = None,
    intent: Optional[str] = None,
    grade: Optional[str] = None,
) -> OraclePanelRow:
    thesis_bits = [x for x in [why_now, context_regime, setup_family] if x]
    thesis = " | ".join(thesis_bits) if thesis_bits else "Oracle action"

    mapped_action = _legacy_action_from_candidate_action(action).value

    return OraclePanelRow(
        pair=pair,
        action=mapped_action,
        side=_legacy_direction_from_side(side).value,
        thesis=thesis,
        entry_idea=entry_idea,
        stop_idea=stop_idea,
        target_idea=target_idea,
        specialist=specialist,
        intent=intent,
        grade=grade,
        warnings=warnings or [],
        tags=(tags or []) + ([f"fg:{fg}"] if fg is not None else []),
        score=score,
        confidence=confidence,
        timestamp=timestamp or utc_now_iso(),
    )


def build_panel_payload(
    last_scan: str,
    next_scan: Optional[str],
    oracle_summary: OracleSummary,
    actions: Optional[List[OraclePanelRow]] = None,
    positions: Optional[List[Dict[str, Any]]] = None,
    health: Optional[OracleHealth] = None,
) -> OraclePanelPayload:
    return OraclePanelPayload(
        last_scan=last_scan,
        next_scan=next_scan,
        oracle_summary=oracle_summary,
        actions=actions or [],
        positions=positions or [],
        health=health or OracleHealth(),
    )


def payload_from_actions(
    last_scan: str,
    next_scan: Optional[str],
    oracle_summary: OracleSummary,
    actions: Optional[List[OraclePanelRow]] = None,
    positions: Optional[List[Dict[str, Any]]] = None,
    health: Optional[OracleHealth] = None,
) -> OraclePanelPayload:
    return build_panel_payload(
        last_scan=last_scan,
        next_scan=next_scan,
        oracle_summary=oracle_summary,
        actions=actions,
        positions=positions,
        health=health,
    )
