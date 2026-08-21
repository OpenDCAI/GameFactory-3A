#!/usr/bin/env python3
"""
scripts/import_generated_asset.py

Host-side launcher for the last leg of the chain:

    models/gen_3d_object  →  test_data/outputs/.../model.glb  →  UE5 / Unity / Godot / Blender asset

It finds the engine binary, drives it in batch mode with the importer that lives
in `engine_adapters/<engine>/import_generated/`, and reports what the engine
said. The engine-side scripts are the contract; this file only launches them.

Blender is in the list for a different reason than the game-engine targets: it is not a
target, it is the neutral step that reads what a game engine will not (`.ply`,
`.usd`), conditions the asset and writes back the `.glb` UE5, Unity, and Godot
want. It is also the only route that needs no project.

Nothing here constructs an output path — sources come from a `--src` or from the
`<kind>_results_summary.json` that `pipeline/assets_gen/gen_3d_object/run.py`
wrote through `pipeline/common/paths.py`.

Usage:
    # validate the artifact and print the exact commands, without an engine
    python scripts/import_generated_asset.py --src out/model.glb --engine both --dry-run

    # Unity (installs the Editor script into the project on first run)
    python scripts/import_generated_asset.py --src out/model.glb \\
        --engine unity --unity-project D:/proj/MyGame

    # UE5 (launches the full editor by default; see --ue-mode)
    python scripts/import_generated_asset.py --src out/model.glb \\
        --engine ue5 --uproject D:/proj/MyGame/MyGame.uproject

    # Blender: condition the asset and write a preview, no project needed
    python scripts/import_generated_asset.py --src out/world.glb \\
        --engine blender --blender-preview

    # Godot (copies into res:// then waits for the editor importer)
    python scripts/import_generated_asset.py --src out/model.glb \
        --engine godot --godot-project /projects/MyGame

    # everything a generation run produced
    python scripts/import_generated_asset.py --engine both \\
        --summary test_data/outputs/<game>/<run>/3d_object_results_summary.json

Environment (so the flags can be omitted):
    AAAGF_UE_EDITOR       path to UnrealEditor-Cmd.exe
    AAAGF_UPROJECT        path to the .uproject
    AAAGF_UNITY           path to Unity.exe
    AAAGF_UNITY_PROJECT   path to the Unity project root
    A3GAME_GODOT_EXECUTABLE preferred path to the Godot 4 editor executable
    A3GAME_GODOT          fallback path to the Godot 4 editor executable
    AAAGF_GODOT           legacy fallback path to the Godot 4 editor executable
    A3GAME_GODOT_PROJECT  path to the Godot project root or project.godot
    AAAGF_GODOT_PROJECT   legacy fallback for A3GAME_GODOT_PROJECT
    AAAGF_BLENDER         path to blender(.exe), or a python that can import bpy
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from engine_adapters.godot import GodotClient  # noqa: E402
from engine_adapters.godot._internal import (  # noqa: E402
    GodotTransport,
    atomic_write_bytes,
    godot_4_version_error,
    godot_import_error_lines,
    inspect_godot_resource,
    inspection_bone_tracks,
    inspection_skeleton_paths,
    read_managed_bytes,
    validate_managed_directory,
    validate_managed_file,
    validate_resource_inspection,
)
from engine_adapters.godot.config import (  # noqa: E402
    GODOT_ASSET_TYPE_DEFAULT_DESTS,
    GodotClientConfig,
    normalize_godot_project_directory,
)
from models.common.glb_utils import glb_summary  # noqa: E402

try:
    from models.common import ply_utils  # noqa: E402
except ImportError:  # pragma: no cover - optional helper, not always present
    ply_utils = None  # type: ignore[assignment]

UE_IMPORTER = (
    _REPO_ROOT / "engine_adapters" / "ue5" / "import_generated" / "import_mesh.py"
)
UE_MOTION_IMPORTER = (
    _REPO_ROOT / "engine_adapters" / "ue5" / "import_generated" / "import_motion.py"
)
UNITY_IMPORTER = (
    _REPO_ROOT
    / "engine_adapters"
    / "unity3d"
    / "import_generated"
    / "ImportGeneratedMesh.cs"
)
BLENDER_IMPORTER = (
    _REPO_ROOT / "engine_adapters" / "blender" / "import_generated" / "import_mesh.py"
)
BLENDER_MOTION_IMPORTER = (
    _REPO_ROOT / "engine_adapters" / "blender" / "import_generated" / "import_motion.py"
)

USAGES = ("asset", "vfx_standalone", "vfx_particle", "motion")
KINDS = ("mesh", "motion")

#: `both` predates the Godot and Blender routes and retains its UE + Unity meaning.
ENGINE_SETS = {
    "both": ["ue5", "unity"],
    "all": ["ue5", "unity", "godot", "blender"],
}


# ── Engine discovery ──────────────────────────────────────────────────────────


def find_unreal_editor(explicit: str | None = None) -> Path | None:
    """
    Locate `UnrealEditor-Cmd`; newest version wins.

    A pointer to `UnrealEditor.exe` is accepted and rewritten to the `-Cmd`
    variant next to it, which is the one that runs commandlets without opening
    a window.
    """
    if explicit or os.environ.get("AAAGF_UE_EDITOR"):
        return _prefer_cmd_binary(Path(explicit or os.environ["AAAGF_UE_EDITOR"]))

    if platform.system() == "Windows":
        # Engines are installed anywhere: the Epic default, or a bare UE_5.x on
        # whatever drive had room.
        patterns = [
            r"C:\Program Files\Epic Games\UE_*\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
        ]
        patterns += [
            f"{d}:/UE_*/Engine/Binaries/Win64/UnrealEditor-Cmd.exe" for d in "CDEFG"
        ]
        patterns += [
            f"{d}:/Epic Games/UE_*/Engine/Binaries/Win64/UnrealEditor-Cmd.exe"
            for d in "CDEFG"
        ]
    elif platform.system() == "Darwin":
        patterns = [
            "/Users/Shared/Epic Games/UE_*/Engine/Binaries/Mac/UnrealEditor-Cmd"
        ]
    else:
        patterns = [
            str(
                Path.home()
                / "UnrealEngine"
                / "*"
                / "Engine"
                / "Binaries"
                / "Linux"
                / "UnrealEditor-Cmd"
            )
        ]

    found = sorted(p for pattern in patterns for p in glob.glob(pattern))
    return Path(found[-1]) if found else None


def _prefer_cmd_binary(path: Path) -> Path:
    """`UnrealEditor.exe` → `UnrealEditor-Cmd.exe` when that one exists."""
    if path.stem.endswith("-Cmd"):
        return path
    cmd = path.with_name(path.stem + "-Cmd" + path.suffix)
    return cmd if cmd.exists() else path


def find_unity(explicit: str | None = None) -> Path | None:
    """Locate a Unity editor binary; newest installed version wins."""
    if explicit:
        return Path(explicit)
    if os.environ.get("AAAGF_UNITY"):
        return Path(os.environ["AAAGF_UNITY"])

    patterns = {
        "Windows": [r"C:\Program Files\Unity\Hub\Editor\*\Editor\Unity.exe"],
        "Darwin": ["/Applications/Unity/Hub/Editor/*/Unity.app/Contents/MacOS/Unity"],
        "Linux": [
            str(Path.home() / "Unity" / "Hub" / "Editor" / "*" / "Editor" / "Unity")
        ],
    }.get(platform.system(), [])
    found = sorted(p for pattern in patterns for p in glob.glob(pattern))
    return Path(found[-1]) if found else None


def find_blender(explicit: str | None = None) -> Path | None:
    """
    Locate something that can run `bpy`; newest installed Blender wins.

    Two things qualify and the importer runs identically under both: a Blender
    application, and a Python whose environment has the pip `bpy` wheel. The
    second is what a headless install script leaves behind, so this interpreter
    is checked before giving up.
    """
    if explicit or os.environ.get("AAAGF_BLENDER"):
        return Path(explicit or os.environ["AAAGF_BLENDER"])

    on_path = shutil.which("blender")
    if on_path:
        return Path(on_path)

    if platform.system() == "Windows":
        patterns = [r"C:\Program Files\Blender Foundation\Blender *\blender.exe"]
    elif platform.system() == "Darwin":
        patterns = ["/Applications/Blender.app/Contents/MacOS/Blender"]
    else:
        patterns = [
            "/usr/share/blender/*/blender",
            str(Path.home() / "blender-*" / "blender"),
        ]
    found = sorted(p for pattern in patterns for p in glob.glob(pattern))
    if found:
        return Path(found[-1])

    if importlib.util.find_spec("bpy") is not None:
        return Path(sys.executable)
    return None


def find_godot(explicit: str | None = None) -> Path | None:
    """Locate a Godot 4 editor binary."""
    configured = str(explicit or "").strip()
    if not configured:
        configured = next(
            (
                value
                for name in (
                    "A3GAME_GODOT_EXECUTABLE",
                    "A3GAME_GODOT",
                    "AAAGF_GODOT",
                )
                if (value := os.environ.get(name, "").strip())
            ),
            "",
        )
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    for name in ("godot4", "godot", "godot-mono"):
        discovered = shutil.which(name)
        if discovered:
            return Path(discovered).resolve()
    patterns = {
        "Windows": [r"C:\Program Files\Godot\Godot*.exe"],
        "Darwin": ["/Applications/Godot.app/Contents/MacOS/Godot"],
        "Linux": ["/usr/local/bin/godot", "/usr/bin/godot4"],
    }.get(platform.system(), [])
    found = sorted(p for pattern in patterns for p in glob.glob(pattern))
    return Path(found[-1]) if found else None


def is_blender_app(binary: Path) -> bool:
    """True for a Blender application, False for a `bpy`-carrying Python."""
    return "blender" in binary.stem.lower()


# ── Source resolution ─────────────────────────────────────────────────────────


def read_json(path: Path):
    """
    Read a JSON artifact.

    `paths.py` writes UTF-8, but summaries produced before that was made explicit
    carry the writer's locale encoding (GBK on a Chinese Windows box), so fall
    back rather than making the user regenerate a run.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        print(
            f"    note: {path.name} is not UTF-8; falling back to the locale encoding"
        )
        return json.loads(path.read_text())


