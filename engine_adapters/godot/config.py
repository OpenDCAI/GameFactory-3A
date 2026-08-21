"""Configuration for the stable GodotClient API."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

SUPPORTED_API_VERSIONS = ("v1",)
DEFAULT_API_VERSION = "v1"
DEFAULT_RUNTIME_HOST = "127.0.0.1"
DEFAULT_RUNTIME_PORT = 30050
DEFAULT_WORLD_ID = "world_001"
DEFAULT_EDITOR_TIMEOUT = 300
DEFAULT_IMPORT_TIMEOUT = 300
DEFAULT_IMPORT_ROOT = "assets/imported"
DEFAULT_AVATAR_DEST = "assets/imported/avatars"
DEFAULT_MOTION_DEST = "assets/imported/motions"
DEFAULT_SCENE_DEST = "assets/imported/scenes"
DEFAULT_ENVIRONMENT_DEST = "assets/imported/environments"
DEFAULT_EFFECT_DEST = "assets/imported/effects"
DEFAULT_MATERIAL_DEST = "assets/imported/materials"
DEFAULT_TEXTURE_DEST = "assets/imported/textures"
DEFAULT_PROP_DEST = "assets/imported/props"
DEFAULT_WEAPON_DEST = "assets/imported/weapons"
DEFAULT_AUDIO_DEST = "assets/imported/audio"

GODOT_ASSET_TYPE_DEFAULT_DESTS = {
    "avatar": DEFAULT_AVATAR_DEST,
    "motion": DEFAULT_MOTION_DEST,
    "scene": DEFAULT_SCENE_DEST,
    "environment": DEFAULT_ENVIRONMENT_DEST,
    "effect": DEFAULT_EFFECT_DEST,
    "material": DEFAULT_MATERIAL_DEST,
    "texture": DEFAULT_TEXTURE_DEST,
    "prop": DEFAULT_PROP_DEST,
    "static_mesh": DEFAULT_PROP_DEST,
    "weapon": DEFAULT_WEAPON_DEST,
    "audio": DEFAULT_AUDIO_DEST,
}


def _first_environment_value(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _optional_path(value: str | Path | None) -> Path | None:
    if value is None or not str(value).strip():
        return None
    path = Path(str(value).strip()).expanduser()
    if os.name == "nt" and path.is_absolute():
        return Path(os.path.abspath(str(path)))
    return path.resolve(strict=False)


def _unresolved_absolute_path(value: str | Path | None) -> Path | None:
    """Return an absolute path while retaining symbolic-link components."""

    if value is None or not str(value).strip():
        return None
    return Path(os.path.abspath(str(Path(str(value).strip()).expanduser())))


def normalize_godot_project_directory(
    value: str | Path | None,
    *,
    resolve: bool = True,
) -> Path | None:
    """Map a project directory or its ``project.godot`` marker to the root."""

    path = _unresolved_absolute_path(value)
    if path is None:
        return None
    project_dir = path.parent if path.name.lower() == "project.godot" else path
    return project_dir.resolve(strict=False) if resolve else project_dir


@dataclass(frozen=True)
class GodotClientConfig:
    """Resolved configuration used by GodotClient and private code."""

    project_path: Path | None
    godot_executable: Path | None
    api_version: str = DEFAULT_API_VERSION
    runtime_host: str = DEFAULT_RUNTIME_HOST
    runtime_port: int = DEFAULT_RUNTIME_PORT
    editor_timeout: int = DEFAULT_EDITOR_TIMEOUT
    import_timeout: int = DEFAULT_IMPORT_TIMEOUT
    project_path_input: Path | None = None

    @classmethod
    def resolve(
        cls,
        project_path: str | Path | None = None,
        godot_executable: str | Path | None = None,
        api_version: str = DEFAULT_API_VERSION,
        *,
        runtime_host: str | None = None,
        runtime_port: int | None = None,
        editor_timeout: int | None = None,
        import_timeout: int | None = None,
    ) -> GodotClientConfig:
        version = str(api_version or "").strip()
        if version not in SUPPORTED_API_VERSIONS:
            supported = ", ".join(SUPPORTED_API_VERSIONS)
            raise ValueError(
                f"Unsupported GodotClient api_version {version!r}; "
                f"supported versions: {supported}"
            )

        configured_project = project_path or _first_environment_value(
            "A3GAME_GODOT_PROJECT",
            "AAAGF_GODOT_PROJECT",
        )
        unresolved_project = _unresolved_absolute_path(configured_project)
        resolved_project = _optional_path(configured_project)
        resolved_executable = _optional_path(
            godot_executable
            or _first_environment_value(
                "A3GAME_GODOT_EXECUTABLE",
                "A3GAME_GODOT",
                "AAAGF_GODOT",
            )
        )
        resolved_host = (
            runtime_host
            or _first_environment_value("A3GAME_GODOT_RUNTIME_HOST")
            or DEFAULT_RUNTIME_HOST
        )
        resolved_port = runtime_port
        if resolved_port is None:
            value = _first_environment_value("A3GAME_GODOT_RUNTIME_PORT")
            resolved_port = int(value) if value else DEFAULT_RUNTIME_PORT
        if not 1 <= int(resolved_port) <= 65535:
            raise ValueError("Godot runtime UDP port must be between 1 and 65535")

        resolved_editor_timeout = editor_timeout
        if resolved_editor_timeout is None:
            value = _first_environment_value("A3GAME_GODOT_EDITOR_TIMEOUT")
            resolved_editor_timeout = int(value) if value else DEFAULT_EDITOR_TIMEOUT
        resolved_import_timeout = import_timeout
        if resolved_import_timeout is None:
            value = _first_environment_value("A3GAME_GODOT_IMPORT_TIMEOUT")
            resolved_import_timeout = int(value) if value else DEFAULT_IMPORT_TIMEOUT
        if int(resolved_editor_timeout) <= 0:
            raise ValueError("editor_timeout must be greater than zero")
        if int(resolved_import_timeout) <= 0:
            raise ValueError("import_timeout must be greater than zero")

        return cls(
            project_path=resolved_project,
            godot_executable=resolved_executable,
            project_path_input=unresolved_project,
            api_version=version,
            runtime_host=str(resolved_host).strip() or DEFAULT_RUNTIME_HOST,
            runtime_port=int(resolved_port),
            editor_timeout=int(resolved_editor_timeout),
            import_timeout=int(resolved_import_timeout),
        )

    @property
    def project_dir(self) -> Path | None:
        path = self.project_path
        if path is None:
            return None
        input_path = self.project_path_input or path
        return path.parent if input_path.name.lower() == "project.godot" else path

    @property
    def project_file(self) -> Path | None:
        project_dir = self.project_dir
        return None if project_dir is None else project_dir / "project.godot"

    @property
    def project_dir_input(self) -> Path | None:
        """Original absolute project directory before resolving symbolic links."""

        path = self.project_path_input or self.project_path
        if path is None:
            return None
        return path.parent if path.name.lower() == "project.godot" else path

    @property
    def project_name(self) -> str:
        project_dir = self.project_dir
        return project_dir.name if project_dir is not None else ""

    @property
    def engine_version_hint(self) -> str:
        executable = self.godot_executable
        if executable is None:
            return ""
        match = re.search(r"(\d+\.\d+(?:\.\d+)?)", executable.name)
        return match.group(1) if match else ""

    @property
    def data_root(self) -> Path:
        configured = _first_environment_value(
            "A3GAME_GODOT_DATA_ROOT",
            "A3GAME_DATA_ROOT",
        )
        if configured:
            unresolved = _unresolved_absolute_path(configured)
            if unresolved is not None:
                return unresolved
        project_dir = self.project_dir
        if project_dir is not None:
            return project_dir / ".a3game"
        return Path(__file__).resolve().parent / "_data"

    @property
    def artifact_registry_path(self) -> Path:
        configured = _first_environment_value(
            "A3GAME_GODOT_ARTIFACT_REGISTRY",
            "A3GAME_ARTIFACT_REGISTRY",
        )
        if configured:
            unresolved = _unresolved_absolute_path(configured)
            if unresolved is not None:
                return unresolved
        return self.data_root / "artifacts.json"

    @property
    def world_registry_root(self) -> Path:
        configured = _first_environment_value(
            "A3GAME_GODOT_WORLD_REGISTRY_ROOT",
            "A3GAME_WORLD_REGISTRY_ROOT",
        )
        if configured:
            unresolved = _unresolved_absolute_path(configured)
            if unresolved is not None:
                return unresolved
        return self.data_root / "worlds"
