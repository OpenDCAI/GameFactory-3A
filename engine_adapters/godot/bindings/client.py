"""Stable material binding operations for GodotClient v1."""

from __future__ import annotations

import json
import math
import re
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from .._internal import (
    GodotTransport,
    atomic_write_bytes,
    atomic_write_text,
    source_descriptor,
    validate_managed_file,
)
from ..assets import GodotAssetsClient
from ..config import DEFAULT_TEXTURE_DEST, GodotClientConfig
from ..contracts import GodotOperationResult

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".svg", ".exr", ".hdr", ".ktx"}
BINDING_SCENE_DEST = "assets/imported/material_bindings"
APPLY_MATERIAL_SCRIPT = (
    Path(__file__).resolve().parents[1] / "_scripts" / "apply_material_binding.gd"
)


def _safe_name(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value)).strip("._") or "material"


def _snapshot_files(paths: list[Path]) -> dict[Path, bytes | None]:
    snapshots: dict[Path, bytes | None] = {}
    for path in dict.fromkeys(paths):
        validate_managed_file(path, label="Godot binding transaction target")
        snapshots[path] = path.read_bytes() if path.is_file() else None
    return snapshots


def _restore_files(snapshots: Mapping[Path, bytes | None]) -> list[str]:
    errors = []
    for path, content in snapshots.items():
        try:
            validate_managed_file(path, label="Godot binding rollback target")
            if content is None:
                if path.is_symlink() or path.is_file():
                    path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(
                    path,
                    content,
                    label="Godot binding rollback target",
                )
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")
    return errors


