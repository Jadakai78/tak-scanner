"""rts_bottle.py — RTS-BOTTLE: two-sided bottle reversal composite pattern detector.

Bullish bottle:
  1. Flush to a new low — liquidity sweep, stops run
  2. Wick rejection — immediate reclaim, long lower wick
  3. Higher lows forming — structure basing
  4. Bullish CHOCH confirmation — first higher high breaks structure
  5. Delta sponsorship — volume backing the reversal

Bearish bottle:
  1. Flush to a new high — breakout squeeze, stops run
  2. Wick rejection — immediate rejection, long upper wick
  3. Lower highs forming — structure capping
  4. Bearish CHOCH confirmation — first lower low breaks structure
  5. Delta sponsorship — volume backing the reversal

Intent output: ATTACK_TRAP (full sequence) | ATTACK | PROBE | WAIT
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    from ._common import build_signal, atr as calc_atr
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import build_signal, atr as calc_atr  # type: ignore

logger = logging.getLogger("strategies.rts_bottle")

LOOKBACK_BARS = 30
FLUSH_MIN_ATR = 1.2
WICK_REJECTION_MIN = 0.40
CHOCH_LOOKBACK = 10
DELTA_CONFIRM_MIN = 0.55
KILL_BUFFER_ATR = 0.15
MIN_RR = 2.0
ATR_PERIOD = 14


def _atr(df: pd.DataFrame) -> float:
    try:
        return float(calc_atr(df, ATR_PERIOD).iloc[-1])
    except Exception:
        hi = df["high"].iloc[-ATR_PERIOD:]
        lo = df["low"].iloc[-ATR_PERIOD:]
        cl = df["close"].iloc[-ATR_PERIOD:]
        tr = pd.concat(
            [hi - lo, (hi - cl.shift()).abs(), (lo - cl.shift()).abs()], axis=1
        ).max(axis=1)
        return float(tr.mean())


def _find_bull_flush(df: pd.DataFrame, atr_val: float) -> Optional[Dict[str, Any]]:
    window = df.iloc[-LOOKBACK_BARS:]
    period_low = float(window["low"].min())
    candidates: List[Dict[str, Any]] = []
    for i in range(len(window) - 1, -1, -1):
        row = window.iloc[i]
        body = float(row["open"] - row["close"])
        candle_range = float(row["high"] - row["low"])
        if body < FLUSH_MIN_ATR * atr_val:
            continue
        if float(row["low"]) > period_low * 1.002:
            continue
        candidates.append(
            {
                "idx": window.index[i],
                "flush_low": float(row["low"]),
                "flush_high": float(row["high"]),
                "flush_range": candle_range,
                "wick_reject": float(row["close"] - row["low"]),
                "side": "LONG",
            }
        )
    return candidates[0] if candidates else None


def _find_bear_flush(df: pd.DataFrame, atr_val: float) -> Optional[Dict[str, Any]]:
    window = df.iloc[-LOOKBACK_BARS:]
    period_high = float(window["high"].max())
    candidates: List[Dict[str, Any]] = []
    for i in range(len(window) - 1, -1, -1):
        row = window.iloc[i]
        body = float(row["close"] - row["open"])
        candle_range = float(row["high"] - row["low"])
        if body < FLUSH_MIN_ATR * atr_val:
            continue
        if float(row["high"]) < period_high * 0.998:
            continue
        candidates.append(
            {
                "idx": window.index[i],
                "flush_low": float(row["low"]),
                "flush_high": float(row["high"]),
                "flush_range": candle_range,
                "wick_reject": float(row["high"] - row["close"]),
                "side": "SHORT",
            }
        )
    return candidates[0] if candidates else None


def _wick_rejection(flush: Dict[str, Any]) -> bool:
    if float(flush["flush_range"]) == 0:
        return False
    return float(flush["wick_reject"]) / float(flush["flush_range"]) >= WICK_REJECTION_MIN


def _count_higher_lows(df: pd.DataFrame, flush_idx, flush_low: float) -> int:
    try:
        pos = df.index.get_loc(flush_idx)
    except KeyError:
        return 0
    post = df.iloc[pos + 1 :]
    if len(post) < 2:
        return 0
    count = 0
    prev = flush_low
    for lo in post["low"].values:
        if float(lo) > prev:
            count += 1
            prev = float(lo)
    return count


def _count_lower_highs(df: pd.DataFrame, flush_idx, flush_high: float) -> int:
    try:
        pos = df.index.get_loc(flush_idx)
    except KeyError:
        return 0
    post = df.iloc[pos + 1 :]
    if len(post) < 2:
        return 0
    count = 0
    prev = flush_high
    for hi in post["high"].values:
        if float(hi) < prev:
            count += 1
            prev = float(hi)
    return count


def _bull_choch_confirmed(df: pd.DataFrame, flush_idx) -> bool:
    try:
        pos = df.index.get_loc(flush_idx)
    except KeyError:
        return False
    post = df.iloc[pos + 1 : pos + 1 + CHOCH_LOOKBACK]
    pre = df.iloc[max(0, pos - LOOKBACK_BARS) : pos]
    if len(post) == 0 or len(pre) == 0:
        return False
    prior_swing_high = float(pre["high"].max())
    return bool((post["close"] > prior_swing_high).any())


def _bear_choch_confirmed(df: pd.DataFrame, flush_idx) -> bool:
    try:
        pos = df.index.get_loc(flush_idx)
    except KeyError:
        return False
    post = df.iloc[pos + 1 : pos + 1 + CHOCH_LOOKBACK]
    pre = df.iloc[max(0, pos - LOOKBACK_BARS) : pos]
    if len(post) == 0 or len(pre) == 0:
        return False
    prior_swing_low = float(pre["low"].min())
    return bool((post["close"] < prior_swing_low).any())


def _score_bottle(
    wick_ok: bool,
    structure_count: int,
    choch: bool,
    delta_score: float,
) -> Tuple[float, float, float, str]:
    defence = 0.0
    defence += 0.25 if wick_ok else 0.0
    defence += min(structure_count, 3) * 0.15
    defence += 0.20 if choch else 0.0
    defence = min(defence, 0.95)

    offence = 0.0
    offence += 0.30 if choch else 0.10
    offence += min(delta_score, 0.40)
    offence += 0.15 if structure_count >= 2 else 0.0
    offence = min(offence, 0.95)

    trap = 0.70
    trap -= 0.20 if wick_ok else 0.0
    trap -= min(structure_count, 3) * 0.08
    trap -= 0.15 if choch else 0.0
    trap -= min(delta_score * 0.3, 0.15)
    trap = max(trap, 0.05)

    if choch and structure_count >= 2 and wick_ok and delta_score >= DELTA_CONFIRM_MIN:
        intent = "ATTACK_TRAP"
    elif choch and structure_count >= 1:
        intent = "ATTACK"
    elif structure_count >= 2 and wick_ok:
        intent = "PROBE"
    else:
        intent = "WAIT"

    return offence, defence, trap, intent


def _build_reasons(
    side: str,
    wick_ok: bool,
    structure_count: int,
    choch: bool,
    delta_score: float,
    intent: str,
) -> List[str]:
    reasons: List[str] = []
    if side == "LONG":
        if wick_ok:
            reasons.append("Wick rejection at flush low — stops absorbed")
        if structure_count >= 2:
            reasons.append(f"{structure_count} higher lows — base forming")
        elif structure_count == 1:
            reasons.append("1 higher low — base starting")
        if choch:
            reasons.append("CHOCH confirmed — structure flipped bullish")
    else:
        if wick_ok:
            reasons.append("Wick rejection at flush high — breakout failed")
        if structure_count >= 2:
            reasons.append(f"{structure_count} lower highs — cap forming")
        elif structure_count == 1:
            reasons.append("1 lower high — cap starting")
        if choch:
            reasons.append("CHOCH confirmed — structure flipped bearish")

    if delta_score >= DELTA_CONFIRM_MIN:
        reasons.append(f"Delta sponsorship {delta_score:.2f} — volume backing reversal")
    elif delta_score > 0:
        reasons.append(f"Delta weak {delta_score:.2f} — watch for volume confirmation")

    if intent == "ATTACK_TRAP":
        reasons.append("Full bottle sequence — ATTACK_TRAP")
    elif intent == "ATTACK":
        reasons.append("CHOCH confirmed, delta light — ATTACK")
    elif intent == "PROBE":
        reasons.append("Base forming, await CHOCH — PROBE")
    return reasons


class RTSBottle:
    """Two-sided bottle reversal composite pattern detector."""

    ID = "RTS_BOTTLE"
    REQUIRED_REGIMES = ["RANGE", "VOLATILE", "FEAR", "TREND_DOWN", "TREND_UP"]
    ENGINE_TYPE = "RTS_REVERSAL"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def generate(
        self,
        pair: str,
        ohlc_df: pd.DataFrame,
        regime: str,
        fg_score: int,
        ai_st: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        """Detect bullish or bearish bottle reversal and return signal or None."""
        df = ohlc_df.copy() if ohlc_df is not None else None
        ai_st = ai_st or {}
        rsi_val = float(ai_st.get("rsi", 50.0))
        vol_ratio = float(ai_st.get("vol_ratio", 1.0))
        delta_score = float(kwargs.get("delta_score", ai_st.get("delta_score", 0.0)))

        if df is None or len(df) < LOOKBACK_BARS + 5:
            return None

        atr_val = _atr(df)
        if atr_val <= 0:
            return None

        candidates: List[Dict[str, Any]] = []

        bull_flush = _find_bull_flush(df, atr_val)
        if bull_flush is not None:
            wick_ok = _wick_rejection(bull_flush)
            structure_count = _count_higher_lows(df, bull_flush["idx"], float(bull_flush["flush_low"]))
            choch = _bull_choch_confirmed(df, bull_flush["idx"])
            if wick_ok and structure_count >= 1:
                offence, defence, trap, intent = _score_bottle(wick_ok, structure_count, choch, delta_score)
                if intent != "WAIT":
                    candidates.append(
                        {
                            "side": "LONG",
                            "flush": bull_flush,
                            "structure_count": structure_count,
                            "choch": choch,
                            "offence": offence,
                            "defence": defence,
                            "trap": trap,
                            "intent": intent,
                            "wick_ok": wick_ok,
                        }
                    )

        bear_flush = _find_bear_flush(df, atr_val)
        if bear_flush is not None:
            wick_ok = _wick_rejection(bear_flush)
            structure_count = _count_lower_highs(df, bear_flush["idx"], float(bear_flush["flush_high"]))
            choch = _bear_choch_confirmed(df, bear_flush["idx"])
            if wick_ok and structure_count >= 1:
                offence, defence, trap, intent = _score_bottle(wick_ok, structure_count, choch, delta_score)
                if intent != "WAIT":
                    candidates.append(
                        {
                            "side": "SHORT",
                            "flush": bear_flush,
                            "structure_count": structure_count,
                            "choch": choch,
                            "offence": offence,
                            "defence": defence,
                            "trap": trap,
                            "intent": intent,
                            "wick_ok": wick_ok,
                        }
                    )

        if not candidates:
            return None

        candidates.sort(
            key=lambda c: (
                c["intent"] == "ATTACK_TRAP",
                c["offence"] + c["defence"] - c["trap"],
            ),
            reverse=True,
        )
        best = candidates[0]

        side = str(best["side"])
        flush = best["flush"]
        structure_count = int(best["structure_count"])
        choch = bool(best["choch"])
        offence = float(best["offence"])
        defence = float(best["defence"])
        trap = float(best["trap"])
        intent = str(best["intent"])
        wick_ok = bool(best["wick_ok"])

        entry = float(df["close"].iloc[-1])
        if side == "LONG":
            sl = float(flush["flush_low"]) - KILL_BUFFER_ATR * atr_val
            risk = entry - sl
            if risk <= 0:
                return None
            tp = entry + risk * MIN_RR
            kill_condition = (
                f"Close below flush wick low {flush['flush_low']:.6g} "
                f"(kill ≤ {sl:.6g})"
            )
        else:
            sl = float(flush["flush_high"]) + KILL_BUFFER_ATR * atr_val
            risk = sl - entry
            if risk <= 0:
                return None
            tp = entry - risk * MIN_RR
            kill_condition = (
                f"Close above flush wick high {flush['flush_high']:.6g} "
                f"(kill ≥ {sl:.6g})"
            )

        raw = build_signal(
            pair=pair,
            bias=side,
            engine=self.ID,
            regime=regime,
            entry=entry,
            sl=sl,
            tp=tp,
            structure_quality=defence,
            rsi_val=rsi_val,
            vol_ratio=vol_ratio,
            fg_score=fg_score,
            kill_condition=kill_condition,
            min_rr=MIN_RR,
            extra={
                "pattern": "BOTTLE_REVERSAL",
                "side": side,
                "rts_family": "BOTTLE",
                "intent": intent,
                "offence_score": round(offence, 3),
                "defence_score": round(defence, 3),
                "trap_score": round(trap, 3),
                "flush_low": flush["flush_low"],
                "flush_high": flush["flush_high"],
                "structure_count": structure_count,
                "choch": choch,
                "wick_ok": wick_ok,
                "delta_score": round(delta_score, 3),
                "rts_reasons": _build_reasons(
                    side, wick_ok, structure_count, choch, delta_score, intent
                ),
            },
        )
        if raw is None:
            return None

        logger.info(
            "RTS-BOTTLE %s | side=%s intent=%s off=%.2f def=%.2f trap=%.2f structure=%d choch=%s delta=%.2f",
            pair,
            side,
            intent,
            offence,
            defence,
            trap,
            structure_count,
            choch,
            delta_score,
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
