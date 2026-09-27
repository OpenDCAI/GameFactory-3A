"""Rigging regressions and an explicit real-OBJ test CLI."""
from __future__ import annotations

from collections import Counter
from contextlib import suppress
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from typing import Any
from unittest.mock import Mock, patch
import numpy as np

from operators.gen_motion import operator as operator_module
from operators.gen_motion.funcs.vibe_motion_utils import skeleton, skinning
from operators.gen_motion.funcs.vibe_motion_utils.pipeline import generate_vibe_motion
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils import LimbGroup
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.export import animated_glb
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.skin_checks import bend_pose
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.skin_units import deform, pose_matrices, validate_weights
from operators.gen_motion.funcs.vibe_motion_utils.motion_utils import MotionClip, SkeletonTemplate
from operators.gen_motion.funcs.vibe_motion_utils.motion_utils.units import fk, matrix_to_quat, quat_from_axis_angle, quat_to_matrix

FIT = {
    'name': 'explicit', 'up': [0, 1, 0], 'forward': [0, 0, 1],
    'spine_axis': 'vertical', 'spine_joints': 3, 'spine_span': [0.1, 0.9], 'root_at': 0.5,
    'limb_groups': [], 'section_resolution': 9, 'chain_samples': 17, 'candidate_keep': 8,
    'spine_center_weight': 2.0, 'spine_continuity_weight': 2.0,
    'simplify_min_gap': 2, 'simplify_gap_ratio': 0.28, 'simplify_length_weight': 0.0004,
    'limb_tangent_step_limit': 2.0, 'limb_anchor_weight': 0.35, 'limb_anchor_power': 3.0,
    'limb_anchor_fraction': 0.5, 'limb_radius_window': 2.0, 'limb_step_window': 2.0,
    'limb_initial_distance_weight': 0.1, 'limb_radius_cap': 1.5, 'limb_continuity_weight': 2.0,
    'section_vertex_tolerance': 1e-10, 'section_plane_nudge': 1e-9,
    'section_weld_tolerance': 1e-8, 'clearance_chunk_size': 256,
}
SKIN = {'kernel': 'inverse', 'falloff': 4.0, 'radius_scale': 2.0, 'max_influences': 3,
        'floor': 0.0001, 'smooth_iterations': 2, 'smooth_rate': 0.5,
        'bone_convention': 'outgoing', 'distance_epsilon_ratio': 1e-6, 'sum_tolerance': 1e-9}
RIG_QA = {'rays': 7, 'ray_det_tolerance': 1e-12, 'ray_edge_tolerance': 1e-10,
          'ray_hit_tolerance': 1e-10, 'ray_merge_tolerance': 1e-8,
          'bone_sample_span': [0.05, 0.95], 'bone_samples': 11,
          'max_outside_joint_ratio': 0.0, 'max_outside_bone_ratio': 0.0, 'min_bone_length_ratio': 0.01}
SKIN_QA = {'max_influences': 3, 'sum_tolerance': 1e-9, 'smoothness_quantile': 0.95,
           'smoothness_limit': 2.0, 'bend_degrees': 5.0, 'bend_seed': 0,
           'bend_scale_span': [0.6, 1.0], 'volume_tolerance': 1.0, 'travel_tolerance': 2.0}
EXPORT: dict[str, Any] = {'text': {'precision': 6, 'sum_tolerance': 1e-9, 'max_influences': 4},
                         'glb': {'bind_tolerance': 1e-9, 'sum_tolerance': 1e-9, 'material': None, 'interpolation': 'STEP'}}
VERTICES = np.array([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
                     [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], dtype=float)
FACES = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [3, 7, 6], [3, 6, 2], [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]])
LIMB = {'name': 'branch', 'at': 0.5, 'joints': 3, 'reach': 'out',
        'window': 1.0, 'lateral_min': 0.1, 'paired': True, 'span': [0.2, 0.8]}
