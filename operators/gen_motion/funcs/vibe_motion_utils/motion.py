"""Public position-first motion generation and diagnostics."""
from __future__ import annotations

import numpy as np
from .motion_utils.checks import metrics
from .motion_utils.intent import generate_motion
from .motion_utils.program import build_plan
from .motion_utils.units import fk


def generate_clip(plan):
    return generate_motion(plan)


def clip_metrics(result, plan):
    return metrics(result, quality=plan.program['quality'])


def joint_positions(clip):
    return fk(clip)[0]


def residual_summary(result):
    return {key: float(np.max(value)) for key, value in result.residuals.items()}


__all__ = ['build_plan', 'generate_clip', 'clip_metrics', 'joint_positions', 'residual_summary']
