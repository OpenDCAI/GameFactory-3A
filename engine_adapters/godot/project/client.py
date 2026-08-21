"""Stable project operations for GodotClient v1."""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .._internal import (
    GodotTransport,
    find_godot_binary,
    godot_4_version_error,
    inspect_godot_resource,
    parse_godot_version,
)
from ..config import GodotClientConfig, normalize_godot_project_directory
from ..contracts import GodotDiagnostic, GodotOperationResult

SUPPORTED_RENDERERS = ("forward_plus", "mobile", "gl_compatibility")
PROJECT_DIRECTORIES = (
    "addons",
    "assets/imported/avatars",
    "assets/imported/motions",
    "assets/imported/scenes",
    "assets/imported/environments",
    "assets/imported/effects",
    "assets/imported/materials",
    "assets/imported/textures",
    "assets/imported/props",
    "assets/imported/weapons",
    "assets/imported/audio",
    "tests",
    "builds",
    ".a3game/worlds/drafts",
    ".a3game/worlds/packages",
)
RESOURCE_UID_PATTERN = re.compile(r"^uid://[a-z0-9]+$")
TEXT_RESOURCE_UID_PATTERN = re.compile(
    r'^\s*\[gd_scene\b[^\]\r\n]*\buid\s*=\s*"([^"]+)"',
    re.MULTILINE,
)
TEXT_SCENE_HEADER_PATTERN = re.compile(r"^\[gd_scene\b[^\]\r\n]*\]$")


def _without_config_comment(line: str, *, markers: str = ";#") -> str:
    """Remove a Godot config comment without touching quoted string content."""

    in_string = False
    escaped = False
    for index, character in enumerate(line):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in markers:
            return line[:index].rstrip()
    return line.rstrip()


def _setting(text: str, section: str, key: str) -> str:
    current = ""
    setting = ""
    for raw_line in text.splitlines():
        # Godot accepts semicolon comments after section tags. Hash comments are
        # accepted after assigned values, but not after a section tag.
        section_line = _without_config_comment(raw_line, markers=";").strip()
        if section_line.startswith("[") and section_line.endswith("]"):
            current = section_line[1:-1].strip()
            continue
        line = _without_config_comment(raw_line).strip()
        if current != section or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        value = value.strip()
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            decoded = value.strip('"')
        # Godot applies the last definition when a project setting is repeated,
        # including when the same section appears more than once.
        setting = str(decoded)
    return setting


def _project_resource_file(project_dir: Path, resource_path: str) -> Path:
    raw = str(resource_path or "").strip().replace("\\", "/")
    if not raw.startswith("res://"):
        raise ValueError(f"Godot resource path must use res://: {resource_path}")
    relative = PurePosixPath(raw[len("res://") :])
    if (
        not relative.parts
        or relative.is_absolute()
        or "." in relative.parts
        or ".." in relative.parts
    ):
        raise ValueError(f"Godot resource path is unsafe: {resource_path}")
    root = project_dir.resolve()
    resolved = (root / Path(*relative.parts)).resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Godot resource path escaped the project: {resource_path}"
        ) from exc
    return resolved


