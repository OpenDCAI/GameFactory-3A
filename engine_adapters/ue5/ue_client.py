"""Stable Agent-facing facade for Unreal Engine environment operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ._internal.transport import (
    PythonRPCTransport,
    RemoteControlClient,
)
from .assets import UEAssetsClient
from .animation import UEAnimationClient
from .bindings import UEBindingsClient
from .build import UEBuildClient
from .config import DEFAULT_API_VERSION, UEClientConfig
from .observe import UEObserveClient
from .project import UEProjectClient
from .plugin import UEPluginClient
from .reflection import UEReflectionClient
from .runtime import UERuntimeClient
from .testing import UETestingClient
from .playtest import UEPlaytestClient
from .world import UEWorldClient


class UEClient:
    """
    Stable Unreal Engine environment API.

    Agent code must construct UEClient from `engine_adapters.ue5` and use its
    public namespace clients. Internal modules are version-specific and are
    not part of the API contract.
    """

    def __init__(
        self,
        project_path: str | Path | None = None,
        ue_root: str | Path | None = None,
        api_version: str = DEFAULT_API_VERSION,
        *,
        host: str | None = None,
        port: int | None = None,
        runtime_host: str | None = None,
        runtime_port: int | None = None,
        python_transport: str | None = None,
        python_plugin_path: str | Path | None = None,
    ) -> None:
        self._config = UEClientConfig.resolve(
            project_path=project_path,
            ue_root=ue_root,
            api_version=api_version,
            host=host,
            port=port,
            runtime_host=runtime_host,
            runtime_port=runtime_port,
            python_transport=python_transport,
            python_plugin_path=python_plugin_path,
        )
        remote_control = RemoteControlClient(
            self._config.remote_url
        )
        python_rpc = PythonRPCTransport(
            self._config,
            remote_control=remote_control,
        )

        self.project = UEProjectClient(self._config)
        self.build = UEBuildClient(self._config)
        self.testing = UETestingClient(self._config)
        self.playtest = UEPlaytestClient(self._config)
        self.plugin = UEPluginClient(self._config)
        self.assets = UEAssetsClient(
            self._config,
            python_rpc,
        )
        self.animation = UEAnimationClient(self.assets)
        self.bindings = UEBindingsClient(
            python_rpc,
            self.assets,
        )
        self.world = UEWorldClient(
            self._config,
            python_rpc,
            self.assets,
        )
        self.reflection = UEReflectionClient(
            python_rpc,
            self.assets,
        )
        self.runtime = UERuntimeClient(
            self._config,
            self.assets,
        )
        self.observe = UEObserveClient(
            self._config,
            remote_control,
            python_rpc,
        )

    @property
    def api_version(self) -> str:
        return self._config.api_version

    def get_environment_info(self) -> dict[str, Any]:
        info = self.project.get_info()
        info["operation"] = "client.get_environment_info"
        info["payload"]["remote_control_url"] = (
            self._config.remote_url
        )
        info["payload"]["python_transport"] = (
            self._config.python_transport
        )
        info["payload"]["runtime_input_host"] = (
            self._config.runtime_host
        )
        info["payload"]["runtime_input_port"] = (
            self._config.runtime_port
        )
        return info
