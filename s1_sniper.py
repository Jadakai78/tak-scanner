"""s1_sniper.py — S1 Tak/SMC Sniper engine.

BOS retest + HTF bias + Ghost-Print volume + Order Block + FVG. Trades clean
Smart-Money-Concept continuation in trending regimes only. Uses a 3/6 minimum
SMC score to emit a live signal.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

try:  # package import
    from ._common import (
        atr,
        build_signal,
        candle_anatomy,
        detect_fvg,
        ema,
        rsi,
        swing_highs,
        swing_lows,
        volume_ratio,
    )
except ImportError:  # standalone execution
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import (  # type: ignore
        atr,
        build_signal,
        candle_anatomy,
        detect_fvg,
        ema,
        rsi,
        swing_highs,
        swing_lows,
        volume_ratio,
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("strategies.s1")

RETEST_TOLERANCE = 0.005  # within 0.5% of BOS level counts as a retest
GHOST_PRINT_MAX = 0.60    # retest volume must be < 60% of BOS candle volume
MIN_SMC_SCORE = 3         # 3/6 minimum SMC criteria (dynamic scoring handles quality)


class S1Sniper:
    """S1 — Tak/SMC Sniper.

    Attributes:
        ENGINE: Engine identifier.
        REQUIRED_REGIMES: Regimes in which this engine is eligible.
    """

    ENGINE = "S1"
    REQUIRED_REGIMES = ["TREND_UP", "TREND_DOWN"]

    def generate(
        self,
        pair: str = None,
        ohlc_df: pd.DataFrame = None,
        regime: str = "RANGE",
        fg_score: int = 50,
        aist: Optional[Dict[str, Any]] = None,
        context=None,
        shared_state=None,
    ) -> Optional[Dict[str, Any]]:
        """Generate an S1 signal if a valid BOS-retest setup exists.

        Args:
            pair: Pair symbol (e.g. 'BTC').
            ohlc_df: 4H OHLC DataFrame.
            regime: Classified regime (must be TREND_UP/TREND_DOWN).
            fg_score: Fear & Greed score.
            aist: Optional AI-SuperTrend result (unused for detection here).
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
            aist = getattr(context, "ai_state", aist)

        if regime not in self.REQUIRED_REGIMES:
            return None
        if ohlc_df is None:
            return None

        df = ohlc_df.reset_index(drop=True)
        if len(df) < 60:
            return None

        bias = "LONG" if regime == "TREND_UP" else "SHORT"
        try:
            return self._detect(pair, df, bias, regime, fg_score)
        except (KeyError, IndexError, ValueError) as exc:
            logger.warning("S1 %s detection error: %s", pair, exc)
            return None

    def _detect(
        self, pair: str, df: pd.DataFrame, bias: str, regime: str, fg_score: int
    ) -> Optional[Dict[str, Any]]:
        """Core SMC detection for one bias direction."""
        close = df["close"]
        ema200 = ema(close, 200)
        ema200_aligned = (
            ema200.iloc[-1] > ema200.iloc[-6] if bias == "LONG"
            else ema200.iloc[-1] < ema200.iloc[-6]
        )
        last_close = float(close.iloc[-1])
        last_low = float(df["low"].iloc[-1])
        last_high = float(df["high"].iloc[-1])

        # --- BOS detection ---------------------------------------------------
        if bias == "LONG":
            pivots = swing_highs(df)
            if not pivots:
                return None
            bos_pivot = pivots[-1]
            bos_level = float(df["high"].iloc[bos_pivot])
            broke = any(
                float(close.iloc[j]) > bos_level
                for j in range(bos_pivot + 1, len(df))
            )
            bos_index = next(
                (
                    j
                    for j in range(bos_pivot + 1, len(df))
                    if float(close.iloc[j]) > bos_level
                ),
                None,
            )
        else:
            pivots = swing_lows(df)
            if not pivots:
                return None
            bos_pivot = pivots[-1]
            bos_level = float(df["low"].iloc[bos_pivot])
            broke = any(
                float(close.iloc[j]) < bos_level
                for j in range(bos_pivot + 1, len(df))
            )
            bos_index = next(
                (
                    j
                    for j in range(bos_pivot + 1, len(df))
                    if float(close.iloc[j]) < bos_level
                ),
                None,
            )

        if not broke or bos_index is None:
            return None

        # --- Retest ----------------------------------------------------------
        dist = abs(last_close - bos_level) / bos_level if bos_level else 1.0
        retest_ok = dist <= RETEST_TOLERANCE
        if bias == "LONG":
            retest_ok = retest_ok and last_low <= bos_level * (1 + RETEST_TOLERANCE)
        else:
            retest_ok = retest_ok and last_high >= bos_level * (1 - RETEST_TOLERANCE)

        # --- Ghost print (quiet retest) -------------------------------------
        bos_vol = float(df["volume"].iloc[bos_index])
        retest_vol = float(df["volume"].iloc[-1])
        ghost_ok = bos_vol > 0 and retest_vol < GHOST_PRINT_MAX * bos_vol

        # --- Order block -----------------------------------------------------
        ob_low, ob_high = self._order_block(df, bos_index, bias)
        ob_present = ob_low is not None

        # --- FVG -------------------------------------------------------------
        fvg = detect_fvg(df, lookback=10)
        want = "bullish" if bias == "LONG" else "bearish"
        fvg_present = fvg is not None and fvg["type"] == want

        # --- SMC scoring (6 criteria) ---------------------------------------
        criteria = {
            "bos": broke,
            "retest": retest_ok,
            "htf_bias": bool(ema200_aligned),
            "ghost_print": ghost_ok,
            "order_block": ob_present,
            "fvg": fvg_present,
        }
        smc_score = sum(1 for v in criteria.values() if v)

        # Tiered conviction bonus — more confluences = higher quality signal
        smc_bonus = {3: 0, 4: 5, 5: 12, 6: 20}.get(smc_score, 0)
        if smc_score < MIN_SMC_SCORE:
            logger.debug(
                "S1 %s SMC score %d/6 < %d — skip", pair, smc_score, MIN_SMC_SCORE
            )
            return None

        structure_quality = (
            0.3 * float(ghost_ok)
            + 0.3 * float(ob_present)
            + 0.2 * float(fvg_present)
            + 0.2 * float(bool(ema200_aligned))
        )

        # --- Entry / SL / TP -------------------------------------------------
        entry = bos_level
        if bias == "LONG":
            sl = (ob_low if ob_low is not None else last_low) * 0.999
            tp = self._next_structure(df, entry, bias)
        else:
            sl = (ob_high if ob_high is not None else last_high) * 1.001
            tp = self._next_structure(df, entry, bias)

        # Keep smc_bonus computed for future scoring integration
        _ = smc_bonus

        return build_signal(
            pair=pair,
            bias=bias,
            engine=self.ENGINE,
            regime=regime,
            entry=entry,
            sl=sl,
            tp=tp,
            structure_quality=structure_quality,
            rsi_val=float(rsi(close).iloc[-1]),
            vol_ratio=volume_ratio(df),
            fg_score=fg_score,
            kill_condition="close back through BOS level before fill",
            extra={
                "smc_score": smc_score,
                "bos_level": round(bos_level, 8),
                "smc_criteria": criteria,
            },
        )

    @staticmethod
    def _order_block(df: pd.DataFrame, bos_index: int, bias: str):
        """Find the order block preceding the BOS.

        For a bullish BOS: the last bearish candle before the break.
        For a bearish BOS: the last bullish candle before the break.

        Returns:
            ``(low, high)`` of the order block, or ``(None, None)``.
        """
        for j in range(bos_index - 1, max(bos_index - 12, -1), -1):
            o = float(df["open"].iloc[j])
            c = float(df["close"].iloc[j])
            if bias == "LONG" and c < o:  # bearish candle before bullish BOS
                return float(df["low"].iloc[j]), float(df["high"].iloc[j])
            if bias == "SHORT" and c > o:  # bullish candle before bearish BOS
                return float(df["low"].iloc[j]), float(df["high"].iloc[j])
        return None, None

    @staticmethod
    def _next_structure(df: pd.DataFrame, entry: float, bias: str) -> float:
        """Nearest opposing swing level to use as a structure-based target."""
        if bias == "LONG":
            highs = [float(df["high"].iloc[i]) for i in swing_highs(df)]
            targets = [h for h in highs if h > entry]
            return min(targets) if targets else entry * 1.03

        lows = [float(df["low"].iloc[i]) for i in swing_lows(df)]
        targets = [low for low in lows if low < entry]
        return max(targets) if targets else entry * 0.97


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from pair_universe import PairUniverse  # type: ignore

    logger.info("=== S1Sniper demo ===")
    pu = PairUniverse()
    eng = S1Sniper()
    for sym, key, reg in [
        ("BTC", "XXBTZUSD", "TREND_UP"),
        ("SOL", "SOLUSD", "TREND_DOWN"),
        ("XRP", "XRPUSD", "TREND_UP"),
    ]:
        df = pu.fetch_ohlc(key, interval=240)
        if df is None:
            print(f"{sym}: fetch failed")
            continue
        sig = eng.generate(sym, df, reg, fg_score=30)
        if sig:
            print(
                f"{sym}: {sig['bias']} entry={sig['entry']} sl={sig['sl']} "
                f"tp={sig['tp']} rr={sig['rr']} smc={sig.get('smc_score')} "
                f"struct={sig['structure_quality']}"
            )
        else:
            print(f"{sym}: no S1 setup in {reg}")
