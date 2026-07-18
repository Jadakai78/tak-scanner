"""microstructure.py — V2 raw feature extraction for JHL scoring system.

Phase 1: non-destructive. Attaches raw microstructure fields to every signal
dict. These feed the V2 defensive/offensive scorer without touching live
execution. One function: enrich(raw, df, active_pairs) → raw (mutated).

All outputs are normalized 0.0-1.0 floats or simple booleans/floats.

V2 additions:
  - FAKEOUT_PROBABILITY    — displacement with no acceptance/follow-through
  - LIQUIDATION_CHAIN_POTENTIAL — clustered equal levels ahead, chain squeeze
  - ORDER_BLOCK_QUALITY    — SMC: last opposing candle before displacement (OB)
  - FVG_PATH_SCORE         — ICT Fair Value Gaps in path → cleaner air reading
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger("microstructure")


# ── helpers ───────────────────────────────────────────────────────────────────

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


def _atr_val(df: pd.DataFrame, period: int = 14) -> float:
    """Return current ATR. Shared helper."""
    try:
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"]  - df["close"].shift()).abs(),
        ], axis=1).max(axis=1)
        v = float(tr.rolling(period).mean().iloc[-1])
        return v if v > 0 else float(tr.mean())
    except Exception:
        return 0.0


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
    highs  = window["high"].values
    lows   = window["low"].values
    closes = window["close"].values
    opens  = window["open"].values

    ref_high = np.max(highs[:-3])
    ref_low  = np.min(lows[:-3])
    atr = window["high"].sub(window["low"]).rolling(14).mean().iloc[-1]
    if atr <= 0:
        return out

    for i in range(-3, 0):
        bar_high  = highs[i]
        bar_low   = lows[i]
        bar_close = closes[i]

        # Bull sweep: wick below ref_low, closes back above it
        if bar_low < ref_low and bar_close > ref_low:
            depth   = min((ref_low - bar_low) / atr, 1.0)
            reclaim = min((bar_close - ref_low) / atr, 1.0)
            accept  = int(sum(1 for c in closes[i:] if c > ref_low))
            out.update({
                "sweep_detected": True, "sweep_side": "bull",
                "sweep_depth": round(float(depth), 3),
                "reclaim_close_ratio": round(float(reclaim), 3),
                "acceptance_bars": accept,
            })
            break

        # Bear sweep: wick above ref_high, closes back below it
        if bar_high > ref_high and bar_close < ref_high:
            depth   = min((bar_high - ref_high) / atr, 1.0)
            reclaim = min((ref_high - bar_close) / atr, 1.0)
            accept  = int(sum(1 for c in closes[i:] if c < ref_high))
            out.update({
                "sweep_detected": True, "sweep_side": "bear",
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
        absorption_count         int  (bars with high vol + low progress)
        absorption_volume_ratio  0-1  (avg vol of absorption bars vs mean)
    """
    out = {"absorption_count": 0, "absorption_volume_ratio": 0.5}
    if len(df) < lookback + 2 or "volume" not in df.columns:
        return out

    window   = df.tail(lookback).copy()
    mean_vol = window["volume"].mean()
    if mean_vol <= 0:
        return out
    atr = window["high"].sub(window["low"]).rolling(14).mean().iloc[-1]
    if atr <= 0:
        return out

    absorption_bars = []
    for _, row in window.iterrows():
        vol_ratio = row["volume"] / mean_vol
        progress  = abs(row["close"] - row["open"]) / atr
        if vol_ratio > 1.5 and progress < 0.4:
            absorption_bars.append(vol_ratio)

    count = len(absorption_bars)
    avg_vol_ratio = float(np.mean(absorption_bars)) if absorption_bars else 0.0
    out["absorption_count"]       = count
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

    window["bar_move"] = (window["close"] - window["open"]) if bias == "LONG" \
                         else (window["open"] - window["close"])

    best_idx = window["bar_move"].idxmax()
    best_bar = window.loc[best_idx]
    bar_range = best_bar["high"] - best_bar["low"]
    body      = abs(best_bar["close"] - best_bar["open"])
    body_ratio = body / bar_range if bar_range > 0 else 0.5

    after = window.loc[best_idx:].tail(3)
    if bias == "LONG":
        ft = sum(1 for _, r in after.iterrows() if r["close"] > r["open"])
    else:
        ft = sum(1 for _, r in after.iterrows() if r["close"] < r["open"])
    ft_ratio = ft / max(len(after), 1)

    disp = body_ratio * 0.6 + ft_ratio * 0.4
    out.update({
        "impulse_body_ratio":   round(float(body_ratio), 3),
        "follow_through_ratio": round(float(ft_ratio), 3),
        "displacement_quality": round(float(disp), 3),
    })
    return out


