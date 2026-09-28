"""Position-first motion regressions using explicit, self-contained JSON inputs."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.interpolate import CubicHermiteSpline, PchipInterpolator
from scipy.spatial.transform import Rotation

from operators.gen_motion.operator import GenMotionOperator
from operators.gen_motion.funcs.vibe_motion_utils import bvh, pipeline


FIXTURE_DIRECTORY = Path(__file__).parent / "vibe_motion_examples"
FIXTURE_NAMES = ("horse_gallop_stop_kick", "turn_jump_chop", "synthetic_chain")
VIDEO_OUTPUT_DIRECTORY = (
    Path(__file__).resolve().parents[1]
    / "test_data/outputs/_GPT6_astra_test/vibe_motion_refine260927"
)


def load_config(name):
    return json.loads((FIXTURE_DIRECTORY / f"{name}.json").read_text(encoding="utf-8"))


def event_time(config, value):
    return config["rhythm"]["events"][value] if isinstance(value, str) else value


def sample_scalar(config, curve, times):
    """Independent scalar interpolation oracle for fixture root trajectories."""
    keys = np.asarray([event_time(config, key) for key in curve["keys"]])
    values = np.asarray(curve["values"])
    times = np.asarray(times)
    method = curve["interpolation"]
    if method == "hermite":
        return CubicHermiteSpline(keys, values, curve["slopes"])(times)
    if method == "pchip":
        return PchipInterpolator(keys, values)(times)
    if method == "linear":
        return np.interp(times, keys, values)
    indices = np.clip(np.searchsorted(keys, times, side="right") - 1, 0, len(keys) - 2)
    phase = np.clip((times - keys[indices]) / (keys[indices + 1] - keys[indices]), 0, 1)
    phase = phase ** 3 * (10 - 15 * phase + 6 * phase ** 2)
    return values[indices] * (1 - phase) + values[indices + 1] * phase


def root_at(config, times):
    params = config["program"]["root"]["params"]
    rest = np.asarray(config["skeleton"]["rest"])
    root = config["skeleton"]["parents"].index(-1)
    offset = np.stack([sample_scalar(config, params[key], times) for key in ("x", "y", "z")], axis=-1)
    return rest[root] + params["scale"] * offset


def heading_at(config, time):
    yaw = np.deg2rad(sample_scalar(config, config["program"]["root"]["params"]["yaw"], time))
    cosine, sine = np.cos(yaw), np.sin(yaw)
    return np.array([[cosine, 0, sine], [0, 1, 0], [-sine, 0, cosine]])


def flexion_degrees(joints, indices):
    proximal = joints[:, indices[1]] - joints[:, indices[0]]
    distal = joints[:, indices[2]] - joints[:, indices[1]]
    cosine = np.einsum("ti,ti->t", proximal, distal)
    cosine /= np.linalg.norm(proximal, axis=1) * np.linalg.norm(distal, axis=1)
    return np.rad2deg(np.arccos(np.clip(cosine, -1, 1)))


def world_rotations(clip):
    """Compose local rotations independently of the production FK implementation."""
    quaternions = np.asarray(clip.quats)[..., [1, 2, 3, 0]]
    local = Rotation.from_quat(quaternions.reshape(-1, 4)).as_matrix()
    local = local.reshape(*quaternions.shape[:-1], 3, 3)
    world = np.empty_like(local)
    for joint in range(clip.num_joints):
        ancestors = []
        cursor = joint
        while cursor >= 0:
            ancestors.append(cursor)
            cursor = clip.template.parents[cursor]
        rotation = np.broadcast_to(np.eye(3), (clip.num_frames, 3, 3))
        for ancestor in reversed(ancestors):
            rotation = rotation @ local[:, ancestor]
        world[:, joint] = rotation
    return world


class VibeMotionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.configs = {name: load_config(name) for name in FIXTURE_NAMES}
        cls.results = {
            name: pipeline.generate_vibe_motion(config=deepcopy(config))
            for name, config in cls.configs.items()
        }

    def assert_bone_lengths(self, config, joints):
        rest = np.asarray(config["skeleton"]["rest"])
        parents = np.asarray(config["skeleton"]["parents"])
        children = np.flatnonzero(parents >= 0)
        expected = np.linalg.norm(rest[children] - rest[parents[children]], axis=-1)
        actual = np.linalg.norm(joints[:, children] - joints[:, parents[children]], axis=-1)
        np.testing.assert_allclose(actual, np.broadcast_to(expected, actual.shape), atol=1e-6, rtol=1e-6)

    def test_fixtures_are_finite_and_export_complete_bvh(self):
        required = {"bvh_bytes", "fps", "joints", "metrics", "residuals", "action", "frames", "notes", "targets", "contacts", "clip", "vibe_report_json"}
        for name, result in self.results.items():
            with self.subTest(fixture=name):
                config = self.configs[name]
                frames = round(config["rhythm"]["duration"] * config["rhythm"]["fps"]) + 1
                count = len(config["skeleton"]["names"])
                self.assertTrue(required <= result.keys())
                self.assertEqual(result["action"], config["program"]["action"])
                self.assertEqual(result["fps"], config["rhythm"]["fps"])
                self.assertEqual(result["frames"], frames)
                self.assertAlmostEqual((frames - 1) / result["fps"], config["rhythm"]["duration"])
                self.assertEqual(result["joints"].shape, (frames, count, 3))
                self.assertTrue(np.isfinite(result["joints"]).all())
                self.assertTrue(np.isfinite(result["clip"].quats).all())
                self.assertTrue(np.isfinite(result["clip"].trans).all())
                self.assert_bone_lengths(config, result["joints"])
                self.assertEqual(result["metrics"]["failures"], [])
                text = result["bvh_bytes"].decode("utf-8")
                hierarchy, motion_text = text.split("MOTION\n", 1)
                self.assertTrue(hierarchy.startswith("HIERARCHY\n"))
                for joint in config["skeleton"]["names"]:
                    self.assertIn(f" {joint}\n", hierarchy)
                rows = motion_text.splitlines()
                self.assertEqual(rows[0], f"Frames: {frames}")
                self.assertAlmostEqual(float(rows[1].split(":")[1]), 1 / result["fps"])
                self.assertEqual(len(rows) - 2, frames)
                channels = np.asarray([list(map(float, row.split())) for row in rows[2:]])
                self.assertEqual(channels.shape, (frames, 3 + 3 * count))
                self.assertTrue(np.isfinite(channels).all())
                self.assertEqual(bvh.clip_to_bvh_bytes(result["clip"]), result["bvh_bytes"])
                report = json.loads(result["vibe_report_json"])
                self.assertEqual(report["metrics"], result["metrics"])
                self.assertEqual(report["frames"], frames)
                self.assertEqual(report["fps"], result["fps"])
                json.dumps(report, allow_nan=False)

    def test_root_curves_and_ballistic_height_have_explicit_timing(self):
        for name, result in self.results.items():
            with self.subTest(fixture=name):
                config = self.configs[name]
                times = np.linspace(0, 1, result["frames"])
                root = config["skeleton"]["parents"].index(-1)
                np.testing.assert_allclose(result["joints"][:, root], root_at(config, times), atol=1e-6)
        config = self.configs["turn_jump_chop"]
        result = self.results["turn_jump_chop"]
        events = config["rhythm"]["events"]
        times = np.arange(result["frames"]) / result["fps"] / config["rhythm"]["duration"]
        airborne = (times[1:-1] > events["release_right"] + 1 / (result["frames"] - 1)) & (times[1:-1] < events["land_left"] - 1 / (result["frames"] - 1))
        root = config["skeleton"]["parents"].index(-1)
        acceleration = np.diff(result["joints"][:, root, 1], n=2) * result["fps"] ** 2
        self.assertGreater(int(airborne.sum()), 10)
        self.assertLess(float(acceleration[airborne].max()), 0)
        np.testing.assert_allclose(acceleration[airborne], acceleration[airborne].mean(), atol=2e-3, rtol=2e-3)

    def test_event_timing_changes_positions_not_frame_count(self):
        config = load_config("synthetic_chain")
        before = self.results["synthetic_chain"]
        config["rhythm"]["events"]["beat"] /= 2
        after = pipeline.generate_vibe_motion(config=config)
        self.assertEqual(before["frames"], after["frames"])
        self.assertFalse(np.allclose(before["targets"]["probe"], after["targets"]["probe"]))
        self.assertFalse(np.allclose(before["joints"], after["joints"]))
        peak = config["program"]["parts"]["probe"]["params"]["trajectory"]["values"][1]
        frame = round(config["rhythm"]["events"]["beat"] * (after["frames"] - 1))
        np.testing.assert_allclose(after["targets"]["probe"][frame], peak, atol=1e-7)

    def test_fps_changes_sampling_density_not_trajectories(self):
        for name in FIXTURE_NAMES:
            with self.subTest(fixture=name):
                config = load_config(name)
                config["rhythm"]["fps"] *= 2
                dense = pipeline.generate_vibe_motion(config=config)
                original = self.results[name]
                self.assertEqual(dense["frames"], 2 * (original["frames"] - 1) + 1)
                self.assertAlmostEqual((dense["frames"] - 1) / dense["fps"], config["rhythm"]["duration"])
                root = config["skeleton"]["parents"].index(-1)
                np.testing.assert_allclose(dense["joints"][::2, root], original["joints"][:, root], atol=1e-9)
                for part in original["targets"]:
                    np.testing.assert_allclose(dense["targets"][part][::2], original["targets"][part], atol=1e-9)
                for part in original["contacts"]:
                    np.testing.assert_array_equal(dense["contacts"][part][::2], original["contacts"][part])
                if name != "turn_jump_chop":
                    np.testing.assert_allclose(dense["joints"][::2], original["joints"], atol=1e-9)

    def test_parent_and_part_order_are_not_semantic(self):
        for name in ("synthetic_chain", "horse_gallop_stop_kick"):
            with self.subTest(fixture=name):
                config = load_config(name)
                spec = config["skeleton"]
                order = list(reversed(range(len(spec["names"]))))
                inverse = {old: new for new, old in enumerate(order)}
                spec["names"] = [spec["names"][old] for old in order]
                spec["rest"] = [spec["rest"][old] for old in order]
                spec["parents"] = [-1 if spec["parents"][old] < 0 else inverse[spec["parents"][old]] for old in order]
                config["program"]["parts"] = dict(reversed(list(config["program"]["parts"].items())))
                result = pipeline.generate_vibe_motion(config=config)
                np.testing.assert_allclose(result["joints"], self.results[name]["joints"][:, order], atol=1e-6)
                self.assert_bone_lengths(config, result["joints"])
                for part, target in result["targets"].items():
                    np.testing.assert_allclose(target, self.results[name]["targets"][part], atol=1e-6)

    def test_contact_targets_use_exact_anchors_and_remain_locked(self):
        for name in ("horse_gallop_stop_kick", "turn_jump_chop"):
            config, result = self.configs[name], self.results[name]
            times = np.linspace(0, 1, result["frames"])
            rest = np.asarray(config["skeleton"]["rest"])
            names = config["skeleton"]["names"]
            root = config["skeleton"]["parents"].index(-1)
            for part, definition in config["program"]["parts"].items():
                if definition["operator"] not in ("contact_path", "position"):
                    continue
                with self.subTest(fixture=name, part=part):
                    params = definition["params"]
                    expected_mask = np.zeros(result["frames"], dtype=bool)
                    expected_ids = np.full(result["frames"], -1, dtype=int)
                    tip = names.index(definition["joints"][-1])
                    for index, window in enumerate(params["contacts"]):
                        start, end = [event_time(config, value) for value in window]
                        mask = (times >= start - 1e-10) & (times <= end + 1e-10)
                        expected_mask |= mask
                        expected_ids[mask] = index
                        self.assertTrue(mask.any())
                        if definition["operator"] == "contact_path":
                            anchor_time = event_time(config, params["anchor_times"][index])
                            expected = root_at(config, anchor_time) + heading_at(config, anchor_time) @ (rest[tip] - rest[root])
                            expected[1] = params["ground_height"]
                        else:
                            trajectory = params["trajectory"]
                            keys = [event_time(config, key) for key in trajectory["keys"]]
                            expected = np.asarray(trajectory["values"])[keys.index(start)]
                        target = result["targets"][part][mask]
                        np.testing.assert_allclose(target, np.broadcast_to(expected, target.shape), atol=1e-6)
                        np.testing.assert_allclose(result["joints"][mask, tip], target, atol=1e-6)
                    self.assertEqual(result["contacts"][part].dtype.kind, "b")
                    np.testing.assert_array_equal(result["contacts"][part], expected_mask)
                    np.testing.assert_array_equal(result["contact_ids"][part], expected_ids)

    def test_contact_endpoints_are_inclusive_and_windows_keep_identity(self):
        config = load_config("synthetic_chain")
        config["rhythm"]["duration"] = 3
        config["rhythm"]["fps"] = 30
        params = config["program"]["parts"]["probe"]["params"]
        params["contacts"] = [[0, 0.7]]
        result = pipeline.generate_vibe_motion(config=config)
        boundary = round(0.7 * config["rhythm"]["duration"] * config["rhythm"]["fps"])
        self.assertTrue(result["contacts"]["probe"][boundary])
        self.assertFalse(result["contacts"]["probe"][boundary + 1])
        self.assertEqual(result["contact_ids"]["probe"][boundary], 0)
        self.assertEqual(result["contact_ids"]["probe"][boundary + 1], -1)
        config = load_config("synthetic_chain")
        config["rhythm"]["fps"] = 1
        params = config["program"]["parts"]["probe"]["params"]
        params["contacts"] = [[0, 0], [1, 1]]
        params["trajectory"]["values"][-1] = params["trajectory"]["values"][1]
        result = pipeline.generate_vibe_motion(config=config)
        np.testing.assert_array_equal(result["contacts"]["probe"], [True, True])
        np.testing.assert_array_equal(result["contact_ids"]["probe"], [0, 1])
        tip = config["skeleton"]["names"].index("tip")
        self.assertGreater(float(np.linalg.norm(np.diff(result["joints"][:, tip], axis=0))), 0.1)
        self.assertEqual(result["metrics"]["contact_pairs"], 0)
        self.assertEqual(result["metrics"]["contact_speed_max"], 0)
        self.assertNotIn("foot_skate", result["metrics"]["failures"])

    def test_contact_orientation_stays_locked_while_body_turns(self):
        config = self.configs["turn_jump_chop"]
        result = self.results["turn_jump_chop"]
        rotations = world_rotations(result["clip"])
        times = np.linspace(0, 1, result["frames"])
        rest = np.asarray(config["skeleton"]["rest"])
        names = config["skeleton"]["names"]
        yaw = sample_scalar(config, config["program"]["root"]["params"]["yaw"], times)
        for name in ("step_a", "step_b"):
            definition = config["program"]["parts"][name]
            params = definition["params"]
            ankle, tip = [names.index(joint) for joint in definition["joints"][-2:]]
            orientation = params["orientation"]
            for index, window in enumerate(params["contacts"]):
                with self.subTest(part=name, window=index):
                    start, end = [event_time(config, value) for value in window]
                    mask = (times >= start - 1e-10) & (times <= end + 1e-10)
                    degrees = sample_scalar(config, orientation, times[mask])
                    np.testing.assert_allclose(degrees, degrees[0], atol=1e-9)
                    expected = Rotation.from_euler("y", float(degrees[0]), degrees=True).as_matrix()
                    for joint in (ankle, tip):
                        actual = rotations[mask, joint]
                        np.testing.assert_allclose(actual, np.broadcast_to(expected, actual.shape), atol=1e-7)
                    expected_bone = expected @ (rest[tip] - rest[ankle])
                    actual_bone = result["joints"][mask, tip] - result["joints"][mask, ankle]
                    np.testing.assert_allclose(actual_bone, np.broadcast_to(expected_bone, actual_bone.shape), atol=1e-7)
                    if index == 0:
                        self.assertGreater(float(np.ptp(yaw[mask])), 1)

    def test_contact_swings_match_explicit_offsets(self):
        name = "horse_gallop_stop_kick"
        config, result = self.configs[name], self.results[name]
        times = np.linspace(0, 1, result["frames"])
        rest = np.asarray(config["skeleton"]["rest"])
        root = config["skeleton"]["parents"].index(-1)
        for part, definition in config["program"]["parts"].items():
            if definition["operator"] != "contact_path":
                continue
            params = definition["params"]
            tip = config["skeleton"]["names"].index(definition["joints"][-1])
            anchors = []
            for time in params["anchor_times"]:
                time = event_time(config, time)
                anchor = root_at(config, time) + heading_at(config, time) @ (rest[tip] - rest[root])
                anchor[1] = params["ground_height"]
                anchors.append(anchor)
            for index, arc in enumerate(params["arcs"]):
                with self.subTest(part=part, gap=index):
                    start = event_time(config, params["contacts"][index][1])
                    end = event_time(config, params["contacts"][index + 1][0])
                    frame = int(np.argmin(abs(times - (start + end) / 2)))
                    phase = (times[frame] - start) / (end - start)
                    blend = phase ** 3 * (10 - 15 * phase + 6 * phase ** 2)
                    expected = (1 - blend) * anchors[index] + blend * anchors[index + 1]
                    expected += heading_at(config, times[frame]) @ np.asarray(arc) * params["scale"] * np.sin(np.pi * phase) ** 2
                    np.testing.assert_allclose(result["targets"][part][frame], expected, atol=1e-6)

    def test_residuals_equal_actual_target_distances(self):
        for name, result in self.results.items():
            config = self.configs[name]
            maxima = []
            for part, target in result["targets"].items():
                with self.subTest(fixture=name, part=part):
                    tip_name = config["program"]["parts"][part]["joints"][-1]
                    tip = config["skeleton"]["names"].index(tip_name)
                    target = np.asarray(target)
                    self.assertEqual(target.shape, (result["frames"], 3))
                    self.assertTrue(np.isfinite(target).all())
                    actual = np.linalg.norm(result["joints"][:, tip] - target, axis=-1)
                    np.testing.assert_allclose(result["residuals"][part], actual, atol=1e-7, rtol=1e-6)
                    maxima.append(float(actual.max()))
            self.assertTrue(maxima)
            self.assertAlmostEqual(result["metrics"]["target_error_max"], max(maxima), places=6)
            if max(maxima) > config["program"]["quality"]["target_tolerance"]:
                self.assertTrue(result["metrics"]["failures"])

    def test_unreachable_target_is_not_silently_replaced(self):
        config = load_config("synthetic_chain")
        trajectory = config["program"]["parts"]["probe"]["params"]["trajectory"]
        trajectory["values"] = (np.asarray(trajectory["values"]) + [0, 10, 0]).tolist()
        original = deepcopy(config)
        result = pipeline.generate_vibe_motion(config=config)
        self.assertEqual(config, original)
        np.testing.assert_allclose(result["targets"]["probe"][0], trajectory["values"][0], atol=1e-7)
        tip = config["skeleton"]["names"].index("tip")
        actual = np.linalg.norm(result["joints"][:, tip] - result["targets"]["probe"], axis=-1)
        np.testing.assert_allclose(result["residuals"]["probe"], actual, atol=1e-7)
        self.assertGreater(float(actual.min()), 1)
        self.assertAlmostEqual(result["metrics"]["target_error_max"], float(actual.max()), places=6)
        self.assertTrue(result["metrics"]["failures"])
        self.assert_bone_lengths(config, result["joints"])

    def test_explicit_pole_and_flexion_bounds_control_geometry(self):
        config = load_config("synthetic_chain")
        part = config["program"]["parts"]["probe"]
        params = part["params"]
        params["trajectory"]["values"] = [params["trajectory"]["values"][0]] * len(params["trajectory"]["keys"])
        positive = pipeline.generate_vibe_motion(config=config)
        params["pole"] = (-np.asarray(params["pole"])).tolist()
        negative = pipeline.generate_vibe_motion(config=config)
        ids = [config["skeleton"]["names"].index(name) for name in part["joints"]]
        self.assertGreater(positive["joints"][0, ids[1], 2], 0)
        self.assertLess(negative["joints"][0, ids[1], 2], 0)
        np.testing.assert_allclose(positive["joints"][:, ids[-1]], negative["joints"][:, ids[-1]], atol=1e-6)
        params["flexion"] = [20, 40]
        bounded = pipeline.generate_vibe_motion(config=config)
        angles = flexion_degrees(bounded["joints"], ids)
        self.assertGreaterEqual(float(angles.min()), params["flexion"][0] - 1e-5)
        self.assertLessEqual(float(angles.max()), params["flexion"][1] + 1e-5)
        self.assertGreater(float(np.max(bounded["residuals"]["probe"])), config["program"]["quality"]["target_tolerance"])
        self.assert_bone_lengths(config, bounded["joints"])
        config = self.configs["turn_jump_chop"]
        result = self.results["turn_jump_chop"]
        dof_names = ("shoulder_flexion", "shoulder_abduction", "shoulder_swivel", "elbow_flexion")
        for name in ("guard", "strike"):
            with self.subTest(arm=name):
                part = config["program"]["parts"][name]
                diagnostic = result["diagnostics"][name]
                dofs = np.asarray(diagnostic["dofs_degrees"])
                self.assertEqual(dofs.shape, (result["frames"], len(dof_names)))
                self.assertTrue(np.isfinite(dofs).all())
                for index, dof in enumerate(dof_names):
                    low, high = part["params"]["limits"][dof]
                    self.assertGreaterEqual(float(dofs[:, index].min()), low - 1e-5)
                    self.assertLessEqual(float(dofs[:, index].max()), high + 1e-5)
                ids = [config["skeleton"]["names"].index(joint) for joint in part["joints"]]
                actual_flexion = flexion_degrees(result["joints"], ids)
                np.testing.assert_allclose(diagnostic["flex_degrees"], actual_flexion, atol=1e-5)
                np.testing.assert_allclose(dofs[:, -1], actual_flexion, atol=1e-5)
                self.assertEqual(np.asarray(diagnostic["solver_success"]).dtype.kind, "b")

    def test_quadruped_categories_do_not_depend_on_names(self):
        config = load_config("horse_gallop_stop_kick")
        mapping = {name: f"node_{index:02d}" for index, name in enumerate(config["skeleton"]["names"])}
        config["skeleton"]["names"] = list(mapping.values())
        parts = config["program"]["parts"]
        for definition in parts.values():
            definition["joints"] = [mapping[name] for name in definition["joints"]]
        lower = [part for part in parts.values() if part["category"] == "lower_limb"]
        self.assertEqual(len(lower), 4)
        config["program"]["parts"] = {f"block_{index}": part for index, part in enumerate(parts.values())}
        result = pipeline.generate_vibe_motion(config=config)
        np.testing.assert_allclose(result["joints"], self.results["horse_gallop_stop_kick"]["joints"], atol=1e-6)
        self.assertEqual(len(result["targets"]), 4)

    def test_aim_uses_position_targets_and_preserves_lengths(self):
        config = load_config("synthetic_chain")
        definition = config["program"]["parts"]["support"]
        rest = np.asarray(config["skeleton"]["rest"])
        first, child = [config["skeleton"]["names"].index(name) for name in definition["joints"]]
        desired = rest[child].copy()
        desired[0] += np.linalg.norm(rest[child] - rest[first])
        probe = config["program"]["parts"]["probe"]["params"]
        definition["operator"] = "aim"
        definition["params"] = {
            "targets": [{"keys": ["start", "end"], "values": [desired.tolist(), desired.tolist()], "interpolation": "linear"}],
            "space": "world", "scale": 1,
            "pole": probe["pole"], "rest_pole": probe["rest_pole"],
        }
        result = pipeline.generate_vibe_motion(config=config)
        actual = result["joints"][:, child] - result["joints"][:, first]
        expected = desired - result["joints"][:, first]
        actual /= np.linalg.norm(actual, axis=1, keepdims=True)
        expected /= np.linalg.norm(expected, axis=1, keepdims=True)
        np.testing.assert_allclose(actual, expected, atol=1e-6)
        self.assert_bone_lengths(config, result["joints"])

    def test_determinism_and_caller_input_preservation(self):
        for name in FIXTURE_NAMES:
            with self.subTest(fixture=name):
                config = load_config(name)
                original = deepcopy(config)
                result = pipeline.generate_vibe_motion(config=config)
                self.assertEqual(config, original)
                np.testing.assert_array_equal(result["joints"], self.results[name]["joints"])
                np.testing.assert_array_equal(result["clip"].quats, self.results[name]["clip"].quats)
                self.assertEqual(result["bvh_bytes"], self.results[name]["bvh_bytes"])
                self.assertEqual(result["vibe_report_json"], self.results[name]["vibe_report_json"])
                for part in result["targets"]:
                    np.testing.assert_array_equal(result["targets"][part], self.results[name]["targets"][part])

    def test_missing_required_config_and_operator_parameters_are_rejected(self):
        groups = {
            "synthetic_chain": [(), ("skeleton",), ("rhythm",), ("program",), ("program", "root"), ("program", "root", "params"), ("program", "solver"), ("program", "quality"), ("program", "parts", "probe", "params")],
            "horse_gallop_stop_kick": [("program", "parts", "fore_a", "params")],
            "turn_jump_chop": [("program", "parts", "guard", "params"), ("program", "parts", "guard", "params", "limits"), ("program", "parts", "step_a", "params"), ("program", "parts", "step_a", "params", "orientation")],
        }
        for fixture, paths in groups.items():
            for path in paths:
                base = load_config(fixture)
                source = base
                for key in path:
                    source = source[key]
                for missing in source:
                    with self.subTest(fixture=fixture, path=path, missing=missing):
                        config = deepcopy(base)
                        node = config
                        for key in path:
                            node = node[key]
                        del node[missing]
                        with self.assertRaises(ValueError):
                            pipeline.generate_vibe_motion(config=config)
        config = load_config("turn_jump_chop")
        del config["program"]["parts"]["guard"]["side"]
        with self.assertRaises(ValueError):
            pipeline.generate_vibe_motion(config=config)
        with self.assertRaises((TypeError, ValueError)):
            pipeline.generate_vibe_motion()

    def test_invalid_curves_and_hierarchy_are_rejected(self):
        mutations = [
            (("rhythm", "duration"), 1.01),
            (("rhythm", "fps"), False),
            (("rhythm", "events", "unused"), "start"),
            (("rhythm", "events", "unused"), "beat"),
            (("rhythm", "events", "beat"), "start"),
            (("rhythm", "events", "beat"), True),
            (("program", "solver", "epsilon"), 0),
            (("program", "forward"), [0, 1, 0]),
            (("skeleton", "parents"), [-1, 0, 3, 2, 3]),
            (("program", "parts", "probe", "joints"), ["socket", "tip", "hinge"]),
            (("program", "parts", "probe", "params", "pole"), [0, 0, 0]),
            (("program", "parts", "probe", "params", "flexion"), [145, 5]),
            (("program", "parts", "probe", "params", "trajectory", "interpolation"), "automatic"),
            (("program", "parts", "probe", "params", "trajectory", "keys"), [0, 0, 1]),
            (("program", "parts", "probe", "params", "trajectory", "values"), [[0, 0, 0], [1, 1, 1]]),
            (("program", "parts", "probe", "params", "trajectory", "values"), [[0, 0, 0], [1, float("nan"), 1], [0, 0, 0]]),
        ]
        for path, invalid in mutations:
            with self.subTest(path=path, invalid=invalid):
                config = load_config("synthetic_chain")
                node = config
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = invalid
                with self.assertRaises(ValueError):
                    pipeline.generate_vibe_motion(config=config)
        for field in ("interpolation", "slopes"):
            config = load_config("turn_jump_chop")
            del config["program"]["root"]["params"]["y"][field]
            with self.subTest(missing_curve_field=field), self.assertRaises(ValueError):
                pipeline.generate_vibe_motion(config=config)

    def test_malformed_skeleton_names_are_rejected(self):
        base = load_config("synthetic_chain")
        names = base["skeleton"]["names"]
        invalid_names = [
            None, 42, "origin", {name: index for index, name in enumerate(names)},
            names[:-1], names[:-1] + [names[0]], names[:-1] + [""],
            names[:-1] + [None], names[:-1] + [42], names[:-1] + [[names[-1]]],
        ]
        for invalid in invalid_names:
            with self.subTest(names=invalid):
                config = deepcopy(base)
                config["skeleton"]["names"] = invalid
                with self.assertRaises(ValueError):
                    pipeline.generate_vibe_motion(config=config)

    def test_legacy_presets_and_rotation_programs_are_rejected(self):
        for field, value in (("preset", "walk"), ("segments", [{"preset": "walk"}]), ("style", {"jump_ratio": 0.5})):
            config = load_config("synthetic_chain")
            config[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                pipeline.generate_vibe_motion(config=config)
        config = load_config("synthetic_chain")
        config["program"]["parts"]["probe"]["operator"] = "rotation"
        with self.assertRaises(ValueError):
            pipeline.generate_vibe_motion(config=config)
        with self.assertRaises((TypeError, ValueError)):
            pipeline.generate_vibe_motion(preset="walk")
        with tempfile.TemporaryDirectory() as directory:
            operator = GenMotionOperator(output_dir=directory)
            with self.assertRaises(ValueError):
                operator.run({"task_type": "vibe", "preset": "walk"})

    def test_operator_accepts_config_without_mesh(self):
        with tempfile.TemporaryDirectory() as directory:
            operator = GenMotionOperator(output_dir=directory)
            for name in FIXTURE_NAMES:
                with self.subTest(fixture=name):
                    task = {"task_type": "vibe", "task_id": name, "config": load_config(name)}
                    original = deepcopy(task)
                    with patch.object(pipeline, "generate_vibe_motion", wraps=pipeline.generate_vibe_motion) as generate:
                        output = operator.run(task)
                    generate.assert_called_once()
                    self.assertEqual(generate.call_args.kwargs["config"], task["config"])
                    self.assertIsNone(generate.call_args.kwargs.get("mesh"))
                    self.assertEqual(task, original)
                    self.assertEqual(Path(output["motion_bvh_path"]).read_bytes(), self.results[name]["bvh_bytes"])
                    np.testing.assert_array_equal(np.load(output["joints_npy_path"]), self.results[name]["joints"])
                    report = json.loads(Path(output["vibe_report_path"]).read_text(encoding="utf-8"))
                    self.assertEqual(report["metrics"], self.results[name]["metrics"])
                    self.assertIsNone(output.get("animated_glb_path"))
                    score = operator.eval(output, task)
                    self.assertEqual(score["vibe_metrics"], report["metrics"])
                    self.assertEqual(score["motion_valid"], not bool(report["metrics"]["failures"]))

    def test_fixtures_and_motion_utilities_have_no_chinese_text(self):
        utility = Path(pipeline.__file__).parent
        paths = list(utility.rglob("*.py")) + list(FIXTURE_DIRECTORY.glob("*.json"))
        for path in paths:
            with self.subTest(path=path):
                self.assertNotRegex(path.read_text(encoding="utf-8"), r"[\u3400-\u9fff]")


class BvhValidationTest(unittest.TestCase):
    def test_invalid_hierarchy_is_rejected(self):
        for parents in ([-1, 2, 1], [-1, 99, 0], [-1, 0.5, 0], [-1, -1, 0]):
            with self.subTest(parents=parents), self.assertRaises(ValueError):
                bvh.validate_hierarchy(parents, np.zeros((3, 3)))

    def test_quaternion_validation_and_large_finite_values(self):
        for quaternion in ([np.inf, 0, 0, 0], [0, 0, 0, 0], [np.nan, 0, 0, 1]):
            with self.subTest(quaternion=quaternion), self.assertRaises(ValueError):
                bvh.quats_to_zxy_degrees(np.asarray(quaternion))
        np.testing.assert_allclose(bvh.quats_to_zxy_degrees(np.array([1e308, 1e308, 0, 0])), [0, 90, 0], atol=1e-6)


def _preview_frames(config, result):
    """Render fixed-camera skeleton views; optional dependencies stay test-only."""
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1280, 768
    font = ImageFont.load_default(size=19)
    small = ImageFont.load_default(size=14)
    title = ImageFont.load_default(size=26)
    positions = result["joints"]
    parents = np.asarray(config["skeleton"]["parents"])
    children = np.flatnonzero(parents >= 0)
    root = int(np.flatnonzero(parents == -1)[0])
    colors = [(94, 192, 250), (248, 175, 83), (198, 156, 255), (242, 125, 148), (72, 212, 187)]
    joint_colors = [(210, 222, 239)] * len(parents)
    for index, spec in enumerate(config["program"]["parts"].values()):
        for joint in spec["joints"]:
            joint = config["skeleton"]["names"].index(joint) if isinstance(joint, str) else joint
            joint_colors[joint] = colors[index % len(colors)]
    points = np.concatenate([positions.reshape(-1, 3), *result["targets"].values()])
    lower, upper = points.min(axis=0), points.max(axis=0)
    ground = config["program"]["quality"]["ground_height"]
    lower[1] = min(lower[1], ground)
    span = max(float(np.max(upper - lower)), np.finfo(float).eps)
    lower[[0, 2]] -= span * 0.08
    upper[[0, 2]] += span * 0.08
    corners = np.array([[x, y, z] for x in (lower[0], upper[0])
                        for y in (lower[1], upper[1]) for z in (lower[2], upper[2])])
    grid = []
    for x in np.linspace(lower[0], upper[0], 9):
        grid.append(np.array([[x, ground, lower[2]], [x, ground, upper[2]]]))
    for z in np.linspace(lower[2], upper[2], 9):
        grid.append(np.array([[lower[0], ground, z], [upper[0], ground, z]]))
    panels = []
    for label, azimuth, elevation, left in (("Perspective", 35, 22, 20), ("Side", 90, 0, 650)):
        azimuth, elevation = np.deg2rad([azimuth, elevation])
        right = np.array([np.cos(azimuth), 0, -np.sin(azimuth)])
        up = np.array([np.sin(azimuth) * np.sin(elevation), np.cos(elevation),
                       np.cos(azimuth) * np.sin(elevation)])
        axes = np.column_stack([right, up])
        projected = corners @ axes
        center = (projected.min(axis=0) + projected.max(axis=0)) / 2
        size = projected.max(axis=0) - projected.min(axis=0)
        scale = min(550 / max(size[0], span * 0.1), 410 / max(size[1], span * 0.1))
        panels.append((label, left, axes, center, scale))
    events = sorted(config["rhythm"]["events"].items(), key=lambda item: item[1])
    for frame, joints in enumerate(positions):
        image = Image.new("RGB", (width, height), (13, 20, 33))
        draw = ImageDraw.Draw(image)
        draw.text((24, 18), config["program"]["action"], font=title, fill=(238, 245, 255))
        draw.text((24, 53), "POSITION TRAJECTORIES  /  RHYTHM  /  INVERSE KINEMATICS", font=small, fill=(135, 162, 188))
        for label, left, axes, center, scale in panels:
            def project(values):
                return (np.asarray(values) @ axes - center) * [scale, -scale] + [left + 305, 350]

            def line(values, color, weight):
                draw.line([tuple(p) for p in project(values)], fill=color, width=weight)

            draw.rounded_rectangle((left, 88, left + 610, 617), radius=12,
                                   fill=(20, 31, 47), outline=(43, 62, 81))
            draw.text((left + 18, 105), f"{label} | Y-up | fixed camera", font=font, fill=(203, 219, 236))
            for segment in grid:
                line(segment, (35, 51, 67), 1)
            line(positions[:, root], (61, 86, 111), 2)
            for target in result["targets"].values():
                line(target, (59, 75, 92), 1)
            depth = joints @ np.cross(axes[:, 0], axes[:, 1])
            for child in children[np.argsort(depth[children])]:
                line(joints[[parents[child], child]], joint_colors[child], 6)
            for joint, (x, y) in enumerate(project(joints)):
                draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=joint_colors[joint])
            for key, target in result["targets"].items():
                x, y = project(target[frame])
                draw.rectangle((x - 4, y - 4, x + 4, y + 4), outline=(244, 242, 201), width=2)
                if key in result["contacts"] and result["contacts"][key][frame]:
                    actual_x, actual_y = project(joints[result["target_joints"][key]])
                    draw.ellipse((actual_x - 9, actual_y - 9, actual_x + 9, actual_y + 9),
                                 outline=(104, 244, 158), width=3)
            draw.text((left + 18, 585), "Square: IK target    Green ring: contact", font=small, fill=(150, 172, 192))
        fraction = frame / (len(positions) - 1)
        event = next((key for key, value in reversed(events) if value <= fraction), "start")
        draw.text((24, 633), f"{frame / result['fps']:.3f}s / {config['rhythm']['duration']:.3f}s"
                  f"   |   frame {frame + 1}/{len(positions)}   |   {event}", font=font, fill=(224, 236, 249))
        draw.line((24, 678, 1256, 678), fill=(57, 75, 94), width=6)
        for _, value in events:
            x = 24 + 1232 * value
            draw.line((x, 670, x, 686), fill=(146, 166, 187), width=2)
        cursor = 24 + 1232 * fraction
        draw.ellipse((cursor - 7, 671, cursor + 7, 685), fill=(96, 219, 224))
        error = max((float(values[frame]) for values in result["residuals"].values()), default=0.0)
        draw.text((24, 701), f"Target error: {error:.3g} world units   |   Skeleton preview, not a skinned character",
                  font=small, fill=(152, 177, 199))
        yield image


def evaluate_bound_motion(mesh, rig, weights, config, poles):
    """Reuse the explicit position/IK action on a newly fitted bind, with fixed skin weights."""
    from operators.gen_motion.funcs.vibe_motion_utils.motion_utils.units import fk, quat_to_matrix
    from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.skin_units import deform
    action = load_config(config['action']['example'])
    original = action['skeleton']
    rest = np.asarray(original['rest'])
    mapping = config['action']['joint_map']
    scale = config['action']['scale']
    if set(mapping) != set(original['names']) or set(mapping.values()) != set(rig.joint_names):
        raise ValueError('Action binding requires a complete one-to-one joint mapping')
    for part, spec in action['program']['parts'].items():
        old = [original['names'].index(n) for n in spec['joints']]
        ids = [rig.joint_names.index(mapping[n]) for n in spec['joints']]
        spec['joints'] = [mapping[n] for n in spec['joints']]
        p = spec['params']
        if spec['operator'] == 'position' and p['space'] == 'world':
            p['trajectory']['values'] = (rig.joints[ids[-1]] + (np.asarray(p['trajectory']['values'])-rest[old[-1]])*scale).tolist()
        elif spec['operator'] == 'arm_arc':
            p['scale'] = float(np.linalg.norm(np.diff(rig.joints[ids], axis=0), axis=1).sum())
        if part in poles:
            p['rest_pole'] = list(poles[part])
    action['program']['root']['params']['scale'] *= scale
    action['program']['quality']['ground_height'] = config['action']['ground_height']
    action['skeleton'] = {'name': rig.name, 'names': rig.joint_names, 'parents': rig.parents.tolist(), 'rest': rig.joints.tolist()}
    result = pipeline.generate_vibe_motion(config=action)
    positions, quaternions = fk(result['clip'])
    rotations = quat_to_matrix(quaternions)
    matrices = np.broadcast_to(np.eye(4), (*positions.shape[:2], 4, 4)).copy()
    matrices[..., :3, :3] = rotations
    matrices[..., :3, 3] = positions-np.einsum('tnij,nj->tni', rotations, rig.joints)
    vertices = np.asarray([deform(mesh.vertices, weights.weights, m) for m in matrices])
    edges = np.unique(np.sort(mesh.faces[:, [[0,1],[1,2],[2,0]]].reshape(-1,2), axis=1), axis=0)
    length = np.linalg.norm(mesh.vertices[edges[:,0]]-mesh.vertices[edges[:,1]], axis=1)
    valid = length > action['program']['solver']['epsilon']
    ratio = np.linalg.norm(vertices[:,edges[valid,0]]-vertices[:,edges[valid,1]], axis=-1)/length[valid]
    low, twist_quantile, high = config['action']['metric_quantiles']
    stretch, compression = float(np.quantile(ratio, high)), float(np.quantile(ratio, low))
    twist = []
    for spec in config['probe_parts'].values():
        ids = [rig.joint_names.index(n) for n in spec['chain']]
        for joint, child in zip(ids, ids[1:]):
            axis = rig.joints[child]-rig.joints[joint]
            axis /= np.linalg.norm(axis)
            component = quaternions[:,joint,1:] @ axis
            valid = np.hypot(component,quaternions[:,joint,0]) > action['program']['solver']['epsilon']
            if valid.any():
                twist.append(float(np.quantile(2*np.arctan2(abs(component[valid]),abs(quaternions[valid,joint,0])), twist_quantile))/np.pi)
    steps = 2*np.arccos(np.clip(abs(np.sum(quaternions[1:]*quaternions[:-1],axis=-1)),0,1))
    foot_error = max((result['residual_summary'][name] for name in config['probe_parts']), default=0.)/mesh.scale
    terms = {'log_strain_p99': max(abs(np.log(stretch)),abs(np.log(max(compression,np.finfo(float).tiny)))),
             'twist_p95': max(twist,default=0.), 'rotation_step': float(steps.max())/np.pi,
             'foot_error': foot_error, 'ground_penetration': float(max(0,action['program']['quality']['ground_height']-vertices[...,1].min()))/mesh.scale}
    score = sum(config['score_weights'][k]*v for k,v in terms.items())
    metrics = {'score_terms': terms, 'edge_stretch_p99': stretch, 'edge_stretch_max': float(ratio.max()),
               'edge_compression_p01': compression, 'motion': result['metrics']}
    return {'score': score, 'metrics': metrics, 'payload': (action, result, vertices)}


def export_bound_motion(mesh, rig, weights, selected, config, output, report, *, ffmpeg):
    from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.export import animated_glb
    from test.test_vibe_rigging import glb_document, rig_preview, write_video
    import struct
    action, result, vertices = selected['payload']
    data = animated_glb(mesh, rig, weights, result['clip'], config=config['export']['glb'])
    path = output / 'turn_jump_chop.glb'
    path.write_bytes(data)
    data = path.read_bytes()
    doc = glb_document(data)
    start = 28 + struct.unpack_from('<I', data, 12)[0]
    def accessor(index, width):
        item = doc['accessors'][index]
        view = doc['bufferViews'][item['bufferView']]
        dtype = {5126:'<f4',5123:'<u2',5125:'<u4'}[item['componentType']]
        return np.frombuffer(data, dtype=dtype, count=item['count']*width,
                             offset=start+view.get('byteOffset',0)+item.get('byteOffset',0)).reshape(-1,width)
    inverse = accessor(doc['skins'][0]['inverseBindMatrices'],16).reshape(-1,4,4).transpose(0,2,1)
    tolerance = config['readback_tolerance']
    np.testing.assert_allclose(inverse[:,:3,3],-rig.joints,atol=tolerance,rtol=0)
    attributes = doc['meshes'][0]['primitives'][0]['attributes']
    indices, sparse = accessor(attributes['JOINTS_0'],4), accessor(attributes['WEIGHTS_0'],4)
    dense = np.zeros_like(weights.weights)
    np.add.at(dense, (np.arange(len(dense))[:,None], indices), sparse)
    np.testing.assert_allclose(dense,weights.weights,atol=tolerance,rtol=0)
    np.testing.assert_allclose(accessor(attributes['POSITION'],3),mesh.vertices,atol=tolerance,rtol=0)
    for channel in doc['animations'][0]['channels']:
        sampler = doc['animations'][0]['samplers'][channel['sampler']]
        np.testing.assert_allclose(accessor(sampler['input'],1).ravel(),np.arange(result['frames'])/result['fps'],atol=tolerance,rtol=0)
        joint = channel['target']['node']-2
        expected = result['clip'].quats[:,joint][:,[1,2,3,0]] if channel['target']['path']=='rotation' else rig.joints[joint]+result['clip'].trans
        np.testing.assert_allclose(accessor(sampler['output'],expected.shape[1]),expected,atol=tolerance,rtol=0)
    (output / 'turn_jump_chop.bvh').write_bytes(result['bvh_bytes'])
    (output / 'action_config.json').write_text(json.dumps(action,indent=2))
    (output / 'motion_report.json').write_text(result['vibe_report_json'])
    np.savez_compressed(output / 'motion.npz',positions=result['joints'],quats=result['clip'].quats,
                        translations=result['clip'].trans,vertices=vertices,fps=result['fps'])
    preview = config['preview']
    if ffmpeg is not None:
        frames = (rig_preview(mesh,rig,vertices[i],result['joints'][i],report,azimuth=25,settings=preview)
                  for i in range(result['frames']))
        write_video(frames,output/'turn_jump_chop.mp4',ffmpeg=ffmpeg,width=preview['width'],height=preview['height'],fps=result['fps'])
    return {'readback_passed':True,'frames':result['frames'],'bind_recomputed':True,'fixed_weights':True}


def export_example_videos(*, ffmpeg, output_dir=VIDEO_OUTPUT_DIRECTORY, names=FIXTURE_NAMES):
    """Write real H.264 previews only when explicitly requested, never during tests."""
    from test.test_vibe_rigging import write_video

    output_dir = Path(output_dir).resolve()
    names = tuple(names)
    if not names or len(set(names)) != len(names) or any(name not in FIXTURE_NAMES for name in names):
        raise ValueError("Select unique known motion examples")
    for name in names:
        for suffix in (".mp4", "_report.json"):
            if (output_dir / f"{name}{suffix}").exists():
                raise FileExistsError(f"Refusing to overwrite {output_dir / (name + suffix)}")
    output_dir.mkdir(parents=True, exist_ok=True)
    videos = []
    for name in names:
        config = load_config(name)
        result = pipeline.generate_vibe_motion(config=config)
        target = output_dir / f"{name}.mp4"
        write_video(_preview_frames(config, result), target, ffmpeg=ffmpeg,
                    width=1280, height=768, fps=result["fps"])
        (output_dir / f"{name}_report.json").write_text(result["vibe_report_json"], encoding="utf-8")
        videos.append(target)
    return videos


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "export-videos":
        import argparse

        parser = argparse.ArgumentParser(description="Export position-first skeleton motion previews")
        parser.add_argument("--ffmpeg", required=True)
        parser.add_argument("--output-dir", type=Path, default=VIDEO_OUTPUT_DIRECTORY)
        parser.add_argument("--examples", nargs="+", choices=FIXTURE_NAMES, default=FIXTURE_NAMES)
        args = parser.parse_args(sys.argv[2:])
        for video in export_example_videos(ffmpeg=args.ffmpeg, output_dir=args.output_dir, names=args.examples):
            print(video)
    else:
        unittest.main()
