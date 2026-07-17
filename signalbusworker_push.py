from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger("signalbusworkerpush")


class SignalBusWorkerPush:
    def __init__(
        self,
        worker_url: str = "https://jhl-signal-bus.blazing-0478.workers.dev/update",
        secret: str = "jhl2026dragon",
        timeout: int = 20,
    ) -> None:
        self.worker_url = worker_url
        self.secret = secret
        self.timeout = timeout

    def push_file(self, bus_path: Path) -> bool:
        try:
            payload = bus_path.read_text(encoding="utf-8")
            resp = requests.post(
                self.worker_url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-JHL-Secret": self.secret,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            logger.info("V4 WORKER PUSH OK status=%s", resp.status_code)
            return True
        except Exception as exc:
            logger.warning("V4 WORKER PUSH FAIL err=%s", exc)
            return False

    def push_payload_text(self, payload_text: str) -> bool:
        try:
            resp = requests.post(
                self.worker_url,
                data=payload_text,
                headers={
                    "Content-Type": "application/json",
                    "X-JHL-Secret": self.secret,
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            logger.info("V4 WORKER PUSH OK status=%s", resp.status_code)
            return True
        except Exception as exc:
            logger.warning("V4 WORKER PUSH FAIL err=%s", exc)
            return False
