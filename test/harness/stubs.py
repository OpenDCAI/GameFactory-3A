"""
test/harness/stubs.py

Stub models and fixtures so any asset-generation chain can run on **CPU in
milliseconds**, with no weights and no network.

Each stub mimics the *interface* of a real wrapper (see
`agent_skills/develop_harness/model_require.md`) while producing trivially cheap
output. That is enough to exercise everything the operators and runners actually
own: task parsing, `game_id` resolution, output paths, artifact naming,
`meta.json`, and summaries.

Usage:
    import sys; sys.path.insert(0, "test/harness")
    import stubs

    op = stubs.build_operator("3d_object", run_id="_test")
    op.run({"game_id": "gameA", "task_id": "t1", "image": stubs.make_ref_image()})

Add a stub whenever you add a model slot — `smoke.py` looks them up by task kind.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

# Repo root on path so `operators.*` resolves when imported standalone.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
from PIL import Image, ImageDraw

# ── Fixtures ──────────────────────────────────────────────────────────────────


def make_ref_image(size: int = 128, seed: int = 0) -> Image.Image:
    """A tiny deterministic RGB image with a clear foreground blob on white."""
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    m = size // 4
    rng = np.random.default_rng(seed)
    color = tuple(int(c) for c in rng.integers(20, 200, size=3))
    d.ellipse([m, m, size - m, size - m], fill=color)          # body
    d.rectangle([m // 2, size // 2 - 4, size - m // 2, size // 2 + 4], fill=color)  # arms
    return img


def write_ref_image(path: str | Path, size: int = 128, seed: int = 0) -> Path:
    """Materialize `make_ref_image` on disk (for jsonl-driven smoke runs)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    make_ref_image(size=size, seed=seed).save(p)
    return p


def make_minimal_glb(triangles: int = 2, pad_to: int = 4096) -> bytes:
    """
    A *valid* binary glTF with `triangles` unlit triangles.

    Real enough that `models.common.glb_utils`, an engine importer or a viewer
    accepts it, cheap enough to build in microseconds. The JSON chunk is padded
    with spaces (legal glTF padding) up to `pad_to` bytes so the artifact clears
    the ">1 KB looks plausible" assertions in `test/`.

    Args:
        triangles: Number of triangle primitives to emit.
        pad_to:    Minimum total size in bytes.

    Returns:
        GLB file content.
    """
    import json
    import struct

    positions: list[float] = []
    for i in range(triangles):
        z = float(i) * 0.1
        positions += [0.0, 0.0, z, 1.0, 0.0, z, 0.0, 1.0, z]
    indices = list(range(3 * triangles))

    pos_blob = struct.pack(f"<{len(positions)}f", *positions)
    idx_blob = struct.pack(f"<{len(indices)}H", *indices)
    idx_pad = (-len(idx_blob)) % 4
    bin_blob = pos_blob + idx_blob + b"\x00" * idx_pad

    doc = {
        "asset": {"version": "2.0", "generator": "AAAGameForge test harness stub"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "StubMesh"}],
        "meshes": [{"name": "StubMesh", "primitives": [
            {"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "buffers": [{"byteLength": len(bin_blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_blob), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos_blob), "byteLength": len(idx_blob),
             "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3 * triangles,
             "type": "VEC3", "min": [0.0, 0.0, 0.0],
             "max": [1.0, 1.0, max(0.0, (triangles - 1) * 0.1)]},
            {"bufferView": 1, "componentType": 5123, "count": 3 * triangles,
             "type": "SCALAR"},
        ],
    }

    json_blob = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    header_and_chunks = 12 + 8 + 8 + len(bin_blob)
    target_json = max(len(json_blob), pad_to - header_and_chunks)
    json_blob += b" " * ((target_json - len(json_blob)) + (-target_json % 4))

    return _pack_glb(doc, bin_blob, json_pad_to=pad_to)


def _pack_glb(doc: dict, bin_blob: bytes, json_pad_to: int = 0) -> bytes:
    """Assemble a GLB from a glTF document and its binary chunk."""
    import json
    import struct

    json_blob = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    overhead = 12 + 8 + 8 + len(bin_blob)
    target = max(len(json_blob), json_pad_to - overhead)
    # Trailing spaces are legal glTF JSON-chunk padding; chunks are 4-byte aligned.
    json_blob += b" " * ((target - len(json_blob)) + (-target % 4))

    return b"".join([
        struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(json_blob) + 8 + len(bin_blob)),
        struct.pack("<I4s", len(json_blob), b"JSON"), json_blob,
        struct.pack("<I4s", len(bin_blob), b"BIN\x00"), bin_blob,
    ])


