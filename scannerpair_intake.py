from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from scannermodels import PairContext


class ScannerPairIntake:
    def __init__(
        self,
        default_timeframe: str = "1h",
        min_rows: int = 50,
    ) -> None:
        self.default_timeframe = default_timeframe
        self.min_rows = min_rows

    def normalize_pairs(self, raw_pairs: Iterable[Dict[str, Any]]) -> List[PairContext]:
        contexts: List[PairContext] = []

        for item in raw_pairs:
            pair = str(item.get("pair", "")).strip().upper()
            if not pair:
                continue

            history = item.get("ohlc") or item.get("candles") or []
            if isinstance(history, list) and len(history) < self.min_rows:
                continue

            context = PairContext(
                pair=pair,
                timeframe=str(item.get("timeframe", self.default_timeframe)),
                last_price=self._safe_float(item.get("last_price")),
                market_regime=str(item.get("market_regime", "unknown")),
                metadata={
                    "atr_pct": self._safe_float(item.get("atr_pct")),
                    "volume_ratio": self._safe_float(item.get("volume_ratio")),
                    "fg_score": item.get("fg_score"),
                    "ohlc": history,
                    "source": item.get("source", "intake"),
                },
            )
            contexts.append(context)

        return contexts

    @staticmethod
    def _safe_float(value: Optional[Any]) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
