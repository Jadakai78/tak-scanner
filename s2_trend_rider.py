"""s2_trend_rider.py — S2 Trend Rider (AI SuperTrend).

Stacked-EMA trend + AI-SuperTrend alignment + pullback-to-ST entry. Uses the
Phase-1 AISupertrend for both direction confirmation and the stop reference.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

try:
    from ._common import build_signal, ema, rsi, volume_ratio
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import build_signal, ema, rsi, volume_ratio  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("strategies.s2")

PULLBACK_TOLERANCE = 0.01  # price within 1% of the AI ST line
VOLUME_MIN = 1.2           # entry candle volume >= 1.2x average


class S2TrendRider:
    """S2 — Trend Rider using AI SuperTrend for direction + stop."""

    ENGINE = "S2"
    REQUIRED_REGIMES = ["TREND_UP", "TREND_DOWN"]

    def generate(
        self,
        pair: str,
        ohlc_df: pd.DataFrame,
        regime: str,
        fg_score: int,
        ai_st: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Generate an S2 signal on an EMA-stacked pullback to the AI ST line.

        Args:
            pair: Pair symbol.
            ohlc_df: 4H OHLC DataFrame.
            regime: Classified regime.
            fg_score: Fear & Greed score.
            ai_st: AI-SuperTrend result dict (required — direction/bands).

        Returns:
            Partial signal dict or ``None``.
        """
        if regime not in self.REQUIRED_REGIMES or ai_st is None:
            return None
        df = ohlc_df.reset_index(drop=True)
        if len(df) < 200:
            return None

        try:
            close = df["close"]
            e20 = ema(close, 20).iloc[-1]
            e50 = ema(close, 50).iloc[-1]
            e200 = ema(close, 200).iloc[-1]
            last = float(close.iloc[-1])

            if e20 > e50 > e200:
                bias = "LONG"
            elif e20 < e50 < e200:
                bias = "SHORT"
            else:
                return None

            st_dir = ai_st.get("direction")
            if (bias == "LONG" and st_dir != "UP") or (bias == "SHORT" and st_dir != "DOWN"):
                return None

            # Pullback: price within 1% of the relevant AI ST band.
            st_line = ai_st.get("lower") if bias == "LONG" else ai_st.get("upper")
            if not st_line:
                return None
            if abs(last - st_line) / last > PULLBACK_TOLERANCE:
                return None

            vr = volume_ratio(df)
            if vr < VOLUME_MIN:
                return None

            entry = last
            if bias == "LONG":
                sl = float(ai_st.get("lower")) * 0.999
                tp = entry + (entry - sl) * 2.0
            else:
                sl = float(ai_st.get("upper")) * 1.001
                tp = entry - (sl - entry) * 2.0

            return build_signal(
                pair=pair, bias=bias, engine=self.ENGINE, regime=regime,
                entry=entry, sl=sl, tp=tp,
                structure_quality=float(ai_st.get("signal_strength", 0.0)),
                rsi_val=float(rsi(close).iloc[-1]),
                vol_ratio=vr, fg_score=fg_score,
                kill_condition="AI SuperTrend flips direction before fill",
                extra={"ai_st_multiplier": ai_st.get("multiplier")},
            )
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            logger.warning("S2 %s error: %s", pair, exc)
            return None


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pair_universe import PairUniverse  # type: ignore
    from ai_supertrend import AISupertrend  # type: ignore

    logger.info("=== S2TrendRider demo ===")
    pu, ast = PairUniverse(), AISupertrend()
    eng = S2TrendRider()
    for sym, key, reg in [("BTC", "XXBTZUSD", "TREND_UP"),
                          ("SOL", "SOLUSD", "TREND_UP"),
                          ("XRP", "XRPUSD", "TREND_UP")]:
        df = pu.fetch_ohlc(key, interval=240)
        if df is None:
            print(f"{sym}: fetch failed"); continue
        st = ast.compute(sym, df)
        sig = eng.generate(sym, df, reg, fg_score=30, ai_st=st)
        if sig:
            print(f"{sym}: {sig['bias']} entry={sig['entry']} sl={sig['sl']} "
                  f"tp={sig['tp']} rr={sig['rr']} struct={sig['structure_quality']}")
        else:
            print(f"{sym}: no S2 setup (ST={st['direction']})")
