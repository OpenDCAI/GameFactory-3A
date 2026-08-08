"""
WorldMirrorModel — wraps HunyuanWorld-Mirror, the feed-forward geometry model
used by Hunyuan WorldPlay: a set of posed or unposed images in, a dense 3D
reconstruction out (world points, metric-ish depth, normals, confidence and
recovered cameras).

Reference: https://github.com/Tencent-Hunyuan/HunyuanWorld-Mirror
Weights:   https://huggingface.co/tencent/HunyuanWorld-Mirror  (~1.5 GB)

The network code is vendored under `world_mirror_utils/` — see its README for
the local modifications. The Gaussian-splatting head is disabled by default
because meshing only needs the depth / pointmap / normal heads, and skipping it
avoids a `gsplat` install.

Usage:
    from models.gen_3d_scene import WorldMirrorModel
    model = WorldMirrorModel(model_path="tencent/HunyuanWorld-Mirror")
    prediction = model.infer([frame_0, frame_1, frame_2])   # list[PIL.Image]
    prediction["points"].shape                              # (3, H, W, 3)
"""
from __future__ import annotations

import gc
from typing import Any, Optional, Sequence

import numpy as np
from PIL import Image

#: Side length the ViT backbone was trained at. Must stay a multiple of 14.
DEFAULT_TARGET_SIZE = 518

#: DINOv2 patch size — every input dimension is rounded to a multiple of this.
PATCH_SIZE = 14


