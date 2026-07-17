from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from scannermodels import PairContext, SpecialistObservation

logger = logging.getLogger("takscannerv4")


class SpecialistAdapter:
    def __init__(self, engine_id: str, engine: Any) -> None:
        self.engine_id = engine_id
        self.engine = engine

    def evaluate(self, context: PairContext, fear_greed_score: int = 50) -> List[SpecialistObservation]:
        pair = context.pair
        regime = context.market_regime
        raw_df = context.metadata.get("df")
        fg = int(fear_greed_score)

        logger.info("V4 adapter calling engine%s pair%s regime%s fg%s", self.engine_id, pair, regime, fg)

        try:
            raw = self.engine.generate(pair, raw_df, regime, fg)
        except Exception:
            raw = None

        logger.info("V4 TAK pair%s engine%s rawnone%s", pair, self.engine_id, raw is None)

        if not raw:
            return []

        observation = self._normalize_observation(context, raw)
        if observation is None:
            return []

        logger.info(
            "V4 OBS pair%s engine%s bias%s rr%s struct%s vol%s conf%.3f score%.2f",
            pair,
            self.engine_id,
            observation.side,
            observation.evidence.get("rr", "na"),
            observation.evidence.get("structure_quality", "na"),
            observation.evidence.get("volume_ratio", "na"),
            observation.confidence,
            observation.score,
        )
        return [observation]

    def _normalize_observation(
        self,
        context: PairContext,
        raw: Dict[str, Any],
    ) -> Optional[SpecialistObservation]:
        pair = context.pair

        side = str(raw.get("bias") or raw.get("side") or "NEUTRAL").upper()
        if side not in {"LONG", "SHORT", "BUY", "SELL"}:
            return None
        if side == "BUY":
            side = "LONG"
        if side == "SELL":
            side = "SHORT"

        rr = _safe_float(raw.get("rr"), 0.0)
        structure_quality = _safe_float(raw.get("structurequality", raw.get("structure_quality")), 0.5)
        volume_ratio = _safe_float(raw.get("volumeratio", raw.get("volume_ratio")), 1.0)
        confidence = _derive_confidence(raw, rr, structure_quality, volume_ratio)
        score = _derive_score(raw, confidence, rr, structure_quality, volume_ratio)

        entry = _safe_optional_float(raw.get("entry"))
        stop = _safe_optional_float(raw.get("sl"))
        target = _safe_optional_float(raw.get("tp"))

        thesis = str(
            raw.get("thesis")
            or f"{self.engine_id} sees {side} opportunity on {pair} in {context.market_regime} with RR {rr:.2f}."
        )

        evidence = {
            "rr": rr,
            "entry_idea": entry,
            "stop_idea": stop,
            "target_idea": target,
            "structure_quality": structure_quality,
            "volume_ratio": volume_ratio,
            "engine": self.engine_id,
            "raw": raw,
        }

        warnings: List[str] = []
        if rr < 1.5:
            warnings.append("low_rr")
        if structure_quality < 0.45:
            warnings.append("weak_structure")
        if volume_ratio < 0.8:
            warnings.append("weak_volume")

        tags: List[str] = [self.engine_id.lower(), str(context.market_regime).lower()]
        if "break" in str(raw.get("engine", self.engine_id)).lower():
            tags.append("breakout")
        if str(context.market_regime).upper() in {"TRENDDOWN", "TRENDUP"}:
            tags.append("trend")
        if raw.get("countertrend"):
            tags.append("countertrend")

        return SpecialistObservation(
            specialist=self.engine_id,
            pair=pair,
            setup_type=str(raw.get("setup_type") or raw.get("engine") or self.engine_id),
            side=side,
            confidence=confidence,
            score=score,
            thesis=thesis,
            evidence=evidence,
            warnings=warnings,
            tags=tags,
            context={
                "regime": context.market_regime,
                "timeframe": context.timeframe,
                "last_price": context.last_price,
            },
        )


class SpecialistRegistry:
    def __init__(self, engine_classes: Dict[str, Any], regime_engines: Optional[Dict[str, List[str]]] = None) -> None:
        self.engine_classes = dict(engine_classes)
        self.regime_engines = dict(regime_engines or {})

    def engines_for_regime(self, regime: str) -> List[SpecialistAdapter]:
        engine_ids = self.regime_engines.get(regime, [])
        adapters: List[SpecialistAdapter] = []

        for engine_id in engine_ids:
            cls = self.engine_classes.get(engine_id)
            if cls is None:
                continue
            try:
                engine = cls()
            except Exception:
                continue
            adapters.append(SpecialistAdapter(engine_id=engine_id, engine=engine))
        return adapters


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_optional_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _derive_confidence(raw: Dict[str, Any], rr: float, structure_quality: float, volume_ratio: float) -> float:
    if raw.get("confidence") is not None:
        return _clamp(float(raw["confidence"]), 0.0, 1.0)

    value = 0.45
    value += min(rr / 10.0, 0.20)
    value += structure_quality * 0.20
    value += min(volume_ratio, 1.5) * 0.10
    return _clamp(value, 0.0, 1.0)


def _derive_score(
    raw: Dict[str, Any],
    confidence: float,
    rr: float,
    structure_quality: float,
    volume_ratio: float,
) -> float:
    if raw.get("score") is not None:
        return float(raw["score"])

    score = 40.0
    score += confidence * 35.0
    score += min(rr, 5.0) * 4.0
    score += structure_quality * 12.0
    score += min(volume_ratio, 1.5) * 5.0
    return round(_clamp(score, 0.0, 100.0), 2)
