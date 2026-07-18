from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

from scannermodels import PairContext

logger = logging.getLogger("scannerpairintake")


class ScannerPairIntake:
    def __init__(self, regime_classifier: Any | None = None) -> None:
        self.regime_classifier = regime_classifier

    def build_contexts(
        self,
        active_pairs: Iterable[Dict[str, Any]],
        timeframe: str = "4h",
        max_pairs: Optional[int] = None,
    ) -> List[PairContext]:
        contexts: List[PairContext] = []

        for item in active_pairs:
            pair = str(item.get("pair", "")).strip()
            if not pair:
                continue

            regime = str(item.get("regime", "")).strip() or self._derive_regime(item)
            metadata = dict(item)

            # Build indicators dict — put ohlc_4h in so adapter can find it
            ohlc_raw = item.get("ohlc_4h")
            indicators: dict = {}
            if ohlc_raw is not None:
                indicators["ohlc_4h"] = ohlc_raw

            contexts.append(
                PairContext(
                    pair=pair,
                    timeframe=str(item.get("timeframe", timeframe)),
                    market_regime=regime,
                    fear_greed=float(item.get("fgscore", 50)),
                    indicators=indicators,
                    context=metadata,
                )
            )

            if max_pairs is not None and len(contexts) >= max_pairs:
                break

        logger.info(
            "V4 intake complete contexts=%s contextpairs=%s",
            len(contexts),
            ", ".join(ctx.pair for ctx in contexts[:150]),
        )
        return contexts

    def _derive_regime(self, item: Dict[str, Any]) -> str:
        if self.regime_classifier is not None and "df" in item:
            try:
                pair = str(item.get("pair", "UNKNOWN"))
                return str(self.regime_classifier.classify(pair, item["df"], item.get("fgscore", 50)))
            except Exception:
                pass

        aist_dir = str(item.get("aist_direction", "")).upper()
        if aist_dir == "UP":
            return "TRENDUP"
        if aist_dir == "DOWN":
            return "TRENDDOWN"
        return str(item.get("market_regime", "unknown"))

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
