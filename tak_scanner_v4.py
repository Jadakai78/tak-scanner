from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from scannermodels import PairContext, ScanResult
from scannerorchestrator import ScannerOrchestrator
from scannerpublisher import ScannerPublisher
from signalbus.bus_writer import SignalBusWriter

logger = logging.getLogger("tak_scanner_v4")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_scan(
    pair_contexts: List[PairContext],
    specialist_registry: Any,
    worker_push_ok: bool | None = None,
    data_freshness_sec: float | None = None,
    global_regime: Dict[str, Any] | None = None,
    regime_map: Dict[str, Any] | None = None,
    market_phase: str | None = None,
    timeframe: str = "1h",
) -> ScanResult:
    """
    Main Tak v4 scan entrypoint.

    - Takes prepared PairContext objects.
    - Runs orchestrator + publisher.
    - Fills canonical audit keys.
    - Writes signal bus payload to disk via SignalBusWriter.
    """
    scan_started_at = utc_now_iso()

    orchestrator = ScannerOrchestrator(specialist_registry=specialist_registry)
    publisher = ScannerPublisher()
    bus_writer = SignalBusWriter()

    shared_state: Dict[str, Any] = {
        "fgscore": None,           # fill if you have Fear & Greed
        "market_phase": market_phase,
        "timeframe": timeframe,
    }

    # Orchestrate per-pair specialists → CandidateSignal list
    candidates = orchestrator.run(pair_contexts, shared_state=shared_state)

    # Publish into ScanResult (signals + positions + audit shell)
    audit: Dict[str, Any] = {}
    positions: List[Dict[str, object]] = []

    result = publisher.publish(
        candidates=candidates,
        positions=positions,
        audit=audit,
    )

    # Fill canonical audit keys for schema
    _fill_audit(
        result=result,
        scan_started_at=scan_started_at,
        scan_completed_at=utc_now_iso(),
        timeframe=timeframe,
        pair_contexts=pair_contexts,
        worker_push_ok=worker_push_ok,
        data_freshness_sec=data_freshness_sec,
        global_regime=global_regime or {},
        regime_map=regime_map or {},
        market_phase=market_phase,
    )

    # Write bus payload (signalbus.json) using canonical schema
    payload = bus_writer.write(result)
    logger.info(
        "Tak v4 scan complete pairs=%s live=%s caution=%s killed=%s",
        len(pair_contexts),
        len(result.live_signals),
        len(result.caution_signals),
        len(result.killed_signals),
    )
    return result


def _fill_audit(
    result: ScanResult,
    scan_started_at: str,
    scan_completed_at: str,
    timeframe: str,
    pair_contexts: List[PairContext],
    worker_push_ok: bool | None,
    data_freshness_sec: float | None,
    global_regime: Dict[str, Any],
    regime_map: Dict[str, Any],
    market_phase: str | None,
) -> None:
    """
    Fill ScanResult.audit with the canonical keys the schema expects.
    """
    audit = result.audit

    active_pairs = [ctx.pair for ctx in pair_contexts]

    # Session
    audit["scan_started_at"] = scan_started_at
    audit["scan_completed_at"] = scan_completed_at
    audit["timeframe"] = timeframe
    audit["active_pairs"] = active_pairs
    audit["pair_count"] = len(active_pairs)
    audit["market_phase"] = market_phase

    # Health
    audit["scanner_ok"] = True
    audit["worker_push_ok"] = worker_push_ok
    audit["data_freshness_sec"] = data_freshness_sec
    audit["degraded"] = False
    audit.setdefault("health_warnings", [])

    # Regimes
    audit["global_regime"] = dict(global_regime or {})
    audit["regime_map"] = dict(regime_map or {})

    # Diagnostics placeholders
    audit.setdefault("route_stats", {})
    audit.setdefault("field_general", {})

    # Counts are already set by publisher; keep them if present
    audit.setdefault("counts", audit.get("counts", {}))
