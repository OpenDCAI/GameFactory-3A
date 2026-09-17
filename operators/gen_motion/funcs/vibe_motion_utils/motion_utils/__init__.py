"""Local skeleton templates, motion recipes, trajectory constraints and analytic IK."""
from .blend import concatenate
from .checks import metrics
from .generate import curve, fit_feet, rotate_joint, smooth, solve_two_bone, strike, swing
from .intent import MotionResult, generate_motion
from .templates import MOTION_PRESETS, resolve_preset
from .units import (
    MotionClip, SkeletonPlan, SkeletonTemplate, build_template, plan_skeleton,
    fk, template_from_skeleton, quat_from_axis_angle, quat_mul, quat_normalize,
    quat_slerp, quat_to_matrix,
)

__all__ = [
    "MotionClip", "MotionResult", "SkeletonPlan", "SkeletonTemplate", "MOTION_PRESETS",
    "build_template", "plan_skeleton", "template_from_skeleton", "generate_motion",
    "resolve_preset", "concatenate", "metrics", "fk", "curve", "smooth", "fit_feet",
    "solve_two_bone", "rotate_joint", "strike", "swing", "quat_from_axis_angle",
    "quat_mul", "quat_normalize", "quat_slerp", "quat_to_matrix",
]
