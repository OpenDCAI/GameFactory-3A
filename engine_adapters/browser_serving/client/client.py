"""Python SDK for the public Browser Serving HTTP API."""

from __future__ import annotations

import json
import os
from typing import Any, Mapping
from urllib.parse import quote

from .http import HTTPTransport, Transport


def _session_path(session_id: str, suffix: str = "") -> str:
    return f"/api/sessions/{quote(str(session_id), safe='')}{suffix}"


class AssetsClient:
    def __init__(self, transport: Transport, engine: str) -> None:
        self._transport = transport
        self._engine = engine

    def upload(
        self,
        file_path: str | os.PathLike[str],
        asset_type: str,
        *,
        engine: str = "",
        destination: str = "",
        options: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        game_id: str = "",
        run_id: str = "",
        task_id: str = "",
        artifact_key: str = "",
    ) -> dict[str, Any]:
        upload = getattr(self._transport, "upload", None)
        if upload is None:
            raise TypeError("Configured transport does not support uploads")
        return upload(
            "/api/assets/upload",
            file_path,
            fields={
                "engine": engine or self._engine,
                "asset_type": asset_type,
                "destination": destination,
                "options_json": json.dumps(dict(options or {})),
                "metadata_json": json.dumps(dict(metadata or {})),
                "game_id": game_id,
                "run_id": run_id,
                "task_id": task_id,
                "artifact_key": artifact_key,
            },
        )

    def import_descriptor(
        self,
        descriptor: Mapping[str, Any],
        asset_type: str,
        *,
        engine: str = "",
        destination: str = "",
        options: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._transport.request(
            "POST",
            "/api/assets/import",
            json_body={
                "engine": engine or self._engine,
                "asset_type": asset_type,
                "source": {"descriptor": dict(descriptor)},
                "destination_uri": destination,
                "options": dict(options or {}),
                "metadata": dict(metadata or {}),
            },
        )

    def list(
        self,
        *,
        asset_type: str = "",
        engine: str = "",
        root_uri: str = "",
    ) -> list[dict[str, Any]]:
        return self._transport.request(
            "GET",
            "/api/assets",
            query={
                "engine": engine or self._engine,
                "type": asset_type,
                "root_uri": root_uri,
            },
        )

    def groups(
        self,
        *,
        engine: str = "",
        root_uri: str = "",
    ) -> dict[str, list[dict[str, Any]]]:
        return self._transport.request(
            "GET",
            "/api/assets/groups",
            query={
                "engine": engine or self._engine,
                "root_uri": root_uri,
            },
        )

    def inspect(
        self,
        artifact_id: str,
        *,
        engine: str = "",
    ) -> dict[str, Any]:
        return self._transport.request(
            "POST",
            "/api/assets/inspect",
            json_body={
                "engine": engine or self._engine,
                "artifact_id": artifact_id,
            },
        )


class WorldsClient:
    def __init__(self, transport: Transport, engine: str) -> None:
        self._transport = transport
        self._engine = engine

    def list(
        self,
        *,
        project_id: str = "",
        engine: str = "",
    ) -> list[dict[str, Any]]:
        return self._transport.request(
            "GET",
            "/api/worlds",
            query={
                "engine": engine or self._engine,
                "project_id": project_id,
            },
        )

    def upload(
        self,
        file_path: str | os.PathLike[str],
        *,
        engine: str = "",
        world_id: str = "",
        project_id: str = "",
        publish: bool = True,
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        merged = {
            **dict(options or {}),
            "world_id": world_id,
            "project_id": project_id,
            "publish": publish,
        }
        assets = AssetsClient(self._transport, self._engine)
        return assets.upload(
            file_path,
            "scene",
            engine=engine or self._engine,
            options=merged,
        )


class SessionsClient:
    def __init__(self, transport: Transport, engine: str) -> None:
        self._transport = transport
        self._engine = engine

    def create(
        self,
        *,
        user_id: str = "",
        participant_id: str = "",
        character: Mapping[str, Any] | None = None,
        engine: str = "",
        options: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._transport.request(
            "POST",
            "/api/sessions",
            json_body={
                "engine": engine or self._engine,
                "user_id": user_id,
                "participant_id": participant_id,
                "character": dict(character or {}),
                "options": dict(options or {}),
            },
        )

    def list(self, *, engine: str = "") -> dict[str, Any]:
        return self._transport.request(
            "GET",
            "/api/sessions",
            query={"engine": engine or self._engine},
        )

    def get(self, session_id: str, *, engine: str = "") -> dict[str, Any]:
        return self._transport.request(
            "GET",
            _session_path(session_id),
            query={"engine": engine or self._engine},
        )

    def recover(
        self,
        snapshot: Mapping[str, Any],
        *,
        engine: str = "",
    ) -> dict[str, Any]:
        return self._transport.request(
            "POST",
            "/api/sessions/recover",
            json_body={
                "engine": engine or self._engine,
                "session": dict(snapshot),
            },
        )

    def catalog(
        self,
        *,
        project_id: str = "",
        engine: str = "",
    ) -> dict[str, Any]:
        return self._transport.request(
            "GET",
            "/api/sessions/catalog",
            query={
                "engine": engine or self._engine,
                "project_id": project_id,
            },
        )

    def configure(
        self,
        session_id: str,
        character: Mapping[str, Any],
        *,
        engine: str = "",
    ) -> dict[str, Any]:
        return self._transport.request(
            "POST",
            _session_path(session_id, "/character"),
            json_body={
                "engine": engine or self._engine,
                "character": dict(character),
            },
        )

    def play_preview_animation(
        self,
        session_id: str,
        animation: str,
        *,
        loop: bool = True,
        play_rate: float = 1.0,
        engine: str = "",
    ) -> dict[str, Any]:
        return self._transport.request(
            "POST",
            _session_path(session_id, "/preview-animation"),
            json_body={
                "engine": engine or self._engine,
                "animation": animation,
                "loop": loop,
                "play_rate": play_rate,
            },
        )

    def load_world(
        self,
        session_id: str,
        *,
        package_id: str = "",
        world_id: str = "",
        project_id: str = "",
        engine: str = "",
    ) -> dict[str, Any]:
        return self._transport.request(
            "POST",
            _session_path(session_id, "/load-world"),
            json_body={
                "engine": engine or self._engine,
                "package_id": package_id,
                "world_id": world_id,
                "project_id": project_id,
            },
        )

    def join(
        self,
        session_id: str,
        *,
        server_uri: str = "",
        engine: str = "",
    ) -> dict[str, Any]:
        return self._transport.request(
            "POST",
            _session_path(session_id, "/join"),
            json_body={
                "engine": engine or self._engine,
                "server_uri": server_uri,
            },
        )

    def leave(
        self,
        session_id: str,
        *,
        engine: str = "",
    ) -> dict[str, Any]:
        return self._transport.request(
            "POST",
            _session_path(session_id, "/leave"),
            query={"engine": engine or self._engine},
        )

    def apply_input(
        self,
        session_id: str,
        input_state: Mapping[str, Any],
        *,
        engine: str = "",
    ) -> dict[str, Any]:
        return self._transport.request(
            "POST",
            _session_path(session_id, "/input"),
            json_body={
                "engine": engine or self._engine,
                "input": dict(input_state),
            },
        )

    def apply_preview_camera(
        self,
        session_id: str,
        input_state: Mapping[str, Any],
        *,
        engine: str = "",
    ) -> dict[str, Any]:
        return self._transport.request(
            "POST",
            _session_path(session_id, "/preview-camera"),
            json_body={
                "engine": engine or self._engine,
                "input": dict(input_state),
            },
        )

    def stop(self, session_id: str, *, engine: str = "") -> dict[str, Any]:
        return self._transport.request(
            "DELETE",
            _session_path(session_id),
            query={"engine": engine or self._engine},
        )


class BrowserServingClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:7870",
        *,
        engine: str = "ue5",
        timeout: float = 30.0,
        headers: Mapping[str, str] | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.default_engine = str(engine or "ue5")
        self._transport = transport or HTTPTransport(
            base_url,
            timeout=timeout,
            headers=headers,
        )
        self.assets = AssetsClient(self._transport, self.default_engine)
        self.worlds = WorldsClient(self._transport, self.default_engine)
        self.sessions = SessionsClient(self._transport, self.default_engine)

    def health(self) -> dict[str, Any]:
        return self._transport.request("GET", "/api/health")

    def engines(self) -> dict[str, Any]:
        return self._transport.request("GET", "/api/engines")

    def engine_status(self, engine: str = "") -> dict[str, Any]:
        selected = quote(engine or self.default_engine, safe="")
        return self._transport.request(
            "GET",
            f"/api/engines/{selected}/status",
        )
