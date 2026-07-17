from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from scannermodels import SpecialistObservation
from scannercandidate_factory import build_candidate

from convictionscorer import ConvictionScorer
from strategies import ENGINECLASSES, REGIMEENGINES

logger = logging.getLogger("takscannerv4")


class SpecialistRegistry:
    def __init__(
        self,
        conviction_scorer: Optional[ConvictionScorer] = None,
    ) -> None:
        self.conviction_scorer = conviction_scorer or ConvictionScorer()

    def engines_for_regime(self, regime: str) -> List[str]:
        return list(REGIMEENGINES.get(regime, []))

    def run_engine(
        self,
        engine_id: str,
        pair: str,
        df: Any,
        regime: str,
        fg_score: int,
        extras: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        engine_cls = ENGINECLASSES.get(engine_id)
        if engine_cls is None:
            return None

        logger.info(
            "V4 adapter calling engine=%s pair=%s regime=%s fg=%s",
            engine_id,
            pair,
            regime,
            fg_score,
        )

        try:
            raw = engine_cls.generate(pair, df, regime, fg_score, **(extras or {}))
            logger.info("V4 TAK pair=%s engine=%s rawnone=%s", pair, engine_id, raw is None)
            return raw
        except Exception:
            logger.exception("V4 adapter failed engine=%s pair=%s", engine_id, pair)
            return None

    def observation_from_raw(
        self,
        engine_id: str,
        pair: str,
        raw: Dict[str, Any],
    ) -> SpecialistObservation:
        graded = self.conviction_scorer.score(raw)
        confidence = float(graded.get("score", 0.0))
        score = round(confidence * 100.0, 2)

        setup_type = str(raw.get("engine") or engine_id)
        side = str(raw.get("bias") or "NEUTRAL")
        thesis = str(raw.get("summary") or raw.get("thesis") or f"{setup_type} setup on {pair}")

        evidence = {
            "entry_idea": raw.get("entry"),
            "stop_idea": raw.get("sl"),
            "target_idea": raw.get("tp"),
            "rr": raw.get("rr"),
            "grade": graded.get("grade"),
            "raw": raw,
        }

        warnings: List[str] = []
        if raw.get("rr") is not None and float(raw.get("rr", 0.0)) < 1.5:
            warnings.append("low_rr")
        if raw.get("structurequality") is not None and float(raw.get("structurequality", 0.0)) < 0.45:
            warnings.append("weak_structure")

        tags: List[str] = []
        if "break" in setup_type.lower():
            tags.append("breakout")
        if raw.get("countertrend"):
            tags.append("countertrend")

        logger.info(
            "V4 OBS pair=%s engine=%s bias=%s rr=%s struct=%s vol=%s conf=%.3f score=%.2f",
            pair,
            engine_id,
            side,
            raw.get("rr"),
            raw.get("structurequality"),
            raw.get("volumeratio"),
            confidence,
            score,
        )

        return SpecialistObservation(
            specialist=engine_id,
            pair=pair,
            setup_type=setup_type,
            side=side,
            confidence=confidence,
            score=score,
            thesis=thesis,
            evidence=evidence,
            warnings=warnings,
            tags=tags,
            context={
                "grade": graded.get("grade"),
                "engine_id": engine_id,
            },
        )

    def collect_candidates_for_pair(
        self,
        pair: str,
        df: Any,
        regime: str,
        fg_score: int,
        extras: Optional[Dict[str, Any]] = None,
    ) -> List[Any]:
        candidates = []

        for engine_id in self.engines_for_regime(regime):
            raw = self.run_engine(
                engine_id=engine_id,
                pair=pair,
                df=df,
                regime=regime,
                fg_score=fg_score,
                extras=extras,
            )
            if raw is None or not raw.get("bias"):
                continue

            obs = self.observation_from_raw(engine_id, pair, raw)
            candidates.append(build_candidate(obs))

        logger.info("V4 orchestrator pair=%s candidates=%s", pair, len(candidates))
        return candidates
