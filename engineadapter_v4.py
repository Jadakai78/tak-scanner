"""engineadapter_v4.py — Bridges legacy generate(pair, ohlc_df, regime, fg_score)
specialists into the ScannerOrchestrator's observe(context, shared_state) contract.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from scannermodels import PairContext, SpecialistObservation

logger = logging.getLogger("engineadapter_v4")


class EngineSpecialistAdapter:
    """Wraps a legacy engine (with .generate()) so the orchestrator can call
    .observe(context, shared_state) on it uniformly.

    Supported engine call patterns (tried in order):
      1. engine.generate(pair, ohlc_df, regime, fg_score, ai_st=...)
      2. engine.generate(pair=pair, ohlc_df=ohlc_df, regime=regime, fg_score=fg_score)
      3. engine.score_mtf(pair, bias, ohlc_4h, pair_key=...) — S8 overlay
    """

    def __init__(
        self,
        name: str,
        engine: Any,
        supported_regimes: list[str] | None = None,
    ) -> None:
        self.name = name
        self.engine = engine
        # Allow engine to declare its own regimes; fall back to param
        self.supported_regimes = (
            getattr(engine, "REQUIRED_REGIMES", None)
            or getattr(engine, "supported_regimes", None)
            or supported_regimes
            or ["*"]
        )

    def observe(
        self,
        context: PairContext,
        shared_state: Dict[str, Any],
    ) -> SpecialistObservation | None:
        pair = context.pair
        regime = context.market_regime or "UNKNOWN"
        fg_score = int(shared_state.get("fgscore") or 50)
        ai_st = shared_state.get("ai_st") or context.indicators.get("ai_st")
        ohlc_df = context.indicators.get("ohlc_df") or context.market_state.get("ohlc_df")

        # Check regime eligibility
        if self.supported_regimes and "*" not in self.supported_regimes and "ALL" not in self.supported_regimes:
            if regime.upper() not in {r.upper() for r in self.supported_regimes}:
                return None

        raw: Optional[Dict[str, Any]] = None
        try:
            raw = self.engine.generate(pair, ohlc_df, regime, fg_score, ai_st=ai_st)
        except TypeError:
            # Try keyword-only form
            try:
                raw = self.engine.generate(
                    pair=pair, ohlc_df=ohlc_df, regime=regime, fg_score=fg_score
                )
            except TypeError:
                logger.debug("Adapter %s: generate() signature mismatch for %s", self.name, pair)
                return None
        except Exception as exc:
            logger.warning("Adapter %s: generate() raised for %s: %s", self.name, pair, exc)
            return None

        if raw is None:
            return None

        return self._to_observation(raw, pair, regime)

    def _to_observation(
        self, raw: Dict[str, Any], pair: str, regime: str
    ) -> SpecialistObservation:
        confidence = float(raw.get("confidence", raw.get("score", 0.5)))
        if confidence > 1.0:
            confidence = confidence / 100.0
        confidence = max(0.0, min(1.0, confidence))

        score = float(raw.get("score", confidence * 100.0))
        score = max(0.0, min(100.0, score))

        evidence = dict(raw.get("evidence", {}))
        # Pull trade plan fields up from raw if not nested in evidence
        for field in ("entry_idea", "stop_idea", "target_idea", "rr", "entry", "sl", "tp"):
            if field not in evidence and field in raw:
                evidence[field] = raw[field]

        ctx = dict(raw.get("context", {}))
        ctx.setdefault("regime", regime)
        ctx.setdefault("timeframe", raw.get("timeframe", "4h"))

        return SpecialistObservation(
            specialist=self.name,
            pair=pair,
            setup_type=str(raw.get("setup_type", raw.get("engine", self.name))),
            side=str(raw.get("side", raw.get("bias", "NEUTRAL"))),
            confidence=confidence,
            score=score,
            thesis=str(raw.get("thesis", "")),
            evidence=evidence,
            warnings=list(raw.get("warnings", [])),
            tags=list(raw.get("tags", [])),
            context=ctx,
        )