#: Cube faces as (normal, four corners in CCW winding seen from outside), with
#: the cube spanning 0..1 on every axis. Scaled by `size` in `make_textured_glb`.
_CUBE_FACES = (
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
    ((0, 0, -1), ((1, 0, 0), (0, 0, 0), (0, 1, 0), (1, 1, 0))),
    ((1, 0, 0), ((1, 0, 1), (1, 0, 0), (1, 1, 0), (1, 1, 1))),
    ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
    ((0, 1, 0), ((0, 1, 1), (1, 1, 1), (1, 1, 0), (0, 1, 0))),
    ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
)


def make_checker_png(size: int = 64, squares: int = 8) -> bytes:
    """A checkerboard PNG — obvious enough that a wrong UV or a missing texture
    is visible at a glance in an engine viewport."""
    from io import BytesIO

    img = Image.new("RGB", (size, size))
    step = max(1, size // squares)
    d = ImageDraw.Draw(img)
    for y in range(0, size, step):
        for x in range(0, size, step):
            dark = ((x // step) + (y // step)) % 2 == 0
            d.rectangle([x, y, x + step - 1, y + step - 1],
                        fill=(40, 40, 48) if dark else (220, 120, 40))
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def make_textured_glb(size: float = 1.0, texture_size: int = 64) -> bytes:
    """
    A textured, normal-bearing, PBR-material cube as a single GLB.

    `make_minimal_glb` proves an engine importer parses a file; this proves the
    parts that actually break in an engine: an **embedded** texture, a PBR
    material, per-vertex normals and UVs. Use it to check that materials and
    textures survive the import, not just geometry.

    Args:
        size: Edge length in glTF units (metres).
        texture_size: Checkerboard resolution.

    Returns:
        GLB file content: 12 triangles, 24 vertices, 1 material, 1 texture.
    """
    import struct

    positions: list[float] = []
    normals: list[float] = []
    uvs: list[float] = []
    indices: list[int] = []
    for normal, corners in _CUBE_FACES:
        base = len(positions) // 3
        for (cx, cy, cz), (u, v) in zip(corners, ((0, 0), (1, 0), (1, 1), (0, 1))):
            positions += [cx * size, cy * size, cz * size]
            normals += list(normal)
            uvs += [u, v]
        indices += [base, base + 1, base + 2, base, base + 2, base + 3]

    pos_blob = struct.pack(f"<{len(positions)}f", *positions)
    nrm_blob = struct.pack(f"<{len(normals)}f", *normals)
    uv_blob = struct.pack(f"<{len(uvs)}f", *uvs)
    idx_blob = struct.pack(f"<{len(indices)}H", *indices)
    idx_blob += b"\x00" * ((-len(idx_blob)) % 4)
    png_blob = make_checker_png(texture_size)
    png_blob += b"\x00" * ((-len(png_blob)) % 4)

    views, offset = [], 0
    for blob, target in ((pos_blob, 34962), (nrm_blob, 34962), (uv_blob, 34962),
                         (idx_blob, 34963), (png_blob, None)):
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(blob)}
        if target:
            view["target"] = target
        views.append(view)
        offset += len(blob)
    bin_blob = pos_blob + nrm_blob + uv_blob + idx_blob + png_blob

    n = len(positions) // 3
    doc = {
        "asset": {"version": "2.0", "generator": "AAAGameForge test harness stub"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "StubTexturedCube"}],
        "meshes": [{"name": "StubTexturedCube", "primitives": [{
            "attributes": {"POSITION": 0, "NORMAL": 1, "TEXCOORD_0": 2},
            "indices": 3, "material": 0, "mode": 4}]}],
        "materials": [{
            "name": "StubCheckerMaterial",
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": 0},
                "metallicFactor": 0.0,
                "roughnessFactor": 0.8,
            },
        }],
        "textures": [{"sampler": 0, "source": 0}],
        "images": [{"bufferView": 4, "mimeType": "image/png", "name": "StubChecker"}],
        "samplers": [{"magFilter": 9729, "minFilter": 9987,
                      "wrapS": 10497, "wrapT": 10497}],
        "buffers": [{"byteLength": len(bin_blob)}],
        "bufferViews": views,
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3",
             "min": [0.0, 0.0, 0.0], "max": [size, size, size]},
            {"bufferView": 1, "componentType": 5126, "count": n, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": n, "type": "VEC2"},
            {"bufferView": 3, "componentType": 5123, "count": len(indices),
             "type": "SCALAR"},
        ],
    }
    return _pack_glb(doc, bin_blob)


