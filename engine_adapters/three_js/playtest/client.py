"""Record a playtest of a three.js project in a headless browser.

This is a **fixed-timestep capture**, not screen recording: there is no display
and no GPU render node here, so WebGL runs on SwiftShader at a fraction of real
time. `record.mjs` documents why each part is shaped the way it is.

Three environment inputs have no default on a machine like this and are
therefore explicit parameters rather than assumptions:

- ``playwright_root`` - a project with ``node_modules/playwright``. A generated
  game does not depend on Playwright, and it should not have to: recording is
  the harness's concern, not the game's.
- ``browser_executable`` / ``browsers_path`` - the browser is not in the default
  ``~/.cache`` location.
- ``library_path`` - Chromium needs ``libatk-bridge-2.0``, ``libgbm`` and
  ``libatspi``, which are absent from this image and cannot be installed. They
  are supplied by prepending a directory to ``LD_LIBRARY_PATH``. Omitting this is
  the single most likely reason a recording fails to start, and the failure looks
  like a browser launch error rather than a missing library.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .._internal.transport import NodeToolchain
from ..config import ThreeClientConfig
from ..contracts import ThreeOperationResult

_RECORDER = Path(__file__).with_name("record.mjs")
_OPERATION = "playtest.record"


class ThreePlaytestClient:
    """Drive a running game through discovered actions and record the result.

    Nothing here is written for a particular game. What the recorder needs to
    know is read from the running game — the input router publishes the very
    tables it dispatches on, so they cannot drift from what the game listens
    for. In order of preference: a ``__A3GAME_PLAYTEST__`` plan the game
    declares for itself; ``actionBindings`` and ``keyBindings``;
    ``[data-game-action]`` elements; a generic keyboard plan.

    Three things vary per game that a *list of verbs* cannot express, so they
    are separate inputs — inferred by default, overridable per recording:

    - ``hold`` - what stays pressed for the whole take. A racing game whose
      throttle is released between actions records a car twitching on the
      start line.
    - ``warmup`` - seconds to simulate before capturing. Games open on a
      countdown or a spawn, and a brawler drops attacks entirely until its
      round reaches FIGHT.
    - ``look`` - whether sweeping the camera is meaningful. Wrong in a
      side-scroller, actively harmful under drag-look.
    """

    def __init__(self, config: ThreeClientConfig) -> None:
        self._config = config
        self._toolchain = NodeToolchain(config)

    def record(
        self,
        *,
        output_dir: str | Path,
        url: str = "",
        action_plan: str | Path | None = None,
        hold: str | list[str] | None = None,
        warmup: float | None = None,
        look: str | bool | None = None,
        playwright_root: str | Path | None = None,
        browser_executable: str | Path | None = None,
        browsers_path: str | Path | None = None,
        library_path: str | Path | None = None,
        ffmpeg: str | Path | None = None,
        duration: float = 14.0,
        fps: int = 20,
        width: int = 1280,
        height: int = 720,
        timeout: float = 900.0,
        dry_run: bool = False,
        mode: str = "gameplay",
        preview: bool = False,
        allow_partial_plan: bool = False,
        source_hash: str | None = None,
    ) -> dict[str, Any]:
        """Capture a unique take below ``output_dir`` without reusing old evidence.

        ``warmup=None`` and ``look=None`` preserve the game's declared plan.
        Explicit ``warmup=0`` disables pre-roll; ``look=False`` means ``off``.
        ``mode`` is gameplay or overview; ``preview`` captures one PNG instead
        of a video. ``allow_partial_plan`` explicitly permits a plan prefix.
        ``source_hash`` is an opaque build label passed through unchanged.
        Successful videos require completed status and verified video metadata;
        PNG previews require preview_completed. The current report path comes
        only from this invocation's final stdout JSON, never BASE/report.json.
        """
        project_dir = self._config.project_dir
        project_file = self._config.project_file
        if project_dir is None or project_file is None or not project_file.is_file():
            return self._fail("project_path must resolve to a project containing package.json")
        if not _RECORDER.is_file():
            return self._fail(f"Playtest recorder is missing: {_RECORDER}")
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   and math.isfinite(value) and value > 0
                   for value in (duration, fps, width, height, timeout)):
            return self._fail("duration, fps, width, height, and timeout must be finite and positive")
        if int(fps) != fps or any(int(value) != value or value % 2 for value in (width, height)):
            return self._fail("fps must be an integer; width and height must be even integers")
        if mode not in ("gameplay", "overview"):
            return self._fail("mode must be gameplay or overview")
        if not isinstance(preview, bool) or not isinstance(allow_partial_plan, bool):
            return self._fail("preview and allow_partial_plan must be boolean")
        if source_hash is not None and (not isinstance(source_hash, str) or not source_hash):
            return self._fail("source_hash must be a non-empty string or None")

        out = Path(output_dir).expanduser().resolve(strict=False)
        plan = self._resolve(action_plan)
        root = self._resolve(playwright_root)
        browser = self._resolve(browser_executable)
        browsers = self._resolve(browsers_path)
        libraries = self._resolve(library_path)
        encoder = self._resolve(ffmpeg)

        if plan is not None and not plan.is_file():
            return self._fail(f"action_plan does not exist: {plan}")
        if root is not None and not (root / "node_modules" / "playwright").is_dir():
            return self._fail(f"playwright_root has no node_modules/playwright: {root}")
        if browser is not None and not browser.is_file():
            return self._fail(f"browser_executable does not exist: {browser}")
        if libraries is not None and not libraries.is_dir():
            return self._fail(f"library_path is not a directory: {libraries}")
        if look is False or look == "false":
            look = "off"
        if look not in (None, "auto", "pan", "off"):
            return self._fail(f"look must be auto, pan, off, False, or None; got {look!r}")
        if warmup is not None and (not isinstance(warmup, (int, float))
                                   or not math.isfinite(warmup) or warmup < 0):
            return self._fail("warmup must be finite and non-negative or None")

        held = [item.strip() for item in (hold.split(",") if isinstance(hold, str) else hold or [])]
        held = [item for item in held if item]
        url = str(url or self._config.dev_server_url)
        arguments = [
            "--url", url,
            "--output-dir", str(out),
            "--duration", str(float(duration)),
            "--fps", str(int(fps)),
            "--width", str(int(width)),
            "--height", str(int(height)),
            "--mode", mode,
        ]
        if look is not None:
            arguments.extend(["--look", look])
        if warmup is not None:
            arguments.extend(["--warmup", str(float(warmup))])
        if preview:
            arguments.append("--preview")
        if allow_partial_plan:
            arguments.append("--allow-partial-plan")
        if source_hash is not None:
            arguments.extend(["--source-hash", source_hash])
        if held:
            arguments.extend(["--hold", ",".join(held)])
        for flag, value in (
            ("--action-plan", plan),
            ("--playwright-root", root),
            ("--browser-executable", browser),
        ):
            if value is not None:
                arguments.extend([flag, str(value)])

        environment: dict[str, str] = {}
        if browsers is not None:
            environment["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers)
        if libraries is not None:
            # Prepend: the image's own libraries must still win where they exist.
            existing = self._toolchain_environment().get("LD_LIBRARY_PATH", "")
            environment["LD_LIBRARY_PATH"] = (
                f"{libraries}:{existing}" if existing else str(libraries)
            )
        if encoder is not None:
            environment["A3GAME_PLAYTEST_FFMPEG"] = str(encoder)

        payload: dict[str, Any] = {
            "engine": "three_js",
            "url": url,
            "project_dir": str(project_dir),
            "output_dir": str(out),
            "report_path": None,
            "mode": mode,
            "preview": preview,
            "allow_partial_plan": allow_partial_plan,
            "source_hash": source_hash,
            "arguments": arguments,
            "action_plan": str(plan) if plan else None,
            "hold": held,
            "warmup": warmup,
            "look": look,
            "playwright_root": str(root) if root else None,
            "browser_executable": str(browser) if browser else None,
            "duration": duration,
            "fps": fps,
            "viewport": {"width": width, "height": height},
            "environment": environment,
            "dry_run": dry_run,
        }
        if dry_run:
            return ThreeOperationResult.success(_OPERATION, payload=payload).to_dict()

        try:
            out.mkdir(parents=True, exist_ok=True)
            previous_entries = {entry.name for entry in out.iterdir()}
            command = self._toolchain.run_node(
                _RECORDER,
                cwd=project_dir,
                extra_args=arguments,
                timeout=timeout,
                environment=environment or None,
            )
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            return self._fail(f"{type(exc).__name__}: {exc}", payload)
        payload["command"] = command.to_dict()

        try:
            lines = command.stdout.strip().splitlines()
            if not lines:
                raise ValueError("Recorder produced no final stdout JSON")
            current = json.loads(lines[-1])
            if not isinstance(current, dict):
                raise ValueError("Final stdout JSON must be an object")
            report_path = self._contained_path(current.get("report"), out)
            take = self._contained_path(current.get("output_dir"), out, directory=True)
            if take.parent != out or take.name in previous_entries:
                raise ValueError("Recorder did not return a new take within output_dir")
            if report_path != take / "report.json":
                raise ValueError("Report does not belong to the current take")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if not isinstance(report, dict):
                raise ValueError("Playtest report must be an object")
            payload.update(report_path=str(report_path), take_dir=str(take), report=report)
            if self._contained_path(report.get("output_dir"), out, directory=True) != take:
                raise ValueError("Report output_dir does not match the current take")
            if report.get("source_hash") != source_hash or current.get("source_hash") != source_hash:
                raise ValueError("Recorder source_hash does not match this invocation")
            if report.get("mode") != mode or report.get("url") != url:
                raise ValueError("Recorder mode or URL does not match this invocation")
            expected_status = "preview_completed" if preview else "completed"
            if command.timed_out or command.returncode != 0:
                raise ValueError("Recorder timed out" if command.timed_out else f"Recorder exited with code {command.returncode}")
            if report.get("status") != expected_status or current.get("status") != expected_status:
                raise ValueError(report.get("crash") or f"Recorder status is not {expected_status}")
            if any(report.get(key) for key in ("crash", "page_errors", "console_errors")):
                raise ValueError("Recorder reported browser errors")
            if report.get("partial_plan") and not allow_partial_plan:
                raise ValueError("Recorder returned an unrequested partial plan")
            if not isinstance(report.get("warnings", []), list):
                raise ValueError("Report warnings must be an array")
            artifacts = [{"type": "playtest_report", "path": str(report_path)}]
            if preview:
                if report.get("preview") is not True or report.get("png_frames") != 1 or report.get("video"):
                    raise ValueError("Invalid PNG-only preview report")
                poster = self._contained_path(report.get("poster"), take)
                if poster.suffix.lower() != ".png":
                    raise ValueError("Preview poster must be a PNG")
                artifacts.append({"type": "playtest_poster", "path": str(poster)})
            else:
                metadata = report.get("video_metadata")
                if not isinstance(metadata, dict):
                    raise ValueError("Completed video has no video_metadata")
                frames = report.get("frames")
                if not isinstance(frames, int) or isinstance(frames, bool) or frames <= 0:
                    raise ValueError("Completed video has no frames")
                expected_frames = max(1, math.floor(duration * fps + 0.5))
                if frames != expected_frames or frames != report.get("target_frames") or frames != metadata.get("frames"):
                    raise ValueError("Video frame counts do not match the requested duration")
                if report.get("fps") != fps or metadata.get("fps") != fps:
                    raise ValueError("Video fps does not match the request")
                if report.get("viewport") != {"width": width, "height": height} or (
                    metadata.get("width"), metadata.get("height")
                ) != (width, height):
                    raise ValueError("Video dimensions do not match the request")
                if not all(isinstance(seconds, (int, float)) and math.isfinite(seconds)
                           and math.isclose(seconds, frames / fps, rel_tol=0, abs_tol=0.002)
                           for seconds in (metadata.get("seconds"), report.get("recorded_seconds"))):
                    raise ValueError("Video duration does not match frame count")
                video = self._contained_path(report.get("video"), take)
                if video.suffix.lower() != ".mp4":
                    raise ValueError("Completed video must be an MP4")
                artifacts.extend([
                    {"type": "playtest_frames", "path": str(self._contained_path(str(take / "frames"), take, directory=True))},
                    {"type": "playtest_video", "path": str(video)},
                ])
                for key in ("init", "poster"):
                    image = self._contained_path(report.get(key), take)
                    artifacts.append({"type": f"playtest_{key}", "path": str(image)})
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            return self._fail(f"Invalid current playtest evidence: {exc}", payload)
        return ThreeOperationResult.success(
            _OPERATION,
            artifacts=artifacts,
            warnings=[str(item) for item in report.get("warnings", [])],
            payload=payload,
        ).to_dict()

    @staticmethod
    def _contained_path(value: Any, parent: Path, *, directory: bool = False) -> Path:
        if not isinstance(value, str) or not value or not Path(value).is_absolute():
            raise ValueError("Evidence paths must be absolute strings")
        candidate = Path(value).resolve(strict=True)
        if candidate == parent or not candidate.is_relative_to(parent):
            raise ValueError("Evidence path escapes the current output scope")
        if directory:
            if not candidate.is_dir():
                raise ValueError("Evidence directory is missing")
        elif not candidate.is_file() or candidate.stat().st_size == 0:
            raise ValueError("Evidence file is missing or empty")
        return candidate

    @staticmethod
    def _resolve(value: str | Path | None) -> Path | None:
        return Path(value).expanduser().resolve(strict=False) if value else None

    @staticmethod
    def _toolchain_environment() -> dict[str, str]:
        import os

        return dict(os.environ)

    @staticmethod
    def _fail(message: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return ThreeOperationResult.failure(_OPERATION, message, payload=payload).to_dict()
