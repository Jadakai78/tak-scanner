from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field

OracleActionType = Literal["signal", "caution", "kill", "flat"]
RiskState = Literal["normal", "elevated", "danger", "invalid", "complete"]
PositionState = Literal["none", "watch", "open", "reducing", "closed"]


class OracleHealthModel(BaseModel):
    scheduler_ok: bool = True
    bus_ok: bool = True
    publish_ok: bool = True
    last_error: Optional[str] = None
    source_path: Optional[str] = None
    heartbeat: Optional[str] = None


class OracleSummaryModel(BaseModel):
    fg: int
    fg_label: str
    market_phase: str
    session: str
    regime_summary: str
    active_pairs: int = 0
    dead_pairs: int = 0
    scan_mode: str = "scheduled"


class OracleActionModel(BaseModel):
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
    tags: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    context: Dict[str, Any] = Field(default_factory=dict)


class OraclePayloadModel(BaseModel):
    last_scan: str
    next_scan: str
    oracle: OracleSummaryModel
    actions: List[OracleActionModel] = Field(default_factory=list)
    positions: List[Dict[str, Any]] = Field(default_factory=list)
    health: OracleHealthModel = Field(default_factory=OracleHealthModel)
    signals: List[Dict[str, Any]] = Field(default_factory=list)
    killedsignals: List[Dict[str, Any]] = Field(default_factory=list)
