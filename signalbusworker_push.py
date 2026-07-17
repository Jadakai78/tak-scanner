from __future__ import annotations

from typing import Iterable

from scannerorchestrator import ScannerOrchestrator
from scannerspecialist_registry import SpecialistRegistry
from scannerreviewer_remi import RemiReviewer
from scannercouncil import ScannerCouncil
from scannerpublisher import ScannerPublisher
from signalbusbus_writer import SignalBusWriter


def run_signalbus_worker(pairs: Iterable[str]) -> None:
    registry = SpecialistRegistry()
    reviewer = RemiReviewer()
    council = ScannerCouncil()
    publisher = ScannerPublisher()
    orchestrator = ScannerOrchestrator(
        registry=registry,
        reviewer=reviewer,
        council=council,
        publisher=publisher,
    )
    writer = SignalBusWriter()

    # NOTE: real specialist registration goes here.
    # registry.register("S6", some_function)

    scan_result = orchestrator.run_scan(pairs)
    writer.write(scan_result)
