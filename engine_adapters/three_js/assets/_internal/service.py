"""Asset staging and manifest service for the three.js adapter."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ...config import (
    THREE_ASSET_TYPE_DEFAULT_DESTS,
    DEFAULT_IMPORT_ROOT,
    ThreeClientConfig,
)
from .artifacts import (
    ArtifactRecord,
    ArtifactRegistry,
    BACKEND,
    SPAWNABLE_BACKEND_CLASSES,
    artifact_id_for,
    asset_id_from_source,
    backend_class_for,
    normalize_artifact_type,
    normalize_backend_path,
)
from .inspectors import inspect_source
from .orientation import (
    RUNTIME_FORWARD_AXIS,
    apply_orientation_update,
    default_orientation,
    orientation_from_options,
    orientation_is_directional,
)

MANIFEST_RELATIVE_PATH = "assets/manifest.json"
MANIFEST_VERSION = 1


@dataclass(frozen=True)
class AssetValidation:
    ok: bool
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)


def _gltf_sidecar_files(source: Path) -> list[Path]:
    """Collect files referenced by a ``.gltf`` document."""

    if source.suffix.lower() != ".gltf":
        return []
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(document, dict):
        return []
    references: list[Path] = []
    for key in ("buffers", "images"):
        for item in document.get(key) or []:
            if not isinstance(item, dict):
                continue
            uri = item.get("uri")
            if not isinstance(uri, str) or not uri.strip():
                continue
            if uri.startswith("data:"):
                continue
            candidate = (source.parent / uri).resolve()
            try:
                candidate.relative_to(source.parent.resolve())
            except ValueError:
                continue
            if candidate.is_file():
                references.append(candidate)
    return sorted(set(references))


def _copy_file(source: Path, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    if (
        target.is_file()
        and target.stat().st_size == source.stat().st_size
        and target.read_bytes() == source.read_bytes()
    ):
        return False
    shutil.copy2(source, target)
    return True


def _copy_tree(source: Path, target: Path) -> list[Path]:
    copied: list[Path] = []
    for item in sorted(
        entry for entry in source.rglob("*") if entry.is_file()
    ):
        destination = target / item.relative_to(source)
        if _copy_file(item, destination):
            copied.append(destination)
    return copied


class AssetService:
    """Stage generated artifacts into a three.js project and register them."""

    def __init__(
        self,
        config: ThreeClientConfig,
        artifact_registry: ArtifactRegistry,
    ) -> None:
        self._config = config
        self.artifacts = artifact_registry

    @property
    def manifest_path(self) -> Path | None:
        public_root = self._config.public_root
        if public_root is None:
            return None
        return public_root / MANIFEST_RELATIVE_PATH

    def default_destination(self, asset_type: str) -> str:
        normalized = str(asset_type or "").strip().lower()
        return THREE_ASSET_TYPE_DEFAULT_DESTS.get(
            normalized,
            DEFAULT_IMPORT_ROOT,
        )

    def resolve_destination(
        self,
        asset_type: str,
        destination: str = "",
    ) -> str:
        resolved = str(destination or "").strip()
        if not resolved:
            resolved = self.default_destination(asset_type)
        return normalize_backend_path(resolved).strip("/")

    def import_asset(
        self,
        source_path: str,
        asset_type: str,
        *,
        dst_path: str = "",
        category: str = "",
        asset_id: str = "",
        replace_existing: bool = False,
        **options: Any,
    ) -> dict[str, Any]:
        """Stage one source artifact and register its web record."""

        public_root = self._config.public_root
        if public_root is None:
            raise ValueError(
                "project_path is not configured; a three.js project "
                "is required before importing assets"
            )
        source = Path(source_path)
        inspection = inspect_source(source, asset_type=asset_type)
        normalized_type = normalize_artifact_type(asset_type)
        destination = self.resolve_destination(asset_type, dst_path)
        explicit_asset_id = str(asset_id or "").strip()
        resolved_asset_id = (
            explicit_asset_id or asset_id_from_source(str(source))
        )
        # Name the staged file after the asset when the caller named the
        # asset. Generated task outputs are all called `model.glb` — that
        # is the artifact key, not an identity — so staging them under the
        # source name makes every generated prop overwrite the last one,
        # leaving a manifest whose entries all point at the same URL.
        # When no asset_id was supplied the source name *is* the identity,
        # and is kept.
        staged_name = (
            f"{resolved_asset_id}{source.suffix}"
            if explicit_asset_id and source.is_file()
            else source.name
        )

        target_root = public_root / destination
        warnings: list[str] = []
        staged: list[Path] = []
        if source.is_dir():
            target = target_root / resolved_asset_id
            if target.exists() and not replace_existing:
                warnings.append(
                    "Existing staged package was updated in place: "
                    f"{target}"
                )
            staged = _copy_tree(source, target)
            entry_points = list(inspection.get("entry_points") or [])
            entry = entry_points[0] if entry_points else ""
            runtime_relative = (
                f"{destination}/{resolved_asset_id}/{entry}"
                if entry
                else f"{destination}/{resolved_asset_id}"
            )
        else:
            target = target_root / staged_name
            if target.is_file() and not replace_existing:
                warnings.append(
                    f"Existing staged file was overwritten: {target}"
                )
            if _copy_file(source, target):
                staged.append(target)
            for sidecar in _gltf_sidecar_files(source):
                relative = sidecar.relative_to(source.parent)
                if _copy_file(sidecar, target_root / relative):
                    staged.append(target_root / relative)
            runtime_relative = f"{destination}/{staged_name}"

        backend_path = "/" + normalize_backend_path(runtime_relative)
        backend_class = backend_class_for(
            asset_type,
            str(inspection.get("representation") or ""),
            skinned=bool(inspection.get("skinned")),
        )
        spawnable = backend_class in SPAWNABLE_BACKEND_CLASSES

        # Orientation is parsed out of `options` rather than being left to
        # fall through as an opaque `option_*` hint: a facing axis that
        # only the metadata knows about cannot reach the runtime, and the
        # runtime is the only place that can act on it.
        orientation, remaining_options = orientation_from_options(
            options,
            asset_type=asset_type,
            inspection=inspection,
        )
        metadata = {
            "destination": destination,
            "staged_root": str(target_root),
            "staged_files": [str(item) for item in staged],
            "inspection": inspection,
        }
        if orientation_is_directional(normalized_type):
            metadata["orientation"] = orientation
            if (
                orientation.get("needs_vision_check")
                and not orientation.get("forward_axis")
            ):
                warnings.append(
                    "No forward_axis was declared for a directional "
                    f"{normalized_type}. glTF does not record facing, so "
                    "the runtime will place this model exactly as it was "
                    "authored — which is a coin flip. Render it with "
                    "three.preview.orientation_report and record the "
                    "answer with three.assets.set_orientation."
                )
        for key, value in remaining_options.items():
            metadata.setdefault(f"option_{key}", value)

        record = ArtifactRecord(
            artifact_id=artifact_id_for(
                BACKEND,
                backend_class,
                resolved_asset_id,
                backend_path,
            ),
            asset_id=resolved_asset_id,
            package_id=resolved_asset_id,
            type=normalized_type,
            category=str(category or ""),
            representation=str(
                inspection.get("representation") or ""
            ),
            backend=BACKEND,
            backend_class=backend_class,
            backend_path=backend_path,
            source_path=str(source),
            spawnable=spawnable,
            state="ready",
            runtime_capabilities={
                "renderable": bool(
                    inspection.get("web_ready", True)
                ),
                "spawnable": spawnable,
                "collidable": normalized_type == "environment",
                "playable": backend_class
                in {"AnimationClip", "Audio", "PositionalAudio"},
                "animated": bool(
                    inspection.get("animation_count") or 0
                ),
                "skinned": bool(inspection.get("skinned")),
            },
            metadata=metadata,
        )
        previous = self.artifacts.get(record.artifact_id)
        if previous and previous.metadata.get("material_bindings"):
            metadata["material_bindings"] = list(
                previous.metadata["material_bindings"]
            )
        self.artifacts.upsert(record)
        warnings.extend(self._supersede_stale(record))
        self.write_manifest()
        return {
            "ok": True,
            "artifacts": [record.to_dict()],
            "warnings": warnings,
            "errors": [],
            "asset_type": normalized_type,
            "src_path": str(source),
            "dest_path": destination,
            "backend_path": backend_path,
            "manifest_path": str(self.manifest_path or ""),
            "staged_file_count": len(staged),
            "inspection": inspection,
            "orientation": dict(metadata.get("orientation") or {}),
        }

    def _supersede_stale(self, record: Any) -> list[str]:
        """Drop older records of the same asset staged at another path.

        An ``asset_id`` names one asset in one project, but an artifact_id
        is derived partly from where the file was staged — so re-importing
        an asset to a different filename registers a *second* record
        instead of replacing the first. The project then carries two
        entries for one asset, and a runtime lookup takes whichever comes
        first, which may be the version nobody wanted.

        The stale staged file goes too. Nothing references it, and an
        orphaned mesh in ``public/`` is pure download weight in a shipped
        build.
        """

        warnings: list[str] = []
        public_root = self._config.public_root
        for existing in self.artifacts.list(type=record.type):
            if existing.artifact_id == record.artifact_id:
                continue
            if existing.asset_id != record.asset_id:
                continue
            self.artifacts.remove(existing.artifact_id)
            warnings.append(
                f"Superseded an earlier record for {record.asset_id!r} "
                f"staged at {existing.backend_path}"
            )
            if public_root is None:
                continue
            for staged in existing.metadata.get("staged_files") or []:
                path = Path(str(staged))
                try:
                    relative = path.relative_to(public_root)
                except ValueError:
                    continue  # never delete outside the project's public/
                if path.is_file() and str(relative) not in {
                    item.backend_path.lstrip("/")
                    for item in self.artifacts.list()
                }:
                    path.unlink()

        return warnings

    def set_orientation(
        self,
        artifact_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Record a facing decision against one registered artifact.

        Separate from ``import_asset`` on purpose. The decision usually
        arrives *after* the import, from whoever looked at the rendered
        views, and re-staging a5 MB mesh to write one string would be an
        absurd price for a fact about it.
        """

        record = self.artifacts.get(artifact_id)
        if record is None:
            raise ValueError(f"Unknown artifact_id: {artifact_id}")
        metadata = dict(record.metadata or {})
        existing = dict(metadata.get("orientation") or {}) or (
            default_orientation(
                asset_type=record.type,
                inspection=metadata.get("inspection"),
            )
        )
        orientation = apply_orientation_update(existing, updates)
        metadata["orientation"] = orientation
        updated = ArtifactRecord.from_dict(
            {**record.to_dict(), "metadata": metadata}
        )
        self.artifacts.upsert(updated)
        self.write_manifest()
        return {
            "artifact_id": updated.artifact_id,
            "asset_id": updated.asset_id,
            "type": updated.type,
            "orientation": orientation,
            "manifest_path": str(self.manifest_path or ""),
        }

    def get_orientation(self, artifact_id: str) -> dict[str, Any]:
        record = self.artifacts.get(artifact_id)
        if record is None:
            raise ValueError(f"Unknown artifact_id: {artifact_id}")
        metadata = dict(record.metadata or {})
        return {
            "artifact_id": record.artifact_id,
            "asset_id": record.asset_id,
            "type": record.type,
            "orientation": dict(metadata.get("orientation") or {})
            or default_orientation(
                asset_type=record.type,
                inspection=metadata.get("inspection"),
            ),
            "inspection": dict(metadata.get("inspection") or {}),
            "runtime_forward_axis": RUNTIME_FORWARD_AXIS,
        }

    def validate_asset(
        self,
        source_path: str,
        asset_type: str,
        *,
        dst_path: str = "",
        **options: Any,
    ) -> AssetValidation:
        """Validate one source artifact without staging it."""

        errors: list[str] = []
        warnings: list[str] = []
        source = Path(source_path)
        payload: dict[str, Any] = {
            "asset_type": normalize_artifact_type(asset_type),
            "source_path": str(source),
            "destination": self.resolve_destination(
                asset_type,
                dst_path,
            ),
        }
        try:
            inspection = inspect_source(source, asset_type=asset_type)
        except Exception as exc:
            return AssetValidation(
                ok=False,
                errors=(f"{type(exc).__name__}: {exc}",),
                payload=payload,
            )
        payload["inspection"] = inspection

        representation = str(inspection.get("representation") or "")
        if not inspection.get("web_ready", False) and not source.is_dir():
            warnings.append(
                f"Representation {representation!r} is not natively "
                "loadable by three.js; convert it to glTF/GLB, KTX2, "
                "or PNG before runtime use"
            )
        normalized_type = payload["asset_type"]
        if normalized_type == "avatar" and not inspection.get("skinned"):
            warnings.append(
                "Avatar artifact declares no skin; three.js will load "
                "it as a static Group instead of a SkinnedMesh"
            )
        if normalized_type == "motion" and not inspection.get(
            "animation_count"
        ):
            errors.append(
                "Motion artifact declares no glTF animation clip"
            )
        required = list(
            inspection.get("extensions_required") or []
        )
        unsupported = [
            item
            for item in required
            if item
            not in {
                "KHR_draco_mesh_compression",
                "EXT_meshopt_compression",
                "KHR_texture_basisu",
                "KHR_materials_unlit",
                "KHR_texture_transform",
                "KHR_lights_punctual",
                "KHR_materials_emissive_strength",
            }
        ]
        if unsupported:
            errors.append(
                "glTF requires extensions three.js cannot load: "
                + ", ".join(sorted(unsupported))
            )
        if inspection.get("draco_compressed"):
            warnings.append(
                "glTF is Draco compressed; the runtime must configure "
                "DRACOLoader with a decoder path"
            )
        if inspection.get("meshopt_compressed"):
            warnings.append(
                "glTF is meshopt compressed; the runtime must "
                "configure MeshoptDecoder"
            )
        if options:
            payload["options"] = dict(options)
        return AssetValidation(
            ok=not errors,
            warnings=tuple(warnings),
            errors=tuple(errors),
            payload=payload,
        )

    def list_assets(
        self,
        *,
        asset_type: str | None = None,
        root_path: str = DEFAULT_IMPORT_ROOT,
    ) -> list[dict[str, Any]]:
        """List staged asset files visible under the project public root."""

        public_root = self._config.public_root
        if public_root is None:
            raise ValueError("project_path is not configured")
        search_root = public_root / normalize_backend_path(
            root_path
        ).strip("/")
        if not search_root.is_dir():
            return []
        records_by_path = {
            record.backend_path: record
            for record in self.artifacts.list(type=asset_type)
        }
        results: list[dict[str, Any]] = []
        for item in sorted(
            entry for entry in search_root.rglob("*") if entry.is_file()
        ):
            relative = item.relative_to(public_root)
            url = "/" + normalize_backend_path(str(relative))
            record = records_by_path.get(url)
            if asset_type and record is None:
                continue
            results.append(
                {
                    "name": item.stem,
                    "path": url,
                    "type": record.type if record else "",
                    "class": record.backend_class if record else "",
                    "bytes": item.stat().st_size,
                    "registered": record is not None,
                }
            )
        return results

    def write_manifest(self) -> Path | None:
        """Write the runtime asset manifest consumed by the framework."""

        manifest_path = self.manifest_path
        if manifest_path is None:
            return None
        entries = {
            record.artifact_id: {
                "artifact_id": record.artifact_id,
                "asset_id": record.asset_id,
                "package_id": record.package_id,
                "type": record.type,
                "category": record.category,
                "representation": record.representation,
                "class": record.backend_class,
                "url": record.backend_path,
                "capabilities": dict(
                    record.runtime_capabilities or {}
                ),
                "animations": list(
                    (record.metadata.get("inspection") or {}).get(
                        "animations"
                    )
                    or []
                ),
                "bounds": dict(
                    (record.metadata.get("inspection") or {}).get(
                        "bounds"
                    )
                    or {}
                ),
                # The runtime reads this to turn an imported model until
                # it faces the way the game thinks it does. Absent means
                # "nobody recorded it", and the runtime then leaves the
                # model exactly as authored rather than guessing.
                "orientation": dict(
                    record.metadata.get("orientation") or {}
                ),
                "material_bindings": list(
                    record.metadata.get("material_bindings") or []
                ),
                "sun": dict(
                    record.metadata.get("sun")
                    or record.metadata.get("option_sun")
                    or {}
                ),
            }
            for record in self.artifacts.list()
        }
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(
                {
                    "manifest_version": MANIFEST_VERSION,
                    "api_version": self._config.api_version,
                    "backend": BACKEND,
                    "engine": "three_js",
                    "engine_version": self._config.engine_version,
                    "assets": entries,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return manifest_path

    def resolve_records(
        self,
        artifact_ids: Iterable[str],
    ) -> list[ArtifactRecord]:
        resolved: list[ArtifactRecord] = []
        for artifact_id in artifact_ids:
            record = self.artifacts.get(artifact_id)
            if record is None:
                raise ValueError(
                    f"Unknown artifact_id: {artifact_id}"
                )
            resolved.append(record)
        return resolved
