"""_common.py — Shared technical-analysis helpers for S1-S9 engines + canonical context builders.

Nine strategy engines share the same primitives (EMA ribbons 25/50/100/200, RSI, ATR, Bollinger,
swing detection, SuperTrend, Hull MA, FVG, candle anatomy, volume, MACD, OBV).
Centralizing them here keeps each engine short and guarantees every engine computes indicators identically.

NEW: Dynamic SL/TP system (replaces hard 2R) + canonical context objects for trend/ST/volume/volatility/structure.
These context objects flow directly into the canonical signal bus schema (meta/session/health/regimes/signals/alerts/diagnostics).

Every engine's ``generate(...)`` returns a *partial* signal dict (or ``None``).
The scanner augments it with AI-SuperTrend + MTF fields before scoring.
Use :func:`build_signal` to assemble a well-formed, validated signal with dynamic R:R.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("strategies.common")

# ---------------------------------------------------------------------------
# Config & Constants
# ---------------------------------------------------------------------------
MIN_RR = 1.5  # Minimum R:R floor (down from hard 2.0 to allow dynamic flexibility)
DEFAULT_TP_MULT = 2.5  # Default TP multiplier for dynamic system
RIBBON_PERIODS = [25, 50, 100, 200]  # EMA ribbon periods (common indicators)
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# ---------------------------------------------------------------------------
# Moving averages / oscillators
# ---------------------------------------------------------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def ema_ribbon(df: pd.DataFrame, price_col: str = "close") -> Dict[str, pd.Series]:
    """
    EMA ribbon: 25, 50, 100, 200.
    Returns dict with keys: "ema_25", "ema_50", "ema_100", "ema_200".
    """
    ribbon = {}
    for period in RIBBON_PERIODS:
        ribbon[f"ema_{period}"] = ema(df[price_col], period)
    return ribbon


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / (avg_loss + 1e-12)
    return 100.0 - (100.0 / (1.0 + rs))


def macd(series: pd.Series, fast: int = MACD_FAST, slow: int = MACD_SLOW, signal: int = MACD_SIGNAL) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    MACD (Moving Average Convergence Divergence).
    Returns: (macd_line, signal_line, histogram)
    """
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def obv(df: pd.DataFrame) -> pd.Series:
    """
    On-Balance Volume (OBV).
    Expects df to have 'close' and 'volume' columns.
    """
    obv_series = pd.Series(index=df.index, dtype=float)
    obv_series.iloc[0] = df["volume"].iloc[0]
    for i in range(1, len(df)):
        if df["close"].iloc[i] > df["close"].iloc[i - 1]:
            obv_series.iloc[i] = obv_series.iloc[i - 1] + df["volume"].iloc[i]
        elif df["close"].iloc[i] < df["close"].iloc[i - 1]:
            obv_series.iloc[i] = obv_series.iloc[i - 1] - df["volume"].iloc[i]
        else:
            obv_series.iloc[i] = obv_series.iloc[i - 1]
    return obv_series


# ---------------------------------------------------------------------------
# Volatility & Bands
# ---------------------------------------------------------------------------
def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (full series)."""
    h_l = df["high"] - df["low"]
    h_cp = (df["high"] - df["close"].shift(1)).abs()
    l_cp = (df["low"] - df["close"].shift(1)).abs()
    tr = pd.concat([h_l, h_cp, l_cp], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> float:
    """Current ATR (scalar)."""
    atr_s = atr_series(df, period)
    return atr_s.iloc[-1] if not atr_s.empty else 0.0


def bollinger(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Bollinger Bands.
    Returns: (upper, middle, lower)
    """
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)
    return upper, middle, lower


# ---------------------------------------------------------------------------
# Trend Indicators
# ---------------------------------------------------------------------------
def hull_ma(series: pd.Series, period: int = 9) -> pd.Series:
    """Hull Moving Average (lag-reduced)."""
    half_length = max(int(period / 2), 1)
    sqrt_length = max(int(np.sqrt(period)), 1)
    wma_half = _wma(series, half_length)
    wma_full = _wma(series, period)
    raw = 2 * wma_half - wma_full
    return _wma(raw, sqrt_length)


