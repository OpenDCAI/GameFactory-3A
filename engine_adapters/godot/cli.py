"""Command-line entry point delegating to the public GodotClient API."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import GodotClient


def _source(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "source_json", ""):
        payload = json.loads(Path(args.source_json).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("--source-json must contain a JSON object")
        return payload
    return {
        "game_id": args.game_id,
        "run_id": args.run_id,
        "task_kind": args.task_kind,
        "task_id": args.task_id,
        "artifact_key": args.artifact_key,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="3AGameFactory Godot adapter")
    parser.add_argument(
        "--project", default=None, help="Godot project directory or project.godot"
    )
    parser.add_argument("--godot", default=None, help="Godot editor executable")
    parser.add_argument("--runtime-host", default=None)
    parser.add_argument("--runtime-port", type=int, default=None)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("info")
    status = commands.add_parser("status")
    status.add_argument("--check-runtime", action="store_true")

    create = commands.add_parser("create-project")
    create.add_argument("--name", default="")
    create.add_argument(
        "--renderer",
        choices=("forward_plus", "mobile", "gl_compatibility"),
        default="gl_compatibility",
    )
    create.add_argument("--overwrite", action="store_true")
    create.add_argument("--dry-run", action="store_true")

    validate = commands.add_parser("validate-project")
    validate.add_argument("--no-engine", action="store_true")

    import_asset = commands.add_parser("import-asset")
    import_asset.add_argument("--source-json", default="")
    import_asset.add_argument("--game-id", default="")
    import_asset.add_argument("--run-id", default="default")
    import_asset.add_argument("--task-kind", default="")
    import_asset.add_argument("--task-id", default="")
    import_asset.add_argument("--artifact-key", default="")
    import_asset.add_argument("--asset-type", required=True)
    import_asset.add_argument("--destination", default="")
    import_asset.add_argument("--name", default="")
    import_asset.add_argument("--asset-id", default="")
    import_asset.add_argument("--replace-existing", action="store_true")
    import_asset.add_argument("--dry-run", action="store_true")

    framework = commands.add_parser("install-framework")
    framework.add_argument("--replace-existing", action="store_true")
    framework.add_argument("--no-enable", action="store_true")
    framework.add_argument("--dry-run", action="store_true")

    build = commands.add_parser("build")
    build.add_argument("--preset", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--debug", action="store_true")
    build.add_argument("--pack-only", action="store_true")
    build.add_argument("--allow-external-output", action="store_true")
    build.add_argument("--dry-run", action="store_true")

    test = commands.add_parser("test")
    test.add_argument("--filter", default="")
    test.add_argument("--script", default="")
    test.add_argument("--test-root", default="res://tests")
    test.add_argument("--report", default="")
    test.add_argument("--dry-run", action="store_true")

    editor = commands.add_parser("launch-editor")
    editor.add_argument("--scene", default="")
    editor.add_argument("--dry-run", action="store_true")

    game = commands.add_parser("launch-game")
    game.add_argument("--scene", default="")
    game.add_argument("--headless", action="store_true")
    game.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = GodotClient(
            project_path=args.project,
            godot_executable=args.godot,
            runtime_host=args.runtime_host,
            runtime_port=args.runtime_port,
        )
        if args.command == "info":
            result = client.get_environment_info()
        elif args.command == "status":
            result = client.observe.check_status(check_runtime=args.check_runtime)
        elif args.command == "create-project":
            result = client.project.create(
                project_name=args.name,
                renderer=args.renderer,
                overwrite=args.overwrite,
                dry_run=args.dry_run,
            )
        elif args.command == "validate-project":
            result = client.project.validate(check_engine=not args.no_engine)
        elif args.command == "import-asset":
            result = client.assets.import_asset(
                _source(args),
                args.asset_type,
                destination=args.destination,
                options={
                    "name": args.name,
                    "asset_id": args.asset_id,
                    "replace_existing": args.replace_existing,
                    "dry_run": args.dry_run,
                },
            )
        elif args.command == "install-framework":
            result = client.plugin.install_framework(
                replace_existing=args.replace_existing,
                enable=not args.no_enable,
                dry_run=args.dry_run,
            )
        elif args.command == "build":
            result = client.build.project(
                preset=args.preset,
                output_path=args.output,
                debug=args.debug,
                pack_only=args.pack_only,
                allow_external_output=args.allow_external_output,
                dry_run=args.dry_run,
            )
        elif args.command == "test":
            result = client.testing.run_automation_tests(
                args.filter,
                script=args.script,
                test_root=args.test_root,
                report_path=args.report,
                dry_run=args.dry_run,
            )
        elif args.command == "launch-editor":
            result = client.runtime.launch_editor(
                scene_path=args.scene, dry_run=args.dry_run
            )
        else:
            result = client.runtime.launch_game(
                scene_path=args.scene,
                headless=args.headless,
                dry_run=args.dry_run,
            )
    except Exception as exc:
        result = {
            "ok": False,
            "operation": f"cli.{args.command}",
            "artifacts": [],
            "diagnostics": [],
            "warnings": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
            "payload": {},
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1
