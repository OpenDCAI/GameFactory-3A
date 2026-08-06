"""Engine backend registration and alias resolution."""

from __future__ import annotations

from .contracts import EngineBackend, UnknownEngineError


_ALIASES = {
    "ue": "ue5",
    "unreal": "ue5",
    "unreal_engine": "ue5",
}


class EngineRegistry:
    def __init__(self, backends: list[EngineBackend] | None = None) -> None:
        self._backends: dict[str, EngineBackend] = {}
        for backend in backends or []:
            self.register(backend)

    def register(self, backend: EngineBackend) -> None:
        key = self.normalize(backend.descriptor.engine_id)
        if not key:
            raise ValueError("Engine backend descriptor requires engine_id")
        if key in self._backends:
            raise ValueError(f"Engine backend is already registered: {key}")
        self._backends[key] = backend

    def resolve(self, engine_id: str) -> EngineBackend:
        key = self.normalize(engine_id)
        try:
            return self._backends[key]
        except KeyError as exc:
            raise UnknownEngineError(engine_id) from exc

    def list(self) -> list[EngineBackend]:
        return [self._backends[key] for key in sorted(self._backends)]

    @staticmethod
    def normalize(engine_id: str) -> str:
        value = (
            str(engine_id or "ue5")
            .strip()
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
        )
        return _ALIASES.get(value, value)
