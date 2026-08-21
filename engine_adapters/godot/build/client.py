"""Stable Godot export operations for GodotClient v1."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .._internal import (
    GodotTransport,
    prepare_managed_file,
    validate_managed_file,
)
from ..config import GodotClientConfig
from ..contracts import GodotDiagnostic, GodotOperationResult

EXPORT_MANIFEST_SCHEMA = "gamefactory3a.godot.export_manifest.v2"
EXPORT_OWNERSHIP_KEY_BYTES = 32


def _preset_names(path: Path) -> list[str]:
    if not path.is_file():
        return []
    names = []
    for match in re.finditer(
        r'^\s*name\s*=\s*"((?:[^"\\]|\\.)*)"\s*$',
        path.read_text(encoding="utf-8", errors="replace"),
        flags=re.MULTILINE,
    ):
        raw_name = match.group(1)
        try:
            names.append(json.loads(f'"{raw_name}"'))
        except json.JSONDecodeError:
            names.append(raw_name)
    return names


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _absolute_without_resolving(path: Path) -> Path:
    """Make an export destination absolute while retaining link components."""

    return Path(os.path.abspath(str(path)))


def _same_file_or_path(left: Path, right: Path) -> bool:
    """Recognize lexical/resolved collisions and existing hard links."""

    if left.resolve(strict=False) == right.resolve(strict=False):
        return True
    try:
        return left.exists() and right.exists() and os.path.samefile(left, right)
    except OSError:
        return False


def _validate_export_destination(
    raw_output: Path,
    output: Path,
    protected_inputs: Sequence[tuple[str, Path]],
) -> None:
    """Reject destinations that could replace inputs or write through links."""

    current = Path(raw_output.anchor)
    for part in raw_output.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError(
                f"Godot export output_path must not contain a symlink: {current}"
            )
        if current == raw_output:
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(
                    "Godot export output_path must be a regular file or directory "
                    f"when it already exists: {current}"
                )
        elif not stat.S_ISDIR(mode):
            raise NotADirectoryError(
                f"Godot export output parent must be a directory: {current}"
            )

    for label, protected in protected_inputs:
        if _same_file_or_path(output, protected):
            raise ValueError(
                "Godot export output_path must not replace protected input "
                f"{label}: {protected}"
            )


def _export_manifest_path(output: Path) -> Path:
    digest = hashlib.sha256(output.name.encode("utf-8")).hexdigest()[:16]
    return output.parent / f".a3game-godot-export-{digest}.json"


def _export_ownership_key_path(config: GodotClientConfig) -> Path:
    return config.data_root / "build" / "export-ownership.key"


def _stat_identity(path: Path) -> tuple[int, int, int, int, int, int]:
    metadata = path.lstat()
    return (
        metadata.st_mode,
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _hash_field(digest: Any, value: str) -> None:
    encoded = value.encode("utf-8", errors="surrogateescape")
    digest.update(len(encoded).to_bytes(8, byteorder="big"))
    digest.update(encoded)


def _update_path_digest(digest: Any, path: Path, relative: str) -> None:
    before = _stat_identity(path)
    mode = before[0]
    _hash_field(digest, relative)
    if stat.S_ISREG(mode):
        digest.update(b"file\0")
        digest.update(before[3].to_bytes(8, byteorder="big"))
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    elif stat.S_ISDIR(mode):
        digest.update(b"directory\0")
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            child_relative = f"{relative}/{child.name}" if relative else child.name
            _update_path_digest(digest, child, child_relative)
    elif stat.S_ISLNK(mode):
        raise ValueError(
            f"Godot managed export contains a symlink and cannot be committed: {path}"
        )
    else:
        raise ValueError(
            f"Godot managed export contains an unsupported filesystem node: {path}"
        )
    if _stat_identity(path) != before:
        raise RuntimeError(f"Godot managed export changed while inspected: {path}")


def _path_ownership_proof(path: Path) -> tuple[str, str]:
    mode = path.lstat().st_mode
    if stat.S_ISREG(mode):
        kind = "file"
    elif stat.S_ISDIR(mode):
        kind = "directory"
    else:
        raise ValueError(
            f"Godot managed export path must be a regular file or directory: {path}"
        )
    digest = hashlib.sha256()
    _update_path_digest(digest, path, "")
    return kind, digest.hexdigest()


def _canonical_manifest(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8", errors="surrogateescape")


def _load_ownership_key(path: Path, *, create: bool) -> bytes:
    path = validate_managed_file(path, label="Godot export ownership key")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        mode = None
    if mode is not None and not stat.S_ISREG(mode):
        raise ValueError(f"Godot export ownership key must be a regular file: {path}")
    if mode is None and create:
        path = prepare_managed_file(path, label="Godot export ownership key")
        key = secrets.token_bytes(EXPORT_OWNERSHIP_KEY_BYTES)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        try:
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError:
            pass
        else:
            try:
                os.write(descriptor, key)
            finally:
                os.close(descriptor)
    validate_managed_file(path, label="Godot export ownership key")
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError as exc:
        raise ValueError(f"Godot export ownership key is missing: {path}") from exc
    except OSError as exc:
        raise ValueError(f"Godot export ownership key is invalid: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(
                f"Godot export ownership key must be a regular file: {path}"
            )
        if os.name == "posix" and (
            metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise ValueError(
                "Godot export ownership key must be owned by the current user "
                f"and accessible only to that user: {path}"
            )
        key = os.read(descriptor, EXPORT_OWNERSHIP_KEY_BYTES + 1)
    finally:
        os.close(descriptor)
    if len(key) != EXPORT_OWNERSHIP_KEY_BYTES:
        raise ValueError(f"Godot export ownership key is invalid: {path}")
    return key


def _manifest_signature(payload: Mapping[str, Any], key: bytes) -> str:
    return hmac.new(key, _canonical_manifest(payload), hashlib.sha256).hexdigest()


def _verify_owned_path(path: Path, expected: tuple[str, str]) -> None:
    actual = _path_ownership_proof(path)
    if not hmac.compare_digest(actual[0], expected[0]) or not hmac.compare_digest(
        actual[1], expected[1]
    ):
        raise ValueError(
            f"Godot export ownership proof does not match the current path: {path}"
        )


def _managed_export_group(
    output: Path,
    protected_inputs: Sequence[tuple[str, Path]],
    ownership_key_path: Path,
) -> tuple[dict[Path, tuple[str, str]], bytes | None]:
    """Return only outputs proven to belong to an earlier adapter export."""

    manifest = _export_manifest_path(output)
    if not manifest.exists() and not manifest.is_symlink():
        if output.exists() or output.is_symlink():
            raise FileExistsError(
                "Godot export refuses to replace an existing output without "
                f"an adapter ownership manifest: {output}"
            )
        return {}, None
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError(f"Godot export manifest must be a regular file: {manifest}")
    for label, protected in protected_inputs:
        if _same_file_or_path(manifest, protected):
            raise ValueError(
                "Godot export manifest must not replace protected input "
                f"{label}: {protected}"
            )
    try:
        manifest_bytes = manifest.read_bytes()
        payload = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Godot export manifest is invalid: {manifest}") from exc
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "schema_version",
            "output_path",
            "output_name",
            "produced",
            "signature",
        }
        or payload.get("schema_version") != EXPORT_MANIFEST_SCHEMA
        or payload.get("output_path") != str(output)
        or payload.get("output_name") != output.name
        or not isinstance(payload.get("produced"), list)
        or not isinstance(payload.get("signature"), str)
    ):
        raise ValueError(f"Godot export manifest is invalid: {manifest}")
    signed_payload = {
        key: value for key, value in payload.items() if key != "signature"
    }
    ownership_key = _load_ownership_key(ownership_key_path, create=False)
    expected_signature = _manifest_signature(signed_payload, ownership_key)
    if not hmac.compare_digest(payload["signature"], expected_signature):
        raise ValueError(f"Godot export manifest signature is invalid: {manifest}")
    produced = payload["produced"]
    names = [item.get("name") for item in produced if isinstance(item, dict)]
    if output.name not in names:
        raise ValueError(
            f"Godot export manifest does not own {output.name}: {manifest}"
        )
    previous: dict[Path, tuple[str, str]] = {}
    seen_names: set[str] = set()
    for record in produced:
        if not isinstance(record, dict) or set(record) != {"kind", "name", "sha256"}:
            raise ValueError(f"Godot export manifest is invalid: {manifest}")
        name = record["name"]
        kind = record["kind"]
        fingerprint = record["sha256"]
        if (
            not isinstance(name, str)
            or not name
            or name in {".", ".."}
            or Path(name).name != name
            or name == manifest.name
            or name in seen_names
            or kind not in {"file", "directory"}
            or not isinstance(fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
        ):
            raise ValueError(f"Godot export manifest has an unsafe entry: {manifest}")
        seen_names.add(name)
        item = output.parent / name
        if item.exists() or item.is_symlink():
            if item.is_symlink():
                raise ValueError(
                    f"Godot managed export path must not be a symlink: {item}"
                )
            mode = item.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ValueError(
                    "Godot managed export path must be a regular file or "
                    f"directory: {item}"
                )
            for label, protected in protected_inputs:
                if _same_file_or_path(item, protected):
                    raise ValueError(
                        "Godot managed export path must not replace protected "
                        f"input {label}: {protected}"
                    )
            proof = (kind, fingerprint)
            _verify_owned_path(item, proof)
            previous[item] = proof
    manifest_proof = _path_ownership_proof(manifest)
    if manifest.read_bytes() != manifest_bytes:
        raise RuntimeError(f"Godot export manifest changed while inspected: {manifest}")
    previous[manifest] = manifest_proof
    return previous, ownership_key


def _validate_staged_export_targets(
    staging_root: Path,
    destination: Path,
    previous_outputs: Mapping[Path, tuple[str, str]],
    protected_inputs: Sequence[tuple[str, Path]],
) -> None:
    """Ensure a staged export can replace only adapter-owned destinations."""

    managed = set(previous_outputs)
    for source in staging_root.iterdir():
        target = destination / source.name
        for label, protected in protected_inputs:
            if _same_file_or_path(target, protected):
                raise ValueError(
                    "Godot staged export must not replace protected input "
                    f"{label}: {protected}"
                )
        if not target.exists() and not target.is_symlink():
            continue
        if target.is_symlink():
            raise ValueError(
                f"Godot staged export target must not be a symlink: {target}"
            )
        mode = target.lstat().st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError(
                "Godot staged export target must be a regular file or "
                f"directory: {target}"
            )
        if target not in managed:
            raise FileExistsError(
                f"Godot export refuses to replace an unmanaged existing path: {target}"
            )


def _stage_export_manifest(
    staging_root: Path,
    output: Path,
    ownership_key: bytes,
) -> Path:
    manifest = _export_manifest_path(output)
    staged_manifest = staging_root / manifest.name
    if staged_manifest.exists() or staged_manifest.is_symlink():
        raise FileExistsError(
            f"Godot export produced the reserved manifest name: {staged_manifest.name}"
        )
    produced = []
    for item in sorted(staging_root.iterdir(), key=lambda path: path.name):
        kind, fingerprint = _path_ownership_proof(item)
        produced.append({"kind": kind, "name": item.name, "sha256": fingerprint})
    payload: dict[str, Any] = {
        "schema_version": EXPORT_MANIFEST_SCHEMA,
        "output_path": str(output),
        "output_name": output.name,
        "produced": produced,
    }
    payload["signature"] = _manifest_signature(payload, ownership_key)
    staged_manifest.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _commit_staged_export(
    staging_root: Path,
    destination: Path,
    previous_outputs: Mapping[Path, tuple[str, str]],
    protected_inputs: Sequence[tuple[str, Path]],
) -> tuple[list[Path], str, str]:
    """Replace the previous sibling set and roll back the whole group."""

    _validate_staged_export_targets(
        staging_root,
        destination,
        previous_outputs,
        protected_inputs,
    )
    staged = sorted(staging_root.iterdir(), key=lambda item: item.name)
    backup_root = Path(
        tempfile.mkdtemp(
            prefix=".a3game-godot-export-backup-",
            dir=str(destination),
        )
    )
    backups: dict[Path, Path] = {}
    installed: list[Path] = []
    try:
        for index, (target, proof) in enumerate(previous_outputs.items()):
            if target.exists() or target.is_symlink():
                _verify_owned_path(target, proof)
                backup = backup_root / str(index)
                shutil.move(str(target), str(backup))
                _verify_owned_path(backup, proof)
                backups[target] = backup
        for source in staged:
            target = destination / source.name
            if target.exists() or target.is_symlink():
                raise FileExistsError(
                    "Godot export target appeared during commit and was not "
                    f"replaced: {target}"
                )
            shutil.move(str(source), str(target))
            installed.append(target)
    except Exception as exc:
        recovery_errors = []
        for target in reversed(installed):
            try:
                _remove_path(target)
            except Exception as recovery_exc:
                recovery_errors.append(
                    f"remove {target}: {type(recovery_exc).__name__}: {recovery_exc}"
                )
        for target, backup in backups.items():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if backup.exists() or backup.is_symlink():
                    if target.exists() or target.is_symlink():
                        raise FileExistsError(
                            "restore target is occupied; original retained at "
                            f"{backup}: {target}"
                        )
                    shutil.move(str(backup), str(target))
            except Exception as recovery_exc:
                recovery_errors.append(
                    f"restore {target}: {type(recovery_exc).__name__}: {recovery_exc}"
                )
        recovery_error = "; ".join(recovery_errors)
        if recovery_error:
            recovery_error += f"; backups retained at {backup_root}"
        else:
            shutil.rmtree(backup_root, ignore_errors=True)
        return [], f"{type(exc).__name__}: {exc}", recovery_error
    else:
        shutil.rmtree(backup_root, ignore_errors=True)
        return installed, "", ""


class GodotBuildClient:
    def __init__(self, config: GodotClientConfig) -> None:
        self._config = config
        self._transport = GodotTransport(config)

    def project(
        self,
        *,
        preset: str,
        output_path: str | Path,
        debug: bool = False,
        pack_only: bool = False,
        extra_args: Sequence[str] = (),
        allow_external_output: bool = False,
        timeout: float | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        operation = "build.project"
        project_dir = self._config.project_dir
        project_file = self._config.project_file
        if project_dir is None or project_file is None or not project_file.is_file():
            return GodotOperationResult.failure(
                operation,
                "project_path does not resolve to an existing project.godot",
            ).to_dict()
        preset_name = str(preset or "").strip()
        if not preset_name:
            return GodotOperationResult.failure(
                operation, "preset is required"
            ).to_dict()
        presets_path = project_dir / "export_presets.cfg"
        presets = _preset_names(presets_path)
        if preset_name not in presets:
            return GodotOperationResult.failure(
                operation,
                f"Export preset {preset_name!r} was not found in {presets_path}; "
                f"available: {', '.join(presets) or '(none)'}",
            ).to_dict()
        output = Path(output_path).expanduser()
        if not output.is_absolute():
            output = project_dir / output
        raw_output = _absolute_without_resolving(output)
        output = raw_output.resolve(strict=False)
        if not allow_external_output:
            try:
                output.relative_to(project_dir.resolve())
            except ValueError:
                return GodotOperationResult.failure(
                    operation,
                    "output_path must stay inside the Godot project unless "
                    "allow_external_output=True",
                    payload={"output_path": str(output)},
                ).to_dict()
        protected_inputs = [
            ("project.godot", project_file),
            ("export_presets.cfg", presets_path),
        ]
        ownership_key_path = _export_ownership_key_path(self._config)
        protected_inputs.append(("export ownership key", ownership_key_path))
        try:
            validate_managed_file(
                ownership_key_path,
                label="Godot export ownership key",
            )
            _validate_export_destination(raw_output, output, protected_inputs)
            previous_outputs, _ = _managed_export_group(
                output,
                protected_inputs,
                ownership_key_path,
            )
        except Exception as exc:
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload={"output_path": str(output)},
            ).to_dict()
        mode = (
            "--export-pack"
            if pack_only
            else ("--export-debug" if debug else "--export-release")
        )
        try:
            if extra_args is None or isinstance(extra_args, (str, bytes, bytearray)):
                raise TypeError("extra_args must be a sequence of arguments")
            normalized_args = [str(item) for item in extra_args]
            arguments = [mode, preset_name, str(output), *normalized_args]
            command = self._transport.command(arguments)
        except Exception as exc:
            return GodotOperationResult.failure(
                operation, f"{type(exc).__name__}: {exc}"
            ).to_dict()
        payload: dict[str, Any] = {
            "preset": preset_name,
            "output_path": str(output),
            "debug": debug,
            "pack_only": pack_only,
            "command": command,
            "cwd": str(project_dir),
            "dry_run": dry_run,
        }
        if dry_run:
            return GodotOperationResult.success(operation, payload=payload).to_dict()
        staging_root: Path | None = None
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            staging_root = Path(
                tempfile.mkdtemp(
                    prefix=".a3game-godot-export-stage-",
                    dir=str(output.parent),
                )
            )
            staged_output = staging_root / output.name
            staged_arguments = [
                mode,
                preset_name,
                str(staged_output),
                *normalized_args,
            ]
            result = self._transport.run(
                staged_arguments,
                timeout=timeout or float(self._config.editor_timeout),
            )
        except Exception as exc:
            payload["rollback"] = {
                "strategy": "isolated_staging",
                "restored_previous_output": bool(previous_outputs),
                "error": "",
            }
            if staging_root is not None:
                shutil.rmtree(staging_root, ignore_errors=True)
            return GodotOperationResult.failure(
                operation,
                f"{type(exc).__name__}: {exc}",
                payload=payload,
            ).to_dict()
        payload.update(result.to_dict())
        diagnostics = self._diagnostics(result.stderr + "\n" + result.stdout)
        if result.returncode != 0:
            payload["rollback"] = {
                "strategy": "isolated_staging",
                "restored_previous_output": bool(previous_outputs),
                "error": "",
            }
            shutil.rmtree(staging_root, ignore_errors=True)
            return GodotOperationResult.failure(
                operation,
                f"Godot export failed with exit code {result.returncode}",
                diagnostics=diagnostics,
                payload=payload,
            ).to_dict()
        if not staged_output.exists() and not staged_output.is_symlink():
            payload["rollback"] = {
                "strategy": "isolated_staging",
                "restored_previous_output": bool(previous_outputs),
                "error": "",
            }
            shutil.rmtree(staging_root, ignore_errors=True)
            return GodotOperationResult.failure(
                operation,
                "Godot returned success but the requested export artifact was not created",
                diagnostics=diagnostics,
                payload=payload,
            ).to_dict()
        try:
            _validate_export_destination(raw_output, output, protected_inputs)
            previous_outputs, ownership_key = _managed_export_group(
                output,
                protected_inputs,
                ownership_key_path,
            )
            if ownership_key is None:
                ownership_key = _load_ownership_key(
                    ownership_key_path,
                    create=True,
                )
            manifest_path = _stage_export_manifest(
                staging_root,
                output,
                ownership_key,
            )
            if not hmac.compare_digest(
                ownership_key,
                _load_ownership_key(ownership_key_path, create=False),
            ):
                raise RuntimeError(
                    "Godot export ownership key changed while preparing commit"
                )
            _validate_staged_export_targets(
                staging_root,
                output.parent,
                previous_outputs,
                protected_inputs,
            )
            produced, commit_error, recovery_error = _commit_staged_export(
                staging_root,
                output.parent,
                previous_outputs,
                protected_inputs,
            )
        except Exception as exc:
            shutil.rmtree(staging_root, ignore_errors=True)
            payload["rollback"] = {
                "strategy": "isolated_staging",
                "restored_previous_output": bool(previous_outputs),
                "error": "",
            }
            return GodotOperationResult.failure(
                operation,
                f"Godot export commit could not start: {type(exc).__name__}: {exc}",
                diagnostics=diagnostics,
                payload=payload,
            ).to_dict()
        shutil.rmtree(staging_root, ignore_errors=True)
        if commit_error:
            payload["rollback"] = {
                "strategy": "staged_group_commit",
                "restored_previous_output": bool(previous_outputs)
                and not recovery_error,
                "error": recovery_error,
            }
            return GodotOperationResult.failure(
                operation,
                f"Godot export commit failed: {commit_error}"
                + (
                    f"; output rollback failed: {recovery_error}"
                    if recovery_error
                    else ""
                ),
                diagnostics=diagnostics,
                payload=payload,
            ).to_dict()
        produced = [path for path in produced if path != manifest_path]
        payload["export_manifest_path"] = str(manifest_path)
        payload["produced_paths"] = [str(path) for path in produced]
        return GodotOperationResult.success(
            operation,
            artifacts=[
                {
                    "type": "godot_export",
                    "path": str(output),
                    "state": "ready",
                    "preset": preset_name,
                }
            ],
            diagnostics=diagnostics,
            payload=payload,
        ).to_dict()

    @staticmethod
    def _diagnostics(output: str) -> list[GodotDiagnostic]:
        diagnostics = []
        for line in output.splitlines():
            stripped = line.strip()
            lowered = stripped.lower()
            if "error:" in lowered or lowered.startswith("error"):
                diagnostics.append(
                    GodotDiagnostic(
                        severity="error",
                        code="GODOT_EXPORT_ERROR",
                        message=stripped[-1000:],
                        source="godot",
                    )
                )
            elif "warning:" in lowered or lowered.startswith("warning"):
                diagnostics.append(
                    GodotDiagnostic(
                        severity="warning",
                        code="GODOT_EXPORT_WARNING",
                        message=stripped[-1000:],
                        source="godot",
                    )
                )
        return diagnostics[-100:]
