"""rts_zone.py — RTS-ZONE: Unmitigated Inventory engine.

Tracks unmitigated support/resistance zones (order-block-like areas).
First touch = tradeable. Second touch = zone dies, auto-cut.
Inherits DeltaSR zone philosophy: structure is inventory.
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

logger = logging.getLogger("strategies.rts_zone")

ATR_PERIOD = 14
ZONE_LOOKBACK = 50
ZONE_TOLERANCE = 0.35
TOUCH_TOLERANCE = 0.20
KILL_BUFFER_ATR = 0.20
MIN_RR = 2.0
MAX_ZONES = 5


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


def _build_zones(df: pd.DataFrame, atr_val: float) -> List[Dict[str, Any]]:
    """
    Identify unmitigated zones from swing pivots.

    A zone is 'unmitigated' if price has returned to it fewer than 2 times
    since formation.
    """
    tol = ZONE_TOLERANCE * atr_val

    window = df.iloc[-ZONE_LOOKBACK:]
    h_idx = swing_highs(window, left=3, right=3)
    l_idx = swing_lows(window, left=3, right=3)

    zones: List[Dict[str, Any]] = []

    for i in h_idx:
        pivot = _scalar(window["high"].iloc[i])
        zone_top = pivot + tol / 2
        zone_bot = pivot - tol / 2
        created_bar = i

        future = window.iloc[i + 1 :]
        touches = int(((future["high"] >= zone_bot) & (future["low"] <= zone_top)).sum())
        mitigated = touches >= 2

        zones.append(
            {
                "is_resistance": True,
                "top": zone_top,
                "bottom": zone_bot,
                "pivot": pivot,
                "created_bar": created_bar,
                "touches": touches,
                "mitigated": mitigated,
            }
        )

    for i in l_idx:
        pivot = _scalar(window["low"].iloc[i])
        zone_top = pivot + tol / 2
        zone_bot = pivot - tol / 2
        created_bar = i

        future = window.iloc[i + 1 :]
        touches = int(((future["high"] >= zone_bot) & (future["low"] <= zone_top)).sum())
        mitigated = touches >= 2

        zones.append(
            {
                "is_resistance": False,
                "top": zone_top,
                "bottom": zone_bot,
                "pivot": pivot,
                "created_bar": created_bar,
                "touches": touches,
                "mitigated": mitigated,
            }
        )

    zones = [z for z in zones if not z["mitigated"]]
    zones.sort(key=lambda z: z["created_bar"], reverse=True)
    return zones[:MAX_ZONES]


def _price_in_zone(price: float, zone: Dict[str, Any], atr_val: float) -> bool:
    touch_tol = TOUCH_TOLERANCE * atr_val
    return zone["bottom"] - touch_tol <= price <= zone["top"] + touch_tol


def _score_zone(
    zone: Dict[str, Any], regime: str, fg_score: int, rsi_val: float
) -> tuple:
    """Returns (offence, defence, trap, intent)."""
    touches = zone["touches"]
    is_resistance = zone["is_resistance"]

    if touches == 0:
        base_trap = 0.78
        base_offence = 0.72
    elif touches == 1:
        base_trap = 0.55
        base_offence = 0.58
    else:
        return 0.0, 0.0, 0.0, "IGNORE"

    defence_score = 0.75
    trap_score = base_trap
    offence_score = base_offence

    if not is_resistance and fg_score < 25:
        offence_score = min(1.0, offence_score + 0.08)
    if is_resistance and fg_score > 75:
        offence_score = min(1.0, offence_score + 0.08)

    if offence_score >= 0.70 and trap_score >= 0.75:
        intent = "ATTACK"
    elif offence_score >= 0.55:
        intent = "PROBE"
    else:
        intent = "WAIT"

    return offence_score, defence_score, trap_score, intent


class RTSZone:
    """RTS-ZONE — Unmitigated Zone Inventory engine."""

    ENGINE = "RTS_ZONE"
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

        zones = _build_zones(df, atr_val)
        if not zones:
            return None

        close = _scalar(df["close"].iloc[-1])

        rsi_series = calc_rsi(df)
        rsi_val = float(rsi_series.iloc[-1]) if hasattr(rsi_series, "iloc") else 50.0

        best = None
        best_score = -1.0

        for zone in zones:
            if not _price_in_zone(close, zone, atr_val):
                continue

            offence, defence, trap, intent = _score_zone(zone, regime, fg_score, rsi_val)

            if intent == "IGNORE":
                continue

            rank = trap * 0.40 + offence * 0.35 + defence * 0.25
            if rank > best_score:
                best_score = rank
                best = {
                    "zone": zone,
                    "offence": offence,
                    "defence": defence,
                    "trap": trap,
                    "intent": intent,
                }

        if best is None:
            return None

        zone = best["zone"]
        intent = best["intent"]
        is_resistance = zone["is_resistance"]

        bias = "SHORT" if is_resistance else "LONG"
        entry = close

        if bias == "LONG":
            sl = zone["bottom"] - KILL_BUFFER_ATR * atr_val
            kill_level = sl
        else:
            sl = zone["top"] + KILL_BUFFER_ATR * atr_val
            kill_level = sl

        risk = abs(entry - sl)
        if risk <= 0:
            return None

        tp = entry + risk * MIN_RR if bias == "LONG" else entry - risk * MIN_RR

        kill_condition = (
            f"RTS-ZONE kill: zone {'bottom' if bias == 'LONG' else 'top'} "
            f"{zone['bottom']:.4f}–{zone['top']:.4f} breached or second touch"
        )

        raw = build_signal(
            pair=pair,
            bias=bias,
            engine="RTS_ZONE",
            regime=regime,
            entry=entry,
            sl=sl,
            tp=tp,
            structure_quality=best["trap"],
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
                "rts_family": "ZONE",
                "intent": intent,
                "kill_level": kill_level,
                "auto_cut": zone["touches"] >= 1,
                "offence_score": best["offence"],
                "defence_score": best["defence"],
                "trap_score": best["trap"],
                "zone_top": zone["top"],
                "zone_bottom": zone["bottom"],
                "zone_touches": zone["touches"],
                "zone_is_resistance": is_resistance,
                "zone_mitigated": zone["mitigated"],
            }
        )

        logger.info(
            "RTS-ZONE %s %s %s | touches=%d intent=%s off=%.2f def=%.2f trap=%.2f",
            pair,
            bias,
            "RESISTANCE" if is_resistance else "SUPPORT",
            zone["touches"],
            intent,
            best["offence"],
            best["defence"],
            best["trap"],
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
