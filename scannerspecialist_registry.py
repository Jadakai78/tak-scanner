from __future__ import annotations

from typing import Callable, Dict, Iterable, List

from scannermodels import PairContext, SpecialistObservation

SpecialistCallable = Callable[[PairContext], Iterable[SpecialistObservation]]


class SpecialistRegistry:
    def __init__(self) -> None:
        self._registry: Dict[str, SpecialistCallable] = {}

    def register(self, name: str, specialist: SpecialistCallable) -> None:
        key = str(name).strip()
        if not key:
            raise ValueError("Specialist name cannot be empty.")
        self._registry[key] = specialist

    def names(self) -> List[str]:
        return sorted(self._registry.keys())

    def run_for_pair(self, context: PairContext) -> List[SpecialistObservation]:
        observations: List[SpecialistObservation] = []

        for name, specialist in self._registry.items():
            produced = specialist(context)
            if produced is None:
                continue

            for obs in produced:
                if not isinstance(obs, SpecialistObservation):
                    raise TypeError(f"Specialist {name} returned non-SpecialistObservation payload.")
                observations.append(obs)

        return observations

    def __len__(self) -> int:
        return len(self._registry)
