"""Epic Pixel Streaming signalling lifecycle used by the UE example backend."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def web_servers_dir(engine_root: str | Path) -> Path:
    return (
        Path(engine_root)
        / "Engine"
        / "Plugins"
        / "Media"
        / "PixelStreaming"
        / "Resources"
        / "WebServers"
    )


def _linux_branch(fetch_script: Path) -> str:
    try:
        content = fetch_script.read_text(
            encoding="utf-8",
            errors="ignore",
        )
    except OSError:
        return ""
    match = re.search(
        r"^\s*PSInfraTagOrBranch\s*=\s*([A-Za-z0-9._-]+)\s*$",
        content,
        flags=re.MULTILINE,
    )
    return match.group(1) if match else ""


def _safe_extract(archive: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=".a3game-pixel-streaming-",
            dir=str(destination),
        )
    )
    try:
        with tarfile.open(archive, mode="r:*") as bundle:
            root = staging.resolve()
            for member in bundle.getmembers():
                target = (staging / member.name).resolve()
                if target != root and root not in target.parents:
                    raise ValueError(
                        "Pixel Streaming archive contains an unsafe path: "
                        f"{member.name}"
                    )
                if member.issym() or member.islnk():
                    link_target = (
                        target.parent / member.linkname
                    ).resolve()
                    if (
                        link_target != root
                        and root not in link_target.parents
                    ):
                        raise ValueError(
                            "Pixel Streaming archive contains an unsafe link: "
                            f"{member.name}"
                        )
            bundle.extractall(staging)
        roots = [
            path
            for path in staging.iterdir()
            if path.name != "__MACOSX"
        ]
        source_root = (
            roots[0]
            if len(roots) == 1 and roots[0].is_dir()
            else staging
        )
        shutil.copytree(
            source_root,
            destination,
            dirs_exist_ok=True,
        )
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def prepare_signalling_server(engine_root: str | Path) -> Path:
    servers = web_servers_dir(engine_root)
    signalling = servers / "SignallingWebServer"
    linux_launcher = (
        signalling
        / "platform_scripts"
        / "bash"
        / "Start_SignallingServer.sh"
    )
    windows_launcher = (
        signalling
        / "platform_scripts"
        / "cmd"
        / "run_local.bat"
    )
    if (
        (os.name == "nt" and windows_launcher.is_file())
        or (os.name != "nt" and linux_launcher.is_file())
    ):
        return signalling

    archive_value = os.environ.get(
        "A3GAME_PIXEL_STREAMING_INFRA_ARCHIVE",
        "",
    ).strip()
    if archive_value:
        archive = Path(archive_value).expanduser().resolve()
        if not archive.is_file():
            raise FileNotFoundError(
                f"Pixel Streaming archive was not found: {archive}"
            )
        _safe_extract(archive, servers)
        if (
            (os.name == "nt" and windows_launcher.is_file())
            or (os.name != "nt" and linux_launcher.is_file())
        ):
            return signalling

    fetch_script = (
        servers / (
            "get_ps_servers.bat"
            if os.name == "nt"
            else "get_ps_servers.sh"
        )
    )
    if not fetch_script.is_file():
        raise FileNotFoundError(
            "Pixel Streaming signalling server is not installed and "
            f"no fetch helper exists under {servers}"
        )
    if not _flag("A3GAME_AUTO_FETCH_PIXEL_STREAMING", True):
        raise FileNotFoundError(
            "Pixel Streaming signalling server is not installed. "
            f"Run the Epic helper: {fetch_script}"
        )

    timeout = int(
        os.environ.get(
            "A3GAME_PIXEL_STREAMING_FETCH_TIMEOUT",
            "600",
        )
    )
    command = [str(fetch_script)]
    if os.name != "nt":
        command = ["bash", str(fetch_script)]
        branch = _linux_branch(fetch_script)
        if branch:
            command.extend(["-b", branch])
    completed = subprocess.run(
        command,
        cwd=str(servers),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        details = "\n".join(
            item.strip()
            for item in (completed.stdout, completed.stderr)
            if item and item.strip()
        )
        raise RuntimeError(
            "Pixel Streaming infrastructure fetch failed "
            f"(exit {completed.returncode})"
            + (f"\n{details[-4000:]}" if details else "")
        )
    if (
        not windows_launcher.is_file()
        and not linux_launcher.is_file()
    ):
        raise FileNotFoundError(
            "Pixel Streaming fetch completed without installing the "
            f"signalling launcher under {signalling}"
        )
    return signalling


def start_signalling_server(
    engine_root: str | Path,
    *,
    http_port: int,
    streamer_port: int,
    sfu_port: int,
    use_frontend: bool = True,
) -> subprocess.Popen:
    signalling = prepare_signalling_server(engine_root)
    arguments = [
        f"--UseFrontend={'true' if use_frontend else 'false'}",
        f"--HttpPort={int(http_port)}",
        f"--StreamerPort={int(streamer_port)}",
        f"--SFUPort={int(sfu_port)}",
    ]
    cirrus = signalling / "cirrus.js"
    node = (
        signalling
        / "platform_scripts"
        / "cmd"
        / "node"
        / "node.exe"
    )
    creationflags = (
        subprocess.CREATE_NO_WINDOW
        if os.name == "nt"
        else 0
    )
    if os.name == "nt" and node.is_file() and cirrus.is_file():
        return subprocess.Popen(
            [str(node), cirrus.name, *arguments],
            cwd=str(signalling),
            creationflags=creationflags,
        )

    launcher = signalling / (
        "platform_scripts/cmd/run_local.bat"
        if os.name == "nt"
        else "platform_scripts/bash/Start_SignallingServer.sh"
    )
    if not launcher.is_file():
        raise FileNotFoundError(
            f"Pixel Streaming signalling launcher was not found: {launcher}"
        )
    command = (
        [str(launcher), *arguments]
        if os.name == "nt"
        else ["bash", str(launcher), "--nosudo", *arguments]
    )
    return subprocess.Popen(
        command,
        cwd=str(launcher.parent),
        creationflags=creationflags,
        start_new_session=os.name != "nt",
    )
