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
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils import reconstruct_joints, refine_orientation
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.checks import MeshContainment, enclosure_evidence
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.sections import canonical_mesh
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.types import RigResult
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.export import animated_glb
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.skin_checks import bend_pose
from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.skin_units import deform, pose_matrices, validate_weights
from operators.gen_motion.funcs.vibe_motion_utils.motion_utils import MotionClip, SkeletonTemplate
from operators.gen_motion.funcs.vibe_motion_utils.motion_utils.units import fk, matrix_to_quat, quat_from_axis_angle, quat_to_matrix

CAMERAS = {key: {'width': 512, 'height': 512, 'right': axis, 'up': [0, 1, 0],
                 'center': [0, 0, 0], 'pixels_per_unit': 100, 'pixel_origin': [256, 256]}
           for key, axis in (('front', [1, 0, 0]), ('side', [0, 0, 1]))}
FIT = {
    'name': 'explicit', 'up': [0, 1, 0], 'forward': [0, 0, 1],
    'names': ['spine.0', 'spine.1', 'spine.2'], 'parents': [-1, 0, 1],
    'chains': {'spine': ['spine.0', 'spine.1', 'spine.2']}, 'cameras': CAMERAS,
    'observations': {f'spine.{i}': {v: {'pixel': [256, y], 'confidence': 1.0, 'inferred': False}
                                  for v in CAMERAS} for i, y in enumerate((336, 256, 176))},
    'fit': {f'spine.{i}': {'radius': [0.1, 0.1, 0.1], 'axis': 1, 'strength': 1.0, 'allow_open': False} for i in range(3)},
    'reconstruction': {'max_reprojection_pixels': 1.0, 'max_fitted_reprojection_pixels': 2.0,
                       'max_condition': 100.0, 'axis_tolerance': 1e-8, 'conflict_policy': 'reject'},
    'geometry': {'section_resolution': 9, 'section_vertex_tolerance': 1e-10, 'section_plane_nudge': 1e-9,
                 'section_weld_tolerance': 1e-8, 'clearance_chunk_size': 256, 'prior_distance_gain': 20.0,
                 'open_quantiles': [0.1, 0.9], 'min_open_endpoints': 4, 'epsilon': 1e-12},
}
SKIN = {'kernel': 'inverse', 'falloff': 4.0, 'radius_scale': 2.0, 'max_influences': 3,
        'floor': 0.0001, 'smooth_iterations': 2, 'smooth_rate': 0.5,
        'bone_convention': 'outgoing', 'distance_epsilon_ratio': 1e-6, 'sum_tolerance': 1e-9,
        'screening': 0.08, 'edge_epsilon_ratio': 1e-6}
RIG_QA = {'rays': 7, 'ray_det_tolerance': 1e-12, 'ray_edge_tolerance': 1e-10,
          'ray_hit_tolerance': 1e-10, 'ray_merge_tolerance': 1e-8,
          'bone_sample_span': [0.05, 0.95], 'bone_samples': 11,
          'max_outside_joint_ratio': 0.0, 'max_outside_bone_ratio': 0.0, 'min_bone_length_ratio': 0.01,
          'containment': {'policy': 'report_only', 'surface_tolerance_ratio': 1e-6, 'area_epsilon': 1e-14,
                          'halfspace_tolerance': 1e-10, 'direction_pairs': 32, 'radius_ratio': 0.25}}
SKIN_QA = {'max_influences': 3, 'sum_tolerance': 1e-9, 'smoothness_quantile': 0.95,
           'smoothness_limit': 2.0, 'bend_degrees': 5.0, 'bend_seed': 0,
           'bend_scale_span': [0.6, 1.0], 'volume_tolerance': 1.0, 'travel_tolerance': 2.0}
EXPORT: dict[str, Any] = {'text': {'precision': 6, 'sum_tolerance': 1e-9, 'max_influences': 4},
                         'glb': {'bind_tolerance': 1e-9, 'sum_tolerance': 1e-9, 'material': None, 'interpolation': 'STEP'}}
