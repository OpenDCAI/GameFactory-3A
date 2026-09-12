import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from engine_adapters.three_js._internal.transport.node import NodeCommandResult
from engine_adapters.three_js.config import ThreeClientConfig
from engine_adapters.three_js.playtest.client import ThreePlaytestClient
from engine_adapters.three_js.world._internal.specs import EnvironmentSpec, WorldSpec


class TestRealismWorld(unittest.TestCase):
    def test_round_trip_water_and_sky(self):
        data = {
            "show_sky": False,
            "environment_rotation_degrees": 90,
            "water": [{"water_id": "lake", "size": [20, 30],
                       "position": [2, 1, -3], "normal_artifact_id": "water-normal",
                       "options": {"quality": "standard", "waveHeight": 0.08}}],
        }
        first = EnvironmentSpec.from_dict(data)
        second = EnvironmentSpec.from_dict(first.to_dict())
        self.assertEqual(first, second)
        self.assertEqual(second.background_rotation_degrees, 90)
        self.assertEqual(second.water[0]["position"], {"x": 2, "y": 1, "z": -3})
        world = WorldSpec.from_dict({"world_id": "scene", "environment": data})
        self.assertIn("water-normal", world.artifact_ids())

    def test_wind_round_trip_and_validation(self):
        env = EnvironmentSpec.from_dict({"wind": {"velocity": [3, 0, -1], "gustStrength": 0.5, "seed": 3}})
        self.assertEqual(EnvironmentSpec.from_dict(env.to_dict()), env)
        self.assertEqual(env.wind["velocity"], {"x": 3, "y": 0, "z": -1})
        for wind in ({"velocity": [1, 2]}, {"velocity": [0, float("inf"), 0]},
                     {"gustStrength": -1}, {"gustPeriod": 0}, {"seed": float("nan")}):
            with self.subTest(wind=wind), self.assertRaises(ValueError):
                EnvironmentSpec.from_dict({"wind": wind})

    def test_no_water_preserves_legacy_world(self):
        self.assertEqual(EnvironmentSpec.from_dict({}).water, ())

    def test_rejects_invalid_water(self):
        cases = [
            [{"water_id": "bad id"}],
            [{"water_id": "lake", "size": -1}],
            [{"water_id": "lake", "size": [20]}],
            [{"water_id": "lake", "size": float("inf")}],
            [{"water_id": "lake", "position": [0, float("nan"), 0]}],
            [{"water_id": "lake", "options": {"quality": "ultra"}}],
            [{"water_id": "lake"}, {"water_id": "lake"}],
            {},
        ]
        for water in cases:
            with self.subTest(water=water), self.assertRaises(ValueError):
                EnvironmentSpec.from_dict({"water": water})


