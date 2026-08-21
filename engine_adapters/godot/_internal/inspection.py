"""Native Godot resource inspection and import-validation helpers."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .transport import GodotProcessResult, GodotTransport

INSPECT_SCRIPT = (
    Path(__file__).resolve().parents[1] / "_scripts" / "inspect_resource.gd"
)
_IMPORT_ERROR = re.compile(
    r"(?:^|\n)\s*(?:SCRIPT ERROR|ERROR):[^\n]*(?:\bimport(?:ing)?\b|\bresource\b)|"
    r"Error importing|Failed (?:loading|to import) resource|"
    r"\bERR_(?:FILE_CORRUPT|PARSE_ERROR)\b|"
    r"(?:^|\n)\s*(?:SCRIPT ERROR|ERROR):[^\n]*"
    r"(?:Could(?:n't| not)|Failed to)\s+(?:load|decode|parse)\b[^\n]*"
    r"\b(?:image|texture|resource|dependency)\b",
    re.IGNORECASE,
)
_VERSION = re.compile(r"(?<!\d)(\d+)(?:\.(\d+))?(?:\.(\d+))?")


def parse_godot_version(value: str) -> tuple[int, ...] | None:
    """Return the numeric prefix from ``godot --version`` output."""

    match = _VERSION.search(str(value or "").strip())
    if match is None:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def godot_4_version_error(value: str) -> str:
    """Explain why a version string cannot drive the Godot 4 adapter."""

    parsed = parse_godot_version(value)
    if parsed is None:
        return f"Could not parse Godot version from: {value or '<empty>'}"
    if parsed[0] != 4:
        return (
            f"Godot 4.x is required; the configured executable reports {value.strip()}"
        )
    return ""


def godot_import_error_lines(output: str) -> list[str]:
    """Return editor error lines that invalidate a zero-exit import run."""

    text = str(output or "")
    if not _IMPORT_ERROR.search(text):
        return []
    return [
        line.strip() for line in text.splitlines() if _IMPORT_ERROR.search(line)
    ] or [text.strip()]


def inspect_godot_resource(
    transport: GodotTransport,
    resource_path: str,
    *,
    timeout: float,
) -> tuple[GodotProcessResult, dict[str, Any]]:
    """Load a ``res://`` resource in Godot and return its native report."""

    if not INSPECT_SCRIPT.is_file():
        raise FileNotFoundError(
            f"Godot reflection script was not found: {INSPECT_SCRIPT}"
        )
    descriptor, report_name = tempfile.mkstemp(
        prefix="a3game-godot-inspect-", suffix=".json"
    )
    os.close(descriptor)
    report = Path(report_name)
    report.unlink(missing_ok=True)
    try:
        process = transport.run(
            ["--script", str(INSPECT_SCRIPT)],
            timeout=timeout,
            environment={
                "A3GAME_GODOT_RESOURCE": resource_path,
                "A3GAME_GODOT_INSPECT_REPORT": str(report),
            },
        )
        if not report.is_file():
            details = process.stderr.strip() or process.stdout.strip()
            raise RuntimeError(
                "Godot resource inspection produced no report"
                + (f": {details[-4000:]}" if details else "")
            )
        inspection = json.loads(report.read_text(encoding="utf-8"))
        if not isinstance(inspection, dict):
            raise TypeError("Godot resource inspection report must be an object")
        return process, inspection
    finally:
        report.unlink(missing_ok=True)


def inspection_skeleton_paths(inspection: dict[str, Any]) -> list[str]:
    """Return live Skeleton3D paths, including paths proven by bone tracks."""

    paths: list[str] = []
    for skeleton in inspection.get("skeletons") or []:
        if not isinstance(skeleton, dict) or not int(skeleton.get("bone_count") or 0):
            continue
        path = _normalize_node_path(str(skeleton.get("path") or ""))
        if path and path not in paths:
            paths.append(path)
    for detail in inspection.get("animation_details") or []:
        if not isinstance(detail, dict):
            continue
        for track in detail.get("tracks") or []:
            if (
                not isinstance(track, dict)
                or not track.get("bone")
                or not track.get("targets_skeleton_bone")
            ):
                continue
            path = _normalize_node_path(str(track.get("node_path") or ""))
            if path and path not in paths:
                paths.append(path)
    return paths


def inspection_bone_tracks(inspection: dict[str, Any]) -> list[dict[str, Any]]:
    """Return normalized track entries that carry a NodePath subname."""

    tracks: list[dict[str, Any]] = []
    for detail in inspection.get("animation_details") or []:
        if not isinstance(detail, dict):
            continue
        for item in detail.get("tracks") or []:
            if not isinstance(item, dict):
                continue
            bone = str(item.get("bone") or "").strip()
            if not bone:
                continue
            tracks.append(
                {
                    "animation": str(detail.get("name") or ""),
                    "node_path": _normalize_node_path(str(item.get("node_path") or "")),
                    "bone": bone,
                    "path": str(item.get("path") or ""),
                    "target_class": str(item.get("target_class") or ""),
                    "targets_skeleton_bone": bool(item.get("targets_skeleton_bone")),
                }
            )
    return tracks


