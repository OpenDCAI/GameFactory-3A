"""Stable Godot add-on installation operations for GodotClient v1."""

from __future__ import annotations

import json
import re
import shutil
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from .._internal import (
    GeneratedAssetSourceResolver,
    GodotTransport,
    source_descriptor,
    validate_regular_directory_tree,
)
from ..config import GodotClientConfig
from ..contracts import GodotOperationResult

FRAMEWORK_ROOT = Path(__file__).resolve().parent / "A3GamePlayable"
FRAMEWORK_INSTALL_DIR = "a3game_playable"
FRAMEWORK_AUTOLOAD_NAME = "A3GameRuntime"
FRAMEWORK_AUTOLOAD_PATH = "res://addons/a3game_playable/runtime.gd"
PLUGIN_VALIDATOR_SCRIPT = (
    Path(__file__).resolve().parents[1] / "_scripts" / "validate_plugin.gd"
)
PLUGIN_VALIDATION_SCHEMA = "gamefactory3a.godot.plugin_validation.v1"


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value or "")).strip("._")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("Godot add-on name is invalid")
    return cleaned


def _validate_tree(source: Path) -> None:
    validate_regular_directory_tree(source, label="Godot add-on source")


def _addons_directory(project_dir: Path) -> Path:
    root = project_dir.resolve(strict=True)
    if not root.is_dir():
        raise NotADirectoryError(f"Godot project root is not a directory: {root}")
    addons = root / "addons"
    if addons.is_symlink():
        raise ValueError(f"Godot add-ons directory must not be a symlink: {addons}")
    if addons.exists() and not addons.is_dir():
        raise NotADirectoryError(f"Godot add-ons path is not a directory: {addons}")
    resolved = addons.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Godot add-ons directory escaped the project root: {addons}"
        ) from exc
    return addons


def _install_target(project_dir: Path, install_dir: str) -> Path:
    root = project_dir.resolve(strict=True)
    addons = _addons_directory(root)
    target = addons / install_dir
    if target.is_symlink():
        raise ValueError(f"Godot add-on target must not be a symlink: {target}")
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Godot add-on target escaped the project root: {target}"
        ) from exc
    return target


def _copied_files(target: Path) -> list[str]:
    return [
        item.relative_to(target).as_posix()
        for item in sorted(target.rglob("*"))
        if item.is_file()
    ]


_SECTION_HEADER_PATTERN = re.compile(
    r"(?m)^[ \t]*\[(?P<name>[^\]\r\n]+)\][ \t]*(?:;[^\r\n]*)?(?:\r?\n|\Z)"
)
_EDITOR_PLUGIN_ENTRY_PATTERN = re.compile(
    r"(?m)^(?P<prefix>[ \t]*enabled[ \t]*=[ \t]*)"
    r"(?P<value>[^\r\n]*)(?P<newline>\r?\n|\Z)"
)
_PACKED_STRING_ARRAY_PATTERN = re.compile(r"PackedStringArray\((?P<arguments>.*?)\)")
_PLUGIN_REQUIRED_KEYS = ("name", "author", "version", "description", "script")


def _split_assignment_comment(value: str) -> tuple[str, str]:
    """Split a Godot setting value from an unquoted trailing comment."""

    in_string = False
    escaped = False
    for index, character in enumerate(value):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
        elif character in ";#":
            comment_start = index
            while comment_start > 0 and value[comment_start - 1] in " \t":
                comment_start -= 1
            return value[:comment_start].rstrip(), value[comment_start:]
    return value.rstrip(), ""


