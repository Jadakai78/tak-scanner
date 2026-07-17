from __future__ import annotations

from typing import Any, Dict, Optional

from scannermodels import PairContext, SpecialistObservation


class EngineSpecialistAdapter:
    def __init__(self, name: str, engine: Any, supported_regimes: list[str] | None = None) -> None:
        self.name = name
        self.engine = engine
        self.supported_regimes = supported_regimes or ["ALL"]

    def observe(self, context: PairContext, shared_state: Dict[str, Any]) -> SpecialistObservation | None:
        pair = context.pair
        regime = context.market_regime
        fgscore = int(shared_state.get("fgscore", 50))
        df = context.metadata.get("df")
        aist = context.metadata.get("aist", {})

        raw = self._generate(pair=pair, df=df, regime=regime, fgscore=fgscore, aist=aist)
        if not raw:
            return None

        side = str(raw.get("side", raw.get("bias", "NEUTRAL")))
        confidence = float(raw.get("confidence", raw.get("conviction", raw.get("score", 0.0))))
        score = float(raw.get("score", confidence * 100.0))
        thesis = str(raw.get("thesis", raw.get("summary", f"{self.name} setup on {pair}")))

        evidence = {
            "entry_idea": raw.get("entry"),
            "stop_idea": raw.get("sl"),
            "target_idea": raw.get("tp"),
            "rr": raw.get("rr"),
            "raw": dict(raw),
        }

        tags = list(raw.get("tags", []))
        if raw.get("news_risk"):
            tags.append("news_risk")
        if raw.get("illiquid"):
            tags.append("illiquid")
        if raw.get("countertrend"):
            tags.append("countertrend")
        if raw.get("breakout"):
            tags.append("breakout")

        return SpecialistObservation(
            specialist=self.name,
            pair=pair,
            setup_type=str(raw.get("setup_type", raw.get("engine", self.name))),
            side=side,
            confidence=max(0.0, min(1.0, confidence if confidence <= 1.0 else confidence / 100.0)),
            score=score,
            thesis=thesis,
            evidence=evidence,
            warnings=list(raw.get("warnings", [])),
            tags=tags,
            context={
                "regime": regime,
                "timeframe": context.timeframe,
                "last_price": context.last_price,
            },
        )

    def _generate(
        self,
        pair: str,
        df: Any,
        regime: str,
        fgscore: int,
        aist: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        if hasattr(self.engine, "generate"):
            return self.engine.generate(pair, df, regime, fgscore, aist=aist)
        if callable(self.engine):
            return self.engine(pair=pair, df=df, regime=regime, fgscore=fgscore, aist=aist)
        return None
