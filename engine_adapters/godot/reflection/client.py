"""Stable Godot resource reflection operations for GodotClient v1."""

from __future__ import annotations

from typing import Any

from .._internal import GodotTransport, inspect_godot_resource
from ..assets import GodotAssetsClient
from ..config import GodotClientConfig
from ..contracts import GodotOperationResult


class GodotReflectionClient:
    def __init__(
        self,
        config: GodotClientConfig,
        assets: GodotAssetsClient,
    ) -> None:
        self._config = config
        self._assets = assets
        self._transport = GodotTransport(config)

    def inspect_artifact(
        self,
        artifact_id: str,
        *,
        live: bool = True,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        operation = "reflection.inspect_artifact"
        try:
            record = self._assets._registry.get(artifact_id)
        except (OSError, TypeError, ValueError) as exc:
            return self._assets._registry_failure(
                operation,
                exc,
                payload={"artifact_id": str(artifact_id or ""), "live": live},
            )
        if record is None:
            return GodotOperationResult.failure(
                operation, f"Unknown artifact_id: {artifact_id}"
            ).to_dict()
        payload: dict[str, Any] = {"artifact": record.to_dict(), "live": live}
        if not live:
            return GodotOperationResult.success(
                operation, artifacts=[record.to_dict()], payload=payload
            ).to_dict()
        try:
            result, inspection = inspect_godot_resource(
                self._transport,
                record.backend_path,
                timeout=timeout,
            )
            payload["process"] = result.to_dict()
            payload["inspection"] = inspection
            if result.returncode != 0 or not inspection.get("ok"):
                return GodotOperationResult.failure(
                    operation,
                    str(
                        inspection.get("error")
                        or f"Godot reflection exited {result.returncode}"
                    ),
                    payload=payload,
                ).to_dict()
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        return GodotOperationResult.success(
            operation, artifacts=[record.to_dict()], payload=payload
        ).to_dict()
