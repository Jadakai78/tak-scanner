from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Protocol

from scannermodels import SpecialistObservation

logger = logging.getLogger(__name__)


class SpecialistProtocol(Protocol):
    name: str

    def observe(self, pair_payload: Dict[str, Any]) -> List[SpecialistObservation]:
        ...


class NullSpecialist:
    name = "null_specialist"

    def observe(self, pair_payload: Dict[str, Any]) -> List[SpecialistObservation]:
        return []


class ScannerSpecialistRegistry:
    def __init__(self, specialists: Iterable[SpecialistProtocol] | None = None) -> None:
        self._specialists: Dict[str, SpecialistProtocol] = {}
        for specialist in specialists or []:
            self.register(specialist)

    def register(self, specialist: SpecialistProtocol) -> None:
        self._specialists[specialist.name] = specialist

    def unregister(self, name: str) -> None:
        self._specialists.pop(name, None)

    def names(self) -> List[str]:
        return list(self._specialists.keys())

    def specialists(self) -> List[SpecialistProtocol]:
        return list(self._specialists.values())

    def observe_pair(self, pair_payload: Dict[str, Any]) -> List[SpecialistObservation]:
        observations: List[SpecialistObservation] = []

        for specialist in self.specialists():
            try:
                result = specialist.observe(pair_payload)
                if result:
                    observations.extend(result)
            except Exception as exc:
                logger.warning(
                    "V4 specialist failed specialist=%s pair=%s error=%s",
                    specialist.name,
                    pair_payload.get("pair"),
                    exc,
                )

        return observations

    def observe_many(self, pair_payloads: Iterable[Dict[str, Any]]) -> Dict[str, List[SpecialistObservation]]:
        output: Dict[str, List[SpecialistObservation]] = {}

        for payload in pair_payloads:
            pair = str(payload.get("pair", "UNKNOWN"))
            output[pair] = self.observe_pair(payload)

        return output