MESH_TEST: dict[str, Any] = {
    'normalization': {'height': 1.8, 'weld_tolerance_ratio': 1e-5},
    'rigging': {**deepcopy(FIT), 'name': 'unrigged_humanoid_test', 'root_at': 0.0,
                'spine_joints': 5, 'spine_span': [0.45, 0.88],
                'section_resolution': 25, 'chain_samples': 41, 'candidate_keep': 32,
                'limb_groups': [
                    {'name': 'arm', 'at': 0.8, 'joints': 3, 'reach': 'down',
                     'window': 0.2, 'lateral_min': 0.45, 'paired': True, 'span': [0.1, 0.42]},
                    {'name': 'leg', 'at': 0.0, 'joints': 4, 'reach': 'down',
                     'window': 0.1, 'lateral_min': 0.1, 'paired': True, 'span': [0.1, 0.96]}]},
    'skinning': {**SKIN, 'max_influences': 4, 'smooth_iterations': 6},
    'rig_quality': {**RIG_QA, 'max_outside_bone_ratio': 0.05},
    'skin_quality': {**SKIN_QA, 'max_influences': 4, 'bend_degrees': 25.0},
    'export': deepcopy(EXPORT),
    'preview': {'width': 1280, 'height': 768, 'fps': 15, 'frames': 61,
                'stress_quantile': 0.99, 'stretch_quantile_limit': 1.3, 'stretch_max_limit': 3.0},
}


def motion_config(*, fitted, skinned) -> dict[str, Any]:
    config: dict[str, Any] = {
        'skeleton': {'name': 'explicit', 'names': ['spine.0', 'spine.1', 'spine.2'],
                     'parents': [-1, 0, 1], 'rest': [[0, -0.8, 0], [0, 0, 0], [0, 0.8, 0]]},
        'rhythm': {'duration': 1, 'fps': 12, 'events': {'begin': 0, 'finish': 1}},
        'program': {'action': 'rest_in_box', 'forward': [0, 0, 1],
                    'root': {'operator': 'curves', 'params': {'scale': 1, **{
                        axis: {'keys': ['begin', 'finish'], 'values': [0, 0], 'interpolation': 'linear'}
                        for axis in ('x', 'y', 'z', 'yaw')}}},
                    'parts': {'support': {'category': 'trunk', 'joints': ['spine.0', 'spine.1', 'spine.2'],
                                          'operator': 'rest', 'params': {}}},
                    'solver': {'epsilon': 1e-9},
                    'quality': {'target_tolerance': 1e-6, 'contact_tolerance': 1e-6,
                                'bone_length_tolerance': 1e-6, 'ground_height': -1,
                                'ground_tolerance': 1e-6, 'limit_tolerance': 1e-6}},
        'rig_quality': deepcopy(RIG_QA), 'export': deepcopy(EXPORT),
    }
    if fitted:
        config.update(skeleton=None, rigging={**deepcopy(FIT), 'root_at': 0.0})
    if skinned:
        config.update(skinning=deepcopy(SKIN), skin_quality=deepcopy(SKIN_QA))
    else:
        del config['export']['glb']
    return config


def glb_document(data):
    if struct.unpack_from('<4sII', data) != (b'glTF', 2, len(data)):
        raise AssertionError('Invalid GLB header')
    length, kind = struct.unpack_from('<I4s', data, 12)
    if kind != b'JSON':
        raise AssertionError('Missing JSON chunk')
    return json.loads(data[20:20 + length])


