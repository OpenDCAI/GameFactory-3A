'Generate local motion, rigging, skinning and export artifacts.'
from __future__ import annotations

import json
from math import isfinite
from numbers import Real
from typing import Any

from . import motion as motion_step
from . import skeleton as skeleton_step
from . import skinning as skinning_step
from .bvh import clip_to_bvh_bytes


DEFAULT_FPS = 30.0
_MOTION_RESERVED = {"name", "preset", "plan", "num_frames", "fps", "heading_deg"}
_SEGMENT_KEYS = {"preset", "num_frames", "heading_deg", "motion_overrides"}


def _resolve_segments(segments, transitions, *, fps, heading_deg):
    """Validate the entire sequence before fitting a rig or generating clips."""
    if not isinstance(segments, list) or not segments:
        raise ValueError("segments must be a non-empty list")
    if transitions is None:
        transitions = [{} for _ in range(len(segments) - 1)]
    if not isinstance(transitions, list) or len(transitions) != len(segments) - 1:
        raise ValueError("transitions must contain exactly len(segments) - 1 entries")
    joins = []
    for index, transition in enumerate(transitions):
        prefix = f"transitions[{index}]"
        if not isinstance(transition, dict):
            raise ValueError(f"{prefix} must be a dict")
        unknown = transition.keys() - {"transition_frames", "carry_facing"}
        if unknown:
            raise ValueError(f"{prefix} has unknown fields: {sorted(unknown)}")
        frames = transition.get("transition_frames", 8)
        carry = transition.get("carry_facing", True)
        if type(frames) is not int or frames < 0:
            raise ValueError(f"{prefix}.transition_frames must be a non-negative integer")
        if type(carry) is not bool:
            raise ValueError(f"{prefix}.carry_facing must be a boolean")
        joins.append({"transition_frames": frames, "carry_facing": carry})
    specs = []
    for index, segment in enumerate(segments):
        prefix = f"segments[{index}]"
        if not isinstance(segment, dict):
            raise ValueError(f"{prefix} must be a dict")
        unknown = segment.keys() - _SEGMENT_KEYS
        if unknown:
            raise ValueError(f"{prefix} has unknown fields: {sorted(unknown)}")
        preset = segment.get("preset")
        if not isinstance(preset, str) or not preset.strip():
            raise ValueError(f"{prefix}.preset must be a non-empty string")
        heading = segment.get("heading_deg", heading_deg)
        if isinstance(heading, bool) or not isinstance(heading, Real) or not isfinite(heading):
            raise ValueError(f"{prefix}.heading_deg must be a finite number")
        if index and joins[index - 1]["carry_facing"] and "heading_deg" in segment:
            raise ValueError(f"{prefix}.heading_deg requires carry_facing=false on its incoming transition")
        overrides = segment.get("motion_overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError(f"{prefix}.motion_overrides must be a dict")
        if _MOTION_RESERVED.intersection(overrides):
            raise ValueError(f"{prefix}.motion_overrides contains reserved arguments")
        try:
            spec = motion_step.branch.resolve_preset(
                preset, num_frames=segment.get("num_frames"), fps=fps,
                heading_deg=heading, **overrides,
            )
        except ValueError as exc:
            raise ValueError(f"{prefix}: {exc}") from exc
        spec["heading_deg"] = float(heading)
        specs.append(spec)
    return specs, joins


def generate_vibe_motion(
    *,
    preset: str | None = None,
    creature: str | None = None,
    mesh: Any | None = None,
    template: str = "biped_armed",
    rig_preset: str = "biped",
    skin_preset: str = "compact",
    num_frames: int | None = None,
    fps: float = DEFAULT_FPS,
    heading_deg: float = 0.0,
    skin: bool = True,
    motion_overrides: dict | None = None,
    rig_overrides: dict | None = None,
    skin_overrides: dict | None = None,
    segments: list[dict] | None = None,
    transitions: list[dict] | None = None,
) -> dict:
    """Generate one preset or a sequence on one shared rig and skin.

    segments is mutually exclusive with preset, num_frames and motion_overrides.
    Each segment selects its preset, frame count and motion_overrides. All use
    the global fps. transitions describes each adjacent pair, inserting eight
    frames and carrying yaw by default; it never removes segment frames.
    heading_deg is the default world heading. A later explicit segment heading
    requires carry_facing=False, otherwise the previous endpoint yaw is used.
    """
    if creature is not None and mesh is not None:
        raise ValueError("Pass either 'creature' or 'mesh', not both.")
    if type(skin) is not bool:
        raise ValueError("skin must be a boolean")
    for name, value in (("fps", fps), ("heading_deg", heading_deg)):
        if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value):
            raise ValueError(f"{name} must be a finite number")
    if fps <= 0:
        raise ValueError("fps must be positive")
    sequence_mode = segments is not None
    if sequence_mode and any(value is not None for value in (preset, num_frames, motion_overrides)):
        raise ValueError("segments cannot be combined with preset, num_frames or motion_overrides")
    if not sequence_mode and transitions is not None:
        raise ValueError("transitions requires segments")
    for name, value in (("motion_overrides", motion_overrides),
                        ("rig_overrides", rig_overrides), ("skin_overrides", skin_overrides)):
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"{name} must be a dict")
    motion_overrides = motion_overrides or {}
    rig_overrides = rig_overrides or {}
    skin_overrides = skin_overrides or {}
    for name, value, reserved in (
        ("motion_overrides", motion_overrides, _MOTION_RESERVED),
        ("rig_overrides", rig_overrides, {"mesh", "preset"}),
        ("skin_overrides", skin_overrides, {"mesh", "rig", "preset"}),
    ):
        if reserved.intersection(value):
            raise ValueError(f"{name} contains reserved arguments: {sorted(reserved.intersection(value))}")
    if rig_preset != "biped" or creature not in (None, "human"):
        raise ValueError("Motion presets support biped humanoids only; use fit_skeleton separately for quadruped/axial rigs")
    if sequence_mode:
        specs, joins = _resolve_segments(segments, transitions, fps=fps, heading_deg=heading_deg)
    else:
        spec = motion_step.branch.resolve_preset(
            "walk" if preset is None else preset, num_frames=num_frames,
            fps=fps, heading_deg=heading_deg, **motion_overrides,
        )
        specs, joins = [spec], []

    notes: list[str] = []
    artifacts: dict[str, Any] = {}
    rig = None
    source_mesh = mesh

    if creature is not None:
        source_mesh = skeleton_step.creature_mesh(creature)

    if source_mesh is not None:
        rig = skeleton_step.fit_skeleton(
            source_mesh,
            preset=rig_preset,
            **(rig_overrides or {}),
        )
        notes += list(getattr(rig, "notes", []) or [])
        plan = motion_step.build_plan(skeleton_step.to_motion_template(rig))

        skin_weights = None
        if skin:
            skin_weights = skinning_step.skin_mesh(
                source_mesh,
                rig,
                preset=skin_preset,
                **(skin_overrides or {}),
            )
            report = skinning_step.validate_skin(skin_weights, source_mesh, rig)
            artifacts["skin_report_json"] = json.dumps(
                {
                    "preset": skin_preset,
                    "bone_convention": skin_overrides.get("bone_convention", "outgoing"),
                    "passed": skinning_step.report_passed(report),
                    "stats": skinning_step.weight_stats(skin_weights),
                    "findings": [
                        {
                            "name": f.check,
                            "check": f.check,
                            "ok": bool(f.ok),
                            "value": f.value,
                            "detail": f.detail,
                        }
                        for f in report.findings
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
            notes += list(getattr(skin_weights, "notes", []) or [])

        artifacts["rig_text"] = skeleton_step.rig_to_text(rig, skin=skin_weights)
        artifacts["skeleton_text"] = skeleton_step.skeleton_to_text(rig)
        artifacts["mesh_obj_bytes"] = skeleton_step.mesh_to_obj(
            source_mesh
        ).encode("utf-8")
        artifacts["rig_report_json"] = json.dumps(
            {
                "rig_preset": rig_preset,
                "name": str(getattr(rig, "name", "")),
                "joints": int(len(rig.joint_names)),
                "chains": {k: int(len(v)) for k, v in (rig.chains or {}).items()},
                "findings": skeleton_step.evaluate_skeleton(source_mesh, rig),
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    else:
        plan = motion_step.build_plan(template)

    sequence_report = {}
    if sequence_mode:
        sequence = motion_step._generate_sequence(specs, joins, plan, fps=fps)
        clip = sequence["clip"]
        metrics, residuals = sequence["metrics"], sequence["residuals"]
        notes += sequence["notes"]
        sequence_report = {
            "mode": "sequence", "segments": sequence["segments"],
            "transitions": sequence["transitions"],
            "frame_ranges": "zero_based_end_exclusive",
            "transition_mode": "insert", "vertical_alignment": "preserve_height",
            "residual_scope": "segments_only", "contact_check": sequence["contact_check"],
        }
    else:
        spec = specs[0]
        result = motion_step.generate_clip(
            spec["name"], plan, num_frames=spec["frames"], fps=fps,
            heading_deg=heading_deg, **spec["params"],
        )
        clip = result.clip
        metrics = motion_step.clip_metrics(result, plan)
        residuals = motion_step.residual_summary(result)
        notes += list(result.notes or [])
        if result.timing:
            sequence_report["timing"] = result.timing
    if rig is not None and skin:
        from .rigging_utils.export import animated_glb
        artifacts["animated_glb_bytes"] = animated_glb(source_mesh, rig, skin_weights, clip)

    artifacts.update(
        bvh_bytes=clip_to_bvh_bytes(clip),
        fps=float(clip.fps),
        joints=motion_step.joint_positions(clip),
        metrics=metrics,
        residuals=residuals,
        preset="sequence" if sequence_mode else specs[0]["name"],
        frames=int(clip.num_frames),
        notes=notes,
    )
    artifacts["vibe_report_json"] = json.dumps(
        {"preset": artifacts["preset"], "frames": artifacts["frames"],
         "fps": artifacts["fps"], "parameters": None if sequence_mode else specs[0]["params"],
         "heading_deg": heading_deg, "template": plan.template.name,
         "rig_preset": rig_preset if rig is not None else None,
         "skin_preset": skin_preset if rig is not None and skin else None,
         "metrics": metrics, "residuals": residuals, "notes": notes, **sequence_report},
        indent=2, ensure_ascii=False, allow_nan=False,
    )
    return artifacts


__all__ = ["DEFAULT_FPS", "generate_vibe_motion"]
