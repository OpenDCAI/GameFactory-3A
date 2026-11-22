"""Motion generation regressions (JSON program -> clip) for ``vibe_motion_utils``.

    python -m unittest test.test_vibe_motion
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from scipy.spatial.transform import Rotation

from operators.gen_motion.operator import GenMotionOperator
from operators.gen_motion.funcs.vibe_motion_utils import bvh, motion, pipeline
from operators.gen_motion.funcs.vibe_motion_utils.motion_utils import (
    MotionProgram, MotionStyle, Rhythm, SkeletonRig, asset, mesh_checks)
from operators.gen_motion.funcs.vibe_motion_utils.motion_utils.generate import generate_motion
from operators.gen_motion.funcs.vibe_motion_utils.motion_utils.program import ease, effector_events, sample_curve
from operators.gen_motion.funcs.vibe_motion_utils.motion_utils.timing import key_curve
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils import skin_mesh
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.export import animated_glb
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.mesh import CreatureMesh
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.probe import (
    edge_stretch, joint_axes, probe_motion, probe_report)
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.types import RigResult


FIXTURE_DIRECTORY = Path(__file__).parent / "vibe_motion_examples"
FIXTURE_NAMES = ("horse_gallop_stop_kick", "turn_jump_chop", "synthetic_chain", "male02_landing",
                 "fox_crouch_tail_spring")
STYLE = dict(turn_degrees=0, travel_ratio=0, jump_ratio=0, crouch_ratio=0, arm_reach=.5, arm_clearance=.1)
REFINE = {"iterations": 0, "foot_margin_ratio": 0., "residual_weight": 1., "ground_weight": 5.}
LIMITS = {"shoulder_flexion": [-180, 180], "shoulder_abduction": [-90, 90], "shoulder_swivel": [-60, 60],
          "elbow_flexion": [2, 165], "target_back_ratio": 1.0, "regularization": 0.005, "max_nfev": 60}


def load_config(name):
    """Example config; ``base`` / ``motion`` wrappers are kept as written."""
    return json.loads((FIXTURE_DIRECTORY / f"{name}.json").read_text(encoding="utf-8"))


def flat_config(name):
    return pipeline.normalize_config(load_config(name))


def C(value):
    return {"initial": value}


def rot(joints, cat="trunk", space="parent", **kw):
    return {"category": cat, "joints": joints, "operator": "rotation",
            "params": {"space": space, "weights": [round(1 / len(joints), 6)] * len(joints),
                       "pitch": kw.get("pitch", C(0)), "twist": kw.get("twist", C(0)), "roll": kw.get("roll", C(0))}}


def arm(side, angle=170, radius=.9, lateral=.3, **extra):
    return {"category": "upper_limb", "side": side, "joints": [f"upperarm.{side}", f"forearm.{side}", f"hand.{side}"],
            "operator": "arm_arc", "params": {"angle": C(angle) if not isinstance(angle, dict) else angle,
                                              "radius": C(radius),
                                              "lateral": C(lateral) if not isinstance(lateral, dict) else lateral,
                                              "preferred_swivel_degrees": 15, "limits": deepcopy(LIMITS), **extra}}


def leg(side, **params):
    p = {"pole": [0, .3, 1], "flexion": [-5, 150]}
    p.update(params)
    if "plants" not in p and "gait" not in p and "keys" not in p:
        p["plants"] = [{"start": 0, "end": "end", "anchor": "start"}]
    return {"category": "lower_limb", "side": side,
            "joints": [f"thigh.{side}", f"shin.{side}", f"foot.{side}", f"toe.{side}"],
            "operator": "effector", "params": p}


def human(parts=None, root=None, refine=None, dur=2.0, phases=None, beats=None):
    base = {"pelvis": rot(["hips"]), "torso": rot(["spine", "chest", "chest_upper"]),
            "head": rot(["neck", "head"], "head"), "armL": arm("L"), "armR": arm("R"), "legL": leg("L"),
            "legR": leg("R")}
    base.update(parts or {})
    r = {"x": C(0), "y": C(0), "z": C(0), "yaw": C(0)}
    r.update(root or {})
    rhythm = {"duration": dur, "fps": 30, "events": {}, "time_unit": "seconds", "phases": phases or {}}
    if beats:
        rhythm["beats"] = beats
    return {"rhythm": rhythm, "style": STYLE,
            "program": {"action": "test", "forward": [0, 0, 1], "root": {"operator": "curves", "params": r},
                        "parts": base, "refine": {**REFINE, **(refine or {})}}}


def make_rig(config):
    skeleton = config["skeleton"]
    return SkeletonRig(skeleton["parents"], skeleton["rest"], skeleton["names"])


def run(rig, spec):
    rhythm = Rhythm(**spec["rhythm"])
    style = MotionStyle(**spec["style"], program=MotionProgram(**deepcopy(spec["program"])))
    return generate_motion(rig, rhythm=rhythm, style=style)


def idx(rig, name):
    return [n.get("name") for n in rig.nodes].index(name)


def angle(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    c = np.sum(a * b, -1) / np.linalg.norm(a, axis=-1) / np.linalg.norm(b, axis=-1)
    return np.degrees(np.arccos(np.clip(c, -1, 1)))


R = Rhythm(duration=4., fps=30, events={}, time_unit="seconds")
T = R.sample()


class CurveAndRhythmTest(unittest.TestCase):
    def test_interp_modes_and_eases(self):
        base = {"keys": [0, 1, 2, 4], "values": [0, 10, 0, 5]}
        lin = sample_curve({**base, "interp": "linear"}, R, None, np.array([.5, 3.]))
        np.testing.assert_allclose(lin, [5, 2.5])
        smooth = sample_curve({**base, "interp": "smooth"}, R, None, np.array([.999, 1., 1.001]))
        self.assertLess(abs(smooth[2] - smooth[0]), 1e-3)
        cubic = sample_curve({**base, "interp": "cubic"}, R, None, np.array([0, 1, 2, 4.]))
        np.testing.assert_allclose(cubic, [0, 10, 0, 5])
        step = sample_curve({**base, "interp": "step"}, R, None, np.array([.99, 1.5]))
        np.testing.assert_allclose(step, [0, 10])
        mixed = sample_curve({**base, "eases": [None, "linear", "back_out"]}, R, None, np.array([1.5, 3.5]))
        self.assertAlmostEqual(mixed[0], 5)
        self.assertGreater(mixed[1], 5)
        default = sample_curve(base, R, None, T)
        np.testing.assert_allclose(default, key_curve(T / 4, np.array([0, .25, .5, 1.]), np.array([0, 10, 0, 5.])))

    def test_new_eases_end_at_targets(self):
        p = np.linspace(0, 1, 201)
        for name in ("sine", "back_in", "back_out", "back_in_out", "elastic", "bounce", "snap"):
            e = ease(p, name)
            self.assertLess(abs(e[0]), 1e-9, name)
            self.assertLess(abs(e[-1] - 1), 1e-6, name)
        self.assertLess(ease(p, "back_in").min(), -.05)
        self.assertGreater(ease(p, "back_out").max(), 1.05)

    def test_rate_add_pattern_noise(self):
        rate = {"rate": {"keys": [0, 2, 4], "values": [90, 90, 0], "interp": "linear"}}
        full = sample_curve(rate, R, None, T)
        self.assertAlmostEqual(full[T == 2.][0], 180, delta=.1)
        self.assertAlmostEqual(full[-1], 270, delta=.2)
        np.testing.assert_allclose(sample_curve(rate, R, None, np.array([3.])), full[np.isclose(T, 3.)], atol=1e-9)
        add = sample_curve({"add": [C(2), {"keys": [0, 4], "values": [0, 4], "interp": "linear"}]}, R, None,
                           np.array([1.]))
        self.assertAlmostEqual(add[0], 3)
        pattern = {"pattern": {"keys": [0, .25, 1], "values": [0, 10, 0], "interp": "linear"}, "start": 1, "end": 3,
                   "count": 4}
        values = sample_curve(pattern, R, None, np.array([.5, 1., 1.125, 1.5, 1.625, 3.5]))
        np.testing.assert_allclose(values, [0, 0, 10, 0, 10, 0])
        noise = {"initial": 0, "oscillators": [{"start": 0, "end": 4, "amplitude": 2, "frequency": 3,
                                                "shape": "noise", "seed": 7}]}
        n1, n2 = sample_curve(noise, R, None, T), sample_curve(noise, R, None, T)
        np.testing.assert_allclose(n1, n2)
        self.assertTrue(0 < np.abs(n1).max() < 4.5 and np.std(np.diff(n1)) > 0)

    def test_style_references_scale_with_style(self):
        style = MotionStyle(**{**STYLE, "turn_degrees": 90}, program=None)
        values = sample_curve({"keys": [0, 4], "values": [0, "$turn_degrees"], "interp": "linear"}, R, style,
                              np.array([2.]))
        self.assertAlmostEqual(values[0], 45)
        half = sample_curve({"keys": [0, 4], "values": [0, {"style": "turn_degrees", "factor": .5}],
                             "interp": "linear"}, R, style, np.array([4.]))
        self.assertAlmostEqual(half[0], 45)
        with self.assertRaises(ValueError):
            sample_curve({"initial": "$unknown"}, R, style, np.array([0.]))

    def test_time_unit_fraction_matches_seconds(self):
        seconds = Rhythm(duration=2., fps=30, events={"mid": 1.}, time_unit="seconds")
        fraction = Rhythm(duration=2., fps=30, events={"mid": .5})
        self.assertEqual(fraction.time_unit, "fraction")
        self.assertAlmostEqual(seconds.seconds("mid"), fraction.seconds("mid"))
        self.assertAlmostEqual(seconds.seconds(1.), 1.)
        self.assertAlmostEqual(fraction.seconds(.5), 1.)
        with self.assertRaises(ValueError):
            Rhythm(duration=2., fps=30, events={}, time_unit="frames")

    def test_beats_generate_phases(self):
        r = Rhythm(duration=4., fps=30, events={}, time_unit="seconds", phases={"go": [0.5, 3.5]},
                   beats={"stomp": {"start": "go.start", "end": "go.end", "count": 3},
                          "sw": {"start": 0, "period": .5, "count": 4, "swing": .2}})
        self.assertAlmostEqual(r.seconds("stomp[2].mid"), 2.0)
        self.assertAlmostEqual(r.seconds("stomp[3].end"), 3.5)
        self.assertAlmostEqual(r.window({"phase": "sw[1]"})[1], .6)
        self.assertAlmostEqual(r.window({"phase": "sw[2]"})[1], 1.0)
        segment = {"segments": [{"phase": "stomp[2]", "to": 1.}], "initial": 0}
        self.assertAlmostEqual(sample_curve(segment, r, None, np.array([2.5]))[0], 1.)
        with self.assertRaises(ValueError):
            Rhythm(duration=1., fps=30, events={}, time_unit="seconds",
                   beats={"b": {"start": 0, "period": .5, "count": 3}})


class ExampleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.configs = {name: flat_config(name) for name in FIXTURE_NAMES}
        cls.results = {name: pipeline.generate_vibe_motion(config=load_config(name)) for name in FIXTURE_NAMES}

    def test_examples_are_finite_and_export_complete_bvh(self):
        required = {"bvh_bytes", "fps", "joints", "metrics", "action", "frames", "notes", "clip", "vibe_report_json"}
        for name, result in self.results.items():
            with self.subTest(example=name):
                config = self.configs[name]
                frames = len(Rhythm(**config["rhythm"]).sample())
                names = config["skeleton"]["names"]
                self.assertTrue(required <= result.keys())
                self.assertEqual(result["action"], config["program"]["action"])
                self.assertEqual(result["fps"], config["rhythm"]["fps"])
                self.assertEqual(result["frames"], frames)
                self.assertEqual(result["joints"].shape, (frames, len(names), 3))
                self.assertTrue(np.isfinite(result["joints"]).all())
                self.assertTrue(np.isfinite(result["clip"].quats).all())
                self.assertTrue(np.isfinite(result["clip"].trans).all())
                self.assertEqual(result["metrics"]["failures"], [])
                self.assertLess(result["metrics"]["bone_length_max_error"], 1e-6 * max(
                    1., float(np.ptp(np.asarray(config["skeleton"]["rest"]), axis=0).max())))
                hierarchy, motion_text = result["bvh_bytes"].decode("utf-8").split("MOTION\n", 1)
                self.assertTrue(hierarchy.startswith("HIERARCHY\n"))
                for joint in names:
                    self.assertIn(f" {joint}\n", hierarchy)
                rows = motion_text.splitlines()
                self.assertEqual(rows[0], f"Frames: {frames}")
                self.assertAlmostEqual(float(rows[1].split(":")[1]), 1 / result["fps"])
                channels = np.asarray([list(map(float, row.split())) for row in rows[2:]])
                self.assertEqual(channels.shape, (frames, 3 + 3 * len(names)))
                self.assertEqual(bvh.clip_to_bvh_bytes(result["clip"]), result["bvh_bytes"])
                report = json.loads(result["vibe_report_json"])
                self.assertEqual(report["metrics"], result["metrics"])
                json.dumps(report, allow_nan=False)

    def test_wrapped_config_equals_flat_config(self):
        wrapped = load_config("male02_landing")
        self.assertEqual(set(wrapped), {"base", "skeleton", "motion"})
        self.assertIn("video", wrapped["motion"])
        flat = flat_config("male02_landing")
        self.assertNotIn("base", flat)
        again = pipeline.generate_vibe_motion(config=flat)
        self.assertEqual(again["bvh_bytes"], self.results["male02_landing"]["bvh_bytes"])

    def test_pipeline_matches_reference_generator(self):
        config = self.configs["turn_jump_chop"]
        rig = make_rig(config)
        result, _, _ = run(rig, config)
        np.testing.assert_allclose(self.results["turn_jump_chop"]["joints"], result["positions"], atol=1e-9)

    def test_input_is_preserved_and_output_deterministic(self):
        config = load_config("turn_jump_chop")
        before = deepcopy(config)
        first = pipeline.generate_vibe_motion(config=config)
        self.assertEqual(config, before)
        self.assertEqual(first["bvh_bytes"], pipeline.generate_vibe_motion(config=config)["bvh_bytes"])

    def test_operator_accepts_config_without_mesh(self):
        with tempfile.TemporaryDirectory() as directory:
            operator = GenMotionOperator(output_dir=directory)
            for name in FIXTURE_NAMES:
                with self.subTest(example=name):
                    task = {"task_type": "vibe", "task_id": name, "config": load_config(name)}
                    original = deepcopy(task)
                    with patch.object(pipeline, "generate_vibe_motion",
                                      wraps=pipeline.generate_vibe_motion) as generate:
                        output = operator.run(task)
                    generate.assert_called_once()
                    self.assertIsNone(generate.call_args.kwargs.get("mesh"))
                    self.assertEqual(task, original)
                    self.assertEqual(Path(output["motion_bvh_path"]).read_bytes(), self.results[name]["bvh_bytes"])
                    np.testing.assert_array_equal(np.load(output["joints_npy_path"]), self.results[name]["joints"])
                    report = json.loads(Path(output["vibe_report_path"]).read_text(encoding="utf-8"))
                    self.assertEqual(report["metrics"], self.results[name]["metrics"])
                    self.assertIsNone(output.get("animated_glb_path"))
                    score = operator.eval(output, task)
                    self.assertEqual(score["vibe_metrics"], report["metrics"])
                    self.assertTrue(score["motion_valid"])

    def test_legacy_arguments_and_missing_config_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            operator = GenMotionOperator(output_dir=directory)
            for task in ({"task_type": "vibe"}, {"task_type": "vibe", "preset": "walk"},
                         {"task_type": "vibe", "config": {"skeleton": {}}}):
                with self.subTest(task=task), self.assertRaises(ValueError):
                    operator.run(task)

    def test_no_chinese_text_in_sources_and_examples(self):
        utility = Path(pipeline.__file__).parent
        paths = list(utility.rglob("*.py")) + list(FIXTURE_DIRECTORY.glob("*.json"))
        for path in paths:
            with self.subTest(path=path.name):
                self.assertNotRegex(path.read_text(encoding="utf-8"), r"[\u3400-\u9fff]")


class OperatorTest(unittest.TestCase):
    """Rig-based operators on the human and quadruped example skeletons (skeleton only, no mesh)."""

    @classmethod
    def setUpClass(cls):
        cls.male = make_rig(flat_config("male02_landing"))
        cls.fox = make_rig(flat_config("fox_crouch_tail_spring"))

    def test_gait_and_heading_travel(self):
        male = self.male
        spec = human(dur=3., root={"forward_speed": C(1.0), "yaw": {"rate": C(30)}},
                     parts={"legL": leg("L", lift=.25, gait={"start": 0, "end": 3, "period": 1., "duty": .6, "offset": 0.}),
                            "legR": leg("R", lift=.25, gait={"start": 0, "end": 3, "period": 1., "duty": .6, "offset": .5})})
        result, blocks, report = run(male, spec)
        root = result["root"]
        self.assertAlmostEqual(root["yaw_degrees"][-1], 90, delta=.5)
        radius = 1.0 / np.deg2rad(30) * blocks.leg_length
        travel = np.linalg.norm(root["position"][-1] - root["position"][0])
        self.assertAlmostEqual(travel, radius * np.sqrt(2), delta=.02 * radius * np.sqrt(2))
        contact = result["tracks"].contacts["legL"]
        self.assertTrue(0.5 < contact.mean() < 0.75)
        self.assertLess(report["metrics"]["contact_foot_max_speed"]["legL"], .05 * blocks.leg_length * 30)

    def test_aim_keys_and_target(self):
        fox = self.fox
        tail = ["tail.0", "tail.1", "tail.2", "tail.3"]
        up, back_up = [0, 1, 0], [0, .7, -.7]
        spec = deepcopy(flat_config("fox_crouch_tail_spring"))
        spec["rhythm"]["duration"] = 1.5
        parts = spec["program"]["parts"]
        parts["tail"] = {"category": "tail", "joints": tail, "operator": "aim", "params": {
            "frame": "body", "keys": [{"t": 0, "dirs": [back_up] * 4}, {"t": 1., "dirs": [up] * 4}]}}
        spec["program"]["root"]["params"] = {"x": C(0), "y": C(0), "z": C(0),
                                             "yaw": {"keys": [0, 1.5], "values": [0, 60]}}
        result, blocks, _ = run(fox, spec)
        positions, heading = result["positions"], result["root"]["heading"]
        ids = [idx(fox, j) for j in tail + ["tail.4"]]
        for frame, want in ((0, back_up), (len(positions) - 1, up)):
            for a, b in zip(ids, ids[1:]):
                self.assertLess(angle(positions[frame, b] - positions[frame, a], heading[frame] @ np.asarray(want, float)), 1.0)
        look = deepcopy(spec)
        look["program"]["parts"]["tail"]["params"] = {"target": {"joint": "head", "offset": [0, .5, 0]}}
        result, blocks, _ = run(fox, look)
        positions = result["positions"]
        target = positions[:, idx(fox, "head")] + np.einsum("tij,j->ti", result["root"]["heading"],
                                                            [0, .5 * blocks.leg_length, 0])
        tip = positions[:, idx(fox, "tail.4")] - positions[:, idx(fox, "tail.3")]
        self.assertLess(angle(tip, target - positions[:, idx(fox, "tail.3")]).max(), 1.0)

    def test_post_ik_orientation_wrist_swivel(self):
        male = self.male
        pitch = {"keys": [0, 2], "values": [0, 60]}
        spec = human(parts={"armL": arm("L", angle={"keys": [0, 2], "values": [170, 60]},
                                        wrist={"space": "world", "pitch": pitch}),
                            "armR": arm("R", angle=100, swivel={"keys": [0, 2], "values": [-50, 50]})})
        result, blocks, _ = run(male, spec)
        world = result["world"]
        j = idx(male, "hand.L")
        want = (blocks.frame[None] @ Rotation.from_euler("x", np.linspace(0, 60, len(world)), degrees=True).as_matrix()
                @ blocks.frame.T @ male.rest_world[j, :3, :3])
        error = Rotation.from_matrix(np.swapaxes(world[:, j, :3, :3], -1, -2) @ want).magnitude()
        self.assertLess(np.degrees(error).max(), .5)
        elbow = result["positions"][:, idx(male, "forearm.R")]
        self.assertGreater(np.linalg.norm(elbow[0] - elbow[-1]), .1 * blocks.leg_length)
        hand = result["positions"][:, idx(male, "hand.R")]
        self.assertLess(np.linalg.norm(hand[0] - hand[-1]), .02 * blocks.leg_length)
        leaf = human(parts={"armL": arm("L", angle={"keys": [0, 2], "values": [170, 80]}),
                            "handend": {**rot(["hand_end.L"], "upper_limb", "world", twist={"initial": 40}), "side": "L"}})
        result, blocks, _ = run(male, leaf)
        j = idx(male, "hand_end.L")
        want = (blocks.frame @ Rotation.from_euler("y", 40, degrees=True).as_matrix() @ blocks.frame.T
                @ male.rest_world[j, :3, :3])
        error = Rotation.from_matrix(np.swapaxes(result["world"][:, j, :3, :3], -1, -2) @ want).magnitude()
        self.assertLess(np.degrees(error).max(), .5)

    def test_end_rotation_and_spline_keys(self):
        male = self.male
        keys = [{"t": 0, "offset": [0, 0, 0]}, {"t": .5, "offset": [0, .3, .3]}, {"t": 1., "offset": [0, .4, .0]},
                {"t": 1.5, "offset": [0, .3, -.3]}, {"t": 2., "offset": [0, 0, 0]}]
        spec = human(parts={"legL": leg("L", keys=keys, interp="spline",
                                        end_rotation={"twist": {"keys": [0, 2], "values": [0, 30]}})})
        result, blocks, _ = run(male, spec)
        speed = np.linalg.norm(np.diff(result["tracks"].feet["legL"], axis=0), axis=-1)
        self.assertGreater(speed[int(round(1.0 * 30)) - 1], .3 * speed.max())
        j = idx(male, "foot.L")
        rel = Rotation.from_matrix(result["world"][-1, j, :3, :3] @ male.rest_world[j, :3, :3].T)
        m = blocks.frame.T @ rel.as_matrix() @ blocks.frame
        self.assertAlmostEqual(np.degrees(np.arctan2(m[0, 2], m[0, 0])), 30, delta=1.)

    def test_world_translate_and_follow(self):
        male = self.male
        prop = {"category": "upper_limb", "side": "L", "joints": ["hand_end.L"], "operator": "translate",
                "params": {"space": "world", "x": C(.5), "y": {"keys": [0, 2], "values": [0, 1]}, "z": C(0)}}
        spec = human(parts={"armL": arm("L", angle={"keys": [0, 2], "values": [170, 20]}), "prop": prop})
        result, blocks, _ = run(male, spec)
        j = idx(male, "hand_end.L")
        want = blocks.rest[j] + blocks.frame @ (np.array([.5, 1., 0]) * blocks.leg_length)
        self.assertLess(np.linalg.norm(result["positions"][-1, j] - want), 1e-6)
        prop["params"]["follow"] = {"joint": "hand.R", "weight": {"keys": [0, 1, 2], "values": [0, 0, 1]}}
        result, blocks, _ = run(male, human(parts={"prop": prop}))
        grip = np.linalg.inv(male.rest_world[idx(male, "hand.L")]) @ male.rest_world[j]
        carried = result["world"][-1, idx(male, "hand.R")] @ grip
        self.assertLess(np.linalg.norm(result["positions"][-1, j] - carried[:3, 3]), 1e-6)

    def test_spring_ground_and_collision(self):
        fox = self.fox
        spec = flat_config("fox_crouch_tail_spring")
        tail = ["tail.0", "tail.1", "tail.2", "tail.3", "tail.4"]
        ids = [idx(fox, j) for j in tail]
        ground = float(fox.rest_world[list(fox.joints), 1, 3].min())
        result, _, _ = run(fox, spec)
        self.assertGreaterEqual(result["positions"][:, ids, 1].min(), ground - 1e-6)
        free = deepcopy(spec)
        del free["program"]["parts"]["tail"]["params"]["ground"]
        result2, _, _ = run(fox, free)
        self.assertLess(result2["positions"][:, ids, 1].min(), ground)
        hit = deepcopy(spec)
        hit["program"]["parts"]["tail"]["params"]["collide"] = ["hL", "hR"]
        result3, _, _ = run(fox, hit)
        self.assertTrue(np.isfinite(result3["positions"]).all())

    def test_smooth_despike(self):
        male = self.male
        jump = {"keys": [0, 1., 1.034, 1.067, 2], "values": [0, 0, 80, 0, 0], "interp": "linear"}
        raw, _, _ = run(male, human(parts={"head": rot(["neck", "head"], "head", pitch=jump)}))
        smoothed, _, _ = run(male, human(parts={"head": rot(["neck", "head"], "head", pitch=jump)},
                                         refine={"smooth": {"parts": ["head"], "sigma_frames": 1.,
                                                            "despike_degrees": 20}}))
        j = idx(male, "head")

        def step(result):
            q = result["quats"][:, j]
            return np.degrees(2 * np.arccos(np.clip(np.abs(np.sum(q[1:] * q[:-1], -1)), 0, 1))).max()
        self.assertLess(step(smoothed), .5 * step(raw))

    def test_root_parent_space_keeps_root_yaw(self):
        male = self.male
        spec = human(root={"yaw": {"keys": [0, 2], "values": [0, 90]}}, parts={"pelvis": rot(["hips"], twist=C(0))})
        result, blocks, _ = run(male, spec)
        j = idx(male, "hips")
        rel = blocks.frame.T @ result["world"][-1, j, :3, :3] @ male.rest_world[j, :3, :3].T @ blocks.frame
        self.assertAlmostEqual(np.degrees(np.arctan2(rel[0, 2], rel[0, 0])), 90, delta=.5)

    def test_gait_plant_merge_lock_and_pre_ik_smoothing(self):
        male = self.male
        legs = {s: leg(s, lift=.2, plant_orient="lock", plants=[{"start": 0, "end": "walk.start", "anchor": "start"}],
                       gait={"phase": "walk", "period": 1., "duty": .6, "offset": off})
                for s, off in (("L", 0.), ("R", .5))}
        spec = human(dur=3., phases={"walk": [.3, 2.8]},
                     root={"y": C(-.09),
                           "forward_speed": {"segments": [{"start": .3, "end": .8, "to": .6}], "initial": 0},
                           "yaw": {"rate": {"segments": [{"start": .3, "end": .8, "to": 30}], "initial": 0}}},
                     parts={"legL": legs["L"], "legR": legs["R"],
                            "torso": rot(["spine", "chest", "chest_upper"], twist={"initial": 0, "oscillators": [
                                {"phase": "walk", "amplitude": 8, "frequency": 1, "shape": "noise"}]})},
                     refine={"smooth": {"parts": ["torso"], "sigma_frames": 2.}})
        _, plants = effector_events(spec["program"]["parts"]["legR"]["params"], Rhythm(**spec["rhythm"]))
        self.assertTrue(plants[0][0] == 0 and plants[0][1] > .3 + 1e-6)
        result, blocks, report = run(male, spec)
        self.assertLess(max(report["metrics"]["contact_foot_max_speed"].values()), .02 * blocks.leg_length)
        j = idx(male, "foot.L")
        contact = result["tracks"].contacts["legL"]
        runs = np.flatnonzero(np.diff(np.r_[0, contact.astype(int), 0]))
        a, b = runs[2], runs[3] - 1
        rel = Rotation.from_matrix(np.swapaxes(result["world"][a, j, :3, :3], -1, -2) @ result["world"][b, j, :3, :3])
        self.assertLess(np.degrees(rel.magnitude()), .5)

    def test_signed_bend_weights_fold_fore_leg(self):
        fox = self.fox
        chain = ["shoulder.L", "elbow.L", "wrist.L", "paw.L"]
        for weights, key in (([1, 1], "same"), ([1, -1], "z")):
            spec = flat_config("fox_crouch_tail_spring")
            spec["program"]["root"]["params"]["y"] = C(0)
            spec["rhythm"]["duration"] = 1.
            spec["program"]["parts"] = {"trunk": rot(["hips", "spine", "chest"]), "fL": {
                "category": "lower_limb", "side": "L", "joints": chain, "operator": "effector", "params": {
                    "pole": "rest", "flexion": [[-110, 110], [-110, 110]], "tip": 0, "bend_weights": weights,
                    "keys": [{"t": 0, "offset": [0, 0, 0]}, {"t": 1, "offset": [0, .45, 0]}]}}}
            result, blocks, _ = run(fox, spec)
            self.assertLess(result["residuals"]["fL"].max(), .01 * blocks.leg_length, key)
            pos, rest = result["positions"][-1], fox.rest_world[:, :3, 3]
            n = blocks.frame[:, 0]

            def bend(points, a, b, c):
                u, v = points[b] - points[a], points[c] - points[b]
                return np.degrees(np.arctan2(np.cross(u, v) @ n, u @ v))
            ids = [idx(fox, j) for j in chain]
            d = [bend(pos, *ids[i:i + 3]) - bend(rest, *ids[i:i + 3]) for i in range(2)]
            self.assertEqual(np.sign(d[0]) != np.sign(d[1]), key == "z", (key, d))

    def test_effector_plants_hold_world_position(self):
        male = self.male
        spec = human(root={"yaw": {"keys": [0, 2], "values": [0, 40]}})
        result, blocks, report = run(male, spec)
        positions = result["positions"][:, idx(male, "toe.L")]
        tolerance = report["metrics"]["ik_max_residual"]["legL"] + 1e-6
        self.assertLess(np.abs(positions - positions[0]).max(), tolerance)
        self.assertLess(tolerance, .01 * blocks.leg_length)

    def test_parts_may_use_category_aliases(self):
        spec = human()
        alias = deepcopy(spec)
        alias["program"]["parts"]["torso"]["category"] = "\u8eaf\u5e72"
        np.testing.assert_allclose(run(self.male, spec)[0]["positions"], run(self.male, alias)[0]["positions"])


class ValidationTest(unittest.TestCase):
    def build(self, spec):
        r = Rhythm(**spec["rhythm"])
        return r, MotionStyle(**spec["style"], program=MotionProgram(**deepcopy(spec["program"])))

    def test_validation_messages(self):
        bad = human(parts={"x": {"category": "tail", "joints": ["tail"], "operator": "aim", "params": {}}})
        r, style = self.build(bad)
        with self.assertRaisesRegex(ValueError, "exactly one of keys / target"):
            style.program.validate(r, None)
        g = human(parts={"legL": leg("L", gait={"start": 0, "end": 2, "period": 1, "duty": 1.2})})
        r, style = self.build(g)
        with self.assertRaisesRegex(ValueError, "duty"):
            style.program.validate(r, None)

    def test_defaults_fill_optional_effector_fields(self):
        rig = make_rig(flat_config("male02_landing"))
        spec = human(parts={"legL": {"category": "lower_limb", "side": "L",
                                     "joints": ["thigh.L", "shin.L", "foot.L", "toe.L"], "operator": "effector",
                                     "params": {"pole": "rest", "flexion": [-5, 150]}}})
        result, _, report = run(rig, spec)
        self.assertTrue(report["metrics"]["finite"])
        self.assertLess(report["metrics"]["ik_max_residual"]["legL"], 1e-9)

    def test_required_fields_and_unknown_fields_are_rejected(self):
        spec = human()
        for change in ("pole", "flexion", "unknown_field"):
            bad = deepcopy(spec)
            params = bad["program"]["parts"]["legL"]["params"]
            if change == "unknown_field":
                params["unknown_field"] = 1
            else:
                del params[change]
            r, style = self.build(bad)
            with self.subTest(change=change), self.assertRaises(ValueError):
                style.program.validate(r, None)
        for key in ("iterations", "foot_margin_ratio", "residual_weight", "ground_weight"):
            bad = deepcopy(spec)
            del bad["program"]["refine"][key]
            with self.subTest(refine=key), self.assertRaises(ValueError):
                self.build(bad)[1].program.validate(self.build(bad)[0], None)

    def test_malformed_skeletons_are_rejected(self):
        config = flat_config("synthetic_chain")
        for key, value in (("names", ["a", "a", "b", "c", "d"]), ("names", ["a b", "c", "d", "e", "f"]),
                           ("parents", [-1, 0, 2, 1, 0]), ("parents", [-1, -1, 0, 2, 3])):
            bad = deepcopy(config)
            bad["skeleton"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                pipeline.generate_vibe_motion(config=bad)

    def test_pipeline_rejects_missing_and_legacy_config(self):
        config = flat_config("synthetic_chain")
        for key in ("skeleton", "rhythm", "style", "program"):
            bad = deepcopy(config)
            del bad[key]
            with self.subTest(missing=key), self.assertRaises(ValueError):
                pipeline.generate_vibe_motion(config=bad)
        bad = {**deepcopy(config), "preset": "walk"}
        with self.assertRaises(ValueError):
            pipeline.generate_vibe_motion(config=bad)


MESH_SKIN = {"kernel": "inverse", "falloff": 4.0, "radius_scale": 2.0, "max_influences": 3, "floor": 0.0001,
             "smooth_iterations": 2, "smooth_rate": 0.5, "bone_convention": "outgoing",
             "distance_epsilon_ratio": 1e-6, "sum_tolerance": 1e-9, "screening": 0.08, "edge_epsilon_ratio": 1e-6}
MESH_GLB = {"bind_tolerance": 1e-9, "sum_tolerance": 1e-9, "material": None, "interpolation": "LINEAR"}


def column_mesh():
    """A tall box (9 rings x 4 sides) around the Y axis, welded and triangulated."""
    rings, sides = 9, 4
    vertices = []
    for ring in range(rings):
        y = -1.0 + 2.0 * ring / (rings - 1)
        for k in range(sides):
            angle_ = 2 * np.pi * k / sides + np.pi / 4
            vertices.append([0.3 * np.cos(angle_), y, 0.3 * np.sin(angle_)])
    faces = []
    for ring in range(rings - 1):
        for k in range(sides):
            a, b = ring * sides + k, ring * sides + (k + 1) % sides
            c, d = a + sides, b + sides
            faces += [[a, b, d], [a, d, c]]
    top, bottom = len(vertices), len(vertices) + 1
    vertices += [[0, 1, 0], [0, -1, 0]]
    for k in range(sides):
        faces.append([(rings - 1) * sides + k, (rings - 1) * sides + (k + 1) % sides, top])
        faces.append([(k + 1) % sides, k, bottom])
    vertices = np.asarray(vertices, float)
    return CreatureMesh("column", vertices, np.asarray(faces, np.int64), np.arange(len(vertices))), vertices


def spine_motion(*, y_values=(0, 0), refine=None):
    skeleton = {"name": "spine", "names": ["s0", "s1", "s2"], "parents": [-1, 0, 1],
                "rest": [[0, -0.8, 0], [0, 0, 0], [0, 0.8, 0]]}
    rhythm = {"duration": 1.0, "fps": 12, "time_unit": "seconds", "events": {}}
    program = {
        "action": "sway", "forward": [0, 0, 1],
        "root": {"operator": "curves", "params": {
            "x": C(0), "y": {"keys": [0, 1], "values": list(y_values), "interp": "linear"}, "z": C(0), "yaw": C(0)}},
        "parts": {"spine": {"category": "trunk", "joints": ["s0", "s1", "s2"], "operator": "rotation", "params": {
            "space": "parent", "weights": [0, 1, 1], "pitch": {"keys": [0, 1], "values": [0, 25], "interp": "linear"},
            "twist": C(0), "roll": C(0)}}},
        "refine": {**REFINE, **(refine or {})}}
    return skeleton, rhythm, program


class MeshFeedbackTest(unittest.TestCase):
    """Skin-feedback refinement, probe motion, capsule depth and GLB readback on a skinned mesh."""

    @classmethod
    def setUpClass(cls):
        cls.mesh, _ = column_mesh()
        cls.skeleton, rhythm, program = spine_motion()
        cls.result = motion.generate_clip(cls.skeleton, rhythm=rhythm, style=STYLE, program=program)
        cls.rig = RigResult(name="spine", joints=np.asarray(cls.skeleton["rest"], float),
                            parents=np.asarray(cls.skeleton["parents"]), joint_names=list(cls.skeleton["names"]),
                            chains={"spine": [0, 1, 2]})
        cls.skin = skin_mesh(cls.mesh, cls.rig, config=MESH_SKIN)

    def test_probe_motion_is_explicit_and_finite(self):
        config = {"fps": 12, "seconds": 2, "amp": 20.0, "cycles": 2}
        times, rotations, translations = probe_motion(self.rig, config)
        self.assertEqual(rotations.shape, (24, 3, 4))
        self.assertTrue(np.isfinite(rotations).all())
        np.testing.assert_allclose(np.linalg.norm(rotations, axis=-1), 1.0, atol=1e-9)
        self.assertEqual(translations, {})
        first, second = joint_axes(self.rig)
        self.assertAlmostEqual(abs(float(first[1] @ second[1])), 0.0, places=9)
        for bad in ({"fps": 12, "seconds": 2, "amp": 20.0}, {**config, "extra": 1}, {**config, "fps": 0},
                    {**config, "joints": {"s1": {"type": "spin"}}},
                    {**config, "joints": {"ghost": {"type": "oscillate"}}}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                probe_motion(self.rig, bad)

    def test_probe_report_measures_edge_stretch(self):
        config = {"fps": 12, "seconds": 2, "amp": 20.0, "cycles": 2}
        report = probe_report(self.mesh, self.rig, self.skin, config, influences=3)
        self.assertTrue(report["finite"])
        self.assertGreaterEqual(report["edge_stretch"]["max"], 1.0)
        rest = np.asarray(self.mesh.vertices, float)
        stretch = edge_stretch(rest, [rest], np.array([[0, 1], [1, 2]]))
        self.assertAlmostEqual(stretch["max"], 1.0)

    def test_skinned_generation_reports_mesh_metrics(self):
        skeleton, rhythm, program = spine_motion(refine={"iterations": 1})
        result = motion.generate_clip(skeleton, rhythm=rhythm, style=STYLE, program=program, mesh=self.mesh,
                                      skin=self.skin)
        metrics = result.report["metrics"]
        self.assertTrue(metrics["finite"])
        self.assertEqual(result.result["vertices"].shape, (result.clip.num_frames, self.mesh.num_vertices, 3))
        np.testing.assert_allclose(result.result["vertices"][0], self.mesh.vertices, atol=1e-9)
        self.assertGreaterEqual(metrics["edge_stretch_max"], metrics["edge_stretch_p99"])

    def test_body_ground_lifts_skinned_vertices(self):
        ground = float(self.mesh.vertices[:, 1].min())
        lowest = {}
        for lift in (False, True):
            skeleton, rhythm, program = spine_motion(
                y_values=(0, -.5), refine={"iterations": 2, "body_ground": {"lift": lift}})
            result = motion.generate_clip(skeleton, rhythm=rhythm, style=STYLE, program=program, mesh=self.mesh,
                                          skin=self.skin)
            lowest[lift] = float(result.result["vertices"][..., 1].min())
        self.assertLess(lowest[False], ground - 1e-3)
        self.assertGreater(lowest[True], lowest[False])

    def test_capsule_depth_pushes_points_out(self):
        a, b = np.zeros((1, 1, 3)), np.array([[[0., 1., 0.]]])
        points = np.array([[[0.05, 0.5, 0.0], [1.0, 0.5, 0.0]]])
        depth, push = mesh_checks.capsule_depth(points, a, b, np.array([0.2]))
        self.assertAlmostEqual(float(depth[0, 0]), 0.15)
        self.assertLess(float(depth[0, 1]), 0.0)
        np.testing.assert_allclose(push[0, 0], [0.15, 0.0, 0.0], atol=1e-9)
        np.testing.assert_allclose(push[0, 1], 0.0)

    def test_glb_readback_matches_the_clip_and_detects_tampering(self):
        glb = animated_glb(self.mesh, self.rig, self.skin, self.result.clip, config=MESH_GLB)
        report = asset.verify_export(glb, self.result.clip, self.mesh, self.skin, self.rig,
                                     position_tolerance=1e-5, time_tolerance=1e-6)
        self.assertEqual(report["frames_checked"], self.result.clip.num_frames)
        self.assertTrue(report["bind_mesh_preserved"])
        tampered = deepcopy(self.result.clip)
        tampered.quats[3, 1] = np.array([0.7071068, 0.7071068, 0.0, 0.0])
        with self.assertRaises(ValueError):
            asset.verify_export(glb, tampered, self.mesh, self.skin, self.rig, position_tolerance=1e-5,
                                time_tolerance=1e-6)


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


def evaluate_bound_motion(mesh, rig, weights, config, poles):
    """Run an example action on a new bind (fixed skin weights) and score the skinned result."""
    from operators.gen_motion.funcs.vibe_motion_utils.motion_utils.units import fk, quat_to_matrix
    from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.skin_units import deform
    action = flat_config(config["action"]["example"])
    original = action["skeleton"]
    mapping = config["action"]["joint_map"]
    if set(mapping) != set(original["names"]) or set(mapping.values()) != set(rig.joint_names):
        raise ValueError("Action binding requires a complete one-to-one joint mapping")
    for key, spec in action["program"]["parts"].items():
        spec["joints"] = [mapping[n] for n in spec["joints"]]
        if key in poles and spec["operator"] in ("foot_swing", "contact_path"):
            spec["params"]["rest_pole"] = list(poles[key])
    skeleton = {"name": rig.name, "names": list(rig.joint_names), "parents": rig.parents.tolist(),
                "rest": rig.joints.tolist()}
    result = motion.generate_clip(skeleton, rhythm=action["rhythm"], style=action["style"],
                                  program=action["program"], mesh=mesh, skin=weights)
    clip, report = result.clip, result.report
    positions, quaternions = fk(clip)
    rotations = quat_to_matrix(quaternions)
    matrices = np.broadcast_to(np.eye(4), (*positions.shape[:2], 4, 4)).copy()
    matrices[..., :3, :3] = rotations
    matrices[..., :3, 3] = positions - np.einsum("tnij,nj->tni", rotations, rig.joints)
    vertices = np.asarray([deform(mesh.vertices, weights.weights, m) for m in matrices])
    edges = np.unique(np.sort(mesh.faces[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1), axis=0)
    length = np.linalg.norm(mesh.vertices[edges[:, 0]] - mesh.vertices[edges[:, 1]], axis=1)
    epsilon = np.finfo(float).eps
    valid = length > epsilon
    ratio = np.linalg.norm(vertices[:, edges[valid, 0]] - vertices[:, edges[valid, 1]], axis=-1) / length[valid]
    low, twist_quantile, high = config["action"]["metric_quantiles"]
    stretch, compression = float(np.quantile(ratio, high)), float(np.quantile(ratio, low))
    twist = []
    for spec in config["probe_parts"].values():
        ids = [rig.joint_names.index(n) for n in spec["chain"]]
        for joint, child in zip(ids, ids[1:]):
            axis = rig.joints[child] - rig.joints[joint]
            axis /= np.linalg.norm(axis)
            component = quaternions[:, joint, 1:] @ axis
            valid = np.hypot(component, quaternions[:, joint, 0]) > epsilon
            if valid.any():
                twist.append(float(np.quantile(2 * np.arctan2(abs(component[valid]), abs(quaternions[valid, joint, 0])),
                                               twist_quantile)) / np.pi)
    steps = 2 * np.arccos(np.clip(abs(np.sum(quaternions[1:] * quaternions[:-1], axis=-1)), 0, 1))
    residuals = report["metrics"]["ik_max_residual"]
    foot_error = max((residuals[name] for name in config["probe_parts"] if name in residuals), default=0.) / mesh.scale
    ground = float(mesh.vertices[:, 1].min())
    terms = {"log_strain_p99": max(abs(np.log(stretch)), abs(np.log(max(compression, np.finfo(float).tiny)))),
             "twist_p95": max(twist, default=0.), "rotation_step": float(steps.max()) / np.pi,
             "foot_error": foot_error,
             "ground_penetration": float(max(0, ground - vertices[..., 1].min())) / mesh.scale}
    score = sum(config["score_weights"][k] * v for k, v in terms.items())
    metrics = {"score_terms": terms, "edge_stretch_p99": stretch, "edge_stretch_max": float(ratio.max()),
               "edge_compression_p01": compression, "motion": report["metrics"]}
    return {"score": score, "metrics": metrics, "payload": (action, result, vertices)}


def export_bound_motion(mesh, rig, weights, selected, config, output, report, *, ffmpeg):
    from operators.gen_motion.funcs.vibe_motion_utils.motion_utils.asset import verify_export
    from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.export import animated_glb as write_glb
    from test.test_vibe_rigging import rig_preview, write_video
    action, result, vertices = selected["payload"]
    name = config["action"]["example"]
    clip = result.clip
    data = write_glb(mesh, rig, weights, clip, config=config["export"]["glb"])
    (output / f"{name}.glb").write_bytes(data)
    verify_export(data, clip, mesh, weights, rig, position_tolerance=config["readback_tolerance"],
                  time_tolerance=config["readback_tolerance"])
    (output / f"{name}.bvh").write_bytes(bvh.clip_to_bvh_bytes(clip))
    (output / "action_config.json").write_text(json.dumps(action, indent=2))
    (output / "motion_report.json").write_text(json.dumps(result.report, indent=2, default=lambda v: np.asarray(v).tolist()))
    np.savez_compressed(output / "motion.npz", positions=result.result["positions"], quats=clip.quats,
                        translations=clip.trans, vertices=vertices, fps=clip.fps)
    preview = config["preview"]
    if ffmpeg is not None:
        frames = (rig_preview(mesh, rig, vertices[i], result.result["positions"][i], report, azimuth=25,
                              settings=preview) for i in range(clip.num_frames))
        write_video(frames, output / f"{name}.mp4", ffmpeg=ffmpeg, width=preview["width"],
                    height=preview["height"], fps=clip.fps)
    return {"readback_passed": True, "frames": clip.num_frames, "bind_recomputed": True, "fixed_weights": True}


if __name__ == "__main__":
    unittest.main()
