"""
WorldPlayModel — wraps Hunyuan WorldPlay, an action-conditioned video world
model. A reference image, a text prompt and a camera trajectory go in; a
sequence of frames flying through the described world comes out.

Reference: https://github.com/Tencent-Hunyuan/HY-WorldPlay
Weights:   tencent/HY-WorldPlay + tencent/HunyuanVideo-1.5

Prerequisites — this wrapper drives the upstream repo in place rather than
vendoring it, because it carries its own large dependency set (diffusers 0.35,
Qwen2.5-VL, ByT5, SigLIP) that conflicts with the geometry stack. Install it
into its own environment.

  1. Clone HY-WorldPlay and point `$HY_WORLDPLAY_REPO` at it (or pass
     `repo_path=`), then install its `requirements.txt`.
  2. Fetch the weights. Upstream's `download_models.py` takes all of
     tencent/HY-WorldPlay, which is 189 GB of five model variants; only the one
     `action_ckpt` names is ever loaded, so prefer `allow_patterns`. With the
     base model, the Qwen2.5-VL text encoder and the rest it comes to ~90 GB.
  3. The vision encoder is the one awkward piece. Upstream takes it from the
     gated `black-forest-labs/FLUX.1-Redux-dev`, but what the pipeline actually
     wants is plain `google/siglip-so400m-patch14-384`, which is not gated — the
     hardcoded `vision_states_dim=1152` and `vision_num_semantic_tokens=729` are
     that model's hidden size and its 27x27 patch grid. Save its vision tower as
     `<model_path>/vision_encoder/siglip/image_encoder` and the matching
     `SiglipImageProcessor` as `.../feature_extractor`.

480p needs ~40 GB of VRAM, less with `offloading=True`.

The frames it produces are the input to `WorldMirrorModel`, which turns them
into geometry. That two-stage split is why this model returns PIL frames rather
than a video file.

Usage — `action_ckpt` is a Hub reference fetched on first use, but `model_path`
has to be the assembled directory from step 3; see `check_model_path`:

    from models.gen_3d_scene import WorldPlayModel
    model = WorldPlayModel(
        model_path=os.environ["WORLDPLAY_CKPT"],
        action_ckpt="tencent/HY-WorldPlay/ar_distilled_action_model"
                    "/diffusion_pytorch_model.safetensors",
        few_step=True,
    )
    frames = model.infer(image, prompt="a misty pine forest", pose="w-8, right-4")
"""
from __future__ import annotations

import gc
import os
import sys
from importlib import util
from pathlib import Path
from typing import Optional

from PIL import Image

#: Third-party packages the upstream pipeline imports at module scope. Checked
#: up front so a missing dependency reports itself as a setup problem rather
#: than a bare ImportError from somewhere inside the vendor tree.
REQUIRED_MODULES = ("diffusers", "loguru", "omegaconf", "av", "qwen_vl_utils")

#: Camera trajectory in WorldPlay's action DSL: comma-separated `action-steps`
#: where w/s/a/d translate and up/down/left/right rotate. A dolly forward with a
#: turn gives WorldMirror the parallax it needs to triangulate depth.
#:
#: The trajectory *is* the clip length — see `latents_for_pose`. This one sums to
#: 15 steps, so 16 latents and 61 frames, and 16 divides by 4 as the
#: autoregressive rollout requires.
DEFAULT_POSE = "w-8, right-4, w-3"

#: Latents the autoregressive rollout consumes per chunk. Its trajectory must be
#: a whole number of chunks.
AR_CHUNK_LATENTS = 4

#: Schedule length the distilled checkpoints are trained for. `few_step` itself
#: only drops guidance to 1.0; the step count still has to be asked for.
FEW_STEP_STEPS = 4

DEFAULT_NEGATIVE_PROMPT = (
    "blurry, low quality, distorted geometry, flickering, text, watermark"
)


