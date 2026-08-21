"""Persistent World draft and package operations for GodotClient v1."""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from .._internal import (
    ArtifactRecord,
    atomic_write_text,
    read_managed_text,
    validate_managed_directory,
)
from ..assets import GodotAssetsClient
from ..config import GodotClientConfig
from ..contracts import GodotOperationResult
from ..reflection import GodotReflectionClient

WORLD_SCENE_SUFFIXES = {
    ".blend",
    ".dae",
    ".fbx",
    ".glb",
    ".gltf",
    ".scn",
    ".tscn",
}
WORLD_DRAFT_SCHEMA_VERSION = "gamefactory3a.godot.world_draft.v1"
WORLD_PACKAGE_SCHEMA_VERSION = "gamefactory3a.godot.world_package.v1"

_WORLD_DRAFT_FIELDS = {
    "schema_version",
    "draft_id",
    "world_id",
    "project_id",
    "status",
    "scene_artifact_id",
    "scene_path",
    "artifacts",
    "metadata",
    "created_at",
}
_WORLD_PACKAGE_FIELDS = _WORLD_DRAFT_FIELDS | {"package_id", "published_at"}


def _safe_id(value: str, prefix: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value or "")).strip("._")
    return cleaned or f"{prefix}_{uuid4().hex[:12]}"


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    serialized = (
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    )
    atomic_write_text(path, serialized, label="Godot World record")


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"World record contains duplicate field {key!r}")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"World record contains non-finite number {value}")


def _required_string(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str):
        raise TypeError(f"World record field {field!r} must be a string")
    if not value.strip():
        raise ValueError(f"World record field {field!r} must not be empty")
    return value


def _validate_record_fields(
    record: Mapping[str, Any],
    *,
    expected_fields: set[str],
) -> None:
    missing = sorted(expected_fields - record.keys())
    if missing:
        raise ValueError(
            "World record is missing required field(s): " + ", ".join(missing)
        )
    unexpected = sorted(record.keys() - expected_fields)
    if unexpected:
        raise ValueError(
            "World record contains unexpected field(s): " + ", ".join(unexpected)
        )


def _validate_common_record(record: Mapping[str, Any]) -> None:
    for field in (
        "draft_id",
        "world_id",
        "project_id",
        "scene_artifact_id",
        "scene_path",
    ):
        _required_string(record, field)
    for field, prefix in (
        ("draft_id", "draft"),
        ("world_id", "world"),
        ("project_id", "project"),
    ):
        value = record[field]
        if _safe_id(value, prefix) != value:
            raise ValueError(f"World record field {field!r} is not a canonical ID")
    scene_path = record["scene_path"]
    if not scene_path.startswith("res://"):
        raise ValueError("World record field 'scene_path' must use res://")
    if Path(scene_path).suffix.lower() not in WORLD_SCENE_SUFFIXES:
        raise ValueError("World record field 'scene_path' is not a supported scene")

    artifacts = record.get("artifacts")
    if not isinstance(artifacts, list):
        raise TypeError("World record field 'artifacts' must be an array")
    for index, artifact_id in enumerate(artifacts):
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise ValueError(
                "World record field 'artifacts' must contain only non-empty "
                f"strings (invalid entry {index})"
            )
    if len(set(artifacts)) != len(artifacts):
        raise ValueError("World record field 'artifacts' must not contain duplicates")
    if not isinstance(record.get("metadata"), dict):
        raise TypeError("World record field 'metadata' must be an object")

    created_at = record.get("created_at")
    if (
        isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or not math.isfinite(created_at)
        or created_at <= 0
    ):
        raise ValueError("World record field 'created_at' must be a finite timestamp")


