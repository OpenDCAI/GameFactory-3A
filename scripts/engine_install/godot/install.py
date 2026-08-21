#!/usr/bin/env python3
"""Install a pinned official Godot 4 editor build without third-party packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


DEFAULT_VERSION = "4.5.1"
RELEASE_CHANNEL = "stable"
RELEASE_BASE_URL = "https://github.com/godotengine/godot/releases/download"
CHECKSUM_ASSET = "SHA512-SUMS.txt"
INSTALL_SCHEMA = "gamefactory3a.godot.install.v1"
_VERSION_PATTERN = re.compile(r"(?:v)?(?P<version>\d+\.\d+(?:\.\d+)?)")


class InstallError(RuntimeError):
    """An installation failure safe to show to an automation caller."""


@dataclass(frozen=True)
class Target:
    system: str
    architecture: str
    asset_label: str
    executable_template: str

    @property
    def key(self) -> str:
        return f"{self.system.lower()}-{self.architecture}"

    def asset_name(self, tag: str) -> str:
        return f"Godot_v{tag}_{self.asset_label}.zip"

    def executable_relative(self, tag: str) -> Path:
        return Path(self.executable_template.format(tag=tag))


def normalize_version(value: str) -> str:
    raw = str(value or "").strip()
    if raw.startswith("v"):
        raw = raw[1:]
    if raw.endswith("-stable"):
        raw = raw[: -len("-stable")]
    if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", raw):
        raise InstallError(
            "Godot version must be an exact stable version such as 4.5.1"
        )
    if int(raw.split(".", 1)[0]) != 4:
        raise InstallError("This adapter supports Godot 4.x only")
    return raw


def release_tag(version: str) -> str:
    return f"{normalize_version(version)}-{RELEASE_CHANNEL}"


def resolve_target(
    system: str | None = None,
    machine: str | None = None,
) -> Target:
    resolved_system = str(system or platform.system()).strip()
    resolved_machine = str(machine or platform.machine()).strip().lower()
    aliases = {
        "amd64": "x86_64",
        "x64": "x86_64",
        "i386": "x86_32",
        "i486": "x86_32",
        "i586": "x86_32",
        "i686": "x86_32",
        "x86": "x86_32",
        "aarch64": "arm64",
        "armv8": "arm64",
        "armv7l": "arm32",
        "armv7": "arm32",
    }
    architecture = aliases.get(resolved_machine, resolved_machine)
    if resolved_system == "Linux":
        labels = {
            "x86_64": "linux.x86_64",
            "x86_32": "linux.x86_32",
            "arm64": "linux.arm64",
            "arm32": "linux.arm32",
        }
        if architecture not in labels:
            raise InstallError(f"Unsupported Linux architecture: {resolved_machine}")
        label = labels[architecture]
        return Target(
            "Linux",
            architecture,
            label,
            "Godot_v{tag}_" + label,
        )
    if resolved_system == "Darwin":
        if architecture not in {"x86_64", "arm64"}:
            raise InstallError(f"Unsupported macOS architecture: {resolved_machine}")
        return Target(
            "Darwin",
            architecture,
            "macos.universal",
            "Godot.app/Contents/MacOS/Godot",
        )
    if resolved_system == "Windows":
        labels = {
            "x86_64": "win64.exe",
            "x86_32": "win32.exe",
            "arm64": "windows_arm64.exe",
        }
        if architecture not in labels:
            raise InstallError(f"Unsupported Windows architecture: {resolved_machine}")
        label = labels[architecture]
        return Target(
            "Windows",
            architecture,
            label,
            "Godot_v{tag}_" + label,
        )
    raise InstallError(
        f"Unsupported operating system: {resolved_system or '<empty>'}; "
        "supported systems are Linux, macOS, and Windows"
    )


def _default_install_root(system: str) -> Path:
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "A3Game" / "Godot"
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(data_home) if data_home else Path.home() / ".local" / "share"
    return base / "a3game" / "godot"


def _default_cache_root(system: str) -> Path:
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "A3Game" / "Cache" / "Godot"
    cache_home = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(cache_home) if cache_home else Path.home() / ".cache"
    return base / "a3game" / "godot"


def _default_bin_dir(system: str) -> Path:
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA", "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "A3Game" / "bin"
    return Path.home() / ".local" / "bin"


def _download(url: str, destination: Path, timeout: float = 120.0) -> None:
    if not url.startswith("https://"):
        raise InstallError(f"Refusing non-HTTPS download URL: {url}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "GameFactory-3A-Godot-Installer/1"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status_code = getattr(response, "status", 200)
            if status_code != 200:
                raise InstallError(f"Download returned HTTP {status_code}: {url}")
            with partial.open("wb") as stream:
                shutil.copyfileobj(response, stream, length=1024 * 1024)
                stream.flush()
                os.fsync(stream.fileno())
        if not partial.is_file() or partial.stat().st_size <= 0:
            raise InstallError(f"Download was empty: {url}")
        os.replace(str(partial), str(destination))
    except (OSError, urllib.error.URLError) as exc:
        raise InstallError(f"Download failed for {url}: {exc}") from exc
    finally:
        try:
            partial.unlink()
        except FileNotFoundError:
            pass


def parse_sha512_sums(contents: str, asset_name: str) -> str:
    matches = []
    for raw_line in contents.splitlines():
        line = raw_line.strip()
        match = re.fullmatch(r"([0-9A-Fa-f]{128})\s+\*?(.+)", line)
        if match and match.group(2).strip() == asset_name:
            matches.append(match.group(1).lower())
    if len(matches) != 1:
        raise InstallError(
            f"Official {CHECKSUM_ASSET} must contain exactly one entry for "
            f"{asset_name}; found {len(matches)}"
        )
    return matches[0]


def sha512_file(path: Path) -> str:
    digest = hashlib.sha512()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_zip_members(archive: zipfile.ZipFile) -> Iterable[zipfile.ZipInfo]:
    seen = set()
    for member in archive.infolist():
        raw = member.filename.replace("\\", "/")
        relative = PurePosixPath(raw)
        if (
            not raw
            or raw.startswith("/")
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise InstallError(f"Archive contains an unsafe path: {member.filename!r}")
        normalized = relative.as_posix()
        if normalized in seen:
            raise InstallError(f"Archive contains a duplicate path: {normalized}")
        seen.add(normalized)
        mode = member.external_attr >> 16
        file_type = stat.S_IFMT(mode)
        if file_type == stat.S_IFLNK:
            raise InstallError(f"Archive contains a symbolic link: {normalized}")
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise InstallError(f"Archive contains a special node: {normalized}")
        yield member


def extract_zip_safely(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve(strict=True)
    try:
        with zipfile.ZipFile(str(archive_path)) as archive:
            members = list(_safe_zip_members(archive))
            for member in members:
                relative = PurePosixPath(member.filename.replace("\\", "/"))
                target = root.joinpath(*relative.parts)
                resolved = target.resolve(strict=False)
                try:
                    resolved.relative_to(root)
                except ValueError as exc:
                    raise InstallError(
                        f"Archive path escaped extraction root: {member.filename!r}"
                    ) from exc
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member, "r") as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                archived_mode = member.external_attr >> 16
                permissions = stat.S_IMODE(archived_mode)
                if permissions:
                    target.chmod(permissions)
    except (OSError, zipfile.BadZipFile) as exc:
        raise InstallError(f"Could not extract {archive_path}: {exc}") from exc


def probe_executable(executable: Path, version: str) -> str:
    if not executable.is_file():
        raise InstallError(f"Godot executable was not found: {executable}")
    if os.name != "nt" and not os.access(str(executable), os.X_OK):
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    try:
        process = subprocess.run(
            [str(executable), "--headless", "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InstallError(f"Godot post-install version probe failed: {exc}") from exc
    output = (process.stdout or process.stderr).strip()
    if process.returncode != 0:
        raise InstallError(
            "Godot post-install version probe exited with status "
            f"{process.returncode}: {output[-1000:]}"
        )
    match = _VERSION_PATTERN.search(output)
    if match is None or match.group("version") != version:
        raise InstallError(
            f"Godot version mismatch: requested {version}, executable reported "
            f"{output or '<empty>'}"
        )
    return output


def _candidate_executables(explicit: str) -> Iterable[Path]:
    seen = set()
    raw_candidates = [
        explicit,
        os.environ.get("A3GAME_GODOT_EXECUTABLE", ""),
        os.environ.get("A3GAME_GODOT", ""),
        os.environ.get("AAAGF_GODOT", ""),
        shutil.which("godot4") or "",
        shutil.which("godot") or "",
        shutil.which("godot-mono") or "",
    ]
    for raw in raw_candidates:
        value = str(raw or "").strip()
        if not value:
            continue
        path = Path(value).expanduser().resolve(strict=False)
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            yield path


def _shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _write_configuration(
    config_dir: Path,
    target: Target,
    tag: str,
    result: dict[str, Any],
) -> list[str]:
    config_dir.mkdir(parents=True, exist_ok=True)
    stem = f"godot-{tag}-{target.key}"
    json_path = config_dir / f"{stem}.json"
    executable = str(result["executable"])
    payload = {"schema_version": INSTALL_SCHEMA, **result}
    json_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    created = [str(json_path)]
    if target.system == "Windows":
        env_path = config_dir / f"{stem}.cmd"
        commands = (
            "@echo off\r\n"
            + f'set "A3GAME_GODOT_EXECUTABLE={executable}"\r\n'
        )
        if result["path_shim"]:
            commands += f'set "PATH={result["bin_dir"]};%PATH%"\r\n'
        env_path.write_text(commands, encoding="utf-8")
    else:
        env_path = config_dir / f"{stem}.env"
        commands = (
            "# Source this file to configure the current shell.\n"
            + "export A3GAME_GODOT_EXECUTABLE="
            + _shell_single_quote(executable)
            + "\n"
        )
        if result["path_shim"]:
            commands += (
                "export PATH="
                + _shell_single_quote(str(result["bin_dir"]))
                + ':"$PATH"\n'
            )
        env_path.write_text(commands, encoding="utf-8")
    created.append(str(env_path))
    return created


def _create_path_shim(
    executable: Path,
    bin_dir: Path,
    target: Target,
    *,
    force: bool,
) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / ("godot4.cmd" if target.system == "Windows" else "godot4")
    windows_contents = (
        ("@echo off\r\n" + f'"{executable}" %*\r\n').encode("utf-8")
        if target.system == "Windows"
        else b""
    )
    if shim.exists() or shim.is_symlink():
        if (
            target.system == "Windows"
            and shim.is_file()
            and not shim.is_symlink()
        ):
            try:
                if shim.read_bytes() == windows_contents:
                    return shim
            except OSError:
                pass
        if target.system != "Windows" and shim.is_symlink():
            if shim.resolve(strict=False) == executable.resolve(strict=False):
                return shim
        if not force:
            raise InstallError(
                f"PATH shim already exists and is not owned by this install: {shim}; "
                "choose --bin-dir, use --no-path-shim, or pass --force"
            )
        if shim.is_dir() and not shim.is_symlink():
            raise InstallError(f"Refusing to replace directory PATH shim: {shim}")
        shim.unlink()
    if target.system == "Windows":
        shim.write_bytes(windows_contents)
    else:
        shim.symlink_to(executable)
    return shim


def _publish_install_tree(staging: Path, install_dir: Path) -> None:
    """Publish a verified staging tree, restoring the prior target on failure."""

    previous = None
    if install_dir.exists() or install_dir.is_symlink():
        if install_dir.is_symlink() or not install_dir.is_dir():
            raise InstallError(
                f"Refusing to replace non-directory install target: {install_dir}"
            )
        previous = Path(
            tempfile.mkdtemp(
                prefix=f".{install_dir.name}-previous-",
                dir=str(install_dir.parent),
            )
        )
        previous.rmdir()
        try:
            os.replace(str(install_dir), str(previous))
        except OSError as exc:
            raise InstallError(
                f"Could not preserve existing Godot install {install_dir}: {exc}"
            ) from exc
    try:
        os.replace(str(staging), str(install_dir))
    except OSError as publish_error:
        if previous is not None and previous.exists():
            try:
                os.replace(str(previous), str(install_dir))
            except OSError as restore_error:
                raise InstallError(
                    "Godot install publication and rollback both failed; "
                    f"preserved install remains at {previous}: {restore_error}"
                ) from publish_error
        raise InstallError(
            f"Could not publish verified Godot install {install_dir}: {publish_error}"
        ) from publish_error
    if previous is not None:
        try:
            shutil.rmtree(str(previous))
        except OSError as exc:
            raise InstallError(
                f"Published Godot but could not remove preserved install {previous}: {exc}"
            ) from exc


def install(args: argparse.Namespace) -> dict[str, Any]:
    version = normalize_version(args.version)
    tag = release_tag(version)
    target = resolve_target(args.system, args.machine)
    asset_name = target.asset_name(tag)
    asset_url = f"{RELEASE_BASE_URL}/{tag}/{asset_name}"
    checksum_url = f"{RELEASE_BASE_URL}/{tag}/{CHECKSUM_ASSET}"
    install_root = Path(
        args.install_root or _default_install_root(target.system)
    ).expanduser().resolve(strict=False)
    cache_root = Path(
        args.cache_dir or _default_cache_root(target.system)
    ).expanduser().resolve(strict=False)
    bin_dir = Path(args.bin_dir or _default_bin_dir(target.system)).expanduser().resolve(
        strict=False
    )
    config_dir = Path(args.config_dir or install_root / "config").expanduser().resolve(
        strict=False
    )
    install_dir = install_root / tag / target.key
    expected_executable = install_dir / target.executable_relative(tag)
    result: dict[str, Any] = {
        "ok": True,
        "action": "plan" if args.dry_run else "",
        "version": version,
        "release_tag": tag,
        "platform": target.system,
        "architecture": target.architecture,
        "asset": asset_name,
        "download_url": asset_url,
        "checksum_url": checksum_url,
        "install_dir": str(install_dir),
        "executable": str(expected_executable),
        "bin_dir": str(bin_dir),
        "path_shim": "",
        "verified_version": "",
        "sha512": "",
        "config_files": [],
    }
    if args.dry_run:
        return result

    selected_executable = None
    verified_version = ""
    if not args.force:
        managed_manifest = install_dir / "a3game-install.json"
        if expected_executable.is_file() and managed_manifest.is_file():
            try:
                manifest = json.loads(managed_manifest.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise InstallError(f"Managed install manifest is invalid: {exc}") from exc
            if (
                manifest.get("schema_version") != INSTALL_SCHEMA
                or manifest.get("version") != version
                or manifest.get("asset") != asset_name
                or manifest.get("executable") != str(expected_executable)
            ):
                raise InstallError(
                    f"Managed install metadata does not match requested target: {install_dir}"
                )
            verified_version = probe_executable(expected_executable, version)
            selected_executable = expected_executable
            result["action"] = "reused-managed"
            result["sha512"] = str(manifest.get("sha512") or "")
        else:
            for candidate in _candidate_executables(args.executable):
                try:
                    candidate_version = probe_executable(candidate, version)
                except InstallError:
                    if args.executable:
                        raise
                    continue
                selected_executable = candidate
                verified_version = candidate_version
                result["action"] = "reused-existing"
                result["install_dir"] = ""
                break

    if selected_executable is None:
        if install_dir.exists() and not args.force:
            raise InstallError(
                f"Install target exists but is incomplete or unverified: {install_dir}; "
                "pass --force to replace that exact target"
            )
        cache_root.mkdir(parents=True, exist_ok=True)
        archive_path = cache_root / asset_name
        checksum_path = cache_root / f"{tag}-{CHECKSUM_ASSET}"
        _download(checksum_url, checksum_path, timeout=args.timeout)
        try:
            expected_sha512 = parse_sha512_sums(
                checksum_path.read_text(encoding="utf-8"), asset_name
            )
        except OSError as exc:
            raise InstallError(f"Could not read official checksum file: {exc}") from exc
        if not archive_path.is_file() or sha512_file(archive_path) != expected_sha512:
            _download(asset_url, archive_path, timeout=args.timeout)
        actual_sha512 = sha512_file(archive_path)
        if actual_sha512 != expected_sha512:
            raise InstallError(
                f"SHA-512 mismatch for {asset_name}: expected {expected_sha512}, "
                f"received {actual_sha512}"
            )
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(
            tempfile.mkdtemp(prefix=f".{target.key}-", dir=str(install_dir.parent))
        )
        shutil.rmtree(str(staging))
        try:
            extract_zip_safely(archive_path, staging)
            staged_executable = staging / target.executable_relative(tag)
            verified_version = probe_executable(staged_executable, version)
            manifest = {
                "schema_version": INSTALL_SCHEMA,
                "version": version,
                "release_tag": tag,
                "platform": target.system,
                "architecture": target.architecture,
                "asset": asset_name,
                "asset_url": asset_url,
                "checksum_url": checksum_url,
                "sha512": actual_sha512,
                "executable": str(expected_executable),
                "verified_version": verified_version,
            }
            (staging / "a3game-install.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _publish_install_tree(staging, install_dir)
        finally:
            if staging.exists():
                shutil.rmtree(str(staging))
        selected_executable = expected_executable
        result["action"] = "installed"
        result["sha512"] = actual_sha512

    result["executable"] = str(selected_executable)
    result["verified_version"] = verified_version
    if not args.no_path_shim:
        result["path_shim"] = str(
            _create_path_shim(
                selected_executable,
                bin_dir,
                target,
                force=args.force,
            )
        )
    result["config_files"] = _write_configuration(config_dir, target, tag, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Install or reuse an official pinned Godot 4 editor non-interactively. "
            "Downloads are verified against the release SHA512-SUMS.txt file."
        )
    )
    parser.add_argument("--version", default=DEFAULT_VERSION)
    parser.add_argument("--install-root", default="")
    parser.add_argument("--cache-dir", default="")
    parser.add_argument("--bin-dir", default="")
    parser.add_argument("--config-dir", default="")
    parser.add_argument(
        "--executable",
        default="",
        help="prefer and validate this existing Godot executable before downloading",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-path-shim", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--system", default="", help=argparse.SUPPRESS)
    parser.add_argument("--machine", default="", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = install(args)
    except InstallError as exc:
        failure = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(failure, sort_keys=True))
        else:
            print(f"Godot installation failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            f"Godot {result['version']} {result['action']}: {result['executable']}"
        )
        print(f"Verified version: {result['verified_version'] or 'dry-run'}")
        if result["path_shim"]:
            print(f"PATH shim: {result['path_shim']}")
        for path in result["config_files"]:
            print(f"Configuration: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