class WorldPlayModel:
    """
    Action-conditioned video generation.

    Args:
        model_path: HunyuanVideo-1.5 base weights (vae / scheduler / transformer
            / text_encoder / vision_encoder).
        device: "cuda". CPU inference is not practical for this model.
        action_ckpt: WorldPlay action-adapter safetensors, as a local path or a
            `<org>/<repo>/<file>` Hub reference. Which one you pick sets
            `model_type`: `ar_model` and `ar_distilled_action_model` are
            autoregressive, `bidirectional_model` is not.
        model_type: "ar" (streaming, long-horizon memory) or "bi" (bidirectional,
            better short clips).
        resolution: "480p" — the only released transformer version.
        repo_path: HY-WorldPlay checkout. Defaults to `$HY_WORLDPLAY_REPO`.
        offloading: Stream modules between CPU and GPU to fit in less VRAM.
        few_step: Pair with a distilled `action_ckpt`. Upstream reads this only
            as "drop guidance to 1.0"; `infer` also takes it as the cue to use
            the short schedule those checkpoints are trained for.
    """

    def __init__(
        self,
        model_path: str,
        device: str = "cuda",
        action_ckpt: Optional[str] = None,
        model_type: str = "ar",
        resolution: str = "480p",
        repo_path: Optional[str] = None,
        offloading: bool = False,
        few_step: bool = False,
    ):
        if model_type not in ("ar", "bi"):
            raise ValueError(f"model_type must be 'ar' or 'bi', got {model_type!r}")

        self.model_path = self.check_model_path(model_path)
        self.device = device
        self.action_ckpt = self.resolve_action_ckpt(action_ckpt)
        self.model_type = model_type
        self.resolution = resolution
        self.repo_path = self.resolve_repo(repo_path)
        self.offloading = offloading
        self.few_step = few_step
        self._pipe = None
        self.load()

    @staticmethod
    def check_model_path(model_path: str) -> str:
        """
        `model_path` has to be an assembled local directory, not a Hub id.

        Upstream's `create_pipeline` only skips diffusers' `download()` when
        handed a directory, and the published `tencent/HunyuanVideo-1.5` repo
        carries no `model_index.json` for `download()` to read. It also has no
        usable `vision_encoder/`, since the one upstream expects comes from a
        gated repo and has to be substituted. So the layout is assembled once,
        locally, and pointed at from here.
        """
        if Path(model_path).is_dir():
            return model_path
        raise NotADirectoryError(
            f"WorldPlay model_path {model_path!r} is not a directory. Unlike the "
            "geometry and sky models, this one cannot be a Hub id: the published "
            "repo has no model_index.json and no ungated vision encoder. Fetch "
            "tencent/HunyuanVideo-1.5 plus google/siglip-so400m-patch14-384 and "
            "assemble them as described in this module's docstring, then set "
            "$WORLDPLAY_CKPT to that directory."
        )

    @staticmethod
    def resolve_action_ckpt(action_ckpt: Optional[str]) -> Optional[str]:
        """
        Accept either a local safetensors path or a Hub reference, return a path.

        A Hub reference is `<org>/<repo>/<path/inside/repo>`, e.g.
        `tencent/HY-WorldPlay/ar_distilled_action_model/diffusion_pytorch_model.safetensors`.
        Only that one file is fetched — the full repo is 189 GB of five variants
        and just the named adapter is ever loaded.
        """
        if not action_ckpt or Path(action_ckpt).exists():
            return action_ckpt

        parts = action_ckpt.split("/")
        if len(parts) < 3:
            raise FileNotFoundError(
                f"action_ckpt {action_ckpt!r} is neither an existing file nor a Hub "
                "reference of the form <org>/<repo>/<file>."
            )

        from huggingface_hub import hf_hub_download

        return hf_hub_download(repo_id="/".join(parts[:2]), filename="/".join(parts[2:]))

    @staticmethod
    def resolve_repo(repo_path: Optional[str] = None) -> Path:
        """
        Locate the HY-WorldPlay checkout, failing with instructions.

        Public so a caller can check this prerequisite before committing to the
        rest of the chain — the geometry model takes some seconds to load, and
        there is no point paying that only to discover the source is missing.
        """
        candidate = repo_path or os.environ.get("HY_WORLDPLAY_REPO")
        if not candidate:
            raise RuntimeError(
                "WorldPlayModel needs the HY-WorldPlay source. Clone "
                "https://github.com/Tencent-Hunyuan/HY-WorldPlay and either set "
                "`export HY_WORLDPLAY_REPO=../HY-WorldPlay` or pass "
                "`repo_path=` to the constructor."
            )
        path = Path(candidate).expanduser().resolve()
        if not (path / "hyvideo" / "generate.py").is_file():
            raise RuntimeError(
                f"{path} does not look like an HY-WorldPlay checkout "
                "(expected hyvideo/generate.py)."
            )
        return path

    @classmethod
    def preflight(cls, repo_path: Optional[str] = None) -> Path:
        """
        Check everything that can be checked without loading weights.

        Both failures here are configuration, not runtime, so they are worth
        surfacing before the geometry model spends time loading.
        """
        path = cls.resolve_repo(repo_path)

        missing = [name for name in REQUIRED_MODULES if not util.find_spec(name)]
        if missing:
            raise RuntimeError(
                f"WorldPlay needs {', '.join(missing)}, which are not installed. "
                f"Run `pip install -r {path / 'requirements.txt'}`. Note that it "
                "pins transformers and huggingface-hub versions that conflict "
                "with the geometry stack, so install it into its own environment "
                "and generate the frames as a separate step — the operator "
                "accepts them via `video_path` or `frames_dir`."
            )
        return path

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def _add_repo_to_path(self) -> None:
        """Make `hyvideo` importable. Cheap, and does not touch weights."""
        if str(self.repo_path) not in sys.path:
            sys.path.insert(0, str(self.repo_path))

    @staticmethod
    def _init_infer_state() -> None:
        """
        Populate the global upstream reads for attention and quantisation config.

        `create_pipeline` fetches this through `get_infer_state()` and does not
        check the result, so leaving it unset fails with an `AttributeError` on
        `NoneType` — but only after the transformer and the 34 GB action
        checkpoint have finished loading.

        Upstream only ever sets it from parsed CLI arguments, so the values here
        mirror that parser's defaults: no SageAttention, no fp8 quantisation, no
        torch.compile, no VAE parallelism. Anything else needs kernels we do not
        install, and the quantisation paths want a calibrated model.
        """
        from types import SimpleNamespace

        from hyvideo.commons.infer_state import initialize_infer_state

        initialize_infer_state(SimpleNamespace(
            use_sageattn=False,
            sage_blocks_range="0-53",
            enable_torch_compile=False,
            use_fp8_gemm=False,
            quant_type="fp8-per-block",
            include_patterns="double_blocks",
            use_vae_parallel=False,
        ))

    def load(self) -> None:
        """Build the diffusion pipeline. Idempotent."""
        if self._pipe is not None:
            return

        self.preflight(str(self.repo_path))
        self._add_repo_to_path()

        import torch
        from hyvideo.pipelines.worldplay_video_pipeline import (
            HunyuanVideo_1_5_Pipeline,
        )

        self._init_infer_state()

        self._pipe = HunyuanVideo_1_5_Pipeline.create_pipeline(
            pretrained_model_name_or_path=self.model_path,
            transformer_version=f"{self.resolution}_i2v",
            enable_offloading=self.offloading,
            create_sr_pipeline=False,
            force_sparse_attn=False,
            transformer_dtype=torch.bfloat16,
            action_ckpt=self.action_ckpt,
        )
        self._torch = torch

    def unload(self) -> None:
        """Release VRAM. Safe to call twice, or before `load()`."""
        if self._pipe is not None:
            try:
                self._pipe.to("cpu")
            except Exception:
                pass
            del self._pipe
            self._pipe = None
        gc.collect()

        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()

    # ── inference ─────────────────────────────────────────────────────────────

    def latents_for_pose(self, pose) -> int:
        """
        Count the latents a trajectory describes.

        The action DSL spends one latent per step plus one for the starting
        camera, so `"w-8, right-4, w-3"` is 16. Upstream turns that into
        `latents * 4 - 3` frames — the VAE packs four frames per latent, with
        the first latent covering a single frame.

        Accepts the same inputs as upstream's `pose_to_input`: a DSL string, a
        path to a pose JSON, or an already-parsed dict.
        """
        import json

        if isinstance(pose, dict):
            return len(pose)

        if isinstance(pose, str) and pose.endswith(".json"):
            with open(pose, "r") as handle:
                return len(json.load(handle))

        self._add_repo_to_path()
        from hyvideo.generate import pose_string_to_json

        return len(pose_string_to_json(pose))

    def infer(
        self,
        image: Image.Image,
        prompt: str,
        pose: str = DEFAULT_POSE,
        video_length: Optional[int] = None,
        seed: int = 42,
        num_inference_steps: Optional[int] = None,
        negative_prompt: str = DEFAULT_NEGATIVE_PROMPT,
        rewrite_prompt: bool = False,
        aspect_ratio: Optional[str] = None,
    ) -> list[Image.Image]:
        """
        Fly a camera through the world implied by `image` and `prompt`.

        Args:
            image: Reference frame the world is grown from.
            prompt: Scene description.
            pose: Camera trajectory in the action DSL, e.g. "w-8, right-4".
                This sets the clip length; see `latents_for_pose`.
            video_length: Frame count. Leave as None to take it from `pose`,
                which is the only value that can work — the trajectory carries
                one keyframe per latent and upstream asserts the two agree.
                Pass it only to assert the length you are expecting.
            seed: Sampling seed; a fixed seed reproduces the clip.
            num_inference_steps: Denoising steps. Defaults to 4 under
                `few_step` and 50 otherwise — the distilled checkpoint is
                trained for the short schedule, and running it for 50 steps
                costs twelve times as much for no gain.
            negative_prompt: Steers sampling away from these attributes.
            rewrite_prompt: Run upstream's LLM prompt rewriter first. Improves
                quality but needs the text encoder loaded and is slow.
            aspect_ratio: Output framing as `"width:height"`. Defaults to the
                reference image's own ratio, so the generated frames keep its
                framing instead of being letterboxed or cropped to 16:9.

        Returns:
            `video_length` RGB PIL frames in capture order.
        """
        if num_inference_steps is None:
            num_inference_steps = FEW_STEP_STEPS if self.few_step else 50

        latent_count = self.latents_for_pose(pose)
        implied_length = latent_count * 4 - 3

        if video_length is None:
            video_length = implied_length
        elif video_length != implied_length:
            raise ValueError(
                f"pose {pose!r} describes {latent_count} latents, which is "
                f"{implied_length} frames, but video_length={video_length} was "
                "requested. The trajectory sets the length — either drop "
                "video_length or lengthen the pose."
            )

        if self.model_type == "ar" and latent_count % AR_CHUNK_LATENTS:
            raise ValueError(
                f"pose {pose!r} gives {latent_count} latents; the autoregressive "
                f"rollout consumes {AR_CHUNK_LATENTS} at a time and needs a "
                "whole number of chunks. Adjust the step counts so they sum to "
                f"a multiple of {AR_CHUNK_LATENTS} minus one — 15 steps gives 16 "
                "latents and 61 frames, 31 gives 32 and 125."
            )

        self.load()
        torch = self._torch

        from hyvideo.generate import pose_to_input

        viewmats, intrinsics, action = pose_to_input(pose, latent_count)

        if aspect_ratio is None:
            width, height = image.size
            aspect_ratio = f"{width}:{height}"

        with torch.inference_mode():
            out = self._pipe(
                prompt=prompt,
                negative_prompt=negative_prompt,
                reference_image=image,
                aspect_ratio=aspect_ratio,
                video_length=video_length,
                seed=seed,
                num_inference_steps=num_inference_steps,
                output_type="pt",
                prompt_rewrite=rewrite_prompt,
                enable_sr=False,
                viewmats=viewmats.unsqueeze(0),
                Ks=intrinsics.unsqueeze(0),
                action=action.unsqueeze(0),
                few_step=self.few_step,
                chunk_latent_frames=4 if self.model_type == "ar" else 16,
                model_type=self.model_type,
            )

        return self._to_frames(out.videos)

    def _to_frames(self, video) -> list[Image.Image]:
        """Convert the pipeline's (1, C, F, H, W) float tensor to PIL frames."""
        if video.ndim == 5:
            video = video[0]
        frames = (video * 255).clamp(0, 255).to(device="cpu", dtype=self._torch.uint8)
        frames = frames.permute(1, 2, 3, 0).numpy()  # (F, H, W, C)
        return [Image.fromarray(frame) for frame in frames]