# ── path of least resistance (swing obstacles) ───────────────────────────────

def _path_features(df: pd.DataFrame, entry: float, tp: float, bias: str,
                   lookback: int = 30) -> Dict[str, Any]:
    """Count structure obstacles between entry and TP.

    Returns:
        path_obstacles_count  int  (significant swing points in the path)
        inefficiency_path     0-1  (1 = clean air, 0 = lots of friction)
    """
    out = {"path_obstacles_count": 0, "inefficiency_path": 0.5}
    if len(df) < lookback or entry is None or tp is None or entry == tp:
        return out

    window = df.tail(lookback).copy()
    lo, hi = (entry, tp) if bias == "LONG" else (tp, entry)

    obstacles = 0
    for i in range(1, len(window) - 1):
        h      = window["high"].iloc[i]
        l      = window["low"].iloc[i]
        prev_h = window["high"].iloc[i - 1]
        prev_l = window["low"].iloc[i - 1]
        next_h = window["high"].iloc[i + 1]
        next_l = window["low"].iloc[i + 1]

        if lo < h < hi and h > prev_h and h > next_h:
            obstacles += 1
        if lo < l < hi and l < prev_l and l < next_l:
            obstacles += 1

    path_score = max(0.0, 1.0 - obstacles / 8.0)
    out["path_obstacles_count"] = obstacles
    out["inefficiency_path"]    = round(float(path_score), 3)
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
    window     = df.tail(lookback).copy()
    atr_series = window["high"].sub(window["low"]).rolling(14).mean()
    if atr_series.isna().all():
        return out
    recent_atr = atr_series.iloc[-3:].mean()
    long_atr   = atr_series.mean()
    if long_atr <= 0:
        return out
    ratio       = recent_atr / long_atr
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

    atr_rank   = _pct_rank(atr_pct, atrs)
    vol_rank   = _pct_rank(volume_ratio, vols)
    leadership = atr_rank * 0.6 + vol_rank * 0.4

    out.update({
        "relative_atr_rank":   round(atr_rank, 3),
        "relative_volume_rank": round(vol_rank, 3),
        "relative_leadership":  round(leadership, 3),
    })
    return out


# ── liquidation magnet ────────────────────────────────────────────────────────

def _liquidation_features(df: pd.DataFrame, entry: float, bias: str,
                           lookback: int = 50) -> Dict[str, Any]:
    """Estimate proximity to equal highs/lows and prior session extremes.

    Returns:
        equal_highs_distance          0-1  (0 = right at equal highs)
        equal_lows_distance           0-1  (0 = right at equal lows)
        liquidation_cluster_distance  0-1  (nearest magnet)
    """
    out = {"equal_highs_distance": 1.0, "equal_lows_distance": 1.0,
           "liquidation_cluster_distance": 1.0}
    if len(df) < 10 or entry is None or entry <= 0:
        return out

    window = df.tail(lookback).copy()
    atr    = window["high"].sub(window["low"]).rolling(14).mean().iloc[-1]
    if atr <= 0:
        return out

    highs = window["high"].values
    lows  = window["low"].values

    high_clusters: List[float] = []
    for i in range(len(highs)):
        cluster = [h for h in highs if abs(h - highs[i]) < atr * 0.3]
        if len(cluster) >= 2:
            high_clusters.append(float(np.mean(cluster)))

    low_clusters: List[float] = []
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
        "equal_highs_distance":        round(float(eh_dist), 3),
        "equal_lows_distance":         round(float(el_dist), 3),
        "liquidation_cluster_distance": round(float(nearest), 3),
    })
    return out


