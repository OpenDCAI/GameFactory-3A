"""Record a playtest of a UE5 project through a dedicated Editor instance.

This follows the ``record.md`` contract for Layer 3 (Play Session): the
adapter owns launching the game, sending real input events, and capturing
frames; the pipeline owns the session lifecycle and record placement.

Recording is split across two cooperating processes:

1. **Editor side** — ``recorder.py`` runs inside the Editor's embedded Python
   via ``-ExecutePythonScript`` (the target project must enable the Python
   Editor Script Plugin). It starts Play-In-Editor, captures Game-view frames
   with the ``HighResShot`` console command, writes the editor-side report,
   ends PIE, and quits the Editor.

2. **Host side** (this client) — launches that Editor, waits for the
   ``play_started.json`` marker, then drives the player input surface: on
   macOS it posts real keyboard events through System Events, and writes the
   exact input trace to ``actions.jsonl``.

When the project has a staged game build (``Binaries/Win64`` plus
``Content/Paks``), the client records the packaged game process directly
instead of the PIE flow: the compiled ``A3GamePlayable`` plugin captures
frames in-engine (``UA3GamePlaytestRecorderSubsystem``), writes the start
marker and a native report, and exits the game when the take ends — the same
in-engine recording model as the Unity and Godot adapters. Unstaged projects
fall back to the editor binary in ``-game`` mode, which needs the editor
target rebuilt so the same plugin module is in
``UnrealEditor-A3GamePlayable.dll``.

The output follows the standard playtest layout (``frames/``, ``video.mp4``,
``report.json``, ``actions.jsonl``). Frame capture quality and engine-side
state snapshots depend on the target project's plugins; the recorder records
what actually happened rather than assuming either is available.
"""

from __future__ import annotations

import json
import ctypes
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..config import UEClientConfig
from ..contracts import UEOperationResult

_OPERATION = "playtest.record"
_REPORT_SCHEMA = "gamefactory3a.ue5.playtest_report.v1"
_RECORDER = Path(__file__).with_name("recorder.py")

#: Allowlisted player-level action names (record.md contract), matching the
#: Godot/Unity adapters so scenarios stay portable across engines.
ALLOWED_ACTIONS = frozenset({
    "move", "look", "jump", "attack", "interact",
    "dash", "pause", "restart", "wait",
})

DEFAULT_KEY_BINDINGS = {
    "jump": "space",
    "attack": "j",
    "interact": "e",
    "dash": "leftshift",
    "pause": "escape",
    "restart": "enter",
}

_KEY_CODES = {
    "space": 49,
    "enter": 36,
    "return": 36,
    "escape": 53,
    "leftshift": 56,
    "left_arrow": 123,
    "right_arrow": 124,
    "down_arrow": 125,
    "up_arrow": 126,
}

_MOVE_KEYS = {"+x": "d", "-x": "a", "+y": "w", "-y": "s"}
_LOOK_KEYS = {"+yaw": "right_arrow", "-yaw": "left_arrow",
              "+pitch": "up_arrow", "-pitch": "down_arrow"}

_WINDOWS_VK = {
    "space": 0x20, "enter": 0x0D, "escape": 0x1B, "leftshift": 0xA0,
    "w": 0x57, "a": 0x41, "s": 0x53, "d": 0x44, "j": 0x4A,
    "e": 0x45, "u": 0x55, "h": 0x48,
}

#: A fighting-style default take matching the Unity/Godot adapters: survive
#: the countdown, approach, attack, trade with the AI opponent.
DEFAULT_ACTIONS: list[dict[str, Any]] = [
    {"action": "wait", "duration_ms": 3500},
    {"action": "move", "x": 1, "y": 0, "duration_ms": 900},
    {"action": "attack", "duration_ms": 200},
    {"action": "wait", "duration_ms": 700},
    {"action": "attack", "duration_ms": 200},
    {"action": "wait", "duration_ms": 700},
    {"action": "attack", "duration_ms": 200},
    {"action": "wait", "duration_ms": 900},
    {"action": "move", "x": -1, "y": 0, "duration_ms": 600},
    {"action": "attack", "duration_ms": 200},
    {"action": "wait", "duration_ms": 3800},
]

