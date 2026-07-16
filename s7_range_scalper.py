"""s7_range_scalper.py — S7 Range Scalper.

Flat-EMA range detection + boundary entry + RSI + BB touch. Buys the range low
and sells the range high while the EMA50 is confirmed flat.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

try:
    from ._common import bollinger, build_signal, ema, rsi, volume_ratio, atr
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import bollinger, build_signal, ema, rsi, volume_ratio, atr  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("strategies.s7")

FLAT_SLOPE_MAX = 0.05      # EMA50 slope < 0.05% over 10 candles
BOUNDARY_TOLERANCE = 0.01  # within 1% of the range edge
RSI_LOW = 40
RSI_HIGH = 60


class S7RangeScalper:
    """S7 — Range Scalper engine."""

    ENGINE = "S7"
    REQUIRED_REGIMES = ["RANGE"]

    def generate(
        self,
        pair: str,
        ohlc_df: pd.DataFrame,
        regime: str,
        fg_score: int,
        aist: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Scalp a range edge inside a confirmed flat-EMA range.

        Args:
            pair: Pair symbol.
            ohlc_df: 4H OHLC DataFrame.
            regime: Must be RANGE.
            fg_score: Fear & Greed score.
            aist: Unused.

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
            e50 = ema(close, 50)
            if float(e50.iloc[-11]) == 0:
                return None
            slope = abs(float(e50.iloc[-1]) - float(e50.iloc[-11])) / \
                abs(float(e50.iloc[-11])) * 100
            if slope >= FLAT_SLOPE_MAX:
                return None

            window = df.tail(20)
            range_high = float(window["high"].max())
            range_low = float(window["low"].min())
            range_size = range_high - range_low
            if range_size <= 0:
                return None

            last = float(close.iloc[-1])
            rsi_val = float(rsi(close).iloc[-1])
            upper, mid, lower = bollinger(close, 20, 2.0)
            ub, lb = float(upper.iloc[-1]), float(lower.iloc[-1])

            near_low = abs(last - range_low) / last <= BOUNDARY_TOLERANCE
            near_high = abs(last - range_high) / last <= BOUNDARY_TOLERANCE
            long_ok = near_low and rsi_val < RSI_LOW and last <= lb * 1.005
            short_ok = near_high and rsi_val > RSI_HIGH and last >= ub * 0.995
            if not (long_ok or short_ok):
                return None
            bias = "LONG" if long_ok else "SHORT"

            if bias == "LONG":
                sl = range_low * 0.997
                tp = range_high
            else:
                sl = range_high * 1.003
                tp = range_low

            atr_pct = (atr(df, 14) / last * 100) if last else 1.0
            range_pct = range_size / last * 100
            struct = min(range_pct / atr_pct, 1.0) if atr_pct > 0 else 0.5

            return build_signal(
                pair=pair, bias=bias, engine=self.ENGINE, regime=regime,
                entry=last, sl=sl, tp=tp, structure_quality=struct,
                rsi_val=rsi_val, vol_ratio=volume_ratio(df), fg_score=fg_score,
                kill_condition="ATR expands > 2x range size (range broken)",
                extra={"range_high": round(range_high, 8),
                       "range_low": round(range_low, 8)},
            )
        except (KeyError, IndexError, ValueError, ZeroDivisionError) as exc:
            logger.warning("S7 %s error: %s", pair, exc)
            return None


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pair_universe import PairUniverse  # type: ignore

    logger.info("=== S7RangeScalper demo ===")
    pu = PairUniverse()
    eng = S7RangeScalper()
    for sym, key in [("BTC", "XXBTZUSD"), ("SOL", "SOLUSD"), ("XRP", "XRPUSD")]:
        df = pu.fetch_ohlc(key, interval=240)
        if df is None:
            print(f"{sym}: fetch failed"); continue
        sig = eng.generate(sym, df, "RANGE", fg_score=50)
        print(f"{sym}: {sig['bias']+' rr='+str(sig['rr']) if sig else 'no S7 setup'}")
