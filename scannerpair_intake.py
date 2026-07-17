from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from scannermodels import PairContext

logger = logging.getLogger("takscannerv4")


class ScannerPairIntake:
    def __init__(self, universe: Any, regime_classifier: Any) -> None:
        self.universe = universe
        self.regime_classifier = regime_classifier
        self.ohlc_columns = ["time", "open", "high", "low", "close", "vwap", "volume", "count"]

    def _df_from_item(self, item: Dict[str, Any]) -> Optional[pd.DataFrame]:
        raw = item.get("ohlc4h") or item.get("ohlc") or item.get("candles")
        pair = str(item.get("pair", "UNKNOWN"))
        if not raw:
            logger.info("V4 DF pair%s dfnoneTrue rows0", pair)
            return None

        try:
            df = pd.DataFrame(raw, columns=self.ohlc_columns[: len(raw[0])]) if raw and isinstance(raw[0], (list, tuple)) else pd.DataFrame(raw)
            for col in ("open", "high", "low", "close", "vwap", "volume"):
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna().reset_index(drop=True)
            logger.info("V4 DF pair%s dfnoneFalse rows%s", pair, len(df))
            return df
        except Exception:
            logger.info("V4 DF pair%s dfnoneTrue rows0", pair)
            return None

    def collect(self, max_pairs: Optional[int] = None) -> List[PairContext]:
        active_pairs: Iterable[Dict[str, Any]] = self.universe.getactivepairs(interval=240, limit=max_pairs)
        contexts: List[PairContext] = []

        for item in active_pairs:
            pair = str(item.get("pair", "UNKNOWN"))
            df = self._df_from_item(item)
            if df is None or len(df) < 60:
                continue

            regime = str(self.regime_classifier.classify(pair, df, 50))
            logger.info("V4 REGIME pair%s regime%s", pair, regime)

            last_price = None
            if "close" in df.columns and len(df) > 0:
                try:
                    last_price = float(df["close"].iloc[-1])
                except Exception:
                    last_price = None

            metadata = {
                "pair_key": item.get("pairkey"),
                "atrpct": item.get("atrpct"),
                "volume_ratio": item.get("volumeratio", item.get("volume_ratio")),
                "raw_item": item,
                "df": df,
            }

            contexts.append(
                PairContext(
                    pair=pair,
                    timeframe="4h",
                    last_price=last_price,
                    market_regime=regime,
                    metadata=metadata,
                )
            )

        logger.info(
            "V4 intake complete contexts%s contextpairs%s",
            len(contexts),
            ", ".join(ctx.pair for ctx in contexts),
        )
        return contexts
