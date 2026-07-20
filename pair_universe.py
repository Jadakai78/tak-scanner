from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional
from datetime import datetime, timezone
import math

PROP_WHITELIST = {
    "symbol",
    "base",
    "quote",
    "timeframe",
    "exchange",
    "last_price",
    "is_tradeable",
    "market_active",
    "data_fresh",
    "metadata",
}

@dataclass
class PairContext:
    symbol: str
    base: str = ""
    quote: str = ""
    timeframe: str = "15m"
    exchange: str = ""
    last_price: Optional[float] = None
    is_tradeable: bool = True
    market_active: bool = True
    data_fresh: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


class MarketDataSource:
    def fetch_pairs(self) -> Iterable[Dict[str, Any]]:
        return [
            {
                "symbol": "BTCUSD",
                "base": "BTC",
                "quote": "USD",
                "timeframe": "15m",
                "exchange": "kraken",
                "last_price": 118250.0,
                "is_tradeable": True,
                "market_active": True,
                "data_fresh": True,
            },
            {
                "symbol": "ETHUSD",
                "base": "ETH",
                "quote": "USD",
                "timeframe": "15m",
                "exchange": "kraken",
                "last_price": 6425.0,
                "is_tradeable": True,
                "market_active": True,
                "data_fresh": True,
            },
            {
                "symbol": "SOLUSD",
                "base": "SOL",
                "quote": "USD",
                "timeframe": "15m",
                "exchange": "kraken",
                "last_price": 214.0,
                "is_tradeable": True,
                "market_active": True,
                "data_fresh": True,
            },
        ]


class PairUniverse:
    """
    Pure active-pairs loader.

    Responsibilities:
    - Load tradeable active pairs.
    - Reject only broken or unusable market rows.
    - Do NOT calculate ATR, RSI, volume scores, or any strategy indicators.
    - Do NOT apply thesis filters or regime preference gates.

    Architecture rule:
    PairUniverse may reject broken data, but it may not reject a thesis.
    Oracle, regime logic, specialists, REMI, APRIL, and RTS own interpretation.
    """

    def __init__(self, market_data_source: MarketDataSource) -> None:
        self.market_data_source = market_data_source

    def get_active_pairs(self) -> List[PairContext]:
        active_pairs: List[PairContext] = []

        for row in self.market_data_source.fetch_pairs():
            pair = self._build_pair_context(row)
            if not pair:
                continue
            if not self._passes_hard_sanity(pair):
                continue
            active_pairs.append(pair)

        return active_pairs

    def _build_pair_context(self, row: Dict[str, Any]) -> Optional[PairContext]:
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            return None

        raw_last_price = row.get("last_price")
        try:
            parsed_last_price = float(raw_last_price) if raw_last_price is not None else None
        except (TypeError, ValueError):
            parsed_last_price = None

        last_price = (
            parsed_last_price
            if parsed_last_price is not None and math.isfinite(parsed_last_price)
            else None
        )

        return PairContext(
            symbol=symbol,
            base=str(row.get("base") or "").upper(),
            quote=str(row.get("quote") or "").upper(),
            timeframe=str(row.get("timeframe") or "15m"),
            exchange=str(row.get("exchange") or ""),
            last_price=last_price,
            is_tradeable=bool(row.get("is_tradeable", True)),
            market_active=bool(row.get("market_active", True)),
            data_fresh=bool(row.get("data_fresh", True)),
            metadata={
                "loaded_at": datetime.now(timezone.utc).isoformat(),
                "source": row.get("source", "market_data_source"),
            },
        )

    def _passes_hard_sanity(self, pair: PairContext) -> bool:
        if not pair.is_tradeable:
            return False
        if not pair.market_active:
            return False
        if not pair.data_fresh:
            return False
        if pair.last_price is None:
            return False
        if pair.last_price <= 0:
            return False
        return True


if __name__ == "__main__":
    source = MarketDataSource()
    universe = PairUniverse(source)
    pairs = universe.get_active_pairs()
    for pair in pairs:
        print(pair)