VERTICES = np.array([[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
                     [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]], dtype=float)
FACES = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                  [3, 7, 6], [3, 6, 2], [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5]])
def mesh_test_settings(inputs):
    settings = dict(readback_tolerance=1e-6, rig_quality={**deepcopy(RIG_QA), 'max_outside_bone_ratio': 0.05},
                    skin_quality={**SKIN_QA, 'max_influences': 4, 'bend_degrees': 25.0}, export=deepcopy(EXPORT),
                    preview={'width':1280,'height':768,'fps':15,'frames':61,'stress_quantile':0.99,
                             'stretch_quantile_limit':1.3,'stretch_max_limit':3.0})
    settings.update(deepcopy(inputs))
    if 'normalization' not in settings:
        settings['normalization'] = deepcopy(settings['source']['normalization'])
    return settings


MESH_TEST = mesh_test_settings(json.loads((Path(__file__).with_name('vibe_motion_examples') / 'rigging_example.json').read_text()))


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
        config.update(skeleton=None, rigging=deepcopy(FIT))
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
    vertices, faces, directives, labels, label = [], [], Counter(), [], ''
    if not np.isfinite([height, weld_tolerance_ratio]).all() or min(height, weld_tolerance_ratio) <= 0:
        raise ValueError('Height and weld tolerance must be finite and positive')
    for line in data.decode('utf-8-sig').splitlines():
        items = line.split('#', 1)[0].split()
        if not items:
            continue
        kind = items[0]
        directives[kind] += 1
        if kind == 'o':
            label = ' '.join(items[1:])
        elif kind == 'v':
            if len(items) != 4:
                raise ValueError('OBJ vertices need three coordinates')
            vertices.append(list(map(float, items[1:])))
            labels.append(label)
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
    merged = np.zeros((len(first), 3))
    np.add.at(merged, weld, vertices)
    merged /= np.bincount(weld)[:, None]
    result = skeleton.mesh_from_arrays(merged, triangles[keep], name=mesh.name)
    objects = {name: np.unique(weld[np.asarray(labels) == name]).tolist() for name in set(labels)}
    audit = {'input': str(Path(path).resolve()), 'source_sha256': sha256(data).hexdigest(), 'objects': objects,
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


def skin_constraints(mesh, audit, rig, config):
    """Translate this fixture's object regions and seam settings into explicit arrays."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.spatial import cKDTree
    if 'skin_regions' not in config:
        return {}, {}
    local, _, _ = canonical_mesh(mesh, config['rigging']['up'], config['rigging']['forward'], vertical=True, config=config['rigging']['geometry'])
    x, y, _ = local.vertices.T
    p = config['skin_regions']
    def ramp(value):
        u = np.clip(value / p['transition_width'], 0, 1)
        return u*u*(3-2*u)
    torso = (1-ramp(np.abs(x)-p['torso_half_width'])) * ramp(y-p['torso_min_y']) * ramp(p['torso_max_y']-y)
    head = ramp(y-p['head_min_y'])
    allowed, bias = np.ones((mesh.num_vertices, rig.num_joints), bool), np.ones((mesh.num_vertices, rig.num_joints))
    for name, ids in rig.chains.items():
        if name == 'spine':
            bias[:, ids] *= (1+(p['torso_spine_bias']-1)*torso)[:, None]
        else:
            side = 1 if name.endswith('.L') else -1
            allowed[np.ix_(side*x < -p['midline_overlap'], ids)] = False
            gate = ramp(y-p['arm_min_y'])*ramp(p['arm_max_y']-y)*(1-torso)*(1-head) if name.startswith('arm') else ramp(p['leg_max_y']-y)
            bias[:, ids] *= (p['soft_floor']+(1-p['soft_floor'])*gate)[:, None]
    tagged, object_allowed = np.zeros(mesh.num_vertices, bool), np.zeros_like(allowed)
    for name, bones in config['object_bones'].items():
        rows = audit['objects'].get(name, [])
        if not rows:
            raise ValueError(f'Missing configured OBJ region: {name}')
        object_allowed[np.ix_(rows, [rig.joint_names.index(n) for n in bones])] = True
        tagged[rows] = True
    allowed[tagged], bias[tagged] = object_allowed[tagged], 1
    cloth = np.asarray(audit['objects'][config['cloth_object']], int)
    edges = mesh.faces[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2)
    graph = coo_matrix((np.ones(len(edges)), edges.T), shape=(mesh.num_vertices, mesh.num_vertices)).tocsr()
    anchors, seams = {}, {}
    for name, spec in config['seams'].items():
        j, start = (rig.joint_names.index(spec[k]) for k in ('joint', 'segment_start'))
        direction = rig.joints[j]-rig.joints[start]
        fraction = (mesh.vertices[cloth]-rig.joints[start]) @ direction / (direction @ direction)
        near = np.linalg.norm(mesh.vertices[cloth]-rig.joints[j], axis=1) <= spec['radius_ratio']*mesh.scale
        cuff = cloth[near & (fraction >= spec['min_segment_fraction'])]
        component = np.asarray(audit['objects'][spec['object']], int)
        if not len(cuff) or not len(component):
            raise ValueError(f'{name}: missing seam evidence')
        _, groups = connected_components(graph[cuff][:, cuff], directed=False)
        cuff = cuff[groups == np.bincount(groups).argmax()]
        for vertex in np.r_[cuff, component]:
            if not allowed[vertex, j] or vertex in anchors and anchors[vertex] != j:
                raise ValueError('Incompatible seam anchor constraints')
            anchors[int(vertex)] = j
        distance, nearest = cKDTree(mesh.vertices[component]).query(mesh.vertices[cuff])
        mask = distance <= config['seam_max_gap_ratio']*mesh.scale
        seams[name] = np.column_stack([cuff[mask], component[nearest[mask]]])
        if not len(seams[name]):
            raise ValueError(f'{name}: no seam correspondence')
    ids = np.asarray(sorted(anchors), int)
    values = np.eye(rig.num_joints)[[anchors[i] for i in ids]]
    return {'allowed_bones': allowed, 'weight_bias': bias, 'anchors': {'ids': ids, 'values': values}}, seams


def run_mesh_test(*, input_path, output_dir, config, source_url=None, ffmpeg=None, with_motion=False,
                  prepare_only=False, annotations=None, estimator=None, provenance=None):
    from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.estimation import prepare_projection, estimate_rigging, load_estimation
    output = Path(output_dir).resolve()
    examples = Path(__file__).with_name('vibe_motion_examples').resolve()
    if output == examples or examples in output.parents:
        raise ValueError('The examples directory is input-only; select an output directory')
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f'Refusing to overwrite {output}')
    config = deepcopy(config)
    mesh, audit, weld = load_unrigged_obj(input_path, **config['normalization'])
    if 'requested_chains' in config['rigging']:
        if prepare_only:
            prepare_projection(mesh, audit, config['rigging'], config['projection'], output)
            return {'prepared': True, 'calibration': str(output / 'projection/calibration.json'), 'estimation_performed': False}
        if annotations is not None:
            config['rigging'] = load_estimation(annotations, mesh, audit, config['rigging'], config['projection'])
        elif estimator is not None and provenance:
            config['rigging'] = estimate_rigging(mesh, audit, config['rigging'], config['projection'], output,
                                                estimate=estimator, provenance=provenance)
        else:
            raise ValueError('Visual estimation needs a configured VLM or explicit --annotations; use --prepare-only to render views')
    elif prepare_only or annotations is not None or estimator is not None:
        raise ValueError('Estimation options require a short rigging request, not resolved landmarks')
    rig = skeleton.fit_skeleton(mesh, config=config['rigging'])
    constraints, seams = skin_constraints(mesh, audit, rig, config)
    weights = skinning.skin_mesh(mesh, rig, config=config['skinning'], **constraints)
    initial, orientation, selected = deepcopy(rig), None, None
    before = skeleton.evaluate_skeleton(mesh, rig, config=config['rig_quality'])
    if with_motion:
        from test.test_vibe_motion import evaluate_bound_motion
        limbs = {k: {'chain': [rig.joint_names.index(n) for n in v['chain']], 'forward': v['forward']} for k, v in config['probe_parts'].items()}
        fixed = weights.weights.copy()
        rig, poles, selected, orientation = refine_orientation(mesh, rig, limbs,
            lambda candidate, poles: evaluate_bound_motion(mesh, candidate, weights, config, poles),
            config=config['orientation'], quality=config['rig_quality'])
        np.testing.assert_array_equal(weights.weights, fixed)
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
              'estimation': {'method': 'explicit_replay' if annotations is not None else 'fresh_estimation' if estimator is not None else 'resolved_input',
                             'annotations': str(Path(annotations).resolve()) if annotations is not None else None, 'provenance': provenance},
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
    report.update(strategy='calibrated_landmarks_constrained_anchored_skinning', reconstruction=rig.params['diagnostics'],
                  initial_rig_quality=before, initial_joints=initial.joints.tolist(), orientation=orientation,
                  anchor_count=len(constraints.get('anchors', {}).get('ids', [])))
    if selected is not None:
        from test.test_vibe_motion import export_bound_motion
        report['motion'] = selected['metrics']
        report['passed'] = bool(report['passed'] and not selected['metrics']['motion']['failures']
                                and selected['metrics']['edge_stretch_max'] <= settings['stretch_max_limit']
                                and selected['metrics']['edge_stretch_p99'] <= settings['stretch_quantile_limit'])
        report['motion_export'] = export_bound_motion(mesh, rig, weights, selected, config, output, report, ffmpeg=ffmpeg)
        frames_world = selected['payload'][2]
        report['seams'] = {name: {'pairs': len(pairs), 'gap_increase_max': float(np.max(
            np.linalg.norm(frames_world[:, pairs[:,0]]-frames_world[:, pairs[:,1]], axis=-1)
            - np.linalg.norm(mesh.vertices[pairs[:,0]]-mesh.vertices[pairs[:,1]], axis=-1))),
            'weight_l1_max': float(np.abs(weights.weights[pairs[:,0]]-weights.weights[pairs[:,1]]).sum(1).max())}
            for name, pairs in seams.items()}
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

    def test_skin_anchors(self):
        allowed = np.ones((8, 3), bool)
        allowed[:4, 2] = False
        anchors = {'ids': [0, 7], 'values': [[1, 0, 0], [0, 0, 1]]}
        skin = skinning.skin_mesh(self.mesh, self.rig, config=SKIN, allowed_bones=allowed, anchors=anchors)
        np.testing.assert_array_equal(skin.weights[anchors['ids']], anchors['values'])
        self.assertTrue(np.all(skin.weights[~allowed] == 0))
        empty = skinning.skin_mesh(self.mesh, self.rig, config=SKIN, anchors={'ids': [], 'values': []})
        np.testing.assert_allclose(empty.weights, self.skin.weights)

    def test_invalid_anchors(self):
        allowed = np.ones((8, 3), bool)
        allowed[:4, 2] = False
        cases = {
            'forbidden_bone': {'ids': [0], 'values': [[0, 0, 1]]},
            'duplicate_vertex': {'ids': [0, 0], 'values': [[1, 0, 0], [1, 0, 0]]},
        }
        for name, anchors in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                skinning.skin_mesh(self.mesh, self.rig, config=SKIN, allowed_bones=allowed, anchors=anchors)

    def test_weight_pruning(self):
        from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.skin_generate import prune_to_k

        top, _ = prune_to_k([[.6, .2, .2]], [[10, 1, 2]], max_influences=2)
        np.testing.assert_allclose(top, [[.75, .25, 0]])

    def test_diffusion_isolation(self):
        from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.skin_generate import anchored_weights

        vertices = np.r_[VERTICES, VERTICES + [0, 0, .001]]
        mesh = skeleton.mesh_from_arrays(vertices, np.r_[FACES, FACES + 8], name='two_surfaces')
        prior = np.r_[np.tile([1., 0], (8, 1)), np.tile([0., 1], (8, 1))]
        weights = anchored_weights(mesh, prior, [], [], np.ones((16, 2), bool), config=SKIN)
        np.testing.assert_allclose(weights, prior, atol=1e-12)

    def test_open_mesh(self):
        opened = skeleton.mesh_from_arrays(VERTICES, FACES[2:], name='open_box')
        config = deepcopy(FIT)
        for hint in config['fit'].values():
            hint.update(allow_open=True, radius=[1.,1.,1.])
        before = skeleton.fit_skeleton(opened, config=config)
        positions = before.joints.copy()
        report = skeleton.evaluate_skeleton(opened, before, config=RIG_QA)
        self.assertTrue(all(v['status'] == 'uncertified' for v in report['containment']['joints'].values()))
        np.testing.assert_array_equal(before.joints, positions)

    def test_containment_names(self):
        closed = MeshContainment(self.mesh, config=RIG_QA)
        self.assertTrue(closed.classify([[0, 0, 0]], ['center'])['passed'])
        for names in ([], ['same', 'same']):
            with self.subTest(names=names), self.assertRaises(ValueError):
                closed.classify([[0, 0, 0], [0, 0, 0]], names)

    def test_orientation_selection(self):
        config = {**MESH_TEST['orientation'], 'min_enclosure_fraction': 0., 'max_enclosure_drop': 1.,
                  'max_reprojection_pixels': 20.}
        limbs = {'probe': {'chain': [0, 1, 2], 'forward': [0, 0, 1]}}
        shared = {}

        def evaluate(rig, poles):
            shared.update(score=5. if poles else 10., metrics={'z': float(rig.joints[1, 2])})
            return shared

        final, poles, selected, report = refine_orientation(
            self.mesh, self.rig, limbs, evaluate, config=config, quality=RIG_QA)
        self.assertEqual(report['initial_score'], 10.)
        self.assertEqual(report['final_score'], 5.)
        self.assertEqual(sum(item['accepted'] for item in report['history']), 1)
        np.testing.assert_array_equal(final.joints, self.rig.joints)
        self.assertIn('probe', poles)
        shared['score'] = 99
        self.assertEqual(selected['score'], 5.)

    def test_fit_topology(self):
        before = deepcopy(FIT)
        np.testing.assert_array_equal(self.rig.parents, [-1, 0, 1])
        np.testing.assert_allclose(self.rig.joints[:, 1], [-0.8, 0, 0.8], atol=1e-8)
        self.assertEqual(self.rig.joint_names, ['spine.0', 'spine.1', 'spine.2'])
        self.assertTrue(skeleton.evaluate_skeleton(self.mesh, self.rig, config=RIG_QA)['ok'])
        self.rig.params['spine_span'] = (0.2, 0.7)
        self.assertEqual(FIT, before)

    def test_invalid_landmarks(self):
        for change in ('parallel', 'missing', 'inconsistent', 'legacy'):
            config = deepcopy(FIT)
            if change == 'parallel':
                config['cameras']['side'] = deepcopy(config['cameras']['front'])
            elif change == 'missing':
                del config['observations']['spine.0']
            elif change == 'inconsistent':
                config['observations']['spine.0']['side']['pixel'][1] += 80
            else:
                config['spine_span'] = [0.1, 0.9]
            with self.subTest(change=change), self.assertRaises(ValueError):
                skeleton.fit_skeleton(self.mesh, config=config)

    def test_fit_equivariance(self):
        rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
        scale, shift = 2.5, np.array([2, -3, 4])
        mesh = skeleton.mesh_from_arrays(scale * VERTICES @ rotation.T + shift, FACES, name='rotated')
        config = deepcopy(FIT)
        config.update(up=rotation @ FIT['up'], forward=rotation @ FIT['forward'])
        for camera in config['cameras'].values():
            camera.update(right=rotation @ camera['right'], up=rotation @ camera['up'],
                          center=scale * rotation @ camera['center'] + shift,
                          pixels_per_unit=camera['pixels_per_unit'] / scale)
        rig = skeleton.fit_skeleton(mesh, config=config)
        np.testing.assert_allclose(rig.joints, scale * self.rig.joints @ rotation.T + shift, atol=1e-7)

    def test_skin_kernels(self):
        matrices = pose_matrices(self.rig.joints, self.rig.parents, {}, None)
        for kernel in ('inverse', 'gaussian', 'linear'):
            with self.subTest(kernel=kernel):
                skin = skinning.skin_mesh(self.mesh, self.rig, config={**SKIN, 'kernel': kernel})
                self.assertEqual(validate_weights(skin, max_influences=3, sum_tolerance=1e-9), [])
                self.assertTrue(skinning.validate_skin(skin, self.mesh, self.rig, config=SKIN_QA).ok)
                np.testing.assert_allclose(deform(VERTICES, skin.weights, matrices), VERTICES)
                indices, weights = skin.to_sparse(4)
                self.assertEqual(indices.shape, (8, 4))
                np.testing.assert_allclose(weights.sum(1), 1)

    def test_rigid_weights(self):
        config = {**SKIN, 'max_influences': 1, 'radius_scale': 0.001, 'smooth_iterations': 0}
        rigid = skinning.skin_mesh(self.mesh, self.rig, config=config)
        np.testing.assert_array_equal(rigid.influences, np.ones(len(VERTICES)))

    def test_required_policy(self):
        for config, function, args in ((FIT, skeleton.fit_skeleton, (self.mesh,)),
                                       (SKIN, skinning.skin_mesh, (self.mesh, self.rig)),
                                       (RIG_QA, skeleton.evaluate_skeleton, (self.mesh, self.rig)),
                                       (SKIN_QA, skinning.validate_skin, (self.skin, self.mesh, self.rig))):
            for key in (*config, None):
                invalid = {k: v for k, v in config.items() if k != key} if key else {**config, 'preset': 'removed'}
                with self.subTest(function=function.__name__, key=key), self.assertRaises(ValueError):
                    function(*args, config=invalid)

    def test_quality_limits(self):
        placement = skeleton.evaluate_skeleton(
            self.mesh, self.rig, config={**RIG_QA, 'min_bone_length_ratio': 2})
        skin = skinning.validate_skin(
            self.skin, self.mesh, self.rig, config={**SKIN_QA, 'max_influences': 1})
        self.assertFalse(placement['ok'])
        self.assertFalse(skin.ok)

    def test_text_export(self):
        text = skeleton.rig_to_text(self.rig, skin=self.skin, **EXPORT['text'])
        self.assertEqual(text.count('\nskin '), len(VERTICES))
        self.assertIn('root spine.0\n', text)
        self.assertNotIn('\nskin ', skeleton.skeleton_to_text(self.rig, precision=6))
        obj = skeleton.mesh_to_obj(self.mesh, precision=6)
        self.assertEqual(obj.splitlines()[0], 'v -1.000000 -1.000000 -1.000000')
        self.assertEqual(obj.count('\nf '), len(FACES))

    def test_mesh_pipeline(self):
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

    def test_required_export_policy(self):
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

    def test_glb_options(self):
        config = motion_config(fitted=False, skinned=True)
        for interpolation, material in (('STEP', None), ('LINEAR', {'name': 'test'})):
            with self.subTest(interpolation=interpolation, material=material):
                config['export']['glb'].update(interpolation=interpolation, material=material)
                result = generate_vibe_motion(config=config, mesh=self.mesh)
                doc = glb_document(result['animated_glb_bytes'])
                self.assertEqual(doc.get('materials'), None if material is None else [material])
                samplers = doc['animations'][0]['samplers']
                self.assertTrue(all(s['interpolation'] == interpolation for s in samplers))

    def test_operator_artifacts(self):
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
                    for argument, artifact in (
                        ('target_mesh_path', 'mesh_obj_path'),
                        ('target_rig_path', 'rig_path'),
                        ('source_motion_path', 'motion_bvh_path'),
                    ):
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
            source, output = root / 'cube.obj', root / 'result'
            settings = {k: deepcopy(MESH_TEST[k]) for k in ('normalization', 'skinning', 'rig_quality', 'skin_quality', 'export', 'preview')}
            source.write_text(skeleton.mesh_to_obj(skeleton.mesh_from_arrays(VERTICES, FACES, name='cube'), precision=8))
            settings['rigging'] = deepcopy(FIT)
            for camera in settings['rigging']['cameras'].values():
                camera.update(center=[0, 0.9, 0], pixels_per_unit=100 / 0.9)
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


class VisualEstimationTest(unittest.TestCase):
    def test_sequential_estimation_and_bound_replay(self):
        from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.estimation import estimate_rigging, load_estimation
        mesh = skeleton.mesh_from_arrays(VERTICES, FACES, name='cube')
        request = {k: deepcopy(FIT[k]) for k in ('name','up','forward','geometry','reconstruction')}
        request.update(task='Synthetic test only', requested_chains={
            key: {'joints': names, 'meaning': key, 'fit': deepcopy(FIT['fit'][names[0]])}
            for key, names in {'base':['spine.0'], 'upper':['spine.1','spine.2']}.items()})
        projection = {'width':128,'height':128,'padding_ratio':.1,
                      'views':{k:{a:v[a] for a in ('right','up')} for k,v in CAMERAS.items()}}
        audit = {'source_sha256':'synthetic-test','normalization':{'height':2,'weld_tolerance_ratio':1e-5}}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            calls = []
            def fake(prompt, images):
                from PIL import Image
                manifest = json.loads((output/'projection/calibration.json').read_text())
                self.assertEqual(set(images), set(CAMERAS))
                for path in images.values():
                    with Image.open(path) as image:
                        self.assertEqual(image.size,(128,128))
                        self.assertGreater(np.asarray(image).max(),24)
                stage = len(calls)
                calls.append(prompt)
                if stage == 0:
                    return {**{k:FIT[k] for k in ('names','parents')}, 'chains':{k:v['joints'] for k,v in request['requested_chains'].items()}}
                if stage == 2:
                    self.assertIn('"spine.0": {"front":', prompt)
                names = list(request['requested_chains'].values())[stage-1]['joints']
                observations = {}
                for name in names:
                    point = np.array([0,(-.8,0,.8)[FIT['names'].index(name)],0])
                    observations[name] = {view:{'pixel':(camera['pixels_per_unit']*np.stack([camera['right'],-np.asarray(camera['up'])]) @ (point-camera['center']) + camera['pixel_origin']).tolist(),
                                                      'confidence':1.,'inferred':False} for view,camera in manifest['cameras'].items()}
                return {'observations':observations}
            resolved = estimate_rigging(mesh,audit,request,projection,output,estimate=fake,provenance={'method':'test_stub'})
            self.assertEqual(len(calls),3)
            np.testing.assert_allclose(skeleton.fit_skeleton(mesh,config=resolved).joints[:,1],[-.8,0,.8],atol=1e-8)
            self.assertEqual(load_estimation(output/'annotations.json',mesh,audit,request,projection),resolved)
            with self.assertRaises(ValueError):
                load_estimation(output/'annotations.json',mesh,{**audit,'source_sha256':'changed'},request,projection)
            from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.estimation import _digest
            bundle = json.loads((output/'annotations.json').read_text())
            tampered = deepcopy(bundle)
            tampered['calibration']['images'] = {}
            tampered['calibration_sha256'] = _digest(tampered['calibration'])
            (output/'invalid.json').write_text(json.dumps(tampered))
            with self.assertRaises(ValueError):
                load_estimation(output/'invalid.json',mesh,audit,request,projection)
            (output/'projection/front.png').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                load_estimation(output/'annotations.json',mesh,audit,request,projection)
        for reply in ({'error':'occluded'}, {'api_response':{'choices':[]}},
                      {'api_response':{'choices':[{'finish_reason':'length','message':{'content':'partial'}}]}}):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                with self.assertRaises(ValueError):
                    estimate_rigging(mesh,audit,request,projection,output,estimate=lambda *args:reply,provenance={'method':'test_stub'})
                self.assertEqual(json.loads((output/'estimation_00.json').read_text())['response'],reply)
                self.assertFalse((output/'annotations.json').exists())

    def test_vision_transport_sends_images_and_fails_without_configuration(self):
        from models.common import cloud_api
        from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.estimation import make_vlm_estimator
        options = MESH_TEST['estimation']
        with self.assertRaises(ValueError):
            make_vlm_estimator(base_url=None,model=None,api_key=None,settings=options)
        with tempfile.TemporaryDirectory() as directory, patch.object(cloud_api,'CloudAPIClient') as factory:
            path = Path(directory)/'view.png'
            path.write_bytes(b'png-test-payload')
            client = factory.return_value
            client.request.return_value = {'choices':[{'finish_reason':'stop','message':{'content':'{"observations":{}}'}}]}
            estimate = make_vlm_estimator(base_url='https://example.invalid/v1',model='test-vision',api_key='test-only',settings=options)
            self.assertEqual(estimate('Inspect image',{'front':path})['api_response'],client.request.return_value)
            body = client.request.call_args.kwargs['json_body']
            self.assertEqual(body['model'],'test-vision')
            self.assertTrue(body['messages'][0]['content'][-1]['image_url']['url'].startswith('data:image/png;base64,'))
            client.close.assert_called_once()
            client.request.return_value['choices'][0]['finish_reason'] = 'length'
            self.assertEqual(estimate('Inspect image',{'front':path})['api_response']['choices'][0]['finish_reason'],'length')

    def test_asymmetric_projection_keeps_all_vertices_inside_padding(self):
        from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.estimation import _calibrate
        from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.generate import camera_matrices
        mesh = skeleton.mesh_from_arrays(np.array([[0,0,0],[3,0,0],[0,2,0],[0,0,1.]]),
                                         np.array([[0,2,1],[0,1,3],[0,3,2],[1,2,3]]),name='asymmetric')
        projection = deepcopy(MESH_TEST['projection'])
        cameras = _calibrate(mesh,MESH_TEST['rigging'],projection)
        for matrix,offset,size in camera_matrices(cameras,tolerance=1e-8).values():
            pixels = mesh.vertices @ matrix.T + offset
            low = (size-1)*projection['padding_ratio']
            high = (size-1)*(1-projection['padding_ratio'])
            self.assertTrue(np.all(pixels >= low-1e-8))
            self.assertTrue(np.all(pixels <= high+1e-8))

    def test_example_is_request_only_and_output_is_protected(self):
        self.assertNotIn('observations',MESH_TEST['rigging'])
        self.assertNotIn('cameras',MESH_TEST['rigging'])
        self.assertNotIn('parents',MESH_TEST['rigging'])
        with self.assertRaises(ValueError):
            run_mesh_test(input_path='unused.obj',output_dir=Path(__file__).with_name('vibe_motion_examples'),config=MESH_TEST)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'rig-mesh':
        import argparse
        parser = argparse.ArgumentParser(description='Test automatic rigging on an unrigged Y-up OBJ')
        parser.add_argument('--input', type=Path, required=True)
        parser.add_argument('--output-dir', type=Path, required=True)
        parser.add_argument('--config', type=Path, help='Complete JSON settings; defaults to MESH_TEST in this file')
        parser.add_argument('--source-url')
        parser.add_argument('--ffmpeg', type=Path)
        parser.add_argument('--with-motion', action='store_true', help='Evaluate and export the position/IK action with fixed skin weights')
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument('--prepare-only', action='store_true', help='Render calibrated views without calling a model')
        modes.add_argument('--annotations', type=Path, help='Explicitly replay a matching generated annotations.json')
        parser.add_argument('--vlm-base-url', help='HTTPS OpenAI-compatible API base, or VIBE_VLM_BASE_URL')
        parser.add_argument('--vlm-model', help='Vision-capable model ID, or VIBE_VLM_MODEL')
        parser.add_argument('--vlm-key-env', default='VIBE_VLM_API_KEY', help='Environment variable containing the API key')
        args = parser.parse_args(sys.argv[2:])
        if args.prepare_only and args.with_motion:
            parser.error('--prepare-only cannot generate motion')
        config = mesh_test_settings(json.loads(args.config.read_text())) if args.config else deepcopy(MESH_TEST)
        estimator, provenance = None, None
        if 'requested_chains' in config['rigging'] and not args.prepare_only and args.annotations is None:
            import os
            from operators.gen_motion.funcs.vibe_motion_utils.rigging_utils.estimation import make_vlm_estimator
            model = args.vlm_model or os.environ.get('VIBE_VLM_MODEL')
            estimator = make_vlm_estimator(base_url=args.vlm_base_url or os.environ.get('VIBE_VLM_BASE_URL'),
                model=model, api_key=os.environ.get(args.vlm_key_env), settings=config['estimation'])
            provenance = {'method': 'vision_api', 'model': model, 'anatomical_ground_truth': False}
        report = run_mesh_test(input_path=args.input, output_dir=args.output_dir, config=config,
            source_url=args.source_url, ffmpeg=args.ffmpeg, with_motion=args.with_motion,
            prepare_only=args.prepare_only, annotations=args.annotations, estimator=estimator, provenance=provenance)
        print(json.dumps(report if args.prepare_only else {**{key: report[key] for key in ('passed', 'rig_quality', 'skin_quality', 'edge_stretch')},
                          'report': str(args.output_dir / 'report.json')}, indent=2))
        sys.exit(0 if args.prepare_only or report['passed'] else 1)
    else:
        unittest.main()