def load_unrigged_obj(path, *, height, weld_tolerance_ratio):
    """Strict geometry-only OBJ loading with source audit and stable welding."""
    data = Path(path).read_bytes()
    vertices, faces, directives = [], [], Counter()
    if not np.isfinite([height, weld_tolerance_ratio]).all() or min(height, weld_tolerance_ratio) <= 0:
        raise ValueError('Height and weld tolerance must be finite and positive')
    for line in data.decode('utf-8-sig').splitlines():
        items = line.split('#', 1)[0].split()
        if not items:
            continue
        kind = items[0]
        directives[kind] += 1
        if kind == 'v':
            if len(items) != 4:
                raise ValueError('OBJ vertices need three coordinates')
            vertices.append(list(map(float, items[1:])))
        elif kind == 'f':
            indices = [int(token.split('/')[0]) for token in items[1:]]
            if len(indices) < 3 or 0 in indices:
                raise ValueError('OBJ faces need three nonzero indices')
            indices = [i - 1 if i > 0 else len(vertices) + i for i in indices]
            if min(indices) < 0 or max(indices) >= len(vertices):
                raise ValueError('OBJ face index out of range')
            faces.extend((indices[0], a, b) for a, b in zip(indices[1:], indices[2:]))
        elif kind not in ('vt', 'vn', 's', 'g', 'o', 'mtllib', 'usemtl'):
            raise ValueError(f'Unexpected OBJ directive: {kind}')
    mesh = skeleton.mesh_from_arrays(np.asarray(vertices), np.asarray(faces), name=Path(path).stem)
    if np.ptp(mesh.vertices[:, 1]) <= 0:
        raise ValueError('Y-up mesh must have nonzero height')
    origin = mesh.center.copy()
    origin[1] = mesh.bounds[0, 1]
    scale = height / np.ptp(mesh.vertices[:, 1])
    vertices = (mesh.vertices - origin) * scale
    _, first, inverse = np.unique(np.rint(vertices / (weld_tolerance_ratio * np.ptp(vertices, axis=0).max())),
                                  axis=0, return_index=True, return_inverse=True)
    order = np.argsort(first)
    weld = np.argsort(order)[inverse]
    triangles = weld[mesh.faces]
    keep = np.all(np.diff(np.sort(triangles, axis=1), axis=1) != 0, axis=1)
    result = skeleton.mesh_from_arrays(vertices[first[order]], triangles[keep], name=mesh.name)
    audit = {'input': str(Path(path).resolve()), 'source_sha256': sha256(data).hexdigest(),
             'directives': dict(directives), 'original_rig_used': False,
             'vertices_before_weld': mesh.num_vertices, 'vertices_after_weld': result.num_vertices,
             'triangles': len(result.faces), 'dropped_degenerate_faces': int((~keep).sum()),
             'coordinate_origin': origin.tolist(), 'coordinate_scale': float(scale),
             'normalization': {'height': height, 'weld_tolerance_ratio': weld_tolerance_ratio},
             'material_policy': 'Geometry only; source materials and UVs are not loaded'}
    return result, audit, weld


def write_video(frames, path, *, ffmpeg, width, height, fps):
    """Shared test encoder; existing outputs are never overwritten."""
    if Path(path).exists():
        raise FileExistsError(path)
    command = [str(ffmpeg), '-v', 'error', '-n', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
               '-s', f'{width}x{height}', '-r', str(fps), '-i', 'pipe:0', '-an',
               '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(path)]
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=errors)
        assert process.stdin is not None
        try:
            for image in frames:
                if image.mode != 'RGB' or image.size != (width, height):
                    raise ValueError('Unexpected video frame format')
                process.stdin.write(image.tobytes())
            process.stdin.close()
            status = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            with suppress(BrokenPipeError):
                process.stdin.close()
        if status:
            errors.seek(0)
            raise RuntimeError(errors.read().decode('utf-8', errors='replace'))


def rig_preview(mesh, rig, posed, joints, report, *, azimuth, settings):
    """Plain rest/posed comparison without decorative UI."""
    from PIL import Image, ImageDraw, ImageFont
    width, height = settings['width'], settings['height']
    image = Image.new('RGB', (width, height), '#101824')
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=18)
    points = np.concatenate([mesh.vertices, posed, rig.joints, joints])
    center = (points.min(0) + points.max(0)) / 2
    extent = np.ptp(points, axis=0)
    scale = min((width / 2 - 80) / np.hypot(extent[0], extent[2]), (height - 150) / extent[1])
    angle = np.deg2rad(azimuth)
    axes = np.array([[np.cos(angle), 0], [0, 1], [-np.sin(angle), 0]])
    depth = np.cross(axes[:, 0], axes[:, 1])
    for panel, (vertices, bones, label) in enumerate(((mesh.vertices, rig.joints, 'Rest / fitted rig'),
                                                    (posed, joints, 'Skin stress test'))):
        offset = [width * (panel + 0.5) / 2, height / 2]
        projected = (vertices - center) @ axes * [scale, -scale] + offset
        triangles = vertices[mesh.faces]
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        shade = np.abs(normals @ depth) / np.maximum(np.linalg.norm(normals, axis=1), np.finfo(float).eps)
        for face in np.argsort(triangles.mean(1) @ depth):
            value = int(60 + 125 * shade[face])
            draw.polygon([tuple(p) for p in projected[mesh.faces[face]]], fill=(value, value, value))
        projected = (bones - center) @ axes * [scale, -scale] + offset
        for joint in np.flatnonzero(rig.parents >= 0):
            draw.line([tuple(projected[rig.parents[joint]]), tuple(projected[joint])], fill='#60d6f0', width=3)
        draw.text((panel * width / 2 + 20, 20), label, font=font, fill='white')
    draw.text((20, height - 60), f"{mesh.name}: {'PASS' if report['passed'] else 'FAIL'} | "
              f"outside bone samples {report['rig_quality']['outside_bone_sample_ratio']:.1%} | "
              f"max stretch {report['edge_stretch']['max']:.3f}", font=font, fill='#f1bc80')
    return image


