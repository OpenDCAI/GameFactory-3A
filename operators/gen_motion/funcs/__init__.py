"""Motion-task step functions. Imports are lazy so bpy subprocesses stay light."""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "fetch_motion": "fetch_motion",
    "list_motion_sources": "fetch_motion",
    "suggest_global_scale": "fetch_motion",
    "generate_motion": "generate_motion",
    "retarget_motion": "retarget_motion",
    "rig_character": "rig_character",
    "generate_vibe_motion": "vibe_motion_utils.pipeline",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f".{module}", __name__), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
