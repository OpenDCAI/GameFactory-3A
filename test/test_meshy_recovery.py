"""Offline Meshy stage recovery: python test/test_meshy_recovery.py."""
from __future__ import annotations

from contextlib import contextmanager
import json
import multiprocessing
import os
from pathlib import Path
import queue
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_api_3d_object import MeshyModel, cloud_api, stubs
from models.common.task_recovery import PendingTaskStore

TEXT = "/openapi/v2/text-to-3d"
IMAGE = "/openapi/v1/image-to-3d"


class RecoveryClient:
    def __init__(self, states=None, *, download_error=False, submit_error=False):
        self.states = states or {}
        self.download_error = download_error
        self.submit_error = submit_error
        self.requests = []
        self.downloads = []
        self.posts = []

    def request(self, method, path, **kwargs):
        self.requests.append((method, path))
        if method == "POST":
            body = kwargs["json_body"]
            stage = body.get("mode", "image")
            if stage == "refine" and self.submit_error:
                raise cloud_api.CloudAPIServerError("refine rejected")
            self.posts.append(dict(body))
            return {"result": stage + "-1"}
        task_id = path.rsplit("/", 1)[-1]
        state = self.states.get(task_id, "SUCCEEDED")
        if isinstance(state, Exception):
            raise state
        return {"id": task_id, "status": state,
                "model_urls": {"glb": "https://fresh.example/" + task_id}}

    def download(self, url):
        self.downloads.append(url)
        if self.download_error:
            raise cloud_api.CloudAPIServerError("download failed")
        return stubs.make_minimal_glb(triangles=3)


def build(root, *, states=None, download_error=False, submit_error=False, **kwargs):
    model = MeshyModel(cache_dir=root, timeout=0, poll_interval=0,
                       **{"api_key": "test-key", **kwargs})
    model._client = RecoveryClient(states, download_error=download_error,
                                   submit_error=submit_error)
    return model


def checkpoints(root):
    return list(Path(root).glob("meshy/pending/*.json"))


def crash_after_refine(root):
    model = build(root)
    original = model._client.request

    def request(method, path, **kwargs):
        if method == "GET" and path.endswith("/refine-1"):
            os._exit(17)
        return original(method, path, **kwargs)

    model._client.request = request
    model.infer(prompt="a chair")


def concurrent_worker(root, attempted, downloading, release, calls, results):
    original_lock = PendingTaskStore.lock

    @contextmanager
    def observed_lock(self, **kwargs):
        attempted.set()
        with original_lock(self, **kwargs):
            yield

    model = build(root)
    model.timeout = 10
    original_request = model._client.request
    original_download = model._client.download

    def request(method, path, **kwargs):
        calls.put((method, path))
        return original_request(method, path, **kwargs)

    def download(url):
        downloading.set()
        if not release.wait(10):
            raise RuntimeError("test release timeout")
        return original_download(url)

    model._client.request = request
    model._client.download = download
    try:
        with patch.object(PendingTaskStore, "lock", observed_lock):
            data = model.infer(prompt="a chair")
        results.put((len(data), model.last_call_info["cached"]))
    except Exception as exc:
        results.put(("error", repr(exc)))