# ── NEW: FAKEOUT PROBABILITY ──────────────────────────────────────────────────

def _fakeout_features(df: pd.DataFrame, bias: str, lookback: int = 15) -> Dict[str, Any]:
    """Detect displacement that failed to produce acceptance or follow-through.

    A fakeout is NOT the same as trap_risk (which is about being IN trap terrain).
    A fakeout is: the move already happened, looked real, but then died.

    Signals:
      - Large impulse bar in bias direction (body >= 0.6 ATR)
      - But close of following 2-3 bars reverses back into the impulse bar body
      - OR volume collapses immediately after the impulse (participation gone)
      - OR sweep happened but reclaim_close_ratio is very low (snap-back missing)

    Returns:
        fakeout_probability  0-1  (1 = very likely failed move)
        fakeout_signature    str  (description of detected pattern)
    """
    out = {"fakeout_probability": 0.0, "fakeout_signature": "none"}
    if len(df) < lookback + 2:
        return out

    window = df.tail(lookback).copy()
    atr    = _atr_val(window)
    if atr <= 0:
        return out

    highs  = window["high"].values
    lows   = window["low"].values
    closes = window["close"].values
    opens  = window["open"].values
    vols   = window["volume"].values if "volume" in window.columns else np.ones(len(window))

    mean_vol = float(np.mean(vols[:-3])) if len(vols) > 3 else 1.0

    score = 0.0
    sigs  = []

    # ── Pattern 1: large impulse bar → immediate reversal into body ───────────
    for i in range(len(window) - 3):
        body  = abs(closes[i] - opens[i])
        rng   = highs[i] - lows[i]
        if body < 0.55 * atr:
            continue  # not a meaningful impulse bar

        is_bull = closes[i] > opens[i]
        is_aligned = (bias == "LONG" and is_bull) or (bias == "SHORT" and not is_bull)
        if not is_aligned:
            continue

        # Check next 2 bars: did price close back inside the impulse body?
        impulse_top = max(closes[i], opens[i])
        impulse_bot = min(closes[i], opens[i])
        reversal_count = 0
        for j in range(i + 1, min(i + 3, len(window))):
            if bias == "LONG" and closes[j] < impulse_bot:
                reversal_count += 1
            elif bias == "SHORT" and closes[j] > impulse_top:
                reversal_count += 1

        if reversal_count >= 1:
            score += 0.40
            sigs.append("impulse_reversal")

        # ── Pattern 2: volume collapse after impulse ──────────────────────────
        if mean_vol > 0 and i + 1 < len(vols):
            next_vol = float(vols[i + 1])
            if next_vol < mean_vol * 0.50:
                score += 0.25
                sigs.append("volume_collapse_post_impulse")

    # ── Pattern 3: sweep with no reclaim (empty reclaim_close_ratio proxy) ────
    sweep = _sweep_features(df, bias, lookback=lookback)
    if sweep["sweep_detected"]:
        rcr = sweep["reclaim_close_ratio"]
        if rcr < 0.20:
            score += 0.35
            sigs.append("sweep_no_reclaim")
        elif rcr < 0.45:
            score += 0.15
            sigs.append("weak_reclaim")

    # ── Pattern 4: acceptance bars = 0 after a detected sweep ────────────────
    if sweep["sweep_detected"] and sweep["acceptance_bars"] == 0:
        score += 0.20
        sigs.append("zero_acceptance_bars")

    fakeout_prob = round(min(float(score), 1.0), 3)
    signature    = "+".join(sigs) if sigs else "none"

    out.update({
        "fakeout_probability": fakeout_prob,
        "fakeout_signature":   signature,
    })
    return out


