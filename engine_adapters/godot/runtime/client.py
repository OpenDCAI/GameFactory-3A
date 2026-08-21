"""Stable Godot editor, project, and exported-player lifecycle."""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from .._internal import (
    GodotTransport,
    managed_process_kwargs,
    terminate_process_tree,
)
from ..assets import GodotAssetsClient
from ..config import GodotClientConfig
from ..contracts import GodotOperationResult
from .sessions import GodotRuntimeSessionsClient


def _normalize_extra_args(extra_args: Sequence[str]) -> list[str]:
    if extra_args is None or isinstance(extra_args, (str, bytes, bytearray)):
        raise TypeError("extra_args must be a sequence of arguments")
    return [str(item) for item in extra_args]


class GodotRuntimeClient:
    def __init__(
        self,
        config: GodotClientConfig,
        assets: GodotAssetsClient,
    ) -> None:
        self._config = config
        self._transport = GodotTransport(config)
        self._players: dict[int, subprocess.Popen[Any]] = {}
        self.sessions = GodotRuntimeSessionsClient(config, assets)

    def launch_editor(
        self,
        *,
        scene_path: str = "",
        extra_args: Sequence[str] = (),
        dry_run: bool = False,
    ) -> dict[str, Any]:
        operation = "runtime.launch_editor"
        arguments = ["--editor"]
        if scene_path:
            try:
                arguments.append(self._scene_argument(scene_path))
            except Exception as exc:
                return GodotOperationResult.failure(
                    operation, f"{type(exc).__name__}: {exc}"
                ).to_dict()
        try:
            arguments.extend(_normalize_extra_args(extra_args))
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        return self._launch(
            operation,
            arguments,
            process_type="godot_editor_process",
            headless=False,
            dry_run=dry_run,
        )

    def stop_editor(self, process_id: int) -> dict[str, Any]:
        return self._stop("runtime.stop_editor", process_id)

    def launch_game(
        self,
        *,
        scene_path: str = "",
        headless: bool = False,
        extra_args: Sequence[str] = (),
        dry_run: bool = False,
    ) -> dict[str, Any]:
        operation = "runtime.launch_game"
        arguments = []
        if scene_path:
            try:
                arguments.append(self._scene_argument(scene_path))
            except Exception as exc:
                return GodotOperationResult.failure(
                    operation, f"{type(exc).__name__}: {exc}"
                ).to_dict()
        try:
            arguments.extend(_normalize_extra_args(extra_args))
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        return self._launch(
            operation,
            arguments,
            process_type="godot_game_process",
            headless=headless,
            dry_run=dry_run,
        )

    def stop_game(self, process_id: int) -> dict[str, Any]:
        return self._stop("runtime.stop_game", process_id)

    def launch_player(
        self,
        build_path: str | Path,
        *,
        extra_args: Sequence[str] = (),
        dry_run: bool = False,
    ) -> dict[str, Any]:
        operation = "runtime.launch_player"
        try:
            executable = Path(build_path).expanduser().resolve(strict=False)
            if executable.is_dir() and executable.suffix.lower() == ".app":
                directory = executable / "Contents" / "MacOS"
                binaries = (
                    sorted(item for item in directory.iterdir() if item.is_file())
                    if directory.is_dir()
                    else []
                )
                executable = binaries[0] if binaries else executable
            normalized_args = _normalize_extra_args(extra_args)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        if not executable.is_file():
            return GodotOperationResult.failure(
                operation, f"Godot player executable was not found: {executable}"
            ).to_dict()
        command = [str(executable), *normalized_args]
        payload = {
            "command": command,
            "build_path": str(build_path),
            "dry_run": dry_run,
        }
        if dry_run:
            return GodotOperationResult.success(operation, payload=payload).to_dict()
        try:
            process = subprocess.Popen(
                command,
                cwd=str(executable.parent),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={
                    **os.environ,
                    "A3GAME_GODOT_RUNTIME_HOST": self._config.runtime_host,
                    "A3GAME_GODOT_RUNTIME_PORT": str(self._config.runtime_port),
                },
                **managed_process_kwargs(),
            )
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}", payload=payload
            ).to_dict()
        returncode = self._startup_returncode(process)
        if returncode is not None:
            try:
                terminate_process_tree(process)
            except Exception as exc:
                return GodotOperationResult.failure(
                    operation,
                    f"Godot player exited during startup (code {returncode}); "
                    f"process tree cleanup failed: {type(exc).__name__}: {exc}",
                    payload={**payload, "returncode": returncode},
                ).to_dict()
            return GodotOperationResult.failure(
                operation,
                f"Godot player exited during startup (code {returncode})",
                payload={**payload, "returncode": returncode},
            ).to_dict()
        self._players[process.pid] = process
        payload["process_id"] = process.pid
        return GodotOperationResult.success(
            operation,
            artifacts=[
                {
                    "type": "godot_player_process",
                    "path": str(executable),
                    "state": "running",
                    "process_id": process.pid,
                }
            ],
            payload=payload,
        ).to_dict()

    def stop_player(self, process_id: int) -> dict[str, Any]:
        operation = "runtime.stop_player"
        try:
            normalized_id = int(process_id)
        except (TypeError, ValueError) as exc:
            return GodotOperationResult.failure(
                operation, f"Invalid Godot player process_id: {process_id!r}: {exc}"
            ).to_dict()
        process = self._players.get(normalized_id)
        if process is None:
            return GodotOperationResult.failure(
                operation, f"Unknown Godot player process_id: {process_id}"
            ).to_dict()
        try:
            returncode = terminate_process_tree(process)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                "Godot player process tree cleanup failed: "
                f"{type(exc).__name__}: {exc}",
                payload={"process_id": normalized_id},
            ).to_dict()
        self._players.pop(normalized_id, None)
        return GodotOperationResult.success(
            operation,
            payload={"process_id": normalized_id, "returncode": returncode},
        ).to_dict()

    def _launch(
        self,
        operation: str,
        arguments: list[str],
        *,
        process_type: str,
        headless: bool,
        dry_run: bool,
    ) -> dict[str, Any]:
        try:
            command = self._transport.command(arguments, headless=headless)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        payload = {
            "command": command,
            "cwd": str(self._config.project_dir or ""),
            "runtime_host": self._config.runtime_host,
            "runtime_port": self._config.runtime_port,
            "dry_run": dry_run,
        }
        if dry_run:
            return GodotOperationResult.success(operation, payload=payload).to_dict()
        try:
            process = self._transport.launch(
                arguments,
                headless=headless,
                environment={
                    "A3GAME_GODOT_RUNTIME_HOST": self._config.runtime_host,
                    "A3GAME_GODOT_RUNTIME_PORT": str(self._config.runtime_port),
                },
            )
            returncode = self._startup_returncode(process)
            if returncode is not None:
                self._transport.stop(process.pid)
                return GodotOperationResult.failure(
                    operation,
                    f"Godot process exited during startup (code {returncode})",
                    payload={**payload, "returncode": returncode},
                ).to_dict()
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}", payload=payload
            ).to_dict()
        payload["process_id"] = process.pid
        return GodotOperationResult.success(
            operation,
            artifacts=[
                {
                    "type": process_type,
                    "path": command[0],
                    "state": "running",
                    "process_id": process.pid,
                }
            ],
            payload=payload,
        ).to_dict()

    @staticmethod
    def _startup_returncode(
        process: subprocess.Popen[Any],
        grace_period: float = 0.1,
    ) -> int | None:
        """Observe immediate startup failure without blocking a live process."""

        deadline = time.monotonic() + max(0.0, grace_period)
        while True:
            returncode = process.poll()
            if returncode is not None or time.monotonic() >= deadline:
                return returncode
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))

    def _stop(self, operation: str, process_id: int) -> dict[str, Any]:
        try:
            normalized_id = int(process_id)
            returncode = self._transport.stop(normalized_id)
        except (TypeError, ValueError) as exc:
            return GodotOperationResult.failure(
                operation, f"Invalid Godot process_id: {process_id!r}: {exc}"
            ).to_dict()
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"Godot process tree cleanup failed: {type(exc).__name__}: {exc}",
                payload={"process_id": normalized_id},
            ).to_dict()
        if returncode is None:
            return GodotOperationResult.failure(
                operation,
                "GodotClient can stop only processes launched by the same "
                f"client; unknown process_id: {process_id}",
            ).to_dict()
        return GodotOperationResult.success(
            operation,
            payload={"process_id": normalized_id, "returncode": returncode},
        ).to_dict()

    def _scene_argument(self, value: str) -> str:
        raw = str(value or "").strip().replace("\\", "/")
        if raw.startswith("res://"):
            relative = PurePosixPath(raw[len("res://") :])
        else:
            relative = PurePosixPath(raw)
        if not raw or relative.is_absolute() or ".." in relative.parts:
            raise ValueError(
                "scene_path must be a non-traversing res:// or project-relative path"
            )
        project_dir = self._config.project_dir
        if project_dir is None:
            raise ValueError("project_path is not configured")
        project_root = project_dir.resolve()
        scene = (project_root / Path(*relative.parts)).resolve(strict=False)
        try:
            resolved_relative = scene.relative_to(project_root)
        except ValueError as exc:
            raise ValueError(
                f"Godot scene escaped the configured project: {value}"
            ) from exc
        if not scene.is_file():
            raise ValueError(f"Godot scene was not found: {scene}")
        return "res://" + resolved_relative.as_posix()
