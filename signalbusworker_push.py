from __future__ import annotations

import json
from pathlib import Path
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
            data=json.dumps(payload),
            headers={
                "Content-Type": "application/json",
                "X-JHL-Secret": self.secret,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response

    def push_file(self, path: str | Path) -> requests.Response:
        file_path = Path(path)
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        return self.push_payload(payload)

    def safe_push_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = self.push_payload(payload)
            return {
                "ok": True,
                "status_code": response.status_code,
                "text": response.text[:500],
            }
        except Exception as exc:
            return {
                "ok": False,
                "status_code": None,
                "error": str(exc),
            }

    def safe_push_file(self, path: str | Path) -> Dict[str, Any]:
        try:
            response = self.push_file(path)
            return {
                "ok": True,
                "status_code": response.status_code,
                "text": response.text[:500],
            }
        except Exception as exc:
            return {
                "ok": False,
                "status_code": None,
                "error": str(exc),
            }
