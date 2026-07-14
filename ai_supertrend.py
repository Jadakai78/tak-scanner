"""ai_supertrend.py — KNN dynamic-multiplier SuperTrend.

AI Component 1 of JHL Trading Architecture v2. A standard SuperTrend uses a
fixed ATR multiplier. This version uses K-Nearest-Neighbors over the pair's own
candle history to pick an *adaptive* multiplier: tighter in clean trends,
wider in chop.

Per-candle feature vector: [atr_pct, rsi, volume_ratio, body_ratio, wick_ratio].
For the current candle we find the K=5 most-similar historical candles and
average the multiplier that would have called their direction fastest, then
clamp to [1.0, 4.0]. Cold start (< 20 stored candles) uses default 2.5.

Per-pair history is persisted to ``models/ai_st_{pair}.pkl``.
"""

from __future__ import annotations

import logging
import pickle
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("ai_supertrend")

MODULE_DIR = Path(__file__).resolve().parent
MODELS_DIR = MODULE_DIR / "models"

K_NEIGHBORS = 5
MIN_HISTORY = 20          # below this -> cold-start default multiplier
DEFAULT_MULTIPLIER = 2.5
MULT_MIN, MULT_MAX = 1.0, 4.0
# Candidate multipliers evaluated when labelling a candle's "optimal" value.
MULT_GRID = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0]
LOOKAHEAD = 3             # candles used to score which multiplier was "correct"


