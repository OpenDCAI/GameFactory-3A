"""Task-neutral helpers for outer-Agent code-generation pipelines."""

from __future__ import annotations

import re
import importlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pipeline.common import paths
from pipeline.common.artifacts import (
    is_relative_to,
    path_digest,
    read_json,
    resolve_repo_path,
)


CONTEXT_USED_SCHEMA = "gamefactory3a.context_used.v1"
EXAMPLE_REFERENCE_PURPOSES = (
    "module_structure",
    "build_configuration",
    "runtime_adapter",
    "native_test",
    "ui_binding",
    "engine_native_ui",
    "browser_session",
    "stream_presentation",
    "launcher_contract",
)
ENGINE_CONTEXT_ROOT = (
    paths.REPO_ROOT
    / "agent_skills"
    / "engine_context"
)
BROWSER_PLAYER_ROOT = (
    paths.REPO_ROOT
    / "engine_adapters"
    / "browser_serving"
    / "frontend"
    / "player"
)
BROWSER_PLAY_EXAMPLE_ROOT = (
    paths.REPO_ROOT
    / "engine_adapters"
    / "browser_serving"
    / "examples"
)
BROWSER_PLAY_EXAMPLE_PATH = (
    BROWSER_PLAY_EXAMPLE_ROOT
    / "BrowserPlayExample"
)
_EXAMPLE_ROLES = (
    "mechanic_example",
    "ui_example",
    "browser_play_example",
)
_ENGINE_DEFINITIONS = (
    {
        "engine_id": "ue5",
        "enabled": True,
        "aliases": {
            "ue",
            "unreal",
            "unreal_engine",
            "unrealengine",
        },
        "primary_api": "ue5_api.md",
        "mechanic_example_roots": (
            "engine_adapters/ue5/examples",
        ),
        "ui_example_roots": (
            "engine_adapters/ue5/examples",
        ),
        "browser_backend_example_roots": (
            "engine_adapters/browser_serving/backends",
        ),
        "browser_backend_example_paths": (
            (
                "engine_adapters/browser_serving/backends/"
                "ue5_example.py"
            ),
        ),
        "browser_backend_entry_point": (
            "engine_adapters.browser_serving.backends:"
            "create_ue5_example_backend"
        ),
        "browser_backend_capabilities": (
            "asset_upload",
            "asset_import",
            "asset_inspection",
            "world_build",
            "world_catalog",
            "runtime_sessions",
            "skeletal_animation",
            "streaming",
            "pixel_streaming",
            "preview_camera",
        ),
    },
    {
        "engine_id": "unity3d",
        "enabled": True,
        "aliases": {"unity"},
        "primary_api": "unity3d_api.md",
        "mechanic_example_roots": (
            "engine_adapters/unity3d/examples",
        ),
        "ui_example_roots": (
            "engine_adapters/unity3d/examples",
        ),
        "browser_backend_example_roots": (
            "engine_adapters/browser_serving/backends",
        ),
        "browser_backend_example_paths": (
            "engine_adapters/browser_serving/backends/"
            "unity3d_example.py",
        ),
        "browser_backend_entry_point": (
            "engine_adapters.browser_serving.backends:"
            "create_unity3d_example_backend"
        ),
        "browser_backend_capabilities": (
            "asset_upload",
            "asset_import",
            "asset_inspection",
            "world_build",
            "world_catalog",
            "runtime_sessions",
            "skeletal_animation",
            "streaming",
            "webgl_streaming",
            "preview_camera",
        ),
    },
    {
        "engine_id": "blender",
        "enabled": False,
        "aliases": set(),
    },
    {
        "engine_id": "godot",
        "enabled": True,
        "aliases": {"godot4", "godot_engine"},
        "primary_api": "godot_api.md",
        "mechanic_example_roots": (
            "engine_adapters/godot/examples",
        ),
        "ui_example_roots": (
            "engine_adapters/godot/examples",
        ),
    },
    {
        "engine_id": "three_js",
        "enabled": False,
        "aliases": {"three", "threejs"},
    },
)