# ── Base ──────────────────────────────────────────────────────────────────────


class _StubBase:
    """Records calls so tests can assert the operator passed things through."""

    def __init__(self, model_path: str = "stub://model", device: str = "cpu", **kw):
        self.model_path = model_path
        self.device = device
        self.extra = kw
        self.calls: list[dict] = []

    def unload(self) -> None:  # idempotent, per R4.2
        self.calls.append({"op": "unload"})


# ── Layer-A generation stubs ──────────────────────────────────────────────────


class StubTrellis2Model(_StubBase):
    """Mimics `models.gen_3d_object.trellis_2_model.Trellis2Model`."""

    def infer(self, image: Image.Image, seed: int = 42, **kw) -> bytes:
        self.calls.append({"op": "infer", "seed": seed, "size": image.size, **kw})
        return b"glTF" + bytes(64)

    def infer_and_save(
        self,
        image: Image.Image,
        output_path: str,
        seed: int = 42,
        decimation_target: int = 1_000_000,
        texture_size: int = 4096,
    ) -> str:
        self.calls.append({
            "op": "infer_and_save", "seed": seed, "output_path": output_path,
            "decimation_target": decimation_target, "texture_size": texture_size,
        })
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        # A real (if trivial) GLB, padded past the ">1KB looks plausible"
        # assertions in test/ — so engine-side importers can be smoked with it.
        out.write_bytes(make_minimal_glb())
        return str(out)


class _StubCloudModel(_StubBase):
    """
    Shared body of the cloud-API stubs (`api_model_require.md` R9 checklist).

    Mimics the *interface* of `TripoModel` / `MeshyModel` including
    `last_call_info` and `balance()`, and **performs no network I/O whatsoever** —
    no key, no socket, no `requests` import. That is what makes
    `smoke.py --kind 3d_object` runnable on a disconnected laptop.
    """

    provider = "stub"
    #: Faces the fake asset reports, so face-budget assertions have something real.
    triangles = 2

    def __init__(self, model_path: str = "stub-v1", device: str = "cpu", **kw):
        super().__init__(model_path=model_path, device=device, **kw)
        self.output_format = kw.get("output_format", "glb")
        self.last_call_info: dict = {}

    def infer(
        self,
        image=None,
        seed: int = 42,
        decimation_target: int | None = None,
        texture_size: int | None = None,
        *,
        prompt: str | None = None,
        negative_prompt: str | None = None,
        image_url: str | None = None,
        verbose: bool | None = None,
        **provider_kwargs,
    ) -> bytes:
        if image is None and image_url is None and not prompt:
            raise ValueError(f"{type(self).__name__}.infer needs an image or a prompt")
        self.calls.append({
            "op": "infer", "seed": seed, "prompt": prompt,
            "decimation_target": decimation_target, "texture_size": texture_size,
            **provider_kwargs,
        })
        data = make_minimal_glb(triangles=self.triangles)
        self.last_call_info = {
            "provider": self.provider, "model": self.model_path,
            "task_id": f"stub_task_{len(self.calls):03d}", "elapsed_sec": 0.0,
            "credits": 0, "cached": False, "triangles": self.triangles,
            "bytes": len(data),
        }
        return data

    def infer_and_save(
        self,
        image=None,
        output_path: str | None = None,
        seed: int = 42,
        decimation_target: int | None = None,
        texture_size: int | None = None,
        *,
        prompt: str | None = None,
        negative_prompt: str | None = None,
        image_url: str | None = None,
        verbose: bool | None = None,
        **provider_kwargs,
    ) -> str:
        if not output_path:
            raise ValueError("output_path is required (model_require.md R1.2)")
        data = self.infer(image, seed, decimation_target, texture_size,
                          prompt=prompt, negative_prompt=negative_prompt,
                          image_url=image_url, verbose=verbose, **provider_kwargs)
        self.calls.append({"op": "infer_and_save", "output_path": output_path})
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return str(out)

    def balance(self) -> dict:
        return {"balance": 1_000_000, "frozen": 0}


