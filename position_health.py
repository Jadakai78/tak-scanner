"""position_health.py — Live open-position health scoring (JHL v2 upgrade).

Scores an open position 0-100 across five weighted sub-scores so the trader
(and the live feed) can see at a glance whether a trade is behaving, fading,
or needs to be cut:

    price_vs_entry     (30%) — progress toward TP vs SL from entry.
    candle_quality     (25%) — current candle body/wick structure vs bias.
    volume_trend       (20%) — current volume vs the 20-period average.
    ai_st_aligned      (15%) — does AISupertrend still agree with the trade?
    time_vs_3candle    (10%) — candles elapsed vs the 3-candle max-hold rule.

Color bands: GREEN(75-100) / YELLOW(50-74) / RED(25-49) / BLACK(0-24=CLOSE NOW).
Sprint mode tightens both ends: BLACK threshold rises to <35 (cut sooner) and
GREEN requires >=80.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("position_health")

# Sub-score weights (must sum to 1.0).
WEIGHTS: Dict[str, float] = {
    "price_vs_entry": 0.30,
    "candle_quality": 0.25,
    "volume_trend": 0.20,
    "ai_st_aligned": 0.15,
    "time_vs_3candle_rule": 0.10,
}

DEFAULT_MAX_CANDLES = 3

# Normal-mode color thresholds (inclusive lower bound).
NORMAL_BANDS = [
    (75, 100, "GREEN"),
    (50, 74, "YELLOW"),
    (25, 49, "RED"),
    (0, 24, "BLACK"),
]

RECOMMENDATIONS = {
    "GREEN": "HOLD — trade healthy, let it run.",
    "YELLOW": "MONITOR — losing steam, watch next candle closely.",
    "RED": "TIGHTEN — consider partial close / move stop to breakeven.",
    "BLACK": "CLOSE NOW — health critical, cut the position.",
}


class PositionHealthManager:
    """Computes a 0-100 health score for an open position.

    Attributes:
        sprint_mode: When True, uses the tighter sprint-mode color bands.
    """

    def __init__(self, sprint_mode: bool = False) -> None:
        """Initialize the manager.

        Args:
            sprint_mode: Whether to apply sprint-mode thresholds (BLACK < 35,
                GREEN >= 80) instead of the normal bands.
        """
        self.sprint_mode = bool(sprint_mode)

    # ------------------------------------------------------------------
    # Sub-scores
    # ------------------------------------------------------------------
    @staticmethod
    def _score_price_vs_entry(position: Dict[str, Any]) -> float:
        """Score progress from entry toward TP vs SL (0-100).

        100 = price sitting right at TP; 0 = price sitting right at (or past)
        SL; 50 = still at entry (no progress either way).
        """
        try:
            entry = float(position["entry"])
            sl = float(position["sl"])
            tp = float(position["tp"])
            current = float(position.get("current_price", entry))
            bias = str(position.get("bias", "LONG")).upper()
        except (KeyError, TypeError, ValueError):
            return 50.0

        if bias == "SHORT":
            entry, sl, tp, current = -entry, -sl, -tp, -current

        total_range = tp - entry
        if total_range == 0:
            return 50.0

        progress = (current - entry) / total_range  # 0..1 toward TP, <0 toward/through SL
        risk_range = entry - sl
        if progress < 0 and risk_range != 0:
            # Moving toward SL: scale 50 -> 0 as we approach/exceed the stop.
            loss_progress = (entry - current) / risk_range
            score = 50.0 - min(max(loss_progress, 0.0), 1.0) * 50.0
        else:
            score = 50.0 + min(max(progress, 0.0), 1.0) * 50.0
        return float(np.clip(score, 0.0, 100.0))

    @staticmethod
    def _score_candle_quality(candles_df: Optional[pd.DataFrame], bias: str) -> float:
        """Score the current candle's body/wick structure vs trade direction.

        A strong candle in the trade's favor (large body, small opposing
        wick) scores high; a candle structurally against the trade scores
        low.
        """
        if candles_df is None or len(candles_df) == 0:
            return 50.0
        try:
            last = candles_df.iloc[-1]
            o, h, l, c = float(last["open"]), float(last["high"]), float(last["low"]), float(last["close"])
        except (KeyError, IndexError, ValueError):
            return 50.0

        rng = h - l
        if rng <= 0:
            return 50.0

        body = abs(c - o)
        body_ratio = body / rng
        bullish = c >= o
        is_long = str(bias).upper() == "LONG"

        # Directional alignment: candle color matches trade direction?
        aligned = bullish if is_long else not bullish

        # Wick against the trade direction (upper wick hurts longs, lower wick hurts shorts).
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        adverse_wick = (upper_wick if is_long else lower_wick) / rng

        base = 50.0 + (body_ratio * 40.0 if aligned else -body_ratio * 40.0)
        base -= adverse_wick * 20.0
        return float(np.clip(base, 0.0, 100.0))

    @staticmethod
    def _score_volume_trend(candles_df: Optional[pd.DataFrame]) -> float:
        """Score current volume vs the trailing 20-period average.

        At/above average volume scores high (conviction); well below average
        scores low (fading interest).
        """
        if candles_df is None or len(candles_df) < 2 or "volume" not in candles_df.columns:
            return 50.0
        try:
            window = candles_df["volume"].tail(20)
            avg = float(window.mean())
            current = float(candles_df["volume"].iloc[-1])
        except (KeyError, ValueError):
            return 50.0
        if avg <= 0:
            return 50.0

        ratio = current / avg
        # ratio 1.0 -> 60, ratio 0.0 -> 20, ratio >=2.0 -> 100 (clipped)
        score = 60.0 + (ratio - 1.0) * 40.0
        return float(np.clip(score, 0.0, 100.0))

    @staticmethod
    def _score_ai_st_aligned(ai_st_signal: Optional[Dict[str, Any]], bias: str) -> float:
        """Score whether AISupertrend still agrees with the trade direction."""
        if not ai_st_signal:
            return 50.0
        direction = str(ai_st_signal.get("direction", "")).upper()
        bias_u = str(bias).upper()
        if not direction:
            return 50.0
        if direction == bias_u:
            strength = float(ai_st_signal.get("signal_strength", 0.7) or 0.7)
            return float(np.clip(60.0 + strength * 40.0, 0.0, 100.0))
        # Disagreement — the stronger the opposing signal, the worse.
        strength = float(ai_st_signal.get("signal_strength", 0.7) or 0.7)
        return float(np.clip(40.0 - strength * 40.0, 0.0, 100.0))

    @staticmethod
    def _score_time_vs_3candle(candles_elapsed: int, max_candles: int = DEFAULT_MAX_CANDLES) -> float:
        """Score candles elapsed vs the max-hold rule (fresher trade = higher)."""
        if max_candles <= 0:
            return 50.0
        remaining_ratio = 1.0 - (candles_elapsed / max_candles)
        score = 50.0 + remaining_ratio * 50.0
        return float(np.clip(score, 0.0, 100.0))

    # ------------------------------------------------------------------
    def _color_for(self, score: float) -> str:
        """Map a 0-100 score to a color band, honoring sprint-mode thresholds."""
        if self.sprint_mode:
            if score < 35:
                return "BLACK"
            if score >= 80:
                return "GREEN"
            if score >= 50:
                return "YELLOW"
            return "RED"
        for lo, hi, color in NORMAL_BANDS:
            if lo <= score <= hi:
                return color
        return "BLACK"

    # ------------------------------------------------------------------
    def score_position(
        self,
        position_dict: Dict[str, Any],
        candles_df: Optional[pd.DataFrame],
        ai_st_signal: Optional[Dict[str, Any]],
        candles_elapsed: int,
        max_candles: int = DEFAULT_MAX_CANDLES,
    ) -> Dict[str, Any]:
        """Score an open position's health.

        Args:
            position_dict: Position dict with at least entry/sl/tp/bias and
                optionally current_price.
            candles_df: Recent OHLCV DataFrame for the pair (most-recent last).
            ai_st_signal: Latest AISupertrend output ``{direction, signal_strength}``.
            candles_elapsed: Number of candles the position has been open.
            max_candles: Max-hold candle count (3-candle rule by default).

        Returns:
            ``{score, color, sub_scores, recommendation}``.
        """
        bias = str(position_dict.get("bias", "LONG")).upper()

        sub_scores = {
            "price_vs_entry": self._score_price_vs_entry(position_dict),
            "candle_quality": self._score_candle_quality(candles_df, bias),
            "volume_trend": self._score_volume_trend(candles_df),
            "ai_st_aligned": self._score_ai_st_aligned(ai_st_signal, bias),
            "time_vs_3candle_rule": self._score_time_vs_3candle(candles_elapsed, max_candles),
        }

        total = sum(sub_scores[k] * WEIGHTS[k] for k in WEIGHTS)
        total = float(np.clip(total, 0.0, 100.0))
        color = self._color_for(total)

        return {
            "score": round(total, 2),
            "color": color,
            "sub_scores": {k: round(v, 2) for k, v in sub_scores.items()},
            "recommendation": RECOMMENDATIONS[color],
            "sprint_mode": self.sprint_mode,
        }


def _demo_candles(bullish: bool = True, vol_boost: float = 1.0) -> pd.DataFrame:
    """Build a small synthetic OHLCV DataFrame for self-tests."""
    base = 100.0
    rows = []
    for i in range(25):
        o = base + i * (0.3 if bullish else -0.3)
        c = o + (0.8 if bullish else -0.8)
        h = max(o, c) + 0.2
        l = min(o, c) - 0.1
        vol = 1000 + (500 if i == 24 else 0) * vol_boost
        rows.append({"open": o, "high": h, "low": l, "close": c, "volume": vol})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    logger.info("=== PositionHealthManager self-test ===")
    mgr = PositionHealthManager(sprint_mode=False)

    # Healthy long: price moved 70% toward TP, bullish candle, volume up, AI-ST agrees, fresh.
    healthy_long = {
        "bias": "LONG", "entry": 100.0, "sl": 95.0, "tp": 110.0, "current_price": 107.0,
    }
    df_bull = _demo_candles(bullish=True, vol_boost=1.0)
    ai_agree = {"direction": "LONG", "signal_strength": 0.85}
    result = mgr.score_position(healthy_long, df_bull, ai_agree, candles_elapsed=1)
    print("Healthy LONG:", result)
    assert result["color"] in ("GREEN", "YELLOW")

    # Sick long: price near SL, bearish candle, AI-ST flipped, near time limit.
    sick_long = {
        "bias": "LONG", "entry": 100.0, "sl": 95.0, "tp": 110.0, "current_price": 95.5,
    }
    df_bear = _demo_candles(bullish=False, vol_boost=0.3)
    ai_disagree = {"direction": "SHORT", "signal_strength": 0.9}
    result2 = mgr.score_position(sick_long, df_bear, ai_disagree, candles_elapsed=3)
    print("Sick LONG:", result2)
    assert result2["color"] in ("RED", "BLACK")

    # Sprint mode: same sick position should still be BLACK/RED but thresholds shifted.
    sprint_mgr = PositionHealthManager(sprint_mode=True)
    result3 = sprint_mgr.score_position(sick_long, df_bear, ai_disagree, candles_elapsed=2, max_candles=3)
    print("Sprint sick LONG:", result3)

    # Edge case: no candle data / no ai_st.
    result4 = mgr.score_position(healthy_long, None, None, candles_elapsed=0)
    print("No candle/AI-ST data (neutral fallback):", result4)
    assert 0.0 <= result4["score"] <= 100.0

    print("All self-tests passed.")
