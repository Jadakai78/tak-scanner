from __future__ import annotations

from typing import Callable, Dict, List

from scannermodels import PairContext, SpecialistObservation


SpecialistFn = Callable[[PairContext], List[SpecialistObservation]]


class SpecialistRegistry:
    def __init__(self) -> None:
        self._registry: Dict[str, SpecialistFn] = {}

    def register(self, name: str, fn: SpecialistFn) -> None:
        self._registry[name] = fn

    def list_specialists(self) -> List[str]:
        return sorted(self._registry.keys())

    def scan_pair(self, context: PairContext) -> List[SpecialistObservation]:
        observations: List[SpecialistObservation] = []

        for name, fn in self._registry.items():
            try:
                batch = fn(context)
                for obs in batch:
                    obs.specialist = name
                    observations.append(obs)
            except Exception as exc:
                observations.append(
                    SpecialistObservation(
                        specialist=name,
                        pair=context.pair,
                        setup_type="error",
                        side="NEUTRAL",
                        confidence=0.0,
                        score=0.0,
                        thesis=f"Specialist {name} failed: {exc}",
                        evidence={"exception": str(exc)},
                        warnings=["specialist_error"],
                        tags=["blocked"],
                        context={"timeframe": context.timeframe},
                    )
                )

        return observations
