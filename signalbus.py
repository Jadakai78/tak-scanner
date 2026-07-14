"""signal_bus.py — Shared-state writer for the Tak scanner + live feed.

``signal_bus.json`` is the single source of truth: the scanner writes it, the
feed reads it. Writes are atomic (write to ``signal_bus.tmp.json`` then
``os.replace``) so the feed never reads a half-written file.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("signal_bus")

MODULE_DIR = Path(__file__).resolve().parent
SIGNAL_BUS_PATH = MODULE_DIR / "signal_bus.json"
TMP_PATH = MODULE_DIR / "signal_bus.tmp.json"

DEFAULT_MAX_AGE_HOURS = 4

_EMPTY_BUS: Dict[str, Any] = {
    "last_scan": None,
    "next_scan": None,
    "f_g": {"score": None, "label": None},
    "active_pairs": 0,
    "dead_pairs": 0,
    "pair_universe": [],
    "signals": [],
    "killed_signals": [],
    "open_positions": [],
    "regime_map": {},
    "sprint_mode": False,
    "session_stats": {
        "signals_fired": 0,
        "signals_killed": 0,
        "s_grade_count": 0,
        "scan_duration_sec": 0.0,
    },
}


class SignalBus:
    """Reads/writes the shared ``signal_bus.json`` state file atomically."""

    def __init__(self, path: Optional[Path] = None, tmp_path: Optional[Path] = None) -> None:
        self.path = path or SIGNAL_BUS_PATH
        self.tmp_path = tmp_path or TMP_PATH

    def get_signals(self) -> Dict[str, Any]:
        """Read and return the current bus, or an empty template on failure."""
        bus = dict(_EMPTY_BUS)
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text() or "{}")
                bus.update(loaded)
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Failed reading bus (%s) — returning template.", exc)
        return self._normalize_bus(bus)

    def update(self, **kwargs):
        bus = self.read() if hasattr(self, "read") else {}
        bus.update(kwargs)
        self.write(bus)

    def expire_old_signals(self, max_age_hours: int = DEFAULT_MAX_AGE_HOURS) -> int:
        """Drop signals whose ``expires_at`` (or age) exceeds the window."""
        bus = self.get_signals()
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=max_age_hours)
        kept = []
        removed = 0
        for sig in bus.get("signals", []):
            if self._still_fresh(sig, now, cutoff):
                kept.append(sig)
            else:
                removed += 1
        if removed:
            bus["signals"] = kept
            bus = self._normalize_bus(bus)
            self._atomic_write(bus)
            logger.info("Expired %d stale signals.", removed)
        return removed

    @staticmethod
    def _normalize_bus(bus: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize schema so legacy keys do not survive indefinitely."""
        normalized = dict(bus)
        if "f_g" in normalized and normalized.get("f_g") is not None:
            normalized.pop("fg", None)
        elif "fg" in normalized and normalized.get("fg") is not None:
            normalized["f_g"] = normalized.pop("fg")
        return normalized

    @staticmethod
    def _still_fresh(sig: Dict[str, Any], now: datetime, cutoff: datetime) -> bool:
        """Return True if a signal has not yet expired."""
        exp = sig.get("expires_at")
        if exp:
            try:
                return datetime.fromisoformat(exp.replace("Z", "+00:00")) > now
            except ValueError:
                pass
        fired = sig.get("fired_at")
        if fired:
            try:
                return datetime.fromisoformat(fired.replace("Z", "+00:00")) > cutoff
            except ValueError:
                pass
        return True

    def _atomic_write(self, bus: Dict[str, Any]) -> None:
        """Write the bus to a temp file then atomically replace the target."""
        try:
            self.tmp_path.write_text(json.dumps(bus, indent=2, default=str))
            os.replace(self.tmp_path, self.path)
            logger.info(
                "Signal bus written: %d signals, %d killed.",
                len(bus.get("signals", [])),
                len(bus.get("killed_signals", [])),
            )
        except OSError as exc:
            logger.error("Atomic write failed: %s", exc)


if __name__ == "__main__":
    logger.info("=== SignalBus demo ===")
    bus = SignalBus()
    now = datetime.now(timezone.utc)

    demo = {
        "last_scan": now.isoformat(),
        "next_scan": (now + timedelta(hours=4)).isoformat(),
        "f_g": {"score": 19, "label": "Extreme Fear"},
        "fg": {"score": 27, "label": "Fear"},
        "active_pairs": 2,
        "dead_pairs": 0,
        "signals": [
            {
                "pair": "BTC",
                "bias": "SHORT",
                "engine": "S1",
                "grade": "S",
                "conviction": 0.91,
                "fired_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=4)).isoformat(),
            },
            {
                "pair": "SOL",
                "bias": "LONG",
                "engine": "S9",
                "grade": "B",
                "conviction": 0.64,
                "fired_at": (now - timedelta(hours=6)).isoformat(),
                "expires_at": (now - timedelta(hours=2)).isoformat(),
            },
        ],
        "killed_signals": [
            {
                "pair": "XRP",
                "engine": "S1",
                "bias": "LONG",
                "kill_reason": "HTF_CONFLICT",
                "killed_at": now.isoformat(),
            },
        ],
        "regime_map": {"BTC": "TREND_DOWN", "SOL": "FEAR"},
        "session_stats": {
            "signals_fired": 2,
            "signals_killed": 1,
            "s_grade_count": 1,
            "scan_duration_sec": 11.2,
        },
    }

    bus.update(demo)
    current = bus.get_signals()
    print("Wrote demo bus. Signals:", len(current["signals"]))
    print("Has f_g:", "f_g" in current)
    print("Has fg:", "fg" in current)
    removed = bus.expire_old_signals(max_age_hours=4)
    print(f"Expired {removed} stale signal(s). Remaining:", len(bus.get_signals()["signals"]))