class GodotBindingsClient:
    def __init__(
        self,
        config: GodotClientConfig,
        assets: GodotAssetsClient,
    ) -> None:
        self._config = config
        self._assets = assets
        self._transport = GodotTransport(config)

    def bind_pbr_material(
        self,
        *,
        asset_id: str,
        source: Mapping[str, Any],
        mesh_assets: list[str],
        destination: str = "assets/imported/materials",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        operation = "bindings.bind_pbr_material"
        resolved = self._assets.resolve_source(source, asset_type="material")
        if not resolved.get("ok"):
            resolved["operation"] = operation
            return resolved
        source_path = Path(str(resolved["payload"].get("path") or ""))
        source_suffix = source_path.suffix.lower()
        if source_suffix not in {".tres", ".res", *IMAGE_SUFFIXES}:
            return GodotOperationResult.failure(
                operation,
                "PBR material source must be a .tres/.res material or an image texture",
                payload={"source_path": str(source_path)},
            ).to_dict()

        material_id = _safe_name(asset_id).lower()
        if not mesh_assets:
            return GodotOperationResult.failure(
                operation, "mesh_assets must contain at least one registered asset"
            ).to_dict()
        resolved_meshes = []
        seen_meshes: set[str] = set()
        try:
            for reference in mesh_assets:
                record = self._assets._registry.find(reference)
                if record is None or not record.spawnable:
                    return GodotOperationResult.failure(
                        operation,
                        f"Unknown or non-spawnable mesh asset: {reference}",
                    ).to_dict()
                if record.artifact_id not in seen_meshes:
                    seen_meshes.add(record.artifact_id)
                    resolved_meshes.append(record)
        except (OSError, TypeError, ValueError) as exc:
            return self._assets._registry_failure(
                operation,
                exc,
                payload={"mesh_assets": [str(item) for item in mesh_assets]},
            )

        resolved_options = dict(options or {})
        dry_run = bool(resolved_options.pop("dry_run", False))
        replace_existing = bool(resolved_options.pop("replace_existing", False))
        material_suffix = (
            source_suffix if source_suffix in {".tres", ".res"} else ".tres"
        )
        try:
            material_root, material_resource_root = self._assets._destination(
                destination
            )
            scene_root, scene_resource_root = self._assets._destination(
                BINDING_SCENE_DEST
            )
            material_path = material_root / f"{material_id}{material_suffix}"
            material_resource = (
                f"res://{material_resource_root}/{material_id}{material_suffix}"
            )
            mesh_bindings = []
            for record in resolved_meshes:
                source_resource = str(record.backend_path)
                source_file = self._resource_file(source_resource)
                target_name = (
                    f"{_safe_name(record.artifact_id).lower()}__{material_id}.tscn"
                )
                mesh_bindings.append(
                    {
                        "artifact_id": record.artifact_id,
                        "source_resource": source_resource,
                        "source_file": str(source_file),
                        "target_resource": f"res://{scene_resource_root}/{target_name}",
                        "target_file": str(scene_root / target_name),
                    }
                )
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()

        binding_path = self._config.data_root / "bindings" / f"{material_id}.json"
        tracked_paths = [
            material_path,
            binding_path,
            self._config.artifact_registry_path,
            *[Path(item["target_file"]) for item in mesh_bindings],
        ]
        import_source_paths = [material_path]
        if source_suffix in IMAGE_SUFFIXES:
            try:
                texture_root, _texture_resource_root = self._assets._destination(
                    DEFAULT_TEXTURE_DEST
                )
            except Exception as exc:
                return GodotOperationResult.failure(
                    operation, f"{type(exc).__name__}: {exc}"
                ).to_dict()
            texture_path = texture_root / f"{material_id}{source_suffix}"
            tracked_paths.append(texture_path)
            import_source_paths.append(texture_path)
        import_sidecar_paths = [
            Path(str(path) + ".import") for path in import_source_paths
        ]
        try:
            existing_import_cache_paths = self._assets._import_cache_paths(
                import_source_paths
            )
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        tracked_paths.extend(import_sidecar_paths)
        tracked_paths.extend(existing_import_cache_paths)

        try:
            for path in dict.fromkeys(tracked_paths):
                validate_managed_file(
                    path,
                    label="Godot binding transaction target",
                )
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()

        conflicts = [
            path
            for path in dict.fromkeys(
                [material_path, binding_path]
                + [Path(item["target_file"]) for item in mesh_bindings]
            )
            if path.exists() or path.is_symlink()
        ]
        payload: dict[str, Any] = {
            "asset_id": material_id,
            "source": source_descriptor(source),
            "source_path": str(source_path),
            "mesh_assets": [item.artifact_id for item in resolved_meshes],
            "material_path": str(material_path),
            "material_resource": material_resource,
            "binding_path": str(binding_path),
            "scene_bindings": [
                {
                    key: value
                    for key, value in item.items()
                    if key != "source_file" and key != "target_file"
                }
                for item in mesh_bindings
            ],
            "replace_existing": replace_existing,
            "dry_run": dry_run,
            "transaction_import_sidecar_targets": [
                str(path) for path in import_sidecar_paths
            ],
            "transaction_existing_import_cache_targets": [
                str(path) for path in existing_import_cache_paths
            ],
        }
        if conflicts and not replace_existing:
            payload["conflicts"] = [str(path) for path in conflicts]
            return GodotOperationResult.failure(
                operation,
                "Godot material binding target already exists: "
                + ", ".join(str(path) for path in conflicts),
                payload=payload,
            ).to_dict()
        try:
            snapshots = {} if dry_run else _snapshot_files(tracked_paths)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}", payload=payload
            ).to_dict()

        texture_record = None
        material_record = None
        warnings: list[str] = []
        if source_suffix in {".tres", ".res"}:
            imported = self._assets.import_material(
                source,
                destination=destination,
                options={
                    "asset_id": material_id,
                    "name": material_id + source_suffix,
                    "dry_run": dry_run,
                    "replace_existing": replace_existing,
                },
            )
            if not imported.get("ok"):
                imported["operation"] = operation
                return imported
            warnings.extend(str(item) for item in imported.get("warnings") or [])
            if imported.get("artifacts"):
                material_record = imported["artifacts"][0]
                material_resource = str(
                    material_record.get("backend_path") or material_resource
                )
                payload["material_resource"] = material_resource
        else:
            imported = self._assets.import_texture(
                source,
                options={
                    "asset_id": material_id + "_albedo",
                    "name": material_id + source_suffix,
                    "dry_run": dry_run,
                    "replace_existing": replace_existing,
                },
            )
            if not imported.get("ok"):
                imported["operation"] = operation
                return imported
            warnings.extend(str(item) for item in imported.get("warnings") or [])
            texture_record = (
                imported.get("artifacts", [None])[0]
                if imported.get("artifacts")
                else None
            )

        if dry_run:
            planned = [material_record] if isinstance(material_record, dict) else []
            planned.extend(
                {
                    "artifact_id": item["artifact_id"],
                    "type": "material_bound_scene",
                    "backend": "godot",
                    "backend_class": "PackedScene",
                    "backend_path": item["target_resource"],
                    "state": "planned",
                }
                for item in mesh_bindings
            )
            return GodotOperationResult.success(
                operation, artifacts=planned, warnings=warnings, payload=payload
            ).to_dict()

        updated_meshes = []
        try:
            if texture_record is not None:
                color = resolved_options.get("albedo_color", [1.0, 1.0, 1.0, 1.0])
                if not isinstance(color, (list, tuple)) or len(color) not in {3, 4}:
                    raise ValueError("options.albedo_color must contain 3 or 4 numbers")
                values = [float(value) for value in color]
                if len(values) == 3:
                    values.append(1.0)
                metallic = float(resolved_options.get("metallic", 0.0))
                roughness = float(resolved_options.get("roughness", 1.0))
                if not all(math.isfinite(value) for value in values):
                    raise ValueError("options.albedo_color values must be finite")
                if not math.isfinite(metallic) or not 0.0 <= metallic <= 1.0:
                    raise ValueError("options.metallic must be between 0 and 1")
                if not math.isfinite(roughness) or not 0.0 <= roughness <= 1.0:
                    raise ValueError("options.roughness must be between 0 and 1")
                texture_path = str(texture_record.get("backend_path") or "")
                atomic_write_text(
                    material_path,
                    "\n".join(
                        [
                            '[gd_resource type="StandardMaterial3D" load_steps=2 format=3]',
                            "",
                            f'[ext_resource type="Texture2D" path={json.dumps(texture_path)} id="1_albedo"]',
                            "",
                            "[resource]",
                            "albedo_color = Color("
                            + ", ".join(str(item) for item in values)
                            + ")",
                            'albedo_texture = ExtResource("1_albedo")',
                            f"metallic = {metallic}",
                            f"roughness = {roughness}",
                            "",
                        ]
                    ),
                    label="Godot generated material",
                )
                process = self._transport.run(
                    ["--import"], timeout=float(self._config.import_timeout)
                )
                payload["material_import_process"] = process.to_dict()
                if process.returncode != 0:
                    raise RuntimeError(
                        process.stderr.strip()
                        or process.stdout.strip()
                        or f"Godot import exited {process.returncode}"
                    )
                record = self._assets._register_resource(
                    resource_path=material_resource,
                    asset_type="material",
                    asset_id=material_id,
                    source_path=str(source_path),
                    backend_class="StandardMaterial3D",
                    metadata={"albedo_texture": texture_path},
                )
                material_record = record.to_dict()

            report = self._apply_to_scenes(material_resource, mesh_bindings)
            payload["apply_process"] = report.pop("process")
            payload["binding_report"] = report
            report_bindings = report.get("bindings") or []
            by_target = {
                str(item.get("target_resource") or ""): dict(item)
                for item in report_bindings
                if isinstance(item, Mapping)
            }
            if len(resolved_meshes) != len(mesh_bindings):
                raise RuntimeError(
                    "Godot material binding target count changed during validation"
                )
            for record, binding in zip(resolved_meshes, mesh_bindings):
                target_resource = str(binding["target_resource"])
                applied = by_target.get(target_resource)
                target_file = Path(binding["target_file"])
                if (
                    applied is None
                    or int(applied.get("mesh_instance_count") or 0) <= 0
                    or not target_file.is_file()
                ):
                    raise RuntimeError(
                        "Godot material binding did not produce a verified bound scene: "
                        + target_resource
                    )
                history = [
                    dict(item)
                    for item in record.metadata.get("material_bindings", [])
                    if isinstance(item, Mapping)
                    and str(item.get("asset_id") or "") != material_id
                ]
                binding_metadata = {
                    "asset_id": material_id,
                    "material": material_resource,
                    "source_resource": str(binding["source_resource"]),
                    "bound_resource": target_resource,
                    "mesh_instance_count": int(applied["mesh_instance_count"]),
                }
                updated_meshes.append(
                    replace(
                        record,
                        backend_path=target_resource,
                        backend_class="PackedScene",
                        metadata={
                            **record.metadata,
                            "material_bindings": [*history, binding_metadata],
                        },
                    )
                )

            atomic_write_text(
                binding_path,
                json.dumps(
                    {
                        "schema_version": "gamefactory3a.godot.material_binding.v1",
                        "asset_id": material_id,
                        "material": material_resource,
                        "mesh_bindings": [
                            {
                                "artifact_id": item.artifact_id,
                                "bound_resource": item.backend_path,
                                "mesh_instance_count": next(
                                    int(value.get("mesh_instance_count") or 0)
                                    for value in report_bindings
                                    if str(value.get("target_resource") or "")
                                    == item.backend_path
                                ),
                            }
                            for item in updated_meshes
                        ],
                        "options": resolved_options,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                label="Godot material binding record",
            )
            self._assets._registry.upsert_many(updated_meshes)
        except Exception as exc:
            rollback_discovery_errors = []
            try:
                current_import_cache_paths = self._assets._import_cache_paths(
                    import_source_paths
                )
            except Exception as cache_exc:
                current_import_cache_paths = []
                rollback_discovery_errors.append(
                    "import cache discovery failed: "
                    f"{type(cache_exc).__name__}: {cache_exc}"
                )
            for path in current_import_cache_paths:
                snapshots.setdefault(path, None)
            payload["transaction_rollback_import_cache_targets"] = [
                str(path) for path in current_import_cache_paths
            ]
            rollback_errors = _restore_files(snapshots)
            rollback_errors.extend(rollback_discovery_errors)
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}"
                + (
                    "; rollback failed: " + "; ".join(rollback_errors)
                    if rollback_errors
                    else ""
                ),
                payload=payload,
            ).to_dict()

        artifacts = [material_record] if isinstance(material_record, dict) else []
        artifacts.extend(item.to_dict() for item in updated_meshes)
        artifacts.append(
            {
                "type": "godot_material_binding",
                "path": str(binding_path),
                "state": "ready",
            }
        )
        return GodotOperationResult.success(
            operation,
            artifacts=artifacts,
            warnings=warnings,
            payload=payload,
        ).to_dict()

    def _resource_file(self, resource_path: str) -> Path:
        raw = str(resource_path or "").strip().replace("\\", "/")
        if not raw.startswith("res://"):
            raise ValueError(
                f"Godot mesh asset must use a project resource path: {resource_path}"
            )
        relative = PurePosixPath(raw[len("res://") :])
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise ValueError(f"Godot mesh resource path is unsafe: {resource_path}")
        project_dir = self._assets._project_dir()
        resolved = (project_dir / Path(*relative.parts)).resolve(strict=False)
        try:
            resolved.relative_to(project_dir)
        except ValueError as exc:
            raise ValueError(
                f"Godot mesh resource escaped the project: {resource_path}"
            ) from exc
        if not resolved.is_file():
            raise FileNotFoundError(
                f"Godot mesh resource was not found: {resource_path}"
            )
        return resolved

    def _apply_to_scenes(
        self,
        material_resource: str,
        mesh_bindings: list[dict[str, str]],
    ) -> dict[str, Any]:
        if not APPLY_MATERIAL_SCRIPT.is_file():
            raise FileNotFoundError(
                f"Godot material binding script was not found: {APPLY_MATERIAL_SCRIPT}"
            )
        with tempfile.TemporaryDirectory(prefix="a3game-godot-binding-") as temporary:
            temporary_root = Path(temporary)
            job_path = temporary_root / "job.json"
            report_path = temporary_root / "report.json"
            job_path.write_text(
                json.dumps(
                    {
                        "schema_version": "gamefactory3a.godot.material_binding_job.v1",
                        "material": material_resource,
                        "bindings": [
                            {
                                key: value
                                for key, value in item.items()
                                if key not in {"source_file", "target_file"}
                            }
                            for item in mesh_bindings
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            process = self._transport.run(
                ["--script", str(APPLY_MATERIAL_SCRIPT)],
                timeout=float(self._config.import_timeout),
                environment={
                    "A3GAME_GODOT_BINDING_JOB": str(job_path),
                    "A3GAME_GODOT_BINDING_REPORT": str(report_path),
                },
            )
            details = process.stderr.strip() or process.stdout.strip()
            if process.returncode != 0:
                raise RuntimeError(
                    "Godot material binding failed"
                    + (f": {details[-4000:]}" if details else "")
                )
            if not report_path.is_file():
                raise RuntimeError("Godot material binding produced no report")
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                raise RuntimeError(
                    f"Godot material binding report is invalid: {exc}"
                ) from exc
            if not isinstance(report, dict) or not report.get("ok"):
                errors = report.get("errors", []) if isinstance(report, dict) else []
                raise RuntimeError(
                    "Godot material binding report rejected the job"
                    + (": " + "; ".join(str(item) for item in errors) if errors else "")
                )
            report["process"] = process.to_dict()
            return report