# ── NEW: LIQUIDATION CHAIN POTENTIAL ─────────────────────────────────────────

def _liquidation_chain_features(
    df: pd.DataFrame, entry: float, bias: str, lookback: int = 60
) -> Dict[str, Any]:
    """Estimate whether a move can trigger a cascading chain of liquidations.

    Logic: multiple clustered stop levels ahead of price in the bias direction.
    When price hits the first cluster, forced exits push it into the next one.

    Returns:
        liquidation_chain_potential  0-1
        chain_level_count            int  (clusters ahead in bias direction)
        nearest_chain_level          float (price of nearest cluster)
    """
    out = {
        "liquidation_chain_potential": 0.0,
        "chain_level_count": 0,
        "nearest_chain_level": 0.0,
    }
    if len(df) < 20 or entry is None or entry <= 0:
        return out

    window = df.tail(lookback).copy()
    atr    = _atr_val(window)
    if atr <= 0:
        return out

    highs = window["high"].values
    lows  = window["low"].values

    # Build clusters of equal highs (buy-side stops) and equal lows (sell-side)
    # A cluster = 3+ pivots within 0.4 ATR of each other
    tol = atr * 0.4

    def _build_clusters(prices: np.ndarray) -> List[float]:
        clusters = []
        visited  = set()
        for i in range(len(prices)):
            if i in visited:
                continue
            group = [j for j in range(len(prices)) if abs(prices[i] - prices[j]) <= tol]
            if len(group) >= 3:
                clusters.append(float(np.mean([prices[j] for j in group])))
                visited.update(group)
        return sorted(set(round(c, 8) for c in clusters))

    high_clusters = _build_clusters(highs)
    low_clusters  = _build_clusters(lows)

    # For LONG bias: stops above entry (equal highs) = chain potential upward
    # For SHORT bias: stops below entry (equal lows) = chain potential downward
    if bias == "LONG":
        ahead = [c for c in high_clusters if c > entry]
    else:
        ahead = [c for c in low_clusters if c < entry]

    chain_count = len(ahead)
    nearest     = min(ahead, key=lambda c: abs(c - entry), default=0.0) if ahead else 0.0

    # Score: more clusters ahead + closer proximity = higher chain potential
    if chain_count == 0:
        chain_score = 0.0
    elif chain_count == 1:
        prox = max(0.0, 1.0 - abs(entry - nearest) / (atr * 8))
        chain_score = 0.35 + prox * 0.25
    elif chain_count == 2:
        prox = max(0.0, 1.0 - abs(entry - nearest) / (atr * 8))
        chain_score = 0.55 + prox * 0.20
    else:
        prox = max(0.0, 1.0 - abs(entry - nearest) / (atr * 8))
        chain_score = 0.75 + prox * 0.15

    out.update({
        "liquidation_chain_potential": round(min(float(chain_score), 1.0), 3),
        "chain_level_count":           chain_count,
        "nearest_chain_level":         round(float(nearest), 6),
    })
    return out


# ── NEW: ORDER BLOCK QUALITY (SMC depth) ─────────────────────────────────────

