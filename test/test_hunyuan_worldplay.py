"""
test/test_hunyuan_worldplay.py

Tests for Hunyuan WorldPlay scene asset generation: a reference image and a
camera trajectory in, a scene mesh out.

The work these tests drive lives in the pipeline, not here, so it can also be run
by hand against a real output:

    python pipeline/assets_gen/gen_3d_scene/hunyuan_worldplay_eval.py --self-check
    python pipeline/assets_gen/gen_3d_scene/hunyuan_worldplay_eval.py --contract-check
    python pipeline/assets_gen/gen_3d_scene/hunyuan_worldplay_render.py scene.glb

Three tiers, each skipped when its prerequisites are absent, so this file is safe
to run anywhere: contract (needs only the HY-WorldPlay checkout), wiring (needs
nothing, stubs stand in for the weights), generation (needs weights and a GPU).

Run from repo root:
    python test/test_hunyuan_worldplay.py
    AAAGF_RUN_GPU_TESTS=1 python test/test_hunyuan_worldplay.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from pipeline.assets_gen.gen_3d_scene import hunyuan_worldplay_eval as scene_eval  # noqa: E402

CKPT = os.environ.get("WORLDMIRROR_CKPT")  # None → the Hub default in run.py
SKYSEG_CKPT = os.environ.get("SKYSEG_CKPT")  # None → download from the Hub
TASKS = _REPO_ROOT / "test_data" / "test_samples" / "3D_scene_gen_collect.jsonl"
OUT_DIR = _REPO_ROOT / "test_data" / "outputs" / "_test_3d_scene"
RUN_GPU = os.environ.get("AAAGF_RUN_GPU_TESTS") == "1"
WORLDPLAY_REPO = scene_eval.worldplay_repo()


def scene_tasks() -> list[dict]:
    """Benchmark tasks whose reference image is actually present."""
    import json

    if not TASKS.is_file():
        return []
    tasks = [json.loads(line) for line in
             TASKS.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [t for t in tasks
            if not t.get("image_path") or (_REPO_ROOT / t["image_path"]).exists()]


class TestMeshContinuity(unittest.TestCase):
    """The meshing fixes, on synthetic depth maps whose answers are known."""

    def test_self_check_meets_its_targets(self):
        scores = scene_eval.self_check()

        # Two walls at depth 2 and 6: no face may bridge them, and both must
        # survive whole rather than as one fused sheet.
        self.assertLess(scores["wall_max_face_depth_span"], 0.5)
        self.assertEqual(scores["wall_components"], 2)
        self.assertLess(scores["boundary_edge_ratio"], 0.05)
        self.assertGreater(scores["wall_faces_ours"], scores["wall_faces_upstream"])

        # A road receding to the horizon is one surface at every distance. The
        # tangent-plane test keeps all of it; an edge-length threshold cannot.
        self.assertEqual(scores["road_components"], 1)
        self.assertGreater(scores["road_faces_tangent_plane"],
                           0.99 * scores["road_faces_available"])
        self.assertLess(scores["road_faces_edge_length"],
                        scores["road_faces_tangent_plane"])


@unittest.skipUnless(
    (WORLDPLAY_REPO / "hyvideo" / "generate.py").is_file(),
    f"no HY-WorldPlay checkout at {WORLDPLAY_REPO}; set HY_WORLDPLAY_REPO",
)
class TestWorldPlayContract(unittest.TestCase):
    """`WorldPlayModel` must call HY-WorldPlay the way HY-WorldPlay expects."""

    def test_calls_match_the_checkout(self):
        findings = scene_eval.check_contract()

        self.assertEqual(findings["rejected"], [], "__call__ would reject these")
        self.assertEqual(findings["missing"], [], "__call__ requires these")
        self.assertEqual(findings["pose"], [])


class TestWorldPlayWiring(unittest.TestCase):
    """The image → frames → mesh path, with stubs in place of the weights."""

    def test_operator_drives_the_video_stage(self):
        from PIL import Image

        from operators.gen_3d_scene.hunyuan_worldplay_operator import Gen3DSceneOperator
        from test.harness.stubs import StubWorldMirrorModel, StubWorldPlayModel

        video_model = StubWorldPlayModel()
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "ref.png"
            Image.new("RGB", (64, 64), (120, 90, 60)).save(image_path)

            result = Gen3DSceneOperator(
                model=StubWorldMirrorModel(),
                video_model=video_model,
                output_dir=directory,
            ).run({
                "task_id": "worldplay_wiring",
                "image_path": str(image_path),
                "prompt": "a misty pine forest",
                "pose": "w-8, right-4",
            })

            self.assertTrue(Path(result["glb_path"]).exists())
            self.assertGreater(result["num_faces"], 0)

        self.assertEqual(len(video_model.calls), 1)
        self.assertEqual(video_model.calls[0]["prompt"], "a misty pine forest")
        self.assertEqual(video_model.calls[0]["pose"], "w-8, right-4")
        self.assertEqual(result["num_frames"], 4)


@unittest.skipUnless(RUN_GPU, "set AAAGF_RUN_GPU_TESTS=1 to run the real pipeline")
class TestSceneGeneration(unittest.TestCase):
    """The real chain over the benchmark task list."""

    @classmethod
    def setUpClass(cls):
        from pipeline.assets_gen.gen_3d_scene.hunyuan_worldplay_run import (
            DEFAULT_CKPT,
            load_model,
            load_sky_model,
            make_operator,
        )

        model, video_model = load_model(CKPT or DEFAULT_CKPT, backend="worldmirror")
        cls.sky_model = load_sky_model(SKYSEG_CKPT)
        cls.operator = make_operator(
            model, video_model, cls.sky_model, output_dir=str(OUT_DIR)
        )

    def test_generates_scene_assets(self):
        """Every benchmark task produces a mesh that holds together and renders."""
        from operators.gen_3d_scene.metrics import evaluate
        from pipeline.assets_gen.gen_3d_scene.hunyuan_worldplay_render import render_glb
        from pipeline.assets_gen.gen_3d_scene.hunyuan_worldplay_run import run_from_jsonl

        tasks = scene_tasks()
        if not tasks:
            self.skipTest(
                f"{TASKS.relative_to(_REPO_ROOT)} lists no tasks. Add a game project "
                "under test_data/test_samples/ with 3D_scene/ref_images/."
            )

        results = run_from_jsonl(str(TASKS), self.operator)
        self.assertEqual(len(results), len(tasks))

        for result in results:
            with self.subTest(task=result["task_id"]):
                glb = Path(result["glb_path"])
                self.assertGreater(glb.stat().st_size, 10_000)
                self.assertGreater(result["num_faces"], 1000)

                scores = evaluate(result, {})
                self.assertLess(scores["boundary_edge_ratio"], 0.05)

                # Rendering is part of the deliverable: a mesh that scores well
                # but cannot be rasterised is not a usable asset.
                self.assertTrue(render_glb(glb, glb.with_suffix(".png")).exists())
                print(f"\n    {result['task_id']}: {result['num_faces']} faces, "
                      f"boundary_edge_ratio={scores['boundary_edge_ratio']:.4f}, "
                      f"stretch_p99={scores['stretch_p99']:.2f}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
