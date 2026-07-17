from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from pairuniverse import PairUniverse
from regimeclassifier import RegimeClassifier


OHLC_COLUMNS = ["time", "open", "high", "low", "close", "vwap", "volume", "count"]


class ScannerPairIntake:
    def __init__(
        self,
        universe: Optional[PairUniverse] = None,
        regime_classifier: Optional[RegimeClassifier] = None,
        max_pairs: Optional[int] = None,
        interval: int = 240,
        min_rows: int = 60,
    ) -> None:
        self.universe = universe or PairUniverse()
        self.regime_classifier = regime_classifier or RegimeClassifier()
        self.max_pairs = max_pairs
        self.interval = interval
        self.min_rows = min_rows

    def fetch_active_pairs(self) -> List[Dict[str, Any]]:
        return list(
            self.universe.get_active_pairs(
                interval=self.interval,
                limit=self.max_pairs,
            )
        )

    def df_from_universe_item(self, item: Dict[str, Any]) -> Optional[pd.DataFrame]:
        raw = item.get("ohlc4h")
        if not raw:
            return None

        try:
            df = pd.DataFrame(raw, columns=OHLC_COLUMNS)
        except Exception:
            return None

        required = ["open", "high", "low", "close", "vwap", "volume"]
        for col in required:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna().reset_index(drop=True)
        if len(df) < self.min_rows:
            return None
        return df

    def build_pair_records(self) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []

        for item in self.fetch_active_pairs():
            pair = str(item.get("pair", "UNKNOWN"))
            df = self.df_from_universe_item(item)
            if df is None:
                continue

            regime = self.regime_classifier.classify(
                pair=pair,
                df=df,
                fg_score=int(item.get("fg_score", 50)),
            )

            records.append(
                {
                    "pair": pair,
                    "pair_key": item.get("pair_key"),
                    "regime": regime,
                    "dataframe": df,
                    "source": item,
                }
            )

        return records