def _plugin_entry_script(source: Path) -> tuple[str, Path, dict[str, str]]:
    """Validate ``plugin.cfg`` metadata and resolve its safe entry script."""

    descriptor = source / "plugin.cfg"
    text = descriptor.read_text(encoding="utf-8-sig")
    headers = list(_SECTION_HEADER_PATTERN.finditer(text))
    sections: list[tuple[int, int]] = []
    for index, header in enumerate(headers):
        if header.group("name") != "plugin":
            continue
        sections.append(
            (
                header.end(),
                headers[index + 1].start() if index + 1 < len(headers) else len(text),
            )
        )
    if not sections:
        raise ValueError("Godot add-on plugin.cfg has no [plugin] section")
    if len(sections) != 1:
        raise ValueError("Godot add-on plugin.cfg declares [plugin] more than once")

    body_start, body_end = sections[0]
    metadata: dict[str, str] = {}
    for key in _PLUGIN_REQUIRED_KEYS:
        entry_pattern = re.compile(
            rf"(?m)^[ \t]*{re.escape(key)}[ \t]*=[ \t]*"
            r"(?P<value>[^\r\n]*)(?:\r?\n|\Z)"
        )
        entries = list(entry_pattern.finditer(text, body_start, body_end))
        if not entries:
            raise ValueError(
                f"Godot add-on plugin.cfg [plugin] section has no {key} entry"
            )
        if len(entries) != 1:
            raise ValueError(
                "Godot add-on plugin.cfg [plugin] section declares "
                f"{key} more than once"
            )
        raw_value, _comment = _split_assignment_comment(entries[0].group("value"))
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Godot add-on plugin.cfg {key} must be one quoted string"
            ) from exc
        if not isinstance(value, str):
            raise ValueError(f"Godot add-on plugin.cfg {key} must be a string")
        metadata[key] = value

    if not metadata["name"].strip():
        raise ValueError("Godot add-on plugin.cfg name must be non-empty")
    script_value = metadata["script"]
    if not script_value.strip():
        raise ValueError(
            "Godot add-on plugin.cfg script must be a non-empty string path"
        )
    if (
        "\\" in script_value
        or "\x00" in script_value
        or script_value.startswith("/")
        or re.match(r"^[A-Za-z]:", script_value)
        or "://" in script_value
    ):
        raise ValueError("Godot add-on plugin.cfg script must be a safe relative path")
    raw_parts = script_value.split("/")
    relative = PurePosixPath(script_value)
    if (
        relative.is_absolute()
        or not raw_parts
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise ValueError(
            "Godot add-on plugin.cfg script must be a non-traversing relative path"
        )

    root = source.resolve(strict=True)
    entry = (root / Path(*raw_parts)).resolve(strict=True)
    try:
        entry.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            "Godot add-on plugin.cfg script escaped the add-on source"
        ) from exc
    if not stat.S_ISREG(entry.lstat().st_mode):
        raise ValueError(
            "Godot add-on plugin.cfg script must resolve to a regular file"
        )
    return relative.as_posix(), entry, metadata