def _validate_draft_record(
    record: Mapping[str, Any],
    *,
    expected_id: str,
    path: Path,
) -> None:
    _validate_record_fields(record, expected_fields=_WORLD_DRAFT_FIELDS)
    if record.get("schema_version") != WORLD_DRAFT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported Godot World draft schema: {record.get('schema_version')!r}"
        )
    _validate_common_record(record)
    if record.get("status") != "draft":
        raise ValueError(
            f"Godot World draft status must be 'draft', got {record.get('status')!r}"
        )
    draft_id = record["draft_id"]
    if draft_id != expected_id or draft_id != path.stem:
        raise ValueError(
            "Godot World draft identity does not match the request and filename: "
            f"record={draft_id!r}, request={expected_id!r}, file={path.stem!r}"
        )


def _validate_package_record(record: Mapping[str, Any], *, path: Path) -> None:
    _validate_record_fields(record, expected_fields=_WORLD_PACKAGE_FIELDS)
    if record.get("schema_version") != WORLD_PACKAGE_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported Godot World package schema: {record.get('schema_version')!r}"
        )
    _validate_common_record(record)
    if record.get("status") != "published":
        raise ValueError(
            "Godot World package status must be 'published', got "
            f"{record.get('status')!r}"
        )
    package_id = _required_string(record, "package_id")
    if _safe_id(package_id, "package") != package_id:
        raise ValueError("World record field 'package_id' is not a canonical ID")
    if package_id != path.stem:
        raise ValueError(
            "Godot World package identity does not match its filename: "
            f"record={package_id!r}, file={path.stem!r}"
        )
    published_at = record.get("published_at")
    if (
        isinstance(published_at, bool)
        or not isinstance(published_at, (int, float))
        or not math.isfinite(published_at)
        or published_at < record["created_at"]
    ):
        raise ValueError(
            "World record field 'published_at' must be a finite timestamp not "
            "earlier than created_at"
        )