def _wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted Moving Average."""
    weights = np.arange(1, period + 1)
    return series.rolling(window=period).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> Tuple[pd.Series, pd.Series]:
    """
    SuperTrend indicator.
    Returns: (supertrend_series, direction_series)
      direction: 1 = uptrend, -1 = downtrend
    """
    hl_avg = (df["high"] + df["low"]) / 2
    atr_val = atr_series(df, period)
    upper_band = hl_avg + (multiplier * atr_val)
    lower_band = hl_avg - (multiplier * atr_val)

    st = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)

    st.iloc[0] = lower_band.iloc[0]
    direction.iloc[0] = 1

    for i in range(1, len(df)):
        if df["close"].iloc[i] > st.iloc[i - 1]:
            st.iloc[i] = lower_band.iloc[i]
            direction.iloc[i] = 1
        elif df["close"].iloc[i] < st.iloc[i - 1]:
            st.iloc[i] = upper_band.iloc[i]
            direction.iloc[i] = -1
        else:
            st.iloc[i] = st.iloc[i - 1]
            direction.iloc[i] = direction.iloc[i - 1]

    return st, direction


# ---------------------------------------------------------------------------
# Structure Detection
# ---------------------------------------------------------------------------
def swing_highs(df: pd.DataFrame, left: int = 5, right: int = 5) -> List[int]:
    """Swing high pivot indices."""
    highs = []
    for i in range(left, len(df) - right):
        if all(df["high"].iloc[i] >= df["high"].iloc[i - j] for j in range(1, left + 1)) and \
           all(df["high"].iloc[i] >= df["high"].iloc[i + j] for j in range(1, right + 1)):
            highs.append(i)
    return highs


def swing_lows(df: pd.DataFrame, left: int = 5, right: int = 5) -> List[int]:
    """Swing low pivot indices."""
    lows = []
    for i in range(left, len(df) - right):
        if all(df["low"].iloc[i] <= df["low"].iloc[i - j] for j in range(1, left + 1)) and \
           all(df["low"].iloc[i] <= df["low"].iloc[i + j] for j in range(1, right + 1)):
            lows.append(i)
    return lows


def detect_fvg(df: pd.DataFrame, lookback: int = 50) -> List[Dict[str, Any]]:
    """
    Fair Value Gap detection.
    Returns list of dicts with 'index', 'top', 'bottom', 'bias'.
    """
    fvgs = []
    for i in range(2, min(len(df), lookback + 2)):
        # Bullish FVG: gap between bar[i-2] high and bar[i] low
        if df["low"].iloc[i] > df["high"].iloc[i - 2]:
            fvgs.append({
                "index": i,
                "top": df["low"].iloc[i],
                "bottom": df["high"].iloc[i - 2],
                "bias": "LONG"
            })
        # Bearish FVG
        elif df["high"].iloc[i] < df["low"].iloc[i - 2]:
            fvgs.append({
                "index": i,
                "top": df["low"].iloc[i - 2],
                "bottom": df["high"].iloc[i],
                "bias": "SHORT"
            })
    return fvgs


# ---------------------------------------------------------------------------
# Candle & Volume Analysis
# ---------------------------------------------------------------------------
def candle_anatomy(df: pd.DataFrame, idx: int = -1) -> Dict[str, float]:
    """
    Candle body/wick ratios.
    Returns: {body_pct, upper_wick_pct, lower_wick_pct, total_range}
    """
    o = df["open"].iloc[idx]
    h = df["high"].iloc[idx]
    l = df["low"].iloc[idx]
    c = df["close"].iloc[idx]
    total_range = h - l
    if total_range <= 0:
        return {"body_pct": 0.0, "upper_wick_pct": 0.0, "lower_wick_pct": 0.0, "total_range": 0.0}

    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    return {
        "body_pct": body / total_range,
        "upper_wick_pct": upper_wick / total_range,
        "lower_wick_pct": lower_wick / total_range,
        "total_range": total_range
    }


def volume_ratio(df: pd.DataFrame, period: int = 20, idx: int = -1) -> float:
    """
    Relative volume: current volume / avg volume.
    """
    avg_vol = df["volume"].rolling(window=period).mean().iloc[idx]
    if avg_vol <= 0:
        return 1.0
    return df["volume"].iloc[idx] / avg_vol


# ---------------------------------------------------------------------------
# Dynamic SL/TP System (replaces hard 2R)
# ---------------------------------------------------------------------------
def compute_dynamic_sl(df: pd.DataFrame, bias: str, entry: float, atr_mult: float = 1.5) -> float:
    """
    Dynamic stop-loss based on ATR and structure.
    For LONG: SL = entry - (ATR * atr_mult) or recent swing low, whichever is tighter.
    For SHORT: SL = entry + (ATR * atr_mult) or recent swing high, whichever is tighter.
    """
    atr_val = atr(df)
    if bias.upper() == "LONG":
        atr_sl = entry - (atr_val * atr_mult)
        swing_lows_list = swing_lows(df, left=5, right=2)
        if swing_lows_list:
            structure_sl = df["low"].iloc[swing_lows_list[-1]]
            return max(atr_sl, structure_sl)  # Tighter stop
        return atr_sl
    else:  # SHORT
        atr_sl = entry + (atr_val * atr_mult)
        swing_highs_list = swing_highs(df, left=5, right=2)
        if swing_highs_list:
            structure_sl = df["high"].iloc[swing_highs_list[-1]]
            return min(atr_sl, structure_sl)  # Tighter stop
        return atr_sl


def compute_dynamic_tp(entry: float, sl: float, bias: str, target_rr: float = DEFAULT_TP_MULT, structure_target: Optional[float] = None) -> float:
    """
    Dynamic take-profit based on R:R and optional structure target.
    If structure_target is provided and better than R:R target, use structure.
    Otherwise, use R:R multiplier.
    """
    risk = abs(entry - sl)
    rr_tp = entry + (risk * target_rr) if bias.upper() == "LONG" else entry - (risk * target_rr)

    if structure_target is not None:
        if bias.upper() == "LONG" and structure_target > rr_tp:
            return structure_target
        elif bias.upper() == "SHORT" and structure_target < rr_tp:
            return structure_target

    return rr_tp


def compute_rr(entry: float, sl: float, tp: float) -> float:
    """Reward-to-risk ratio."""
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0
    return abs(tp - entry) / risk


def enforce_min_rr(entry: float, sl: float, bias: str, min_rr: float = MIN_RR) -> float:
    """Return a TP that satisfies the minimum R:R for the given bias."""
    risk = abs(entry - sl)
    if bias.upper() == "LONG":
        return entry + min_rr * risk
    return entry - min_rr * risk


# ---------------------------------------------------------------------------
# Canonical Context Builders (for signal bus schema)
# ---------------------------------------------------------------------------
def build_trend_context(df: pd.DataFrame, ribbon: Dict[str, pd.Series]) -> Dict[str, Any]:
    """
    Trend context object: ribbon order, slope, compression, reclaim status.
    """
    ema_25 = ribbon["ema_25"].iloc[-1]
    ema_50 = ribbon["ema_50"].iloc[-1]
    ema_100 = ribbon["ema_100"].iloc[-1]
    ema_200 = ribbon["ema_200"].iloc[-1]

    order = "bullish" if ema_25 > ema_50 > ema_100 > ema_200 else \
            "bearish" if ema_25 < ema_50 < ema_100 < ema_200 else "mixed"

    slope_25 = (ribbon["ema_25"].iloc[-1] - ribbon["ema_25"].iloc[-5]) / ribbon["ema_25"].iloc[-5] if len(ribbon["ema_25"]) >= 5 else 0.0
    slope_200 = (ribbon["ema_200"].iloc[-1] - ribbon["ema_200"].iloc[-5]) / ribbon["ema_200"].iloc[-5] if len(ribbon["ema_200"]) >= 5 else 0.0

    compression = abs(ema_25 - ema_200) / ema_200 if ema_200 > 0 else 0.0
    reclaim_status = "reclaimed_200" if df["close"].iloc[-1] > ema_200 and df["close"].iloc[-2] <= ema_200 else "none"

    return {
        "ribbon_order": order,
        "slope_fast": round(slope_25, 6),
        "slope_slow": round(slope_200, 6),
        "compression": round(compression, 6),
        "reclaim_status": reclaim_status
    }


def build_st_context(df: pd.DataFrame) -> Dict[str, Any]:
    """
    SuperTrend context: direction, distance, strength, phase, flip risk.
    """
    st, direction = supertrend(df)
    st_val = st.iloc[-1]
    direction_val = direction.iloc[-1]
    close = df["close"].iloc[-1]

    distance = abs(close - st_val) / st_val if st_val > 0 else 0.0
    strength = "strong" if distance > 0.02 else "weak"
    phase = "uptrend" if direction_val == 1 else "downtrend"
    flip_risk = "high" if distance < 0.005 else "low"

    return {
        "direction": phase,
        "distance": round(distance, 6),
        "strength": strength,
        "phase": phase,
        "flip_risk": flip_risk
    }


def build_volume_context(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Volume context: relative volume, spike state, quiet-pullback, participation grade.
    """
    rvol = volume_ratio(df)
    obv_series = obv(df)
    obv_slope = (obv_series.iloc[-1] - obv_series.iloc[-5]) / abs(obv_series.iloc[-5]) if len(obv_series) >= 5 and obv_series.iloc[-5] != 0 else 0.0

    spike_state = "spike" if rvol > 2.0 else "quiet" if rvol < 0.5 else "normal"
    participation_grade = "strong" if obv_slope > 0.05 else "weak" if obv_slope < -0.05 else "neutral"

    return {
        "relative_volume": round(rvol, 2),
        "spike_state": spike_state,
        "quiet_pullback": spike_state == "quiet",
        "participation_grade": participation_grade,
        "obv_slope": round(obv_slope, 6)
    }