class StubTripoModel(_StubCloudModel):
    """Mimics `models.gen_3d_object.tripo_model.TripoModel`."""

    provider = "tripo"


class StubMeshyModel(_StubCloudModel):
    """Mimics `models.gen_3d_object.meshy_model.MeshyModel`."""

    provider = "meshy"


class StubPuppeteerModel(_StubBase):
    """Mimic Puppeteer's in-memory skeleton and skinning result."""

    def infer(
        self,
        mesh: bytes,
        mesh_format: str = ".glb",
        seed: int = 42,
        **kw,
    ) -> dict:
        self.calls.append(
            {
                "op": "infer",
                "mesh_format": mesh_format,
                "mesh_bytes": len(mesh),
                "seed": seed,
                **kw,
            }
        )
        joints = [
            "joints joint0 0 0 0",
            "joints joint1 0 1 0",
            "joints joint2 -1 1 0",
            "joints joint3 1 1 0",
            "joints joint4 -0.5 -1 0",
            "joints joint5 0.5 -1 0",
            "root joint0",
            "hier joint0 joint1",
            "hier joint1 joint2",
            "hier joint1 joint3",
            "hier joint0 joint4",
            "hier joint0 joint5",
        ]
        rig = "\n".join(
            [
                *joints,
                "skin 0 joint0 1.0",
                "skin 1 joint1 1.0",
                "skin 2 joint2 1.0",
            ]
        ) + "\n"
        return {
            "rig_text": rig,
            "skeleton_text": "\n".join(joints) + "\n",
            "mesh_obj_bytes": (
                b"o StubAvatar\n"
                b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
            ),
            "joint_count": 6,
            "skin_vertex_count": 3,
            "seed": seed,
        }


class StubMoMaskModel(_StubBase):
    """Mimic one HumanML3D MoMask generation at the native 20 FPS."""

    def infer(self, prompt: str, seed: int = 42, **kw) -> dict:
        self.calls.append(
            {"op": "infer", "prompt": prompt, "seed": seed, **kw}
        )
        bvh = (
            "HIERARCHY\n"
            "ROOT Hips\n"
            "{\n"
            "  OFFSET 0 0 0\n"
            "  CHANNELS 6 Xposition Yposition Zposition "
            "Zrotation Xrotation Yrotation\n"
            "  End Site\n"
            "  {\n"
            "    OFFSET 0 1 0\n"
            "  }\n"
            "}\n"
            "MOTION\n"
            "Frames: 2\n"
            "Frame Time: 0.050000\n"
            "0 0 0 0 0 0\n"
            "0 0 0 5 0 0\n"
        ).encode("utf-8")
        joints = np.zeros((2, 22, 3), dtype=np.float32)
        return {
            "bvh_bytes": bvh,
            "raw_bvh_bytes": bvh,
            "ik_bvh_bytes": bvh,
            "joints": joints,
            "preview_mp4_bytes": b"\x00\x00\x00\x18ftypmp42stub",
            "fps": 20,
            "seed": seed,
        }