def run_mesh_test(*, input_path, output_dir, config, source_url=None, ffmpeg=None):
    output = Path(output_dir)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f'Refusing to overwrite {output}')
    mesh, audit, weld = load_unrigged_obj(input_path, **config['normalization'])
    rig = skeleton.fit_skeleton(mesh, config=config['rigging'])
    weights = skinning.skin_mesh(mesh, rig, config=config['skinning'])
    placement = skeleton.evaluate_skeleton(mesh, rig, config=config['rig_quality'])
    skin_report = skinning.validate_skin(weights, mesh, rig, config=config['skin_quality'])
    qa, settings = config['skin_quality'], config['preview']
    rotations = bend_pose(rig.joints, rig.parents, degrees=qa['bend_degrees'], seed=qa['bend_seed'], scale_span=qa['bend_scale_span'])
    matrices = pose_matrices(rig.joints, rig.parents, rotations, None)
    posed = deform(mesh.vertices, weights.weights, matrices)
    joints = np.einsum('nij,nj->ni', matrices[:, :3, :3], rig.joints) + matrices[:, :3, 3]
    edges = np.unique(np.sort(mesh.faces[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1), axis=0)
    lengths = np.linalg.norm(mesh.vertices[edges[:, 0]] - mesh.vertices[edges[:, 1]], axis=1)
    valid = lengths > np.finfo(float).eps
    stretch = np.linalg.norm(posed[edges[:, 0]] - posed[edges[:, 1]], axis=1)[valid] / lengths[valid]
    error = {'quantile': settings['stress_quantile'], 'value': float(np.quantile(stretch, settings['stress_quantile'])),
             'max': float(stretch.max()), 'quantile_limit': settings['stretch_quantile_limit'], 'max_limit': settings['stretch_max_limit']}
    report = {'input_audit': audit, 'source_url': source_url, 'parameters': deepcopy(config),
              'rig_quality': placement, 'skin_quality': {'ok': skin_report.ok, 'findings': [vars(f) for f in skin_report.findings]},
              'edge_stretch': error, 'passed': bool(placement['ok'] and skin_report.ok and error['value'] <= error['quantile_limit']
                                                  and error['max'] <= error['max_limit']),
              'notes': ['Unrigged geometry only; original materials and UVs are not loaded.',
                        'This is a skin stress diagnostic, not generated motion or physical simulation.',
                        'Individual asset licensing must be verified separately.']}
    clip = MotionClip.rest_clip(SkeletonTemplate(rig.name, rig.joint_names, rig.parents, rig.joints, rig.chains),
                                settings['frames'], fps=settings['fps'])
    wave = np.sin(np.linspace(0, np.pi, clip.num_frames)) ** 2
    for joint, rotation in rotations.items():
        q = matrix_to_quat(rotation)
        q *= 1 if q[0] >= 0 else -1
        sine = np.linalg.norm(q[1:])
        if sine > np.finfo(float).eps:
            clip.quats[:, joint] = quat_from_axis_angle(q[1:] / sine, 2 * np.arctan2(sine, q[0]) * wave)
    output.mkdir(parents=True, exist_ok=True)
    text = config['export']['text']
    for name, content in {'normalized.obj': skeleton.mesh_to_obj(mesh, precision=text['precision']),
                          'rig.txt': skeleton.rig_to_text(rig, skin=weights, **text),
                          'skeleton.json': json.dumps({'names': rig.joint_names, 'parents': rig.parents.tolist(),
                                                       'rest': rig.joints.tolist(), 'chains': rig.chains}, indent=2),
                          'report.json': json.dumps(report, indent=2, allow_nan=False)}.items():
        (output / name).write_text(content, encoding='utf-8')
    np.savez_compressed(output / 'binding.npz', vertices=mesh.vertices, faces=mesh.faces, joints=rig.joints,
                        parents=rig.parents, weights=weights.weights, source_to_welded=weld)
    (output / 'rigging_test.glb').write_bytes(animated_glb(mesh, rig, weights, clip, config=config['export']['glb']))
    rig_preview(mesh, rig, posed, joints, report, azimuth=0, settings=settings).save(output / 'preview.png')
    if ffmpeg is not None:
        def frames():
            positions, quaternions = fk(clip)
            for frame, rotation in enumerate(quat_to_matrix(quaternions)):
                matrices[:, :3, :3] = rotation
                matrices[:, :3, 3] = positions[frame] - np.einsum('nij,nj->ni', rotation, rig.joints)
                moved = deform(mesh.vertices, weights.weights, matrices)
                yield rig_preview(mesh, rig, moved, positions[frame], report,
                                  azimuth=360 * frame / (clip.num_frames - 1), settings=settings)
        write_video(frames(), output / 'rigging_test.mp4', ffmpeg=ffmpeg,
                    width=settings['width'], height=settings['height'], fps=settings['fps'])
    return report


class ExplicitRiggingTest(unittest.TestCase):
    mesh: Any = None
    rig: Any = None
    skin: Any = None

    def setUp(self):
        self.mesh = skeleton.mesh_from_arrays(VERTICES, FACES, name='cube')
        self.rig = skeleton.fit_skeleton(self.mesh, config=FIT)
        self.skin = skinning.skin_mesh(self.mesh, self.rig, config=SKIN)

    def test_fit_preserves_explicit_topology_and_input(self):
        before = deepcopy(FIT)
        np.testing.assert_array_equal(self.rig.parents, [1, -1, 1])
        np.testing.assert_allclose(self.rig.joints[:, 1], [-0.8, 0, 0.8], atol=1e-8)
        self.assertEqual(self.rig.joint_names, ['spine.0', 'spine.1', 'spine.2'])
        self.assertTrue(skeleton.evaluate_skeleton(self.mesh, self.rig, config=RIG_QA)['ok'])
        self.rig.params['spine_span'] = (0.2, 0.7)
        self.assertEqual(FIT, before)

    def test_paired_limbs_use_required_fields_and_requested_count(self):
        rig = skeleton.fit_skeleton(self.mesh, config={**FIT, 'limb_groups': [LIMB]})
        self.assertEqual(rig.num_joints, 9)
        self.assertEqual(set(rig.chains), {'spine', 'branch.L', 'branch.R'})
        for key in LIMB:
            with self.subTest(missing=key), self.assertRaises(ValueError):
                LimbGroup.coerce({k: v for k, v in LIMB.items() if k != key})

    def test_fit_is_equivariant_under_explicit_axes(self):
        rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
        scale, shift = 2.5, np.array([2, -3, 4])
        mesh = skeleton.mesh_from_arrays(scale * VERTICES @ rotation.T + shift, FACES, name='rotated')
        config = {**FIT, 'up': rotation @ FIT['up'], 'forward': rotation @ FIT['forward']}
        rig = skeleton.fit_skeleton(mesh, config=config)
        np.testing.assert_allclose(rig.joints, scale * self.rig.joints @ rotation.T + shift, atol=1e-7)

    def test_skin_constraints_sparse_weights_and_identity_lbs(self):
        for kernel in ('inverse', 'gaussian', 'linear'):
            with self.subTest(kernel=kernel):
                skin = skinning.skin_mesh(self.mesh, self.rig, config={**SKIN, 'kernel': kernel})
                self.assertEqual(validate_weights(skin, max_influences=3, sum_tolerance=1e-9), [])
                self.assertTrue(skinning.validate_skin(skin, self.mesh, self.rig, config=SKIN_QA).ok)
                np.testing.assert_allclose(deform(VERTICES, skin.weights, pose_matrices(self.rig.joints, self.rig.parents, {}, None)), VERTICES)
                indices, weights = skin.to_sparse(4)
                self.assertEqual(indices.shape, (8, 4))
                np.testing.assert_allclose(weights.sum(1), 1)
        rigid = skinning.skin_mesh(self.mesh, self.rig, config={**SKIN, 'max_influences': 1, 'radius_scale': 0.001, 'smooth_iterations': 0})
        np.testing.assert_array_equal(rigid.influences, np.ones(len(VERTICES)))

    def test_every_policy_key_is_required(self):
        for config, function, args in ((FIT, skeleton.fit_skeleton, (self.mesh,)),
                                       (SKIN, skinning.skin_mesh, (self.mesh, self.rig)),
                                       (RIG_QA, skeleton.evaluate_skeleton, (self.mesh, self.rig)),
                                       (SKIN_QA, skinning.validate_skin, (self.skin, self.mesh, self.rig))):
            for key in (*config, None):
                invalid = {k: v for k, v in config.items() if k != key} if key else {**config, 'preset': 'removed'}
                with self.subTest(function=function.__name__, key=key), self.assertRaises(ValueError):
                    function(*args, config=invalid)

    def test_quality_thresholds_actually_change_findings(self):
        self.assertFalse(skeleton.evaluate_skeleton(self.mesh, self.rig, config={**RIG_QA, 'min_bone_length_ratio': 2})['ok'])
        self.assertFalse(skinning.validate_skin(self.skin, self.mesh, self.rig, config={**SKIN_QA, 'max_influences': 1}).ok)

    def test_text_serializers_keep_vertex_and_joint_order(self):
        text = skeleton.rig_to_text(self.rig, skin=self.skin, **EXPORT['text'])
        self.assertEqual(text.count('\nskin '), len(VERTICES))
        self.assertIn('root spine.1\n', text)
        self.assertNotIn('\nskin ', skeleton.skeleton_to_text(self.rig, precision=6))
        obj = skeleton.mesh_to_obj(self.mesh, precision=6)
        self.assertEqual(obj.splitlines()[0], 'v -1.000000 -1.000000 -1.000000')
        self.assertEqual(obj.count('\nf '), len(FACES))

    def test_mesh_pipeline_explicit_and_fitted_with_optional_skin(self):
        for fitted in (False, True):
            for skinned in (False, True):
                config = motion_config(fitted=fitted, skinned=skinned)
                before = deepcopy(config)
                result: dict[str, Any] = generate_vibe_motion(config=config, mesh=self.mesh)
                self.assertEqual(config, before)
                self.assertEqual(result['joints'].shape, (13, 3, 3))
                self.assertEqual(result['metrics']['failures'], [])
                self.assertTrue(json.loads(result['rig_report_json'])['ok'])
                self.assertEqual('animated_glb_bytes' in result, skinned)
                if skinned:
                    self.assertTrue(json.loads(result['skin_report_json'])['passed'])
                    self.assertEqual(len(glb_document(result['animated_glb_bytes'])['skins'][0]['joints']), 3)

    def test_export_policy_is_required_and_material_is_preserved(self):
        config = motion_config(fitted=False, skinned=True)
        for path in (('export',), ('export', 'text'), ('export', 'glb'),
                     *(('export', group, key) for group in EXPORT for key in EXPORT[group])):
            invalid = deepcopy(config)
            node = invalid
            for key in path[:-1]:
                node = node[key]
            del node[path[-1]]
            with self.subTest(path=path), self.assertRaises(ValueError):
                generate_vibe_motion(config=invalid, mesh=self.mesh)
        for interpolation, material in (('STEP', None), ('LINEAR', {'name': 'test'})):
            config['export']['glb'].update(interpolation=interpolation, material=material)
            doc = glb_document(generate_vibe_motion(config=config, mesh=self.mesh)['animated_glb_bytes'])
            self.assertEqual(doc.get('materials'), None if material is None else [material])
            self.assertTrue(all(s['interpolation'] == interpolation for s in doc['animations'][0]['samplers']))

    def test_operator_mesh_and_mock_retarget_use_current_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'cube.obj'
            path.write_text(skeleton.mesh_to_obj(self.mesh, precision=6))
            for task_type in ('vibe', 'vibe_retarget'):
                retarget = Mock(return_value={})
                operator = operator_module.GenMotionOperator(output_dir=directory, retarget_fn=retarget)
                task = {'task_type': task_type, 'task_id': task_type, 'target_mesh_path': str(path),
                        'config': motion_config(fitted=False, skinned=True)}
                before = deepcopy(task)
                with patch.object(operator_module, '_load_mesh_arrays', return_value=(VERTICES, FACES)) as loader:
                    result = operator.run(task)
                loader.assert_called_once_with(path)
                self.assertEqual(task, before)
                self.assertTrue(Path(result['animated_glb_path']).read_bytes().startswith(b'glTF'))
                self.assertTrue(Path(result['motion_bvh_path']).read_bytes().startswith(b'HIERARCHY'))
                if task_type == 'vibe_retarget':
                    retarget.assert_called_once()
                    for argument, artifact in (('target_mesh_path', 'mesh_obj_path'), ('target_rig_path', 'rig_path'), ('source_motion_path', 'motion_bvh_path')):
                        self.assertEqual(retarget.call_args.kwargs[argument], result[artifact])
                    self.assertEqual(retarget.call_args.kwargs['fps'], 12)
                else:
                    retarget.assert_not_called()


class ObjMeshInputTest(unittest.TestCase):
    def test_loader_rejects_non_geometry_and_invalid_faces(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.obj'
            for text in ('bone fake\n', 'v 0 0 0\n', 'v 0 0 0\nv 1 1 0\nv 0 1 1\nf 0 1 2\n',
                         'v 0 0 0\nv 1 1 0\nv 0 1 1\nf 1 2 9\n'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    load_unrigged_obj(path, height=1.8, weld_tolerance_ratio=1e-5)

    def test_loader_triangulates_and_keeps_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'quad.obj'
            text = 'v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nv 0 0 0\nf -5 -4 -3 -2\n'
            path.write_text(text)
            mesh, audit, weld = load_unrigged_obj(path, height=2, weld_tolerance_ratio=1e-5)
            self.assertEqual(path.read_text(), text)
            self.assertEqual((mesh.num_vertices, len(mesh.faces)), (4, 2))
            self.assertEqual(weld[0], weld[-1])
            self.assertEqual(audit['vertices_before_weld'], 5)
            self.assertFalse(audit['original_rig_used'])
            self.assertAlmostEqual(np.ptp(mesh.vertices[:, 1]), 2)

    def test_cli_writes_artifacts_and_returns_quality_failure(self):
        import importlib.util
        if importlib.util.find_spec('PIL') is None:
            self.skipTest('Pillow is required for previews')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output, settings = root / 'cube.obj', root / 'result', deepcopy(MESH_TEST)
            source.write_text(skeleton.mesh_to_obj(skeleton.mesh_from_arrays(VERTICES, FACES, name='cube'), precision=8))
            settings['rigging'] = deepcopy(FIT)
            settings['preview']['stretch_max_limit'] = 0
            config = root / 'config.json'
            config.write_text(json.dumps(settings))
            result = subprocess.run([sys.executable, '-m', 'test.test_vibe_rigging', 'rig-mesh', '--input', str(source),
                                     '--output-dir', str(output), '--config', str(config)],
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
            self.assertEqual((result.returncode, result.stderr), (1, ''))
            self.assertFalse(json.loads(result.stdout)['passed'])
            for name in ('normalized.obj', 'rig.txt', 'skeleton.json', 'binding.npz', 'rigging_test.glb', 'report.json', 'preview.png'):
                self.assertTrue((output / name).is_file(), name)
            with self.assertRaises(FileExistsError):
                run_mesh_test(input_path=source, output_dir=output, config=settings)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'rig-mesh':
        import argparse
        parser = argparse.ArgumentParser(description='Test automatic rigging on an unrigged Y-up OBJ')
        parser.add_argument('--input', type=Path, required=True)
        parser.add_argument('--output-dir', type=Path, required=True)
        parser.add_argument('--config', type=Path, help='Complete JSON settings; defaults to MESH_TEST in this file')
        parser.add_argument('--source-url')
        parser.add_argument('--ffmpeg', type=Path)
        args = parser.parse_args(sys.argv[2:])
        config = json.loads(args.config.read_text()) if args.config else deepcopy(MESH_TEST)
        report = run_mesh_test(input_path=args.input, output_dir=args.output_dir, config=config,
                               source_url=args.source_url, ffmpeg=args.ffmpeg)
        print(json.dumps({**{key: report[key] for key in ('passed', 'rig_quality', 'skin_quality', 'edge_stretch')},
                          'report': str(args.output_dir / 'report.json')}, indent=2))
        sys.exit(0 if report['passed'] else 1)
    else:
        unittest.main()
