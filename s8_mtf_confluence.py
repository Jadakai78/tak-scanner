"""s8_mtf_confluence.py — S8 Multi-Timeframe Confluence (overlay).

Not a standalone signal generator. Scores Daily / 4H / 1H agreement for a
proposed bias and returns a verdict that the scanner feeds into the
ConvictionScorer as ``mtf_alignment`` (FULL / PARTIAL / CONFLICT).

Weighting: Daily 0.45 + 4H 0.35 + 1H 0.20.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pandas as pd

try:
    from ._common import ema, swing_highs, swing_lows
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import ema, swing_highs, swing_lows  # type: ignore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("strategies.s8")

FULL_THRESHOLD = 0.75
PARTIAL_THRESHOLD = 0.40
TF_WEIGHTS = {"daily": 0.45, "h4": 0.35, "h1": 0.20}


class S8MTFConfluence:
    """S8 — Multi-Timeframe Confluence overlay scorer.

    Attributes:
        fetch_ohlc: Callable ``(pair_key, interval) -> DataFrame|None``.
        ai_supertrend: Optional AISupertrend instance for per-TF ST direction.
    """

    ENGINE = "S8"
    REQUIRED_REGIMES = ["TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "FEAR", "DEAD"]

    def __init__(
        self,
        fetch_ohlc: Optional[Callable[[str, int], Optional[pd.DataFrame]]] = None,
        ai_supertrend: Optional[Any] = None,
    ) -> None:
        """Initialize the overlay.

        Args:
            fetch_ohlc: OHLC fetcher; defaults to ``PairUniverse().fetch_ohlc``.
            ai_supertrend: Optional AISupertrend; lazily created if omitted.
        """
        if fetch_ohlc is None:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from pair_universe import PairUniverse  # type: ignore
            fetch_ohlc = PairUniverse().fetch_ohlc
        self.fetch_ohlc = fetch_ohlc
        self._ast = ai_supertrend

    def _ai_st(self):
        """Lazily instantiate the AISupertrend helper."""
        if self._ast is None:
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
            from ai_supertrend import AISupertrend  # type: ignore
            self._ast = AISupertrend()
        return self._ast

    # ------------------------------------------------------------------
    def _tf_score(
        self, pair: str, bias: str, df: Optional[pd.DataFrame]
    ) -> float:
        """Score one timeframe's agreement with the bias (0-1).

        Components (equal thirds): EMA alignment, AI-ST direction match, key
        level proximity.

        Args:
            pair: Pair symbol (for ST history keying).
            bias: 'LONG' or 'SHORT'.
            df: OHLC for the timeframe, or ``None`` (returns neutral 0.5).

        Returns:
            Weighted-average sub-score in [0, 1].
        """
        if df is None or len(df) < 60:
            return 0.5  # neutral when data is unavailable

        close = df["close"]
        last = float(close.iloc[-1])

        # EMA alignment.
        e20 = float(ema(close, 20).iloc[-1])
        e50 = float(ema(close, 50).iloc[-1])
        e200 = float(ema(close, 200).iloc[-1]) if len(df) >= 200 else e50
        if bias == "LONG":
            ema_score = 1.0 if e20 > e50 > e200 else (0.5 if e20 > e50 else 0.0)
        else:
            ema_score = 1.0 if e20 < e50 < e200 else (0.5 if e20 < e50 else 0.0)

        # AI-ST direction match.
        try:
            st = self._ai_st().compute(f"{pair}_mtf", df, update=True)
            st_match = 1.0 if (
                (bias == "LONG" and st["direction"] == "UP")
                or (bias == "SHORT" and st["direction"] == "DOWN")
            ) else 0.0
        except (KeyError, ValueError, IndexError) as exc:
            logger.debug("S8 %s ST failed: %s", pair, exc)
            st_match = 0.5

        # Key level proximity (trade toward the nearer opposing level).
        level_score = self._level_score(df, last, bias)

        return (ema_score + st_match + level_score) / 3.0

    @staticmethod
    def _level_score(df: pd.DataFrame, last: float, bias: str) -> float:
        """Proximity of price to a relevant swing level (0 / 0.5 / 1.0)."""
        if bias == "LONG":
            levels = [float(df["low"].iloc[i]) for i in swing_lows(df)]
        else:
            levels = [float(df["high"].iloc[i]) for i in swing_highs(df)]
        if not levels:
            return 0.5
        nearest = min(abs(last - lv) / last for lv in levels)
        if nearest <= 0.01:
            return 1.0
        if nearest <= 0.03:
            return 0.5
        return 0.0

        mtf = self.s8.score_mtf(
            pair=pair,
            bias=rawbias,
            ohlc_4h=df,
            pair_key=item.getpairkey,
    )
        """Score multi-timeframe confluence for a proposed signal.

        Args:
            pair: Pair symbol (e.g. 'BTC').
            bias: 'LONG' or 'SHORT'.
            ohlc_4h: The 4H OHLC already computed by the scanner.
            pair_key: Kraken pair key for fetching Daily/1H; defaults to
                ``f"{pair}USD"`` if not provided.

        Returns:
            ``{mtf_verdict, mtf_score, daily_score, h4_score, h1_score}``.
        """
        bias = bias.upper()
        key = pair_key or f"{pair}USD"
        try:
            daily = self.fetch_ohlc(key, 1440)
        except Exception as exc:  # noqa: BLE001 - never let a fetch crash scoring
            logger.warning("S8 daily fetch failed for %s: %s", pair, exc)
            daily = None
        try:
            h1 = self.fetch_ohlc(key, 60)
        except Exception as exc:  # noqa: BLE001
            logger.warning("S8 1H fetch failed for %s: %s", pair, exc)
            h1 = None

        daily_score = self._tf_score(pair, bias, daily)
        h4_score = self._tf_score(pair, bias, ohlc_4h)
        h1_score = self._tf_score(pair, bias, h1)

        mtf_score = (
            daily_score * TF_WEIGHTS["daily"]
            + h4_score * TF_WEIGHTS["h4"]
            + h1_score * TF_WEIGHTS["h1"]
        )
        if mtf_score >= FULL_THRESHOLD:
            verdict = "FULL"
        elif mtf_score >= PARTIAL_THRESHOLD:
            verdict = "PARTIAL"
        else:
            verdict = "CONFLICT"

        logger.info("S8 %s %s -> %s (%.2f) [D=%.2f 4H=%.2f 1H=%.2f]",
                    pair, bias, verdict, mtf_score, daily_score, h4_score, h1_score)
        return {
            "mtf_verdict": verdict,
            "mtf_score": round(mtf_score, 4),
            "daily_score": round(daily_score, 4),
            "h4_score": round(h4_score, 4),
            "h1_score": round(h1_score, 4),
        }


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pair_universe import PairUniverse  # type: ignore

    logger.info("=== S8MTFConfluence demo ===")
    pu = PairUniverse()
    s8 = S8MTFConfluence(fetch_ohlc=pu.fetch_ohlc)
    for sym, key, bias in [("BTC", "XXBTZUSD", "LONG"),
                           ("SOL", "SOLUSD", "SHORT"),
                           ("XRP", "XRPUSD", "LONG")]:
        df = pu.fetch_ohlc(key, interval=240)
        if df is None:
            print(f"{sym}: fetch failed"); continue
        res = s8.score_mtf(sym, bias, df, pair_key=key)
        print(f"{sym} {bias}: {res['mtf_verdict']} score={res['mtf_score']} "
              f"(D={res['daily_score']} 4H={res['h4_score']} 1H={res['h1_score']})")
