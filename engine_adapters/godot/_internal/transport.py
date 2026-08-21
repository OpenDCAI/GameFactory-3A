"""Subprocess transport for the Godot editor executable."""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import GodotClientConfig


def managed_process_kwargs() -> dict[str, Any]:
    """Create an independently stoppable process group on each platform."""

    if os.name == "nt":
        return {
            "creationflags": int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
            )
        }
    return {"start_new_session": True}


def _signal_posix_process_group(
    process: subprocess.Popen[Any],
    signal_number: int,
) -> None:
    try:
        os.killpg(process.pid, signal_number)
    except ProcessLookupError:
        if process.poll() is None:
            process.send_signal(signal_number)
    except OSError:
        if process.poll() is None:
            process.send_signal(signal_number)


def _terminate_windows_process_tree(
    process: subprocess.Popen[Any],
    timeout: float,
    kill_timeout: float,
) -> int:
    ctrl_break = getattr(signal, "CTRL_BREAK_EVENT", None)
    if ctrl_break is not None:
        try:
            os.kill(process.pid, ctrl_break)
        except (OSError, ValueError):
            pass
    try:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(1.0, timeout),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        if process.poll() is None:
            process.kill()
    try:
        process.wait(timeout=max(0.0, timeout))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=max(0.0, kill_timeout))
    return int(process.returncode)


def terminate_process_tree(
    process: subprocess.Popen[Any],
    *,
    timeout: float = 10.0,
    kill_timeout: float = 5.0,
) -> int:
    """Terminate a managed process and all descendants in its launch group."""

    if os.name == "nt":
        return _terminate_windows_process_tree(process, timeout, kill_timeout)

    _signal_posix_process_group(process, signal.SIGTERM)
    try:
        process.wait(timeout=max(0.0, timeout))
    except subprocess.TimeoutExpired:
        _signal_posix_process_group(process, signal.SIGKILL)
        process.wait(timeout=max(0.0, kill_timeout))
    else:
        # The group leader can exit before a descendant that ignores SIGTERM.
        _signal_posix_process_group(process, signal.SIGKILL)
    return int(process.returncode)


def find_godot_binary(configured: str | Path | None = None) -> Path | None:
    """Resolve an editor build, preferring an explicitly configured path."""

    if configured is not None and str(configured).strip():
        value = Path(configured).expanduser().resolve(strict=False)
        if value.is_file():
            return value
        if value.is_dir():
            candidates = (
                value / "godot4",
                value / "godot",
                value / "Godot",
                value / "Godot.app" / "Contents" / "MacOS" / "Godot",
            )
            return next((item for item in candidates if item.is_file()), value)
        return value
    for name in ("godot4", "godot", "godot-mono"):
        discovered = shutil.which(name)
        if discovered:
            return Path(discovered).resolve()
    candidates: list[Path] = []
    if platform.system() == "Windows":
        candidates.extend(
            [
                Path(os.environ.get("ProgramFiles", "C:/Program Files"))
                / "Godot"
                / "Godot.exe",
                Path(os.environ.get("LOCALAPPDATA", ""))
                / "Programs"
                / "Godot"
                / "Godot.exe",
            ]
        )
    elif platform.system() == "Darwin":
        candidates.append(Path("/Applications/Godot.app/Contents/MacOS/Godot"))
    else:
        candidates.extend([Path("/usr/local/bin/godot"), Path("/usr/bin/godot4")])
    return next((item for item in candidates if item.is_file()), None)


@dataclass(frozen=True)
class GodotProcessResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": list(self.command),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "stdout_tail": self.stdout[-4000:],
            "stderr_tail": self.stderr[-4000:],
            "duration_seconds": self.duration_seconds,
        }


class GodotTransport:
    """Construct and execute shell-free Godot commands."""

    def __init__(self, config: GodotClientConfig) -> None:
        self.config = config
        self._processes: dict[int, subprocess.Popen[Any]] = {}

    @property
    def binary(self) -> Path | None:
        return find_godot_binary(self.config.godot_executable)

    def command(
        self,
        arguments: Sequence[str],
        *,
        headless: bool = True,
        require_project: bool = True,
    ) -> list[str]:
        binary = self.binary
        if binary is None or not binary.is_file():
            raise FileNotFoundError(
                "Godot editor binary was not found; set "
                "A3GAME_GODOT_EXECUTABLE or pass godot_executable"
            )
        command = [str(binary)]
        if headless:
            command.append("--headless")
        if require_project:
            project_dir = self.config.project_dir
            project_file = self.config.project_file
            if (
                project_dir is None
                or project_file is None
                or not project_file.is_file()
            ):
                raise FileNotFoundError(
                    "project_path does not resolve to an existing project.godot"
                )
            command.extend(["--path", str(project_dir)])
        command.extend(str(item) for item in arguments)
        return command

    def run(
        self,
        arguments: Sequence[str],
        *,
        headless: bool = True,
        require_project: bool = True,
        timeout: float | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> GodotProcessResult:
        command = self.command(
            arguments,
            headless=headless,
            require_project=require_project,
        )
        started = time.monotonic()
        process = subprocess.Popen(
            command,
            cwd=(
                str(self.config.project_dir)
                if require_project and self.config.project_dir is not None
                else None
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            env={**os.environ, **dict(environment or {})},
            **managed_process_kwargs(),
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            terminate_process_tree(process, timeout=1.0, kill_timeout=1.0)
            stdout, stderr = process.communicate()
            exc.output = stdout
            exc.stdout = stdout
            exc.stderr = stderr
            raise
        return GodotProcessResult(
            command=tuple(command),
            returncode=int(process.returncode),
            stdout=stdout or "",
            stderr=stderr or "",
            duration_seconds=round(time.monotonic() - started, 3),
        )

    def version(self, timeout: float = 10.0) -> GodotProcessResult:
        return self.run(
            ["--version"],
            headless=False,
            require_project=False,
            timeout=timeout,
        )

    def launch(
        self,
        arguments: Sequence[str],
        *,
        headless: bool = False,
        environment: Mapping[str, str] | None = None,
    ) -> subprocess.Popen[Any]:
        command = self.command(
            arguments,
            headless=headless,
            require_project=True,
        )
        process = subprocess.Popen(
            command,
            cwd=str(self.config.project_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={**os.environ, **dict(environment or {})},
            **managed_process_kwargs(),
        )
        self._processes[process.pid] = process
        return process

    def stop(self, process_id: int, timeout: float = 10.0) -> int | None:
        process = self._processes.get(int(process_id))
        if process is None:
            return None
        returncode = terminate_process_tree(process, timeout=timeout)
        self._processes.pop(process.pid, None)
        return returncode
