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
        # Big enough to pass the ">1KB looks plausible" assertions in test/.
        out.write_bytes(b"glTF\x02\x00\x00\x00" + b"\x00" * 4096)
        return str(out)


class StubQwenEditModel(_StubBase):
    """Mimics `models.gen_image.qwen_edit.QwenEditModel`."""

    def load(self) -> None:
        self.calls.append({"op": "load"})

    def edit(self, image: Image.Image, prompt: str, seed: int = 42,
             steps: int = 40) -> Image.Image:
        self.calls.append({"op": "edit", "seed": seed, "steps": steps,
                           "prompt_len": len(prompt)})
        # Return a white-bg image with a blob, i.e. what the real T-pose edit yields.
        return make_ref_image(size=max(image.size), seed=seed)


# ── Tool-model stubs ──────────────────────────────────────────────────────────


class StubRMBGModel(_StubBase):
    """Mimics `models.tools.image_matting.rmbg.RMBGModel` — HxW float32 in [0, 1]."""

    def predict(self, image: Image.Image, **kw) -> np.ndarray:
        self.calls.append({"op": "predict", "size": image.size})
        arr = np.asarray(image.convert("RGB"))
        # Foreground = anything not near-white, matching RMBG's practical behaviour.
        fg = (arr < 240).any(axis=-1)
        return fg.astype(np.float32)

    def __call__(self, image: Image.Image, **kw) -> np.ndarray:
        return self.predict(image, **kw)

    def remove_background(self, image: Image.Image) -> Image.Image:
        rgba = np.array(image.convert("RGBA"))
        rgba[..., 3] = (self.predict(image) * 255).astype(np.uint8)
        return Image.fromarray(rgba, "RGBA")


class StubDepthAnythingModel(_StubBase):
    """Mimics `models.tools.image_matting.depth_anything.DepthAnythingModel`."""

    def predict(self, image: Image.Image, **kw) -> np.ndarray:
        self.calls.append({"op": "predict", "size": image.size})
        arr = np.asarray(image.convert("RGB")).astype(np.float32)
        # Darker (non-white) pixels read as "closer".
        return 1.0 - arr.mean(axis=-1) / 255.0

    def __call__(self, image: Image.Image, **kw) -> np.ndarray:
        return self.predict(image, **kw)


# ── Registry ──────────────────────────────────────────────────────────────────

#: task_kind -> factory returning the kwargs for that task's operator.
#: Extend this when you add an asset task, so `smoke.py --kind <new>` works.
STUB_OPERATOR_KWARGS: dict[str, Any] = {
    "3d_object": lambda: {"model": StubTrellis2Model()},
    "tpose": lambda: {"gen_model": StubQwenEditModel(), "mask_model": StubRMBGModel()},
}


def build_operator(task_kind: str, run_id: str = "_smoke",
                   output_dir: Optional[str] = None,
                   default_game_id: Optional[str] = None):
    """
    Instantiate the operator for `task_kind`, wired to stub models.

    Raises a clear error when a task has no stub registered yet.
    """
    if task_kind not in STUB_OPERATOR_KWARGS:
        raise KeyError(
            f"No stub registered for task_kind={task_kind!r}. Add one to "
            f"STUB_OPERATOR_KWARGS in test/harness/stubs.py. "
            f"Available: {sorted(STUB_OPERATOR_KWARGS)}"
        )

    operator_cls = _import_operator(task_kind)
    kwargs = STUB_OPERATOR_KWARGS[task_kind]()
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
    "retarget": ("operators.retarget.operator", "RetargetOperator"),
}


def _import_operator(task_kind: str):
    import importlib

    module_path, class_name = OPERATOR_LOCATION[task_kind]
    module = importlib.import_module(module_path)
    return getattr(module, class_name)
