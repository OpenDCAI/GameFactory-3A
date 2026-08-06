"""Stage browser uploads as standard AAAGameForge task artifacts."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, BinaryIO
from uuid import uuid4

from pipeline.common import paths

from .config import BrowserServingConfig
from .contracts import StagedUpload


_TASK_KIND_BY_ASSET_TYPE = {
    "avatar": "3d_object",
    "environment": "3d_object",
    "effect": "3d_object",
    "material": "3d_object",
    "object": "3d_object",
    "prop": "3d_object",
    "static_mesh": "3d_object",
    "texture": "3d_object",
    "weapon": "3d_object",
    "motion": "motion",
    "animation": "motion",
    "scene": "3d_scene",
    "world": "3d_scene",
    "audio": "audio",
}

_ARTIFACT_KEY_BY_TASK_KIND = {
    "3d_object": "model_path",
    "motion": "motion_path",
    "3d_scene": "scene_path",
    "audio": "audio_path",
}


def _safe_part(value: str, fallback: str) -> str:
    cleaned = re.sub(
        r"[^0-9A-Za-z_.-]+",
        "_",
        str(value or "").strip(),
    ).strip("._")
    return cleaned or fallback


class UploadStore:
    def __init__(self, config: BrowserServingConfig) -> None:
        self.config = config

    def stage(
        self,
        stream: BinaryIO,
        *,
        filename: str,
        asset_type: str,
        media_type: str = "",
        game_id: str = "",
        run_id: str = "",
        task_id: str = "",
        artifact_key: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> StagedUpload:
        normalized_type = (
            str(asset_type or "")
            .strip()
            .lower()
            .replace("-", "_")
        )
        task_kind = _TASK_KIND_BY_ASSET_TYPE.get(normalized_type)
        if task_kind is None:
            raise ValueError(
                f"Unsupported browser upload asset_type: {asset_type}"
            )
        resolved_game = _safe_part(
            game_id or self.config.upload_game_id,
            "_browser_uploads",
        )
        resolved_run = _safe_part(
            run_id or self.config.upload_run_id,
            "browser_uploads",
        )
        stem = _safe_part(Path(filename).stem, normalized_type or "asset")
        resolved_task = _safe_part(
            task_id or f"upload_{stem}_{uuid4().hex[:8]}",
            f"upload_{uuid4().hex[:8]}",
        )
        resolved_key = (
            str(artifact_key or "").strip()
            or _ARTIFACT_KEY_BY_TASK_KIND[task_kind]
        )
        if not resolved_key.endswith("_path"):
            raise ValueError("artifact_key must end with '_path'")

        task_dir = paths.task_output_dir(
            resolved_game,
            task_kind,
            resolved_task,
            run_id=resolved_run,
        )
        upload_dir = task_dir / "browser_upload"
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_part(Path(filename).name, f"{stem}.bin")
        destination = (upload_dir / safe_name).resolve()
        try:
            destination.relative_to(task_dir.resolve())
        except ValueError as exc:
            raise ValueError("Upload destination escaped the task directory") from exc

        with destination.open("wb") as handle:
            shutil.copyfileobj(stream, handle)

        relative = destination.relative_to(task_dir)
        task_meta = {
            "game_id": resolved_game,
            "run_id": resolved_run,
            "task_kind": task_kind,
            "task_id": resolved_task,
            resolved_key: relative.as_posix(),
            "source": "browser_upload",
            "asset_type": normalized_type,
            "media_type": media_type,
            "metadata": dict(metadata or {}),
        }
        paths.write_task_meta(task_dir, task_meta)
        descriptor = {
            "game_id": resolved_game,
            "run_id": resolved_run,
            "task_kind": task_kind,
            "task_id": resolved_task,
            "artifact_key": resolved_key,
        }
        return StagedUpload(
            source_path=destination,
            descriptor=descriptor,
            filename=safe_name,
            asset_type=normalized_type,
            media_type=media_type,
            metadata=dict(metadata or {}),
        )
