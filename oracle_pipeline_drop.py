from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence
from datetime import datetime, timezone
import uuid


class Regime(str, Enum):
    RANGE = "RANGE"
    FEAR = "FEAR"
    GREED = "GREED"
    TRENDUP = "TRENDUP"
    TRENDDOWN = "TRENDDOWN"
    CHOP = "CHOP"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"


class Route(str, Enum):
    LIVE = "live_signals"
    CAUTION = "caution_signals"
    KILLED = "killed_signals"


@dataclass
class PairContext:
    symbol: str
    base: str = ""
    quote: str = ""
    timeframe: str = "15m"
    exchange: str = ""
    last_price: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketOracleState:
    fear_greed: Optional[float] = None
    bias: str = "neutral"
    volatility_state: str = "normal"
    liquidity_state: str = "normal"
    notes: List[str] = field(default_factory=list)
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class SpecialistObservation:
    specialist_id: str
    pair: str
    direction: str
    thesis: str
    entry: Optional[float] = None
    stop: Optional[float] = None
    targets: List[float] = field(default_factory=list)
    confidence: float = 0.5
    regime_fit: float = 0.5
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CandidateSignal:
    candidate_id: str
    pair: str
    regime: Regime
    specialist_id: str
    direction: str
    thesis: str
    entry: Optional[float]
    stop: Optional[float]
    targets: List[float]
    base_confidence: float
    adjusted_confidence: float
    review_score: float
    council_score: float
    trap_risk: float
    route: Route
    status: str
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    oracle_bias: str = "neutral"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ReviewResult:
    approved: bool
    score_adjustment: float
    adjusted_confidence: float
    review_score: float
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TrapDecision:
    triggered: bool
    trap_risk: float
    route_override: Optional[Route] = None
    reasons: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CouncilDecision:
    route: Route
    status: str
    council_score: float
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SignalSnapshot:
    snapshot_id: str
    generated_at: str
    oracle_state: Dict[str, Any]
    totals: Dict[str, int]
    live_signals: List[Dict[str, Any]]
    caution_signals: List[Dict[str, Any]]
    killed_signals: List[Dict[str, Any]]
    debug: Dict[str, Any] = field(default_factory=dict)


class Oracle:
    def get_state(self) -> MarketOracleState:
        return MarketOracleState(
            fear_greed=42.0,
            bias="defensive",
            volatility_state="elevated",
            liquidity_state="stable",
            notes=["Fear/Greed context loaded", "Bias can influence confidence adjustments"],
        )


class PairUniverse:
    def get_active_pairs(self) -> List[PairContext]:
        pairs = [
            PairContext(symbol="BTCUSDT", base="BTC", quote="USDT", timeframe="15m", last_price=118250.0),
            PairContext(symbol="ETHUSDT", base="ETH", quote="USDT", timeframe="15m", last_price=6425.0),
            PairContext(symbol="SOLUSDT", base="SOL", quote="USDT", timeframe="15m", last_price=214.0),
        ]
        return pairs


class RegimeClassifier:
    def classify_regime(self, pair: PairContext, oracle_state: MarketOracleState) -> Regime:
        if oracle_state.bias == "defensive" and pair.base in {"BTC", "ETH"}:
            return Regime.FEAR
        if pair.base == "SOL":
            return Regime.VOLATILE
        return Regime.UNKNOWN


class Specialist:
    specialist_id = "BASE"

    def observe(self, pair: PairContext, regime: Regime, oracle_state: MarketOracleState) -> Optional[SpecialistObservation]:
        raise NotImplementedError


