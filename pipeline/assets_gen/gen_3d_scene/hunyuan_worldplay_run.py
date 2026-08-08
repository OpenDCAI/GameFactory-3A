"""
pipeline/assets_gen/gen_3d_scene/hunyuan_worldplay_run.py

3D scene generation demo runner.

Loads a geometry model (and optionally a video world model), injects them into
Gen3DSceneOperator, reads tasks from
test_data/test_samples/3D_scene_gen_collect.jsonl (or a single game's
scene_tasks.jsonl), and writes scene meshes grouped per game project:

    test_data/outputs/<game_id>/<run_id>/assets/3d_scene/<task_id>/scene.glb

Two backends fill the model slot (model_require.md R6):

    --backend worldmirror   frames → mesh. Real footage, a rendered flythrough
                            or a pre-generated video.        (default, ~1.5 GB)
    --backend worldplay     image + prompt → frames → mesh. Adds Hunyuan
                            WorldPlay in front, so a scene can be invented from
                            one reference image.                      (~90 GB)

The worldplay backend needs its own environment and a checkpoint layout that
upstream's `download_models.py` does not quite produce here; see
`models/gen_3d_scene/world_play_model.py` for what to fetch and why. Pair
`--few-step` with a distilled `--action-ckpt`: it is roughly twelve times
faster and is what those weights are trained for. Add `--offloading` when the
card has less than ~40 GB free.

Usage, with RUN=pipeline/assets_gen/gen_3d_scene/hunyuan_worldplay_run.py:

    python $RUN                                  # every task in the default jsonl
    python $RUN --game gameA_cyberpunk_shooter   # one game's scene_tasks.jsonl
    python $RUN --frames-dir path/to/frames/     # single demo from frames
    python $RUN --video fly.mp4 --task-id my_scene --run-id auto

    # Invent a scene from one image. Weights are Hub ids, pulled on first use;
    # only the HY-WorldPlay source has to be checked out by hand.
    export HY_WORLDPLAY_REPO=../HY-WorldPlay
    python $RUN --backend worldplay --few-step --offloading \
        --image ref.png --prompt "a rain-slicked neon alley"

    python $RUN --max-offplane 4 --close-holes 3  # cut harder, close bigger holes
    python $RUN --no-sky-removal                  # interior scene, no sky
    python $RUN --no-dedup                        # upstream's stacked sheets
    python $RUN --out-dir outputs/3d_scene        # legacy flat output
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ── Make repo root importable regardless of CWD ───────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# ──────────────────────────────────────────────────────────────────────────────

from models.gen_3d_scene.world_play_model import DEFAULT_POSE  # noqa: E402
from pipeline.common import paths  # noqa: E402

#: Registered task kind — keys into paths.TASK_* tables.
TASK_KIND = "3d_scene"

#: Geometry weights. HF repo id — downloads on first run.
#: Override via env var:  export WORLDMIRROR_CKPT=/your/local/path
DEFAULT_CKPT = "tencent/HunyuanWorld-Mirror"
DEFAULT_TASKS = paths.collect_jsonl(TASK_KIND)

#: HunyuanVideo-1.5 base weights, `worldplay` backend only. Unlike every other
#: checkpoint here this cannot be a Hub id — it must be a locally assembled
#: directory. See `WorldPlayModel.check_model_path` for why.
DEFAULT_WORLDPLAY_CKPT = os.environ.get("WORLDPLAY_CKPT", "")

#: WorldPlay action adapters, as `<org>/<repo>/<file>` Hub references. Only the
#: named file is fetched; the full repo is 189 GB of five variants. `--few-step`
#: picks the distilled one, which is what that schedule is trained for.
ACTION_CKPTS = {
    False: "tencent/HY-WorldPlay/ar_model/diffusion_pytorch_model.safetensors",
    True: ("tencent/HY-WorldPlay/ar_distilled_action_model"
           "/diffusion_pytorch_model.safetensors"),
}

#: backend -> (default ckpt, env var holding an override).
BACKENDS: dict[str, tuple[str, str]] = {
    "worldmirror": (DEFAULT_CKPT, "WORLDMIRROR_CKPT"),
    "worldplay": (DEFAULT_CKPT, "WORLDMIRROR_CKPT"),
}


def resolve_ckpt(backend: str, cli_value: str | None) -> str:
    """Checkpoint precedence: CLI flag > env var > backend default (R3.1)."""
    default, env_var = BACKENDS[backend]
    return cli_value or os.environ.get(env_var) or default


def load_model(
    ckpt: str,
    device: str = "cuda",
    backend: str = "worldmirror",
    target_size: int = 518,
    **backend_kwargs,
):
    """
    Load the geometry model, and the video model when the backend needs one (R2.1).

    Args:
        ckpt: Weights path / HF repo id for the geometry model.
        device: "cuda" or "cpu". CPU works but is impractically slow.
        backend: One of `BACKENDS`.
        target_size: Geometry model input resolution; multiple of 14.
        **backend_kwargs: `worldplay` only — `worldplay_ckpt`, `action_ckpt`,
            `repo_path`, `model_type`, `offloading`.

    Returns:
        `(model, video_model)`. `video_model` is None for `worldmirror`.
    """
    from models.gen_3d_scene import WorldMirrorModel

    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}. Known: {sorted(BACKENDS)}")

    if backend == "worldplay":
        # Before the geometry weights, so a missing checkout does not cost a load.
        from models.gen_3d_scene import WorldPlayModel

        WorldPlayModel.preflight(backend_kwargs.get("repo_path"))

    print(f"[run] Loading WorldMirrorModel from: {ckpt}")
    model = WorldMirrorModel(model_path=ckpt, device=device, target_size=target_size)

    if backend != "worldplay":
        return model, None

    worldplay_ckpt = backend_kwargs.pop("worldplay_ckpt", DEFAULT_WORLDPLAY_CKPT)
    print(f"[run] Loading WorldPlayModel from: {worldplay_ckpt}")
    return model, WorldPlayModel(
        model_path=worldplay_ckpt, device=device, **backend_kwargs
    )


def load_sky_model(ckpt: str | None = None, device: str = "cpu"):
    """
    Load the sky segmenter (~40 MB ONNX, downloads on first run).

    Outdoor scenes need it. The geometry model cannot represent "infinitely
    far", so it puts sky at a finite depth that no threshold can separate from
    real surface, and the mesher turns it into a curtain over the scene.
    """
    from models.tools.segmentation import SkySegmentationModel

    print("[run] Loading SkySegmentationModel")
    return SkySegmentationModel(model_path=ckpt, device=device)


def make_operator(
    model,
    video_model=None,
    sky_model=None,
    output_dir: str | None = None,
    run_id: str = paths.DEFAULT_RUN_ID,
    default_game_id: str | None = None,
):
    """
    Build the operator.

    Leave `output_dir` unset for the per-game layout. Passing it keeps the legacy
    flat `<output_dir>/<task_id>.glb` behaviour.
    """
    from operators.gen_3d_scene.hunyuan_worldplay_operator import Gen3DSceneOperator

    return Gen3DSceneOperator(
        model=model,
        video_model=video_model,
        sky_model=sky_model,
        output_dir=output_dir,
        run_id=run_id,
        default_game_id=default_game_id,
    )


def generate(inp: dict, operator) -> dict:
    """Thin wrapper so eval.py can import and reuse."""
    return operator.run(inp)


def run_from_jsonl(
    tasks_path: str,
    operator,
    game_filter: str | None = None,
    defaults: dict | None = None,
) -> list[dict]:
    """
    Iterate a jsonl file and run each task, optionally restricted to one game.

    `defaults` supplies mesh settings from the CLI. A task that names the same
    key overrides it, so a jsonl can pin the parameters a particular scene needs.
    """
    results = []
    for task, game_id in paths.iter_tasks(tasks_path, game_filter=game_filter):
        task = {**(defaults or {}), **task}
        source = (
            task.get("video_path") or task.get("frames_dir") or task.get("image_path")
        )
        print(f"[run] game={game_id}  task_id={task.get('task_id', '?')}  source={source}")
        result = generate(task, operator)
        print(
            f"       → glb={result['glb_path']}  "
            f"{result['num_faces']} faces from {result['num_frames']} frames  "
            f"({result['elapsed_sec']}s)"
        )
        results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser(description="Run 3D scene generation.")
    parser.add_argument("--backend", default=os.environ.get("AAAGF_SCENE_BACKEND", "worldmirror"),
                        choices=sorted(BACKENDS),
                        help="worldmirror: frames → mesh. worldplay: also invent the frames")
    parser.add_argument("--ckpt", default=None,
                        help="Geometry weights. Precedence: this flag > $WORLDMIRROR_CKPT > default")
    parser.add_argument("--worldplay-ckpt", default=DEFAULT_WORLDPLAY_CKPT,
                        help="Assembled HunyuanVideo-1.5 directory, or $WORLDPLAY_CKPT. "
                             "Must be local; see WorldPlayModel.check_model_path")
    parser.add_argument("--action-ckpt", default=os.environ.get("WORLDPLAY_ACTION_CKPT"),
                        help="WorldPlay action adapter, as a local safetensors path "
                             "or an <org>/<repo>/<file> Hub reference. Defaults to "
                             "the adapter matching --few-step")
    parser.add_argument("--repo-path", default=os.environ.get("HY_WORLDPLAY_REPO"),
                        help="HY-WorldPlay checkout (worldplay backend)")
    parser.add_argument("--model-type", default="ar", choices=["ar", "bi"],
                        help="WorldPlay rollout mode (worldplay backend)")
    parser.add_argument("--offloading", action="store_true",
                        help="Stream WorldPlay modules to CPU to fit in less VRAM")
    parser.add_argument("--few-step", action="store_true",
                        help="Use the 4-step distilled schedule, ~12x faster. Only valid "
                             "when --action-ckpt names a distilled adapter")
    parser.add_argument("--game", default=None,
                        help=f"Game project id. Known: {paths.list_games() or '<none>'}")
    parser.add_argument("--tasks", default=None,
                        help="Explicit jsonl path (overrides the --game task-list lookup)")
    parser.add_argument("--run-id", default=paths.DEFAULT_RUN_ID,
                        help="Run directory name; 'auto' for a timestamp")
    parser.add_argument("--out-dir", default=None,
                        help="Legacy flat output dir; bypasses the per-game layout")
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--target-size", type=int, default=518,
                        help="Geometry model input resolution; must be a multiple of 14")
    # Mesh continuity
    parser.add_argument("--max-offplane", type=float, default=8.0,
                        help="Cull a face when an edge endpoint sits this many local "
                             "pixel footprints off the tangent plane at the other end. "
                             "Lower cuts more at occlusion boundaries")
    parser.add_argument("--max-tilt", type=float, default=75.0,
                        help="Cull a face whose own normal differs by more than "
                             "this many degrees from the normals predicted at its "
                             "corners. Catches sheets spanning a blurred depth edge")
    parser.add_argument("--close-holes", type=int, default=2,
                        help="Radius of the pinhole-closing pass; 0 disables it")
    parser.add_argument("--min-component-faces", type=int, default=64,
                        help="Drop mesh fragments below this face count")
    parser.add_argument("--no-dedup", action="store_true",
                        help="Keep a full sheet per frame instead of merging "
                             "incrementally — reproduces upstream's overlapping output")
    parser.add_argument("--frame-stride", type=int, default=1,
                        help="Mesh every Nth frame")
    parser.add_argument("--no-sky-removal", action="store_true",
                        help="Keep sky in the mesh. It is predicted at a finite "
                             "depth, so it becomes a curtain over the scene")
    parser.add_argument("--skyseg-ckpt", default=os.environ.get("SKYSEG_CKPT"),
                        help="Local skyseg.onnx; downloaded from the Hub if unset")
    parser.add_argument("--sky-threshold", type=float, default=0.5,
                        help="Sky probability above which a pixel is dropped")
    # Single-demo mode
    parser.add_argument("--video", default=None, help="Path to a single video (demo mode)")
    parser.add_argument("--frames-dir", default=None, help="Directory of frames (demo mode)")
    parser.add_argument("--image", default=None,
                        help="Reference image (demo mode; needs --backend worldplay)")
    parser.add_argument("--prompt", default="", help="Scene description (worldplay backend)")
    parser.add_argument("--pose", default=DEFAULT_POSE,
                        help="Camera trajectory in WorldPlay's action DSL. It also "
                             "sets the clip length: steps + 1 latents, "
                             "latents * 4 - 3 frames, latents a multiple of 4")
    parser.add_argument("--num-frames", type=int, default=8,
                        help="Frames fed to the geometry model")
    parser.add_argument("--task-id", default="demo")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    run_id = paths.new_run_id() if args.run_id == "auto" else args.run_id
    ckpt = resolve_ckpt(args.backend, args.ckpt)

    backend_kwargs = {}
    if args.backend == "worldplay":
        backend_kwargs = {
            "worldplay_ckpt": args.worldplay_ckpt,
            "action_ckpt": args.action_ckpt or ACTION_CKPTS[args.few_step],
            "repo_path": args.repo_path,
            "model_type": args.model_type,
            "offloading": args.offloading,
            "few_step": args.few_step,
        }

    model, video_model = load_model(ckpt, device=args.device, backend=args.backend,
                                    target_size=args.target_size, **backend_kwargs)
    sky_model = None if args.no_sky_removal else load_sky_model(args.skyseg_ckpt)
    operator = make_operator(model, video_model=video_model, sky_model=sky_model,
                             output_dir=args.out_dir, run_id=run_id,
                             default_game_id=args.game)

    mesh_options = {
        "max_offplane": args.max_offplane,
        "max_tilt": args.max_tilt,
        "close_holes": args.close_holes,
        "min_component_faces": args.min_component_faces,
        "dedup": not args.no_dedup,
        "frame_stride": args.frame_stride,
        "num_frames": args.num_frames,
        "sky_threshold": args.sky_threshold,
    }

    if args.video or args.frames_dir or args.image:
        # Single demo
        task = {"task_id": args.task_id, "seed": args.seed, "game_id": args.game,
                **mesh_options}
        if args.video:
            task["video_path"] = args.video
        elif args.frames_dir:
            task["frames_dir"] = args.frames_dir
        else:
            task.update(image_path=args.image, prompt=args.prompt, pose=args.pose)
        result = generate(task, operator)
        print(f"[run] Done: {result}")
        return

    # Batch from jsonl
    tasks_path = paths.resolve_tasks_path(TASK_KIND, args.tasks, args.game)
    print(f"[run] run_id={run_id}  tasks={paths.rel_to_repo(tasks_path)}")
    results = run_from_jsonl(str(tasks_path), operator, game_filter=args.game,
                             defaults=mesh_options)
    if not results:
        print("[run] No matching tasks — nothing to do.")
        return

    if args.out_dir:
        # Legacy flat mode: keep the single summary next to the artifacts.
        summary_path = Path(args.out_dir) / "results_summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        with open(summary_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"[run] Wrote summary → {summary_path}")
    else:
        for p in paths.write_results_summary(results, TASK_KIND, run_id):
            print(f"[run] Wrote summary → {paths.rel_to_repo(p)}")


if __name__ == "__main__":
    main()
