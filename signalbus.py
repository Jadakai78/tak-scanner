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

logger = logging.getLogger("signalbus")

MODULE_DIR = Path(__file__).resolve().parent
# Canonical runtime location (volume mount) — default for writers
APP_DATA = Path("/app/data")
SIGNALBUS_PATH = APP_DATA / "signal_bus.json"
TMP_PATH = APP_DATA / "signalbus.tmp.json"
# Legacy fallbacks (read-only compatibility)
LEGACY_SIGNALBUS = MODULE_DIR / "signalbus.json"
LEGACY_SIGNAL_BUS = MODULE_DIR / "signal_bus.json"

DEFAULT_MAX_AGE_HOURS = 4

_EMPTY_BUS: Dict[str, Any] = {
    "lastscan": None,
    "nextscan": None,
    "fg": {"score": None, "label": None},
    "activepairs": 0,
    "deadpairs": 0,
    "signals": [],
    "killedsignals": [],
    "openpositions": [],
    "regimemap": {},
    "quiethours": False,
    "sprintmode": False,
    "sessionstats": {
        "signalsfired": 0,
        "signalskilled": 0,
        "sgradecount": 0,
        "scandurationsec": 0.0,
    },
}


class SignalBus:
    def __init__(self, path: Optional[Path] = None, tmp_path: Optional[Path] = None) -> None:
        # Default writer goes to /app/data/signal_bus.json
        self.path = path or SIGNALBUS_PATH
        self.tmp_path = tmp_path or TMP_PATH

    def _candidates(self) -> list[Path]:
        # Read candidates in preferred order for backward compatibility
        return [APP_DATA / "signal_bus.json", LEGACY_SIGNAL_BUS, LEGACY_SIGNALBUS]

    def read(self) -> Dict[str, Any]:
        bus = dict(_EMPTY_BUS)
        # Try configured path first, then fallbacks
        paths_to_try = [self.path] + [p for p in self._candidates() if p != self.path]
        for p in paths_to_try:
            try:
                if p.exists():
                    loaded = json.loads(p.read_text(encoding="utf-8") or "{}")
                    if isinstance(loaded, dict):
                        bus.update(loaded)
                    break
            except (json.JSONDecodeError, OSError) as exc:
                logger.error("Failed reading signal bus (%s); trying next candidate.", exc)
                continue
        return bus

    def write(self, bus: Dict[str, Any]) -> None:
        self._atomic_write(bus)

    def update(self, **kwargs: Any) -> None:
        bus = self.read()
        bus.update(kwargs)
        self._atomic_write(bus)

    def expire_old_signals(self, max_age_hours: int = DEFAULT_MAX_AGE_HOURS) -> int:
        bus = self.read()
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
            self._atomic_write(bus)
            logger.info("Expired %d stale signals.", removed)

        return removed

    @staticmethod
    def _still_fresh(sig: Dict[str, Any], now: datetime, cutoff: datetime) -> bool:
        exp = sig.get("expiresat")
        if exp:
            try:
                return datetime.fromisoformat(str(exp).replace("Z", "+00:00")) > now
            except ValueError:
                pass

        fired = sig.get("firedat")
        if fired:
            try:
                return datetime.fromisoformat(str(fired).replace("Z", "+00:00")) > cutoff
            except ValueError:
                pass

        return True

    def _atomic_write(self, bus: Dict[str, Any]) -> None:
        try:
            payload = json.dumps(bus, indent=2, default=str)
            # Ensure the parent directory exists
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            # Write tmp file in same directory for atomic replace
            try:
                self.tmp_path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            self.tmp_path.write_text(payload, encoding="utf-8")
            os.replace(str(self.tmp_path), str(self.path))
            logger.info(
                "Signal bus written: %d signals, %d killed.",
                len(bus.get("signals", [])),
                len(bus.get("killedsignals", [])),
            )
        except OSError as exc:
            logger.error("Atomic write failed: %s", exc)
            raise
