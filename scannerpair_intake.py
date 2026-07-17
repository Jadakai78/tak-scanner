from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional

from scannermodels import PairContext


class ScannerPairIntake:
    def __init__(
        self,
        default_timeframe: str = "1h",
        minimum_rows: int = 80,
    ) -> None:
        self.default_timeframe = default_timeframe
        self.minimum_rows = minimum_rows

    def normalize_pair_record(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        pair = str(payload.get("pair") or payload.get("symbol") or "").strip().upper()
        timeframe = str(payload.get("timeframe") or self.default_timeframe)
        regime = str(payload.get("market_regime") or payload.get("regime") or "unknown")
        last_price = payload.get("last_price")

        record = {
            "pair": pair,
            "timeframe": timeframe,
            "market_regime": regime,
            "last_price": last_price,
            "ohlc": list(payload.get("ohlc", [])),
            "volume_ratio": payload.get("volume_ratio"),
            "atr_pct": payload.get("atr_pct"),
            "metadata": dict(payload.get("metadata", {})),
        }
        return record

    def to_context(self, payload: Dict[str, Any]) -> PairContext:
        record = self.normalize_pair_record(payload)
        metadata = dict(record.get("metadata", {}))
        metadata["ohlc_rows"] = len(record.get("ohlc", []))
        metadata["volume_ratio"] = record.get("volume_ratio")
        metadata["atr_pct"] = record.get("atr_pct")

        return PairContext(
            pair=record["pair"],
            timeframe=record["timeframe"],
            last_price=record["last_price"],
            market_regime=record["market_regime"],
            metadata=metadata,
        )

    def pair_is_eligible(self, payload: Dict[str, Any]) -> bool:
        record = self.normalize_pair_record(payload)
        if not record["pair"]:
            return False
        if len(record["ohlc"]) < self.minimum_rows:
            return False
        return True

    def prepare(self, universe: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        prepared: List[Dict[str, Any]] = []

        for item in universe:
            record = self.normalize_pair_record(dict(item))
            if not self.pair_is_eligible(record):
                continue

            context = self.to_context(record)
            prepared.append(
                {
                    "context": asdict(context),
                    "pair": record["pair"],
                    "timeframe": record["timeframe"],
                    "market_regime": record["market_regime"],
                    "last_price": record["last_price"],
                    "ohlc": record["ohlc"],
                    "volume_ratio": record["volume_ratio"],
                    "atr_pct": record["atr_pct"],
                    "metadata": record["metadata"],
                }
            )

        return prepared

    def prepare_from_active_pairs(
        self,
        active_pairs: Iterable[Dict[str, Any]],
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        prepared = self.prepare(active_pairs)
        if limit is not None:
            return prepared[:limit]
        return prepared
