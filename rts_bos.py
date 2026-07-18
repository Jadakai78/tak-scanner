"""rts_bos.py — RTS-BOS: BOS Retest Continuation engine.

Waits for a structural Break of Structure, then trades the retest.
Does NOT anticipate the break — follows it per foundation rule.
Intent: ATTACK_BREAK (valid retest), PROBE (marginal), WAIT (no retest yet).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

try:
    from ._common import swing_highs, swing_lows, build_signal, atr as calc_atr, rsi as calc_rsi
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import swing_highs, swing_lows, build_signal, atr as calc_atr, rsi as calc_rsi  # type: ignore

logger = logging.getLogger("strategies.rts_bos")

ATR_PERIOD = 14
BOS_RETEST_BARS = 10  # bars to look back for BOS retest opportunity
RETEST_TOLERANCE = 0.30  # ATR units — how close to BOS level counts as retest
KILL_BUFFER_ATR = 0.20
MIN_RR = 2.0


def _scalar(x: Any, default: float = 0.0) -> float:
    try:
        if hasattr(x, "iloc"):
            return float(x.iloc[-1])
        return float(x)
    except Exception:
        return float(default)


def _atr(df: pd.DataFrame) -> float:
    try:
        return float(calc_atr(df, ATR_PERIOD).iloc[-1])
    except Exception:
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - df["close"].shift()).abs(),
                (df["low"] - df["close"].shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return float(tr.iloc[-ATR_PERIOD:].mean())


def _detect_bos(df: pd.DataFrame, atr_val: float) -> Optional[Dict[str, Any]]:
    """
    Find the most recent BOS event and check if current bar is retesting it.

    BOS bullish: current bar broke above the last confirmed swing high,
    and now price has pulled back to within RETEST_TOLERANCE.
    BOS bearish: current bar broke below the last confirmed swing low,
    and now price has pulled back to within RETEST_TOLERANCE.
    """
    if len(df) < BOS_RETEST_BARS + 6:
        return None

    window = df.iloc[-(BOS_RETEST_BARS + 4) :]
    h_idx = swing_highs(window, left=2, right=2)
    l_idx = swing_lows(window, left=2, right=2)

    if not h_idx and not l_idx:
        return None

    last = df.iloc[-1]
    cur_close = _scalar(last["close"])
    cur_low = _scalar(last["low"])
    cur_high = _scalar(last["high"])
    tol = RETEST_TOLERANCE * atr_val

    # Check bullish BOS: price previously broke above swing high
    if h_idx:
        bos_high = _scalar(window["high"].iloc[h_idx[-1]])
        bos_closed_above = (window["close"] > bos_high).any()
        if bos_closed_above:
            if cur_low <= bos_high + tol and cur_close > bos_high - tol:
                return {
                    "direction": "BULLISH",
                    "bos_level": bos_high,
                    "bias": "LONG",
                    "retest_valid": True,
                    "distance_to_bos": abs(cur_low - bos_high),
                }

    # Check bearish BOS: price previously broke below swing low
    if l_idx:
        bos_low = _scalar(window["low"].iloc[l_idx[-1]])
        bos_closed_below = (window["close"] < bos_low).any()
        if bos_closed_below:
            if cur_high >= bos_low - tol and cur_close < bos_low + tol:
                return {
                    "direction": "BEARISH",
                    "bos_level": bos_low,
                    "bias": "SHORT",
                    "retest_valid": True,
                    "distance_to_bos": abs(cur_high - bos_low),
                }

    return None


def _score_bos(
    bos: Dict[str, Any], atr_val: float, fg_score: int, rsi_val: float, regime: str
) -> tuple:
    """Returns (offence, defence, trap, intent)."""
    dist = bos.get("distance_to_bos", 0.0)
    precision = max(0.0, 1.0 - dist / max(atr_val, 1e-9))

    trap_score = 0.55
    offence_score = min(1.0, 0.60 + precision * 0.20)
    defence_score = min(1.0, 0.65 + precision * 0.15)

    if regime in ("TREND_UP",) and bos["direction"] == "BULLISH":
        offence_score = min(1.0, offence_score + 0.10)
    if regime in ("TREND_DOWN",) and bos["direction"] == "BEARISH":
        offence_score = min(1.0, offence_score + 0.10)

    if offence_score >= 0.72 and precision >= 0.70:
        intent = "ATTACK_BREAK"
    elif offence_score >= 0.55:
        intent = "PROBE"
    else:
        intent = "WAIT"

    return offence_score, defence_score, trap_score, intent


class RTSBos:
    """RTS-BOS — BOS Retest Continuation engine."""

    ENGINE = "RTS_BOS"
    REQUIRED_REGIMES = ["TREND_UP", "TREND_DOWN", "VOLATILE", "RANGE", "FEAR"]

    def generate(
        self,
        pair: str,
        ohlc_df: pd.DataFrame,
        regime: str,
        fg_score: int,
        ai_st: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if len(ohlc_df) < 20:
            return None

        df = ohlc_df.loc[:, ~ohlc_df.columns.duplicated()].copy()

        atr_val = _atr(df)
        if atr_val <= 0:
            return None

        bos = _detect_bos(df, atr_val)
        if bos is None or not bos.get("retest_valid"):
            return None

        rsi_series = calc_rsi(df)
        rsi_val = float(rsi_series.iloc[-1]) if hasattr(rsi_series, "iloc") else 50.0

        offence, defence, trap, intent = _score_bos(bos, atr_val, fg_score, rsi_val, regime)

        if intent in ("WAIT", "IGNORE"):
            return None

        bias = bos["bias"]
        bos_level = bos["bos_level"]
        close = _scalar(df["close"].iloc[-1])
        entry = close

        if bias == "LONG":
            sl = bos_level - KILL_BUFFER_ATR * atr_val
            kill_level = sl
        else:
            sl = bos_level + KILL_BUFFER_ATR * atr_val
            kill_level = sl

        risk = abs(entry - sl)
        if risk <= 0:
            return None

        tp = entry + risk * MIN_RR if bias == "LONG" else entry - risk * MIN_RR

        kill_condition = f"RTS-BOS kill: BOS level {bos_level:.4f} reclaimed against thesis"

        raw = build_signal(
            pair=pair,
            bias=bias,
            engine="RTS_BOS",
            regime=regime,
            entry=entry,
            sl=sl,
            tp=tp,
            structure_quality=offence,
            rsi_val=rsi_val,
            vol_ratio=1.0,
            fg_score=fg_score,
            kill_condition=kill_condition,
            min_rr=MIN_RR,
        )

        if raw is None:
            return None

        raw.update(
            {
                "rts_family": "BOS",
                "intent": intent,
                "kill_level": kill_level,
                "auto_cut": False,
                "offence_score": offence,
                "defence_score": defence,
                "trap_score": trap,
                "bos_level": bos_level,
                "bos_direction": bos["direction"],
                "bos_retest_valid": bos["retest_valid"],
                "bos_distance": bos.get("distance_to_bos", 0.0),
            }
        )

        logger.info(
            "RTS-BOS %s %s %s | intent=%s off=%.2f def=%.2f",
            pair,
            bias,
            bos["direction"],
            intent,
            offence,
            defence,
        )


        # ── RTS-DELTA overlay — apply delta_modifier to offence_score ─────────
        try:
            from rts_delta import score_delta_context
            _delta_ctx = score_delta_context(df, bias)
            _mod = float(_delta_ctx.get("delta_modifier", 0.0))
            if _mod != 0.0:
                raw["offence_score"] = round(
                    min(1.0, max(0.0, float(raw.get("offence_score", 0.65)) + _mod)), 3
                )
                # Re-evaluate intent if modifier pushes score over/under thresholds
                _off = raw["offence_score"]
                _trap = float(raw.get("trap_score", 0.0))
                _old_intent = raw.get("intent", "WAIT")
                if _old_intent in ("WAIT", "PROBE") and _off >= 0.72 and _trap >= 0.75:
                    raw["intent"] = "ATTACK_TRAP"
                elif _old_intent == "WAIT" and _off >= 0.60:
                    raw["intent"] = "PROBE"
                elif _old_intent in ("ATTACK_TRAP", "ATTACK_BREAK", "ATTACK") and _off < 0.50:
                    raw["intent"] = "PROBE"
            raw.update({
                "delta_bias":          _delta_ctx.get("delta_bias"),
                "sponsorship_quality": _delta_ctx.get("sponsorship_quality"),
                "delta_modifier":      _mod,
                "vp_context":          _delta_ctx.get("vp_context"),
                "vpoc":                _delta_ctx.get("vpoc"),
                "vah":                 _delta_ctx.get("vah"),
                "val":                 _delta_ctx.get("val"),
            })
        except Exception as _delta_exc:
            logger.debug("RTS-DELTA overlay skipped: %s", _delta_exc)

        return raw