def mapping_list(
    value: Any,
    name: str,
) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(
        value,
        Sequence,
    ):
        raise TypeError(f"{name} must be a sequence")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise TypeError(
                f"{name}[{index}] must be an object"
            )
        result.append(dict(item))
    return result


def string_list(
    value: Any,
    name: str,
) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(
        value,
        Sequence,
    ):
        raise TypeError(f"{name} must be a sequence")
    return [
        str(item).strip()
        for item in value
        if str(item).strip()
    ]


def repair_payload(
    task: Mapping[str, Any],
    mode: str,
) -> tuple[dict[str, Any], int]:
    if mode == "generate":
        return {}, 0
    raw_repair = task.get("repair")
    if not isinstance(raw_repair, Mapping):
        raise TypeError(
            "repair must be an object in repair mode"
        )
    repair = dict(raw_repair)
    attempt = int(repair.get("attempt") or 0)
    max_attempts = int(repair.get("max_attempts") or 0)
    if attempt <= 0 or max_attempts <= 0:
        raise ValueError(
            "repair attempt and max_attempts must be positive"
        )
    if attempt > max_attempts:
        raise ValueError(
            "repair attempt must not exceed max_attempts"
        )
    failures = mapping_list(
        repair.get("failures", []),
        "repair.failures",
    )
    if not failures:
        raise ValueError(
            "repair.failures must not be empty"
        )
    previous = repair.get("previous_result")
    if not isinstance(previous, Mapping):
        raise TypeError(
            "repair.previous_result must be an object"
        )
    repair["attempt"] = attempt
    repair["max_attempts"] = max_attempts
    repair["failures"] = failures
    repair["previous_result"] = dict(previous)
    return repair, attempt


def validate_boundaries(
    workspace: Path,
    read_only_paths: Sequence[Path],
) -> None:
    for reference in read_only_paths:
        resolved = reference.resolve(strict=False)
        if (
            is_relative_to(workspace, resolved)
            or is_relative_to(resolved, workspace)
        ):
            raise ValueError(
                "workspace and read-only paths must not overlap: "
                f"{resolved}"
            )


