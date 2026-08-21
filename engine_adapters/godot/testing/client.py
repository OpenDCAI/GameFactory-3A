"""Stable engine-native test execution for GodotClient v1."""

from __future__ import annotations

import json
import math
import os
import stat
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any

from .._internal import GodotTransport
from ..config import GodotClientConfig
from ..contracts import GodotDiagnostic, GodotOperationResult

DEFAULT_RUNNER = Path(__file__).resolve().parents[1] / "_scripts" / "test_runner.gd"
TEST_REPORT_SCHEMA = "gamefactory3a.godot.tests.v1"
TEST_CASE_STATUSES = frozenset({"passed", "failed", "skipped"})


def _reject_json_constant(value: str) -> None:
    """Reject Python's non-standard NaN/Infinity JSON extensions."""

    raise ValueError(f"non-standard JSON constant {value!r}")


def _validate_strict_json(value: Any) -> None:
    """Recursively reject values that cannot be represented by strict JSON."""

    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"report must contain only strict JSON values: {exc}") from exc


def _absolute_without_resolving(path: Path) -> Path:
    """Make a report destination absolute while retaining link components."""

    return Path(os.path.abspath(str(path)))


def _same_file_or_path(left: Path, right: Path) -> bool:
    """Recognize both lexical/resolved collisions and existing hard links."""

    if left.resolve(strict=False) == right.resolve(strict=False):
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _native_test_inputs(project_dir: Path, test_root: str) -> list[Path]:
    """Return direct GDScript inputs selected by the bundled native runner."""

    value = str(test_root).strip()
    if not value:
        return []
    if value.startswith("res://"):
        raw_relative = value[len("res://") :].replace("\\", "/")
        relative = PurePosixPath(raw_relative)
        if relative.is_absolute() or ".." in relative.parts:
            return []
        root = project_dir / Path(*relative.parts)
    elif "://" in value:
        # Godot owns the mapping for schemes such as user://, so there is no
        # reliable host path to compare here.
        return []
    else:
        root = Path(value).expanduser()
        if not root.is_absolute():
            root = project_dir / root
    try:
        return [
            entry
            for entry in root.iterdir()
            if entry.name.casefold().startswith("test_")
            and entry.name.casefold().endswith(".gd")
            and entry.is_file()
        ]
    except OSError:
        return []


def _validate_report_destination(
    raw_report: Path,
    report: Path,
    protected_inputs: list[tuple[str, Path]],
) -> None:
    """Reject destinations that could replace inputs or write through links."""

    current = Path(raw_report.anchor)
    for part in raw_report.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError(
                f"Godot test report path must not contain a symlink: {current}"
            )
        if current == raw_report:
            if not stat.S_ISREG(mode):
                raise ValueError(
                    f"Godot test report path must be a regular file: {current}"
                )
        elif not stat.S_ISDIR(mode):
            raise NotADirectoryError(
                f"Godot test report parent must be a directory: {current}"
            )

    for label, protected in protected_inputs:
        if _same_file_or_path(report, protected):
            raise ValueError(
                "Godot test report path must not replace protected input "
                f"{label}: {protected}"
            )


def _validated_test_cases(report_data: Any) -> list[dict[str, Any]]:
    """Validate the native runner report before treating its exit code as useful."""

    if not isinstance(report_data, dict):
        raise ValueError("report must be a JSON object")
    if report_data.get("schema_version") != TEST_REPORT_SCHEMA:
        raise ValueError(
            "schema_version must be "
            f"{TEST_REPORT_SCHEMA!r}, got {report_data.get('schema_version')!r}"
        )
    raw_cases = report_data.get("tests")
    if not isinstance(raw_cases, list):
        raise ValueError("tests must be an array")

    cases: list[dict[str, Any]] = []
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise ValueError(f"tests[{index}] must be an object")
        name = raw_case.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"tests[{index}].name must be a non-empty string")
        status = raw_case.get("status")
        if status not in TEST_CASE_STATUSES:
            raise ValueError(
                f"tests[{index}].status must be one of "
                f"{', '.join(sorted(TEST_CASE_STATUSES))}, got {status!r}"
            )
        for field in ("file", "message"):
            if field in raw_case and not isinstance(raw_case[field], str):
                raise ValueError(f"tests[{index}].{field} must be a string")
        if "duration_ms" in raw_case:
            duration = raw_case["duration_ms"]
            if (
                isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or (isinstance(duration, float) and not math.isfinite(duration))
                or duration < 0
            ):
                raise ValueError(
                    f"tests[{index}].duration_ms must be a finite non-negative number"
                )
        cases.append(dict(raw_case))

    expected_counts = {
        "total": len(cases),
        "passed": sum(item["status"] == "passed" for item in cases),
        "failed": sum(item["status"] == "failed" for item in cases),
        "skipped": sum(item["status"] == "skipped" for item in cases),
    }
    for field, expected in expected_counts.items():
        if field not in report_data:
            continue
        actual = report_data[field]
        if isinstance(actual, bool) or not isinstance(actual, int):
            raise ValueError(f"{field} must be an integer")
        if actual != expected:
            raise ValueError(f"{field} must be {expected}, got {actual}")
    return cases


