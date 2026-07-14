"""rts_liq.py — RTS-LIQ: Liquidity & Forced Flow engine.

Detects liquidity pool sweeps (EQH/EQL, PDH/PDL, breakout shelves, swing
high/low pools) and resolves them into actionable intent with mechanical
kill levels. Outputs the full RTS shared envelope.

Pool types: EQH, EQL, PDH, PDL, PWH, PWL,
            BREAKOUT_SHELF_HIGH, BREAKOUT_SHELF_LOW,
            SWING_HIGH_POOL, SWING_LOW_POOL
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

try:
    from ._common import swing_highs, swing_lows, build_signal, atr as calc_atr
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import swing_highs, swing_lows, build_signal, atr as calc_atr  # type: ignore

logger = logging.getLogger("strategies.rts_liq")

# ── tunables ──────────────────────────────────────────────────────────────────
EQH_EQL_TOLERANCE_ATR = 0.35      # two pivots within 0.35 ATR = equal level
SWEEP_MIN_ATR         = 0.10      # min wick beyond level to count as sweep
RECLAIM_WINDOW_BARS   = 6         # bars to close back inside after sweep
BREAKOUT_SHELF_BARS   = 6         # min bars level must be tested to be a shelf
SWING_LOOKBACK        = 40        # bars to look for major swing pool
KILL_BUFFER_ATR       = 0.20      # stop placement buffer beyond sweep extreme
MIN_RR                = 2.0
ATR_PERIOD            = 14


def _atr(df: pd.DataFrame) -> float:
    """Return current ATR value."""
    try:
        return float(calc_atr(df, ATR_PERIOD).iloc[-1])
    except Exception:
        hi = df["high"].iloc[-ATR_PERIOD:]
        lo = df["low"].iloc[-ATR_PERIOD:]
        cl = df["close"].iloc[-ATR_PERIOD:]
        tr = pd.concat([hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1).max(axis=1)
        return float(tr.mean())


# ── pool detectors ────────────────────────────────────────────────────────────

def _detect_eqh_eql(
    df: pd.DataFrame, atr_val: float
) -> List[Dict[str, Any]]:
    """Return list of EQH/EQL pool events on the last bar."""
    pools: List[Dict[str, Any]] = []
    tol = EQH_EQL_TOLERANCE_ATR * atr_val
    h_idx = swing_highs(df, left=2, right=2)
    l_idx = swing_lows(df, left=2, right=2)
    last = len(df) - 1

    def _find_equal_cluster(indices: List[int], col: str, side: str) -> None:
        for i in range(len(indices) - 1):
            for j in range(i + 1, len(indices)):
                if abs(df[col].iloc[indices[i]] - df[col].iloc[indices[j]]) <= tol:
                    level = (df[col].iloc[indices[i]] + df[col].iloc[indices[j]]) / 2
                    # Was the current bar a sweep?
                    if side == "BUY":
                        swept = df["high"].iloc[last] > level
                        if swept:
                            pools.append({
                                "pool_type": "EQH",
                                "sweep_side": "BUY_SIDE",
                                "level": level,
                                "sweep_extreme": df["high"].iloc[last],
                            })
                    else:
                        swept = df["low"].iloc[last] < level
                        if swept:
                            pools.append({
                                "pool_type": "EQL",
                                "sweep_side": "SELL_SIDE",
                                "level": level,
                                "sweep_extreme": df["low"].iloc[last],
                            })

    _find_equal_cluster(h_idx[-8:], "high", "BUY")
    _find_equal_cluster(l_idx[-8:], "low", "SELL")
    return pools


def _detect_pdh_pdl(df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Detect sweeps of Previous Day High/Low using daily rollup."""
    pools: List[Dict[str, Any]] = []
    if len(df) < 48:  # need at least 2 days of 4H bars
        return pools
    # Group 4H bars into days (6 bars per day)
    bars_per_day = 6
    last_bar = df.iloc[-1]
    # Previous day window
    prev_day = df.iloc[-(bars_per_day * 2):-(bars_per_day)]
    if prev_day.empty:
        return pools
    pdh = float(prev_day["high"].max())
    pdl = float(prev_day["low"].min())

    cur_high = float(last_bar["high"])
    cur_low  = float(last_bar["low"])

    if cur_high > pdh:
        pools.append({"pool_type": "PDH", "sweep_side": "BUY_SIDE",
                      "level": pdh, "sweep_extreme": cur_high})
    if cur_low < pdl:
        pools.append({"pool_type": "PDL", "sweep_side": "SELL_SIDE",
                      "level": pdl, "sweep_extreme": cur_low})

    # Previous week (30 bars ≈ 5 days)
    if len(df) >= 36:
        prev_week = df.iloc[-36:-6]
        pwh = float(prev_week["high"].max())
        pwl = float(prev_week["low"].min())
        if cur_high > pwh:
            pools.append({"pool_type": "PWH", "sweep_side": "BUY_SIDE",
                          "level": pwh, "sweep_extreme": cur_high})
        if cur_low < pwl:
            pools.append({"pool_type": "PWL", "sweep_side": "SELL_SIDE",
                          "level": pwl, "sweep_extreme": cur_low})
    return pools


