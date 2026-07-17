from __future__ import annotations

from typing import Any, Dict, Optional

import requests


class SignalBusWorkerPush:
    def __init__(
        self,
        worker_url: str,
        secret: Optional[str] = None,
        timeout: int = 20,
    ) -> None:
        self.worker_url = worker_url
        self.secret = secret
        self.timeout = timeout

    def push_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.secret:
            headers["X-JHL-Secret"] = self.secret

        response = requests.post(
            self.worker_url,
            json=payload,
            headers=headers,
            timeout=self.timeout,
        )
        response.raise_for_status()

        try:
            body = response.json()
        except Exception:
            body = {"text": response.text}

        return {
            "status_code": response.status_code,
            "body": body,
        }
