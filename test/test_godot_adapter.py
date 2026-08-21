"""CPU-only contract tests for the Godot 4 adapter."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from engine_adapters.godot import GodotClient
from engine_adapters.godot._internal import godot_import_error_lines
from engine_adapters.godot.contracts import GodotOperationResult
from pipeline.common import paths
from pipeline.common.code_mapping import (
    normalize_engine_id,
    resolve_browser_backend_registration,
    resolve_engine_registration,
)

RESULT_KEYS = {
    "ok",
    "operation",
    "artifacts",
    "diagnostics",
    "warnings",
    "errors",
    "payload",
}

REAL_GODOT = os.environ.get("A3GAME_TEST_GODOT_EXECUTABLE", "").strip()
CORRUPT_TEXTURE_IMPORT_ERRORS = (
    "ERROR: Condition failed. Returning: ERR_FILE_CORRUPT\n"
    "ERROR: Failed loading resource: ERR_PARSE_ERROR\n"
    "ERROR: glTF: Couldn't load image index '0' with its given mimetype: image/png."
)


FAKE_GODOT = r"""#!/usr/bin/env python3
import hashlib
import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
invocation_marker = os.environ.get("A3GAME_FAKE_GODOT_INVOCATION_MARKER", "")
if invocation_marker:
    Path(invocation_marker).write_text(json.dumps(args), encoding="utf-8")
if "--version" in args:
    print(os.environ.get("A3GAME_FAKE_GODOT_VERSION", "4.4.stable.fake"))
    raise SystemExit(0)

if "--import" in args:
    project = Path(args[args.index("--path") + 1])
    imported_root = project / "assets" / "imported"
    importable_suffixes = {
        ".glb", ".gltf", ".fbx", ".obj", ".dae", ".blend",
        ".png", ".jpg", ".jpeg", ".webp", ".svg", ".exr", ".hdr", ".ktx",
        ".wav", ".ogg", ".mp3",
    }
    if imported_root.is_dir():
        for source in imported_root.rglob("*"):
            if source.is_file() and source.suffix.lower() in importable_suffixes:
                resource_path = "res://" + source.relative_to(project).as_posix()
                digest = hashlib.md5(resource_path.encode()).hexdigest()
                cache_root = project / ".godot" / "imported"
                cache_root.mkdir(parents=True, exist_ok=True)
                cache_base = cache_root / f"{source.name}-{digest}"
                cache_payload = "fake-cache:" + source.read_bytes().hex()
                cache_scn = Path(str(cache_base) + ".scn")
                cache_md5 = Path(str(cache_base) + ".md5")
                cache_scn.write_text(
                    cache_payload,
                    encoding="utf-8",
                )
                cache_md5.write_text(
                    cache_payload,
                    encoding="utf-8",
                )
                Path(str(source) + ".import").write_text(
                    "path=\"res://.godot/imported/"
                    + cache_scn.name
                    + "\"\n"
                    + "fake-import:"
                    + source.read_bytes().hex(),
                    encoding="utf-8",
                )
    if os.environ.get("A3GAME_FAKE_GODOT_FAIL_IMPORT") == "1":
        print("ERROR: forced import failure", file=sys.stderr)
        raise SystemExit(7)
    if os.environ.get("A3GAME_FAKE_GODOT_IMPORT_ERROR_ZERO") == "1":
        stream = (
            sys.stdout
            if os.environ.get("A3GAME_FAKE_GODOT_IMPORT_ERROR_STREAM") == "stdout"
            else sys.stderr
        )
        print(
            os.environ.get(
                "A3GAME_FAKE_GODOT_IMPORT_ERROR_TEXT",
                "ERROR: Error importing 'res://forced-invalid.glb'",
            ),
            file=stream,
        )
    raise SystemExit(0)

for flag in ("--export-release", "--export-debug", "--export-pack"):
    if flag in args:
        output = Path(args[args.index(flag) + 2])
        output.parent.mkdir(parents=True, exist_ok=True)
        if os.environ.get("A3GAME_FAKE_GODOT_NO_EXPORT") != "1":
            export_payload = os.environ.get(
                "A3GAME_FAKE_GODOT_EXPORT_PAYLOAD", "fake-godot-export"
            ).encode()
            if os.environ.get("A3GAME_FAKE_GODOT_EXPORT_DIRECTORY") == "1":
                executable = output / "Contents" / "MacOS" / output.stem
                executable.parent.mkdir(parents=True)
                executable.write_bytes(export_payload)
            else:
                output.write_bytes(export_payload)
            if os.environ.get("A3GAME_FAKE_GODOT_EXPORT_COMPANIONS") == "1":
                for suffix in (".wasm", ".pck", ".js", ".png"):
                    output.with_suffix(suffix).write_bytes(
                        ("fake-godot-export" + suffix).encode()
                    )
        if os.environ.get("A3GAME_FAKE_GODOT_FAIL_EXPORT") == "1":
            print("ERROR: forced export failure", file=sys.stderr)
            raise SystemExit(9)
        raise SystemExit(0)

if "--script" in args:
    plugin_report = os.environ.get("A3GAME_GODOT_PLUGIN_REPORT", "")
    plugin_resource = os.environ.get("A3GAME_GODOT_PLUGIN_RESOURCE", "")
    plugin_descriptor = os.environ.get("A3GAME_GODOT_PLUGIN_DESCRIPTOR", "")
    if plugin_report:
        project = Path(args[args.index("--path") + 1])
        entry = (
            project / plugin_resource[len("res://"):]
            if plugin_resource.startswith("res://")
            else Path("")
        )
        descriptor_path = (
            project / plugin_descriptor[len("res://"):]
            if plugin_descriptor.startswith("res://")
            else Path("")
        )
        error = ""
        content = ""
        if not plugin_resource.startswith("res://addons/"):
            error = "Plugin entry must be an add-on res:// resource"
        elif not descriptor_path.is_file():
            error = "Plugin descriptor could not be parsed"
        elif "A3GAME_INVALID_PLUGIN_CONFIG" in descriptor_path.read_text(
            encoding="utf-8", errors="replace"
        ):
            error = "Plugin descriptor could not be parsed"
        elif not entry.is_file():
            error = "Plugin entry script could not be loaded"
        else:
            content = entry.read_text(encoding="utf-8", errors="replace")
            if "A3GAME_INVALID_GDSCRIPT" in content:
                error = "Plugin entry script could not be loaded: parse error"
            elif "extends EditorPlugin" not in content:
                error = "Plugin entry script must inherit EditorPlugin"
            elif "@tool" not in content:
                error = "Plugin entry script must run in tool mode"
            elif "func _init(required_value)" in content:
                error = (
                    "Plugin entry script cannot be instantiated without arguments: "
                    "_init requires 1 argument(s)"
                )
        Path(plugin_report).write_text(json.dumps({
            "schema_version": "gamefactory3a.godot.plugin_validation.v1",
            "ok": not error,
            "resource_path": plugin_resource,
            "descriptor_path": plugin_descriptor,
            "metadata": {},
            "resource_class": "GDScript" if not error else "",
            "base_type": "EditorPlugin" if not error else "",
            "can_instantiate": not error,
            "instantiated": not error,
            "instance_class": "EditorPlugin" if not error else "",
            "is_tool": not error,
            "error": error,
        }), encoding="utf-8")
        if error:
            print("SCRIPT ERROR: " + error, file=sys.stderr)
        # Godot can report resource errors while returning zero, so callers must
        # validate the structured report instead of trusting this exit status.
        raise SystemExit(0)
    test_report = os.environ.get("A3GAME_GODOT_TEST_REPORT", "")
    inspect_report = os.environ.get("A3GAME_GODOT_INSPECT_REPORT", "")
    binding_job = os.environ.get("A3GAME_GODOT_BINDING_JOB", "")
    binding_report = os.environ.get("A3GAME_GODOT_BINDING_REPORT", "")
    if binding_job and binding_report:
        job = json.loads(Path(binding_job).read_text(encoding="utf-8"))
        project = Path(args[args.index("--path") + 1])
        applied = []
        for binding in job.get("bindings", []):
            target_resource = binding["target_resource"]
            target = project / target_resource[len("res://"):]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join([
                '[gd_scene load_steps=3 format=3]',
                f'[ext_resource type="PackedScene" path="{binding["source_resource"]}" id="1_source"]',
                f'[ext_resource type="Material" path="{job["material"]}" id="2_material"]',
                '[node name="BoundScene" instance=ExtResource("1_source")]',
                '[node name="A3MaterialBindingProof" type="MeshInstance3D" parent="."]',
                'material_override = ExtResource("2_material")',
                '',
            ]), encoding="utf-8")
            applied.append({
                "artifact_id": binding["artifact_id"],
                "source_resource": binding["source_resource"],
                "target_resource": target_resource,
                "material": job["material"],
                "mesh_instance_count": 1,
            })
            if os.environ.get("A3GAME_FAKE_GODOT_FAIL_BINDING") == "1":
                break
        failed = os.environ.get("A3GAME_FAKE_GODOT_FAIL_BINDING") == "1"
        Path(binding_report).write_text(json.dumps({
            "schema_version": "gamefactory3a.godot.material_binding_report.v1",
            "ok": not failed,
            "bindings": applied,
            "errors": ["forced binding failure"] if failed else [],
        }), encoding="utf-8")
        raise SystemExit(11 if failed else 0)
    if test_report and os.environ.get("A3GAME_FAKE_GODOT_NO_REPORT") != "1":
        path = Path(test_report)
        path.parent.mkdir(parents=True, exist_ok=True)
        custom_report = os.environ.get("A3GAME_FAKE_GODOT_TEST_REPORT_JSON", "")
        report_text = custom_report or json.dumps({
            "schema_version": "gamefactory3a.godot.tests.v1",
            "tests": [{"name": "fake_native_contract", "status": "passed"}],
        })
        path.write_text(report_text, encoding="utf-8")
    if inspect_report:
        path = Path(inspect_report)
        path.parent.mkdir(parents=True, exist_ok=True)
        resource_reference = os.environ.get("A3GAME_GODOT_RESOURCE", "")
        resource = resource_reference
        if resource_reference.startswith("uid://"):
            expected_uid = os.environ.get("A3GAME_FAKE_GODOT_UID", "")
            resource = (
                os.environ.get("A3GAME_FAKE_GODOT_UID_RESOURCE", "")
                if resource_reference == expected_uid
                else ""
            )
        invalid = (
            not resource
            or os.environ.get("A3GAME_FAKE_GODOT_UNLOADABLE") == "1"
        )
        motion = "/motions/" in resource
        avatar = "/avatars/" in resource
        suffix = Path(resource).suffix.lower()
        texture = suffix in {".png", ".jpg", ".jpeg", ".webp", ".svg", ".exr", ".hdr", ".ktx"}
        audio = suffix in {".wav", ".ogg", ".mp3"}
        material = "/materials/" in resource and suffix in {".tres", ".res"}
        resource_class = (
            "CompressedTexture2D" if texture else
            "AudioStreamWAV" if audio else
            "StandardMaterial3D" if material else
            "PackedScene"
        )
        skeletons = []
        if (avatar or motion) and os.environ.get("A3GAME_FAKE_GODOT_NO_SKELETON") != "1":
            skeletons = [{
                "path": "Character/Skeleton3D",
                "bone_count": 2,
                "bones": ["Hips", "Spine"],
            }]
            if os.environ.get("A3GAME_FAKE_GODOT_TRACK_OTHER_SKELETON") == "1":
                skeletons.append({
                    "path": "Other/Skeleton3D",
                    "bone_count": 2,
                    "bones": ["Hips", "Spine"],
                })
        animations = []
        animation_details = []
        if motion and os.environ.get("A3GAME_FAKE_GODOT_NO_ANIMATION") != "1":
            animations = ["Walk"]
            tracks = []
            if os.environ.get("A3GAME_FAKE_GODOT_NO_BONE_TRACK") != "1":
                property_track = os.environ.get("A3GAME_FAKE_GODOT_PROPERTY_TRACK") == "1"
                other_skeleton = os.environ.get("A3GAME_FAKE_GODOT_TRACK_OTHER_SKELETON") == "1"
                skeleton_path = "Other/Skeleton3D" if other_skeleton else "Character/Skeleton3D"
                tracks = [{
                    "path": "Root:position" if property_track else skeleton_path + ":Hips",
                    "node_path": "Root" if property_track else skeleton_path,
                    "bone": "position" if property_track else "Hips",
                    "type": 0,
                    "target_class": "Node3D" if property_track else "Skeleton3D",
                    "targets_skeleton_bone": not property_track,
                }]
            animation_details = [{
                "player_path": "AnimationPlayer",
                "library": "",
                "name": "Walk",
                "length": 1.0,
                "track_count": len(tracks),
                "tracks": tracks,
            }]
        nodes = [{"name": "Root", "class": "Node3D", "path": "."}]
        if (
            any(part in resource for part in ("/avatars/", "/props/", "/weapons/"))
            and os.environ.get("A3GAME_FAKE_GODOT_NO_MESH") != "1"
        ):
            nodes.append({"name": "Body", "class": "MeshInstance3D", "path": "Body"})
        skinned_meshes = []
        if avatar and skeletons and os.environ.get("A3GAME_FAKE_GODOT_NO_SKIN") != "1":
            skin_linked = os.environ.get("A3GAME_FAKE_GODOT_NO_SKIN_LINK") != "1"
            skinned_meshes = [{
                "path": "Body",
                "skeleton": "Character/Skeleton3D",
                "skeleton_path": "Character/Skeleton3D" if skin_linked else "",
                "skeleton_resolved": skin_linked,
                "has_skin": True,
            }]
        report = {
            "schema_version": "gamefactory3a.godot.resource_inspection.v1",
            "ok": not invalid,
            "resource_path": resource_reference,
            "resolved_resource_path": resource,
            "resource_class": resource_class if not invalid else "",
            "is_packed_scene": resource_class == "PackedScene" and not invalid,
            "is_animation_library": False,
            "is_animation": False,
            "is_texture_2d": texture and not invalid,
            "is_audio_stream": audio and not invalid,
            "is_material": material and not invalid,
            "instantiable": resource_class == "PackedScene" and not invalid,
            "nodes": nodes,
            "animations": animations,
            "animation_details": animation_details,
            "skeletons": skeletons,
            "skinned_meshes": skinned_meshes,
            "error": "Resource could not be loaded" if invalid else "",
        }
        path.write_text(json.dumps(report), encoding="utf-8")
        raise SystemExit(1 if invalid else 0)
    raise SystemExit(0)