class WorldMirrorModel:
    """
    Dense 3D reconstruction from a handful of images.

    Args:
        model_path: Local directory or HuggingFace repo id holding the weights.
        device: "cuda" or "cpu". CPU works but is very slow.
        target_size: Longest input side fed to the backbone. Larger recovers
            finer geometry at quadratic cost; must be a multiple of 14.
        enable_gs: Keep the Gaussian-splatting branch active. Requires `gsplat`
            and is unnecessary for mesh generation.
    """

    def __init__(
        self,
        model_path: str = "tencent/HunyuanWorld-Mirror",
        device: str = "cuda",
        target_size: int = DEFAULT_TARGET_SIZE,
        enable_gs: bool = False,
    ):
        if target_size % PATCH_SIZE:
            raise ValueError(
                f"target_size must be a multiple of {PATCH_SIZE}, got {target_size}"
            )

        self.model_path = model_path
        self.device = device
        self.target_size = target_size
        self.enable_gs = enable_gs
        self._model = None
        self.load()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Load (or reload) the network onto `self.device`. Idempotent."""
        if self._model is not None:
            return

        import torch

        from models.gen_3d_scene.world_mirror_utils.src.models.models.worldmirror import (
            WorldMirror,
        )

        model = WorldMirror.from_pretrained(self.model_path)
        # Toggled after loading rather than passed to the constructor: the
        # checkpoint always carries gs_head weights, and from_pretrained loads
        # strictly. `_gen_all_preds` reads this flag at inference time.
        model.enable_gs = self.enable_gs
        self._model = model.to(self.device).eval()
        self._torch = torch

    def unload(self) -> None:
        """Release VRAM. Safe to call twice, or before `load()`."""
        if self._model is not None:
            try:
                self._model.to("cpu")
            except Exception:
                pass
            del self._model
            self._model = None
        gc.collect()

        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    # ── inference ─────────────────────────────────────────────────────────────

    def infer(
        self,
        images: Sequence[Image.Image],
        seed: int = 42,
    ) -> dict[str, np.ndarray]:
        """
        Reconstruct a scene from an ordered sequence of views.

        Args:
            images: Frames in capture order, at least one. Any size; they are
                resized to `target_size` and centre-cropped to a common shape.
            seed: Accepted for interface consistency. The network is a
                deterministic feed-forward pass, so this has no effect.

        Returns:
            A dict of numpy arrays, all sharing the frame count S and the
            processed resolution (H, W):

            | key           | shape        | dtype   | meaning                          |
            |---------------|--------------|---------|----------------------------------|
            | `points`      | (S, H, W, 3) | float32 | world-space point per pixel      |
            | `depth`       | (S, H, W)    | float32 | per-view depth, > 0 where valid  |
            | `normals`     | (S, H, W, 3) | float32 | unit world-space normals         |
            | `confidence`  | (S, H, W)    | float32 | pointmap confidence, unbounded ≥ 0 |
            | `colors`      | (S, H, W, 3) | uint8   | the resized input frames         |
            | `camera_poses`| (S, 4, 4)    | float32 | camera-to-world, OpenCV convention |
            | `intrinsics`  | (S, 3, 3)    | float32 | pinhole K for (H, W)             |

            Scene scale is arbitrary but internally consistent across frames.
        """
        if not images:
            raise ValueError("WorldMirrorModel.infer needs at least one image.")

        self.load()
        torch = self._torch

        batch = self._preprocess(images)  # (1, S, 3, H, W) in [0, 1]
        batch = batch.to(self.device)

        use_amp = (
            self.device.startswith("cuda")
            and torch.cuda.is_available()
            and torch.cuda.is_bf16_supported()
        )
        with torch.inference_mode():
            with torch.amp.autocast(
                "cuda", enabled=use_amp, dtype=torch.bfloat16 if use_amp else torch.float32
            ):
                preds = self._model(views={"img": batch}, cond_flags=[0, 0, 0])

        colors = (batch[0].permute(0, 2, 3, 1).clamp(0, 1) * 255).to(torch.uint8)

        def to_numpy(tensor) -> np.ndarray:
            return tensor.detach().float().cpu().numpy()

        return {
            "points": to_numpy(preds["pts3d"][0]),
            "depth": to_numpy(preds["depth"][0, ..., 0]),
            "normals": to_numpy(preds["normals"][0]),
            "confidence": to_numpy(preds["pts3d_conf"][0]),
            "colors": colors.cpu().numpy(),
            "camera_poses": to_numpy(preds["camera_poses"][0]),
            "intrinsics": to_numpy(preds["camera_intrs"][0]),
        }

    # ── preprocessing ─────────────────────────────────────────────────────────

    def _preprocess(self, images: Sequence[Image.Image]):
        """
        Resize frames to a shared, patch-aligned resolution.

        Mirrors upstream's "crop" strategy — width fixed to `target_size`,
        height scaled proportionally, rounded to a multiple of 14 and
        centre-cropped if it overshoots — but takes PIL images rather than file
        paths, per the models/ contract.
        """
        import torch

        tensors = []
        for image in images:
            rgb = self._flatten_alpha(image)
            width = self.target_size
            height = max(
                PATCH_SIZE,
                round(rgb.height * (width / rgb.width) / PATCH_SIZE) * PATCH_SIZE,
            )
            rgb = rgb.resize((width, height), Image.Resampling.BICUBIC)

            array = np.asarray(rgb, dtype=np.float32) / 255.0
            tensor = torch.from_numpy(array).permute(2, 0, 1)  # (3, H, W)
            if height > self.target_size:
                top = (height - self.target_size) // 2
                tensor = tensor[:, top : top + self.target_size, :]
            tensors.append(tensor)

        # Frames of different aspect ratios end up different heights; crop them
        # all to the shortest so they stack.
        min_height = min(t.shape[1] for t in tensors)
        tensors = [t[:, :min_height, :] for t in tensors]

        return torch.stack(tensors).unsqueeze(0)  # (1, S, 3, H, W)

    @staticmethod
    def _flatten_alpha(image: Image.Image) -> Image.Image:
        """Composite RGBA onto white, matching upstream preprocessing."""
        if image.mode == "RGBA":
            background = Image.new("RGBA", image.size, (255, 255, 255, 255))
            image = Image.alpha_composite(background, image)
        return image.convert("RGB")
