"""Contract checks for Browser Serving CG-video job orchestration."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from pipeline.common import paths


class _StubVideoModel:
    def infer_and_save(self, request, output_path: str) -> str:
        del request
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"stub-mp4")
        return str(target)


class BrowserServingCgVideoTests(unittest.TestCase):
    def test_task_identity_is_queued_and_materialized(self) -> None:
        from engine_adapters.browser_serving.cg_video import CgVideoGateway

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            samples = root / "samples"
            outputs = root / "outputs"
            task_dir = samples / "game_demo" / "cg_video"
            task_dir.mkdir(parents=True)
            (task_dir / "cg_tasks.jsonl").write_text(
                json.dumps(
                    {
                        "game_id": "game_demo",
                        "task_id": "opening_001",
                        "model": "h3",
                        "mode": "text_to_video",
                        "scene": "opening",
                        "duration_sec": 1,
                        "aspect_ratio": "16:9",
                        "prompt": "A short establishing shot.",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                paths,
                "TEST_SAMPLES_ROOT",
                samples,
            ), mock.patch.object(paths, "OUTPUT_ROOT", outputs):
                gateway = CgVideoGateway(
                    output_root=outputs,
                    model_factory=lambda backend, task, options: _StubVideoModel(),
                )
                try:
                    queued = gateway.submit(
                        game_id="game_demo",
                        task_id="opening_001",
                        run_id="run_001",
                        idempotency_key="race-intro",
                        playback={"autoplay": True, "muted": True},
                    )
                    self.assertIn(
                        queued["status"],
                        {"queued", "running", "ready"},
                    )
                    for _ in range(100):
                        current = gateway.get(queued["request_id"])
                        if current["status"] in {"ready", "failed"}:
                            break
                        time.sleep(0.01)
                    self.assertEqual(current["status"], "ready")
                    self.assertEqual(current["playback"]["muted"], True)
                    artifact = current["video"]["artifact_id"]
                    self.assertTrue(gateway.media_path(artifact).is_file())
                    duplicate = gateway.submit(
                        game_id="game_demo",
                        task_id="opening_001",
                        run_id="run_001",
                        idempotency_key="race-intro",
                    )
                    self.assertEqual(duplicate["request_id"], queued["request_id"])
                finally:
                    gateway.close()

    def test_reference_audio_and_video_are_rejected_before_queueing(self) -> None:
        from engine_adapters.browser_serving.cg_video import CgVideoGateway

        gateway = CgVideoGateway(enabled=True)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                samples = root / "samples" / "game_demo" / "cg_video"
                samples.mkdir(parents=True)
                (samples / "cg_tasks.jsonl").write_text(
                    json.dumps(
                        {
                            "game_id": "game_demo",
                            "task_id": "cutscene_001",
                            "mode": "reference_to_video",
                            "prompt": "Use the declared references.",
                            "reference_video_paths": ["future.mp4"],
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                with mock.patch.object(paths, "TEST_SAMPLES_ROOT", root / "samples"):
                    with self.assertRaises(NotImplementedError):
                        gateway.submit(
                            game_id="game_demo",
                            task_id="cutscene_001",
                        )
        finally:
            gateway.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
