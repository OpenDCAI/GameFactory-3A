"""Regression checks for local procedural motion and skinned GLB export.

Edit MOTION_TASKS below to supply the same task dictionaries an LLM sends to
GenMotionOperator.run. Use target_mesh_path for a real compatible character;
creature="human" supplies a procedural fixture. Tests do not implement blending.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import struct
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

import numpy as np

from operators.gen_motion.operator import GenMotionOperator
from operators.gen_motion.funcs.vibe_motion_utils import (
    bvh,
    motion,
    motion_utils,
    skeleton,
    skinning,
)
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.export import animated_glb
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.skin_units import pose_matrices, deform


MOTION_TASKS: tuple[dict[str, Any], ...] = (
    {
        "task_type": "vibe",
        "task_id": "walk_custom",
        "preset": "walk",
        "num_frames": 120,
        "fps": 60.0,
        "heading_deg": 90.0,
        "motion_overrides": {
            "steps": 3,
            "step_length": 0.35,
            "foot_height": 0.18,
        },
    },
    {
        "task_type": "vibe",
        "task_id": "walk_then_boxing",
        "creature": "human",
        "fps": 30.0,
        "segments": [
            {
                "preset": "walk",
                "num_frames": 120,
                "motion_overrides": {"steps": 3, "step_length": 0.35},
            },
            {
                "preset": "boxing",
                "num_frames": 120,
                "motion_overrides": {"combo": [0, 1, 0, 1], "reach": 0.92},
            },
            {
                "preset": "turn_jump_chop",
                "num_frames": 144,
                "motion_overrides": {"turn_deg": 180.0, "jump_height": 0.4},
            },
        ],
        "transitions": [
            {"transition_frames": 12, "carry_facing": True},
            {"transition_frames": 16, "carry_facing": True},
        ],
    },
)


class VibeMotionTest(unittest.TestCase):
    """Check function independence, numeric contracts and operator artifacts."""

    def test_all_presets(self):
        plan = motion.build_plan()
        for preset in motion.list_presets():
            with self.subTest(preset=preset):
                result = motion.generate_clip(preset, plan)
                self.assertEqual(motion.clip_metrics(result, plan)["failures"], [])
                self.assertTrue(np.isfinite(motion.joint_positions(result.clip)).all())
                text = bvh.clip_to_bvh(result.clip)
                rows = text.split("MOTION\n", 1)[1].splitlines()
                self.assertEqual(len(rows) - 2, result.clip.num_frames)
                self.assertTrue(all(len(line.split()) == 3 + 3 * result.clip.num_joints for line in rows[2:]))

    def test_parameterized_operator_tasks(self):
        for task in MOTION_TASKS:
            with self.subTest(task=task["task_id"]), tempfile.TemporaryDirectory() as directory:
                original = deepcopy(task)
                op = GenMotionOperator(output_dir=directory)
                with (
                    patch.object(skeleton, "fit_skeleton", wraps=skeleton.fit_skeleton) as fit,
                    patch.object(skinning, "skin_mesh", wraps=skinning.skin_mesh) as skin,
                    patch.object(motion, "build_plan", wraps=motion.build_plan) as build,
                ):
                    result = op.run(task)
                self.assertEqual(task, original)
                build.assert_called_once()
                has_mesh = bool(task.get("creature") or task.get("target_mesh_path"))
                self.assertEqual(fit.call_count, int(has_mesh))
                self.assertEqual(skin.call_count, int(has_mesh and task.get("skin", True)))
                report = json.loads(Path(result["vibe_report_path"]).read_text())
                segments = task.get("segments", [task])
                records = report.get("segments", [report])
                transitions = task.get("transitions", [{}] * (len(segments) - 1))
                cursor = 0
                for index, (segment, record) in enumerate(zip(segments, records)):
                    defaults = motion_utils.resolve_preset(segment["preset"])
                    params = {**defaults["params"], **segment.get("motion_overrides", {})}
                    self.assertEqual(record["parameters"], json.loads(json.dumps(params)))
                    self.assertEqual(record["preset"], defaults["name"])
                    count = segment.get("num_frames", defaults["frames"])
                    self.assertEqual(record["frames"], count)
                    if "segments" in task:
                        self.assertEqual(record["start_frame"], cursor)
                        self.assertEqual(record["end_frame"], cursor + count)
                    cursor += count
                    if index < len(transitions):
                        cursor += transitions[index].get("transition_frames", 8)
                self.assertEqual(len(records), len(segments))
                self.assertEqual(report["frames"], cursor)
                self.assertEqual(report["fps"], task.get("fps", 30.0))
                joints = np.load(result["joints_npy_path"])
                self.assertEqual(joints.shape[0], cursor)
                self.assertTrue(np.isfinite(joints).all())
                text = Path(result["motion_bvh_path"]).read_text()
                rows = text.split("MOTION\n", 1)[1].splitlines()
                self.assertEqual(rows[0], f"Frames: {cursor}")
                self.assertEqual(len(rows) - 2, cursor)
                self.assertAlmostEqual(float(rows[1].split(":")[1]), 1 / report["fps"])
                if result["animated_glb_path"]:
                    raw = Path(result["animated_glb_path"]).read_bytes()
                    size = struct.unpack_from("<I", raw, 12)[0]
                    doc = json.loads(raw[20:20 + size])
                    self.assertEqual(len(doc["skins"]), 1)
                    self.assertEqual(len(doc["animations"]), 1)
                    for sampler in doc["animations"][0]["samplers"]:
                        times = doc["accessors"][sampler["input"]]
                        self.assertEqual(times["count"], cursor)
                        self.assertEqual(doc["accessors"][sampler["output"]]["count"], cursor)
                        self.assertAlmostEqual(times["max"][0], (cursor - 1) / report["fps"], places=5)
                if "segments" in task:
                    self.assertEqual(report["mode"], "sequence")
                    self.assertEqual(report["transition_mode"], "insert")
                    self.assertEqual(report["metrics"]["target_error_scope"], "segments_only")
                    self.assertFalse(report["transitions"][0]["foot_lock_enforced"])
                score = op.eval(result, task)
                self.assertEqual(score["vibe_metrics"], report["metrics"])
                if report["metrics"]["failures"]:
                    self.assertFalse(score["motion_valid"])
                else:
                    self.assertTrue(score["motion_valid"])

    def test_sequence_input_validation(self):
        valid = [{"preset": "walk"}, {"preset": "boxing"}]
        invalid: list[dict[str, Any]] = [
            {"segments": []},
            {"segments": {}},
            {"segments": [None]},
            {"segments": [{}]},
            {"segments": [{"preset": "missing"}]},
            {"segments": [{"preset": "walk"}, {"preset": "boxing", "motion_overrides": {"combo": []}}]},
            {"segments": valid, "preset": "walk"},
            {"segments": valid, "num_frames": 120},
            {"segments": valid, "motion_overrides": {}},
            {"segments": valid, "fps": 0},
            {"segments": valid, "fps": True},
            {"segments": valid, "heading_deg": float("nan")},
            {"segments": [{"preset": "walk", "num_frames": False}]},
            {"segments": [{"preset": "walk", "fps": 60}]},
            {"segments": [{"preset": "walk", "heading_deg": True}]},
            {"segments": [{"preset": "walk", "motion_overrides": []}]},
            {"segments": [{"preset": "walk", "motion_overrides": {"fps": 60}}]},
            {"segments": [{"preset": "walk", "motion_overrides": {"step_lenght": 0.3}}]},
            {"segments": [{"preset": "walk"}, {"preset": "walk", "heading_deg": 90}]},
            {"segments": valid, "transitions": []},
            {"segments": valid, "transitions": {}},
            {"segments": valid, "transitions": [None]},
            {"segments": valid, "transitions": [{"frames": 8}]},
            {"segments": valid, "transitions": [{"transition_frames": -1}]},
            {"segments": valid, "transitions": [{"transition_frames": True}]},
            {"segments": valid, "transitions": [{"transition_frames": 1.5}]},
            {"segments": valid, "transitions": [{"carry_facing": "false"}]},
            {"transitions": []},
        ]
        with tempfile.TemporaryDirectory() as directory:
            op = GenMotionOperator(output_dir=directory)
            for fields in invalid:
                with (
                    self.subTest(fields=fields),
                    patch.object(skeleton, "fit_skeleton") as fit,
                    patch.object(skinning, "skin_mesh") as skin,
                    patch.object(motion, "build_plan") as build,
                    patch.object(motion, "generate_clip") as generate,
                ):
                    with self.assertRaises(ValueError):
                        op.run({"task_type": "vibe", "creature": "human", **fields})
                    fit.assert_not_called()
                    skin.assert_not_called()
                    build.assert_not_called()
                    generate.assert_not_called()

    def test_sequence_heading_height_and_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            op = GenMotionOperator(output_dir=directory)
            for carry in (True, False):
                second: dict[str, Any] = {"preset": "stride", "motion_overrides": {"crouch": 0.08}}
                if not carry:
                    second["heading_deg"] = 0.0
                task: dict[str, Any] = {
                    "task_type": "vibe", "task_id": f"heading_{carry}", "heading_deg": 90.,
                    "segments": [{"preset": "stride"}, second],
                }
                if not carry:
                    task["transitions"] = [{"transition_frames": 0, "carry_facing": False}]
                result = op.run(task)
                report = json.loads(Path(result["vibe_report_path"]).read_text())
                transition = 8 if carry else 0
                self.assertEqual(report["frames"], 120 + transition)
                self.assertAlmostEqual(report["segments"][1]["effective_heading_deg"], 90 if carry else 0, places=4)
                self.assertLess(report["metrics"]["target_error_max"], 1e-5)
                positions = np.load(result["joints_npy_path"])
                movement = positions[-1, 0] - positions[60 + transition, 0]
                self.assertGreater(movement[0 if carry else 2], 0.1)
                self.assertAlmostEqual(movement[2 if carry else 0], 0, places=5)
                plan = motion.build_plan()
                expected = motion.generate_clip("stride", plan, crouch=0.08)
                expected_y = motion.joint_positions(expected.clip)[:, :, 1]
                np.testing.assert_allclose(positions[60 + transition:, :, 1], expected_y, atol=1e-6)
            one = op.run({"task_type": "vibe", "task_id": "one", "segments": [{"preset": "walking"}]})
            default = op.run({"task_type": "vibe", "task_id": "default"})
            self.assertEqual(Path(one["motion_bvh_path"]).read_bytes(), Path(default["motion_bvh_path"]).read_bytes())
            self.assertEqual(json.loads(Path(one["vibe_report_path"]).read_text())["transitions"], [])

    def test_sequence_inherits_generated_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            op = GenMotionOperator(output_dir=directory)
            task = {
                "task_type": "vibe", "task_id": "turn_then_walk", "heading_deg": 30.,
                "segments": [
                    {"preset": "turn_jump_chop", "motion_overrides": {"turn_deg": 90.}},
                    {"preset": "stride", "motion_overrides": {"crouch": 0.08}},
                ],
            }
            result = op.run(task)
            report = json.loads(Path(result["vibe_report_path"]).read_text())
            self.assertAlmostEqual(report["segments"][1]["effective_heading_deg"], 120, places=4)
            self.assertLess(report["metrics"]["target_error_max"], 1e-5)
            self.assertEqual(report["metrics"]["failures"], [])
            self.assertTrue(op.eval(result, task)["artifact_valid"])
            positions = np.load(result["joints_npy_path"])
            start = report["segments"][1]["start_frame"]
            delta = positions[-1, 0] - positions[start, 0]
            self.assertGreater(delta[0], 0)
            self.assertLess(delta[2], 0)

    def test_sequence_target_error_is_not_hidden(self):
        generate = motion.generate_clip

        def wrong_targets(*args, **kwargs):
            result = generate(*args, **kwargs)
            for targets in result.foot_targets.values():
                targets[:, 0] += 1.0
            return result

        with tempfile.TemporaryDirectory() as directory:
            op = GenMotionOperator(output_dir=directory)
            task = {"task_type": "vibe", "segments": [{"preset": "walk"}]}
            with patch.object(motion, "generate_clip", side_effect=wrong_targets):
                result = op.run(task)
            report = json.loads(Path(result["vibe_report_path"]).read_text())
            self.assertIn("ik_target", report["metrics"]["failures"])
            self.assertGreater(report["metrics"]["target_error_max"], 0.9)
            self.assertFalse(op.eval(result, task)["artifact_valid"])

    def test_sequence_mesh_and_retarget_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            mesh_path = Path(directory) / "character.obj"
            mesh_path.write_text(skeleton.mesh_to_obj(skeleton.creature_mesh("human")))
            task = {
                "task_type": "vibe_retarget", "task_id": "sequence_mesh",
                "target_mesh_path": str(mesh_path), "fps": 24,
                "segments": [{"preset": "stride"}, {"preset": "jab"}],
            }
            op = GenMotionOperator(output_dir=directory)
            with patch.object(op, "_retarget", return_value={}) as retarget:
                result = op.run(task)
            retarget.assert_called_once()
            self.assertEqual(retarget.call_args.kwargs["fps"], 24)
            self.assertEqual(retarget.call_args.args[0], Path(result["motion_bvh_path"]))
            self.assertIn("Frames: 128", Path(result["motion_bvh_path"]).read_text())
            self.assertTrue(Path(result["animated_glb_path"]).is_file())
            self.assertTrue(Path(result["rig_path"]).is_file())

    def test_sequence_checks_final_transition(self):
        concatenate = motion.concatenate_clips

        def broken_transition(clips, **kwargs):
            clip = concatenate(clips, **kwargs)
            clip.trans[clips[0].num_frames, 1] -= 5
            return clip

        with tempfile.TemporaryDirectory() as directory:
            op = GenMotionOperator(output_dir=directory)
            task = {"task_type": "vibe", "task_id": "broken_join", "segments": [{"preset": "walk"}, {"preset": "boxing"}]}
            with patch.object(motion, "concatenate_clips", side_effect=broken_transition):
                result = op.run(task)
            report = json.loads(Path(result["vibe_report_path"]).read_text())
            self.assertTrue(all(not segment["metrics"]["failures"] for segment in report["segments"]))
            self.assertIn("ground", report["metrics"]["failures"])
            self.assertFalse(op.eval(result, task)["artifact_valid"])

    def test_metrics_checks_collision_once_on_every_frame(self):
        from operators.gen_motion.funcs.vibe_motion_utils.motion_utils import checks, validation

        plan = motion.build_plan()
        rest = motion_utils.MotionClip.rest_clip(plan.template, 5)
        collision = rest.copy()
        shoulder = plan.template.joint_names.index("L_shoulder")
        collision.quats[1, shoulder] = motion_utils.quat_from_axis_angle(
            np.array([0.0, 0.0, 1.0]), np.deg2rad(150.0),
        )
        self.assertTrue(validation.check_self_collision(collision, plan, stride=2).ok)
        full_collision = validation.check_self_collision(collision, plan, stride=1)
        self.assertFalse(full_collision.ok)
        self.assertEqual(full_collision.frames, [1])
        below_ground = collision.copy()
        below_ground.trans[:, 1] -= 1.0
        for label, clip in (("rest", rest), ("odd_frame", collision), ("multiple_failures", below_ground)):
            with self.subTest(case=label):
                original_quats, original_trans = clip.quats.copy(), clip.trans.copy()
                report = validation.validate_clip(clip, plan)
                expected = [f.check for f in report.failures if f.check != "self_collision"]
                if not validation.check_self_collision(clip, plan, stride=1).ok:
                    expected.append("self_collision")
                with (
                    patch.object(
                        validation, "check_self_collision", wraps=validation.check_self_collision,
                    ) as check,
                    patch.object(checks, "check_self_collision", check, create=True),
                ):
                    actual = checks.metrics(clip, plan)
                check.assert_called_once_with(clip, plan, radius_ratio=0.035, stride=1)
                self.assertEqual(actual["failures"], expected)
                np.testing.assert_array_equal(clip.quats, original_quats)
                np.testing.assert_array_equal(clip.trans, original_trans)

    def test_validation_collision_stride_and_early_abort(self):
        from operators.gen_motion.funcs.vibe_motion_utils.motion_utils import validation

        plan = motion.build_plan()
        clip = motion.generate_clip("walk", plan).clip
        cases: tuple[tuple[dict[str, Any], int, float], ...] = (
            ({}, 2, 0.035),
            ({"collision_stride": 1}, 1, 0.035),
            ({"collision_stride": 3, "radius_ratio": 0.04}, 3, 0.04),
        )
        for options, stride, radius in cases:
            with self.subTest(options=options):
                expected = validation.check_self_collision(clip, plan, stride=stride, radius_ratio=radius)
                with patch.object(
                    validation, "check_self_collision", wraps=validation.check_self_collision,
                ) as check:
                    report = validation.validate_clip(clip, plan, **options)
                check.assert_called_once_with(clip, plan, radius_ratio=radius, stride=stride)
                self.assertEqual([f for f in report.findings if f.check == "self_collision"], [expected])
        with (
            patch.object(validation, "check_finite", return_value=validation.Finding("finite", False, 1.0, "invalid pose")),
            patch.object(validation, "check_self_collision") as check,
        ):
            report = validation.validate_clip(clip, plan, collision_stride=1)
        check.assert_not_called()
        self.assertEqual([f.check for f in report.failures], ["finite", "aborted"])

    def test_validation(self):
        plan = motion.build_plan()
        for frames in [0, False, 60.5]:
            with self.subTest(frames=frames), self.assertRaises(ValueError):
                motion.generate_clip("walk", plan, num_frames=frames)
        with self.assertRaises(ValueError):
            motion.concatenate_clips([motion.generate_clip("walk", plan).clip], transition_frames=1.5)
        for parents in [[-1, 2, 1], [-1, 99, 0], [-1, 0.5, 0]]:
            with self.subTest(parents=parents), self.assertRaises(ValueError):
                bvh.validate_hierarchy(parents, np.zeros((3, 3)))
        for quat in [[np.inf, 0, 0, 0], [0, 0, 0, 0]]:
            with self.assertRaises(ValueError):
                bvh.quats_to_zxy_degrees(np.array(quat))
        np.testing.assert_allclose(bvh.quats_to_zxy_degrees(np.array([1e308, 1e308, 0, 0])), [0, 90, 0], atol=1e-6)

    def test_rig_weights_and_glb(self):
        mesh = skeleton.creature_mesh("human")
        rig = skeleton.fit_skeleton(mesh)
        for preset in skinning.list_presets():
            with self.subTest(preset=preset):
                weights = skinning.skin_mesh(mesh, rig, preset=preset)
                np.testing.assert_allclose(weights.weights.sum(1), 1, atol=1e-8)
                self.assertLessEqual(skinning.weight_stats(weights)["max_influences"], 4)
        weights = skinning.skin_mesh(mesh, rig)
        np.testing.assert_allclose(deform(mesh.vertices, weights.weights, pose_matrices(rig.joints, rig.parents)), mesh.vertices, atol=1e-9)
        plan = motion.build_plan(skeleton.to_motion_template(rig))
        clip = motion.generate_clip("walk", plan).clip
        raw = animated_glb(mesh, rig, weights, clip)
        self.assertEqual(struct.unpack_from('<4sII', raw), (b'glTF', 2, len(raw)))
        n = struct.unpack_from('<I', raw, 12)[0]
        doc = json.loads(raw[20:20+n])
        self.assertEqual(len(doc['skins'][0]['joints']), len(rig.parents))
        self.assertEqual(len(doc['animations'][0]['channels']), len(rig.parents) + 1)
        binary = raw[28+n:]
        for view in doc['bufferViews']:
            self.assertEqual(view['byteOffset'] % 4, 0)
            self.assertLessEqual(view['byteOffset'] + view['byteLength'], len(binary))
        index = doc['skins'][0]['inverseBindMatrices']
        view = doc['bufferViews'][doc['accessors'][index]['bufferView']]
        matrices = np.frombuffer(binary, '<f4', len(rig.parents)*16, view['byteOffset']).reshape(-1,4,4).transpose(0,2,1)
        np.testing.assert_allclose(matrices[:, :3, 3], -clip.template.rest, atol=1e-6)
        self.assertIn("joints ", skeleton.skeleton_to_text(rig))

    def test_repeatability_and_composition(self):
        plan = motion.build_plan()
        first = motion.generate_clip("walk", plan).clip
        second = motion.generate_clip("walk", plan).clip
        np.testing.assert_array_equal(first.quats, second.quats)
        longer = motion.generate_clip("walk", plan, step_length=.35).clip
        self.assertGreater(longer.trans[-1, 2], first.trans[-1, 2])
        combined = motion.concatenate_clips([first, second], transition_frames=8)
        self.assertEqual(combined.num_frames, first.num_frames * 2 + 8)
        low = motion.generate_clip("walk", plan, crouch=0.08).clip
        snapshots = [(clip.quats.copy(), clip.trans.copy()) for clip in (first, low)]
        legacy = motion.concatenate_clips([first, low])
        preserved = motion.concatenate_clips([first, low], align_vertical=False)
        start = first.num_frames + 8
        np.testing.assert_allclose(
            legacy.trans[start:, 1], low.trans[:, 1] - low.trans[0, 1] + first.trans[-1, 1],
        )
        np.testing.assert_allclose(preserved.trans[start:, 1], low.trans[:, 1])
        for clip, (quats, trans) in zip((first, low), snapshots):
            np.testing.assert_array_equal(clip.quats, quats)
            np.testing.assert_array_equal(clip.trans, trans)

    def test_operator_and_stale_files(self):
        with tempfile.TemporaryDirectory() as directory:
            op = GenMotionOperator(output_dir=directory)
            task = {"task_type": "vibe", "task_id": "same", "creature": "human"}
            result = op.run(task)
            self.assertTrue(Path(result["animated_glb_path"]).is_file())
            report = json.loads(Path(result["vibe_report_path"]).read_text())
            self.assertEqual(report["fps"], 30)
            next_result = op.run({"task_type": "vibe", "task_id": "same", "fps": 29.97})
            self.assertIsNone(next_result["animated_glb_path"])
            self.assertIsNone(next_result["rig_path"])
            self.assertTrue(op.eval(next_result, {"task_type": "vibe"})["artifact_valid"])
            with patch.object(op, "_retarget") as retarget:
                with self.assertRaises(ValueError):
                    op.run({"task_type": "vibe_retarget", "task_id": "same"})
                retarget.assert_not_called()


if __name__ == "__main__":
    unittest.main()
