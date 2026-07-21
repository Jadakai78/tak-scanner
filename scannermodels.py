from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional


@dataclass
class PairContext:
    pair: str
    market_regime: str
    timeframe: str = "1h"
    fear_greed: Optional[int] = None
    session: Optional[str] = None
    indicators: Dict[str, Any] = field(default_factory=dict)
    market_state: Dict[str, Any] = field(default_factory=dict)
    shared_state: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pair": self.pair,
            "market_regime": self.market_regime,
            "timeframe": self.timeframe,
            "fear_greed": self.fear_greed,
            "session": self.session,
            "indicators": dict(self.indicators),
            "market_state": dict(self.market_state),
            "shared_state": dict(self.shared_state),
        }


SpecialistActionBias = Literal["signal", "caution", "kill", "flat"]


@dataclass
class BooleanIshSpecialistResult:
    specialist_name: str
    mission_role: str
    pair: str
    claim: bool
    action_bias: SpecialistActionBias
    setup_family: Optional[str] = None
    side: Optional[str] = None
    why_now: str = ""
    boolean_flags: Dict[str, bool] = field(default_factory=dict)
    offense_score: float = 0.0
    defense_score: float = 0.0
    trap_score: float = 0.0
    warnings: List[str] = field(default_factory=list)
    kill_reasons: List[str] = field(default_factory=list)
    diagnostics: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "specialist_name": self.specialist_name,
            "mission_role": self.mission_role,
            "pair": self.pair,
            "claim": bool(self.claim),
            "action_bias": self.action_bias,
            "setup_family": self.setup_family,
            "side": self.side,
            "why_now": self.why_now,
            "boolean_flags": dict(self.boolean_flags),
            "offense_score": float(self.offense_score or 0.0),
            "defense_score": float(self.defense_score or 0.0),
            "trap_score": float(self.trap_score or 0.0),
            "warnings": list(self.warnings),
            "kill_reasons": list(self.kill_reasons),
            "diagnostics": dict(self.diagnostics),
        }
