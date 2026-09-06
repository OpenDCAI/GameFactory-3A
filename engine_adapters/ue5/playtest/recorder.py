"""In-editor PIE playtest recorder for the UE5 adapter.

This script runs inside the Editor's embedded Python interpreter via
``-ExecutePythonScript`` (the Python Editor Script Plugin must be enabled in
the target project). It loads the play map, starts Play-In-Editor, captures
Game-view frames with the ``HighResShot`` console command at the requested
frame rate, writes the editor-side report, ends PIE, and quits the Editor.

The host client (``engine_adapters/ue5/playtest/client.py``) owns the
scenario timeline: it polls the ``play_started.json`` marker this script
writes and posts real keyboard events while PIE runs.

Only ``unreal`` and the standard library are used. Unreal Editor APIs move
between engine versions, so every call is resolved defensively and every
failure is recorded in the report instead of raising.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import unreal

_OPERATION = "playtest.record"


def _argv_map(argv: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    index = 0
    while index + 1 < len(argv):
        key = str(argv[index])
        if key.startswith("--"):
            result[key[2:]] = str(argv[index + 1])
        index += 2
    return result


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _level_editor():
    for factory in (
        lambda: unreal.get_editor_subsystem(unreal.LevelEditorSubsystem),
        lambda: unreal.LevelEditorSubsystem(),
        lambda: unreal.EditorLevelLibrary,
    ):
        try:
            candidate = factory()
            if candidate is not None:
                return candidate
        except Exception:  # noqa: BLE001 - probe defensively per engine version
            continue
    return None


def _game_world():
    editor = _level_editor()
    for name in ("get_game_world", "get_editor_world"):
        getter = getattr(editor, name, None)
        if getter is None:
            continue
        try:
            world = getter()
            if world is not None:
                return world
        except Exception:  # noqa: BLE001
            continue
    return None


def _console(world, command: str) -> None:
    unreal.SystemLibrary.execute_console_command(world, command, None)


def main() -> int:
    args = _argv_map(list(sys.argv[1:]))
    output = Path(args.get("a3-playtest-output", "")).expanduser()
    if not str(output):
        print("RECORDER_ERROR: --a3-playtest-output is required")
        return 2
    fps = max(int(float(args.get("a3-playtest-fps", "20"))), 1)
    duration = max(float(args.get("a3-playtest-duration", "12")), 0.1)
    warmup = max(float(args.get("a3-playtest-warmup", "0")), 0.0)
    map_path = args.get("a3-playtest-map", "")

    report: dict = {
        "schema_version": "gamefactory3a.ue5.playtest_editor_report.v1",
        "ok": False,
        "map": map_path,
        "fps": fps,
        "duration": duration,
        "frames": 0,
        "state_snapshots": 0,
        "warnings": [],
        "errors": [],
    }
    frames_dir = output / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    state = {"started": False, "start": 0.0, "last": 0.0, "frame": 0}
    started_at = time.monotonic()
    boot_timeout = max(float(args.get("a3-playtest-start-timeout", "300")), 30.0)

    if map_path:
        try:
            unreal.EditorLoadingAndSavingUtils.load_map(map_path)
        except Exception as exc:  # noqa: BLE001
            report["errors"].append(f"load_map failed: {exc}")
            _write_json(output / "_editor_report.json", report)
            unreal.SystemLibrary.quit_editor()
            return 3

    editor = _level_editor()
    if editor is None or not hasattr(editor, "editor_play_simulation"):
        report["errors"].append(
            "no editor API to start PIE; enable the Python Editor Script Plugin"
        )
        _write_json(output / "_editor_report.json", report)
        unreal.SystemLibrary.quit_editor()
        return 3
    editor.editor_play_simulation()

    def tick(_delta: float) -> None:
        world = _game_world()
        if world is None:
            if not state["started"] and time.monotonic() - started_at > boot_timeout:
                report["errors"].append("PIE did not start in time")
                _write_json(output / "_editor_report.json", report)
                unreal.SystemLibrary.quit_editor()
                return
            return
        now = time.monotonic()
        if not state["started"]:
            state["started"] = True
            state["start"] = now
            state["last"] = now
            _write_json(output / "play_started.json", {
                "play_started_wall": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "editor_pid": os.getpid(),
            })
        elapsed = now - state["start"]
        if elapsed >= warmup + duration:
            unreal.unregister_slate_post_tick_callback(handle)
            report["ok"] = report.get("frames", 0) > 0
            if not report["ok"]:
                report["errors"].append("no frames were captured")
            _write_json(output / "_editor_report.json", report)
            try:
                stop = getattr(editor, "editor_request_end_play_map", None)
                if stop is not None:
                    stop()
            except Exception as exc:  # noqa: BLE001
                report["warnings"].append(f"end PIE failed: {exc}")
            unreal.SystemLibrary.quit_editor()
            return
        if elapsed < warmup:
            return
        if now - state["last"] < 1.0 / fps:
            return
        state["last"] = now
        state["frame"] += 1
        name = f"f{state['frame']:05d}.png"
        try:
            _console(world, f"HighResShot filename={frames_dir / name}")
            report["frames"] = state["frame"]
        except Exception as exc:  # noqa: BLE001
            report["warnings"].append(f"capture failed: {exc}")

    handle = unreal.register_slate_post_tick_callback(tick)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
