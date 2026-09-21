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
    pipeline,
    rigging_utils,
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
                "preset": "turn_jump_chop_tuned",
                "num_frames": 96,
                "motion_overrides": {
                    "turn_deg": 180.0, "jump_height": 0.24,
                    "gravity_ratio": 6.5, "takeoff_ratio": 0.30,
                    "hand_clearance": 0.24, "strike_reach": 0.86,
                },
            },
        ],
        "transitions": [
            {"transition_frames": 12, "carry_facing": True},
            {"transition_frames": 16, "carry_facing": True},
        ],
    },
    {
        "task_type": "vibe", "task_id": "full_jump_chop",
        "preset": "turn_jump_chop_tuned", "num_frames": 96, "fps": 30.0,
        "creature": "human", "rig_overrides": {"motion_ready": False},
        "motion_overrides": {"turn_deg": 180.0, "gravity_ratio": 6.5, "strike_reach": 0.86},
    },
)


MOTION_COMPARE_TASKS = {
    "legacy_motion": {
        "task_type": "vibe", "task_id": "jump_chop", "preset": "turn_jump_chop",
        "num_frames": 96, "fps": 30.0,
        "motion_overrides": {"torso_twist_deg": 0., "torso_lean_deg": 0., "arm_clearance": 0.},
    },
    "partial_motion": {
        "task_type": "vibe", "task_id": "jump_chop", "preset": "turn_jump_chop",
        "num_frames": 96, "fps": 30.0,
        "motion_overrides": {"torso_twist_deg": 16., "torso_lean_deg": 10., "arm_clearance": .055},
    },
    "full_motion": {
        "task_type": "vibe", "task_id": "jump_chop", "preset": "turn_jump_chop_tuned",
        "num_frames": 96, "fps": 30.0,
        "motion_overrides": {"jump_height": .24, "gravity_ratio": 6.5, "hand_clearance": .24},
    },
}


GESTURE_TASKS: tuple[dict[str, Any], ...] = (
    {
        'task_type': 'vibe', 'task_id': 'reach_forward', 'preset': 'task_space',
        'num_frames': 96, 'fps': 30.,
        'motion_overrides': {
            'plant_feet': True,
            'root_positions': {'times': [0., 1.], 'values': [[0., -.03, 0.], [0., -.03, 0.]]},
            'targets': [
                {'role': 'limb.L.0', 'times': [0., .35, .65, 1.],
                 'values': [[.3, -.4, .3], [.25, -.1, .8], [.25, -.1, .8], [.3, -.4, .3]],
                 'pole': [1., -.2, -.3]},
                {'role': 'limb.R.0', 'times': [0., 1.],
                 'values': [[-.3, -.4, .3], [-.3, -.4, .3]], 'pole': [-1., -.2, -.3]},
            ],
        },
    },
    {
        'task_type': 'vibe', 'task_id': 'wave', 'preset': 'task_space',
        'num_frames': 96, 'fps': 30.,
        'motion_overrides': {
            'plant_feet': True,
            'root_positions': {'times': [0., 1.], 'values': [[0., -.03, 0.], [0., -.03, 0.]]},
            'targets': [
                {'role': 'limb.L.0', 'times': [0., .2, .4, .6, .8, 1.],
                 'values': [[.3, -.4, .3], [.3, .6, .3], [.6, .5, .3], [.3, .6, .3], [.6, .5, .3], [.3, -.4, .3]],
                 'pole': [1., -.2, -.3]},
                {'role': 'limb.R.0', 'times': [0., 1.],
                 'values': [[-.3, -.4, .3], [-.3, -.4, .3]], 'pole': [-1., -.2, -.3]},
            ],
        },
    },
)


