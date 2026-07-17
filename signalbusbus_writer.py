from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from signalbusschema import build_signal_bus_payload
from scannermodels import ScanResult


class SignalBusWriter:
    def __init__(self, path: str = "app/signalbus.json") -> None:
        self.path = Path(path)

    def write_result(self, result: ScanResult) -> Dict[str, Any]:
        payload = build_signal_bus_payload(result)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload

    def write_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return payload
