from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from scannerorchestrator import ScannerOrchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)

logger = logging.getLogger("scannerbootstrap")
MODULE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = MODULE_DIR / "config.json"


def load_config() -> Dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("BOOT config load failed err=%s using defaults", exc)
        return {}


def resolve_max_pairs(cfg: Dict[str, Any]) -> Optional[int]:
    value = cfg.get("max_pairs")
    if value in (None, "", 0, "0"):
        return None
    try:
        return int(value)
    except Exception:
        return None


def main() -> Dict[str, Any]:
    cfg = load_config()
    max_pairs = resolve_max_pairs(cfg)

    logger.info("BOOT start max_pairs=%s", max_pairs)

    orchestrator = ScannerOrchestrator(max_pairs=max_pairs)
    payload = orchestrator.run_scan()

    audit = payload.get("audit", {})
    logger.info(
        "BOOT complete active=%s prepared=%s observations=%s candidates=%s live=%s caution=%s killed=%s push_ok=%s",
        audit.get("active_pairs"),
        audit.get("prepared_pairs"),
        audit.get("observations_total"),
        audit.get("candidates_total"),
        audit.get("live_total"),
        audit.get("caution_total"),
        audit.get("killed_total"),
        audit.get("worker_push_ok"),
    )
    return payload


if __name__ == "__main__":
    result = main()
    audit = result.get("audit", {})
    print(
        "scan complete | "
        f"active={audit.get('active_pairs', 0)} | "
        f"prepared={audit.get('prepared_pairs', 0)} | "
        f"obs={audit.get('observations_total', 0)} | "
        f"cand={audit.get('candidates_total', 0)} | "
        f"live={audit.get('live_total', 0)} | "
        f"caution={audit.get('caution_total', 0)} | "
        f"killed={audit.get('killed_total', 0)} | "
        f"push_ok={audit.get('worker_push_ok', False)}"
    )
