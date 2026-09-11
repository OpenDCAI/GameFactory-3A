"""Browser-facing orchestration for CG-video generation jobs.

The gateway accepts a repository task identity and delegates model execution to
the CG-video operator.  It deliberately does not author prompts or
encode provider requests; those responsibilities remain with the task and the
injected model factory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from time import time
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from pipeline.common import paths

from .contracts import BrowserServingError


TASK_KIND = "cg_video"
VIDEO_FILENAME = "video.mp4"
MEDIA_TYPE = "video/mp4"
JOB_STATES = frozenset({"queued", "running", "ready", "failed"})
SUPPORTED_MODES = frozenset(
    {
        "text_to_video",
        "first_frame_to_video",
        "first_last_frame_to_video",
        "reference_to_video",
    }
)
BACKEND_ALIASES = {
    "h3": "minimax-h3",
    "minimax": "minimax-h3",
    "minimax-h3": "minimax-h3",
    "seedance": "seedance",
}
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ALLOWED_OPTION_KEYS = frozenset(
    {
        "device",
        "timeout",
        "poll_interval",
        "max_retries",
        "cache_dir",
        "resolution",
        "ratio",
        "generate_audio",
        "watermark",
        "minimax_runtime",
        "comfyui_path",
        "hf_cache_dir",
        "hf_revision",
        "local_files_only",
        "width",
        "height",
        "fps",
        "steps",
        "scheduler",
        "sampler_mode",
        "ref_image_size",
        "prompt_optimizer",
        "fast_pretreatment",
        "verbose",
    }
)


class CgVideoError(BrowserServingError):
    """Raised when a CG-video request cannot be accepted or served."""


class CgVideoGatewayProtocol(Protocol):
    """Protocol implemented by a CG-video job gateway."""

    @property
    def enabled(self) -> bool:
        ...

    def submit(
        self,
        *,
        game_id: str,
        task_id: str,
        run_id: str = "",
        backend: str = "",
        trigger_id: str = "",
        session_id: str = "",
        engine: str = "",
        idempotency_key: str = "",
        options: Mapping[str, Any] | None = None,
        playback: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def get(self, request_id: str) -> dict[str, Any]:
        ...

    def media_path(self, artifact_id: str) -> Path:
        ...


@dataclass
class _CgVideoJob:
    request_id: str
    game_id: str
    task_id: str
    run_id: str
    backend: str
    trigger_id: str = ""
    session_id: str = ""
    engine: str = ""
    state: str = "queued"
    artifact_id: str = ""
    video_path: str = ""
    error: str = ""
    task_meta: dict[str, Any] = field(default_factory=dict)
    playback: dict[str, bool] = field(default_factory=dict)
    created_at: float = field(default_factory=time)
    updated_at: float = field(default_factory=time)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "request_id": self.request_id,
            "status": self.state,
            "game_id": self.game_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "backend": self.backend,
            "trigger_id": self.trigger_id,
            "session_id": self.session_id,
            "engine": self.engine,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "task": dict(self.task_meta),
            "playback": dict(self.playback),
        }
        if self.artifact_id:
            payload["video"] = {
                "artifact_id": self.artifact_id,
                "media_type": MEDIA_TYPE,
                "path": self.video_path,
            }
        if self.error:
            payload["error"] = self.error
        return payload


class CgVideoGateway:
    """Queue CG-video tasks and materialize standard task artifacts.

    Model construction is lazy and injectable.  The default factory loads the
    repository's H3 or Seedance model wrapper only when a job starts.
    """

    def __init__(
        self,
        *,
        enabled: bool = True,
        allow_cloud: bool = False,
        max_workers: int = 1,
        output_root: str | Path | None = None,
        model_factory: Callable[[str, Mapping[str, Any], Mapping[str, Any]], Any]
        | None = None,
        operator_factory: Callable[..., Any] | None = None,
    ) -> None:
        workers = int(max_workers)
        if workers < 1:
            raise ValueError("max_workers must be positive")
        self._enabled = bool(enabled)
        self.allow_cloud = bool(allow_cloud)
        self.output_root = Path(output_root or paths.OUTPUT_ROOT).expanduser().resolve(
            strict=False
        )
        self._model_factory = model_factory or self._default_model_factory
        self._operator_factory = operator_factory or self._default_operator_factory
        self._executor = ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="a3game-cg-video",
        )
        self._jobs: dict[str, _CgVideoJob] = {}
        self._idempotency: dict[str, str] = {}
        self._artifacts: dict[str, Path] = {}
        self._models: dict[str, Any] = {}
        self._lock = RLock()

    @property
    def enabled(self) -> bool:
        """Whether the gateway accepts new jobs."""

        return self._enabled

    def close(self) -> None:
        """Stop accepting work and release the job executor."""

        self._executor.shutdown(wait=False, cancel_futures=True)

    def submit(
        self,
        *,
        game_id: str,
        task_id: str,
        run_id: str = "",
        backend: str = "",
        trigger_id: str = "",
        session_id: str = "",
        engine: str = "",
        idempotency_key: str = "",
        options: Mapping[str, Any] | None = None,
        playback: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Accept one task identity and return a queued job descriptor."""

        if not self.enabled:
            raise CgVideoError("CG-video generation is disabled")
        resolved_game = _identity(game_id, "game_id")
        resolved_task = _identity(task_id, "task_id")
        task = self._load_task(resolved_game, resolved_task)
        resolved_options = _options(options)
        resolved_playback = _playback(playback)
        resolved_backend = self._resolve_backend(task, backend)
        self._validate_task(task, resolved_backend, resolved_options)
        self._check_cloud_policy(resolved_backend, resolved_options)

        resolved_run = _run_id(run_id)
        key = str(idempotency_key or "").strip()

        request_id = f"cgreq_{uuid4().hex[:20]}"
        job = _CgVideoJob(
            request_id=request_id,
            game_id=resolved_game,
            task_id=resolved_task,
            run_id=resolved_run,
            backend=resolved_backend,
            trigger_id=str(trigger_id or ""),
            session_id=str(session_id or ""),
            engine=str(engine or ""),
            task_meta={
                key: task[key]
                for key in (
                    "mode",
                    "model",
                    "scene",
                    "duration_sec",
                    "aspect_ratio",
                )
                if key in task
            },
            playback=resolved_playback,
        )
        with self._lock:
            if key:
                existing = self._idempotency.get(key)
                if existing:
                    return self._jobs[existing].to_dict()
            self._jobs[request_id] = job
            if key:
                self._idempotency[key] = request_id
        self._executor.submit(self._run, job, task, resolved_options)
        return job.to_dict()

    def get(self, request_id: str) -> dict[str, Any]:
        """Return one job descriptor or raise ``KeyError``."""

        value = str(request_id or "").strip()
        with self._lock:
            job = self._jobs.get(value)
            if job is None:
                raise KeyError(f"Unknown CG-video request_id: {value}")
            return job.to_dict()

    def media_path(self, artifact_id: str) -> Path:
        """Resolve a completed artifact inside the configured output root."""

        value = str(artifact_id or "").strip()
        with self._lock:
            path = self._artifacts.get(value)
        if path is None:
            raise KeyError(f"Unknown CG-video artifact_id: {value}")
        resolved = path.expanduser().resolve(strict=False)
        _within(resolved, self.output_root, "CG-video artifact")
        if not resolved.is_file():
            raise FileNotFoundError(f"CG-video artifact does not exist: {resolved}")
        return resolved

    def _run(
        self,
        job: _CgVideoJob,
        task: Mapping[str, Any],
        options: Mapping[str, Any],
    ) -> None:
        self._set_state(job, "running")
        try:
            model = self._get_model(job.backend, task, options)
            operator = self._operator_factory(
                model,
                run_id=job.run_id,
                default_game_id=job.game_id,
            )
            result = operator.run(dict(task))
            raw_path = result.get("video_path") if isinstance(result, Mapping) else ""
            if not raw_path:
                raise CgVideoError("CG-video operator returned no video_path")
            video_path = Path(str(raw_path)).expanduser().resolve(strict=False)
            _within(video_path, self.output_root, "CG-video output")
            if not video_path.is_file():
                raise FileNotFoundError(f"CG-video output does not exist: {video_path}")
            artifact_id = _artifact_id(job.game_id, job.run_id, job.task_id)
            with self._lock:
                self._artifacts[artifact_id] = video_path
                job.artifact_id = artifact_id
                job.video_path = str(video_path)
                job.updated_at = time()
                job.state = "ready"
        except Exception as exc:
            with self._lock:
                job.error = f"{type(exc).__name__}: {exc}"
                job.updated_at = time()
                job.state = "failed"

    def _set_state(self, job: _CgVideoJob, state: str) -> None:
        if state not in JOB_STATES:
            raise ValueError(f"Unsupported CG-video job state: {state}")
        with self._lock:
            job.state = state
            job.updated_at = time()

    def _get_model(
        self,
        backend: str,
        task: Mapping[str, Any],
        options: Mapping[str, Any],
    ) -> Any:
        task_model_options = {
            key: task[key]
            for key in ("model", "aspect_ratio", "video_width", "video_height")
            if key in task
        }
        cache_key = json.dumps(
            {
                "backend": backend,
                "options": dict(options),
                "task_model_options": task_model_options,
            },
            sort_keys=True,
            default=str,
        )
        with self._lock:
            if cache_key in self._models:
                return self._models[cache_key]
        model = self._model_factory(backend, task, options)
        with self._lock:
            self._models[cache_key] = model
        return model

    @staticmethod
    def _default_operator_factory(
        model: Any,
        *,
        run_id: str,
        default_game_id: str,
    ) -> Any:
        from operators.gen_cg_video.operator import GenCGVideoOperator

        return GenCGVideoOperator(
            model=model,
            run_id=run_id,
            default_game_id=default_game_id,
        )

    @staticmethod
    def _default_model_factory(
        backend: str,
        task: Mapping[str, Any],
        options: Mapping[str, Any],
    ) -> Any:
        """Build the selected model without importing it at gateway startup."""

        from pipeline.assets_gen.gen_cg_video.run import load_model, resolve_ckpt

        opts = dict(options)
        device = str(opts.get("device") or "cuda")
        if backend == "seedance":
            ckpt = resolve_ckpt(
                backend,
                None,
            )
            kwargs = {
                "cache_dir": opts.get("cache_dir")
                or os.environ.get("GAMEFACTORY3A_API_CACHE"),
                "timeout": int(opts.get("timeout", 1800)),
                "poll_interval": float(opts.get("poll_interval", 3.0)),
                "max_retries": int(opts.get("max_retries", 3)),
                "resolution": str(opts.get("resolution") or "720p"),
                "ratio": str(
                    opts.get("ratio")
                    or task.get("aspect_ratio")
                    or "16:9"
                ),
                "generate_audio": bool(opts.get("generate_audio", True)),
                "watermark": bool(opts.get("watermark", False)),
                "verbose": bool(opts.get("verbose", False)),
            }
            return load_model(
                ckpt,
                device=device,
                backend=backend,
                **kwargs,
            )

        runtime = str(
            opts.get("minimax_runtime")
            or os.environ.get("MINIMAX_H3_RUNTIME")
            or "local"
        ).strip().lower()
        ckpt = resolve_ckpt(backend, None)
        kwargs = {
            "runtime": runtime,
            "comfyui_path": opts.get("comfyui_path"),
            "hf_cache_dir": opts.get("hf_cache_dir"),
            "hf_revision": opts.get("hf_revision"),
            "local_files_only": bool(opts.get("local_files_only", False)),
            "width": int(opts.get("width", task.get("video_width") or 864)),
            "height": int(opts.get("height", task.get("video_height") or 480)),
            "fps": float(opts.get("fps", 24.0)),
            "steps": int(opts.get("steps", 20)),
            "scheduler": str(opts.get("scheduler") or "simple"),
            "sampler_mode": str(opts.get("sampler_mode") or "res_multistep"),
            "ref_image_size": str(opts.get("ref_image_size") or "match"),
            "cache_dir": opts.get("cache_dir")
            or os.environ.get("GAMEFACTORY3A_API_CACHE"),
            "timeout": int(opts.get("timeout", 1800)),
            "poll_interval": float(opts.get("poll_interval", 10.0)),
            "max_retries": int(opts.get("max_retries", 3)),
            "resolution": str(opts.get("resolution") or "768P"),
            "prompt_optimizer": bool(opts.get("prompt_optimizer", True)),
            "fast_pretreatment": bool(opts.get("fast_pretreatment", False)),
            "verbose": bool(opts.get("verbose", False)),
        }
        return load_model(
            ckpt,
            device=device,
            backend=backend,
            **kwargs,
        )

    @staticmethod
    def _load_task(game_id: str, task_id: str) -> dict[str, Any]:
        task_path = paths.task_jsonl(game_id, TASK_KIND)
        if not task_path.is_file() or task_path.stat().st_size == 0:
            raise FileNotFoundError(
                f"CG-video task list is empty or missing: {task_path}"
            )
        for task, _ in paths.iter_tasks(task_path):
            if str(task.get("task_id") or "") != task_id:
                continue
            declared_game = str(task.get("game_id") or game_id)
            if declared_game != game_id:
                raise ValueError(
                    f"CG-video task {task_id!r} declares game_id={declared_game!r}"
                )
            normalized = dict(task)
            normalized["game_id"] = game_id
            return normalized
        raise KeyError(
            f"CG-video task_id {task_id!r} was not found in {task_path}"
        )

    @staticmethod
    def _resolve_backend(task: Mapping[str, Any], requested: str) -> str:
        candidate = str(
            requested
            or task.get("backend")
            or task.get("model")
            or "h3"
        ).strip().lower()
        try:
            return BACKEND_ALIASES[candidate]
        except KeyError as exc:
            raise ValueError(
                f"Unsupported CG-video backend/model {candidate!r}; "
                f"expected one of {sorted(BACKEND_ALIASES)}"
            ) from exc

    @staticmethod
    def _validate_task(
        task: Mapping[str, Any],
        backend: str,
        options: Mapping[str, Any],
    ) -> None:
        prompt = task.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("CG-video task prompt must be a non-empty string")
        mode = str(task.get("mode") or "text_to_video")
        if mode not in SUPPORTED_MODES:
            raise ValueError(
                f"Unsupported CG-video mode {mode!r}; expected one of "
                f"{sorted(SUPPORTED_MODES)}"
            )
        duration = task.get("duration_sec", 5)
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError("CG-video duration_sec must be a positive number")
        if duration <= 0:
            raise ValueError("CG-video duration_sec must be a positive number")
        if task.get("reference_video_paths") is not None:
            raise NotImplementedError(
                "CG-video reference_video_paths are reserved for future support"
            )
        if task.get("reference_audio_paths") is not None:
            raise NotImplementedError(
                "CG-video reference_audio_paths are reserved for future support"
            )
        expected = {
            "text_to_video": (False, False, False),
            "first_frame_to_video": (True, False, False),
            "first_last_frame_to_video": (True, True, False),
            "reference_to_video": (False, False, True),
        }[mode]
        actual = (
            task.get("first_frame_path") is not None,
            task.get("last_frame_path") is not None,
            task.get("reference_image_paths") is not None,
        )
        if actual != expected:
            raise ValueError(
                f"mode={mode!r} requires first/last/reference image fields "
                f"{expected}; got {actual}"
            )
        ratio = task.get("aspect_ratio")
        if ratio is not None and not re.fullmatch(
            r"[1-9][0-9]*:[1-9][0-9]*", str(ratio)
        ):
            raise ValueError("CG-video aspect_ratio must use the form WIDTH:HEIGHT")
        if backend != "minimax-h3":
            return

        runtime = str(
            options.get("minimax_runtime")
            or os.environ.get("MINIMAX_H3_RUNTIME")
            or "local"
        ).strip().lower()
        if runtime == "api":
            if mode not in {"text_to_video", "first_frame_to_video"}:
                raise NotImplementedError(
                    "MiniMax H3 API supports text_to_video and "
                    "first_frame_to_video only"
                )
            if duration not in (6, 10):
                raise ValueError("MiniMax H3 API duration_sec must be 6 or 10")
            resolution = str(options.get("resolution") or "768P").upper()
            if resolution == "1080P" and duration != 6:
                raise ValueError(
                    "MiniMax H3 API 1080P output is available only for 6 seconds"
                )
            return

        if runtime != "local":
            raise ValueError("minimax_runtime must be 'local' or 'api'")
        if duration > 15:
            raise ValueError("local MiniMax H3 duration_sec must not exceed 15")
        for name in ("width", "height"):
            task_key = f"video_{name}"
            value = options.get(name, task.get(task_key))
            if value is None:
                continue
            if isinstance(value, bool) or int(value) <= 0:
                raise ValueError(f"CG-video {name} must be positive")
            if int(value) % 32:
                raise ValueError(
                    f"MiniMax H3 local {name} must be divisible by 32"
                )
        if mode == "reference_to_video":
            refs = task.get("reference_image_paths")
            if not isinstance(refs, (list, tuple)) or not refs:
                raise ValueError(
                    "reference_to_video requires a non-empty reference_image_paths list"
                )
            if len(refs) > 9:
                raise ValueError("local MiniMax H3 supports at most 9 reference images")

    def _check_cloud_policy(
        self,
        backend: str,
        options: Mapping[str, Any],
    ) -> None:
        minimax_runtime = str(
            options.get("minimax_runtime")
            or os.environ.get("MINIMAX_H3_RUNTIME")
            or "local"
        ).lower()
        if minimax_runtime not in {"local", "api"}:
            raise ValueError("minimax_runtime must be 'local' or 'api'")
        cloud = backend == "seedance" or (
            backend == "minimax-h3" and minimax_runtime == "api"
        )
        if cloud and not self.allow_cloud:
            raise CgVideoError(
                "Cloud CG-video generation is disabled; enable it explicitly "
                "on the Browser Serving server"
            )


