from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Literal, Optional

ActionState = Literal["actionable", "watch", "killed", "info"]
ScanMode = Literal["scheduled", "manual", "replay"]


@dataclass
class OracleHealth:
    writer_ok: bool = True
    reader_ok: bool = True
    publish_ok: bool = True
    api_ready: bool = True
    last_error: Optional[str] = None
    source_path: Optional[str] = None
    bus_path: Optional[str] = None
    last_good_path: Optional[str] = None
    heartbeat: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OracleSummary:
    pairs_scanned: int = 0
    opportunity_count: int = 0
    watchlist_count: int = 0
    killed_count: int = 0
    top_regime: Optional[str] = None
    market_phase: Optional[str] = None
    active_session: Optional[str] = None
    scan_mode: ScanMode = "scheduled"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OracleMarket:
    fear_greed: int = 50
    fear_greed_label: str = "Neutral"
    session: Optional[str] = None
    market_phase: Optional[str] = None
    regime_counts: Dict[str, int] = field(default_factory=dict)
    htf_bias_overview: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OraclePanel:
    default_sort: str = "panel_rank"
    default_view: str = "opportunities"
    default_filters: Dict[str, Any] = field(
        default_factory=lambda: {
            "min_confidence": 0.55,
            "hide_killed": False,
            "show_against_htf": True,
        }
    )
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OracleRowContext:
    timeframe: Optional[str] = None
    session: Optional[str] = None
    fear_greed: Optional[int] = None
    htf_bias: Optional[str] = None
    market_regime: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OraclePanelRow:
    pair: str
    panel_rank: int = 0
    action_state: ActionState = "info"
    side: str = "neutral"
    setup_family: Optional[str] = None
    specialist: Optional[str] = None
    regime: Optional[str] = None
    htf_bias: Optional[str] = None
    htf_alignment: Optional[bool] = None
    offense_score: float = 0.0
    defense_score: float = 0.0
    trap_score: float = 0.0
    confidence: float = 0.0
    score: float = 0.0
    why_now: str = ""
    entry_idea: Optional[str] = None
    stop_idea: Optional[str] = None
    target_idea: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    kill_reasons: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    oracle_context: OracleRowContext = field(default_factory=OracleRowContext)
    indicators: Dict[str, Any] = field(default_factory=dict)
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pair": self.pair,
            "panel_rank": self.panel_rank,
            "action_state": self.action_state,
            "side": self.side,
            "setup_family": self.setup_family,
            "specialist": self.specialist,
            "regime": self.regime,
            "htf_bias": self.htf_bias,
            "htf_alignment": self.htf_alignment,
            "offense_score": self.offense_score,
            "defense_score": self.defense_score,
            "trap_score": self.trap_score,
            "confidence": self.confidence,
            "score": self.score,
            "why_now": self.why_now,
            "entry_idea": self.entry_idea,
            "stop_idea": self.stop_idea,
            "target_idea": self.target_idea,
            "warnings": list(self.warnings),
            "kill_reasons": list(self.kill_reasons),
            "tags": list(self.tags),
            "oracle_context": self.oracle_context.to_dict(),
            "indicators": dict(self.indicators),
            "diagnostics": dict(self.diagnostics),
        }


@dataclass
class OraclePanelPayload:
    schema_version: str
    generated_at: str
    last_scan: str
    next_scan: str
    summary: OracleSummary = field(default_factory=OracleSummary)
    market: OracleMarket = field(default_factory=OracleMarket)
    panel: OraclePanel = field(default_factory=OraclePanel)
    opportunities: List[OraclePanelRow] = field(default_factory=list)
    watchlist: List[OraclePanelRow] = field(default_factory=list)
    killed: List[OraclePanelRow] = field(default_factory=list)
    health: OracleHealth = field(default_factory=OracleHealth)
    api_source: Optional[str] = None
    api_served_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "last_scan": self.last_scan,
            "next_scan": self.next_scan,
            "summary": self.summary.to_dict(),
            "market": self.market.to_dict(),
            "panel": self.panel.to_dict(),
            "opportunities": [r.to_dict() for r in self.opportunities],
            "watchlist": [r.to_dict() for r in self.watchlist],
            "killed": [r.to_dict() for r in self.killed],
            "health": self.health.to_dict(),
            "api_source": self.api_source,
            "api_served_at": self.api_served_at,
        }


def build_panel_payload(
    *,
    generated_at: str,
    last_scan: str,
    next_scan: str,
    summary: Optional[OracleSummary] = None,
    market: Optional[OracleMarket] = None,
    panel: Optional[OraclePanel] = None,
    opportunities: Optional[List[OraclePanelRow]] = None,
    watchlist: Optional[List[OraclePanelRow]] = None,
    killed: Optional[List[OraclePanelRow]] = None,
    health: Optional[OracleHealth] = None,
    api_source: Optional[str] = None,
    api_served_at: Optional[str] = None,
) -> OraclePanelPayload:
    opportunities = opportunities or []
    watchlist = watchlist or []
    killed = killed or []

    summary = summary or OracleSummary(
        pairs_scanned=0,
        opportunity_count=len(opportunities),
        watchlist_count=len(watchlist),
        killed_count=len(killed),
    )

    if summary.opportunity_count != len(opportunities):
        summary.opportunity_count = len(opportunities)
    if summary.watchlist_count != len(watchlist):
        summary.watchlist_count = len(watchlist)
    if summary.killed_count != len(killed):
        summary.killed_count = len(killed)

    return OraclePanelPayload(
        schema_version="oracle-panel-v1",
        generated_at=generated_at,
        last_scan=last_scan,
        next_scan=next_scan,
        summary=summary,
        market=market or OracleMarket(),
        panel=panel or OraclePanel(),
        opportunities=opportunities,
        watchlist=watchlist,
        killed=killed,
        health=health or OracleHealth(),
        api_source=api_source,
        api_served_at=api_served_at,
    )
