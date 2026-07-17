from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


SignalSide = str
SignalStatus = str
ReviewDecision = str
CouncilDecision = str


@dataclass
class PairContext:
    pair: str
    timeframe: str = "1h"
    last_price: Optional[float] = None
    market_regime: str = "unknown"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SpecialistObservation:
    specialist: str
    pair: str
    setup_type: str
    side: SignalSide
    confidence: float
    score: float
    thesis: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateSignal:
    candidate_id: str
    pair: str
    setup_type: str
    side: SignalSide
    specialist: str
    confidence: float
    score: float
    thesis: str
    entry_idea: Optional[float] = None
    stop_idea: Optional[float] = None
    target_idea: Optional[float] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    review: Optional["RemiReview"] = None
    council: Optional["CouncilResult"] = None
    final_status: SignalStatus = "pending"


@dataclass
class RemiReview:
    decision: ReviewDecision
    adjusted_score: float
    confidence_delta: float
    rationale: str
    caution_flags: List[str] = field(default_factory=list)
    evidence_notes: List[str] = field(default_factory=list)


@dataclass
class CouncilResult:
    decision: CouncilDecision
    battlefield_ok: bool
    veto_reasons: List[str] = field(default_factory=list)
    route: str = "unassigned"
    execution_ready: bool = False


@dataclass
class PublishedSignal:
    bucket: str
    pair: str
    candidate_id: str
    setup_type: str
    side: SignalSide
    score: float
    specialist: str
    thesis: str
    route: str
    execution_ready: bool
    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ScanResult:
    live_signals: List[PublishedSignal] = field(default_factory=list)
    caution_signals: List[PublishedSignal] = field(default_factory=list)
    killed_signals: List[PublishedSignal] = field(default_factory=list)
    positions: List[Dict[str, Any]] = field(default_factory=list)
    audit: Dict[str, Any] = field(default_factory=dict)
