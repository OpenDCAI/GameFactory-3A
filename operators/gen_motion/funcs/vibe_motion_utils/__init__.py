'Local procedural motion, mesh rigging, skinning and artifact exports.'
from importlib import import_module
from typing import Any

_EXPORTS = {
    "clip_to_bvh": "bvh",
    "clip_to_bvh_bytes": "bvh",
    "creature_mesh": "skeleton",
    "fit_skeleton": "skeleton",
    "mesh_from_arrays": "skeleton",
    "rig_to_text": "skeleton",
    "to_motion_template": "skeleton",
    "build_plan": "motion",
    "clip_metrics": "motion",
    "generate_clip": "motion",
    "joint_positions": "motion",
    "skin_mesh": "skinning",
    "validate_skin": "skinning",
    "generate_vibe_motion": "pipeline",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(f".{module}", __name__), name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_EXPORTS))