def build_volatility_context(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Volatility context: ATR level, expansion, compression-release state.
    """
    atr_val = atr(df)
    atr_sma = atr_series(df).rolling(window=14).mean().iloc[-1] if len(df) >= 14 else atr_val
    atr_ratio = atr_val / atr_sma if atr_sma > 0 else 1.0

    expansion = "expanding" if atr_ratio > 1.2 else "compressing" if atr_ratio < 0.8 else "stable"

    return {
        "atr_level": round(atr_val, 8),
        "atr_expansion": expansion,
        "compression_release_state": expansion
    }


def build_structure_context(df: pd.DataFrame, bias: str) -> Dict[str, Any]:
    """
    Structure context: swing levels, BOS landmarks, target path, liquidity map.
    """
    swing_highs_list = swing_highs(df)
    swing_lows_list = swing_lows(df)

    last_swing_high = df["high"].iloc[swing_highs_list[-1]] if swing_highs_list else df["high"].max()
    last_swing_low = df["low"].iloc[swing_lows_list[-1]] if swing_lows_list else df["low"].min()

    bos_landmark = "bullish_bos" if df["close"].iloc[-1] > last_swing_high else \
                   "bearish_bos" if df["close"].iloc[-1] < last_swing_low else "none"

    target_path = "clear" if bos_landmark != "none" else "blocked"

    fvg_list = detect_fvg(df)
    liquidity_map = f"{len(fvg_list)}_fvgs" if fvg_list else "no_fvgs"

    return {
        "swing_levels": {
            "last_high": round(last_swing_high, 8),
            "last_low": round(last_swing_low, 8)
        },
        "bos_landmark": bos_landmark,
        "target_path": target_path,
        "liquidity_map": liquidity_map
    }


# ---------------------------------------------------------------------------
# Signal Assembly
# ---------------------------------------------------------------------------
def build_signal(
    *,
    pair: str,
    bias: str,
    engine: str,
    regime: str,
    entry: float,
    sl: float,
    tp: float,
    structure_quality: float,
    rsi_val: float,
    vol_ratio: float,
    fg_score: int,
    kill_condition: str,
    extra: Optional[Dict[str, Any]] = None,
    min_rr: float = MIN_RR,
) -> Optional[Dict[str, Any]]:
    """
    Assemble a validated partial signal dict with dynamic SL/TP and context objects.
    If the raw TP does not meet `min_rr`, the TP is stretched to exactly `min_rr`.
    """
    if not pair or not bias or not engine:
        return None
    if entry <= 0 or sl <= 0 or tp <= 0:
        return None

    # Validate bias direction
    if bias.upper() == "LONG" and not (sl < entry < tp):
        return None
    if bias.upper() == "SHORT" and not (tp < entry < sl):
        return None

    # Enforce minimum R:R
    rr = compute_rr(entry, sl, tp)
    if rr < min_rr:
        tp = enforce_min_rr(entry, sl, bias, min_rr)
        rr = compute_rr(entry, sl, tp)

    signal = {
        "pair": pair,
        "bias": bias.upper(),
        "engine": engine,
        "regime": regime,
        "entry": round(entry, 8),
        "sl": round(sl, 8),
        "tp": round(tp, 8),
        "rr": round(rr, 2),
        "structure_quality": round(structure_quality, 2),
        "rsi": round(rsi_val, 1),
        "vol_ratio": round(vol_ratio, 2),
        "fg_score": fg_score,
        "kill_condition": kill_condition,
    }

    if extra:
        signal.update(extra)

    return signal
"""