def _identity(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if not IDENTIFIER_PATTERN.fullmatch(normalized):
        raise ValueError(f"{label} must be a safe non-empty identifier")
    return normalized


def _run_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized or normalized.lower() == "auto":
        return paths.new_run_id()
    return _identity(normalized, "run_id")


def _options(value: Mapping[str, Any] | None) -> dict[str, Any]:
    options = dict(value or {})
    unknown = sorted(set(options) - ALLOWED_OPTION_KEYS)
    if unknown:
        raise ValueError(
            f"Unsupported CG-video options: {', '.join(unknown)}"
        )
    return options


def _playback(value: Mapping[str, Any] | None) -> dict[str, bool]:
    """Normalize browser playback hints without making them generation inputs."""

    source = dict(value or {})
    allowed = {"autoplay", "muted", "loop", "controls", "plays_inline"}
    unknown = sorted(set(source) - allowed)
    if unknown:
        raise ValueError(
            f"Unsupported CG-video playback options: {', '.join(unknown)}"
        )
    return {key: bool(source[key]) for key in sorted(source)}


def _artifact_id(game_id: str, run_id: str, task_id: str) -> str:
    digest = hashlib.sha256(
        f"{game_id}\0{run_id}\0{task_id}".encode("utf-8")
    ).hexdigest()[:24]
    return f"cg_video_{digest}"


def _within(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escaped the configured output root") from exc
