"""Read-only structural metrics for rig, motion and retarget artifacts."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_EXPECTED_CHAINS = {"spine", "left_arm", "right_arm", "left_leg", "right_leg"}


def _artifact_ok(value: str | None) -> bool:
    if value is None:
        return False
    path = Path(value)
    return path.is_file() and path.stat().st_size > 0


def _read_json(path_like: str | None) -> dict[str, Any] | None:
    if not _artifact_ok(path_like):
        return None
    try:
        value = json.loads(
            Path(str(path_like)).read_text(encoding="utf-8-sig")
        )
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _rig_metrics(result: dict) -> dict[str, Any]:
    rig_path = result.get("rig_path")
    skeleton_path = result.get("skeleton_path")
    text = (
        Path(rig_path).read_text(encoding="utf-8", errors="replace")
        if _artifact_ok(rig_path)
        else ""
    )
    lines = [line.strip() for line in text.splitlines()]
    joint_count = sum(line.startswith("joints ") for line in lines)
    skin_vertex_count = sum(line.startswith("skin ") for line in lines)
    has_root = any(line.startswith("root ") for line in lines)
    artifacts = {
        "rig": _artifact_ok(rig_path),
        "skeleton": _artifact_ok(skeleton_path),
        "mesh_obj": _artifact_ok(result.get("mesh_obj_path")),
    }
    return {
        "rig_artifacts": artifacts,
        "rig_valid": all(artifacts.values())
        and joint_count > 0
        and skin_vertex_count > 0
        and has_root,
        "joint_count": joint_count,
        "skin_vertex_count": skin_vertex_count,
        "rig_has_root": has_root,
    }


def _bvh_metrics(result: dict) -> dict[str, Any]:
    selected_path = result.get("motion_bvh_path")
    text = (
        Path(selected_path).read_text(
            encoding="utf-8",
            errors="replace",
        )
        if _artifact_ok(selected_path)
        else ""
    )
    frame_match = re.search(r"(?im)^\s*Frames:\s*(\d+)", text)
    time_match = re.search(
        r"(?im)^\s*Frame\s+Time:\s*([0-9.eE+-]+)",
        text,
    )
    frames = int(frame_match.group(1)) if frame_match else 0
    frame_time = float(time_match.group(1)) if time_match else None
    fps = round(1.0 / frame_time) if frame_time and frame_time > 0 else None
    artifacts = {
        "motion_bvh": _artifact_ok(selected_path),
        "motion_raw_bvh": _artifact_ok(result.get("raw_motion_bvh_path")),
        "motion_ik_bvh": _artifact_ok(result.get("ik_motion_bvh_path")),
        "joints_npy": _artifact_ok(result.get("joints_npy_path")),
        "preview_mp4": _artifact_ok(result.get("preview_mp4_path")),
    }
    return {
        "motion_artifacts": artifacts,
        "motion_valid": all(artifacts.values())
        and frames > 0
        and fps == 20,
        "motion_frame_count": frames,
        "motion_fps": fps,
    }


def _retarget_metrics(result: dict, task: dict) -> dict[str, Any]:
    mapping = _read_json(result.get("mapping_path"))
    info = _read_json(result.get("retarget_info_path"))
    export_anim_only = bool(task.get("export_anim_only", True))
    artifacts = {
        "retargeted_fbx": _artifact_ok(result.get("retargeted_fbx_path")),
        "anim_only_fbx": (
            _artifact_ok(result.get("anim_only_fbx_path"))
            if export_anim_only
            else not _artifact_ok(result.get("anim_only_fbx_path"))
        ),
        "mapping": mapping is not None,
        "retarget_info": info is not None,
    }

    bone_map = mapping.get("bone_map", {}) if mapping else {}
    chains = mapping.get("retarget_chains", {}) if mapping else {}
    source_count = int((info or {}).get("source_bone_count") or 0)
    target_count = int((info or {}).get("target_bone_count") or 0)
    mapped_source = len(set(bone_map))
    mapped_target = len(set(bone_map.values())) if bone_map else 0
    present_chains = _EXPECTED_CHAINS.intersection(chains)
    source_range = (info or {}).get("source_frame_range")
    output_range = (info or {}).get("output_frame_range")
    timing_preserved = (
        isinstance(source_range, list)
        and len(source_range) == 2
        and source_range == output_range
    )
    return {
        "retarget_artifacts": artifacts,
        "retarget_valid": all(artifacts.values()),
        "mapping_valid": bool(bone_map),
        "mapped_bone_count": mapped_source,
        "source_bone_coverage": (
            round(mapped_source / source_count, 4) if source_count else None
        ),
        "target_bone_coverage": (
            round(mapped_target / target_count, 4) if target_count else None
        ),
        "required_chain_coverage": round(
            len(present_chains) / len(_EXPECTED_CHAINS),
            4,
        ),
        "missing_chains": sorted(_EXPECTED_CHAINS - present_chains),
        "timing_preserved": timing_preserved,
        "fps": (info or {}).get("fps"),
        "anim_only_available": _artifact_ok(result.get("anim_only_fbx_path")),
    }


def evaluate(result: dict, task: dict) -> dict[str, Any]:
    """Evaluate existing artifacts without invoking any model or subprocess."""
    task_type = str(
        task.get("task_type", result.get("task_type", "retarget"))
    ).lower()
    score: dict[str, Any] = {
        "task_id": result.get("task_id"),
        "task_type": task_type,
    }
    validations: list[bool] = []

    if task_type in {"rig", "humanoid"}:
        rig = _rig_metrics(result)
        score.update(rig)
        validations.append(bool(rig["rig_valid"]))
    if task_type in {"text_to_motion", "humanoid"}:
        motion = _bvh_metrics(result)
        score.update(motion)
        validations.append(bool(motion["motion_valid"]))
    if task_type in {"vibe", "vibe_retarget"}:
        import math

        report = _read_json(result.get("vibe_report_path")) or {}
        motion = _bvh_metrics(result)
        fps = report.get("fps", 0)
        frames = report.get("frames", 0)
        metrics = report.get("metrics", {})
        valid = (motion["motion_artifacts"]["motion_bvh"]
                 and motion["motion_artifacts"]["joints_npy"]
                 and isinstance(fps, (int, float)) and math.isfinite(fps) and fps > 0
                 and isinstance(frames, int) and frames > 0
                 and motion["motion_frame_count"] == frames
                 and isinstance(metrics, dict) and metrics.get("failures") == [])
        score.update(motion, motion_valid=bool(valid), motion_fps=fps, vibe_metrics=metrics)
        validations.append(bool(valid))
        if task.get("creature") is not None or task.get("target_mesh_path") or task.get("target_glb_path") or result.get("rig_path"):
            rig = _rig_metrics(result)
            skin_required = task.get("skin", True) or task_type == "vibe_retarget"
            rig_valid = rig["rig_valid"] if skin_required else (
                all(rig["rig_artifacts"].values()) and rig["joint_count"] > 0 and rig["rig_has_root"])
            score.update(rig, rig_valid=bool(rig_valid))
            validations.append(bool(rig_valid))
            if skin_required:
                skin = _read_json(result.get("skin_report_path")) or {}
                validations.append(skin.get("passed") is True)
    if task_type in {"retarget", "humanoid", "vibe_retarget"}:
        retarget = _retarget_metrics(result, task)
        score.update(retarget)
        validations.append(bool(retarget["retarget_valid"]))

    if not validations:
        score["artifact_valid"] = False
        score["error"] = f"Unsupported task_type: {task_type}"
    else:
        score["artifact_valid"] = all(validations)

    # Preserve the original retarget evaluator's public ``artifacts`` field.
    if task_type == "retarget":
        score["artifacts"] = score["retarget_artifacts"]
    return score


__all__ = ["evaluate"]
