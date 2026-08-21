"""Generic runtime session state and UDP bridge for GodotClient v1."""

from __future__ import annotations

import json
import math
import socket
import threading
import time
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from ..assets import GodotAssetsClient
from ..config import DEFAULT_WORLD_ID, GodotClientConfig
from ..contracts import GodotOperationResult


class GodotRuntimeSessionsClient:
    def __init__(
        self,
        config: GodotClientConfig,
        assets: GodotAssetsClient,
    ) -> None:
        self._config = config
        self._assets = assets
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    def join(
        self,
        *,
        world_id: str = "",
        participant_id: str = "",
        user_id: str = "",
        avatar_artifact_id: str = "",
        idle_motion_artifact_id: str = "",
        move_motion_artifact_id: str = "",
        controller_kind: str = "human",
        transform: Mapping[str, Any] | None = None,
        parameters: Mapping[str, Any] | None = None,
        require_runtime: bool = False,
    ) -> dict[str, Any]:
        operation = "runtime.sessions.join"
        try:
            avatar_path = self._artifact_path(avatar_artifact_id, {"avatar"})
            idle_path = self._artifact_path(idle_motion_artifact_id, {"motion"})
            move_path = self._artifact_path(move_motion_artifact_id, {"motion"})
            resolved_participant = str(
                participant_id or f"participant_{uuid4().hex[:12]}"
            )
            if transform is not None and not isinstance(transform, Mapping):
                raise TypeError("transform must be a mapping when provided")
            if parameters is not None and not isinstance(parameters, Mapping):
                raise TypeError("parameters must be a mapping when provided")
            normalized_transform = dict(transform) if transform is not None else None
            normalized_parameters = dict(parameters) if parameters is not None else None
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        with self._lock:
            previous = self._find_session(participant_id=resolved_participant)
            now = time.time()
            controller_id = f"controller_{uuid4().hex[:12]}"
            entity_id = str(
                previous.get("entity_id")
                if previous is not None
                else f"entity_{uuid4().hex[:12]}"
            )
            session = {
                "world_id": str(
                    previous.get("world_id")
                    if previous is not None
                    else world_id or DEFAULT_WORLD_ID
                ),
                "participant_id": resolved_participant,
                "user_id": str(
                    user_id
                    or (previous.get("user_id", "") if previous is not None else "")
                ),
                "controller_id": controller_id,
                "controller_kind": str(controller_kind),
                "entity_id": entity_id,
                "avatar_artifact_id": str(
                    avatar_artifact_id
                    or (
                        previous.get("avatar_artifact_id", "")
                        if previous is not None
                        else ""
                    )
                ),
                "avatar_resource_path": str(
                    avatar_path
                    or (
                        previous.get("avatar_resource_path", "")
                        if previous is not None
                        else ""
                    )
                ),
                "idle_motion_artifact_id": str(
                    idle_motion_artifact_id
                    or (
                        previous.get("idle_motion_artifact_id", "")
                        if previous is not None
                        else ""
                    )
                ),
                "idle_motion_resource_path": str(
                    idle_path
                    or (
                        previous.get("idle_motion_resource_path", "")
                        if previous is not None
                        else ""
                    )
                ),
                "move_motion_artifact_id": str(
                    move_motion_artifact_id
                    or (
                        previous.get("move_motion_artifact_id", "")
                        if previous is not None
                        else ""
                    )
                ),
                "move_motion_resource_path": str(
                    move_path
                    or (
                        previous.get("move_motion_resource_path", "")
                        if previous is not None
                        else ""
                    )
                ),
                "transform": dict(
                    normalized_transform
                    if normalized_transform is not None
                    else previous.get("transform", {})
                    if previous is not None
                    else {}
                ),
                "parameters": dict(
                    normalized_parameters
                    if normalized_parameters is not None
                    else previous.get("parameters", {})
                    if previous is not None
                    else {}
                ),
                "active": True,
                "online": True,
                "created_at": now,
                "last_seen_at": now,
                "last_input": {},
            }
            try:
                json.dumps(session, separators=(",", ":"), allow_nan=False)
            except Exception as exc:
                return GodotOperationResult.failure(
                    operation, f"{type(exc).__name__}: {exc}"
                ).to_dict()
            bridge = self._send({"operation": "session.join", **session})
            if not bridge.get("ok") and (
                require_runtime or bridge.get("reachable") is True
            ):
                return GodotOperationResult.failure(
                    operation,
                    "Godot runtime bridge rejected or did not acknowledge the session",
                    payload={"session": session, "bridge": bridge},
                ).to_dict()
            for old_session in self._sessions.values():
                if old_session.get("participant_id") == resolved_participant:
                    old_session["active"] = False
                    old_session["online"] = False
            self._sessions[controller_id] = session
        return GodotOperationResult.success(
            operation,
            warnings=(
                []
                if bridge.get("ok")
                else [
                    "Session is registered locally; the Godot runtime bridge is not reachable"
                ]
            ),
            payload={**session, "bridge": bridge},
        ).to_dict()

    def leave(
        self,
        *,
        participant_id: str = "",
        controller_id: str = "",
    ) -> dict[str, Any]:
        operation = "runtime.sessions.leave"
        try:
            normalized_participant = str(participant_id or "")
            normalized_controller = str(controller_id or "")
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        with self._lock:
            session = self._find_session(
                normalized_participant,
                normalized_controller,
            )
            if session is None:
                return GodotOperationResult.failure(
                    operation, "Runtime session was not found"
                ).to_dict()
            bridge = self._send(
                {
                    "operation": "session.leave",
                    "controller_id": session["controller_id"],
                }
            )
            if not bridge.get("ok") and bridge.get("reachable") is True:
                return GodotOperationResult.failure(
                    operation,
                    "Godot runtime bridge rejected or did not acknowledge leave",
                    payload={"session": dict(session), "bridge": bridge},
                ).to_dict()
            session["active"] = False
            session["online"] = False
            session["last_seen_at"] = time.time()
            snapshot = dict(session)
        return GodotOperationResult.success(
            operation,
            warnings=(
                [] if bridge.get("ok") else ["Godot runtime did not acknowledge leave"]
            ),
            payload={**snapshot, "bridge": bridge},
        ).to_dict()

    def heartbeat(self, controller_id: str) -> dict[str, Any]:
        operation = "runtime.sessions.heartbeat"
        try:
            normalized_id = str(controller_id)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        with self._lock:
            session = self._sessions.get(normalized_id)
            if session is None:
                return GodotOperationResult.failure(
                    operation,
                    f"Unknown controller_id: {normalized_id}",
                ).to_dict()
            if not session.get("active") or not session.get("online"):
                return GodotOperationResult.failure(
                    operation,
                    f"Controller is not active: {normalized_id}",
                    payload=dict(session),
                ).to_dict()
            session["last_seen_at"] = time.time()
            return GodotOperationResult.success(
                operation, payload=dict(session)
            ).to_dict()

    def apply_input(
        self,
        controller_id: str,
        *,
        move_x: float = 0.0,
        move_y: float = 0.0,
        run: bool = False,
        jump: bool = False,
        yaw: float = 0.0,
        pitch: float = 0.0,
        seq: int = 0,
        require_runtime: bool = False,
    ) -> dict[str, Any]:
        operation = "runtime.sessions.apply_input"
        try:
            normalized_id = str(controller_id)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        try:
            input_state = {
                "move_x": float(move_x),
                "move_y": float(move_y),
                "run": bool(run),
                "jump": bool(jump),
                "yaw": float(yaw),
                "pitch": float(pitch),
                "seq": int(seq),
                "timestamp": time.time(),
            }
            numeric_values = (
                input_state["move_x"],
                input_state["move_y"],
                input_state["yaw"],
                input_state["pitch"],
            )
            if not all(math.isfinite(value) for value in numeric_values):
                raise ValueError("runtime input values must be finite numbers")
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        with self._lock:
            session = self._sessions.get(normalized_id)
            if session is None:
                return GodotOperationResult.failure(
                    operation, f"Unknown controller_id: {normalized_id}"
                ).to_dict()
            if not session.get("active") or not session.get("online"):
                return GodotOperationResult.failure(
                    operation,
                    f"Controller is not bound to an active entity: {normalized_id}",
                    payload={"controller_id": normalized_id},
                ).to_dict()
            bridge = self._send(
                {
                    "operation": "session.input",
                    "controller_id": normalized_id,
                    "input": input_state,
                }
            )
            if not bridge.get("ok") and (
                require_runtime or bridge.get("reachable") is True
            ):
                return GodotOperationResult.failure(
                    operation,
                    "Godot runtime bridge rejected or did not acknowledge input",
                    payload={"input": input_state, "bridge": bridge},
                ).to_dict()
            session["last_input"] = input_state
            session["last_seen_at"] = time.time()
            entity_id = str(session["entity_id"])
        return GodotOperationResult.success(
            operation,
            warnings=(
                []
                if bridge.get("ok")
                else ["Input was recorded locally but not acknowledged by Godot"]
            ),
            payload={
                "controller_id": normalized_id,
                "entity_id": entity_id,
                "input": input_state,
                "bridge": bridge,
            },
        ).to_dict()

    def snapshot(self, *, world_id: str = "") -> dict[str, Any]:
        operation = "runtime.sessions.snapshot"
        try:
            normalized_world_id = str(world_id or "")
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        with self._lock:
            sessions = [
                dict(item)
                for item in self._sessions.values()
                if not normalized_world_id
                or str(item.get("world_id") or "") == normalized_world_id
            ]
        return GodotOperationResult.success(
            operation,
            payload={
                "world_id": normalized_world_id,
                "sessions": sessions,
                "count": len(sessions),
                "active_count": sum(
                    bool(item.get("active") and item.get("online")) for item in sessions
                ),
            },
        ).to_dict()

    def reset_world(self, *, world_id: str = "") -> dict[str, Any]:
        operation = "runtime.sessions.reset_world"
        try:
            normalized_world_id = str(world_id or DEFAULT_WORLD_ID)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        with self._lock:
            bridge = self._send(
                {"operation": "world.reset", "world_id": normalized_world_id}
            )
            if not bridge.get("ok") and bridge.get("reachable") is True:
                return GodotOperationResult.failure(
                    operation,
                    "Godot runtime bridge rejected or did not acknowledge reset",
                    payload={"world_id": normalized_world_id, "bridge": bridge},
                ).to_dict()
            controller_ids = [
                controller_id
                for controller_id, session in self._sessions.items()
                if session.get("world_id") == normalized_world_id
            ]
            for controller_id in controller_ids:
                self._sessions.pop(controller_id, None)
            sessions_remaining = len(self._sessions)
        return GodotOperationResult.success(
            operation,
            warnings=(
                [] if bridge.get("ok") else ["Godot runtime did not acknowledge reset"]
            ),
            payload={
                "world_id": normalized_world_id,
                "removed_sessions": len(controller_ids),
                "sessions_remaining": sessions_remaining,
                "bridge": bridge,
            },
        ).to_dict()

    def clear_entity(
        self,
        *,
        participant_id: str = "",
        controller_id: str = "",
        entity_id: str = "",
        destroy_actor: bool = True,
    ) -> dict[str, Any]:
        operation = "runtime.sessions.clear_entity"
        try:
            if not isinstance(destroy_actor, bool):
                raise TypeError("destroy_actor must be a boolean")
            normalized_participant = str(participant_id or "")
            normalized_controller = str(controller_id or "")
            normalized_entity = str(entity_id or "")
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        with self._lock:
            session = self._find_session(
                normalized_participant,
                normalized_controller,
                normalized_entity,
            )
            if session is None:
                return GodotOperationResult.failure(
                    operation, "Runtime entity was not found"
                ).to_dict()
            bridge = self._send(
                {
                    "operation": "entity.clear",
                    "entity_id": session["entity_id"],
                    "destroy_actor": destroy_actor,
                }
            )
            if not bridge.get("ok") and bridge.get("reachable") is True:
                return GodotOperationResult.failure(
                    operation,
                    "Godot runtime bridge rejected or did not acknowledge clear",
                    payload={
                        "session": dict(session),
                        "destroy_actor": destroy_actor,
                        "bridge": bridge,
                    },
                ).to_dict()
            resolved_entity_id = str(session["entity_id"])
            controller_ids = [
                key
                for key, item in self._sessions.items()
                if str(item.get("entity_id") or "") == resolved_entity_id
            ]
            for key in controller_ids:
                self._sessions.pop(key, None)
            sessions_remaining = len(self._sessions)
        return GodotOperationResult.success(
            operation,
            warnings=(
                [] if bridge.get("ok") else ["Godot runtime did not acknowledge clear"]
            ),
            payload={
                "removed": bool(controller_ids),
                "removed_sessions": len(controller_ids),
                "sessions_remaining": sessions_remaining,
                "destroy_actor": destroy_actor,
                "session": session,
                "bridge": bridge,
            },
        ).to_dict()

    def probe(self, timeout: float = 0.25) -> dict[str, Any]:
        return self._send({"operation": "status"}, timeout=timeout)

    def _artifact_path(self, artifact_id: str, expected_types: set[str]) -> str:
        value = str(artifact_id or "").strip()
        if not value:
            return ""
        record = self._assets._registry.find(value)
        if record is None:
            raise ValueError(f"Unknown artifact_id: {artifact_id}")
        if record.type not in expected_types:
            raise ValueError(
                f"Artifact {artifact_id} has type {record.type!r}; "
                f"expected: {', '.join(sorted(expected_types))}"
            )
        return record.backend_path

    def _find_session(
        self,
        participant_id: str = "",
        controller_id: str = "",
        entity_id: str = "",
    ) -> dict[str, Any] | None:
        with self._lock:
            if controller_id:
                return self._sessions.get(str(controller_id))
            if participant_id:
                candidates = [
                    session
                    for session in self._sessions.values()
                    if session.get("participant_id") == participant_id
                ]
                if candidates:
                    active = [
                        session
                        for session in candidates
                        if session.get("active") and session.get("online")
                    ]
                    return max(
                        active or candidates,
                        key=lambda item: float(item.get("last_seen_at") or 0.0),
                    )
                return None
            if entity_id:
                candidates = [
                    session
                    for session in self._sessions.values()
                    if session.get("entity_id") == entity_id
                ]
                if candidates:
                    return max(
                        candidates,
                        key=lambda item: float(item.get("last_seen_at") or 0.0),
                    )
        return None

    def _send(
        self,
        message: dict[str, Any],
        *,
        timeout: float = 0.25,
    ) -> dict[str, Any]:
        request_id = uuid4().hex
        payload = {**message, "request_id": request_id}
        sock: socket.socket | None = None
        response_received = False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(float(timeout))
            sock.sendto(
                json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                (self._config.runtime_host, self._config.runtime_port),
            )
            response, _address = sock.recvfrom(65535)
            response_received = True
            decoded = json.loads(response.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ValueError("Runtime response must be an object")
            if str(decoded.get("request_id") or "") != request_id:
                raise ValueError("Runtime response request_id mismatch")
            if not isinstance(decoded.get("ok"), bool):
                raise ValueError("Runtime response ok must be a boolean")
            if str(decoded.get("operation") or "") != str(
                message.get("operation") or ""
            ):
                raise ValueError("Runtime response operation mismatch")
            return {**decoded, "reachable": True}
        except Exception as exc:
            return {
                "ok": False,
                "reachable": response_received,
                "error": f"{type(exc).__name__}: {exc}",
                "host": self._config.runtime_host,
                "port": self._config.runtime_port,
            }
        finally:
            if sock is not None:
                sock.close()
