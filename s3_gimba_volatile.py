"""s3_gimba_volatile.py â€” S3 Gimba Volatile.

3-candle momentum push + ATR expansion + chop rejection + volume spike. Only
fires in the VOLATILE regime. Rides fresh expansion, not chop.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

try:
    from ._common import atr_series, build_signal, rsi, volume_ratio
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import atr_series, build_signal, rsi, volume_ratio  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("strategies.s3")

ATR_EXPANSION_MIN = 1.5   # current ATR > 1.5x avg ATR(20)
OVERLAP_CHOP_MAX = 0.5    # per-candle overlap ratio must stay below this
VOLUME_SPIKE = 2.0        # >2x avg on at least one momentum candle


class S3GimbaVolatile:
    """S3 â€” Gimba Volatile momentum engine."""

    ENGINE = "S3"
    REQUIRED_REGIMES = ["VOLATILE", "TREND_DOWN", "TREND_UP"]

    def generate(
        self,
        pair: str,
        ohlc_df: pd.DataFrame,
        regime: str,
        fg_score: int,
        ai_st: Optional[Dict[str, Any]] = None,
        context=None,
        shared_state=None,
    ) -> Optional[Dict[str, Any]]:
        """Detect a clean 3-candle momentum push during volatility expansion.

        Args:
            pair: Pair symbol.
            ohlc_df: 4H OHLC DataFrame.
            regime: Must be VOLATILE.
            fg_score: Fear & Greed score.
            ai_st: Unused.

        Returns:
            Partial signal dict or ``None``.
        """
        # ── Unpack PairContext when called by orchestrator ─────────────────────
        if context is not None:
            pair     = getattr(context, "pair", pair)
            ohlc_df  = getattr(context, "ohlc_df", ohlc_df)
            regime   = getattr(context, "market_regime", regime) or regime
            fg_score = getattr(context, "fg_score", fg_score) or fg_score
            ai_st    = getattr(context, "ai_state", ai_st)
                if regime not in self.REQUIRED_REGIMES:
            return None
        df = ohlc_df.reset_index(drop=True)
        if len(df) < 30:
            return None

        try:
            closes = df["close"].to_numpy(dtype=float)
            last3 = closes[-3:]
            up = last3[0] < last3[1] < last3[2]
            down = last3[0] > last3[1] > last3[2]
            if not (up or down):
                return None
            bias = "LONG" if up else "SHORT"

            atr_s = atr_series(df, 14)
            cur_atr = float(atr_s.iloc[-1])
            avg_atr = float(atr_s.tail(20).mean())
            expansion = cur_atr / avg_atr if avg_atr > 0 else 0.0
            if expansion < ATR_EXPANSION_MIN:
                return None

            # Chop rejection: overlap ratio must stay low on all 3 candles.
            if cur_atr <= 0:
                return None
            for i in range(len(df) - 3, len(df)):
                hi = min(df["high"].iloc[i], df["high"].iloc[i - 1])
                lo = max(df["low"].iloc[i], df["low"].iloc[i - 1])
                overlap = max(hi - lo, 0.0) / cur_atr
                if overlap > OVERLAP_CHOP_MAX:
                    logger.debug("S3 %s chop overlap %.2f", pair, overlap)
                    return None

            # Volume spike on at least one of the 3 momentum candles.
            avg_vol = float(df["volume"].tail(20).mean())
            spike = any(
                float(df["volume"].iloc[i]) > VOLUME_SPIKE * avg_vol
                for i in range(len(df) - 3, len(df))
            ) if avg_vol > 0 else False
            if not spike:
                return None

            entry = float(closes[-1])
            if bias == "LONG":
                sl = float(df["low"].iloc[-3]) * 0.999
                tp = entry + (entry - sl) * 2.0
            else:
                sl = float(df["high"].iloc[-3]) * 1.001
                tp = entry - (sl - entry) * 2.0

            return build_signal(
                pair=pair, bias=bias, engine=self.ENGINE, regime=regime,
                entry=entry, sl=sl, tp=tp,
                structure_quality=min(expansion / 3.0, 1.0),
                rsi_val=float(rsi(df["close"]).iloc[-1]),
                vol_ratio=volume_ratio(df), fg_score=fg_score,
                kill_condition="overlap ratio > 0.5 on any momentum candle",
                extra={"atr_expansion": round(expansion, 3)},
            )
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("S3 %s error: %s", pair, exc)
            return None


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pair_universe import PairUniverse  # type: ignore

    logger.info("=== S3GimbaVolatile demo ===")
    pu = PairUniverse()
    eng = S3GimbaVolatile()
    for sym, key in [("BTC", "XXBTZUSD"), ("SOL", "SOLUSD"), ("XRP", "XRPUSD")]:
        df = pu.fetch_ohlc(key, interval=240)
        if df is None:
            print(f"{sym}: fetch failed"); continue
        sig = eng.generate(sym, df, "VOLATILE", fg_score=30)
        print(f"{sym}: {sig['bias']+' rr='+str(sig['rr']) if sig else 'no S3 setup'}")

