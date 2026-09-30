from __future__ import annotations
from typing import Any
import numpy as np
from . import rigging_utils as branch


def skin_mesh(mesh: Any, rig: Any, *, config: dict, allowed_bones=None, weight_bias=None, anchors=None) -> Any:
    """Generate constrained, anchored topology-diffused weights."""
    return branch.skin_mesh(mesh, rig, config=config, allowed_bones=allowed_bones,
                            weight_bias=weight_bias, anchors=anchors)


def validate_skin(skin: Any, mesh: Any, rig: Any, *, config: dict) -> Any:
    """Check constraints and deformation against caller-supplied thresholds."""
    return branch.validate_skin(skin, mesh, rig, config=config)


def format_report(report: Any, skin: Any, label: str) -> str:
    return branch.format_skin_report(report, skin, label)


def report_passed(report: Any) -> bool:
    return all(bool(f.ok) for f in report.findings)


def weight_stats(skin: Any) -> dict[str, int | float]:
    weights = np.asarray(skin.weights, dtype=np.float64)
    if weights.ndim != 2:
        raise ValueError(f'weights must be (V,J), got {weights.shape}')
    influences = np.count_nonzero(weights > 0.0, axis=1)
    return {
        'vertices': int(weights.shape[0]),
        'joints': int(weights.shape[1]),
        'mean_influences': float(influences.mean()) if len(influences) else 0.0,
        'max_influences': int(influences.max()) if len(influences) else 0,
        'max_row_sum_error': float(np.abs(weights.sum(axis=1) - 1.0).max()) if len(weights) else 0.0,
    }


__all__ = ['format_report', 'report_passed', 'skin_mesh', 'validate_skin', 'weight_stats']