def _detect_breakout_shelves(
    df: pd.DataFrame, atr_val: float
) -> List[Dict[str, Any]]:
    """Detect horizontal shelf sweeps — range high/low tested ≥3 times."""
    pools: List[Dict[str, Any]] = []
    tol = EQH_EQL_TOLERANCE_ATR * atr_val
    window = df.iloc[-SWING_LOOKBACK:]
    last = df.iloc[-1]

    # Cluster high pivots → shelf high
    h_idx = swing_highs(window, left=2, right=2)
    if len(h_idx) >= 3:
        cluster_h = [float(window["high"].iloc[i]) for i in h_idx]
        median_h = float(np.median(cluster_h))
        near_h = [v for v in cluster_h if abs(v - median_h) <= tol]
        if len(near_h) >= 3 and float(last["high"]) > median_h:
            pools.append({
                "pool_type": "BREAKOUT_SHELF_HIGH",
                "sweep_side": "BUY_SIDE",
                "level": median_h,
                "sweep_extreme": float(last["high"]),
            })

    # Cluster low pivots → shelf low
    l_idx = swing_lows(window, left=2, right=2)
    if len(l_idx) >= 3:
        cluster_l = [float(window["low"].iloc[i]) for i in l_idx]
        median_l = float(np.median(cluster_l))
        near_l = [v for v in cluster_l if abs(v - median_l) <= tol]
        if len(near_l) >= 3 and float(last["low"]) < median_l:
            pools.append({
                "pool_type": "BREAKOUT_SHELF_LOW",
                "sweep_side": "SELL_SIDE",
                "level": median_l,
                "sweep_extreme": float(last["low"]),
            })
    return pools


def _detect_swing_pools(
    df: pd.DataFrame, atr_val: float
) -> List[Dict[str, Any]]:
    """Major external swing high/low stop pools."""
    pools: List[Dict[str, Any]] = []
    window = df.iloc[-SWING_LOOKBACK:]
    last = df.iloc[-1]

    h_idx = swing_highs(window, left=4, right=4)
    if h_idx:
        major_h = float(window["high"].iloc[h_idx[-1]])
        if float(last["high"]) > major_h:
            pools.append({
                "pool_type": "SWING_HIGH_POOL",
                "sweep_side": "BUY_SIDE",
                "level": major_h,
                "sweep_extreme": float(last["high"]),
            })

    l_idx = swing_lows(window, left=4, right=4)
    if l_idx:
        major_l = float(window["low"].iloc[l_idx[-1]])
        if float(last["low"]) < major_l:
            pools.append({
                "pool_type": "SWING_LOW_POOL",
                "sweep_side": "SELL_SIDE",
                "level": major_l,
                "sweep_extreme": float(last["low"]),
            })
    return pools


# ── reclaim / acceptance resolver ─────────────────────────────────────────────

def _resolve_reclaim(
    df: pd.DataFrame, pool: Dict[str, Any], atr_val: float
) -> Tuple[str, str, float]:
    """
    Returns (reclaim_status, sweep_type, displacement).

    reclaim_status: RECLAIMED | ACCEPTED | UNCLEAR
    sweep_type: FAST_WICK | SLOW_ACCEPT
    displacement: how far beyond the level the price moved (ATR units)
    """
    level = pool["level"]
    extreme = pool["sweep_extreme"]
    side = pool["sweep_side"]
    last = df.iloc[-1]
    close = float(last["close"])

    displacement = abs(extreme - level) / max(atr_val, 1e-9)

    # Sweep type: fast wick = wick is large relative to body
    body = abs(float(last["close"]) - float(last["open"]))
    wick = abs(extreme - max(float(last["open"]), float(last["close"]))) if side == "BUY_SIDE" else abs(min(float(last["open"]), float(last["close"])) - extreme)
    sweep_type = "FAST_WICK" if body > 0 and wick / body >= 1.5 else "SLOW_ACCEPT"

    # Reclaim: closed back inside level on this bar
    if side == "BUY_SIDE":
        if close < level:
            reclaim = "RECLAIMED"
        elif close > level:
            reclaim = "ACCEPTED"
        else:
            reclaim = "UNCLEAR"
    else:
        if close > level:
            reclaim = "RECLAIMED"
        elif close < level:
            reclaim = "ACCEPTED"
        else:
            reclaim = "UNCLEAR"

    # Check lookback if current bar is UNCLEAR
    if reclaim == "UNCLEAR" and len(df) >= RECLAIM_WINDOW_BARS:
        recent = df.iloc[-RECLAIM_WINDOW_BARS:]
        if side == "BUY_SIDE":
            if (recent["close"] < level).any():
                reclaim = "RECLAIMED"
            elif (recent["close"] > level).all():
                reclaim = "ACCEPTED"
        else:
            if (recent["close"] > level).any():
                reclaim = "RECLAIMED"
            elif (recent["close"] < level).all():
                reclaim = "ACCEPTED"

    return reclaim, sweep_type, displacement


