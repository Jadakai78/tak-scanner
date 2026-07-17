from __future__ import annotations

import json
from typing import Any, Dict

from signalbusschema import build_signal_bus_payload
from scannermodels import ScanResult


class SignalBusWriter:
    def __init__(self, path: str = "signal_bus.json") -> None:
        self.path = path

    def write(self, result: ScanResult) -> Dict[str, Any]:
        payload = build_signal_bus_payload(result)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return payload
