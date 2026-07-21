from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

OracleActionType = Literal["signal", "caution", "kill", "flat"]
RiskState = Literal["normal", "elevated", "danger", "invalid", "complete"]
PositionState = Literal["none", "watch", "open", "reducing", "closed"]


@dataclass
class OracleHealth:
    scheduler_ok: bool = True
    bus_ok: bool = True
    publish_ok: bool = True
    last_error: Optional[str] = None
    source_path: Optional[str] = None
    heartbeat: Optional[str] = None


@dataclass
class OracleSummary:
    fg: int
    fg_label: str
    market_phase: str
    session: str
    regime_summary: str
    active_pairs: int = 0
    dead_pairs: int = 0
    scan_mode: str = "scheduled"


@dataclass
class OracleContext:
    pair: str
    timestamp: str
    regime: str
    session: str
    fg: int
    fg_label: str
    trend_bias: Optional[str] = None
    liquidity_state: Optional[str] = None
    volatility_state: Optional[str] = None
    htf_structure: Optional[str] = None
    setup_family_candidates: List[str] = field(default_factory=list)
    indicators: Dict[str, Any] = field(default_factory=dict)
    market_notes: List[str] = field(default_factory=list)


@dataclass
class OracleAction:
    pair: str
    action: OracleActionType
    timestamp: str
    setup_family: Optional[str] = None
    side: Optional[str] = None
    confidence: float = 0.0
    score: float = 0.0
    risk_state: RiskState = "normal"
    position_state: PositionState = "none"
    why_now: str = ""
    invalidation_reason: Optional[str] = None
    entry_idea: Optional[str] = None
    stop_idea: Optional[str] = None
    target_idea: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OraclePosition:
    pair: str
    side: str
    position_state: PositionState
    risk_state: RiskState
    entry_reference: Optional[str] = None
    current_thesis: Optional[str] = None
    caution_flags: List[str] = field(default_factory=list)
    kill_flags: List[str] = field(default_factory=list)
    last_oracle_action: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OraclePayload:
    last_scan: str
    next_scan: str
    oracle: OracleSummary
    actions: List[OracleAction] = field(default_factory=list)
    positions: List[OraclePosition] = field(default_factory=list)
    health: OracleHealth = field(default_factory=OracleHealth)

    # Temporary compatibility layer for legacy feed/UI consumers.
    signals: List[Dict[str, Any]] = field(default_factory=list)
    killedsignals: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_scan": self.last_scan,
            "next_scan": self.next_scan,
            "oracle": asdict(self.oracle),
            "actions": [a.to_dict() for a in self.actions],
            "positions": [p.to_dict() for p in self.positions],
            "health": asdict(self.health),
            "signals": self.signals,
            "killedsignals": self.killedsignals,
        }


def make_oracle_action(
    *,
    pair: str,
    action: OracleActionType,
    timestamp: str,
    score: float = 0.0,
    confidence: float = 0.0,
    why_now: str = "",
    setup_family: Optional[str] = None,
    side: Optional[str] = None,
    risk_state: RiskState = "normal",
    position_state: PositionState = "none",
    invalidation_reason: Optional[str] = None,
    entry_idea: Optional[str] = None,
    stop_idea: Optional[str] = None,
    target_idea: Optional[str] = None,
    tags: Optional[List[str]] = None,
    warnings: Optional[List[str]] = None,
    context: Optional[Dict[str, Any]] = None,
) -> OracleAction:
    return OracleAction(
        pair=pair,
        action=action,
        timestamp=timestamp,
        setup_family=setup_family,
        side=side,
        confidence=float(confidence or 0.0),
        score=float(score or 0.0),
        risk_state=risk_state,
        position_state=position_state,
        why_now=why_now or "",
        invalidation_reason=invalidation_reason,
        entry_idea=entry_idea,
        stop_idea=stop_idea,
        target_idea=target_idea,
        tags=list(tags or []),
        warnings=list(warnings or []),
        context=dict(context or {}),
    )


def payload_from_actions(
    *,
    last_scan: str,
    next_scan: str,
    oracle: OracleSummary,
    actions: List[OracleAction],
    positions: Optional[List[OraclePosition]] = None,
    health: Optional[OracleHealth] = None,
) -> OraclePayload:
    positions = positions or []
    health = health or OracleHealth()

    signals: List[Dict[str, Any]] = []
    killedsignals: List[Dict[str, Any]] = []

    for action in actions:
        row = action.to_dict()
        if action.action == "signal":
            signals.append(row)
        elif action.action == "kill":
            killedsignals.append(row)

    return OraclePayload(
        last_scan=last_scan,
        next_scan=next_scan,
        oracle=oracle,
        actions=actions,
        positions=positions,
        health=health,
        signals=signals,
        killedsignals=killedsignals,
    )
