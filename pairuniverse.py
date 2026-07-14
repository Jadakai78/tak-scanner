"""pair_universe.py — Dynamic Kraken USD pair discovery and ATR ranking.

Layer 1 (Data Ingestion) foundation for JHL Trading Architecture v2.

Pulls the full Kraken spot pair list, filters to tradeable USD-quoted pairs
(excluding stablecoins), fetches 4H OHLC for each, computes volatility/volume/
momentum metrics, filters out dead pairs, ranks by ATR% descending, and writes
the ranked universe to the shared ``signal_bus.json``.

No static pair lists anywhere — the universe is rebuilt every scan cycle so a
newly listed Kraken pair appears automatically.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("pair_universe")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
KRAKEN_BASE = "https://api.kraken.com/0/public"
ASSET_PAIRS_URL = f"{KRAKEN_BASE}/AssetPairs"
OHLC_URL = f"{KRAKEN_BASE}/OHLC"

MODULE_DIR = Path(__file__).resolve().parent
SIGNAL_BUS_PATH = MODULE_DIR / "signal_bus.json"

REQUEST_TIMEOUT = 10  # seconds
RATE_LIMIT_SLEEP = 0.5  # seconds between OHLC fetches

# Stablecoins to skip — matched against the base asset of the pair.
STABLECOINS = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "PYUSD",
    "USDD", "FRAX", "LUSD", "EURT", "USDS", "UST", "USD", "ZUSD",
    "EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "USDG", "RLUSD", "USDR",
}

# Filter thresholds (from blueprint DYNAMIC PAIR UNIVERSE section).
MIN_VOLUME_24H_USD = 100_000.0   # lowered — prop pairs often below 500K
MIN_ATR_PCT = 0.30               # lowered — prop pairs can be low volatility

# All 54 Kraken prop-eligible pairs — always included regardless of ATR/vol.
# Updated from kraken_prop_pairs.csv — drop new symbols here as needed.
PROP_WHITELIST: set[str] = {
    "BTC","ETH","SOL","HYPE","XRP","ZEC","SUI","ADA","DOGE","AAVE",
    "LTC","TAO","LINK","UNI","NEAR","ARB","ONDO","TRX","AVAX","DOT",
    "BCH","PUMP","CRV","ALGO","TIA","HBAR","WLD","FARTCOIN","POL","XPL",
    "WIF","BNB","INJ","FIL","JUP","ATOM","LDO","PENGU","VIRTUAL","RENDER",
    "JTO","GRASS","KAITO","TRUMP","ASTER","OP","POPCAT","APT","S","STX",
    "ETC","MOODENG","PNUT","AIXBT",
}


class PairUniverse:
    """Builds and ranks the dynamic Kraken USD trading universe.

    Typical usage::

        pu = PairUniverse()
        ranked = pu.get_active_pairs(interval=240)

    Attributes:
        session: Reusable ``requests.Session`` for connection pooling.
        signal_bus_path: Where the ranked universe is persisted.
    """

    def __init__(
        self,
        signal_bus_path: Optional[Path] = None,
        min_volume_24h: float = MIN_VOLUME_24H_USD,
        min_atr_pct: float = MIN_ATR_PCT,
    ) -> None:
        """Initialize the universe builder.

        Args:
            signal_bus_path: Override path for the signal bus JSON file.
            min_volume_24h: Minimum 24h USD volume for a pair to qualify.
            min_atr_pct: Minimum ATR% for a pair to be considered "active".
        """
        self.session = requests.Session()
        self.signal_bus_path = signal_bus_path or SIGNAL_BUS_PATH
        self.min_volume_24h = min_volume_24h
        self.min_atr_pct = min_atr_pct

    # ------------------------------------------------------------------
    # Kraken API helpers
    # ------------------------------------------------------------------
    def fetch_asset_pairs(self) -> Dict[str, Any]:
        """Fetch all Kraken asset pairs.

        Returns:
            Mapping of Kraken pair key -> pair metadata. Empty dict on failure.
        """
        try:
            resp = self.session.get(ASSET_PAIRS_URL, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("error"):
                logger.error("AssetPairs API error: %s", payload["error"])
                return {}
            return payload.get("result", {})
        except (requests.RequestException, ValueError) as exc:
            logger.error("Failed to fetch AssetPairs: %s", exc)
            return {}

    def fetch_ohlc(self, pair_key: str, interval: int = 240) -> Optional[pd.DataFrame]:
        """Fetch OHLC candles for a single pair.

        Args:
            pair_key: Kraken pair identifier (the AssetPairs result key).
            interval: Candle interval in minutes (1, 5, 15, 60, 240, 1440).

        Returns:
            DataFrame with columns [time, open, high, low, close, vwap, volume,
            count] or ``None`` on failure.
        """
        try:
            resp = self.session.get(
                OHLC_URL,
                params={"pair": pair_key, "interval": interval},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("error"):
                logger.warning("OHLC error for %s: %s", pair_key, payload["error"])
                return None
            result = payload.get("result", {})
            # Result contains the OHLC list under the canonical pair name plus
            # a "last" key. Grab whichever key is not "last".
            data_key = next((k for k in result if k != "last"), None)
            if data_key is None:
                logger.warning("No OHLC data returned for %s", pair_key)
                return None
            rows = result[data_key]
            if not rows:
                return None
            df = pd.DataFrame(
                rows,
                columns=["time", "open", "high", "low", "close", "vwap",
                         "volume", "count"],
            )
            for col in ["open", "high", "low", "close", "vwap", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["time"] = pd.to_numeric(df["time"], errors="coerce")
            return df.dropna().reset_index(drop=True)
        except (requests.RequestException, ValueError, KeyError) as exc:
            logger.warning("Failed to fetch OHLC for %s: %s", pair_key, exc)
            return None

    # ------------------------------------------------------------------
    # Indicator math
    # ------------------------------------------------------------------
    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
        """Compute the latest Average True Range (Wilder) value.

        Args:
            df: OHLC DataFrame.
            period: ATR lookback.

        Returns:
            Latest ATR value or ``None`` if insufficient data.
        """
        if len(df) < period + 1:
            return None
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        close = df["close"].to_numpy()
        prev_close = np.roll(close, 1)
        tr = np.maximum.reduce([
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ])
        tr[0] = high[0] - low[0]
        # Wilder smoothing via EMA with alpha = 1/period.
        atr = pd.Series(tr).ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
        return float(atr)

    @staticmethod
    def _rsi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
        """Compute the latest RSI(period) value.

        Args:
            df: OHLC DataFrame.
            period: RSI lookback.

        Returns:
            Latest RSI (0-100) or ``None`` if insufficient data.
        """
        if len(df) < period + 1:
            return None
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100 - (100 / (1 + rs)))

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------
    @staticmethod
    def _is_usd_quoted(altname: str) -> bool:
        """Return True if the pair altname is USD-quoted."""
        return altname.endswith("USD") or altname.endswith("ZUSD")

    @staticmethod
    def _base_asset(info: Dict[str, Any], altname: str) -> str:
        """Best-effort extraction of the base asset symbol from pair metadata."""
        base = info.get("base", "")
        # Kraken prefixes some assets with X/Z (e.g. XXBT, ZUSD). Strip a single
        # leading X/Z when the symbol is 4 chars (legacy naming).
        if len(base) == 4 and base[0] in ("X", "Z"):
            base = base[1:]
        if not base:
            # Fall back to trimming the USD suffix off the altname.
            for suffix in ("ZUSD", "USD"):
                if altname.endswith(suffix):
                    base = altname[: -len(suffix)]
                    break
        return base.upper()

    def _candidate_pairs(self, asset_pairs: Dict[str, Any]) -> List[Dict[str, str]]:
        """Filter raw AssetPairs down to non-stablecoin USD-quoted candidates.

        Args:
            asset_pairs: Raw result mapping from :meth:`fetch_asset_pairs`.

        Returns:
            List of ``{pair_key, altname, base}`` candidate descriptors.
        """
        candidates: List[Dict[str, str]] = []
        for pair_key, info in asset_pairs.items():
            altname = info.get("altname", "")
            if not altname or not self._is_usd_quoted(altname):
                continue
            # Skip non-tradeable / dark-pool (.d) pairs.
            if pair_key.endswith(".d"):
                continue
            base = self._base_asset(info, altname)
            if base in STABLECOINS:
                continue
            candidates.append({"pair_key": pair_key, "altname": altname, "base": base})
        return candidates

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def analyze_pair(
        self, candidate: Dict[str, str], interval: int = 240
    ) -> Optional[Dict[str, Any]]:
        """Fetch OHLC and compute metrics for one candidate pair.

        Args:
            candidate: Descriptor from :meth:`_candidate_pairs`.
            interval: OHLC interval in minutes.

        Returns:
            Metrics dict if the pair passes all filters, else ``None``.
        """
        df = self.fetch_ohlc(candidate["pair_key"], interval=interval)
        if df is None or len(df) < 21:
            return None

        try:
            close = float(df["close"].iloc[-1])
            if close <= 0:
                return None

            atr = self._atr(df, period=14)
            if atr is None:
                return None
            atr_pct = (atr / close) * 100

            rsi = self._rsi(df, period=14)
            if rsi is None:
                return None

            # 24h volume = last 6 candles (6 x 4H) in USD terms (volume x close).
            last6 = df.tail(6)
            volume_24h = float((last6["volume"] * last6["close"]).sum())

            current_vol = float(df["volume"].iloc[-1])
            avg20_vol = float(df["volume"].tail(20).mean())
            volume_ratio = current_vol / avg20_vol if avg20_vol > 0 else 0.0
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("Metric calc failed for %s: %s", candidate["altname"], exc)
            return None

        # Filters — bypassed for prop-whitelisted pairs.
        is_prop = candidate["base"] in PROP_WHITELIST
        if not is_prop:
            if volume_24h < self.min_volume_24h:
                return None
            if atr_pct < self.min_atr_pct:
                return None

        return {
            "pair": candidate["base"],
            "altname": candidate["altname"],
            "pair_key": candidate["pair_key"],
            "is_prop": is_prop,
            "atr_pct": round(atr_pct, 4),
            "volume_24h": round(volume_24h, 2),
            "rsi": round(rsi, 2),
            "volume_ratio": round(volume_ratio, 4),
            "close": close,
            "ohlc_4h": df.values.tolist(),
        }

    def get_active_pairs(
        self,
        interval: int = 240,
        limit: Optional[int] = None,
        sort_by: str = "atr_pct",
    ) -> List[Dict[str, Any]]:
        """Build the ranked active pair universe.

        Args:
            interval: OHLC interval in minutes (default 240 = 4H).
            limit: Optional cap on the number of pairs returned, applied
                AFTER ranking (so the highest-``sort_by`` pairs always win —
                previously this was applied to the unranked, alphabetically
                ordered candidate list, which silently excluded high-ATR%%
                pairs like BTC/ETH/SOL/XRP). ``None`` returns all.
            sort_by: Metric key to rank pairs by, descending. Defaults to
                ``"atr_pct"`` (volatility) so the most active pairs surface
                first regardless of how many candidates exist.

        Returns:
            List of metric dicts ranked by ``sort_by`` descending. Also
            written to ``signal_bus.json`` under the ``pair_universe`` key.
        """
        asset_pairs = self.fetch_asset_pairs()
        if not asset_pairs:
            logger.error("No asset pairs fetched — aborting universe build.")
            return []

        candidates = self._candidate_pairs(asset_pairs)
        logger.info("Found %d USD-quoted non-stablecoin candidates.", len(candidates))

        # Fetch all candidates in parallel — dramatically faster than serial.
        # ThreadPoolExecutor caps concurrency so we don't hammer the API;
        # 8 workers ≈ 8x speedup while staying well within Kraken's rate limits.
        ranked: List[Dict[str, Any]] = []
        MAX_WORKERS = 8
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            future_to_cand = {
                pool.submit(self.analyze_pair, cand, interval): cand
                for cand in candidates
            }
            for future in as_completed(future_to_cand):
                try:
                    metrics = future.result(timeout=15)
                except Exception as exc:
                    cand = future_to_cand[future]
                    logger.warning("analyze_pair failed for %s: %s", cand.get("altname"), exc)
                    metrics = None
                if metrics is not None:
                    ranked.append(metrics)
                    logger.info(
                        "PASS %s | ATR%%=%.2f vol24h=$%.0f rsi=%.1f%s",
                        metrics["altname"], metrics["atr_pct"],
                        metrics["volume_24h"], metrics["rsi"],
                        " [PROP]" if metrics.get("is_prop") else "",
                    )

        ranked.sort(key=lambda d: d.get(sort_by, 0.0), reverse=True)
        if limit is not None:
            ranked = ranked[:limit]
        logger.info("Active universe: %d pairs (sorted by %s).", len(ranked), sort_by)
        self._write_signal_bus(ranked)
        return ranked

    def _write_signal_bus(self, ranked: List[Dict[str, Any]]) -> None:
        """Persist the ranked universe into ``signal_bus.json`` (merge-safe).

        Preserves any other top-level keys already present in the bus.

        Args:
            ranked: Ranked list of pair metric dicts.
        """
        bus: Dict[str, Any] = {}
        if self.signal_bus_path.exists():
            try:
                bus = json.loads(self.signal_bus_path.read_text() or "{}")
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Could not read existing signal bus: %s", exc)
                bus = {}

        # Store a lean copy in the bus (drop the heavy raw OHLC to keep it small).
        lean = [
            {k: v for k, v in item.items() if k != "ohlc_4h"} for item in ranked
        ]
        bus["pair_universe"] = {
            "count": len(lean),
            "pairs": lean,
        }
        try:
            self.signal_bus_path.write_text(json.dumps(bus, indent=2, default=str))
            logger.info("Wrote %d pairs to %s", len(lean), self.signal_bus_path)
        except OSError as exc:
            logger.error("Failed to write signal bus: %s", exc)


if __name__ == "__main__":
    # Demo: build a small slice of the universe so it stays rate-limit friendly.
    logger.info("=== PairUniverse demo ===")
    pu = PairUniverse()

    # Directly analyze a few well-known pairs for a fast, deterministic demo.
    demo_candidates = [
        {"pair_key": "XXBTZUSD", "altname": "XBTUSD", "base": "BTC"},
        {"pair_key": "SOLUSD", "altname": "SOLUSD", "base": "SOL"},
        {"pair_key": "XRPUSD", "altname": "XRPUSD", "base": "XRP"},
    ]
    results = []
    for c in demo_candidates:
        m = pu.analyze_pair(c, interval=240)
        if m:
            results.append(m)
            print(
                f"{m['pair']:5s} ATR%={m['atr_pct']:.2f} "
                f"vol24h=${m['volume_24h']:,.0f} RSI={m['rsi']:.1f} "
                f"volRatio={m['volume_ratio']:.2f} close={m['close']}"
            )
        else:
            print(f"{c['base']:5s} -> filtered out or fetch failed")
        time.sleep(RATE_LIMIT_SLEEP)

    results.sort(key=lambda d: d["atr_pct"], reverse=True)
    pu._write_signal_bus(results)
    print(f"\nRanked {len(results)} demo pairs by ATR% and wrote signal_bus.json")
