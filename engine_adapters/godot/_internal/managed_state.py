"""Filesystem primitives for adapter-managed persistent state."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def unresolved_absolute_path(path: str | Path) -> Path:
    """Return an absolute path without following symbolic-link components."""

    return Path(os.path.abspath(str(Path(path).expanduser())))


def _validate_managed_node(
    path: str | Path,
    *,
    label: str,
    target_kind: str,
) -> Path:
    """Reject links and unexpected nodes in a managed path's complete chain."""

    target = unresolved_absolute_path(path)
    current = Path(target.anchor)
    components = [current]
    for part in target.parts[1:]:
        current = current / part
        components.append(current)
    for current in components:
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            if current == target and target_kind == "file":
                raise ValueError(
                    f"{label} must be a regular file, not a link or special "
                    f"node: {current}"
                )
            raise ValueError(f"{label} path must not contain a symlink: {current}")
        if current == target:
            valid = stat.S_ISREG(mode) if target_kind == "file" else stat.S_ISDIR(mode)
            if not valid:
                raise ValueError(f"{label} must be a regular {target_kind}: {current}")
        elif not stat.S_ISDIR(mode):
            raise NotADirectoryError(f"{label} parent must be a directory: {current}")
    return target


def validate_managed_file(path: str | Path, *, label: str) -> Path:
    """Validate a managed file and every existing parent without following links."""

    return _validate_managed_node(path, label=label, target_kind="file")


def validate_managed_directory(path: str | Path, *, label: str) -> Path:
    """Validate a managed directory and every existing parent."""

    return _validate_managed_node(path, label=label, target_kind="directory")


def prepare_managed_file(path: str | Path, *, label: str) -> Path:
    """Create a managed file's parent hierarchy and validate it again."""

    target = validate_managed_file(path, label=label)
    target.parent.mkdir(parents=True, exist_ok=True)
    return validate_managed_file(target, label=label)


def read_managed_text(
    path: str | Path,
    *,
    label: str,
    encoding: str = "utf-8",
) -> str:
    """Read one validated regular file without following its final node."""

    target = validate_managed_file(path, label=label)
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{label} must be a regular file: {target}")
        with os.fdopen(descriptor, "r", encoding=encoding) as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def read_managed_bytes(
    path: str | Path,
    *,
    label: str,
) -> bytes:
    """Read one validated regular file without following its final node."""

    target = validate_managed_file(path, label=label)
    flags = os.O_RDONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError(f"{label} must be a regular file: {target}")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def atomic_write_bytes(
    path: str | Path,
    content: bytes,
    *,
    label: str,
) -> Path:
    """Atomically replace a managed file after validating its path twice."""

    target = prepare_managed_file(path, label=label)
    try:
        existing_mode = stat.S_IMODE(target.lstat().st_mode)
    except FileNotFoundError:
        existing_mode = None
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.stem}.",
        suffix=target.suffix or ".tmp",
        dir=str(target.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            os.chmod(temporary, existing_mode)
        if not stat.S_ISREG(temporary.lstat().st_mode):
            raise ValueError(f"{label} staging path must be a regular file")
        validate_managed_file(target, label=label)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def atomic_write_text(
    path: str | Path,
    content: str,
    *,
    label: str,
    encoding: str = "utf-8",
) -> Path:
    """Encode and atomically replace a managed text file."""

    return atomic_write_bytes(
        path,
        content.encode(encoding),
        label=label,
    )