def sources_from_args(args) -> list[tuple[str, str | None]]:
    """Return `[(path, asset_name), ...]` from `--src` or `--summary`."""
    if args.src:
        return [(str(Path(args.src).resolve()), args.name)]

    summary = read_json(Path(args.summary))
    out = []
    for entry in summary:
        if getattr(args, "kind", "mesh") == "motion":
            path = (
                entry.get("retargeted_fbx_path")
                or entry.get("anim_only_fbx_path")
                or entry.get("glb_path")
            )
        else:
            path = entry.get("glb_path")
        if path:
            out.append((str(Path(path).resolve()), entry.get("task_id")))
    if not out:
        raise SystemExit(f"no artifacts listed in {args.summary}")
    return out


def validate_source(path: str) -> dict:
    """
    Check the artifact before spending an engine launch on it.

    Catches the cheap failures: a missing file, a "GLB" that is actually an error
    page the download step never noticed, and a `.ply` that turns out to be the
    Gaussian-splat half of a world and carries no geometry at all.
    """
    p = Path(path)
    info: dict = {"path": str(p), "exists": p.is_file()}
    if not info["exists"]:
        info["error"] = "file does not exist"
        return info
    info["bytes"] = p.stat().st_size
    if p.suffix.lower() == ".glb":
        info.update(glb_summary(p.read_bytes()))
    elif p.suffix.lower() == ".gltf":
        try:
            document = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(document, dict):
                raise ValueError("glTF document must be an object")
            accessors = document.get("accessors", [])
            triangles = 0
            for mesh in document.get("meshes", []):
                for primitive in mesh.get("primitives", []):
                    if primitive.get("mode", 4) != 4:
                        continue
                    accessor_index = primitive.get("indices")
                    if accessor_index is None:
                        accessor_index = primitive.get("attributes", {}).get("POSITION")
                    count = (
                        accessors[accessor_index].get("count", 0)
                        if isinstance(accessor_index, int)
                        and 0 <= accessor_index < len(accessors)
                        else 0
                    )
                    triangles += int(count) // 3
            info.update(
                {
                    "triangles": triangles,
                    "meshes": len(document.get("meshes", [])),
                    "materials": len(document.get("materials", [])),
                    "textures": len(document.get("textures", [])),
                    "images": len(document.get("images", [])),
                    "nodes": len(document.get("nodes", [])),
                    "generator": (document.get("asset") or {}).get("generator"),
                }
            )
        except (
            AttributeError,
            IndexError,
            json.JSONDecodeError,
            OSError,
            TypeError,
            ValueError,
        ) as exc:
            info["triangles"] = None
            info["error"] = f"invalid glTF JSON: {exc}"
    elif p.suffix.lower() == ".fbx":
        # FBX is binary; structural validity is proven by the engine importer,
        # not by a host-side parse. A Kaydara header is enough to refuse an
        # empty placeholder before paying for an editor launch.
        header = p.read_bytes()[:24]
        info["format"] = "fbx"
        info["looks_like_fbx"] = header.startswith(b"Kaydara FBX Binary") or (
            b"FBX" in header
        )
        if info["bytes"] < 64 or not info["looks_like_fbx"]:
            info["error"] = "file does not look like an FBX"
    elif p.suffix.lower() == ".ply":
        if ply_utils is None:
            info["warning"] = "ply_utils unavailable; skipping PLY inspect"
        else:
            try:
                info.update(ply_utils.describe(p))
            except (ply_utils.PlyError, OSError) as e:
                info["error"] = str(e)
    return info