class FearFadeSpecialist(Specialist):
    specialist_id = "S6"

    def observe(self, pair: PairContext, regime: Regime, oracle_state: MarketOracleState) -> Optional[SpecialistObservation]:
        if regime not in {Regime.FEAR, Regime.RANGE}:
            return None
        return SpecialistObservation(
            specialist_id=self.specialist_id,
            pair=pair.symbol,
            direction="LONG",
            thesis="Fear regime mean-reversion setup with controlled risk.",
            entry=pair.last_price,
            stop=(pair.last_price or 0) * 0.985 if pair.last_price else None,
            targets=[(pair.last_price or 0) * 1.01, (pair.last_price or 0) * 1.02] if pair.last_price else [],
            confidence=0.59,
            regime_fit=0.81,
            reasons=["Panic conditions favor reversion specialist", "Oracle bias is defensive but not capitulation"],
            warnings=["Lower conviction if liquidity thins"],
        )


class TrendBreakdownSpecialist(Specialist):
    specialist_id = "S7"

    def observe(self, pair: PairContext, regime: Regime, oracle_state: MarketOracleState) -> Optional[SpecialistObservation]:
        if regime not in {Regime.TRENDDOWN, Regime.FEAR, Regime.VOLATILE}:
            return None
        return SpecialistObservation(
            specialist_id=self.specialist_id,
            pair=pair.symbol,
            direction="SHORT",
            thesis="Breakdown continuation if weakness persists through support.",
            entry=pair.last_price,
            stop=(pair.last_price or 0) * 1.012 if pair.last_price else None,
            targets=[(pair.last_price or 0) * 0.99, (pair.last_price or 0) * 0.975] if pair.last_price else [],
            confidence=0.56,
            regime_fit=0.73,
            reasons=["Weak tape can continue in fear/volatile conditions"],
            warnings=["High trap exposure around support sweeps"],
        )


class VolatilityCompressionSpecialist(Specialist):
    specialist_id = "S9"

    def observe(self, pair: PairContext, regime: Regime, oracle_state: MarketOracleState) -> Optional[SpecialistObservation]:
        if regime not in {Regime.RANGE, Regime.VOLATILE, Regime.CHOP}:
            return None
        return SpecialistObservation(
            specialist_id=self.specialist_id,
            pair=pair.symbol,
            direction="NEUTRAL",
            thesis="Compression watchlist candidate awaiting expansion confirmation.",
            entry=pair.last_price,
            stop=None,
            targets=[],
            confidence=0.48,
            regime_fit=0.76,
            reasons=["Useful when volatility clusters before breakout"],
            warnings=["Needs confirmation before promotion to live"],
        )


class SpecialistRegistry:
    def __init__(self) -> None:
        self._specialists: Dict[str, Specialist] = {
            "S6": FearFadeSpecialist(),
            "S7": TrendBreakdownSpecialist(),
            "S9": VolatilityCompressionSpecialist(),
        }
        self._regime_map: Dict[Regime, List[str]] = {
            Regime.FEAR: ["S6", "S7"],
            Regime.RANGE: ["S6", "S9"],
            Regime.TRENDDOWN: ["S7"],
            Regime.VOLATILE: ["S7", "S9"],
            Regime.CHOP: ["S9"],
        }

    def resolve_for_regime(self, regime: Regime) -> List[Specialist]:
        specialist_ids = self._regime_map.get(regime, [])
        return [self._specialists[sid] for sid in specialist_ids if sid in self._specialists]


class REMI:
    def review(
        self,
        pair: PairContext,
        regime: Regime,
        observation: SpecialistObservation,
        oracle_state: MarketOracleState,
    ) -> ReviewResult:
        confidence = observation.confidence
        score_adjustment = 0.0
        reasons: List[str] = []
        warnings = list(observation.warnings)

        if oracle_state.bias == "defensive" and observation.direction == "LONG":
            confidence -= 0.05
            score_adjustment -= 0.04
            reasons.append("Defensive oracle bias reduced long confidence")

        if observation.regime_fit >= 0.8:
            confidence += 0.06
            score_adjustment += 0.08
            reasons.append("Strong regime fit increased trust")

        if observation.direction == "NEUTRAL":
            confidence -= 0.03
            score_adjustment -= 0.02
            warnings.append("Neutral setup should not auto-promote to live")

        adjusted_confidence = max(0.0, min(1.0, confidence))
        review_score = max(0.0, min(1.0, adjusted_confidence + 0.15 + score_adjustment))
        approved = review_score >= 0.4

        return ReviewResult(
            approved=approved,
            score_adjustment=score_adjustment,
            adjusted_confidence=adjusted_confidence,
            review_score=review_score,
            reasons=reasons,
            warnings=warnings,
            metadata={"oracle_bias": oracle_state.bias, "pair": pair.symbol, "regime": regime.value},
        )