_KICK_SEGMENT: dict[str, Any] = {
    'plant_feet': False, 'root_yaw': None,
    'root_positions': {
        'times': [0., .18, .30, .50, .70, .82, 1.],
        'values': [[0, 0, 0], [0, .01, .02], [0, .09, .14], [0, .22, .34],
                   [0, .08, .54], [0, .01, .62], [0, 0, .66]],
        'modes': ['smooth', 'return', 'smooth', 'smooth', 'impact', 'smooth']},
    'rotations': [
        {'role': 'limb.L.1', 'at': 0, 'axis': [-1., 0, 0], 'times': [0., .18, .30, .50, .70, 1.],
         'values': [0., -10., 45., 78., 26., 0.],
         'modes': ['smooth', 'return', 'impact', 'smooth', 'smooth']},
        {'role': 'limb.L.1', 'at': 1, 'axis': [1., 0, 0], 'times': [0., .18, .30, .50, .70, 1.],
         'values': [0., 28., 55., 4., 30., 2.],
         'modes': ['smooth', 'smooth', 'impact', 'smooth', 'smooth']},
        {'role': 'limb.R.1', 'at': 0, 'axis': [-1., 0, 0], 'times': [0., .18, .50, .82, 1.],
         'values': [0., -6., -26., -4., 0.], 'modes': ['return', 'smooth', 'smooth', 'smooth']},
        {'role': 'limb.R.1', 'at': 1, 'axis': [1., 0, 0], 'times': [0., .18, .50, .82, 1.],
         'values': [0., 22., 70., 22., 2.], 'modes': ['smooth', 'smooth', 'smooth', 'smooth']}],
    'targets': [
        {'role': 'limb.L.0', 'times': [0., .18, .30, .50, .70, 1.],
         'values': [[.30, -.42, .30], [.32, -.34, .36], [.30, -.18, .46],
                    [.28, -.12, .50], [.30, -.30, .40], [.30, -.42, .30]],
         'pole': [1., -.2, -.3]},
        {'role': 'limb.R.0', 'times': [0., .18, .30, .50, .70, 1.],
         'values': [[-.30, -.42, .30], [-.32, -.30, .34], [-.30, -.08, .44],
                    [-.28, .00, .48], [-.30, -.26, .38], [-.30, -.42, .30]],
         'pole': [-1., -.2, -.3]}],
}

_PUNCH_SEGMENT: dict[str, Any] = {
    'plant_feet': False, 'root_yaw': None,
    'root_positions': {'times': [0., .30, .55, .80, 1.],
                       'values': [[0, 0, 0], [0, 0, .02], [0, 0, .03], [0, 0, .02], [0, 0, 0]]},
    'rotations': [
        {'role': 'trunk', 'at': 1, 'axis': [0., 1., 0.], 'times': [0., .30, .55, .80, 1.],
         'values': [0., -9., 9., -6., 0.], 'modes': ['smooth', 'impact', 'impact', 'smooth']},
        {'role': 'limb.L.1', 'at': 1, 'axis': [1., 0, 0], 'times': [0., .5, 1.], 'values': [2., 4., 2.]},
        {'role': 'limb.R.1', 'at': 1, 'axis': [1., 0, 0], 'times': [0., .5, 1.], 'values': [2., 4., 2.]}],
    'targets': [
        {'role': 'limb.L.0', 'times': [0., .12, .30, .42, .62, .80, 1.],
         'values': [[.30, -.42, .30], [.26, -.20, .44], [.24, -.14, .86], [.26, -.18, .48],
                    [.24, -.16, .46], [.25, -.18, .46], [.30, -.42, .30]],
         'modes': ['smooth', 'impact', 'return', 'smooth', 'smooth', 'smooth'], 'pole': [1., -.2, -.3]},
        {'role': 'limb.R.0', 'times': [0., .12, .30, .52, .68, .86, 1.],
         'values': [[-.30, -.42, .30], [-.26, -.18, .42], [-.26, -.20, .46], [-.24, -.14, .86],
                    [-.26, -.18, .48], [-.25, -.20, .44], [-.30, -.42, .30]],
         'modes': ['smooth', 'smooth', 'impact', 'return', 'smooth', 'smooth'], 'pole': [-1., -.2, -.3]}],
}

