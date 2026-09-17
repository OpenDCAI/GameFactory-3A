'Generate and validate per-vertex skin weights in mesh vertex order.'
from __future__ import annotations

from typing import Any

import numpy as np

from . import rigging_utils as branch


SKIN_PRESETS = ("rigid", "smooth", "compact", "axial")


MAX_INFLUENCES = 4


def list_presets() -> list[str]:
    'Return the available skinning preset names.'
    return sorted(branch.SKIN_PRESETS)


def preset_for_morphology(morphology: str) -> str:
    'Return the recommended weight preset for a morphology name.'
    table = branch.SKIN_PRESET_FOR_MORPHOLOGY
    if morphology not in table:
        raise ValueError(
            f"No skin preset for morphology {morphology!r}. Known: "
            + ", ".join(sorted(table))
        )
    return table[morphology]


def skin_mesh(
    mesh: Any,
    rig: Any,
    *,
    preset: str = "compact",
    **overrides: Any,
) -> Any:
    """Compute per-vertex weights and return ``SkinWeights``.

    Weights index the mesh that was rigged, so vertices must not be reordered
    between rigging and skinning.
    """
    if preset not in SKIN_PRESETS:
        raise ValueError(
            f"Unknown skin preset {preset!r}. Available: "
            + ", ".join(SKIN_PRESETS)
        )
    return branch.skin_mesh(mesh, rig, preset, **overrides)


def validate_skin(
    skin: Any,
    mesh: Any,
    rig: Any,
) -> Any:
    """Return the ``SkinReport`` for constraints, smoothness and deformation.

    These are self-consistency checks: they confirm non-negative weights, rows
    summing to one and influence counts within :data:`MAX_INFLUENCES`, not that
    the weights match an artist's intent.
    """
    return branch.validate_skin(skin, mesh, rig)


def format_report(report: Any, skin: Any, label: str) -> str:
    'Render a ``SkinReport`` as human-readable summary text.'
    return branch.format_skin_report(report, skin, label)


def report_passed(report: Any) -> bool:
    'Return whether every finding in a ``SkinReport`` passed.'
    return all(bool(getattr(f, "ok", False)) for f in report.findings)


def weight_stats(skin: Any) -> dict[str, int | float]:
    'Return vertex count, joint count and influence statistics for weights.'
    weights = np.asarray(skin.weights, dtype=np.float64)
    if weights.ndim != 2:
        raise ValueError(f"weights must be (V,J), got {weights.shape}")
    influences = np.count_nonzero(weights > 0.0, axis=1)
    return {
        "vertices": int(weights.shape[0]),
        "joints": int(weights.shape[1]),
        "mean_influences": float(influences.mean()) if len(influences) else 0.0,
        "max_influences": int(influences.max()) if len(influences) else 0,
        "max_row_sum_error": float(
            np.abs(weights.sum(axis=1) - 1.0).max() if len(weights) else 0.0
        ),
    }


__all__ = [
    "MAX_INFLUENCES",
    "SKIN_PRESETS",
    "format_report",
    "list_presets",
    "preset_for_morphology",
    "report_passed",
    "skin_mesh",
    "validate_skin",
    "weight_stats",
]
