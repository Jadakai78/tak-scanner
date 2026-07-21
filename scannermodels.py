from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
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


JsonDict = Dict[str, Any]


@dataclass
class PairContext:
    pair: str
    market_regime: str
    timeframe: str = "1h"
    fear_greed: Optional[float] = None
    session: Optional[str] = None
    context: JsonDict = field(default_factory=dict)
    indicators: JsonDict = field(default_factory=dict)
    market_state: JsonDict = field(default_factory=dict)
    diagnostics: JsonDict = field(default_factory=dict)


@dataclass
class SpecialistObservation:
    specialist: str
    pair: str
    setup_type: str
    side: str
    confidence: float
    score: float
    thesis: str
    evidence: JsonDict = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    context: JsonDict = field(default_factory=dict)


@dataclass
class ReviewResult:
    decision: str = "hold"
    adjusted_score: float = 0.0
    confidence_delta: float = 0.0
    rationale: str = ""
    caution_flags: List[str] = field(default_factory=list)
    evidence_notes: List[str] = field(default_factory=list)


@dataclass
class CouncilDecision:
    decision: str = "reject"
    battlefield_ok: bool = False
    veto_reasons: List[str] = field(default_factory=list)
    route: str = "killed_signals"
    execution_ready: bool = False


@dataclass
class TrendContext:
    ribbon_state: Optional[str] = None
    ribbon_order: List[str] = field(default_factory=list)
    ribbon_slope: Optional[str] = None
    compression_state: Optional[str] = None
    expansion_state: Optional[str] = None
    reclaim_status: Optional[str] = None


@dataclass
class SupertrendContext:
    direction: Optional[str] = None
    line_distance: Optional[float] = None
    strength: Optional[float] = None
    phase: Optional[str] = None
    flip_risk: Optional[str] = None


@dataclass
class VolumeContext:
    relative_volume: Optional[float] = None
    participation_grade: Optional[str] = None
    spike_state: Optional[str] = None
    quiet_pullback: Optional[bool] = None
    delta_state: Optional[str] = None
    cvd_state: Optional[str] = None


@dataclass
class VolatilityContext:
    atr_level: Optional[float] = None
    atr_expansion: Optional[bool] = None
    compression_release: Optional[str] = None


@dataclass
class StructureContext:
    nearest_swing_high: Optional[float] = None
    nearest_swing_low: Optional[float] = None
    bos_level: Optional[float] = None
    choch_level: Optional[float] = None
    zone_ref: Optional[str] = None
    target_path: List[float] = field(default_factory=list)
    liquidity_map: List[str] = field(default_factory=list)


@dataclass
class CommonIndicatorContext:
    market_regime: Optional[str] = None
    timeframe: Optional[str] = None
    mtf_verdict: Optional[str] = None
    mtf_score: Optional[float] = None
    mtf_alignment: Optional[str] = None
    trend_context: TrendContext = field(default_factory=TrendContext)
    st_context: SupertrendContext = field(default_factory=SupertrendContext)
    volume_context: VolumeContext = field(default_factory=VolumeContext)
    volatility_context: VolatilityContext = field(default_factory=VolatilityContext)
    structure_context: StructureContext = field(default_factory=StructureContext)
    extra: JsonDict = field(default_factory=dict)


@dataclass
class ExecutionContext:
    entry_idea: Optional[float] = None
    stop_idea: Optional[float] = None
    target_idea: Optional[float] = None
    rr_estimate: Optional[float] = None
    offensive_score: Optional[float] = None
    defensive_score: Optional[float] = None
    trap_risk: Optional[float] = None
    survivability: Optional[float] = None
    liquidity_proximity: Optional[float] = None
    execution_intent: Optional[str] = None
    invalidation_basis: Optional[str] = None
    target_basis: Optional[str] = None
    cut_now: bool = False


@dataclass
class ClaimScore:
    bot: str
    score: float
    threshold: Optional[float] = None
    lead_threshold: Optional[float] = None
    outcome: Optional[str] = None


@dataclass
class ToolCheck:
    name: str
    required: bool = True
    available: bool = False
    note: str = ""


@dataclass
class ClaimContext:
    lead_bot: Optional[str] = None
    attached_bots: List[str] = field(default_factory=list)
    co_claims: List[str] = field(default_factory=list)
    claim_status: Optional[str] = None
    claim_scores: List[ClaimScore] = field(default_factory=list)
    tool_checks: List[ToolCheck] = field(default_factory=list)
    common_indicator_ok: Optional[bool] = None
    mission_fit: Optional[bool] = None
    survival_ok: Optional[bool] = None


@dataclass
class SignalDiagnostics:
    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    raw_context: JsonDict = field(default_factory=dict)
    raw_evidence: JsonDict = field(default_factory=dict)
    legacy_payload: JsonDict = field(default_factory=dict)


@dataclass
class CandidateSignal:
    pair: str
    candidate_id: str
    setup_type: str
    side: str
    specialist: str
    thesis: str
    score: float

    confidence: float = 0.0
    final_status: str = "candidate"

    entry_idea: Optional[float] = None
    stop_idea: Optional[float] = None
    target_idea: Optional[float] = None

    context: JsonDict = field(default_factory=dict)
    evidence: JsonDict = field(default_factory=dict)

    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    review: Optional[ReviewResult] = None
    council: Optional[CouncilDecision] = None


@dataclass
class PublishedSignal:
    bucket: str
    pair: str
    candidate_id: str
    setup_type: str
    side: str
    score: float
    specialist: str
    thesis: str
    route: str
    execution_ready: bool = False

    confidence: Optional[float] = None
    final_status: str = "published"

    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    payload: JsonDict = field(default_factory=dict)

    review: Optional[ReviewResult] = None
    council: Optional[CouncilDecision] = None
    indicators: CommonIndicatorContext = field(default_factory=CommonIndicatorContext)
    execution: ExecutionContext = field(default_factory=ExecutionContext)
    claims: ClaimContext = field(default_factory=ClaimContext)
    diagnostics: SignalDiagnostics = field(default_factory=SignalDiagnostics)


@dataclass
class ScanResult:
    live_signals: List[PublishedSignal] = field(default_factory=list)
    caution_signals: List[PublishedSignal] = field(default_factory=list)
    killed_signals: List[PublishedSignal] = field(default_factory=list)
    positions: List[JsonDict] = field(default_factory=list)
    audit: JsonDict = field(default_factory=dict)