# Flying kick then punch combo. Both segments use task_space so the seam poses
# match; the kick leaves the ground, the punch keeps a standing stance.
FLYING_KICK_COMBO: dict[str, Any] = {
    'task_type': 'vibe', 'task_id': 'flying_kick_punch', 'creature': 'human', 'fps': 30.,
    'segments': [
        {'preset': 'task_space', 'num_frames': 96, 'motion_overrides': _KICK_SEGMENT},
        {'preset': 'task_space', 'num_frames': 96, 'motion_overrides': _PUNCH_SEGMENT},
    ],
    'transitions': [{'transition_frames': 8, 'carry_facing': True}],
}


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

    def test_skin_distances_follow_rotation_pivots(self):
        rig = rigging_utils.RigResult(
            "arm", np.array([[0., 0, 0], [1., 0, 0], [2., 0, 0]]),
            np.array([-1, 0, 1]), ["shoulder", "elbow", "wrist"],
        )
        vertices = np.array([[.5, .01, 0], [1.5, .01, 0]])
        mesh = rigging_utils.CreatureMesh("points", vertices, np.empty((0, 3), int), np.arange(2))
        incoming = rigging_utils.bone_distances(mesh, rig)
        outgoing = rigging_utils.bone_distances(mesh, rig, convention="outgoing")
        self.assertEqual(incoming.argmin(1).tolist(), [1, 2])
        self.assertEqual(outgoing.argmin(1).tolist(), [0, 1])
        skin = skinning.skin_mesh(mesh, rig, preset="rigid")
        np.testing.assert_array_equal(skin.weights.argmax(1), [0, 1])
        matrix = np.array([[0., -1, 0], [1., 0, 0], [0., 0, 1]])
        posed = deform(vertices, skin.weights, pose_matrices(rig.joints, rig.parents, {1: matrix}))
        np.testing.assert_allclose(posed[0], vertices[0], atol=1e-8)
        np.testing.assert_allclose(posed[1], [.99, .5, 0], atol=1e-8)
        legacy = skinning.skin_mesh(mesh, rig, preset="rigid", bone_convention="incoming")
        np.testing.assert_array_equal(legacy.weights.argmax(1), [1, 2])
        with self.assertRaises(ValueError):
            skinning.skin_mesh(mesh, rig, bone_convention="invalid")

    def test_skin_outgoing_handles_branch_and_leaf(self):
        rig = rigging_utils.RigResult(
            "branch", np.array([[0., 0, 0], [1., 0, 0], [0., 1, 0]]),
            np.array([-1, 0, 0]), ["root", "x", "y"],
        )
        vertices = np.array([[.5, 0, 0], [0, .5, 0], [2, 0, 0]])
        mesh = rigging_utils.CreatureMesh("points", vertices, np.empty((0, 3), int), np.arange(3))
        original = vertices.copy()
        dist = rigging_utils.bone_distances(mesh, rig, convention="outgoing")
        np.testing.assert_allclose(dist[:, 0], [0, 0, 1])
        np.testing.assert_allclose(dist[:, 1], np.linalg.norm(vertices - rig.joints[1], axis=1))
        np.testing.assert_array_equal(original, mesh.vertices)

    def test_jump_refinement_preserves_root_and_contacts(self):
        legacy_params: dict[str, Any] = dict(torso_twist_deg=0., torso_lean_deg=0., arm_clearance=0.)
        for frames, fps, turn, weapon in ((60, 24., -180., False), (120, 30., 0., False), (240, 60., 360., True)):
            with self.subTest(frames=frames, turn=turn, weapon=weapon):
                template = motion_utils.build_template("biped_armed", weapon=weapon)
                rest, parents = template.rest.copy(), template.parents.copy()
                plan = motion.build_plan(template)
                kwargs: dict[str, Any] = dict(num_frames=frames, fps=fps, turn_deg=turn, heading_deg=37.)
                before = motion.generate_clip("turn_jump_chop", plan, **kwargs, **legacy_params)
                partial: dict[str, Any] = dict(torso_twist_deg=16., torso_lean_deg=10., arm_clearance=.055)
                after = motion.generate_clip("turn_jump_chop", plan, **kwargs, **partial)
                repeated = motion.generate_clip("turn_jump_chop", plan, **kwargs, **partial)
                root = int(np.flatnonzero(parents < 0)[0])
                np.testing.assert_array_equal(after.clip.trans, before.clip.trans)
                np.testing.assert_array_equal(after.clip.quats[:, root], before.clip.quats[:, root])
                np.testing.assert_array_equal(after.clip.quats, repeated.clip.quats)
                positions = motion.joint_positions(after.clip)
                for role in plan.support_roles:
                    np.testing.assert_array_equal(after.contacts[role], before.contacts[role])
                    np.testing.assert_array_equal(after.foot_targets[role], before.foot_targets[role])
                    np.testing.assert_array_equal(after.clip.quats[:, plan.roles[role].joints], before.clip.quats[:, plan.roles[role].joints])
                    mask = after.contacts[role]
                    steps = np.diff(positions[:, plan.roles[role].joints[-1]], axis=0)[mask[:-1] & mask[1:]]
                    self.assertLess(float(np.linalg.norm(steps, axis=-1).max()), 1e-5)
                self.assertLess(max(float(v.max()) for v in after.residuals.values()), 1e-5)
                self.assertLess(motion.clip_metrics(after, plan)["bone_error_max"], 1e-5)
                self.assertFalse(np.allclose(after.clip.quats, before.clip.quats))
                np.testing.assert_array_equal(template.rest, rest)
                np.testing.assert_array_equal(template.parents, parents)

    def test_guard_refinement_respects_weapon_host_and_heading(self):
        template = motion_utils.build_template("biped_armed", weapon=True)
        plan = motion.build_plan(template)
        arms = [r for r in template.limb_roles if r not in plan.support_roles]
        host = plan.roles[template.weapon_roles[0]].host
        primary = next(r for r in arms if host in plan.roles[r].joints)
        options: dict[str, Any] = dict(torso_twist_deg=0., torso_lean_deg=0., heading_deg=67.)
        before = motion.generate_clip("turn_jump_chop", plan, arm_clearance=0., **options)
        after = motion.generate_clip("turn_jump_chop", plan, arm_clearance=.055, **options)
        np.testing.assert_array_equal(after.clip.quats[:, plan.roles[primary].joints], before.clip.quats[:, plan.roles[primary].joints])
        self.assertEqual(set(after.hand_targets), set(arms) - {primary})
        old_pos, old_q = motion_utils.fk(before.clip)
        new_pos, new_q = motion_utils.fk(after.clip)
        for role, target in after.hand_targets.items():
            wrist = plan.roles[role].joints[-1]
            np.testing.assert_allclose(new_pos[:, wrist], target, atol=1e-5)
            np.testing.assert_allclose(motion_utils.quat_to_matrix(new_q[:, wrist]), motion_utils.quat_to_matrix(old_q[:, wrist]), atol=1e-6)
        weapon_tip = plan.roles[template.weapon_roles[0]].joints[-1]
        np.testing.assert_allclose(new_pos[:, weapon_tip], old_pos[:, weapon_tip], atol=1e-6)

    def test_refinement_parameters_are_validated_before_rigging(self):
        invalid = (("torso_twist_deg", 26), ("torso_lean_deg", -1), ("arm_clearance", .11),
                   ("arm_clearance", True), ("torso_lean_deg", float("nan")), ("torso_twist_deg", float("inf")))
        with tempfile.TemporaryDirectory() as directory:
            op = GenMotionOperator(output_dir=directory)
            for key, value in invalid:
                with self.subTest(key=key, value=value), patch.object(skeleton, "fit_skeleton") as fit:
                    with self.assertRaises(ValueError):
                        op.run({"task_type": "vibe", "creature": "human", "preset": "turn_jump_chop", "motion_overrides": {key: value}})
                    fit.assert_not_called()

    def test_full_jump_has_ballistic_flight_and_landing(self):
        plan = motion.build_plan()
        result = motion.generate_clip("turn_jump_chop_tuned", plan, num_frames=191, fps=60)
        timing, clip = result.timing, result.clip
        t = np.arange(clip.num_frames) / clip.fps
        air = (t[1:-1] > timing["takeoff_seconds"] + .04) & (t[1:-1] < timing["landing_seconds"] - .04)
        acceleration = np.diff(clip.trans[:, 1].astype(float), n=2) * clip.fps**2
        self.assertGreater(int(air.sum()), 6)
        np.testing.assert_allclose(acceleration[air], -timing["gravity"], rtol=.002, atol=.002)
        land, settle = [round(timing[k] * clip.fps) for k in ("landing_seconds", "settle_seconds")]
        self.assertLess(clip.trans[settle, 1], clip.trans[land, 1] - .06 * plan.support_length)
        self.assertTrue(.25 < timing["flight_seconds"] < .65)
        same_duration = motion.generate_clip("turn_jump_chop_tuned", plan, num_frames=96, fps=30)
        self.assertEqual(same_duration.timing, timing)
        for time_key in ("takeoff_seconds", "landing_seconds"):
            frame = round(timing[time_key] * clip.fps)
            velocity = np.diff(clip.trans[:, 1].astype(float)) * clip.fps
            self.assertLess(abs(velocity[frame] - velocity[frame - 1]), timing["gravity"] / clip.fps * 2)

    def test_full_jump_rebuilds_primary_hand_and_locks_feet(self):
        template = motion_utils.build_template("biped_armed", weapon=True)
        plan = motion.build_plan(template)
        for turn in (-180., 180., 360.):
            with self.subTest(turn=turn):
                result = motion.generate_clip("turn_jump_chop_tuned", plan, num_frames=191, fps=60, turn_deg=turn, heading_deg=25.)
                again = motion.generate_clip("turn_jump_chop_tuned", plan, num_frames=191, fps=60, turn_deg=turn, heading_deg=25.)
                np.testing.assert_array_equal(result.clip.quats, again.clip.quats)
                positions, rotations = motion_utils.fk(result.clip)
                root = int(np.flatnonzero(template.parents < 0)[0])
                matrix = motion_utils.quat_to_matrix(rotations[:, root])
                yaw = np.rad2deg(np.unwrap(np.arctan2(matrix[:, 0, 2], matrix[:, 2, 2])))
                self.assertAlmostEqual(yaw[-1] - yaw[0], turn, places=3)
                host = plan.roles[template.weapon_roles[0]].host
                arm = next(r for r in template.limb_roles if host in plan.roles[r].joints)
                wrist = plan.roles[arm].joints[-1]
                hand = np.einsum("tji,tj->ti", matrix, positions[:, wrist] - positions[:, root])
                speed = np.linalg.norm(np.diff(hand, axis=0), axis=-1) * result.clip.fps
                t = np.arange(result.clip.num_frames) / result.clip.fps
                timing = result.timing
                wind = (t[:-1] > .05) & (t[:-1] < timing["takeoff_seconds"])
                attack = (t[:-1] >= timing["strike_start_seconds"]) & (t[:-1] <= timing["impact_seconds"])
                self.assertGreater(speed[attack].max(), 2 * speed[wind].max())
                hold, hit = [int(timing[k] * result.clip.fps) for k in ("strike_start_seconds", "impact_seconds")]
                self.assertGreater(hand[hold, 1], hand[hit, 1] + .2 * plan.support_length)
                self.assertGreater(hand[hit, 2], .25 * plan.support_length)
                for role, contact in result.contacts.items():
                    tip = plan.roles[role].joints[-1]
                    both = contact[:-1] & contact[1:]
                    step = np.linalg.norm(np.diff(positions[:, tip], axis=0)[both], axis=-1)
                    self.assertLess(step.max(), 1e-5)
                for role, target in result.hand_targets.items():
                    np.testing.assert_allclose(positions[:, plan.roles[role].joints[-1]], target, atol=1e-5)
                self.assertLess(max(motion.residual_summary(result).values()), 1e-5)

    def test_full_jump_parameters_and_invalid_mixed_contract(self):
        plan = motion.build_plan()
        normal = motion.generate_clip("turn_jump_chop_tuned", plan)
        low = motion.generate_clip("turn_jump_chop_tuned", plan, jump_height=.15, strike_reach=.7, hand_clearance=.35)
        self.assertLess(low.timing["flight_seconds"], normal.timing["flight_seconds"])
        self.assertLess(low.clip.trans[:, 1].max(), normal.clip.trans[:, 1].max())
        self.assertFalse(np.allclose(low.clip.quats, normal.clip.quats))
        bad: list[dict[str, Any]] = [
            {"landing_ratio": .72}, {"raise_deg": 92}, {"strike_ratio": .18}, {"arm_clearance": .055},
            {"gravity_ratio": True}, {"gravity_ratio": float("nan")}, {"gravity_ratio": 0},
            {"hand_clearance": .6}, {"foot_tuck": -1}, {"jump_height": .8},
            {"num_frames": 59}, {"num_frames": 60, "fps": 120}, {"num_frames": 60, "fps": 45},
            {"num_frames": 61, "fps": 8.5, "jump_height": .4, "gravity_ratio": 4.},
        ]
        for params in bad:
            with self.subTest(params=params), self.assertRaises(ValueError):
                motion.generate_clip("turn_jump_chop_tuned", plan, **params)

    def test_full_jump_hand_failure_is_reported(self):
        plan = motion.build_plan()
        result = motion.generate_clip("turn_jump_chop_tuned", plan)
        role = next(iter(result.hand_targets))
        result.hand_targets[role][:, 0] += 1.
        metric = motion.clip_metrics(result, plan)
        self.assertGreater(metric["hand_target_error_max"], .9)
        self.assertIn("ik_target", metric["failures"])

    def test_three_joint_arm_adapter_preserves_fitted_rig(self):
        mesh = skeleton.creature_mesh('human')
        rig = skeleton.fit_skeleton(mesh, motion_ready=False)
        joints, parents = rig.joints.copy(), rig.parents.copy()
        template = skeleton.to_motion_template(rig)
        plan = motion.build_plan(template)
        self.assertEqual(len(rig.parents), 19)
        self.assertEqual(template.num_joints, 19)
        for role in template.limb_roles:
            if role not in plan.support_roles:
                ids = plan.roles[role].joints
                self.assertEqual(len(ids), 4)
                self.assertIn(ids[0], plan.roles['trunk'].joints)
                self.assertTrue(all(rig.joint_names[j].startswith('arm.') for j in ids[-3:]))
        result = motion.generate_clip('turn_jump_chop_tuned', plan)
        np.testing.assert_array_equal(rig.joints, joints)
        np.testing.assert_array_equal(rig.parents, parents)
        np.testing.assert_allclose(template.rest, joints, atol=1e-7)
        metric = motion.clip_metrics(result, plan)
        self.assertAlmostEqual(metric['ik_residual_max'], max(motion.residual_summary(result).values()))
        # The procedural fixture fits unequal arm segments; metadata adaptation cannot fix unreachable targets.
        self.assertGreater(metric['hand_target_error_max'], .001)
        self.assertIn('ik_target', metric['failures'])

    def test_full_jump_rejects_non_wrist_weapon(self):
        template = motion_utils.build_template('biped_armed', weapon=True)
        plan = motion.build_plan(template)
        weapon = plan.roles[template.weapon_roles[0]]
        arm = next(plan.roles[r] for r in template.limb_roles if weapon.host in plan.roles[r].joints)
        template.parents[weapon.joints[0]] = arm.joints[-2]
        with self.assertRaisesRegex(ValueError, 'wrist'):
            motion.generate_clip('turn_jump_chop_tuned', motion.build_plan(template))

    def test_operator_switches_presets_and_custom_gestures(self):
        tasks = [dict(task_type='vibe', task_id=name, preset=name) for name in motion.list_presets()]
        tasks += list(GESTURE_TASKS)
        positions = {}
        with tempfile.TemporaryDirectory() as directory:
            operator = GenMotionOperator(output_dir=directory)
            for task in tasks:
                with self.subTest(task=task['task_id']):
                    original = deepcopy(task)
                    output = operator.run(task)
                    self.assertEqual(task, original)
                    report = json.loads(Path(output['vibe_report_path']).read_text())
                    self.assertEqual(report['metrics']['failures'], [])
                    positions[task['task_id']] = np.load(output['joints_npy_path'])
            self.assertFalse(np.allclose(positions['wave'], positions['reach_forward']))
            sequence = dict(task_type='vibe', task_id='gesture_sequence', segments=[
                {key: value for key, value in task.items() if key in ('preset', 'num_frames', 'motion_overrides')}
                for task in GESTURE_TASKS
            ])
            output = operator.run(sequence)
            report = json.loads(Path(output['vibe_report_path']).read_text())
            self.assertEqual(report['frames'], 200)
            self.assertEqual(report['metrics']['failures'], [])

    def test_task_space_scales_and_preserves_input(self):
        from dataclasses import replace

        parameters = deepcopy(GESTURE_TASKS[0]['motion_overrides'])
        original = deepcopy(parameters)
        template = motion_utils.build_template('biped_armed')
        base_plan = motion.build_plan(template)
        base = motion.generate_clip('task_space', base_plan, **parameters)
        for scale in (.5, 2.):
            with self.subTest(scale=scale):
                plan = motion.build_plan(replace(template, rest=template.rest * scale))
                result = motion.generate_clip('task_space', plan, **parameters)
                np.testing.assert_allclose(motion.joint_positions(result.clip), motion.joint_positions(base.clip) * scale, atol=1e-5)
                self.assertLess(max(motion.residual_summary(result).values()), 1e-5)
        self.assertEqual(parameters, original)
        for preset in ('boxing', 'jab', 'task_space'):
            result = motion.generate_clip(preset, base_plan, **(parameters if preset == 'task_space' else {}))
            role = next(iter(result.hand_targets))
            result.hand_targets[role][:, 0] += 1
            self.assertIn('ik_target', motion.clip_metrics(result, base_plan)['failures'])

    def test_task_space_rejects_invalid_tracks(self):
        bad: list[dict[str, Any]] = [
            {'plant_feet': 'yes'}, {'rotations': {}},
            {'root_yaw': {'times': [0., 0.], 'values': [0., 1.]}},
            {'root_positions': {'times': [0., 1.], 'values': [[0., 0.], [0., 0.]]}},
            {'root_yaw': {'times': [0., 1.], 'values': [0., float('nan')]}},
            {'targets': [{'role': 'missing', 'times': [0., 1.], 'values': [[0., 0., 0.], [0., 0., 0.]], 'pole': [0., 1., 0.]}]},
            {'rotations': [{'role': 'head', 'at': 99, 'axis': [1., 0., 0.], 'times': [0., 1.], 'values': [0., 20.]}]},
            {'rotations': [{'role': 'head', 'at': 0, 'axis': [0., 0., 0.], 'times': [0., 1.], 'values': [0., 20.]}]},
            {'rotations': [{'role': 'trunk', 'at': 0, 'axis': [0., 1., 0.], 'times': [0., 1.], 'values': [0., 20.]}]},
        ]
        with tempfile.TemporaryDirectory() as directory:
            operator = GenMotionOperator(output_dir=directory)
            for overrides in bad:
                with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                    operator.run(dict(task_type='vibe', preset='task_space', motion_overrides=overrides))

    def test_tuned_choreography_is_parameterized(self):
        plan = motion.build_plan()
        keys = [[.3, -.4, .3], [.5, -.25, .25], [.5, -.3, .25], [.3, -.4, .3]]
        options: dict[str, Any] = dict(push_seconds=.3, settle_seconds=.3, recover_seconds=.5,
                       attack_flight_ratio=.25, impact_flight_ratio=.85, guard_hand_keys=keys)
        original = deepcopy(options)
        result = motion.generate_clip('turn_jump_chop_tuned', plan, **options)
        baseline = motion.generate_clip('turn_jump_chop_tuned', plan)
        self.assertEqual(options, original)
        self.assertAlmostEqual(result.timing['settle_seconds'] - result.timing['landing_seconds'], .3)
        self.assertFalse(np.allclose(result.clip.quats, baseline.clip.quats))
        invalid_cases: list[dict[str, Any]] = [
            {'guard_hand_keys': []}, {'elbow_pole': [0, 0, 0]},
            {'blade_keys': [[0, 0, 0]] * 5},
            {'attack_flight_ratio': .95, 'impact_flight_ratio': .9},
        ]
        for invalid in invalid_cases:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                motion.generate_clip('turn_jump_chop_tuned', plan, **invalid)

    def test_flying_kick_then_punch_combo(self):
        task = deepcopy(FLYING_KICK_COMBO)
        with tempfile.TemporaryDirectory() as directory:
            result = GenMotionOperator(output_dir=directory).run(task)
            report = json.loads(Path(result['vibe_report_path']).read_text())
            joints = np.load(result['joints_npy_path'])
        self.assertEqual(task, FLYING_KICK_COMBO)
        self.assertEqual(report['frames'], 200)
        self.assertEqual(report['metrics']['failures'], [])
        for record in report['segments']:
            self.assertEqual(record['metrics']['failures'], [])
        self.assertLess(report['transitions'][0]['joint_step_max'], 1e-5)
        plan = motion.build_plan(skeleton.to_motion_template(
            skeleton.fit_skeleton(skeleton.creature_mesh('human'))))
        ground = plan.ground
        hip = plan.roles['limb.L.1'].joints[0]
        toe = plan.roles['limb.L.1'].joints[-1]
        wrists = [plan.roles[r].joints[-1] for r in ('limb.L.0', 'limb.R.0')]
        # Airborne: every foot leaves the ground during the kick.
        clearance = joints[:96, [toe, plan.roles['limb.R.1'].joints[-1]], 1].min(axis=1) - ground
        self.assertGreater(float(clearance.max()), .2 * plan.support_length)
        self.assertLess(float(clearance[0]), .01 * plan.support_length)
        # The kicking foot extends forward, then the combo returns to a stance.
        self.assertGreater(float(joints[:96, toe, 2].max() - joints[0, toe, 2]), .5 * plan.support_length)
        self.assertLess(float(clearance[-1]), .05 * plan.support_length)
        # Punches reach further forward than the guard pose, on both arms.
        start = report['segments'][1]['start_frame']
        for wrist in wrists:
            guard = float(joints[start, wrist, 2])
            self.assertGreater(float(joints[start:, wrist, 2].max() - guard), .2 * plan.support_length)
        forward = joints[-1, hip, 2] - joints[0, hip, 2]
        self.assertGreater(float(forward), .5 * plan.support_length)

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


