from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from convictionscorer import ConvictionScorer
from microstructure import enrich as micro_enrich
from phasepath import apply_phasepath
from strategies import ENGINECLASSES, REGIMEENGINES, S8MTFConfluence, scoredeltacontext

from scannermodels import PairContext, SpecialistObservation

logger = logging.getLogger("scannerspecialistregistry")


class ScannerSpecialistRegistry:
    def __init__(
        self,
        conviction_scorer: Optional[ConvictionScorer] = None,
        mtf_confluence: Optional[S8MTFConfluence] = None,
    ) -> None:
        self.conviction_scorer = conviction_scorer or ConvictionScorer()
        self.mtf_confluence = mtf_confluence or S8MTFConfluence()

    def specialist_ids_for_regime(self, regime: str) -> List[str]:
        return list(REGIMEENGINES.get(regime, []))

    def run_specialists(
        self,
        pair: str,
        df,
        context: PairContext,
        source_item: Dict[str, Any],
        aist: Optional[Dict[str, Any]] = None,
    ) -> List[SpecialistObservation]:
        aist = aist or {}
        regime = context.market_regime
        specialists = self.specialist_ids_for_regime(regime)
        observations: List[SpecialistObservation] = []

        logger.info("V4 SPECIALISTS pair=%s regime=%s count=%s", pair, regime, len(specialists))

        for specialist_id in specialists:
            cls = ENGINECLASSES.get(specialist_id)
            if cls is None:
                logger.warning("V4 SPECIALIST MISSING pair=%s specialist=%s", pair, specialist_id)
                continue

            try:
                raw = cls.generate(pair, df, regime, context.metadata.get("fg_score", 50), aist=aist)
                logger.info("V4 RAW pair=%s specialist=%s rawnone=%s", pair, specialist_id, raw is None)
            except Exception as exc:
                logger.warning("V4 RAW FAIL pair=%s specialist=%s err=%s", pair, specialist_id, exc)
                continue

            if not raw:
                continue

            try:
                raw["regime"] = regime
                raw["aistdirection"] = aist.get("direction")
                raw["aiststrength"] = aist.get("signalstrength")
                raw["atrpct"] = source_item.get("atrpct", 0.0)
                raw["volumeratio"] = source_item.get("volumeratio", 1.0)

                micro_enrich(raw, df, [source_item])
                applyphasepath(raw, df, specialist_id)

                try:
                    deltactx = scoredeltacontext(df, raw.get("bias", "LONG"))
                    raw.update(deltactx)
                    if "offencescore" in raw:
                        raw["offencescore"] = min(
                            1.0,
                            max(
                                0.0,
                                float(raw.get("offencescore", 0.0)) + float(deltactx.get("deltamodifier", 0.0)),
                            ),
                        )
                except Exception:
                    pass

                mtf = self.mtf_confluence.scoremtf(
                    pair,
                    raw.get("bias"),
                    df,
                    pairkey=source_item.get("pairkey"),
                )
                raw["mtfalignment"] = mtf.get("mtfverdict")
                raw["mtfscore"] = mtf.get("mtfscore")

                graded = self.conviction_scorer.score(raw)
                logger.info(
                    "V4 GRADE pair=%s specialist=%s grade=%s score=%s",
                    pair,
                    specialist_id,
                    graded.get("grade"),
                    graded.get("score"),
                )

                bias = str(raw.get("bias", "NEUTRAL")).upper()
                side = "LONG" if bias == "LONG" else "SHORT" if bias == "SHORT" else "NEUTRAL"

                thesis = " | ".join(
                    [
                        f"engine={specialist_id}",
                        f"grade={graded.get('grade', '?')}",
                        f"regime={regime}",
                        f"mtf={raw.get('mtfalignment', 'NONE')}",
                        f"aist={aist.get('direction', 'UNKNOWN')}",
                    ]
                )

                obs = SpecialistObservation(
                    specialist=specialist_id,
                    pair=pair,
                    setup_type=str(raw.get("rtsfamily") or raw.get("engine") or specialist_id),
                    side=side,
                    confidence=float(graded.get("score", 0.0)),
                    score=float(graded.get("score", 0.0) * 100.0),
                    thesis=thesis,
                    evidence={
                        **raw,
                        "entry_idea": raw.get("entry"),
                        "stop_idea": raw.get("sl"),
                        "target_idea": raw.get("tp"),
                        "rr": raw.get("rr"),
                        "grade": graded.get("grade"),
                    },
                    warnings=[],
                    tags=[
                        str(regime).lower(),
                        str(raw.get("engine", specialist_id)).lower(),
                    ],
                    context={
                        "aist": aist,
                        "mtf": mtf,
                        "graded": graded,
                    },
                )
                observations.append(obs)
            except Exception as exc:
                logger.warning("V4 OBS FAIL pair=%s specialist=%s err=%s", pair, specialist_id, exc)

        logger.info("V4 OBS pair=%s observations=%s", pair, len(observations))
        return observations