# ── scoring ───────────────────────────────────────────────────────────────────

def _score_pool(
    pool: Dict[str, Any], reclaim: str, sweep_type: str,
    displacement: float, regime: str, fg_score: int,
) -> Tuple[float, float, float, str, str]:
    """
    Returns (offence_score, defence_score, trap_score, intent, continuation_status).
    All scores 0.0–1.0.
    """
    pool_type = pool["pool_type"]

    # Base trap quality from pool importance
    pool_weight = {
        "EQH": 0.80, "EQL": 0.80,
        "PDH": 0.75, "PDL": 0.75,
        "PWH": 0.85, "PWL": 0.85,
        "SWING_HIGH_POOL": 0.78, "SWING_LOW_POOL": 0.78,
        "BREAKOUT_SHELF_HIGH": 0.70, "BREAKOUT_SHELF_LOW": 0.70,
    }.get(pool_type, 0.60)

    # Trap score: fast wick + reclaim = high trap quality
    trap_score = pool_weight
    if reclaim == "RECLAIMED":
        trap_score = min(1.0, trap_score + 0.15)
    if sweep_type == "FAST_WICK":
        trap_score = min(1.0, trap_score + 0.10)
    if displacement >= 0.5:
        trap_score = min(1.0, trap_score + 0.08)
    if reclaim == "ACCEPTED":
        trap_score *= 0.60  # continuation, not a trap

    # Offence: how far can price travel if valid?
    offence_score = min(1.0, 0.55 + displacement * 0.20)
    if reclaim == "RECLAIMED":
        offence_score = min(1.0, offence_score + 0.15)
    # Fear = better long traps from EQL/PDL
    if fg_score < 25 and pool_type in ("EQL", "PDL", "PWL", "SWING_LOW_POOL"):
        offence_score = min(1.0, offence_score + 0.10)

    # Defence: clarity of kill level
    defence_score = 0.70  # base — kill is always sweep extreme
    if sweep_type == "FAST_WICK":
        defence_score = min(1.0, defence_score + 0.10)  # wick = clean kill
    if reclaim == "UNCLEAR":
        defence_score *= 0.80

    # Intent mapping per taxonomy
    continuation_status = "CONTINUATION_UNCERTAIN"
    if reclaim == "RECLAIMED" and trap_score >= 0.75 and offence_score >= 0.68:
        intent = "ATTACK_TRAP"
    elif reclaim == "ACCEPTED" and offence_score >= 0.65:
        intent = "ATTACK_BREAK"
        continuation_status = "CONTINUATION_CONFIRMED"
    elif reclaim in ("RECLAIMED", "ACCEPTED") and offence_score >= 0.50:
        intent = "PROBE"
    elif reclaim == "UNCLEAR":
        intent = "WAIT"
    else:
        intent = "IGNORE"

    return offence_score, defence_score, trap_score, intent, continuation_status


# ── main engine ───────────────────────────────────────────────────────────────

