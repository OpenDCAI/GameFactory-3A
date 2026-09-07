"""Evaluate an existing game playtest report without running the game again."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.common.artifacts import write_json


REPORT_SCHEMA = "a3game.playtest_report.v1"


def evaluate_report(
    report_path: str | Path,
    *,
    max_console_errors: int = 0,
) -> dict[str, Any]:
    """Score recording integrity and runtime errors from a fresh report."""
    path = Path(report_path).expanduser().resolve(strict=False)
    errors: list[str] = []
    report: dict[str, Any] = {}
    if not path.is_file():
        errors.append(f"Playtest report is missing: {path}")
    else:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError("report root must be an object")
            report = raw
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"Invalid playtest report: {exc}")

    engine = str(report.get("engine") or "").strip().lower()
    actions = report.get("actions", [])
    executed = report.get("executed_actions", [])
    page_errors = report.get("page_errors", [])
    console_errors = report.get("console_errors", [])
    if not isinstance(actions, list):
        actions = []
        errors.append("report.actions must be an array")
    if not isinstance(executed, list):
        executed = []
        errors.append("report.executed_actions must be an array")
    if not isinstance(page_errors, list):
        page_errors = []
        errors.append("report.page_errors must be an array")
    if not isinstance(console_errors, list):
        console_errors = []
        errors.append("report.console_errors must be an array")

    failed_actions = [item for item in executed if isinstance(item, dict) and not item.get("ok")]
    fallback_only = bool(actions) and all(
        isinstance(item, dict) and item.get("source") == "fallback" for item in actions
    )
    checks = {
        "schema": report.get("schema_version") == REPORT_SCHEMA,
        "completed": report.get("status") == "completed",
        "frames_captured": isinstance(report.get("frames"), int) and report.get("frames", 0) > 0,
        "actions_available": bool(actions),
        "actions_executed": bool(executed),
        "no_failed_actions": not failed_actions,
        "no_page_errors": not page_errors,
        "console_error_budget": len(console_errors) <= max_console_errors,
        "no_crash": not str(report.get("crash") or "").strip(),
    }
    for name, passed in checks.items():
        if not passed:
            errors.append(f"Playtest check failed: {name}")
    warnings = [str(item) for item in report.get("warnings", [])]
    if fallback_only:
        warnings.append(
            "Only fallback actions were used; expose __A3GAME_PLAYTEST__.actions for game-specific coverage."
        )
    return {
        "ok": not errors,
        "status": "passed" if not errors else "failed",
        "engine": report.get("engine"),
        "report_path": str(path),
        "scope": "blender_playtest_recording" if engine == "blender" else "browser_playtest_recording",
        "authoritative_validation": False,
        "score": 1.0 if not errors else 0.0,
        "checks": checks,
        "summary": {
            "actions": len(actions),
            "executed_actions": len(executed),
            "failed_actions": len(failed_actions),
            "frames": report.get("frames", 0),
            "page_errors": len(page_errors),
            "console_errors": len(console_errors),
            "video": report.get("video"),
        },
        "warnings": list(dict.fromkeys(warnings)),
        "errors": list(dict.fromkeys(errors)),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate one existing playtest report.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--out", default="", help="Optional path for the evaluation JSON")
    parser.add_argument("--max-console-errors", type=int, default=0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = evaluate_report(args.report, max_console_errors=args.max_console_errors)
    if args.out:
        write_json(Path(args.out).expanduser().resolve(strict=False), result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
