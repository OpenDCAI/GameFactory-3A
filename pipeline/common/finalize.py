"""Generic workspace finalization for outer-Agent code generation."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline.common import paths
from pipeline.common.artifacts import (
    build_file_manifest,
    diff_file_manifests,
    is_relative_to,
    partition_manifest,
    read_json,
    write_json,
)
from pipeline.common.prepare import (
    PACKET_SCHEMA,
    SNAPSHOT_SCHEMA,
)


ArtifactChecker = Callable[
    [
        Path,
        Sequence[str],
        Mapping[str, Mapping[str, Any]],
    ],
    tuple[dict[str, bool | None], list[str], list[str]],
]


def _now() -> str:
    return datetime.now().astimezone().isoformat(
        timespec="seconds"
    )


def _manifest(
    value: Any,
    name: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be an object")
    result: dict[str, dict[str, Any]] = {}
    for relative_path, metadata in value.items():
        if not isinstance(metadata, Mapping):
            raise TypeError(
                f"{name}.{relative_path} must be an object"
            )
        result[str(relative_path)] = dict(metadata)
    return result


def _changed_paths_text(
    changes: Mapping[str, Sequence[str]],
) -> str:
    items = [
        f"{field}={list(values)}"
        for field, values in changes.items()
        if values
    ]
    return "; ".join(items)


def _write_summary(
    result: dict[str, Any],
    *,
    standard_output_layout: bool,
) -> None:
    if standard_output_layout:
        paths.write_results_summary(
            [result],
            result["task_kind"],
            result["run_id"],
        )
        return
    summary_path = (
        Path(result["output_dir"]).parent
        / "results_summary.json"
    )
    write_json(summary_path, [result])


def finalize_workspace(
    packet_path: str | Path,
    *,
    summary: str = "",
    artifact_checker: ArtifactChecker | None = None,
) -> dict[str, Any]:
    """Validate direct edits and persist one generic finalized result."""
    started = time.time()
    packet_file = Path(packet_path).expanduser().resolve(
        strict=False
    )
    if packet_file.name != "task_packet.json":
        raise ValueError(
            "packet_path must point to task_packet.json"
        )
    stage_dir = packet_file.parent
    snapshot_path = stage_dir / "workspace_snapshot.json"
    finalize_result_path = stage_dir / "finalize_result.json"
    snapshot = read_json(
        snapshot_path,
        "Code-generation workspace snapshot",
    )
    if snapshot.get("schema_version") != SNAPSHOT_SCHEMA:
        raise ValueError(
            "Unsupported code-generation snapshot schema"
        )
    workspace = Path(
        str(snapshot.get("workspace") or "")
    ).expanduser().resolve(strict=False)
    if not workspace.is_dir():
        raise FileNotFoundError(
            f"Prepared workspace was not found: {workspace}"
        )
    if not is_relative_to(packet_file, workspace):
        raise ValueError(
            "Prepared task packet is outside its workspace"
        )

    excluded = snapshot.get("excluded_manifest_paths", [])
    reserved_roots = snapshot.get("reserved_roots", [])
    if isinstance(excluded, (str, bytes)) or not isinstance(
        excluded,
        Sequence,
    ):
        raise TypeError(
            "snapshot.excluded_manifest_paths must be a sequence"
        )
    if (
        isinstance(reserved_roots, (str, bytes))
        or not isinstance(reserved_roots, Sequence)
    ):
        raise TypeError(
            "snapshot.reserved_roots must be a sequence"
        )
    current_manifest = build_file_manifest(
        workspace,
        exclude_relative_paths=[
            str(item)
            for item in excluded
        ],
    )
    current_task_files, current_protected_files = (
        partition_manifest(
            current_manifest,
            [str(item) for item in reserved_roots],
        )
    )
    baseline_task_files = _manifest(
        snapshot.get("task_files", {}),
        "snapshot.task_files",
    )
    baseline_protected_files = _manifest(
        snapshot.get("protected_files", {}),
        "snapshot.protected_files",
    )
    task_changes = diff_file_manifests(
        baseline_task_files,
        current_task_files,
    )
    protected_changes = diff_file_manifests(
        baseline_protected_files,
        current_protected_files,
    )

    errors: list[str] = []
    warnings: list[str] = []
    if any(protected_changes.values()):
        errors.append(
            "Outer Agent modified Pipeline-owned workspace paths: "
            + _changed_paths_text(protected_changes)
        )

    packet = read_json(
        packet_file,
        "Prepared code-generation task packet",
    )
    if packet.get("schema_version") != PACKET_SCHEMA:
        errors.append(
            "Unsupported prepared task packet schema"
        )
    if str(packet.get("packet_id") or "") != str(
        snapshot.get("packet_id") or ""
    ):
        errors.append(
            "Prepared packet identity does not match its snapshot"
        )
    if Path(
        str(packet.get("workspace") or "")
    ).resolve(strict=False) != workspace:
        errors.append(
            "Prepared packet workspace does not match its snapshot"
        )

    reported_changes = [
        *task_changes["generated_files"],
        *task_changes["modified_files"],
        *task_changes["deleted_files"],
    ]
    if not reported_changes:
        errors.append(
            "Workspace contains no task-owned file changes"
        )

    required = packet.get("required_output_artifacts", [])
    if isinstance(required, (str, bytes)) or not isinstance(
        required,
        Sequence,
    ):
        errors.append(
            "required_output_artifacts must be a sequence"
        )
        required = []
    artifact_checks: dict[str, bool | None] = {}
    if artifact_checker is not None:
        checks, check_errors, check_warnings = artifact_checker(
            workspace,
            [str(item) for item in required],
            current_task_files,
        )
        artifact_checks.update(checks)
        errors.extend(check_errors)
        warnings.extend(check_warnings)

    identity = snapshot.get("identity")
    if not isinstance(identity, Mapping):
        raise TypeError("snapshot.identity must be an object")
    ok = not errors
    result = {
        "ok": ok,
        "status": "completed" if ok else "failed",
        "generation_owner": "outer_agent",
        "game_id": str(identity.get("game_id") or ""),
        "run_id": str(identity.get("run_id") or ""),
        "task_kind": str(identity.get("task_kind") or ""),
        "task_id": str(identity.get("task_id") or ""),
        "mode": str(identity.get("mode") or ""),
        "repair_attempt": int(
            identity.get("repair_attempt") or 0
        ),
        "engine": str(identity.get("engine") or ""),
        "project_name": str(
            identity.get("project_name") or ""
        ),
        "gameplay_module_name": str(
            identity.get("gameplay_module_name") or ""
        ),
        "output_dir": str(workspace),
        "workspace": str(workspace),
        "task_packet_path": str(packet_file),
        "instructions_path": str(
            stage_dir / "instructions.md"
        ),
        "workspace_snapshot_path": str(snapshot_path),
        "finalize_result_path": str(
            finalize_result_path
        ),
        "generated_files": task_changes[
            "generated_files"
        ],
        "modified_files": task_changes[
            "modified_files"
        ],
        "deleted_files": task_changes[
            "deleted_files"
        ],
        "protected_path_changes": protected_changes,
        "required_artifact_checks": artifact_checks,
        "warnings": list(dict.fromkeys(warnings)),
        "errors": list(dict.fromkeys(errors)),
        "summary": str(summary or "").strip(),
        "finalized_at": _now(),
        "elapsed_sec": round(time.time() - started, 2),
    }
    for key, value in identity.items():
        result.setdefault(str(key), value)
    write_json(finalize_result_path, result)
    if ok:
        paths.write_task_meta(workspace, result)
    _write_summary(
        result,
        standard_output_layout=bool(
            identity.get("standard_output_layout")
        ),
    )
    return result
