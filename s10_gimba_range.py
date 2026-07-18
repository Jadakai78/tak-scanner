"""s10_gimba_range.py — S10 Gimba Range.

BB + RSI mean-reversion on 1H timeframe. Counter-trend engine.
Fires in RANGE, FEAR, VOLATILE, TREND_DOWN regimes.
Entry/SL/TP wired through build_signal — output lands in live feed.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

try:
    from ._common import build_signal, atr_series, volume_ratio
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _common import build_signal, atr_series, volume_ratio  # type: ignore

logger = logging.getLogger("strategies.s10")

# ── Config ───────────────────────────────────────────────────────────────────
BB_PERIOD       = 20
BB_STD          = 2.0
RSI_PERIOD      = 14
RSI_OB          = 65
RSI_OS          = 35
MIN_BARS        = 50
KRAKEN_INTERVAL = 60   # 1H
MIN_RR          = 2.0


# ── Indicators ───────────────────────────────────────────────────────────────
def _add_bb(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["bb_mid"]   = df["close"].rolling(BB_PERIOD).mean()
    df["bb_std"]   = df["close"].rolling(BB_PERIOD).std()
    df["bb_upper"] = df["bb_mid"] + BB_STD * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - BB_STD * df["bb_std"]
    rng = (df["bb_upper"] - df["bb_lower"]).replace(0, np.nan)
    df["bb_pct"]   = (df["close"] - df["bb_lower"]) / rng
    return df


def _add_rsi(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    delta = df["close"].diff()
    gain  = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    loss  = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    rs    = gain / loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


# ── Engine ───────────────────────────────────────────────────────────────────
class S10GimbaRange:
    """S10 — Gimba Range mean-reversion engine."""

    ENGINE           = "S10"
    REQUIRED_REGIMES = ["RANGE", "VOLATILE", "FEAR", "TREND_DOWN"]
    ENGINE_TYPE      = "COUNTER_TREND"

    def generate(
        self,
        pair: str,
        ohlc_df: Optional[pd.DataFrame] = None,
        regime: str = "RANGE",
        fg_score: float = 50.0,
        ai_st: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> Optional[Dict[str, Any]]:
        """Detect BB lower/upper band touch with RSI confirmation.

        Args:
            pair:     Pair symbol (e.g. 'BTC', 'SOL').
            ohlc_df:  1H OHLC DataFrame (passed by scanner orchestrator).
            regime:   Market regime string.
            fg_score: Fear & Greed index.
            ai_st:    Unused (API compat).

        Returns:
            Validated signal dict from build_signal, or None.
        """
        if regime not in self.REQUIRED_REGIMES:
            return None

        df = ohlc_df
        if df is None or len(df) < MIN_BARS:
            logger.debug("S10 %s — insufficient bars", pair)
            return None

        try:
            df = _add_bb(df)
            df = _add_rsi(df)

            last   = df.iloc[-1]
            rsi_v  = float(last["rsi"])
            bb_pct = float(last["bb_pct"])
            close  = float(last["close"])

            if pd.isna(rsi_v) or pd.isna(bb_pct):
                return None

            bias   = None
            reason = None
            confidence = 0.0

            if bb_pct <= 0.15 and rsi_v <= RSI_OS:
                bias       = "LONG"
                rsi_str    = max(0.0, (RSI_OS - rsi_v) / RSI_OS)
                bb_str     = max(0.0, (0.15 - bb_pct) / 0.15)
                confidence = round(min(0.90, 0.50 + rsi_str * 0.25 + bb_str * 0.25), 3)
                reason     = f"BB%={bb_pct:.2f} RSI={rsi_v:.1f} lower-band bounce LONG"

            elif bb_pct >= 0.85 and rsi_v >= RSI_OB:
                bias       = "SHORT"
                rsi_str    = max(0.0, (rsi_v - RSI_OB) / (100.0 - RSI_OB))
                bb_str     = max(0.0, (bb_pct - 0.85) / 0.15)
                confidence = round(min(0.90, 0.50 + rsi_str * 0.25 + bb_str * 0.25), 3)
                reason     = f"BB%={bb_pct:.2f} RSI={rsi_v:.1f} upper-band fade SHORT"

            if bias is None:
                return None

            # ── Entry / SL / TP ──────────────────────────────────────────────
            atr_s   = atr_series(df, 14)
            cur_atr = float(atr_s.iloc[-1]) if not atr_s.empty else 0.0

            entry = close
            if bias == "LONG":
                # SL below BB lower band by 0.5 ATR
                sl = float(last["bb_lower"]) - 0.5 * cur_atr
                tp = entry + (entry - sl) * MIN_RR
            else:
                # SL above BB upper band by 0.5 ATR
                sl = float(last["bb_upper"]) + 0.5 * cur_atr
                tp = entry - (sl - entry) * MIN_RR

            kill_condition = (
                f"S10 kill: price closes beyond BB {'lower' if bias == 'LONG' else 'upper'} "
                f"band against thesis"
            )

            raw = build_signal(
                pair=pair,
                bias=bias,
                engine=self.ENGINE,
                regime=regime,
                entry=entry,
                sl=sl,
                tp=tp,
                structure_quality=confidence,
                rsi_val=rsi_v,
                vol_ratio=volume_ratio(df),
                fg_score=int(fg_score),
                kill_condition=kill_condition,
                extra={
                    "bb_pct":   round(bb_pct, 3),
                    "bb_upper": round(float(last["bb_upper"]), 6),
                    "bb_lower": round(float(last["bb_lower"]), 6),
                    "bb_mid":   round(float(last["bb_mid"]), 6),
                    "reason":   reason,
                    "engine_type": self.ENGINE_TYPE,
                },
            )
            return raw

        except (KeyError, IndexError, ValueError, TypeError) as exc:
            logger.warning("S10 %s error: %s", pair, exc)
            return None


def run(pair: str, **kwargs):
    """Module-level shim for direct diagnostic calls."""
    return S10GimbaRange().generate(pair, **kwargs)
