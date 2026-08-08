"""
operators/gen_3d_scene/hunyuan_worldplay_operator.py

Gen3DSceneOperator — accepts a loaded geometry model (and an optional video
world model) and processes an input dict into a textured scene mesh.

A two-stage chain; the operator runs whichever stages the task needs:

  1. Frames — generated from a reference image by a world model such as
     `WorldPlayModel` (`.infer(image, prompt, pose, ...) -> list[PIL.Image]`),
     or read from a video / image directory when the footage already exists.
  2. Geometry — a model like `WorldMirrorModel`
     (`.infer(images) -> dict[str, np.ndarray]`) turns those frames into
     pointmaps, depth, normals and cameras, which `funcs/` meshes.

The optional `sky_model` segments sky out before meshing. Outdoor scenes need
it: depth heads cannot express "infinitely far", so sky lands at a finite depth
indistinguishable from real surface and gets meshed into a curtain over
everything.

Every slot is model-agnostic. Only `model` is required — without `video_model`
the task must supply its own frames, and without `sky_model` any sky ends up in
the mesh.

Output layout — two modes, chosen by whether `output_dir` is given:

  * **per-game (default, `output_dir=None`)**::

        test_data/outputs/<game_id>/<run_id>/assets/3d_scene/<task_id>/
            ├── scene.glb        # the deliverable
            ├── scene_points.ply # dense point cloud (intermediate)
            ├── frames/          # frames the mesh was built from
            └── meta.json

  * **flat (legacy, `output_dir="..."`)** — `<output_dir>/<task_id>.glb` plus
    `<output_dir>/<task_id>_points.ply`.

Usage:
    from models.gen_3d_scene import WorldMirrorModel
    from operators.gen_3d_scene.hunyuan_worldplay_operator import Gen3DSceneOperator

    op = Gen3DSceneOperator(model=WorldMirrorModel())

    result = op.run({
        "game_id": "gameA_cyberpunk_shooter",
        "task_id": "alley_001",
        "video_path": "test_data/test_samples/.../flythrough.mp4",
        "frame_stride": 4,
    })
    print(result["glb_path"])
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from PIL import Image

#: Registered task kind for this operator (see pipeline/common/paths.py).
TASK_KIND = "3d_scene"

#: Artifact names used by the per-game layout.
GLB_FILENAME = "scene.glb"
POINTS_FILENAME = "scene_points.ply"
FRAMES_DIRNAME = "frames"

#: Image extensions recognised when a task points at a directory of frames.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp")

#: Video extensions recognised for `video_path`.
VIDEO_SUFFIXES = (".mp4", ".avi", ".mov", ".webm", ".gif")

#: Frames sampled from a video when the task does not say otherwise. WorldMirror
#: attends across all of them at once, so cost grows quickly with frame count
#: while coverage saturates.
DEFAULT_NUM_FRAMES = 8

#: Camera trajectory handed to the world model when a task does not name one.
#: Re-exported rather than restated: the step counts are not free, since the
#: trajectory sets the clip length and WorldPlay only accepts some lengths.
from models.gen_3d_scene.world_play_model import DEFAULT_POSE  # noqa: E402


class Gen3DSceneOperator:
    """
    Operator for scene-scale 3D asset generation.

    Args:
        model: A loaded geometry model with
            `.infer(images: list[PIL.Image]) -> dict[str, np.ndarray]`
            (currently `WorldMirrorModel`).
        video_model: (optional) A loaded world model with
            `.infer(image, prompt, pose, video_length, seed) -> list[PIL.Image]`
            (currently `WorldPlayModel`). Required only for tasks that generate
            a scene from a single reference image.
        sky_model: (optional) A loaded segmenter with
            `.predict(image) -> np.ndarray` returning a HxW sky probability
            (currently `SkySegmentationModel`). Supply it for outdoor scenes:
            the geometry model puts sky at a finite depth, so without a
            segmenter it gets meshed into a curtain over the scene.
        output_dir (str, optional): **Legacy flat mode.** When given, artifacts
            are written as `<output_dir>/<task_id>.glb`. When omitted (default),
            the per-game layout under
            `test_data/outputs/<game_id>/<run_id>/assets/3d_scene/<task_id>/`
            is used instead.
        run_id (str): Groups all artifacts of one generation run. Per-game mode only.
        default_game_id (str, optional): Fallback game project for tasks that
            carry no `game_id` and whose input paths reveal none.
    """

    def __init__(
        self,
        model: Any,
        video_model: Optional[Any] = None,
        sky_model: Optional[Any] = None,
        output_dir: Optional[str] = None,
        run_id: str = "default",
        default_game_id: Optional[str] = None,
    ):
        self.model = model
        self.video_model = video_model
        self.sky_model = sky_model
        self.run_id = run_id
        self.default_game_id = default_game_id
        self.output_dir = Path(output_dir) if output_dir else None
        if self.output_dir is not None:
            self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── input resolution ──────────────────────────────────────────────────────

    @staticmethod
    def _resolve_path(path_like: str) -> Path:
        """Resolve a task path that may be relative to the repo root."""
        path = Path(path_like)
        if not path.is_absolute() and not path.exists():
            repo_root = Path(__file__).resolve().parents[2]
            path = repo_root / path_like
        return path

    def _load_frames(self, inp: dict, num_frames: int, seed: int) -> tuple[list, str]:
        """
        Produce the frames the scene is reconstructed from.

        Returns `(frames, source)` where `source` records which branch ran, for
        `meta.json`.
        """
        if "frames" in inp and inp["frames"]:
            return list(inp["frames"]), "frames"

        if inp.get("frames_dir"):
            directory = self._resolve_path(inp["frames_dir"])
            paths = sorted(
                p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES
            )
            if not paths:
                raise ValueError(f"No images found in frames_dir {directory}")
            return [Image.open(p).convert("RGB") for p in paths], "frames_dir"

        if inp.get("video_path"):
            video = self._resolve_path(inp["video_path"])
            if video.suffix.lower() not in VIDEO_SUFFIXES:
                raise ValueError(f"Unsupported video format: {video}")
            return self._sample_video(video, num_frames), "video_path"

        if inp.get("image_path") or inp.get("image"):
            image = (
                inp["image"]
                if isinstance(inp.get("image"), Image.Image)
                else Image.open(self._resolve_path(inp["image_path"])).convert("RGB")
            )
            if self.video_model is None:
                # Single-view reconstruction. The geometry model handles one frame
                # fine; what it cannot do is invent surfaces the camera never saw,
                # so the mesh covers only what is visible in this image.
                return [image], "image"
            frames = self.video_model.infer(
                image,
                prompt=inp.get("prompt", ""),
                pose=inp.get("pose", DEFAULT_POSE),
                video_length=inp.get("video_length"),
                seed=seed,
            )
            return self._subsample(frames, num_frames), "video_model"

        raise ValueError(
            "Input must contain one of 'frames', 'frames_dir', 'video_path', "
            "or 'image_path' / 'image'."
        )

    @staticmethod
    def _sample_video(path: Path, num_frames: int) -> list:
        """Read a video and return `num_frames` evenly spaced RGB frames."""
        import cv2

        capture = cv2.VideoCapture(str(path))
        try:
            frames = []
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frames.append(Image.fromarray(frame[:, :, ::-1]))
        finally:
            capture.release()

        if not frames:
            raise ValueError(f"Could not read any frames from {path}")
        return Gen3DSceneOperator._subsample(frames, num_frames)

    @staticmethod
    def _subsample(frames: list, num_frames: int) -> list:
        """Evenly spaced subset, always including the first and last frame."""
        if num_frames <= 0 or len(frames) <= num_frames:
            return frames
        import numpy as np

        picks = np.linspace(0, len(frames) - 1, num_frames).round().astype(int)
        return [frames[i] for i in dict.fromkeys(picks.tolist())]

    def _load_sky_masks(self, inp: dict, frames: list, shape: tuple[int, int, int]):
        """
        Resolve the sky mask into `scene_mask`'s (S, H, W) non-sky form.

        The geometry model places sky at a plausible mid-scene depth rather than
        at infinity, so nothing in the prediction marks it out and it survives
        every threshold to be meshed into a stretched curtain over the scene.
        Sky has to be identified from the image instead, by one of, in order:

          * `sky_masks` in the task — an (S, H, W) bool array, True = keep.
          * `sky_mask_dir` in the task — one greyscale image per frame in
            filename order, bright where sky.
          * `self.sky_model` — segmented on the fly. Used whenever the operator
            was given one, unless the task sets `remove_sky` false.

        Returns None when none of those apply, which keeps every pixel.
        """
        import numpy as np

        count, height, width = shape

        if inp.get("sky_masks") is not None:
            masks = np.asarray(inp["sky_masks"], dtype=bool)
            if masks.shape != shape:
                raise ValueError(
                    f"sky_masks has shape {masks.shape}, expected {shape} to match "
                    "the prediction grid"
                )
            return masks

        if inp.get("sky_mask_dir"):
            directory = self._resolve_path(inp["sky_mask_dir"])
            paths = sorted(
                p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES
            )
            if len(paths) != count:
                raise ValueError(
                    f"sky_mask_dir {directory} holds {len(paths)} masks but the "
                    f"prediction has {count} frames"
                )
            loaded = [
                np.asarray(
                    Image.open(p).convert("L").resize((width, height), Image.NEAREST)
                )
                for p in paths
            ]
            return ~(np.stack(loaded) > 127)

        if self.sky_model is None or not inp.get("remove_sky", True):
            return None

        threshold = inp.get("sky_threshold", 0.5)
        predicted = [
            np.asarray(
                Image.fromarray(self.sky_model.predict(frame)).resize(
                    (width, height), Image.BILINEAR
                )
            )
            for frame in frames
        ]
        return np.stack(predicted) < threshold

    # ── output resolution ─────────────────────────────────────────────────────

    def _resolve_out_paths(self, inp: dict, task_id: str) -> tuple[str, Path, Path, Path]:
        """Return `(game_id, task_dir, glb_path, points_path)` for either mode."""
        if self.output_dir is not None:
            game_id = str(inp.get("game_id") or inp.get("game") or "")
            return (
                game_id,
                self.output_dir,
                self.output_dir / f"{task_id}.glb",
                self.output_dir / f"{task_id}_points.ply",
            )

        from pipeline.common import paths

        game_id = paths.infer_game_id(inp, fallback=self.default_game_id)
        task_dir = paths.task_output_dir(game_id, TASK_KIND, task_id, run_id=self.run_id)
        return game_id, task_dir, task_dir / GLB_FILENAME, task_dir / POINTS_FILENAME

    # ── entry point ───────────────────────────────────────────────────────────

    def run(self, inp: dict) -> dict:
        """
        Reconstruct a scene mesh.

        Args:
            inp (dict):
                Frame source — exactly one of:
                - frames (list[PIL.Image]): pre-loaded frames
                - frames_dir (str): directory of images, read in filename order
                - video_path (str): video to sample frames from
                - image_path (str) / image (PIL.Image): reference image. With a
                  `video_model` it is flown through to produce frames; without
                  one it is reconstructed on its own as a single view.

                - game_id (str, optional): inferred from input paths when omitted
                - task_id (str, optional): names the output directory
                - prompt (str, optional): scene description, `video_model` only
                - pose (str, optional): camera trajectory, `video_model` only
                - video_length (int, optional): frames to generate, `video_model`
                  only. Derived from `pose` when omitted, which is normally the
                  only consistent value.
                - num_frames (int, optional): frames fed to the geometry model
                  (default 8)
                - seed (int, optional): default 42
                - remove_sky (bool, optional): segment sky out with the
                  operator's `sky_model`. Default True, ignored when no
                  `sky_model` was supplied.
                - sky_threshold (float, optional): sky probability above which a
                  pixel is dropped (default 0.5)
                - sky_mask_dir (str, optional): pre-computed per-frame greyscale
                  masks, bright where sky. Overrides `sky_model`.
                - sky_masks (np.ndarray, optional): the same thing in memory, as
                  (S, H, W) bool with True meaning keep.
                - max_offplane (float, optional): face-continuity threshold in
                  local pixel footprints; lower cuts more at occlusion
                  boundaries (default 8.0)
                - max_tilt (float, optional): degrees a face's own normal may
                  differ from the normals predicted at its corners, which is
                  what catches the sheets spanning a blurred depth edge
                  (default 75.0)
                - close_holes (int, optional): pinhole-closing radius (default 2)
                - min_component_faces (int, optional): floater removal (default 64)
                - dedup (bool, optional): contribute only newly visible geometry
                  per frame instead of stacking full sheets (default True)
                - frame_stride (int, optional): mesh every Nth frame (default 1)
                - save_frames (bool, optional): write the source frames (default True)
                - save_points (bool, optional): write the point cloud (default True)

        Returns:
            dict:
                - task_id (str)
                - glb_path (str): the scene mesh
                - points_path (str | None): dense point cloud
                - frames_dir (str | None): frames the mesh was built from
                - num_frames (int), num_vertices (int), num_faces (int)
                - elapsed_sec (float)
                - game_id (str), task_kind (str), output_dir (str)
        """
        from operators.gen_3d_scene.funcs.build_scene_mesh import build_scene_mesh
        from operators.gen_3d_scene.funcs.scene_mask import scene_mask

        task_id = inp.get("task_id", f"task_{int(time.time())}")
        seed = inp.get("seed", 42)
        num_frames = inp.get("num_frames", DEFAULT_NUM_FRAMES)
        save_frames = inp.get("save_frames", True)
        save_points = inp.get("save_points", True)

        game_id, task_dir, glb_target, points_target = self._resolve_out_paths(
            inp, task_id
        )

        frames, frame_source = self._load_frames(inp, num_frames, seed)

        t0 = time.time()
        prediction = self.model.infer(frames, seed=seed)
        valid = scene_mask(
            prediction,
            sky_masks=self._load_sky_masks(
                inp, frames, prediction["depth"].shape
            ),
            confidence_percentile=inp.get("confidence_percentile", 0.0),
            min_confidence=inp.get("min_confidence", 1.0),
        )
        mesh = build_scene_mesh(
            prediction,
            valid=valid,
            max_offplane=inp.get("max_offplane", 8.0),
            max_tilt=inp.get("max_tilt", 75.0),
            close_holes=inp.get("close_holes", 2),
            min_component_faces=inp.get("min_component_faces", 64),
            dedup=inp.get("dedup", True),
            frame_stride=inp.get("frame_stride", 1),
        )
        elapsed = time.time() - t0

        glb_target.parent.mkdir(parents=True, exist_ok=True)
        mesh.export(str(glb_target))

        points_path = None
        if save_points:
            points_path = points_target
            self._export_points(prediction, valid, points_path)

        frames_path = None
        if save_frames and self.output_dir is None:
            frames_path = task_dir / FRAMES_DIRNAME
            frames_path.mkdir(parents=True, exist_ok=True)
            for i, frame in enumerate(frames):
                frame.save(str(frames_path / f"frame_{i:04d}.png"))

        out = {
            "task_id": task_id,
            "glb_path": str(glb_target),
            "points_path": str(points_path) if points_path else None,
            "frames_dir": str(frames_path) if frames_path else None,
            "num_frames": len(frames),
            "num_vertices": int(len(mesh.vertices)),
            "num_faces": int(len(mesh.faces)),
            "elapsed_sec": round(elapsed, 2),
            "game_id": game_id,
            "task_kind": TASK_KIND,
            "output_dir": str(task_dir),
        }

        if self.output_dir is None:
            from pipeline.common import paths

            paths.write_task_meta(
                task_dir,
                {
                    **out,
                    "run_id": self.run_id,
                    "frame_source": frame_source,
                    "prompt": inp.get("prompt"),
                    "pose": inp.get("pose"),
                    "seed": seed,
                    "max_offplane": inp.get("max_offplane", 8.0),
                    "max_tilt": inp.get("max_tilt", 75.0),
                    "close_holes": inp.get("close_holes", 2),
                    "dedup": inp.get("dedup", True),
                    "model": type(self.model).__name__,
                    "video_model": (
                        type(self.video_model).__name__ if self.video_model else None
                    ),
                    "sky_model": (
                        type(self.sky_model).__name__ if self.sky_model else None
                    ),
                },
            )

        return out

    def run_batch(self, inputs: list[dict]) -> list[dict]:
        """Run a list of input dicts sequentially and return results."""
        return [self.run(inp) for inp in inputs]

    def eval(self, result: dict, task: dict) -> dict:
        """Score an already-generated scene. Reads artifacts only."""
        from operators.gen_3d_scene.metrics import evaluate

        return evaluate(result, task)

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _export_points(prediction, valid, path: Path) -> None:
        """Write the masked pointmap as a coloured PLY."""
        import trimesh

        points = prediction["points"].reshape(-1, 3)[valid.reshape(-1)]
        colors = prediction["colors"].reshape(-1, 3)[valid.reshape(-1)]
        path.parent.mkdir(parents=True, exist_ok=True)
        trimesh.PointCloud(vertices=points, colors=colors).export(str(path))
