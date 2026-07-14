# remi.py — JHL Holdings Kill Protocol
# Collapsed from 6 sequential checks to 3 hard gates.
# Gate 1: Macro + News (combined)
# Gate 2: HTF Conflict (Daily EMA200)
# Gate 3: Composite Filter (volume trap + duplicate + regime + FG)
# First failing gate kills the signal. Kill logged to remikills.log.
# July 5, 2026 — Probability collapse build.

from __future__ import annotations
import json
import logging
import numpy as np
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from remi_macro import RemiMacro
from remi_news import RemiFeed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s"
)
logger = logging.getLogger("remi")

MODULE_DIR      = Path(__file__).resolve().parent
SIGNAL_BUS_PATH = MODULE_DIR / "signal_bus.json"
KILL_LOG_PATH   = MODULE_DIR / "remikills.log"
DUPLICATE_WINDOW_HOURS = 4

# Engine → allowed regimes (unchanged from v1)
ENGINE_REQUIRED_REGIMES = {
    "S1": ["TREND_UP", "TREND_DOWN"],
    "S2": ["TREND_UP", "TREND_DOWN"],
    "S3": ["VOLATILE"],
    "S4": ["RANGE"],
    "S5": ["TREND_UP", "TREND_DOWN"],
    "S6": ["RANGE", "FEAR"],
    "S7": ["RANGE"],
    "S8": ["TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "FEAR", "DEAD"],
    "S9": ["FEAR"],
}

# Shared singletons — one fetch per scan cycle
_remimacro = RemiMacro()
_remifeed  = RemiFeed()


class Remi:
    """
    3-gate kill protocol.
    Gate 1 — Macro + News (combined, one fetch)
    Gate 2 — HTF Conflict (Daily EMA200 slope)
    Gate 3 — Composite Filter (volume trap + duplicate + regime + FG)
    """

    def __init__(
        self,
        signal_bus_path: Optional[Path] = None,
        kill_log_path:   Optional[Path] = None,
    ) -> None:
        self.signal_bus_path = signal_bus_path or SIGNAL_BUS_PATH
        self.kill_log_path   = kill_log_path   or KILL_LOG_PATH

    # ------------------------------------------------------------------
    # PUBLIC
    # ------------------------------------------------------------------

    def evaluate(
        self,
        signal:     Dict[str, Any],
        ohlc_daily: Optional[object],   # pd.DataFrame preferred
        fg_score:   int,
        now:        Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Run all 3 gates. First kill wins.
        Returns: {status: CLEAN|KILLED, reason: str|None, caution: bool}
        """
        now    = now or datetime.now(timezone.utc)
        bias   = str(signal.get("bias", "")).upper()
        pair   = str(signal.get("pair", ""))
        caution = False

        # ── GATE 1: MACRO + NEWS ──────────────────────────────────────
        macro = _remimacro.check()
        if macro.kill:
            return self._kill(signal, macro.reason or "MACRO_KILL")
        if macro.caution:
            caution = True
            logger.info("Remi %s GATE1 CAUTION — %s", pair, macro.reason)

        news = _remifeed.evaluate_pair(pair)
        if news.kill:
            return self._kill(signal, "NEWS_SENTIMENT_KILL")
        if news.caution:
            caution = True
            logger.info("Remi %s GATE1 CAUTION — news score %s", pair, news.score)

        # ── GATE 2: HTF CONFLICT ──────────────────────────────────────
        if self._htf_conflict(ohlc_daily, bias):
            return self._kill(signal, "HTF_CONFLICT")

        # ── GATE 3: COMPOSITE FILTER ──────────────────────────────────
        kill_reason = self._composite(signal, fg_score, bias, now)
        if kill_reason:
            return self._kill(signal, kill_reason)

        return {"status": "CLEAN", "reason": None, "caution": caution}

    # ------------------------------------------------------------------
    # GATE 3 — COMPOSITE (volume trap + duplicate + regime + FG)
    # ------------------------------------------------------------------

    def _composite(
        self,
        signal:   Dict[str, Any],
        fg_score: int,
        bias:     str,
        now:      datetime,
    ) -> Optional[str]:
        """Returns kill reason string or None if all pass."""

        # Fear & Greed hard conflict
        if fg_score > 75 and bias == "LONG":
            return "FG_GREED_LONG"
        if fg_score < 15 and bias == "SHORT":
            return "FG_FEAR_SHORT"
        if 15 <= fg_score < 30 and bias == "LONG":
            # Caution only — logged upstream, don't kill
            pass

        # Volume trap
        if self._volume_trap(signal):
            return "VOLUME_TRAP"

        # Duplicate signal (same pair/bias/engine within window)
        if self._is_duplicate(signal, now):
            return "DUPLICATE"

        # Regime mismatch
        engine   = str(signal.get("engine", "")).upper()
        required = ENGINE_REQUIRED_REGIMES.get(engine, [])
        if required and signal.get("regime") not in required:
            return "REGIME_MISMATCH"

        return None  # All checks passed

    # ------------------------------------------------------------------
    # HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _htf_conflict(ohlc_daily, bias: str) -> bool:
        """True if Daily EMA200 slope opposes signal bias."""
        if ohlc_daily is None:
            return False
        try:
            import pandas as pd
            if not isinstance(ohlc_daily, pd.DataFrame):
                return False
            if len(ohlc_daily) < 206:
                return False
            ema200 = ohlc_daily["close"].ewm(span=200, adjust=False).mean()
            prev   = float(ema200.iloc[-6])
            if prev == 0:
                return False
            slope  = float(ema200.iloc[-1] - prev) / abs(prev) * 100
        except (KeyError, IndexError, ValueError):
            return False

        if slope > 0 and bias == "SHORT":
            return True
        if slope < 0 and bias == "LONG":
            return True
        return False

    def _is_duplicate(self, signal: Dict[str, Any], now: datetime) -> bool:
        """True if identical pair/bias/engine fired within DUPLICATE_WINDOW_HOURS."""
        if not self.signal_bus_path.exists():
            return False
        try:
            bus = json.loads(self.signal_bus_path.read_text() or "{}")
        except (json.JSONDecodeError, OSError):
            return False

        cutoff = now - timedelta(hours=DUPLICATE_WINDOW_HOURS)
        for prior in bus.get("signals", []):
            if (
                prior.get("pair")   == signal.get("pair")   and
                prior.get("bias")   == signal.get("bias")   and
                prior.get("engine") == signal.get("engine")
            ):
                fired = prior.get("fired_at")
                if not fired:
                    continue
                try:
                    ts = datetime.fromisoformat(fired.replace("Z", "+0000"))
                except ValueError:
                    continue
                if ts > cutoff:
                    return True
        return False

    @staticmethod
    def _volume_trap(signal: Dict[str, Any]) -> bool:
        """
        Trap: candle N-2 volume > 3x avg AND candles N-1, N below avg.
        Uses ohlc_4h list on the signal if present.
        """
        ohlc = signal.get("ohlc_4h")
        if not ohlc or len(ohlc) < 20:
            return False
        try:
            vols = np.array([float(r[6]) for r in ohlc], dtype=float)
        except (ValueError, IndexError, TypeError):
            return False

        avg = float(vols[-20:].mean())
        if avg == 0:
            return False
        return (
            vols[-3] > 3 * avg and
            vols[-2] < avg     and
            vols[-1] < avg
        )

    # ------------------------------------------------------------------
    # KILL + LOG
    # ------------------------------------------------------------------

    def _kill(self, signal: Dict[str, Any], reason: str) -> Dict[str, Any]:
        self._log_kill(signal, reason)
        logger.info(
            "Remi KILLED %s %s %s — %s",
            signal.get("pair"), signal.get("engine"),
            signal.get("bias"), reason
        )
        return {"status": "KILLED", "reason": reason, "caution": False}

    def _log_kill(self, signal: Dict[str, Any], reason: str) -> None:
        rec = {
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "pair":         signal.get("pair"),
            "engine":       signal.get("engine"),
            "bias":         signal.get("bias"),
            "kill_reason":  reason,
            "signal_score": signal.get("conviction", signal.get("score")),
        }
        try:
            with self.kill_log_path.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError as exc:
            logger.error("Failed to write kill log: %s", exc)


# ----------------------------------------------------------------------
# QUICK SMOKE TEST
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import pandas as pd

    remi = Remi()

    base = {
        "pair": "BTC", "bias": "LONG", "engine": "S1",
        "regime": "TREND_UP", "entry": 61000,
        "sl": 60000, "tp": 63000, "conviction": 0.9,
    }

    # Synthetic daily uptrend
    up = pd.DataFrame({
        "close":  np.linspace(40000, 61000, 220),
        "high":   np.linspace(40000, 61000, 220) * 1.01,
        "low":    np.linspace(40000, 61000, 220) * 0.99,
        "open":   np.linspace(40000, 61000, 220),
        "volume": [100.0] * 220,
    })

    print("CLEAN case:         ", remi.evaluate(dict(base), up, fg_score=40))
    print("HTF_CONFLICT:       ", remi.evaluate({**base, "bias": "SHORT"}, up, fg_score=40)["reason"])
    print("FG_GREED_LONG:      ", remi.evaluate(dict(base), up, fg_score=80)["reason"])
    print("REGIME_MISMATCH S1: ", remi.evaluate({**base, "regime": "RANGE"}, up, fg_score=40)["reason"])