def _order_block_features(df: pd.DataFrame, bias: str, lookback: int = 40) -> Dict[str, Any]:
    """Detect the ICT-style Order Block nearest to current price.

    An Order Block (OB) is the LAST opposing candle before a displacement move:
      - Bull OB: last DOWN candle before a bullish BOS / impulse upward
      - Bear OB: last UP candle before a bearish BOS / impulse downward

    Mitigation rule (SMC): price returning into the OB body = first touch valid,
    second touch = OB dead. Here we detect:
      - ob_detected     (bool)
      - ob_quality      0-1   (fresh OB near current price scores high)
      - ob_mitigated    (bool) (price already returned = partially spent)
      - ob_level_top    float
      - ob_level_bot    float
      - ob_distance     0-1   (0 = price is at OB, 1 = far away)

    Returns:
        ob_detected      bool
        ob_quality       0-1
        ob_mitigated     bool
        ob_level_top     float
        ob_level_bot     float
        ob_distance      0-1
    """
    out = {
        "ob_detected": False, "ob_quality": 0.0,
        "ob_mitigated": False,
        "ob_level_top": 0.0, "ob_level_bot": 0.0,
        "ob_distance": 1.0,
    }
    if len(df) < 10:
        return out

    window = df.tail(lookback).copy().reset_index(drop=True)
    atr    = _atr_val(window)
    if atr <= 0:
        return out

    closes = window["close"].values
    opens  = window["open"].values
    highs  = window["high"].values
    lows   = window["low"].values
    n      = len(window)

    # Find the most recent significant displacement (body >= 0.6 ATR in bias direction)
    displacement_idx = None
    for i in range(n - 1, 1, -1):
        body = closes[i] - opens[i]
        if bias == "LONG"  and body >= 0.60 * atr:
            displacement_idx = i; break
        if bias == "SHORT" and body <= -0.60 * atr:
            displacement_idx = i; break

    if displacement_idx is None or displacement_idx == 0:
        return out

    # OB = the last OPPOSING candle before displacement_idx
    ob_idx = None
    for i in range(displacement_idx - 1, -1, -1):
        body = closes[i] - opens[i]
        if bias == "LONG"  and body < 0:   # last red candle before green impulse
            ob_idx = i; break
        if bias == "SHORT" and body > 0:   # last green candle before red impulse
            ob_idx = i; break

    if ob_idx is None:
        return out

    ob_top = float(max(opens[ob_idx], closes[ob_idx]))
    ob_bot = float(min(opens[ob_idx], closes[ob_idx]))

    cur_close = float(closes[-1])
    cur_low   = float(lows[-1])
    cur_high  = float(highs[-1])

    # Distance from current price to OB
    if bias == "LONG":
        dist_raw = max(0.0, cur_close - ob_top)   # price above OB = distance
    else:
        dist_raw = max(0.0, ob_bot - cur_close)   # price below OB = distance
    ob_dist = min(dist_raw / (atr * 6), 1.0)

    # Mitigation: has price re-entered the OB body after the displacement?
    mitigated = False
    for i in range(displacement_idx + 1, n):
        if ob_bot <= closes[i] <= ob_top:
            mitigated = True
            break
        if ob_bot <= lows[i] <= ob_top or ob_bot <= highs[i] <= ob_top:
            mitigated = True
            break

    # OB quality: freshness + proximity + size relative to ATR
    ob_body = ob_top - ob_bot
    size_score    = min(ob_body / atr, 1.0)     # bigger OB body = more significant
    recency_score = max(0.0, 1.0 - (n - 1 - ob_idx) / max(lookback, 1))  # more recent = better
    prox_score    = 1.0 - ob_dist

    quality = size_score * 0.3 + recency_score * 0.3 + prox_score * 0.4
    if mitigated:
        quality *= 0.40   # mitigated OB is stale — dock heavily

    out.update({
        "ob_detected":   True,
        "ob_quality":    round(float(quality), 3),
        "ob_mitigated":  mitigated,
        "ob_level_top":  round(ob_top, 6),
        "ob_level_bot":  round(ob_bot, 6),
        "ob_distance":   round(float(ob_dist), 3),
    })
    return out


# ── NEW: FVG PATH SCORE (ICT Fair Value Gap) ──────────────────────────────────