# ── Command construction ──────────────────────────────────────────────────────


def _ue_importer(args) -> Path:
    return (
        UE_MOTION_IMPORTER if getattr(args, "kind", "mesh") == "motion" else UE_IMPORTER
    )


def _blender_importer(args) -> Path:
    return (
        BLENDER_MOTION_IMPORTER
        if getattr(args, "kind", "mesh") == "motion"
        else BLENDER_IMPORTER
    )


def ue_command(
    editor: Path,
    uproject: Path,
    src: str,
    args,
    asset_name: str | None,
    report: Path,
    source_tris: int | None = None,
) -> tuple[list[str], dict]:
    """
    UnrealEditor-Cmd invocation that runs `import_mesh.py` or
    `import_motion.py` in the project.

    Parameters travel in a JSON job file named by `$AAAGF_IMPORT_JOB` rather than
    on the command line: `-script="file.py --a b"` has to survive both the shell
    and UE's own argument parser, and paths lose their quoting on the way.

    Returns:
        (command, extra environment)
    """
    importer = _ue_importer(args)
    if getattr(args, "kind", "mesh") == "motion":
        job = {
            "src": src,
            "dest": getattr(args, "ue_motion_dest", None) or "/Game/Generated/Motion",
            "name": asset_name,
            "existing_skeleton": getattr(args, "ue_skeleton", None),
            "no_mesh": bool(getattr(args, "ue_anim_only", False)),
            "report": str(report),
        }
    else:
        job = {
            "src": src,
            "dest": args.ue_dest,
            "name": asset_name,
            "usage": args.usage,
            "target_tris": args.target_tris,
            "pivot": args.pivot,
            "normalize_scale": args.normalize_scale,
            "report": str(report),
            # Read from the file here so the engine side can flag a mismatch — under
            # Nanite, UE reports the fallback mesh, not the source density.
            "source_tris": source_tris,
        }
    job_path = report.with_name(report.stem + "_job.json")
    job_path.parent.mkdir(parents=True, exist_ok=True)
    job_path.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")

    env = {"AAAGF_IMPORT_JOB": str(job_path)}
    if args.ue_mode == "editor":
        # Full editor: Slate exists, so the post-import Content Browser sync that
        # AssetTools performs cannot assert. Costs a real editor window and a
        # slower start, and the script has to close it when it is done.
        command = [
            str(_prefer_gui_binary(editor)),
            str(uproject),
            f"-ExecutePythonScript={importer}",
            "-unattended",
            "-nopause",
            "-nosplash",
            "-stdout",
            "-utf8output",
        ]
        env["AAAGF_UE_QUIT_WHEN_DONE"] = "1"
    else:
        command = [
            str(editor),
            str(uproject),
            "-run=pythonscript",
            f"-script={importer}",
            "-unattended",
            "-nopause",
            "-nosplash",
            "-stdout",
            "-utf8output",
        ]
    command += list(args.ue_extra or [])
    if args.ue_route:
        env["AAAGF_UE_IMPORT_ROUTE"] = args.ue_route
    return command, env


def _prefer_gui_binary(path: Path) -> Path:
    """`UnrealEditor-Cmd.exe` → `UnrealEditor.exe` when that one exists."""
    if not path.stem.endswith("-Cmd"):
        return path
    gui = path.with_name(path.stem[: -len("-Cmd")] + path.suffix)
    return gui if gui.exists() else path


def unity_command(
    unity: Path, project: Path, src: str, args, asset_name: str | None, report: Path
) -> list[str]:
    """Unity batch-mode invocation of `ImportGeneratedMesh.RunFromCLI`."""
    cmd = [
        str(unity),
        "-batchmode",
        "-quit",
        "-nographics",
        "-projectPath",
        str(project),
        "-executeMethod",
        "ImportGeneratedMesh.RunFromCLI",
        "-logFile",
        str(report.with_suffix(".unity.log")),
        "--src",
        src,
        "--dest",
        args.unity_dest,
        "--usage",
        args.usage,
        "--report",
        str(report),
    ]
    if asset_name:
        cmd += ["--name", asset_name]
    if args.target_tris:
        cmd += ["--target-tris", str(args.target_tris)]
    if args.pivot:
        cmd += ["--pivot", args.pivot]
    if args.normalize_scale:
        cmd += ["--normalize-scale"]
    return cmd


def godot_command(godot: Path, project: Path) -> list[str]:
    """Godot's documented import-only editor invocation."""
    return [str(godot), "--headless", "--path", str(project), "--import"]


def _remove_godot_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _assert_no_godot_symlink_components(path: Path, boundary: Path) -> None:
    """Reject existing links before resolving a project-managed path."""

    try:
        relative = path.relative_to(boundary)
    except ValueError as exc:
        raise ValueError(f"Godot managed path escaped the project: {path}") from exc
    current = boundary
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(
                f"Godot managed path must not contain a symlink: {current}"
            )


def _validate_godot_managed_paths(paths: list[Path]) -> None:
    """Only absent paths and regular files are valid compatibility targets."""

    for path in dict.fromkeys(paths):
        if path.is_symlink():
            raise ValueError(f"Godot managed path must not be a symlink: {path}")
        if path.exists() and not path.is_file():
            raise ValueError(f"Godot managed path must be a regular file: {path}")


