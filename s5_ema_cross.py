"""s5_ema_cross.py — S5 EMA Cross Hybrid.

Fresh EMA50/EMA200 cross + FVG sweep + volume + Hull-MA confirmation. Trades
the early leg of a new trend, only while the cross is fresh (<=10 candles old).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

try:
    from ._common import build_signal, detect_fvg, ema, hull_ma, rsi, volume_ratio
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import build_signal, detect_fvg, ema, hull_ma, rsi, volume_ratio  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("strategies.s5")

MAX_CANDLES_SINCE_CROSS = 10
VOLUME_MIN = 1.5


class S5EMACross:
    """S5 — EMA Cross Hybrid engine."""

    ENGINE = "S5"
    REQUIRED_REGIMES = ["TREND_UP", "TREND_DOWN"]

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

RSI_OS = 30            # loosened from 35 — dynamic scorer handles quality
RSI_OB = 70            # loosened from 65
SMC_TOLERANCE = 0.01   # within 1% of a recent swing
OVEREXTENSION = 0.03   # >3% beyond EMA50 (crypto needs room)


class S4MeanReversion:
    """S4 — Mean Reversion engine."""

    ENGINE = "S4"
    REQUIRED_REGIMES = ["RANGE"]

    def generate(
        self,
        pair: str = None,
        ohlc_df: pd.DataFrame = None,
        regime: str = "RANGE",
        fg_score: int = 50,
        ai_st: Optional[Dict[str, Any]] = None,
        context=None,
        shared_state=None,
    ) -> Optional[Dict[str, Any]]:
        """Generate an S4 signal for range mean reversion.

        Args:
            pair: Pair symbol.
            ohlc_df: 4H OHLC DataFrame.
            regime: Must be RANGE.
            fg_score: Fear & Greed score.
            ai_st: Optional AI-SuperTrend state (unused here).
            context: Optional orchestrator PairContext.
            shared_state: Optional shared orchestrator state.

        Returns:
            Partial signal dict or ``None``.
        """
        # Unpack PairContext when called by orchestrator
        if context is not None:
            pair = getattr(context, "pair", pair)
            ohlc_df = getattr(context, "ohlc_df", ohlc_df)
            regime = getattr(context, "market_regime", regime) or regime
            fg_score = getattr(context, "fg_score", fg_score) or fg_score
            ai_st = getattr(context, "ai_state", ai_st)

        if regime not in self.REQUIRED_REGIMES:
            return None
        if ohlc_df is None:
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

            long_ok = (
                last <= lb
                and rsi_val < RSI_OS
                and last < e50 * (1 - OVEREXTENSION)
            )
            short_ok = (
                last >= ub
                and rsi_val > RSI_OB
                and last > e50 * (1 + OVEREXTENSION)
            )
            if not (long_ok or short_ok):
                return None

            bias = "LONG" if long_ok else "SHORT"

            # SMC level proximity
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

            tp = e50  # revert to mean
            struct = ((100 - rsi_val) / 100) if bias == "LONG" else (rsi_val / 100)

            return build_signal(
                pair=pair,
                bias=bias,
                engine=self.ENGINE,
                regime=regime,
                entry=last,
                sl=sl,
                tp=tp,
                structure_quality=struct,
                rsi_val=rsi_val,
                vol_ratio=volume_ratio(df),
                fg_score=fg_score,
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
            print(f"{sym}: fetch failed")
            continue

        sig = eng.generate(sym, df, "RANGE", fg_score=50)
        print(f"{sym}: {sig['bias'] + ' rr=' + str(sig['rr']) if sig else 'no S4 setup'}")

        Args:
            pair: Pair symbol.
            ohlc_df: 4H OHLC DataFrame.
            regime: TREND_UP / TREND_DOWN.
            fg_score: Fear & Greed score.
            ai_st: Unused.

        Returns:
            Partial signal dict or ``None``.
        """
        if regime not in self.REQUIRED_REGIMES:
            return None
        df = ohlc_df.reset_index(drop=True)
        if len(df) < 210:
            return None

        try:
            close = df["close"]
            e50 = ema(close, 50)
            e200 = ema(close, 200)
            diff = (e50 - e200).to_numpy()
            sign = np.sign(diff)

            # Locate the most recent sign change (the cross).
            cross_idx = None
            for i in range(len(sign) - 1, 0, -1):
                if sign[i] != sign[i - 1] and sign[i] != 0:
                    cross_idx = i
                    break
            if cross_idx is None:
                return None
            candles_since = len(df) - 1 - cross_idx
            if candles_since > MAX_CANDLES_SINCE_CROSS:
                return None

            bias = "LONG" if diff[-1] > 0 else "SHORT"
            if (bias == "LONG" and regime != "TREND_UP") or \
               (bias == "SHORT" and regime != "TREND_DOWN"):
                return None

            # Clean cross: separation widening since the cross.
            if abs(diff[-1]) <= abs(diff[cross_idx]):
                return None

            # FVG sweep in the right direction.
            fvg = detect_fvg(df, lookback=candles_since + 3)
            want = "bullish" if bias == "LONG" else "bearish"
            if fvg is None or fvg["type"] != want:
                return None

            # Volume on the cross candle.
            avg_vol = float(df["volume"].tail(20).mean())
            if avg_vol <= 0 or float(df["volume"].iloc[cross_idx]) < VOLUME_MIN * avg_vol:
                return None

            # Hull MA direction agrees.
            hma = hull_ma(close, 20)
            hma_up = float(hma.iloc[-1]) > float(hma.iloc[-2])
            if (bias == "LONG") != hma_up:
                return None

            last = float(close.iloc[-1])
            e200_last = float(e200.iloc[-1])
            if bias == "LONG":
                sl = min(e200_last, last) * 0.998
                tp = last + (last - sl) * 2.0
            else:
                sl = max(e200_last, last) * 1.002
                tp = last - (sl - last) * 2.0

            struct = 1.0 - (candles_since / MAX_CANDLES_SINCE_CROSS)
            return build_signal(
                pair=pair, bias=bias, engine=self.ENGINE, regime=regime,
                entry=last, sl=sl, tp=tp, structure_quality=struct,
                rsi_val=float(rsi(close).iloc[-1]),
                vol_ratio=volume_ratio(df), fg_score=fg_score,
                kill_condition="EMA50 crosses back through EMA200 before fill",
                extra={"candles_since_cross": int(candles_since)},
            )
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("S5 %s error: %s", pair, exc)
            return None


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pair_universe import PairUniverse  # type: ignore

    logger.info("=== S5EMACross demo ===")
    pu = PairUniverse()
    eng = S5EMACross()
    for sym, key, reg in [("BTC", "XXBTZUSD", "TREND_UP"),
                          ("SOL", "SOLUSD", "TREND_DOWN"),
                          ("XRP", "XRPUSD", "TREND_UP")]:
        df = pu.fetch_ohlc(key, interval=240)
        if df is None:
            print(f"{sym}: fetch failed"); continue
        sig = eng.generate(sym, df, reg, fg_score=40)
        print(f"{sym}: {sig['bias']+' rr='+str(sig['rr']) if sig else 'no S5 setup'}")