def _normalized_engine_token(value: Any) -> str:
    normalized = (
        str(value or "")
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    if not normalized or not re.fullmatch(
        r"[a-z0-9_]+",
        normalized,
    ):
        raise ValueError(
            f"Engine identifier is invalid: {value!r}"
        )
    return normalized


def _repo_owned_path(
    value: Any,
    name: str,
    *,
    require_file: bool = False,
    require_dir: bool = False,
) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{name} must not be empty")
    resolved = resolve_repo_path(
        raw,
        name,
        must_exist=True,
        require_file=require_file,
    )
    repo_root = paths.REPO_ROOT.resolve(strict=False)
    if not is_relative_to(resolved, repo_root):
        raise ValueError(
            f"{name} resolves outside the repository: {resolved}"
        )
    if require_dir and not resolved.is_dir():
        raise FileNotFoundError(
            f"{name} must be a directory: {resolved}"
        )
    return resolved


def _definition_paths(
    entry: Mapping[str, Any],
    field: str,
    *,
    require_file: bool = False,
    require_dir: bool = False,
) -> list[Path]:
    return [
        _repo_owned_path(
            value,
            f"Engine definition {field}",
            require_file=require_file,
            require_dir=require_dir,
        )
        for value in entry.get(field, ())
    ]


def resolve_engine_registration(
    engine: Any,
) -> dict[str, Any]:
    requested = _normalized_engine_token(engine)
    matching: dict[str, Any] | None = None
    for entry in _ENGINE_DEFINITIONS:
        engine_id = _normalized_engine_token(
            entry.get("engine_id")
        )
        aliases = {
            _normalized_engine_token(alias)
            for alias in entry.get("aliases", set())
        }
        if requested == engine_id or requested in aliases:
            matching = dict(entry)
            break
    if matching is None:
        raise ValueError(
            f"Engine is not registered: {engine!r}"
        )
    engine_id = _normalized_engine_token(
        matching.get("engine_id")
    )
    if not bool(matching.get("enabled", False)):
        raise ValueError(
            f"Engine is registered but disabled: {engine_id}"
        )
    context_root = _repo_owned_path(
        ENGINE_CONTEXT_ROOT,
        "Engine Context directory",
        require_dir=True,
    )
    primary_api_relative = str(
        matching.get("primary_api") or ""
    ).strip()
    primary_api = (
        context_root / primary_api_relative
    ).resolve(strict=False)
    if (
        not primary_api_relative
        or Path(primary_api_relative).is_absolute()
        or ".." in Path(primary_api_relative).parts
        or not is_relative_to(primary_api, context_root)
        or not primary_api.is_file()
    ):
        raise FileNotFoundError(
            f"{engine_id}.primary_api is invalid: "
            f"{primary_api_relative!r}"
        )
    if not primary_api.read_text(
        encoding="utf-8"
    ).strip():
        raise ValueError(
            f"{engine_id}.primary_api is empty: {primary_api}"
        )

    browser_root = context_root
    browser_api_relative = "browser_serving_api.md"
    browser_api = (
        browser_root / browser_api_relative
    ).resolve(strict=False)
    if (
        not browser_api_relative
        or Path(browser_api_relative).is_absolute()
        or ".." in Path(browser_api_relative).parts
        or not is_relative_to(browser_api, browser_root)
        or not browser_api.is_file()
    ):
        raise FileNotFoundError(
            "browser_serving.primary_api is invalid: "
            f"{browser_api_relative!r}"
        )
    if not browser_api.read_text(
        encoding="utf-8"
    ).strip():
        raise ValueError(
            "browser_serving.primary_api is empty: "
            f"{browser_api}"
        )

    resolved: dict[str, Any] = {
        **matching,
        "engine_id": engine_id,
        "context_root": context_root,
        "primary_api": primary_api,
        "browser_serving_context_root": browser_root,
        "browser_serving_primary_api": browser_api,
    }
    for role in _EXAMPLE_ROLES:
        if role == "browser_play_example":
            roots = [
                _repo_owned_path(
                    BROWSER_PLAY_EXAMPLE_ROOT,
                    "Browser Play Example root",
                    require_dir=True,
                )
            ]
            defaults = [
                _repo_owned_path(
                    BROWSER_PLAY_EXAMPLE_PATH,
                    "Browser Play Example",
                    require_dir=True,
                )
            ]
        else:
            roots = _definition_paths(
                matching,
                f"{role}_roots",
                require_dir=True,
            )
            defaults = _definition_paths(
                matching,
                f"{role}_paths",
            )
        if not roots:
            raise ValueError(
                f"{engine_id}.{role}_roots must not be empty"
            )
        for path in defaults:
            if not any(
                is_relative_to(path, root)
                for root in roots
            ):
                raise ValueError(
                    f"{engine_id}.{role}_paths entry is "
                    f"outside registered roots: {path}"
                )
        if role == "browser_play_example" and not defaults:
            raise ValueError(
                f"{engine_id}.{role}_paths must not be empty"
            )
        resolved[f"{role}_roots"] = roots
        resolved[f"{role}_paths"] = defaults
    return resolved


def normalize_engine_id(
    engine: Any,
) -> str:
    return str(
        resolve_engine_registration(engine)["engine_id"]
    )


def resolve_browser_backend_registration(
    engine: Any,
) -> dict[str, Any]:
    registration = resolve_engine_registration(engine)
    entry_point = str(
        registration.get(
            "browser_backend_entry_point"
        )
        or ""
    ).strip()
    module_name, separator, attribute_name = (
        entry_point.partition(":")
    )
    if (
        not separator
        or not module_name
        or not attribute_name
    ):
        raise ValueError(
            "Browser Serving backend is not registered for "
            f"Engine {registration['engine_id']!r}"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ValueError(
            "Browser Serving backend module cannot be loaded "
            f"for Engine {registration['engine_id']!r}: "
            f"{module_name}"
        ) from exc
    factory = getattr(module, attribute_name, None)
    if not callable(factory):
        raise ValueError(
            "Browser Serving backend factory is not callable "
            f"for Engine {registration['engine_id']!r}: "
            f"{entry_point}"
        )
    capabilities = [
        str(value)
        for value in registration.get(
            "browser_backend_capabilities",
            (),
        )
        if str(value)
    ]
    if not capabilities:
        raise ValueError(
            "Browser Serving backend capabilities are not "
            f"declared for Engine {registration['engine_id']!r}"
        )
    return {
        "engine": registration["engine_id"],
        "registered": True,
        "entry_point": entry_point,
        "capabilities": capabilities,
    }


def resolve_browser_play_registration(
    engine: Any,
) -> dict[str, Any]:
    backend = resolve_browser_backend_registration(engine)
    frontend_root = _repo_owned_path(
        BROWSER_PLAYER_ROOT,
        "Browser Serving player frontend",
        require_dir=True,
    )
    required_files = (
        "viewer.html",
        "viewer.js",
        "viewer.css",
    )
    for filename in required_files:
        path = (frontend_root / filename).resolve(
            strict=False
        )
        if (
            not is_relative_to(path, frontend_root)
            or not path.is_file()
        ):
            raise FileNotFoundError(
                "Browser Serving player frontend is "
                f"missing {filename}: {path}"
            )
    return {
        "owner": "framework",
        "backend": backend,
        "frontend_root": str(frontend_root),
        "entry_point": "viewer.html",
        "frontend_files": list(required_files),
        "session_stream_url_field": "stream_url",
        "legacy_session_stream_url_fields": [
            "pixel_streaming_url",
        ],
    }


def _raw_path_values(
    value: Any,
    name: str,
) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, (str, Path, Mapping)):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    raise TypeError(f"{name} must be a sequence")


def _legacy_examples_for_role(
    task: Mapping[str, Any],
    role: str,
) -> list[Any]:
    values = _raw_path_values(
        task.get("example_paths") or task.get("examples"),
        "example_paths",
    )
    result: list[Any] = []
    for item in values:
        raw = (
            item.get("path")
            if isinstance(item, Mapping)
            else item
        )
        name = Path(str(raw or "")).name.lower()
        if (
            role == "mechanic_example"
            and "ui" not in name
            and "browser" not in name
        ):
            result.append(item)
        elif (
            role == "ui_example"
            and "ui" in name
            and "browser" not in name
        ):
            result.append(item)
        elif (
            role == "browser_play_example"
            and "browser" in name
        ):
            result.append(item)
        elif (
            role == "browser_backend_example"
            and name.endswith("_example.py")
        ):
            result.append(item)
    return result


def resolve_engine_example_paths(
    task: Mapping[str, Any],
    registration: Mapping[str, Any],
    role: str,
) -> list[Path]:
    if role not in _EXAMPLE_ROLES:
        raise ValueError(f"Unknown Example role: {role}")
    field = f"{role}_paths"
    raw_values = _raw_path_values(
        task.get(field),
        field,
    )
    if not raw_values:
        raw_values = _legacy_examples_for_role(task, role)
    if not raw_values:
        return [
            Path(path).resolve(strict=False)
            for path in registration[field]
        ]
    roots = [
        Path(path).resolve(strict=False)
        for path in registration[f"{role}_roots"]
    ]
    result: list[Path] = []
    for index, item in enumerate(raw_values):
        raw_path = (
            item.get("path")
            if isinstance(item, Mapping)
            else item
        )
        raw_text = str(raw_path or "").strip()
        if ".." in Path(raw_text).parts:
            raise ValueError(
                f"{field}[{index}] must not contain '..'"
            )
        path = resolve_repo_path(
            raw_text,
            f"{field}[{index}]",
            must_exist=True,
        )
        if not any(
            is_relative_to(path, root)
            for root in roots
        ):
            raise ValueError(
                f"{field}[{index}] is outside the selected "
                f"Engine roots: {path}"
            )
        result.append(path)
    return list(dict.fromkeys(result))


def resolve_engine_example_roots(
    registration: Mapping[str, Any],
    role: str,
) -> list[Path]:
    if role not in _EXAMPLE_ROLES:
        raise ValueError(f"Unknown Example role: {role}")
    return [
        Path(path).expanduser().resolve(strict=False)
        for path in registration[f"{role}_roots"]
    ]


def provenance_digests(
    paths_to_digest: Sequence[str | Path],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in paths_to_digest:
        path = Path(value).expanduser().resolve(strict=False)
        result[str(path)] = path_digest(path)
    return result


def _usage_path(
    value: Any,
    name: str,
) -> Path:
    raw = str(value or "").strip()
    if not raw or ".." in Path(raw).parts:
        raise ValueError(
            f"{name} must be a non-traversing path"
        )
    path = resolve_repo_path(
        raw,
        name,
        must_exist=True,
    )
    if not is_relative_to(
        path,
        paths.REPO_ROOT.resolve(strict=False),
    ):
        raise ValueError(
            f"{name} resolves outside the repository: {path}"
        )
    return path


def validate_context_used(
    usage_path: str | Path,
    *,
    engine: str,
    context_expectations: Sequence[Mapping[str, Any]],
    example_expectations: Mapping[
        str,
        Sequence[str | Path],
    ],
    expected_digests: Mapping[
        str,
        Mapping[str, Any],
    ],
    example_roots: Mapping[
        str,
        Sequence[str | Path],
    ] | None = None,
    required_example_roles: Sequence[str] = (),
    allowed_example_purposes: Sequence[str] = (
        EXAMPLE_REFERENCE_PURPOSES
    ),
    require_example_purpose: bool = False,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    try:
        usage = read_json(
            usage_path,
            "Context usage manifest",
        )
    except (FileNotFoundError, TypeError, ValueError) as exc:
        return False, [str(exc)]
    if usage.get("schema_version") != CONTEXT_USED_SCHEMA:
        errors.append(
            "Context usage schema_version must be "
            f"{CONTEXT_USED_SCHEMA!r}"
        )
    if str(usage.get("engine") or "") != engine:
        errors.append(
            "Context usage engine must match the canonical "
            f"Engine {engine!r}"
        )
    try:
        context_entries = mapping_list(
            usage.get("context_used", []),
            "context_used",
        )
        example_entries = mapping_list(
            usage.get("examples_used", []),
            "examples_used",
        )
    except TypeError as exc:
        return False, [*errors, str(exc)]

    expected_contexts = {
        (
            str(item.get("stage") or ""),
            str(item.get("role") or ""),
        ): Path(
            str(item.get("path") or "")
        ).resolve(strict=False)
        for item in context_expectations
    }
    actual_contexts: dict[tuple[str, str], Path] = {}
    for index, item in enumerate(context_entries):
        key = (
            str(item.get("stage") or "").strip(),
            str(item.get("role") or "").strip(),
        )
        if key in actual_contexts:
            errors.append(
                "Context usage duplicates stage/role: "
                f"{key[0]}/{key[1]}"
            )
            continue
        try:
            actual_contexts[key] = _usage_path(
                item.get("path"),
                f"context_used[{index}].path",
            )
        except (
            FileNotFoundError,
            TypeError,
            ValueError,
        ) as exc:
            errors.append(str(exc))
    if set(actual_contexts) != set(expected_contexts):
        errors.append(
            "Context usage must record exactly the required "
            "stage/role entries"
        )
    for key, expected_path in expected_contexts.items():
        if actual_contexts.get(key) != expected_path:
            errors.append(
                "Context usage path does not match the "
                f"registered API for {key[0]}/{key[1]}"
            )

    expected_examples = {
        role: {
            Path(path).expanduser().resolve(strict=False)
            for path in values
        }
        for role, values in example_expectations.items()
    }
    allowed_roots = {
        role: {
            Path(path).expanduser().resolve(strict=False)
            for path in values
        }
        for role, values in (
            example_roots or {}
        ).items()
    }
    allowed_example_roles = (
        set(expected_examples) | set(allowed_roots)
    )
    required_root_roles = {
        str(role).strip()
        for role in required_example_roles
        if str(role).strip()
    }
    unknown_required_roles = (
        required_root_roles - allowed_example_roles
    )
    if unknown_required_roles:
        errors.append(
            "Context usage requires unregistered Example roles: "
            + ", ".join(sorted(unknown_required_roles))
        )
    allowed_purposes = {
        str(purpose).strip()
        for purpose in allowed_example_purposes
        if str(purpose).strip()
    }
    actual_examples: dict[str, set[Path]] = {
        role: set()
        for role in allowed_example_roles
    }
    for index, item in enumerate(example_entries):
        role = str(item.get("role") or "").strip()
        if role not in allowed_example_roles:
            errors.append(
                "Context usage contains an unexpected "
                f"Example role: {role!r}"
            )
            continue
        raw_purpose = item.get("purpose")
        if isinstance(raw_purpose, str):
            purpose_values = [raw_purpose]
        elif (
            isinstance(raw_purpose, Sequence)
            and not isinstance(raw_purpose, (str, bytes))
        ):
            purpose_values = list(raw_purpose)
        elif raw_purpose in (None, ""):
            purpose_values = []
        else:
            errors.append(
                f"examples_used[{index}].purpose must be a "
                "string or sequence"
            )
            purpose_values = []
        purposes = {
            str(purpose).strip()
            for purpose in purpose_values
            if str(purpose).strip()
        }
        if require_example_purpose and not purposes:
            errors.append(
                f"examples_used[{index}].purpose must record at "
                "least one engineering reference purpose"
            )
        unknown_purposes = purposes - allowed_purposes
        if unknown_purposes:
            errors.append(
                f"examples_used[{index}].purpose contains "
                "unsupported values: "
                + ", ".join(sorted(unknown_purposes))
            )
        try:
            path = _usage_path(
                item.get("path"),
                f"examples_used[{index}].path",
            )
        except (
            FileNotFoundError,
            TypeError,
            ValueError,
        ) as exc:
            errors.append(str(exc))
            continue
        if path in actual_examples[role]:
            errors.append(
                "Context usage duplicates Example path: "
                f"{path}"
            )
        actual_examples[role].add(path)
    for role, expected_paths in expected_examples.items():
        if actual_examples[role] != expected_paths:
            errors.append(
                "Context usage must record every required "
                f"{role} path and no others"
            )
    for role, roots in allowed_roots.items():
        for path in actual_examples[role]:
            if not any(
                is_relative_to(path, root)
                for root in roots
            ):
                errors.append(
                    "Context usage Example path is outside the "
                    f"allowed {role} roots: {path}"
                )
    for role in required_root_roles:
        roots = allowed_roots.get(role, set())
        consulted_subpath = any(
            path != root and is_relative_to(path, root)
            for path in actual_examples.get(role, set())
            for root in roots
        )
        if not consulted_subpath:
            errors.append(
                "Context usage must record at least one actually "
                f"consulted {role} path below its allowed root"
            )

    for raw_path, expected in expected_digests.items():
        path = Path(raw_path).expanduser().resolve(
            strict=False
        )
        try:
            current = path_digest(path)
        except (FileNotFoundError, ValueError) as exc:
            errors.append(str(exc))
            continue
        if current != dict(expected):
            errors.append(
                "Read-only Context or Example changed after "
                f"prepare: {path}"
            )
    return not errors, errors


def select_task(
    tasks_path: str | Path,
    *,
    game_id: str | None,
    task_id: str | None,
    task_name: str,
) -> dict[str, Any]:
    matches = [
        dict(task)
        for task, _ in paths.iter_tasks(
            tasks_path,
            game_filter=game_id,
        )
        if not task_id
        or str(task.get("task_id") or "") == task_id
    ]
    if not matches:
        raise ValueError(
            f"No matching {task_name} task was found"
        )
    if len(matches) > 1:
        raise ValueError(
            f"{task_name} prepare handles one task at a time; "
            "pass --task-id"
        )
    return matches[0]
