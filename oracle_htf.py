"""oracle_htf.py — JHL Oracle HTF Engine
Single source of truth for higher-timeframe market context.
Runs every scan cycle. Output written to signal_bus.json under 'oracle' key.

Publishes:
  - bias: LONG / SHORT / NEUTRAL
  - trend_strength: 0.0–1.0
  - rsi8_d1: RSI-8 on daily
  - rsi8_w1: RSI-8 on weekly
  - premium_discount: PREMIUM / DISCOUNT / FAIR
  - pct_in_range: 0–100 (where price sits in W1 range)
  - active_fvgs: list of {tf, direction, top, bottom, filled}
  - liquidity_pools: {buy_side: [...], sell_side: [...]}
  - htf_bos: CONFIRMED / NONE
  - htf_choch: True/False
  - no_trade_zone: True/False (price in middle chop zone)
  - conviction_bonus: float added to aligned signals
  - conviction_penalty: float subtracted from counter-trend signals
"""

import logging
import numpy as np
from typing import Dict, Any, List, Optional

logger = logging.getLogger("oracle_htf")

# ── RSI-8 ──────────────────────────────────────────────────────────────────

def _rsi(closes: np.ndarray, period: int = 8) -> float:
    """Wilder RSI. Returns latest value 0-100."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g  = gains[:period].mean()
    avg_l  = losses[:period].mean()
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - (100 / (1 + rs)), 2)


# ── FVG Detection ──────────────────────────────────────────────────────────

def _find_fvgs(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
               tf: str, max_fvgs: int = 3) -> List[Dict]:
    """Detect unfilled Fair Value Gaps (3-candle imbalance)."""
    fvgs = []
    for i in range(2, len(highs)):
        # Bullish FVG: candle[i-2].high < candle[i].low
        if lows[i] > highs[i - 2]:
            gap_top    = float(lows[i])
            gap_bottom = float(highs[i - 2])
            filled     = float(closes[-1]) < gap_top  # price returned to fill
            fvgs.append({
                "tf": tf, "direction": "BULL",
                "top": round(gap_top, 4), "bottom": round(gap_bottom, 4),
                "filled": filled
            })
        # Bearish FVG: candle[i-2].low > candle[i].high
        elif highs[i] < lows[i - 2]:
            gap_top    = float(lows[i - 2])
            gap_bottom = float(highs[i])
            filled     = float(closes[-1]) > gap_bottom
            fvgs.append({
                "tf": tf, "direction": "BEAR",
                "top": round(gap_top, 4), "bottom": round(gap_bottom, 4),
                "filled": filled
            })
    # Return most recent unfilled FVGs only
    unfilled = [f for f in fvgs if not f["filled"]]
    return unfilled[-max_fvgs:] if unfilled else []


# ── Liquidity Pool Detection ───────────────────────────────────────────────

def _find_liquidity_pools(highs: np.ndarray, lows: np.ndarray,
                          closes: np.ndarray) -> Dict:
    """
    Equal highs = sell-side liquidity (stops sitting above).
    Equal lows  = buy-side liquidity (stops sitting below).
    Tolerance: 0.3% of price.
    """
    price     = float(closes[-1])
    tolerance = price * 0.003

    buy_side  = []  # equal lows below price — pools of stops to sweep
    sell_side = []  # equal highs above price

    for i in range(len(lows) - 1):
        for j in range(i + 1, len(lows)):
            if abs(lows[i] - lows[j]) <= tolerance:
                level = round(float((lows[i] + lows[j]) / 2), 4)
                if level < price and level not in buy_side:
                    buy_side.append(level)
            if abs(highs[i] - highs[j]) <= tolerance:
                level = round(float((highs[i] + highs[j]) / 2), 4)
                if level > price and level not in sell_side:
                    sell_side.append(level)

    # Nearest pools only
    buy_side  = sorted(buy_side,  reverse=True)[:3]
    sell_side = sorted(sell_side)[:3]
    return {"buy_side": buy_side, "sell_side": sell_side}


# ── BOS / CHoCH Detection ─────────────────────────────────────────────────

def _htf_structure(highs: np.ndarray, lows: np.ndarray,
                   closes: np.ndarray) -> Dict:
    """
    Simple HTF BOS: price closed above last swing high = bullish BOS.
    CHoCH: was making higher highs, now made lower low.
    Uses last 20 candles.
    """
    if len(highs) < 10:
        return {"htf_bos": "NONE", "htf_choch": False}

    swing_high = float(highs[-10:-1].max())
    swing_low  = float(lows[-10:-1].min())
    last_close = float(closes[-1])

    bos    = "NONE"
    choch  = False

    if last_close > swing_high:
        bos = "CONFIRMED_BULL"
    elif last_close < swing_low:
        bos = "CONFIRMED_BEAR"

    # CHoCH: prior trend was up (close > midpoint of range) but now broke low
    mid = (swing_high + swing_low) / 2
    if closes[-5] > mid and last_close < swing_low:
        choch = True
    elif closes[-5] < mid and last_close > swing_high:
        choch = True

    return {"htf_bos": bos, "htf_choch": choch}


# ── Premium / Discount ────────────────────────────────────────────────────

def _premium_discount(w1_high: float, w1_low: float,
                      price: float) -> Dict:
    """
    Where is price in the weekly range?
    >65% = PREMIUM (overbought for longs)
    <35% = DISCOUNT (good for longs)
    35-65% = FAIR (no edge)
    """
    rng = w1_high - w1_low
    if rng <= 0:
        return {"premium_discount": "FAIR", "pct_in_range": 50.0}
    pct = round(((price - w1_low) / rng) * 100, 1)
    if pct >= 65:
        zone = "PREMIUM"
    elif pct <= 35:
        zone = "DISCOUNT"
    else:
        zone = "FAIR"
    return {"premium_discount": zone, "pct_in_range": pct}


# ── RSI-8 Trend Bias ──────────────────────────────────────────────────────

def _rsi8_bias(rsi_d1: float, rsi_w1: float) -> Dict:
    """
    RSI-8 rules (30m+ adapted for D1/W1):
    - Both > 55: strong LONG bias
    - Both < 45: strong SHORT bias
    - D1 > 55, W1 > 50: LONG bias
    - D1 < 45, W1 < 50: SHORT bias
    - Divergence or 45-55 range: NEUTRAL
    Trend strength = how far from 50 (normalized 0-1).
    """
    d1_bull = rsi_d1 > 55
    d1_bear = rsi_d1 < 45
    w1_bull = rsi_w1 > 50
    w1_bear = rsi_w1 < 50

    if d1_bull and w1_bull:
        bias = "LONG"
        strength = round(min((rsi_d1 - 50) / 50, 1.0), 2)
    elif d1_bear and w1_bear:
        bias = "SHORT"
        strength = round(min((50 - rsi_d1) / 50, 1.0), 2)
    else:
        bias = "NEUTRAL"
        strength = round(abs(rsi_d1 - 50) / 50, 2)

    return {"bias": bias, "trend_strength": strength}


# ── Main Oracle Run ───────────────────────────────────────────────────────

def run_oracle(pair: str, df_h4, df_d1, df_w1) -> Dict[str, Any]:
    """
    Full Oracle analysis for one pair.
    df_h4/d1/w1: pandas DataFrame with columns [open, high, low, close, volume].
    Returns oracle dict for signal_bus 'oracle' key.
    """
    result: Dict[str, Any] = {
        "pair":              pair,
        "bias":              "NEUTRAL",
        "trend_strength":    0.5,
        "rsi8_d1":           50.0,
        "rsi8_w1":           50.0,
        "premium_discount":  "FAIR",
        "pct_in_range":      50.0,
        "active_fvgs":       [],
        "liquidity_pools":   {"buy_side": [], "sell_side": []},
        "htf_bos":           "NONE",
        "htf_choch":         False,
        "no_trade_zone":     False,
        "conviction_bonus":  0.0,
        "conviction_penalty": 0.0,
    }

    try:
        # ── RSI-8 on D1 and W1 ──────────────────────────────────────────
        if df_d1 is not None and len(df_d1) >= 10:
            closes_d1 = df_d1["close"].values.astype(float)
            result["rsi8_d1"] = _rsi(closes_d1, 8)

        if df_w1 is not None and len(df_w1) >= 10:
            closes_w1 = df_w1["close"].values.astype(float)
            result["rsi8_w1"] = _rsi(closes_w1, 8)

        # ── RSI-8 bias ──────────────────────────────────────────────────
        bias_data = _rsi8_bias(result["rsi8_d1"], result["rsi8_w1"])
        result.update(bias_data)

        # ── Premium / Discount (weekly range) ───────────────────────────
        if df_w1 is not None and len(df_w1) >= 4:
            w1_high  = float(df_w1["high"].values[-4:].max())
            w1_low   = float(df_w1["low"].values[-4:].min())
            price    = float(df_d1["close"].values[-1]) if df_d1 is not None else 0
            pd_data  = _premium_discount(w1_high, w1_low, price)
            result.update(pd_data)

        # ── FVGs on D1 ──────────────────────────────────────────────────
        if df_d1 is not None and len(df_d1) >= 5:
            result["active_fvgs"] = _find_fvgs(
                df_d1["high"].values.astype(float),
                df_d1["low"].values.astype(float),
                df_d1["close"].values.astype(float),
                tf="D1"
            )

        # ── Liquidity pools on D1 ────────────────────────────────────────
        if df_d1 is not None and len(df_d1) >= 10:
            result["liquidity_pools"] = _find_liquidity_pools(
                df_d1["high"].values[-20:].astype(float),
                df_d1["low"].values[-20:].astype(float),
                df_d1["close"].values.astype(float)
            )

        # ── HTF structure (BOS/CHoCH) on D1 ────────────────────────────
        if df_d1 is not None and len(df_d1) >= 10:
            struct = _htf_structure(
                df_d1["high"].values.astype(float),
                df_d1["low"].values.astype(float),
                df_d1["close"].values.astype(float)
            )
            result.update(struct)

        # ── No-trade zone ────────────────────────────────────────────────
        # FAIR premium/discount + NEUTRAL bias = chop, no edge
        result["no_trade_zone"] = (
            result["premium_discount"] == "FAIR" and
            result["bias"] == "NEUTRAL"
        )

        # ── Conviction modifiers ─────────────────────────────────────────
        strength = result["trend_strength"]
        if result["bias"] != "NEUTRAL":
            result["conviction_bonus"]   = round(strength * 8.0, 2)   # up to +8 pts
            result["conviction_penalty"] = round(strength * 12.0, 2)  # up to −12 pts

        logger.info(
            "Oracle %s | bias=%s str=%.2f rsi8_d1=%.1f rsi8_w1=%.1f pd=%s",
            pair, result["bias"], result["trend_strength"],
            result["rsi8_d1"], result["rsi8_w1"], result["premium_discount"]
        )

    except Exception as e:
        logger.warning("Oracle error for %s: %s", pair, e)

    return result


# ── Aggregate Oracle (market-wide) ────────────────────────────────────────

def build_market_oracle(pair_oracles: List[Dict]) -> Dict[str, Any]:
    """
    Aggregate individual pair oracles into a market-wide read.
    Used for Council context and feed display.
    """
    if not pair_oracles:
        return {"market_bias": "NEUTRAL", "long_pct": 0, "short_pct": 0}

    longs   = sum(1 for o in pair_oracles if o.get("bias") == "LONG")
    shorts  = sum(1 for o in pair_oracles if o.get("bias") == "SHORT")
    total   = len(pair_oracles)

    long_pct  = round((longs  / total) * 100, 1)
    short_pct = round((shorts / total) * 100, 1)

    if long_pct >= 60:
        market_bias = "LONG"
    elif short_pct >= 60:
        market_bias = "SHORT"
    else:
        market_bias = "NEUTRAL"

    avg_strength = round(
        sum(o.get("trend_strength", 0.5) for o in pair_oracles) / total, 2
    )

    return {
        "market_bias":    market_bias,
        "long_pct":       long_pct,
        "short_pct":      short_pct,
        "neutral_pct":    round(100 - long_pct - short_pct, 1),
        "avg_strength":   avg_strength,
        "pairs_analyzed": total,
    }
