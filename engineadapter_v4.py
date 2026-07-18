"""engineadapter_v4.py — bridges legacy specialist generate() to orchestrator observe()."""
from __future__ import annotations

import logging
from typing import Any, Dict

import pandas as pd

logger = logging.getLogger("engineadapterv4")

REGIME_ALLOWED: Dict[str, list] = {
    "S1":  ["TREND_UP", "TRENDUP", "TREND_DOWN", "TRENDDOWN"],
    "S2":  ["TREND_UP", "TRENDUP", "TREND_DOWN", "TRENDDOWN"],
    "S3":  ["VOLATILE",  "TREND_DOWN", "TRENDDOWN", "TREND_UP", "TRENDUP"],
    "S4":  ["RANGE"],
    "S5":  ["TREND_UP", "TRENDUP", "TREND_DOWN", "TRENDDOWN"],
    "S6":  ["RANGE", "FEAR", "TREND_DOWN", "TRENDDOWN"],
    "S7":  ["RANGE"],
    "S9":  ["FEAR", "TREND_DOWN", "TRENDDOWN"],
    "S10": ["RANGE", "VOLATILE", "FEAR", "TREND_DOWN", "TRENDDOWN"],
    # RTS runs in ALL regimes — liquidity sweeps happen everywhere
    "RTS_LIQ":    [],
    "RTS_BOS":    [],
    "RTS_CHOCH":  [],
    "RTS_ZONE":   [],
    "RTS_DELTA":  [],
    "RTS_BOTTLE": [],
}

OHLC_COLS = ["timestamp", "open", "high", "low", "close", "vwap", "volume", "count"]


def _parse_ohlc(raw) -> pd.DataFrame | None:
    """Convert ohlc_4h (list of lists) or existing DataFrame to a labeled DataFrame."""
    if raw is None:
        return None
    if isinstance(raw, pd.DataFrame):
        return raw
    try:
        rows = list(raw)
        if not rows:
            return None
        # Kraken OHLC row: [timestamp, open, high, low, close, vwap, volume, count]
        ncols = len(rows[0]) if rows else 0
        cols = OHLC_COLS[:ncols]
        df = pd.DataFrame(rows, columns=cols)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        return df
    except Exception as exc:
        logger.debug("OHLC parse failed: %s", exc)
        return None


class EngineSpecialistAdapter:
    """Wrap a legacy engine specialist so the orchestrator can call observe()."""

    def __init__(self, name: str, engine: Any) -> None:
        self.name   = name
        self.engine = engine

    def observe(self, context: Any, shared_state: Dict[str, Any]) -> Any:
        pair    = getattr(context, "pair", "UNKNOWN")
        regime  = getattr(context, "market_regime", "unknown")
        fg_score = shared_state.get("fgscore", 50)

        # Regime gate
        allowed = REGIME_ALLOWED.get(self.name, [])
        if allowed and regime.upper() not in [r.upper() for r in allowed]:
            return None

        # Resolve OHLC: metadata["ohlc_4h"] is a list of lists from analyze_pair
        metadata = getattr(context, "metadata", {}) or {}
        indicators = getattr(context, "indicators", {}) or {}

        raw_ohlc = (
            metadata.get("ohlc_4h")
            or indicators.get("ohlc_df")
            or indicators.get("ohlc_4h")
        )
        ohlc_df = _parse_ohlc(raw_ohlc)

        if ohlc_df is None or len(ohlc_df) < 14:
            logger.debug("%s skipped %s — no OHLC (%s rows)", self.name, pair,
                         len(ohlc_df) if ohlc_df is not None else "None")
            return None

        # Try calling the underlying engine
        for method_name in ("generate", "observe", "scan", "run"):
            method = getattr(self.engine, method_name, None)
            if method is None:
                continue
            try:
                if method_name == "generate":
                    return method(pair, ohlc_df, regime, fg_score)
                elif method_name in ("observe",):
                    return method(context=context, shared_state=shared_state)
                else:
                    return method(context=context, shared_state=shared_state)
            except TypeError:
                try:
                    return method(pair, ohlc_df, regime, fg_score)
                except Exception as exc:
                    logger.warning("%s.%s fallback failed for %s: %s",
                                   self.name, method_name, pair, exc)
                    return None
            except Exception as exc:
                logger.warning("%s.%s failed for %s: %s",
                               self.name, method_name, pair, exc)
                return None

        logger.warning("%s: no callable method found on %s", self.name, type(self.engine).__name__)
        return None
