"""rts_choch.py — RTS-CHOCH: Structure Flip Specialist.

Detects Change of Character (CHOCH) after a liquidity sweep event.
Requires: sweep → opposing BOS → delta support.
Intent: ATTACK_TRAP (reversal after sweep), ATTACK_BREAK (continuation CHOCH).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

try:
    from ._common import swing_highs, swing_lows, build_signal, atr as calc_atr, rsi as calc_rsi
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import swing_highs, swing_lows, build_signal, atr as calc_atr, rsi as calc_rsi  # type: ignore

logger = logging.getLogger("strategies.rts_choch")

ATR_PERIOD = 14
CHOCH_WINDOW = 8  # bars after sweep to find opposing BOS
BOS_THRESHOLD_ATR = 0.30  # how far price must break beyond swing to count as BOS
KILL_BUFFER_ATR = 0.25
MIN_RR = 2.0


def _scalar(x: Any, default: float = 0.0) -> float:
    """
    Safe scalar extractor.

    Handles cases where x is a Series (e.g., duplicate labels or non-unique selection)
    by taking the last element. Falls back to default on error.
    """
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


def _detect_choch(
    df: pd.DataFrame, atr_val: float, window: int = CHOCH_WINDOW
) -> Optional[Dict[str, Any]]:
    """
    Scan recent bars for a CHOCH pattern:
    1. Price sweeps a swing extreme (LIQ event)
    2. Within `window` bars, price breaks the opposing swing in the new direction
    3. Returns choch dict or None
    """
    if len(df) < window + 6:
        return None

    recent = df.iloc[-(window + 4) :]
    h_idx = swing_highs(recent, left=2, right=2)
    l_idx = swing_lows(recent, left=2, right=2)

    if not h_idx or not l_idx:
        return None

    last_close = _scalar(df["close"].iloc[-1])
    last_high = _scalar(df["high"].iloc[-1])
    last_low = _scalar(df["low"].iloc[-1])

    # Major recent swing reference
    major_high = _scalar(recent["high"].iloc[h_idx[-1]])
    major_low = _scalar(recent["low"].iloc[l_idx[-1]])

    # CHOCH bullish: price swept below major_low, then broke above a swing high
    if last_low < major_low and last_close > major_high:
        sweep_level = major_low
        choch_level = major_high
        displacement = (last_high - choch_level) / max(atr_val, 1e-9)
        return {
            "direction": "BULLISH",
            "sweep_level": sweep_level,
            "choch_level": choch_level,
            "displacement": displacement,
            "bias": "LONG",
        }

    # CHOCH bearish: price swept above major_high, then broke below a swing low
    if last_high > major_high and last_close < major_low:
        sweep_level = major_high
        choch_level = major_low
        displacement = (choch_level - last_low) / max(atr_val, 1e-9)
        return {
            "direction": "BEARISH",
            "sweep_level": sweep_level,
            "choch_level": choch_level,
            "displacement": displacement,
            "bias": "SHORT",
        }

    # Partial CHOCH: sweep only (no BOS yet) — WAIT state
    if last_low < major_low:
        return {
            "direction": "PARTIAL_BULLISH",
            "sweep_level": major_low,
            "choch_level": None,
            "displacement": 0.0,
            "bias": "LONG",
        }

    if last_high > major_high:
        return {
            "direction": "PARTIAL_BEARISH",
            "sweep_level": major_high,
            "choch_level": None,
            "displacement": 0.0,
            "bias": "SHORT",
        }

    return None


def _score_choch(
    choch: Dict[str, Any], fg_score: int, rsi_val: float
) -> tuple:
    """Returns (offence, defence, trap, intent)."""
    direction = choch["direction"]
    displacement = choch.get("displacement", 0.0)

    if direction in ("BULLISH", "BEARISH"):
        # Full CHOCH confirmed
        trap_score = min(1.0, 0.72 + displacement * 0.08)
        offence_score = min(1.0, 0.65 + displacement * 0.10)
        defence_score = 0.78  # clear kill at CHOCH level

        # RSI divergence bonus
        if direction == "BULLISH" and rsi_val < 35:
            offence_score = min(1.0, offence_score + 0.08)
        if direction == "BEARISH" and rsi_val > 65:
            offence_score = min(1.0, offence_score + 0.08)

        if offence_score >= 0.72 and trap_score >= 0.75:
            intent = "ATTACK_TRAP"
        elif offence_score >= 0.60:
            intent = "PROBE"
        else:
            intent = "WAIT"

    elif direction in ("PARTIAL_BULLISH", "PARTIAL_BEARISH"):
        trap_score = 0.55
        offence_score = 0.50
        defence_score = 0.65
        intent = "WAIT"

    else:
        return 0.0, 0.0, 0.0, "IGNORE"

    return offence_score, defence_score, trap_score, intent


class RTSChoch:
    """RTS-CHOCH — Structure Flip engine."""

    ENGINE = "RTS_CHOCH"
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

        # Strip duplicate columns to avoid Series-return surprises
        df = ohlc_df.loc[:, ~ohlc_df.columns.duplicated()].copy()

        atr_val = _atr(df)
        if atr_val <= 0:
            return None

        choch = _detect_choch(df, atr_val)
        if choch is None:
            return None

        rsi_series = calc_rsi(df)
        rsi_val = float(rsi_series.iloc[-1]) if hasattr(rsi_series, "iloc") else 50.0

        offence, defence, trap, intent = _score_choch(choch, fg_score, rsi_val)

        if intent == "IGNORE":
            return None

        bias = choch["bias"]
        choch_level = choch.get("choch_level")
        sweep_level = choch["sweep_level"]
        close = _scalar(df["close"].iloc[-1])
        entry = close

        if intent == "WAIT":
            return None  # no actionable level yet

        if choch_level is None:
            return None

        # Kill level: beyond sweep_level with buffer
        if bias == "LONG":
            sl = sweep_level - KILL_BUFFER_ATR * atr_val
            kill_level = sl
        else:
            sl = sweep_level + KILL_BUFFER_ATR * atr_val
            kill_level = sl

        risk = abs(entry - sl)
        if risk <= 0:
            return None

        tp = entry + risk * MIN_RR if bias == "LONG" else entry - risk * MIN_RR

        kill_condition = (
            f"RTS-CHOCH kill: CHOCH level {choch_level:.4f} fails / "
            f"sweep level {sweep_level:.4f} breached"
        )

        raw = build_signal(
            pair=pair,
            bias=bias,
            engine="RTS_CHOCH",
            regime=regime,
            entry=entry,
            sl=sl,
            tp=tp,
            structure_quality=trap,
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
                "rts_family": "CHOCH",
                "intent": intent,
                "kill_level": kill_level,
                "auto_cut": False,
                "offence_score": offence,
                "defence_score": defence,
                "trap_score": trap,
                # CHOCH-specific
                "choch_direction": choch["direction"],
                "choch_level": choch_level,
                "choch_sweep_level": sweep_level,
                "choch_displacement": choch.get("displacement", 0.0),
                "flip_confirmed": choch["direction"] in ("BULLISH", "BEARISH"),
            }
        )

        logger.info(
            "RTS-CHOCH %s %s %s | intent=%s off=%.2f def=%.2f trap=%.2f",
            pair,
            bias,
            choch["direction"],
            intent,
            offence,
            defence,
            trap,
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