def retarget_mapping() -> dict:
    """Return the small humanoid mapping used by motion smoke tests."""
    return {
        "root_bones": {"source": "Hips", "puppeteer": "joint0"},
        "bone_map": {
            "Hips": "joint0",
            "Spine": "joint1",
            "LeftArm": "joint2",
            "RightArm": "joint3",
            "LeftLeg": "joint4",
            "RightLeg": "joint5",
        },
        "retarget_chains": {
            "spine": {
                "source": ["Hips", "Spine"],
                "puppeteer": ["joint0", "joint1"],
            },
            "left_arm": {
                "source": ["LeftArm"],
                "puppeteer": ["joint2"],
            },
            "right_arm": {
                "source": ["RightArm"],
                "puppeteer": ["joint3"],
            },
            "left_leg": {
                "source": ["LeftLeg"],
                "puppeteer": ["joint4"],
            },
            "right_leg": {
                "source": ["RightLeg"],
                "puppeteer": ["joint5"],
            },
        },
    }


def retarget_info(fps: int) -> dict:
    """Return structural metadata matching the stub mapping."""
    return {
        "source_frame_range": [1, 2],
        "output_frame_range": [1, 2],
        "fps": fps,
        "source_bone_count": 6,
        "target_bone_count": 6,
    }


def stub_retarget_motion(
    *,
    output_path: str,
    anim_only_output_path: str | None,
    mapping_path: str | None,
    mapping_output_path: str,
    info_output_path: str,
    fps: int = 30,
    export_anim_only: bool = True,
    **_kw,
) -> dict[str, str | None]:
    """Mimic the retarget function without importing or invoking Blender."""
    output = Path(output_path)
    animation = (
        Path(anim_only_output_path)
        if anim_only_output_path is not None
        else None
    )
    mapping_output = Path(mapping_output_path)
    info_output = Path(info_output_path)
    for path in (output, animation, mapping_output, info_output):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)

    output.write_bytes(b"Kaydara FBX Binary  \x00" + bytes(256))
    if export_anim_only and animation is not None:
        animation.write_bytes(b"Kaydara FBX Binary  \x00" + bytes(128))
    if mapping_path:
        shutil.copyfile(mapping_path, mapping_output)
    else:
        mapping_output.write_text(
            json.dumps(retarget_mapping(), indent=2),
            encoding="utf-8",
        )
    info_output.write_text(
        json.dumps(retarget_info(fps), indent=2),
        encoding="utf-8",
    )
    return {
        "retargeted_fbx_path": str(output),
        "anim_only_fbx_path": (
            str(animation)
            if export_anim_only and animation is not None
            else None
        ),
        "mapping_path": str(mapping_output),
        "retarget_info_path": str(info_output),
    }


class StubQwenEditModel(_StubBase):
    """Mimics `models.gen_image.qwen_edit_model.QwenEditModel`."""

    def load(self) -> None:
        self.calls.append({"op": "load"})

    def infer(self, image: Image.Image, prompt: str, seed: int = 42,
              steps: int = 40) -> Image.Image:
        self.calls.append({"op": "infer", "seed": seed, "steps": steps,
                           "prompt_len": len(prompt)})
        # Return a white-bg image with a blob, i.e. what the real T-pose edit yields.
        return make_ref_image(size=max(image.size), seed=seed)

    def edit(self, image: Image.Image, prompt: str, seed: int = 42,
             steps: int = 40) -> Image.Image:
        """Compatibility alias, mirroring `QwenEditModel.edit`."""
        return self.infer(image, prompt=prompt, seed=seed, steps=steps)


