"""Prepare or finalize one outer-Agent Generate-Mechanic task."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.code_gen.gen_mechanic.artifacts import (
    finalize,
    required_artifact_checks as _required_artifact_checks,
    scan_ue_ui_contamination as _scan_ue_ui_contamination,
)
from pipeline.code_gen.gen_mechanic.contracts import (
    MECHANIC_CONTRACT_FILENAME,
    MECHANIC_CONTRACT_SCHEMA,
    validate_mechanic_contract as _validate_mechanic_contract,
)
from pipeline.code_gen.gen_mechanic.packet import (
    CONTEXT_ROOT,
    ENGINE_CONTEXT_ROOT,
    PROMPTS_ROOT,
    REPAIR_PROMPT_PATH,
    SKILL_PATH,
    SYSTEM_PROMPT_PATH,
    TASK_KIND,
    TASK_PROMPT_PATH,
    prepare,
)
from pipeline.common import paths
from pipeline.common.artifacts import read_json
from pipeline.common.code_gen import select_task


def _select_task(
    tasks_path: str | Path,
    *,
    game_id: str | None,
    task_id: str | None,
) -> dict[str, Any]:
    return select_task(
        tasks_path,
        game_id=game_id,
        task_id=task_id,
        task_name="Mechanic",
    )


def _direct_task(args: argparse.Namespace) -> dict[str, Any]:
    if not args.engine:
        raise ValueError(
            "--engine is required with --requirement-path"
        )
    task: dict[str, Any] = {
        "game_id": args.game,
        "task_id": args.task_id or "demo",
        "engine": args.engine,
        "requirement_path": args.requirement_path,
        "project_name": args.project_name,
        "gameplay_module_name": args.module_name,
        "example_paths": list(args.example),
        "asset_sources": [],
        "motion_sources": [],
        "acceptance_criteria": [],
        "required_output_artifacts": [],
    }
    return {
        key: value
        for key, value in task.items()
        if value is not None and value != ""
    }


def _prepare_command(args: argparse.Namespace) -> int:
    run_id = (
        paths.new_run_id()
        if args.run_id == "auto"
        else args.run_id
    )
    if args.requirement_path:
        task = _direct_task(args)
    else:
        tasks_path = paths.resolve_tasks_path(
            TASK_KIND,
            args.tasks,
            args.game,
        )
        task = select_task(
            tasks_path,
            game_id=args.game,
            task_id=args.task_id,
            task_label="Mechanic",
        )
    if args.mode:
        task["mode"] = args.mode
    if args.repair_json:
        task["mode"] = "repair"
        task["repair"] = read_json(
            args.repair_json,
            "Mechanic repair payload",
        )
    packet = prepare(
        task,
        run_id=run_id,
        output_dir=args.out_dir,
        default_game_id=args.game,
        force=args.force,
    )
    print(
        json.dumps(
            {
                "packet_id": packet["packet_id"],
                "workspace": packet["workspace"],
                "task_packet_path": packet[
                    "artifacts"
                ]["task_packet_path"],
                "instructions_path": packet[
                    "artifacts"
                ]["instructions_path"],
                "skill_path": packet["context"][
                    "skill_path"
                ],
                "engine_context_path": packet[
                    "context"
                ]["engine_context_path"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _finalize_command(args: argparse.Namespace) -> int:
    result = finalize(
        args.packet,
        summary=args.summary,
    )
    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if result["ok"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare or finalize one outer-Agent Mechanic "
            "code-generation task."
        )
    )
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )
    prepare_parser = subparsers.add_parser(
        "prepare",
        help="Prepare one task packet and workspace.",
    )
    prepare_parser.add_argument("--game", default=None)
    prepare_parser.add_argument("--tasks", default=None)
    prepare_parser.add_argument("--task-id", default=None)
    prepare_parser.add_argument(
        "--run-id",
        default=paths.DEFAULT_RUN_ID,
    )
    prepare_parser.add_argument("--out-dir", default=None)
    prepare_parser.add_argument(
        "--mode",
        choices=["generate", "repair"],
        default=None,
    )
    prepare_parser.add_argument(
        "--repair-json",
        default=None,
    )
    prepare_parser.add_argument(
        "--requirement-path",
        default=None,
    )
    prepare_parser.add_argument("--engine", default=None)
    prepare_parser.add_argument(
        "--project-name",
        default=None,
    )
    prepare_parser.add_argument(
        "--module-name",
        default=None,
    )
    prepare_parser.add_argument(
        "--example",
        action="append",
        default=[],
    )
    prepare_parser.add_argument(
        "--force",
        action="store_true",
    )
    prepare_parser.set_defaults(handler=_prepare_command)

    finalize_parser = subparsers.add_parser(
        "finalize",
        help="Finalize a directly edited workspace.",
    )
    finalize_parser.add_argument(
        "--packet",
        required=True,
    )
    finalize_parser.add_argument(
        "--summary",
        default="",
    )
    finalize_parser.set_defaults(handler=_finalize_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (FileNotFoundError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    return 2


__all__ = [
    "build_parser",
    "finalize",
    "main",
    "prepare",
]


if __name__ == "__main__":
    raise SystemExit(main())
