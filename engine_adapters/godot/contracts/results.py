"""Stable result structures returned by GodotClient v1 operations."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GodotDiagnostic:
    severity: str
    message: str
    code: str = ""
    file: str = ""
    line: int | None = None
    column: int | None = None
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "severity": self.severity,
            "message": self.message,
        }
        for key, value in {
            "code": self.code,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "source": self.source,
        }.items():
            if value not in {"", None}:
                result[key] = value
        return result


@dataclass(frozen=True)
class GodotOperationResult:
    operation: str
    ok: bool
    artifacts: tuple[dict[str, Any], ...] = ()
    diagnostics: tuple[GodotDiagnostic, ...] = ()
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def success(
        cls,
        operation: str,
        *,
        artifacts: list[dict[str, Any]] | None = None,
        diagnostics: list[GodotDiagnostic] | None = None,
        warnings: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> GodotOperationResult:
        return cls(
            operation=operation,
            ok=True,
            artifacts=tuple(artifacts or ()),
            diagnostics=tuple(diagnostics or ()),
            warnings=tuple(warnings or ()),
            payload=dict(payload or {}),
        )

    @classmethod
    def failure(
        cls,
        operation: str,
        *errors: str,
        diagnostics: list[GodotDiagnostic] | None = None,
        warnings: list[str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> GodotOperationResult:
        return cls(
            operation=operation,
            ok=False,
            diagnostics=tuple(diagnostics or ()),
            warnings=tuple(warnings or ()),
            errors=tuple(str(error) for error in errors if str(error)),
            payload=dict(payload or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "ok": self.ok,
            "operation": self.operation,
            "artifacts": [dict(item) for item in self.artifacts],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "payload": dict(self.payload),
        }
        try:
            json.dumps(result, allow_nan=False)
        except (TypeError, ValueError) as exc:
            return {
                "ok": False,
                "operation": str(self.operation),
                "artifacts": [],
                "diagnostics": [],
                "warnings": [str(item) for item in self.warnings],
                "errors": [
                    *[str(item) for item in self.errors],
                    (
                        "Godot operation result must contain only strict JSON values: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                ],
                "payload": {},
            }
        return result
