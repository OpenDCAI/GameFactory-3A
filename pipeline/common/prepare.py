"""Generic workspace preparation for outer-Agent code generation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline.common import paths
from pipeline.common.artifacts import (
    build_file_manifest,
    partition_manifest,
    write_json,
)


PACKET_SCHEMA = "aaagameforge.code_gen.packet.v1"
SNAPSHOT_SCHEMA = "aaagameforge.code_gen.workspace_snapshot.v1"
DEFAULT_RESERVED_ROOTS = (
    "meta.json",
    "demo_outputs",
    "evaluation",
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


def resolve_task_workspace(
    task: Mapping[str, Any],
    *,
    task_kind: str,
    task_id: str,
    run_id: str,
    output_dir: str | None,
    default_game_id: str | None,
) -> tuple[str, Path, bool]:
    game_id = paths.infer_game_id(
        dict(task),
        fallback=default_game_id,
    )
    if output_dir:
        root = Path(output_dir).expanduser().resolve(
            strict=False
        )
        target = root / task_id
        target.mkdir(parents=True, exist_ok=True)
        return game_id, target, False
    return (
        game_id,
        paths.task_output_dir(
            game_id,
            task_kind,
            task_id,
            run_id=run_id,
        ).resolve(strict=False),
        True,
    )


def code_gen_stage_dir(
    workspace: str | Path,
    *,
    mode: str,
    repair_attempt: int,
) -> Path:
    root = Path(workspace)
    if mode == "generate":
        return root / "demo_outputs" / "code_gen"
    return (
        root
        / "demo_outputs"
        / "repairs"
        / f"attempt_{repair_attempt:02d}"
    )


def _clear_stage_files(
    files: Sequence[Path],
    *,
    force: bool,
) -> None:
    existing = [path for path in files if path.exists()]
    if existing and not force:
        raise FileExistsError(
            "prepared stage already exists; use a new run or "
            "repair attempt, or pass force=True: "
            + ", ".join(str(path) for path in existing)
        )
    for path in existing:
        if not path.is_file():
            raise ValueError(
                f"prepared stage path must be a file: {path}"
            )
        path.unlink()


def prepare_workspace(
    packet: Mapping[str, Any],
    *,
    workspace: str | Path,
    stage_dir: str | Path,
    instructions: str,
    reserved_roots: Sequence[str] = DEFAULT_RESERVED_ROOTS,
    force: bool = False,
) -> dict[str, Any]:
    """Persist one prepared packet, instructions, metadata, and snapshot."""
    task_dir = Path(workspace).expanduser().resolve(
        strict=False
    )
    task_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(stage_dir).expanduser().resolve(
        strict=False
    )
    stage.mkdir(parents=True, exist_ok=True)

    packet_path = stage / "task_packet.json"
    instructions_path = stage / "instructions.md"
    snapshot_path = stage / "workspace_snapshot.json"
    finalize_result_path = stage / "finalize_result.json"
    _clear_stage_files(
        (
            packet_path,
            instructions_path,
            snapshot_path,
            finalize_result_path,
        ),
        force=force,
    )

    prepared = dict(packet)
    prepared["schema_version"] = PACKET_SCHEMA
    prepared["prepared_at"] = _now()
    prepared["workspace"] = str(task_dir)
    instruction_context = prepared.get("instructions", {})
    if not isinstance(instruction_context, Mapping):
        raise TypeError("packet.instructions must be an object")
    prepared["instructions"] = {
        **dict(instruction_context),
        "path": str(instructions_path),
    }
    prepared["artifacts"] = {
        "task_packet_path": str(packet_path),
        "instructions_path": str(instructions_path),
        "workspace_snapshot_path": str(snapshot_path),
        "finalize_result_path": str(finalize_result_path),
        "meta_path": str(task_dir / "meta.json"),
    }

    instructions_path.write_text(
        instructions,
        encoding="utf-8",
    )
    write_json(packet_path, prepared)
    identity_keys = (
        "game_id",
        "run_id",
        "task_kind",
        "task_id",
        "mode",
        "repair_attempt",
        "engine",
        "project_name",
        "gameplay_module_name",
        "standard_output_layout",
    )
    identity = {
        key: prepared.get(key)
        for key in identity_keys
    }
    extra_identity = prepared.get("identity", {})
    if not isinstance(extra_identity, Mapping):
        raise TypeError("packet.identity must be an object")
    identity.update(
        {
            str(key): value
            for key, value in extra_identity.items()
        }
    )
    paths.write_task_meta(
        task_dir,
        {
            "status": "prepared",
            "generation_owner": prepared.get(
                "generation_owner",
                "outer_agent",
            ),
            **identity,
            "output_dir": str(task_dir),
            "workspace": str(task_dir),
            "task_packet_path": str(packet_path),
            "instructions_path": str(instructions_path),
            "workspace_snapshot_path": str(snapshot_path),
            "prepared_at": prepared["prepared_at"],
        },
    )

    snapshot_relative = snapshot_path.relative_to(
        task_dir
    ).as_posix()
    finalize_relative = finalize_result_path.relative_to(
        task_dir
    ).as_posix()
    manifest = build_file_manifest(
        task_dir,
        exclude_relative_paths=(
            snapshot_relative,
            finalize_relative,
        ),
    )
    task_files, protected_files = partition_manifest(
        manifest,
        reserved_roots,
    )
    snapshot = {
        "schema_version": SNAPSHOT_SCHEMA,
        "packet_id": str(
            prepared.get("packet_id") or ""
        ),
        "workspace": str(task_dir),
        "identity": identity,
        "reserved_roots": list(reserved_roots),
        "excluded_manifest_paths": [
            snapshot_relative,
            finalize_relative,
        ],
        "task_files": task_files,
        "protected_files": protected_files,
    }
    write_json(snapshot_path, snapshot)
    return prepared
