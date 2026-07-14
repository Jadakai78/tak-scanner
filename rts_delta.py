"""rts_delta.py — RTS-DELTA: Sponsorship & Volume Profile confirmer.

Confirms whether buyers or sellers are actually backing the move.
Derives delta_bias, sponsorship_quality, and vp_context from OHLC proxies
(real delta/VPOC requires tick data; we use volume-weighted approximations).

This engine is a CONFIRMER — it does not generate standalone signals.
It is called as an overlay (like S8) to boost or downgrade intent from
other RTS engines. It also runs standalone to filter low-sponsorship ideas.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import numpy as np

try:
    from ._common import atr as calc_atr, volume_ratio as calc_vol_ratio
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import atr as calc_atr, volume_ratio as calc_vol_ratio  # type: ignore

logger = logging.getLogger("strategies.rts_delta")

ATR_PERIOD      = 14
DELTA_MIN_PCT   = 0.60   # 60/40 split = BUY or SELL dominant
VPOC_BARS       = 40     # bars for volume profile calculation
HVN_THRESHOLD   = 1.5    # bars with volume > 1.5× average are HVN nodes


def _atr(df: pd.DataFrame) -> float:
    try:
        return float(calc_atr(df, ATR_PERIOD).iloc[-1])
    except Exception:
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"] - df["close"].shift()).abs(),
        ], axis=1).max(axis=1)
        return float(tr.iloc[-ATR_PERIOD:].mean())


def _estimate_delta(df: pd.DataFrame, bars: int = 5) -> Dict[str, Any]:
    """
    Estimate buy/sell volume split from OHLC.

    Approximation: if close > open → bullish bar (buy pressure),
    weighted by volume. Gives us a rough delta proxy without tick data.
    """
    recent = df.iloc[-bars:].copy()
    if "volume" not in recent.columns:
        return {"buy_vol": 0.5, "sell_vol": 0.5, "total_delta": 0.0, "delta_bias": "NEUTRAL"}

    total_vol = float(recent["volume"].sum())
    if total_vol <= 0:
        return {"buy_vol": 0.5, "sell_vol": 0.5, "total_delta": 0.0, "delta_bias": "NEUTRAL"}

    # Weight each bar's volume by candle direction and body size
    buy_vol = 0.0
    sell_vol = 0.0
    for _, row in recent.iterrows():
        body = abs(row["close"] - row["open"])
        total_body = row["high"] - row["low"]
        vol = row["volume"] if row["volume"] > 0 else 0
        if total_body > 0:
            ratio = min(1.0, body / total_body)
        else:
            ratio = 0.5
        if row["close"] >= row["open"]:
            buy_vol += vol * (0.5 + ratio * 0.5)
            sell_vol += vol * (0.5 - ratio * 0.5)
        else:
            sell_vol += vol * (0.5 + ratio * 0.5)
            buy_vol += vol * (0.5 - ratio * 0.5)

    total = buy_vol + sell_vol
    buy_pct = buy_vol / total if total > 0 else 0.5
    sell_pct = sell_vol / total if total > 0 else 0.5

    if buy_pct >= DELTA_MIN_PCT:
        bias = "BUY_DOMINANT"
    elif sell_pct >= DELTA_MIN_PCT:
        bias = "SELL_DOMINANT"
    else:
        bias = "NEUTRAL"

    total_delta = buy_vol - sell_vol

    return {
        "buy_vol": buy_pct,
        "sell_vol": sell_pct,
        "total_delta": total_delta,
        "delta_bias": bias,
    }


def _compute_vp(df: pd.DataFrame, atr_val: float) -> Dict[str, Any]:
    """
    Compute simple volume profile: VPOC, VAH, VAL, HVN nodes.
    Uses price midpoint of each bar weighted by volume.
    """
    window = df.iloc[-VPOC_BARS:].copy()
    if "volume" not in window.columns or window["volume"].sum() == 0:
        mid = float(df["close"].iloc[-1])
        return {
            "vpoc": mid, "vah": mid, "val": mid, "hnv": [],
            "vp_context": "PRICE_AT_VPOC",
        }

    # Build price buckets (20 levels)
    price_min = float(window["low"].min())
    price_max = float(window["high"].max())
    if price_max <= price_min:
        mid = (price_max + price_min) / 2
        return {"vpoc": mid, "vah": mid, "val": mid, "hnv": [],
                "vp_context": "PRICE_AT_VPOC"}

    n_buckets = 20
    bucket_size = (price_max - price_min) / n_buckets
    buckets = np.zeros(n_buckets)

    for _, row in window.iterrows():
        mid_price = (row["high"] + row["low"]) / 2
        bucket_idx = min(int((mid_price - price_min) / bucket_size), n_buckets - 1)
        buckets[bucket_idx] += row.get("volume", 1.0)

    vpoc_idx = int(np.argmax(buckets))
    vpoc = price_min + (vpoc_idx + 0.5) * bucket_size

    # VAH/VAL: 70% of volume value area
    total_vol = buckets.sum()
    sorted_idx = np.argsort(buckets)[::-1]
    cum_vol = 0.0
    va_buckets = []
    for idx in sorted_idx:
        cum_vol += buckets[idx]
        va_buckets.append(idx)
        if cum_vol >= 0.70 * total_vol:
            break
    vah = price_min + (max(va_buckets) + 1) * bucket_size
    val = price_min + min(va_buckets) * bucket_size

    # HVN nodes: buckets > threshold × average
    avg_vol = total_vol / n_buckets
    hnv = [
        price_min + (i + 0.5) * bucket_size
        for i in range(n_buckets)
        if buckets[i] > HVN_THRESHOLD * avg_vol
    ]

    # VP context relative to current price
    cur_close = float(df["close"].iloc[-1])
    if cur_close > vah:
        vp_context = "PRICE_ABOVE_VPOC"
    elif cur_close < val:
        vp_context = "PRICE_BELOW_VPOC"
    else:
        vp_context = "PRICE_AT_VPOC"

    return {
        "vpoc": vpoc,
        "vah": vah,
        "val": val,
        "hnv": hnv[:5],  # top 5 HVN nodes
        "vp_context": vp_context,
    }


def score_delta_context(df: pd.DataFrame, bias: str) -> Dict[str, Any]:
    """
    Public function called by other RTS engines to get delta confirmation.
    Returns the full RTS-DELTA context dict to be merged into raw.

    bias: 'LONG' or 'SHORT' from the calling engine
    """
    atr_val = _atr(df)
    delta = _estimate_delta(df)
    vp = _compute_vp(df, atr_val)

    delta_bias = delta["delta_bias"]
    vp_ctx = vp["vp_context"]

    # Sponsorship quality
    aligned = (
        (bias == "LONG" and delta_bias == "BUY_DOMINANT") or
        (bias == "SHORT" and delta_bias == "SELL_DOMINANT")
    )
    misaligned = (
        (bias == "LONG" and delta_bias == "SELL_DOMINANT") or
        (bias == "SHORT" and delta_bias == "BUY_DOMINANT")
    )

    if aligned and abs(delta["total_delta"]) > 0:
        sponsorship_quality = "HIGH"
    elif misaligned:
        sponsorship_quality = "LOW"
    else:
        sponsorship_quality = "MEDIUM"

    # Delta modifier for offence score
    delta_modifier = 0.0
    if sponsorship_quality == "HIGH":
        delta_modifier = +0.08
    elif sponsorship_quality == "LOW":
        delta_modifier = -0.10

    return {
        "delta_bias": delta_bias,
        "delta_buy_pct": round(delta["buy_vol"], 3),
        "delta_sell_pct": round(delta["sell_vol"], 3),
        "total_delta": round(delta["total_delta"], 2),
        "sponsorship_quality": sponsorship_quality,
        "vp_context": vp_ctx,
        "vpoc": round(vp["vpoc"], 6),
        "vah": round(vp["vah"], 6),
        "val": round(vp["val"], 6),
        "hnv": [round(v, 6) for v in vp["hnv"]],
        "delta_modifier": delta_modifier,  # apply to offence_score in calling engine
    }


class RTSDelta:
    """
    RTS-DELTA — standalone sponsorship scanner.

    When run standalone, it only fires if delta is strongly aligned
    with the current price action direction (not a trend engine —
    it confirms a thesis, so standalone use is limited).
    It is most useful as an overlay called by other RTS engines.
    """

    ENGINE = "RTS_DELTA"
    REQUIRED_REGIMES = ["TREND_UP", "TREND_DOWN", "VOLATILE", "RANGE", "FEAR"]

    def generate(
        self,
        pair: str,
        ohlc_df: pd.DataFrame,
        regime: str,
        fg_score: int,
        ai_st: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Standalone: only emit context fields, no full signal.
        Other RTS engines call score_delta_context() directly.
        Return None to avoid duplicate signals — DELTA is an overlay.
        """
        return None
