from __future__ import annotations

from typing import Any, Dict, Iterable, List


class SpecialistRegistry:
    def __init__(self, specialists: Dict[str, Any] | None = None) -> None:
        self._specialists: Dict[str, Any] = dict(specialists or {})

    def register(self, name: str, specialist: Any) -> None:
        self._specialists[name] = specialist

    def names(self) -> List[str]:
        return list(self._specialists.keys())

    def get(self, name: str) -> Any:
        return self._specialists[name]

    def resolve_for_regime(self, regime: str) -> List[Any]:
        selected: List[Any] = []
        regime_upper = str(regime).upper()

        for name, specialist in self._specialists.items():
            supported = getattr(specialist, "supported_regimes", None)
            if not supported:
                selected.append(specialist)
                continue

            normalized = {str(x).upper() for x in supported}
            if regime_upper in normalized or "*" in normalized or "ALL" in normalized:
                selected.append(specialist)

        return selected

    def from_engine_map(self, engine_classes: Dict[str, Any], regime_engines: Dict[str, Iterable[str]]) -> "SpecialistRegistry":
        registry = SpecialistRegistry()

        for regime, engine_names in regime_engines.items():
            for engine_name in engine_names:
                engine_cls = engine_classes.get(engine_name)
                if engine_cls is None:
                    continue
                instance = engine_cls() if callable(engine_cls) else engine_cls
                supported = getattr(instance, "supported_regimes", None)
                if supported is None:
                    try:
                        setattr(instance, "supported_regimes", [str(regime)])
                    except Exception:
                        pass
                registry.register(engine_name, instance)

        return registry