_EDITOR_BOOT_TIMEOUT = 600.0
_EDITOR_SHUTDOWN_TIMEOUT = 240.0
_GAME_BOOT_TIMEOUT = 600.0
_GAME_SHUTDOWN_TIMEOUT = 60.0


def _editor_binary(ue_root: Path) -> Path:
    if os.name == "nt":
        return ue_root / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"
    if sys_platform() == "Darwin":
        return (
            ue_root / "Engine" / "Binaries" / "Mac" / "UnrealEditor.app"
            / "Contents" / "MacOS" / "UnrealEditor"
        )
    return ue_root / "Engine" / "Binaries" / "Linux" / "UnrealEditor"


def sys_platform() -> str:
    import platform

    return platform.system()


class UE5PlaytestClient:
    """Launch a dedicated UE5 Editor and record one playtest take."""

    def __init__(self, config: UEClientConfig) -> None:
        self._config = config

    def record(
        self,
        *,
        output_dir: str | Path,
        map_path: str = "",
        scenario: str | Path | None = None,
        action_plan: list[dict[str, Any]] | None = None,
        duration: float = 12.0,
        fps: int = 20,
        warmup: float = 0.0,
        timeout: float | None = None,
        ffmpeg: str | Path | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Record one playtest into ``output_dir``.

        Args:
            map_path: Optional map to load before PIE, e.g. ``/Game/Arena``.
                Empty plays the project's default map.
            scenario: Path to a scenario JSON file (``{"actions": [...]}``).
            action_plan: Inline action list overriding the default take.
            duration: Take length in seconds.
            fps: Capture frame rate.
            warmup: Seconds PIE runs before the scenario starts.
            timeout: Maximum seconds for the whole take after PIE starts.
            ffmpeg: Optional ffmpeg binary for encoding ``video.mp4``.
            dry_run: Validate and print the plan without running.
        """
        ue_root = self._config.ue_root
        project_file = self._config.project_file
        if ue_root is None:
            return self._fail("ue_root is not configured")
        if project_file is None or not project_file.is_file():
            return self._fail("project_path must resolve to an existing .uproject file")
        editor = _editor_binary(ue_root)
        if not editor.is_file():
            return self._fail(f"Unreal Editor was not found: {editor}")
        if not _RECORDER.is_file():
            return self._fail(f"Playtest recorder is missing: {_RECORDER}")
        if duration <= 0 or fps <= 0:
            return self._fail("duration and fps must be positive")
        if timeout is None or timeout <= 0:
            timeout = 900.0

        actions, actions_error = self._load_actions(scenario, action_plan)
        if actions_error:
            return self._fail(actions_error)

        out = Path(output_dir).expanduser().resolve(strict=False)
        encoder = Path(ffmpeg).expanduser() if ffmpeg else None
        events = self._timeline(actions)
        plan_seconds = sum(
            int(item.get("duration_ms", 100)) for item in actions
        ) / 1000.0
        if plan_seconds > duration:
            return self._fail(
                f"scenario needs {plan_seconds:.1f}s but duration is {duration:.1f}s"
            )

        game_binary = self._game_binary(project_file)
        # A bare Development exe only boots from a staged build: without
        # Content/Paks the runtime shader library is missing and the game
        # exits before the first frame. Unstaged projects run through the
        # editor binary in -game mode instead.
        launch_as_editor_game = (
            game_binary is None
            or not (project_file.parent / "Content" / "Paks").is_dir()
        )
        if launch_as_editor_game:
            game_binary = _editor_binary(ue_root)
        if game_binary is not None and game_binary.is_file():
            return self._record_game(
                game_binary=game_binary,
                project_file=project_file,
                map_path=map_path,
                output_dir=out,
                actions=actions,
                events=events,
                duration=duration,
                fps=fps,
                warmup=warmup,
                timeout=timeout,
                ffmpeg=encoder,
                dry_run=dry_run,
                launch_as_editor_game=launch_as_editor_game,
            )

        command = [
            str(editor),
            str(project_file),
        ]
        if str(map_path or "").strip():
            command.append(str(map_path).strip())
        command.extend([
            "-ExecCmds=WebControl.StartServer "
            f"{self._config.port}",
            "-NoSplash",
            "-Log",
            "-A3GameRuntimeInputPort=" f"{self._config.runtime_port}",
            f"-ExecutePythonScript={_RECORDER}",
            "--a3-playtest-output", str(out),
            "--a3-playtest-fps", str(int(fps)),
            "--a3-playtest-duration", str(float(duration)),
            "--a3-playtest-warmup", str(float(warmup)),
        ])
        if map_path:
            command.extend(["--a3-playtest-map", str(map_path).strip()])

        payload: dict[str, Any] = {
            "engine": "ue5",
            "project_file": str(project_file),
            "map_path": str(map_path or ""),
            "output_dir": str(out),
            "report_path": str(out / "report.json"),
            "duration": duration,
            "fps": fps,
            "warmup": warmup,
            "action_count": len(actions),
            "actions": actions,
            "events": [
                {"t_ms": event[0], "phase": event[1], "key": event[2]}
                for event in events
            ],
            "command": command,
            "editor_binary": str(editor),
            "ffmpeg": str(encoder) if encoder else None,
            "input_transport": "macos_system_events",
        }
        if dry_run:
            return UEOperationResult.success(_OPERATION, payload=payload).to_dict()

        input_error = self._check_input_driver()
        if input_error:
            return self._fail(input_error, payload)

        out.mkdir(parents=True, exist_ok=True)
        (out / "_scenario.json").write_text(
            json.dumps(
                {"actions": actions, "fps": fps, "duration": duration},
                indent=2,
            ),
            encoding="utf-8",
        )

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            process = subprocess.Popen(
                command,
                cwd=str(project_file.parent),
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            return self._fail(f"failed to launch Unreal Editor: {exc}", payload)
        payload["process_id"] = process.pid

        marker = out / "play_started.json"
        if not self._wait_for_marker(marker, process, payload, _EDITOR_BOOT_TIMEOUT):
            self._stop_process(process)
            return self._fail(
                payload.get("error") or "Unreal Editor did not start PIE",
                payload,
            )

        started = time.monotonic()
        warnings = self._drive_events(process.pid, events, started, out, warmup)

        if not self._wait_for_exit(process, started + duration + _EDITOR_SHUTDOWN_TIMEOUT):
            warnings.append("Editor did not exit on time; terminating")
            self._stop_process(process)

        editor_report = self._read_editor_report(out, payload)
        frames = sorted((out / "frames").glob("f*.png")) if (out / "frames").is_dir() else []
        video_path = self._encode_video(out, frames, fps, encoder)
        report = {
            "schema_version": _REPORT_SCHEMA,
            "engine": "ue5",
            "status": "passed" if frames else "failed",
            "url": str(project_file),
            "output_dir": str(out),
            "map": str(map_path or ""),
            "fps": fps,
            "requested_seconds": duration,
            "recorded_seconds": len(frames) / fps if fps > 0 else 0,
            "warmup": warmup,
            "action_count": len(actions),
            "executed_actions": [item["action"] for item in actions],
            "frames": len(frames),
            "video": str(video_path) if video_path else None,
            "game_state": None,
            "warnings": warnings,
            "errors": [],
            "editor_report": editor_report,
        }
        report_path = out / "report.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        payload["report"] = report

        if not frames:
            return self._fail(
                (editor_report or {}).get("errors") or ["no frames were captured"],
                payload,
            )

        artifacts = [
            {"type": "playtest_report", "path": str(report_path)},
            {"type": "playtest_actions", "path": str(out / "actions.jsonl")},
            {"type": "playtest_frames", "path": str(out / "frames")},
        ]
        if video_path:
            artifacts.append({"type": "playtest_video", "path": str(video_path)})
        return UEOperationResult.success(
            _OPERATION,
            artifacts=artifacts,
            warnings=[str(item) for item in report["warnings"]],
            payload=payload,
        ).to_dict()

    # ── Scenario handling ─────────────────────────────────────────────────

    @staticmethod
    def _game_binary(project_file: Path) -> Path | None:
        binary_root = project_file.parent / "Binaries" / "Win64"
        if not binary_root.is_dir():
            return None
        candidates = sorted(binary_root.glob("*.exe"))
        preferred = binary_root / f"{project_file.stem}.exe"
        if preferred.is_file():
            return preferred
        return candidates[0] if len(candidates) == 1 else None

    def _record_game(
        self,
        *,
        game_binary: Path,
        project_file: Path,
        map_path: str,
        output_dir: Path,
        actions: list[dict[str, Any]],
        events: list[tuple[int, str, str]],
        duration: float,
        fps: int,
        warmup: float,
        timeout: float,
        ffmpeg: Path | None,
        dry_run: bool,
        launch_as_editor_game: bool = False,
    ) -> dict[str, Any]:
        """Record one take through the game process itself.

        The A3GamePlayable plugin compiled into the game reads the
        ``-A3Playtest*`` arguments, captures viewport frames, writes
        ``play_started.json`` on its first tick and ``_editor_report.json``
        when the take ends, then exits the game. The host only injects
        player input between those two markers.
        """
        payload: dict[str, Any] = {
            "engine": "ue5",
            "mode": "game",
            "game_binary": str(game_binary),
            "launch_as_editor_game": launch_as_editor_game,
            "project_file": str(project_file),
            "map_path": str(map_path or ""),
            "output_dir": str(output_dir),
            "report_path": str(output_dir / "report.json"),
            "duration": duration,
            "fps": fps,
            "warmup": warmup,
            "actions": actions,
            "input_transport": "windows_sendinput" if os.name == "nt" else "trace_only",
            "native_capture": True,
        }
        command = [str(game_binary)]
        if launch_as_editor_game:
            command.append(str(project_file))
        if map_path:
            command.append(str(map_path))
        command.extend([
            f"-A3PlaytestOutput={output_dir}",
            f"-A3PlaytestFps={int(fps)}",
            f"-A3PlaytestDuration={float(duration)}",
            "-windowed", "-NoSplash", "-log",
        ])
        if launch_as_editor_game:
            command.append("-game")
        payload["command"] = command
        if dry_run:
            return UEOperationResult.success(_OPERATION, payload=payload).to_dict()
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "frames").mkdir(exist_ok=True)
        actions_path = output_dir / "actions.jsonl"
        with actions_path.open("w", encoding="utf-8") as handle:
            for index, action in enumerate(actions, 1):
                handle.write(json.dumps({"seq": index, "action": action["action"], **action}) + "\n")
        try:
            process = subprocess.Popen(
                command,
                cwd=str(project_file.parent),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                start_new_session=os.name != "nt",
            )
        except OSError as exc:
            return self._fail(f"failed to launch game: {exc}", payload)
        payload["process_id"] = process.pid
        # The in-game recorder writes play_started.json on its first tick;
        # input is aligned to that moment instead of process launch so game
        # boot does not eat the action timeline.
        marker = output_dir / "play_started.json"
        if not self._wait_for_marker(
            marker,
            process,
            payload,
            _GAME_BOOT_TIMEOUT,
            exit_error=(
                "the game exited before recording started; check the "
                "project's Saved/Logs for a startup error (an unstaged "
                "Development binary exits immediately when the runtime "
                "shader library is missing)"
            ),
            timeout_error=(
                f"the game did not begin recording within {_GAME_BOOT_TIMEOUT:.0f}s; "
                "the launched binary likely predates the A3GamePlayable "
                "playtest recorder — rebuild the project and retry"
            ),
        ):
            payload["game_log_tail"] = _game_log_tail(project_file)
            self._stop_process(process)
            return self._fail(
                payload.get("error") or "game did not start recording",
                payload,
            )
        started = time.monotonic()
        warnings = self._drive_events(process.pid, events, started, output_dir, warmup)
        # The in-game recorder exits the game when the take ends; the kill
        # below is only a fallback for games that ignore the exit request.
        deadline = started + min(
            float(timeout),
            duration + warmup + _GAME_SHUTDOWN_TIMEOUT,
        )
        if not self._wait_for_exit(process, deadline):
            warnings.append("game did not exit after the take; terminating")
            self._stop_process(process)
        frames = sorted((output_dir / "frames").glob("f*.png"))
        native_report = self._read_editor_report(output_dir, payload)
        video_path = self._encode_video(output_dir, frames, fps, ffmpeg)
        report = {
            "schema_version": _REPORT_SCHEMA,
            "engine": "ue5",
            "mode": "game",
            "status": "passed" if frames else "failed",
            "url": str(project_file),
            "output_dir": str(output_dir),
            "map": str(map_path or ""),
            "fps": fps,
            "requested_seconds": duration,
            "recorded_seconds": len(frames) / fps if fps else 0,
            "warmup": warmup,
            "action_count": len(actions),
            "executed_actions": [item["action"] for item in actions],
            "frames": len(frames),
            "video": str(video_path) if video_path else None,
            "game_state": None,
            "native_report": native_report,
            "warnings": warnings,
            "errors": [],
        }
        report_path = output_dir / "report.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        payload["report"] = report
        if not frames:
            errors = [
                str(item)
                for item in ((native_report or {}).get("errors") or [])
            ]
            if not errors:
                if native_report is None:
                    errors = [
                        "no playtest recorder report was produced; the "
                        "launched game binary likely predates the "
                        "A3GamePlayable playtest recorder — rebuild the "
                        "project and retry",
                    ]
                else:
                    errors = ["game recorder captured no frames"]
            return self._fail(errors, payload)
        artifacts = [
            {"type": "playtest_report", "path": str(report_path)},
            {"type": "playtest_actions", "path": str(actions_path)},
            {"type": "playtest_frames", "path": str(output_dir / "frames")},
        ]
        if video_path:
            artifacts.append({"type": "playtest_video", "path": str(video_path)})
        return UEOperationResult.success(_OPERATION, artifacts=artifacts, warnings=warnings, payload=payload).to_dict()

    def _load_actions(
        self,
        scenario: str | Path | None,
        action_plan: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]] | None, str | None]:
        actions: list[dict[str, Any]]
        if scenario is not None:
            path = Path(scenario).expanduser()
            if not path.is_file():
                return None, f"scenario file does not exist: {path}"
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                return None, f"invalid scenario JSON: {exc}"
            actions = list(data.get("actions") or [])
        elif action_plan is not None:
            actions = list(action_plan)
        else:
            actions = [dict(item) for item in DEFAULT_ACTIONS]
        for action in actions:
            if not isinstance(action, dict) or action.get("action") not in ALLOWED_ACTIONS:
                return None, (
                    "invalid action plan: use allowlisted action names "
                    f"{sorted(ALLOWED_ACTIONS)}"
                )
        if not actions:
            return None, "action plan must not be empty"
        return actions, None

    @staticmethod
    def _timeline(actions: list[dict[str, Any]]) -> list[tuple[int, str, str]]:
        """Expand actions into ``(t_ms, phase, key)`` key events."""
        events: list[tuple[int, str, str]] = []
        t_ms = 0
        for action in actions:
            name = str(action.get("action", ""))
            duration_ms = int(action.get("duration_ms", 100))
            if name == "wait":
                t_ms += duration_ms
                continue
            if name == "move":
                x = int(action.get("x", 0) or 0)
                y = int(action.get("y", 0) or 0)
                keys = []
                if x:
                    keys.append(_MOVE_KEYS["+x" if x > 0 else "-x"])
                if y:
                    keys.append(_MOVE_KEYS["+y" if y > 0 else "-y"])
                for key in keys:
                    events.append((t_ms, "down", key))
                for key in keys:
                    events.append((t_ms + max(duration_ms, 100), "up", key))
                t_ms += max(duration_ms, 100)
                continue
            if name == "look":
                yaw = int(action.get("yaw_delta", 0) or 0)
                pitch = int(action.get("pitch_delta", 0) or 0)
                if yaw:
                    events.append((t_ms, "tap", _LOOK_KEYS["+yaw" if yaw > 0 else "-yaw"]))
                if pitch:
                    events.append((t_ms, "tap", _LOOK_KEYS["+pitch" if pitch > 0 else "-pitch"]))
                t_ms += max(duration_ms, 100)
                continue
            key = str(DEFAULT_KEY_BINDINGS.get(name, ""))
            if key:
                events.append((t_ms, "tap", key))
            t_ms += max(duration_ms, 100)
        events.sort(key=lambda item: item[0])
        return events

    # ── Editor lifecycle ───────────────────────────────────────────────────

    @staticmethod
    def _wait_for_marker(
        marker: Path,
        process: subprocess.Popen[Any],
        payload: dict[str, Any],
        timeout: float,
        exit_error: str = "",
        timeout_error: str = "",
    ) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if marker.is_file():
                return True
            if process.poll() is not None:
                payload["editor_exit_code"] = process.returncode
                payload["error"] = exit_error or (
                    "Unreal Editor exited before starting PIE; check that "
                    "the project enables the Python Editor Script Plugin"
                )
                return False
            time.sleep(1.0)
        payload["error"] = timeout_error or (
            f"Unreal Editor did not start PIE within {timeout:.0f}s"
        )
        return False

    @staticmethod
    def _wait_for_exit(process: subprocess.Popen[Any], deadline: float) -> bool:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return True
            time.sleep(1.0)
        return False

    @staticmethod
    def _stop_process(process: subprocess.Popen[Any]) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()

    @staticmethod
    def _read_editor_report(out: Path, payload: dict[str, Any]) -> dict[str, Any] | None:
        path = out / "_editor_report.json"
        if not path.is_file():
            payload["editor_report_missing"] = True
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            payload["editor_report_invalid"] = f"{type(exc).__name__}: {exc}"
            return None

    @staticmethod
    def _encode_video(
        out: Path,
        frames: list[Path],
        fps: int,
        encoder: Path | None,
    ) -> Path | None:
        if not frames:
            return None
        binary = encoder or shutil.which("ffmpeg")
        if not binary:
            return None
        video_path = out / "video.mp4"
        try:
            completed = subprocess.run(
                [
                    str(binary), "-y",
                    "-framerate", str(max(fps, 1)),
                    "-i", str(out / "frames" / "f%05d.png"),
                    # Editor captures can have odd heights; yuv420p requires
                    # even dimensions.
                    "-vf", "crop=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-crf", "23",
                    str(video_path),
                ],
                capture_output=True,
                timeout=120,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        # A failed encode can leave a zero-byte file behind; never report
        # that as a playable video.
        if (
            completed.returncode != 0
            or not video_path.is_file()
            or video_path.stat().st_size == 0
        ):
            video_path.unlink(missing_ok=True)
            return None
        return video_path

    # ── Player input ──────────────────────────────────────────────────────

    def _check_input_driver(self) -> str | None:
        if sys_platform() == "Windows":
            return None
        if sys_platform() != "Darwin":
            return (
                "UE5 playtest input injection is currently implemented for "
                "macOS only (System Events); on this platform the take would "
                "record without player input"
            )
        ok, error = _osascript(
            'tell application "System Events" to get name of first application process'
        )
        if not ok:
            return (
                "System Events is not available for keyboard injection: "
                f"{error}. Grant the calling terminal Accessibility "
                "permission (System Settings → Privacy & Security → "
                "Accessibility) and retry."
            )
        return None

    @staticmethod
    def _drive_events(
        process_id: int,
        events: list[tuple[int, str, str]],
        started: float,
        out: Path,
        warmup: float,
    ) -> list[str]:
        """Post key events on schedule; return non-fatal warnings."""
        warnings: list[str] = []
        actions_path = out / "actions.jsonl"
        held: set[str] = set()
        seq = 0
        with actions_path.open("w", encoding="utf-8") as handle:
            try:
                for t_ms, phase, key in events:
                    target = started + warmup + t_ms / 1000.0
                    remaining = target - time.monotonic()
                    if remaining > 0:
                        time.sleep(remaining)
                    ok, error = _key_event(process_id, key, phase)
                    seq += 1
                    if not ok:
                        warnings.append(f"key event failed: {error}")
                    handle.write(json.dumps({
                        "seq": seq,
                        "t_monotonic_ms": int((time.monotonic() - started) * 1000),
                        "phase": phase,
                        "key": key,
                    }) + "\n")
                    if phase == "down":
                        held.add(key)
                    elif phase == "up":
                        held.discard(key)
            finally:
                # Never leave a key pressed in the Editor session.
                for key in sorted(held):
                    _key_event(process_id, key, "up")
        return warnings

    @staticmethod
    def _fail(message: str | list[str], payload: dict[str, Any] | None = None):
        errors = [str(item) for item in (message if isinstance(message, list) else [message])]
        return UEOperationResult.failure(
            _OPERATION,
            *errors,
            payload=payload,
        ).to_dict()


def _osascript(script: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "osascript failed").strip()
    return True, result.stdout.strip()


def _key_event(process_id: int, key: str, phase: str) -> tuple[bool, str]:
    """Post one real keyboard event to the Editor process via System Events."""
    if sys_platform() == "Windows":
        return _windows_key_event(process_id, key, phase)
    lines = ['tell application "System Events"']
    lines.append(
        "set frontmost of (first application process whose unix id is "
        f"{int(process_id)}) to true"
    )
    if key in _KEY_CODES:
        lines.append(f"key code {int(_KEY_CODES[key])}")
    elif phase == "down":
        lines.append(f'key down "{key}"')
    elif phase == "up":
        lines.append(f'key up "{key}"')
    else:
        lines.append(f'keystroke "{key}"')
    lines.append("end tell")
    return _osascript("\n".join(lines))


def _windows_key_event(process_id: int, key: str, phase: str) -> tuple[bool, str]:
    vk = _WINDOWS_VK.get(str(key).lower())
    if vk is None:
        return False, f"unsupported Windows key: {key}"
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found = {"hwnd": 0}

    @enum_proc
    def callback(hwnd: int, _lparam: int) -> bool:
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == int(process_id) and user32.IsWindowVisible(hwnd):
            found["hwnd"] = hwnd
            return False
        return True

    user32.EnumWindows(callback, 0)
    if found["hwnd"]:
        user32.ShowWindow(found["hwnd"], 5)
        user32.SetForegroundWindow(found["hwnd"])
    flags = 0 if phase == "down" else 0x0002
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                    ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                    ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]
    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("ki", KEYBDINPUT),
                    ("padding", ctypes.c_byte * 8)]
    item = INPUT(type=1, ki=KEYBDINPUT(wVk=vk, dwFlags=flags))
    sent = user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
    return (True, "") if sent == 1 else (False, "SendInput returned zero")


def _game_log_tail(project_file: Path, limit: int = 2000) -> str:
    """Return the tail of the game's own log for failure payloads."""
    log = project_file.parent / "Saved" / "Logs" / f"{project_file.stem}.log"
    if not log.is_file():
        return ""
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return text[-limit:]