class StubVideoModel(_StubBase):
    """Mimics the shared ``infer`` / ``infer_and_save`` CG-video interface."""

    output_format = "mp4"

    def __init__(self, model_path: str = "stub-seedance", device: str = "cpu", **kw):
        super().__init__(model_path=model_path, device=device, **kw)
        self.last_call_info: dict = {}

    def infer(self, request, **provider_kwargs) -> bytes:
        self.calls.append({
            "op": "infer",
            "mode": request.mode.value,
            "prompt": request.prompt,
            "duration_sec": request.duration_sec,
            "seed": request.seed,
            "first_frame": request.first_frame is not None,
            "last_frame": request.last_frame is not None,
            "reference_count": len(request.reference_images),
            **provider_kwargs,
        })
        # Minimal MP4-like fixture: the harness checks placement and non-empty
        # artifacts, while the paid integration test checks provider decoding.
        data = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isomaaagf-video-stub"
        self.last_call_info = {
            "provider": "stub-video",
            "model": self.model_path,
            "task_id": f"stub_video_{len(self.calls):03d}",
            "elapsed_sec": 0.0,
            "bytes": len(data),
            "cached": False,
        }
        return data

    def infer_and_save(self, request, output_path: str, **provider_kwargs) -> str:
        data = self.infer(request, **provider_kwargs)
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        self.calls.append({"op": "infer_and_save", "output_path": str(out)})
        return str(out)


class StubQwen3TTSModel(_StubBase):
    """Mimics ``models.gen_audio.qwen3_tts_model.Qwen3TTSModel``."""

    def infer(self, text: str, seed: int = 42, **kw) -> dict:
        sample_rate = 24_000
        duration = max(0.25, min(2.0, len(text) * 0.08))
        samples = max(1, int(sample_rate * duration))
        t = np.arange(samples, dtype=np.float32) / sample_rate
        frequency = 180.0 + float(seed % 80)
        waveform = (0.2 * np.sin(2.0 * np.pi * frequency * t)).astype(np.float32)
        self.calls.append({"op": "infer", "text": text, "seed": seed, **kw})
        return {"waveform": waveform[None, :], "sample_rate": sample_rate}


class StubWooshDFlowModel(_StubBase):
    """Mimics ``models.gen_audio.woosh_model.WooshDFlowModel``."""

    def infer(self, prompt: str, seed: int = 42, duration_sec=None, **kw) -> dict:
        sample_rate = 48_000
        duration = float(duration_sec or 1.0)
        samples = max(1, int(sample_rate * duration))
        rng = np.random.default_rng(seed)
        envelope = np.exp(-np.linspace(0.0, 8.0, samples, dtype=np.float32))
        waveform = (0.25 * rng.standard_normal(samples) * envelope).astype(np.float32)
        self.calls.append({"op": "infer", "prompt": prompt, "seed": seed, **kw})
        return {"waveform": waveform[None, :], "sample_rate": sample_rate}


class StubSeedAudioModel(_StubBase):
    """Mimics ``SeedAudioModel`` in either dialogue or sound-effect mode."""

    def __init__(self, mode: str = "dialogue", **kw):
        super().__init__(model_path="stub://seed-audio-1.0", device="cpu", **kw)
        self.mode = mode

    def infer(self, text=None, seed: int = 42, *, prompt=None,
              duration_sec=None, **kw) -> dict:
        sample_rate = 24_000
        source = str(text or prompt or "")
        duration = float(duration_sec or max(0.25, min(2.0, len(source) * 0.08)))
        samples = max(1, int(sample_rate * duration))
        t = np.arange(samples, dtype=np.float32) / sample_rate
        if self.mode == "dialogue":
            waveform = 0.2 * np.sin(2.0 * np.pi * (180.0 + seed % 80) * t)
        else:
            rng = np.random.default_rng(seed)
            envelope = np.exp(-np.linspace(0.0, 8.0, samples, dtype=np.float32))
            waveform = 0.25 * rng.standard_normal(samples) * envelope
        self.calls.append({"op": "infer", "mode": self.mode, "text": text,
                           "prompt": prompt, "seed": seed, **kw})
        return {"waveform": waveform.astype(np.float32)[None, :],
                "sample_rate": sample_rate}


# ── Tool-model stubs ──────────────────────────────────────────────────────────


class StubRMBGModel(_StubBase):
    """Mimics `models.tools.image_matting.rmbg_model.RMBGModel` — HxW float32 in [0, 1]."""

    def infer(self, image: Image.Image, **kw) -> np.ndarray:
        self.calls.append({"op": "infer", "size": image.size})
        arr = np.asarray(image.convert("RGB"))
        # Foreground = anything not near-white, matching RMBG's practical behaviour.
        fg = (arr < 240).any(axis=-1)
        return fg.astype(np.float32)

    def __call__(self, image: Image.Image, **kw) -> np.ndarray:
        return self.infer(image, **kw)

    def remove_background(self, image: Image.Image) -> Image.Image:
        rgba = np.array(image.convert("RGBA"))
        rgba[..., 3] = (self.infer(image) * 255).astype(np.uint8)
        return Image.fromarray(rgba, "RGBA")