def _validate_plugin_with_godot(
    source: Path,
    install_dir: str,
    entry_script: str,
    config: GodotClientConfig,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load an add-on entry script with Godot in an isolated project."""

    if not PLUGIN_VALIDATOR_SCRIPT.is_file():
        raise FileNotFoundError(
            f"Godot plugin validator was not found: {PLUGIN_VALIDATOR_SCRIPT}"
        )
    with tempfile.TemporaryDirectory(
        prefix="a3game-godot-plugin-validate-"
    ) as temporary:
        validation_root = Path(temporary)
        staged = validation_root / "addons" / install_dir
        staged.parent.mkdir(parents=True)
        shutil.copytree(source, staged, symlinks=True)
        _validate_tree(staged)
        staged_entry, _staged_entry_file, _staged_metadata = _plugin_entry_script(
            staged
        )
        if staged_entry != entry_script:
            raise ValueError(
                "Godot add-on entry script changed during native validation"
            )
        (validation_root / "project.godot").write_text(
            "; Isolated GameFactory-3A add-on validation project.\n"
            "config_version=5\n\n"
            "[application]\n"
            'config/name="GameFactory-3A Plugin Validation"\n',
            encoding="utf-8",
        )
        report = validation_root / "plugin-validation.json"
        resource = f"res://addons/{install_dir}/{entry_script}"
        descriptor_resource = f"res://addons/{install_dir}/plugin.cfg"
        validation_config = replace(
            config,
            project_path=validation_root,
            project_path_input=validation_root,
        )
        process = GodotTransport(validation_config).run(
            ["--editor", "--script", str(PLUGIN_VALIDATOR_SCRIPT)],
            timeout=config.editor_timeout,
            environment={
                "A3GAME_GODOT_PLUGIN_RESOURCE": resource,
                "A3GAME_GODOT_PLUGIN_DESCRIPTOR": descriptor_resource,
                "A3GAME_GODOT_PLUGIN_REPORT": str(report),
            },
        )
        process_summary = {
            "returncode": process.returncode,
            "stdout_tail": process.stdout[-4000:],
            "stderr_tail": process.stderr[-4000:],
            "duration_seconds": process.duration_seconds,
        }
        if not report.is_file():
            details = process.stderr.strip() or process.stdout.strip()
            raise RuntimeError(
                "Godot plugin validation produced no report"
                + (f": {details[-4000:]}" if details else "")
            )
        try:
            validation = json.loads(report.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Godot plugin validation report is invalid JSON") from exc
        if not isinstance(validation, dict):
            raise ValueError("Godot plugin validation report must be an object")
        if validation.get("schema_version") != PLUGIN_VALIDATION_SCHEMA:
            raise ValueError("Godot plugin validation report has an unsupported schema")
        if validation.get("resource_path") != resource:
            raise ValueError(
                "Godot plugin validation report does not match the entry script"
            )
        if validation.get("descriptor_path") != descriptor_resource:
            raise ValueError("Godot plugin validation report does not match plugin.cfg")
        if validation.get("ok") is not True:
            detail = str(
                validation.get("error")
                or process.stderr.strip()
                or "Godot could not load the plugin entry script"
            )
            raise ValueError(f"Godot plugin entry validation failed: {detail}")
        if process.returncode != 0:
            raise RuntimeError(
                "Godot plugin validation reported success but exited with "
                f"status {process.returncode}"
            )
        if validation.get("base_type") != "EditorPlugin":
            raise ValueError("Godot plugin entry script must inherit EditorPlugin")
        if validation.get("can_instantiate") is not True:
            raise ValueError("Godot plugin entry script cannot be instantiated")
        if validation.get("is_tool") is not True:
            raise ValueError("Godot plugin entry script must run in tool mode")
        if validation.get("instantiated") is not True:
            raise ValueError(
                "Godot plugin entry script did not produce an EditorPlugin instance"
            )
        if validation.get("instance_class") != "EditorPlugin":
            raise ValueError(
                "Godot plugin entry script produced an unexpected instance type"
            )
        return process_summary, validation


def _editor_plugin_entries(
    text: str,
) -> tuple[
    list[tuple[int, int]],
    list[tuple[int, int, str, str, str, list[str]]],
]:
    headers = list(_SECTION_HEADER_PATTERN.finditer(text))
    sections: list[tuple[int, int]] = []
    entries: list[tuple[int, int, str, str, str, list[str]]] = []
    for index, header in enumerate(headers):
        if header.group("name") != "editor_plugins":
            continue
        body_start = header.end()
        body_end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        sections.append((body_start, body_end))
        for entry in _EDITOR_PLUGIN_ENTRY_PATTERN.finditer(text, body_start, body_end):
            raw_value, comment = _split_assignment_comment(entry.group("value"))
            packed = _PACKED_STRING_ARRAY_PATTERN.fullmatch(raw_value)
            if packed is None:
                raise ValueError(
                    "Godot editor_plugins/enabled must use a single-line "
                    "PackedStringArray(...) value"
                )
            arguments = packed.group("arguments").strip()
            if arguments.endswith(","):
                arguments = arguments[:-1].rstrip()
            try:
                values = json.loads(f"[{arguments}]") if arguments else []
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "Godot editor_plugins/enabled contains an invalid "
                    "PackedStringArray(...) value"
                ) from exc
            if not isinstance(values, list) or any(
                not isinstance(value, str) for value in values
            ):
                raise ValueError(
                    "Godot editor_plugins/enabled must contain only string paths"
                )
            entries.append(
                (
                    entry.start(),
                    entry.end(),
                    entry.group("prefix"),
                    comment,
                    entry.group("newline"),
                    values,
                )
            )
    return sections, entries


def _validate_plugin_update(
    text: str,
) -> tuple[
    list[tuple[int, int]],
    list[tuple[int, int, str, str, str, list[str]]],
]:
    sections, entries = _editor_plugin_entries(text)
    if len(sections) > 1:
        raise ValueError(
            "Godot [editor_plugins] is declared more than once; refusing an "
            "ambiguous plugin enablement without changing the project"
        )
    if len(entries) > 1:
        raise ValueError(
            "Godot editor_plugins/enabled is declared more than once; refusing "
            "an ambiguous plugin enablement without changing the project"
        )
    return sections, entries


def _enable_plugin(project_file: Path, install_dir: str) -> None:
    resource = f"res://addons/{install_dir}/plugin.cfg"
    text = project_file.read_text(encoding="utf-8")
    sections, entries = _validate_plugin_update(text)
    replacement = (
        "PackedStringArray("
        + ", ".join(
            json.dumps(item)
            for item in dict.fromkeys([*(entries[0][5] if entries else []), resource])
        )
        + ")"
    )
    if entries:
        start, end, prefix, comment, newline, _values = entries[0]
        updated = text[:start] + prefix + replacement + comment + newline + text[end:]
    elif sections:
        body_start, body_end = sections[0]
        body = text[body_start:body_end]
        newline = "\r\n" if "\r\n" in text else "\n"
        header_separator = "" if text[:body_start].endswith(("\n", "\r")) else newline
        updated_body = (
            header_separator
            + body
            + ("" if not body or body.endswith(("\n", "\r")) else newline)
            + "enabled="
            + replacement
            + newline
        )
        updated = text[:body_start] + updated_body + text[body_end:]
    else:
        newline = "\r\n" if "\r\n" in text else "\n"
        suffix = "" if not text or text.endswith(("\n", "\r")) else newline
        updated = (
            text
            + suffix
            + newline
            + "[editor_plugins]"
            + newline
            + "enabled="
            + replacement
            + newline
        )
    project_file.write_text(updated, encoding="utf-8")

    _sections, updated_entries = _validate_plugin_update(
        project_file.read_text(encoding="utf-8")
    )
    if len(updated_entries) != 1 or resource not in updated_entries[0][5]:
        raise RuntimeError(
            f"Godot did not retain the enabled editor plugin setting: {resource}"
        )


def _autoload_entries(
    text: str, name: str
) -> tuple[
    list[tuple[int, int]],
    list[tuple[int, int, str, str, str, Any]],
]:
    headers = list(_SECTION_HEADER_PATTERN.finditer(text))
    sections: list[tuple[int, int]] = []
    entry_pattern = re.compile(
        rf"(?m)^(?P<prefix>[ \t]*{re.escape(name)}[ \t]*=[ \t]*)"
        r"(?P<value>[^\r\n]*)(?P<newline>\r?\n|\Z)"
    )
    entries: list[tuple[int, int, str, str, str, Any]] = []
    for index, header in enumerate(headers):
        if header.group("name") != "autoload":
            continue
        body_start = header.end()
        body_end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        sections.append((body_start, body_end))
        for existing in entry_pattern.finditer(text, body_start, body_end):
            raw_value, comment = _split_assignment_comment(existing.group("value"))
            try:
                value = json.loads(raw_value)
            except json.JSONDecodeError:
                value = raw_value
            entries.append(
                (
                    existing.start(),
                    existing.end(),
                    existing.group("prefix"),
                    comment,
                    existing.group("newline"),
                    value,
                )
            )
    return sections, entries


def _validate_autoload_update(
    text: str,
    name: str,
    resource: str,
    *,
    replace_existing: bool,
) -> None:
    _sections, entries = _autoload_entries(text, name)
    expected = "*" + resource
    if not entries or replace_existing:
        return
    values = [entry[5] for entry in entries]
    if len(entries) != 1:
        raise FileExistsError(
            f"Godot autoload {name!r} is declared {len(entries)} times "
            f"with values {values!r}; set replace_existing=True to normalize it"
        )
    if values[0] != expected:
        raise FileExistsError(
            f"Godot autoload {name!r} already points to {values[0]!r}; "
            "set replace_existing=True to replace it"
        )


def _enable_autoload(
    project_file: Path,
    name: str,
    resource: str,
    *,
    replace_existing: bool,
) -> None:
    text = project_file.read_text(encoding="utf-8")
    sections, entries = _autoload_entries(text, name)
    _validate_autoload_update(
        text,
        name,
        resource,
        replace_existing=replace_existing,
    )
    encoded_value = json.dumps("*" + resource)
    setting = f"{name}={encoded_value}"
    if entries and not replace_existing:
        return
    if not sections:
        suffix = "" if text.endswith("\n") else "\n"
        project_file.write_text(
            text + suffix + "\n[autoload]\n" + setting + "\n",
            encoding="utf-8",
        )
        return
    if entries:
        replacements = [
            (
                entries[0][0],
                entries[0][1],
                entries[0][2] + encoded_value + entries[0][3] + entries[0][4],
            ),
            *[
                (start, end, "")
                for start, end, _prefix, _comment, _newline, _value in entries[1:]
            ],
        ]
        for start, end, replacement in reversed(replacements):
            text = text[:start] + replacement + text[end:]
        project_file.write_text(text, encoding="utf-8")
        return
    body_start, body_end = sections[0]
    body = text[body_start:body_end]
    new_body = body + ("" if body.endswith("\n") else "\n") + setting + "\n"
    project_file.write_text(
        text[:body_start] + new_body + text[body_end:],
        encoding="utf-8",
    )


class GodotPluginClient:
    def __init__(self, config: GodotClientConfig) -> None:
        self._config = config
        self._sources = GeneratedAssetSourceResolver()

    def install(
        self,
        source: Mapping[str, Any],
        *,
        install_dir: str = "",
        replace_existing: bool = False,
        enable: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        operation = "plugin.install"
        try:
            resolved = self._sources.resolve(source, allow_directory=True)
            source_root = resolved.path
            if not source_root.is_dir():
                raise ValueError("Godot add-on source must be a directory")
            descriptor = source_root / "plugin.cfg"
            if not descriptor.is_file():
                raise FileNotFoundError(
                    f"Godot add-on source has no plugin.cfg: {descriptor}"
                )
            name = _safe_name(install_dir or source_root.name)
            project_dir, project_file = self._project()
            target = _install_target(project_dir, name)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload={"source": source_descriptor(source)},
            ).to_dict()
        return self._install_tree(
            operation,
            source_root,
            target,
            project_file,
            name,
            source_descriptor=resolved.descriptor(),
            replace_existing=replace_existing,
            enable=enable,
            dry_run=dry_run,
            artifact_type="godot_gameplay_addon",
        )

    def install_framework(
        self,
        *,
        replace_existing: bool = False,
        enable: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        operation = "plugin.install_framework"
        try:
            project_dir, project_file = self._project()
            if not (FRAMEWORK_ROOT / "plugin.cfg").is_file():
                raise FileNotFoundError(
                    f"A3GamePlayable framework source was not found: {FRAMEWORK_ROOT}"
                )
            target = _install_target(project_dir, FRAMEWORK_INSTALL_DIR)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        return self._install_tree(
            operation,
            FRAMEWORK_ROOT,
            target,
            project_file,
            FRAMEWORK_INSTALL_DIR,
            source_descriptor={"adapter_owned": "A3GamePlayable"},
            replace_existing=replace_existing,
            enable=enable,
            dry_run=dry_run,
            artifact_type="godot_runtime_framework",
            autoload_name=FRAMEWORK_AUTOLOAD_NAME,
            autoload_path=FRAMEWORK_AUTOLOAD_PATH,
        )

    def list(self) -> dict[str, Any]:
        try:
            project_dir, _project_file = self._project()
            addons = _addons_directory(project_dir)
        except Exception as exc:
            return GodotOperationResult.failure(
                "plugin.list", f"{type(exc).__name__}: {exc}"
            ).to_dict()
        artifacts = []
        warnings = []
        if addons.is_dir():
            resolved_addons = addons.resolve(strict=True)
            for plugin_dir in sorted(addons.iterdir(), key=lambda item: item.name):
                try:
                    plugin_mode = plugin_dir.lstat().st_mode
                except OSError as exc:
                    warnings.append(
                        f"Skipped unreadable Godot add-on entry {plugin_dir.name}: {exc}"
                    )
                    continue
                if stat.S_ISLNK(plugin_mode):
                    warnings.append(
                        f"Skipped symbolic-link Godot add-on: {plugin_dir.name}"
                    )
                    continue
                if not stat.S_ISDIR(plugin_mode):
                    continue
                descriptor = plugin_dir / "plugin.cfg"
                try:
                    descriptor_mode = descriptor.lstat().st_mode
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    warnings.append(
                        "Skipped unreadable Godot add-on descriptor "
                        f"{plugin_dir.name}/plugin.cfg: {exc}"
                    )
                    continue
                if stat.S_ISLNK(descriptor_mode):
                    warnings.append(
                        "Skipped symbolic-link Godot add-on descriptor: "
                        f"{plugin_dir.name}/plugin.cfg"
                    )
                    continue
                if not stat.S_ISREG(descriptor_mode):
                    warnings.append(
                        "Skipped non-regular Godot add-on descriptor: "
                        f"{plugin_dir.name}/plugin.cfg"
                    )
                    continue
                try:
                    resolved_plugin_dir = plugin_dir.resolve(strict=True)
                    resolved_descriptor = descriptor.resolve(strict=True)
                    resolved_plugin_dir.relative_to(resolved_addons)
                    resolved_descriptor.relative_to(resolved_plugin_dir)
                except (OSError, ValueError) as exc:
                    warnings.append(
                        "Skipped Godot add-on descriptor outside project add-ons: "
                        f"{plugin_dir.name}/plugin.cfg ({exc})"
                    )
                    continue
                artifacts.append(
                    {
                        "artifact_id": descriptor.parent.name,
                        "asset_id": descriptor.parent.name,
                        "type": (
                            "godot_runtime_framework"
                            if descriptor.parent.name == FRAMEWORK_INSTALL_DIR
                            else "godot_gameplay_addon"
                        ),
                        "backend": "godot",
                        "backend_class": "EditorPlugin",
                        "backend_path": "res://"
                        + descriptor.relative_to(project_dir).as_posix(),
                        "state": "ready",
                        "metadata": {"path": str(descriptor.parent)},
                    }
                )
        return GodotOperationResult.success(
            "plugin.list",
            artifacts=artifacts,
            warnings=warnings,
            payload={"count": len(artifacts)},
        ).to_dict()

    def _project(self) -> tuple[Path, Path]:
        project_dir = self._config.project_dir
        project_file = self._config.project_file
        if project_dir is None or project_file is None or not project_file.is_file():
            raise FileNotFoundError(
                "project_path does not resolve to an existing project.godot"
            )
        root = project_dir.resolve(strict=True)
        if project_file.is_symlink():
            raise ValueError(
                f"Godot project file must not be a symlink: {project_file}"
            )
        resolved_project_file = project_file.resolve(strict=True)
        if resolved_project_file.parent != root:
            raise ValueError(
                f"Godot project file escaped the project root: {project_file}"
            )
        return root, resolved_project_file

    def _install_tree(
        self,
        operation: str,
        source: Path,
        target: Path,
        project_file: Path,
        install_dir: str,
        *,
        source_descriptor: Mapping[str, Any],
        replace_existing: bool,
        enable: bool,
        dry_run: bool,
        artifact_type: str,
        autoload_name: str = "",
        autoload_path: str = "",
    ) -> dict[str, Any]:
        resource = f"res://addons/{install_dir}/plugin.cfg"
        payload = {
            "source": dict(source_descriptor),
            "source_path": str(source),
            "target": str(target),
            "plugin": resource,
            "replace_existing": replace_existing,
            "enabled": enable,
            "autoload": autoload_name if enable else "",
            "dry_run": dry_run,
        }
        backup_root: Path | None = None
        backup: Path | None = None
        staging_root: Path | None = None
        original_project: str | None = None
        project_changed = False
        installed = False
        try:
            _validate_tree(source)
            entry_script, _entry_file, plugin_metadata = _plugin_entry_script(source)
            payload["entry_script"] = entry_script
            payload["plugin_metadata"] = plugin_metadata
            checked_target = _install_target(project_file.parent, install_dir)
            if checked_target != target:
                raise ValueError(
                    f"Godot add-on target changed during validation: {target}"
                )
            if (target.exists() or target.is_symlink()) and not replace_existing:
                raise FileExistsError(
                    f"Godot add-on target already exists: {target}; "
                    "set replace_existing=True to replace it"
                )
            project_text = project_file.read_text(encoding="utf-8")
            if enable:
                _validate_plugin_update(project_text)
            if enable and autoload_name and autoload_path:
                _validate_autoload_update(
                    project_text,
                    autoload_name,
                    autoload_path,
                    replace_existing=replace_existing,
                )
            staging_root = Path(tempfile.mkdtemp(prefix="a3game-godot-plugin-stage-"))
            staging = staging_root / target.name
            shutil.copytree(source, staging, symlinks=True)
            _validate_tree(staging)
            staged_entry, _staged_entry_file, staged_metadata = _plugin_entry_script(
                staging
            )
            if staged_entry != entry_script or staged_metadata != plugin_metadata:
                raise ValueError("Godot add-on descriptor changed during staging")
            process_summary, validation = _validate_plugin_with_godot(
                staging,
                install_dir,
                entry_script,
                self._config,
            )
            payload["native_validation"] = {
                "process": process_summary,
                "report": validation,
            }
            if dry_run:
                return GodotOperationResult.success(
                    operation, payload=payload
                ).to_dict()
            original_project = project_text
            checked_target = _install_target(project_file.parent, install_dir)
            if checked_target != target:
                raise ValueError(
                    f"Godot add-on target changed during staging: {target}"
                )
            if (target.exists() or target.is_symlink()) and not replace_existing:
                raise FileExistsError(
                    f"Godot add-on target already exists: {target}; "
                    "set replace_existing=True to replace it"
                )
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() or target.is_symlink():
                backup_root = Path(
                    tempfile.mkdtemp(prefix="a3game-godot-plugin-backup-")
                )
                backup = backup_root / target.name
                shutil.move(str(target), str(backup))
            installed = True
            shutil.move(str(staging), str(target))
            if enable:
                project_changed = True
                _enable_plugin(project_file, install_dir)
                if autoload_name and autoload_path:
                    _enable_autoload(
                        project_file,
                        autoload_name,
                        autoload_path,
                        replace_existing=replace_existing,
                    )
            copied = _copied_files(target)
        except Exception as exc:
            if installed:
                if target.is_symlink() or target.is_file():
                    target.unlink(missing_ok=True)
                elif target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
            if backup is not None and (backup.exists() or backup.is_symlink()):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(backup), str(target))
            if original_project is not None and project_changed:
                try:
                    project_file.write_text(original_project, encoding="utf-8")
                except OSError:
                    pass
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        finally:
            if staging_root is not None:
                shutil.rmtree(staging_root, ignore_errors=True)
            if backup_root is not None:
                shutil.rmtree(backup_root, ignore_errors=True)
        payload["copied_files"] = copied
        return GodotOperationResult.success(
            operation,
            artifacts=[
                {
                    "type": artifact_type,
                    "path": str(target),
                    "backend_path": resource,
                    "state": "ready",
                }
            ],
            payload=payload,
        ).to_dict()
