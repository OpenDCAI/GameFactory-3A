"""Public command-line entry points backed only by UEClient."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any, Sequence

from engine_adapters.ue5 import UEClient


def _emit(result: dict[str, Any]) -> int:
    print(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result.get("ok") else 1


def _client(
    args: argparse.Namespace,
    *,
    project_attr: str = "project",
) -> UEClient:
    return UEClient(
        project_path=getattr(args, project_attr, ""),
        ue_root=args.ue_root,
        api_version=args.api_version,
        host=getattr(args, "remote_host", None),
        port=getattr(args, "remote_port", None),
        runtime_host=getattr(args, "runtime_host", None),
        runtime_port=getattr(args, "runtime_port", None),
        python_transport=getattr(
            args,
            "python_transport",
            None,
        ),
    )


def _source_descriptor(
    args: argparse.Namespace,
) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "game_id": args.game_id,
            "run_id": args.run_id,
            "task_kind": args.task_kind,
            "task_id": args.task_id,
            "artifact_key": args.artifact_key,
        }.items()
        if value
    }


def _create_project(args: argparse.Namespace) -> dict[str, Any]:
    client = _client(args, project_attr="project_path")
    created = client.project.create(dry_run=args.dry_run)
    result = {
        "ok": bool(created.get("ok")),
        "operation": "scripts.create_project",
        "artifacts": list(created.get("artifacts") or []),
        "diagnostics": list(
            created.get("diagnostics") or []
        ),
        "warnings": list(created.get("warnings") or []),
        "errors": list(created.get("errors") or []),
        "payload": {
            "create": created,
            "build_requested": not args.skip_build,
            "dry_run": args.dry_run,
        },
    }
    if (
        not created.get("ok")
        or args.dry_run
        or args.skip_build
    ):
        return result

    built = client.build.project(
        target=args.target,
        configuration=args.configuration,
        timeout=args.build_timeout,
    )
    result["payload"]["build"] = built
    result["ok"] = bool(built.get("ok"))
    result["artifacts"].extend(
        built.get("artifacts") or []
    )
    result["diagnostics"].extend(
        built.get("diagnostics") or []
    )
    result["warnings"].extend(
        built.get("warnings") or []
    )
    result["errors"].extend(built.get("errors") or [])
    return result


def _wait_for_editor(
    client: UEClient,
    *,
    map_path: str,
    no_launch_editor: bool,
    timeout: float,
) -> dict[str, Any]:
    def accept_python_readiness(
        status: dict[str, Any],
    ) -> bool:
        payload = status.get("payload") or {}
        python_status = payload.get("python_execution") or {}
        if not python_status.get("ok"):
            return False
        status["ok"] = True
        status["errors"] = []
        warnings = list(status.get("warnings") or [])
        warning = (
            "Remote Control is unavailable; continuing because "
            "Unreal Python execution is ready"
        )
        if warning not in warnings:
            warnings.append(warning)
        status["warnings"] = warnings
        payload["readiness"] = "python_execution"
        status["payload"] = payload
        return True

    status = client.observe.check_status(
        timeout=min(5.0, timeout),
    )
    if status.get("ok") or accept_python_readiness(status):
        return status
    if no_launch_editor:
        return status

    launched = client.runtime.launch_editor(
        map_path=map_path,
    )
    if not launched.get("ok"):
        return launched

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.observe.check_status(
            timeout=min(3.0, timeout),
        )
        if (
            status.get("ok")
            or accept_python_readiness(status)
        ):
            status["payload"]["launched_editor"] = launched
            return status
        time.sleep(1.0)
    status["errors"] = [
        f"Unreal Editor was not ready after {timeout:.1f}s"
    ]
    status["payload"]["launched_editor"] = launched
    return status


def _import_asset(args: argparse.Namespace) -> dict[str, Any]:
    client = _client(args)
    source = _source_descriptor(args)
    asset_type = str(args.asset_type).strip().lower()
    if args.dry_run:
        resolved = client.assets.resolve_source(
            source,
            asset_type=asset_type,
        )
        return {
            "ok": bool(resolved.get("ok")),
            "operation": "scripts.import_asset",
            "artifacts": list(
                resolved.get("artifacts") or []
            ),
            "diagnostics": list(
                resolved.get("diagnostics") or []
            ),
            "warnings": list(
                resolved.get("warnings") or []
            ),
            "errors": list(resolved.get("errors") or []),
            "payload": {
                "dry_run": True,
                "source": source,
                "asset_type": asset_type,
                "destination": args.destination,
                "resolved": resolved,
            },
        }

    ready = _wait_for_editor(
        client,
        map_path=args.map,
        no_launch_editor=args.no_launch_editor,
        timeout=args.editor_timeout,
    )
    if not ready.get("ok"):
        ready["operation"] = "scripts.import_asset"
        return ready

    options = {
        key: value
        for key, value in {
            "category": args.category,
            "generate_collision": args.collision,
        }.items()
        if value is not None and value != ""
    }
    if asset_type == "scene":
        result = client.world.build(
            source,
            options={
                "world_id": args.world_id,
                "project_id": args.project_id,
                "publish": not args.no_publish,
                "native_map": args.native_map,
                "replace_existing": args.replace_existing,
                "preview_in_editor": not args.no_preview,
                "repair_missing_collision": (
                    args.repair_missing_collision
                ),
            },
        )
    elif asset_type == "motion":
        result = client.assets.import_motion(
            source,
            skeleton=args.skeleton,
            destination=args.destination,
            options=options,
        )
    elif asset_type == "effect":
        result = client.assets.import_effect(
            source,
            destination=args.destination,
            options={
                "replace_existing": args.replace_existing,
            },
        )
    else:
        result = client.assets.import_asset(
            source,
            asset_type,
            destination=args.destination,
            options=options,
        )
    result["payload"]["environment_status"] = ready
    return result


def _run_editor(args: argparse.Namespace) -> dict[str, Any]:
    client = _client(args)
    return client.runtime.launch_editor(
        map_path=args.map,
        extra_args=args.extra_arg,
        dry_run=args.dry_run,
    )


def _record_playtest(args: argparse.Namespace) -> dict[str, Any]:
    client = _client(args)
    action_plan = json.loads(args.actions) if args.actions else None
    return client.playtest.record(
        output_dir=args.output_dir,
        map_path=args.map,
        scenario=args.scenario or None,
        action_plan=action_plan,
        duration=args.duration,
        fps=args.fps,
        warmup=args.warmup,
        timeout=args.timeout,
        ffmpeg=args.ffmpeg or None,
        dry_run=args.dry_run,
    )


def _add_client_arguments(
    parser: argparse.ArgumentParser,
) -> None:
    parser.add_argument("--ue-root", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--api-version", default="v1")
    parser.add_argument(
        "--remote-host",
        default="127.0.0.1",
    )
    parser.add_argument(
        "--remote-port",
        type=int,
        default=30010,
    )
    parser.add_argument(
        "--runtime-host",
        default="127.0.0.1",
    )
    parser.add_argument(
        "--runtime-port",
        type=int,
        default=30020,
    )
    parser.add_argument(
        "--python-transport",
        choices=("remote_execution", "remote_control"),
        default="remote_execution",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a3game-ue")
    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    create = commands.add_parser("create-project")
    create.add_argument("--ue-root", required=True)
    create.add_argument("--project-path", required=True)
    create.add_argument("--api-version", default="v1")
    create.add_argument("--target", default="")
    create.add_argument(
        "--configuration",
        default="Development",
    )
    create.add_argument(
        "--build-timeout",
        type=float,
        default=None,
    )
    create.add_argument("--skip-build", action="store_true")
    create.add_argument("--dry-run", action="store_true")

    asset = commands.add_parser("import-asset")
    _add_client_arguments(asset)
    asset.add_argument("--game-id", required=True)
    asset.add_argument("--run-id", default="default")
    asset.add_argument("--task-kind", default="")
    asset.add_argument("--task-id", required=True)
    asset.add_argument("--artifact-key", default="")
    asset.add_argument("--type", dest="asset_type", required=True)
    asset.add_argument("--destination", default="")
    asset.add_argument("--category", default="")
    asset.add_argument("--skeleton", default="")
    asset.add_argument(
        "--collision",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    asset.add_argument("--map", default="")
    asset.add_argument(
        "--no-launch-editor",
        action="store_true",
    )
    asset.add_argument(
        "--editor-timeout",
        type=float,
        default=180.0,
    )
    asset.add_argument("--world-id", default="")
    asset.add_argument("--project-id", default="")
    asset.add_argument("--native-map", default="")
    asset.add_argument("--no-publish", action="store_true")
    asset.add_argument("--no-preview", action="store_true")
    asset.add_argument(
        "--replace-existing",
        action="store_true",
    )
    asset.add_argument(
        "--repair-missing-collision",
        action="store_true",
    )
    asset.add_argument("--dry-run", action="store_true")

    run = commands.add_parser("run")
    _add_client_arguments(run)
    run.add_argument("--map", default="")
    run.add_argument(
        "--extra-arg",
        action="append",
        default=[],
    )
    run.add_argument("--dry-run", action="store_true")

    playtest = commands.add_parser("playtest")
    _add_client_arguments(playtest)
    playtest.add_argument("--output-dir", required=True)
    playtest.add_argument("--map", default="")
    playtest.add_argument("--scenario", default="")
    playtest.add_argument("--actions", default="")
    playtest.add_argument("--duration", type=float, default=12.0)
    playtest.add_argument("--fps", type=int, default=20)
    playtest.add_argument("--warmup", type=float, default=0.0)
    playtest.add_argument("--ffmpeg", default="")
    playtest.add_argument("--timeout", type=float, default=None)
    playtest.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.command == "create-project":
            return _emit(_create_project(args))
        if args.command == "import-asset":
            return _emit(_import_asset(args))
        if args.command == "run":
            return _emit(_run_editor(args))
        if args.command == "playtest":
            return _emit(_record_playtest(args))
        raise AssertionError(args.command)
    except (
        FileExistsError,
        FileNotFoundError,
        RuntimeError,
        TimeoutError,
        ValueError,
    ) as exc:
        return _emit(
            {
                "ok": False,
                "operation": "scripts.error",
                "artifacts": [],
                "diagnostics": [],
                "warnings": [],
                "errors": [
                    f"{type(exc).__name__}: {exc}"
                ],
                "payload": {},
            }
        )


if __name__ == "__main__":
    raise SystemExit(main())
