"""Application service delegating browser operations to engine backends."""

from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO, Mapping

from .config import BrowserServingConfig
from .contracts import (
    AssetImportRequest,
    EngineCapabilityError,
    normalize_backend_result,
    serving_result,
)
from .registry import EngineRegistry
from .storage import UploadStore


class BrowserServingService:
    def __init__(
        self,
        registry: EngineRegistry,
        config: BrowserServingConfig,
        *,
        uploads: UploadStore | None = None,
    ) -> None:
        self.registry = registry
        self.config = config
        self.uploads = uploads or UploadStore(config)

    def health(self) -> dict[str, Any]:
        return serving_result(
            "browser.health",
            payload={
                "service": "aaagameforge-browser-serving",
                "api_version": "v1",
                "engines": [
                    backend.descriptor.engine_id
                    for backend in self.registry.list()
                ],
            },
        )

    def list_engines(self) -> dict[str, Any]:
        return {
            "engines": [
                backend.descriptor.to_dict()
                for backend in self.registry.list()
            ]
        }

    def engine_status(self, engine: str) -> dict[str, Any]:
        backend = self.registry.resolve(engine)
        return normalize_backend_result(
            "engine.status",
            backend.descriptor.engine_id,
            backend.status(),
        )

    def stage_and_import(
        self,
        stream: BinaryIO,
        *,
        filename: str,
        asset_type: str,
        engine: str,
        media_type: str = "",
        destination: str = "",
        options: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        game_id: str = "",
        run_id: str = "",
        task_id: str = "",
        artifact_key: str = "",
    ) -> dict[str, Any]:
        backend = self._require(engine, "asset_import")
        staged = self.uploads.stage(
            stream,
            filename=filename,
            asset_type=asset_type,
            media_type=media_type,
            game_id=game_id,
            run_id=run_id,
            task_id=task_id,
            artifact_key=artifact_key,
            metadata=dict(metadata or {}),
        )
        request = AssetImportRequest(
            engine=backend.descriptor.engine_id,
            asset_type=staged.asset_type,
            descriptor=dict(staged.descriptor),
            source_path=staged.source_path,
            destination=destination,
            options=dict(options or {}),
            metadata=dict(metadata or {}),
        )
        result = (
            backend.build_world(request)
            if staged.asset_type in {"scene", "world"}
            else backend.import_asset(request)
        )
        normalized = normalize_backend_result(
            "assets.upload",
            backend.descriptor.engine_id,
            result,
        )
        normalized["payload"].setdefault("upload", staged.to_dict())
        normalized.setdefault("upload", staged.to_dict())
        return normalized

    def import_descriptor(
        self,
        *,
        engine: str,
        asset_type: str,
        descriptor: Mapping[str, Any],
        source_path: str | Path = "",
        destination: str = "",
        options: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        backend = self._require(engine, "asset_import")
        request = AssetImportRequest(
            engine=backend.descriptor.engine_id,
            asset_type=str(asset_type or "").strip().lower(),
            descriptor={
                str(key): str(value)
                for key, value in dict(descriptor or {}).items()
                if str(value).strip()
            },
            source_path=Path(source_path).expanduser().resolve(
                strict=False
            )
            if str(source_path or "").strip()
            else Path(),
            destination=destination,
            options=dict(options or {}),
            metadata=dict(metadata or {}),
        )
        result = (
            backend.build_world(request)
            if request.asset_type in {"scene", "world"}
            else backend.import_asset(request)
        )
        return normalize_backend_result(
            "assets.import",
            backend.descriptor.engine_id,
            result,
        )

    def list_assets(
        self,
        engine: str,
        *,
        asset_type: str = "",
        root_uri: str = "",
    ) -> list[dict[str, Any]]:
        backend = self._require(engine, "asset_inspection")
        return [
            dict(item)
            for item in backend.list_assets(
                asset_type,
                root_uri=root_uri,
            )
        ]

    def asset_groups(
        self,
        engine: str,
        *,
        root_uri: str = "",
    ) -> dict[str, list[dict[str, Any]]]:
        return {
            asset_type: self.list_assets(
                engine,
                asset_type=asset_type,
                root_uri=root_uri,
            )
            for asset_type in (
                "avatar",
                "skeleton",
                "motion",
                "environment",
                "prop",
                "weapon",
                "effect",
                "material",
                "texture",
            )
        }

    def inspect_asset(
        self,
        engine: str,
        artifact_id: str,
    ) -> dict[str, Any]:
        backend = self._require(engine, "asset_inspection")
        return normalize_backend_result(
            "assets.inspect",
            backend.descriptor.engine_id,
            backend.inspect_asset(artifact_id),
        )

    def list_worlds(
        self,
        engine: str,
        *,
        project_id: str = "",
    ) -> list[dict[str, Any]]:
        backend = self._require(engine, "world_catalog")
        return [
            dict(item)
            for item in backend.list_worlds(project_id=project_id)
        ]

    def call_session(
        self,
        engine: str,
        method: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        backend = self._require(engine, "runtime_sessions")
        function = getattr(backend, method)
        return normalize_backend_result(
            f"sessions.{method}",
            backend.descriptor.engine_id,
            function(*args, **kwargs),
        )

    def debug(
        self,
        engine: str,
        operation: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        backend = self.registry.resolve(engine)
        return normalize_backend_result(
            f"debug.{operation}",
            backend.descriptor.engine_id,
            backend.debug(operation, payload),
        )

    def viewer_config(self, engine: str) -> dict[str, Any]:
        backend = self.registry.resolve(engine)
        result = self.debug(engine, "viewer_config")
        result["payload"].setdefault(
            "gateway_url",
            self.config.public_gateway_url,
        )
        result.setdefault(
            "gateway_url",
            self.config.public_gateway_url,
        )
        result.setdefault(
            "engine_id",
            backend.descriptor.engine_id,
        )
        return result

    def _require(self, engine: str, capability: str):
        backend = self.registry.resolve(engine)
        capabilities = backend.descriptor.capabilities.to_dict()
        if not capabilities.get(capability, False):
            raise EngineCapabilityError(
                backend.descriptor.engine_id,
                capability,
            )
        return backend
