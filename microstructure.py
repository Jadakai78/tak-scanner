"""microstructure.py — V2 raw feature extraction for JHL scoring system.

Phase 1: non-destructive. Attaches raw microstructure fields to every signal
dict. These feed the V2 defensive/offensive scorer without touching live
execution. One function: enrich(raw, df, active_pairs) → raw (mutated).

All outputs are normalized 0.0-1.0 floats or simple booleans/floats.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("microstructure")


# ── helpers ──────────────────────────────────────────────────────────────────

def _safe(val: float, default: float = 0.5) -> float:
    """Return default if val is NaN/None/inf."""
    if val is None:
        return default
    try:
        f = float(val)
        return default if (np.isnan(f) or np.isinf(f)) else float(np.clip(f, 0.0, 1.0))
    except (TypeError, ValueError):
        return default


def _pct_rank(val: float, universe: List[float]) -> float:
    """Percentile rank of val within universe list."""
    if not universe:
        return 0.5
    below = sum(1 for v in universe if v < val)
    return below / len(universe)


# ── sweep / stop-hunt detection ───────────────────────────────────────────────

def _sweep_features(df: pd.DataFrame, bias: str, lookback: int = 20) -> Dict[str, Any]:
    """Detect wick-through + close-back-inside (sweep) on recent bars.

    Returns:
        sweep_detected (bool)
        sweep_side     ('bull'|'bear'|None)
        sweep_depth    0-1  (how deep the wick went past the level)
        reclaim_close_ratio  0-1  (how strongly price closed back inside)
        acceptance_bars  int  (how many bars held after reclaim)
    """
    out = {
        "sweep_detected": False,
        "sweep_side": None,
        "sweep_depth": 0.0,
        "reclaim_close_ratio": 0.0,
        "acceptance_bars": 0,
    }
    if len(df) < lookback + 2:
        return out

    window = df.tail(lookback + 5).copy()
    highs = window["high"].values
    lows  = window["low"].values
    closes = window["close"].values
    opens  = window["open"].values

    # Rolling high/low of the lookback window (excluding last 3 bars = the sweep zone)
    ref_high = np.max(highs[:-3])
    ref_low  = np.min(lows[:-3])
    atr = window["high"].sub(window["low"]).rolling(14).mean().iloc[-1]
    if atr <= 0:
        return out

    # Check last 3 bars for a sweep + reclaim
    for i in range(-3, 0):
        bar_high = highs[i]
        bar_low  = lows[i]
        bar_close = closes[i]
        bar_open  = opens[i]

        # Bull sweep: wick below ref_low, closes back above it
        if bar_low < ref_low and bar_close > ref_low:
            depth = min((ref_low - bar_low) / atr, 1.0)
            reclaim = min((bar_close - ref_low) / atr, 1.0)
            accept = int(sum(1 for c in closes[i:] if c > ref_low))
            out.update({
                "sweep_detected": True,
                "sweep_side": "bull",
                "sweep_depth": round(float(depth), 3),
                "reclaim_close_ratio": round(float(reclaim), 3),
                "acceptance_bars": accept,
            })
            break

        # Bear sweep: wick above ref_high, closes back below it
        if bar_high > ref_high and bar_close < ref_high:
            depth = min((bar_high - ref_high) / atr, 1.0)
            reclaim = min((ref_high - bar_close) / atr, 1.0)
            accept = int(sum(1 for c in closes[i:] if c < ref_high))
            out.update({
                "sweep_detected": True,
                "sweep_side": "bear",
                "sweep_depth": round(float(depth), 3),
                "reclaim_close_ratio": round(float(reclaim), 3),
                "acceptance_bars": accept,
            })
            break

    return out


# ── absorption detection ──────────────────────────────────────────────────────

def _absorption_features(df: pd.DataFrame, lookback: int = 10) -> Dict[str, Any]:
    """High volume + low price progress = absorption.

    Returns:
        absorption_count       int  (bars with high vol + low progress)
        absorption_volume_ratio  0-1  (avg vol of absorption bars vs mean)
    """
    out = {"absorption_count": 0, "absorption_volume_ratio": 0.5}
    if len(df) < lookback + 2 or "volume" not in df.columns:
        return out

    window = df.tail(lookback).copy()
    mean_vol = window["volume"].mean()
    if mean_vol <= 0:
        return out
    atr = window["high"].sub(window["low"]).rolling(14).mean().iloc[-1]
    if atr <= 0:
        return out

    absorption_bars = []
    for _, row in window.iterrows():
        vol_ratio = row["volume"] / mean_vol
        progress = abs(row["close"] - row["open"]) / atr
        # High volume (>1.5x mean) but small price move (<0.4 ATR) = absorption
        if vol_ratio > 1.5 and progress < 0.4:
            absorption_bars.append(vol_ratio)

    count = len(absorption_bars)
    avg_vol_ratio = float(np.mean(absorption_bars)) if absorption_bars else 0.0
    out["absorption_count"] = count
    out["absorption_volume_ratio"] = round(min(avg_vol_ratio / 3.0, 1.0), 3)
    return out


# ── displacement / impulse quality ───────────────────────────────────────────

def _displacement_features(df: pd.DataFrame, bias: str, lookback: int = 5) -> Dict[str, Any]:
    """Rate the strength of the recent impulse leg.

    Returns:
        impulse_body_ratio   0-1  (body vs range of the impulse bar)
        follow_through_ratio 0-1  (continuation after impulse)
        displacement_quality 0-1  (composite)
    """
    out = {"impulse_body_ratio": 0.5, "follow_through_ratio": 0.5, "displacement_quality": 0.5}
    if len(df) < lookback + 2:
        return out

    window = df.tail(lookback).copy()
    atr = window["high"].sub(window["low"]).rolling(14, min_periods=3).mean().iloc[-1]
    if atr <= 0:
        return out

    # Find the strongest bar in the window in the bias direction
    if bias == "LONG":
        window["bar_move"] = window["close"] - window["open"]
    else:
        window["bar_move"] = window["open"] - window["close"]

    best_idx = window["bar_move"].idxmax()
    best_bar = window.loc[best_idx]
    bar_range = best_bar["high"] - best_bar["low"]

    body = abs(best_bar["close"] - best_bar["open"])
    body_ratio = body / bar_range if bar_range > 0 else 0.5

    # Follow-through: bars after impulse continuing in same direction
    after = window.loc[best_idx:].tail(3)
    if bias == "LONG":
        ft = sum(1 for _, r in after.iterrows() if r["close"] > r["open"])
    else:
        ft = sum(1 for _, r in after.iterrows() if r["close"] < r["open"])
    ft_ratio = ft / max(len(after), 1)

    disp = (body_ratio * 0.6 + ft_ratio * 0.4)
    out.update({
        "impulse_body_ratio": round(float(body_ratio), 3),
        "follow_through_ratio": round(float(ft_ratio), 3),
        "displacement_quality": round(float(disp), 3),
    })
    return out


# ── path of least resistance (inefficiency) ──────────────────────────────────

def _path_features(df: pd.DataFrame, entry: float, tp: float, bias: str,
                   lookback: int = 30) -> Dict[str, Any]:
    """Count structure obstacles between entry and TP.

    Returns:
        path_obstacles_count  int  (significant swing points in the path)
        inefficiency_path     0-1  (1 = clean air, 0 = lots of friction)
    """
    out = {"path_obstacles_count": 0, "inefficiency_path": 0.5}
    if len(df) < lookback or entry is None or tp is None:
        return out
    if entry == tp:
        return out

    window = df.tail(lookback).copy()
    lo, hi = (entry, tp) if bias == "LONG" else (tp, entry)

    # Swing highs/lows inside the path zone
    obstacles = 0
    for i in range(1, len(window) - 1):
        h = window["high"].iloc[i]
        l = window["low"].iloc[i]
        prev_h = window["high"].iloc[i - 1]
        prev_l = window["low"].iloc[i - 1]
        next_h = window["high"].iloc[i + 1]
        next_l = window["low"].iloc[i + 1]

        # Swing high inside path
        if lo < h < hi and h > prev_h and h > next_h:
            obstacles += 1
        # Swing low inside path
        if lo < l < hi and l < prev_l and l < next_l:
            obstacles += 1

    path_score = max(0.0, 1.0 - obstacles / 8.0)  # 8+ obstacles = fully blocked
    out["path_obstacles_count"] = obstacles
    out["inefficiency_path"] = round(float(path_score), 3)
    return out


# ── volatility compression / release ─────────────────────────────────────────

def _compression_features(df: pd.DataFrame, lookback: int = 20) -> Dict[str, Any]:
    """Detect BB squeeze / ATR compression ready to release.

    Returns:
        compression_ratio  0-1  (1 = maximum compression, 0 = expanded)
    """
    out = {"compression_ratio": 0.5}
    if len(df) < lookback + 2:
        return out
    window = df.tail(lookback).copy()
    atr_series = window["high"].sub(window["low"]).rolling(14).mean()
    if atr_series.isna().all():
        return out
    recent_atr = atr_series.iloc[-3:].mean()
    long_atr = atr_series.mean()
    if long_atr <= 0:
        return out
    ratio = recent_atr / long_atr  # < 1 = compressed, > 1 = expanded
    # Compress to 0-1: 0.3 ratio = very compressed (near 1.0), 1.5 = expanded (near 0.0)
    compression = max(0.0, min(1.0, (1.5 - ratio) / 1.2))
    out["compression_ratio"] = round(float(compression), 3)
    return out


# ── relative leadership ───────────────────────────────────────────────────────

def _leadership_features(pair: str, atr_pct: float, volume_ratio: float,
                          active_pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compare this pair's ATR% and volume rank against the active universe.

    Returns:
        relative_atr_rank     0-1  (percentile in universe)
        relative_volume_rank  0-1  (percentile in universe)
        relative_leadership   0-1  (composite)
    """
    out = {"relative_atr_rank": 0.5, "relative_volume_rank": 0.5, "relative_leadership": 0.5}
    if not active_pairs:
        return out

    atrs = [p.get("atr_pct", 0.0) for p in active_pairs if p.get("atr_pct")]
    vols = [p.get("volume_ratio", 0.0) for p in active_pairs if p.get("volume_ratio")]

    atr_rank = _pct_rank(atr_pct, atrs)
    vol_rank  = _pct_rank(volume_ratio, vols)
    leadership = atr_rank * 0.6 + vol_rank * 0.4

    out.update({
        "relative_atr_rank": round(atr_rank, 3),
        "relative_volume_rank": round(vol_rank, 3),
        "relative_leadership": round(leadership, 3),
    })
    return out


