from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from signalbusschema import build_signal_bus_payload
from scannermodels import ScanResult


class SignalBusWriter:
    def __init__(self, output_path: str = "app/signalbus.json") -> None:
        self.output_path = Path(output_path)

    def write_payload_dict(self, payload: Dict[str, Any]) -> Path:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.output_path

    def write_scan_result(self, result: ScanResult) -> Path:
        payload = build_signal_bus_payload(result)
        return self.write_payload_dict(payload)
