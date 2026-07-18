"""s9_capitulation.py â€” S9 Capitulation Fear Specialist.

F&G < 25 hard gate + long lower wick + volume spike + support level + recovery
close. LONG only â€” fear capitulation is a long-only mean-snap. Skips entirely
when F&G >= 25.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

try:
    from ._common import build_signal, candle_anatomy, rsi, swing_lows, volume_ratio
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import build_signal, candle_anatomy, rsi, swing_lows, volume_ratio  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("strategies.s9")

FG_HARD_GATE = 30          # widen to 30 — fear at 26-29 is still a cap environment
WICK_MIN = 0.45            # lower wick > 45% of range (loosened from 55%)
VOLUME_SPIKE = 1.8         # >1.8x avg(20) — real caps happen 1.8-2.5x, not just 2.5+
SUPPORT_TOLERANCE = 0.02   # wick low within 2% of a support level
RECOVERY_UPPER = 0.60      # close in upper 40% => close >= low + 0.6*range


class S9Capitulation:
    """S9 â€” Capitulation Fear Specialist (LONG only)."""

    ENGINE = "S9"
    REQUIRED_REGIMES = ["FEAR", "TREND_DOWN"]

    def generate(
        self,
        pair: str,
        ohlc_df: pd.DataFrame,
        regime: str,
        fg_score: int,
        ai_st: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Detect a capitulation-wick reversal during extreme fear.

        Args:
            pair: Pair symbol.
            ohlc_df: 4H OHLC DataFrame.
            regime: Must be FEAR.
            fg_score: Fear & Greed score â€” HARD GATE at < 25.
            ai_st: Unused.

        Returns:
            Partial LONG signal dict or ``None``.
        """
        if regime not in self.REQUIRED_REGIMES:
            return None
        if fg_score >= FG_HARD_GATE:
            logger.debug("S9 %s skipped: F&G %d >= %d", pair, fg_score, FG_HARD_GATE)
            return None
        df = ohlc_df.reset_index(drop=True)
        if len(df) < 100:
            return None

        try:
            row = df.iloc[-1]
            anat = candle_anatomy(row)
            if anat["range"] <= 0:
                return None

            low = float(row["low"])
            close = float(row["close"])
            rng = anat["range"]

            wick_ok = anat["lower_wick_ratio"] > WICK_MIN
            recovery_ok = close >= low + RECOVERY_UPPER * rng

            avg_vol = float(df["volume"].tail(20).mean())
            vr = float(row["volume"]) / avg_vol if avg_vol > 0 else 0.0
            vol_ok = vr > VOLUME_SPIKE

            window = df.tail(100)
            supports = [float(window["low"].iloc[i]) for i in swing_lows(window)]
            support_ok = any(
                abs(low - lv) / low <= SUPPORT_TOLERANCE for lv in supports
            ) if low > 0 else False

            if not (wick_ok and recovery_ok and vol_ok and support_ok):
                return None

            entry = close
            sl = low * 0.995
            tp = entry + (entry - sl) * 2.0
            vol_norm = min(vr / 5.0, 1.0)
            struct = anat["lower_wick_ratio"] * vol_norm

            return build_signal(
                pair=pair, bias="LONG", engine=self.ENGINE, regime=regime,
                entry=entry, sl=sl, tp=tp, structure_quality=struct,
                rsi_val=float(rsi(df["close"]).iloc[-1]),
                vol_ratio=volume_ratio(df), fg_score=fg_score,
                kill_condition="close in lower 50% of range (no recovery)",
                extra={"lower_wick_ratio": round(anat["lower_wick_ratio"], 3),
                       "vol_spike": round(vr, 2)},
            )
        except (KeyError, IndexError, ValueError, ZeroDivisionError) as exc:
            logger.warning("S9 %s error: %s", pair, exc)
            return None


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pair_universe import PairUniverse  # type: ignore

    logger.info("=== S9Capitulation demo ===")
    pu = PairUniverse()
    eng = S9Capitulation()
    for sym, key in [("BTC", "XXBTZUSD"), ("SOL", "SOLUSD"), ("XRP", "XRPUSD")]:
        df = pu.fetch_ohlc(key, interval=240)
        if df is None:
            print(f"{sym}: fetch failed"); continue
        # Demo with F&G=12 (extreme fear) so the hard gate is open.
        sig = eng.generate(sym, df, "FEAR", fg_score=12)
        print(f"{sym}: {sig['bias']+' rr='+str(sig['rr']) if sig else 'no S9 setup'}")