class GodotWorldClient:
    def __init__(
        self,
        config: GodotClientConfig,
        assets: GodotAssetsClient,
        reflection: GodotReflectionClient,
    ) -> None:
        self._config = config
        self._assets = assets
        self._reflection = reflection

    def build(
        self,
        source: Mapping[str, Any],
        *,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            resolved_options = dict(options or {})
        except Exception as exc:
            return GodotOperationResult.failure(
                "world.build", f"{type(exc).__name__}: {exc}"
            ).to_dict()
        publish = bool(resolved_options.pop("publish", True))
        imported = self._assets.import_scene(
            source,
            destination=str(resolved_options.pop("destination", "")),
            options=resolved_options,
        )
        if not imported.get("ok"):
            imported["operation"] = "world.build"
            return imported
        if imported.get("payload", {}).get("dry_run"):
            imported["operation"] = "world.build"
            imported["payload"]["publish"] = publish
            return imported
        artifacts = imported.get("artifacts") or []
        if not artifacts:
            return GodotOperationResult.failure(
                "world.build", "Scene import produced no registered artifact"
            ).to_dict()
        scene = dict(artifacts[0])
        spec = {
            "world_id": _safe_id(
                str(resolved_options.get("world_id") or scene.get("asset_id") or ""),
                "world",
            ),
            "project_id": _safe_id(
                str(resolved_options.get("project_id") or self._config.project_name),
                "project",
            ),
            "scene_artifact_id": str(scene.get("artifact_id") or ""),
            "scene_path": str(scene.get("backend_path") or ""),
            "artifacts": [str(scene.get("artifact_id") or "")],
            "metadata": resolved_options.get("metadata") or {},
        }
        draft = self.create_draft(spec)
        if not draft.get("ok"):
            draft["operation"] = "world.build"
            return draft
        draft_id = str(draft["payload"].get("draft_id") or "")
        validation = self.validate_draft(draft_id)
        if not validation.get("ok"):
            validation["operation"] = "world.build"
            return validation
        package = self.publish_draft(draft_id) if publish else draft
        package["operation"] = "world.build"
        package["payload"]["scene_import"] = imported
        return package

    def create_draft(
        self,
        spec: Mapping[str, Any],
        *,
        draft_id: str = "",
        project_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operation = "world.create_draft"
        if not isinstance(spec, Mapping):
            return GodotOperationResult.failure(
                operation, "spec must be an object"
            ).to_dict()
        try:
            value = dict(spec)
            if draft_id:
                value["draft_id"] = draft_id
            if project_id:
                value["project_id"] = project_id
            if metadata is not None:
                spec_metadata = value.get("metadata") or {}
                if not isinstance(spec_metadata, Mapping):
                    raise TypeError("spec metadata must be an object")
                if not isinstance(metadata, Mapping):
                    raise TypeError("metadata must be an object")
                value["metadata"] = {**dict(spec_metadata), **dict(metadata)}
            world_id = _safe_id(str(value.get("world_id") or ""), "world")
            project_id = _safe_id(
                str(value.get("project_id") or self._config.project_name), "project"
            )
            draft_id = _safe_id(str(value.get("draft_id") or ""), "draft")
            scene_artifact_id = str(value.get("scene_artifact_id") or "").strip()
            scene_path = str(value.get("scene_path") or "").strip()
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        if not scene_artifact_id and not scene_path:
            return GodotOperationResult.failure(
                operation,
                "spec requires scene_artifact_id or scene_path",
            ).to_dict()
        try:
            record, scene_path, scene_errors = self._resolve_scene_record(
                scene_artifact_id,
                scene_path,
            )
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        if scene_errors or record is None:
            return GodotOperationResult.failure(
                operation,
                *scene_errors,
                payload={
                    "scene_artifact_id": scene_artifact_id,
                    "scene_path": scene_path,
                },
            ).to_dict()
        scene_artifact_id = record.artifact_id
        path = self._draft_path(draft_id)
        try:
            raw_artifacts = value.get("artifacts") or []
            if not isinstance(raw_artifacts, (list, tuple)):
                raise TypeError("artifacts must be an array")
            raw_metadata = value.get("metadata") or {}
            if not isinstance(raw_metadata, Mapping):
                raise TypeError("metadata must be an object")
            payload = {
                "schema_version": WORLD_DRAFT_SCHEMA_VERSION,
                "draft_id": draft_id,
                "world_id": world_id,
                "project_id": project_id,
                "status": "draft",
                "scene_artifact_id": scene_artifact_id,
                "scene_path": scene_path,
                "artifacts": [str(item) for item in raw_artifacts if str(item).strip()],
                "metadata": dict(raw_metadata),
                "created_at": time.time(),
            }
            _validate_draft_record(payload, expected_id=draft_id, path=path)
            _atomic_json(path, payload)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload={"draft_id": draft_id, "path": str(path)},
            ).to_dict()
        return GodotOperationResult.success(
            operation,
            artifacts=[
                {"type": "godot_world_draft", "path": str(path), "state": "draft"}
            ],
            payload={**payload, "path": str(path)},
        ).to_dict()

    def validate_draft(self, draft_id: str) -> dict[str, Any]:
        operation = "world.validate_draft"
        try:
            expected_id = str(draft_id or "")
            if not expected_id or _safe_id(expected_id, "draft") != expected_id:
                raise ValueError("draft_id must be a non-empty canonical ID")
            path = self._draft_path(expected_id)
            draft = self._read_json(path)
            _validate_draft_record(draft, expected_id=expected_id, path=path)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        errors = []
        scene_artifact_id = str(draft.get("scene_artifact_id") or "")
        scene_path = str(draft.get("scene_path") or "")
        try:
            record, scene_path, scene_errors = self._resolve_scene_record(
                scene_artifact_id,
                scene_path,
            )
            errors.extend(scene_errors)
            if record is not None:
                scene_artifact_id = record.artifact_id
            for artifact_id in draft.get("artifacts", []):
                if self._assets._registry.get(str(artifact_id)) is None:
                    errors.append(f"Unknown World artifact: {artifact_id}")
        except (OSError, TypeError, ValueError) as exc:
            return self._assets._registry_failure(
                operation,
                exc,
                payload={"draft_id": str(draft_id or "")},
            )
        return GodotOperationResult(
            operation=operation,
            ok=not errors,
            errors=tuple(errors),
            payload={
                **draft,
                "scene_artifact_id": scene_artifact_id,
                "scene_path": scene_path,
                "validated": not errors,
            },
        ).to_dict()

    def publish_draft(self, draft_id: str) -> dict[str, Any]:
        validation = self.validate_draft(draft_id)
        if not validation.get("ok"):
            validation["operation"] = "world.publish_draft"
            return validation
        draft = dict(validation["payload"])
        package_id = _safe_id(
            str(draft.get("package_id") or ""),
            "pkg_" + str(draft.get("world_id") or "world"),
        )
        package = {
            **draft,
            "schema_version": WORLD_PACKAGE_SCHEMA_VERSION,
            "package_id": package_id,
            "status": "published",
            "published_at": time.time(),
        }
        package.pop("validated", None)
        path = self._package_path(package_id)
        try:
            _validate_package_record(package, path=path)
            _atomic_json(path, package)
        except Exception as exc:
            return GodotOperationResult.failure(
                "world.publish_draft",
                f"{type(exc).__name__}: {exc}",
                payload={"package_id": package_id, "path": str(path)},
            ).to_dict()
        artifact = self._package_artifact(package, path)
        return GodotOperationResult.success(
            "world.publish_draft",
            artifacts=[artifact],
            payload={**package, "path": str(path)},
        ).to_dict()

    def list_packages(
        self,
        *,
        project_id: str = "",
        world_id: str = "",
    ) -> dict[str, Any]:
        packages = []
        root = self._config.world_registry_root / "packages"
        normalized_project_id = ""
        normalized_world_id = ""
        try:
            normalized_project_id = str(project_id or "")
            normalized_world_id = str(world_id or "")
            root = validate_managed_directory(
                root,
                label="Godot World package directory",
            )
            if root.is_dir():
                for path in sorted(root.glob("*.json")):
                    package = self._read_json(path)
                    _validate_package_record(package, path=path)
                    if (
                        normalized_project_id
                        and package["project_id"] != normalized_project_id
                    ):
                        continue
                    if (
                        normalized_world_id
                        and package["world_id"] != normalized_world_id
                    ):
                        continue
                    packages.append(self._package_artifact(package, path))
        except Exception as exc:
            return GodotOperationResult.failure(
                "world.list_packages",
                f"{type(exc).__name__}: {exc}",
                payload={
                    "project_id": normalized_project_id,
                    "world_id": normalized_world_id,
                },
            ).to_dict()
        return GodotOperationResult.success(
            "world.list_packages",
            artifacts=packages,
            payload={
                "project_id": normalized_project_id,
                "world_id": normalized_world_id,
                "count": len(packages),
            },
        ).to_dict()

    def _draft_path(self, draft_id: str) -> Path:
        return (
            self._config.world_registry_root
            / "drafts"
            / (_safe_id(draft_id, "draft") + ".json")
        )

    def _package_path(self, package_id: str) -> Path:
        return (
            self._config.world_registry_root
            / "packages"
            / (_safe_id(package_id, "package") + ".json")
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            text = read_managed_text(path, label="Godot World record")
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"World record was not found: {path}") from exc
        payload = json.loads(
            text,
            object_pairs_hook=_strict_json_object,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(payload, dict):
            raise ValueError(f"World record must be an object: {path}")
        return payload

    def _resource_file(self, value: str) -> Path:
        if not value.startswith("res://"):
            raise ValueError(f"World scene path must use res://: {value}")
        relative = Path(value[len("res://") :])
        project_dir = self._config.project_dir
        if project_dir is None:
            raise ValueError("project_path is not configured")
        resolved = (project_dir / relative).resolve(strict=False)
        try:
            resolved.relative_to(project_dir.resolve())
        except ValueError as exc:
            raise ValueError(f"World scene path escaped the project: {value}") from exc
        return resolved

    def _resolve_scene_record(
        self,
        artifact_id: str,
        scene_path: str,
    ) -> tuple[ArtifactRecord | None, str, list[str]]:
        """Resolve one registered, ready, instantiable Godot scene."""

        requested_id = str(artifact_id or "").strip()
        requested_path = str(scene_path or "").strip()
        errors: list[str] = []
        if not requested_id and not requested_path:
            return (
                None,
                "",
                ["World draft requires scene_artifact_id or scene_path"],
            )
        record = self._assets._registry.get(requested_id) if requested_id else None
        if requested_id and record is None:
            errors.append(f"Unknown scene artifact: {requested_id}")

        path_record = None
        if requested_path:
            candidate = self._assets._registry.find(requested_path)
            if candidate is not None and candidate.backend_path == requested_path:
                path_record = candidate
            else:
                errors.append(f"Godot World scene is not registered: {requested_path}")
        if record is not None and path_record is not None:
            if record.artifact_id != path_record.artifact_id:
                errors.append(
                    "scene_artifact_id and scene_path reference different "
                    "registered resources"
                )
        elif record is None and not requested_id:
            record = path_record

        if record is None:
            return None, requested_path, errors

        registered_path = str(record.backend_path or "").strip()
        if requested_path and registered_path != requested_path:
            errors.append(
                "Registered scene path does not match scene_path: "
                f"{registered_path!r} != {requested_path!r}"
            )
        if record.type != "scene":
            errors.append(
                f"World scene artifact must have type 'scene', got {record.type!r}"
            )
        if record.backend_class != "PackedScene":
            errors.append(
                "World scene artifact must be a PackedScene, got "
                f"{record.backend_class!r}"
            )
        if not record.spawnable:
            errors.append("World scene artifact is not marked spawnable")
        if record.state != "ready":
            errors.append(f"World scene artifact is not ready (state={record.state!r})")
        suffix = Path(registered_path).suffix.lower()
        if suffix not in WORLD_SCENE_SUFFIXES:
            errors.append(
                "World scene artifact does not use a Godot scene/importable "
                f"resource format: {registered_path}"
            )
        try:
            path = self._resource_file(registered_path)
            if not path.is_file():
                errors.append(f"Godot scene resource was not found: {registered_path}")
            elif suffix == ".tscn" and not re.search(
                r"^\s*\[gd_scene\b",
                path.read_text(encoding="utf-8", errors="replace"),
            ):
                errors.append(
                    f"Godot text scene has no [gd_scene] header: {registered_path}"
                )
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        if not errors:
            inspection = self._reflection.inspect_artifact(record.artifact_id)
            if not inspection.get("ok"):
                details = "; ".join(
                    str(item) for item in inspection.get("errors", []) if str(item)
                )
                errors.append(
                    "Godot could not inspect the registered World scene"
                    + (f": {details}" if details else "")
                )
            else:
                native = inspection.get("payload", {}).get("inspection", {})
                native_class = str(native.get("resource_class") or "")
                if native_class != "PackedScene":
                    errors.append(
                        "Registered World scene loads as "
                        f"{native_class or '<unknown>'}, not PackedScene"
                    )
                elif native.get("instantiable") is not True:
                    errors.append(
                        "Registered World PackedScene could not be instantiated"
                    )
        return record, registered_path, errors

    @staticmethod
    def _package_artifact(package: Mapping[str, Any], path: Path) -> dict[str, Any]:
        metadata = dict(package)
        return {
            "artifact_id": package["package_id"],
            "asset_id": package["world_id"],
            "type": "world_package",
            "backend": "godot",
            "backend_class": "PackedScene",
            "backend_path": package["scene_path"],
            "state": package["status"],
            "metadata": {**metadata, "registry_path": str(path)},
        }
