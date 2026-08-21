"""Stable Agent-facing facade for Godot Engine environment operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .animation import GodotAnimationClient
from .assets import GodotAssetsClient
from .bindings import GodotBindingsClient
from .build import GodotBuildClient
from .config import DEFAULT_API_VERSION, GodotClientConfig
from .observe import GodotObserveClient
from .plugin import GodotPluginClient
from .project import GodotProjectClient
from .reflection import GodotReflectionClient
from .runtime import GodotRuntimeClient
from .testing import GodotTestingClient
from .world import GodotWorldClient


class GodotClient:
    """Stable Godot 4 environment API with the shared adapter namespaces."""

    def __init__(
        self,
        project_path: str | Path | None = None,
        godot_executable: str | Path | None = None,
        api_version: str = DEFAULT_API_VERSION,
        *,
        runtime_host: str | None = None,
        runtime_port: int | None = None,
        editor_timeout: int | None = None,
        import_timeout: int | None = None,
    ) -> None:
        self._config = GodotClientConfig.resolve(
            project_path=project_path,
            godot_executable=godot_executable,
            api_version=api_version,
            runtime_host=runtime_host,
            runtime_port=runtime_port,
            editor_timeout=editor_timeout,
            import_timeout=import_timeout,
        )
        self.project = GodotProjectClient(self._config)
        self.build = GodotBuildClient(self._config)
        self.testing = GodotTestingClient(self._config)
        self.plugin = GodotPluginClient(self._config)
        self.assets = GodotAssetsClient(self._config)
        self.animation = GodotAnimationClient(self.assets)
        self.bindings = GodotBindingsClient(self._config, self.assets)
        self.reflection = GodotReflectionClient(self._config, self.assets)
        self.world = GodotWorldClient(self._config, self.assets, self.reflection)
        self.runtime = GodotRuntimeClient(self._config, self.assets)
        self.observe = GodotObserveClient(self._config, self.runtime.sessions)

    @property
    def api_version(self) -> str:
        return self._config.api_version

    def get_environment_info(self, *, probe_version: bool = True) -> dict[str, Any]:
        info = self.project.get_info(probe_version=probe_version)
        info["operation"] = "client.get_environment_info"
        info["payload"].update(
            {
                "runtime_host": self._config.runtime_host,
                "runtime_port": self._config.runtime_port,
                "artifact_registry_path": str(self._config.artifact_registry_path),
                "world_registry_root": str(self._config.world_registry_root),
            }
        )
        return info
