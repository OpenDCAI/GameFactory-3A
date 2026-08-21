"""Contract tests for the non-interactive Godot installer."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock


INSTALLER_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "engine_install"
    / "godot"
    / "install.py"
)
SPEC = importlib.util.spec_from_file_location("a3game_godot_installer", INSTALLER_PATH)
assert SPEC is not None and SPEC.loader is not None
installer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = installer
SPEC.loader.exec_module(installer)


class GodotInstallerTests(unittest.TestCase):
    def test_exact_stable_version_and_platform_mapping(self) -> None:
        self.assertEqual("4.5.1", installer.normalize_version("v4.5.1-stable"))
        with self.assertRaises(installer.InstallError):
            installer.normalize_version("latest")
        with self.assertRaises(installer.InstallError):
            installer.normalize_version("3.6.1")

        linux = installer.resolve_target("Linux", "AMD64")
        self.assertEqual("Godot_v4.5.1-stable_linux.x86_64.zip", linux.asset_name("4.5.1-stable"))
        self.assertEqual("Godot_v4.5.1-stable_linux.x86_64", str(linux.executable_relative("4.5.1-stable")))
        mac = installer.resolve_target("Darwin", "arm64")
        self.assertEqual("macos.universal", mac.asset_label)
        self.assertEqual(Path("Godot.app/Contents/MacOS/Godot"), mac.executable_relative("4.5.1-stable"))
        windows = installer.resolve_target("Windows", "aarch64")
        self.assertEqual("windows_arm64.exe", windows.asset_label)
        self.assertEqual(
            "Godot_v4.5.1-stable_windows_arm64.exe.zip",
            windows.asset_name("4.5.1-stable"),
        )
        self.assertEqual(
            Path("Godot_v4.5.1-stable_windows_arm64.exe"),
            windows.executable_relative("4.5.1-stable"),
        )
        with self.assertRaises(installer.InstallError):
            installer.resolve_target("Plan9", "mips")

    def test_supported_targets_match_official_4_5_1_editor_archives(self) -> None:
        official_archives = {
            "Godot_v4.5.1-stable_linux.arm32.zip",
            "Godot_v4.5.1-stable_linux.arm64.zip",
            "Godot_v4.5.1-stable_linux.x86_32.zip",
            "Godot_v4.5.1-stable_linux.x86_64.zip",
            "Godot_v4.5.1-stable_macos.universal.zip",
            "Godot_v4.5.1-stable_win32.exe.zip",
            "Godot_v4.5.1-stable_win64.exe.zip",
            "Godot_v4.5.1-stable_windows_arm64.exe.zip",
        }
        supported_hosts = (
            ("Linux", "armv7l"),
            ("Linux", "aarch64"),
            ("Linux", "i686"),
            ("Linux", "AMD64"),
            ("Darwin", "x86_64"),
            ("Darwin", "arm64"),
            ("Windows", "x86"),
            ("Windows", "AMD64"),
            ("Windows", "ARM64"),
        )

        resolved_archives = {
            installer.resolve_target(system, machine).asset_name("4.5.1-stable")
            for system, machine in supported_hosts
        }
        self.assertEqual(official_archives, resolved_archives)

    def test_checksum_parser_is_exact_and_fails_closed(self) -> None:
        digest = "a" * 128
        asset = "Godot_v4.5.1-stable_linux.x86_64.zip"
        self.assertEqual(
            digest,
            installer.parse_sha512_sums(f"{digest}  {asset}\n", asset),
        )
        with self.assertRaises(installer.InstallError):
            installer.parse_sha512_sums(f"{digest}  another.zip\n", asset)
        with self.assertRaises(installer.InstallError):
            installer.parse_sha512_sums(
                f"{digest}  {asset}\n{digest} *{asset}\n", asset
            )

    def test_safe_extractor_rejects_traversal_and_symbolic_links(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            traversal = root / "traversal.zip"
            with zipfile.ZipFile(str(traversal), "w") as archive:
                archive.writestr("../outside", b"no")
            with self.assertRaises(installer.InstallError):
                installer.extract_zip_safely(traversal, root / "traversal-output")
            self.assertFalse((root / "outside").exists())

            linked = root / "linked.zip"
            info = zipfile.ZipInfo("Godot")
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(str(linked), "w") as archive:
                archive.writestr(info, "../../outside")
            with self.assertRaises(installer.InstallError):
                installer.extract_zip_safely(linked, root / "linked-output")

    @unittest.skipIf(installer.os.name == "nt", "fixture executable is POSIX")
    def test_verified_install_is_atomic_and_idempotently_reused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_archive = root / "official.zip"
            tag = "4.5.1-stable"
            executable_name = f"Godot_v{tag}_linux.x86_64"
            executable = zipfile.ZipInfo(executable_name)
            executable.create_system = 3
            executable.external_attr = (stat.S_IFREG | 0o755) << 16
            with zipfile.ZipFile(str(source_archive), "w") as archive:
                archive.writestr(
                    executable,
                    "#!/usr/bin/env sh\nprintf '%s\\n' '4.5.1.stable.official.fixture'\n",
                )
            digest = hashlib.sha512(source_archive.read_bytes()).hexdigest()
            asset_name = f"Godot_v{tag}_linux.x86_64.zip"
            calls = []

            def fake_download(url: str, destination: Path, timeout: float = 120.0) -> None:
                calls.append(url)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if url.endswith(installer.CHECKSUM_ASSET):
                    destination.write_text(
                        f"{digest}  {asset_name}\n", encoding="utf-8"
                    )
                else:
                    destination.write_bytes(source_archive.read_bytes())

            parser = installer.build_parser()
            args = parser.parse_args(
                [
                    "--version",
                    "4.5.1",
                    "--install-root",
                    str(root / "install"),
                    "--cache-dir",
                    str(root / "cache"),
                    "--config-dir",
                    str(root / "config"),
                    "--no-path-shim",
                    "--system",
                    "Linux",
                    "--machine",
                    "x86_64",
                ]
            )
            with mock.patch.object(installer, "_download", side_effect=fake_download):
                first = installer.install(args)
                second = installer.install(args)

            self.assertEqual("installed", first["action"])
            self.assertEqual("reused-managed", second["action"])
            self.assertEqual(2, len(calls))
            installed_executable = Path(first["executable"])
            self.assertTrue(installed_executable.is_file())
            self.assertIn("4.5.1.stable", first["verified_version"])
            self.assertEqual(first["executable"], second["executable"])
            self.assertEqual(digest, second["sha512"])
            self.assertEqual(2, len(first["config_files"]))

    def test_dry_run_is_non_mutating_and_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = installer.build_parser().parse_args(
                [
                    "--dry-run",
                    "--install-root",
                    str(root / "install"),
                    "--system",
                    "Linux",
                    "--machine",
                    "arm64",
                ]
            )
            result = installer.install(args)
            self.assertEqual("plan", result["action"])
            self.assertEqual(
                "Godot_v4.5.1-stable_linux.arm64.zip",
                result["asset"],
            )
            self.assertTrue(result["download_url"].startswith("https://github.com/godotengine/godot/releases/download/"))
            self.assertFalse((root / "install").exists())

    def test_windows_managed_install_and_path_shim_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install_root = root / "install"
            install_dir = install_root / "4.5.1-stable" / "windows-x86_64"
            executable = install_dir / "Godot_v4.5.1-stable_win64.exe"
            executable.parent.mkdir(parents=True)
            executable.touch()
            (install_dir / "a3game-install.json").write_text(
                json.dumps(
                    {
                        "schema_version": installer.INSTALL_SCHEMA,
                        "version": "4.5.1",
                        "asset": "Godot_v4.5.1-stable_win64.exe.zip",
                        "executable": str(executable),
                        "sha512": "fixture-sha512",
                    }
                ),
                encoding="utf-8",
            )
            args = installer.build_parser().parse_args(
                [
                    "--version",
                    "4.5.1",
                    "--install-root",
                    str(install_root),
                    "--cache-dir",
                    str(root / "cache"),
                    "--bin-dir",
                    str(root / "bin"),
                    "--config-dir",
                    str(root / "config"),
                    "--system",
                    "Windows",
                    "--machine",
                    "AMD64",
                ]
            )

            with mock.patch.object(
                installer,
                "probe_executable",
                return_value="4.5.1.stable.official.fixture",
            ):
                first = installer.install(args)
                second = installer.install(args)

            self.assertEqual("reused-managed", first["action"])
            self.assertEqual("reused-managed", second["action"])
            self.assertEqual(first["path_shim"], second["path_shim"])
            self.assertEqual("fixture-sha512", second["sha512"])
            shim = Path(first["path_shim"])
            expected = (
                "@echo off\r\n" + f'"{executable.resolve()}" %*\r\n'
            ).encode("utf-8")
            self.assertEqual(expected, shim.read_bytes())

            shim.write_bytes(expected + b"rem foreign change\r\n")
            with mock.patch.object(
                installer,
                "probe_executable",
                return_value="4.5.1.stable.official.fixture",
            ):
                with self.assertRaisesRegex(
                    installer.InstallError,
                    "PATH shim already exists and is not owned",
                ):
                    installer.install(args)
            self.assertEqual(expected + b"rem foreign change\r\n", shim.read_bytes())

    def test_atomic_publication_restores_existing_install_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staging = root / "staging"
            destination = root / "godot"
            staging.mkdir()
            destination.mkdir()
            (staging / "new").write_text("new", encoding="utf-8")
            (destination / "old").write_text("old", encoding="utf-8")
            real_replace = installer.os.replace
            calls = 0

            def fail_publication(source: str, target: str) -> None:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("forced publication failure")
                real_replace(source, target)

            with mock.patch.object(
                installer.os, "replace", side_effect=fail_publication
            ):
                with self.assertRaises(installer.InstallError):
                    installer._publish_install_tree(staging, destination)

            self.assertEqual("old", (destination / "old").read_text(encoding="utf-8"))
            self.assertFalse((destination / "new").exists())
            self.assertEqual("new", (staging / "new").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
