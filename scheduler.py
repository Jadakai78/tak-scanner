from __future__ import annotations

import importlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import requests

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("scheduler")

SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "1200"))
API_PUSH_URL = os.getenv("API_PUSH_URL", "").strip()
API_PUSH_TOKEN = os.getenv("API_PUSH_TOKEN", "").strip()

BASE_DATA_DIR = Path("/app/data") if Path("/app/data").exists() else Path(__file__).resolve().parent
BUS_PATH = BASE_DATA_DIR / "signal_bus.json"
LAST_GOOD_PATH = BASE_DATA_DIR / "last_good_signal_bus.json"

# Module/file to validate before importing OracleRunner
ORACLE_MODULE = "oracle_runner"
ORACLE_FILE = Path(__file__).resolve().parent / f"{ORACLE_MODULE}.py"


def _scan_null_bytes(paths: Iterable[Path]) -> List[Tuple[Path, int]]:
    """
    Return [(path, null_count), ...] for files containing at least one null byte.
    """
    bad: List[Tuple[Path, int]] = []
    for p in paths:
        try:
            if not p.exists() or not p.is_file():
                continue
            data = p.read_bytes()
            count = data.count(b"\x00")
            if count:
                bad.append((p, count))
        except Exception as e:
            logger.warning("Could not inspect %s for null bytes: %s", p, e)
    return bad


def _preflight_or_die() -> None:
    """
    Fail fast with clear logs if source corruption exists.
    """
    candidates = [ORACLE_FILE, Path(__file__).resolve()]
    bad = _scan_null_bytes(candidates)
    if bad:
        for path, count in bad:
            logger.critical("Source corruption detected: %s contains %d null byte(s)", path, count)
        logger.critical("Refusing to start. Replace corrupted file(s) and restart.")
        raise SystemExit(1)


class Scheduler:
    def __init__(self) -> None:
        _preflight_or_die()

        # Import lazily after preflight so corruption errors are actionable.
        try:
            oracle_mod = importlib.import_module(ORACLE_MODULE)
            OracleRunner = getattr(oracle_mod, "OracleRunner")
        except Exception as e:
            logger.exception("Failed to import OracleRunner from %s: %s", ORACLE_MODULE, e)
            raise SystemExit(1)

        self.runner = OracleRunner()

    def run_forever(self) -> None:
        logger.info("Scheduler starting | interval=%ss | bus=%s", SCAN_INTERVAL_SECONDS, BUS_PATH)
        while True:
            started = datetime.now(timezone.utc)
            try:
                payload = self.run_once()
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                logger.info(
                    "Cycle complete | elapsed=%.2fs | signals=%s | killed=%s",
                    elapsed,
                    len(payload.get("signals", [])),
                    len(payload.get("killedsignals", [])),
                )
            except Exception as e:
                logger.exception("Scheduler cycle failed: %s", e)
            time.sleep(SCAN_INTERVAL_SECONDS)

    def run_once(self) -> Dict[str, Any]:
        payload = self.runner.run_once()
        self._write_last_good(payload)
        self._push_payload(payload)
        return payload

    def _write_last_good(self, payload: Dict[str, Any]) -> None:
        try:
            LAST_GOOD_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LAST_GOOD_PATH.open("w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            logger.info("Last-good snapshot updated: %s", LAST_GOOD_PATH)
        except Exception as e:
            logger.exception("Failed to write last-good snapshot: %s", e)

    def _push_payload(self, payload: Dict[str, Any]) -> None:
        if not API_PUSH_URL:
            logger.info("API push skipped: API_PUSH_URL not configured")
            return

        headers = {"Content-Type": "application/json"}
        if API_PUSH_TOKEN:
            headers["Authorization"] = f"Bearer {API_PUSH_TOKEN}"

        try:
            response = requests.post(API_PUSH_URL, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            logger.info("API push OK: %s", response.status_code)
        except Exception as e:
            logger.exception("API push failed: %s", e)


def main() -> None:
    Scheduler().run_forever()


if __name__ == "__main__":
    main()