class RTSLiq:
    """RTS-LIQ — Liquidity Sweep Specialist."""

    ENGINE = "RTS_LIQ"
    REQUIRED_REGIMES = ["TREND_UP", "TREND_DOWN", "VOLATILE", "RANGE", "FEAR"]

    def generate(
        self,
        pair: str,
        ohlc_df: pd.DataFrame,
        regime: str,
        fg_score: int,
        ai_st: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Scan all pool types on the current bar and return the best signal."""
        if len(ohlc_df) < 40:
            return None

        df = ohlc_df.copy()
        atr_val = _atr(df)
        if atr_val <= 0:
            return None

        last = df.iloc[-1]
        close = float(last["close"])
        open_ = float(last["open"])

        # Collect all detected pools
        all_pools: List[Dict[str, Any]] = []
        all_pools.extend(_detect_eqh_eql(df, atr_val))
        all_pools.extend(_detect_pdh_pdl(df))
        all_pools.extend(_detect_breakout_shelves(df, atr_val))
        all_pools.extend(_detect_swing_pools(df, atr_val))

        if not all_pools:
            return None

        best: Optional[Dict[str, Any]] = None
        best_score = -1.0

        for pool in all_pools:
            reclaim, sweep_type, displacement = _resolve_reclaim(df, pool, atr_val)
            if displacement < SWEEP_MIN_ATR:
                continue  # wick too small to count as a real sweep

            offence, defence, trap, intent, cont_status = _score_pool(
                pool, reclaim, sweep_type, displacement, regime, fg_score
            )

            if intent == "IGNORE":
                continue

            # Composite ranking score
            rank = trap * 0.40 + offence * 0.35 + defence * 0.25

            if rank > best_score:
                best_score = rank
                best = {
                    "pool": pool,
                    "reclaim": reclaim,
                    "sweep_type": sweep_type,
                    "displacement": displacement,
                    "offence": offence,
                    "defence": defence,
                    "trap": trap,
                    "intent": intent,
                    "cont_status": cont_status,
                }

        if best is None:
            return None

        pool = best["pool"]
        intent = best["intent"]
        side = pool["sweep_side"]
        level = pool["level"]
        extreme = pool["sweep_extreme"]

        # Determine trade bias from sweep + intent
        if side == "BUY_SIDE" and intent in ("ATTACK_TRAP", "PROBE"):
            bias = "SHORT"  # swept buy-side stops = trap → short
        elif side == "SELL_SIDE" and intent in ("ATTACK_TRAP", "PROBE"):
            bias = "LONG"   # swept sell-side stops = trap → long
        elif side == "BUY_SIDE" and intent == "ATTACK_BREAK":
            bias = "LONG"   # genuine breakout up
        elif side == "SELL_SIDE" and intent == "ATTACK_BREAK":
            bias = "SHORT"  # genuine breakout down
        else:
            return None

        # Entry = current close
        entry = close

        # Kill level: beyond sweep extreme with buffer
        kill_level = extreme + KILL_BUFFER_ATR * atr_val if side == "BUY_SIDE" else extreme - KILL_BUFFER_ATR * atr_val

        # SL = kill level
        sl = kill_level

        # TP: 2R minimum from entry
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        tp = entry + risk * MIN_RR if bias == "LONG" else entry - risk * MIN_RR

        # Structure quality proxy from trap score
        structure_quality = best["trap"]

        # Kill condition description
        kill_condition = (
            f"RTS-LIQ {pool['pool_type']} kill: price {'above' if side == 'BUY_SIDE' else 'below'} "
            f"{kill_level:.4f} (sweep extreme {extreme:.4f} + buffer)"
        )

        raw = build_signal(
            pair=pair,
            bias=bias,
            engine="RTS_LIQ",
            regime=regime,
            entry=entry,
            sl=sl,
            tp=tp,
            structure_quality=structure_quality,
            rsi_val=50.0,   # not used for intent scoring
            vol_ratio=1.0,
            fg_score=fg_score,
            kill_condition=kill_condition,
            min_rr=MIN_RR,
        )

        if raw is None:
            return None

        # Attach full RTS envelope + LIQ-specific fields
        raw.update({
            # RTS shared envelope
            "rts_family": "LIQ",
            "intent": intent,
            "kill_level": kill_level,
            "auto_cut": False,
            "offence_score": best["offence"],
            "defence_score": best["defence"],
            "trap_score": best["trap"],
            # LIQ-specific
            "liquidity_pool_type": pool["pool_type"],
            "sweep_side": pool["sweep_side"],
            "sweep_level": level,
            "sweep_displacement": best["displacement"],
            "sweep_type": best["sweep_type"],
            "reclaim_status": best["reclaim"],
            "reclaim_window_bars": RECLAIM_WINDOW_BARS,
            "continuation_status": best["cont_status"],
            "trap_quality": best["trap"],
        })

        logger.info(
            "RTS-LIQ %s %s %s | pool=%s reclaim=%s intent=%s off=%.2f def=%.2f trap=%.2f",
            pair, bias, pool["pool_type"], pool["pool_type"],
            best["reclaim"], intent, best["offence"], best["defence"], best["trap"]
        )
        return raw
