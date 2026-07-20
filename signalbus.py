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
TMP_PATH = APP_DATA / "signal_bus.tmp.json"
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
    "rts_signals": [],
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
        """
        Expire stale signals in BOTH legacy and RTS lanes:
        - legacy lane: signals
        - RTS lane: rts_signals

        Supports both timestamp styles:
        - expiresat / firedat
        - expires_at / fired_at
        """
        bus = self.read()
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=max_age_hours)

        removed_total = 0

        for lane in ("signals", "rts_signals"):
            original = bus.get(lane, [])
            if not isinstance(original, list):
                continue

            kept = []
            removed = 0
            for sig in original:
                if isinstance(sig, dict) and self._still_fresh(sig, now, cutoff):
                    kept.append(sig)
                else:
                    removed += 1

            if removed:
                bus[lane] = kept
                removed_total += removed
                logger.info("Expired %d stale signals from lane '%s'.", removed, lane)

        if removed_total:
            self._atomic_write(bus)

        return removed_total

    @staticmethod
    def _parse_iso(value: Any) -> Optional[datetime]:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    def _still_fresh(self, sig: Dict[str, Any], now: datetime, cutoff: datetime) -> bool:
        # Prefer explicit expires timestamp if present
        exp = self._parse_iso(sig.get("expiresat")) or self._parse_iso(sig.get("expires_at"))
        if exp is not None:
            return exp > now

        # Fallback to fired timestamp age check
        fired = self._parse_iso(sig.get("firedat")) or self._parse_iso(sig.get("fired_at"))
        if fired is not None:
            return fired > cutoff

        # No timestamps => keep (defensive default)
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

            # After successful replace, collect mtime and write a debug helper file
            try:
                try:
                    mtime = datetime.utcfromtimestamp(self.path.stat().st_mtime).isoformat() + "Z"
                except Exception:
                    mtime = None
                debug = {
                    "written_path": str(self.path.resolve()),
                    "written_at": datetime.now(timezone.utc).isoformat(),
                    "file_mtime": mtime,
                    "signals_count": len(bus.get("signals", [])),
                    "rts_signals_count": len(bus.get("rts_signals", [])),
                    "killed_count": len(bus.get("killedsignals", [])),
                }
                dbgpath = self.path.parent / "signal_bus.write_debug.json"
                try:
                    dbgpath.write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")
                except Exception:
                    logger.debug("Failed to write debug helper file %s", dbgpath)
            except Exception:
                pass

            logger.info(
                "Signal bus written: legacy=%d rts=%d killed=%d path=%s",
                len(bus.get("signals", [])),
                len(bus.get("rts_signals", [])),
                len(bus.get("killedsignals", [])),
                str(self.path),
            )
        except OSError as exc:
            logger.error("Atomic write failed: %s", exc)
            raise
