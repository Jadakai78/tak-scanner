from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from scannermodels import PublishedSignal, ScanResult


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def serialize_published_signal(signal: PublishedSignal) -> Dict[str, Any]:
    return asdict(signal)


def build_signal_bus_payload(result: ScanResult) -> Dict[str, Any]:
    return {
        "generated_at": utc_now_iso(),
        "live_signals": [serialize_published_signal(x) for x in result.live_signals],
        "caution_signals": [serialize_published_signal(x) for x in result.caution_signals],
        "killed_signals": [serialize_published_signal(x) for x in result.killed_signals],
        "positions": list(result.positions),
        "audit": dict(result.audit),
    }


def empty_signal_bus() -> Dict[str, Any]:
    return {
        "generated_at": utc_now_iso(),
        "live_signals": [],
        "caution_signals": [],
        "killed_signals": [],
        "positions": [],
        "audit": {},
    }