time.sleep(30)
"""


class GodotAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="a3game-godot-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output_root_patch = mock.patch.object(
            paths, "OUTPUT_ROOT", self.root / "outputs"
        )
        self.output_root_patch.start()
        self.addCleanup(self.output_root_patch.stop)
        self.environment_patch = mock.patch.dict(
            os.environ,
            {
                "A3GAME_GODOT_DATA_ROOT": str(self.root / "adapter-data"),
                "A3GAME_GODOT_ARTIFACT_REGISTRY": "",
                "A3GAME_DATA_ROOT": "",
                "A3GAME_ARTIFACT_REGISTRY": "",
            },
            clear=False,
        )
        self.environment_patch.start()
        self.addCleanup(self.environment_patch.stop)

        self.fake_godot = self.root / "godot4"
        self.fake_godot.write_text(FAKE_GODOT, encoding="utf-8")
        self.fake_godot.chmod(0o755)
        self.project = self.root / "GodotProject"
        self.client = GodotClient(
            project_path=self.project,
            godot_executable=self.fake_godot,
            editor_timeout=5,
            import_timeout=5,
        )
        created = self.client.project.create(project_name="Godot Test")
        self.assert_result(created, ok=True)

    def assert_result(self, result: dict, *, ok: bool | None = None) -> None:
        self.assertEqual(RESULT_KEYS, set(result), result)
        json.dumps(result, allow_nan=False)
        if ok is not None:
            self.assertIs(result["ok"], ok, result)

    @staticmethod
    def corrupt_embedded_texture_gltf() -> bytes:
        mesh_buffer = struct.pack(
            "<9f3H",
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0,
            1,
            2,
        )
        document = {
            "asset": {"version": "2.0", "generator": "GameFactory-3A test"},
            "buffers": [
                {
                    "uri": "data:application/octet-stream;base64,"
                    + base64.b64encode(mesh_buffer).decode("ascii"),
                    "byteLength": len(mesh_buffer),
                }
            ],
            "bufferViews": [
                {"buffer": 0, "byteOffset": 0, "byteLength": 36, "target": 34962},
                {
                    "buffer": 0,
                    "byteOffset": 36,
                    "byteLength": 6,
                    "target": 34963,
                },
            ],
            "accessors": [
                {
                    "bufferView": 0,
                    "componentType": 5126,
                    "count": 3,
                    "type": "VEC3",
                    "min": [0.0, 0.0, 0.0],
                    "max": [1.0, 1.0, 0.0],
                },
                {
                    "bufferView": 1,
                    "componentType": 5123,
                    "count": 3,
                    "type": "SCALAR",
                },
            ],
            "images": [{"uri": "data:image/png;base64,bm90IGEgcG5n"}],
            "textures": [{"source": 0}],
            "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
            "meshes": [
                {
                    "primitives": [
                        {
                            "attributes": {"POSITION": 0},
                            "indices": 1,
                            "material": 0,
                        }
                    ]
                }
            ],
            "nodes": [{"mesh": 0}],
            "scenes": [{"nodes": [0]}],
            "scene": 0,
        }
        return json.dumps(document, separators=(",", ":")).encode("utf-8")

    def godot_import_cache(self, target: Path) -> dict[str, bytes]:
        resource_path = "res://" + target.relative_to(self.project).as_posix()
        digest = hashlib.md5(resource_path.encode()).hexdigest()
        cache_root = self.project / ".godot" / "imported"
        return {
            path.name: path.read_bytes()
            for path in sorted(cache_root.glob(f"{target.name}-{digest}.*"))
            if path.is_file()
        }

    def make_artifact(
        self,
        *,
        task_id: str,
        suffix: str,
        content: bytes,
        task_kind: str = "3d_object",
        artifact_key: str = "glb_path",
    ) -> tuple[dict[str, str], Path]:
        game_id = "godot_adapter_test"
        run_id = "run_001"
        task_dir = paths.task_output_dir(game_id, task_kind, task_id, run_id=run_id)
        artifact = task_dir / f"artifact{suffix}"
        artifact.write_bytes(content)
        (task_dir / "meta.json").write_text(
            json.dumps(
                {
                    "game_id": game_id,
                    "run_id": run_id,
                    "task_kind": task_kind,
                    "task_id": task_id,
                    artifact_key: str(artifact),
                }
            ),
            encoding="utf-8",
        )
        return (
            {
                "game_id": game_id,
                "run_id": run_id,
                "task_kind": task_kind,
                "task_id": task_id,
                "artifact_key": artifact_key,
            },
            artifact,
        )

    def test_project_observe_and_engine_registration(self) -> None:
        validation = self.client.project.validate()
        self.assert_result(validation, ok=True)
        self.assertEqual(
            "res://main.tscn",
            validation["payload"]["main_scene_load_reference"],
        )
        self.assertTrue(validation["payload"]["main_scene_load_process"])
        self.assertTrue(
            validation["payload"]["main_scene_load_inspection"]["is_packed_scene"]
        )
        info = self.client.get_environment_info()
        self.assert_result(info, ok=True)
        self.assertEqual("4.4.stable.fake", info["payload"]["engine_version"])
        status = self.client.observe.check_status()
        self.assert_result(status, ok=True)

        self.assertEqual("godot", normalize_engine_id("godot4"))
        registration = resolve_engine_registration("godot_engine")
        self.assertEqual("godot", registration["engine_id"])
        self.assertTrue(Path(registration["primary_api"]).is_file())
        with self.assertRaisesRegex(ValueError, "not registered"):
            resolve_browser_backend_registration("godot")

    def test_world_public_methods_match_cross_engine_call_contract(self) -> None:
        def source_signature(
            path: Path, class_name: str, method_name: str
        ) -> tuple[tuple[str, ...], tuple[str, ...]]:
            source = path.read_text(encoding="utf-8")
            class_source = source[source.index(f"class {class_name}") :]
            match = re.search(
                rf"^    def {method_name}\((.*?)^    \) ->",
                class_source,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing {class_name}.{method_name}")
            positional: list[str] = []
            keyword_only: list[str] = []
            destination = positional
            entries: list[str] = []
            start = 0
            depth = 0
            signature_text = match.group(1)
            for index, character in enumerate(signature_text):
                if character in "[({":
                    depth += 1
                elif character in "])}":
                    depth -= 1
                elif character == "," and depth == 0:
                    entries.append(signature_text[start:index])
                    start = index + 1
            entries.append(signature_text[start:])
            for entry in entries:
                entry = entry.strip()
                if not entry:
                    continue
                if entry == "*":
                    destination = keyword_only
                    continue
                destination.append(entry.split(":", 1)[0].split("=", 1)[0].strip())
            return tuple(positional + keyword_only), tuple(keyword_only)

        clients = (
            (Path("engine_adapters/ue5/world/client.py"), "UEWorldClient"),
            (Path("engine_adapters/unity3d/world/client.py"), "UnityWorldClient"),
            (Path("engine_adapters/three_js/world/client.py"), "ThreeWorldClient"),
            (Path("engine_adapters/godot/world/client.py"), "GodotWorldClient"),
        )
        for path, class_name in clients:
            with self.subTest(client=class_name, method="create_draft"):
                names, keyword_only = source_signature(path, class_name, "create_draft")
                self.assertEqual(
                    ("self", "spec", "draft_id", "project_id", "metadata"), names
                )
                self.assertEqual(("draft_id", "project_id", "metadata"), keyword_only)
            with self.subTest(client=class_name, method="list_packages"):
                names, keyword_only = source_signature(path, class_name, "list_packages")
                self.assertEqual(("self", "project_id", "world_id"), names)
                self.assertEqual(("project_id", "world_id"), keyword_only)

        scene = self.client.assets._register_resource(
            resource_path="res://main.tscn",
            asset_type="scene",
            asset_id="world-contract-scene",
            backend_class="PackedScene",
        )
        created = self.client.world.create_draft(
            {
                "draft_id": "draft-from-spec",
                "world_id": "world-contract",
                "project_id": "project-from-spec",
                "scene_artifact_id": scene.artifact_id,
                "metadata": {"from_spec": True, "shared": "spec"},
            },
            draft_id="draft-contract",
            project_id="project-contract",
            metadata={"from_argument": True, "shared": "argument"},
        )
        self.assert_result(created, ok=True)
        self.assertEqual("draft-contract", created["payload"]["draft_id"])
        self.assertEqual("project-contract", created["payload"]["project_id"])
        self.assertEqual(
            {
                "from_spec": True,
                "from_argument": True,
                "shared": "argument",
            },
            created["payload"]["metadata"],
        )
        self.assert_result(
            self.client.world.publish_draft("draft-contract"),
            ok=True,
        )
        listed = self.client.world.list_packages(
            project_id="project-contract",
            world_id="world-contract",
        )
        self.assert_result(listed, ok=True)
        self.assertEqual(1, listed["payload"]["count"])
        excluded = self.client.world.list_packages(world_id="another-world")
        self.assert_result(excluded, ok=True)
        self.assertEqual(0, excluded["payload"]["count"])
        invalid_metadata = self.client.world.create_draft(
            {"scene_artifact_id": scene.artifact_id},
            metadata=[],  # type: ignore[arg-type]
        )
        self.assert_result(invalid_metadata, ok=False)

    @unittest.skipUnless(os.name == "posix", "symlink boundary probe requires POSIX")
    def test_project_create_rejects_symlinked_targets_without_writing(self) -> None:
        for filename in ("project.godot", "main.tscn"):
            with self.subTest(filename=filename):
                case_root = self.root / f"create-linked-{filename}"
                project = case_root / "project"
                project.mkdir(parents=True)
                outside = case_root / f"outside-{filename}"
                link = project / filename
                link.symlink_to(outside)
                client = GodotClient(
                    project_path=project,
                    godot_executable=self.fake_godot,
                )

                result = client.project.create(project_name="Must Stay Inside")

                self.assert_result(result, ok=False)
                self.assertIn("symlink", " ".join(result["errors"]).lower())
                self.assertTrue(link.is_symlink())
                self.assertEqual(outside, Path(os.readlink(str(link))))
                self.assertFalse(outside.exists())
                other = project / (
                    "main.tscn" if filename == "project.godot" else "project.godot"
                )
                self.assertFalse(other.exists())
                if filename == "project.godot":
                    direct_client = GodotClient(
                        project_path=link,
                        godot_executable=self.fake_godot,
                    )
                    direct_result = direct_client.project.create(
                        project_name="Reject Direct Project Link"
                    )
                    self.assert_result(direct_result, ok=False)
                    self.assertIn("symlink", " ".join(direct_result["errors"]).lower())
                    self.assertFalse(outside.exists())

        case_root = self.root / "create-linked-root"
        outside_project = case_root / "outside-project"
        outside_project.mkdir(parents=True)
        sentinel = outside_project / "sentinel.txt"
        sentinel.write_bytes(b"unchanged")
        linked_project = case_root / "linked-project"
        linked_project.symlink_to(outside_project, target_is_directory=True)
        linked_client = GodotClient(
            project_path=linked_project,
            godot_executable=self.fake_godot,
        )

        linked_result = linked_client.project.create(project_name="Reject Root Link")

        self.assert_result(linked_result, ok=False)
        self.assertIn("symlink", " ".join(linked_result["errors"]).lower())
        self.assertEqual(b"unchanged", sentinel.read_bytes())
        self.assertEqual([sentinel], list(outside_project.iterdir()))

        case_root = self.root / "create-linked-directory"
        project = case_root / "project"
        imported = project / "assets" / "imported"
        imported.parent.mkdir(parents=True)
        outside_imported = case_root / "outside-imported"
        outside_imported.mkdir(parents=True)
        sentinel = outside_imported / "sentinel.txt"
        sentinel.write_bytes(b"unchanged")
        imported.symlink_to(outside_imported, target_is_directory=True)
        directory_client = GodotClient(
            project_path=project,
            godot_executable=self.fake_godot,
        )

        directory_result = directory_client.project.create(
            project_name="Reject Directory Link"
        )

        self.assert_result(directory_result, ok=False)
        self.assertIn("symlink", " ".join(directory_result["errors"]).lower())
        self.assertFalse((project / "project.godot").exists())
        self.assertFalse((project / "main.tscn").exists())
        self.assertEqual(b"unchanged", sentinel.read_bytes())
        self.assertEqual([sentinel], list(outside_imported.iterdir()))

    def test_project_create_rejects_wrong_managed_path_types_without_writing(
        self,
    ) -> None:
        project_file = self.root / "project-root-is-a-file"
        project_file.write_bytes(b"unchanged")
        root_client = GodotClient(
            project_path=project_file,
            godot_executable=self.fake_godot,
        )

        root_result = root_client.project.create(project_name="Reject File Root")

        self.assert_result(root_result, ok=False)
        self.assertIn("directory", " ".join(root_result["errors"]).lower())
        self.assertEqual(b"unchanged", project_file.read_bytes())

        project = self.root / "managed-directory-is-a-file"
        project.mkdir()
        assets = project / "assets"
        assets.write_bytes(b"unchanged")
        directory_client = GodotClient(
            project_path=project,
            godot_executable=self.fake_godot,
        )

        directory_result = directory_client.project.create(
            project_name="Reject File Directory"
        )

        self.assert_result(directory_result, ok=False)
        self.assertIn("directory", " ".join(directory_result["errors"]).lower())
        self.assertEqual(b"unchanged", assets.read_bytes())
        self.assertFalse((project / "project.godot").exists())
        self.assertFalse((project / "main.tscn").exists())

    def test_project_create_explicit_directory_and_marker_are_equivalent(
        self,
    ) -> None:
        client = GodotClient(godot_executable=self.fake_godot)
        for identity in ("directory", "project_file"):
            for dry_run in (True, False):
                with self.subTest(identity=identity, dry_run=dry_run):
                    project = self.root / f"create-{identity}-{dry_run}"
                    project_file = project / "project.godot"
                    project_identity = (
                        project_file if identity == "project_file" else project
                    )

                    result = client.project.create(
                        project_path=project_identity,
                        project_name="Project Identity",
                        dry_run=dry_run,
                    )

                    self.assert_result(result, ok=True)
                    self.assertEqual(
                        str(project.resolve(strict=False)),
                        result["payload"]["project_path"],
                    )
                    self.assertEqual(
                        str(project_file.resolve(strict=False)),
                        result["payload"]["project_file"],
                    )
                    self.assertFalse(project_file.is_dir())
                    self.assertEqual(not dry_run, project_file.is_file())
                    self.assertFalse((project_file / "project.godot").exists())

    def test_project_settings_use_the_last_duplicate_definition(self) -> None:
        cases = (
            (
                "same_section_valid_then_missing",
                (
                    "[application]",
                    'run/main_scene="res://main.tscn"',
                    'run/main_scene="res://missing.tscn"',
                ),
                "res://missing.tscn",
                False,
            ),
            (
                "same_section_missing_then_valid",
                (
                    "[application]",
                    'run/main_scene="res://missing.tscn"',
                    'run/main_scene="res://main.tscn"',
                ),
                "res://main.tscn",
                True,
            ),
            (
                "repeated_section_valid_then_missing",
                (
                    "[application]",
                    'run/main_scene="res://main.tscn"',
                    "[application]",
                    'run/main_scene="res://missing.tscn"',
                ),
                "res://missing.tscn",
                False,
            ),
            (
                "repeated_section_missing_then_valid",
                (
                    "[application]",
                    'run/main_scene="res://missing.tscn"',
                    "[application]",
                    'run/main_scene="res://main.tscn"',
                ),
                "res://main.tscn",
                True,
            ),
        )

        for name, settings, expected_scene, expected_ok in cases:
            with self.subTest(name=name):
                self.client._config.project_file.write_text(
                    "\n".join(("config_version=5", *settings, "")),
                    encoding="utf-8",
                )

                info = self.client.project.get_info(probe_version=False)
                validation = self.client.project.validate(check_engine=True)

                self.assert_result(info, ok=True)
                self.assertEqual(expected_scene, info["payload"]["main_scene"])
                self.assert_result(validation, ok=expected_ok)
                self.assertEqual(
                    expected_scene,
                    validation["payload"]["main_scene"],
                )
                self.assertEqual(
                    expected_scene,
                    validation["payload"]["main_scene_load_reference"],
                )
                if expected_ok:
                    self.assertNotIn("missing.tscn", " ".join(validation["errors"]))
                else:
                    self.assertIn("missing.tscn", " ".join(validation["errors"]))

    def test_project_settings_ignore_comments_outside_quoted_values(self) -> None:
        cases = (
            (
                "semicolon_after_value",
                "[application]",
                ('run/main_scene="res://main.tscn" ; inline comment',),
                "",
            ),
            (
                "hash_after_value",
                "[application]",
                ('run/main_scene="res://main.tscn" # inline comment',),
                "",
            ),
            (
                "semicolon_after_section",
                "[application] ; inline comment",
                ('run/main_scene="res://main.tscn"',),
                "",
            ),
            (
                "comment_markers_inside_string",
                "[application]",
                (
                    'config/name="Godot #; Comments" ; actual comment',
                    'run/main_scene="res://main.tscn"',
                ),
                "Godot #; Comments",
            ),
            (
                "escaped_quotes_protect_comment_markers",
                "[application]",
                (
                    r'config/name="Godot \"#;\" Comments" ; actual comment',
                    'run/main_scene="res://main.tscn"',
                ),
                'Godot "#;" Comments',
            ),
        )

        for name, section_line, setting_lines, expected_name in cases:
            with self.subTest(name=name):
                self.client._config.project_file.write_text(
                    "\n".join(("config_version=5", section_line, *setting_lines, "")),
                    encoding="utf-8",
                )

                info = self.client.project.get_info(probe_version=False)
                validation = self.client.project.validate(check_engine=True)

                self.assert_result(info, ok=True)
                self.assertEqual("res://main.tscn", info["payload"]["main_scene"])
                self.assertEqual(expected_name, info["payload"]["project_name"])
                self.assert_result(validation, ok=True)
                self.assertEqual(
                    "res://main.tscn",
                    validation["payload"]["main_scene"],
                )
                self.assertEqual(
                    "res://main.tscn",
                    validation["payload"]["main_scene_load_reference"],
                )

    def test_project_settings_do_not_accept_hash_after_section_tag(self) -> None:
        self.client._config.project_file.write_text(
            "config_version=5\n"
            "[application] # not a Godot section comment\n"
            'run/main_scene="res://main.tscn"\n',
            encoding="utf-8",
        )

        info = self.client.project.get_info(probe_version=False)
        validation = self.client.project.validate(check_engine=True)

        self.assert_result(info, ok=True)
        self.assertEqual("", info["payload"]["main_scene"])
        self.assert_result(validation, ok=True)
        self.assertEqual("", validation["payload"]["main_scene"])
        self.assertEqual("", validation["payload"]["main_scene_load_reference"])
        self.assertIn(
            "GODOT_MAIN_SCENE_UNSET",
            {item["code"] for item in validation["diagnostics"]},
        )

    def test_project_validation_supports_godot_resource_uids(self) -> None:
        valid_uid = "uid://wnp3jskbvlj6"
        project_text = self.client._config.project_file.read_text(encoding="utf-8")
        self.client._config.project_file.write_text(
            project_text.replace("res://main.tscn", valid_uid),
            encoding="utf-8",
        )
        (self.project / "main.tscn").write_text(
            f'[gd_scene format=3 uid="{valid_uid}"]\n\n'
            '[node name="Main" type="Node3D"]\n',
            encoding="utf-8",
        )

        static_validation = self.client.project.validate(check_engine=False)
        self.assert_result(static_validation, ok=True)
        self.assertEqual(
            "res://main.tscn",
            static_validation["payload"]["main_scene_resolved"],
        )
        self.assertEqual(
            "project_resource_uid",
            static_validation["payload"]["main_scene_resolution"],
        )
        native_without_uid_cache = self.client.project.validate()
        self.assert_result(native_without_uid_cache, ok=False)
        self.assertEqual(
            valid_uid,
            native_without_uid_cache["payload"]["main_scene_load_reference"],
        )
        self.assertIn(
            "Resource could not be loaded",
            " ".join(native_without_uid_cache["errors"]),
        )

        with mock.patch.dict(
            os.environ,
            {
                "A3GAME_FAKE_GODOT_UID": valid_uid,
                "A3GAME_FAKE_GODOT_UID_RESOURCE": "res://main.tscn",
            },
            clear=False,
        ):
            native_with_uid_cache = self.client.project.validate()
        self.assert_result(native_with_uid_cache, ok=True)
        self.assertEqual(
            valid_uid,
            native_with_uid_cache["payload"]["main_scene_load_reference"],
        )
        self.assertEqual(
            "res://main.tscn",
            native_with_uid_cache["payload"]["main_scene_resolved"],
        )
        self.assertTrue(native_with_uid_cache["payload"]["main_scene_uid_process"])

        binary_uid = "uid://d3h5n8x1q7m2c"
        self.client._config.project_file.write_text(
            project_text.replace("res://main.tscn", binary_uid),
            encoding="utf-8",
        )
        (self.project / "binary.scn").write_bytes(b"fake-binary-scene")
        with mock.patch.dict(
            os.environ,
            {
                "A3GAME_FAKE_GODOT_UID": binary_uid,
                "A3GAME_FAKE_GODOT_UID_RESOURCE": "res://binary.scn",
            },
            clear=False,
        ):
            native_validation = self.client.project.validate()
        self.assert_result(native_validation, ok=True)
        self.assertEqual(
            binary_uid,
            native_validation["payload"]["main_scene_load_reference"],
        )
        self.assertEqual(
            "godot_resource_load",
            native_validation["payload"]["main_scene_resolution"],
        )
        self.assertTrue(native_validation["payload"]["main_scene_uid_process"])

        missing_uid = "uid://c1missinguid9"
        self.client._config.project_file.write_text(
            project_text.replace("res://main.tscn", missing_uid),
            encoding="utf-8",
        )
        missing_validation = self.client.project.validate(check_engine=False)
        self.assert_result(missing_validation, ok=False)
        self.assertIn(missing_uid, " ".join(missing_validation["errors"]))

        outside_scene = self.root / "outside.tscn"
        outside_scene.write_text(
            '[gd_scene format=3]\n\n[node name="Outside" type="Node3D"]\n',
            encoding="utf-8",
        )
        self.client._config.project_file.write_text(
            project_text.replace("res://main.tscn", "res://../outside.tscn"),
            encoding="utf-8",
        )
        traversal = self.client.project.validate(check_engine=False)
        self.assert_result(traversal, ok=False)
        self.assertIn("unsafe", " ".join(traversal["errors"]))

    def test_project_validation_rejects_unloadable_uid_sidecar_scene(self) -> None:
        sidecar_uid = "uid://s1dec4rscene9"
        project_text = self.client._config.project_file.read_text(encoding="utf-8")
        self.client._config.project_file.write_text(
            project_text.replace("res://main.tscn", sidecar_uid),
            encoding="utf-8",
        )
        scene = self.project / "sidecar.tscn"
        scene.write_text("this is not a Godot scene\n", encoding="utf-8")
        Path(str(scene) + ".uid").write_text(sidecar_uid, encoding="utf-8")

        static_validation = self.client.project.validate(check_engine=False)
        self.assert_result(static_validation, ok=False)
        self.assertIn("[gd_scene] header", " ".join(static_validation["errors"]))

        scene.write_text(
            '[gd_scene format=3]\n\n[node name="Main" type="Node3D"]\n',
            encoding="utf-8",
        )
        with mock.patch.dict(
            os.environ,
            {
                "A3GAME_FAKE_GODOT_UID": sidecar_uid,
                "A3GAME_FAKE_GODOT_UID_RESOURCE": "res://sidecar.tscn",
                "A3GAME_FAKE_GODOT_UNLOADABLE": "1",
            },
            clear=False,
        ):
            native_validation = self.client.project.validate()
        self.assert_result(native_validation, ok=False)
        self.assertEqual(
            sidecar_uid,
            native_validation["payload"]["main_scene_load_reference"],
        )
        self.assertTrue(native_validation["payload"]["main_scene_uid_process"])
        self.assertFalse(
            native_validation["payload"]["main_scene_uid_inspection"]["ok"]
        )
        self.assertIn("PackedScene", " ".join(native_validation["errors"]))

    def test_public_resource_registration_requires_native_project_resource(
        self,
    ) -> None:
        self.assertFalse(hasattr(self.client.assets, "registry"))

        registered = self.client.assets.register_resource(
            resource_path="res://main.tscn",
            asset_type="scene",
            asset_id="existing-main",
        )
        self.assert_result(registered, ok=True)
        record = registered["artifacts"][0]
        self.assertEqual("PackedScene", record["backend_class"])
        self.assertTrue(record["spawnable"])
        self.assertTrue(record["metadata"]["native_inspection"]["ok"])
        self.assertEqual(record["artifact_id"], registered["payload"]["artifact_id"])

        outside = self.root / "outside-register.tscn"
        outside.write_text(
            '[gd_scene format=3]\n\n[node name="Outside" type="Node3D"]\n',
            encoding="utf-8",
        )
        invalid_cases = (
            (
                "missing",
                {"resource_path": "res://never-created.tscn"},
                {},
                "not found",
            ),
            (
                "traversal",
                {"resource_path": "res://../outside-register.tscn"},
                {},
                "non-traversing",
            ),
            (
                "class claim",
                {
                    "resource_path": "res://main.tscn",
                    "backend_class": "Resource",
                },
                {},
                "does not match",
            ),
            (
                "spawnable claim",
                {"resource_path": "res://main.tscn", "spawnable": False},
                {},
                "does not match",
            ),
            (
                "native type",
                {
                    "resource_path": "res://main.tscn",
                    "asset_type": "texture",
                },
                {},
                "Texture2D",
            ),
            (
                "native load",
                {"resource_path": "res://main.tscn"},
                {"A3GAME_FAKE_GODOT_UNLOADABLE": "1"},
                "could not be loaded",
            ),
        )
        for name, overrides, environment, expected_error in invalid_cases:
            with self.subTest(name=name), mock.patch.dict(
                os.environ, environment, clear=False
            ):
                result = self.client.assets.register_resource(
                    asset_type=str(overrides.get("asset_type") or "scene"),
                    asset_id=f"invalid-{name}",
                    resource_path=str(overrides["resource_path"]),
                    backend_class=str(overrides.get("backend_class") or ""),
                    spawnable=overrides.get("spawnable"),
                )
                self.assert_result(result, ok=False)
                self.assertIn(expected_error, " ".join(result["errors"]))

        listed = self.client.assets.list_registered()
        self.assert_result(listed, ok=True)
        self.assertEqual(
            [record["artifact_id"]],
            [item["artifact_id"] for item in listed["artifacts"]],
        )

    def test_public_asset_registry_enforces_strict_json_without_rewriting(
        self,
    ) -> None:
        seeded = self.client.assets.register_resource(
            resource_path="res://main.tscn",
            asset_type="scene",
            asset_id="strict-json-seed",
            metadata={"finite": 1.25},
        )
        self.assert_result(seeded, ok=True)
        seeded_id = seeded["artifacts"][0]["artifact_id"]
        registry_path = self.client._config.artifact_registry_path
        original = registry_path.read_bytes()

        for name, value in (
            ("nan", float("nan")),
            ("positive_infinity", float("inf")),
            ("negative_infinity", float("-inf")),
        ):
            with self.subTest(write=name):
                rejected = self.client.assets.register_resource(
                    resource_path="res://main.tscn",
                    asset_type="scene",
                    asset_id=f"strict-json-{name}",
                    metadata={"nested": {"score": value}},
                )
                self.assert_result(rejected, ok=False)
                self.assertIn("strict JSON", " ".join(rejected["errors"]))
                self.assertEqual(original, registry_path.read_bytes())

        contract_failure = GodotOperationResult.success(
            "strict-json-contract",
            payload={"score": float("nan")},
        ).to_dict()
        self.assert_result(contract_failure, ok=False)
        self.assertIn("strict JSON", " ".join(contract_failure["errors"]))

        original_payload = json.loads(original)
        constants = {
            "nan": ("NaN", "non-standard JSON constant"),
            "positive_infinity": ("Infinity", "non-standard JSON constant"),
            "negative_infinity": ("-Infinity", "non-standard JSON constant"),
            "overflowing_number": ("1e10000", "strict JSON"),
        }
        for name, (constant, expected_error) in constants.items():
            with self.subTest(read=name):
                corrupted = json.loads(json.dumps(original_payload))
                corrupted["artifacts"][0]["metadata"]["nonfinite"] = constant
                serialized = json.dumps(corrupted).replace(f'"{constant}"', constant)
                registry_path.write_text(serialized, encoding="utf-8")
                malformed = registry_path.read_bytes()

                listed = self.client.assets.list_registered()
                metadata = self.client.assets.get_metadata(seeded_id)
                blocked = self.client.assets.register_resource(
                    resource_path="res://main.tscn",
                    asset_type="scene",
                    asset_id=f"blocked-by-{name}",
                )
                for result in (listed, metadata, blocked):
                    self.assert_result(result, ok=False)
                    self.assertIn(expected_error, " ".join(result["errors"]))
                with self.assertRaisesRegex(ValueError, expected_error):
                    self.client.assets._registry.list()
                self.assertEqual(malformed, registry_path.read_bytes())
                registry_path.write_bytes(original)

        listed = self.client.assets.list_registered()
        metadata = self.client.assets.get_metadata(seeded_id)
        self.assert_result(listed, ok=True)
        self.assert_result(metadata, ok=True)
        self.assertEqual(
            [seeded_id], [item["artifact_id"] for item in listed["artifacts"]]
        )

    def test_public_skeletal_resource_registration_uses_browser_contract(
        self,
    ) -> None:
        for asset_type in ("avatar", "motion"):
            with self.subTest(asset_type=asset_type):
                resource = (
                    self.project
                    / "assets"
                    / "imported"
                    / f"{asset_type}s"
                    / f"registered_{asset_type}.tscn"
                )
                resource.parent.mkdir(parents=True, exist_ok=True)
                resource.write_text(
                    '[gd_scene format=3]\n\n[node name="Root" type="Node3D"]\n',
                    encoding="utf-8",
                )
                registered = self.client.assets.register_resource(
                    resource_path=(
                        "res://" + resource.relative_to(self.project).as_posix()
                    ),
                    asset_type=asset_type,
                    asset_id=f"registered-{asset_type}",
                )
                self.assert_result(registered, ok=True)
                metadata = registered["artifacts"][0]["metadata"]
                self.assertEqual("Character/Skeleton3D", metadata["skeleton"])
                self.assertEqual("Character/Skeleton3D", metadata["skeleton_path"])

    @unittest.skipUnless(
        REAL_GODOT,
        "set A3GAME_TEST_GODOT_EXECUTABLE to run native registration checks",
    )
    def test_real_godot_public_resource_registration_contract(self) -> None:
        client = GodotClient(
            project_path=self.project,
            godot_executable=Path(REAL_GODOT).expanduser().resolve(strict=True),
            editor_timeout=30,
            import_timeout=30,
        )
        registered = client.assets.register_resource(
            resource_path="res://main.tscn",
            asset_type="scene",
            asset_id="real-existing-main",
        )
        self.assert_result(registered, ok=True)

        invalid_scene = self.project / "invalid-register.tscn"
        invalid_scene.write_text("not a Godot scene\n", encoding="utf-8")
        unloadable = client.assets.register_resource(
            resource_path="res://invalid-register.tscn",
            asset_type="scene",
            asset_id="real-unloadable",
        )
        missing = client.assets.register_resource(
            resource_path="res://never-created.tscn",
            asset_type="scene",
            asset_id="real-missing",
        )
        self.assert_result(unloadable, ok=False)
        self.assert_result(missing, ok=False)

        listed = client.assets.list_registered()
        self.assert_result(listed, ok=True)
        self.assertEqual(
            [registered["artifacts"][0]["artifact_id"]],
            [item["artifact_id"] for item in listed["artifacts"]],
        )

    @unittest.skipUnless(
        REAL_GODOT,
        "set A3GAME_TEST_GODOT_EXECUTABLE to run corrupt-texture import checks",
    )
    def test_real_godot_rejects_corrupt_embedded_texture_on_both_import_paths(
        self,
    ) -> None:
        client = GodotClient(
            project_path=self.project,
            godot_executable=Path(REAL_GODOT).expanduser().resolve(strict=True),
            editor_timeout=60,
            import_timeout=60,
        )
        source, source_file = self.make_artifact(
            task_id="real-corrupt-texture",
            suffix=".gltf",
            content=self.corrupt_embedded_texture_gltf(),
        )
        registry_path = client._config.artifact_registry_path
        original_registry = (
            registry_path.read_bytes() if registry_path.exists() else None
        )

        rejected = client.assets.import_prop(source)
        self.assert_result(rejected, ok=False)
        self.assertEqual(0, rejected["payload"]["import_process"]["returncode"])
        self.assertIn("ERR_FILE_CORRUPT", " ".join(rejected["errors"]))
        self.assertIn("Couldn't load image", " ".join(rejected["errors"]))
        target = Path(rejected["payload"]["target_path"])
        self.assertFalse(target.exists())
        self.assertFalse(Path(str(target) + ".import").exists())
        self.assertEqual({}, self.godot_import_cache(target))
        if original_registry is None:
            self.assertFalse(registry_path.exists())
        else:
            self.assertEqual(original_registry, registry_path.read_bytes())

        completed = subprocess.run(
            [
                sys.executable,
                "scripts/import_generated_asset.py",
                "--engine",
                "godot",
                "--src",
                str(source_file),
                "--godot",
                str(Path(REAL_GODOT).expanduser().resolve(strict=True)),
                "--godot-project",
                str(self.project),
                "--timeout",
                "60",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(1, completed.returncode, completed.stdout + completed.stderr)
        compatibility_report = json.loads(
            (source_file.parent / "artifact_godot_import.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIs(compatibility_report["ok"], False)
        self.assertEqual(0, compatibility_report["import_process"]["returncode"])
        self.assertIn("ERR_FILE_CORRUPT", compatibility_report["error"])
        self.assertIn("Couldn't load image", compatibility_report["error"])
        self.assertFalse(target.exists())
        self.assertFalse(Path(str(target) + ".import").exists())
        self.assertEqual({}, self.godot_import_cache(target))
        if original_registry is None:
            self.assertFalse(registry_path.exists())
        else:
            self.assertEqual(original_registry, registry_path.read_bytes())

    def test_godot_4_version_gate_applies_to_status_and_import(self) -> None:
        source, _ = self.make_artifact(
            task_id="godot3_prop",
            suffix=".glb",
            content=b"valid-only-to-the-fake",
        )
        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_VERSION": "3.5.3.stable.fake"},
            clear=False,
        ):
            validation = self.client.project.validate()
            status = self.client.observe.check_status()
            info = self.client.get_environment_info()
            imported = self.client.assets.import_prop(source)
        self.assert_result(validation, ok=False)
        self.assert_result(status, ok=False)
        self.assert_result(imported, ok=False)
        for result in (validation, status, imported):
            self.assertIn("Godot 4.x is required", " ".join(result["errors"]))
        self.assertFalse(info["payload"]["engine_version_supported"])
        self.assertEqual(3, info["payload"]["engine_version_major"])
        self.assertFalse(Path(imported["payload"]["target_path"]).exists())
        self.assertIsNone(self.client.assets._registry.find("godot3_prop"))

    def test_cli_configuration_failures_are_structured_json(self) -> None:
        cases = (
            (
                ["--runtime-port", "70000", "info"],
                {},
                "Godot runtime UDP port",
            ),
            (
                [
                    "--project",
                    str(self.project),
                    "--godot",
                    str(self.fake_godot),
                    "info",
                ],
                {"A3GAME_GODOT_EDITOR_TIMEOUT": "0"},
                "editor_timeout",
            ),
        )
        for arguments, overrides, expected_error in cases:
            with self.subTest(arguments=arguments):
                environment = os.environ.copy()
                for name in (
                    "A3GAME_GODOT_RUNTIME_PORT",
                    "A3GAME_GODOT_EDITOR_TIMEOUT",
                    "A3GAME_GODOT_IMPORT_TIMEOUT",
                ):
                    environment.pop(name, None)
                environment.update(overrides)
                process = subprocess.run(
                    [sys.executable, "-m", "engine_adapters.godot", *arguments],
                    cwd=Path(__file__).resolve().parents[1],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(1, process.returncode, process)
                result = json.loads(process.stdout)
                self.assert_result(result, ok=False)
                self.assertEqual("cli.info", result["operation"])
                self.assertIn(expected_error, " ".join(result["errors"]))
                self.assertNotIn("Traceback", process.stderr)

    def test_import_requires_native_load_even_when_import_exits_zero(self) -> None:
        source, _ = self.make_artifact(
            task_id="unloadable",
            suffix=".glb",
            content=b"not-a-real-glb",
        )
        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_UNLOADABLE": "1"},
            clear=False,
        ):
            result = self.client.assets.import_prop(
                source,
                destination="res://new/imports",
            )
        self.assert_result(result, ok=False)
        self.assertEqual(0, result["payload"]["import_process"]["returncode"])
        self.assertIn("could not be loaded", " ".join(result["errors"]))
        self.assertFalse(Path(result["payload"]["target_path"]).exists())
        self.assertFalse(
            Path(str(result["payload"]["target_path"]) + ".import").exists()
        )
        self.assertEqual(
            {}, self.godot_import_cache(Path(result["payload"]["target_path"]))
        )
        self.assertFalse((self.project / "new").exists())
        self.assertIsNone(self.client.assets._registry.find("unloadable"))

        for stream in ("stderr", "stdout"):
            with self.subTest(import_error_stream=stream):
                with mock.patch.dict(
                    os.environ,
                    {
                        "A3GAME_FAKE_GODOT_IMPORT_ERROR_ZERO": "1",
                        "A3GAME_FAKE_GODOT_IMPORT_ERROR_STREAM": stream,
                    },
                    clear=False,
                ):
                    output_failure = self.client.assets.import_prop(source)
                self.assert_result(output_failure, ok=False)
                self.assertIn("despite exit code 0", " ".join(output_failure["errors"]))
                self.assertIn(
                    "ERROR: Error importing",
                    output_failure["payload"]["import_process"][stream],
                )
                target = Path(output_failure["payload"]["target_path"])
                self.assertFalse(target.exists())
                self.assertFalse(Path(str(target) + ".import").exists())
                self.assertEqual({}, self.godot_import_cache(target))
                self.assertIsNone(self.client.assets._registry.find("unloadable"))

        meshless_source, _ = self.make_artifact(
            task_id="meshless_prop",
            suffix=".glb",
            content=b"scene-without-mesh",
        )
        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_NO_MESH": "1"},
            clear=False,
        ):
            meshless = self.client.assets.import_prop(
                meshless_source,
                options={"name": "meshless.glb"},
            )
        self.assert_result(meshless, ok=False)
        self.assertIn("MeshInstance3D", " ".join(meshless["errors"]))
        self.assertFalse(Path(meshless["payload"]["target_path"]).exists())
        self.assertFalse(
            Path(str(meshless["payload"]["target_path"]) + ".import").exists()
        )
        self.assertEqual(
            {}, self.godot_import_cache(Path(meshless["payload"]["target_path"]))
        )

    def test_import_rejects_real_decode_error_text_without_blanket_error_matching(
        self,
    ) -> None:
        self.assertEqual(
            CORRUPT_TEXTURE_IMPORT_ERRORS.splitlines(),
            godot_import_error_lines(CORRUPT_TEXTURE_IMPORT_ERRORS),
        )
        harmless_output = 'ERROR: Parameter "t" is null.'
        self.assertEqual([], godot_import_error_lines(harmless_output))

        source, _ = self.make_artifact(
            task_id="real-editor-diagnostics",
            suffix=".glb",
            content=b"valid-only-to-the-fake",
        )
        with mock.patch.dict(
            os.environ,
            {
                "A3GAME_FAKE_GODOT_IMPORT_ERROR_ZERO": "1",
                "A3GAME_FAKE_GODOT_IMPORT_ERROR_TEXT": (CORRUPT_TEXTURE_IMPORT_ERRORS),
            },
            clear=False,
        ):
            rejected = self.client.assets.import_prop(source)
        self.assert_result(rejected, ok=False)
        self.assertIn("ERR_FILE_CORRUPT", " ".join(rejected["errors"]))
        self.assertIn("Couldn't load image", " ".join(rejected["errors"]))
        target = Path(rejected["payload"]["target_path"])
        self.assertFalse(target.exists())
        self.assertFalse(Path(str(target) + ".import").exists())
        self.assertEqual({}, self.godot_import_cache(target))
        self.assertIsNone(self.client.assets._registry.find("real-editor-diagnostics"))

        with mock.patch.dict(
            os.environ,
            {
                "A3GAME_FAKE_GODOT_IMPORT_ERROR_ZERO": "1",
                "A3GAME_FAKE_GODOT_IMPORT_ERROR_TEXT": harmless_output,
            },
            clear=False,
        ):
            accepted = self.client.assets.import_prop(source)
        self.assert_result(accepted, ok=True)
        self.assertTrue(Path(accepted["payload"]["target_path"]).is_file())

    def test_asset_import_reflection_binding_animation_and_rollback(self) -> None:
        prop_source, prop_file = self.make_artifact(
            task_id="crate", suffix=".glb", content=b"original-glb"
        )
        imported = self.client.assets.import_prop(prop_source)
        self.assert_result(imported, ok=True)
        prop = imported["artifacts"][0]
        target = Path(imported["payload"]["target_path"])
        import_sidecar = Path(str(target) + ".import")
        self.assertEqual(b"original-glb", target.read_bytes())
        self.assertTrue(import_sidecar.is_file())
        original_import_sidecar = import_sidecar.read_bytes()
        original_import_cache = self.godot_import_cache(target)
        self.assertTrue(original_import_cache)
        self.assertIn("--import", imported["payload"]["import_process"]["command"])

        inspected = self.client.reflection.inspect_artifact(prop["artifact_id"])
        self.assert_result(inspected, ok=True)
        self.assertEqual(
            "PackedScene", inspected["payload"]["inspection"]["resource_class"]
        )
        invalid_destination = self.client.assets.import_prop(
            prop_source, destination="res://../escape"
        )
        self.assert_result(invalid_destination, ok=False)

        prop_file.write_bytes(b"replacement-glb")
        with mock.patch.dict(
            os.environ, {"A3GAME_FAKE_GODOT_FAIL_IMPORT": "1"}, clear=False
        ):
            failed = self.client.assets.import_prop(
                prop_source, options={"replace_existing": True}
            )
        self.assert_result(failed, ok=False)
        self.assertEqual(b"original-glb", target.read_bytes())
        self.assertEqual(original_import_sidecar, import_sidecar.read_bytes())
        self.assertEqual(original_import_cache, self.godot_import_cache(target))
        registered = self.client.assets.get_metadata(prop["artifact_id"])
        self.assert_result(registered, ok=True)
        prop_registry_before = self.client._config.artifact_registry_path.read_bytes()
        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_UNLOADABLE": "1"},
            clear=False,
        ):
            failed_native_load = self.client.assets.import_prop(
                prop_source,
                options={"replace_existing": True},
            )
        self.assert_result(failed_native_load, ok=False)
        self.assertEqual(b"original-glb", target.read_bytes())
        self.assertEqual(original_import_sidecar, import_sidecar.read_bytes())
        self.assertEqual(original_import_cache, self.godot_import_cache(target))
        self.assertEqual(
            prop_registry_before,
            self.client._config.artifact_registry_path.read_bytes(),
        )

        avatar_source, _ = self.make_artifact(
            task_id="avatar", suffix=".glb", content=b"avatar-glb"
        )
        avatar = self.client.assets.import_avatar(
            avatar_source, options={"skeleton_path": "Character/Skeleton3D"}
        )
        self.assert_result(avatar, ok=True)
        self.assertEqual(
            "Character/Skeleton3D",
            avatar["artifacts"][0]["metadata"]["skeleton_path"],
        )
        skeleton = self.client.animation.resolve_skeleton(
            avatar["artifacts"][0]["artifact_id"]
        )
        self.assert_result(skeleton, ok=True)

        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_NO_SKIN_LINK": "1"},
            clear=False,
        ):
            unbound_avatar_source, _ = self.make_artifact(
                task_id="unbound_avatar",
                suffix=".glb",
                content=b"unbound-avatar-glb",
            )
            unbound_avatar = self.client.assets.import_avatar(
                unbound_avatar_source,
                options={
                    "name": "unbound_avatar.glb",
                    "skeleton_path": "Character/Skeleton3D",
                },
            )
        self.assert_result(unbound_avatar, ok=False)
        self.assertIn("bound to a live Skeleton3D", " ".join(unbound_avatar["errors"]))
        self.assertFalse(Path(unbound_avatar["payload"]["target_path"]).exists())

        motion_source, _ = self.make_artifact(
            task_id="walk",
            suffix=".glb",
            content=b"motion-glb",
            task_kind="motion",
            artifact_key="retargeted_glb_path",
        )
        motion = self.client.animation.import_motion(
            motion_source, skeleton="Character/Skeleton3D"
        )
        self.assert_result(motion, ok=True)
        self.assertEqual("PackedScene", motion["artifacts"][0]["backend_class"])
        self.assertEqual(
            "Character/Skeleton3D",
            motion["artifacts"][0]["metadata"]["skeleton"],
        )
        self.assertEqual(
            "Character/Skeleton3D",
            motion["artifacts"][0]["metadata"]["skeleton_path"],
        )
        self.assertEqual(
            ["Walk"],
            motion["payload"]["inspection"]["animations"],
        )
        compatible = self.client.animation.validate_compatibility(
            motion["artifacts"][0]["artifact_id"], "Character/Skeleton3D"
        )
        self.assert_result(compatible, ok=True)

        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_NO_SKELETON": "1"},
            clear=False,
        ):
            no_skeleton_source, _ = self.make_artifact(
                task_id="root_only",
                suffix=".glb",
                content=b"root-animation-only",
                task_kind="motion",
                artifact_key="retargeted_glb_path",
            )
            no_skeleton = self.client.animation.import_motion(
                no_skeleton_source,
                skeleton="Character/Skeleton3D",
                options={"name": "root_only.glb"},
            )
        self.assert_result(no_skeleton, ok=False)
        self.assertIn("Skeleton3D", " ".join(no_skeleton["errors"]))
        self.assertFalse(Path(no_skeleton["payload"]["target_path"]).exists())

        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_NO_BONE_TRACK": "1"},
            clear=False,
        ):
            no_tracks_source, _ = self.make_artifact(
                task_id="no_bone_tracks",
                suffix=".glb",
                content=b"non-skeletal-animation",
                task_kind="motion",
                artifact_key="retargeted_glb_path",
            )
            no_tracks = self.client.animation.import_motion(
                no_tracks_source,
                skeleton="Character/Skeleton3D",
                options={"name": "no_bone_tracks.glb"},
            )
        self.assert_result(no_tracks, ok=False)
        self.assertIn("bone-targeted", " ".join(no_tracks["errors"]))
        self.assertFalse(Path(no_tracks["payload"]["target_path"]).exists())

        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_PROPERTY_TRACK": "1"},
            clear=False,
        ):
            property_track_source, _ = self.make_artifact(
                task_id="property_track",
                suffix=".glb",
                content=b"property-animation",
                task_kind="motion",
                artifact_key="retargeted_glb_path",
            )
            property_track = self.client.animation.import_motion(
                property_track_source,
                skeleton="Character/Skeleton3D",
                options={"name": "property_track.glb"},
            )
        self.assert_result(property_track, ok=False)
        self.assertIn("live Skeleton3D bone", " ".join(property_track["errors"]))
        self.assertFalse(Path(property_track["payload"]["target_path"]).exists())

        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_TRACK_OTHER_SKELETON": "1"},
            clear=False,
        ):
            wrong_target_source, _ = self.make_artifact(
                task_id="wrong_skeleton_target",
                suffix=".glb",
                content=b"wrong-skeleton-target",
                task_kind="motion",
                artifact_key="retargeted_glb_path",
            )
            wrong_target = self.client.animation.import_motion(
                wrong_target_source,
                skeleton="Character/Skeleton3D",
                options={"name": "wrong_skeleton_target.glb"},
            )
        self.assert_result(wrong_target, ok=False)
        self.assertIn(
            "drives the requested live Skeleton3D",
            " ".join(wrong_target["errors"]),
        )
        self.assertFalse(Path(wrong_target["payload"]["target_path"]).exists())

        texture_source, texture_file = self.make_artifact(
            task_id="crate_texture", suffix=".png", content=b"not-empty-png"
        )
        binding = self.client.bindings.bind_pbr_material(
            asset_id="crate_material",
            source=texture_source,
            mesh_assets=[prop["artifact_id"]],
        )
        self.assert_result(binding, ok=True)
        material_path = Path(binding["payload"]["material_path"])
        binding_path = Path(binding["payload"]["binding_path"])
        self.assertTrue(material_path.is_file())
        bound_prop = self.client.assets._registry.get(prop["artifact_id"])
        self.assertIsNotNone(bound_prop)
        self.assertTrue(bound_prop.backend_path.endswith(".tscn"))
        bound_scene = self.project / bound_prop.backend_path[len("res://") :]
        self.assertTrue(bound_scene.is_file())
        self.assertIn(
            binding["payload"]["material_resource"],
            bound_scene.read_text(encoding="utf-8"),
        )
        binding_manifest = json.loads(binding_path.read_text(encoding="utf-8"))
        self.assertEqual(
            bound_prop.backend_path,
            binding_manifest["mesh_bindings"][0]["bound_resource"],
        )
        self.assertGreater(
            binding_manifest["mesh_bindings"][0]["mesh_instance_count"], 0
        )
        original_material = material_path.read_bytes()
        original_binding = binding_path.read_bytes()
        original_bound_scene = bound_scene.read_bytes()
        texture_record = self.client.assets._registry.find("crate_material_albedo")
        self.assertIsNotNone(texture_record)
        texture_backend_path = str(texture_record.backend_path)
        texture_path = self.project / texture_backend_path[len("res://") :]
        original_texture = texture_path.read_bytes()
        texture_import_sidecar = Path(str(texture_path) + ".import")
        self.assertTrue(texture_import_sidecar.is_file())
        original_texture_import_sidecar = texture_import_sidecar.read_bytes()
        original_texture_import_cache = self.godot_import_cache(texture_path)
        self.assertTrue(original_texture_import_cache)
        original_registry = self.client._config.artifact_registry_path.read_bytes()
        texture_file.write_bytes(b"replacement-png")
        failed_process = mock.Mock(returncode=7, stderr="forced", stdout="")
        failed_process.to_dict.return_value = {
            "returncode": 7,
            "stdout": "",
            "stderr": "forced",
        }
        with mock.patch.object(
            self.client.bindings._transport, "run", return_value=failed_process
        ):
            failed_binding = self.client.bindings.bind_pbr_material(
                asset_id="crate_material",
                source=texture_source,
                mesh_assets=[prop["artifact_id"]],
                options={"replace_existing": True, "metallic": 0.5},
            )
        self.assert_result(failed_binding, ok=False)
        self.assertEqual(original_material, material_path.read_bytes())
        self.assertEqual(original_binding, binding_path.read_bytes())
        self.assertEqual(original_texture, texture_path.read_bytes())
        self.assertEqual(
            original_texture_import_sidecar,
            texture_import_sidecar.read_bytes(),
        )
        self.assertEqual(
            original_texture_import_cache,
            self.godot_import_cache(texture_path),
        )
        self.assertEqual(
            original_registry, self.client._config.artifact_registry_path.read_bytes()
        )
        with mock.patch.dict(
            os.environ, {"A3GAME_FAKE_GODOT_FAIL_BINDING": "1"}, clear=False
        ):
            failed_apply = self.client.bindings.bind_pbr_material(
                asset_id="crate_material",
                source=texture_source,
                mesh_assets=[prop["artifact_id"]],
                options={"replace_existing": True, "metallic": 0.5},
            )
        self.assert_result(failed_apply, ok=False)
        self.assertEqual(original_material, material_path.read_bytes())
        self.assertEqual(original_binding, binding_path.read_bytes())
        self.assertEqual(original_bound_scene, bound_scene.read_bytes())
        self.assertEqual(original_texture, texture_path.read_bytes())
        self.assertEqual(
            original_texture_import_sidecar,
            texture_import_sidecar.read_bytes(),
        )
        self.assertEqual(
            original_texture_import_cache,
            self.godot_import_cache(texture_path),
        )
        self.assertEqual(
            original_registry, self.client._config.artifact_registry_path.read_bytes()
        )

    @unittest.skipUnless(os.name == "posix", "symlink boundary probe requires POSIX")
    def test_binding_rejects_linked_state_parent_before_import(self) -> None:
        mesh = self.client.assets._register_resource(
            resource_path="res://main.tscn",
            asset_type="prop",
            asset_id="linked-binding-mesh",
            backend_class="PackedScene",
        )
        texture_source, _ = self.make_artifact(
            task_id="linked_binding_texture",
            suffix=".png",
            content=b"must-not-import",
        )
        data_root = self.client._config.data_root
        data_root.mkdir(parents=True, exist_ok=True)
        outside = self.root / "outside-binding-state"
        outside.mkdir()
        victim = outside / "linked-binding.json"
        victim.write_bytes(b"USER OWNED BINDING\n")
        (data_root / "bindings").symlink_to(outside, target_is_directory=True)
        registry_before = self.client._config.artifact_registry_path.read_bytes()

        with mock.patch.object(self.client.bindings._transport, "run") as run:
            result = self.client.bindings.bind_pbr_material(
                asset_id="linked-binding",
                source=texture_source,
                mesh_assets=[mesh.artifact_id],
            )

        self.assert_result(result, ok=False)
        self.assertIn("symlink", " ".join(result["errors"]).lower())
        run.assert_not_called()
        self.assertEqual(b"USER OWNED BINDING\n", victim.read_bytes())
        self.assertEqual(
            registry_before,
            self.client._config.artifact_registry_path.read_bytes(),
        )
        self.assertFalse(
            (self.project / "assets/imported/textures/linked-binding.png").exists()
        )

    def test_gltf_sidecars_are_staged_and_rolled_back_together(self) -> None:
        descriptor, gltf = self.make_artifact(
            task_id="gltf_prop",
            suffix=".gltf",
            content=json.dumps(
                {
                    "buffers": [{"uri": "mesh.bin"}],
                    "images": [{"uri": "textures/my%20image.png"}],
                }
            ).encode(),
        )
        sidecar = gltf.parent / "mesh.bin"
        sidecar.write_bytes(b"original-sidecar")
        texture_sidecar = gltf.parent / "textures" / "my image.png"
        texture_sidecar.parent.mkdir()
        texture_sidecar.write_bytes(b"original-texture")
        imported = self.client.assets.import_prop(descriptor)
        self.assert_result(imported, ok=True)
        target = Path(imported["payload"]["target_path"])
        target_sidecar = target.parent / "mesh.bin"
        self.assertEqual(b"original-sidecar", target_sidecar.read_bytes())
        target_texture = target.parent / "textures" / "my image.png"
        self.assertEqual(b"original-texture", target_texture.read_bytes())

        conflict_descriptor, conflict_gltf = self.make_artifact(
            task_id="gltf_sidecar_conflict",
            suffix=".gltf",
            content=json.dumps({"buffers": [{"uri": "nested/mesh.bin"}]}).encode(),
        )
        (conflict_gltf.parent / "nested").mkdir()
        (conflict_gltf.parent / "nested" / "mesh.bin").write_bytes(b"new-sidecar")
        conflict_target = target.parent / "new-model.gltf"
        conflict_sidecar = target.parent / "nested" / "mesh.bin"
        conflict_sidecar.parent.mkdir(parents=True, exist_ok=True)
        conflict_sidecar.write_bytes(b"keep-sidecar")
        conflict = self.client.assets.import_prop(
            conflict_descriptor, options={"name": "new-model.gltf"}
        )
        self.assert_result(conflict, ok=False)
        self.assertFalse(conflict_target.exists())
        self.assertEqual(b"keep-sidecar", conflict_sidecar.read_bytes())

        unsafe_descriptor, _unsafe_gltf = self.make_artifact(
            task_id="gltf_encoded_traversal",
            suffix=".gltf",
            content=json.dumps({"buffers": [{"uri": "%2e%2e/outside.bin"}]}).encode(),
        )
        unsafe = self.client.assets.import_prop(
            unsafe_descriptor, options={"name": "unsafe.gltf"}
        )
        self.assert_result(unsafe, ok=False)
        self.assertFalse((target.parent / "unsafe.gltf").exists())

        gltf.write_text(
            json.dumps(
                {
                    "buffers": [{"uri": "mesh.bin"}],
                    "images": [{"uri": "textures/my%20image.png"}],
                    "extras": {"v": 2},
                }
            ),
            encoding="utf-8",
        )
        sidecar.write_bytes(b"replacement-sidecar")
        texture_sidecar.write_bytes(b"replacement-texture")
        with mock.patch.dict(
            os.environ, {"A3GAME_FAKE_GODOT_FAIL_IMPORT": "1"}, clear=False
        ):
            failed = self.client.assets.import_prop(
                descriptor, options={"replace_existing": True}
            )
        self.assert_result(failed, ok=False)
        self.assertNotIn('"v": 2', target.read_text(encoding="utf-8"))
        self.assertEqual(b"original-sidecar", target_sidecar.read_bytes())
        self.assertEqual(b"original-texture", target_texture.read_bytes())

    def test_asset_validation_runs_the_same_source_and_path_preflight_as_import(
        self,
    ) -> None:
        missing_descriptor, _missing_gltf = self.make_artifact(
            task_id="missing_gltf_sidecar",
            suffix=".gltf",
            content=json.dumps(
                {
                    "asset": {"version": "2.0"},
                    "buffers": [{"uri": "missing.bin"}],
                }
            ).encode(),
        )
        validation = self.client.assets.validate(missing_descriptor, "prop")
        imported = self.client.assets.import_prop(missing_descriptor)
        self.assert_result(validation, ok=False)
        self.assert_result(imported, ok=False)
        self.assertIn("sidecar was not found", " ".join(validation["errors"]))
        self.assertIn("sidecar was not found", " ".join(imported["errors"]))

        linked_descriptor, linked_source = self.make_artifact(
            task_id="linked_import_sidecar",
            suffix=".glb",
            content=b"generated-glb",
        )
        linked_target = (
            self.project / "assets" / "imported" / "props" / linked_source.name
        )
        linked_target.parent.mkdir(parents=True, exist_ok=True)
        outside_import = self.root / "outside-generated.import"
        Path(str(linked_target) + ".import").symlink_to(outside_import)
        linked_validation = self.client.assets.validate(linked_descriptor, "prop")
        linked_import = self.client.assets.import_prop(
            linked_descriptor,
            options={"replace_existing": True},
        )
        self.assert_result(linked_validation, ok=False)
        self.assert_result(linked_import, ok=False)
        self.assertIn("symlink", " ".join(linked_validation["errors"]))
        self.assertIn("symlink", " ".join(linked_import["errors"]))
        self.assertFalse(outside_import.exists())

        task_dir = paths.task_output_dir(
            "godot_adapter_test",
            "3d_scene",
            "linked_source_tree",
            run_id="run_001",
        )
        source_tree = task_dir / "environment"
        source_tree.mkdir()
        (source_tree / "environment.tscn").write_text(
            '[gd_scene format=3]\n\n[node name="Environment" type="Node3D"]\n',
            encoding="utf-8",
        )
        outside_source = self.root / "outside-source"
        outside_source.mkdir()
        (source_tree / "linked").symlink_to(
            outside_source,
            target_is_directory=True,
        )
        (task_dir / "meta.json").write_text(
            json.dumps(
                {
                    "game_id": "godot_adapter_test",
                    "run_id": "run_001",
                    "task_kind": "3d_scene",
                    "task_id": "linked_source_tree",
                    "scene_path": str(source_tree),
                }
            ),
            encoding="utf-8",
        )
        directory_validation = self.client.assets.validate(
            {
                "game_id": "godot_adapter_test",
                "run_id": "run_001",
                "task_kind": "3d_scene",
                "task_id": "linked_source_tree",
                "artifact_key": "scene_path",
            },
            "environment",
            options={"entrypoint": "environment.tscn"},
        )
        self.assert_result(directory_validation, ok=False)
        self.assertIn("symbolic links", " ".join(directory_validation["errors"]))

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "mkfifo") and hasattr(socket, "AF_UNIX"),
        "special filesystem node probes require POSIX",
    )
    def test_asset_and_plugin_directory_sources_reject_special_nodes(self) -> None:
        def create_special_node(path: Path, kind: str) -> None:
            if kind == "fifo":
                os.mkfifo(path)
                return
            unix_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                unix_socket.bind(str(path))
            finally:
                unix_socket.close()

        for kind in ("fifo", "socket"):
            with self.subTest(node_type=kind):
                game_id = "g"
                run_id = "r"
                task_id = f"s_{kind[0]}"
                task_dir = paths.task_output_dir(
                    game_id,
                    "3d_scene",
                    task_id,
                    run_id=run_id,
                )
                scene_tree = task_dir / "s"
                scene_tree.mkdir()
                (scene_tree / "main.tscn").write_text(
                    '[gd_scene format=3]\n\n[node name="Main" type="Node3D"]\n',
                    encoding="utf-8",
                )
                create_special_node(scene_tree / "n", kind)

                plugin_tree = task_dir / "p"
                plugin_tree.mkdir()
                (plugin_tree / "plugin.cfg").write_text(
                    '[plugin]\nname="Special Node Probe"\n'
                    'description=""\nauthor="Test"\nversion="1.0"\n'
                    'script="plugin.gd"\n',
                    encoding="utf-8",
                )
                create_special_node(plugin_tree / "n", kind)

                (task_dir / "meta.json").write_text(
                    json.dumps(
                        {
                            "game_id": game_id,
                            "run_id": run_id,
                            "task_kind": "3d_scene",
                            "task_id": task_id,
                            "scene_path": str(scene_tree),
                            "plugin_path": str(plugin_tree),
                        }
                    ),
                    encoding="utf-8",
                )
                base_descriptor = {
                    "game_id": game_id,
                    "run_id": run_id,
                    "task_kind": "3d_scene",
                    "task_id": task_id,
                }
                scene_descriptor = {
                    **base_descriptor,
                    "artifact_key": "scene_path",
                }
                validation = self.client.assets.validate(
                    scene_descriptor,
                    "scene",
                    options={"entrypoint": "main.tscn"},
                )
                imported = self.client.assets.import_scene(
                    scene_descriptor,
                    options={"entrypoint": "main.tscn"},
                )
                plugin = self.client.plugin.install(
                    {**base_descriptor, "artifact_key": "plugin_path"},
                    install_dir=f"special_{kind}",
                )
                for result in (validation, imported, plugin):
                    self.assert_result(result, ok=False)
                    self.assertIn(
                        "regular files and directories",
                        " ".join(result["errors"]),
                    )
                self.assertFalse((self.project / "addons" / f"special_{kind}").exists())

    def test_build_rejects_protected_and_unmanaged_outputs(self) -> None:
        presets_path = self.project / "export_presets.cfg"
        presets_path.write_text(
            '[preset.0]\n\nname="Linux"\nplatform="Linux/X11"\n',
            encoding="utf-8",
        )
        project_file = self.project / "project.godot"
        main_scene = self.project / "main.tscn"
        preserved = {
            project_file: project_file.read_bytes(),
            presets_path: presets_path.read_bytes(),
            main_scene: main_scene.read_bytes(),
        }

        with mock.patch.object(self.client.build._transport, "run") as run:
            for output_path, expected_error in (
                ("project.godot", "protected input"),
                ("export_presets.cfg", "protected input"),
                ("main.tscn", "ownership manifest"),
            ):
                for dry_run in (True, False):
                    with self.subTest(output=output_path, dry_run=dry_run):
                        result = self.client.build.project(
                            preset="Linux",
                            output_path=output_path,
                            dry_run=dry_run,
                        )
                        self.assert_result(result, ok=False)
                        self.assertIn(expected_error, " ".join(result["errors"]))
            run.assert_not_called()

        self.assertEqual(
            preserved,
            {path: path.read_bytes() for path in preserved},
        )

        ownership_key = self.client._config.data_root / "build" / "export-ownership.key"
        with mock.patch.object(self.client.build._transport, "run") as run:
            protected_key = self.client.build.project(
                preset="Linux",
                output_path=ownership_key,
                allow_external_output=True,
            )
            self.assert_result(protected_key, ok=False)
            self.assertIn("protected input", " ".join(protected_key["errors"]))
            run.assert_not_called()

        web_root = self.project / "builds" / "unmanaged-web"
        web_root.mkdir(parents=True)
        unmanaged_companion = web_root / "index.js"
        unmanaged_companion.write_bytes(b"user-owned-javascript")
        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_EXPORT_COMPANIONS": "1"},
            clear=False,
        ):
            collision = self.client.build.project(
                preset="Linux",
                output_path=web_root / "index.html",
            )
        self.assert_result(collision, ok=False)
        self.assertIn("unmanaged existing path", " ".join(collision["errors"]))
        self.assertEqual(b"user-owned-javascript", unmanaged_companion.read_bytes())
        self.assertFalse((web_root / "index.html").exists())
        self.assertEqual([], list(web_root.glob(".a3game-godot-export-*.json")))
        self.assertEqual([], list(web_root.glob(".a3game-godot-export-stage-*")))

    @unittest.skipUnless(os.name == "posix", "symlink boundary probe requires POSIX")
    def test_build_rejects_linked_state_parent_before_launch(self) -> None:
        (self.project / "export_presets.cfg").write_text(
            '[preset.0]\n\nname="Linux"\nplatform="Linux/X11"\n',
            encoding="utf-8",
        )
        data_root = self.client._config.data_root
        data_root.mkdir(parents=True, exist_ok=True)
        outside = self.root / "outside-build-state"
        outside.mkdir()
        (data_root / "build").symlink_to(outside, target_is_directory=True)

        with mock.patch.object(self.client.build._transport, "run") as run:
            result = self.client.build.project(
                preset="Linux",
                output_path="builds/linked-state/game.x86_64",
            )

        self.assert_result(result, ok=False)
        self.assertIn("symlink", " ".join(result["errors"]).lower())
        run.assert_not_called()
        self.assertEqual([], list(outside.iterdir()))

    def test_build_regenerates_missing_authenticated_web_companion(self) -> None:
        (self.project / "export_presets.cfg").write_text(
            '[preset.0]\n\nname="Web"\nplatform="Web"\n',
            encoding="utf-8",
        )
        output = self.project / "builds" / "recover-web" / "index.html"
        environment = {"A3GAME_FAKE_GODOT_EXPORT_COMPANIONS": "1"}
        with mock.patch.dict(os.environ, environment, clear=False):
            seeded = self.client.build.project(preset="Web", output_path=output)
        self.assert_result(seeded, ok=True)
        missing_companion = output.with_suffix(".wasm")
        missing_companion.unlink()

        with mock.patch.dict(os.environ, environment, clear=False):
            rebuilt = self.client.build.project(preset="Web", output_path=output)

        self.assert_result(rebuilt, ok=True)
        self.assertEqual(b"fake-godot-export.wasm", missing_companion.read_bytes())
        manifest = json.loads(
            Path(rebuilt["payload"]["export_manifest_path"]).read_text(encoding="utf-8")
        )
        self.assertIn(
            missing_companion.name,
            [record["name"] for record in manifest["produced"]],
        )

    @unittest.skipUnless(os.name == "posix", "symlink boundary probe requires POSIX")
    def test_build_rejects_nested_export_symlink_before_first_commit(self) -> None:
        (self.project / "export_presets.cfg").write_text(
            '[preset.0]\n\nname="macOS"\nplatform="macOS"\n',
            encoding="utf-8",
        )
        output = self.project / "builds" / "first.app"
        project_file = self.project / "project.godot"
        original_project = project_file.read_bytes()
        from engine_adapters.godot.build import client as build_module

        real_run = build_module.GodotTransport.run

        def export_with_nested_link(transport, arguments, **kwargs):
            result = real_run(transport, arguments, **kwargs)
            staged_output = Path(arguments[2])
            resources = staged_output / "Contents" / "Resources"
            resources.mkdir(parents=True, exist_ok=True)
            (resources / "project-config").symlink_to(project_file)
            return result

        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_EXPORT_DIRECTORY": "1"},
            clear=False,
        ), mock.patch.object(
            build_module.GodotTransport,
            "run",
            new=export_with_nested_link,
        ):
            result = self.client.build.project(
                preset="macOS",
                output_path=output,
            )

        self.assert_result(result, ok=False)
        self.assertIn("symlink", " ".join(result["errors"]).lower())
        self.assertEqual(original_project, project_file.read_bytes())
        self.assertFalse(output.exists())
        self.assertEqual([], list(output.parent.glob(".a3game-godot-export-*.json")))
        self.assertEqual([], list(output.parent.glob(".a3game-godot-export-stage-*")))

    @unittest.skipUnless(os.name == "posix", "symlink boundary probe requires POSIX")
    def test_build_rejects_nested_export_symlinks_and_preserves_owned_tree(
        self,
    ) -> None:
        (self.project / "export_presets.cfg").write_text(
            '[preset.0]\n\nname="macOS"\nplatform="macOS"\n',
            encoding="utf-8",
        )
        output = self.project / "builds" / "game.app"
        environment = {
            "A3GAME_FAKE_GODOT_EXPORT_DIRECTORY": "1",
            "A3GAME_FAKE_GODOT_EXPORT_PAYLOAD": "original-directory-export",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            seeded = self.client.build.project(
                preset="macOS",
                output_path=output,
            )
        self.assert_result(seeded, ok=True)
        manifest = Path(seeded["payload"]["export_manifest_path"])
        original_manifest = manifest.read_bytes()
        original_tree = {
            path.relative_to(output): path.read_bytes()
            for path in output.rglob("*")
            if path.is_file()
        }
        outside = self.root / "mutable-external-input"
        outside.write_bytes(b"external")
        protected_targets = {
            "project": self.project / "project.godot",
            "ownership-key": (
                self.client._config.data_root / "build" / "export-ownership.key"
            ),
            "external": outside,
        }
        from engine_adapters.godot.build import client as build_module

        real_run = build_module.GodotTransport.run
        for label, protected_target in protected_targets.items():
            with self.subTest(link_target=label):

                def export_with_nested_link(
                    transport,
                    arguments,
                    *,
                    link_target=protected_target,
                    **kwargs,
                ):
                    result = real_run(transport, arguments, **kwargs)
                    staged_output = Path(arguments[2])
                    resources = staged_output / "Contents" / "Resources"
                    resources.mkdir(parents=True, exist_ok=True)
                    (resources / "mutable-link").symlink_to(link_target)
                    return result

                with mock.patch.dict(
                    os.environ,
                    {
                        **environment,
                        "A3GAME_FAKE_GODOT_EXPORT_PAYLOAD": "replacement-export",
                    },
                    clear=False,
                ), mock.patch.object(
                    build_module.GodotTransport,
                    "run",
                    new=export_with_nested_link,
                ):
                    rejected = self.client.build.project(
                        preset="macOS",
                        output_path=output,
                    )
                self.assert_result(rejected, ok=False)
                self.assertIn("symlink", " ".join(rejected["errors"]).lower())
                self.assertEqual(original_manifest, manifest.read_bytes())
                self.assertEqual(
                    original_tree,
                    {
                        path.relative_to(output): path.read_bytes()
                        for path in output.rglob("*")
                        if path.is_file()
                    },
                )
                self.assertEqual(
                    [],
                    list(output.parent.glob(".a3game-godot-export-stage-*")),
                )

    def test_build_rebuilds_owned_directory_exports_and_rejects_tampering(
        self,
    ) -> None:
        (self.project / "export_presets.cfg").write_text(
            '[preset.0]\n\nname="macOS"\nplatform="macOS"\n',
            encoding="utf-8",
        )
        output = self.project / "builds" / "game.app"
        executable = output / "Contents" / "MacOS" / "game"
        directory_environment = {
            "A3GAME_FAKE_GODOT_EXPORT_DIRECTORY": "1",
            "A3GAME_FAKE_GODOT_EXPORT_PAYLOAD": "first-directory-export",
        }
        with mock.patch.dict(os.environ, directory_environment, clear=False):
            seeded = self.client.build.project(
                preset="macOS",
                output_path=output,
            )
        self.assert_result(seeded, ok=True)
        self.assertTrue(output.is_dir())
        self.assertEqual(b"first-directory-export", executable.read_bytes())
        manifest = Path(seeded["payload"]["export_manifest_path"])
        original_manifest = manifest.read_bytes()
        manifest_payload = json.loads(original_manifest)
        output_record = next(
            record
            for record in manifest_payload["produced"]
            if record["name"] == output.name
        )
        self.assertEqual("directory", output_record["kind"])

        with mock.patch.dict(
            os.environ,
            {
                **directory_environment,
                "A3GAME_FAKE_GODOT_EXPORT_PAYLOAD": "second-directory-export",
            },
            clear=False,
        ):
            rebuilt = self.client.build.project(
                preset="macOS",
                output_path=output,
            )
        self.assert_result(rebuilt, ok=True)
        self.assertEqual(b"second-directory-export", executable.read_bytes())
        rebuilt_manifest = manifest.read_bytes()

        executable.write_bytes(b"user-tampered-directory-export")
        with mock.patch.object(self.client.build._transport, "run") as run:
            tampered_output = self.client.build.project(
                preset="macOS",
                output_path=output,
            )
        self.assert_result(tampered_output, ok=False)
        self.assertIn("ownership proof", " ".join(tampered_output["errors"]))
        run.assert_not_called()
        self.assertEqual(b"user-tampered-directory-export", executable.read_bytes())

        executable.write_bytes(b"second-directory-export")
        manifest.write_text("{}\n", encoding="utf-8")
        with mock.patch.object(self.client.build._transport, "run") as run:
            tampered_manifest = self.client.build.project(
                preset="macOS",
                output_path=output,
            )
        self.assert_result(tampered_manifest, ok=False)
        self.assertIn("manifest", " ".join(tampered_manifest["errors"]))
        run.assert_not_called()
        self.assertEqual(b"second-directory-export", executable.read_bytes())
        manifest.write_bytes(rebuilt_manifest)

        unmanaged = self.project / "builds" / "unmanaged.app"
        unmanaged.mkdir()
        unmanaged_marker = unmanaged / "user-data"
        unmanaged_marker.write_bytes(b"preserve")
        with mock.patch.object(self.client.build._transport, "run") as run:
            unmanaged_result = self.client.build.project(
                preset="macOS",
                output_path=unmanaged,
            )
        self.assert_result(unmanaged_result, ok=False)
        self.assertIn("ownership manifest", " ".join(unmanaged_result["errors"]))
        run.assert_not_called()
        self.assertEqual(b"preserve", unmanaged_marker.read_bytes())

    @unittest.skipUnless(os.name == "posix", "special node probe requires POSIX")
    def test_build_directory_export_rejects_special_nodes_and_rolls_back(
        self,
    ) -> None:
        (self.project / "export_presets.cfg").write_text(
            '[preset.0]\n\nname="macOS"\nplatform="macOS"\n',
            encoding="utf-8",
        )
        output = self.project / "builds" / "game.app"
        executable = output / "Contents" / "MacOS" / "game"
        environment = {
            "A3GAME_FAKE_GODOT_EXPORT_DIRECTORY": "1",
            "A3GAME_FAKE_GODOT_EXPORT_PAYLOAD": "original-directory-export",
        }
        with mock.patch.dict(os.environ, environment, clear=False):
            seeded = self.client.build.project(
                preset="macOS",
                output_path=output,
            )
        self.assert_result(seeded, ok=True)
        manifest = Path(seeded["payload"]["export_manifest_path"])
        original_manifest = manifest.read_bytes()

        from engine_adapters.godot.build import client as build_module

        real_run = build_module.GodotTransport.run

        def export_with_fifo(transport, arguments, **kwargs):
            result = real_run(transport, arguments, **kwargs)
            staged_output = Path(arguments[2])
            os.mkfifo(staged_output / "unsupported-node")
            return result

        with mock.patch.dict(
            os.environ, environment, clear=False
        ), mock.patch.object(
            build_module.GodotTransport,
            "run",
            new=export_with_fifo,
        ):
            special_node = self.client.build.project(
                preset="macOS",
                output_path=output,
            )
        self.assert_result(special_node, ok=False)
        self.assertIn("unsupported filesystem node", " ".join(special_node["errors"]))
        self.assertEqual(b"original-directory-export", executable.read_bytes())
        self.assertEqual(original_manifest, manifest.read_bytes())
        self.assertEqual([], list(output.parent.glob(".a3game-godot-export-stage-*")))

        real_move = build_module.shutil.move

        def fail_installing_directory(source, destination, *args, **kwargs):
            source_path = Path(source)
            if (
                ".a3game-godot-export-stage-" in str(source_path)
                and source_path.name == output.name
            ):
                raise OSError("forced directory commit failure")
            return real_move(source, destination, *args, **kwargs)

        with mock.patch.dict(
            os.environ,
            {
                **environment,
                "A3GAME_FAKE_GODOT_EXPORT_PAYLOAD": (
                    "replacement-directory-export"
                ),
            },
            clear=False,
        ), mock.patch.object(
            build_module.shutil,
            "move",
            side_effect=fail_installing_directory,
        ):
            failed_commit = self.client.build.project(
                preset="macOS",
                output_path=output,
            )
        self.assert_result(failed_commit, ok=False)
        self.assertTrue(
            failed_commit["payload"]["rollback"]["restored_previous_output"]
        )
        self.assertEqual(b"original-directory-export", executable.read_bytes())
        self.assertEqual(original_manifest, manifest.read_bytes())
        self.assertEqual([], list(output.parent.glob(".a3game-godot-export-stage-*")))
        self.assertEqual([], list(output.parent.glob(".a3game-godot-export-backup-*")))

    def test_build_rejects_tampered_manifests_and_replaced_outputs(self) -> None:
        (self.project / "export_presets.cfg").write_text(
            '[preset.0]\n\nname="Linux"\nplatform="Linux/X11"\n',
            encoding="utf-8",
        )
        output = self.project / "builds" / "signed-web" / "index.html"
        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_EXPORT_COMPANIONS": "1"},
            clear=False,
        ):
            seeded = self.client.build.project(
                preset="Linux",
                output_path=output,
            )
        self.assert_result(seeded, ok=True)

        manifest = Path(seeded["payload"]["export_manifest_path"])
        original_manifest = manifest.read_bytes()
        original_payload = json.loads(original_manifest)
        owned_paths = {
            output.parent / record["name"]: (
                output.parent / record["name"]
            ).read_bytes()
            for record in original_payload["produced"]
        }
        user_file = output.parent / "user-source.txt"
        user_file.write_bytes(b"USER-FILE-MUST-SURVIVE")
        user_directory = output.parent / "addons"
        user_directory.mkdir()
        (user_directory / "plugin.gd").write_bytes(b"USER-DIRECTORY-MUST-SURVIVE")
        expected_user_tree = {
            path.relative_to(output.parent): path.read_bytes()
            for path in (user_file, user_directory / "plugin.gd")
        }

        mutations = {}
        added_file = json.loads(original_manifest)
        added_file["produced"].append(
            {"kind": "file", "name": user_file.name, "sha256": "0" * 64}
        )
        mutations["added_file"] = added_file
        added_directory = json.loads(original_manifest)
        added_directory["produced"].append(
            {"kind": "directory", "name": user_directory.name, "sha256": "0" * 64}
        )
        mutations["added_directory"] = added_directory
        deleted_entry = json.loads(original_manifest)
        deleted_entry["produced"].pop()
        mutations["deleted_entry"] = deleted_entry
        replaced_entry = json.loads(original_manifest)
        replaced_entry["produced"][0]["name"] = user_file.name
        mutations["replaced_entry"] = replaced_entry

        with mock.patch.object(self.client.build._transport, "run") as run:
            for name, payload in mutations.items():
                with self.subTest(manifest_mutation=name):
                    manifest.write_text(json.dumps(payload), encoding="utf-8")
                    rejected = self.client.build.project(
                        preset="Linux",
                        output_path=output,
                    )
                    self.assert_result(rejected, ok=False)
                    self.assertIn("manifest", " ".join(rejected["errors"]))
                    self.assertEqual(
                        owned_paths,
                        {path: path.read_bytes() for path in owned_paths},
                    )
                    self.assertEqual(
                        expected_user_tree,
                        {
                            path.relative_to(output.parent): path.read_bytes()
                            for path in (user_file, user_directory / "plugin.gd")
                        },
                    )
                    self.assertEqual(
                        [],
                        list(output.parent.glob(".a3game-godot-export-stage-*")),
                    )
                    self.assertEqual(
                        [],
                        list(output.parent.glob(".a3game-godot-export-backup-*")),
                    )
                    manifest.write_bytes(original_manifest)
            run.assert_not_called()

        replaced_owned_path = output.with_suffix(".wasm")
        original_owned_bytes = replaced_owned_path.read_bytes()
        replaced_owned_path.write_bytes(b"USER-REPLACED-OWNED-PATH")
        with mock.patch.object(self.client.build._transport, "run") as run:
            replaced = self.client.build.project(
                preset="Linux",
                output_path=output,
            )
            self.assert_result(replaced, ok=False)
            self.assertIn("ownership proof", " ".join(replaced["errors"]))
            run.assert_not_called()
        self.assertEqual(b"USER-REPLACED-OWNED-PATH", replaced_owned_path.read_bytes())
        self.assertEqual(
            expected_user_tree,
            {
                path.relative_to(output.parent): path.read_bytes()
                for path in (user_file, user_directory / "plugin.gd")
            },
        )
        self.assertEqual([], list(output.parent.glob(".a3game-godot-export-stage-*")))
        self.assertEqual([], list(output.parent.glob(".a3game-godot-export-backup-*")))
        replaced_owned_path.write_bytes(original_owned_bytes)

        ownership_key = self.client._config.data_root / "build" / "export-ownership.key"
        original_key = ownership_key.read_bytes()
        ownership_key.write_bytes(b"T" * len(original_key))
        with mock.patch.object(self.client.build._transport, "run") as run:
            invalid_key = self.client.build.project(
                preset="Linux",
                output_path=output,
            )
            self.assert_result(invalid_key, ok=False)
            self.assertIn("signature", " ".join(invalid_key["errors"]))
            run.assert_not_called()
        ownership_key.write_bytes(original_key)
        self.assertEqual(
            owned_paths,
            {path: path.read_bytes() for path in owned_paths},
        )

    @unittest.skipUnless(os.name == "posix", "filesystem alias probes require POSIX")
    def test_build_rejects_input_aliases_before_run_and_commit(self) -> None:
        presets_path = self.project / "export_presets.cfg"
        presets_path.write_text(
            '[preset.0]\n\nname="Linux"\nplatform="Linux/X11"\n',
            encoding="utf-8",
        )
        project_file = self.project / "project.godot"
        preserved = {
            project_file: project_file.read_bytes(),
            presets_path: presets_path.read_bytes(),
        }
        aliases = []
        for label, source in (
            ("project", project_file),
            ("presets", presets_path),
        ):
            hard_link = self.project / f"{label}-hard-link"
            symbolic_link = self.project / f"{label}-symbolic-link"
            os.link(source, hard_link)
            symbolic_link.symlink_to(source)
            aliases.extend((hard_link, symbolic_link))

        with mock.patch.object(self.client.build._transport, "run") as run:
            for alias in aliases:
                for dry_run in (True, False):
                    with self.subTest(alias=alias.name, dry_run=dry_run):
                        result = self.client.build.project(
                            preset="Linux",
                            output_path=alias,
                            dry_run=dry_run,
                        )
                        self.assert_result(result, ok=False)
            run.assert_not_called()

        from engine_adapters.godot.build import client as build_module

        raced_output = self.project / "builds" / "raced.x86_64"
        real_run = build_module.GodotTransport.run

        def race_with_project_input(transport, *args, **kwargs):
            result = real_run(transport, *args, **kwargs)
            raced_output.parent.mkdir(parents=True, exist_ok=True)
            os.link(project_file, raced_output)
            return result

        with mock.patch.object(
            build_module.GodotTransport,
            "run",
            new=race_with_project_input,
        ):
            raced = self.client.build.project(
                preset="Linux",
                output_path=raced_output,
            )
        self.assert_result(raced, ok=False)
        self.assertIn("protected input", " ".join(raced["errors"]))
        self.assertEqual(
            preserved,
            {path: path.read_bytes() for path in preserved},
        )
        self.assertEqual(project_file.read_bytes(), raced_output.read_bytes())

    def test_build_testing_runtime_world_and_plugin_lifecycle(self) -> None:
        (self.project / "export_presets.cfg").write_text(
            '[preset.0]\n\nname="Linux"\nplatform="Linux/X11"\n',
            encoding="utf-8",
        )
        build = self.client.build.project(
            preset="Linux", output_path="builds/game.x86_64"
        )
        self.assert_result(build, ok=True)
        build_path = Path(build["artifacts"][0]["path"])
        self.assertTrue(build_path.is_file())
        previous_build = build_path.read_bytes()
        with mock.patch.dict(
            os.environ, {"A3GAME_FAKE_GODOT_FAIL_EXPORT": "1"}, clear=False
        ):
            failed_build = self.client.build.project(
                preset="Linux", output_path="builds/game.x86_64"
            )
        self.assert_result(failed_build, ok=False)
        self.assertEqual(previous_build, build_path.read_bytes())

        web_output = self.project / "builds" / "web" / "index.html"
        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_EXPORT_COMPANIONS": "1"},
            clear=False,
        ):
            seeded_web = self.client.build.project(
                preset="Linux", output_path="builds/web/index.html"
            )
        self.assert_result(seeded_web, ok=True)
        web_wasm = web_output.with_suffix(".wasm")
        previous_web_group = {
            path: path.read_bytes()
            for path in (
                web_output,
                web_wasm,
                web_output.with_suffix(".pck"),
                web_output.with_suffix(".js"),
                web_output.with_suffix(".png"),
            )
        }
        unrelated_web_note = web_output.with_name("index.notes.txt")
        unrelated_web_note.write_bytes(b"unrelated-user-file")
        with mock.patch.dict(
            os.environ,
            {
                "A3GAME_FAKE_GODOT_EXPORT_COMPANIONS": "1",
                "A3GAME_FAKE_GODOT_FAIL_EXPORT": "1",
            },
            clear=False,
        ):
            failed_web = self.client.build.project(
                preset="Linux", output_path="builds/web/index.html"
            )
        self.assert_result(failed_web, ok=False)
        self.assertTrue(failed_web["payload"]["rollback"]["restored_previous_output"])
        self.assertEqual(
            previous_web_group,
            {path: path.read_bytes() for path in previous_web_group},
        )
        self.assertEqual(b"unrelated-user-file", unrelated_web_note.read_bytes())

        with mock.patch.dict(
            os.environ,
            {
                "A3GAME_FAKE_GODOT_EXPORT_COMPANIONS": "1",
                "A3GAME_FAKE_GODOT_FAIL_EXPORT": "",
            },
            clear=False,
        ):
            successful_web = self.client.build.project(
                preset="Linux", output_path="builds/web/index.html"
            )
        self.assert_result(successful_web, ok=True)
        self.assertEqual(b"fake-godot-export", web_output.read_bytes())
        self.assertEqual(b"unrelated-user-file", unrelated_web_note.read_bytes())
        for suffix in (".wasm", ".pck", ".js", ".png"):
            self.assertTrue(web_output.with_suffix(suffix).is_file())

        previous_group = {
            path: path.read_bytes()
            for path in (
                web_output,
                web_output.with_suffix(".wasm"),
                web_output.with_suffix(".pck"),
                web_output.with_suffix(".js"),
                web_output.with_suffix(".png"),
            )
        }
        from engine_adapters.godot.build import client as build_module

        real_move = build_module.shutil.move

        def fail_during_group_commit(source, destination, *args, **kwargs):
            if (
                ".a3game-godot-export-stage-" in str(source)
                and Path(source).suffix == ".js"
            ):
                raise OSError("forced group commit failure")
            return real_move(source, destination, *args, **kwargs)

        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_EXPORT_COMPANIONS": "1"},
            clear=False,
        ), mock.patch.object(
            build_module.shutil,
            "move",
            side_effect=fail_during_group_commit,
        ):
            failed_commit = self.client.build.project(
                preset="Linux", output_path="builds/web/index.html"
            )
        self.assert_result(failed_commit, ok=False)
        self.assertTrue(
            failed_commit["payload"]["rollback"]["restored_previous_output"]
        )
        self.assertEqual(
            previous_group,
            {path: path.read_bytes() for path in previous_group},
        )
        self.assertEqual(b"unrelated-user-file", unrelated_web_note.read_bytes())

        with mock.patch.dict(
            os.environ, {"A3GAME_FAKE_GODOT_EXPORT_COMPANIONS": ""}, clear=False
        ):
            replaced_web = self.client.build.project(
                preset="Linux", output_path="builds/web/index.html"
            )
        self.assert_result(replaced_web, ok=True)
        self.assertEqual([str(web_output)], replaced_web["payload"]["produced_paths"])
        for suffix in (".wasm", ".pck", ".js", ".png"):
            self.assertFalse(web_output.with_suffix(suffix).exists())
        self.assertEqual(b"unrelated-user-file", unrelated_web_note.read_bytes())

        native_test = self.client.testing.run_automation_tests()
        self.assert_result(native_test, ok=True)
        self.assertEqual(1, native_test["payload"]["passed_count"])
        with mock.patch.dict(
            os.environ, {"A3GAME_FAKE_GODOT_NO_REPORT": "1"}, clear=False
        ):
            no_report = self.client.testing.run_automation_tests()
        self.assert_result(no_report, ok=False)
        custom_runner = self.project / "tests" / "custom.gd"
        custom_runner.parent.mkdir(parents=True, exist_ok=True)
        custom_runner.write_text("extends SceneTree\n", encoding="utf-8")
        custom = self.client.testing.run_automation_tests(
            script="res://tests/custom.gd", dry_run=True
        )
        self.assert_result(custom, ok=True)
        self.assertEqual("res://tests/custom.gd", custom["payload"]["runner"])
        self.assertEqual("res://tests/custom.gd", custom["payload"]["command"][-1])
        escaped_runner = self.client.testing.run_automation_tests(
            script="res://../custom.gd", dry_run=True
        )
        self.assert_result(escaped_runner, ok=False)

        launch = self.client.runtime.launch_game(headless=True)
        self.assert_result(launch, ok=True)
        process_id = launch["payload"]["process_id"]
        stopped = self.client.runtime.stop_game(process_id)
        self.assert_result(stopped, ok=True)
        unknown = self.client.runtime.stop_game(process_id)
        self.assert_result(unknown, ok=False)

        failed_player = self.root / "failed-player"
        failed_player.write_text("#!/bin/sh\nexit 7\n", encoding="utf-8")
        failed_player.chmod(0o755)
        player_failure = self.client.runtime.launch_player(failed_player)
        self.assert_result(player_failure, ok=False)
        self.assertEqual(7, player_failure["payload"]["returncode"])
        self.assertEqual({}, self.client.runtime._players)

        live_player = self.root / "live-player"
        live_player.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
        live_player.chmod(0o755)
        player_launch = self.client.runtime.launch_player(live_player)
        self.assert_result(player_launch, ok=True)
        player_stop = self.client.runtime.stop_player(
            player_launch["payload"]["process_id"]
        )
        self.assert_result(player_stop, ok=True)

        framework = self.client.plugin.install_framework()
        self.assert_result(framework, ok=True)
        project_settings = (self.project / "project.godot").read_text(encoding="utf-8")
        self.assertIn(
            'A3GameRuntime="*res://addons/a3game_playable/runtime.gd"',
            project_settings,
        )
        reinstalled = self.client.plugin.install_framework(replace_existing=True)
        self.assert_result(reinstalled, ok=True)
        project_settings = (self.project / "project.godot").read_text(encoding="utf-8")
        self.assertEqual(1, project_settings.count("[editor_plugins]"))
        self.assertEqual(1, project_settings.count("[autoload]"))
        self.assertEqual(
            1,
            project_settings.count(
                'enabled=PackedStringArray("res://addons/a3game_playable/plugin.cfg")'
            ),
        )
        self.assertEqual(
            1,
            project_settings.count(
                'A3GameRuntime="*res://addons/a3game_playable/runtime.gd"'
            ),
        )
        marker = self.project / "addons" / "a3game_playable" / "user-marker.txt"
        marker.write_text("preserve", encoding="utf-8")
        from engine_adapters.godot.plugin import client as plugin_module

        with mock.patch.object(
            plugin_module, "_enable_plugin", side_effect=RuntimeError("forced")
        ):
            replacement = self.client.plugin.install_framework(replace_existing=True)
        self.assert_result(replacement, ok=False)
        self.assertEqual("preserve", marker.read_text(encoding="utf-8"))
        listed_plugins = self.client.plugin.list()
        self.assert_result(listed_plugins, ok=True)

        scene_source, _ = self.make_artifact(
            task_id="arena",
            suffix=".tscn",
            content=b'[gd_scene format=3]\n\n[node name="Arena" type="Node3D"]\n',
            task_kind="3d_scene",
            artifact_key="scene_path",
        )
        world = self.client.world.build(
            scene_source,
            options={"world_id": "arena", "project_id": "demo"},
        )
        self.assert_result(world, ok=True)
        worlds = self.client.world.list_packages(project_id="demo")
        self.assert_result(worlds, ok=True)
        self.assertEqual(1, worlds["payload"]["count"])

    def test_plugin_installs_accept_project_file_and_isolate_validation(
        self,
    ) -> None:
        task_dir = paths.task_output_dir(
            "godot_adapter_test",
            "3d_scene",
            "project_file_plugin_source",
            run_id="run_001",
        )
        source_root = task_dir / "custom_addon"
        source_root.mkdir()
        (source_root / "plugin.cfg").write_text(
            '[plugin]\nname="Custom Add-on"\ndescription=""\n'
            'author="Test"\nversion="1.0"\nscript="plugin.gd"\n',
            encoding="utf-8",
        )
        (source_root / "plugin.gd").write_text(
            "@tool\nextends EditorPlugin\n",
            encoding="utf-8",
        )
        (task_dir / "meta.json").write_text(
            json.dumps(
                {
                    "game_id": "godot_adapter_test",
                    "run_id": "run_001",
                    "task_kind": "3d_scene",
                    "task_id": "project_file_plugin_source",
                    "plugin_path": str(source_root),
                }
            ),
            encoding="utf-8",
        )
        source = {
            "game_id": "godot_adapter_test",
            "run_id": "run_001",
            "task_kind": "3d_scene",
            "task_id": "project_file_plugin_source",
            "artifact_key": "plugin_path",
        }
        client = GodotClient(
            project_path=self.project / "project.godot",
            godot_executable=self.fake_godot,
            editor_timeout=5,
            import_timeout=5,
        )

        from engine_adapters.godot.plugin import client as plugin_module

        original_run = plugin_module.GodotTransport.run
        validation_roots: list[Path] = []

        def capture_validation_run(transport, *args, **kwargs):
            validation_root = transport.config.project_path
            self.assertIsNotNone(validation_root)
            self.assertEqual(
                validation_root,
                transport.config.project_path_input,
            )
            self.assertEqual(validation_root, transport.config.project_dir)
            self.assertEqual(
                validation_root / "project.godot",
                transport.config.project_file,
            )
            result = original_run(transport, *args, **kwargs)
            path_index = result.command.index("--path")
            self.assertEqual(
                str(validation_root),
                result.command[path_index + 1],
            )
            validation_roots.append(validation_root)
            return result

        with mock.patch.object(
            plugin_module.GodotTransport,
            "run",
            new=capture_validation_run,
        ):
            framework = client.plugin.install_framework()
            custom = client.plugin.install(
                source,
                install_dir="custom_addon",
            )

        self.assert_result(framework, ok=True)
        self.assert_result(custom, ok=True)
        self.assertEqual(2, len(validation_roots))
        for validation_root in validation_roots:
            self.assertNotEqual(self.project, validation_root)
            self.assertFalse(validation_root.exists())
        self.assertTrue(
            (self.project / "addons" / "a3game_playable" / "plugin.cfg").is_file()
        )
        self.assertTrue(
            (self.project / "addons" / "custom_addon" / "plugin.cfg").is_file()
        )

        project_after_install = (self.project / "project.godot").read_bytes()
        installers = (
            (
                "framework",
                self.project / "addons" / "a3game_playable",
                lambda dry_run: client.plugin.install_framework(dry_run=dry_run),
            ),
            (
                "custom",
                self.project / "addons" / "custom_addon",
                lambda dry_run: client.plugin.install(
                    source,
                    install_dir="custom_addon",
                    dry_run=dry_run,
                ),
            ),
        )
        for label, target, install_again in installers:
            marker = target / "user-marker.txt"
            marker.write_text(label, encoding="utf-8")
            for dry_run in (True, False):
                with self.subTest(installer=label, dry_run=dry_run):
                    conflict = install_again(dry_run)
                    self.assert_result(conflict, ok=False)
                    self.assertIn("FileExistsError", " ".join(conflict["errors"]))
                    self.assertIn(
                        "replace_existing=True",
                        " ".join(conflict["errors"]),
                    )
                    self.assertEqual(
                        project_after_install,
                        (self.project / "project.godot").read_bytes(),
                    )
                    self.assertEqual(label, marker.read_text(encoding="utf-8"))

    def test_framework_install_respects_existing_autoload_and_rolls_back(self) -> None:
        project_file = self.project / "project.godot"
        framework_target = self.project / "addons" / "a3game_playable"
        custom_autoload = 'A3GameRuntime="*res://addons/custom/runtime.gd"'
        framework_autoload = 'A3GameRuntime="*res://addons/a3game_playable/runtime.gd"'
        project_file.write_text(
            project_file.read_text(encoding="utf-8")
            + "\n[autoload]\n"
            + custom_autoload
            + "\n",
            encoding="utf-8",
        )

        project_before_conflict = project_file.read_bytes()
        addons_before_conflict = sorted(
            path.relative_to(self.project).as_posix()
            for path in (self.project / "addons").rglob("*")
        )
        conflict = self.client.plugin.install_framework()
        self.assert_result(conflict, ok=False)
        self.assertIn("FileExistsError", " ".join(conflict["errors"]))
        self.assertIn("replace_existing=True", " ".join(conflict["errors"]))
        self.assertEqual(project_before_conflict, project_file.read_bytes())
        self.assertEqual(
            addons_before_conflict,
            sorted(
                path.relative_to(self.project).as_posix()
                for path in (self.project / "addons").rglob("*")
            ),
        )
        self.assertFalse(framework_target.exists())

        project_file.write_text(
            project_file.read_text(encoding="utf-8").replace(
                custom_autoload, framework_autoload
            ),
            encoding="utf-8",
        )
        matching = self.client.plugin.install_framework()
        self.assert_result(matching, ok=True)
        self.assertEqual(
            1,
            project_file.read_text(encoding="utf-8").count(framework_autoload),
        )

        marker = framework_target / "user-marker.txt"
        marker.write_text("old add-on", encoding="utf-8")
        project_file.write_text(
            project_file.read_text(encoding="utf-8").replace(
                framework_autoload, custom_autoload
            ),
            encoding="utf-8",
        )
        replaced = self.client.plugin.install_framework(replace_existing=True)
        self.assert_result(replaced, ok=True)
        replaced_project = project_file.read_text(encoding="utf-8")
        self.assertNotIn(custom_autoload, replaced_project)
        self.assertEqual(1, replaced_project.count(framework_autoload))
        self.assertFalse(marker.exists())

        marker.write_text("restore add-on", encoding="utf-8")
        project_file.write_text(
            project_file.read_text(encoding="utf-8").replace(
                framework_autoload, custom_autoload
            ),
            encoding="utf-8",
        )
        project_before_failure = project_file.read_bytes()
        from engine_adapters.godot.plugin import client as plugin_module

        with mock.patch.object(
            plugin_module,
            "_copied_files",
            side_effect=RuntimeError("forced post-enable failure"),
        ):
            failed_replacement = self.client.plugin.install_framework(
                replace_existing=True
            )
        self.assert_result(failed_replacement, ok=False)
        self.assertIn(
            "forced post-enable failure",
            " ".join(failed_replacement["errors"]),
        )
        self.assertEqual(project_before_failure, project_file.read_bytes())
        self.assertEqual("restore add-on", marker.read_text(encoding="utf-8"))

    def test_framework_install_accepts_matching_commented_autoload(self) -> None:
        framework_autoload = 'A3GameRuntime="*res://addons/a3game_playable/runtime.gd"'

        for index, comment in enumerate(
            (" ; retained semicolon comment", " # retained hash comment")
        ):
            with self.subTest(comment=comment):
                project = self.root / f"MatchingCommentedAutoload{index}"
                client = GodotClient(
                    project_path=project,
                    godot_executable=self.fake_godot,
                    editor_timeout=5,
                    import_timeout=5,
                )
                self.assert_result(
                    client.project.create(project_name=f"Matching Comment {index}"),
                    ok=True,
                )
                project_file = project / "project.godot"
                project_file.write_text(
                    project_file.read_text(encoding="utf-8")
                    + "\n[autoload]\n"
                    + framework_autoload
                    + comment
                    + "\n",
                    encoding="utf-8",
                )

                installed = client.plugin.install_framework()

                self.assert_result(installed, ok=True)
                project_text = project_file.read_text(encoding="utf-8")
                self.assertEqual(1, project_text.count(framework_autoload))
                self.assertIn(framework_autoload + comment, project_text)
                self.assertTrue(
                    (project / "addons" / "a3game_playable" / "runtime.gd").is_file()
                )

    def test_framework_install_handles_commented_autoload_conflict_atomically(
        self,
    ) -> None:
        custom_autoload = 'A3GameRuntime="*res://addons/custom/runtime.gd"'
        framework_autoload = 'A3GameRuntime="*res://addons/a3game_playable/runtime.gd"'

        for index, comment in enumerate(
            (" ; retained semicolon comment", " # retained hash comment")
        ):
            with self.subTest(comment=comment):
                project = self.root / f"ConflictingCommentedAutoload{index}"
                client = GodotClient(
                    project_path=project,
                    godot_executable=self.fake_godot,
                    editor_timeout=5,
                    import_timeout=5,
                )
                self.assert_result(
                    client.project.create(project_name=f"Conflicting Comment {index}"),
                    ok=True,
                )
                project_file = project / "project.godot"
                project_file.write_text(
                    project_file.read_text(encoding="utf-8")
                    + "\n[autoload]\n"
                    + custom_autoload
                    + comment
                    + "\n",
                    encoding="utf-8",
                )
                project_before = project_file.read_bytes()
                target = project / "addons" / "a3game_playable"

                conflict = client.plugin.install_framework()

                self.assert_result(conflict, ok=False)
                self.assertIn("FileExistsError", " ".join(conflict["errors"]))
                self.assertEqual(project_before, project_file.read_bytes())
                self.assertFalse(target.exists())

                replaced = client.plugin.install_framework(replace_existing=True)

                self.assert_result(replaced, ok=True)
                project_text = project_file.read_text(encoding="utf-8")
                self.assertEqual(1, project_text.count(framework_autoload))
                self.assertNotIn(custom_autoload, project_text)
                self.assertIn(framework_autoload + comment, project_text)

    def test_framework_install_rejects_commented_autoload_conflict_atomically(
        self,
    ) -> None:
        project_file = self.project / "project.godot"
        framework_target = self.project / "addons" / "a3game_playable"
        custom_autoload = 'A3GameRuntime="*res://addons/custom/runtime.gd"'
        project_file.write_text(
            project_file.read_text(encoding="utf-8")
            + "\n[autoload] ; preserve this section comment\n"
            + custom_autoload
            + "\n",
            encoding="utf-8",
        )
        project_before = project_file.read_bytes()
        addons_before = sorted(
            path.relative_to(self.project).as_posix()
            for path in (self.project / "addons").rglob("*")
        )

        conflict = self.client.plugin.install_framework()

        self.assert_result(conflict, ok=False)
        self.assertIn("FileExistsError", " ".join(conflict["errors"]))
        self.assertIn("replace_existing=True", " ".join(conflict["errors"]))
        self.assertEqual(project_before, project_file.read_bytes())
        self.assertEqual(
            addons_before,
            sorted(
                path.relative_to(self.project).as_posix()
                for path in (self.project / "addons").rglob("*")
            ),
        )
        self.assertFalse(framework_target.exists())

    def test_framework_install_replaces_autoload_in_commented_section(self) -> None:
        project_file = self.project / "project.godot"
        header = "[autoload] ; preserve this section comment"
        custom_autoload = 'A3GameRuntime="*res://addons/custom/runtime.gd"'
        framework_autoload = 'A3GameRuntime="*res://addons/a3game_playable/runtime.gd"'
        project_file.write_text(
            project_file.read_text(encoding="utf-8")
            + f"\n{header}\n{custom_autoload}\n",
            encoding="utf-8",
        )

        replaced = self.client.plugin.install_framework(replace_existing=True)

        self.assert_result(replaced, ok=True)
        replaced_project = project_file.read_text(encoding="utf-8")
        self.assertIn(header, replaced_project)
        self.assertEqual(1, replaced_project.count("[autoload]"))
        self.assertEqual(1, replaced_project.count(framework_autoload))
        self.assertNotIn(custom_autoload, replaced_project)
        self.assertLess(
            replaced_project.index(header),
            replaced_project.index(framework_autoload),
        )

    def test_framework_install_normalizes_duplicate_autoloads_atomically(self) -> None:
        framework_autoload = 'A3GameRuntime="*res://addons/a3game_playable/runtime.gd"'
        custom_autoload = 'A3GameRuntime="*res://addons/custom/runtime.gd"'

        for index, declarations in enumerate(
            (
                (framework_autoload, custom_autoload),
                (custom_autoload, framework_autoload),
            )
        ):
            with self.subTest(declarations=declarations):
                project = self.root / f"DuplicateAutoload{index}"
                client = GodotClient(
                    project_path=project,
                    godot_executable=self.fake_godot,
                    editor_timeout=5,
                    import_timeout=5,
                )
                self.assert_result(
                    client.project.create(project_name=f"Duplicate {index}"),
                    ok=True,
                )
                project_file = project / "project.godot"
                project_file.write_text(
                    project_file.read_text(encoding="utf-8")
                    + "\n[autoload]\n"
                    + "\n".join(declarations)
                    + "\n",
                    encoding="utf-8",
                )
                framework_target = project / "addons" / "a3game_playable"
                project_before_conflict = project_file.read_bytes()
                addons_before_conflict = sorted(
                    path.relative_to(project).as_posix()
                    for path in (project / "addons").rglob("*")
                )

                conflict = client.plugin.install_framework()
                self.assert_result(conflict, ok=False)
                self.assertIn("declared 2 times", " ".join(conflict["errors"]))
                self.assertIn("replace_existing=True", " ".join(conflict["errors"]))
                self.assertEqual(project_before_conflict, project_file.read_bytes())
                self.assertEqual(
                    addons_before_conflict,
                    sorted(
                        path.relative_to(project).as_posix()
                        for path in (project / "addons").rglob("*")
                    ),
                )
                self.assertFalse(framework_target.exists())

                replaced = client.plugin.install_framework(replace_existing=True)
                self.assert_result(replaced, ok=True)
                replaced_project = project_file.read_text(encoding="utf-8")
                self.assertEqual(1, replaced_project.count(framework_autoload))
                self.assertNotIn(custom_autoload, replaced_project)

                marker = framework_target / "user-marker.txt"
                marker.write_text("restore duplicate add-on", encoding="utf-8")
                project_file.write_text(
                    replaced_project.replace(
                        framework_autoload,
                        "\n".join(declarations),
                    ),
                    encoding="utf-8",
                )
                project_before_failure = project_file.read_bytes()
                from engine_adapters.godot.plugin import client as plugin_module

                with mock.patch.object(
                    plugin_module,
                    "_copied_files",
                    side_effect=RuntimeError("forced duplicate rollback failure"),
                ):
                    failed = client.plugin.install_framework(replace_existing=True)
                self.assert_result(failed, ok=False)
                self.assertIn(
                    "forced duplicate rollback failure",
                    " ".join(failed["errors"]),
                )
                self.assertEqual(project_before_failure, project_file.read_bytes())
                self.assertEqual(
                    "restore duplicate add-on", marker.read_text(encoding="utf-8")
                )

    def test_framework_install_preserves_existing_enabled_plugins(self) -> None:
        project_file = self.project / "project.godot"
        existing = "res://addons/existing/plugin.cfg"
        framework = "res://addons/a3game_playable/plugin.cfg"
        project_file.write_text(
            project_file.read_text(encoding="utf-8")
            + "\n[editor_plugins]\n"
            + f"enabled = PackedStringArray({json.dumps(existing)}) ; keep enabled\n",
            encoding="utf-8",
        )

        result = self.client.plugin.install_framework()
        self.assert_result(result, ok=True)
        project_text = project_file.read_text(encoding="utf-8")
        self.assertEqual(1, project_text.count("[editor_plugins]"))
        self.assertEqual(1, project_text.count("enabled = PackedStringArray("))
        self.assertIn(
            "enabled = PackedStringArray("
            + f"{json.dumps(existing)}, {json.dumps(framework)}) ; keep enabled",
            project_text,
        )

    def test_framework_install_preserves_hash_commented_enabled_plugins(self) -> None:
        project_file = self.project / "project.godot"
        existing = "res://addons/existing/plugin.cfg"
        framework = "res://addons/a3game_playable/plugin.cfg"
        project_file.write_text(
            project_file.read_text(encoding="utf-8")
            + "\n[editor_plugins]\n"
            + f"enabled = PackedStringArray({json.dumps(existing)}) # keep enabled\n",
            encoding="utf-8",
        )

        result = self.client.plugin.install_framework()

        self.assert_result(result, ok=True)
        project_text = project_file.read_text(encoding="utf-8")
        self.assertEqual(1, project_text.count("[editor_plugins]"))
        self.assertEqual(1, project_text.count("enabled = PackedStringArray("))
        self.assertIn(
            "enabled = PackedStringArray("
            + f"{json.dumps(existing)}, {json.dumps(framework)}) # keep enabled",
            project_text,
        )

    def test_framework_install_enables_empty_editor_plugin_section_at_eof(
        self,
    ) -> None:
        project = self.root / "EditorPluginSectionAtEof"
        client = GodotClient(
            project_path=project,
            godot_executable=self.fake_godot,
            editor_timeout=5,
            import_timeout=5,
        )
        self.assert_result(
            client.project.create(project_name="Editor Plugin Section At EOF"),
            ok=True,
        )
        project_file = project / "project.godot"
        project_file.write_text(
            project_file.read_text(encoding="utf-8").rstrip("\n")
            + "\n\n[editor_plugins]",
            encoding="utf-8",
        )

        result = client.plugin.install_framework()
        self.assert_result(result, ok=True)
        project_text = project_file.read_text(encoding="utf-8")
        self.assertIn(
            "[editor_plugins]\n"
            'enabled=PackedStringArray("res://addons/a3game_playable/plugin.cfg")',
            project_text,
        )

    def test_plugin_installs_reject_ambiguous_enabled_settings_atomically(
        self,
    ) -> None:
        task_dir = paths.task_output_dir(
            "godot_adapter_test",
            "3d_scene",
            "ambiguous_plugin_source",
            run_id="run_001",
        )
        source_root = task_dir / "custom_addon"
        source_root.mkdir()
        (source_root / "plugin.cfg").write_text(
            '[plugin]\nname="Custom Add-on"\ndescription=""\n'
            'author="Test"\nversion="1.0"\nscript="plugin.gd"\n',
            encoding="utf-8",
        )
        (source_root / "plugin.gd").write_text(
            "@tool\nextends EditorPlugin\n",
            encoding="utf-8",
        )
        (task_dir / "meta.json").write_text(
            json.dumps(
                {
                    "game_id": "godot_adapter_test",
                    "run_id": "run_001",
                    "task_kind": "3d_scene",
                    "task_id": "ambiguous_plugin_source",
                    "plugin_path": str(source_root),
                }
            ),
            encoding="utf-8",
        )
        source = {
            "game_id": "godot_adapter_test",
            "run_id": "run_001",
            "task_kind": "3d_scene",
            "task_id": "ambiguous_plugin_source",
            "artifact_key": "plugin_path",
        }
        other_resource = "res://addons/last/plugin.cfg"

        for installer in ("framework", "custom"):
            target_resource = (
                "res://addons/a3game_playable/plugin.cfg"
                if installer == "framework"
                else "res://addons/custom_addon/plugin.cfg"
            )
            target_name = (
                "a3game_playable" if installer == "framework" else "custom_addon"
            )
            for layout in ("duplicate_keys", "duplicate_sections"):
                for target_first in (True, False):
                    label = f"{installer}-{layout}-{target_first}"
                    with self.subTest(case=label):
                        project = self.root / label
                        client = GodotClient(
                            project_path=project,
                            godot_executable=self.fake_godot,
                            editor_timeout=5,
                            import_timeout=5,
                        )
                        self.assert_result(
                            client.project.create(project_name=label),
                            ok=True,
                        )
                        first, second = (
                            (target_resource, other_resource)
                            if target_first
                            else (other_resource, target_resource)
                        )
                        declarations = (
                            "\n[editor_plugins]\n"
                            + f"enabled=PackedStringArray({json.dumps(first)})\n"
                        )
                        if layout == "duplicate_keys":
                            declarations += (
                                f"enabled=PackedStringArray({json.dumps(second)})\n"
                            )
                        else:
                            declarations += (
                                "\n[editor_plugins]\n"
                                + "enabled=PackedStringArray("
                                + json.dumps(second)
                                + ")\n"
                            )
                        project_file = project / "project.godot"
                        project_file.write_text(
                            project_file.read_text(encoding="utf-8") + declarations,
                            encoding="utf-8",
                        )
                        target = project / "addons" / target_name
                        target.mkdir()
                        marker = target / "user-marker.txt"
                        marker.write_text("preserve", encoding="utf-8")
                        project_before = project_file.read_bytes()

                        if installer == "framework":
                            result = client.plugin.install_framework(
                                replace_existing=True
                            )
                        else:
                            result = client.plugin.install(
                                source,
                                install_dir="custom_addon",
                                replace_existing=True,
                            )

                        self.assert_result(result, ok=False)
                        self.assertIn("ambiguous", " ".join(result["errors"]))
                        self.assertEqual(project_before, project_file.read_bytes())
                        self.assertEqual("preserve", marker.read_text(encoding="utf-8"))

    def test_plugin_install_validates_entry_script_atomically(self) -> None:
        task_dir = paths.task_output_dir(
            "godot_adapter_test",
            "3d_scene",
            "plugin_entry_validation",
            run_id="run_001",
        )
        descriptor = {
            "game_id": "godot_adapter_test",
            "run_id": "run_001",
            "task_kind": "3d_scene",
            "task_id": "plugin_entry_validation",
            "artifact_key": "plugin_path",
        }
        project_file = self.project / "project.godot"
        project_before = project_file.read_bytes()

        def set_source(
            label: str,
            plugin_config: str,
            files: dict[str, str],
        ) -> Path:
            source_root = task_dir / label
            source_root.mkdir()
            (source_root / "plugin.cfg").write_text(
                plugin_config,
                encoding="utf-8",
            )
            for relative, content in files.items():
                path = source_root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            (task_dir / "meta.json").write_text(
                json.dumps(
                    {
                        "game_id": "godot_adapter_test",
                        "run_id": "run_001",
                        "task_kind": "3d_scene",
                        "task_id": "plugin_entry_validation",
                        "plugin_path": str(source_root),
                    }
                ),
                encoding="utf-8",
            )
            return source_root

        invalid_cases = (
            (
                "missing_declaration",
                (
                    '[plugin]\nname="Missing declaration"\ndescription=""\n'
                    'author="Test"\nversion="1.0"\n'
                ),
                {"plugin.gd": "@tool\nextends EditorPlugin\n"},
                "no script entry",
            ),
            (
                "missing_metadata",
                '[plugin]\nname="Missing metadata"\nscript="plugin.gd"\n',
                {"plugin.gd": "@tool\nextends EditorPlugin\n"},
                "no author entry",
            ),
            (
                "missing_file",
                (
                    '[plugin]\nname="Missing file"\ndescription=""\n'
                    'author="Test"\nversion="1.0"\nscript="missing.gd"\n'
                ),
                {},
                "FileNotFoundError",
            ),
            (
                "traversal",
                (
                    '[plugin]\nname="Traversal"\ndescription=""\n'
                    'author="Test"\nversion="1.0"\nscript="../outside.gd"\n'
                ),
                {},
                "non-traversing",
            ),
            (
                "directory_entry",
                (
                    '[plugin]\nname="Directory entry"\ndescription=""\n'
                    'author="Test"\nversion="1.0"\nscript="entry"\n'
                ),
                {"entry/child.gd": "@tool\nextends EditorPlugin\n"},
                "regular file",
            ),
            (
                "syntax_error",
                (
                    '[plugin]\nname="Syntax error"\ndescription=""\n'
                    'author="Test"\nversion="1.0"\nscript="plugin.gd"\n'
                ),
                {
                    "plugin.gd": (
                        "@tool\nextends EditorPlugin\nA3GAME_INVALID_GDSCRIPT\n"
                    )
                },
                "parse error",
            ),
            (
                "not_tool",
                (
                    '[plugin]\nname="Not tool"\ndescription=""\n'
                    'author="Test"\nversion="1.0"\nscript="plugin.gd"\n'
                ),
                {"plugin.gd": "extends EditorPlugin\n"},
                "tool mode",
            ),
            (
                "wrong_base",
                (
                    '[plugin]\nname="Wrong base"\ndescription=""\n'
                    'author="Test"\nversion="1.0"\nscript="plugin.gd"\n'
                ),
                {"plugin.gd": "@tool\nextends Node\n"},
                "inherit EditorPlugin",
            ),
            (
                "constructor_requires_argument",
                (
                    '[plugin]\nname="Required constructor argument"\ndescription=""\n'
                    'author="Test"\nversion="1.0"\nscript="plugin.gd"\n'
                ),
                {
                    "plugin.gd": (
                        "@tool\nextends EditorPlugin\n"
                        "func _init(required_value):\n\tpass\n"
                    )
                },
                "cannot be instantiated without arguments",
            ),
            (
                "malformed_descriptor",
                (
                    '[plugin]\nname="Malformed"\ndescription=""\n'
                    'author="Test"\nversion="1.0"\nscript="plugin.gd"\n'
                    'A3GAME_INVALID_PLUGIN_CONFIG="\n'
                ),
                {"plugin.gd": "@tool\nextends EditorPlugin\n"},
                "descriptor could not be parsed",
            ),
        )
        (task_dir / "outside.gd").write_text(
            "@tool\nextends EditorPlugin\n",
            encoding="utf-8",
        )
        for label, plugin_config, files, expected_error in invalid_cases:
            with self.subTest(case=label):
                set_source(label, plugin_config, files)
                target = self.project / "addons" / label
                result = self.client.plugin.install(
                    descriptor,
                    install_dir=label,
                )
                self.assert_result(result, ok=False)
                self.assertIn(expected_error, " ".join(result["errors"]))
                self.assertFalse(target.exists())
                self.assertEqual(project_before, project_file.read_bytes())

        valid_source = set_source(
            "valid_entry",
            '[plugin]\nname="Valid entry"\ndescription=""\n'
            'author="Test"\nversion="1.0"\nscript="src/plugin.gd"\n',
            {"src/plugin.gd": "@tool\nextends EditorPlugin\n"},
        )

        from engine_adapters.godot.plugin import client as plugin_module

        original_validation = plugin_module._validate_plugin_with_godot
        raced_target = self.project / "addons" / "raced_addon"
        raced_marker = raced_target / "user-marker.txt"

        def create_target_during_validation(*args, **kwargs):
            validation_result = original_validation(*args, **kwargs)
            raced_target.mkdir()
            raced_marker.write_text("preserve", encoding="utf-8")
            return validation_result

        with mock.patch.object(
            plugin_module,
            "_validate_plugin_with_godot",
            side_effect=create_target_during_validation,
        ):
            raced_install = self.client.plugin.install(
                descriptor,
                install_dir="raced_addon",
            )
        self.assert_result(raced_install, ok=False)
        self.assertIn("FileExistsError", " ".join(raced_install["errors"]))
        self.assertEqual("preserve", raced_marker.read_text(encoding="utf-8"))
        self.assertEqual(project_before, project_file.read_bytes())

        installed = self.client.plugin.install(
            descriptor,
            install_dir="validated_addon",
        )
        self.assert_result(installed, ok=True)
        self.assertEqual("src/plugin.gd", installed["payload"]["entry_script"])
        validation = installed["payload"]["native_validation"]
        self.assertEqual(0, validation["process"]["returncode"])
        self.assertTrue(validation["report"]["ok"])
        self.assertEqual("EditorPlugin", validation["report"]["base_type"])
        self.assertTrue(validation["report"]["instantiated"])
        self.assertEqual("EditorPlugin", validation["report"]["instance_class"])

        target = self.project / "addons" / "validated_addon"
        target_script_before = (target / "src" / "plugin.gd").read_bytes()
        project_after_install = project_file.read_bytes()
        (valid_source / "src" / "plugin.gd").write_text(
            "@tool\nextends EditorPlugin\nA3GAME_INVALID_GDSCRIPT\n",
            encoding="utf-8",
        )
        rejected_replacement = self.client.plugin.install(
            descriptor,
            install_dir="validated_addon",
            replace_existing=True,
        )
        self.assert_result(rejected_replacement, ok=False)
        self.assertIn("parse error", " ".join(rejected_replacement["errors"]))
        self.assertEqual(project_after_install, project_file.read_bytes())
        self.assertEqual(
            target_script_before,
            (target / "src" / "plugin.gd").read_bytes(),
        )

    def test_plugin_install_rejects_symlinked_addons_and_targets(self) -> None:
        task_dir = paths.task_output_dir(
            "godot_adapter_test",
            "3d_scene",
            "plugin_source",
            run_id="run_001",
        )
        source_root = task_dir / "custom_addon"
        source_root.mkdir()
        (source_root / "plugin.cfg").write_text(
            '[plugin]\nname="Custom Add-on"\ndescription=""\n'
            'author="Test"\nversion="1.0"\nscript="plugin.gd"\n',
            encoding="utf-8",
        )
        (source_root / "plugin.gd").write_text(
            "@tool\nextends EditorPlugin\n",
            encoding="utf-8",
        )
        (task_dir / "meta.json").write_text(
            json.dumps(
                {
                    "game_id": "godot_adapter_test",
                    "run_id": "run_001",
                    "task_kind": "3d_scene",
                    "task_id": "plugin_source",
                    "plugin_path": str(source_root),
                }
            ),
            encoding="utf-8",
        )
        source = {
            "game_id": "godot_adapter_test",
            "run_id": "run_001",
            "task_kind": "3d_scene",
            "task_id": "plugin_source",
            "artifact_key": "plugin_path",
        }
        project_file = self.project / "project.godot"
        original_project = project_file.read_bytes()
        addons = self.project / "addons"
        addons.rmdir()
        outside_addons = self.root / "outside-addons"
        outside_addons.mkdir()
        addons.symlink_to(outside_addons, target_is_directory=True)

        framework_parent_escape = self.client.plugin.install_framework()
        custom_parent_escape = self.client.plugin.install(
            source, install_dir="custom_addon"
        )
        self.assert_result(framework_parent_escape, ok=False)
        self.assert_result(custom_parent_escape, ok=False)
        self.assertIn("symlink", " ".join(framework_parent_escape["errors"]))
        self.assertIn("symlink", " ".join(custom_parent_escape["errors"]))
        self.assertEqual([], list(outside_addons.iterdir()))
        self.assertEqual(original_project, project_file.read_bytes())

        addons.unlink()
        addons.mkdir()
        outside_framework = self.root / "outside-framework"
        outside_framework.mkdir()
        framework_target = addons / "a3game_playable"
        framework_target.symlink_to(outside_framework, target_is_directory=True)
        framework_target_escape = self.client.plugin.install_framework(
            replace_existing=True
        )
        self.assert_result(framework_target_escape, ok=False)
        self.assertIn("symlink", " ".join(framework_target_escape["errors"]))
        self.assertEqual([], list(outside_framework.iterdir()))

        framework_target.unlink()
        outside_custom = self.root / "outside-custom"
        outside_custom.mkdir()
        custom_target = addons / "custom_addon"
        custom_target.symlink_to(outside_custom, target_is_directory=True)
        custom_target_escape = self.client.plugin.install(
            source,
            install_dir="custom_addon",
            replace_existing=True,
        )
        self.assert_result(custom_target_escape, ok=False)
        self.assertIn("symlink", " ".join(custom_target_escape["errors"]))
        self.assertEqual([], list(outside_custom.iterdir()))
        self.assertEqual(original_project, project_file.read_bytes())

        custom_target.unlink()
        custom_install = self.client.plugin.install(
            source,
            install_dir="custom_addon",
        )
        self.assert_result(custom_install, ok=True)
        self.assertTrue((custom_target / "plugin.cfg").is_file())
        self.assertIn(
            "res://addons/custom_addon/plugin.cfg",
            project_file.read_text(encoding="utf-8"),
        )

    @unittest.skipUnless(os.name == "posix", "symlink boundary probe requires POSIX")
    def test_plugin_list_ignores_linked_addons_and_descriptors(self) -> None:
        addons = self.project / "addons"
        addons.mkdir(exist_ok=True)
        local = addons / "local_addon"
        local.mkdir()
        (local / "plugin.cfg").write_text("[plugin]\n", encoding="utf-8")

        outside = self.root / "outside-listed-addon"
        outside.mkdir()
        outside_descriptor = outside / "plugin.cfg"
        outside_descriptor.write_text("[plugin]\n", encoding="utf-8")
        (addons / "external_addon").symlink_to(outside, target_is_directory=True)
        linked_descriptor = addons / "linked_descriptor"
        linked_descriptor.mkdir()
        (linked_descriptor / "plugin.cfg").symlink_to(outside_descriptor)

        listed = self.client.plugin.list()

        self.assert_result(listed, ok=True)
        self.assertEqual(
            ["local_addon"],
            [item["asset_id"] for item in listed["artifacts"]],
        )
        self.assertEqual(2, len(listed["warnings"]))
        self.assertTrue(
            all("symbolic-link" in warning for warning in listed["warnings"])
        )

    def test_testing_report_path_failures_are_structured(self) -> None:
        non_directory = self.project / "report-parent"
        non_directory.write_text("not a directory", encoding="utf-8")
        result = self.client.testing.run_automation_tests(
            report_path=non_directory / "report.json"
        )
        self.assert_result(result, ok=False)
        self.assertIn("NotADirectoryError", " ".join(result["errors"]))
        self.assertEqual(
            str((non_directory / "report.json").resolve(strict=False)),
            result["payload"]["report_path"],
        )

    def test_testing_rejects_report_input_collisions_without_writing(self) -> None:
        project_file = self.project / "project.godot"
        original_project = project_file.read_bytes()
        project_collision = self.client.testing.run_automation_tests(
            report_path=project_file
        )
        self.assert_result(project_collision, ok=False)
        self.assertIn("protected input", " ".join(project_collision["errors"]))
        self.assertEqual(original_project, project_file.read_bytes())

        custom_runner = self.project / "tests" / "custom_runner.gd"
        custom_runner.write_bytes(b"extends SceneTree\n")
        original_runner = custom_runner.read_bytes()
        runner_collision = self.client.testing.run_automation_tests(
            script="res://tests/custom_runner.gd",
            report_path=custom_runner,
        )
        self.assert_result(runner_collision, ok=False)
        self.assertIn("protected input", " ".join(runner_collision["errors"]))
        self.assertEqual(original_runner, custom_runner.read_bytes())

        test_script = self.project / "tests" / "test_preserved.gd"
        test_script.write_bytes(b"extends RefCounted\nfunc run_test(): return true\n")
        original_test = test_script.read_bytes()
        test_collision = self.client.testing.run_automation_tests(
            report_path=test_script
        )
        self.assert_result(test_collision, ok=False)
        self.assertIn("protected input", " ".join(test_collision["errors"]))
        self.assertEqual(original_test, test_script.read_bytes())

    @unittest.skipUnless(os.name == "posix", "filesystem alias probes require POSIX")
    def test_testing_rejects_report_aliases_and_special_nodes(self) -> None:
        project_file = self.project / "project.godot"
        original_project = project_file.read_bytes()

        hard_link = self.project / "hard-linked-report.json"
        os.link(project_file, hard_link)
        hard_link_result = self.client.testing.run_automation_tests(
            report_path=hard_link
        )
        self.assert_result(hard_link_result, ok=False)
        self.assertIn("protected input", " ".join(hard_link_result["errors"]))
        self.assertEqual(original_project, project_file.read_bytes())
        self.assertEqual(original_project, hard_link.read_bytes())

        sentinel = self.root / "report-sentinel.json"
        sentinel.write_bytes(b"sentinel")
        symbolic_link = self.project / "symbolic-report.json"
        symbolic_link.symlink_to(sentinel)
        symbolic_result = self.client.testing.run_automation_tests(
            report_path=symbolic_link
        )
        self.assert_result(symbolic_result, ok=False)
        self.assertIn("symlink", " ".join(symbolic_result["errors"]).lower())
        self.assertTrue(symbolic_link.is_symlink())
        self.assertEqual(b"sentinel", sentinel.read_bytes())

        outside = self.root / "outside-report-parent"
        outside.mkdir()
        linked_parent = self.project / "linked-report-parent"
        linked_parent.symlink_to(outside, target_is_directory=True)
        parent_result = self.client.testing.run_automation_tests(
            report_path=linked_parent / "report.json"
        )
        self.assert_result(parent_result, ok=False)
        self.assertIn("symlink", " ".join(parent_result["errors"]).lower())
        self.assertEqual([], list(outside.iterdir()))

        fifo = self.project / "report.fifo"
        os.mkfifo(fifo)
        fifo_result = self.client.testing.run_automation_tests(report_path=fifo)
        self.assert_result(fifo_result, ok=False)
        self.assertIn("regular file", " ".join(fifo_result["errors"]))
        self.assertTrue(stat.S_ISFIFO(fifo.lstat().st_mode))

    def test_testing_stages_reports_before_replacing_existing_output(self) -> None:
        report = self.project / "test-results.json"
        report.write_bytes(b"previous-report")

        with mock.patch.dict(
            os.environ, {"A3GAME_FAKE_GODOT_NO_REPORT": "1"}, clear=False
        ):
            no_report = self.client.testing.run_automation_tests(report_path=report)
        self.assert_result(no_report, ok=False)
        self.assertEqual(b"previous-report", report.read_bytes())

        with mock.patch.dict(
            os.environ,
            {"A3GAME_FAKE_GODOT_TEST_REPORT_JSON": "not-json"},
            clear=False,
        ):
            invalid_report = self.client.testing.run_automation_tests(
                report_path=report
            )
        self.assert_result(invalid_report, ok=False)
        self.assertEqual(b"previous-report", report.read_bytes())

        valid_report = self.client.testing.run_automation_tests(report_path=report)
        self.assert_result(valid_report, ok=True)
        self.assertEqual(
            "gamefactory3a.godot.tests.v1",
            json.loads(report.read_text(encoding="utf-8"))["schema_version"],
        )

    def test_testing_rejects_non_finite_json_before_publishing(self) -> None:
        report = self.project / "strict-test-results.json"
        original = b'{"sentinel":true}\n'
        reports = {
            "nan_duration": (
                (
                    '{"schema_version":"gamefactory3a.godot.tests.v1",'
                    '"tests":[{"name":"nan_duration","status":"passed",'
                    '"duration_ms":NaN}],"total":1,"passed":1,"failed":0,'
                    '"skipped":0}'
                ),
                "non-standard JSON constant 'NaN'",
            ),
            "positive_infinity": (
                (
                    '{"schema_version":"gamefactory3a.godot.tests.v1",'
                    '"tests":[{"name":"positive_infinity","status":"passed",'
                    '"duration_ms":Infinity}],"total":1,"passed":1,"failed":0,'
                    '"skipped":0}'
                ),
                "non-standard JSON constant 'Infinity'",
            ),
            "negative_infinity": (
                (
                    '{"schema_version":"gamefactory3a.godot.tests.v1",'
                    '"tests":[{"name":"negative_infinity","status":"passed",'
                    '"duration_ms":-Infinity}],"total":1,"passed":1,"failed":0,'
                    '"skipped":0}'
                ),
                "non-standard JSON constant '-Infinity'",
            ),
            "overflowing_number": (
                (
                    '{"schema_version":"gamefactory3a.godot.tests.v1",'
                    '"tests":[{"name":"overflowing_number","status":"passed",'
                    '"duration_ms":1e10000}],"total":1,"passed":1,"failed":0,'
                    '"skipped":0}'
                ),
                "Out of range float values",
            ),
            "unknown_nested_field": (
                (
                    '{"schema_version":"gamefactory3a.godot.tests.v1",'
                    '"tests":[{"name":"unknown_nested_field","status":"passed",'
                    '"extensions":{"measurements":[0,NaN]}}],"total":1,'
                    '"passed":1,"failed":0,"skipped":0}'
                ),
                "non-standard JSON constant 'NaN'",
            ),
        }
        for name, (report_text, expected_detail) in reports.items():
            with self.subTest(name=name):
                report.write_bytes(original)
                with mock.patch.dict(
                    os.environ,
                    {"A3GAME_FAKE_GODOT_TEST_REPORT_JSON": report_text},
                    clear=False,
                ):
                    result = self.client.testing.run_automation_tests(
                        report_path=report
                    )

                self.assert_result(result, ok=False)
                self.assertEqual("testing.run_automation_tests", result["operation"])
                self.assertEqual(1, len(result["errors"]))
                self.assertIn(
                    "Godot test report is invalid strict JSON: ValueError:",
                    result["errors"][0],
                )
                self.assertIn(expected_detail, result["errors"][0])
                self.assertEqual(0, result["payload"]["returncode"])
                json.dumps(result, allow_nan=False)
                self.assertEqual(original, report.read_bytes())

    def test_testing_rejects_malformed_or_unclassified_native_reports(self) -> None:
        malformed_reports = (
            {
                "schema_version": "gamefactory3a.godot.tests.v1",
                "tests": [{"name": "crashed_case", "status": "crashed"}],
            },
            {
                "schema_version": "gamefactory3a.godot.tests.v1",
                "tests": [{"name": "missing_status"}],
            },
            {
                "schema_version": "gamefactory3a.godot.tests.v1",
                "tests": ["not-an-object"],
            },
            {
                "schema_version": "gamefactory3a.godot.tests.v0",
                "tests": [{"name": "wrong_schema", "status": "passed"}],
            },
            {
                "schema_version": "gamefactory3a.godot.tests.v1",
                "tests": [{"name": "bad_count", "status": "passed"}],
                "total": 2,
            },
        )
        for report in malformed_reports:
            with self.subTest(report=report):
                with mock.patch.dict(
                    os.environ,
                    {
                        "A3GAME_FAKE_GODOT_TEST_REPORT_JSON": json.dumps(report),
                    },
                    clear=False,
                ):
                    result = self.client.testing.run_automation_tests()
                self.assert_result(result, ok=False)
                self.assertIn(
                    "report schema is invalid",
                    " ".join(result["errors"]),
                )
                self.assertEqual(0, result["payload"]["returncode"])

    def test_obj_and_invalid_runtime_arguments_fail_structurally(self) -> None:
        obj_source, _ = self.make_artifact(
            task_id="unsupported-obj",
            suffix=".obj",
            content=b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
        )
        for asset_type in (
            "environment",
            "prop",
            "scene",
            "static_mesh",
            "weapon",
        ):
            with self.subTest(asset_type=asset_type):
                obj_validation = self.client.assets.validate(obj_source, asset_type)
                self.assert_result(obj_validation, ok=False)
                self.assertIn(
                    f"Unsupported {asset_type} format .obj",
                    obj_validation["errors"][0],
                )
        self.assert_result(self.client.world.build(obj_source), ok=False)

        invalid_join = self.client.runtime.sessions.join(parameters={"bad": object()})
        self.assert_result(invalid_join, ok=False)
        json.dumps(invalid_join)
        for invalid_mapping in (
            {"transform": 1},
            {"parameters": 1},
        ):
            with self.subTest(invalid_mapping=next(iter(invalid_mapping))):
                invalid_join = self.client.runtime.sessions.join(**invalid_mapping)
                self.assert_result(invalid_join, ok=False)
                self.assertEqual("runtime.sessions.join", invalid_join["operation"])
                self.assertIn("must be a mapping", " ".join(invalid_join["errors"]))
                json.dumps(invalid_join)
        invalid_status = self.client.observe.check_status(
            timeout="bad",
            check_runtime=True,
        )
        self.assert_result(invalid_status, ok=False)
        self.assertEqual("observe.check_status", invalid_status["operation"])
        self.assertIn("ValueError", " ".join(invalid_status["errors"]))
        json.dumps(invalid_status)
        self.assertEqual(
            [], self.client.runtime.sessions.snapshot()["payload"]["sessions"]
        )

        joined = self.client.runtime.sessions.join()
        self.assert_result(joined, ok=True)
        controller_id = joined["payload"]["controller_id"]
        invalid_input = self.client.runtime.sessions.apply_input(
            controller_id,
            move_x="not-a-number",
        )
        self.assert_result(invalid_input, ok=False)
        json.dumps(invalid_input)
        self.assertEqual(
            {},
            self.client.runtime.sessions.snapshot()["payload"]["sessions"][0][
                "last_input"
            ],
        )

        invalid_launch = self.client.runtime.launch_game(
            extra_args=None,
            dry_run=True,
        )
        self.assert_result(invalid_launch, ok=False)
        json.dumps(invalid_launch)

        (self.project / "export_presets.cfg").write_text(
            '[preset.0]\n\nname="Linux"\nplatform="Linux/X11"\n',
            encoding="utf-8",
        )
        invalid_build = self.client.build.project(
            preset="Linux",
            output_path="builds/game.x86_64",
            extra_args=None,
            dry_run=True,
        )
        self.assert_result(invalid_build, ok=False)
        json.dumps(invalid_build)

    def test_runtime_session_reset_and_clear_remove_only_target_state(self) -> None:
        messages: list[dict] = []

        def acknowledge(message: dict, **_kwargs) -> dict:
            messages.append(dict(message))
            return {"ok": True, "operation": message["operation"], "reachable": True}

        sessions = self.client.runtime.sessions
        with mock.patch.object(sessions, "_send", side_effect=acknowledge):
            joined_default = sessions.join(participant_id="default")
            joined_arena_a = sessions.join(participant_id="arena-a", world_id="arena")
            joined_arena_b = sessions.join(participant_id="arena-b", world_id="arena")
            joined_lobby = sessions.join(participant_id="lobby", world_id="lobby")
            for joined in (
                joined_default,
                joined_arena_a,
                joined_arena_b,
                joined_lobby,
            ):
                self.assert_result(joined, ok=True)

            reset_arena = sessions.reset_world(world_id="arena")
            self.assert_result(reset_arena, ok=True)
            self.assertEqual("arena", reset_arena["payload"]["world_id"])
            self.assertEqual(2, reset_arena["payload"]["removed_sessions"])
            self.assertEqual(2, reset_arena["payload"]["sessions_remaining"])
            self.assertEqual(0, sessions.snapshot(world_id="arena")["payload"]["count"])
            self.assertEqual(1, sessions.snapshot(world_id="lobby")["payload"]["count"])

            repeated_reset = sessions.reset_world(world_id="arena")
            self.assert_result(repeated_reset, ok=True)
            self.assertEqual(0, repeated_reset["payload"]["removed_sessions"])
            self.assertEqual(2, repeated_reset["payload"]["sessions_remaining"])

            reset_default = sessions.reset_world()
            self.assert_result(reset_default, ok=True)
            self.assertEqual("world_001", reset_default["payload"]["world_id"])
            self.assertEqual(1, reset_default["payload"]["removed_sessions"])
            self.assertEqual(1, reset_default["payload"]["sessions_remaining"])
            self.assertEqual(
                ["lobby"],
                [
                    session["world_id"]
                    for session in sessions.snapshot()["payload"]["sessions"]
                ],
            )

            clear_retained = sessions.clear_entity(
                controller_id=joined_lobby["payload"]["controller_id"],
                destroy_actor=False,
            )
            self.assert_result(clear_retained, ok=True)
            self.assertFalse(clear_retained["payload"]["destroy_actor"])
            self.assertEqual(1, clear_retained["payload"]["removed_sessions"])
            self.assertEqual(0, clear_retained["payload"]["sessions_remaining"])
            self.assertEqual(
                {
                    "operation": "entity.clear",
                    "entity_id": joined_lobby["payload"]["entity_id"],
                    "destroy_actor": False,
                },
                messages[-1],
            )

            joined_destroy = sessions.join(participant_id="destroy", world_id="arena")
            clear_destroyed = sessions.clear_entity(
                entity_id=joined_destroy["payload"]["entity_id"]
            )
            self.assert_result(clear_destroyed, ok=True)
            self.assertTrue(clear_destroyed["payload"]["destroy_actor"])
            self.assertEqual(1, clear_destroyed["payload"]["removed_sessions"])
            self.assertEqual(0, sessions.snapshot()["payload"]["count"])

            message_count = len(messages)
            missing = sessions.clear_entity(entity_id="missing")
            self.assert_result(missing, ok=False)
            self.assertEqual(message_count, len(messages))

        reset_messages = [
            message for message in messages if message["operation"] == "world.reset"
        ]
        self.assertEqual(
            ["arena", "arena", "world_001"],
            [message["world_id"] for message in reset_messages],
        )

    def test_runtime_leave_deactivates_controller_and_rejects_false_input(self) -> None:
        messages: list[dict] = []

        def acknowledge(message: dict, **_kwargs) -> dict:
            messages.append(dict(message))
            if message["operation"] == "session.input" and message["input"]["seq"] == 2:
                return {
                    "ok": False,
                    "operation": message["operation"],
                    "reachable": True,
                    "error": "Unknown controller_id",
                }
            return {
                "ok": True,
                "operation": message["operation"],
                "reachable": True,
            }

        sessions = self.client.runtime.sessions
        with mock.patch.object(sessions, "_send", side_effect=acknowledge):
            joined = sessions.join(participant_id="lifecycle")
            self.assert_result(joined, ok=True)
            controller_id = joined["payload"]["controller_id"]

            accepted = sessions.apply_input(controller_id, move_x=0.5, seq=1)
            self.assert_result(accepted, ok=True)
            accepted_input = dict(accepted["payload"]["input"])

            rejected_by_runtime = sessions.apply_input(
                controller_id,
                move_x=0.75,
                seq=2,
            )
            self.assert_result(rejected_by_runtime, ok=False)
            self.assertIn("rejected", " ".join(rejected_by_runtime["errors"]))
            self.assertEqual(
                accepted_input,
                sessions.snapshot()["payload"]["sessions"][0]["last_input"],
            )

            left = sessions.leave(controller_id=controller_id)
            self.assert_result(left, ok=True)
            self.assertFalse(left["payload"]["active"])
            self.assertFalse(left["payload"]["online"])
            message_count = len(messages)

            after_leave = sessions.apply_input(controller_id, move_x=1.0, seq=3)
            heartbeat = sessions.heartbeat(controller_id)
            self.assert_result(after_leave, ok=False)
            self.assert_result(heartbeat, ok=False)
            self.assertEqual(message_count, len(messages))

            snapshot = sessions.snapshot()["payload"]["sessions"][0]
            self.assertFalse(snapshot["active"])
            self.assertFalse(snapshot["online"])
            self.assertEqual(accepted_input, snapshot["last_input"])

    def test_runtime_participant_reconnect_replaces_active_controller(self) -> None:
        native_sessions: dict[str, dict] = {}
        messages: list[dict] = []

        def bridge(message: dict, **_kwargs) -> dict:
            messages.append(dict(message))
            operation = message["operation"]
            if operation == "status":
                return {
                    "ok": True,
                    "operation": operation,
                    "reachable": True,
                    "sessions": len(native_sessions),
                }
            if operation == "session.join":
                replaced = [
                    controller_id
                    for controller_id, session in native_sessions.items()
                    if session["participant_id"] == message["participant_id"]
                ]
                if any(
                    native_sessions[controller_id]["entity_id"] != message["entity_id"]
                    for controller_id in replaced
                ):
                    return {
                        "ok": False,
                        "operation": operation,
                        "reachable": True,
                        "error": "participant entity mismatch",
                    }
                for controller_id in replaced:
                    native_sessions.pop(controller_id)
                native_sessions[message["controller_id"]] = dict(message)
                return {
                    "ok": True,
                    "operation": operation,
                    "reachable": True,
                    "replaced_controllers": len(replaced),
                    "sessions": len(native_sessions),
                }
            if operation == "session.leave":
                native_sessions.pop(message["controller_id"], None)
                return {
                    "ok": True,
                    "operation": operation,
                    "reachable": True,
                    "sessions": len(native_sessions),
                }
            if operation == "session.input":
                accepted = message["controller_id"] in native_sessions
                return {
                    "ok": accepted,
                    "operation": operation,
                    "reachable": True,
                    "error": "" if accepted else "Unknown controller_id",
                }
            if operation == "entity.clear":
                removed = [
                    controller_id
                    for controller_id, session in native_sessions.items()
                    if session["entity_id"] == message["entity_id"]
                ]
                for controller_id in removed:
                    native_sessions.pop(controller_id)
                return {
                    "ok": True,
                    "operation": operation,
                    "reachable": True,
                    "removed_sessions": len(removed),
                    "sessions": len(native_sessions),
                }
            raise AssertionError(f"unexpected bridge operation: {operation}")

        sessions = self.client.runtime.sessions
        with mock.patch.object(sessions, "_send", side_effect=bridge):
            first = sessions.join(participant_id="reconnect", world_id="arena")
            second = sessions.join(participant_id="reconnect", world_id="ignored")
            self.assert_result(first, ok=True)
            self.assert_result(second, ok=True)
            first_controller = first["payload"]["controller_id"]
            second_controller = second["payload"]["controller_id"]
            self.assertNotEqual(first_controller, second_controller)
            self.assertEqual(
                first["payload"]["entity_id"], second["payload"]["entity_id"]
            )
            self.assertEqual("arena", second["payload"]["world_id"])
            self.assertEqual(1, second["payload"]["bridge"]["replaced_controllers"])

            message_count = len(messages)
            old_input = sessions.apply_input(first_controller, seq=1)
            self.assert_result(old_input, ok=False)
            self.assertEqual(message_count, len(messages))
            native_old_input = sessions._send(
                {
                    "operation": "session.input",
                    "controller_id": first_controller,
                    "input": {"seq": 1},
                }
            )
            self.assertFalse(native_old_input["ok"])

            snapshot = sessions.snapshot()["payload"]
            participant_sessions = [
                session
                for session in snapshot["sessions"]
                if session["participant_id"] == "reconnect"
            ]
            self.assertEqual(2, len(participant_sessions))
            self.assertEqual(
                [False, True], [item["active"] for item in participant_sessions]
            )
            self.assertEqual(1, snapshot["active_count"])
            self.assertEqual(snapshot["active_count"], sessions.probe()["sessions"])

            left = sessions.leave(participant_id="reconnect")
            self.assert_result(left, ok=True)
            self.assertEqual(second_controller, left["payload"]["controller_id"])
            self.assertEqual(second_controller, messages[-1]["controller_id"])
            new_input = sessions.apply_input(second_controller, seq=2)
            self.assert_result(new_input, ok=False)
            native_new_input = sessions._send(
                {
                    "operation": "session.input",
                    "controller_id": second_controller,
                    "input": {"seq": 2},
                }
            )
            self.assertFalse(native_new_input["ok"])
            snapshot = sessions.snapshot()["payload"]
            self.assertEqual(0, snapshot["active_count"])
            self.assertEqual(snapshot["active_count"], sessions.probe()["sessions"])

            cleared = sessions.clear_entity(participant_id="reconnect")
            self.assert_result(cleared, ok=True)
            self.assertEqual(2, cleared["payload"]["removed_sessions"])
            self.assertEqual(0, sessions.snapshot()["payload"]["count"])

    def test_runtime_rejects_received_nacks_and_protocol_errors_without_state(
        self,
    ) -> None:
        server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.addCleanup(server.close)
        server.bind(("127.0.0.1", 0))
        server.settimeout(5.0)
        runtime_port = int(server.getsockname()[1])
        response_kinds = (
            "nack_with_forged_reachability",
            "ack",
            "wrong_request_id",
            "invalid_json",
            "nack_with_forged_reachability",
            "nack_with_forged_reachability",
            "invalid_json",
            "wrong_request_id",
        )
        requests: list[dict] = []
        responder_errors: list[BaseException] = []

        def respond() -> None:
            try:
                for kind in response_kinds:
                    packet, address = server.recvfrom(65535)
                    request = json.loads(packet.decode("utf-8"))
                    requests.append(request)
                    if kind == "invalid_json":
                        server.sendto(b"{not-json", address)
                        continue
                    response = {
                        "ok": kind == "ack",
                        "operation": request["operation"],
                        "request_id": (
                            "wrong-request-id"
                            if kind == "wrong_request_id"
                            else request["request_id"]
                        ),
                    }
                    if kind == "nack_with_forged_reachability":
                        response.update(
                            {
                                "reachable": False,
                                "error": "forced runtime NACK",
                            }
                        )
                    server.sendto(json.dumps(response).encode("utf-8"), address)
            except (KeyError, OSError, TypeError, ValueError) as exc:
                responder_errors.append(exc)

        responder = threading.Thread(target=respond, daemon=True)
        responder.start()
        client = GodotClient(
            project_path=self.project,
            godot_executable=self.fake_godot,
            runtime_host="127.0.0.1",
            runtime_port=runtime_port,
            editor_timeout=5,
            import_timeout=5,
        )
        sessions = client.runtime.sessions

        rejected_join = sessions.join(participant_id="rejected")
        accepted_join = sessions.join(participant_id="accepted")
        controller_id = accepted_join["payload"]["controller_id"]
        wrong_id_input = sessions.apply_input(controller_id, seq=1)
        invalid_json_input = sessions.apply_input(controller_id, seq=2)
        rejected_input = sessions.apply_input(controller_id, seq=3)
        rejected_leave = sessions.leave(controller_id=controller_id)
        rejected_reset = sessions.reset_world()
        rejected_clear = sessions.clear_entity(controller_id=controller_id)

        responder.join(timeout=5.0)
        self.assertFalse(responder.is_alive())
        if responder_errors:
            raise responder_errors[0]
        self.assertEqual(
            [
                "session.join",
                "session.join",
                "session.input",
                "session.input",
                "session.input",
                "session.leave",
                "world.reset",
                "entity.clear",
            ],
            [request["operation"] for request in requests],
        )

        self.assert_result(rejected_join, ok=False)
        rejected_bridge = rejected_join["payload"]["bridge"]
        self.assertIs(rejected_bridge["reachable"], True)
        self.assertEqual("forced runtime NACK", rejected_bridge["error"])
        self.assert_result(accepted_join, ok=True)

        for result, error in (
            (wrong_id_input, "request_id mismatch"),
            (invalid_json_input, "JSONDecodeError"),
            (rejected_input, "forced runtime NACK"),
            (rejected_leave, "forced runtime NACK"),
            (rejected_reset, "JSONDecodeError"),
            (rejected_clear, "request_id mismatch"),
        ):
            with self.subTest(error=error):
                self.assert_result(result, ok=False)
                bridge = result["payload"]["bridge"]
                self.assertIs(bridge["reachable"], True)
                self.assertIn(error, bridge["error"])

        snapshot = sessions.snapshot()
        self.assertEqual(1, snapshot["payload"]["count"])
        self.assertEqual(
            controller_id,
            snapshot["payload"]["sessions"][0]["controller_id"],
        )
        retained = snapshot["payload"]["sessions"][0]
        self.assertIs(retained["active"], True)
        self.assertIs(retained["online"], True)
        self.assertEqual({}, retained["last_input"])

    def test_runtime_clear_rejects_non_boolean_destroy_without_side_effects(
        self,
    ) -> None:
        messages: list[dict] = []

        def acknowledge(message: dict, **_kwargs) -> dict:
            messages.append(dict(message))
            return {"ok": True, "operation": message["operation"], "reachable": True}

        sessions = self.client.runtime.sessions
        with mock.patch.object(sessions, "_send", side_effect=acknowledge):
            joined = sessions.join(participant_id="strict-destroy")
            self.assert_result(joined, ok=True)
            controller_id = joined["payload"]["controller_id"]
            message_count = len(messages)

            for invalid in ("false", 0, 1, None):
                with self.subTest(invalid=invalid):
                    result = sessions.clear_entity(
                        controller_id=controller_id,
                        destroy_actor=invalid,
                    )
                    self.assert_result(result, ok=False)
                    self.assertIn(
                        "destroy_actor must be a boolean", result["errors"][0]
                    )
                    self.assertEqual(message_count, len(messages))
                    snapshot = sessions.snapshot()
                    self.assertEqual(1, snapshot["payload"]["count"])
                    self.assertEqual(
                        controller_id,
                        snapshot["payload"]["sessions"][0]["controller_id"],
                    )

    def test_corrupt_artifact_registry_fails_without_overwriting_source(
        self,
    ) -> None:
        registry_path = self.client._config.artifact_registry_path
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        first_entry = {
            "artifact_id": "duplicate",
            "asset_id": "first",
            "type": "prop",
            "backend_path": "res://assets/imported/props/first.glb",
            "source_path": "/generated/first.glb",
            "backend_class": "PackedScene",
            "state": "ready",
            "spawnable": True,
            "metadata": {},
        }
        second_entry = {
            **first_entry,
            "asset_id": "second",
            "backend_path": "res://assets/imported/props/second.glb",
            "source_path": "/generated/second.glb",
        }
        missing_asset_id = dict(first_entry)
        missing_asset_id.pop("asset_id")
        malformed_payloads = {
            "truncated_json": b'{"artifacts":[',
            "wrong_schema": json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.artifacts.v0",
                    "artifacts": [],
                }
            ).encode(),
            "top_level_list": b"[]",
            "wrong_artifacts_container": json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.artifacts.v1",
                    "artifacts": {},
                }
            ).encode(),
            "non_object_entry": json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.artifacts.v1",
                    "artifacts": ["not-an-artifact"],
                }
            ).encode(),
            "missing_required_field": json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.artifacts.v1",
                    "artifacts": [missing_asset_id],
                }
            ).encode(),
            "wrong_field_type": json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.artifacts.v1",
                    "artifacts": [{**first_entry, "spawnable": "yes"}],
                }
            ).encode(),
            "wrong_metadata_type": json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.artifacts.v1",
                    "artifacts": [{**first_entry, "metadata": []}],
                }
            ).encode(),
            "duplicate_artifact_id": json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.artifacts.v1",
                    "artifacts": [first_entry, second_entry],
                }
            ).encode(),
            "ambiguous_asset_id": json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.artifacts.v1",
                    "artifacts": [
                        first_entry,
                        {
                            **second_entry,
                            "artifact_id": "second-artifact",
                            "asset_id": "first",
                        },
                    ],
                }
            ).encode(),
            "ambiguous_backend_path": json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.artifacts.v1",
                    "artifacts": [
                        first_entry,
                        {
                            **second_entry,
                            "artifact_id": "second-artifact",
                            "backend_path": first_entry["backend_path"],
                        },
                    ],
                }
            ).encode(),
        }

        for name, malformed in malformed_payloads.items():
            with self.subTest(name=name):
                registry_path.write_bytes(malformed)
                listed = self.client.assets.list_registered()
                self.assert_result(listed, ok=False)
                self.assertIn("registry read failed", listed["errors"][0])
                with self.assertRaises((json.JSONDecodeError, TypeError, ValueError)):
                    self.client.assets._register_resource(
                        resource_path="res://assets/imported/recovery.glb",
                        asset_type="prop",
                        asset_id="must_not_replace_registry",
                    )
                self.assertEqual(malformed, registry_path.read_bytes())

        valid = json.dumps(
            {
                "schema_version": "gamefactory3a.godot.artifacts.v1",
                "artifacts": [],
            }
        ).encode()
        registry_path.write_bytes(valid)
        with mock.patch(
            "engine_adapters.godot._internal.registry.read_managed_text",
            side_effect=OSError("forced registry read failure"),
        ), mock.patch.object(
            self.client.assets._registry, "_write"
        ) as write:
            listed = self.client.assets.list_registered()
            self.assert_result(listed, ok=False)
            self.assertIn("forced registry read failure", listed["errors"][0])
            with self.assertRaisesRegex(OSError, "forced registry read failure"):
                self.client.assets._register_resource(
                    resource_path="res://assets/imported/recovery.glb",
                    asset_type="prop",
                    asset_id="must_not_replace_registry",
                )
            write.assert_not_called()
        self.assertEqual(valid, registry_path.read_bytes())

    def test_artifact_registry_rejects_symlink_without_following_it(self) -> None:
        external = self.root / "external-artifacts.json"
        external.write_text(
            json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.artifacts.v1",
                    "artifacts": [],
                }
            ),
            encoding="utf-8",
        )
        original = external.read_bytes()
        linked = self.root / "linked-artifacts.json"
        try:
            linked.symlink_to(external)
        except OSError as exc:
            self.skipTest(f"symbolic links are unavailable: {exc}")

        with mock.patch.dict(
            os.environ,
            {"A3GAME_GODOT_ARTIFACT_REGISTRY": str(linked)},
            clear=False,
        ):
            client = GodotClient(
                project_path=self.project,
                godot_executable=self.fake_godot,
            )
            self.assertEqual(
                Path(os.path.abspath(linked)),
                client._config.artifact_registry_path,
            )
            listed = client.assets.list_registered()
            self.assert_result(listed, ok=False)
            self.assertIn("not a link", listed["errors"][0])
            with self.assertRaisesRegex(ValueError, "not a link"):
                client.assets._register_resource(
                    resource_path="res://assets/imported/props/blocked.glb",
                    asset_type="prop",
                    asset_id="blocked",
                )

        self.assertTrue(linked.is_symlink())
        self.assertEqual(original, external.read_bytes())

    @unittest.skipUnless(os.name == "posix", "symlink boundary probe requires POSIX")
    def test_artifact_registry_rejects_symlinked_parent_chain(self) -> None:
        outside = self.root / "outside-adapter-state"
        outside.mkdir()
        external_registry = outside / "artifacts.json"
        external_registry.write_text(
            json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.artifacts.v1",
                    "artifacts": [],
                }
            ),
            encoding="utf-8",
        )
        original = external_registry.read_bytes()
        linked_data_root = self.root / "linked-adapter-state"
        linked_data_root.symlink_to(outside, target_is_directory=True)

        with mock.patch.dict(
            os.environ,
            {
                "A3GAME_GODOT_DATA_ROOT": str(linked_data_root),
                "A3GAME_GODOT_ARTIFACT_REGISTRY": "",
                "A3GAME_DATA_ROOT": "",
                "A3GAME_ARTIFACT_REGISTRY": "",
            },
            clear=False,
        ):
            client = GodotClient(
                project_path=self.project,
                godot_executable=self.fake_godot,
            )
            self.assertEqual(
                Path(os.path.abspath(linked_data_root)) / "artifacts.json",
                client._config.artifact_registry_path,
            )
            listed = client.assets.list_registered()
            self.assert_result(listed, ok=False)
            self.assertIn("symlink", " ".join(listed["errors"]).lower())
            with self.assertRaisesRegex(ValueError, "symlink"):
                client.assets._register_resource(
                    resource_path="res://main.tscn",
                    asset_type="scene",
                    asset_id="must-not-escape",
                    backend_class="PackedScene",
                )

        self.assertTrue(linked_data_root.is_symlink())
        self.assertEqual(original, external_registry.read_bytes())

    def test_corrupt_registry_returns_failures_at_public_read_boundaries(
        self,
    ) -> None:
        draft_path = self.client.world._draft_path("corrupt-registry")
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(
            json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.world_draft.v1",
                    "draft_id": "corrupt-registry",
                    "world_id": "world",
                    "project_id": "project",
                    "status": "draft",
                    "scene_artifact_id": "missing",
                    "scene_path": "res://missing.tscn",
                    "artifacts": ["missing"],
                    "metadata": {},
                    "created_at": time.time(),
                }
            ),
            encoding="utf-8",
        )
        material_source, _material_path = self.make_artifact(
            task_id="corrupt_registry_material",
            suffix=".png",
            content=b"not-decoded-before-registry-read",
            task_kind="3d_object",
            artifact_key="image_path",
        )
        registry_path = self.client._config.artifact_registry_path
        malformed = b"{not valid json"
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_bytes(malformed)

        results = (
            self.client.assets.list(),
            self.client.assets.list_registered(),
            self.client.assets.get_metadata("missing"),
            self.client.reflection.inspect_artifact("missing", live=False),
            self.client.animation.resolve_skeleton("missing"),
            self.client.animation.validate_compatibility(
                "missing", "Character/Skeleton3D"
            ),
            self.client.bindings.bind_pbr_material(
                asset_id="material",
                source=material_source,
                mesh_assets=["missing"],
            ),
            self.client.world.create_draft(
                {
                    "scene_artifact_id": "missing",
                    "world_id": "world",
                }
            ),
            self.client.world.validate_draft("corrupt-registry"),
            self.client.runtime.sessions.join(avatar_artifact_id="missing"),
        )
        expected_operations = (
            "assets.list",
            "assets.list_registered",
            "assets.get_metadata",
            "reflection.inspect_artifact",
            "animation.resolve_skeleton",
            "animation.validate_compatibility",
            "bindings.bind_pbr_material",
            "world.create_draft",
            "world.validate_draft",
            "runtime.sessions.join",
        )
        self.assertEqual(len(results), len(expected_operations))
        for result, operation in zip(results, expected_operations):
            with self.subTest(operation=operation):
                self.assert_result(result, ok=False)
                self.assertEqual(operation, result["operation"])
                self.assertIn("JSONDecodeError", " ".join(result["errors"]))

        self.assertEqual(malformed, registry_path.read_bytes())

    def test_artifact_registry_rejects_ambiguous_upsert_without_writing(self) -> None:
        first = self.client.assets._register_resource(
            resource_path="res://assets/imported/props/first.glb",
            asset_type="prop",
            asset_id="shared-reference",
            backend_class="PackedScene",
        )
        registry_path = self.client._config.artifact_registry_path
        original = registry_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "ambiguous lookup reference"):
            self.client.assets._register_resource(
                resource_path="res://assets/imported/motions/second.glb",
                asset_type="motion",
                asset_id="shared-reference",
                backend_class="PackedScene",
            )
        self.assertEqual(original, registry_path.read_bytes())

        with self.assertRaisesRegex(ValueError, "ambiguous lookup reference"):
            self.client.assets._register_resource(
                resource_path="res://assets/imported/props/third.glb",
                asset_type="prop",
                asset_id=first.artifact_id,
                backend_class="PackedScene",
            )
        self.assertEqual(original, registry_path.read_bytes())

    @unittest.skipUnless(
        REAL_GODOT,
        "set A3GAME_TEST_GODOT_EXECUTABLE to run the native test runner contract",
    )
    def test_real_godot_test_runner_rejects_out_of_contract_results(self) -> None:
        tests_dir = self.project / "tests"
        tests_dir.mkdir(exist_ok=True)
        scripts = {
            "test_valid_bool.gd": "return true",
            "test_valid_dictionary.gd": 'return {"ok": true}',
            "test_integer_return.gd": "return 1",
            "test_string_return.gd": 'return "true"',
            "test_missing_ok.gd": 'return {"message": "missing ok"}',
            "test_non_boolean_ok.gd": 'return {"ok": 1}',
        }
        for filename, result_expression in scripts.items():
            (tests_dir / filename).write_text(
                f"extends RefCounted\n\nfunc run_test():\n\t{result_expression}\n",
                encoding="utf-8",
            )
        client = GodotClient(
            project_path=self.project,
            godot_executable=Path(REAL_GODOT).expanduser().resolve(strict=True),
            editor_timeout=30,
            import_timeout=30,
        )

        result = client.testing.run_automation_tests(timeout=10)

        self.assert_result(result, ok=False)
        self.assertEqual(1, result["payload"]["returncode"])
        self.assertEqual(6, result["payload"]["matched_count"])
        self.assertEqual(2, result["payload"]["passed_count"])
        self.assertEqual(4, result["payload"]["failed_count"])
        cases = {Path(case["file"]).name: case for case in result["payload"]["cases"]}
        for filename in (
            "test_integer_return.gd",
            "test_string_return.gd",
            "test_missing_ok.gd",
            "test_non_boolean_ok.gd",
        ):
            self.assertEqual("failed", cases[filename]["status"])
        self.assertIn(
            "must return bool or Dictionary",
            cases["test_integer_return.gd"]["message"],
        )
        self.assertIn(
            "must return bool or Dictionary",
            cases["test_string_return.gd"]["message"],
        )
        self.assertIn(
            "must contain a boolean 'ok' field",
            cases["test_missing_ok.gd"]["message"],
        )
        self.assertIn(
            "field 'ok' must be a boolean",
            cases["test_non_boolean_ok.gd"]["message"],
        )

    @unittest.skipUnless(
        REAL_GODOT,
        "set A3GAME_TEST_GODOT_EXECUTABLE to run the native World contract",
    )
    def test_real_godot_world_record_tampering_contract(self) -> None:
        client = GodotClient(
            project_path=self.project,
            godot_executable=Path(REAL_GODOT).expanduser().resolve(strict=True),
            editor_timeout=30,
            import_timeout=30,
        )
        scene = client.assets._register_resource(
            resource_path="res://main.tscn",
            asset_type="scene",
            asset_id="real-strict-world-scene",
            backend_class="PackedScene",
        )
        created = client.world.create_draft(
            {
                "draft_id": "real-strict-draft",
                "world_id": "real-strict-world",
                "project_id": "real-strict-project",
                "scene_artifact_id": scene.artifact_id,
            }
        )
        self.assert_result(created, ok=True)
        draft_path = Path(created["payload"]["path"])
        valid_draft = json.loads(draft_path.read_text(encoding="utf-8"))
        damaged = {
            **valid_draft,
            "schema_version": "attacker.invalid.v999",
            "status": "already_deleted",
            "draft_id": "different-id",
        }
        draft_path.write_text(json.dumps(damaged), encoding="utf-8")
        self.assert_result(
            client.world.validate_draft("real-strict-draft"),
            ok=False,
        )
        self.assert_result(
            client.world.publish_draft("real-strict-draft"),
            ok=False,
        )

        draft_path.write_text(json.dumps(valid_draft), encoding="utf-8")
        published = client.world.publish_draft("real-strict-draft")
        self.assert_result(published, ok=True)
        package_path = Path(published["payload"]["path"])
        package_path.write_text("{}", encoding="utf-8")
        listed = client.world.list_packages()
        self.assert_result(listed, ok=False)
        self.assertEqual([], listed["artifacts"])

    @unittest.skipUnless(
        REAL_GODOT,
        "set A3GAME_TEST_GODOT_EXECUTABLE to run the native runtime contract",
    )
    def test_real_godot_runtime_session_cleanup_contract(self) -> None:
        executable = Path(REAL_GODOT).expanduser().resolve(strict=True)
        project = self.root / "real-runtime-project"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as port_probe:
            port_probe.bind(("127.0.0.1", 0))
            runtime_port = int(port_probe.getsockname()[1])
        client = GodotClient(
            project_path=project,
            godot_executable=executable,
            runtime_port=runtime_port,
            editor_timeout=30,
            import_timeout=30,
        )
        self.assert_result(
            client.project.create(project_name="Runtime Session Contract"), ok=True
        )
        self.assert_result(client.plugin.install_framework(), ok=True)

        marker_root = project / "runtime-markers"
        marker_root.mkdir()
        (project / "probe_entity.gd").write_text(
            'extends "res://addons/a3game_playable/runtime_entity.gd"\n\n'
            'var marker_root := ""\n\n'
            "func _exit_tree() -> void:\n"
            "\tif marker_root.is_empty():\n"
            "\t\treturn\n"
            "\tvar marker := FileAccess.open("
            'marker_root.path_join(a3game_entity_id + ".destroyed"), '
            "FileAccess.WRITE)\n"
            "\tif marker != null:\n"
            "\t\tmarker.store_string(a3game_entity_id)\n",
            encoding="utf-8",
        )
        (project / "main.gd").write_text(
            "extends Node3D\n\n"
            'const ProbeEntity = preload("res://probe_entity.gd")\n'
            f"const MARKER_ROOT := {json.dumps(str(marker_root))}\n\n"
            "func _ready() -> void:\n"
            "\tA3GameRuntime.session_joined.connect(_on_session_joined)\n"
            "\tA3GameRuntime.session_reconnected.connect(_on_session_reconnected)\n"
            "\tA3GameRuntime.session_left.connect(_on_session_left)\n\n"
            "func _write_signal_marker(kind: String, session: Dictionary) -> void:\n"
            '\tvar controller_id := str(session.get("controller_id", ""))\n'
            "\tvar marker := FileAccess.open("
            'MARKER_ROOT.path_join(controller_id + "." + kind), '
            "FileAccess.WRITE)\n"
            "\tif marker != null:\n"
            "\t\tmarker.store_string(controller_id)\n\n"
            "func _on_session_joined(session: Dictionary) -> void:\n"
            "\tvar entity = ProbeEntity.new()\n"
            '\tentity.a3game_entity_id = str(session.get("entity_id", ""))\n'
            "\tentity.marker_root = MARKER_ROOT\n"
            "\tadd_child(entity)\n\n"
            "func _on_session_reconnected("
            "_previous_session: Dictionary, session: Dictionary) -> void:\n"
            '\t_write_signal_marker("reconnected", session)\n\n'
            "func _on_session_left(session: Dictionary) -> void:\n"
            '\t_write_signal_marker("left", session)\n',
            encoding="utf-8",
        )
        (project / "main.tscn").write_text(
            "[gd_scene load_steps=2 format=3]\n\n"
            '[ext_resource path="res://main.gd" type="Script" id="1"]\n\n'
            '[node name="Main" type="Node3D"]\n'
            'script = ExtResource("1")\n',
            encoding="utf-8",
        )

        launched = client.runtime.launch_game(headless=True)
        self.assert_result(launched, ok=True)
        process_id = int(launched["payload"]["process_id"])

        def wait_for(predicate, timeout: float = 3.0) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if predicate():
                    return True
                time.sleep(0.05)
            return bool(predicate())

        try:
            self.assertTrue(
                wait_for(lambda: bool(client.runtime.sessions.probe().get("ok")))
            )

            retained = client.runtime.sessions.join(
                participant_id="retained",
                world_id="retained_world",
                require_runtime=True,
            )
            self.assert_result(retained, ok=True)
            retained_entity = retained["payload"]["entity_id"]
            retained_marker = marker_root / f"{retained_entity}.destroyed"
            clear_retained = client.runtime.sessions.clear_entity(
                controller_id=retained["payload"]["controller_id"],
                destroy_actor=False,
            )
            self.assert_result(clear_retained, ok=True)
            retained_bridge = clear_retained["payload"]["bridge"]
            self.assertEqual(1, retained_bridge["removed_sessions"])
            self.assertEqual(1, retained_bridge["matched_nodes"])
            self.assertEqual(0, retained_bridge["destroy_queued_nodes"])
            self.assertEqual(0, client.runtime.sessions.snapshot()["payload"]["count"])
            self.assertEqual(0, client.runtime.sessions.probe()["sessions"])
            time.sleep(0.2)
            self.assertFalse(retained_marker.exists())

            malformed = client.runtime.sessions._send(
                {
                    "operation": "entity.clear",
                    "entity_id": retained_entity,
                    "destroy_actor": "false",
                }
            )
            self.assertFalse(malformed["ok"])
            self.assertIn("boolean", malformed["error"])
            self.assertFalse(retained_marker.exists())

            destroyed = client.runtime.sessions.join(
                participant_id="destroyed",
                world_id="destroy_world",
                require_runtime=True,
            )
            self.assert_result(destroyed, ok=True)
            destroyed_entity = destroyed["payload"]["entity_id"]
            destroyed_marker = marker_root / f"{destroyed_entity}.destroyed"
            clear_destroyed = client.runtime.sessions.clear_entity(
                entity_id=destroyed_entity,
                destroy_actor=True,
            )
            self.assert_result(clear_destroyed, ok=True)
            destroyed_bridge = clear_destroyed["payload"]["bridge"]
            self.assertEqual(1, destroyed_bridge["removed_sessions"])
            self.assertEqual(1, destroyed_bridge["matched_nodes"])
            self.assertEqual(1, destroyed_bridge["destroy_queued_nodes"])
            self.assertTrue(wait_for(destroyed_marker.is_file))

            departed = client.runtime.sessions.join(
                participant_id="departed",
                world_id="departed_world",
                require_runtime=True,
            )
            self.assert_result(departed, ok=True)
            departed_controller = departed["payload"]["controller_id"]
            accepted = client.runtime.sessions.apply_input(
                departed_controller,
                move_x=0.5,
                seq=1,
                require_runtime=True,
            )
            self.assert_result(accepted, ok=True)
            accepted_input = dict(accepted["payload"]["input"])
            left = client.runtime.sessions.leave(controller_id=departed_controller)
            self.assert_result(left, ok=True)
            self.assertFalse(left["payload"]["active"])
            self.assertFalse(left["payload"]["online"])
            self.assertEqual(0, client.runtime.sessions.probe()["sessions"])

            after_leave = client.runtime.sessions.apply_input(
                departed_controller,
                move_x=1.0,
                seq=2,
                require_runtime=True,
            )
            heartbeat = client.runtime.sessions.heartbeat(departed_controller)
            self.assert_result(after_leave, ok=False)
            self.assert_result(heartbeat, ok=False)
            departed_snapshot = client.runtime.sessions.snapshot()["payload"][
                "sessions"
            ][0]
            self.assertFalse(departed_snapshot["active"])
            self.assertFalse(departed_snapshot["online"])
            self.assertEqual(accepted_input, departed_snapshot["last_input"])
            native_after_leave = client.runtime.sessions._send(
                {
                    "operation": "session.input",
                    "controller_id": departed_controller,
                    "input": {"move_x": 1.0, "seq": 2},
                }
            )
            self.assertFalse(native_after_leave["ok"])
            self.assertIn("Unknown controller_id", native_after_leave["error"])
            departed_rejoined = client.runtime.sessions.join(
                participant_id="departed",
                world_id="ignored_after_leave",
                require_runtime=True,
            )
            self.assert_result(departed_rejoined, ok=True)
            self.assertEqual(
                departed["payload"]["entity_id"],
                departed_rejoined["payload"]["entity_id"],
            )
            self.assertEqual(
                0, departed_rejoined["payload"]["bridge"]["replaced_controllers"]
            )
            self.assertTrue(departed_rejoined["payload"]["bridge"]["entity_reused"])
            departed_rejoined_controller = departed_rejoined["payload"]["controller_id"]
            self.assertTrue(
                (marker_root / f"{departed_rejoined_controller}.reconnected").is_file()
            )
            self.assert_result(
                client.runtime.sessions.apply_input(
                    departed_rejoined_controller,
                    seq=3,
                    require_runtime=True,
                ),
                ok=True,
            )
            departed_cleanup = client.runtime.sessions.clear_entity(
                controller_id=departed_controller,
            )
            self.assert_result(departed_cleanup, ok=True)
            self.assertEqual(2, departed_cleanup["payload"]["removed_sessions"])
            self.assertEqual(
                1, departed_cleanup["payload"]["bridge"]["removed_sessions"]
            )
            self.assertEqual(1, departed_cleanup["payload"]["bridge"]["matched_nodes"])
            self.assertEqual(0, client.runtime.sessions.snapshot()["payload"]["count"])

            first_reconnect = client.runtime.sessions.join(
                participant_id="reconnected",
                world_id="reconnected_world",
                require_runtime=True,
            )
            second_reconnect = client.runtime.sessions.join(
                participant_id="reconnected",
                world_id="ignored_world",
                require_runtime=True,
            )
            third_reconnect = client.runtime.sessions.join(
                participant_id="reconnected",
                world_id="also_ignored_world",
                require_runtime=True,
            )
            self.assert_result(first_reconnect, ok=True)
            self.assert_result(second_reconnect, ok=True)
            self.assert_result(third_reconnect, ok=True)
            first_reconnect_controller = first_reconnect["payload"]["controller_id"]
            second_reconnect_controller = second_reconnect["payload"]["controller_id"]
            third_reconnect_controller = third_reconnect["payload"]["controller_id"]
            self.assertNotEqual(first_reconnect_controller, second_reconnect_controller)
            self.assertNotEqual(second_reconnect_controller, third_reconnect_controller)
            self.assertEqual(
                first_reconnect["payload"]["entity_id"],
                second_reconnect["payload"]["entity_id"],
            )
            self.assertEqual(
                first_reconnect["payload"]["entity_id"],
                third_reconnect["payload"]["entity_id"],
            )
            self.assertEqual(
                1, second_reconnect["payload"]["bridge"]["replaced_controllers"]
            )
            self.assertEqual(
                1, third_reconnect["payload"]["bridge"]["replaced_controllers"]
            )
            self.assertTrue(second_reconnect["payload"]["bridge"]["entity_reused"])
            self.assertTrue(third_reconnect["payload"]["bridge"]["entity_reused"])
            self.assertTrue(
                (marker_root / f"{second_reconnect_controller}.reconnected").is_file()
            )
            self.assertTrue(
                (marker_root / f"{third_reconnect_controller}.reconnected").is_file()
            )
            self.assertFalse(
                (marker_root / f"{first_reconnect_controller}.left").exists()
            )
            self.assertFalse(
                (marker_root / f"{second_reconnect_controller}.left").exists()
            )

            for stale_controller in (
                first_reconnect_controller,
                second_reconnect_controller,
            ):
                old_reconnect_input = client.runtime.sessions.apply_input(
                    stale_controller,
                    seq=1,
                    require_runtime=True,
                )
                self.assert_result(old_reconnect_input, ok=False)
                native_old_reconnect_input = client.runtime.sessions._send(
                    {
                        "operation": "session.input",
                        "controller_id": stale_controller,
                        "input": {"seq": 1},
                    }
                )
                self.assertFalse(native_old_reconnect_input["ok"])
            current_reconnect_input = client.runtime.sessions.apply_input(
                third_reconnect_controller,
                seq=2,
                require_runtime=True,
            )
            self.assert_result(current_reconnect_input, ok=True)
            reconnect_snapshot = client.runtime.sessions.snapshot()["payload"]
            self.assertEqual(3, reconnect_snapshot["count"])
            self.assertEqual(1, reconnect_snapshot["active_count"])
            self.assertEqual(
                reconnect_snapshot["active_count"],
                client.runtime.sessions.probe()["sessions"],
            )

            left_reconnect = client.runtime.sessions.leave(participant_id="reconnected")
            self.assert_result(left_reconnect, ok=True)
            self.assertEqual(
                third_reconnect_controller,
                left_reconnect["payload"]["controller_id"],
            )
            self.assertTrue(
                (marker_root / f"{third_reconnect_controller}.left").is_file()
            )
            new_reconnect_input = client.runtime.sessions.apply_input(
                third_reconnect_controller,
                seq=3,
                require_runtime=True,
            )
            self.assert_result(new_reconnect_input, ok=False)
            native_new_reconnect_input = client.runtime.sessions._send(
                {
                    "operation": "session.input",
                    "controller_id": third_reconnect_controller,
                    "input": {"seq": 3},
                }
            )
            self.assertFalse(native_new_reconnect_input["ok"])
            reconnect_snapshot = client.runtime.sessions.snapshot()["payload"]
            self.assertEqual(0, reconnect_snapshot["active_count"])
            self.assertEqual(
                reconnect_snapshot["active_count"],
                client.runtime.sessions.probe()["sessions"],
            )
            reconnect_cleanup = client.runtime.sessions.clear_entity(
                participant_id="reconnected",
            )
            self.assert_result(reconnect_cleanup, ok=True)
            self.assertEqual(3, reconnect_cleanup["payload"]["removed_sessions"])
            reconnect_bridge = reconnect_cleanup["payload"]["bridge"]
            self.assertEqual(0, reconnect_bridge["removed_sessions"])
            self.assertEqual(1, reconnect_bridge["matched_nodes"])
            self.assertEqual(1, reconnect_bridge["destroy_queued_nodes"])
            reconnect_entity = first_reconnect["payload"]["entity_id"]
            self.assertTrue(
                wait_for((marker_root / f"{reconnect_entity}.destroyed").is_file)
            )
            self.assertEqual(0, client.runtime.sessions.snapshot()["payload"]["count"])

            for participant_id, world_id in (
                ("reset-a", "reset_world"),
                ("reset-b", "reset_world"),
                ("other", "other_world"),
                ("default", ""),
            ):
                joined = client.runtime.sessions.join(
                    participant_id=participant_id,
                    world_id=world_id,
                    require_runtime=True,
                )
                self.assert_result(joined, ok=True)
            self.assertEqual(4, client.runtime.sessions.probe()["sessions"])

            reset = client.runtime.sessions.reset_world(world_id="reset_world")
            self.assert_result(reset, ok=True)
            self.assertEqual(2, reset["payload"]["removed_sessions"])
            self.assertEqual(2, reset["payload"]["bridge"]["removed_sessions"])
            self.assertEqual(2, client.runtime.sessions.probe()["sessions"])
            self.assertEqual(2, client.runtime.sessions.snapshot()["payload"]["count"])
            self.assertEqual(
                1,
                client.runtime.sessions.snapshot(world_id="other_world")["payload"][
                    "count"
                ],
            )

            repeated = client.runtime.sessions.reset_world(world_id="reset_world")
            self.assert_result(repeated, ok=True)
            self.assertEqual(0, repeated["payload"]["removed_sessions"])
            self.assertEqual(0, repeated["payload"]["bridge"]["removed_sessions"])

            reset_default = client.runtime.sessions.reset_world()
            self.assert_result(reset_default, ok=True)
            self.assertEqual("world_001", reset_default["payload"]["world_id"])
            self.assertEqual(1, reset_default["payload"]["removed_sessions"])
            self.assertEqual(1, reset_default["payload"]["bridge"]["removed_sessions"])
            self.assertEqual(1, client.runtime.sessions.probe()["sessions"])
            self.assertEqual(
                1,
                client.runtime.sessions.snapshot(world_id="other_world")["payload"][
                    "count"
                ],
            )
        finally:
            stopped = client.runtime.stop_game(process_id)
            self.assert_result(stopped, ok=True)

    @unittest.skipUnless(os.name == "posix", "symlink boundary probe requires POSIX")
    def test_runtime_rejects_scene_symlinks_outside_project(self) -> None:
        outside = self.root / "outside-scenes"
        outside.mkdir()
        outside_scene = outside / "outside.tscn"
        outside_scene.write_text(
            '[gd_scene format=3]\n\n[node name="Outside" type="Node3D"]\n',
            encoding="utf-8",
        )
        (self.project / "linked-directory").symlink_to(
            outside, target_is_directory=True
        )
        (self.project / "linked-file.tscn").symlink_to(outside_scene)

        for launch in (
            self.client.runtime.launch_editor,
            self.client.runtime.launch_game,
        ):
            for scene_path in (
                "res://linked-directory/outside.tscn",
                "res://linked-file.tscn",
            ):
                with self.subTest(launch=launch.__name__, scene_path=scene_path):
                    result = launch(scene_path=scene_path, dry_run=True)
                    self.assert_result(result, ok=False)
                    self.assertIn("escaped", " ".join(result["errors"]))

    @unittest.skipUnless(
        os.name == "posix" and Path("/proc").is_dir(),
        "process-group lifecycle probe requires POSIX /proc",
    )
    def test_runtime_stops_editor_game_and_player_process_groups(self) -> None:
        process_stub = self.root / "godot-process-tree-stub"
        process_stub.write_text(
            "#!/bin/sh\n"
            "sleep 60 &\n"
            'printf \'%s\' "$!" > "$A3GAME_GODOT_TEST_CHILD_PID_FILE"\n'
            "wait\n",
            encoding="utf-8",
        )
        process_stub.chmod(0o755)
        process_client = GodotClient(
            project_path=self.project,
            godot_executable=process_stub,
            editor_timeout=2,
            import_timeout=2,
        )

        def process_state(process_id: int) -> str:
            try:
                tail = (
                    (Path("/proc") / str(process_id) / "stat")
                    .read_text(encoding="utf-8")
                    .rsplit(")", 1)[1]
                )
            except (IndexError, OSError):
                return ""
            fields = tail.strip().split()
            return fields[0] if fields else ""

        cases = (
            (
                "editor",
                lambda: process_client.runtime.launch_editor(),
                process_client.runtime.stop_editor,
            ),
            (
                "game",
                lambda: process_client.runtime.launch_game(headless=True),
                process_client.runtime.stop_game,
            ),
            (
                "player",
                lambda: process_client.runtime.launch_player(process_stub),
                process_client.runtime.stop_player,
            ),
        )
        for name, launch, stop in cases:
            with self.subTest(process_type=name):
                pid_file = self.root / f"{name}-child.pid"
                with mock.patch.dict(
                    os.environ,
                    {"A3GAME_GODOT_TEST_CHILD_PID_FILE": str(pid_file)},
                    clear=False,
                ):
                    launched = launch()
                self.assert_result(launched, ok=True)
                parent_pid = int(launched["payload"]["process_id"])
                child_pid = 0
                try:
                    deadline = time.monotonic() + 2.0
                    while not pid_file.is_file() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(pid_file.is_file())
                    child_pid = int(pid_file.read_text(encoding="utf-8"))
                    self.assertNotIn(process_state(child_pid), {"", "Z", "X", "x"})

                    stopped = stop(parent_pid)
                    self.assert_result(stopped, ok=True)
                    deadline = time.monotonic() + 2.0
                    while (
                        process_state(child_pid) not in {"", "Z", "X", "x"}
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.01)
                    self.assertIn(process_state(child_pid), {"", "Z", "X", "x"})
                finally:
                    stop(parent_pid)
                    if child_pid and process_state(child_pid) not in {
                        "",
                        "Z",
                        "X",
                        "x",
                    }:
                        try:
                            os.kill(child_pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass

    @unittest.skipUnless(
        os.name == "posix" and Path("/proc").is_dir(),
        "process-group timeout probe requires POSIX /proc",
    )
    def test_bounded_godot_command_timeout_stops_child_processes(self) -> None:
        process_stub = self.root / "godot-timeout-process-tree-stub"
        process_stub.write_text(
            "#!/bin/sh\n"
            "sleep 60 &\n"
            'printf \'%s\' "$!" > "$A3GAME_GODOT_TEST_CHILD_PID_FILE"\n'
            "wait\n",
            encoding="utf-8",
        )
        process_stub.chmod(0o755)
        process_client = GodotClient(
            project_path=self.project,
            godot_executable=process_stub,
            editor_timeout=2,
            import_timeout=2,
        )
        (self.project / "export_presets.cfg").write_text(
            '[preset.0]\n\nname="Linux"\nplatform="Linux/X11"\n',
            encoding="utf-8",
        )
        pid_file = self.root / "timeout-child.pid"

        def process_state(process_id: int) -> str:
            try:
                tail = (
                    (Path("/proc") / str(process_id) / "stat")
                    .read_text(encoding="utf-8")
                    .rsplit(")", 1)[1]
                )
            except (IndexError, OSError):
                return ""
            fields = tail.strip().split()
            return fields[0] if fields else ""

        child_pid = 0
        try:
            with mock.patch.dict(
                os.environ,
                {"A3GAME_GODOT_TEST_CHILD_PID_FILE": str(pid_file)},
                clear=False,
            ):
                result = process_client.build.project(
                    preset="Linux",
                    output_path="builds/timeout.x86_64",
                    timeout=0.2,
                )
            self.assert_result(result, ok=False)
            self.assertIn("TimeoutExpired", " ".join(result["errors"]))
            self.assertTrue(pid_file.is_file())
            child_pid = int(pid_file.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 2.0
            while (
                process_state(child_pid) not in {"", "Z", "X", "x"}
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)
            self.assertIn(process_state(child_pid), {"", "Z", "X", "x"})
        finally:
            if child_pid and process_state(child_pid) not in {"", "Z", "X", "x"}:
                try:
                    os.kill(child_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_invalid_source_arguments_return_structured_failures(self) -> None:
        class MalformedSource(dict):
            def keys(self):
                raise RuntimeError("broken mapping")

            def __iter__(self):
                raise RuntimeError("broken mapping")

            def __str__(self) -> str:
                raise RuntimeError("unprintable source")

        unsafe_path = self.root / "not-a-component"
        json_unsafe_source = {
            "game_id": unsafe_path,
            "run_id": "run",
            "task_kind": "3d_object",
            "task_id": "task",
            "nested": {
                "paths": [unsafe_path, (unsafe_path,)],
                "not_finite": float("nan"),
            },
        }
        for source in (
            None,
            "not-a-source-descriptor",
            MalformedSource(),
            json_unsafe_source,
        ):
            with self.subTest(source_type=type(source).__name__):
                results = (
                    self.client.assets.import_asset(source, "prop"),
                    self.client.assets.validate(source, "prop"),
                    self.client.assets.resolve_source(source, asset_type="prop"),
                    self.client.plugin.install(source),
                    self.client.bindings.bind_pbr_material(
                        asset_id="invalid",
                        source=source,
                        mesh_assets=["missing"],
                    ),
                    self.client.world.build(source),
                )
                for result in results:
                    self.assert_result(result, ok=False)
                    self.assertIsInstance(result["payload"].get("source"), dict)
                    json.loads(json.dumps(result, allow_nan=False))
                if source is json_unsafe_source:
                    normalized = results[0]["payload"]["source"]
                    self.assertEqual(str(unsafe_path), normalized["game_id"])
                    self.assertEqual(
                        [str(unsafe_path), [str(unsafe_path)]],
                        normalized["nested"]["paths"],
                    )
                    self.assertEqual("nan", normalized["nested"]["not_finite"])

    def test_runtime_process_launch_group_settings_are_cross_platform(self) -> None:
        from engine_adapters.godot._internal import transport as transport_module

        with mock.patch.object(transport_module.os, "name", "posix"):
            self.assertEqual(
                {"start_new_session": True},
                transport_module.managed_process_kwargs(),
            )
        with mock.patch.object(transport_module.os, "name", "nt"):
            windows = transport_module.managed_process_kwargs()
        self.assertEqual(
            int(
                getattr(
                    transport_module.subprocess,
                    "CREATE_NEW_PROCESS_GROUP",
                    0x00000200,
                )
            ),
            windows["creationflags"],
        )

    def test_world_requires_a_registered_instantiable_scene(self) -> None:
        project_file_draft = self.client.world.create_draft(
            {
                "draft_id": "project-file",
                "world_id": "invalid",
                "scene_path": "res://project.godot",
            }
        )
        self.assert_result(project_file_draft, ok=False)

        unregistered_scene = self.project / "unregistered.tscn"
        unregistered_scene.write_text(
            '[gd_scene format=3]\n\n[node name="Root" type="Node3D"]\n',
            encoding="utf-8",
        )
        unregistered = self.client.world.create_draft(
            {
                "draft_id": "unregistered",
                "world_id": "invalid",
                "scene_path": "res://unregistered.tscn",
            }
        )
        self.assert_result(unregistered, ok=False)

        material_file = self.project / "material.tres"
        material_file.write_text(
            '[gd_resource type="StandardMaterial3D" format=3]\n',
            encoding="utf-8",
        )
        material = self.client.assets._register_resource(
            resource_path="res://material.tres",
            asset_type="material",
            asset_id="material",
            backend_class="Resource",
        )
        non_scene = self.client.world.create_draft(
            {
                "draft_id": "non-scene",
                "world_id": "invalid",
                "scene_artifact_id": material.artifact_id,
            }
        )
        self.assert_result(non_scene, ok=False)

        wrong_class_file = self.project / "wrong-class.tscn"
        wrong_class_file.write_text(
            '[gd_scene format=3]\n\n[node name="Root" type="Node3D"]\n',
            encoding="utf-8",
        )
        wrong_class = self.client.assets._register_resource(
            resource_path="res://wrong-class.tscn",
            asset_type="scene",
            asset_id="wrong-class",
            backend_class="Resource",
        )
        wrong_class_draft = self.client.world.create_draft(
            {
                "draft_id": "wrong-class",
                "world_id": "invalid",
                "scene_artifact_id": wrong_class.artifact_id,
            }
        )
        self.assert_result(wrong_class_draft, ok=False)

        first_source, _ = self.make_artifact(
            task_id="world_first",
            suffix=".tscn",
            content=b'[gd_scene format=3]\n\n[node name="First" type="Node3D"]\n',
            task_kind="3d_scene",
            artifact_key="scene_path",
        )
        second_source, _ = self.make_artifact(
            task_id="world_second",
            suffix=".tscn",
            content=b'[gd_scene format=3]\n\n[node name="Second" type="Node3D"]\n',
            task_kind="3d_scene",
            artifact_key="scene_path",
        )
        first = self.client.assets.import_scene(
            first_source,
            options={"name": "first.tscn"},
        )
        second = self.client.assets.import_scene(
            second_source,
            options={"name": "second.tscn"},
        )
        self.assert_result(first, ok=True)
        self.assert_result(second, ok=True)
        first_artifact = first["artifacts"][0]
        second_artifact = second["artifacts"][0]

        with mock.patch.object(
            self.client.world._reflection,
            "inspect_artifact",
            return_value={
                "ok": True,
                "errors": [],
                "payload": {
                    "inspection": {
                        "resource_class": "Resource",
                        "instantiable": False,
                    }
                },
            },
        ):
            native_type_mismatch = self.client.world.create_draft(
                {
                    "draft_id": "native-type-mismatch",
                    "world_id": "invalid",
                    "scene_artifact_id": first_artifact["artifact_id"],
                }
            )
        self.assert_result(native_type_mismatch, ok=False)

        mismatch = self.client.world.create_draft(
            {
                "draft_id": "mismatch",
                "world_id": "invalid",
                "scene_artifact_id": first_artifact["artifact_id"],
                "scene_path": second_artifact["backend_path"],
            }
        )
        self.assert_result(mismatch, ok=False)

        valid = self.client.world.create_draft(
            {
                "draft_id": "valid-then-tampered",
                "world_id": "valid",
                "scene_path": first_artifact["backend_path"],
            }
        )
        self.assert_result(valid, ok=True)
        self.assertEqual(
            first_artifact["artifact_id"],
            valid["payload"]["scene_artifact_id"],
        )
        draft_path = Path(valid["payload"]["path"])
        draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft["scene_path"] = second_artifact["backend_path"]
        draft_path.write_text(json.dumps(draft), encoding="utf-8")
        self.assert_result(
            self.client.world.validate_draft("valid-then-tampered"),
            ok=False,
        )
        self.assert_result(
            self.client.world.publish_draft("valid-then-tampered"),
            ok=False,
        )
        draft["scene_artifact_id"] = ""
        draft["scene_path"] = ""
        draft_path.write_text(json.dumps(draft), encoding="utf-8")
        self.assert_result(
            self.client.world.validate_draft("valid-then-tampered"),
            ok=False,
        )

    def test_world_records_fail_closed_on_schema_shape_and_identity_damage(
        self,
    ) -> None:
        scene = self.client.assets._register_resource(
            resource_path="res://main.tscn",
            asset_type="scene",
            asset_id="strict-world-scene",
            backend_class="PackedScene",
        )
        created = self.client.world.create_draft(
            {
                "draft_id": "strict-draft",
                "world_id": "strict-world",
                "project_id": "strict-project",
                "scene_artifact_id": scene.artifact_id,
                "artifacts": [scene.artifact_id],
                "metadata": {"owner": "test"},
            }
        )
        self.assert_result(created, ok=True)
        self.assert_result(
            self.client.world.validate_draft(" strict-draft "),
            ok=False,
        )
        duplicate_artifacts = self.client.world.create_draft(
            {
                "draft_id": "duplicate-artifacts",
                "scene_artifact_id": scene.artifact_id,
                "artifacts": [scene.artifact_id, scene.artifact_id],
            }
        )
        self.assert_result(duplicate_artifacts, ok=False)
        self.assertFalse(self.client.world._draft_path("duplicate-artifacts").exists())
        draft_path = Path(created["payload"]["path"])
        valid_draft = json.loads(draft_path.read_text(encoding="utf-8"))
        draft_cases = {
            "unknown schema": {
                **valid_draft,
                "schema_version": "attacker.invalid.v999",
            },
            "illegal status": {**valid_draft, "status": "already_deleted"},
            "wrong identity": {**valid_draft, "draft_id": "different-id"},
            "missing world": {
                key: value for key, value in valid_draft.items() if key != "world_id"
            },
            "empty scene identity": {**valid_draft, "scene_artifact_id": ""},
            "wrong artifacts shape": {**valid_draft, "artifacts": {}},
            "wrong metadata shape": {**valid_draft, "metadata": []},
            "wrong timestamp type": {**valid_draft, "created_at": "now"},
        }
        for name, damaged in draft_cases.items():
            with self.subTest(record="draft", damage=name):
                draft_path.write_text(json.dumps(damaged), encoding="utf-8")
                validated = self.client.world.validate_draft("strict-draft")
                published = self.client.world.publish_draft("strict-draft")
                self.assert_result(validated, ok=False)
                self.assert_result(published, ok=False)
                self.assertTrue(validated["errors"])
                self.assertTrue(published["errors"])

        for damaged_text in ("{not valid json", '{"draft_id":"a","draft_id":"b"}'):
            with self.subTest(record="draft", damage=damaged_text):
                draft_path.write_text(damaged_text, encoding="utf-8")
                self.assert_result(
                    self.client.world.validate_draft("strict-draft"),
                    ok=False,
                )
                self.assert_result(
                    self.client.world.publish_draft("strict-draft"),
                    ok=False,
                )

        draft_path.write_text(json.dumps(valid_draft), encoding="utf-8")
        published = self.client.world.publish_draft("strict-draft")
        self.assert_result(published, ok=True)
        package_path = Path(published["payload"]["path"])
        valid_package = json.loads(package_path.read_text(encoding="utf-8"))
        package_cases = {
            "empty object": {},
            "unknown schema": {
                **valid_package,
                "schema_version": "attacker.invalid.v999",
            },
            "illegal status": {**valid_package, "status": "draft"},
            "wrong identity": {
                **valid_package,
                "package_id": "different-package",
            },
            "missing project": {
                key: value
                for key, value in valid_package.items()
                if key != "project_id"
            },
            "empty world": {**valid_package, "world_id": ""},
            "empty scene path": {**valid_package, "scene_path": ""},
            "wrong artifacts shape": {**valid_package, "artifacts": {}},
            "wrong metadata shape": {**valid_package, "metadata": []},
            "wrong timestamp order": {
                **valid_package,
                "published_at": valid_package["created_at"] - 1,
            },
        }
        for name, damaged in package_cases.items():
            with self.subTest(record="package", damage=name):
                package_path.write_text(json.dumps(damaged), encoding="utf-8")
                listed = self.client.world.list_packages()
                self.assert_result(listed, ok=False)
                self.assertEqual([], listed["artifacts"])
                self.assertTrue(listed["errors"])

        for damaged_text in (
            "{not valid json",
            '{"package_id":"a","package_id":"b"}',
            json.dumps({**valid_package, "created_at": float("nan")}),
        ):
            with self.subTest(record="package", damage=damaged_text):
                package_path.write_text(damaged_text, encoding="utf-8")
                self.assert_result(self.client.world.list_packages(), ok=False)

        package_path.write_text(json.dumps(valid_package), encoding="utf-8")
        wrong_filename = package_path.with_name("wrong-filename.json")
        wrong_filename.write_text(json.dumps(valid_package), encoding="utf-8")
        try:
            self.assert_result(self.client.world.list_packages(), ok=False)
        finally:
            wrong_filename.unlink()
        listed = self.client.world.list_packages(project_id="strict-project")
        self.assert_result(listed, ok=True)
        self.assertEqual(1, listed["payload"]["count"])

    def test_world_rejects_non_json_metadata_without_writing_a_draft(self) -> None:
        scene_file = self.project / "json-boundary.tscn"
        scene_file.write_text(
            '[gd_scene format=3]\n\n[node name="Root" type="Node3D"]\n',
            encoding="utf-8",
        )
        scene = self.client.assets._register_resource(
            resource_path="res://json-boundary.tscn",
            asset_type="scene",
            asset_id="json-boundary",
            backend_class="PackedScene",
        )
        draft_path = (
            self.client._config.world_registry_root
            / "drafts"
            / "non-json-metadata.json"
        )
        result = self.client.world.create_draft(
            {
                "draft_id": "non-json-metadata",
                "scene_artifact_id": scene.artifact_id,
                "metadata": {"path": Path("/tmp/not-json")},
            }
        )
        self.assert_result(result, ok=False)
        self.assertIn("not JSON serializable", " ".join(result["errors"]))
        self.assertFalse(draft_path.exists())
        self.assertEqual([], list(draft_path.parent.glob(".non-json-metadata.*.json")))

    @unittest.skipUnless(os.name == "posix", "symlink boundary probe requires POSIX")
    def test_world_rejects_linked_registry_directories_without_external_writes(
        self,
    ) -> None:
        for linked_component in ("worlds", "drafts"):
            with self.subTest(linked_component=linked_component):
                data_root = self.root / f"state-{linked_component}"
                outside = self.root / f"outside-{linked_component}"
                outside.mkdir()
                victim = outside / "victim.json"
                victim.write_bytes(b"USER OWNED BYTES\n")
                with mock.patch.dict(
                    os.environ,
                    {
                        "A3GAME_GODOT_DATA_ROOT": str(data_root),
                        "A3GAME_GODOT_ARTIFACT_REGISTRY": "",
                        "A3GAME_DATA_ROOT": "",
                        "A3GAME_ARTIFACT_REGISTRY": "",
                        "A3GAME_GODOT_WORLD_REGISTRY_ROOT": "",
                        "A3GAME_WORLD_REGISTRY_ROOT": "",
                    },
                    clear=False,
                ):
                    client = GodotClient(
                        project_path=self.project,
                        godot_executable=self.fake_godot,
                    )
                    scene = client.assets._register_resource(
                        resource_path="res://main.tscn",
                        asset_type="scene",
                        asset_id=f"scene-{linked_component}",
                        backend_class="PackedScene",
                    )
                    world_root = data_root / "worlds"
                    if linked_component == "worlds":
                        world_root.symlink_to(outside, target_is_directory=True)
                    else:
                        world_root.mkdir()
                        (world_root / "drafts").symlink_to(
                            outside,
                            target_is_directory=True,
                        )

                    result = client.world.create_draft(
                        {
                            "draft_id": "victim",
                            "world_id": "world",
                            "scene_artifact_id": scene.artifact_id,
                        }
                    )

                self.assert_result(result, ok=False)
                self.assertIn("symlink", " ".join(result["errors"]).lower())
                self.assertEqual(b"USER OWNED BYTES\n", victim.read_bytes())
                self.assertEqual([victim], list(outside.iterdir()))

        data_root = self.root / "state-packages"
        outside = self.root / "outside-packages"
        outside.mkdir()
        victim = outside / "victim.json"
        victim.write_bytes(b"USER OWNED PACKAGE\n")
        with mock.patch.dict(
            os.environ,
            {
                "A3GAME_GODOT_DATA_ROOT": str(data_root),
                "A3GAME_GODOT_ARTIFACT_REGISTRY": "",
                "A3GAME_DATA_ROOT": "",
                "A3GAME_ARTIFACT_REGISTRY": "",
                "A3GAME_GODOT_WORLD_REGISTRY_ROOT": "",
                "A3GAME_WORLD_REGISTRY_ROOT": "",
            },
            clear=False,
        ):
            client = GodotClient(
                project_path=self.project,
                godot_executable=self.fake_godot,
            )
            scene = client.assets._register_resource(
                resource_path="res://main.tscn",
                asset_type="scene",
                asset_id="scene-packages",
                backend_class="PackedScene",
            )
            draft = client.world.create_draft(
                {
                    "draft_id": "linked-package",
                    "world_id": "world",
                    "scene_artifact_id": scene.artifact_id,
                }
            )
            self.assert_result(draft, ok=True)
            packages = data_root / "worlds" / "packages"
            packages.symlink_to(outside, target_is_directory=True)

            result = client.world.publish_draft("linked-package")

        self.assert_result(result, ok=False)
        self.assertIn("symlink", " ".join(result["errors"]).lower())
        self.assertEqual(b"USER OWNED PACKAGE\n", victim.read_bytes())
        self.assertEqual([victim], list(outside.iterdir()))

    def test_configuration_and_failure_edges(self) -> None:
        with self.assertRaises(ValueError):
            GodotClient(
                project_path=self.project,
                godot_executable=self.fake_godot,
                api_version="v2",
            )
        missing = GodotClient(
            project_path=self.project,
            godot_executable=self.root / "missing-godot",
        )
        self.assert_result(missing.project.validate(), ok=False)
        missing_preset = self.client.build.project(
            preset="Missing", output_path="builds/missing"
        )
        self.assert_result(missing_preset, ok=False)
        with mock.patch.dict(
            os.environ,
            {
                "A3GAME_GODOT_PROJECT": "",
                "A3GAME_GODOT_EXECUTABLE": "",
                "A3GAME_GODOT": "",
                "AAAGF_GODOT_PROJECT": str(self.project),
                "AAAGF_GODOT": str(self.fake_godot),
            },
            clear=False,
        ):
            legacy_client = GodotClient()
        self.assertEqual(self.project.resolve(), legacy_client._config.project_dir)
        self.assertEqual(
            self.fake_godot.resolve(), legacy_client._config.godot_executable
        )

    def test_source_identity_cannot_escape_output_root(self) -> None:
        base = {
            "game_id": "godot_adapter_test",
            "run_id": "run_001",
            "task_kind": "3d_object",
            "task_id": "crate",
            "artifact_key": "glb_path",
        }
        for key, value in (
            ("game_id", str(self.root / "outside")),
            ("game_id", "../outside"),
            ("run_id", "../outside"),
            ("task_id", "../outside"),
        ):
            descriptor = {**base, key: value}
            result = self.client.assets.resolve_source(descriptor, asset_type="prop")
            self.assert_result(result, ok=False)

        outside_task = (
            self.root / "outside" / "run_001" / "assets" / "3d_object" / "crate"
        )
        outside_task.mkdir(parents=True)
        outside_artifact = outside_task / "artifact.glb"
        outside_artifact.write_bytes(b"outside")
        (outside_task / "meta.json").write_text(
            json.dumps({**base, "glb_path": str(outside_artifact)}),
            encoding="utf-8",
        )
        paths.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        (paths.OUTPUT_ROOT / "linked_game").symlink_to(
            self.root / "outside", target_is_directory=True
        )
        linked = self.client.assets.resolve_source(
            {**base, "game_id": "linked_game"}, asset_type="prop"
        )
        self.assert_result(linked, ok=False)

    def test_windows_wrappers_quote_cmd_sensitive_assignments(self) -> None:
        wrapper_root = Path("scripts/engine_install/godot")
        for name in ("create_project.cmd", "import_asset.cmd", "run.cmd"):
            with self.subTest(wrapper=name):
                text = (wrapper_root / name).read_text(encoding="utf-8")
                self.assertIn("if not defined A3GAME_GODOT_PROJECT", text)
                self.assertIn('set "SCRIPT_DIR=%~dp0"', text)
                self.assertIn('set "REPO_ROOT=%SCRIPT_DIR%..\\..\\.."', text)
                self.assertIn('set "PYTHONPATH=%REPO_ROOT%;%PYTHONPATH%"', text)
                self.assertIn('set "PYTHON_BIN=python"', text)
                self.assertIn(
                    'if defined A3GAME_PYTHON set "PYTHON_BIN=%A3GAME_PYTHON%"',
                    text,
                )
                self.assertNotIn("set SCRIPT_DIR=", text)
                self.assertNotIn("set REPO_ROOT=", text)
                self.assertNotIn("set PYTHONPATH=", text)

    def assert_compatibility_registry_parent_link_rejected(
        self,
        *,
        use_linked_data_root: bool,
    ) -> None:
        """Exercise the public compatibility CLI against one linked parent."""

        suffix = "data-root" if use_linked_data_root else "registry"
        document = json.dumps(
            {
                "asset": {"version": "2.0", "generator": "registry-link-test"},
                "scenes": [{"nodes": []}],
                "scene": 0,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        document += b" " * ((4 - len(document) % 4) % 4)
        source = self.root / f"linked-{suffix}.glb"
        source.write_bytes(
            struct.pack("<4sII", b"glTF", 2, 20 + len(document))
            + struct.pack("<I4s", len(document), b"JSON")
            + document
        )

        outside = self.root / f"outside-{suffix}"
        outside.mkdir()
        victim = outside / "artifacts.json"
        victim.write_text(
            json.dumps(
                {
                    "schema_version": "gamefactory3a.godot.artifacts.v1",
                    "artifacts": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        victim.chmod(0o644)
        linked_parent = self.root / f"linked-{suffix}-parent"
        linked_parent.symlink_to(outside, target_is_directory=True)
        safe_data_root = self.root / f"safe-{suffix}-data"
        marker = self.root / f"godot-started-{suffix}.json"
        environment = {
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "A3GAME_FAKE_GODOT_INVOCATION_MARKER": str(marker),
            "A3GAME_GODOT_DATA_ROOT": (
                str(linked_parent) if use_linked_data_root else str(safe_data_root)
            ),
            "A3GAME_GODOT_ARTIFACT_REGISTRY": (
                "" if use_linked_data_root else str(linked_parent / "artifacts.json")
            ),
            "A3GAME_DATA_ROOT": "",
            "A3GAME_ARTIFACT_REGISTRY": "",
        }
        before_bytes = victim.read_bytes()
        before_stat = victim.stat()
        before_listing = sorted(item.name for item in outside.iterdir())

        completed = subprocess.run(
            [
                sys.executable,
                "scripts/import_generated_asset.py",
                "--engine",
                "godot",
                "--src",
                str(source),
                "--godot",
                str(self.fake_godot),
                "--godot-project",
                str(self.project),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=environment,
        )

        self.assertEqual(1, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("symlink", (completed.stdout + completed.stderr).lower())
        self.assertFalse(marker.exists(), "Godot must not start for an unsafe registry")
        self.assertEqual(before_bytes, victim.read_bytes())
        after_stat = victim.stat()
        self.assertEqual(
            stat.S_IMODE(before_stat.st_mode), stat.S_IMODE(after_stat.st_mode)
        )
        self.assertEqual(before_stat.st_ino, after_stat.st_ino)
        self.assertEqual(
            before_listing, sorted(item.name for item in outside.iterdir())
        )
        self.assertFalse(
            (self.project / "assets" / "imported" / "props" / source.name).exists()
        )
        self.assertFalse(safe_data_root.exists())
        report = json.loads(
            (self.root / f"linked-{suffix}_godot_import.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("version_process", report)
        self.assertNotIn("artifact_id", report)

    @unittest.skipUnless(os.name == "posix", "symlink boundary probe requires POSIX")
    def test_compatibility_cli_rejects_linked_godot_data_root(self) -> None:
        self.assert_compatibility_registry_parent_link_rejected(
            use_linked_data_root=True
        )

    @unittest.skipUnless(os.name == "posix", "symlink boundary probe requires POSIX")
    def test_compatibility_cli_rejects_linked_registry_parent(self) -> None:
        self.assert_compatibility_registry_parent_link_rejected(
            use_linked_data_root=False
        )

    def test_compatibility_import_cli_dry_run(self) -> None:
        document = json.dumps(
            {
                "asset": {"version": "2.0", "generator": "adapter-cli-test"},
                "scenes": [{"nodes": []}],
                "scene": 0,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        document += b" " * ((4 - len(document) % 4) % 4)
        source = self.root / "minimal.glb"
        source.write_bytes(
            struct.pack("<4sII", b"glTF", 2, 20 + len(document))
            + struct.pack("<I4s", len(document), b"JSON")
            + document
        )
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/import_generated_asset.py",
                "--engine",
                "godot",
                "--src",
                str(source),
                "--godot",
                str(self.fake_godot),
                "--godot-project",
                str(self.project),
                "--dry-run",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("[godot]", completed.stdout)
        self.assertIn("--headless", completed.stdout)
        self.assertIn("--import", completed.stdout)

        from scripts import import_generated_asset as compatibility

        outside = self.root / "outside-target.glb"
        outside.write_bytes(b"keep")
        target = self.project / "assets" / "imported" / "props" / "linked.glb"
        target.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            compatibility.prepare_godot_asset(
                self.project,
                str(source),
                mock.Mock(
                    godot_dest="assets/imported/props",
                    godot_replace_existing=True,
                    dry_run=False,
                ),
                "linked.glb",
            )
        self.assertEqual(b"keep", outside.read_bytes())

        dangling_target = (
            self.project / "assets" / "imported" / "props" / "dangling.glb"
        )
        outside_import = self.root / "outside-dangling.import"
        dangling_import = Path(str(dangling_target) + ".import")
        dangling_import.symlink_to(outside_import)
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            compatibility.prepare_godot_asset(
                self.project,
                str(source),
                mock.Mock(
                    godot_dest="assets/imported/props",
                    godot_replace_existing=True,
                    dry_run=False,
                ),
                "dangling.glb",
            )
        self.assertTrue(dangling_import.is_symlink())
        self.assertFalse(outside_import.exists())

    def test_compatibility_godot_executable_priority_matches_client(self) -> None:
        from engine_adapters.godot.config import GodotClientConfig
        from scripts import import_generated_asset as compatibility

        explicit = self.root / "explicit-godot"
        modern = self.root / "modern-godot"
        modern_alias = self.root / "modern-alias-godot"
        legacy = self.root / "legacy-godot"
        with mock.patch.dict(
            os.environ,
            {
                "A3GAME_GODOT_EXECUTABLE": str(modern),
                "A3GAME_GODOT": str(modern_alias),
                "AAAGF_GODOT": str(legacy),
            },
            clear=False,
        ):
            self.assertEqual(modern.resolve(), compatibility.find_godot())
            self.assertEqual(
                modern.resolve(),
                GodotClientConfig.resolve(project_path=self.project).godot_executable,
            )
            self.assertEqual(
                explicit.resolve(),
                compatibility.find_godot(str(explicit)),
            )

        with mock.patch.dict(
            os.environ,
            {
                "A3GAME_GODOT_EXECUTABLE": "  ",
                "A3GAME_GODOT": str(modern_alias),
                "AAAGF_GODOT": str(legacy),
            },
            clear=False,
        ):
            self.assertEqual(modern_alias.resolve(), compatibility.find_godot())
            self.assertEqual(
                modern_alias.resolve(),
                GodotClientConfig.resolve(project_path=self.project).godot_executable,
            )

    def test_compatibility_import_accepts_project_marker_from_all_inputs(
        self,
    ) -> None:
        document = json.dumps(
            {
                "asset": {"version": "2.0", "generator": "project-marker-test"},
                "scenes": [{"nodes": []}],
                "scene": 0,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        document += b" " * ((4 - len(document) % 4) % 4)
        source = self.root / "project-marker.glb"
        source.write_bytes(
            struct.pack("<4sII", b"glTF", 2, 20 + len(document))
            + struct.pack("<I4s", len(document), b"JSON")
            + document
        )
        base_command = [
            sys.executable,
            "scripts/import_generated_asset.py",
            "--engine",
            "godot",
            "--src",
            str(source),
            "--godot",
            str(self.fake_godot),
            "--dry-run",
        ]
        project_file = self.project / "project.godot"
        for source_name in ("cli", "A3GAME_GODOT_PROJECT", "AAAGF_GODOT_PROJECT"):
            with self.subTest(source=source_name):
                command = list(base_command)
                environment = {
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "A3GAME_GODOT_PROJECT": "",
                    "AAAGF_GODOT_PROJECT": "",
                }
                if source_name == "cli":
                    command.extend(["--godot-project", str(project_file)])
                else:
                    environment[source_name] = str(project_file)

                completed = subprocess.run(
                    command,
                    cwd=Path(__file__).resolve().parents[1],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                    env=environment,
                )

                self.assertEqual(
                    0,
                    completed.returncode,
                    completed.stdout + completed.stderr,
                )
                self.assertIn(f"--path {self.project}", completed.stdout)
                self.assertNotIn(
                    f"--path {project_file}",
                    completed.stdout,
                )
                self.assertFalse(project_file.is_dir())

        legacy_project = self.root / "LegacyGodotProject"
        legacy_project.mkdir()
        (legacy_project / "project.godot").write_text(
            '[application]\nconfig/name="Legacy"\n', encoding="utf-8"
        )
        preferred = subprocess.run(
            base_command,
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "A3GAME_GODOT_PROJECT": str(project_file),
                "AAAGF_GODOT_PROJECT": str(legacy_project / "project.godot"),
            },
        )
        self.assertEqual(0, preferred.returncode, preferred.stdout + preferred.stderr)
        self.assertIn(f"--path {self.project}", preferred.stdout)
        self.assertNotIn(f"--path {legacy_project}", preferred.stdout)

    def test_compatibility_import_stages_gltf_sidecars_and_routes_motion(
        self,
    ) -> None:
        source_root = self.root / "compatibility-sources"
        source_root.mkdir()
        source = source_root / "model.gltf"
        source.write_text(
            json.dumps(
                {
                    "asset": {"version": "2.0"},
                    "buffers": [{"uri": "mesh.bin", "byteLength": 8}],
                    "meshes": [],
                }
            ),
            encoding="utf-8",
        )
        sidecar = source_root / "mesh.bin"
        sidecar.write_bytes(b"old-mesh")
        command = [
            sys.executable,
            "scripts/import_generated_asset.py",
            "--engine",
            "godot",
            "--src",
            str(source),
            "--godot",
            str(self.fake_godot),
            "--godot-project",
            str(self.project),
        ]
        completed = subprocess.run(
            command,
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "A3GAME_FAKE_GODOT_FAIL_IMPORT": "",
            },
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        prop_root = self.project / "assets" / "imported" / "props"
        target = prop_root / "model.gltf"
        target_sidecar = prop_root / "mesh.bin"
        self.assertEqual(source.read_bytes(), target.read_bytes())
        self.assertEqual(b"old-mesh", target_sidecar.read_bytes())
        target_import_sidecar = Path(str(target) + ".import")
        original_import_sidecar = target_import_sidecar.read_bytes()
        original_import_cache = self.godot_import_cache(target)
        self.assertTrue(original_import_cache)
        registered = self.client.assets.list_registered()
        self.assert_result(registered, ok=True)
        self.assertEqual(1, registered["payload"]["count"])
        prop_record = registered["artifacts"][0]
        self.assertEqual("prop", prop_record["type"])
        self.assertEqual("PackedScene", prop_record["backend_class"])
        self.assertTrue(prop_record["spawnable"])
        self.assertEqual(
            "res://assets/imported/props/model.gltf",
            prop_record["backend_path"],
        )
        self.assertTrue(prop_record["metadata"]["native_inspection"]["ok"])
        registry_after_prop = self.client._config.artifact_registry_path.read_bytes()

        original_target = target.read_bytes()
        source.write_text(
            json.dumps(
                {
                    "asset": {"version": "2.0"},
                    "buffers": [{"uri": "mesh.bin", "byteLength": 8}],
                    "extras": {"replacement": True},
                }
            ),
            encoding="utf-8",
        )
        sidecar.write_bytes(b"new-mesh")
        failed = subprocess.run(
            [*command, "--godot-replace-existing"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "A3GAME_FAKE_GODOT_FAIL_IMPORT": "1",
            },
        )
        self.assertEqual(1, failed.returncode, failed.stdout + failed.stderr)
        self.assertEqual(original_target, target.read_bytes())
        self.assertEqual(b"old-mesh", target_sidecar.read_bytes())
        self.assertEqual(original_import_sidecar, target_import_sidecar.read_bytes())
        self.assertEqual(original_import_cache, self.godot_import_cache(target))
        self.assertEqual(
            registry_after_prop,
            self.client._config.artifact_registry_path.read_bytes(),
        )

        stdout_failure = subprocess.run(
            [*command, "--godot-replace-existing"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "A3GAME_FAKE_GODOT_FAIL_IMPORT": "",
                "A3GAME_FAKE_GODOT_IMPORT_ERROR_ZERO": "1",
                "A3GAME_FAKE_GODOT_IMPORT_ERROR_STREAM": "stdout",
            },
        )
        self.assertEqual(
            1,
            stdout_failure.returncode,
            stdout_failure.stdout + stdout_failure.stderr,
        )
        self.assertIn("[godot] FAILED", stdout_failure.stdout)
        self.assertEqual(original_target, target.read_bytes())
        self.assertEqual(b"old-mesh", target_sidecar.read_bytes())
        self.assertEqual(original_import_sidecar, target_import_sidecar.read_bytes())
        self.assertEqual(original_import_cache, self.godot_import_cache(target))
        self.assertEqual(
            registry_after_prop,
            self.client._config.artifact_registry_path.read_bytes(),
        )
        stdout_failure_report = json.loads(
            (source_root / "model_godot_import.json").read_text(encoding="utf-8")
        )
        self.assertIs(stdout_failure_report["ok"], False)
        self.assertEqual(0, stdout_failure_report["import_process"]["returncode"])
        self.assertIn(
            "ERROR: Error importing",
            stdout_failure_report["import_process"]["stdout"],
        )
        self.assertIn("despite exit code 0", stdout_failure_report["error"])

        motion_root = self.root / "motion-source"
        motion_root.mkdir()
        motion_source = motion_root / "walk.gltf"
        motion_source.write_text(
            json.dumps(
                {
                    "asset": {"version": "2.0"},
                    "buffers": [{"uri": "walk.bin", "byteLength": 6}],
                    "animations": [{}],
                }
            ),
            encoding="utf-8",
        )
        (motion_root / "walk.bin").write_bytes(b"motion")
        motion = subprocess.run(
            [
                sys.executable,
                "scripts/import_generated_asset.py",
                "--engine",
                "godot",
                "--kind",
                "motion",
                "--src",
                str(motion_source),
                "--godot",
                str(self.fake_godot),
                "--godot-project",
                str(self.project),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "A3GAME_FAKE_GODOT_FAIL_IMPORT": "",
            },
        )
        self.assertEqual(0, motion.returncode, motion.stdout + motion.stderr)
        motion_target = self.project / "assets" / "imported" / "motions"
        self.assertTrue((motion_target / "walk.gltf").is_file())
        self.assertEqual(b"motion", (motion_target / "walk.bin").read_bytes())
        motion_report = json.loads(
            (motion_root / "walk_godot_import.json").read_text(encoding="utf-8")
        )
        self.assertEqual("motion", motion_report["asset_type"])
        self.assertEqual("motion", motion_report["usage"])
        self.assertEqual(
            "res://assets/imported/motions/walk.gltf",
            motion_report["asset_path"],
        )
        self.assertEqual("PackedScene", motion_report["backend_class"])
        self.assertEqual(["Walk"], motion_report["animations"])
        self.assertEqual(
            ["Character/Skeleton3D"],
            motion_report["skeletons"],
        )
        registered = self.client.assets.list_registered()
        self.assert_result(registered, ok=True)
        self.assertEqual(2, registered["payload"]["count"])
        motion_record = next(
            item for item in registered["artifacts"] if item["type"] == "motion"
        )
        self.assertFalse(motion_record["spawnable"])
        self.assertEqual(
            "res://assets/imported/motions/walk.gltf",
            motion_record["backend_path"],
        )
        self.assertEqual(
            ["Walk"],
            motion_record["metadata"]["native_inspection"]["animations"],
        )
        self.assertEqual(
            "Character/Skeleton3D",
            motion_record["metadata"]["skeleton_path"],
        )

    def test_compatibility_report_failure_rolls_back_and_cleans_backup(self) -> None:
        from scripts import import_generated_asset as compatibility

        document = json.dumps(
            {
                "asset": {"version": "2.0"},
                "scenes": [{"nodes": []}],
                "scene": 0,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        document += b" " * ((4 - len(document) % 4) % 4)
        source = self.root / "transaction.glb"
        source.write_bytes(
            struct.pack("<4sII", b"glTF", 2, 20 + len(document))
            + struct.pack("<I4s", len(document), b"JSON")
            + document
        )
        target = self.project / "assets" / "imported" / "props" / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"OLD")
        self.client.assets._register_resource(
            resource_path="res://preexisting.tscn",
            asset_type="scene",
            asset_id="preexisting",
            backend_class="PackedScene",
        )
        registry_path = self.client._config.artifact_registry_path
        registry_path.chmod(0o644)
        original_registry = registry_path.read_bytes()
        original_registry_mode = stat.S_IMODE(registry_path.stat().st_mode)
        blocked_report_dir = self.root / "report-parent-is-a-file"
        blocked_report_dir.write_text("not a directory", encoding="utf-8")
        backup_root = self.root / "controlled-godot-backup"
        backup_root.mkdir()
        arguments = [
            "scripts/import_generated_asset.py",
            "--engine",
            "godot",
            "--src",
            str(source),
            "--godot",
            str(self.fake_godot),
            "--godot-project",
            str(self.project),
            "--godot-replace-existing",
            "--report-dir",
            str(blocked_report_dir),
        ]
        with mock.patch.object(
            sys, "argv", arguments
        ), mock.patch.object(
            compatibility.tempfile,
            "mkdtemp",
            return_value=str(backup_root),
        ), mock.patch.dict(
            os.environ,
            {
                "A3GAME_FAKE_GODOT_FAIL_IMPORT": "",
                "A3GAME_FAKE_GODOT_UNLOADABLE": "",
            },
            clear=False,
        ), mock.patch(
            "builtins.print"
        ) as printed:
            result = compatibility.main()
        self.assertEqual(1, result)
        self.assertIn(
            "Godot import report could not be written",
            "\n".join(
                " ".join(str(item) for item in call.args)
                for call in printed.call_args_list
            ),
        )
        self.assertEqual(b"OLD", target.read_bytes())
        self.assertFalse(Path(str(target) + ".import").exists())
        self.assertEqual({}, self.godot_import_cache(target))
        self.assertFalse(backup_root.exists())
        self.assertEqual(
            original_registry,
            registry_path.read_bytes(),
        )
        self.assertEqual(
            original_registry_mode,
            stat.S_IMODE(registry_path.stat().st_mode),
        )

    def test_compatibility_registry_failure_rolls_back_import(self) -> None:
        document = json.dumps(
            {
                "asset": {"version": "2.0"},
                "scenes": [{"nodes": []}],
                "scene": 0,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        document += b" " * ((4 - len(document) % 4) % 4)
        source = self.root / "registry-failure.glb"
        source.write_bytes(
            struct.pack("<4sII", b"glTF", 2, 20 + len(document))
            + struct.pack("<I4s", len(document), b"JSON")
            + document
        )
        duplicate = {
            "artifact_id": "duplicate",
            "asset_id": "first",
            "type": "prop",
            "backend_path": "res://assets/imported/props/first.glb",
            "source_path": "/generated/first.glb",
            "backend_class": "PackedScene",
            "state": "ready",
            "spawnable": True,
            "metadata": {},
        }
        registry_path = self.client._config.artifact_registry_path
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        malformed = json.dumps(
            {
                "schema_version": "gamefactory3a.godot.artifacts.v1",
                "artifacts": [
                    duplicate,
                    {
                        **duplicate,
                        "asset_id": "second",
                        "backend_path": "res://assets/imported/props/second.glb",
                    },
                ],
            }
        ).encode()
        registry_path.write_bytes(malformed)

        completed = subprocess.run(
            [
                sys.executable,
                "scripts/import_generated_asset.py",
                "--engine",
                "godot",
                "--src",
                str(source),
                "--godot",
                str(self.fake_godot),
                "--godot-project",
                str(self.project),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        self.assertEqual(1, completed.returncode, completed.stdout + completed.stderr)
        target = self.project / "assets" / "imported" / "props" / "registry-failure.glb"
        self.assertFalse(target.exists())
        self.assertFalse(Path(str(target) + ".import").exists())
        self.assertEqual({}, self.godot_import_cache(target))
        self.assertEqual(malformed, registry_path.read_bytes())
        report = json.loads(
            (self.root / "registry-failure_godot_import.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIs(report["ok"], False)
        self.assertIn("duplicate artifact_id", report["error"])

    def test_compatibility_import_rejects_zero_exit_unloadable_resource(
        self,
    ) -> None:
        source_root = self.root / "invalid-compatibility-source"
        source_root.mkdir()
        source = source_root / "bad.glb"
        document = json.dumps(
            {
                "asset": {"version": "2.0"},
                "scenes": [{"nodes": []}],
                "scene": 0,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        document += b" " * ((4 - len(document) % 4) % 4)
        source.write_bytes(
            struct.pack("<4sII", b"glTF", 2, 20 + len(document))
            + struct.pack("<I4s", len(document), b"JSON")
            + document
        )
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/import_generated_asset.py",
                "--engine",
                "godot",
                "--src",
                str(source),
                "--godot",
                str(self.fake_godot),
                "--godot-project",
                str(self.project),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "A3GAME_FAKE_GODOT_UNLOADABLE": "1",
            },
        )
        self.assertEqual(1, completed.returncode, completed.stdout + completed.stderr)
        target = self.project / "assets" / "imported" / "props" / "bad.glb"
        self.assertFalse(target.exists())
        self.assertFalse(Path(str(target) + ".import").exists())
        self.assertEqual({}, self.godot_import_cache(target))
        report = json.loads(
            (source_root / "bad_godot_import.json").read_text(encoding="utf-8")
        )
        self.assertIs(report["ok"], False)
        self.assertEqual(0, report["import_process"]["returncode"])
        self.assertIn("could not be loaded", report["error"])

    def test_compatibility_import_rejects_real_decode_error_text(self) -> None:
        source_root = self.root / "decode-error-compatibility-source"
        source_root.mkdir()
        source = source_root / "bad-texture.gltf"
        source.write_bytes(self.corrupt_embedded_texture_gltf())

        completed = subprocess.run(
            [
                sys.executable,
                "scripts/import_generated_asset.py",
                "--engine",
                "godot",
                "--src",
                str(source),
                "--godot",
                str(self.fake_godot),
                "--godot-project",
                str(self.project),
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env={
                **os.environ,
                "PYTHONDONTWRITEBYTECODE": "1",
                "A3GAME_FAKE_GODOT_IMPORT_ERROR_ZERO": "1",
                "A3GAME_FAKE_GODOT_IMPORT_ERROR_TEXT": (CORRUPT_TEXTURE_IMPORT_ERRORS),
            },
        )

        self.assertEqual(1, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("[godot] FAILED", completed.stdout)
        target = self.project / "assets" / "imported" / "props" / "bad-texture.gltf"
        self.assertFalse(target.exists())
        self.assertFalse(Path(str(target) + ".import").exists())
        self.assertEqual({}, self.godot_import_cache(target))
        self.assertIsNone(self.client.assets._registry.find("bad-texture"))
        report = json.loads(
            (source_root / "bad-texture_godot_import.json").read_text(encoding="utf-8")
        )
        self.assertIs(report["ok"], False)
        self.assertEqual(0, report["import_process"]["returncode"])
        self.assertIn("ERR_FILE_CORRUPT", report["error"])
        self.assertIn("Couldn't load image", report["error"])

    def test_compatibility_launcher_reports_timeout(self) -> None:
        from scripts import import_generated_asset as compatibility

        with mock.patch.object(
            compatibility.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(
                ["godot", "--import"], 2, output="partial output"
            ),
        ):
            result = compatibility.run_engine(
                ["godot", "--import"],
                self.root / "timeout-report.json",
                "godot",
                2,
                False,
            )
        self.assertIs(result["ok"], False)
        self.assertIn("timed out after 2 seconds", result["error"])
        self.assertEqual("partial output", result["stdout_tail"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
