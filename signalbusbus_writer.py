from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict
from signalbusbus_writer import SignalBusBusWriter

logger = logging.getLogger("signalbusbuswriter")


class SignalBusBusWriter:
    def __init__(self, output_path: str = "/app/signalbus.json") -> None:
        self.output_path = Path(output_path)

    def write(self, payload: Dict[str, Any]) -> Path:
        self.output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(
            "V4 BUS WRITE path=%s live=%s caution=%s killed=%s",
            self.output_path,
            len(payload.get("live_signals", [])),
            len(payload.get("caution_signals", [])),
            len(payload.get("killed_signals", [])),
        )
        return self.output_path
