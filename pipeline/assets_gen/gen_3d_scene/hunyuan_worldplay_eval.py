"""
pipeline/assets_gen/gen_3d_scene/hunyuan_worldplay_eval.py

Score scene meshes that a previous `hunyuan_worldplay_run.py` produced. Generates
nothing and loads no model — it reads `<run>/3d_scene_results_summary.json`,
measures each mesh, and writes the scores next to it.

The metrics quantify mesh continuity (`boundary_edge_ratio`,
`largest_component_face_ratio`, `stretch_p99`), so this is the tool for checking
whether a change to `--max-stretch` or `--close-holes` actually helped instead of
judging it by eye. See `operators/gen_3d_scene/metrics/`.

`--self-check` runs the same meshing over synthetic depth maps whose correct
output is known, and compares it against upstream HunyuanWorldMirror's pixel
culling. That needs no weights and no GPU, so it is the quick way to confirm the
continuity work still holds after a change.

Usage, with EVAL=pipeline/assets_gen/gen_3d_scene/hunyuan_worldplay_eval.py:

    python $EVAL                    # score the default run of every game
    python $EVAL --self-check       # synthetic meshing check, no weights needed
    python $EVAL --game gameA_cyberpunk_shooter --run-id 20260806_120000
    python $EVAL --game gameA_cyberpunk_shooter --run-id baseline --compare-to tuned
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path

# ── Make repo root importable regardless of CWD ───────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# ──────────────────────────────────────────────────────────────────────────────

import numpy as np  # noqa: E402

from pipeline.common import paths  # noqa: E402

TASK_KIND = "3d_scene"

#: Metrics where a smaller number is better, used when printing a comparison.
LOWER_IS_BETTER = ("boundary_edge_ratio", "num_components", "stretch_p99")

#: Focal length of the synthetic pinhole camera used by `--self-check`, in pixels.
FOCAL = 100.0


def load_results(game_id: str, run_id: str) -> list[dict]:
    """Read the summary `run.py` wrote for one game and run."""
    summary = paths.results_summary_path(game_id, TASK_KIND, run_id)
    if not summary.is_file():
        raise FileNotFoundError(
            f"No results for game={game_id!r} run_id={run_id!r} at {summary}. "
            "Run pipeline/assets_gen/gen_3d_scene/hunyuan_worldplay_run.py first."
        )
    return json.loads(summary.read_text(encoding="utf-8"))


def evaluate_results(results: list[dict], tasks: dict[str, dict] | None = None) -> list[dict]:
    """Score every result, pairing it with its task dict when one is available."""
    from operators.gen_3d_scene.metrics import evaluate

    scored = []
    for result in results:
        task_id = result.get("task_id", "?")
        task = (tasks or {}).get(task_id, {})
        scored.append({"task_id": task_id, **evaluate(result, task)})
    return scored


def write_scores(scored: list[dict], game_id: str, run_id: str) -> Path:
    """Write per-task metrics and an aggregate to the run's eval directory."""
    for row in scored:
        eval_dir = paths.eval_output_dir(game_id, TASK_KIND, row["task_id"], run_id)
        (eval_dir / "metrics.json").write_text(
            json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    summary_path = paths.eval_summary_path(game_id, run_id)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {"task_kind": TASK_KIND, "per_task": scored, "mean": _mean(scored)},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return summary_path


def _mean(scored: list[dict]) -> dict[str, float]:
    """Average each numeric metric across tasks."""
    numeric = [
        key
        for key in (scored[0] if scored else {})
        if key != "task_id" and isinstance(scored[0][key], (int, float))
    ]
    return {
        key: round(sum(row[key] for row in scored) / len(scored), 6) for key in numeric
    }


# ── synthetic self-check ──────────────────────────────────────────────────────


def make_scene(height=64, width=64, pinholes=40, seed=0) -> dict[str, np.ndarray]:
    """
    Two fronto-parallel walls with a hard occlusion boundary down the middle.

    The left half sits at depth 2 and the right at depth 6, so the seam is a
    genuine foreground/background straddle that no face may cross. The dropouts
    stand in for the low-confidence speckle upstream's percentile filter deletes.
    """
    depth = np.full((height, width), 2.0)
    depth[:, width // 2:] = 6.0

    normals = np.zeros((height, width, 3))
    normals[..., 2] = -1.0

    valid = np.ones((height, width), dtype=bool)
    rng = np.random.default_rng(seed)
    for _ in range(pinholes):
        row = int(rng.integers(2, height - 2))
        valid[row, int(rng.integers(2, width // 2 - 2))] = False

    return _unproject(depth, normals, valid)


def make_ground_plane(height=64, width=64) -> dict[str, np.ndarray]:
    """
    A stone road receding to the horizon.

    Depth follows `focal * camera_height / (row - horizon)`, so neighbouring
    pixels near the horizon are dozens of footprints apart in 3D while still
    lying on one continuous surface. This is the case an edge-length or
    relative-depth threshold cannot get right.

    Only rows below the horizon are valid: clamping the sky to a finite depth
    would build a wall carrying the plane's normals, which is the very artefact
    the meshing is meant to reject.
    """
    camera_height, horizon = 1.5, (height - 1) / 2
    rows, _ = np.mgrid[0:height, 0:width]
    below = rows - horizon
    on_ground = below >= 2.0  # stop short of the horizon's singularity
    depth = FOCAL * camera_height / np.where(on_ground, below, 1.0)

    normals = np.zeros((height, width, 3))
    normals[..., 1] = 1.0

    return _unproject(depth, normals, on_ground)


def _unproject(depth, normals, valid) -> dict[str, np.ndarray]:
    height, width = depth.shape
    rows, columns = np.mgrid[0:height, 0:width]
    points = np.stack([
        (columns - (width - 1) / 2) * depth / FOCAL,
        (rows - (height - 1) / 2) * depth / FOCAL,
        depth,
    ], axis=-1)
    return {
        "points": points.astype(np.float32),
        "depth": depth.astype(np.float32),
        "normals": normals.astype(np.float32),
        "colors": np.full((height, width, 3), 180, dtype=np.uint8),
        "valid": valid,
    }


def upstream_valid_mask(scene: dict[str, np.ndarray]) -> np.ndarray:
    """
    Reproduce `HunyuanWorldMirror/infer.py::create_filter_mask`'s pixel culling.

    Only the parts that delete pixels are modelled: the 10% confidence
    percentile and the 3x3-dilated depth edge.
    """
    depth, valid = scene["depth"], scene["valid"].copy()

    # No real confidence map here, so stand in with a random decile.
    valid &= np.random.default_rng(1).random(depth.shape) >= 0.10

    padded = np.pad(depth, 1, mode="edge")
    windows = np.stack([padded[r:r + depth.shape[0], c:c + depth.shape[1]]
                        for r in range(3) for c in range(3)])
    spread = windows.max(axis=0) - windows.min(axis=0)
    return valid & ~(spread / depth > 0.03)


def upstream_faces(valid: np.ndarray) -> int:
    """Count faces under the all-four-corners rule of `create_image_mesh`."""
    quads = valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, 1:] & valid[1:, :-1]
    return int(quads.sum()) * 2


def boundary_edge_ratio(mesh) -> float:
    """Share of edges used by exactly one face — every hole shows up here."""
    faces = np.asarray(mesh.faces)
    edges = np.sort(
        np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1
    )
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return float((counts == 1).sum() / len(counts))


def _mesh(scene: dict[str, np.ndarray], **kwargs):
    from operators.gen_3d_scene.funcs.points_to_mesh import points_to_mesh

    return points_to_mesh(
        scene["points"], scene["colors"], valid=scene["valid"],
        depth=scene["depth"], focal=FOCAL, **kwargs,
    )


def self_check() -> dict[str, float]:
    """
    Mesh the synthetic scenes and report the numbers the continuity work targets.

    Returns the measurements so a caller can assert on them; `main` just prints.
    """
    walls = make_scene(pinholes=40)
    ours = _mesh(walls, normals=walls["normals"])
    theirs = upstream_faces(upstream_valid_mask(walls))

    depths = ours.vertices[:, 2][ours.faces]
    straddle = float((depths.max(axis=1) - depths.min(axis=1)).max())

    road = make_ground_plane()
    valid = road["valid"]
    every_quad = upstream_faces(valid)
    tangent = _mesh(road, normals=road["normals"])
    edge_length = _mesh(road)

    return {
        "wall_faces_ours": float(len(ours.faces)),
        "wall_faces_upstream": float(theirs),
        "wall_components": float(len(ours.split(only_watertight=False))),
        "wall_max_face_depth_span": straddle,
        "boundary_edge_ratio": boundary_edge_ratio(ours),
        "road_faces_tangent_plane": float(len(tangent.faces)),
        "road_faces_edge_length": float(len(edge_length.faces)),
        "road_faces_available": float(every_quad),
        "road_components": float(len(tangent.split(only_watertight=False))),
    }


def _print_self_check() -> None:
    scores = self_check()
    print("[eval] meshing self-check on synthetic scenes")
    print(f"       two walls    ours={scores['wall_faces_ours']:.0f} faces  "
          f"upstream={scores['wall_faces_upstream']:.0f}  "
          f"components={scores['wall_components']:.0f}  "
          f"boundary_edge_ratio={scores['boundary_edge_ratio']:.4f}")
    print(f"       receding road  tangent-plane={scores['road_faces_tangent_plane']:.0f}  "
          f"edge-length={scores['road_faces_edge_length']:.0f}  "
          f"of {scores['road_faces_available']:.0f} available  "
          f"components={scores['road_components']:.0f}")

    # A face spanning the two walls would be ~4 deep; a face on either wall is 0.
    if scores["wall_max_face_depth_span"] > 0.5:
        print("       WARNING: a face bridges the occlusion boundary")


# ── WorldPlay call contract ───────────────────────────────────────────────────


def worldplay_repo(repo_path: str | Path | None = None) -> Path:
    """The HY-WorldPlay checkout: argument, then `$HY_WORLDPLAY_REPO`, then sibling."""
    return Path(repo_path or os.environ.get("HY_WORLDPLAY_REPO")
                or _REPO_ROOT.parent / "HY-WorldPlay")


def function_params(source: Path, name: str) -> tuple[set[str], set[str], bool]:
    """
    Read a function's parameters from source. Returns (accepted, required, **kwargs).

    Parsed rather than imported, because HY-WorldPlay pins `diffusers` and a
    `transformers` version that cannot coexist with the geometry stack, so its
    modules are usually not importable wherever this runs.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            args = node.args
            positional = args.posonlyargs + args.args

            # Defaults right-align onto the positional parameters.
            required = [a.arg for a in positional[: len(positional) - len(args.defaults)]]
            required += [a.arg for a, d in zip(args.kwonlyargs, args.kw_defaults)
                         if d is None]

            drop = {"self", "cls"}
            return ({a.arg for a in positional + args.kwonlyargs} - drop,
                    set(required) - drop, args.kwarg is not None)
    raise LookupError(f"no function named {name!r} in {source}")


def call_keywords(source: Path, callee: str) -> set[str]:
    """Collect the keyword argument names of every `<callee>(...)` in a file."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        tail = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if tail == callee:
            found |= {kw.arg for kw in node.keywords if kw.arg}
    return found


def check_contract(repo_path: str | Path | None = None) -> dict[str, list[str]]:
    """
    Compare `WorldPlayModel`'s calls against the upstream signatures.

    The way this integration breaks is a renamed or newly required argument, and
    both are visible in the source alone. `aspect_ratio`, for one, is required by
    `__call__` with no default, and omitting it fails only once ~90 GB of weights
    have loaded.

    Returns `{"rejected": [...], "missing": [...], "pose": [...]}`, all empty when
    the wrapper is consistent with the checkout.
    """
    repo = worldplay_repo(repo_path)
    pipeline_source = repo / "hyvideo" / "pipelines" / "worldplay_video_pipeline.py"
    if not pipeline_source.is_file():
        raise FileNotFoundError(
            f"No HY-WorldPlay checkout at {repo}. Clone "
            "https://github.com/Tencent-Hunyuan/HY-WorldPlay and set "
            "HY_WORLDPLAY_REPO, or pass --repo-path."
        )

    wrapper = _REPO_ROOT / "models" / "gen_3d_scene" / "world_play_model.py"
    accepted, required, takes_kwargs = function_params(pipeline_source, "__call__")
    passed = call_keywords(wrapper, "_pipe")

    return {
        "rejected": sorted(() if takes_kwargs else passed - accepted),
        "missing": sorted(required - passed),
        "pose": check_default_pose(),
    }


def check_default_pose() -> list[str]:
    """
    Confirm the default trajectory is a clip length WorldPlay will accept.

    Upstream's `pose_to_input` asserts the latent count equals the pose's
    keyframe count, and the autoregressive rollout wants a whole number of
    chunks. Both are checked deep inside a run, so an inconsistent default costs
    a full model load to discover.
    """
    from models.gen_3d_scene.world_play_model import AR_CHUNK_LATENTS, DEFAULT_POSE

    latents = int(sum(float(p.rsplit("-", 1)[1]) for p in DEFAULT_POSE.split(","))) + 1
    if latents % AR_CHUNK_LATENTS:
        return [f"{DEFAULT_POSE!r} gives {latents} latents, "
                f"not a multiple of {AR_CHUNK_LATENTS}"]
    return []


def _print_contract(repo_path: str | None) -> None:
    try:
        findings = check_contract(repo_path)
    except FileNotFoundError as exc:
        print(f"[eval] {exc}")
        return

    print(f"[eval] WorldPlay call contract against {worldplay_repo(repo_path)}")
    labels = {"rejected": "kwargs __call__ would reject",
              "missing": "kwargs __call__ requires but we omit",
              "pose": "default pose"}
    for key, label in labels.items():
        problems = findings[key]
        print(f"       {label:38s} {', '.join(problems) if problems else 'ok'}")


def main():
    parser = argparse.ArgumentParser(description="Score generated 3D scenes.")
    parser.add_argument("--self-check", action="store_true",
                        help="Mesh synthetic scenes instead of scoring a run")
    parser.add_argument("--contract-check", action="store_true",
                        help="Check WorldPlayModel against the HY-WorldPlay source")
    parser.add_argument("--repo-path", default=None,
                        help="HY-WorldPlay checkout for --contract-check")
    parser.add_argument("--game", default=None,
                        help=f"Game project id; all games when omitted. "
                             f"Known: {paths.list_games() or '<none>'}")
    parser.add_argument("--run-id", default=paths.DEFAULT_RUN_ID)
    parser.add_argument("--compare-to", default=None,
                        help="Second run id to diff against --run-id")
    args = parser.parse_args()

    if args.self_check:
        _print_self_check()
    if args.contract_check:
        _print_contract(args.repo_path)
    if args.self_check or args.contract_check:
        return

    games = [args.game] if args.game else paths.list_games()
    if not games:
        print("[eval] No game projects found — nothing to do.")
        return

    for game_id in games:
        try:
            scored = evaluate_results(load_results(game_id, args.run_id))
        except FileNotFoundError as exc:
            print(f"[eval] {exc}")
            continue

        summary_path = write_scores(scored, game_id, args.run_id)
        print(f"[eval] {game_id}: {len(scored)} scenes → {paths.rel_to_repo(summary_path)}")
        for key, value in _mean(scored).items():
            print(f"       {key:32s} {value:.6g}")

        if args.compare_to:
            _print_comparison(game_id, args.run_id, args.compare_to, _mean(scored))


def _print_comparison(game_id: str, run_id: str, other_run: str, baseline: dict) -> None:
    """Print how a second run moved each metric relative to the first."""
    try:
        other = _mean(evaluate_results(load_results(game_id, other_run)))
    except FileNotFoundError as exc:
        print(f"[eval] {exc}")
        return

    print(f"[eval] {run_id} → {other_run}")
    for key, before in baseline.items():
        after = other.get(key)
        if after is None or before == 0:
            continue
        change = (after - before) / abs(before) * 100
        better = (change < 0) == (key in LOWER_IS_BETTER)
        print(f"       {key:32s} {before:12.6g} → {after:12.6g}  "
              f"{change:+7.1f}%  {'better' if change and better else '' }")


if __name__ == "__main__":
    main()
