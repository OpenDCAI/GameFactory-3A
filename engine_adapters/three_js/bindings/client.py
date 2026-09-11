"""Stable material and asset binding operations for ThreeClient v1."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..assets import ThreeAssetsClient
from ..assets._internal.artifacts import ArtifactRecord
from ..assets._internal.inspectors import classify_suffix
from ..config import ThreeClientConfig
from ..contracts import ThreeOperationResult


BINDINGS_RELATIVE_ROOT = "assets/bindings"

PBR_TEXTURE_SLOTS = (
    "map",
    "normalMap",
    "roughnessMap",
    "metalnessMap",
    "aoMap",
    "emissiveMap",
    "alphaMap",
    "displacementMap",
    "clearcoatMap",
    "sheenColorMap",
)
PBR_SCALAR_SLOTS = (
    "roughness",
    "metalness",
    "clearcoat",
    "clearcoatRoughness",
    "sheen",
    "sheenRoughness",
    "transmission",
    "ior",
    "iridescence",
    "emissiveIntensity",
    "normalScale",
    "aoMapIntensity",
    "envMapIntensity",
    "opacity",
)
PBR_COLOR_SLOTS = ("color", "emissive", "sheenColor", "attenuationColor")
SUPPORTED_MATERIAL_TYPES = (
    "MeshStandardMaterial",
    "MeshPhysicalMaterial",
    "MeshBasicMaterial",
    "MeshLambertMaterial",
    "MeshPhongMaterial",
    "MeshMatcapMaterial",
    "MeshToonMaterial",
)
TEXTURE_SUFFIX_BY_SLOT = {
    "map": ("_basecolor", "_albedo", "_diffuse", "_color"),
    "normalMap": ("_normal", "_nrm"),
    "roughnessMap": ("_roughness", "_rough"),
    "metalnessMap": ("_metallic", "_metalness", "_metal"),
    "aoMap": ("_ao", "_occlusion"),
    "emissiveMap": ("_emissive", "_emission"),
    "alphaMap": ("_alpha", "_opacity"),
    "displacementMap": ("_height", "_displacement"),
}


def _infer_slot(name: str) -> str:
    lowered = name.lower()
    for slot, markers in TEXTURE_SUFFIX_BY_SLOT.items():
        if any(marker in lowered for marker in markers):
            return slot
    return ""


class ThreeBindingsClient:
    """Create runtime material bindings applied after asset load."""

    def __init__(
        self,
        config: ThreeClientConfig,
        assets: ThreeAssetsClient,
    ) -> None:
        self._config = config
        self._assets = assets

    def bind_pbr_material(
        self,
        *,
        asset_id: str,
        source: Mapping[str, Any],
        mesh_assets: list[str],
        destination: str = "",
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Bind a PBR texture set to registered mesh artifacts."""

        public_root = self._config.public_root
        if public_root is None:
            return ThreeOperationResult.failure(
                "bindings.bind_pbr_material",
                "project_path is not configured",
            ).to_dict()
        try:
            resolved = self._assets._resolve_source(
                source,
                "material",
                allow_directory=True,
            )
        except Exception as exc:
            return ThreeOperationResult.failure(
                "bindings.bind_pbr_material",
                f"{type(exc).__name__}: {exc}",
                payload={"source": dict(source)},
            ).to_dict()

        config = dict(options or {})
        material_type = str(
            config.pop("type", "MeshStandardMaterial")
        )
        if material_type not in SUPPORTED_MATERIAL_TYPES:
            supported = ", ".join(SUPPORTED_MATERIAL_TYPES)
            return ThreeOperationResult.failure(
                "bindings.bind_pbr_material",
                f"Unsupported three.js material type "
                f"{material_type!r}; supported: {supported}",
                payload={"source": resolved.descriptor()},
            ).to_dict()

        registry = self._assets._service.artifacts
        warnings: list[str] = []
        target_paths: list[str] = []
        for artifact_id in mesh_assets:
            record = registry.get(artifact_id)
            if record is None:
                return ThreeOperationResult.failure(
                    "bindings.bind_pbr_material",
                    f"Unknown artifact_id: {artifact_id}",
                    payload={"source": resolved.descriptor()},
                ).to_dict()
            if not record.runtime_capabilities.get("renderable"):
                warnings.append(
                    f"Artifact {artifact_id} is not renderable; the "
                    "binding will be ignored at runtime"
                )
            target_paths.append(record.backend_path)

        textures, texture_warnings = self._stage_textures(
            resolved.path,
            asset_id=asset_id,
            destination=destination,
            explicit=config,
        )
        warnings.extend(texture_warnings)

        binding = {
            "binding_version": 1,
            "asset_id": str(asset_id),
            "material_type": material_type,
            "targets": target_paths,
            "textures": textures,
            "scalars": {
                key: config[key]
                for key in PBR_SCALAR_SLOTS
                if key in config
            },
            "colors": {
                key: config[key]
                for key in PBR_COLOR_SLOTS
                if key in config
            },
            "flags": {
                key: config[key]
                for key in (
                    "transparent",
                    "side",
                    "flatShading",
                    "wireframe",
                    "depthWrite",
                    "vertexColors",
                )
                if key in config
            },
            "source": resolved.descriptor(),
        }
        ignored = sorted(
            set(config)
            - set(PBR_SCALAR_SLOTS)
            - set(PBR_COLOR_SLOTS)
            - set(PBR_TEXTURE_SLOTS)
            - {
                "transparent",
                "side",
                "flatShading",
                "wireframe",
                "depthWrite",
                "vertexColors",
            }
        )
        if ignored:
            warnings.append(
                "Ignored material options: " + ", ".join(ignored)
            )

        binding_path = (
            public_root
            / BINDINGS_RELATIVE_ROOT
            / f"{asset_id}.json"
        )
        binding_url = "/" + f"{BINDINGS_RELATIVE_ROOT}/{asset_id}.json"
        try:
            binding_path.parent.mkdir(parents=True, exist_ok=True)
            binding_path.write_text(
                json.dumps(binding, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            updates = []
            for record in registry.list():
                metadata = dict(record.metadata or {})
                previous = list(metadata.get("material_bindings") or [])
                bindings = [url for url in previous if url != binding_url]
                if record.backend_path in target_paths:
                    bindings.append(binding_url)
                if bindings != previous:
                    metadata["material_bindings"] = bindings
                    updates.append(ArtifactRecord.from_dict(
                        {**record.to_dict(), "metadata": metadata}
                    ))
            registry.upsert_many(updates)
            self._assets._service.write_manifest()
        except Exception as exc:
            return ThreeOperationResult.failure(
                "bindings.bind_pbr_material",
                f"{type(exc).__name__}: {exc}",
                payload={"source": resolved.descriptor()},
            ).to_dict()

        return ThreeOperationResult.success(
            "bindings.bind_pbr_material",
            artifacts=[
                {
                    "type": "web_material_binding",
                    "path": str(binding_path),
                    "state": "ready",
                }
            ],
            warnings=warnings,
            payload={
                **binding,
                "binding_path": str(binding_path),
                "binding_url": "/"
                + f"{BINDINGS_RELATIVE_ROOT}/{asset_id}.json",
            },
        ).to_dict()

    def _stage_textures(
        self,
        source_path: Path,
        *,
        asset_id: str,
        destination: str,
        explicit: dict[str, Any],
    ) -> tuple[dict[str, str], list[str]]:
        """Stage texture files and map them to three.js material slots."""

        warnings: list[str] = []
        textures: dict[str, str] = {}
        for slot in PBR_TEXTURE_SLOTS:
            value = explicit.pop(slot, None)
            if isinstance(value, str) and value.strip():
                textures[slot] = value.strip()

        candidates = (
            sorted(
                item
                for item in source_path.rglob("*")
                if item.is_file()
            )
            if source_path.is_dir()
            else [source_path]
        )
        resolved_destination = self._assets._service.resolve_destination(
            "texture",
            destination,
        )
        public_root = self._config.public_root
        assert public_root is not None
        target_root = public_root / resolved_destination / asset_id
        for candidate in candidates:
            family, _ = classify_suffix(candidate)
            if family != "texture":
                continue
            slot = _infer_slot(candidate.stem)
            relative = (
                f"{resolved_destination}/{asset_id}/{candidate.name}"
            )
            target = target_root / candidate.name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(candidate.read_bytes())
            if not slot:
                warnings.append(
                    "Texture slot could not be inferred from file "
                    f"name: {candidate.name}"
                )
                continue
            textures.setdefault(slot, "/" + relative)
        if not textures:
            warnings.append(
                "No PBR textures were bound; the material will use "
                "scalar and color values only"
            )
        return textures, warnings