class GodotTestingClient:
    def __init__(self, config: GodotClientConfig) -> None:
        self._config = config
        self._transport = GodotTransport(config)

    def run_automation_tests(
        self,
        test_filter: str = "",
        *,
        script: str | Path = "",
        test_root: str = "res://tests",
        report_path: str | Path = "",
        timeout: float | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        try:
            return self._run_automation_tests(
                test_filter,
                script=script,
                test_root=test_root,
                report_path=report_path,
                timeout=timeout,
                dry_run=dry_run,
            )
        except Exception as exc:
            return GodotOperationResult.failure(
                "testing.run_automation_tests",
                f"{type(exc).__name__}: {exc}",
            ).to_dict()

    def _run_automation_tests(
        self,
        test_filter: str = "",
        *,
        script: str | Path = "",
        test_root: str = "res://tests",
        report_path: str | Path = "",
        timeout: float | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        operation = "testing.run_automation_tests"
        project_dir = self._config.project_dir
        project_file = self._config.project_file
        if project_dir is None or project_file is None or not project_file.is_file():
            return GodotOperationResult.failure(
                operation,
                "project_path does not resolve to an existing project.godot",
            ).to_dict()
        raw_script = str(script).strip()
        if raw_script.startswith("res://"):
            raw_relative = raw_script[len("res://") :].replace("\\", "/")
            relative = PurePosixPath(raw_relative)
            if (
                not raw_relative
                or relative.is_absolute()
                or ".." in relative.parts
                or "." in relative.parts
            ):
                return GodotOperationResult.failure(
                    operation,
                    "Godot test runner must be a non-traversing res:// file",
                ).to_dict()
            runner_file = (project_dir / Path(*relative.parts)).resolve(strict=False)
            try:
                runner_file.relative_to(project_dir.resolve())
            except ValueError:
                return GodotOperationResult.failure(
                    operation, "Godot test runner escaped the project"
                ).to_dict()
            if not runner_file.is_file():
                return GodotOperationResult.failure(
                    operation, f"Godot test runner was not found: {raw_script}"
                ).to_dict()
            runner = "res://" + relative.as_posix()
        else:
            runner_file = (
                Path(raw_script).expanduser() if raw_script else DEFAULT_RUNNER
            )
            if not runner_file.is_absolute():
                runner_file = project_dir / runner_file
            runner_file = runner_file.resolve(strict=False)
            if not runner_file.is_file():
                return GodotOperationResult.failure(
                    operation, f"Godot test runner was not found: {runner_file}"
                ).to_dict()
            runner = str(runner_file)
        raw_report = (
            Path(report_path).expanduser()
            if str(report_path).strip()
            else (self._config.data_root / "reports" / "godot-tests.json")
        )
        if not raw_report.is_absolute():
            raw_report = project_dir / raw_report
        raw_report = _absolute_without_resolving(raw_report)
        report = raw_report.resolve(strict=False)
        try:
            command = self._transport.command(["--script", runner])
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        payload: dict[str, Any] = {
            "runner": runner,
            "test_root": str(test_root),
            "test_filter": str(test_filter),
            "report_path": str(report),
            "command": command,
            "cwd": str(project_dir),
            "dry_run": dry_run,
        }
        protected_inputs = [
            ("project.godot", project_file),
            ("Godot test runner", runner_file),
            *(
                ("Godot test script", path)
                for path in _native_test_inputs(project_dir, str(test_root))
            ),
        ]
        try:
            _validate_report_destination(raw_report, report, protected_inputs)
        except (OSError, ValueError) as exc:
            return GodotOperationResult.failure(
                operation,
                f"Godot test report path is unsafe: {type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        if dry_run:
            return GodotOperationResult.success(operation, payload=payload).to_dict()
        try:
            report.parent.mkdir(parents=True, exist_ok=True)
            _validate_report_destination(raw_report, report, protected_inputs)
        except (OSError, ValueError) as exc:
            return GodotOperationResult.failure(
                operation,
                f"Godot test report path could not be prepared: "
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        return self._run_with_staged_report(
            operation=operation,
            runner=runner,
            test_root=str(test_root),
            test_filter=str(test_filter),
            raw_report=raw_report,
            report=report,
            protected_inputs=protected_inputs,
            timeout=timeout,
            payload=payload,
        )

    def _run_with_staged_report(
        self,
        *,
        operation: str,
        runner: str,
        test_root: str,
        test_filter: str,
        raw_report: Path,
        report: Path,
        protected_inputs: list[tuple[str, Path]],
        timeout: float | None,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Run Godot against a private report and publish only validated JSON."""

        try:
            staging = tempfile.TemporaryDirectory(
                prefix=".a3game-godot-test-", dir=str(report.parent)
            )
        except OSError as exc:
            return GodotOperationResult.failure(
                operation,
                "Godot test report staging could not be prepared: "
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        with staging as temporary:
            staged_report = Path(temporary) / "report.json"
            environment = {
                "A3GAME_GODOT_TEST_ROOT": test_root,
                "A3GAME_GODOT_TEST_FILTER": test_filter,
                "A3GAME_GODOT_TEST_REPORT": str(staged_report),
            }
            return self._collect_staged_report(
                operation=operation,
                runner=runner,
                raw_report=raw_report,
                report=report,
                staged_report=staged_report,
                protected_inputs=protected_inputs,
                timeout=timeout,
                environment=environment,
                payload=payload,
            )

    def _collect_staged_report(
        self,
        *,
        operation: str,
        runner: str,
        raw_report: Path,
        report: Path,
        staged_report: Path,
        protected_inputs: list[tuple[str, Path]],
        timeout: float | None,
        environment: dict[str, str],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        started_at = time.time()
        try:
            result = self._transport.run(
                ["--script", runner],
                timeout=timeout or float(self._config.editor_timeout),
                environment=environment,
            )
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        payload.update(result.to_dict())
        try:
            staged_mode = staged_report.lstat().st_mode
        except FileNotFoundError:
            return GodotOperationResult.failure(
                operation,
                "Godot test runner produced no report; a zero exit code alone is not success",
                payload=payload,
            ).to_dict()
        except OSError as exc:
            return GodotOperationResult.failure(
                operation,
                f"Godot test report could not be inspected: {exc}",
                payload=payload,
            ).to_dict()
        if not stat.S_ISREG(staged_mode):
            return GodotOperationResult.failure(
                operation,
                "Godot test runner produced a non-regular report",
                payload=payload,
            ).to_dict()
        if staged_report.lstat().st_mtime < started_at - 1.0:
            return GodotOperationResult.failure(
                operation,
                "Godot test report is stale",
                payload=payload,
            ).to_dict()
        try:
            report_data = json.loads(
                staged_report.read_text(encoding="utf-8"),
                parse_constant=_reject_json_constant,
            )
            _validate_strict_json(report_data)
        except (json.JSONDecodeError, OSError, TypeError, ValueError) as exc:
            return GodotOperationResult.failure(
                operation,
                "Godot test report is invalid strict JSON: "
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        try:
            normalized_cases = _validated_test_cases(report_data)
        except ValueError as exc:
            payload["report_schema_version"] = (
                report_data.get("schema_version")
                if isinstance(report_data, dict)
                else None
            )
            return GodotOperationResult.failure(
                operation,
                f"Godot test report schema is invalid: {exc}",
                payload=payload,
            ).to_dict()
        try:
            _validate_report_destination(raw_report, report, protected_inputs)
            if not stat.S_ISREG(staged_report.lstat().st_mode):
                raise ValueError("Godot staged test report must be a regular file")
            os.replace(staged_report, report)
        except (OSError, ValueError) as exc:
            return GodotOperationResult.failure(
                operation,
                "Godot test report could not be published: "
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        failed = [
            item
            for item in normalized_cases
            if str(item.get("status") or "") == "failed"
        ]
        passed = [
            item
            for item in normalized_cases
            if str(item.get("status") or "") == "passed"
        ]
        skipped = [
            item
            for item in normalized_cases
            if str(item.get("status") or "") == "skipped"
        ]
        payload.update(
            {
                "matched_count": len(normalized_cases),
                "passed_count": len(passed),
                "failed_count": len(failed),
                "skipped_count": len(skipped),
                "cases": normalized_cases,
                "failed_cases": failed,
            }
        )
        diagnostics = [
            GodotDiagnostic(
                severity="error",
                code="GODOT_TEST_FAILED",
                message=str(
                    item.get("message") or f"Test failed: {item.get('name', '')}"
                ),
                file=str(item.get("file") or ""),
                source="godot_test_runner",
            )
            for item in failed
        ]
        if not normalized_cases:
            return GodotOperationResult.failure(
                operation,
                "Godot test report matched zero tests",
                payload=payload,
            ).to_dict()
        if result.returncode != 0 or failed:
            return GodotOperationResult.failure(
                operation,
                f"{len(failed)} of {len(normalized_cases)} Godot tests failed",
                diagnostics=diagnostics,
                payload=payload,
            ).to_dict()
        return GodotOperationResult.success(
            operation,
            artifacts=[
                {"type": "godot_test_report", "path": str(report), "state": "ready"}
            ],
            warnings=([f"{len(skipped)} Godot tests were skipped"] if skipped else []),
            payload=payload,
        ).to_dict()
