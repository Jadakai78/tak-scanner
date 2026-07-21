"""regime_classifier.py — AI market-regime detection (Random Forest + rules).

AI Component 3 of JHL Trading Architecture v2. Classifies each pair's current
market regime into one of six states used by the strategy engines to decide
which engines are eligible to fire:

    TREND_UP / TREND_DOWN / RANGE / VOLATILE / FEAR / DEAD

Cold-start uses a deterministic rule-based bootstrap. Once ``tak_journal.csv``
has accumulated >= 100 labelled samples per class, a Random Forest is trained
and persisted to ``models/regime_rf.pkl`` and used instead.
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
import requests

try:
    from sklearn.ensemble import RandomForestClassifier
    _SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover - sklearn is a declared dependency
    _SKLEARN_AVAILABLE = False

logger = logging.getLogger(__name__)
logger.propagate = False

MODULE_DIR = Path(__file__).resolve().parent
MODELS_DIR = MODULE_DIR / "models"
MODEL_PATH = MODELS_DIR / "regime_rf.pkl"
JOURNAL_PATH = MODULE_DIR / "tak_journal.csv"

REGIMES = ["TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "FEAR", "DEAD"]

# Ordered feature vector used for both the rule bootstrap and the RF model.
FEATURE_KEYS = [
    "atr_pct_14", "atr_pct_50", "ema_slope_20", "ema_slope_50", "rsi_14",
    "bb_width", "volume_ratio", "candle_overlap_ratio", "fg_score", "return_24h",
]

MIN_SAMPLES_PER_CLASS = 100

# Funding-rate regime-input thresholds (as a fraction, e.g. 0.0001 = 0.01%).
FUNDING_BIAS_THRESHOLD = 0.0001    # 0.01% -- minimum funding to assign a directional bias
FUNDING_EXTREME_THRESHOLD = 0.0005  # 0.05% -- either direction flags VOLATILE_FUNDING
KRAKEN_FUNDING_URL = "https://api.kraken.com/0/public/FundingRate"
FUNDING_REQUEST_TIMEOUT = 8


class RegimeClassifier:
    """Classifies market regime from OHLC + sentiment features.

    Uses a rule-based bootstrap until a Random Forest can be trained from the
    trade journal (>= 100 samples per regime class), then prefers the RF.

    Attributes:
        model: Loaded ``RandomForestClassifier`` or ``None`` if not yet trained.
        model_path: Path to the persisted RF pickle.
        journal_path: Path to the CSV of labelled training samples.
    """

    def __init__(
        self,
        model_path: Optional[Path] = None,
        journal_path: Optional[Path] = None,
    ) -> None:
        """Initialize and attempt to load an existing trained model.

        Args:
            model_path: Override path to the RF pickle.
            journal_path: Override path to the training journal CSV.
        """
        self.model_path = model_path or MODEL_PATH
        self.journal_path = journal_path or JOURNAL_PATH
        self.model: Optional[Any] = None
        self.session = requests.Session()
        self._load_model()

    # ------------------------------------------------------------------
    # Feature extraction
    # ------------------------------------------------------------------
    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        """Exponential moving average."""
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> float:
        """Latest RSI value (0-100)."""
        if len(close) < period + 1:
            return 50.0
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return float(100 - (100 / (1 + rs)))

    @staticmethod
    def _atr(df: pd.DataFrame, period: int) -> float:
        """Latest ATR (Wilder) value."""
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
        return float(pd.Series(tr).ewm(alpha=1 / period, adjust=False).mean().iloc[-1])

    def extract_features(
        self, ohlc_df: pd.DataFrame, fg_score: float
    ) -> Dict[str, float]:
        """Compute the 10-element regime feature dictionary.

        Args:
            ohlc_df: OHLC DataFrame with columns open/high/low/close/volume.
            fg_score: Fear & Greed index value (0-100).

        Returns:
            Dict keyed by :data:`FEATURE_KEYS`. Missing data falls back to
            neutral defaults so classification never crashes.
        """
        defaults = {
            "atr_pct_14": 0.0, "atr_pct_50": 0.0, "ema_slope_20": 0.0,
            "ema_slope_50": 0.0, "rsi_14": 50.0, "bb_width": 0.0,
            "volume_ratio": 1.0, "candle_overlap_ratio": 0.0,
            "fg_score": float(fg_score), "return_24h": 0.0,
        }
        if ohlc_df is None or len(ohlc_df) < 21:
            logger.warning("Insufficient OHLC for feature extraction; using defaults.")
            return defaults

        df = ohlc_df.copy().reset_index(drop=True)
        close = df["close"]
        last_close = float(close.iloc[-1])
        if last_close <= 0:
            return defaults

        feats = dict(defaults)
        try:
            feats["atr_pct_14"] = self._atr(df, 14) / last_close * 100
            period50 = 50 if len(df) >= 51 else min(len(df) - 1, 14)
            feats["atr_pct_50"] = self._atr(df, period50) / last_close * 100

            ema20 = self._ema(close, 20)
            ema50 = self._ema(close, 50)
            if len(ema20) > 5 and ema20.iloc[-6] != 0:
                feats["ema_slope_20"] = (
                    (ema20.iloc[-1] - ema20.iloc[-6]) / abs(ema20.iloc[-6]) * 100
                )
            if len(ema50) > 5 and ema50.iloc[-6] != 0:
                feats["ema_slope_50"] = (
                    (ema50.iloc[-1] - ema50.iloc[-6]) / abs(ema50.iloc[-6]) * 100
                )

            feats["rsi_14"] = self._rsi(close, 14)

            # Bollinger band width (20, 2 std) as % of price.
            mid = close.rolling(20).mean().iloc[-1]
            std = close.rolling(20).std().iloc[-1]
            if mid and not np.isnan(std):
                upper, lower = mid + 2 * std, mid - 2 * std
                feats["bb_width"] = (upper - lower) / last_close * 100

            avg20_vol = float(df["volume"].tail(20).mean())
            cur_vol = float(df["volume"].iloc[-1])
            feats["volume_ratio"] = cur_vol / avg20_vol if avg20_vol > 0 else 1.0

            feats["candle_overlap_ratio"] = self._candle_overlap_ratio(df)

            if len(close) >= 7 and close.iloc[-7] != 0:
                feats["return_24h"] = (
                    (last_close - close.iloc[-7]) / close.iloc[-7] * 100
                )
        except (KeyError, IndexError, ValueError, ZeroDivisionError) as exc:
            logger.warning("Feature extraction partial failure: %s", exc)

        # Sanitize any NaN/inf that slipped through.
        for k, v in feats.items():
            if v is None or np.isnan(v) or np.isinf(v):
                feats[k] = defaults[k]
        return feats

    def _candle_overlap_ratio(self, df: pd.DataFrame) -> float:
        """Average bar-to-bar overlap of the last 5 candles, normalized by ATR.

        Overlap = min(high, prev_high) - max(low, prev_low), floored at 0.
        A high value means candles sit on top of each other (chop/range).
        """
        atr = self._atr(df, 14)
        if atr <= 0 or len(df) < 6:
            return 0.0
        tail = df.tail(6).reset_index(drop=True)
        overlaps: List[float] = []
        for i in range(1, len(tail)):
            hi = min(tail["high"].iloc[i], tail["high"].iloc[i - 1])
            lo = max(tail["low"].iloc[i], tail["low"].iloc[i - 1])
            overlaps.append(max(hi - lo, 0.0))
        if not overlaps:
            return 0.0
        return float(np.mean(overlaps) / atr)

    # ------------------------------------------------------------------
    # Rule-based bootstrap
    # ------------------------------------------------------------------
    @staticmethod
    def _rule_based_regime(features: Dict[str, float]) -> str:
        """Deterministic regime classification used until the RF is trained.

        Args:
            features: Feature dict from :meth:`extract_features`.

        Returns:
            One of :data:`REGIMES`.
        """
        if features["fg_score"] < 20:
            return "FEAR"
        if features["atr_pct_14"] < 0.5:
            return "DEAD"
        if features["atr_pct_14"] > 4.0 and features["volume_ratio"] > 2.0:
            return "VOLATILE"
        if abs(features["ema_slope_20"]) > 0.3 and abs(features["ema_slope_50"]) > 0.1:
            return "TREND_UP" if features["ema_slope_20"] > 0 else "TREND_DOWN"
        if features["bb_width"] < 3.0 and abs(features["ema_slope_20"]) < 0.1:
            return "RANGE"
        return "TREND_UP" if features["ema_slope_20"] > 0 else "TREND_DOWN"

    # ------------------------------------------------------------------
    # Model persistence / training
    # ------------------------------------------------------------------
    def _load_model(self) -> None:
        """Load the RF model from disk if it exists."""
        if not self.model_path.exists():
            logger.info("No trained RF model found — will use rule bootstrap.")
            return
        try:
            with self.model_path.open("rb") as fh:
                self.model = pickle.load(fh)
            logger.info("Loaded RF regime model from %s", self.model_path)
        except (pickle.UnpicklingError, OSError, EOFError) as exc:
            logger.error("Failed to load RF model: %s", exc)
            self.model = None

    def _journal_ready(self) -> Optional[pd.DataFrame]:
        """Return the journal DataFrame if it has enough samples per class.

        Returns:
            The journal DataFrame if every regime class has >=
            :data:`MIN_SAMPLES_PER_CLASS` labelled samples, else ``None``.
        """
        if not self.journal_path.exists():
            return None
        try:
            df = pd.read_csv(self.journal_path)
        except (pd.errors.ParserError, OSError, ValueError) as exc:
            logger.warning("Could not read journal: %s", exc)
            return None
        needed = set(FEATURE_KEYS) | {"regime"}
        if not needed.issubset(df.columns):
            logger.warning("Journal missing required columns for training.")
            return None
        counts = df["regime"].value_counts()
        if any(counts.get(r, 0) < MIN_SAMPLES_PER_CLASS for r in REGIMES):
            logger.info("Journal not yet balanced (>=%d/class) — using rules.",
                        MIN_SAMPLES_PER_CLASS)
            return None
        return df

    def train(self, force: bool = False) -> bool:
        """Train and persist the Random Forest from the trade journal.

        Args:
            force: Train even if per-class sample minimums are not met (used in
                tests / demos with synthetic data).

        Returns:
            True if a model was trained and saved, else False.
        """
        if not _SKLEARN_AVAILABLE:
            logger.error("scikit-learn unavailable — cannot train RF.")
            return False

        if force:
            if not self.journal_path.exists():
                logger.error("No journal to train from.")
                return False
            df = pd.read_csv(self.journal_path)
        else:
            df = self._journal_ready()
            if df is None:
                return False

        try:
            X = df[FEATURE_KEYS].to_numpy(dtype=float)
            y = df["regime"].astype(str).to_numpy()
            model = RandomForestClassifier(
                n_estimators=200, max_depth=8, random_state=42, n_jobs=-1,
            )
            model.fit(X, y)
            MODELS_DIR.mkdir(parents=True, exist_ok=True)
            with self.model_path.open("wb") as fh:
                pickle.dump(model, fh)
            self.model = model
            logger.info("Trained + saved RF on %d samples -> %s",
                        len(df), self.model_path)
            return True
        except (ValueError, KeyError, OSError) as exc:
            logger.error("RF training failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Funding-rate regime input
    # ------------------------------------------------------------------
    def fetch_funding_rate(
        self, pair: str, spot_price: Optional[float] = None, perp_price: Optional[float] = None,
    ) -> Optional[float]:
        """Fetch the current funding rate for a pair (fraction, e.g. 0.0001 = 0.01%).

        Tries Kraken's FundingRate endpoint first (perpetuals). If unavailable
        (endpoint missing/pair not a perp/HTTP error), falls back to an
        estimated funding rate from the spot-vs-perp price premium when both
        prices are supplied.

        Args:
            pair: Kraken pair symbol (e.g. "PF_XBTUSD" or "XBTUSD").
            spot_price: Optional spot price for the premium-based fallback.
            perp_price: Optional perpetual price for the premium-based fallback.

        Returns:
            Funding rate as a fraction, or ``None`` if it cannot be determined.
        """
        try:
            resp = self.session.get(
                KRAKEN_FUNDING_URL, params={"pair": pair}, timeout=FUNDING_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("error"):
                result = data.get("result", {})
                # Kraken shape varies; try a couple of common key patterns defensively.
                rate = None
                if isinstance(result, dict):
                    if "fundingRate" in result:
                        rate = result["fundingRate"]
                    elif pair in result and isinstance(result[pair], dict):
                        rate = result[pair].get("fundingRate")
                if rate is not None:
                    return float(rate)
        except Exception as exc:  # noqa: BLE001 - funding endpoint is best-effort
            logger.info("Kraken FundingRate unavailable for %s (%s) — trying premium fallback.",
                        pair, exc)

        if spot_price and perp_price and spot_price > 0:
            # Simple premium approximation: (perp - spot) / spot.
            return float((perp_price - spot_price) / spot_price)

        logger.info("No funding rate available for %s (no perp API, no premium data).", pair)
        return None

    @staticmethod
    def _funding_bias(funding_rate: Optional[float]) -> Dict[str, Any]:
        """Derive a directional bias + volatility flag from a funding rate.

        Positive funding (longs paying shorts) biases SHORT; negative funding
        (shorts paying longs) biases LONG. Magnitudes beyond
        :data:`FUNDING_EXTREME_THRESHOLD` in either direction flag
        VOLATILE_FUNDING.

        Args:
            funding_rate: Funding rate as a fraction, or None if unknown.

        Returns:
            ``{funding_rate, funding_rate_bias, volatile_funding}``.
        """
        if funding_rate is None:
            return {"funding_rate": None, "funding_rate_bias": "NEUTRAL", "volatile_funding": False}

        if funding_rate > FUNDING_BIAS_THRESHOLD:
            bias = "SHORT"
        elif funding_rate < -FUNDING_BIAS_THRESHOLD:
            bias = "LONG"
        else:
            bias = "NEUTRAL"

        volatile = abs(funding_rate) > FUNDING_EXTREME_THRESHOLD
        return {
            "funding_rate": funding_rate,
            "funding_rate_bias": bias,
            "volatile_funding": volatile,
        }

    def classify_full(
        self,
        pair: str,
        ohlc_df: pd.DataFrame,
        fg_score: float,
        funding_pair: Optional[str] = None,
        spot_price: Optional[float] = None,
        perp_price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Classify regime and enrich the result with the funding-rate signal.

        Args:
            pair: Pair symbol (for logging).
            ohlc_df: OHLC DataFrame.
            fg_score: Fear & Greed score (0-100).
            funding_pair: Kraken funding-rate pair symbol (defaults to ``pair``).
            spot_price: Optional spot price for the premium fallback.
            perp_price: Optional perp price for the premium fallback.

        Returns:
            ``{regime, funding_rate, funding_rate_bias, volatile_funding}``.
            When funding is extreme, ``regime`` is suffixed with the
            ``VOLATILE_FUNDING`` modifier (regime itself is unchanged; the
            modifier is additive/informational).
        """
        regime = self.classify(pair, ohlc_df, fg_score)
        funding_rate = self.fetch_funding_rate(funding_pair or pair, spot_price, perp_price)
        funding_info = self._funding_bias(funding_rate)

        out = {"regime": regime, **funding_info}
        if funding_info["volatile_funding"]:
            out["regime_modifier"] = "VOLATILE_FUNDING"
            logger.info("[FUNDING] %s extreme funding %.4f%% -> VOLATILE_FUNDING modifier",
                        pair, (funding_rate or 0) * 100)
        else:
            out["regime_modifier"] = None
        return out

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def classify(
        self, pair: str, ohlc_df: pd.DataFrame, fg_score: float
    ) -> str:
        """Classify the current regime for a pair.

        Prefers the Random Forest when a valid model is loaded and the journal
        is balanced; otherwise falls back to the rule bootstrap.

        Args:
            pair: Pair symbol (for logging only).
            ohlc_df: OHLC DataFrame.
            fg_score: Fear & Greed score (0-100).

        Returns:
            One of :data:`REGIMES`.
        """
        features = self.extract_features(ohlc_df, fg_score)

        if self.model is not None and self._journal_ready() is not None:
            try:
                vec = np.array([[features[k] for k in FEATURE_KEYS]], dtype=float)
                regime = str(self.model.predict(vec)[0])
                logger.info("[RF] %s -> %s", pair, regime)
                return regime
            except (ValueError, KeyError) as exc:
                logger.warning("RF predict failed for %s (%s) — using rules.",
                               pair, exc)

        regime = self._rule_based_regime(features)
        logger.info("[RULE] %s -> %s", pair, regime)
        return regime