# ── liquidation magnet ────────────────────────────────────────────────────────

def _liquidation_features(df: pd.DataFrame, entry: float, bias: str,
                           lookback: int = 50) -> Dict[str, Any]:
    """Estimate proximity to equal highs/lows and prior session extremes.

    Returns:
        equal_highs_distance    0-1  (0 = right at equal highs)
        equal_lows_distance     0-1  (0 = right at equal lows)
        liquidation_cluster_distance  0-1  (nearest magnet)
    """
    out = {"equal_highs_distance": 1.0, "equal_lows_distance": 1.0,
           "liquidation_cluster_distance": 1.0}
    if len(df) < 10 or entry is None or entry <= 0:
        return out

    window = df.tail(lookback).copy()
    atr = window["high"].sub(window["low"]).rolling(14).mean().iloc[-1]
    if atr <= 0:
        return out

    highs = window["high"].values
    lows  = window["low"].values

    # Equal highs: cluster of highs within 0.3 ATR of each other
    high_clusters = []
    for i in range(len(highs)):
        cluster = [h for h in highs if abs(h - highs[i]) < atr * 0.3]
        if len(cluster) >= 2:
            high_clusters.append(float(np.mean(cluster)))

    low_clusters = []
    for i in range(len(lows)):
        cluster = [l for l in lows if abs(l - lows[i]) < atr * 0.3]
        if len(cluster) >= 2:
            low_clusters.append(float(np.mean(cluster)))

    def _dist(level: float) -> float:
        return min(abs(entry - level) / (atr * 5), 1.0)

    eh_dist = min([_dist(h) for h in high_clusters], default=1.0)
    el_dist = min([_dist(l) for l in low_clusters], default=1.0)
    nearest = min(eh_dist, el_dist)

    out.update({
        "equal_highs_distance": round(float(eh_dist), 3),
        "equal_lows_distance": round(float(el_dist), 3),
        "liquidation_cluster_distance": round(float(nearest), 3),
    })
    return out


