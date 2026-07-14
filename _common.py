"""_common.py — Shared technical-analysis helpers for the S1-S9 engines.

Nine strategy engines share the same primitives (EMA, RSI, ATR, Bollinger
Bands, swing detection, SuperTrend, Hull MA, FVG detection, candle anatomy).
Centralizing them here keeps each engine short and guarantees every engine
computes indicators identically.

Every engine's ``generate(...)`` returns a *partial* signal dict (or ``None``).
The scanner later augments it with AI-SuperTrend + MTF fields before scoring.
Use :func:`build_signal` to assemble a well-formed, 2R-gated signal.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger("strategies.common")

MIN_RR = 2.0  # Global 2R hard gate (Rule / prop requirement).


# ---------------------------------------------------------------------------
# Moving averages / oscillators
# ---------------------------------------------------------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average.

    Args:
        series: Price series.
        period: EMA span.

    Returns:
        EMA series aligned to the input.
    """
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder smoothing).

    Args:
        close: Close-price series.
        period: RSI lookback.

    Returns:
        RSI series (0-100); NaN-safe (fills neutral 50).
    """
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def atr_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (Wilder) as a full series.

    Args:
        df: OHLC DataFrame (needs high/low/close).
        period: ATR lookback.

    Returns:
        ATR series aligned to df rows.
    """
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    prev_close = np.roll(close, 1)
    tr = np.maximum.reduce([
        high - low,
        np.abs(high - prev_close),
        np.abs(low - prev_close),
    ])
    tr[0] = high[0] - low[0]
    return pd.Series(tr, index=df.index).ewm(alpha=1 / period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> float:
    """Latest ATR value.

    Args:
        df: OHLC DataFrame.
        period: ATR lookback.

    Returns:
        Latest ATR float (0.0 if insufficient data).
    """
    if len(df) < period + 1:
        return 0.0
    return float(atr_series(df, period).iloc[-1])


def bollinger(
    close: pd.Series, period: int = 20, num_std: float = 2.0
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands.

    Args:
        close: Close-price series.
        period: SMA/STD lookback.
        num_std: Standard-deviation multiplier.

    Returns:
        ``(upper, middle, lower)`` band series.
    """
    mid = close.rolling(period).mean()
    std = close.rolling(period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def hull_ma(close: pd.Series, period: int = 20) -> pd.Series:
    """Hull Moving Average (smoother, lower-lag MA).

    Args:
        close: Close-price series.
        period: HMA period.

    Returns:
        Hull MA series.
    """
    half = max(int(period / 2), 1)
    sqrt_p = max(int(np.sqrt(period)), 1)
    wma_half = _wma(close, half)
    wma_full = _wma(close, period)
    return _wma(2 * wma_half - wma_full, sqrt_p)


def _wma(series: pd.Series, period: int) -> pd.Series:
    """Weighted moving average (linear weights)."""
    weights = np.arange(1, period + 1, dtype=float)
    return series.rolling(period).apply(
        lambda x: np.dot(x, weights) / weights.sum(), raw=True
    )


# ---------------------------------------------------------------------------
# Structure / swings
# ---------------------------------------------------------------------------
def swing_highs(df: pd.DataFrame, left: int = 2, right: int = 2) -> List[int]:
    """Indices of pivot swing highs (fractal-style).

    A bar is a swing high if its high is >= the ``left`` bars before and
    ``right`` bars after it.

    Args:
        df: OHLC DataFrame.
        left: Bars required to the left.
        right: Bars required to the right.

    Returns:
        List of integer positional indices of swing highs.
    """
    highs = df["high"].to_numpy(dtype=float)
    idxs: List[int] = []
    for i in range(left, len(highs) - right):
        window = highs[i - left : i + right + 1]
        if highs[i] == window.max() and np.argmax(window) == left:
            idxs.append(i)
    return idxs


def swing_lows(df: pd.DataFrame, left: int = 2, right: int = 2) -> List[int]:
    """Indices of pivot swing lows (fractal-style).

    Args:
        df: OHLC DataFrame.
        left: Bars required to the left.
        right: Bars required to the right.

    Returns:
        List of integer positional indices of swing lows.
    """
    lows = df["low"].to_numpy(dtype=float)
    idxs: List[int] = []
    for i in range(left, len(lows) - right):
        window = lows[i - left : i + right + 1]
        if lows[i] == window.min() and np.argmin(window) == left:
            idxs.append(i)
    return idxs


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    """Classic SuperTrend direction series (+1 up, -1 down).

    Args:
        df: OHLC DataFrame.
        period: ATR period.
        multiplier: ATR band multiplier.

    Returns:
        Series of +1/-1 direction values aligned to df rows.
    """
    hl2 = (df["high"] + df["low"]) / 2.0
    atr_val = atr_series(df, period)
    upper = hl2 + multiplier * atr_val
    lower = hl2 - multiplier * atr_val
    close = df["close"].to_numpy(dtype=float)

    direction = np.ones(len(df), dtype=int)
    up = upper.to_numpy().copy()
    lo = lower.to_numpy().copy()
    for i in range(1, len(df)):
        if close[i] > up[i - 1]:
            direction[i] = 1
        elif close[i] < lo[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
            # Tighten bands in the prevailing direction.
            if direction[i] == 1 and lo[i] < lo[i - 1]:
                lo[i] = lo[i - 1]
            if direction[i] == -1 and up[i] > up[i - 1]:
                up[i] = up[i - 1]
    return pd.Series(direction, index=df.index)


# ---------------------------------------------------------------------------
# SMC helpers
# ---------------------------------------------------------------------------
def detect_fvg(df: pd.DataFrame, lookback: int = 10) -> Optional[Dict[str, Any]]:
    """Detect the most recent Fair Value Gap in the last ``lookback`` candles.

    A bullish FVG exists when candle[i-1].high < candle[i+1].low (a 3-candle
    gap up); bearish when candle[i-1].low > candle[i+1].high.

    Args:
        df: OHLC DataFrame.
        lookback: How many recent candles to scan.

    Returns:
        ``{type: 'bullish'|'bearish', top, bottom, index}`` or ``None``.
    """
    n = len(df)
    if n < 3:
        return None
    start = max(1, n - lookback)
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    for i in range(n - 2, start - 1, -1):
        # gap between candle i-1 and i+1, with i as the middle.
        if high[i - 1] < low[i + 1]:
            return {"type": "bullish", "bottom": high[i - 1],
                    "top": low[i + 1], "index": i}
        if low[i - 1] > high[i + 1]:
            return {"type": "bearish", "top": low[i - 1],
                    "bottom": high[i + 1], "index": i}
    return None


def candle_anatomy(row: pd.Series) -> Dict[str, float]:
    """Body / wick geometry of a single candle as fractions of its range.

    Args:
        row: A single OHLC row (open/high/low/close).

    Returns:
        Dict with ``range``, ``body``, ``upper_wick``, ``lower_wick``,
        ``body_ratio``, ``upper_wick_ratio``, ``lower_wick_ratio``.
    """
    o = float(row["open"])
    h = float(row["high"])
    low = float(row["low"])
    c = float(row["close"])
    rng = h - low
    if rng <= 0:
        return {"range": 0.0, "body": 0.0, "upper_wick": 0.0, "lower_wick": 0.0,
                "body_ratio": 0.0, "upper_wick_ratio": 0.0, "lower_wick_ratio": 0.0}
    body = abs(c - o)
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - low
    return {
        "range": rng,
        "body": body,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "body_ratio": body / rng,
        "upper_wick_ratio": upper_wick / rng,
        "lower_wick_ratio": lower_wick / rng,
    }


def volume_ratio(df: pd.DataFrame, window: int = 20) -> float:
    """Latest volume relative to its trailing average.

    Args:
        df: OHLC DataFrame (needs volume).
        window: Averaging window.

    Returns:
        current_volume / avg_volume (1.0 if avg is 0).
    """
    if len(df) < 2:
        return 1.0
    avg = float(df["volume"].tail(window).mean())
    cur = float(df["volume"].iloc[-1])
    return cur / avg if avg > 0 else 1.0


# ---------------------------------------------------------------------------
# Signal assembly
# ---------------------------------------------------------------------------
def compute_rr(entry: float, sl: float, tp: float) -> float:
    """Reward-to-risk ratio.

    Args:
        entry: Entry price.
        sl: Stop-loss price.
        tp: Take-profit price.

    Returns:
        reward/risk, or 0.0 on degenerate inputs.
    """
    risk = abs(entry - sl)
    if risk <= 0:
        return 0.0
    return abs(tp - entry) / risk


def enforce_min_rr(
    entry: float, sl: float, bias: str, min_rr: float = MIN_RR
) -> float:
    """Return a TP that satisfies the minimum R:R for the given bias.

    Args:
        entry: Entry price.
        sl: Stop-loss price.
        bias: 'LONG' or 'SHORT'.
        min_rr: Minimum reward:risk to enforce.

    Returns:
        A take-profit price at exactly ``min_rr`` from entry.
    """
    risk = abs(entry - sl)
    if bias.upper() == "LONG":
        return entry + min_rr * risk
    return entry - min_rr * risk


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
    """Assemble a validated partial signal dict, applying the 2R hard gate.

    If the raw TP does not meet ``min_rr``, the TP is stretched to exactly
    ``min_rr`` (engines target "2R minimum"). If SL/entry are degenerate the
    signal is rejected (``None``).

    Args:
        pair: Pair symbol (altname base, e.g. 'BTC').
        bias: 'LONG' or 'SHORT'.
        engine: Engine id ('S1'..'S9').
        regime: Classified regime for the pair.
        entry: Entry price.
        sl: Stop-loss price.
        tp: Proposed take-profit price.
        structure_quality: Engine's 0-1 structure score.
        rsi_val: Current RSI (0-100).
        vol_ratio: Current volume ratio.
        fg_score: Fear & Greed score.
        kill_condition: Human-readable invalidation description.
        extra: Optional engine-specific fields to merge in.
        min_rr: Minimum reward:risk (default 2.0).

    Returns:
        A partial signal dict, or ``None`` if it cannot form a valid trade.
        The scanner fills ``ai_st_direction``/``ai_st_strength``/
        ``mtf_alignment`` before scoring.
    """
    bias = bias.upper()
    if entry <= 0 or sl <= 0 or entry == sl:
        return None
    # Sanity: SL must be on the correct side of entry.
    if bias == "LONG" and sl >= entry:
        return None
    if bias == "SHORT" and sl <= entry:
        return None

    rr = compute_rr(entry, sl, tp)
    if rr < min_rr:
        tp = enforce_min_rr(entry, sl, bias, min_rr)
        rr = compute_rr(entry, sl, tp)

    signal: Dict[str, Any] = {
        "pair": pair,
        "bias": bias,
        "engine": engine,
        "regime": regime,
        "entry": round(float(entry), 8),
        "sl": round(float(sl), 8),
        "tp": round(float(tp), 8),
        "rr": round(float(rr), 3),
        "structure_quality": round(float(min(max(structure_quality, 0.0), 1.0)), 4),
        "rsi": round(float(rsi_val), 2),
        "volume_ratio": round(float(vol_ratio), 4),
        "fg_score": int(fg_score),
        "kill_condition": kill_condition,
        # Placeholders filled by the scanner:
        "ai_st_direction": None,
        "ai_st_strength": 0.0,
        "mtf_alignment": "PARTIAL",
    }
    if extra:
        signal.update(extra)
    return signal