class TestThreePlaytest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".playtest-test-", dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "package.json").write_text("{}", encoding="utf-8")
        self.out = self.root / "takes"
        self.client = ThreePlaytestClient(ThreeClientConfig.resolve(project_path=self.project))
        self.url = "http://127.0.0.1:5170/games/example/"

    def command(self, stdout, returncode=0, timed_out=False):
        return NodeCommandResult(("node",), str(self.project), returncode,
                                 stdout=stdout, timed_out=timed_out)

    def evidence(self, *, parent=None, preview=False, change=None, summary_change=None):
        parent = parent or self.out
        parent.mkdir(parents=True, exist_ok=True)
        take = Path(tempfile.mkdtemp(prefix="gameplay-take-", dir=parent))
        (take / "frames").mkdir()
        for name in ("video.mp4", "init.png", "poster.png"):
            (take / name).write_bytes(b"test fixture")
        report = {
            "status": "preview_completed" if preview else "completed", "output_dir": str(take),
            "source_hash": "AbC123", "mode": "gameplay", "url": self.url,
            "frames": 0 if preview else 2, "target_frames": 2, "fps": 20,
            "recorded_seconds": 0 if preview else 0.1, "viewport": {"width": 1280, "height": 720},
            "video": None if preview else str(take / "video.mp4"),
            "video_metadata": {"frames": 2, "seconds": 0.1, "fps": 20, "width": 1280, "height": 720},
            "init": str(take / "init.png"), "poster": str(take / "poster.png"),
            "warnings": [], "page_errors": [], "console_errors": [], "crash": "",
            "preview": preview, "png_frames": 1 if preview else 0,
        }
        if change:
            change(report, take)
        report_path = take / "report.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        summary = {"status": report["status"], "output_dir": str(take),
                   "report": str(report_path), "source_hash": "AbC123"}
        if summary_change:
            summary_change(summary, take)
        return self.command("diagnostic output\n" + json.dumps(summary) + "\n")

    def record(self, callback, **options):
        defaults = {"output_dir": self.out, "url": self.url, "duration": 0.1, "source_hash": "AbC123"}
        defaults.update(options)
        with patch.object(self.client._toolchain, "run_node", side_effect=callback) as run:
            result = self.client.record(**defaults)
        return result, run

    def test_defaults_and_passthrough_without_overriding_declared_plan(self):
        result = self.client.record(output_dir=self.out, dry_run=True)
        self.assertTrue(result["ok"])
        payload = result["payload"]
        self.assertEqual(payload["duration"], 14)
        self.assertEqual(payload["viewport"], {"width": 1280, "height": 720})
        self.assertIsNone(payload["report_path"])
        self.assertNotIn("--look", payload["arguments"])
        self.assertNotIn("--warmup", payload["arguments"])
        self.assertFalse(self.out.exists())
        result = self.client.record(output_dir=self.out, dry_run=True, url=self.url, preview=True,
                                    mode="overview", allow_partial_plan=True, source_hash="AbC123",
                                    look=False, warmup=0)
        argv = result["payload"]["arguments"]
        for flag in ("--preview", "--allow-partial-plan"):
            self.assertIn(flag, argv)
        for flag, value in (("--mode", "overview"), ("--source-hash", "AbC123"),
                            ("--look", "off"), ("--warmup", "0.0"), ("--url", self.url)):
            self.assertEqual(argv[argv.index(flag) + 1], value)

    def test_current_unique_take_and_artifacts_are_accepted(self):
        result, _ = self.record(lambda *a, **kw: self.evidence())
        self.assertTrue(result["ok"], result)
        take = Path(result["payload"]["take_dir"])
        self.assertEqual(Path(result["payload"]["report_path"]), take / "report.json")
        self.assertTrue(all(Path(item["path"]).is_relative_to(take) for item in result["artifacts"]))

    def test_old_root_report_is_never_a_fallback(self):
        self.out.mkdir()
        old = self.out / "report.json"
        old.write_text('{"status":"completed","frames":200}', encoding="utf-8")
        for stdout in ("", "not JSON", json.dumps({"report": str(old), "output_dir": str(self.out)})):
            with self.subTest(stdout=stdout):
                result, _ = self.record(lambda *a, **kw: self.command(stdout))
                self.assertFalse(result["ok"])
        self.assertTrue(old.exists())

    def test_preexisting_take_and_outside_paths_are_rejected(self):
        stale = self.evidence()
        result, _ = self.record(lambda *a, **kw: stale)
        self.assertFalse(result["ok"])
        result, _ = self.record(lambda *a, **kw: self.evidence(parent=self.root / "outside"))
        self.assertFalse(result["ok"])
        result, _ = self.record(lambda *a, **kw: self.evidence(
            summary_change=lambda s, t: s.update(report="report.json")))
        self.assertFalse(result["ok"])

    def test_symlink_artifact_escape_is_rejected(self):
        outside = self.root / "other.mp4"
        outside.write_bytes(b"old evidence")
        def change(report, take):
            link = take / "escape.mp4"
            link.symlink_to(outside)
            report["video"] = str(link)
        result, _ = self.record(lambda *a, **kw: self.evidence(change=change))
        self.assertFalse(result["ok"])

    def test_failed_or_unverified_capture_cannot_be_success(self):
        for update in ({"status": "failed"}, {"video_metadata": None}, {"frames": 1},
                       {"page_errors": ["bad shader"]}, {"console_errors": ["asset failed"]},
                       {"crash": "renderer died"}, {"partial_plan": True},
                       {"recorded_seconds": 9}, {"source_hash": "old"}):
            with self.subTest(update=update):
                result, _ = self.record(lambda *a, **kw: self.evidence(change=lambda r, t: r.update(update)))
                self.assertFalse(result["ok"], result)
                self.assertIn("report", result["payload"])

    def test_nonzero_exit_or_timeout_rejects_even_completed_report(self):
        for timeout in (False, True):
            def run(*a, **kw):
                completed = self.evidence()
                return self.command(completed.stdout, returncode=0 if timeout else 1, timed_out=timeout)
            result, _ = self.record(run)
            self.assertFalse(result["ok"])

    def test_preview_requires_explicit_flag_and_png(self):
        result, _ = self.record(lambda *a, **kw: self.evidence(preview=True), preview=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual([item["type"] for item in result["artifacts"]], ["playtest_report", "playtest_poster"])
        result, _ = self.record(lambda *a, **kw: self.evidence(preview=True))
        self.assertFalse(result["ok"])
        result, _ = self.record(lambda *a, **kw: self.evidence(), preview=True)
        self.assertFalse(result["ok"])

    def test_summary_hash_and_last_line_must_match(self):
        result, _ = self.record(lambda *a, **kw: self.evidence(
            summary_change=lambda s, t: s.update(source_hash="old")))
        self.assertFalse(result["ok"])
        result, _ = self.record(lambda *a, **kw: self.command(self.evidence().stdout + "trailing garbage"))
        self.assertFalse(result["ok"])

    def test_invalid_arguments_fail_before_running_node(self):
        for values in ({"width": 1279}, {"fps": 2.5}, {"duration": float("nan")},
                       {"warmup": float("inf")}, {"preview": "false"}, {"source_hash": ""}):
            with self.subTest(values=values), patch.object(self.client._toolchain, "run_node") as run:
                result = self.client.record(output_dir=self.out, **values)
                self.assertFalse(result["ok"])
                run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