def validate_resource_inspection(
    inspection: dict[str, Any],
    asset_type: str,
    *,
    expected_skeleton: str = "",
) -> list[str]:
    """Validate the native resource class and capabilities for an asset type."""

    normalized_type = str(asset_type or "").strip().lower()
    resource_class = str(inspection.get("resource_class") or "")
    errors: list[str] = []
    if not inspection.get("ok") or not resource_class:
        return [str(inspection.get("error") or "Godot could not load the resource")]

    spawnable = {
        "avatar",
        "environment",
        "prop",
        "scene",
        "static_mesh",
        "weapon",
    }
    if normalized_type in spawnable:
        if not inspection.get("is_packed_scene"):
            errors.append(
                f"{normalized_type} must load as PackedScene, got {resource_class}"
            )
        elif not inspection.get("instantiable"):
            errors.append(f"{normalized_type} PackedScene could not be instantiated")
    elif normalized_type == "texture" and not inspection.get("is_texture_2d"):
        errors.append(f"texture must inherit Texture2D, got {resource_class}")
    elif normalized_type == "audio" and not inspection.get("is_audio_stream"):
        errors.append(f"audio must inherit AudioStream, got {resource_class}")
    elif normalized_type == "material" and not (
        inspection.get("is_material") or inspection.get("is_texture_2d")
    ):
        errors.append(
            "material must inherit Material or be an importable Texture2D, "
            f"got {resource_class}"
        )

    node_classes = {
        str(item.get("class") or "")
        for item in inspection.get("nodes") or []
        if isinstance(item, dict)
    }
    if (
        normalized_type in {"avatar", "prop", "static_mesh", "weapon"}
        and "MeshInstance3D" not in node_classes
    ):
        errors.append(f"{normalized_type} contains no MeshInstance3D")

    skeleton_bones = {
        _normalize_node_path(str(item.get("path") or "")): {
            str(bone) for bone in item.get("bones") or [] if str(bone)
        }
        for item in inspection.get("skeletons") or []
        if isinstance(item, dict) and int(item.get("bone_count") or 0)
    }
    skeleton_bones.pop("", None)
    live_skeleton_paths = list(skeleton_bones)
    skeleton_paths = inspection_skeleton_paths(inspection)
    bone_tracks = inspection_bone_tracks(inspection)
    verified_bone_tracks = [
        track
        for track in bone_tracks
        if track["targets_skeleton_bone"]
        and track["node_path"] in skeleton_bones
        and track["bone"] in skeleton_bones[track["node_path"]]
    ]
    if normalized_type == "avatar" and not live_skeleton_paths:
        errors.append("avatar contains no Skeleton3D with bones")
    if normalized_type == "avatar":
        resolved_skinned_meshes = [
            item
            for item in inspection.get("skinned_meshes") or []
            if isinstance(item, dict)
            and item.get("has_skin")
            and item.get("skeleton_resolved")
            and _normalize_node_path(str(item.get("skeleton_path") or ""))
            in skeleton_bones
        ]
        if not resolved_skinned_meshes:
            errors.append(
                "avatar contains no skinned MeshInstance3D bound to a live "
                "Skeleton3D with bones"
            )
    if normalized_type == "motion":
        valid_motion_class = bool(
            inspection.get("is_packed_scene")
            or inspection.get("is_animation_library")
            or inspection.get("is_animation")
        )
        if not valid_motion_class:
            errors.append(
                "motion must load as PackedScene, AnimationLibrary, or Animation, "
                f"got {resource_class}"
            )
        if not inspection.get("animations"):
            errors.append("motion contains no animation clips")
        if inspection.get("is_packed_scene") and not live_skeleton_paths:
            errors.append("motion PackedScene contains no Skeleton3D with bones")
        elif not skeleton_paths:
            errors.append("motion contains no skeletal track target")
        if inspection.get("is_packed_scene") and not verified_bone_tracks:
            errors.append(
                "motion contains no bone-targeted animation track referencing "
                "a live Skeleton3D bone"
            )
        elif not inspection.get("is_packed_scene") and not bone_tracks:
            errors.append("motion contains no bone-targeted animation tracks")

    expected = _normalize_node_path(expected_skeleton)
    proven_skeleton_paths = (
        live_skeleton_paths if inspection.get("is_packed_scene") else skeleton_paths
    )
    if expected and expected not in proven_skeleton_paths:
        errors.append(
            "Skeleton3D path was not proven by the imported resource: "
            "expected="
            f"{expected} actual={', '.join(proven_skeleton_paths) or '<missing>'}"
        )
    elif (
        normalized_type == "motion"
        and inspection.get("is_packed_scene")
        and expected
        and not any(track["node_path"] == expected for track in verified_bone_tracks)
    ):
        errors.append(
            "No bone-targeted animation track drives the requested live "
            f"Skeleton3D path: {expected}"
        )
    return errors


def _normalize_node_path(value: str) -> str:
    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/") or ("." if normalized == "." else "")
