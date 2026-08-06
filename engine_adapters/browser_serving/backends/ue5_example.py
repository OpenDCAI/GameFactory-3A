"""Unreal Engine example backend implemented only through public UEClient."""

from __future__ import annotations

import socket
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from threading import RLock
from time import sleep, time
from typing import Any, Callable, Mapping
from uuid import uuid4

from engine_adapters.ue5 import UEClient

from ..config import BrowserServingConfig
from ..contracts import (
    AssetImportRequest,
    AssetRecord,
    EngineCapabilities,
    EngineDescriptor,
    WorldRecord,
    serving_result,
)
from ._pixel_streaming import start_signalling_server


def _is_tcp_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex((host, int(port))) != 0


def _is_udp_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.bind((host, int(port)))
            return True
        except OSError:
            return False


def _http_reachable(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return int(response.status) < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _payload(result: Mapping[str, Any]) -> dict[str, Any]:
    return dict(result.get("payload") or {})


def _errors(result: Mapping[str, Any]) -> list[str]:
    return [str(item) for item in result.get("errors") or []]


def _normalize_asset(
    item: Mapping[str, Any],
    *,
    asset_type: str = "",
) -> dict[str, Any]:
    value = dict(item)
    if value.get("native"):
        return AssetRecord.from_dict(value).to_dict()
    primary = dict(value.get("primary_asset") or {})
    backend_path = str(
        value.get("backend_path")
        or value.get("path")
        or primary.get("path")
        or ""
    )
    backend_class = str(
        value.get("backend_class")
        or value.get("class")
        or primary.get("class")
        or ""
    )
    metadata = dict(value.get("metadata") or {})
    skeleton_path = str(metadata.get("skeleton_path") or "")
    if not skeleton_path:
        for dependency in metadata.get("dependencies") or []:
            if (
                isinstance(dependency, Mapping)
                and str(dependency.get("type") or "") == "skeleton"
            ):
                assets = dependency.get("assets") or []
                if assets:
                    skeleton_path = str(assets[0])
                    break
    if skeleton_path:
        metadata["skeleton_path"] = skeleton_path
    resolved_type = str(
        value.get("artifact_type")
        or value.get("type")
        or asset_type
        or ""
    )
    return AssetRecord(
        artifact_id=str(
            value.get("artifact_id")
            or backend_path
        ),
        asset_id=str(
            value.get("asset_id")
            or value.get("name")
            or backend_path.rsplit("/", 1)[-1]
        ),
        artifact_type=resolved_type,
        engine="ue5",
        state=str(value.get("state") or "ready"),
        capabilities={
            str(key): bool(item)
            for key, item in dict(
                value.get("runtime_capabilities") or {}
            ).items()
        },
        native={
            "backend": "ue",
            "class": backend_class,
            "path": backend_path,
            "primary_asset": primary,
            "editor": dict(value.get("editor_backend") or {}),
            "runtime": dict(value.get("runtime") or {}),
        },
        metadata=metadata,
    ).to_dict()


def _normalize_world(item: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(item)
    metadata = dict(value.get("metadata") or {})
    package = (
        metadata
        if metadata.get("package_id")
        else value
    )
    return WorldRecord(
        package_id=str(
            package.get("package_id")
            or value.get("artifact_id")
            or ""
        ),
        world_id=str(package.get("world_id") or ""),
        project_id=str(package.get("project_id") or ""),
        engine="ue5",
        state=str(
            package.get("status")
            or value.get("state")
            or "published"
        ),
        manifest=dict(package.get("manifest") or {}),
    ).to_dict()


@dataclass
class UE5BrowserSession:
    session_id: str
    participant_id: str
    user_id: str
    state: str
    pixel_http_port: int
    pixel_streamer_port: int
    pixel_sfu_port: int
    input_port: int
    remote_control_port: int
    pixel_streaming_url: str
    ue_input_host: str
    preview_map: str
    client: Any = field(repr=False)
    character: dict[str, Any] = field(default_factory=dict)
    controller_id: str = ""
    entity_id: str = ""
    world_id: str = ""
    project_id: str = ""
    runtime_package_id: str = ""
    world_map_path: str = ""
    ue_client_pid: int = 0
    signalling_pid: int = 0
    signalling_process: subprocess.Popen | None = field(
        default=None,
        repr=False,
    )
    created_at: float = field(default_factory=time)
    updated_at: float = field(default_factory=time)
    error: str = ""
    last_command: str = ""
    recovered_external: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "participant_id": self.participant_id,
            "user_id": self.user_id,
            "state": self.state,
            "pixel_http_port": self.pixel_http_port,
            "pixel_streamer_port": self.pixel_streamer_port,
            "pixel_sfu_port": self.pixel_sfu_port,
            "input_port": self.input_port,
            "ue_input_host": self.ue_input_host,
            "ue_input_port": self.input_port,
            "remote_control_port": self.remote_control_port,
            "pixel_streaming_url": self.pixel_streaming_url,
            "preview_map": self.preview_map,
            "character": dict(self.character),
            "controller_id": self.controller_id,
            "entity_id": self.entity_id,
            "world_id": self.world_id,
            "project_id": self.project_id,
            "runtime_package_id": self.runtime_package_id,
            "world_map_path": self.world_map_path,
            "ue_client_pid": self.ue_client_pid,
            "signalling_pid": self.signalling_pid,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "error": self.error,
            "last_command": self.last_command,
            "recovered_external": self.recovered_external,
        }


class UE5ExampleBackend:
    """First backend example for the engine-agnostic serving contract."""

    def __init__(
        self,
        config: BrowserServingConfig,
        *,
        client_factory: Callable[..., Any] = UEClient,
        signalling_factory: Callable[..., subprocess.Popen] = (
            start_signalling_server
        ),
        http_reachable: Callable[[str, float], bool] = _http_reachable,
    ) -> None:
        self.config = config
        self._client_factory = client_factory
        self._signalling_factory = signalling_factory
        self._http_reachable = http_reachable
        self._sessions: dict[str, UE5BrowserSession] = {}
        self._lock = RLock()
        self._multiplayer_slots: list[dict[str, Any]] = [
            {"slot": 0},
            {"slot": 1},
        ]
        self._descriptor = EngineDescriptor(
            engine_id="ue5",
            display_name="Unreal Engine 5 Example",
            backend_kind="example_backend",
            capabilities=EngineCapabilities(
                asset_upload=True,
                asset_import=True,
                asset_inspection=True,
                world_build=True,
                world_catalog=True,
                runtime_sessions=True,
                skeletal_animation=True,
                pixel_streaming=True,
                preview_camera=True,
            ),
        )
        self._admin_client = self._new_client(
            remote_port=config.ue_remote_port,
            runtime_port=config.base_runtime_port,
        )

    @property
    def descriptor(self) -> EngineDescriptor:
        return self._descriptor

    def _new_client(self, *, remote_port: int, runtime_port: int):
        return self._client_factory(
            project_path=self.config.ue_project,
            ue_root=self.config.ue_root,
            host=self.config.ue_host,
            port=remote_port,
            runtime_host=self.config.runtime_host,
            runtime_port=runtime_port,
        )

    def status(self) -> dict[str, Any]:
        environment = self._admin_client.get_environment_info()
        observation = self._admin_client.observe.check_status(
            check_python=False
        )
        configured = (
            self.config.ue_project is not None
            and self.config.ue_root is not None
        )
        return serving_result(
            "engine.status",
            engine="ue5",
            ok=configured,
            payload={
                "configured": configured,
                "environment": environment,
                "observation": observation,
                "remote_control": _payload(observation).get(
                    "remote_control",
                    {},
                ),
                "python_execution": _payload(observation).get(
                    "python_execution",
                    {},
                ),
                "message": (
                    "UE5 backend configured"
                    if configured
                    else "Set A3GAME_UE_PROJECT and A3GAME_UE_ROOT"
                ),
            },
            warnings=[] if configured else [
                "UE5 example backend is not configured"
            ],
        )

    def import_asset(
        self,
        request: AssetImportRequest,
    ) -> dict[str, Any]:
        asset_type = request.asset_type
        source = dict(request.descriptor)
        options = dict(request.options)
        if asset_type == "motion":
            skeleton = str(
                options.pop("skeleton", "")
                or options.pop("skeleton_path", "")
                or request.metadata.get("skeleton_path", "")
            )
            result = self._admin_client.assets.import_motion(
                source,
                skeleton=skeleton,
                destination=request.destination,
                options=options,
            )
        else:
            method_name = {
                "avatar": "import_avatar",
                "effect": "import_effect",
                "material": "import_material",
                "prop": "import_prop",
                "object": "import_prop",
                "static_mesh": "import_prop",
                "texture": "import_texture",
                "weapon": "import_weapon",
            }.get(asset_type, "import_asset")
            method = getattr(self._admin_client.assets, method_name)
            if method_name == "import_asset":
                result = method(
                    source,
                    asset_type,
                    destination=request.destination,
                    options=options,
                )
            else:
                result = method(
                    source,
                    destination=request.destination,
                    options=options,
                )
        return self._translate_ue_result(
            "assets.import",
            result,
            asset_type=asset_type,
        )

    def build_world(
        self,
        request: AssetImportRequest,
    ) -> dict[str, Any]:
        options = dict(request.options)
        result = self._admin_client.world.build(
            request.descriptor,
            options=options,
        )
        translated = self._translate_ue_result(
            "worlds.build",
            result,
            asset_type="scene",
        )
        translated["payload"].setdefault(
            "worlds",
            self.list_worlds(
                project_id=str(options.get("project_id") or "")
            ),
        )
        return translated

    def inspect_asset(self, artifact_id: str) -> dict[str, Any]:
        result = self._admin_client.assets.get_metadata(artifact_id)
        return self._translate_ue_result(
            "assets.inspect",
            result,
        )

    def list_assets(
        self,
        asset_type: str = "",
        *,
        root_uri: str = "",
    ) -> list[dict[str, Any]]:
        result = self._admin_client.assets.list(
            asset_type,
            root=root_uri or "/Game/Imported",
        )
        if not result.get("ok"):
            result = self._admin_client.assets.list_registered(asset_type)
        return [
            _normalize_asset(item, asset_type=asset_type)
            for item in result.get("artifacts") or []
            if isinstance(item, Mapping)
        ]

    def list_worlds(
        self,
        *,
        project_id: str = "",
    ) -> list[dict[str, Any]]:
        result = self._admin_client.world.list_packages(
            project_id=project_id
        )
        return [
            _normalize_world(item)
            for item in result.get("artifacts") or []
            if isinstance(item, Mapping)
        ]

    def create_session(
        self,
        request: Mapping[str, Any],
    ) -> dict[str, Any]:
        slot, ports = self._allocate_ports()
        session_id = f"bs_{uuid4().hex[:10]}"
        user_id = str(request.get("user_id") or "")
        participant_id = str(
            request.get("participant_id")
            or f"participant_{user_id or session_id}"
        )
        pixel_url = (
            f"http://{self.config.pixel_host}:"
            f"{ports['http']}/player.html"
        )
        client = self._new_client(
            remote_port=ports["remote"],
            runtime_port=ports["input"],
        )
        session = UE5BrowserSession(
            session_id=session_id,
            participant_id=participant_id,
            user_id=user_id,
            state="CREATED",
            pixel_http_port=ports["http"],
            pixel_streamer_port=ports["streamer"],
            pixel_sfu_port=ports["sfu"],
            input_port=ports["input"],
            remote_control_port=ports["remote"],
            pixel_streaming_url=pixel_url,
            ue_input_host=self.config.runtime_host,
            preview_map=self.config.preview_map,
            client=client,
            character=dict(request.get("character") or {}),
        )
        with self._lock:
            self._sessions[session_id] = session
        try:
            session.state = "BOOTING_CLIENT"
            if not self.config.dry_run:
                if self.config.ue_root is None:
                    raise ValueError("A3GAME_UE_ROOT is not configured")
                signalling = self._signalling_factory(
                    self.config.ue_root,
                    http_port=ports["http"],
                    streamer_port=ports["streamer"],
                    sfu_port=ports["sfu"],
                    use_frontend=self.config.pixel_use_frontend,
                )
                session.signalling_process = signalling
                session.signalling_pid = int(signalling.pid)
                self._wait_for_http(pixel_url)
            launch = self._launch_runtime(session, session.preview_map)
            if not launch.get("ok"):
                raise RuntimeError(
                    "; ".join(_errors(launch))
                    or "UE runtime launch failed"
                )
            session.ue_client_pid = int(
                _payload(launch).get("process_id") or 0
            )
            session.state = "PREVIEW_READY"
            if session.character:
                self.configure_session(
                    session.session_id,
                    session.character,
                )
        except Exception as exc:
            session.state = "ERROR"
            session.error = f"{type(exc).__name__}: {exc}"
            self._stop_session_processes(session)
            raise
        return serving_result(
            "sessions.create",
            engine="ue5",
            payload={"slot": slot, **session.to_dict()},
        )

    def list_sessions(self) -> dict[str, Any]:
        self._refresh_process_state()
        return serving_result(
            "sessions.list",
            engine="ue5",
            payload={
                "sessions": [
                    item.to_dict()
                    for item in self._sessions.values()
                ]
            },
        )

    def get_session(self, session_id: str) -> dict[str, Any]:
        self._refresh_process_state()
        session = self._require_session(session_id)
        return serving_result(
            "sessions.get",
            engine="ue5",
            payload=session.to_dict(),
        )

    def recover_session(
        self,
        snapshot: Mapping[str, Any],
    ) -> dict[str, Any]:
        session_id = str(
            snapshot.get("session_id")
            or snapshot.get("sessionId")
            or ""
        ).strip()
        if not session_id:
            raise ValueError("session_id is required")
        if session_id in self._sessions:
            return self.get_session(session_id)
        pixel_url = str(
            snapshot.get("pixel_streaming_url")
            or snapshot.get("pixelStreamingUrl")
            or ""
        )
        if not pixel_url or not self._http_reachable(pixel_url, 2.0):
            raise RuntimeError(
                "Stored Pixel Streaming page is not reachable"
            )
        input_port = int(
            snapshot.get("input_port")
            or snapshot.get("inputPort")
            or snapshot.get("ue_input_port")
            or 0
        )
        remote_port = int(
            snapshot.get("remote_control_port")
            or self.config.ue_remote_port
        )
        session = UE5BrowserSession(
            session_id=session_id,
            participant_id=str(
                snapshot.get("participant_id")
                or f"participant_{session_id}"
            ),
            user_id=str(snapshot.get("user_id") or ""),
            state=str(snapshot.get("state") or "PREVIEW_READY"),
            pixel_http_port=int(
                snapshot.get("pixel_http_port") or 0
            ),
            pixel_streamer_port=int(
                snapshot.get("pixel_streamer_port") or 0
            ),
            pixel_sfu_port=int(
                snapshot.get("pixel_sfu_port") or 0
            ),
            input_port=input_port,
            remote_control_port=remote_port,
            pixel_streaming_url=pixel_url,
            ue_input_host=str(
                snapshot.get("ue_input_host")
                or self.config.runtime_host
            ),
            preview_map=str(
                snapshot.get("preview_map")
                or self.config.preview_map
            ),
            client=self._new_client(
                remote_port=remote_port,
                runtime_port=input_port,
            ),
            character=dict(snapshot.get("character") or {}),
            controller_id=str(snapshot.get("controller_id") or ""),
            entity_id=str(snapshot.get("entity_id") or ""),
            world_id=str(snapshot.get("world_id") or ""),
            project_id=str(snapshot.get("project_id") or ""),
            runtime_package_id=str(
                snapshot.get("runtime_package_id") or ""
            ),
            ue_client_pid=int(snapshot.get("ue_client_pid") or 0),
            signalling_pid=int(snapshot.get("signalling_pid") or 0),
            recovered_external=True,
        )
        self._sessions[session_id] = session
        return serving_result(
            "sessions.recover",
            engine="ue5",
            payload={"recovered": True, **session.to_dict()},
        )

    def session_catalog(
        self,
        *,
        project_id: str = "",
    ) -> dict[str, Any]:
        return serving_result(
            "sessions.catalog",
            engine="ue5",
            payload={
                "avatars": self.list_assets("avatar"),
                "motions": self.list_assets("motion"),
                "worlds": self.list_worlds(project_id=project_id),
                "runtime_ready": True,
                "project_id": project_id,
            },
        )

    def configure_session(
        self,
        session_id: str,
        character: Mapping[str, Any],
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        resolved = dict(character)
        avatar_id = self._artifact_reference(
            str(
                resolved.get("avatar_id")
                or resolved.get("avatar_asset_path")
                or ""
            ),
            "avatar",
        )
        idle_id = self._artifact_reference(
            str(
                resolved.get("idle_animation")
                or resolved.get("idle_animation_path")
                or ""
            ),
            "motion",
            required=False,
        )
        move_id = self._artifact_reference(
            str(
                resolved.get("move_animation")
                or resolved.get("move_animation_path")
                or ""
            ),
            "motion",
            required=False,
        )
        if not avatar_id:
            raise ValueError("character.avatar_id is required")
        result = session.client.runtime.sessions.join(
            world_id=session.world_id,
            participant_id=session.participant_id,
            user_id=session.user_id,
            avatar_artifact_id=avatar_id,
            idle_motion_artifact_id=idle_id,
            move_motion_artifact_id=move_id,
            parameters={
                key: value
                for key, value in resolved.items()
                if key not in {
                    "avatar_id",
                    "avatar_asset_path",
                    "idle_animation",
                    "idle_animation_path",
                    "move_animation",
                    "move_animation_path",
                }
            },
        )
        if not result.get("ok"):
            return self._translate_ue_result(
                "sessions.configure",
                result,
            )
        result_payload = _payload(result)
        session.character = {
            **resolved,
            "avatar_id": avatar_id,
            "idle_animation": idle_id,
            "move_animation": move_id,
        }
        session.controller_id = str(
            result_payload.get("controller_id") or ""
        )
        session.entity_id = str(result_payload.get("entity_id") or "")
        session.state = (
            "IN_WORLD"
            if session.state == "IN_WORLD"
            else "PREVIEWING"
        )
        session.updated_at = time()
        session.last_command = "configure_session"
        return serving_result(
            "sessions.configure",
            engine="ue5",
            payload={
                "runtime": result,
                **session.to_dict(),
            },
        )

    def play_preview_animation(
        self,
        session_id: str,
        animation: str,
        *,
        loop: bool = True,
        play_rate: float = 1.0,
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        animation_id = self._artifact_reference(
            animation,
            "motion",
        )
        character = {
            **session.character,
            "idle_animation": animation_id,
            "preview_animation": animation_id,
            "preview_loop": bool(loop),
            "preview_play_rate": float(play_rate),
        }
        result = self.configure_session(session_id, character)
        session.last_command = "play_preview_animation"
        result["payload"]["preview_animation"] = animation_id
        result["payload"]["preview_loop"] = bool(loop)
        result["payload"]["preview_play_rate"] = float(play_rate)
        return result

    def load_world(
        self,
        session_id: str,
        *,
        package_id: str = "",
        world_id: str = "",
        project_id: str = "",
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        worlds = self.list_worlds(project_id=project_id)
        selected = next(
            (
                item
                for item in worlds
                if (
                    package_id
                    and item.get("package_id") == package_id
                )
                or (
                    world_id
                    and item.get("world_id") == world_id
                )
            ),
            None,
        )
        if selected is None:
            raise FileNotFoundError(
                f"World package was not found: {package_id or world_id}"
            )
        session.runtime_package_id = str(
            selected.get("package_id") or ""
        )
        session.world_id = str(selected.get("world_id") or "")
        session.project_id = str(selected.get("project_id") or "")
        session.world_map_path = self._world_map_path(selected)
        session.updated_at = time()
        session.last_command = "load_world"
        return serving_result(
            "sessions.load_world",
            engine="ue5",
            payload={
                "loaded": True,
                "package": selected,
                **session.to_dict(),
            },
        )

    def join_world(
        self,
        session_id: str,
        *,
        server_uri: str = "",
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        del server_uri
        target_map = session.world_map_path or session.preview_map
        if target_map and target_map != session.preview_map:
            relaunch = self._relaunch_runtime(session, target_map)
            if not relaunch.get("ok"):
                return relaunch
        session.state = "IN_WORLD"
        if session.character:
            configured = self.configure_session(
                session_id,
                session.character,
            )
            if not configured.get("ok"):
                return configured
        session.state = "IN_WORLD"
        session.updated_at = time()
        session.last_command = "join_world"
        return serving_result(
            "sessions.join_world",
            engine="ue5",
            payload={
                "joined": True,
                "server_url": "",
                **session.to_dict(),
            },
        )

    def leave_world(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        runtime = {}
        if session.controller_id:
            runtime = session.client.runtime.sessions.leave(
                participant_id=session.participant_id,
                controller_id=session.controller_id,
            )
        session.state = "PREVIEW_READY"
        session.updated_at = time()
        session.last_command = "leave_world"
        return serving_result(
            "sessions.leave_world",
            engine="ue5",
            payload={"runtime": runtime, **session.to_dict()},
        )

    def apply_input(
        self,
        session_id: str,
        input_state: Mapping[str, Any],
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        if not session.controller_id:
            return serving_result(
                "sessions.apply_input",
                engine="ue5",
                payload={
                    "skipped": True,
                    "reason": "session has no configured controller",
                    **session.to_dict(),
                },
            )
        result = session.client.runtime.sessions.apply_input(
            session.controller_id,
            move_x=float(input_state.get("move_x", 0.0)),
            move_y=float(input_state.get("move_y", 0.0)),
            run=bool(input_state.get("run", False)),
            jump=bool(input_state.get("jump", False)),
            yaw=float(input_state.get("yaw", 0.0)),
            pitch=float(input_state.get("pitch", 0.0)),
            seq=int(input_state.get("seq", 0)),
        )
        session.last_command = "apply_input"
        session.updated_at = time()
        warnings = []
        action = str(input_state.get("action") or "").strip()
        if action:
            warnings.append(
                "The UEClient v1 normalized input contract does not "
                f"define game-specific action {action!r}"
            )
        translated = self._translate_ue_result(
            "sessions.apply_input",
            result,
        )
        translated["warnings"].extend(warnings)
        translated["payload"].update(session.to_dict())
        for key, value in session.to_dict().items():
            translated.setdefault(key, value)
        return translated

    def apply_preview_camera(
        self,
        session_id: str,
        camera_input: Mapping[str, Any],
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        normalized = {
            "move_x": float(camera_input.get("pan_y_delta", 0.0))
            / 100.0,
            "move_y": float(camera_input.get("zoom_delta", 0.0))
            / 100.0,
            "yaw": float(camera_input.get("yaw_delta", 0.0)),
            "pitch": float(camera_input.get("pitch_delta", 0.0)),
        }
        result = self.apply_input(session_id, normalized)
        result["operation"] = "sessions.apply_preview_camera"
        session.last_command = "apply_preview_camera"
        return result

    def handle_runtime_event(
        self,
        session_id: str,
        event: str,
        *,
        world_name: str = "",
        entity_name: str = "",
    ) -> dict[str, Any]:
        session = self._require_session(session_id)
        normalized = str(event or "").strip().upper()
        if normalized in {"SESSION_READY", "RENDER_READY"}:
            session.state = "IN_WORLD"
        elif normalized == "PREVIEW_READY":
            session.state = "PREVIEW_READY"
        elif normalized == "SESSION_ERROR":
            session.state = "ERROR"
        session.last_command = normalized
        session.updated_at = time()
        return serving_result(
            "sessions.runtime_event",
            engine="ue5",
            payload={
                "event": normalized,
                "world_name": world_name,
                "entity_name": entity_name,
                **session.to_dict(),
            },
        )

    def stop_session(self, session_id: str) -> dict[str, Any]:
        session = self._require_session(session_id)
        snapshot = session.to_dict()
        if session.controller_id:
            session.client.runtime.sessions.clear_entity(
                participant_id=session.participant_id,
                controller_id=session.controller_id,
                entity_id=session.entity_id,
            )
        self._stop_session_processes(session)
        session.state = "DESTROYED"
        with self._lock:
            self._sessions.pop(session_id, None)
        return serving_result(
            "sessions.stop",
            engine="ue5",
            payload={
                "removed": True,
                "session": snapshot,
                **snapshot,
            },
        )

    def debug(
        self,
        operation: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = dict(payload or {})
        normalized = (
            str(operation or "")
            .strip()
            .lower()
            .replace("/", "_")
            .replace("-", "_")
        )
        session = self._first_session()
        if normalized == "viewer_config":
            return serving_result(
                "debug.viewer_config",
                engine="ue5",
                payload={
                    "pixel_streaming_url": (
                        session.pixel_streaming_url
                        if session is not None
                        else ""
                    ),
                    "pixel_streaming_enabled": bool(session),
                    "default_actor_label": "A3Game_BrowserPreview",
                    "playable_actor_label": "A3Game_BrowserPlayer",
                    "playable_blueprint_path": "",
                    "default_walk_speed": 360.0,
                    "default_run_speed": 650.0,
                    "movement_step": 35.0,
                    "rotation_step": 15.0,
                    "viewer_host": self.config.gateway_host,
                    "viewer_port": self.config.gateway_port,
                    "runtime_ue_host": self.config.runtime_host,
                    "runtime_ue_port": (
                        session.input_port
                        if session is not None
                        else self.config.base_runtime_port
                    ),
                    "runtime_udp_mode": "ueclient",
                },
            )
        if normalized == "pixel_status":
            url = (
                session.pixel_streaming_url
                if session is not None
                else ""
            )
            reachable = bool(
                url
                and (
                    self.config.dry_run
                    or self._http_reachable(url, 1.0)
                )
            )
            return serving_result(
                "debug.pixel_status",
                engine="ue5",
                payload={
                    "reachable": reachable,
                    "stream_ready": reachable,
                    "url": url,
                    "message": (
                        "reachable"
                        if reachable
                        else "no active UE5 browser session"
                    ),
                },
            )
        if normalized == "runtime_world_state":
            result = self._admin_client.runtime.sessions.snapshot(
                world_id=str(data.get("world_id") or "")
            )
            return self._translate_ue_result(
                "debug.runtime_world_state",
                result,
            )
        if normalized in {
            "runtime_clear_entity",
            "scene_clear",
        }:
            if session is None:
                return serving_result(
                    "debug.clear",
                    engine="ue5",
                    payload={"removed": False},
                )
            result = session.client.runtime.sessions.clear_entity(
                participant_id=session.participant_id,
                controller_id=session.controller_id,
                entity_id=session.entity_id,
            )
            return self._translate_ue_result("debug.clear", result)
        if normalized in {
            "scene_present_avatar",
            "scene_preview_runtime_avatar",
        }:
            active = session
            if active is None:
                created = self.create_session(
                    {
                        "user_id": "admin_preview",
                        "participant_id": "admin_preview",
                        "character": {},
                    }
                )
                active = self._require_session(
                    str(created.get("session_id") or "")
                )
            return self.configure_session(
                active.session_id,
                {
                    "avatar_id": (
                        data.get("avatar_asset_path")
                        or data.get("avatar_id")
                        or ""
                    ),
                    "idle_animation": data.get(
                        "idle_animation_path",
                        "",
                    ),
                    "move_animation": data.get(
                        "move_animation_path",
                        "",
                    ),
                },
            )
        if normalized in {
            "scene_play_motion",
            "scene_set_animation",
        }:
            if session is None:
                raise ValueError("No active preview session")
            return self.play_preview_animation(
                session.session_id,
                str(
                    data.get("motion_asset_path")
                    or data.get("animation")
                    or ""
                ),
                loop=bool(data.get("looping", True)),
            )
        if normalized in {
            "scene_move",
            "scene_rotate",
            "editor_camera_input",
            "editor_camera_start",
        }:
            if session is None:
                return serving_result(
                    f"debug.{normalized}",
                    engine="ue5",
                    payload={"skipped": True, "reason": "no session"},
                )
            return self.apply_preview_camera(
                session.session_id,
                {
                    "yaw_delta": data.get(
                        "yaw_delta",
                        data.get("yawDelta", 0.0),
                    ),
                    "pitch_delta": data.get(
                        "pitch_delta",
                        data.get("pitchDelta", 0.0),
                    ),
                    "zoom_delta": data.get(
                        "zoom_delta",
                        data.get("move_y", 0.0),
                    ),
                    "pan_y_delta": data.get(
                        "pan_y_delta",
                        data.get("move_x", 0.0),
                    ),
                },
            )
        if normalized == "scene_transform":
            return self.debug(
                "runtime_world_state",
                {"world_id": str(data.get("world_id") or "")},
            )
        if normalized == "scene_multiplayer_debug_config":
            slots = data.get("slots")
            if isinstance(slots, list):
                self._multiplayer_slots = [
                    dict(item)
                    for item in slots
                    if isinstance(item, Mapping)
                ]
            return serving_result(
                "debug.multiplayer_config",
                engine="ue5",
                payload={"slots": list(self._multiplayer_slots)},
            )
        return serving_result(
            f"debug.{normalized or 'unknown'}",
            engine="ue5",
            ok=False,
            errors=[f"Unsupported UE5 example debug operation: {operation}"],
        )

    def _allocate_ports(self) -> tuple[int, dict[str, int]]:
        for slot in range(self.config.max_sessions):
            stride = slot * self.config.session_port_stride
            ports = {
                "http": self.config.base_pixel_http_port + stride,
                "streamer": (
                    self.config.base_pixel_streamer_port + stride
                ),
                "sfu": self.config.base_pixel_sfu_port + stride,
                "input": self.config.base_runtime_port + slot,
                "remote": self.config.ue_remote_port + slot,
            }
            if self.config.dry_run or (
                _is_tcp_port_free(self.config.pixel_host, ports["http"])
                and _is_tcp_port_free(
                    self.config.pixel_host,
                    ports["streamer"],
                )
                and _is_tcp_port_free(
                    self.config.pixel_host,
                    ports["sfu"],
                )
                and _is_tcp_port_free(
                    self.config.ue_host,
                    ports["remote"],
                )
                and _is_udp_port_free(
                    self.config.runtime_host,
                    ports["input"],
                )
            ):
                return slot, ports
        raise RuntimeError("No free browser serving session ports")

    def _launch_runtime(
        self,
        session: UE5BrowserSession,
        map_path: str,
    ) -> dict[str, Any]:
        extra_args = [
            "-game",
            "-AudioMixer",
            (
                "-PixelStreamingURL=ws://"
                f"{self.config.pixel_host}:"
                f"{session.pixel_streamer_port}"
            ),
            f"-A3GameBrowserSessionId={session.session_id}",
        ]
        if self.config.render_offscreen:
            extra_args.append("-RenderOffscreen")
        if self.config.dry_run:
            return {
                "ok": True,
                "operation": "runtime.launch_editor",
                "artifacts": [],
                "warnings": [],
                "errors": [],
                "payload": {
                    "process_id": 0,
                    "map_path": map_path,
                    "extra_args": extra_args,
                    "dry_run": True,
                },
            }
        return session.client.runtime.launch_editor(
            map_path=map_path,
            extra_args=extra_args,
            dry_run=self.config.dry_run,
        )

    def _relaunch_runtime(
        self,
        session: UE5BrowserSession,
        map_path: str,
    ) -> dict[str, Any]:
        if session.ue_client_pid and not session.recovered_external:
            session.client.runtime.stop_editor(session.ue_client_pid)
        launch = self._launch_runtime(session, map_path)
        if launch.get("ok"):
            session.ue_client_pid = int(
                _payload(launch).get("process_id") or 0
            )
            session.updated_at = time()
        return self._translate_ue_result(
            "sessions.relaunch_runtime",
            launch,
        )

    def _wait_for_http(self, url: str) -> None:
        deadline = time() + self.config.pixel_start_timeout
        while time() < deadline:
            if self._http_reachable(url, 1.0):
                return
            sleep(0.5)
        raise TimeoutError(
            f"Timed out waiting for Pixel Streaming page: {url}"
        )

    def _stop_session_processes(
        self,
        session: UE5BrowserSession,
    ) -> None:
        if session.ue_client_pid and not session.recovered_external:
            session.client.runtime.stop_editor(session.ue_client_pid)
        process = session.signalling_process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def _refresh_process_state(self) -> None:
        for session in self._sessions.values():
            process = session.signalling_process
            if (
                process is not None
                and process.poll() is not None
                and session.state not in {"DESTROYED", "ERROR"}
            ):
                session.state = "ERROR"
                session.error = (
                    "Pixel Streaming signalling exited with code "
                    f"{process.returncode}"
                )

    def _require_session(self, session_id: str) -> UE5BrowserSession:
        try:
            return self._sessions[str(session_id)]
        except KeyError as exc:
            raise KeyError(f"Unknown browser session: {session_id}") from exc

    def _first_session(self) -> UE5BrowserSession | None:
        return next(iter(self._sessions.values()), None)

    def _artifact_reference(
        self,
        reference: str,
        asset_type: str,
        *,
        required: bool = True,
    ) -> str:
        value = str(reference or "").strip()
        if not value:
            if required:
                raise ValueError(f"{asset_type} asset is required")
            return ""
        for asset in self.list_assets(asset_type):
            if value in {
                str(asset.get("artifact_id") or ""),
                str((asset.get("native") or {}).get("path") or ""),
            }:
                return str(asset.get("artifact_id") or value)
        if required:
            raise KeyError(f"Unknown {asset_type} asset: {value}")
        return ""

    @staticmethod
    def _world_map_path(world: Mapping[str, Any]) -> str:
        manifest = dict(world.get("manifest") or {})
        engine_data = dict(manifest.get("ue") or {})
        if engine_data.get("level_path"):
            return str(engine_data["level_path"])
        world_data = dict(manifest.get("world") or {})
        metadata = dict(world_data.get("metadata") or {})
        return str(
            metadata.get("level_path")
            or world_data.get("level_path")
            or ""
        )

    @staticmethod
    def _translate_ue_result(
        operation: str,
        result: Mapping[str, Any],
        *,
        asset_type: str = "",
    ) -> dict[str, Any]:
        value = dict(result)
        ue_payload = dict(value.get("payload") or {})
        artifacts = [
            _normalize_asset(item, asset_type=asset_type)
            for item in value.get("artifacts") or []
            if isinstance(item, Mapping)
        ]
        return serving_result(
            operation,
            engine="ue5",
            ok=bool(value.get("ok", False)),
            payload={
                "ue_operation": value.get("operation", ""),
                "ue_payload": ue_payload,
                **ue_payload,
            },
            artifacts=artifacts,
            warnings=[
                str(item)
                for item in value.get("warnings") or []
            ],
            errors=_errors(value),
        )


def create_ue5_example_backend(
    config: BrowserServingConfig | None = None,
) -> UE5ExampleBackend:
    return UE5ExampleBackend(
        config or BrowserServingConfig.from_environment()
    )
