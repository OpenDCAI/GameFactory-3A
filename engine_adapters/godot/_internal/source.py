"""Resolve generated 3AGameFactory artifacts from task descriptors."""

from __future__ import annotations

import json
import math
import os
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pipeline.common import paths

ASSET_TYPE_TASK_KIND = {
    "avatar": "3d_object",
    "effect": "3d_object",
    "environment": "3d_object",
    "material": "3d_object",
    "prop": "3d_object",
    "static_mesh": "3d_object",
    "texture": "3d_object",
    "weapon": "3d_object",
    "scene": "3d_scene",
    "motion": "motion",
    "audio": "audio",
}


def validate_regular_directory_tree(source: Path, *, label: str) -> None:
    """Reject links and special nodes before copying a directory tree."""

    root_mode = source.lstat().st_mode
    if stat.S_ISLNK(root_mode):
        raise ValueError(
            f"{label} must not contain symbolic links (symlinks): {source}"
        )
    if not stat.S_ISDIR(root_mode):
        raise ValueError(f"{label} must be a directory: {source}")

    pending = [source]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                mode = entry.stat(follow_symlinks=False).st_mode
                if stat.S_ISLNK(mode):
                    raise ValueError(
                        f"{label} must not contain symbolic links (symlinks): {path}"
                    )
                if stat.S_ISDIR(mode):
                    pending.append(path)
                elif not stat.S_ISREG(mode):
                    raise ValueError(
                        f"{label} must contain only regular files and directories: "
                        f"{path}"
                    )


def _diagnostic_text(value: Any) -> str:
    """Return text without trusting an arbitrary object's ``__str__`` method."""

    try:
        return str(value)
    except Exception:
        return f"<{type(value).__name__}>"


