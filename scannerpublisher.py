from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import requests

from scannermodels import ScanResult
from signalbusschema import build_signal_bus_payload


logger = logging.getLogger("scannerpublisher")


class ScannerPublisher:
    """
    Simple V4 publisher:
    - build one signal bus payload from ScanResult
    - write to app/signalbus.json
    - push to worker
    - return the original ScanResult unchanged
    """

    def __init__(
        self,
        app_dir: Path,
        worker_url: str,
        worker_secret: str,
    ) -> None:
        self.app_dir = app_dir
        self.worker_url = worker_url
        self.worker_secret = worker_secret

        self.bus_path = self.app_dir / "signalbus.json"

    def publish(self, result: ScanResult) -> ScanResult:
        payload_dict = build_signal_bus_payload(result)

        try:
            text = json.dumps(payload_dict, ensure_ascii=False, indent=2)
            self.bus_path.write_text(text, encoding="utf-8")
            logger.info(
                "BUS WRITE OK live=%d caution=%d killed=%d",
                len(result.live_signals),
                len(result.caution_signals),
                len(result.killed_signals),
            )
        except Exception as exc:
            logger.warning("BUS WRITE FAILED err=%s", exc)

        try:
            data = self.bus_path.read_text(encoding="utf-8")
            headers = {
                "Content-Type": "application/json",
                "X-JHL-Secret": self.worker_secret,
            }
            resp = requests.post(self.worker_url, data=data, headers=headers, timeout=20)
            resp.raise_for_status()
            logger.info("Worker push OK status=%s", resp.status_code)
        except Exception as exc:
            logger.warning("Worker push failed err=%s", exc)

        return result