def _load_demo_df(pair_key: str) -> Optional[pd.DataFrame]:
    """Fetch live 4H OHLC for the demo via the PairUniverse fetcher."""
    try:
        from pair_universe import PairUniverse
    except ImportError:
        from jhl_v2.pair_universe import PairUniverse  # type: ignore
    return PairUniverse().fetch_ohlc(pair_key, interval=240)


if __name__ == "__main__":
    logger.info("=== RegimeClassifier demo ===")
    rc = RegimeClassifier()

    demo = {"BTC": "XXBTZUSD", "SOL": "SOLUSD", "XRP": "XRPUSD"}
    fg = 55  # neutral sentiment for the demo
    for sym, key in demo.items():
        df = _load_demo_df(key)
        if df is None:
            print(f"{sym:5s} -> OHLC fetch failed")
            continue
        feats = rc.extract_features(df, fg)
        regime = rc.classify(sym, df, fg)
        print(
            f"{sym:5s} regime={regime:11s} "
            f"ATR%14={feats['atr_pct_14']:.2f} "
            f"emaSlope20={feats['ema_slope_20']:.3f} "
            f"bbW={feats['bb_width']:.2f} rsi={feats['rsi_14']:.1f} "
            f"overlap={feats['candle_overlap_ratio']:.2f} fg={fg}"
        )

    # Show the rule engine hits every branch with synthetic feature sets.
    print("\nRule bootstrap sanity check:")
    samples = {
        "FEAR": {"fg_score": 10, "atr_pct_14": 2.0},
        "DEAD": {"fg_score": 50, "atr_pct_14": 0.3},
        "VOLATILE": {"fg_score": 50, "atr_pct_14": 5.0, "volume_ratio": 3.0},
        "TREND_UP": {"fg_score": 50, "atr_pct_14": 1.5, "ema_slope_20": 0.5,
                     "ema_slope_50": 0.2},
        "RANGE": {"fg_score": 50, "atr_pct_14": 1.0, "ema_slope_20": 0.05,
                  "bb_width": 2.0},
    }
    base = {k: 0.0 for k in FEATURE_KEYS}
    base["rsi_14"] = 50.0
    base["volume_ratio"] = 1.0
    for expected, overrides in samples.items():
        f = dict(base)
        f.update(overrides)
        got = RegimeClassifier._rule_based_regime(f)
        print(f"  expect {expected:9s} -> got {got:9s} "
              f"{'OK' if got == expected else 'DIFF'}")

    print("\nFunding-rate bias sanity check:")
    funding_samples = {
        0.0002: "SHORT",     # positive funding -> short bias
        -0.0002: "LONG",     # negative funding -> long bias
        0.00005: "NEUTRAL",  # below threshold -> neutral
        0.0008: "SHORT",     # extreme positive -> short bias + volatile
        -0.0008: "LONG",     # extreme negative -> long bias + volatile
    }
    for rate, expected_bias in funding_samples.items():
        info = RegimeClassifier._funding_bias(rate)
        extreme = " VOLATILE_FUNDING" if info["volatile_funding"] else ""
        ok = "OK" if info["funding_rate_bias"] == expected_bias else "DIFF"
        print(f"  funding={rate:+.4f} -> bias={info['funding_rate_bias']:8s}{extreme} "
              f"(expect {expected_bias}) {ok}")

    print("\nFunding fallback via spot/perp premium (no live perp API needed):")
    fallback_rate = rc.fetch_funding_rate("NONEXISTENT_PAIR", spot_price=100.0, perp_price=100.06)
    print(f"  spot=100.0 perp=100.06 -> estimated funding={fallback_rate}")
