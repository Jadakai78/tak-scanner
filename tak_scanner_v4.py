from __future__ import annotations

from typing import Iterable, List

from scannerorchestrator import ScannerOrchestrator
from scannerspecialist_registry import SpecialistRegistry
from scannerreviewer_remi import RemiReviewer
from scannercouncil import ScannerCouncil
from scannerpublisher import ScannerPublisher
from signalbusbus_writer import SignalBusWriter


def register_core_specialists(registry: SpecialistRegistry) -> None:
    """
    Hook point for engine specialists.
    Example:

        from engines import ENGINE_CLASSES
        registry.register("S6", ENGINE_CLASSES["S6"]().generate)

    For now this stays empty until you plug in your engines.
    """
    # TODO: plug in S6 and others here when ready.
    # registry.register("S6", ENGINE_CLASSES["S6"]().generate)
    ...


def run_v4_scan(pairs: Iterable[str]) -> None:
    """
    Run the v4 scanner pipeline and write signal_bus.json.
    """
    registry = SpecialistRegistry()
    register_core_specialists(registry)

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

    scan_result = orchestrator.run_scan(pairs)
    writer.write(scan_result)


def run_single_pair(pair: str) -> None:
    """
    Convenience entry for testing one pair in PowerShell:

        python tak_scanner_v4.py ADA/USD
    """
    run_v4_scan([pair])


if __name__ == "__main__":
    import sys

    cli_pairs: List[str] = sys.argv[1:] or ["ADA/USD"]
    run_v4_scan(cli_pairs)
