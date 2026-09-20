'Generate local preset motion clips, trajectories and numerical metrics.'
from __future__ import annotations

from typing import Any

import numpy as np

from . import motion_utils as branch



TEMPLATES = ("biped_armed", "biped")


ARM_PRESETS = ("boxing", "jab", "chop", "turn_jump_chop", "turn_jump_chop_tuned")


def list_presets() -> list[str]:
    'Return the available motion preset names.'
    return sorted(branch.MOTION_PRESETS)


def preset_defaults(name: str) -> dict:
    'Return the resolved parameter set for one preset, without generating.'
    return branch.resolve_preset(name)


def build_plan(template: str | Any = "biped_armed") -> Any:
    'Return a ``SkeletonPlan`` from a template name or a ``SkeletonTemplate``.'
    if isinstance(template, str):
        if template not in TEMPLATES:
            raise ValueError(
                f"Unknown template {template!r}. Available: "
                + ", ".join(TEMPLATES)
            )
        template = branch.build_template(template)
    return branch.plan_skeleton(template)


def generate_clip(
    preset: str,
    plan: Any,
    *,
    num_frames: int | None = None,
    fps: float = 30.0,
    heading_deg: float = 0.0,
    **overrides: Any,
) -> Any:
    "Generate one clip and return the branch's ``MotionResult``."
    return branch.generate_motion(
        preset,
        plan,
        num_frames=num_frames,
        fps=float(fps),
        heading_deg=float(heading_deg),
        **overrides,
    )


def clip_metrics(result: Any, plan: Any) -> dict:
    'Return bone-length, ground, contact and IK-target metrics for a result.'
    metrics = branch.metrics(
        result.clip,
        plan,
        contacts=result.contacts,
        targets=result.foot_targets,
    )
    if result.hand_targets or result.residuals:
        positions, _ = branch.fk(result.clip)
        hand_error = max((
            float(np.linalg.norm(positions[:, plan.roles[role].joints[-1]] - target, axis=-1).max())
            for role, target in result.hand_targets.items()
        ), default=0.0)
        metrics["hand_target_error_max"] = hand_error
        metrics["ik_residual_max"] = max(residual_summary(result).values(), default=0.0)
        if max(hand_error, metrics["target_error_max"], metrics["ik_residual_max"]) > 0.001:
            metrics["failures"] = list(dict.fromkeys([*metrics["failures"], "ik_target"]))
    return {
        key: (
            float(value)
            if isinstance(value, (int, float, np.floating))
            else value
        )
        for key, value in metrics.items()
    }


def concatenate_clips(
    clips: list[Any],
    *,
    transition_frames: int = 8,
    carry_facing: bool = True,
    align_vertical: bool = True,
) -> Any:
    """Insert transition frames; optionally preserve ground-relative heights."""
    if not clips:
        raise ValueError("concatenate_clips needs at least one clip")
    if type(transition_frames) is not int or transition_frames < 0:
        raise ValueError("transition_frames must be a non-negative integer")
    if type(carry_facing) is not bool:
        raise ValueError("carry_facing must be a boolean")
    if type(align_vertical) is not bool:
        raise ValueError("align_vertical must be a boolean")
    return branch.concatenate(
        clips, transition_frames=transition_frames, carry_facing=carry_facing,
        align_vertical=align_vertical,
    )


