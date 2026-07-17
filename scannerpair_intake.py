from __future__ import annotations

from typing import Iterable, List

from scannermodels import PairContext


class PairIntake:
    def __init__(self, default_timeframe: str = "1h") -> None:
        self.default_timeframe = default_timeframe

    def build_contexts(self, pairs: Iterable[str]) -> List[PairContext]:
        contexts: List[PairContext] = []

        for pair in pairs:
            contexts.append(
                PairContext(
                    pair=str(pair),
                    timeframe=self.default_timeframe,
                    metadata={"source": "v4_scanner"},
                )
            )

        return contexts
