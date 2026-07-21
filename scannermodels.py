from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

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

    def to_dict(self) -> Dict[str, object]:
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


# Drop this into scannermodels.py near PairContext / other shared runtime models.
# Intended flow:
# Oracle -> PairContext -> specialist returns BooleanIshSpecialistResult
# -> orchestrator builds candidate -> scanner converts candidate into OracleAction