class StubDepthAnythingModel(_StubBase):
    """Mimics `models.tools.image_matting.depth_anything_model.DepthAnythingModel`."""

    def infer(self, image: Image.Image, **kw) -> np.ndarray:
        self.calls.append({"op": "infer", "size": image.size})
        arr = np.asarray(image.convert("RGB")).astype(np.float32)
        # Darker (non-white) pixels read as "closer".
        return 1.0 - arr.mean(axis=-1) / 255.0

    def __call__(self, image: Image.Image, **kw) -> np.ndarray:
        return self.infer(image, **kw)


class StubWorldMirrorModel(_StubBase):
    """
    Mimics `models.gen_3d_scene.world_mirror_model.WorldMirrorModel`.

    Returns a pinhole unprojection of a two-slab depth map: a near wall on the
    left, a far wall on the right. That gives the meshing code a real occlusion
    boundary to cut and a real surface to keep, at 64×64 and in microseconds.
    """

    #: Focal length the fake intrinsics advertise, in pixels.
    FOCAL = 100.0

    def infer(self, images, seed: int = 42, **kw) -> dict[str, np.ndarray]:
        self.calls.append({"op": "infer", "frames": len(images), "seed": seed})
        frames, height, width = len(images), 64, 64

        depth = np.full((height, width), 2.0, dtype=np.float32)
        depth[:, width // 2 :] = 6.0

        rows, columns = np.mgrid[0:height, 0:width]
        x = (columns - (width - 1) / 2) * depth / self.FOCAL
        y = (rows - (height - 1) / 2) * depth / self.FOCAL
        points = np.stack([x, y, depth], axis=-1).astype(np.float32)

        intrinsic = np.array(
            [[self.FOCAL, 0, (width - 1) / 2], [0, self.FOCAL, (height - 1) / 2], [0, 0, 1]],
            dtype=np.float32,
        )
        normals = np.zeros((frames, height, width, 3), dtype=np.float32)
        normals[..., 2] = -1.0

        return {
            "points": np.repeat(points[None], frames, axis=0),
            "depth": np.repeat(depth[None], frames, axis=0),
            "normals": normals,
            "confidence": np.full((frames, height, width), 5.0, dtype=np.float32),
            "colors": np.full((frames, height, width, 3), 180, dtype=np.uint8),
            "camera_poses": np.repeat(np.eye(4, dtype=np.float32)[None], frames, axis=0),
            "intrinsics": np.repeat(intrinsic[None], frames, axis=0),
        }


class StubWorldPlayModel(_StubBase):
    """Mimics `models.gen_3d_scene.world_play_model.WorldPlayModel`."""

    def infer(self, image: Image.Image, prompt: str = "", pose: str = "",
              video_length: int = 61, seed: int = 42, **kw) -> list[Image.Image]:
        self.calls.append({"op": "infer", "prompt": prompt, "pose": pose, "seed": seed})
        # A handful of frames is enough; the geometry stub ignores their content.
        return [image.convert("RGB") for _ in range(4)]


# ── Registry ──────────────────────────────────────────────────────────────────

#: task_kind -> {backend name: stub class} for slots with several backends.
#: The first entry is the default. Select one with
#: `build_operator(kind, model_key="tripo")` or `smoke.py --backend tripo`.
STUB_BACKENDS: dict[str, dict[str, Any]] = {
    "3d_object": {
        "trellis2": StubTrellis2Model,
        "tripo": StubTripoModel,
        "meshy": StubMeshyModel,
    },
    "3d_scene": {
        "worldmirror": StubWorldMirrorModel,
        "worldplay": StubWorldMirrorModel,
    },
    "cg_video": {
        "seedance": StubVideoModel,
    },
    "audio": {
        "local": StubQwen3TTSModel,
        "seed_audio": StubSeedAudioModel,
    },
}

#: task_kind -> factory returning the kwargs for that task's operator.
#: Extend this when you add an asset task, so `smoke.py --kind <new>` works.
STUB_OPERATOR_KWARGS: dict[str, Any] = {
    "motion": lambda model_key=None: {
        "puppeteer_model": StubPuppeteerModel(),
        "momask_model": StubMoMaskModel(),
        "retarget_fn": stub_retarget_motion,
    },
    "3d_object": lambda model_key=None: {
        "model": STUB_BACKENDS["3d_object"][model_key or "trellis2"]()},
    "tpose": lambda model_key=None: {
        "gen_model": StubQwenEditModel(), "mask_model": StubRMBGModel()},
    # `worldplay` adds the video stage in front, so a reference image alone is a
    # valid task; `worldmirror` alone needs the task to bring its own frames.
    "3d_scene": lambda model_key=None: {
        "model": STUB_BACKENDS["3d_scene"][model_key or "worldmirror"](),
        "video_model": StubWorldPlayModel()},
    "cg_video": lambda model_key=None: {
        "model": STUB_BACKENDS["cg_video"][model_key or "seedance"]()},
    "audio": lambda model_key=None: {
        "dialogue_model": (
            StubSeedAudioModel(mode="dialogue")
            if model_key == "seed_audio" else StubQwen3TTSModel()
        ),
        "sound_effect_model": (
            StubSeedAudioModel(mode="sound_effect")
            if model_key == "seed_audio" else StubWooshDFlowModel()
        ),
    },
}


def build_operator(task_kind: str, run_id: str = "_smoke",
                   output_dir: Optional[str] = None,
                   default_game_id: Optional[str] = None,
                   model_key: Optional[str] = None):
    """
    Instantiate the operator for `task_kind`, wired to stub models.

    Args:
        model_key: Backend to stub for slots that have several (see
                   `STUB_BACKENDS`). None picks the slot's default, which keeps
                   every existing caller behaving exactly as before.

    Raises a clear error when a task has no stub registered yet.
    """
    if task_kind not in STUB_OPERATOR_KWARGS:
        raise KeyError(
            f"No stub registered for task_kind={task_kind!r}. Add one to "
            f"STUB_OPERATOR_KWARGS in test/harness/stubs.py. "
            f"Available: {sorted(STUB_OPERATOR_KWARGS)}"
        )
    known = STUB_BACKENDS.get(task_kind, {})
    if model_key and model_key not in known:
        raise KeyError(
            f"No stub backend {model_key!r} for task_kind={task_kind!r}. "
            f"Available: {sorted(known) or '<single backend>'}"
        )

    operator_cls = _import_operator(task_kind)
    factory = STUB_OPERATOR_KWARGS[task_kind]
    kwargs = factory(model_key) if task_kind in STUB_BACKENDS else factory()
    return operator_cls(
        **kwargs,
        output_dir=output_dir,
        run_id=run_id,
        default_game_id=default_game_id,
    )


#: task_kind -> (module path, class name)
OPERATOR_LOCATION: dict[str, tuple[str, str]] = {
    "3d_object": ("operators.gen_3d_object.operator", "Gen3DObjectOperator"),
    "tpose": ("operators.gen_tpose_image.operator", "GenTPoseImageOperator"),
    "3d_scene": ("operators.gen_3d_scene.operator", "Gen3DSceneOperator"),
    "motion": ("operators.gen_motion.operator", "GenMotionOperator"),
    "cg_video": ("operators.gen_cg_video.operator", "GenCGVideoOperator"),
    "audio": ("operators.gen_audio.operator", "GenAudioOperator"),
    "mechanic": ("operators.gen_mechanic.operator", "GenMechanicOperator"),
    "ui": ("operators.gen_ui.operator", "GenUIOperator"),
}


def _import_operator(task_kind: str):
    import importlib

    module_path, class_name = OPERATOR_LOCATION[task_kind]
    module = importlib.import_module(module_path)
    return getattr(module, class_name)