class TestMeshyRecovery(unittest.TestCase):
    def pending(self, root, stage="refine", **kwargs):
        model = build(root, states={stage + "-1": "IN_PROGRESS"}, **kwargs)
        with self.assertRaises(cloud_api.CloudTaskTimeout):
            model.infer(prompt="a chair")
        return model

    def test_preview_timeout_resumes_preview_then_creates_one_refine(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.pending(root, "preview")
            second = build(root)
            second.infer(prompt="a chair")
            self.assertEqual(second._client.requests, [("GET", TEXT + "/preview-1"),
                ("POST", TEXT), ("GET", TEXT + "/refine-1")])
            self.assertEqual([p["mode"] for p in first._client.posts + second._client.posts],
                             ["preview", "refine"])
            self.assertEqual(second._client.posts[0]["preview_task_id"], "preview-1")
            self.assertTrue(second.last_call_info["resumed"])
            self.assertEqual(checkpoints(root), [])

    def test_refine_timeout_skips_preview_and_reuses_both_ids(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.pending(root)
            self.assertEqual(len(checkpoints(root)), 2)
            second = build(root, states={"preview-1": cloud_api.CloudAPIRequestError(
                "preview expired", status=404)})
            data = second.infer(prompt="a chair")
            self.assertEqual(second._client.requests, [("GET", TEXT + "/refine-1")])
            self.assertEqual(len(first._client.posts + second._client.posts), 2)
            self.assertEqual(second.last_call_info["task_ids"], ["preview-1", "refine-1"])
            self.assertEqual(checkpoints(root), [])
            third = build(root)
            third.api_key = None
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(third.infer(prompt="a chair"), data)
            self.assertEqual(third._client.requests, [])
            self.assertEqual(third._client.downloads, [])

    def test_refine_submission_error_keeps_preview(self):
        with tempfile.TemporaryDirectory() as root:
            first = build(root, submit_error=True)
            with self.assertRaises(cloud_api.CloudAPIServerError):
                first.infer(prompt="a chair")
            self.assertEqual(len(checkpoints(root)), 1)
            second = build(root)
            second.infer(prompt="a chair")
            self.assertEqual([p["mode"] for p in second._client.posts], ["refine"])

    def test_single_stage_image_and_untextured_text_resume(self):
        for inputs, kwargs, stage, endpoint in (
                ({"image": stubs.make_ref_image()}, {}, "image", IMAGE),
                ({"image_url": "https://input.example/a.png"}, {}, "image", IMAGE),
                ({"prompt": "a chair"}, {"texture": False}, "preview", TEXT),
                ({"prompt": "a chair"}, {"refine": False}, "preview", TEXT)):
            with self.subTest(inputs=inputs.keys(), kwargs=kwargs), tempfile.TemporaryDirectory() as root:
                first = build(root, states={stage + "-1": "IN_PROGRESS"}, **kwargs)
                with self.assertRaises(cloud_api.CloudTaskTimeout):
                    first.infer(**inputs)
                second = build(root, **kwargs)
                second.infer(**inputs)
                self.assertEqual(second._client.requests, [("GET", endpoint + "/" + stage + "-1")])
                self.assertEqual(checkpoints(root), [])

    def test_terminal_missing_network_and_download_errors_preserve_refine(self):
        for state, error in (("FAILED", cloud_api.CloudTaskFailed),
                ("EXPIRED", cloud_api.CloudTaskFailed),
                (cloud_api.CloudAPIRequestError("missing", status=404), cloud_api.CloudAPIRequestError),
                (cloud_api.CloudAPIServerError("offline"), cloud_api.CloudAPIServerError),
                ("SUCCEEDED", cloud_api.CloudAPIServerError)):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as root:
                self.pending(root)
                for _ in range(2):
                    model = build(root, states={"refine-1": state}, download_error=state == "SUCCEEDED")
                    with self.assertRaises(error) as ctx:
                        model.infer(prompt="a chair")
                    self.assertEqual(ctx.exception.task_id, "refine-1")
                    self.assertTrue(any(str(p) in str(ctx.exception) for p in checkpoints(root)))
                    self.assertEqual(model._client.requests, [("GET", TEXT + "/refine-1")])
                    self.assertEqual(len(checkpoints(root)), 2)
                recovered = build(root)
                recovered.infer(prompt="a chair")
                self.assertEqual(recovered._client.downloads, ["https://fresh.example/refine-1"])

    def test_invalid_or_orphaned_checkpoint_blocks_all_network(self):
        for contents in ("{", "[]", None):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as root:
                self.pending(root)
                preview = next(p for p in checkpoints(root) if json.loads(p.read_text())["task_id"] == "preview-1")
                if contents is None:
                    preview.unlink()
                else:
                    preview.write_text(contents)
                model = build(root)
                with self.assertRaises(cloud_api.CloudAPIError):
                    model.infer(prompt="a chair")
                self.assertEqual(model._client.requests, [])

    def test_scope_and_pbr_separate_pending_requests(self):
        with tempfile.TemporaryDirectory() as root:
            self.pending(root, "preview")
            for kwargs in ({"api_key": "other-key"}, {"api_base": "https://other.example"},
                           {"enable_pbr": False}, {"model_path": "meshy-5"}):
                self.pending(root, "preview", **kwargs)
            self.assertEqual(len(checkpoints(root)), 5)
            for path in checkpoints(root):
                content = path.read_text()
                self.assertEqual(set(json.loads(content)), {"version", "scope", "task_id", "created_at"})
                for private in ("test-key", "other-key", "https://", "a chair"):
                    self.assertNotIn(private, content)

    def test_completed_cache_distinguishes_refine_pbr(self):
        with tempfile.TemporaryDirectory() as root:
            first = build(root)
            first.infer(prompt="a chair")
            second = build(root, enable_pbr=False)
            second.infer(prompt="a chair")
            self.assertEqual(len(second._client.posts), 2)
            self.assertNotEqual(first.last_call_info["cache_key"], second.last_call_info["cache_key"])

    def test_scope_uses_credentials_held_by_client(self):
        with tempfile.TemporaryDirectory() as root:
            model = build(root, states={"refine-1": "IN_PROGRESS"})
            model._client._api_key = "original-key"
            with self.assertRaises(cloud_api.CloudTaskTimeout):
                model.infer(prompt="a chair")
            model.api_key = "new-unused-key"
            model._client.states = {}
            model._client.requests.clear()
            model.infer(prompt="a chair")
            self.assertEqual(model._client.requests, [("GET", TEXT + "/refine-1")])

    def test_checkpoint_write_failure_identifies_accepted_stage_before_polling(self):
        for stage in ("preview", "refine"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as root:
                model = build(root)
                original = PendingTaskStore.save

                def save(store, task_id):
                    if task_id == stage + "-1":
                        with patch("models.common.task_recovery._atomic_write", side_effect=OSError("disk full")):
                            original(store, task_id)
                    else:
                        original(store, task_id)

                with patch.object(PendingTaskStore, "save", save):
                    with self.assertRaises(cloud_api.CloudAPIError) as ctx:
                        model.infer(prompt="a chair")
                self.assertEqual(ctx.exception.task_id, stage + "-1")
                self.assertNotIn(("GET", TEXT + "/" + stage + "-1"), model._client.requests)

    def test_cache_publication_failure_retains_both_stages(self):
        with tempfile.TemporaryDirectory() as root:
            model = build(root)
            original = os.replace

            def replace(source, destination):
                if Path(destination).suffix == ".glb":
                    raise OSError("interrupted publication")
                return original(source, destination)

            with patch.object(cloud_api.os, "replace", replace):
                with self.assertRaises(OSError):
                    model.infer(prompt="a chair")
            self.assertEqual(len(checkpoints(root)), 2)
            self.assertEqual(list(Path(root).glob("meshy/*.glb")), [])
            second = build(root)
            second.infer(prompt="a chair")
            self.assertEqual(second._client.requests, [("GET", TEXT + "/refine-1")])

    def test_cleanup_failure_keeps_preview_and_completed_cache(self):
        with tempfile.TemporaryDirectory() as root:
            model = build(root)
            with patch.object(PendingTaskStore, "clear", side_effect=OSError("busy")):
                data = model.infer(prompt="a chair")
            self.assertEqual(len(checkpoints(root)), 2)
            other = build(root)
            self.assertEqual(other.infer(prompt="a chair"), data)
            self.assertEqual(other._client.requests, [])

    def test_disabled_cache_has_no_recovery(self):
        self.pending(None)
        model = build(None)
        model.infer(prompt="a chair")
        self.assertEqual(len(model._client.posts), 2)
        self.assertFalse(model.last_call_info["resumed"])

    def test_process_crash_after_refine_checkpoint_resumes(self):
        ctx = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as root:
            process = ctx.Process(target=crash_after_refine, args=(root,))
            process.start()
            try:
                process.join(15)
                self.assertEqual(process.exitcode, 17)
                self.assertEqual(len(checkpoints(root)), 2)
                model = build(root)
                model.infer(prompt="a chair")
                self.assertEqual(model._client.requests, [("GET", TEXT + "/refine-1")])
            finally:
                if process.is_alive():
                    process.terminate()
                    process.join(5)

    def test_two_processes_submit_only_one_preview_and_refine(self):
        ctx = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as root:
            attempted1, attempted2 = ctx.Event(), ctx.Event()
            downloading, release = ctx.Event(), ctx.Event()
            calls, results = ctx.Queue(), ctx.Queue()
            processes = [ctx.Process(target=concurrent_worker, args=(
                root, attempted, downloading, release, calls, results))
                for attempted in (attempted1, attempted2)]
            try:
                processes[0].start()
                self.assertTrue(downloading.wait(10))
                requests = [calls.get(timeout=2) for _ in range(4)]
                self.assertEqual(requests, [("POST", TEXT), ("GET", TEXT + "/preview-1"),
                                           ("POST", TEXT), ("GET", TEXT + "/refine-1")])
                processes[1].start()
                self.assertTrue(attempted2.wait(10))
                with self.assertRaises(queue.Empty):
                    calls.get(timeout=0.3)
                with self.assertRaises(queue.Empty):
                    results.get(timeout=0.3)
                release.set()
                for process in processes:
                    process.join(15)
                    self.assertEqual(process.exitcode, 0)
                responses = [results.get(timeout=2) for _ in range(2)]
                self.assertTrue(all(isinstance(r[0], int) and r[0] > 0 for r in responses), responses)
                self.assertEqual(sorted(r[1] for r in responses), [False, True])
                with self.assertRaises(queue.Empty):
                    calls.get(timeout=0.1)
                self.assertEqual(checkpoints(root), [])
            finally:
                release.set()
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                        process.join(5)
                calls.close()
                results.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