@dataclass
class PreparedGodotAsset:
    """Transactional filesystem staging used by the compatibility launcher."""

    target: Path
    resource_path: str
    asset_type: str
    project: Path
    import_sources: tuple[Path, ...]
    touched: tuple[Path, ...] = ()
    backups: dict[Path, Path] = field(default_factory=dict)
    backup_root: Path | None = None
    created_directories: tuple[Path, ...] = ()
    dry_run: bool = False
    registry_path: Path | None = None
    registry_existed: bool = False
    registry_bytes: bytes | None = None
    registry_created_directories: tuple[Path, ...] = ()
    _registry_transaction_active: bool = False
    _finalized: bool = False

    @property
    def created(self) -> bool:
        return self.target not in self.backups

    @property
    def backup(self) -> Path | None:
        return self.backups.get(self.target)

    def __iter__(self):
        """Keep legacy four-value unpacking for direct helper callers."""

        yield self.target
        yield self.resource_path
        yield self.created
        yield self.backup

    def commit(self) -> None:
        if self._finalized:
            return
        if self.backup_root is not None:
            shutil.rmtree(self.backup_root, ignore_errors=True)
        self.registry_bytes = None
        self.registry_created_directories = ()
        self._registry_transaction_active = False
        self._finalized = True

    def begin_registry_transaction(self, path: Path) -> None:
        """Snapshot the registry so a later report failure can undo the import."""

        if self._registry_transaction_active:
            raise RuntimeError("Godot registry transaction is already active")
        registry_path = validate_managed_file(
            path,
            label="Godot artifact registry",
        )
        try:
            original = read_managed_bytes(
                registry_path,
                label="Godot artifact registry",
            )
        except FileNotFoundError:
            existed = False
            original = None
        else:
            existed = True

        missing_directories = []
        current = registry_path.parent
        while current != current.parent:
            try:
                current.lstat()
            except FileNotFoundError:
                missing_directories.append(current)
            else:
                break
            current = current.parent
        self.registry_path = registry_path
        self.registry_existed = existed
        self.registry_bytes = original
        self.registry_created_directories = tuple(missing_directories)
        self._registry_transaction_active = True

    def _rollback_registry(self) -> None:
        if not self._registry_transaction_active or self.registry_path is None:
            return
        path = self.registry_path
        if self.registry_existed:
            if self.registry_bytes is None:
                raise RuntimeError("Godot registry snapshot is missing")
            atomic_write_bytes(
                path,
                self.registry_bytes,
                label="Godot artifact registry",
            )
        else:
            safe_path = validate_managed_file(
                path,
                label="Godot artifact registry",
            )
            safe_path.unlink(missing_ok=True)
            for directory in self.registry_created_directories:
                try:
                    safe_directory = validate_managed_directory(
                        directory,
                        label="Godot artifact registry directory",
                    )
                    safe_directory.rmdir()
                except OSError:
                    pass
        self.registry_bytes = None
        self.registry_created_directories = ()
        self._registry_transaction_active = False

    def rollback(self) -> None:
        if self._finalized or self.dry_run:
            self._finalized = True
            return
        recovery_errors = []
        managed_paths = list(self.touched)
        try:
            managed_paths.extend(
                _godot_import_cache_paths(self.project, list(self.import_sources))
            )
        except (OSError, ValueError) as exc:
            recovery_errors.append(
                f"discover Godot import cache: {type(exc).__name__}: {exc}"
            )
        for path in sorted(
            dict.fromkeys(managed_paths),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                _remove_godot_path(path)
            except Exception as exc:
                recovery_errors.append(f"remove {path}: {type(exc).__name__}: {exc}")
        for path, backup in self.backups.items():
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                if backup.exists() or backup.is_symlink():
                    shutil.move(str(backup), str(path))
            except Exception as exc:
                recovery_errors.append(f"restore {path}: {type(exc).__name__}: {exc}")
        try:
            self._rollback_registry()
        except Exception as exc:
            recovery_errors.append(
                f"restore artifact registry: {type(exc).__name__}: {exc}"
            )
        for directory in sorted(
            self.created_directories,
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        if self.backup_root is not None and not recovery_errors:
            shutil.rmtree(self.backup_root, ignore_errors=True)
        self._finalized = True
        if recovery_errors:
            raise RuntimeError(
                "; ".join(recovery_errors) + f"; backups retained at {self.backup_root}"
            )


def _godot_gltf_sidecars(source: Path, target: Path) -> list[tuple[Path, Path]]:
    if source.suffix.lower() != ".gltf":
        return []
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"glTF JSON is invalid: {source}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"glTF JSON must be an object: {source}")
    uris = []
    for collection in (payload.get("buffers", []), payload.get("images", [])):
        if not isinstance(collection, list):
            raise ValueError(f"glTF buffers/images must be arrays: {source}")
        for item in collection:
            if isinstance(item, dict) and isinstance(item.get("uri"), str):
                uris.append(item["uri"])

    sidecars: list[tuple[Path, Path]] = []
    seen_destinations: set[Path] = set()
    source_root = source.parent.resolve()
    destination_root = target.parent.resolve()
    for uri in uris:
        parsed_uri = urlsplit(uri)
        if parsed_uri.scheme or parsed_uri.netloc:
            continue
        if parsed_uri.query or parsed_uri.fragment:
            raise ValueError(f"glTF sidecar URI has unsupported query/fragment: {uri}")
        decoded_uri = unquote(parsed_uri.path, errors="strict")
        if "\x00" in decoded_uri:
            raise ValueError(f"glTF sidecar URI is unsafe: {uri}")
        relative = PurePosixPath(decoded_uri.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"glTF sidecar URI is unsafe: {uri}")
        sidecar = (source_root / Path(*relative.parts)).resolve()
        try:
            sidecar.relative_to(source_root)
        except ValueError as exc:
            raise ValueError(f"glTF sidecar escaped its source: {uri}") from exc
        if not sidecar.is_file():
            raise FileNotFoundError(f"glTF sidecar was not found: {sidecar}")
        raw_destination = target.parent / Path(*relative.parts)
        _assert_no_godot_symlink_components(raw_destination, target.parent)
        destination = raw_destination.resolve(strict=False)
        try:
            destination.relative_to(destination_root)
        except ValueError as exc:
            raise ValueError(f"glTF sidecar escaped its destination: {uri}") from exc
        if destination not in seen_destinations:
            seen_destinations.add(destination)
            sidecars.append((sidecar, destination))
    return sidecars


def _godot_import_cache_paths(project: Path, paths: list[Path]) -> list[Path]:
    """Resolve Godot 4 cache files belonging to staged resource paths."""

    project = project.resolve()
    raw_cache_root = project / ".godot" / "imported"
    _assert_no_godot_symlink_components(raw_cache_root, project)
    cache_root = raw_cache_root.resolve(strict=False)
    try:
        cache_root.relative_to(project)
    except ValueError as exc:
        raise ValueError("Godot import cache escaped the project") from exc
    if not cache_root.is_dir():
        return []
    sources: list[Path] = []
    for path in paths:
        if path.is_dir():
            sources.extend(
                Path(str(item)[: -len(".import")])
                for item in path.rglob("*.import")
                if item.is_file()
            )
        else:
            sources.append(path)
    cache_paths: list[Path] = []
    for source in dict.fromkeys(sources):
        try:
            relative = source.resolve(strict=False).relative_to(project)
        except ValueError as exc:
            raise ValueError(
                f"Godot imported source escaped the project: {source}"
            ) from exc
        resource_path = "res://" + relative.as_posix()
        digest = hashlib.md5(resource_path.encode()).hexdigest()
        prefix = f"{source.name}-{digest}."
        for item in cache_root.iterdir():
            if not item.name.startswith(prefix):
                continue
            if item.is_symlink():
                raise ValueError(
                    f"Godot import cache target must not be a symlink: {item}"
                )
            if not item.is_file():
                raise ValueError(
                    f"Godot import cache target must be a regular file: {item}"
                )
            cache_paths.append(item)
    return list(dict.fromkeys(cache_paths))


def _new_directories(paths: list[Path], boundary: Path) -> tuple[Path, ...]:
    directories = set()
    for path in paths:
        try:
            path.relative_to(boundary)
        except ValueError as exc:
            raise ValueError(
                f"Godot import directory escaped the project: {path}"
            ) from exc
        current = path
        while current != boundary and not current.exists():
            directories.add(current)
            current = current.parent
    return tuple(sorted(directories, key=lambda item: len(item.parts)))


def _backup_godot_targets(paths: list[Path]) -> tuple[Path | None, dict[Path, Path]]:
    existing = [
        path for path in dict.fromkeys(paths) if path.exists() or path.is_symlink()
    ]
    if not existing:
        return None, {}
    backup_root = Path(tempfile.mkdtemp(prefix="a3game-godot-import-"))
    backups: dict[Path, Path] = {}
    try:
        for index, path in enumerate(existing):
            backup = backup_root / str(index)
            shutil.move(str(path), str(backup))
            backups[path] = backup
    except Exception:
        for path, backup in backups.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists():
                shutil.move(str(backup), str(path))
        shutil.rmtree(backup_root, ignore_errors=True)
        raise
    return backup_root, backups


def prepare_godot_asset(
    project: Path,
    src: str,
    args,
    asset_name: str | None,
) -> PreparedGodotAsset:
    """Transactionally stage a generated file and any local glTF sidecars."""
    raw_project = normalize_godot_project_directory(project, resolve=False)
    if raw_project is None:  # pragma: no cover - Path input is always non-empty
        raise ValueError("Godot project path is required")
    if raw_project.is_symlink():
        raise ValueError(f"Godot project path must not be a symlink: {raw_project}")
    project = raw_project.resolve(strict=False)
    project_file = project / "project.godot"
    if project_file.is_symlink():
        raise ValueError(f"Godot project marker must not be a symlink: {project_file}")
    if not project_file.is_file():
        raise FileNotFoundError(f"Godot project.godot was not found: {project_file}")
    asset_type = "motion" if getattr(args, "kind", "mesh") == "motion" else "prop"
    raw_destination = (
        str(
            getattr(args, "godot_dest", "")
            or GODOT_ASSET_TYPE_DEFAULT_DESTS[asset_type]
        )
        .strip()
        .replace("\\", "/")
    )
    if raw_destination.startswith("res://"):
        raw_destination = raw_destination[len("res://") :]
    destination = Path(raw_destination)
    if not raw_destination or destination.is_absolute() or ".." in destination.parts:
        raise ValueError(
            "--godot-dest must be a non-traversing project-relative or res:// path"
        )
    raw_target_root = project / destination
    _assert_no_godot_symlink_components(raw_target_root, project)
    target_root = raw_target_root.resolve(strict=False)
    try:
        target_root.relative_to(project)
    except ValueError as exc:
        raise ValueError("Godot import destination escaped the project") from exc
    source = Path(src).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Godot import source was not found: {source}")
    name = (
        re.sub(
            r"[^0-9A-Za-z_.-]+",
            "_",
            str(asset_name or source.name),
        ).strip("._")
        or source.name
    )
    if not Path(name).suffix:
        name += source.suffix.lower()
    target = target_root / name
    if target.is_symlink():
        raise ValueError(f"Godot import target must not be a symlink: {target}")
    try:
        target.resolve(strict=False).relative_to(project)
    except ValueError as exc:
        raise ValueError("Godot import target escaped the project") from exc
    if target.exists() and not target.is_file():
        raise ValueError(f"Godot import target must be a file: {target}")
    sidecars = _godot_gltf_sidecars(source, target)
    touched = list(
        dict.fromkeys([target, *(destination for _source, destination in sidecars)])
    )
    for path in touched:
        if path.is_symlink():
            raise ValueError(f"Godot import target must not be a symlink: {path}")
        if path.exists() and not path.is_file():
            raise ValueError(f"Godot import target must be a file: {path}")
    conflicts = [path for path in touched if path.exists()]
    if conflicts and not bool(getattr(args, "godot_replace_existing", False)):
        raise FileExistsError(
            "Godot import target already exists: "
            + ", ".join(str(path) for path in conflicts)
            + "; "
            "pass --godot-replace-existing to replace it"
        )
    resource_path = "res://" + target.relative_to(project).as_posix()
    managed_paths = list(touched)
    managed_paths.extend(Path(str(path) + ".import") for path in touched)
    managed_paths.extend(_godot_import_cache_paths(project, touched))
    managed_paths = list(dict.fromkeys(managed_paths))
    _validate_godot_managed_paths(managed_paths)
    prepared = PreparedGodotAsset(
        target=target,
        resource_path=resource_path,
        asset_type=asset_type,
        project=project,
        import_sources=tuple(touched),
        touched=tuple(managed_paths),
        created_directories=_new_directories(
            [path.parent for path in managed_paths],
            project,
        ),
        dry_run=bool(getattr(args, "dry_run", False)),
    )
    if prepared.dry_run:
        return prepared

    target_root.mkdir(parents=True, exist_ok=True)
    prepared.backup_root, prepared.backups = _backup_godot_targets(managed_paths)
    try:
        shutil.copy2(source, target)
        for sidecar_source, sidecar_destination in sidecars:
            sidecar_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(sidecar_source, sidecar_destination)
    except Exception:
        prepared.rollback()
        raise
    return prepared


def blender_command(
    binary: Path,
    src: str,
    args,
    asset_name: str | None,
    report: Path,
    source_tris: int | None = None,
) -> tuple[list[str], dict]:
    """
    Blender invocation that runs `import_mesh.py` or `import_motion.py`
    with no project and no window.

    Parameters travel in the same JSON job file the UE5 route uses: Blender puts
    everything after a bare `--` into `sys.argv` untouched, but one job shape for
    every engine is worth more than saving a file.

    Returns:
        (command, extra environment)
    """
    importer = _blender_importer(args)
    if getattr(args, "kind", "mesh") == "motion":
        job = {
            "src": src,
            "dest": args.blender_dest or str(report.parent / "blender_library"),
            "name": asset_name,
            "export": args.blender_export,
            "preview": args.blender_preview,
            "report": str(report),
        }
    else:
        job = {
            "src": src,
            "dest": args.blender_dest or str(report.parent / "blender_library"),
            "name": asset_name,
            "usage": args.usage,
            "target_tris": args.target_tris,
            "source_tris": source_tris,
            "pivot": args.pivot,
            "normalize_scale": args.normalize_scale,
            "export": args.blender_export,
            "preview": args.blender_preview,
            "report": str(report),
        }
    job_path = report.with_name(report.stem + "_job.json")
    job_path.parent.mkdir(parents=True, exist_ok=True)
    job_path.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")

    if is_blender_app(binary):
        command = [
            str(binary),
            "--background",
            "--factory-startup",
            "--python",
            str(importer),
        ]
    else:
        command = [str(binary), str(importer)]
    return command, {
        "AAAGF_IMPORT_JOB": str(job_path),
        "AAAGF_BLENDER_EXIT_ON_DONE": "1",
    }


def _quote(value: str) -> str:
    return f'"{value}"' if " " in value else value


def install_unity_editor_script(project: Path) -> Path:
    """
    Copy the importer into `<project>/Assets/Editor/`.

    Unity only compiles editor code that sits in a folder named `Editor`, so the
    file cannot simply be referenced from this repo.
    """
    dest_dir = project / "Assets" / "Editor"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / UNITY_IMPORTER.name
    shutil.copy2(UNITY_IMPORTER, dest)
    return dest


# ── Run ───────────────────────────────────────────────────────────────────────


def run_engine(
    cmd: list[str],
    report_path: Path,
    label: str,
    timeout: int,
    dry_run: bool,
    extra_env: dict | None = None,
    cwd: Path | None = None,
) -> dict:
    """Run one engine invocation and return the report it wrote."""
    printable = " ".join(_quote(c) for c in cmd)
    print(f"\n[{label}] {printable}")
    for key, value in (extra_env or {}).items():
        print(f"[{label}] {key}={value}")
    if dry_run:
        return {"ok": None, "dry_run": True, "command": printable}

    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.exists():
        report_path.unlink()

    env = {**os.environ, **(extra_env or {})}
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            errors="replace",
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        return {
            "ok": False,
            "error": f"{label} timed out after {timeout} seconds",
            "stdout_tail": str(partial)[-2000:],
            "exit_code": None,
            "command": printable,
        }
    except OSError as exc:
        return {
            "ok": False,
            "error": f"{label} could not start: {type(exc).__name__}: {exc}",
            "stdout_tail": "",
            "exit_code": None,
            "command": printable,
        }
    tail = (proc.stdout or "")[-2000:]
    if proc.returncode != 0:
        print(f"[{label}] exit code {proc.returncode}")
        print(tail)

    if report_path.is_file():
        report = read_json(report_path)
    else:
        report = {
            "ok": False,
            "error": f"{label} wrote no report (exit {proc.returncode}); "
            f"see the engine log",
            "stdout_tail": tail,
        }
    report["exit_code"] = proc.returncode
    return report


def run_godot_import(
    godot: Path,
    project: Path,
    prepared: PreparedGodotAsset,
    report_path: Path,
    timeout: int,
    dry_run: bool,
    metadata: dict,
) -> dict:
    """Import and then prove the staged resource through Godot itself."""

    cmd = godot_command(godot, project)
    printable = " ".join(_quote(item) for item in cmd)
    print(f"\n[godot] {printable}")
    if dry_run:
        return {"ok": None, "dry_run": True, "command": printable}

    report: dict = {
        "ok": False,
        "asset_path": prepared.resource_path,
        "engine": "godot",
        "asset_type": prepared.asset_type,
        **dict(metadata),
    }
    try:
        config = GodotClientConfig.resolve(
            project_path=project,
            godot_executable=godot,
            editor_timeout=timeout,
            import_timeout=timeout,
        )
        validate_managed_file(
            config.artifact_registry_path,
            label="Godot artifact registry",
        )
        transport = GodotTransport(config)
        version_process = transport.version(timeout=float(timeout))
        report["version_process"] = version_process.to_dict()
        if version_process.returncode != 0:
            raise RuntimeError(
                "Godot --version failed: "
                + (
                    version_process.stderr.strip()
                    or f"exit {version_process.returncode}"
                )
            )
        engine_version = (
            version_process.stdout.strip().splitlines()[0]
            if version_process.stdout.strip()
            else ""
        )
        report["engine_version"] = engine_version
        version_error = godot_4_version_error(engine_version)
        if version_error:
            raise RuntimeError(version_error)

        process = transport.run(["--import"], timeout=float(timeout))
        report["import_process"] = process.to_dict()
        report["exit_code"] = process.returncode
        if process.returncode != 0:
            raise RuntimeError(
                "Godot resource import failed: "
                + (process.stderr.strip() or f"exit {process.returncode}")
            )
        import_errors = godot_import_error_lines(process.stdout + "\n" + process.stderr)
        if import_errors:
            raise RuntimeError(
                "Godot resource import reported errors despite exit code 0: "
                + " | ".join(import_errors)[-4000:]
            )

        inspection_process, inspection = inspect_godot_resource(
            transport,
            prepared.resource_path,
            timeout=float(timeout),
        )
        report["inspection_process"] = inspection_process.to_dict()
        report["inspection"] = inspection
        if inspection_process.returncode != 0 or not inspection.get("ok"):
            raise RuntimeError(
                str(
                    inspection.get("error")
                    or "Godot could not load the imported resource"
                )
            )
        errors = validate_resource_inspection(
            inspection,
            prepared.asset_type,
        )
        if errors:
            raise RuntimeError("; ".join(errors))
        client = GodotClient(
            project_path=project,
            godot_executable=godot,
            editor_timeout=timeout,
            import_timeout=timeout,
        )
        prepared.begin_registry_transaction(config.artifact_registry_path)
        skeletons = inspection_skeleton_paths(inspection)
        track_skeletons = list(
            dict.fromkeys(
                track["node_path"]
                for track in inspection_bone_tracks(inspection)
                if track.get("targets_skeleton_bone") and track.get("node_path")
            )
        )
        preferred_skeletons = (
            track_skeletons if prepared.asset_type == "motion" else skeletons
        )
        actual_skeleton = preferred_skeletons[0] if preferred_skeletons else ""
        raw_source_path = str(metadata.get("src_path") or "").strip()
        source_path = (
            str(Path(raw_source_path).expanduser().resolve()) if raw_source_path else ""
        )
        record = client.assets._register_resource(
            resource_path=prepared.resource_path,
            asset_type=prepared.asset_type,
            asset_id=prepared.target.stem,
            source_path=source_path,
            backend_class=str(inspection.get("resource_class") or ""),
            spawnable=(
                prepared.asset_type == "prop" and bool(inspection.get("instantiable"))
            ),
            metadata={
                "source": {
                    "kind": "path",
                    "path": source_path,
                    "launcher": "scripts/import_generated_asset.py",
                },
                "destination": prepared.target.parent.relative_to(project).as_posix(),
                "skeleton": actual_skeleton,
                "skeleton_path": actual_skeleton,
                "native_inspection": inspection,
                "options": {
                    "usage": str(metadata.get("usage") or ""),
                    "compatibility_import": True,
                },
            },
        )
        report.update(
            {
                "ok": True,
                "backend_class": str(inspection.get("resource_class") or ""),
                "animations": list(inspection.get("animations") or []),
                "skeletons": skeletons,
                "artifact_id": record.artifact_id,
                "artifact": record.to_dict(),
            }
        )
    except subprocess.TimeoutExpired as exc:
        report["error"] = f"godot timed out after {timeout} seconds"
        report["exit_code"] = None
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        report["stdout_tail"] = str(partial)[-2000:]
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (OSError, TypeError, ValueError) as exc:
        previous_error = str(report.get("error") or "").strip()
        persistence_error = (
            f"Godot import report could not be written: {type(exc).__name__}: {exc}"
        )
        report["ok"] = False
        report["report_path"] = str(report_path)
        report["report_error"] = persistence_error
        report["error"] = (
            f"{previous_error}; {persistence_error}"
            if previous_error
            else persistence_error
        )
    return report


def summarize(label: str, report: dict) -> None:
    if report.get("dry_run"):
        print(f"[{label}] dry run — command printed, engine not launched")
        return
    if report.get("ok"):
        asset = (
            report.get("assetPath") or report.get("asset_path") or report.get("object")
        )
        # Only Unity makes a prefab; naming it for the others reads as a failure.
        prefab = report.get("prefabPath")
        print(
            f"[{label}] OK  asset={asset}  "
            f"{f'prefab={prefab}  ' if prefab else ''}"
            f"tris={report.get('triangles') or report.get('tris')}"
        )
        for kind, path in (report.get("exports") or {}).items():
            print(f"[{label}]   {kind}: {path}")
        if report.get("preview"):
            print(f"[{label}]   preview: {report['preview']}")
    else:
        print(f"[{label}] FAILED  {report.get('error')}")
    for w in report.get("warnings", []):
        print(f"[{label}]   warning: {w}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--src", help="One generated .glb / .fbx")
    src.add_argument("--summary", help="A <kind>_results_summary.json from run.py")

    ap.add_argument(
        "--engine",
        default="both",
        choices=["ue5", "unity", "godot", "blender", "both", "all"],
        help="'both' is UE5 + Unity; 'all' adds Godot and Blender",
    )
    ap.add_argument(
        "--kind",
        default="mesh",
        choices=list(KINDS),
        help=(
            "'mesh' (default) runs the static-prop importers; 'motion' runs "
            "import_motion.py and expects a retargeted .fbx"
        ),
    )
    ap.add_argument("--name", default=None, help="Asset name (single --src only)")
    ap.add_argument(
        "--usage",
        default="asset",
        choices=USAGES,
        help="Import tier; 'asset' imports verbatim (part B4); "
        "'motion' is implied by --kind motion",
    )
    ap.add_argument(
        "--ue-skeleton",
        default=None,
        help="For --kind motion: import animation onto this existing Skeleton",
    )
    ap.add_argument(
        "--ue-anim-only",
        action="store_true",
        help="For --kind motion: do not import a SkeletalMesh (needs --ue-skeleton)",
    )
    ap.add_argument(
        "--ue-motion-dest",
        default="/Game/Generated/Motion",
        help="UE package path for motion imports",
    )
    ap.add_argument(
        "--target-tris",
        type=int,
        default=None,
        help="Advisory triangle budget for mesh particles",
    )
    ap.add_argument(
        "--pivot", default=None, choices=["keep", "center", "bottom", "top"]
    )
    ap.add_argument("--normalize-scale", action="store_true")

    ap.add_argument("--ue-editor", default=None, help="UnrealEditor-Cmd path")
    ap.add_argument("--uproject", default=os.environ.get("AAAGF_UPROJECT"))
    ap.add_argument("--ue-dest", default="/Game/Generated/Meshes")
    ap.add_argument(
        "--ue-mode",
        default=os.environ.get("AAAGF_UE_MODE", "editor"),
        choices=["editor", "commandlet"],
        help="'editor' launches the full editor (Slate exists, so the "
        "post-import Content Browser sync cannot assert); "
        "'commandlet' is faster but crashes on some 5.x builds",
    )
    ap.add_argument(
        "--ue-route",
        default=os.environ.get("AAAGF_UE_IMPORT_ROUTE"),
        choices=["automated", "interchange", "task"],
        help="Force one import API instead of trying them in order",
    )
    ap.add_argument(
        "--ue-extra",
        action="append",
        default=None,
        metavar="ARG",
        help="Extra UE command-line argument, repeatable. Because the "
        "value itself starts with a dash, attach it with '=': "
        "--ue-extra=-EnablePlugins=PythonScriptPlugin  (that one "
        "imports into a project without editing its .uproject)",
    )
    ap.add_argument("--unity", default=None, help="Unity editor binary path")
    ap.add_argument("--unity-project", default=os.environ.get("AAAGF_UNITY_PROJECT"))
    ap.add_argument("--unity-dest", default="Assets/Generated/Meshes")
    ap.add_argument(
        "--no-install-editor-script",
        action="store_true",
        help="Do not copy ImportGeneratedMesh.cs into the Unity project",
    )

    ap.add_argument("--godot", default=None, help="Godot 4 editor binary path")
    ap.add_argument(
        "--godot-project",
        default=(
            os.environ.get("A3GAME_GODOT_PROJECT", "").strip()
            or os.environ.get("AAAGF_GODOT_PROJECT", "").strip()
            or None
        ),
        help="Godot project directory or its project.godot file",
    )
    ap.add_argument(
        "--godot-dest",
        default=None,
        help=(
            "Godot res:// destination (default: assets/imported/props for "
            "mesh, assets/imported/motions for motion)"
        ),
    )
    ap.add_argument("--godot-replace-existing", action="store_true")

    ap.add_argument(
        "--blender", default=None, help="blender(.exe), or a python that can import bpy"
    )
    ap.add_argument(
        "--blender-dest",
        default=None,
        help="Library directory for the conditioned copies "
        "(default: blender_library/ next to the report)",
    )
    ap.add_argument(
        "--blender-export",
        nargs="*",
        default=["glb"],
        choices=["glb", "fbx", "blend"],
        help="Formats Blender writes back out",
    )
    ap.add_argument(
        "--blender-preview",
        action="store_true",
        help="Also render a poster frame of what came in",
    )

    ap.add_argument(
        "--report-dir",
        default=None,
        help="Where engine reports land (default: next to the source)",
    )
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the artifact and print the commands only",
    )
    args = ap.parse_args()

    engines = ENGINE_SETS.get(args.engine, [args.engine])
    sources = sources_from_args(args)

    failures = 0
    for path, asset_name in sources:
        info = validate_source(path)
        print(f"\n=== {path}")
        print(f"    {json.dumps(info, ensure_ascii=False)}")
        if not info.get("exists"):
            failures += 1
            continue
        if info.get("triangles") == 0:
            print("    warning: the 3D source parses but contains no triangles")
        if info.get("error"):
            print(f"    error: {info['error']}")
            failures += 1
            continue

        # Unity is launched with the generated project as cwd so project-
        # relative Assets paths behave like the Editor. Keep host reports
        # absolute so that cwd does not relocate them into the project.
        report_dir = (
            Path(args.report_dir).expanduser().resolve(strict=False)
            if args.report_dir
            else Path(path).parent.resolve(strict=False)
        )
        stem = asset_name or Path(path).stem

        for engine in engines:
            if args.kind == "motion" and engine == "unity":
                print("[unity] --kind motion is not wired yet; use blender or ue5")
                failures += 1
                continue
            extra_env: dict = {}
            godot_prepared: PreparedGodotAsset | None = None
            if engine == "ue5":
                editor = find_unreal_editor(args.ue_editor)
                if not editor or not editor.exists():
                    print(
                        "[ue5] no UnrealEditor-Cmd found — pass --ue-editor or set "
                        "AAAGF_UE_EDITOR"
                    )
                    failures += 1
                    continue
                if not args.uproject:
                    print("[ue5] no project — pass --uproject or set AAAGF_UPROJECT")
                    failures += 1
                    continue
                report_path = report_dir / f"{stem}_ue5_import.json"
                cmd, extra_env = ue_command(
                    editor,
                    Path(args.uproject),
                    path,
                    args,
                    asset_name,
                    report_path,
                    source_tris=info.get("triangles"),
                )
            elif engine == "blender":
                binary = find_blender(args.blender)
                if not binary or not binary.exists():
                    print(
                        "[blender] no Blender found — pass --blender, set "
                        "AAAGF_BLENDER, or `pip install bpy` into this "
                        "environment"
                    )
                    failures += 1
                    continue
                report_path = report_dir / f"{stem}_blender_import.json"
                cmd, extra_env = blender_command(
                    binary,
                    path,
                    args,
                    asset_name,
                    report_path,
                    source_tris=info.get("triangles"),
                )
            elif engine == "godot":
                godot = find_godot(args.godot)
                if not godot or not godot.is_file():
                    print(
                        "[godot] no Godot editor found — pass --godot or set "
                        "A3GAME_GODOT_EXECUTABLE/A3GAME_GODOT/AAAGF_GODOT"
                    )
                    failures += 1
                    continue
                if not args.godot_project:
                    print(
                        "[godot] no project — pass --godot-project or set "
                        "A3GAME_GODOT_PROJECT/AAAGF_GODOT_PROJECT"
                    )
                    failures += 1
                    continue
                project = Path(args.godot_project).expanduser()
                report_path = report_dir / f"{stem}_godot_import.json"
                try:
                    godot_prepared = prepare_godot_asset(
                        project,
                        path,
                        args,
                        asset_name,
                    )
                    project = godot_prepared.project
                except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
                    print(f"[godot] {exc}")
                    failures += 1
                    continue
                godot_metadata = {
                    "src_path": path,
                    "bytes": info.get("bytes"),
                    "triangles": info.get("triangles"),
                    "usage": (
                        "motion"
                        if godot_prepared.asset_type == "motion"
                        else args.usage
                    ),
                    "warnings": [],
                }
            else:
                unity = find_unity(args.unity)
                if not unity or not unity.exists():
                    print(
                        "[unity] no Unity editor found — pass --unity or set AAAGF_UNITY"
                    )
                    failures += 1
                    continue
                if not args.unity_project:
                    print(
                        "[unity] no project — pass --unity-project or set "
                        "AAAGF_UNITY_PROJECT"
                    )
                    failures += 1
                    continue
                project = Path(args.unity_project).expanduser().resolve(strict=False)
                if not args.no_install_editor_script and not args.dry_run:
                    installed = install_unity_editor_script(project)
                    print(f"[unity] editor script → {installed}")
                report_path = report_dir / f"{stem}_unity_import.json"
                cmd = unity_command(unity, project, path, args, asset_name, report_path)

            if godot_prepared is not None:
                try:
                    report = run_godot_import(
                        godot,
                        project,
                        godot_prepared,
                        report_path,
                        args.timeout,
                        args.dry_run,
                        godot_metadata,
                    )
                    summarize(engine, report)
                    if report.get("ok") is True or args.dry_run:
                        godot_prepared.commit()
                    else:
                        failures += 1
                        if report.get("ok") is not False:
                            print("[godot] invalid import report: missing boolean ok")
                except Exception as exc:
                    failures += 1
                    print(
                        "[godot] unexpected import failure: "
                        f"{type(exc).__name__}: {exc}"
                    )
                finally:
                    if not godot_prepared._finalized:
                        try:
                            godot_prepared.rollback()
                        except RuntimeError as exc:
                            failures += 1
                            print(f"[godot] rollback failed: {exc}")
                continue

            report = run_engine(
                cmd,
                report_path,
                engine,
                args.timeout,
                args.dry_run,
                extra_env=extra_env,
                cwd=project if engine == "unity" else None,
            )
            summarize(engine, report)
            if report.get("ok") is False:
                failures += 1

    print(f"\n{'─' * 60}")
    print("FAILED" if failures else "OK", f"({failures} failure(s))")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
