from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import pandas as pd

from pairuniverse import PairUniverse
from regimeclassifier import RegimeClassifier
from aisupertrend import AISupertrend
from scannermodels import PairContext

logger = logging.getLogger("scannerpairintake")

OHLC_COLUMNS = ["time", "open", "high", "low", "close", "vwap", "volume", "count"]


class ScannerPairIntake:
    def __init__(
        self,
        universe: Optional[PairUniverse] = None,
        regime_classifier: Optional[RegimeClassifier] = None,
        ai_supertrend: Optional[AISupertrend] = None,
        min_rows: int = 60,
    ) -> None:
        self.universe = universe or PairUniverse()
        self.regime_classifier = regime_classifier or RegimeClassifier()
        self.ai_supertrend = ai_supertrend or AISupertrend()
        self.min_rows = min_rows

    def get_active_pairs(self, interval: int = 240, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        active = self.universe.getactivepairs(interval=interval, limit=limit)
        logger.info("V4 INTAKE active_pairs=%s interval=%s", len(active), interval)
        return active

    def dataframe_from_item(self, item: Dict[str, Any]) -> Optional[pd.DataFrame]:
        raw = item.get("ohlc4h")
        pair = item.get("pair", "UNKNOWN")

        if not raw:
            logger.info("V4 DF pair=%s dfnone=True rows=0", pair)
            return None

        try:
            df = pd.DataFrame(raw, columns=OHLC_COLUMNS)
            for col in ["open", "high", "low", "close", "vwap", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna().reset_index(drop=True)
            logger.info("V4 DF pair=%s dfnone=%s rows=%s", pair, False, len(df))
            return df
        except Exception as exc:
            logger.warning("V4 DF FAIL pair=%s err=%s", pair, exc)
            return None

    def build_context(self, item: Dict[str, Any], df: pd.DataFrame, fg_score: int) -> PairContext:
        pair = str(item.get("pair", "UNKNOWN"))
        last_price = None
        if not df.empty and "close" in df.columns:
            try:
                last_price = float(df["close"].iloc[-1])
            except Exception:
                last_price = None

        regime = self.regime_classifier.classify(pair, df, fg_score)
        logger.info("V4 REGIME pair=%s regime=%s", pair, regime)

        return PairContext(
            pair=pair,
            timeframe="4h",
            last_price=last_price,
            market_regime=regime,
            metadata={
                "pairkey": item.get("pairkey"),
                "atrpct": item.get("atrpct"),
                "volumeratio": item.get("volumeratio"),
                "fg_score": fg_score,
                "source_item": item,
            },
        )

    def compute_supertrend(self, pair: str, df: pd.DataFrame) -> Dict[str, Any]:
        try:
            result = self.ai_supertrend.compute(pair, df)
            logger.info(
                "V4 AIST pair=%s dir=%s strength=%s",
                pair,
                result.get("direction"),
                result.get("signalstrength"),
            )
            return result
        except Exception as exc:
            logger.warning("V4 AIST FAIL pair=%s err=%s", pair, exc)
            return {}

    def prepare_pair(
        self,
        item: Dict[str, Any],
        fg_score: int,
    ) -> Optional[Dict[str, Any]]:
        pair = str(item.get("pair", "UNKNOWN"))
        df = self.dataframe_from_item(item)

        if df is None or len(df) < self.min_rows:
            logger.info("V4 SKIP pair=%s reason=insufficient_rows", pair)
            return None

        context = self.build_context(item, df, fg_score)
        aist = self.compute_supertrend(pair, df)

        return {
            "pair": pair,
            "item": item,
            "df": df,
            "context": context,
            "aist": aist,
        }
