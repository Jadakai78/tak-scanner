"""s4_mean_reversion.py — S4 Mean Reversion (BB / RSI).

Bollinger-band extreme + RSI OS/OB + SMC swing level + EMA overextension.
Fires only in RANGE regime; fades stretched moves back toward the EMA50 mean.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

try:
    from ._common import bollinger, build_signal, ema, rsi, swing_highs, swing_lows, volume_ratio
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import bollinger, build_signal, ema, rsi, swing_highs, swing_lows, volume_ratio  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("strategies.s4")

RSI_OS = 35
RSI_OB = 65
SMC_TOLERANCE = 0.01       # within 1% of a recent swing
OVEREXTENSION = 0.02       # >2% beyond EMA50


class S4MeanReversion:
    """S4 — Mean Reversion engine."""

    ENGINE = "S4"
    REQUIRED_REGIMES = ["RANGE"]

    def generate(
        self,
        pair: str,
        ohlc_df: pd.DataFrame,
        regime: str,
        fg_score: int,
        ai_st: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Detect a mean-reversion fade at a band extreme.

        Args:
            pair: Pair symbol.
            ohlc_df: 4H OHLC DataFrame.
            regime: Must be RANGE.
            fg_score: Fear & Greed score.
            ai_st: Unused.

        Returns:
            Partial signal dict or ``None``.
        """
        if regime not in self.REQUIRED_REGIMES:
            return None
        df = ohlc_df.reset_index(drop=True)
        if len(df) < 55:
            return None

        try:
            close = df["close"]
            last = float(close.iloc[-1])
            upper, mid, lower = bollinger(close, 20, 2.0)
            ub, lb = float(upper.iloc[-1]), float(lower.iloc[-1])
            rsi_val = float(rsi(close).iloc[-1])
            e50 = float(ema(close, 50).iloc[-1])

            long_ok = (last <= lb and rsi_val < RSI_OS
                       and last < e50 * (1 - OVEREXTENSION))
            short_ok = (last >= ub and rsi_val > RSI_OB
                        and last > e50 * (1 + OVEREXTENSION))
            if not (long_ok or short_ok):
                return None
            bias = "LONG" if long_ok else "SHORT"

            # SMC level proximity.
            if bias == "LONG":
                lows = [float(df["low"].iloc[i]) for i in swing_lows(df)]
                near = any(abs(last - lv) / last <= SMC_TOLERANCE for lv in lows)
                swing = min(lows) if lows else last * 0.99
                sl = swing * 0.997
            else:
                highs = [float(df["high"].iloc[i]) for i in swing_highs(df)]
                near = any(abs(last - lv) / last <= SMC_TOLERANCE for lv in highs)
                swing = max(highs) if highs else last * 1.01
                sl = swing * 1.003
            if not near:
                return None

            tp = e50  # revert to the mean
            struct = ((100 - rsi_val) / 100) if bias == "LONG" else (rsi_val / 100)

            return build_signal(
                pair=pair, bias=bias, engine=self.ENGINE, regime=regime,
                entry=last, sl=sl, tp=tp, structure_quality=struct,
                rsi_val=rsi_val, vol_ratio=volume_ratio(df), fg_score=fg_score,
                kill_condition="close beyond BB band (extension accelerating)",
                extra={"bb_upper": round(ub, 8), "bb_lower": round(lb, 8)},
            )
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("S4 %s error: %s", pair, exc)
            return None


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pair_universe import PairUniverse  # type: ignore

    logger.info("=== S4MeanReversion demo ===")
    pu = PairUniverse()
    eng = S4MeanReversion()
    for sym, key in [("BTC", "XXBTZUSD"), ("SOL", "SOLUSD"), ("XRP", "XRPUSD")]:
        df = pu.fetch_ohlc(key, interval=240)
        if df is None:
            print(f"{sym}: fetch failed"); continue
        sig = eng.generate(sym, df, "RANGE", fg_score=50)
        print(f"{sym}: {sig['bias']+' rr='+str(sig['rr']) if sig else 'no S4 setup'}")