class AISupertrend:
    """Adaptive SuperTrend whose ATR multiplier is chosen by KNN.

    Attributes:
        k: Number of nearest neighbors to consult.
        models_dir: Directory holding per-pair history pickles.
    """

    def __init__(self, k: int = K_NEIGHBORS, models_dir: Optional[Path] = None) -> None:
        """Initialize the AI SuperTrend.

        Args:
            k: Number of nearest neighbors (default 5).
            models_dir: Override directory for per-pair history pickles.
        """
        self.k = k
        self.models_dir = models_dir or MODELS_DIR
        self.models_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @staticmethod
    def _safe_pair(pair: str) -> str:
        """Sanitize a pair symbol for use in a filename."""
        return re.sub(r"[^A-Za-z0-9_]", "_", pair).upper()

    def _history_path(self, pair: str) -> Path:
        """Path to a pair's persisted feature/label history."""
        return self.models_dir / f"ai_st_{self._safe_pair(pair)}.pkl"

    def _load_history(self, pair: str) -> Dict[str, np.ndarray]:
        """Load stored history for a pair.

        Returns:
            Dict with ``features`` (N x 5) and ``multipliers`` (N,) arrays.
            Empty arrays if no history exists.
        """
        path = self._history_path(pair)
        if not path.exists():
            return {"features": np.empty((0, 5)), "multipliers": np.empty((0,))}
        try:
            with path.open("rb") as fh:
                data = pickle.load(fh)
            if "features" in data and "multipliers" in data:
                return data
        except (pickle.UnpicklingError, OSError, EOFError, KeyError) as exc:
            logger.warning("Could not load history for %s: %s", pair, exc)
        return {"features": np.empty((0, 5)), "multipliers": np.empty((0,))}

    def _save_history(self, pair: str, history: Dict[str, np.ndarray]) -> None:
        """Persist a pair's history dict."""
        try:
            with self._history_path(pair).open("wb") as fh:
                pickle.dump(history, fh)
        except OSError as exc:
            logger.error("Failed to save history for %s: %s", pair, exc)

    # ------------------------------------------------------------------
    # Indicator math
    # ------------------------------------------------------------------
    @staticmethod
    def _atr_series(df: pd.DataFrame, period: int = 14) -> np.ndarray:
        """Full ATR (Wilder) series aligned to df rows."""
        high = df["high"].to_numpy()
        low = df["low"].to_numpy()
        close = df["close"].to_numpy()
        prev_close = np.roll(close, 1)
        tr = np.maximum.reduce([
            high - low,
            np.abs(high - prev_close),
            np.abs(low - prev_close),
        ])
        tr[0] = high[0] - low[0]
        return pd.Series(tr).ewm(alpha=1 / period, adjust=False).mean().to_numpy()

    @staticmethod
    def _rsi_series(close: pd.Series, period: int = 14) -> np.ndarray:
        """Full RSI series aligned to close."""
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50.0).to_numpy()

    def _build_features(self, df: pd.DataFrame) -> np.ndarray:
        """Build the per-candle feature matrix (N x 5).

        Columns: [atr_pct, rsi, volume_ratio, body_ratio, wick_ratio].

        Args:
            df: OHLC DataFrame.

        Returns:
            N x 5 float array (rows with NaN inputs are zero-filled per element).
        """
        n = len(df)
        close = df["close"]
        atr = self._atr_series(df, 14)
        rsi = self._rsi_series(close, 14)
        vol = df["volume"].to_numpy(dtype=float)
        avg20 = pd.Series(vol).rolling(20, min_periods=1).mean().to_numpy()

        o = df["open"].to_numpy(dtype=float)
        h = df["high"].to_numpy(dtype=float)
        low = df["low"].to_numpy(dtype=float)
        c = close.to_numpy(dtype=float)
        rng = np.where((h - low) == 0, np.nan, h - low)

        atr_pct = np.where(c != 0, atr / c * 100, 0.0)
        volume_ratio = np.where(avg20 != 0, vol / avg20, 1.0)
        body_ratio = np.abs(c - o) / rng
        wick_ratio = (h - np.maximum(o, c)) / rng

        feats = np.column_stack([
            atr_pct, rsi, volume_ratio,
            np.nan_to_num(body_ratio, nan=0.0),
            np.nan_to_num(wick_ratio, nan=0.0),
        ])
        return np.nan_to_num(feats, nan=0.0, posinf=0.0, neginf=0.0)

    def _optimal_multiplier(self, df: pd.DataFrame, idx: int, atr: np.ndarray) -> float:
        """Find the multiplier that would have called this candle fastest.

        For candle ``idx`` we know the actual direction over the next
        :data:`LOOKAHEAD` candles. We pick the smallest multiplier from
        :data:`MULT_GRID` whose SuperTrend band flips to that direction; if none
        flip, we fall back to the widest (most conservative) multiplier.

        Args:
            df: OHLC DataFrame.
            idx: Index of the candle to label.
            atr: Precomputed ATR series.

        Returns:
            The optimal multiplier for this candle.
        """
        c = df["close"].to_numpy()
        h = df["high"].to_numpy()
        low = df["low"].to_numpy()
        if idx + LOOKAHEAD >= len(c):
            return DEFAULT_MULTIPLIER

        hl2 = (h[idx] + low[idx]) / 2.0
        actual_up = c[idx + LOOKAHEAD] > c[idx]

        best = MULT_MAX
        for m in MULT_GRID:
            upper = hl2 + m * atr[idx]
            lower = hl2 - m * atr[idx]
            # Direction the band would signal on the next candle's close.
            nxt = c[idx + 1]
            if nxt > upper:
                signal_up = True
            elif nxt < lower:
                signal_up = False
            else:
                continue  # no flip at this multiplier — try a tighter one
            if signal_up == actual_up:
                best = m
                break
        return float(np.clip(best, MULT_MIN, MULT_MAX))

    # ------------------------------------------------------------------
    # Learning / update
    # ------------------------------------------------------------------
    def update_history(self, pair: str, ohlc_df: pd.DataFrame) -> int:
        """Recompute and persist the pair's (features, optimal-multiplier) history.

        Every labelled candle (all but the last :data:`LOOKAHEAD`) becomes a
        training example for the KNN lookup.

        Args:
            pair: Pair symbol.
            ohlc_df: OHLC DataFrame.

        Returns:
            Number of labelled samples stored.
        """
        if ohlc_df is None or len(ohlc_df) < 21:
            return 0
        df = ohlc_df.reset_index(drop=True)
        feats = self._build_features(df)
        atr = self._atr_series(df, 14)

        rows: List[np.ndarray] = []
        labels: List[float] = []
        for idx in range(14, len(df) - LOOKAHEAD):
            rows.append(feats[idx])
            labels.append(self._optimal_multiplier(df, idx, atr))

        history = {
            "features": np.array(rows) if rows else np.empty((0, 5)),
            "multipliers": np.array(labels) if labels else np.empty((0,)),
        }
        self._save_history(pair, history)
        return len(labels)

    # ------------------------------------------------------------------
    # KNN dynamic multiplier
    # ------------------------------------------------------------------
    def _knn_multiplier(
        self, pair: str, current_feat: np.ndarray, history: Dict[str, np.ndarray]
    ) -> tuple[float, float]:
        """Average the optimal multiplier of the K nearest historical candles.

        Args:
            pair: Pair symbol (logging).
            current_feat: Length-5 feature vector for the current candle.
            history: Stored history dict.

        Returns:
            ``(multiplier, agreement)`` where agreement is the fraction of the K
            neighbors whose stored multiplier sits on the same side of the mean
            (a rough direction-consistency proxy, 0-1).
        """
        X = history["features"]
        y = history["multipliers"]
        if len(X) < MIN_HISTORY:
            return DEFAULT_MULTIPLIER, 0.0

        # Standardize features so no single dimension dominates the distance.
        mean = X.mean(axis=0)
        std = X.std(axis=0)
        std[std == 0] = 1.0
        Xn = (X - mean) / std
        cur = (current_feat - mean) / std

        dists = np.linalg.norm(Xn - cur, axis=1)
        k = min(self.k, len(X))
        nn_idx = np.argsort(dists)[:k]
        nn_mults = y[nn_idx]

        multiplier = float(np.clip(nn_mults.mean(), MULT_MIN, MULT_MAX))
        # Agreement: fraction of neighbors on the majority side of the median.
        med = np.median(nn_mults)
        majority = max((nn_mults <= med).sum(), (nn_mults > med).sum())
        agreement = majority / k
        return multiplier, float(agreement)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def compute(
        self, pair: str, ohlc_df: pd.DataFrame, update: bool = True
    ) -> Dict[str, Any]:
        """Compute the AI SuperTrend state for the latest candle.

        Args:
            pair: Pair symbol.
            ohlc_df: OHLC DataFrame (open/high/low/close/volume).
            update: If True, refresh + persist the pair's history first so the
                KNN warms up over repeated calls.

        Returns:
            Dict with keys ``direction`` ('UP'|'DOWN'), ``multiplier`` (float),
            ``upper`` (float), ``lower`` (float), ``signal_strength`` (0-1).
        """
        fallback = {
            "direction": "UP", "multiplier": DEFAULT_MULTIPLIER,
            "upper": 0.0, "lower": 0.0, "signal_strength": 0.0,
        }
        if ohlc_df is None or len(ohlc_df) < 15:
            logger.warning("Insufficient OHLC for %s — returning fallback.", pair)
            return fallback

        df = ohlc_df.reset_index(drop=True)
        if update:
            n = self.update_history(pair, df)
            logger.debug("Updated %s history: %d samples", pair, n)
        history = self._load_history(pair)

        feats = self._build_features(df)
        current_feat = feats[-1]
        multiplier, agreement = self._knn_multiplier(pair, current_feat, history)

        atr = self._atr_series(df, 14)
        h = float(df["high"].iloc[-1])
        low = float(df["low"].iloc[-1])
        close = float(df["close"].iloc[-1])
        hl2 = (h + low) / 2.0
        upper = hl2 + multiplier * atr[-1]
        lower = hl2 - multiplier * atr[-1]

        # Direction: standard SuperTrend read on the latest close.
        if close > upper:
            direction = "UP"
        elif close < lower:
            direction = "DOWN"
        else:
            # Inside the bands — bias by which band is nearer.
            direction = "UP" if (close - lower) >= (upper - close) else "DOWN"

        result = {
            "direction": direction,
            "multiplier": round(multiplier, 3),
            "upper": round(upper, 6),
            "lower": round(lower, 6),
            "signal_strength": round(agreement, 3),
        }
        logger.info(
            "%s dir=%s mult=%.2f strength=%.2f (hist=%d)",
            pair, direction, multiplier, agreement, len(history["multipliers"]),
        )
        return result


def _load_demo_df(pair_key: str) -> Optional[pd.DataFrame]:
    """Fetch live 4H OHLC for the demo via the PairUniverse fetcher."""
    try:
        from pair_universe import PairUniverse
    except ImportError:
        from jhl_v2.pair_universe import PairUniverse  # type: ignore
    return PairUniverse().fetch_ohlc(pair_key, interval=240)


if __name__ == "__main__":
    logger.info("=== AISupertrend demo ===")
    ast = AISupertrend()
    demo = {"BTC": "XXBTZUSD", "SOL": "SOLUSD", "XRP": "XRPUSD"}
    for sym, key in demo.items():
        df = _load_demo_df(key)
        if df is None:
            print(f"{sym:5s} -> OHLC fetch failed")
            continue
        # Call twice: first warms/persists history, second uses the KNN.
        ast.compute(sym, df)
        res = ast.compute(sym, df)
        print(
            f"{sym:5s} dir={res['direction']:4s} mult={res['multiplier']:.2f} "
            f"upper={res['upper']:.4f} lower={res['lower']:.4f} "
            f"strength={res['signal_strength']:.2f}"
        )