def _generate_sequence(specs: list[dict], transitions: list[dict], plan: Any, *, fps: float) -> dict:
    """Generate and join resolved segments without fitting rigs or writing files.

    Authored contacts and targets are transformed with their segments. Transition
    contacts are inferred from height; no IK lock or velocity continuity is
    promised. Target errors and IK residuals cover authored segments only.
    """
    from .motion_utils.units import root_index
    from .motion_utils.validation import check_foot_skate

    results = [
        generate_clip(
            spec["name"], plan, num_frames=spec["frames"], fps=fps,
            heading_deg=spec["heading_deg"], **spec["params"],
        )
        for spec in specs
    ]
    clip = results[0].clip.copy()
    starts = [0]
    joins = []
    for index, (result, transition) in enumerate(zip(results[1:], transitions)):
        start = clip.num_frames
        count = transition["transition_frames"]
        clip = concatenate_clips(
            [clip, result.clip], align_vertical=False, **transition,
        )
        starts.append(start + count)
        joins.append({
            **transition, "from_segment": index, "to_segment": index + 1,
            "start_frame": start, "end_frame": start + count,
        })

    positions, _ = branch.fk(clip)
    root = root_index(plan.template)
    center = plan.template.rest[root]
    contacts = {
        role: positions[:, plan.roles[role].joints[-1], 1]
        <= plan.ground + 0.01 * plan.support_length
        for role in plan.support_roles
    }
    target_error = 0.0
    residuals: dict[str, float] = {}
    records = []
    notes = []
    for index, (spec, result, start) in enumerate(zip(specs, results, starts)):
        end = start + result.clip.num_frames
        rotation = (
            branch.quat_to_matrix(clip.quats[start, root])
            @ branch.quat_to_matrix(result.clip.quats[0, root]).T
        )
        for role, contact in result.contacts.items():
            contacts[role][start:end] = contact
        for role, target in result.foot_targets.items():
            transformed = (
                (target - center - result.clip.trans[0]) @ rotation.T
                + center + clip.trans[start]
            )
            error = np.linalg.norm(
                positions[start:end, plan.roles[role].joints[-1]] - transformed,
                axis=-1,
            )
            target_error = max(target_error, float(error.max()))
        segment_residuals = residual_summary(result)
        for role, value in segment_residuals.items():
            residuals[role] = max(residuals.get(role, 0.0), value)
        facing = branch.quat_to_matrix(clip.quats[start, root]) @ np.array([0., 0., 1.])
        records.append({
            "preset": spec["name"], "frames": spec["frames"],
            "parameters": spec["params"], "heading_deg": spec["heading_deg"],
            "effective_heading_deg": float(np.rad2deg(np.arctan2(facing[0], facing[2]))),
            "start_frame": start, "end_frame": end,
            "metrics": clip_metrics(result, plan), "residuals": segment_residuals,
            "notes": list(result.notes),
            **({"timing": result.timing, "timing_origin_seconds": start / fps} if result.timing else {}),
        })
        notes.extend(f"segments[{index}]: {note}" for note in result.notes)

    metrics = branch.metrics(clip, plan, contacts=contacts)
    metrics["target_error_max"] = target_error
    metrics["target_error_scope"] = "segments_only"
    metrics["target_error_tolerance"] = 0.001
    failures = list(metrics["failures"])
    failures.extend(
        failure for record in records for failure in record["metrics"]["failures"]
    )
    if target_error > metrics["target_error_tolerance"]:
        failures.append("ik_target")
    skate = check_foot_skate(clip, plan)
    if not skate.ok:
        failures.append("foot_skate")
    metrics["failures"] = list(dict.fromkeys(failures))
    for join in joins:
        window = positions[join["start_frame"] - 1:join["end_frame"] + 1]
        delta = np.linalg.norm(np.diff(window, axis=0), axis=-1)
        join["root_step_max"] = float(delta[:, root].max())
        join["joint_step_max"] = float(delta.max())
        join["contacts"] = "inferred_from_height"
        join["foot_lock_enforced"] = False
    if joins:
        notes.append(
            "Transitions interpolate poses and root height; foot locking and velocity "
            "continuity are not enforced. Inspect the final animation on the target mesh."
        )
    return {
        "clip": clip, "segments": records, "transitions": joins,
        "metrics": metrics, "residuals": residuals, "notes": notes,
        "contact_check": {"ok": bool(skate.ok), "detail": skate.detail},
    }


def joint_positions(clip: Any) -> np.ndarray:
    'Return world joint positions shaped ``(frames, joints, 3)`` via forward kinematics.'
    positions, _ = branch.fk(clip)
    return np.asarray(positions, dtype=np.float32)


def residual_summary(result: Any) -> dict:
    'Return the worst IK residual per solved chain, in metres.'
    return {
        str(key): float(np.max(value))
        for key, value in (result.residuals or {}).items()
    }


__all__ = [
    "ARM_PRESETS",
    "TEMPLATES",
    "build_plan",
    "clip_metrics",
    "concatenate_clips",
    "generate_clip",
    "joint_positions",
    "list_presets",
    "preset_defaults",
    "residual_summary",
]
