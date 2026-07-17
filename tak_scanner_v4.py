from __future__ import annotations

from typing import Iterable, List

from scannerorchestrator import ScannerOrchestrator
from scannerspecialist_registry import SpecialistRegistry
from scannerreviewer_remi import RemiReviewer
from scannercouncil import ScannerCouncil
from scannerpublisher import ScannerPublisher
from signalbusbus_writer import SignalBusWriter
from scannermodels import SpecialistObservation

from s6_reversal import S6Reversal


class S6Adapter:
    """
    Adapter that converts legacy S6Reversal.generate(...) output
    into a SpecialistObservation for v4.
    """

    def __init__(self) -> None:
        self.engine = S6Reversal()

    def generate(self, context) -> List[SpecialistObservation]:
        pair = context.pair

        if "ohlc_df" not in context.metadata:
            return []

        ohlc_df = context.metadata.get("ohlc_df")
        regime = str(context.metadata.get("regime", "RANGE"))
        fg_score = int(context.metadata.get("fg_score", 50))
        aist = context.metadata.get("aist")

        raw = self.engine.generate(
            pair=pair,
            ohlc_df=ohlc_df,
            regime=regime,
            fg_score=fg_score,
            aist=aist,
        )

        if not raw:
            return []

        bias = str(raw.get("bias", "NEUTRAL")).upper()
        side = "LONG" if bias == "LONG" else "SHORT"

        rr = float(raw.get("rr", 0.0))
        confidence = min(max(rr / 3.0, 0.0), 1.0)
        score = max(0.0, min(100.0, rr * 30.0 + confidence * 10.0))

        pattern = raw.get("extra", {}).get("pattern", "reversal")

        obs = SpecialistObservation(
            specialist="S6",
            pair=pair,
            setup_type=f"s6_{pattern}",
            side=side,
            confidence=confidence,
            score=score,
            thesis=f"S6 reversal setup detected for {pair} ({pattern}).",
            evidence={
                "entry_idea": raw.get("entry"),
                "stop_idea": raw.get("sl"),
                "target_idea": raw.get("tp"),
                "rr": raw.get("rr"),
                "fg_score": raw.get("fg_score"),
                "kill_condition": raw.get("kill_condition"),
                "raw_signal": raw,
            },
            warnings=[],
            tags=["s6", "reversal", pattern, str(regime).lower()],
            context={
                "regime": regime,
                "fg_score": fg_score,
            },
        )
        return [obs]


def register_core_specialists(registry: SpecialistRegistry) -> None:
    s6 = S6Adapter()
    registry.register("S6", s6.generate)


def run_v4_scan(pairs: Iterable[str]) -> None:
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
    run_v4_scan([pair])


if __name__ == "__main__":
    import sys

    cli_pairs: List[str] = sys.argv[1:] or ["ADAUSD"]
    run_v4_scan(cli_pairs)