def _json_safe_diagnostic(value: Any, active: set[int]) -> Any:
    """Recursively copy an arbitrary value into strict JSON-compatible types."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _diagnostic_text(bytes(value))

    is_mapping = isinstance(value, Mapping)
    is_sequence = isinstance(value, Sequence)
    if is_mapping or is_sequence:
        identity = id(value)
        if identity in active:
            return f"<recursive {type(value).__name__}>"
        active.add(identity)
        try:
            if is_mapping:
                return {
                    key if isinstance(key, str) else _diagnostic_text(key): (
                        _json_safe_diagnostic(item, active)
                    )
                    for key, item in value.items()
                }
            return [_json_safe_diagnostic(item, active) for item in value]
        except Exception:
            return _diagnostic_text(value)
        finally:
            active.remove(identity)

    return _diagnostic_text(value)


def source_descriptor(source: Any) -> dict[str, Any]:
    """Return a diagnostic-safe, strictly JSON-compatible source copy."""

    value = _json_safe_diagnostic(source, set())
    return value if isinstance(value, dict) else {"value": value}


def _validate_identity_component(name: str, value: str) -> None:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError(
            f"Godot asset source {name} must be one non-traversing path component"
        )


@dataclass(frozen=True)
class ResolvedAssetSource:
    game_id: str
    run_id: str
    task_kind: str
    task_id: str
    artifact_key: str
    task_dir: Path
    meta_path: Path
    path: Path
    metadata: dict[str, Any]

    def descriptor(self) -> dict[str, str]:
        return {
            "game_id": self.game_id,
            "run_id": self.run_id,
            "task_kind": self.task_kind,
            "task_id": self.task_id,
            "artifact_key": self.artifact_key,
        }


class GeneratedAssetSourceResolver:
    """Resolve one repository-owned artifact declared by ``meta.json``."""

    def resolve(
        self,
        source: Mapping[str, Any],
        *,
        asset_type: str = "",
        allow_directory: bool = False,
    ) -> ResolvedAssetSource:
        if not isinstance(source, Mapping):
            raise TypeError(
                "Godot asset source must be a descriptor object with "
                "game_id, run_id, task_kind, and task_id"
            )
        descriptor = dict(source)
        game_id = str(descriptor.get("game_id") or "").strip()
        task_id = str(descriptor.get("task_id") or "").strip()
        run_id = str(descriptor.get("run_id") or paths.DEFAULT_RUN_ID).strip()
        task_kind = str(
            descriptor.get("task_kind")
            or ASSET_TYPE_TASK_KIND.get(str(asset_type or "").strip().lower(), "")
        ).strip()
        missing = [
            name
            for name, value in {
                "game_id": game_id,
                "run_id": run_id,
                "task_kind": task_kind,
                "task_id": task_id,
            }.items()
            if not value
        ]
        if missing:
            raise ValueError(
                "Godot asset source descriptor is missing: " + ", ".join(missing)
            )
        for name, value in {
            "game_id": game_id,
            "run_id": run_id,
            "task_id": task_id,
        }.items():
            _validate_identity_component(name, value)
        paths.check_kind(task_kind)

        output_root = paths.OUTPUT_ROOT.expanduser().resolve(strict=False)
        task_dir = paths.task_output_dir(
            game_id,
            task_kind,
            task_id,
            run_id=run_id,
            create=False,
        ).resolve()
        try:
            task_dir.relative_to(output_root)
        except ValueError as exc:
            raise ValueError(
                f"Generated task directory must stay inside OUTPUT_ROOT: {task_dir}"
            ) from exc
        meta_path = task_dir / "meta.json"
        if not meta_path.is_file():
            raise FileNotFoundError(
                f"Generated asset metadata was not found: {meta_path}"
            )
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Generated asset metadata is invalid JSON: {meta_path}"
            ) from exc
        if not isinstance(metadata, dict):
            raise ValueError(f"Generated asset metadata must be an object: {meta_path}")
        expected = {
            "game_id": game_id,
            "run_id": run_id,
            "task_kind": task_kind,
            "task_id": task_id,
        }
        mismatches = []
        for key, expected_value in expected.items():
            actual = str(metadata.get(key) or "").strip()
            if actual and actual != expected_value:
                mismatches.append(f"{key}={actual!r} (expected {expected_value!r})")
        if mismatches:
            raise ValueError(
                "Generated asset metadata identity mismatch: " + "; ".join(mismatches)
            )

        artifact_key = str(descriptor.get("artifact_key") or "").strip()
        candidate_keys = sorted(
            str(key)
            for key, value in metadata.items()
            if (
                key != "output_dir"
                and str(key).endswith("_path")
                and isinstance(value, str)
                and value.strip()
            )
        )
        if not artifact_key:
            if len(candidate_keys) != 1:
                available = ", ".join(candidate_keys) or "(none)"
                raise ValueError(
                    "artifact_key is required when meta.json declares "
                    f"zero or multiple artifact files; available: {available}"
                )
            artifact_key = candidate_keys[0]
        raw_path = metadata.get(artifact_key)
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError(f"meta.json does not declare artifact {artifact_key!r}")
        artifact_path = Path(raw_path).expanduser()
        if not artifact_path.is_absolute():
            artifact_path = task_dir / artifact_path
        artifact_path = artifact_path.resolve()
        try:
            artifact_path.relative_to(task_dir)
        except ValueError as exc:
            raise ValueError(
                "Generated artifact path must stay inside its task "
                f"directory: {artifact_path}"
            ) from exc
        exists = artifact_path.exists() if allow_directory else artifact_path.is_file()
        if not exists:
            raise FileNotFoundError(
                f"Generated artifact was not found: {artifact_path}"
            )
        return ResolvedAssetSource(
            game_id=game_id,
            run_id=run_id,
            task_kind=task_kind,
            task_id=task_id,
            artifact_key=artifact_key,
            task_dir=task_dir,
            meta_path=meta_path,
            path=artifact_path,
            metadata=metadata,
        )