class RTS:
    def detect_trap(
        self,
        pair: PairContext,
        regime: Regime,
        observation: SpecialistObservation,
        review: ReviewResult,
    ) -> TrapDecision:
        trap_risk = 0.18
        reasons: List[str] = []

        if regime in {Regime.FEAR, Regime.VOLATILE} and observation.direction == "SHORT":
            trap_risk += 0.37
            reasons.append("Short in fear/volatile regime risks squeeze trap")

        if "support sweeps" in " ".join(observation.warnings).lower():
            trap_risk += 0.22
            reasons.append("Specialist flagged support-sweep trap risk")

        if review.adjusted_confidence < 0.5:
            trap_risk += 0.1
            reasons.append("Low confidence increases ambush probability")

        trap_risk = max(0.0, min(1.0, trap_risk))
        triggered = trap_risk >= 0.65

        return TrapDecision(
            triggered=triggered,
            trap_risk=trap_risk,
            route_override=Route.KILLED if triggered else None,
            reasons=reasons,
            metadata={"pair": pair.symbol, "regime": regime.value},
        )


class APRIL:
    def adjudicate(
        self,
        pair: PairContext,
        regime: Regime,
        observation: SpecialistObservation,
        review: ReviewResult,
        trap: TrapDecision,
    ) -> CouncilDecision:
        reasons = list(review.reasons)
        warnings = list(review.warnings)
        tags: List[str] = [regime.value.lower(), observation.specialist_id.lower()]

        if trap.triggered:
            reasons.extend(trap.reasons)
            tags.append("trap_kill")
            return CouncilDecision(
                route=Route.KILLED,
                status="killed_by_rts",
                council_score=0.0,
                reasons=reasons,
                warnings=warnings,
                tags=tags,
                metadata={"pair": pair.symbol, "route_source": "RTS"},
            )

        if not review.approved:
            warnings.append("Rejected by REMI threshold")
            return CouncilDecision(
                route=Route.KILLED,
                status="killed_by_review",
                council_score=review.review_score,
                reasons=reasons,
                warnings=warnings,
                tags=tags,
                metadata={"pair": pair.symbol, "route_source": "REMI"},
            )

        if observation.direction == "NEUTRAL" or review.adjusted_confidence < 0.58:
            tags.append("needs_confirmation")
            return CouncilDecision(
                route=Route.CAUTION,
                status="caution",
                council_score=review.review_score,
                reasons=reasons,
                warnings=warnings,
                tags=tags,
                metadata={"pair": pair.symbol, "route_source": "APRIL"},
            )

        return CouncilDecision(
            route=Route.LIVE,
            status="live",
            council_score=review.review_score,
            reasons=reasons,
            warnings=warnings,
            tags=tags,
            metadata={"pair": pair.symbol, "route_source": "APRIL"},
        )


class SignalRouter:
    def __init__(self) -> None:
        self.live_signals: List[CandidateSignal] = []
        self.caution_signals: List[CandidateSignal] = []
        self.killed_signals: List[CandidateSignal] = []

    def route(self, signal: CandidateSignal) -> None:
        if signal.route == Route.LIVE:
            self.live_signals.append(signal)
        elif signal.route == Route.CAUTION:
            self.caution_signals.append(signal)
        else:
            self.killed_signals.append(signal)


