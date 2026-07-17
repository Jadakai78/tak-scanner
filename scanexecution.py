from __future__ import annotations

from typing import Any, Dict, Iterable, List

from scannermodels import CandidateSignal, ScanResult
from scannerpublisher import publish_all
from signalbusbus_writer import SignalBusWriter
from signalbusworker_push import SignalBusWorkerPush


class ScanExecution:
    def __init__(
        self,
        writer: SignalBusWriter,
        worker_push: SignalBusWorkerPush | None = None,
    ) -> None:
        self.writer = writer
        self.worker_push = worker_push

    def finalize(
        self,
        live_candidates: Iterable[CandidateSignal],
        caution_candidates: Iterable[CandidateSignal],
        killed_candidates: Iterable[CandidateSignal],
        positions: List[Dict[str, Any]] | None = None,
        audit: Dict[str, Any] | None = None,
        push: bool = True,
    ) -> Dict[str, Any]:
        result: ScanResult = publish_all(
            live_candidates=live_candidates,
            caution_candidates=caution_candidates,
            killed_candidates=killed_candidates,
            positions=positions,
            audit=audit,
        )

        payload = self.writer.write_result(result)

        push_result = None
        if push and self.worker_push is not None:
            push_result = self.worker_push.safe_push_payload(payload)

        return {
            "result": result,
            "payload": payload,
            "push_result": push_result,
        }
