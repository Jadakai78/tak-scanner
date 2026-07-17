from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import requests


logger = logging.getLogger("signalbusworkerpush")

DEFAULT_WORKER_URL = "https://jhl-signal-bus.blazing-0478.workers.dev/update"
DEFAULT_SECRET = "jhl2026dragon"
DEFAULT_BUS_PATH = Path("app/signalbus.json")


class SignalBusWorkerPush:
    def __init__(
        self,
        worker_url: str = DEFAULT_WORKER_URL,
        secret: str = DEFAULT_SECRET,
        bus_path: Path | str = DEFAULT_BUS_PATH,
        timeout: int = 20,
    ) -> None:
        self.worker_url = worker_url
        self.secret = secret
        self.bus_path = Path(bus_path)
        self.timeout = timeout

    def push_text(self, payload_text: str) -> requests.Response:
        response = requests.post(
            self.worker_url,
            data=payload_text,
            headers={
                "Content-Type": "application/json",
                "X-JHL-Secret": self.secret,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

    def push_file(self, bus_path: Optional[Path | str] = None) -> requests.Response:
        target = Path(bus_path) if bus_path is not None else self.bus_path
        payload_text = target.read_text(encoding="utf-8")
        return self.push_text(payload_text)

    def safe_push_file(self, bus_path: Optional[Path | str] = None) -> bool:
        try:
            response = self.push_file(bus_path=bus_path)
            logger.info("Worker push OK status=%s", response.status_code)
            return True
        except Exception as exc:
            logger.warning("Worker push failed: %s", exc)
            return False