# ── public API ────────────────────────────────────────────────────────────────

def enrich(
    raw: Dict[str, Any],
    df: pd.DataFrame,
    active_pairs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Attach all V2 raw microstructure features to a signal dict.

    Mutates and returns raw. Safe to call even if df is short — each
    sub-function handles insufficient data gracefully.
    """
    bias   = str(raw.get("bias", "LONG")).upper()
    entry  = raw.get("entry")
    tp     = raw.get("tp")
    pair   = raw.get("pair", "")
    atr_pct = raw.get("atr_pct", 0.0)
    volume_ratio = raw.get("volume_ratio", 1.0)

    try:
        sweep   = _sweep_features(df, bias)
        absorb  = _absorption_features(df)
        displace = _displacement_features(df, bias)
        path    = _path_features(df, entry, tp, bias)
        compress = _compression_features(df)
        lead    = _leadership_features(pair, atr_pct, volume_ratio, active_pairs or [])
        liq     = _liquidation_features(df, entry, bias)
    except Exception as exc:
        logger.warning("microstructure.enrich failed for %s: %s", pair, exc)
        return raw

    raw.update({
        # Sweep / stop-hunt
        "sweep_detected": sweep["sweep_detected"],
        "sweep_side": sweep["sweep_side"],
        "sweep_depth": sweep["sweep_depth"],
        "reclaim_close_ratio": sweep["reclaim_close_ratio"],
        "acceptance_bars": sweep["acceptance_bars"],
        # Absorption
        "absorption_count": absorb["absorption_count"],
        "absorption_volume_ratio": absorb["absorption_volume_ratio"],
        # Displacement
        "impulse_body_ratio": displace["impulse_body_ratio"],
        "follow_through_ratio": displace["follow_through_ratio"],
        "displacement_quality": displace["displacement_quality"],
        # Path
        "path_obstacles_count": path["path_obstacles_count"],
        "inefficiency_path": path["inefficiency_path"],
        # Compression
        "compression_ratio": compress["compression_ratio"],
        # Leadership
        "relative_atr_rank": lead["relative_atr_rank"],
        "relative_volume_rank": lead["relative_volume_rank"],
        "relative_leadership": lead["relative_leadership"],
        # Liquidation magnets
        "equal_highs_distance": liq["equal_highs_distance"],
        "equal_lows_distance": liq["equal_lows_distance"],
        "liquidation_cluster_distance": liq["liquidation_cluster_distance"],
    })

    logger.debug("%s micro | sweep=%s depth=%.2f disp=%.2f path=%.2f compress=%.2f lead=%.2f",
                 pair, sweep["sweep_detected"], sweep["sweep_depth"],
                 displace["displacement_quality"], path["inefficiency_path"],
                 compress["compression_ratio"], lead["relative_leadership"])
    return raw
