from __future__ import annotations

import logging
from typing import Any, Dict

from tak_scanner_v4 import TakScannerV4

logger = logging.getLogger(__name__)


class OracleRunner:
    """Thin Oracle entrypoint for Phase 1.

    Keeps scheduler/feed transport simple while delegating the actual
    scan and payload construction to TakScannerV4.
    """

    def __init__(self) -> None:
        self.scanner = TakScannerV4()

    def run_once(self) -> Dict[str, Any]:
        payload = self.scanner.run_scan()
        logger.info(
            "OracleRunner completed scan | last_scan=%s | opportunities=%s | watchlist=%s | killed=%s",
            payload.get("last_scan"),
            len(payload.get("opportunities", [])),
            len(payload.get("watchlist", [])),
            len(payload.get("killed", [])),
        )
        return payload


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    OracleRunner().run_once()
