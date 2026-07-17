from __future__ import annotations

from typing import Any, Dict, Optional

import requests


class SignalBusWorkerPush:
    def __init__(
        self,
        worker_url: str,
        secret: str,
        timeout: int = 20,
    ) -> None:
        self.worker_url = worker_url
        self.secret = secret
        self.timeout = timeout

    def push_payload(self, payload: Dict[str, Any]) -> requests.Response:
        response = requests.post(
            self.worker_url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-JHL-Secret": self.secret,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

    def push_scan_result(self, result: Any) -> requests.Response:
        from signalbusschema import build_signal_bus_payload

        payload = build_signal_bus_payload(result)
        return self.push_payload(payload)

    def try_push_payload(self, payload: Dict[str, Any]) -> Optional[int]:
        try:
            response = self.push_payload(payload)
            return response.status_code
        except Exception:
            return None