def _static_uid_resources(project_dir: Path, resource_uid: str) -> list[str]:
    """Find project files that authoritatively declare one Godot resource UID."""

    root = project_dir.resolve()
    matches: set[str] = set()
    for path in root.rglob("*.tscn"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
                header = handle.read(4096)
        except OSError:
            continue
        declaration = TEXT_RESOURCE_UID_PATTERN.search(header)
        if declaration is not None and declaration.group(1) == resource_uid:
            matches.add("res://" + path.relative_to(root).as_posix())
    for sidecar in root.rglob("*.uid"):
        if sidecar.is_symlink() or not sidecar.is_file():
            continue
        try:
            declared_uid = sidecar.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        resource = sidecar.with_suffix("")
        if (
            declared_uid == resource_uid
            and resource.suffix.lower() in {".scn", ".tscn"}
            and not resource.is_symlink()
            and resource.is_file()
        ):
            matches.add("res://" + resource.relative_to(root).as_posix())
    return sorted(matches)


def _text_scene_header_error(path: Path) -> str:
    """Return an error when a text scene cannot be identified statically."""

    if path.suffix.lower() != ".tscn":
        return ""
    try:
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            header = handle.read(4096)
    except OSError as exc:
        return f"Configured main scene could not be read: {type(exc).__name__}: {exc}"
    first_content = next(
        (
            line.strip()
            for line in header.splitlines()
            if line.strip() and not line.strip().startswith(";")
        ),
        "",
    )
    if not TEXT_SCENE_HEADER_PATTERN.fullmatch(first_content):
        return f"Configured main scene has no [gd_scene] header: {path}"
    return ""


def _assert_directory_chain(path: Path) -> None:
    """Reject links and non-directories in an absolute directory path."""

    for component in reversed((path, *path.parents)):
        if component.is_symlink():
            raise ValueError(
                f"Godot project path must not contain a symlink: {component}"
            )
        if component.exists() and not component.is_dir():
            raise ValueError(
                f"Godot project path component must be a directory: {component}"
            )


def _validate_create_paths(raw_project_dir: Path, project_dir: Path) -> None:
    """Validate every create target before the operation writes anything."""

    _assert_directory_chain(raw_project_dir)
    resolved_root = project_dir.resolve(strict=False)
    managed: tuple[tuple[str, str], ...] = (
        ("project.godot", "file"),
        ("main.tscn", "file"),
        *((relative, "directory") for relative in PROJECT_DIRECTORIES),
    )
    for relative, expected_type in managed:
        target = raw_project_dir / relative
        current = raw_project_dir
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError(
                    f"Godot managed path must not contain a symlink: {current}"
                )
            if not current.exists():
                continue
            is_target = current == target
            if is_target and expected_type == "file":
                if not current.is_file():
                    raise ValueError(
                        f"Godot managed path must be a regular file: {current}"
                    )
            elif not current.is_dir():
                raise ValueError(
                    f"Godot managed path component must be a directory: {current}"
                )
        try:
            target.resolve(strict=False).relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(
                f"Godot managed path escaped the project: {target}"
            ) from exc


class GodotProjectClient:
    def __init__(self, config: GodotClientConfig) -> None:
        self._config = config
        self._transport = GodotTransport(config)

    def get_info(self, *, probe_version: bool = True) -> dict[str, Any]:
        project_dir = self._config.project_dir
        project_file = self._config.project_file
        binary = find_godot_binary(self._config.godot_executable)
        name = ""
        main_scene = ""
        if project_file is not None and project_file.is_file():
            text = project_file.read_text(encoding="utf-8", errors="replace")
            name = _setting(text, "application", "config/name")
            main_scene = _setting(text, "application", "run/main_scene")
        version = self._config.engine_version_hint
        version_probe: dict[str, Any] = {}
        if probe_version and binary is not None and binary.is_file():
            version = ""
            try:
                result = self._transport.version()
                version_probe = result.to_dict()
                if result.returncode == 0:
                    version = (
                        result.stdout.strip().splitlines()[0]
                        if result.stdout.strip()
                        else ""
                    )
            except Exception as exc:
                version_probe = {"error": f"{type(exc).__name__}: {exc}"}
        parsed_version = parse_godot_version(version)
        version_error = godot_4_version_error(version) if version else ""
        return GodotOperationResult.success(
            "project.get_info",
            payload={
                "api_version": self._config.api_version,
                "project_path": str(project_dir) if project_dir else "",
                "project_file": str(project_file) if project_file else "",
                "project_exists": bool(project_file and project_file.is_file()),
                "project_name": name,
                "main_scene": main_scene,
                "godot_executable": str(binary) if binary else "",
                "godot_executable_exists": bool(binary and binary.is_file()),
                "engine_version": version,
                "engine_version_major": (
                    parsed_version[0] if parsed_version is not None else None
                ),
                "engine_version_supported": bool(version and not version_error),
                "engine_version_error": version_error,
                "version_probe": version_probe,
                "runtime_host": self._config.runtime_host,
                "runtime_port": self._config.runtime_port,
            },
        ).to_dict()

    def create(
        self,
        project_path: str | Path | None = None,
        *,
        project_name: str = "",
        renderer: str = "gl_compatibility",
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        raw_project_dir = (
            normalize_godot_project_directory(project_path, resolve=False)
            if project_path is not None
            else self._config.project_dir_input
        )
        project_dir = (
            raw_project_dir.resolve(strict=False)
            if project_path is not None and raw_project_dir is not None
            else self._config.project_dir
        )
        if project_dir is None:
            return GodotOperationResult.failure(
                "project.create",
                "project_path is required",
            ).to_dict()
        normalized_renderer = str(renderer or "").strip().lower()
        if normalized_renderer not in set(SUPPORTED_RENDERERS):
            return GodotOperationResult.failure(
                "project.create",
                "renderer must be one of: forward_plus, mobile, gl_compatibility",
            ).to_dict()
        name = str(project_name or project_dir.name).strip()
        if not name or any(character in name for character in "\r\n\0"):
            return GodotOperationResult.failure(
                "project.create",
                "project_name must be a non-empty single-line value",
            ).to_dict()
        project_file = project_dir / "project.godot"
        main_scene = project_dir / "main.tscn"
        payload = {
            "project_path": str(project_dir),
            "project_file": str(project_file),
            "project_name": name,
            "renderer": normalized_renderer,
            "main_scene": str(main_scene),
            "overwrite": overwrite,
            "dry_run": dry_run,
        }
        try:
            _validate_create_paths(raw_project_dir or project_dir, project_dir)
        except (OSError, ValueError) as exc:
            return GodotOperationResult.failure(
                "project.create",
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        if project_file.exists() and not overwrite:
            return GodotOperationResult.failure(
                "project.create",
                f"Godot project already exists: {project_file}",
                payload=payload,
            ).to_dict()
        if dry_run:
            return GodotOperationResult.success(
                "project.create", payload=payload
            ).to_dict()
        try:
            project_dir.mkdir(parents=True, exist_ok=True)
            project_file.write_text(
                "\n".join(
                    [
                        "; Engine configuration file.",
                        "; Managed through GodotClient; generated gameplay lives in addons/.",
                        "config_version=5",
                        "",
                        "[application]",
                        f"config/name={json.dumps(name, ensure_ascii=False)}",
                        'run/main_scene="res://main.tscn"',
                        "",
                        "[display]",
                        "window/size/viewport_width=1280",
                        "window/size/viewport_height=720",
                        "window/stretch/mode=" + json.dumps("canvas_items"),
                        "",
                        "[rendering]",
                        "renderer/rendering_method=" + json.dumps(normalized_renderer),
                        "renderer/rendering_method.mobile="
                        + json.dumps(normalized_renderer),
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            if not main_scene.exists() or overwrite:
                main_scene.write_text(
                    '[gd_scene format=3]\n\n[node name="Main" type="Node3D"]\n',
                    encoding="utf-8",
                )
            for relative in PROJECT_DIRECTORIES:
                (project_dir / relative).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return GodotOperationResult.failure(
                "project.create",
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        return GodotOperationResult.success(
            "project.create",
            artifacts=[
                {
                    "type": "godot_project",
                    "path": str(project_file),
                    "state": "ready",
                },
                {
                    "type": "godot_scene",
                    "path": str(main_scene),
                    "state": "ready",
                },
            ],
            payload=payload,
        ).to_dict()

    def validate(self, *, check_engine: bool = True) -> dict[str, Any]:
        project_dir = self._config.project_dir
        project_file = self._config.project_file
        diagnostics: list[GodotDiagnostic] = []
        errors: list[str] = []
        main_scene = ""
        main_scene_resolved = ""
        main_scene_resolution = ""
        unresolved_uid = ""
        main_scene_load_reference = ""
        main_scene_load_process: dict[str, Any] = {}
        main_scene_load_inspection: dict[str, Any] = {}
        if project_dir is None or project_file is None or not project_file.is_file():
            errors.append("project_path does not resolve to project.godot")
            text = ""
        else:
            text = project_file.read_text(encoding="utf-8", errors="replace")
            if not re.search(r"^\s*config_version\s*=\s*\d+", text, re.MULTILINE):
                errors.append("project.godot has no config_version")
            main_scene = _setting(text, "application", "run/main_scene")
            if main_scene.startswith("res://"):
                try:
                    scene_path = _project_resource_file(project_dir, main_scene)
                except ValueError as exc:
                    errors.append(str(exc))
                else:
                    main_scene_resolved = main_scene
                    main_scene_resolution = "resource_path"
                    if not scene_path.is_file():
                        errors.append(
                            f"Configured main scene was not found: {scene_path}"
                        )
                    else:
                        header_error = _text_scene_header_error(scene_path)
                        if header_error:
                            errors.append(header_error)
            elif main_scene.startswith("uid://"):
                if not RESOURCE_UID_PATTERN.fullmatch(main_scene):
                    errors.append(
                        f"application/run/main_scene has an invalid Godot UID: {main_scene}"
                    )
                else:
                    static_matches = _static_uid_resources(project_dir, main_scene)
                    if len(static_matches) == 1:
                        main_scene_resolved = static_matches[0]
                        main_scene_resolution = "project_resource_uid"
                        try:
                            scene_path = _project_resource_file(
                                project_dir, main_scene_resolved
                            )
                        except ValueError as exc:
                            errors.append(str(exc))
                        else:
                            header_error = _text_scene_header_error(scene_path)
                            if header_error:
                                errors.append(header_error)
                    elif len(static_matches) > 1:
                        errors.append(
                            "application/run/main_scene UID is declared by multiple "
                            f"resources: {', '.join(static_matches)}"
                        )
                    else:
                        unresolved_uid = main_scene
            elif main_scene:
                errors.append(
                    "application/run/main_scene must use a res:// path or uid:// identifier"
                )
            else:
                diagnostics.append(
                    GodotDiagnostic(
                        severity="warning",
                        code="GODOT_MAIN_SCENE_UNSET",
                        message="No application/run/main_scene is configured",
                        source="project.godot",
                    )
                )
        binary = find_godot_binary(self._config.godot_executable)
        version = ""
        engine_supported = False
        if check_engine:
            if binary is None or not binary.is_file():
                errors.append(
                    "Godot editor binary was not found; set A3GAME_GODOT_EXECUTABLE"
                )
            else:
                try:
                    result = self._transport.version()
                    if result.returncode != 0:
                        errors.append(
                            "Godot --version failed: "
                            + (result.stderr.strip() or f"exit {result.returncode}")
                        )
                    else:
                        version = (
                            result.stdout.strip().splitlines()[0]
                            if result.stdout.strip()
                            else ""
                        )
                        version_error = godot_4_version_error(version)
                        if version_error:
                            errors.append(version_error)
                        else:
                            engine_supported = True
                except Exception as exc:
                    errors.append(f"Godot version probe failed: {exc}")
        if unresolved_uid and not check_engine:
            errors.append(
                "Configured main scene UID was not found in project resource "
                f"declarations while engine checks are disabled: {unresolved_uid}"
            )
        elif unresolved_uid and not engine_supported:
            errors.append(
                f"Configured main scene UID could not be resolved: {unresolved_uid}"
            )

        if check_engine and engine_supported:
            if main_scene.startswith("uid://") and (
                unresolved_uid or main_scene_resolved
            ):
                # Loading the statically matched res:// path only proves that the
                # scene file is valid. Godot must resolve the configured UID itself
                # for project startup to work (the UID cache may still be missing).
                main_scene_load_reference = main_scene
            elif main_scene_resolved:
                main_scene_load_reference = main_scene_resolved
        if main_scene_load_reference:
            try:
                result, inspection = inspect_godot_resource(
                    self._transport,
                    main_scene_load_reference,
                    timeout=float(self._config.editor_timeout),
                )
                main_scene_load_process = result.to_dict()
                main_scene_load_inspection = inspection
                if (
                    result.returncode != 0
                    or not inspection.get("ok")
                    or not inspection.get("is_packed_scene")
                    or not inspection.get("instantiable")
                ):
                    details = str(
                        inspection.get("error")
                        or "resource did not load as an instantiable PackedScene"
                    )
                    errors.append(
                        "Configured main scene could not be loaded as a PackedScene: "
                        f"{main_scene_load_reference}: {details}"
                    )
                elif unresolved_uid:
                    main_scene_resolved = unresolved_uid
                    main_scene_resolution = "godot_resource_load"
            except Exception as exc:
                errors.append(
                    "Configured main scene native load failed: "
                    f"{type(exc).__name__}: {exc}"
                )
        return GodotOperationResult(
            operation="project.validate",
            ok=not errors,
            diagnostics=tuple(diagnostics),
            errors=tuple(errors),
            payload={
                "project_path": str(project_dir) if project_dir else "",
                "project_file": str(project_file) if project_file else "",
                "godot_executable": str(binary) if binary else "",
                "engine_version": version,
                "engine_checked": check_engine,
                "main_scene": main_scene,
                "main_scene_resolved": main_scene_resolved,
                "main_scene_resolution": main_scene_resolution,
                "main_scene_load_reference": main_scene_load_reference,
                "main_scene_load_process": main_scene_load_process,
                "main_scene_load_inspection": main_scene_load_inspection,
                "main_scene_uid_process": (
                    main_scene_load_process if main_scene.startswith("uid://") else {}
                ),
                "main_scene_uid_inspection": (
                    main_scene_load_inspection
                    if main_scene.startswith("uid://")
                    else {}
                ),
            },
        ).to_dict()