def export_refinement_preview(output_dir, *, mesh_path, task_inputs, stage="after"):
    """Export operator tasks and mesh snapshots to a new output subdirectory."""
    from hashlib import sha256

    fk, quat_to_matrix = motion_utils.fk, motion_utils.quat_to_matrix

    repo = Path(__file__).resolve().parents[1]
    output = (repo / output_dir).resolve()
    source = (repo / mesh_path).resolve()
    output.relative_to(repo)
    source_path = source.relative_to(repo).as_posix()
    if not source.is_file():
        raise FileNotFoundError(source_path)
    if not isinstance(stage, str) or not stage or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in stage):
        raise ValueError('stage must be a simple directory name')
    if not task_inputs:
        raise ValueError('task_inputs must contain operator tasks')
    tasks = deepcopy(list(task_inputs))
    destination = output / stage
    destination.mkdir(parents=True, exist_ok=False)
    records = []
    for task in tasks:
        task.pop("creature", None)
        task["target_mesh_path"] = source_path
        captured = {}
        original_skin = skinning.skin_mesh
        original_bvh = pipeline.clip_to_bvh_bytes

        def capture_skin(mesh, rig, **kwargs):
            weights = original_skin(mesh, rig, **kwargs)
            captured.update(mesh=mesh, skin=weights)
            return weights

        def capture_exported(clip):
            captured.update(clip=clip)
            return original_bvh(clip)

        op = GenMotionOperator(output_dir=str(destination / task["task_id"]))
        with (
            patch.object(skinning, "skin_mesh", side_effect=capture_skin),
            patch.object(pipeline, "clip_to_bvh_bytes", side_effect=capture_exported),
        ):
            result = op.run(task)
        report = json.loads(Path(result["vibe_report_path"]).read_text())
        record = {
            "id": task["task_id"], "task": task, "report": report,
            "evaluation": op.eval(result, task),
            "artifacts": {
                key: Path(value).relative_to(output).as_posix()
                for key, value in result.items() if key.endswith("_path") and value
            },
        }
        if "skin" in captured:
            mesh, clip, weights = captured["mesh"], captured["clip"], captured["skin"].weights
            joints, quats = fk(clip)
            rotations = quat_to_matrix(quats)
            vertices = []
            for positions, rotation in zip(joints, rotations):
                matrices = np.tile(np.eye(4), (clip.num_joints, 1, 1))
                matrices[:, :3, :3] = rotation
                matrices[:, :3, 3] = positions - np.einsum("jab,jb->ja", rotation, clip.template.rest)
                vertices.append(deform(mesh.vertices, weights, matrices))
            vertices = np.asarray(vertices)
            edges = np.unique(np.sort(np.concatenate([
                mesh.faces[:, [0, 1]], mesh.faces[:, [1, 2]], mesh.faces[:, [2, 0]],
            ]), axis=1), axis=0)
            rest_lengths = np.linalg.norm(mesh.vertices[edges[:, 0]] - mesh.vertices[edges[:, 1]], axis=-1)
            live = rest_lengths > mesh.scale * 1e-8
            ratios = np.linalg.norm(vertices[:, edges[live, 0]] - vertices[:, edges[live, 1]], axis=-1) / rest_lengths[live]
            record["deformation"] = {
                "edge_ratio_p99": float(np.quantile(ratios, .99)),
                "edge_ratio_p01": float(np.quantile(ratios, .01)),
                "mesh_ground_penetration": float(max(0, mesh.bounds[0, 1] - vertices[..., 1].min())),
            }
            snapshot = destination / task["task_id"] / "snapshot.npz"
            np.savez_compressed(
                snapshot, vertices=vertices.astype(np.float32), faces=mesh.faces,
                joints=joints, quats=clip.quats, trans=clip.trans, rest=clip.template.rest,
                parents=clip.template.parents, weights=weights, fps=clip.fps,
                rest_vertices=mesh.vertices, joint_names=clip.template.joint_names,
            )
            record["snapshot"] = snapshot.relative_to(output).as_posix()
        records.append(record)
        print(stage, task["task_id"], report["metrics"], record.get("deformation", {}), flush=True)
    manifest = {
        "stage": stage, "producer": "GenMotionOperator.run",
        "source_mesh": source_path, "source_sha256": sha256(source.read_bytes()).hexdigest(),
        "clips": records,
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False))
    return manifest


if __name__ == "__main__":
    unittest.main()
