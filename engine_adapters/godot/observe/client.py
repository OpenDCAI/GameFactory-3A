"""Stable environment readiness probes for GodotClient v1."""

from __future__ import annotations

import math
from typing import Any

from .._internal import (
    GodotTransport,
    find_godot_binary,
    godot_4_version_error,
    parse_godot_version,
)
from ..config import GodotClientConfig
from ..contracts import GodotDiagnostic, GodotOperationResult
from ..runtime.sessions import GodotRuntimeSessionsClient


class GodotObserveClient:
    def __init__(
        self,
        config: GodotClientConfig,
        sessions: GodotRuntimeSessionsClient,
    ) -> None:
        self._config = config
        self._sessions = sessions
        self._transport = GodotTransport(config)

    def check_status(
        self,
        *,
        timeout: float = 5.0,
        check_runtime: bool = False,
    ) -> dict[str, Any]:
        operation = "observe.check_status"
        try:
            normalized_timeout = float(timeout)
            if not math.isfinite(normalized_timeout):
                raise ValueError("timeout must be a finite number")
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
            ).to_dict()
        binary = find_godot_binary(self._config.godot_executable)
        project_file = self._config.project_file
        binary_exists = bool(binary and binary.is_file())
        project_exists = bool(project_file and project_file.is_file())
        version = ""
        version_result: dict[str, Any] = {}
        if binary_exists:
            try:
                process = self._transport.version(timeout=max(1.0, normalized_timeout))
                version_result = process.to_dict()
                if process.returncode == 0:
                    version = process.stdout.strip().splitlines()[0]
            except Exception as exc:
                version_result = {"error": f"{type(exc).__name__}: {exc}"}
        runtime = (
            self._sessions.probe(min(max(normalized_timeout, 0.05), 2.0))
            if check_runtime
            else {
                "checked": False,
                "reachable": False,
            }
        )
        runtime_ready = bool(runtime.get("ok")) if check_runtime else True
        errors = []
        diagnostics: list[GodotDiagnostic] = []
        if not binary_exists:
            errors.append("Godot editor binary was not found")
            diagnostics.append(
                GodotDiagnostic(
                    severity="warning",
                    code="GODOT_EDITOR_BINARY_NOT_FOUND",
                    message="Set A3GAME_GODOT_EXECUTABLE or add godot4/godot to PATH",
                    source="observe",
                )
            )
        elif not version:
            errors.append("Godot version probe failed")
        else:
            version_error = godot_4_version_error(version)
            if version_error:
                errors.append(version_error)
        if not project_exists:
            errors.append("Godot project.godot was not found")
        if check_runtime and not runtime_ready:
            errors.append("Godot A3GameRuntime bridge is not reachable")
        return GodotOperationResult(
            operation=operation,
            ok=not errors,
            diagnostics=tuple(diagnostics),
            errors=tuple(errors),
            payload={
                "api_version": self._config.api_version,
                "editor_binary_exists": binary_exists,
                "editor_binary_path": str(binary) if binary else "",
                "engine_version": version,
                "engine_version_major": ((parse_godot_version(version) or (None,))[0]),
                "engine_version_supported": bool(
                    version and not godot_4_version_error(version)
                ),
                "version_result": version_result,
                "project_path_exists": project_exists,
                "project_path": str(self._config.project_dir or ""),
                "project_file": str(project_file or ""),
                "runtime_checked": check_runtime,
                "runtime_ready": runtime_ready,
                "runtime": runtime,
                "runtime_host": self._config.runtime_host,
                "runtime_port": self._config.runtime_port,
            },
        ).to_dict()
