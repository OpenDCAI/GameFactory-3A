"""Stable generated-asset import operations for GodotClient v1."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

from .._internal import (
    ArtifactRecord,
    ArtifactRegistry,
    GeneratedAssetSourceResolver,
    GodotTransport,
    ResolvedAssetSource,
    godot_4_version_error,
    godot_import_error_lines,
    inspect_godot_resource,
    inspection_bone_tracks,
    inspection_skeleton_paths,
    source_descriptor,
    validate_regular_directory_tree,
    validate_resource_inspection,
)
from ..config import (
    DEFAULT_IMPORT_ROOT,
    GODOT_ASSET_TYPE_DEFAULT_DESTS,
    GodotClientConfig,
)
from ..contracts import GodotOperationResult

SUPPORTED_IMPORT_ASSET_TYPES = {
    "audio",
    "avatar",
    "effect",
    "environment",
    "material",
    "motion",
    "prop",
    "scene",
    "static_mesh",
    "texture",
    "weapon",
}
SUPPORTED_SUFFIXES = {
    "audio": {".wav", ".ogg", ".mp3"},
    "avatar": {".glb", ".gltf", ".fbx", ".blend", ".dae"},
    "effect": {".tscn", ".scn", ".res", ".tres", ".glb", ".gltf"},
    "environment": {".tscn", ".scn", ".glb", ".gltf", ".fbx"},
    "material": {".tres", ".res", ".png", ".jpg", ".jpeg", ".webp", ".exr", ".hdr"},
    "motion": {".glb", ".gltf", ".fbx", ".dae"},
    "prop": {".glb", ".gltf", ".fbx", ".blend", ".dae"},
    "scene": {".tscn", ".scn", ".glb", ".gltf", ".fbx"},
    "static_mesh": {".glb", ".gltf", ".fbx", ".blend", ".dae"},
    "texture": {".png", ".jpg", ".jpeg", ".webp", ".svg", ".exr", ".hdr", ".ktx"},
    "weapon": {".glb", ".gltf", ".fbx", ".blend", ".dae"},
}
TYPE_ALIASES = {"object": "prop", "animation": "motion", "world": "scene"}
SPAWNABLE_TYPES = {"avatar", "environment", "prop", "scene", "static_mesh", "weapon"}


def _normalize_type(asset_type: str) -> str:
    normalized = (
        str(asset_type or "").strip().lower().replace("-", "_").replace(" ", "_")
    )
    return TYPE_ALIASES.get(normalized, normalized)


def _safe_asset_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "_", value).strip("._")
    return cleaned or "asset"


class GodotAssetsClient:
    def __init__(self, config: GodotClientConfig) -> None:
        self._config = config
        self._sources = GeneratedAssetSourceResolver()
        self._transport = GodotTransport(config)
        self._registry = ArtifactRegistry(config.artifact_registry_path)

    def _registry_failure(
        self,
        operation: str,
        exc: BaseException,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return GodotOperationResult.failure(
            operation,
            f"Godot artifact registry read failed: {type(exc).__name__}: {exc}",
            payload={
                "registry_path": str(self._config.artifact_registry_path),
                **dict(payload or {}),
            },
        ).to_dict()

    def import_asset(
        self,
        source: Mapping[str, Any],
        asset_type: str,
        *,
        destination: str = "",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operation = "assets.import_asset"
        normalized_type = _normalize_type(asset_type)
        if normalized_type not in SUPPORTED_IMPORT_ASSET_TYPES:
            return GodotOperationResult.failure(
                operation,
                "Unsupported asset type: "
                + str(asset_type)
                + "; supported: "
                + ", ".join(sorted(SUPPORTED_IMPORT_ASSET_TYPES)),
            ).to_dict()
        allow_directory = normalized_type in {"effect", "environment", "scene"}
        try:
            resolved = self._sources.resolve(
                source,
                asset_type=normalized_type,
                allow_directory=allow_directory,
            )
            validation = self._validate_resolved(resolved, normalized_type)
            if validation:
                raise ValueError(validation)
            target_root, resource_root = self._destination(
                destination
                or GODOT_ASSET_TYPE_DEFAULT_DESTS.get(
                    normalized_type, DEFAULT_IMPORT_ROOT
                )
            )
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload={
                    "source": source_descriptor(source),
                    "asset_type": normalized_type,
                },
            ).to_dict()

        resolved_options = dict(options or {})
        dry_run = bool(resolved_options.pop("dry_run", False))
        replace_existing = bool(resolved_options.pop("replace_existing", False))
        name = _safe_asset_name(
            str(resolved_options.pop("name", "") or resolved.path.name)
        )
        if resolved.path.is_file() and not Path(name).suffix:
            name += resolved.path.suffix.lower()
        target = target_root / name
        resource_path = "res://" + (PurePosixPath(resource_root) / name).as_posix()
        payload: dict[str, Any] = {
            "source": resolved.descriptor(),
            "source_path": str(resolved.path),
            "asset_type": normalized_type,
            "destination": resource_root,
            "target_path": str(target),
            "resource_path": resource_path,
            "replace_existing": replace_existing,
            "dry_run": dry_run,
        }
        sidecars: list[tuple[Path, Path]] = []
        if resolved.path.is_dir():
            try:
                self._validate_source_tree(resolved.path)
            except Exception as exc:
                return GodotOperationResult.failure(
                    operation,
                    f"{type(exc).__name__}: {exc}",
                    payload=payload,
                ).to_dict()
        else:
            try:
                sidecars = self._gltf_sidecars(resolved.path, target)
            except Exception as exc:
                return GodotOperationResult.failure(
                    operation,
                    f"{type(exc).__name__}: {exc}",
                    payload=payload,
                ).to_dict()

        touched = [target, *(destination for _source, destination in sidecars)]
        try:
            existing_import_cache_paths = self._import_cache_paths(touched)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        managed_paths = list(dict.fromkeys(touched))
        import_sidecar_targets: list[Path] = []
        if resolved.path.is_file():
            import_sidecar_targets = [
                Path(str(path) + ".import") for path in list(managed_paths)
            ]
            managed_paths.extend(import_sidecar_targets)
        managed_paths.extend(existing_import_cache_paths)
        managed_paths = list(dict.fromkeys(managed_paths))
        try:
            self._validate_managed_paths(
                managed_paths,
                allowed_directories={target} if resolved.path.is_dir() else set(),
            )
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        conflicts = [
            path
            for path in dict.fromkeys(touched)
            if path.exists() or path.is_symlink()
        ]
        payload["sidecar_targets"] = [
            str(destination) for _source, destination in sidecars
        ]
        payload["import_sidecar_targets"] = [
            str(path) for path in import_sidecar_targets
        ]
        payload["existing_import_cache_targets"] = [
            str(path) for path in existing_import_cache_paths
        ]
        if conflicts and not replace_existing:
            payload["conflicts"] = [str(path) for path in conflicts]
            return GodotOperationResult.failure(
                operation,
                "Godot import target already exists: "
                + ", ".join(str(path) for path in conflicts),
                payload=payload,
            ).to_dict()
        if dry_run:
            return GodotOperationResult.success(
                operation,
                artifacts=[
                    {
                        "type": normalized_type,
                        "backend": "godot",
                        "backend_path": resource_path,
                        "state": "planned",
                    }
                ],
                payload=payload,
            ).to_dict()

        try:
            version_process = self._transport.version()
            payload["version_process"] = version_process.to_dict()
            if version_process.returncode != 0:
                details = (
                    version_process.stderr.strip() or version_process.stdout.strip()
                )
                raise RuntimeError(
                    "Godot --version failed"
                    + (f": {details[-4000:]}" if details else "")
                )
            engine_version = version_process.stdout.strip().splitlines()[0]
            payload["engine_version"] = engine_version
            version_error = godot_4_version_error(engine_version)
            if version_error:
                raise RuntimeError(version_error)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()

        backup_root: Path | None = None
        backups: dict[Path, Path] = {}
        created_directories = self._new_directories(
            managed_paths,
            self._project_dir(),
        )
        inspection: dict[str, Any] = {}
        backup_complete = False
        try:
            target_root.mkdir(parents=True, exist_ok=True)
            backup_root, backups = self._backup_existing(managed_paths)
            backup_complete = True
            if resolved.path.is_dir():
                shutil.copytree(resolved.path, target, symlinks=False)
                primary = self._directory_entrypoint(
                    target,
                    str(resolved_options.get("entrypoint") or ""),
                    normalized_type,
                )
                resource_path = (
                    "res://" + primary.relative_to(self._project_dir()).as_posix()
                )
            else:
                shutil.copy2(resolved.path, target)
                for sidecar_source, sidecar_destination in sidecars:
                    sidecar_destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(sidecar_source, sidecar_destination)
            process = self._transport.run(
                ["--import"],
                timeout=float(self._config.import_timeout),
            )
            payload["import_process"] = process.to_dict()
            payload["resource_path"] = resource_path
            if process.returncode != 0:
                details = process.stderr.strip() or process.stdout.strip()
                raise RuntimeError(
                    "Godot resource import failed"
                    + (f": {details[-4000:]}" if details else "")
                )
            import_errors = godot_import_error_lines(
                process.stdout + "\n" + process.stderr
            )
            if import_errors:
                raise RuntimeError(
                    "Godot resource import reported errors despite exit code 0: "
                    + " | ".join(import_errors)[-4000:]
                )
            inspection_process, inspection = self._inspect_native(resource_path)
            payload["inspection_process"] = inspection_process.to_dict()
            payload["inspection"] = inspection
            if inspection_process.returncode != 0 or not inspection.get("ok"):
                raise RuntimeError(
                    str(
                        inspection.get("error")
                        or "Godot could not load the imported resource"
                    )
                )
            requested_skeleton = self._requested_skeleton(
                normalized_type, resolved_options
            )
            inspection_errors = validate_resource_inspection(
                inspection,
                normalized_type,
                expected_skeleton=requested_skeleton,
            )
            if inspection_errors:
                raise RuntimeError("; ".join(inspection_errors))
        except Exception as exc:
            if backup_complete:
                try:
                    current_import_cache_paths = self._import_cache_paths(touched)
                except Exception:
                    current_import_cache_paths = []
                rollback_paths = list(
                    dict.fromkeys([*managed_paths, *current_import_cache_paths])
                )
                payload["rollback_import_cache_targets"] = [
                    str(path) for path in current_import_cache_paths
                ]
                self._rollback(
                    rollback_paths,
                    backups,
                    backup_root,
                    created_directories,
                )
            else:
                self._remove_created_directories(created_directories)
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()

        asset_id = _safe_asset_name(
            str(resolved_options.get("asset_id") or resolved.task_id or Path(name).stem)
        ).lower()
        backend_class = str(inspection.get("resource_class") or "Resource")
        requested_skeleton = self._requested_skeleton(normalized_type, resolved_options)
        skeleton_paths = inspection_skeleton_paths(inspection)
        track_skeleton_paths = list(
            dict.fromkeys(
                track["node_path"]
                for track in inspection_bone_tracks(inspection)
                if track.get("targets_skeleton_bone") and track.get("node_path")
            )
        )
        preferred_skeletons = (
            track_skeleton_paths if normalized_type == "motion" else skeleton_paths
        )
        actual_skeleton = (
            requested_skeleton
            if requested_skeleton in preferred_skeletons
            else (preferred_skeletons[0] if preferred_skeletons else "")
        )
        digest = hashlib.sha256(
            f"godot|{normalized_type}|{resource_path}".encode()
        ).hexdigest()[:10]
        record = ArtifactRecord(
            artifact_id=f"godot_{normalized_type}_{asset_id}_{digest}",
            asset_id=asset_id,
            type=normalized_type,
            backend_path=resource_path,
            source_path=str(resolved.path),
            backend_class=backend_class,
            spawnable=(
                normalized_type in SPAWNABLE_TYPES
                and bool(inspection.get("instantiable"))
            ),
            metadata={
                "source": resolved.descriptor(),
                "destination": resource_root,
                "skeleton": actual_skeleton,
                "skeleton_path": actual_skeleton,
                "requested_skeleton": requested_skeleton,
                "native_inspection": inspection,
                "options": {
                    str(key): value
                    for key, value in resolved_options.items()
                    if key not in {"entrypoint"}
                },
            },
        )
        try:
            self._registry.upsert(record)
        except Exception as exc:
            try:
                current_import_cache_paths = self._import_cache_paths(touched)
            except Exception:
                current_import_cache_paths = []
            rollback_paths = list(
                dict.fromkeys([*managed_paths, *current_import_cache_paths])
            )
            self._rollback(
                rollback_paths,
                backups,
                backup_root,
                created_directories,
            )
            return GodotOperationResult.failure(
                operation,
                f"Godot registry update failed; imported files were rolled back: {exc}",
                payload=payload,
            ).to_dict()
        self._discard_backup(backup_root)
        warnings = []
        if resolved.path.suffix.lower() == ".fbx":
            warnings.append(
                "FBX import depends on the selected Godot version; prefer glTF/GLB for portability"
            )
        return GodotOperationResult.success(
            operation,
            artifacts=[record.to_dict()],
            warnings=warnings,
            payload={**payload, "artifact_id": record.artifact_id},
        ).to_dict()

    def _inspect_native(self, resource_path: str):
        return inspect_godot_resource(
            self._transport,
            resource_path,
            timeout=float(self._config.import_timeout),
        )

    @staticmethod
    def _requested_skeleton(
        asset_type: str,
        options: Mapping[str, Any],
    ) -> str:
        if asset_type == "motion":
            return str(
                options.get("skeleton") or options.get("skeleton_artifact_id") or ""
            ).strip()
        if asset_type == "avatar":
            return str(options.get("skeleton_path") or "").strip()
        return ""

    def import_avatar(self, source: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self._typed_import("assets.import_avatar", source, "avatar", **kwargs)

    def import_motion(
        self,
        source: Mapping[str, Any],
        *,
        skeleton: str = "",
        destination: str = "",
        avatar_name: str = "",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_options = dict(options or {})
        if skeleton:
            resolved_options["skeleton"] = skeleton
        if avatar_name:
            resolved_options["avatar_name"] = avatar_name
        return self._typed_import(
            "assets.import_motion",
            source,
            "motion",
            destination=destination,
            options=resolved_options,
        )

    def import_scene(self, source: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self._typed_import("assets.import_scene", source, "scene", **kwargs)

    def import_prop(self, source: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self._typed_import("assets.import_prop", source, "prop", **kwargs)

    def import_weapon(self, source: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self._typed_import("assets.import_weapon", source, "weapon", **kwargs)

    def import_material(
        self, source: Mapping[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        return self._typed_import(
            "assets.import_material", source, "material", **kwargs
        )

    def import_texture(
        self, source: Mapping[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        return self._typed_import("assets.import_texture", source, "texture", **kwargs)

    def import_effect(self, source: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self._typed_import("assets.import_effect", source, "effect", **kwargs)

    def import_audio(self, source: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self._typed_import("assets.import_audio", source, "audio", **kwargs)

    def validate(
        self,
        source: Mapping[str, Any],
        asset_type: str,
        *,
        destination: str = "",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_type = _normalize_type(asset_type)
        try:
            if normalized_type not in SUPPORTED_IMPORT_ASSET_TYPES:
                raise ValueError(f"Unsupported asset type: {asset_type}")
            resolved = self._sources.resolve(
                source,
                asset_type=normalized_type,
                allow_directory=normalized_type in {"effect", "environment", "scene"},
            )
            error = self._validate_resolved(resolved, normalized_type)
            if error:
                raise ValueError(error)
            target_root, resource_root = self._destination(
                destination
                or GODOT_ASSET_TYPE_DEFAULT_DESTS.get(
                    normalized_type, DEFAULT_IMPORT_ROOT
                )
            )
            resolved_options = dict(options or {})
            name = _safe_asset_name(
                str(resolved_options.get("name") or resolved.path.name)
            )
            if resolved.path.is_file() and not Path(name).suffix:
                name += resolved.path.suffix.lower()
            target = target_root / name
            if resolved.path.is_dir():
                self._validate_source_tree(resolved.path)
                self._directory_entrypoint(
                    resolved.path,
                    str(resolved_options.get("entrypoint") or ""),
                    normalized_type,
                )
                sidecars: list[tuple[Path, Path]] = []
            else:
                sidecars = self._gltf_sidecars(resolved.path, target)
            touched = [target, *(item for _source, item in sidecars)]
            managed_paths = list(touched)
            if resolved.path.is_file():
                managed_paths.extend(Path(str(path) + ".import") for path in touched)
            managed_paths.extend(self._import_cache_paths(touched))
            self._validate_managed_paths(
                list(dict.fromkeys(managed_paths)),
                allowed_directories={target} if resolved.path.is_dir() else set(),
            )
        except Exception as exc:
            return GodotOperationResult.failure(
                "assets.validate",
                f"{type(exc).__name__}: {exc}",
                payload={
                    "source": source_descriptor(source),
                    "asset_type": normalized_type,
                },
            ).to_dict()
        return GodotOperationResult.success(
            "assets.validate",
            payload={
                "source": resolved.descriptor(),
                "source_path": str(resolved.path),
                "asset_type": normalized_type,
                "destination": resource_root,
                "destination_path": str(target_root),
                "target_path": str(target),
                "sidecar_targets": [str(item) for _source, item in sidecars],
            },
        ).to_dict()

    def resolve_source(
        self,
        source: Mapping[str, Any],
        *,
        asset_type: str = "",
    ) -> dict[str, Any]:
        try:
            resolved = self._sources.resolve(
                source,
                asset_type=_normalize_type(asset_type),
                allow_directory=_normalize_type(asset_type)
                in {"effect", "environment", "scene"},
            )
        except Exception as exc:
            return GodotOperationResult.failure(
                "assets.resolve_source",
                f"{type(exc).__name__}: {exc}",
                payload={"source": source_descriptor(source)},
            ).to_dict()
        return GodotOperationResult.success(
            "assets.resolve_source",
            payload={
                "source": resolved.descriptor(),
                "path": str(resolved.path),
                "meta_path": str(resolved.meta_path),
                "metadata": dict(resolved.metadata),
            },
        ).to_dict()

    def list(
        self,
        asset_type: str = "",
        *,
        root: str = DEFAULT_IMPORT_ROOT,
    ) -> dict[str, Any]:
        normalized_type = _normalize_type(asset_type)
        try:
            _target, normalized_root = self._destination(root)
        except Exception as exc:
            return GodotOperationResult.failure(
                "assets.list", f"{type(exc).__name__}: {exc}"
            ).to_dict()
        try:
            records = [
                record
                for record in self._registry.list(normalized_type)
                if record.backend_path.startswith(
                    "res://" + normalized_root.rstrip("/") + "/"
                )
                or record.backend_path == "res://" + normalized_root.rstrip("/")
            ]
        except (OSError, TypeError, ValueError) as exc:
            return self._registry_failure(
                "assets.list",
                exc,
                payload={"asset_type": normalized_type, "root": normalized_root},
            )
        return GodotOperationResult.success(
            "assets.list",
            artifacts=[item.to_dict() for item in records],
            payload={
                "asset_type": normalized_type,
                "root": normalized_root,
                "count": len(records),
            },
        ).to_dict()

    def list_registered(self, asset_type: str = "") -> dict[str, Any]:
        normalized_type = _normalize_type(asset_type)
        try:
            records = self._registry.list(normalized_type)
        except (OSError, TypeError, ValueError) as exc:
            return self._registry_failure(
                "assets.list_registered",
                exc,
                payload={"asset_type": normalized_type},
            )
        return GodotOperationResult.success(
            "assets.list_registered",
            artifacts=[item.to_dict() for item in records],
            payload={
                "asset_type": normalized_type,
                "count": len(records),
                "registry_path": str(self._config.artifact_registry_path),
            },
        ).to_dict()

    def get_metadata(self, artifact_id: str) -> dict[str, Any]:
        try:
            record = self._registry.get(artifact_id)
        except (OSError, TypeError, ValueError) as exc:
            return self._registry_failure(
                "assets.get_metadata",
                exc,
                payload={"artifact_id": str(artifact_id or "")},
            )
        if record is None:
            return GodotOperationResult.failure(
                "assets.get_metadata", f"Unknown artifact_id: {artifact_id}"
            ).to_dict()
        return GodotOperationResult.success(
            "assets.get_metadata", artifacts=[record.to_dict()]
        ).to_dict()

    def register_resource(
        self,
        *,
        resource_path: str,
        asset_type: str,
        asset_id: str,
        source_path: str = "",
        backend_class: str = "",
        spawnable: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register an existing project resource after native Godot validation."""

        operation = "assets.register_resource"
        normalized_type = _normalize_type(asset_type)
        payload: dict[str, Any] = {
            "resource_path": str(resource_path or ""),
            "asset_type": normalized_type,
            "asset_id": str(asset_id or ""),
        }
        try:
            if normalized_type not in SUPPORTED_IMPORT_ASSET_TYPES:
                raise ValueError(f"Unsupported Godot asset type: {asset_type}")
            if not str(asset_id or "").strip():
                raise ValueError("asset_id must not be empty")
            if spawnable is not None and not isinstance(spawnable, bool):
                raise TypeError("spawnable must be a boolean when provided")
            resource_file, normalized_resource_path = self._existing_resource_file(
                resource_path
            )
            payload.update(
                {
                    "resource_path": normalized_resource_path,
                    "resource_file": str(resource_file),
                }
            )

            version_process = self._transport.version()
            payload["version_process"] = version_process.to_dict()
            if version_process.returncode != 0:
                details = (
                    version_process.stderr.strip() or version_process.stdout.strip()
                )
                raise RuntimeError(
                    "Godot --version failed"
                    + (f": {details[-4000:]}" if details else "")
                )
            engine_version = version_process.stdout.strip().splitlines()[0]
            payload["engine_version"] = engine_version
            version_error = godot_4_version_error(engine_version)
            if version_error:
                raise RuntimeError(version_error)

            inspection_process, inspection = self._inspect_native(
                normalized_resource_path
            )
            payload["inspection_process"] = inspection_process.to_dict()
            payload["inspection"] = inspection
            inspection_errors = validate_resource_inspection(
                inspection,
                normalized_type,
            )
            if inspection_process.returncode != 0 and not inspection_errors:
                inspection_errors = [
                    str(
                        inspection.get("error")
                        or f"Godot inspection exited {inspection_process.returncode}"
                    )
                ]
            if inspection_errors:
                raise ValueError("; ".join(inspection_errors))

            inspected_class = str(inspection.get("resource_class") or "").strip()
            claimed_class = str(backend_class or "").strip()
            if claimed_class and claimed_class != inspected_class:
                raise ValueError(
                    "backend_class does not match Godot inspection: "
                    f"{claimed_class!r} != {inspected_class!r}"
                )
            inspected_spawnable = normalized_type in SPAWNABLE_TYPES and bool(
                inspection.get("instantiable")
            )
            if spawnable is not None and spawnable is not inspected_spawnable:
                raise ValueError(
                    "spawnable does not match Godot inspection: "
                    f"{spawnable!r} != {inspected_spawnable!r}"
                )
            skeleton_paths = inspection_skeleton_paths(inspection)
            track_skeleton_paths = list(
                dict.fromkeys(
                    track["node_path"]
                    for track in inspection_bone_tracks(inspection)
                    if track.get("targets_skeleton_bone") and track.get("node_path")
                )
            )
            preferred_skeletons = (
                track_skeleton_paths if normalized_type == "motion" else skeleton_paths
            )
            actual_skeleton = preferred_skeletons[0] if preferred_skeletons else ""
            record_metadata = {
                **dict(metadata or {}),
                "native_inspection": inspection,
            }
            if normalized_type in {"avatar", "motion"}:
                record_metadata.update(
                    {
                        "skeleton": actual_skeleton,
                        "skeleton_path": actual_skeleton,
                    }
                )
            record = self._register_resource(
                resource_path=normalized_resource_path,
                asset_type=normalized_type,
                asset_id=asset_id,
                source_path=source_path,
                backend_class=inspected_class,
                spawnable=inspected_spawnable,
                metadata=record_metadata,
            )
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        return GodotOperationResult.success(
            operation,
            artifacts=[record.to_dict()],
            payload={**payload, "artifact_id": record.artifact_id},
        ).to_dict()

    def _register_resource(
        self,
        *,
        resource_path: str,
        asset_type: str,
        asset_id: str,
        source_path: str = "",
        backend_class: str = "Resource",
        spawnable: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ArtifactRecord:
        """Write a record already validated by an adapter-owned transaction."""

        normalized_type = _normalize_type(asset_type)
        if normalized_type not in SUPPORTED_IMPORT_ASSET_TYPES:
            raise ValueError(f"Unsupported Godot asset type: {asset_type}")
        if spawnable is not None and not isinstance(spawnable, bool):
            raise TypeError("spawnable must be a boolean when provided")
        digest = hashlib.sha256(
            f"godot|{normalized_type}|{resource_path}".encode()
        ).hexdigest()[:10]
        normalized_id = _safe_asset_name(asset_id).lower()
        return self._registry.upsert(
            ArtifactRecord(
                artifact_id=f"godot_{normalized_type}_{normalized_id}_{digest}",
                asset_id=normalized_id,
                type=normalized_type,
                backend_path=resource_path,
                source_path=source_path,
                backend_class=backend_class,
                spawnable=(
                    normalized_type in SPAWNABLE_TYPES
                    if spawnable is None
                    else spawnable
                ),
                metadata=dict(metadata or {}),
            )
        )

    def _typed_import(
        self,
        operation: str,
        source: Mapping[str, Any],
        asset_type: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        result = self.import_asset(source, asset_type, **kwargs)
        result["operation"] = operation
        return result

    def _project_dir(self) -> Path:
        project_dir = self._config.project_dir
        project_file = self._config.project_file
        if project_dir is None or project_file is None or not project_file.is_file():
            raise FileNotFoundError(
                "project_path does not resolve to an existing project.godot"
            )
        return project_dir.resolve()

    def _existing_resource_file(self, value: str) -> tuple[Path, str]:
        raw = str(value or "").strip()
        if not raw.startswith("res://"):
            raise ValueError("Godot resource_path must use res://")
        relative_text = raw[len("res://") :]
        relative = PurePosixPath(relative_text)
        if (
            not relative_text
            or "\\" in relative_text
            or "\x00" in relative_text
            or "?" in relative_text
            or "#" in relative_text
            or relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != relative_text
        ):
            raise ValueError(
                "Godot resource_path must be a canonical, non-traversing res:// path"
            )
        project_dir = self._project_dir()
        candidate = project_dir / Path(*relative.parts)
        self._assert_no_symlink_components(candidate, project_dir)
        if not candidate.exists():
            raise FileNotFoundError(f"Godot resource was not found: {raw}")
        if not candidate.is_file():
            raise ValueError(f"Godot resource must be a regular file: {raw}")
        resolved = candidate.resolve(strict=True)
        try:
            resolved.relative_to(project_dir)
        except ValueError as exc:
            raise ValueError(f"Godot resource escaped the project: {raw}") from exc
        return resolved, "res://" + relative.as_posix()

    def _destination(self, value: str) -> tuple[Path, str]:
        raw = str(value or "").strip().replace("\\", "/")
        if raw.startswith("res://"):
            raw = raw[len("res://") :]
        path = PurePosixPath(raw)
        if not raw or path.is_absolute() or ".." in path.parts:
            raise ValueError(
                "Godot destination must be a non-traversing project-relative or res:// path"
            )
        normalized = path.as_posix().strip("/")
        project_dir = self._project_dir()
        raw_target = project_dir / Path(*path.parts)
        self._assert_no_symlink_components(raw_target, project_dir)
        target = raw_target.resolve(strict=False)
        try:
            target.relative_to(project_dir)
        except ValueError as exc:
            raise ValueError("Godot destination escaped the project") from exc
        return target, normalized

    @staticmethod
    def _validate_resolved(
        resolved: ResolvedAssetSource,
        asset_type: str,
    ) -> str:
        if resolved.path.is_dir():
            return (
                ""
                if asset_type in {"effect", "environment", "scene"}
                else (f"{asset_type} source must be a file")
            )
        suffix = resolved.path.suffix.lower()
        supported = SUPPORTED_SUFFIXES[asset_type]
        if suffix not in supported:
            return (
                f"Unsupported {asset_type} format {suffix or '<none>'}; "
                f"supported: {', '.join(sorted(supported))}"
            )
        if resolved.path.stat().st_size <= 0:
            return f"Generated asset is empty: {resolved.path}"
        return ""

    def _directory_entrypoint(
        self,
        target: Path,
        entrypoint: str,
        asset_type: str,
    ) -> Path:
        if entrypoint:
            relative = PurePosixPath(entrypoint.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("entrypoint must stay inside the imported directory")
            selected = target / Path(*relative.parts)
            if not selected.is_file():
                raise FileNotFoundError(f"Asset entrypoint was not found: {selected}")
            return selected
        candidates = sorted(
            item
            for item in target.rglob("*")
            if item.is_file() and item.suffix.lower() in SUPPORTED_SUFFIXES[asset_type]
        )
        if len(candidates) != 1:
            raise ValueError(
                "Directory asset requires options.entrypoint when it contains "
                f"{len(candidates)} supported resource files"
            )
        return candidates[0]

    @staticmethod
    def _validate_source_tree(source: Path) -> None:
        validate_regular_directory_tree(source, label="Directory import source")

    @staticmethod
    def _gltf_sidecars(source: Path, target: Path) -> list[tuple[Path, Path]]:
        if source.suffix.lower() != ".gltf":
            return []
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ValueError(f"glTF JSON is invalid: {source}") from exc
        uris = []
        for collection in (payload.get("buffers", []), payload.get("images", [])):
            for item in collection:
                if isinstance(item, dict) and isinstance(item.get("uri"), str):
                    uris.append(item["uri"])
        sidecars: list[tuple[Path, Path]] = []
        seen_destinations: set[Path] = set()
        for uri in uris:
            parsed_uri = urlsplit(uri)
            if parsed_uri.scheme or parsed_uri.netloc:
                continue
            if parsed_uri.query or parsed_uri.fragment:
                raise ValueError(
                    f"glTF sidecar URI has unsupported query/fragment: {uri}"
                )
            decoded_uri = unquote(parsed_uri.path, errors="strict")
            if "\x00" in decoded_uri:
                raise ValueError(f"glTF sidecar URI is unsafe: {uri}")
            relative = PurePosixPath(decoded_uri.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"glTF sidecar URI is unsafe: {uri}")
            sidecar = (source.parent / Path(*relative.parts)).resolve()
            try:
                sidecar.relative_to(source.parent.resolve())
            except ValueError as exc:
                raise ValueError(
                    f"glTF sidecar escaped its source directory: {uri}"
                ) from exc
            if not sidecar.is_file():
                raise FileNotFoundError(f"glTF sidecar was not found: {sidecar}")
            raw_destination = target.parent / Path(*relative.parts)
            GodotAssetsClient._assert_no_symlink_components(
                raw_destination,
                target.parent,
            )
            destination = raw_destination.resolve(strict=False)
            try:
                destination.relative_to(target.parent.resolve())
            except ValueError as exc:
                raise ValueError(
                    f"glTF sidecar escaped its destination: {uri}"
                ) from exc
            if destination not in seen_destinations:
                seen_destinations.add(destination)
                sidecars.append((sidecar, destination))
        return sidecars

    @staticmethod
    def _assert_no_symlink_components(path: Path, boundary: Path) -> None:
        """Reject existing symlinks before resolving a project-managed path."""

        try:
            relative = path.relative_to(boundary)
        except ValueError as exc:
            raise ValueError(f"Godot managed path escaped the project: {path}") from exc
        current = boundary
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(
                    f"Godot managed path must not contain a symlink: {current}"
                )

    @staticmethod
    def _validate_managed_paths(
        paths: list[Path],
        *,
        allowed_directories: set[Path],
    ) -> None:
        """Ensure Godot cannot write through links or special filesystem nodes."""

        for path in dict.fromkeys(paths):
            if path.is_symlink():
                raise ValueError(f"Godot managed path must not be a symlink: {path}")
            if not path.exists():
                continue
            if path in allowed_directories and path.is_dir():
                continue
            if not path.is_file():
                raise ValueError(f"Godot managed path must be a regular file: {path}")

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path, ignore_errors=True)

    @classmethod
    def _backup_existing(
        cls,
        paths: list[Path],
    ) -> tuple[Path, dict[Path, Path]]:
        backup_root = Path(tempfile.mkdtemp(prefix="a3game-godot-import-"))
        backups: dict[Path, Path] = {}
        try:
            for index, path in enumerate(dict.fromkeys(paths)):
                if not path.exists() and not path.is_symlink():
                    continue
                backup = backup_root / str(index)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(backup))
                backups[path] = backup
        except Exception:
            for path, backup in backups.items():
                path.parent.mkdir(parents=True, exist_ok=True)
                if backup.exists() or backup.is_symlink():
                    shutil.move(str(backup), str(path))
            cls._discard_backup(backup_root)
            raise
        return backup_root, backups

    @classmethod
    def _rollback(
        cls,
        touched: list[Path],
        backups: dict[Path, Path],
        backup_root: Path | None,
        created_directories: tuple[Path, ...] = (),
    ) -> None:
        for path in sorted(
            dict.fromkeys(touched), key=lambda item: len(item.parts), reverse=True
        ):
            cls._remove_path(path)
        for path, backup in backups.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists() or backup.is_symlink():
                shutil.move(str(backup), str(path))
        cls._remove_created_directories(created_directories)
        cls._discard_backup(backup_root)

    @staticmethod
    def _remove_created_directories(directories: tuple[Path, ...]) -> None:
        for directory in sorted(
            directories,
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass

    @staticmethod
    def _new_directories(
        paths: list[Path],
        boundary: Path,
    ) -> tuple[Path, ...]:
        directories: set[Path] = set()
        for path in paths:
            try:
                path.relative_to(boundary)
            except ValueError as exc:
                raise ValueError(
                    f"Godot import path escaped the project: {path}"
                ) from exc
            current = path.parent
            while current != boundary and not current.exists():
                try:
                    current.relative_to(boundary)
                except ValueError as exc:
                    raise ValueError(
                        f"Godot import directory escaped the project: {current}"
                    ) from exc
                directories.add(current)
                current = current.parent
        return tuple(sorted(directories, key=lambda item: len(item.parts)))

    def _import_cache_paths(self, paths: list[Path]) -> list[Path]:
        project_dir = self._project_dir()
        raw_cache_root = project_dir / ".godot" / "imported"
        self._assert_no_symlink_components(raw_cache_root, project_dir)
        cache_root = raw_cache_root.resolve(strict=False)
        try:
            cache_root.relative_to(project_dir)
        except ValueError as exc:
            raise ValueError("Godot import cache escaped the project") from exc
        if not cache_root.is_dir():
            return []
        sources: list[Path] = []
        for path in paths:
            if path.is_dir():
                sources.extend(
                    Path(str(item)[: -len(".import")])
                    for item in path.rglob("*.import")
                    if item.is_file()
                )
            else:
                sources.append(path)
        cache_paths: list[Path] = []
        for source in dict.fromkeys(sources):
            try:
                relative = source.resolve(strict=False).relative_to(project_dir)
            except ValueError as exc:
                raise ValueError(
                    f"Godot imported source escaped the project: {source}"
                ) from exc
            resource_path = "res://" + relative.as_posix()
            digest = hashlib.md5(resource_path.encode()).hexdigest()
            prefix = f"{source.name}-{digest}."
            for item in cache_root.iterdir():
                if not item.name.startswith(prefix):
                    continue
                if item.is_symlink():
                    raise ValueError(
                        f"Godot import cache target must not be a symlink: {item}"
                    )
                if not item.is_file():
                    raise ValueError(
                        f"Godot import cache target must be a regular file: {item}"
                    )
                cache_paths.append(item)
        return list(dict.fromkeys(cache_paths))

    @staticmethod
    def _discard_backup(backup_root: Path | None) -> None:
        if backup_root is not None:
            shutil.rmtree(backup_root, ignore_errors=True)
