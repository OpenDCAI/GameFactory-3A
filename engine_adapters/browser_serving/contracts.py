"""Public contracts for the engine-agnostic browser serving adapter."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Mapping, Protocol


API_VERSION = "v1"


@dataclass(frozen=True)
class EngineCapabilities:
    asset_upload: bool = False
    asset_import: bool = False
    asset_inspection: bool = False
    world_build: bool = False
    world_catalog: bool = False
    runtime_sessions: bool = False
    skeletal_animation: bool = False
    pixel_streaming: bool = False
    preview_camera: bool = False

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)


@dataclass(frozen=True)
class EngineDescriptor:
    engine_id: str
    display_name: str
    capabilities: EngineCapabilities
    api_version: str = API_VERSION
    backend_kind: str = "example"

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_id": self.engine_id,
            "display_name": self.display_name,
            "api_version": self.api_version,
            "backend_kind": self.backend_kind,
            "capabilities": self.capabilities.to_dict(),
        }


@dataclass(frozen=True)
class AssetRecord:
    artifact_id: str
    asset_id: str
    artifact_type: str
    engine: str
    state: str = "ready"
    capabilities: dict[str, bool] = field(default_factory=dict)
    native: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AssetRecord":
        payload = dict(data)
        return cls(
            artifact_id=str(payload.get("artifact_id") or ""),
            asset_id=str(payload.get("asset_id") or ""),
            artifact_type=str(
                payload.get("artifact_type")
                or payload.get("type")
                or ""
            ),
            engine=str(
                payload.get("engine")
                or payload.get("backend")
                or ""
            ),
            state=str(payload.get("state") or "ready"),
            capabilities={
                str(key): bool(value)
                for key, value in dict(
                    payload.get("capabilities")
                    or payload.get("runtime_capabilities")
                    or {}
                ).items()
            },
            native=dict(payload.get("native") or {}),
            metadata=dict(payload.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "asset_id": self.asset_id,
            "artifact_type": self.artifact_type,
            "engine": self.engine,
            "state": self.state,
            "capabilities": dict(self.capabilities),
            "native": dict(self.native),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class WorldRecord:
    package_id: str
    world_id: str
    engine: str
    project_id: str = ""
    state: str = "published"
    manifest: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "WorldRecord":
        payload = dict(data)
        return cls(
            package_id=str(
                payload.get("package_id")
                or payload.get("artifact_id")
                or ""
            ),
            world_id=str(payload.get("world_id") or ""),
            engine=str(
                payload.get("engine")
                or payload.get("backend")
                or ""
            ),
            project_id=str(payload.get("project_id") or ""),
            state=str(
                payload.get("state")
                or payload.get("status")
                or "published"
            ),
            manifest=dict(payload.get("manifest") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "world_id": self.world_id,
            "project_id": self.project_id,
            "engine": self.engine,
            "state": self.state,
            "manifest": dict(self.manifest),
        }


@dataclass(frozen=True)
class StagedUpload:
    source_path: Path
    descriptor: dict[str, str]
    filename: str
    asset_type: str
    media_type: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_path": str(self.source_path),
            "descriptor": dict(self.descriptor),
            "filename": self.filename,
            "asset_type": self.asset_type,
            "media_type": self.media_type,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AssetImportRequest:
    engine: str
    asset_type: str
    descriptor: dict[str, str]
    source_path: Path
    destination: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class UploadSource(Protocol):
    filename: str
    file: BinaryIO
    content_type: str | None


class EngineBackend(Protocol):
    """Backend contract implemented by UE, Unity, Web, or future engines."""

    @property
    def descriptor(self) -> EngineDescriptor:
        ...

    def status(self) -> dict[str, Any]:
        ...

    def import_asset(
        self,
        request: AssetImportRequest,
    ) -> dict[str, Any]:
        ...

    def inspect_asset(self, artifact_id: str) -> dict[str, Any]:
        ...

    def list_assets(
        self,
        asset_type: str = "",
        *,
        root_uri: str = "",
    ) -> list[dict[str, Any]]:
        ...

    def list_worlds(
        self,
        *,
        project_id: str = "",
    ) -> list[dict[str, Any]]:
        ...

    def build_world(
        self,
        request: AssetImportRequest,
    ) -> dict[str, Any]:
        ...

    def create_session(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        ...

    def list_sessions(self) -> dict[str, Any]:
        ...

    def get_session(self, session_id: str) -> dict[str, Any]:
        ...

    def recover_session(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        ...

    def session_catalog(
        self,
        *,
        project_id: str = "",
    ) -> dict[str, Any]:
        ...

    def configure_session(
        self,
        session_id: str,
        character: Mapping[str, Any],
    ) -> dict[str, Any]:
        ...

    def play_preview_animation(
        self,
        session_id: str,
        animation: str,
        *,
        loop: bool = True,
        play_rate: float = 1.0,
    ) -> dict[str, Any]:
        ...

    def load_world(
        self,
        session_id: str,
        *,
        package_id: str = "",
        world_id: str = "",
        project_id: str = "",
    ) -> dict[str, Any]:
        ...

    def join_world(
        self,
        session_id: str,
        *,
        server_uri: str = "",
    ) -> dict[str, Any]:
        ...

    def leave_world(self, session_id: str) -> dict[str, Any]:
        ...

    def apply_input(
        self,
        session_id: str,
        input_state: Mapping[str, Any],
    ) -> dict[str, Any]:
        ...

    def apply_preview_camera(
        self,
        session_id: str,
        camera_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        ...

    def handle_runtime_event(
        self,
        session_id: str,
        event: str,
        *,
        world_name: str = "",
        entity_name: str = "",
    ) -> dict[str, Any]:
        ...

    def stop_session(self, session_id: str) -> dict[str, Any]:
        ...

    def debug(
        self,
        operation: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


class BrowserServingError(RuntimeError):
    pass


class UnknownEngineError(BrowserServingError):
    def __init__(self, engine_id: str) -> None:
        self.engine_id = engine_id
        super().__init__(f"Unknown browser serving engine: {engine_id}")


class EngineCapabilityError(BrowserServingError):
    def __init__(self, engine_id: str, capability: str) -> None:
        self.engine_id = engine_id
        self.capability = capability
        super().__init__(
            f"Engine '{engine_id}' does not support '{capability}'"
        )


def serving_result(
    operation: str,
    *,
    engine: str = "",
    ok: bool = True,
    payload: Mapping[str, Any] | None = None,
    artifacts: list[Mapping[str, Any]] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
) -> dict[str, Any]:
    """Create the stable result shape and retain v0 flat-field compatibility."""

    body = dict(payload or {})
    result: dict[str, Any] = {
        "ok": bool(ok),
        "operation": operation,
        "engine": engine,
        "artifacts": [dict(item) for item in artifacts or []],
        "warnings": [str(item) for item in warnings or []],
        "errors": [str(item) for item in errors or []],
        "payload": body,
    }
    for key, value in body.items():
        result.setdefault(key, value)
    return result


def normalize_backend_result(
    operation: str,
    engine: str,
    result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    value = dict(result or {})
    if {
        "ok",
        "operation",
        "artifacts",
        "warnings",
        "errors",
        "payload",
    }.issubset(value):
        value["engine"] = str(value.get("engine") or engine)
        return value
    reserved = {
        "ok",
        "operation",
        "engine",
        "artifacts",
        "warnings",
        "errors",
    }
    payload = {
        key: item
        for key, item in value.items()
        if key not in reserved
    }
    return serving_result(
        str(value.get("operation") or operation),
        engine=str(value.get("engine") or engine),
        ok=bool(value.get("ok", True)),
        payload=payload,
        artifacts=[
            dict(item)
            for item in value.get("artifacts") or []
            if isinstance(item, Mapping)
        ],
        warnings=[
            str(item)
            for item in value.get("warnings") or []
        ],
        errors=[
            str(item)
            for item in value.get("errors") or []
        ],
    )
