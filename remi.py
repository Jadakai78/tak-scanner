"""remi.py — Remi, the engine-aware kill protocol (Layer 3 QC).

Each engine class has its own kill/caution rules. Trend engines require HTF
alignment. Counter-trend and range engines are exempt from HTF_CONFLICT kills
and get a caution instead. Macro proximity tags CAUTION only.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("remi")

MODULE_DIR = Path(__file__).resolve().parent
SIGNAL_BUS_PATH = MODULE_DIR / "signal_bus.json"
KILL_LOG_PATH = MODULE_DIR / "remi_kills.log"

DUPLICATE_WINDOW_HOURS = 4
MACRO_WINDOW_HOURS = 2
DISABLE_DUPLICATE_KILL = True

# Engines that trade WITH the trend — HTF conflict is a hard kill
TREND_ENGINES = {"S1", "S2", "S5"}

# Engines that trade AGAINST or ACROSS the trend — HTF conflict is caution only
COUNTER_TREND_ENGINES = {"S3", "S4", "S6", "S7", "S9", "S10"}

# Regime mismatch kills are engine-specific
ENGINE_REQUIRED_REGIMES: Dict[str, list] = {
    "S1": ["TREND_UP", "TREND_DOWN"],
    "S2": ["TREND_UP", "TREND_DOWN"],
    "S3": ["VOLATILE", "TREND_DOWN", "TREND_UP"],
    "S4": ["RANGE"],
    "S5": ["TREND_UP", "TREND_DOWN"],
    "S6": ["RANGE", "FEAR", "TREND_DOWN"],
    "S7": ["RANGE"],
    "S8": ["TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "FEAR", "DEAD"],
    "S9": ["FEAR", "TREND_DOWN"],
    "S10": ["TREND_DOWN", "VOLATILE", "RANGE", "FEAR"],
}

# FG gates per engine class
FG_GREED_LONG_GATE = 75     # above this, LONG is killed for trend engines
FG_FEAR_SHORT_GATE = 15     # below this, SHORT is killed for trend engines
FG_CAUTION_LONG_LOW = 15    # below this, LONG gets caution (all engines)
FG_CAUTION_LONG_HIGH = 30   # above low, LONG gets caution (all engines)


class Remi:
    """Engine-aware stress-tester that kills weak or conflicted signals."""

    def __init__(
        self,
        signal_bus_path: Optional[Path] = None,
        kill_log_path: Optional[Path] = None,
    ) -> None:
        self.signal_bus_path = signal_bus_path or SIGNAL_BUS_PATH
        self.kill_log_path = kill_log_path or KILL_LOG_PATH

    def evaluate(
        self,
        signal: Dict[str, Any],
        ohlc_daily: Optional[pd.DataFrame],
        fg_score: int,
        now: Optional[datetime] = None,
        rts_outputs: Optional[list] = None,
        current_bar: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        bias = str(signal.get("bias", "")).upper()
        engine = str(signal.get("engine", "")).upper()
        caution = False
        caution_reason: Optional[str] = None

        # ── Trap gate — RTS kill check FIRST, before all other logic ──
        if rts_outputs:
            try:
                from trap_detector import remi_trap_gate
                trap_result = remi_trap_gate(signal, rts_outputs, current_bar)
                if trap_result["status"] == "KILLED":
                    return self._kill(signal, f"TRAP_DETECTED: {trap_result['reason']}")
                if trap_result["status"] == "CAUTION":
                    caution = True
                    caution_reason = f"TRAP_CAUTION: {trap_result['reason']}"
            except Exception as exc:
                logger.warning("Remi trap gate error %s: %s", signal.get("pair"), exc)

        # ── Macro window (always caution, never kill) ──────────────────
        if self._macro_window(now):
            caution = True
            caution_reason = "macro_window"
            logger.info("Remi %s %s: macro window -> CAUTION", signal.get("pair"), engine)

        # ── HTF conflict — engine-aware ────────────────────────────────
        htf = self._htf_conflict(ohlc_daily, bias)
        if htf:
            if engine in TREND_ENGINES:
                return self._kill(signal, "HTF_CONFLICT")
            else:
                caution = True
                caution_reason = caution_reason or "HTF_CONFLICT_counter_trend"
                logger.info(
                    "Remi %s %s %s: HTF_CONFLICT -> CAUTION (counter-trend engine)",
                    signal.get("pair"), engine, bias,
                )

        # ── Fear & Greed gates — stricter for trend engines ────────────
        if engine in TREND_ENGINES:
            if fg_score > FG_GREED_LONG_GATE and bias == "LONG":
                return self._kill(signal, "FG_GREED_LONG")
            if fg_score < FG_FEAR_SHORT_GATE and bias == "SHORT":
                return self._kill(signal, "FG_FEAR_SHORT")
        else:
            # Counter-trend engines: extreme F&G is an EDGE not a kill
            if fg_score > 85 and bias == "LONG":
                return self._kill(signal, "FG_EXTREME_GREED_LONG")
            if fg_score < 10 and bias == "SHORT":
                return self._kill(signal, "FG_EXTREME_FEAR_SHORT")

        # Caution zone for longs in fear
        if FG_CAUTION_LONG_LOW <= fg_score <= FG_CAUTION_LONG_HIGH and bias == "LONG":
            caution = True
            caution_reason = caution_reason or "fg_fear_caution"

        # ── Duplicate kill ─────────────────────────────────────────────
        if not DISABLE_DUPLICATE_KILL and self._is_duplicate(signal, now):
            return self._kill(signal, "DUPLICATE")

        # ── Volume trap ────────────────────────────────────────────────
        if self._volume_trap(signal):
            return self._kill(signal, "VOLUME_TRAP")

        # ── Regime mismatch ────────────────────────────────────────────
        required = ENGINE_REQUIRED_REGIMES.get(engine, [])
        if required and signal.get("regime") not in required:
            return self._kill(signal, "REGIME_MISMATCH")

        status = "CAUTION" if caution else "CLEAN"
        return {"status": status, "reason": None, "caution": caution_reason}

    @staticmethod
    def _macro_window(now: datetime) -> bool:
        weekday = now.weekday()
        hour = now.hour
        windows = {0: 13, 2: 18, 4: 13}
        target = windows.get(weekday)
        if target is None:
            return False
        return abs(hour - target) <= MACRO_WINDOW_HOURS

    @staticmethod
    def _htf_conflict(ohlc_daily: Optional[pd.DataFrame], bias: str) -> bool:
        if ohlc_daily is None or len(ohlc_daily) < 206:
            return False
        try:
            ema200 = ohlc_daily["close"].ewm(span=200, adjust=False).mean()
            prev = float(ema200.iloc[-6])
            if prev == 0:
                return False
            slope = (float(ema200.iloc[-1]) - prev) / abs(prev) * 100
        except (KeyError, IndexError, ValueError):
            return False
        if slope > 0 and bias == "SHORT":
            return True
        if slope < 0 and bias == "LONG":
            return True
        return False

    def _is_duplicate(self, signal: Dict[str, Any], now: datetime) -> bool:
        if not self.signal_bus_path.exists():
            return False
        try:
            bus = json.loads(self.signal_bus_path.read_text() or "{}")
        except (json.JSONDecodeError, OSError):
            return False
        cutoff = now - timedelta(hours=DUPLICATE_WINDOW_HOURS)
        for prior in bus.get("signals", []):
            if (
                prior.get("pair") == signal.get("pair")
                and prior.get("bias") == signal.get("bias")
                and prior.get("engine") == signal.get("engine")
            ):
                fired = prior.get("fired_at")
                if not fired:
                    continue
                try:
                    ts = datetime.fromisoformat(fired.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts >= cutoff:
                    return True
        return False

    @staticmethod
    def _volume_trap(signal: Dict[str, Any]) -> bool:
        ohlc = signal.get("ohlc_4h")
        if not ohlc or len(ohlc) < 20:
            return False
        try:
            vols = np.array([float(r[6]) for r in ohlc], dtype=float)
        except (ValueError, IndexError, TypeError):
            return False
        avg = float(vols[-20:].mean())
        if avg <= 0:
            return False
        return bool(vols[-3] > 3 * avg and vols[-2] < avg and vols[-1] < avg)

    def _kill(self, signal: Dict[str, Any], reason: str) -> Dict[str, Any]:
        self._log_kill(signal, reason)
        logger.info(
            "Remi KILLED %s %s %s: %s",
            signal.get("pair"),
            signal.get("engine"),
            signal.get("bias"),
            reason,
        )
        return {"status": "KILLED", "reason": reason, "caution": False}

    def _log_kill(self, signal: Dict[str, Any], reason: str) -> None:
        rec = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pair": signal.get("pair"),
            "engine": signal.get("engine"),
            "bias": signal.get("bias"),
            "kill_reason": reason,
            "signal_score": signal.get("conviction", signal.get("score")),
        }
        try:
            with self.kill_log_path.open("a") as fh:
                fh.write(json.dumps(rec) + "\n")
        except OSError as exc:
            logger.error("Failed to write kill log: %s", exc)


if __name__ == "__main__":
    logger.info("=== Remi demo ===")
    remi = Remi()
    base = {
        "pair": "BTC", "bias": "LONG", "engine": "S1", "regime": "TREND_UP",
        "entry": 61000, "sl": 60000, "tp": 63000, "conviction": 0.9,
    }
    up = pd.DataFrame({"close": np.linspace(40000, 61000, 220)})
    up["high"] = up["close"] * 1.01
    up["low"] = up["close"] * 0.99
    up["open"] = up["close"]
    up["volume"] = 100.0
    short = dict(base, bias="SHORT")
    mism = dict(base, regime="RANGE")
    s6sig = dict(base, engine="S6", bias="LONG", regime="TREND_DOWN")
    print("CLEAN:", remi.evaluate(dict(base), up, fg_score=40))
    print("HTF_CONFLICT S1 SHORT:", remi.evaluate(short, up, fg_score=40)["reason"])
    print("HTF_CONFLICT S6 LONG (should be CAUTION):", remi.evaluate(s6sig, up, fg_score=40)["status"])
    print("REGIME_MISMATCH S1 RANGE:", remi.evaluate(mism, up, fg_score=40)["reason"])
