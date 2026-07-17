from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from signalbusschema import build_signal_bus_payload
from scannermodels import ScanResult


DEFAULT_SIGNALBUS_PATH = Path("app/signalbus.json")


class SignalBusWriter:
    def __init__(self, bus_path: Path | str = DEFAULT_SIGNALBUS_PATH) -> None:
        self.bus_path = Path(bus_path)

    def write_payload(self, payload: Dict[str, Any]) -> Path:
        self.bus_path.parent.mkdir(parents=True, exist_ok=True)
        self.bus_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.bus_path

    def write_scan_result(self, result: ScanResult) -> Path:
        payload = build_signal_bus_payload(result)
        return self.write_payload(payload)

    def read_payload(self) -> Dict[str, Any]:
        if not self.bus_path.exists():
            return {}
        return json.loads(self.bus_path.read_text(encoding="utf-8"))