def _fvg_features(
    df: pd.DataFrame, entry: float, tp: float, bias: str, lookback: int = 30
) -> Dict[str, Any]:
    """Detect Fair Value Gaps (FVGs) between entry and TP.

    ICT FVG: 3-candle pattern where candle[i-1].high < candle[i+1].low (bull FVG)
    or candle[i-1].low > candle[i+1].high (bear FVG). The gap in the middle
    candle is an imbalance — price is likely to fill it.

    FVGs IN the path between entry and TP = friction (price will fill them).
    FVGs already FILLED (mitigated) = clean air.
    FVGs AHEAD of TP = potential extension targets.

    Returns:
        fvg_count_in_path     int   (open FVGs between entry and TP)
        fvg_path_score        0-1   (1 = no open FVGs in path = clean air)
        fvg_nearest_level     float (nearest open FVG midpoint)
        fvg_extension_count   int   (open FVGs beyond TP = continuation fuel)
    """
    out = {
        "fvg_count_in_path":   0,
        "fvg_path_score":      1.0,
        "fvg_nearest_level":   0.0,
        "fvg_extension_count": 0,
    }
    if len(df) < 10 or entry is None or tp is None or entry == tp:
        return out

    window = df.tail(lookback).copy().reset_index(drop=True)
    atr    = _atr_val(window)
    if atr <= 0:
        return out

    highs  = window["high"].values
    lows   = window["low"].values
    closes = window["close"].values

    path_lo, path_hi = (entry, tp) if bias == "LONG" else (tp, entry)

    fvgs_in_path:      List[float] = []
    fvgs_beyond_tp:    List[float] = []

    for i in range(1, len(window) - 1):
        # Bull FVG: gap between candle[i-1] high and candle[i+1] low
        bull_gap_lo = highs[i - 1]
        bull_gap_hi = lows[i + 1]
        if bull_gap_hi > bull_gap_lo:   # genuine gap exists
            mid = (bull_gap_lo + bull_gap_hi) / 2.0
            # Is this FVG already filled?
            filled = any(
                lows[j] <= bull_gap_lo for j in range(i + 1, len(window))
            )
            if not filled:
                if path_lo < mid < path_hi:
                    fvgs_in_path.append(mid)
                elif (bias == "LONG" and mid > path_hi) or (bias == "SHORT" and mid < path_lo):
                    fvgs_beyond_tp.append(mid)

        # Bear FVG: gap between candle[i-1] low and candle[i+1] high
        bear_gap_hi = lows[i - 1]
        bear_gap_lo = highs[i + 1]
        if bear_gap_hi > bear_gap_lo:
            mid = (bear_gap_hi + bear_gap_lo) / 2.0
            filled = any(
                highs[j] >= bear_gap_hi for j in range(i + 1, len(window))
            )
            if not filled:
                if path_lo < mid < path_hi:
                    fvgs_in_path.append(mid)
                elif (bias == "SHORT" and mid < path_lo) or (bias == "LONG" and mid > path_hi):
                    fvgs_beyond_tp.append(mid)

    fvg_count = len(fvgs_in_path)
    # Path score: each open FVG in path subtracts — they will get filled = friction
    path_score = max(0.0, 1.0 - fvg_count * 0.22)
    nearest    = min(fvgs_in_path, key=lambda x: abs(x - entry), default=0.0) \
                 if fvgs_in_path else 0.0

    out.update({
        "fvg_count_in_path":   fvg_count,
        "fvg_path_score":      round(float(path_score), 3),
        "fvg_nearest_level":   round(float(nearest), 6),
        "fvg_extension_count": len(fvgs_beyond_tp),
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
    bias         = str(raw.get("bias", "LONG")).upper()
    entry        = raw.get("entry")
    tp           = raw.get("tp")
    pair         = raw.get("pair", "")
    atr_pct      = raw.get("atr_pct", 0.0)
    volume_ratio = raw.get("volume_ratio", 1.0)

    try:
        sweep    = _sweep_features(df, bias)
        absorb   = _absorption_features(df)
        displace = _displacement_features(df, bias)
        path     = _path_features(df, entry, tp, bias)
        compress = _compression_features(df)
        lead     = _leadership_features(pair, atr_pct, volume_ratio, active_pairs or [])
        liq      = _liquidation_features(df, entry, bias)
        # V2 additions
        fakeout  = _fakeout_features(df, bias)
        chain    = _liquidation_chain_features(df, entry, bias)
        ob       = _order_block_features(df, bias)
        fvg      = _fvg_features(df, entry, tp, bias)
    except Exception as exc:
        logger.warning("microstructure.enrich failed for %s: %s", pair, exc)
        return raw

    raw.update({
        # Sweep / stop-hunt
        "sweep_detected":       sweep["sweep_detected"],
        "sweep_side":           sweep["sweep_side"],
        "sweep_depth":          sweep["sweep_depth"],
        "reclaim_close_ratio":  sweep["reclaim_close_ratio"],
        "acceptance_bars":      sweep["acceptance_bars"],
        # Absorption
        "absorption_count":        absorb["absorption_count"],
        "absorption_volume_ratio": absorb["absorption_volume_ratio"],
        # Displacement
        "impulse_body_ratio":   displace["impulse_body_ratio"],
        "follow_through_ratio": displace["follow_through_ratio"],
        "displacement_quality": displace["displacement_quality"],
        # Path (swing obstacles)
        "path_obstacles_count": path["path_obstacles_count"],
        "inefficiency_path":    path["inefficiency_path"],
        # Compression
        "compression_ratio": compress["compression_ratio"],
        # Leadership
        "relative_atr_rank":    lead["relative_atr_rank"],
        "relative_volume_rank": lead["relative_volume_rank"],
        "relative_leadership":  lead["relative_leadership"],
        # Liquidation magnets
        "equal_highs_distance":        liq["equal_highs_distance"],
        "equal_lows_distance":         liq["equal_lows_distance"],
        "liquidation_cluster_distance": liq["liquidation_cluster_distance"],
        # ── V2 additions ──────────────────────────────────────────────────────
        # Fakeout probability (defensive)
        "fakeout_probability": fakeout["fakeout_probability"],
        "fakeout_signature":   fakeout["fakeout_signature"],
        # Liquidation chain potential (offensive bonus)
        "liquidation_chain_potential": chain["liquidation_chain_potential"],
        "chain_level_count":           chain["chain_level_count"],
        "nearest_chain_level":         chain["nearest_chain_level"],
        # Order block quality (SMC depth — OB proximity upgrade)
        "ob_detected":   ob["ob_detected"],
        "ob_quality":    ob["ob_quality"],
        "ob_mitigated":  ob["ob_mitigated"],
        "ob_level_top":  ob["ob_level_top"],
        "ob_level_bot":  ob["ob_level_bot"],
        "ob_distance":   ob["ob_distance"],
        # FVG path score (ICT depth — cleaner path reading)
        "fvg_count_in_path":   fvg["fvg_count_in_path"],
        "fvg_path_score":      fvg["fvg_path_score"],
        "fvg_nearest_level":   fvg["fvg_nearest_level"],
        "fvg_extension_count": fvg["fvg_extension_count"],
    })

    logger.debug(
        "%s micro | sweep=%s depth=%.2f disp=%.2f path=%.2f fakeout=%.2f "
        "chain=%.2f ob_q=%.2f fvg_path=%.2f compress=%.2f lead=%.2f",
        pair,
        sweep["sweep_detected"], sweep["sweep_depth"],
        displace["displacement_quality"], path["inefficiency_path"],
        fakeout["fakeout_probability"], chain["liquidation_chain_potential"],
        ob["ob_quality"], fvg["fvg_path_score"],
        compress["compression_ratio"], lead["relative_leadership"],
    )
    return raw
