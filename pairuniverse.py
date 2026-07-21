"""pair_universe.py — Dynamic Kraken USD pair discovery and ATR ranking.

Layer 1 (Data Ingestion) foundation for JHL Trading Architecture v2.

Pulls the full Kraken spot pair list, filters to tradeable USD-quoted pairs
(excluding stablecoins), fetches 4H OHLC for each, computes volatility/volume/
momentum metrics, filters out dead pairs, and ranks by ATR% descending.

Important: this module is now a pure data provider. It does NOT write to
signal_bus.json. The scanner is the single writer of the final bus payload.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

KRAKEN_BASE = "https://api.kraken.com/0/public"
ASSET_PAIRS_URL = f"{KRAKEN_BASE}/AssetPairs"
OHLC_URL = f"{KRAKEN_BASE}/OHLC"

REQUEST_TIMEOUT = 10
RATE_LIMIT_SLEEP = 0.5

STABLECOINS = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "USDP", "GUSD", "PYUSD",
    "USDD", "FRAX", "LUSD", "EURT", "USDS", "UST", "USD", "ZUSD",
    "EUR", "GBP", "AUD", "CAD", "CHF", "JPY", "USDG", "RLUSD", "USDR",
}

MIN_VOLUME_24H_USD = 100_000.0
MIN_ATR_PCT = 0.30

PROP_WHITELIST: set[str] = {
    "BTC","ETH","SOL","HYPE","XRP","ZEC","SUI","ADA","DOGE","AAVE",
    "LTC","TAO","LINK","UNI","NEAR","ARB","ONDO","TRX","AVAX","DOT",
    "BCH","PUMP","CRV","ALGO","TIA","HBAR","WLD","FARTCOIN","POL","XPL",
    "WIF","BNB","INJ","FIL","JUP","ATOM","LDO","PENGU","VIRTUAL","RENDER",
    "JTO","GRASS","KAITO","TRUMP","ASTER","OP","POPCAT","APT","S","STX",
    "ETC","MOODENG","PNUT","AIXBT",
}


class PairUniverse:
    def __init__(
        self,
        min_volume_24h: float = MIN_VOLUME_24H_USD,
        min_atr_pct: float = MIN_ATR_PCT,
    ) -> None:
        self.session = requests.Session()
        self.min_volume_24h = min_volume_24h
        self.min_atr_pct = min_atr_pct

    def fetch_asset_pairs(self) -> Dict[str, Any]:
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
            data_key = next((k for k in result if k != "last"), None)
            if data_key is None:
                logger.warning("No OHLC data returned for %s", pair_key)
                return None

            rows = result[data_key]
            if not rows:
                return None

            df = pd.DataFrame(
                rows,
                columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"],
            )
            for col in ["open", "high", "low", "close", "vwap", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df["time"] = pd.to_numeric(df["time"], errors="coerce")
            return df.dropna().reset_index(drop=True)
        except (requests.RequestException, ValueError, KeyError) as exc:
            logger.warning("Failed to fetch OHLC for %s: %s", pair_key, exc)
            return None

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> Optional[float]:
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
        atr = pd.Series(tr).ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
        return float(atr)

    @staticmethod
    def _rsi(df: pd.DataFrame, period: int = 14) -> Optional[float]:
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

    @staticmethod
    def _is_usd_quoted(altname: str) -> bool:
        return altname.endswith("USD") or altname.endswith("ZUSD")

    @staticmethod
    def _base_asset(info: Dict[str, Any], altname: str) -> str:
        base = info.get("base", "")
        if len(base) == 4 and base[0] in ("X", "Z"):
            base = base[1:]
        if not base:
            for suffix in ("ZUSD", "USD"):
                if altname.endswith(suffix):
                    base = altname[:-len(suffix)]
                    break
        return base.upper()

    def _candidate_pairs(self, asset_pairs: Dict[str, Any]) -> List[Dict[str, str]]:
        candidates: List[Dict[str, str]] = []
        for pair_key, info in asset_pairs.items():
            altname = info.get("altname", "")
            if not altname or not self._is_usd_quoted(altname):
                continue
            if pair_key.endswith(".d"):
                continue
            base = self._base_asset(info, altname)
            if base in STABLECOINS:
                continue
            candidates.append(
                {"pair_key": pair_key, "altname": altname, "base": base}
            )
        return candidates

    def analyze_pair(
        self, candidate: Dict[str, str], interval: int = 240
    ) -> Optional[Dict[str, Any]]:
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

            last6 = df.tail(6)
            volume_24h = float((last6["volume"] * last6["close"]).sum())

            current_vol = float(df["volume"].iloc[-1])
            avg20_vol = float(df["volume"].tail(20).mean())
            volume_ratio = current_vol / avg20_vol if avg20_vol > 0 else 0.0
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("Metric calc failed for %s: %s", candidate["altname"], exc)
            return None

        is_prop = candidate["base"] in PROP_WHITELIST
        if not is_prop:
            # FILTER REMOVED: Let specialist strategies decide pair quality
            # if volume_24h < self.min_volume_24h:
            #     return None
            # if atr_pct < self.min_atr_pct:
            #     return None
            pass

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
        asset_pairs = self.fetch_asset_pairs()
        if not asset_pairs:
            logger.error("No asset pairs fetched — aborting universe build.")
            return []

        candidates = self._candidate_pairs(asset_pairs)
        logger.info("Found %d USD-quoted non-stablecoin candidates.", len(candidates))

        ranked: List[Dict[str, Any]] = []
        max_workers = 8

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_cand = {
                pool.submit(self.analyze_pair, cand, interval): cand
                for cand in candidates
            }
            for future in as_completed(future_to_cand):
                try:
                    metrics = future.result(timeout=15)
                except Exception as exc:
                    cand = future_to_cand[future]
                    logger.warning(
                        "analyze_pair failed for %s: %s",
                        cand.get("altname"),
                        exc,
                    )
                    metrics = None

                if metrics is not None:
                    ranked.append(metrics)
                    logger.info(
                        "PASS %s | ATR%%=%.2f vol24h=$%.0f rsi=%.1f%s",
                        metrics["altname"],
                        metrics["atr_pct"],
                        metrics["volume_24h"],
                        metrics["rsi"],
                        " [PROP]" if metrics.get("is_prop") else "",
                    )

        ranked.sort(key=lambda d: d.get(sort_by, 0.0), reverse=True)
        if limit is not None:
            ranked = ranked[:limit]

        logger.info("Active universe: %d pairs (sorted by %s).", len(ranked), sort_by)
        return ranked

    @staticmethod
    def to_bus_pairs(ranked: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [{k: v for k, v in item.items() if k != "ohlc_4h"} for item in ranked]


if __name__ == "__main__":
    logger.info("=== PairUniverse demo ===")
    pu = PairUniverse()

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
    lean = PairUniverse.to_bus_pairs(results)
    print(f"\nRanked {len(lean)} demo pairs (no signal_bus write in demo mode)")
