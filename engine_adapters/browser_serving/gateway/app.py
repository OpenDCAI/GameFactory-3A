"""FastAPI Gateway for engine-agnostic browser serving."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..config import BrowserServingConfig
from ..contracts import (
    BrowserServingError,
    EngineCapabilityError,
    UnknownEngineError,
)
from ..registry import EngineRegistry
from ..service import BrowserServingService


logger = logging.getLogger("aaagameforge.browser_serving")
PLAYER_DIR = (
    Path(__file__).resolve().parents[1]
    / "frontend"
    / "player"
)
PLAYER_HTML = PLAYER_DIR / "viewer.html"


class AssetSourcePayload(BaseModel):
    uri: str = ""
    descriptor: dict[str, Any] = Field(default_factory=dict)
    media_type: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssetImportPayload(BaseModel):
    engine: str = "ue5"
    source: AssetSourcePayload | None = None
    asset_type: str = Field(..., min_length=1)
    destination_uri: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssetInspectPayload(BaseModel):
    engine: str = "ue5"
    artifact_id: str = Field(..., min_length=1)


class SessionCreatePayload(BaseModel):
    engine: str = "ue5"
    user_id: str = ""
    participant_id: str = ""
    character: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)


class SessionRecoverPayload(BaseModel):
    engine: str = "ue5"
    session: dict[str, Any] = Field(default_factory=dict)


class SessionCharacterPayload(BaseModel):
    engine: str = "ue5"
    character: dict[str, Any] = Field(default_factory=dict)


class SessionAnimationPayload(BaseModel):
    engine: str = "ue5"
    animation: str = Field(..., min_length=1)
    loop: bool = True
    play_rate: float = 1.0


class SessionWorldPayload(BaseModel):
    engine: str = "ue5"
    package_id: str = ""
    world_id: str = ""
    project_id: str = ""


class SessionJoinPayload(BaseModel):
    engine: str = "ue5"
    server_uri: str = ""


class SessionInputPayload(BaseModel):
    engine: str = "ue5"
    input: dict[str, Any] = Field(default_factory=dict)
    move_x: float = 0.0
    move_y: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    run: bool = False
    jump: bool = False

    def resolved(self) -> dict[str, Any]:
        if self.input:
            return dict(self.input)
        return {
            "move_x": self.move_x,
            "move_y": self.move_y,
            "yaw": self.yaw,
            "pitch": self.pitch,
            "run": self.run,
            "jump": self.jump,
        }


class RuntimeEventPayload(BaseModel):
    engine: str = "ue5"
    session_id: str = Field(..., min_length=1)
    event: str = Field(..., min_length=1)
    world_name: str = ""
    pawn_name: str = ""
    entity_name: str = ""


def _json_object(value: str, label: str) -> dict[str, Any]:
    if not str(value or "").strip():
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must be a JSON object")
    return payload


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, UnknownEngineError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, EngineCapabilityError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (KeyError, ValueError)):
        return HTTPException(status_code=400, detail=str(exc))
    logger.error(
        "Browser serving error: %s: %s",
        type(exc).__name__,
        exc,
        exc_info=True,
    )
    return HTTPException(
        status_code=500,
        detail=f"{type(exc).__name__}: {exc}",
    )


def _reachable(url: str) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=2.0) as response:
            reachable = int(response.status) < 500
            return {
                "ok": True,
                "reachable": reachable,
                "url": url,
                "status_code": int(response.status),
                "message": "reachable" if reachable else "server error",
            }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "ok": True,
            "reachable": False,
            "url": url,
            "message": str(exc),
        }


def create_app(
    service: BrowserServingService | None = None,
    *,
    config: BrowserServingConfig | None = None,
    backends: list[Any] | None = None,
) -> FastAPI:
    resolved_config = config or BrowserServingConfig.from_environment()
    if service is None:
        if backends is None:
            from ..backends import create_ue5_example_backend

            backends = [create_ue5_example_backend(resolved_config)]
        service = BrowserServingService(
            EngineRegistry(backends),
            resolved_config,
        )

    app = FastAPI(
        title="AAAGameForge Browser Serving",
        version="1.0.0",
    )
    app.state.browser_serving_service = service
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            value.strip()
            for value in os.environ.get(
                "A3GAME_BROWSER_CORS_ORIGINS",
                (
                    "http://127.0.0.1:7860,"
                    "http://localhost:7860,"
                    "http://127.0.0.1:7870,"
                    "http://localhost:7870"
                ),
            ).split(",")
            if value.strip()
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if PLAYER_DIR.is_dir():
        app.mount(
            "/static",
            StaticFiles(directory=PLAYER_DIR),
            name="static",
        )

    @app.get("/")
    def index():
        if PLAYER_HTML.is_file():
            return FileResponse(PLAYER_HTML)
        return service.health()

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return service.health()

    @app.get("/api/engines")
    def engines() -> dict[str, Any]:
        return service.list_engines()

    @app.get("/api/engines/{engine}/capabilities")
    def capabilities(engine: str) -> dict[str, Any]:
        backend = service.registry.resolve(engine)
        return backend.descriptor.to_dict()

    @app.get("/api/engines/{engine}/status")
    def engine_status(engine: str) -> dict[str, Any]:
        return service.engine_status(engine)

    @app.post("/api/assets/upload")
    def upload_asset(
        file: UploadFile = File(...),
        engine: str = Form("ue5"),
        asset_type: str = Form(...),
        destination: str = Form(""),
        options_json: str = Form("{}"),
        metadata_json: str = Form("{}"),
        game_id: str = Form(""),
        run_id: str = Form(""),
        task_id: str = Form(""),
        artifact_key: str = Form(""),
    ) -> dict[str, Any]:
        return service.stage_and_import(
            file.file,
            filename=file.filename or "upload.bin",
            asset_type=asset_type,
            engine=engine,
            media_type=file.content_type or "",
            destination=destination,
            options=_json_object(options_json, "options_json"),
            metadata=_json_object(metadata_json, "metadata_json"),
            game_id=game_id,
            run_id=run_id,
            task_id=task_id,
            artifact_key=artifact_key,
        )

    @app.post("/api/assets/import")
    def import_asset(request: AssetImportPayload) -> dict[str, Any]:
        source = request.source or AssetSourcePayload()
        if source.descriptor:
            return service.import_descriptor(
                engine=request.engine,
                asset_type=request.asset_type,
                descriptor=source.descriptor,
                destination=request.destination_uri,
                options=request.options,
                metadata={
                    **source.metadata,
                    **request.metadata,
                },
            )
        if source.uri:
            path = Path(source.uri).expanduser().resolve()
            with path.open("rb") as handle:
                return service.stage_and_import(
                    handle,
                    filename=path.name,
                    asset_type=request.asset_type,
                    engine=request.engine,
                    media_type=source.media_type,
                    destination=request.destination_uri,
                    options=request.options,
                    metadata={
                        **source.metadata,
                        **request.metadata,
                    },
                )
        raise ValueError("source.descriptor or source.uri is required")

    @app.get("/api/assets")
    def assets(
        asset_type: str = Query("", alias="type"),
        engine: str = "ue5",
        root_uri: str = "",
    ) -> list[dict[str, Any]]:
        return service.list_assets(
            engine,
            asset_type=asset_type,
            root_uri=root_uri,
        )

    @app.get("/api/assets/groups")
    def asset_groups(
        engine: str = "ue5",
        root_uri: str = "",
    ) -> dict[str, list[dict[str, Any]]]:
        return service.asset_groups(engine, root_uri=root_uri)

    @app.post("/api/assets/inspect")
    def inspect_asset(request: AssetInspectPayload) -> dict[str, Any]:
        return service.inspect_asset(request.engine, request.artifact_id)

    @app.get("/api/worlds")
    def worlds(
        engine: str = "ue5",
        project_id: str = "",
    ) -> list[dict[str, Any]]:
        return service.list_worlds(engine, project_id=project_id)

    @app.post("/api/sessions")
    def create_session(request: SessionCreatePayload) -> dict[str, Any]:
        return service.call_session(
            request.engine,
            "create_session",
            {
                "user_id": request.user_id,
                "participant_id": request.participant_id,
                "character": dict(request.character),
                "options": dict(request.options),
            },
        )

    @app.get("/api/sessions")
    def list_sessions(engine: str = "ue5") -> dict[str, Any]:
        return service.call_session(engine, "list_sessions")

    @app.get("/api/sessions/catalog")
    def session_catalog(
        engine: str = "ue5",
        project_id: str = "",
    ) -> dict[str, Any]:
        return service.call_session(
            engine,
            "session_catalog",
            project_id=project_id,
        )

    @app.post("/api/sessions/recover")
    def recover_session(request: SessionRecoverPayload) -> dict[str, Any]:
        return service.call_session(
            request.engine,
            "recover_session",
            request.session,
        )

    @app.post("/api/sessions/runtime-event")
    def runtime_event(request: RuntimeEventPayload) -> dict[str, Any]:
        return service.call_session(
            request.engine,
            "handle_runtime_event",
            request.session_id,
            request.event,
            world_name=request.world_name,
            entity_name=request.entity_name or request.pawn_name,
        )

    @app.get("/api/sessions/{session_id}")
    def get_session(
        session_id: str,
        engine: str = "ue5",
    ) -> dict[str, Any]:
        return service.call_session(engine, "get_session", session_id)

    @app.post("/api/sessions/{session_id}/character")
    def configure_session(
        session_id: str,
        request: SessionCharacterPayload,
    ) -> dict[str, Any]:
        return service.call_session(
            request.engine,
            "configure_session",
            session_id,
            request.character,
        )

    @app.post("/api/sessions/{session_id}/preview-animation")
    def preview_animation(
        session_id: str,
        request: SessionAnimationPayload,
    ) -> dict[str, Any]:
        return service.call_session(
            request.engine,
            "play_preview_animation",
            session_id,
            request.animation,
            loop=request.loop,
            play_rate=request.play_rate,
        )

    @app.post("/api/sessions/{session_id}/load-world")
    def load_world(
        session_id: str,
        request: SessionWorldPayload,
    ) -> dict[str, Any]:
        return service.call_session(
            request.engine,
            "load_world",
            session_id,
            package_id=request.package_id,
            world_id=request.world_id,
            project_id=request.project_id,
        )

    @app.post("/api/sessions/{session_id}/join")
    def join_world(
        session_id: str,
        request: SessionJoinPayload | None = None,
    ) -> dict[str, Any]:
        payload = request or SessionJoinPayload()
        return service.call_session(
            payload.engine,
            "join_world",
            session_id,
            server_uri=payload.server_uri,
        )

    @app.post("/api/sessions/{session_id}/leave")
    def leave_world(
        session_id: str,
        engine: str = "ue5",
    ) -> dict[str, Any]:
        return service.call_session(engine, "leave_world", session_id)

    @app.post("/api/sessions/{session_id}/input")
    def apply_input(
        session_id: str,
        request: SessionInputPayload,
    ) -> dict[str, Any]:
        return service.call_session(
            request.engine,
            "apply_input",
            session_id,
            request.resolved(),
        )

    @app.post("/api/sessions/{session_id}/preview-camera")
    def preview_camera(
        session_id: str,
        request: SessionInputPayload,
    ) -> dict[str, Any]:
        return service.call_session(
            request.engine,
            "apply_preview_camera",
            session_id,
            request.resolved(),
        )

    @app.delete("/api/sessions/{session_id}")
    def stop_session(
        session_id: str,
        engine: str = "ue5",
    ) -> dict[str, Any]:
        return service.call_session(engine, "stop_session", session_id)

    @app.websocket("/api/sessions/{session_id}/input-ws")
    async def input_ws(session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                if message.get("type") == "ping":
                    await websocket.send_json(
                        {"ok": True, "type": "pong"}
                    )
                    continue
                try:
                    result = service.call_session(
                        str(
                            message.get("engine")
                            or resolved_config.default_engine
                        ),
                        "apply_input",
                        session_id,
                        message,
                    )
                    await websocket.send_json(
                        {"type": "input_ack", **result}
                    )
                except Exception as exc:
                    await websocket.send_json(
                        {
                            "ok": False,
                            "type": "error",
                            "error": str(exc),
                        }
                    )
        except WebSocketDisconnect:
            return

    @app.get("/api/engines/{engine}/debug/status")
    def debug_status(engine: str) -> dict[str, Any]:
        return service.engine_status(engine)

    @app.get("/api/engines/{engine}/debug/viewer/config")
    def viewer_config(engine: str) -> dict[str, Any]:
        return service.viewer_config(engine)

    @app.get("/api/engines/{engine}/debug/viewer/pixel-status")
    def pixel_status(engine: str) -> dict[str, Any]:
        return service.debug(engine, "pixel_status")

    @app.get("/api/engines/{engine}/debug/viewer/url-status")
    def url_status(engine: str, url: str) -> dict[str, Any]:
        del engine
        return _reachable(url)

    @app.get("/api/engines/{engine}/debug/runtime/world-state")
    def runtime_world_state(
        engine: str,
        world_id: str = "",
    ) -> dict[str, Any]:
        return service.debug(
            engine,
            "runtime_world_state",
            {"world_id": world_id},
        )

    @app.api_route(
        "/api/engines/{engine}/debug/{operation:path}",
        methods=["GET", "POST", "DELETE"],
    )
    async def generic_debug(
        engine: str,
        operation: str,
        request: Request,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = dict(request.query_params)
        if request.method != "GET":
            try:
                body = await request.json()
            except Exception:
                body = {}
            if isinstance(body, Mapping):
                payload.update(dict(body))
        return service.debug(
            engine,
            operation.replace("/", "_").replace("-", "_"),
            payload,
        )

    @app.websocket("/api/engines/{engine}/debug/runtime/ws")
    async def runtime_debug_ws(engine: str, websocket: WebSocket) -> None:
        await websocket.accept()
        session_id = ""
        try:
            while True:
                message = await websocket.receive_json()
                kind = str(message.get("type") or "")
                if kind == "join":
                    result = service.call_session(
                        engine,
                        "create_session",
                        {
                            "user_id": message.get("user_id", ""),
                            "participant_id": message.get(
                                "participant_id",
                                "",
                            ),
                            "character": {
                                "avatar_id": message.get(
                                    "avatar_asset_path",
                                    "",
                                ),
                                "idle_animation": message.get(
                                    "idle_animation_path",
                                    "",
                                ),
                                "move_animation": message.get(
                                    "move_animation_path",
                                    "",
                                ),
                            },
                            "options": {},
                        },
                    )
                    session_id = str(result.get("session_id") or "")
                    await websocket.send_json({"type": "joined", **result})
                elif kind in {"input", "input_state"} and session_id:
                    result = service.call_session(
                        engine,
                        "apply_input",
                        session_id,
                        message,
                    )
                    await websocket.send_json(
                        {"type": "input_ack", **result}
                    )
                elif kind == "leave" and session_id:
                    result = service.call_session(
                        engine,
                        "leave_world",
                        session_id,
                    )
                    await websocket.send_json({"type": "left", **result})
                elif kind == "world_state":
                    result = service.debug(
                        engine,
                        "runtime_world_state",
                        {"world_id": message.get("world_id", "")},
                    )
                    await websocket.send_json(
                        {"type": "world_state", **result}
                    )
                else:
                    await websocket.send_json(
                        {
                            "ok": False,
                            "type": "error",
                            "error": f"Unsupported message type: {kind}",
                        }
                    )
        except WebSocketDisconnect:
            return

    @app.websocket("/api/engines/{engine}/debug/editor-camera/input-ws")
    async def editor_camera_ws(engine: str, websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                message = await websocket.receive_json()
                service.debug(engine, "editor_camera_input", message)
        except WebSocketDisconnect:
            return

    async def error_response(request, exc):
        del request
        error = _http_error(exc)
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": error.detail},
            headers=error.headers,
        )

    for error_type in (
        UnknownEngineError,
        EngineCapabilityError,
        FileNotFoundError,
        KeyError,
        ValueError,
        BrowserServingError,
    ):
        app.add_exception_handler(error_type, error_response)

    return app
