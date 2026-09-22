"""Contract checks for Browser Serving CG-video job orchestration."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from pipeline.common import paths


class _StubVideoModel:
    def __init__(self) -> None:
        self.calls = 0

    def infer_and_save(self, request, output_path: str) -> str:
        del request
        self.calls += 1
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"stub-mp4")
        return str(target)


class _RecordingVideoModel:
    """A model that records ``last_call_info`` the way a real backend does.

    ``runtime`` is the field that marks an artifact as a real generation, so a
    model that omits it produces artifacts the gateway must refuse to reuse.
    """

    def __init__(self, delay: float = 0.0) -> None:
        self.calls = 0
        self.delay = delay
        self.last_call_info: dict[str, object] = {}

    def infer_and_save(self, request, output_path: str) -> str:
        del request
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"stub-mp4")
        self.last_call_info = {
            "provider": "stub",
            "runtime": "local",
            "model": "stub-h3",
            "bytes": len(b"stub-mp4"),
            "cached": False,
        }
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


class CgVideoReuseTests(unittest.TestCase):
    """A materialized artifact is an asset: ask again, get the same clip.

    A miss is the case worth pinning down: it regenerates by default, and with
    ``prebuilt_only`` it must not.
    """

    def _fixture(self, *, model=None, delay: float = 0.0, prebuilt_only: bool = False):
        from engine_adapters.browser_serving.cg_video import CgVideoGateway

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        samples = root / "samples"
        outputs = root / "outputs"
        task_dir = samples / "game_demo" / "cg_video"
        task_dir.mkdir(parents=True)
        row = {
            "game_id": "game_demo",
            "task_id": "opening_001",
            "model": "h3",
            "mode": "text_to_video",
            "scene": "opening",
            "duration_sec": 1,
            "seed": 42,
            "prompt": "A short establishing shot.",
        }
        task_jsonl = task_dir / "cg_tasks.jsonl"

        def write_task(**overrides) -> None:
            task_jsonl.write_text(
                json.dumps({**row, **overrides}) + "\n", encoding="utf-8"
            )

        write_task()

        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(paths, "TEST_SAMPLES_ROOT", samples))
        stack.enter_context(mock.patch.object(paths, "OUTPUT_ROOT", outputs))

        if model is None:
            model = _RecordingVideoModel(delay=delay)
        gateway = CgVideoGateway(
            output_root=outputs,
            prebuilt_only=prebuilt_only,
            model_factory=lambda backend, task, options: model,
        )
        self.addCleanup(gateway.close)
        return gateway, model, outputs, write_task

    @staticmethod
    def _wait(gateway, request_id: str, timeout: float = 5.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = gateway.get(request_id)
            if current["status"] in {"ready", "failed"}:
                return current
            time.sleep(0.01)
        raise AssertionError(f"job {request_id} never settled")

    @staticmethod
    def _store_artifact(outputs, run_id: str, **overrides) -> Path:
        """Hand-write the layout a real generation leaves on disk."""

        task_dir = paths.task_output_dir(
            "game_demo", "cg_video", "opening_001", run_id=run_id
        )
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "video.mp4").write_bytes(b"stored-mp4")
        meta = {
            "task_id": "opening_001",
            "game_id": "game_demo",
            "run_id": run_id,
            "mode": "text_to_video",
            "prompt": "A short establishing shot.",
            "duration_sec": 1,
            "seed": 42,
            "first_frame_path": None,
            "last_frame_path": None,
            "reference_image_paths": [],
            "model_call": {"provider": "stub", "runtime": "local"},
            **overrides,
        }
        (task_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        return task_dir

    def test_repeat_submit_reuses_the_materialized_artifact(self) -> None:
        gateway, model, outputs, _ = self._fixture()
        first = gateway.submit(
            game_id="game_demo", task_id="opening_001", run_id="run_001"
        )
        done = self._wait(gateway, first["request_id"])
        self.assertEqual(done["status"], "ready")
        self.assertEqual(model.calls, 1)
        artifact = done["video"]["artifact_id"]
        runs_before = sorted(entry.name for entry in (outputs / "game_demo").glob("*"))

        # A page reload submits with run_id="auto" and no idempotency key, so an
        # artifact-level lookup is the only thing that can catch it.
        second = gateway.submit(
            game_id="game_demo", task_id="opening_001", run_id="auto"
        )
        self.assertEqual(second["status"], "ready")
        self.assertEqual(second["video"]["artifact_id"], artifact)
        self.assertEqual(second["run_id"], "run_001")
        self.assertEqual(model.calls, 1)
        self.assertTrue(gateway.media_path(artifact).is_file())
        self.assertEqual(
            sorted(entry.name for entry in (outputs / "game_demo").glob("*")),
            runs_before,
        )

    def test_reuse_is_skipped_when_the_task_definition_changed(self) -> None:
        gateway, model, _outputs, write_task = self._fixture()
        first = gateway.submit(
            game_id="game_demo", task_id="opening_001", run_id="run_001"
        )
        done = self._wait(gateway, first["request_id"])

        write_task(prompt="A completely different shot.")
        second = gateway.submit(
            game_id="game_demo", task_id="opening_001", run_id="run_002"
        )
        self.assertIn(second["status"], {"queued", "running"})
        again = self._wait(gateway, second["request_id"])
        self.assertNotEqual(again["video"]["artifact_id"], done["video"]["artifact_id"])
        self.assertEqual(model.calls, 2)

    def test_reuse_requires_a_recorded_generation(self) -> None:
        stub = _StubVideoModel()
        gateway, _, _, _ = self._fixture(model=stub)
        first = gateway.submit(
            game_id="game_demo", task_id="opening_001", run_id="run_001"
        )
        done = self._wait(gateway, first["request_id"])

        # The stub records no `model_call`, so the artifact cannot be shown to be
        # a real generation and must not be handed back as one.
        second = gateway.submit(
            game_id="game_demo", task_id="opening_001", run_id="run_002"
        )
        self.assertIn(second["status"], {"queued", "running"})
        again = self._wait(gateway, second["request_id"])
        self.assertNotEqual(again["video"]["artifact_id"], done["video"]["artifact_id"])
        self.assertEqual(stub.calls, 2)

    def test_reuse_can_be_disabled(self) -> None:
        gateway, model, _outputs, _ = self._fixture()
        first = gateway.submit(
            game_id="game_demo", task_id="opening_001", run_id="run_001"
        )
        done = self._wait(gateway, first["request_id"])

        second = gateway.submit(
            game_id="game_demo",
            task_id="opening_001",
            run_id="run_002",
            options={"reuse": False},
        )
        self.assertIn(second["status"], {"queued", "running"})
        again = self._wait(gateway, second["request_id"])
        self.assertNotEqual(again["video"]["artifact_id"], done["video"]["artifact_id"])
        self.assertEqual(model.calls, 2)

    def test_prebuilt_only_serves_a_stored_artifact(self) -> None:
        gateway, model, outputs, _ = self._fixture(prebuilt_only=True)
        self._store_artifact(outputs, "run_007")

        hit = gateway.submit(game_id="game_demo", task_id="opening_001", run_id="auto")
        self.assertEqual(hit["status"], "ready")
        self.assertEqual(hit["run_id"], "run_007")
        self.assertEqual(model.calls, 0)

    def test_prebuilt_only_reports_a_missing_artifact(self) -> None:
        gateway, model, outputs, _ = self._fixture(prebuilt_only=True)
        # The request that changed this demo's ending clip was a play-time miss
        # that fell through to a ~100 s H3 generation.  Here it answers, and the
        # answer is that production has not generated this clip.
        miss = gateway.submit(
            game_id="game_demo", task_id="opening_001", run_id="auto"
        )
        self.assertEqual(miss["status"], "failed")
        self.assertIn("pre-generated", miss["error"])
        self.assertEqual(model.calls, 0)
        self.assertEqual(list((outputs / "game_demo").glob("*")), [])

    def test_prebuilt_only_refuses_a_regeneration_request(self) -> None:
        gateway, model, _outputs, _ = self._fixture(prebuilt_only=True)
        # `reuse: false` asks for a regeneration, which is the one thing a
        # play-time gateway must not do, so it cannot be the way around the gate.
        forced = gateway.submit(
            game_id="game_demo",
            task_id="opening_001",
            run_id="run_002",
            options={"reuse": False},
        )
        self.assertEqual(forced["status"], "failed")
        self.assertEqual(model.calls, 0)

    def test_pinned_run_id_scopes_the_lookup(self) -> None:
        gateway, model, _outputs, _ = self._fixture()
        first = gateway.submit(
            game_id="game_demo", task_id="opening_001", run_id="run_001"
        )
        self._wait(gateway, first["request_id"])

        # An explicit run id asks about *that* run, so it must not quietly serve
        # a different run's artifact.
        second = gateway.submit(
            game_id="game_demo", task_id="opening_001", run_id="run_002"
        )
        self.assertIn(second["status"], {"queued", "running"})
        self._wait(gateway, second["request_id"])
        self.assertEqual(model.calls, 2)

    def test_duplicate_submits_during_a_generation_share_one_job(self) -> None:
        gateway, model, _outputs, _ = self._fixture(delay=0.4)
        first = gateway.submit(game_id="game_demo", task_id="opening_001", run_id="auto")
        # Arrives while the first is still generating.  At max_workers=1 a second
        # job here would queue a whole extra generation behind the first, which is
        # how reloading a page used to make the wait longer instead of shorter.
        second = gateway.submit(game_id="game_demo", task_id="opening_001", run_id="auto")
        self.assertEqual(second["request_id"], first["request_id"])
        self._wait(gateway, first["request_id"])
        self.assertEqual(model.calls, 1)

    def test_newest_artifact_wins_by_write_time_not_directory_name(self) -> None:
        gateway, model, outputs, _ = self._fixture()
        # Two stored artifacts for one task.  'cachecheck' sorts after the
        # timestamped name but was written first, so picking by directory name
        # would return the stale one.
        older = self._store_artifact(outputs, "cachecheck")
        newer = self._store_artifact(outputs, "20200101_000000")
        os.utime(older / "meta.json", (1_000_000, 1_000_000))
        os.utime(newer / "meta.json", (2_000_000, 2_000_000))

        hit = gateway.submit(game_id="game_demo", task_id="opening_001", run_id="auto")
        self.assertEqual(hit["status"], "ready")
        self.assertEqual(hit["run_id"], "20200101_000000")
        self.assertEqual(model.calls, 0)

    def test_restart_recovers_artifacts_from_disk(self) -> None:
        gateway, _model, outputs, _ = self._fixture()
        task_dir = self._store_artifact(outputs, "run_009")
        from engine_adapters.browser_serving.cg_video import _artifact_id

        artifact = _artifact_id("game_demo", "run_009", "opening_001")
        # A fresh process has never seen this artifact id, but the id is a pure
        # function of the on-disk layout, so the media route can still serve it.
        gateway._artifacts.clear()
        self.assertTrue(gateway.media_path(artifact).is_file())
        self.assertTrue((task_dir / "video.mp4").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
