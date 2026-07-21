"""s6_reversal.py — S6 Reversal at Key Level.

Key SMC level + hammer/shooting-star + SuperTrend flip + EMA overextension.
Fires in RANGE or FEAR at swing extremes.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

try:
    from ._common import (
        build_signal,
        candle_anatomy,
        ema,
        rsi,
        supertrend,
        swing_highs,
        swing_lows,
        volume_ratio,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import (  # type: ignore
        build_signal,
        candle_anatomy,
        ema,
        rsi,
        supertrend,
        swing_highs,
        swing_lows,
        volume_ratio,
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("strategies.s6")

LEVEL_TOLERANCE = 0.030  # within 3% of a swing level
OVEREXTENSION = 0.012  # >1.2% beyond EMA50
WICK_BODY_MULT = 1.5  # wick > 1.5x body
BODY_THIRD = 1.0 / 3.0


class S6Reversal:
    """S6 — Reversal engine."""

    ENGINE = "S6"
    REQUIRED_REGIMES = ["RANGE", "FEAR", "TREND_DOWN"]

    def generate(
        self,
        pair: str | None = None,
        ohlc_df: pd.DataFrame | None = None,
        regime: str = "RANGE",
        fg_score: int = 50,
        aist: Optional[Dict[str, Any]] = None,
        context=None,
        shared_state=None,
    ) -> Optional[Dict[str, Any]]:
        """Detect a reversal candle at a key level with a SuperTrend flip.

        Args:
            pair: Pair symbol.
            ohlc_df: 4H OHLC DataFrame.
            regime: Market regime.
            fg_score: Fear & Greed score.
            aist: Optional AI state.
            context: Optional orchestrator PairContext.
            shared_state: Optional shared state.

        Returns:
            Partial signal dict or ``None``.
        """
        del shared_state  # reserved for orchestrator compatibility

        if context is not None:
            pair = getattr(context, "pair", pair)
            ohlc_df = getattr(context, "ohlc_df", ohlc_df)
            regime = getattr(context, "market_regime", regime) or regime
            fg_score = getattr(context, "fg_score", fg_score) or fg_score
            aist = getattr(context, "ai_state", aist)

        if ohlc_df is None or ohlc_df.empty:
            return None

        if regime not in self.REQUIRED_REGIMES:
            return None

        df = ohlc_df.reset_index(drop=True)
        if len(df) < 60:
            return None

        try:
            close = df["close"]
            last = float(close.iloc[-1])
            row = df.iloc[-1]
            anat = candle_anatomy(row)
            if anat["range"] <= 0 or anat["body"] <= 0:
                return None

            e50 = float(ema(close, 50).iloc[-1])
            st = supertrend(df, period=10, multiplier=3.0)
            st_flip_up = st.iloc[-1] == 1 and st.iloc[-2] == -1
            st_flip_down = st.iloc[-1] == -1 and st.iloc[-2] == 1

            window = df.tail(50).reset_index(drop=True)
            highs = [float(window["high"].iloc[i]) for i in swing_highs(window)]
            lows = [float(window["low"].iloc[i]) for i in swing_lows(window)]

            hammer = (
                anat["lower_wick"] > WICK_BODY_MULT * anat["body"]
                and (min(row["open"], row["close"]) - row["low"]) >= 0
                and anat["body_ratio"] <= BODY_THIRD
            )
            star = (
                anat["upper_wick"] > WICK_BODY_MULT * anat["body"]
                and anat["body_ratio"] <= BODY_THIRD
            )

            near_low = any(abs(last - lv) / last <= LEVEL_TOLERANCE for lv in lows)
            near_high = any(abs(last - lv) / last <= LEVEL_TOLERANCE for lv in highs)

            long_ok = (
                hammer
                and near_low
                and st_flip_up
                and last < e50 * (1 - OVEREXTENSION)
            )
            short_ok = (
                star
                and near_high
                and st_flip_down
                and last > e50 * (1 + OVEREXTENSION)
            )

            if not (long_ok or short_ok):
                return None

            bias = "LONG" if long_ok else "SHORT"

            if bias == "LONG":
                sl = float(row["low"]) * 0.997
                struct = max(0.55, anat["lower_wick_ratio"])
            else:
                sl = float(row["high"]) * 1.003
                struct = max(0.55, anat["upper_wick_ratio"])

            tp = e50

            return build_signal(
                pair=pair,
                bias=bias,
                engine=self.ENGINE,
                regime=regime,
                entry=last,
                sl=sl,
                tp=tp,
                structure_quality=struct,
                rsi_val=float(rsi(close).iloc[-1]),
                vol_ratio=volume_ratio(df),
                fg_score=fg_score,
                kill_condition="no SuperTrend flip or no reversal candle",
                extra={
                    "pattern": "hammer" if bias == "LONG" else "shooting_star",
                },
            )
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("S6 %s error: %s", pair, exc)
            return None


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pair_universe import PairUniverse  # type: ignore

    logger.info("=== S6Reversal demo ===")
    pu = PairUniverse()
    eng = S6Reversal()
    for sym, key in [("BTC", "XXBTZUSD"), ("SOL", "SOLUSD"), ("XRP", "XRPUSD")]:
        df = pu.fetch_ohlc(key, interval=240)
        if df is None:
            print(f"{sym}: fetch failed")
            continue
        sig = eng.generate(sym, df, "RANGE", fg_score=50)
        print(f"{sym}: {sig['bias'] + ' rr=' + str(sig['rr']) if sig else 'no S6 setup'}")