class ScannerEngine:
    def __init__(self) -> None:
        self.oracle = Oracle()
        self.universe = PairUniverse()
        self.regime_classifier = RegimeClassifier()
        self.registry = SpecialistRegistry()
        self.remi = REMI()
        self.april = APRIL()
        self.rts = RTS()
        self.router = SignalRouter()
        self.december_enabled = False

    def build_candidate(
        self,
        pair: PairContext,
        regime: Regime,
        observation: SpecialistObservation,
        review: ReviewResult,
        council: CouncilDecision,
        trap: TrapDecision,
        oracle_state: MarketOracleState,
    ) -> CandidateSignal:
        return CandidateSignal(
            candidate_id=str(uuid.uuid4()),
            pair=pair.symbol,
            regime=regime,
            specialist_id=observation.specialist_id,
            direction=observation.direction,
            thesis=observation.thesis,
            entry=observation.entry,
            stop=observation.stop,
            targets=observation.targets,
            base_confidence=observation.confidence,
            adjusted_confidence=review.adjusted_confidence,
            review_score=review.review_score,
            council_score=council.council_score,
            trap_risk=trap.trap_risk,
            route=council.route,
            status=council.status,
            reasons=observation.reasons + review.reasons + trap.reasons + council.reasons,
            warnings=observation.warnings + review.warnings + council.warnings,
            tags=council.tags,
            oracle_bias=oracle_state.bias,
            metadata={
                "pair": pair.symbol,
                "timeframe": pair.timeframe,
                "exchange": pair.exchange,
                "route_source": council.metadata.get("route_source", "APRIL"),
                "december_enabled": self.december_enabled,
            },
        )

    def run_scan(self) -> SignalSnapshot:
        oracle_state = self.oracle.get_state()
        active_pairs = self.universe.get_active_pairs()
        debug_rows: List[Dict[str, Any]] = []

        for pair in active_pairs:
            regime = self.regime_classifier.classify_regime(pair, oracle_state)
            specialists = self.registry.resolve_for_regime(regime)

            for specialist in specialists:
                observation = specialist.observe(pair, regime, oracle_state)
                if not observation:
                    continue

                review = self.remi.review(pair, regime, observation, oracle_state)
                trap = self.rts.detect_trap(pair, regime, observation, review)
                council = self.april.adjudicate(pair, regime, observation, review, trap)
                candidate = self.build_candidate(pair, regime, observation, review, council, trap, oracle_state)
                self.router.route(candidate)

                debug_rows.append(
                    {
                        "pair": pair.symbol,
                        "regime": regime.value,
                        "specialist": specialist.specialist_id,
                        "direction": observation.direction,
                        "review_score": review.review_score,
                        "trap_risk": trap.trap_risk,
                        "route": council.route.value,
                        "status": council.status,
                    }
                )

        return SignalSnapshot(
            snapshot_id=str(uuid.uuid4()),
            generated_at=datetime.now(timezone.utc).isoformat(),
            oracle_state=oracle_state.__dict__,
            totals={
                "pairs_scanned": len(active_pairs),
                "live": len(self.router.live_signals),
                "caution": len(self.router.caution_signals),
                "killed": len(self.router.killed_signals),
            },
            live_signals=[signal.__dict__ | {"regime": signal.regime.value, "route": signal.route.value} for signal in self.router.live_signals],
            caution_signals=[signal.__dict__ | {"regime": signal.regime.value, "route": signal.route.value} for signal in self.router.caution_signals],
            killed_signals=[signal.__dict__ | {"regime": signal.regime.value, "route": signal.route.value} for signal in self.router.killed_signals],
            debug={
                "december_enabled": self.december_enabled,
                "rts_trap_authority": True,
                "rows": debug_rows,
            },
        )


if __name__ == "__main__":
    engine = ScannerEngine()
    snapshot = engine.run_scan()
    from pprint import pprint

    pprint(snapshot)
