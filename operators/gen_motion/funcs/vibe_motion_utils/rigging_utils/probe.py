"""Probe motion for a freshly skinned rig: small per-joint oscillations, then LBS and edge stretch.

Meant for eyeballing rigging quality before any designed motion. Every setting is explicit:
``probe_motion(rig, config)`` needs ``fps``, ``seconds``, ``amp`` (degrees), ``cycles`` and optional ``chains``
{chain: amp} and ``joints`` {joint: {axis?, amp, cycles, type: oscillate | spin | counter | translate}}.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from .skin_units import SkinWeights
from .templates import finite_number, integer

__all__ = ['edge_stretch', 'joint_axes', 'lbs', 'pose', 'probe_motion', 'probe_report']

_JOINT_TYPES = ('oscillate', 'spin', 'counter', 'translate')


def _require(config, required, label, optional=()):
    if not isinstance(config, dict):
        raise ValueError(f'{label} config must be a dictionary')
    missing, unknown = set(required) - set(config), set(config) - set(required) - set(optional)
    if missing or unknown:
        raise ValueError(f'{label} config missing keys {sorted(missing)}, unknown keys {sorted(unknown)}')
    return config


def joint_axes(rig, up=(0.0, 1.0, 0.0), forward=(0.0, 0.0, 1.0)):
    """Two unit axes per joint perpendicular to its bone (toward the mean child, else along the incoming bone)."""
    count = rig.num_joints
    first, second = np.zeros((count, 3)), np.zeros((count, 3))
    up, forward = np.asarray(up, float), np.asarray(forward, float)
    for j in range(count):
        kids = np.flatnonzero(rig.parents == j)
        if len(kids):
            bone = rig.joints[kids].mean(0) - rig.joints[j]
        elif rig.parents[j] >= 0:
            bone = rig.joints[j] - rig.joints[rig.parents[j]]
        else:
            bone = up
        bone = bone / max(np.linalg.norm(bone), 1e-20)
        reference = forward if abs(bone @ forward) < .9 else up
        x = np.cross(bone, reference)
        x /= max(np.linalg.norm(x), 1e-20)
        first[j], second[j] = x, np.cross(bone, x)
    return first, second


def _validate(config):
    p = _require(config, ('fps', 'seconds', 'amp', 'cycles'), 'probe motion', optional=('chains', 'joints'))
    for key in ('fps', 'seconds', 'amp', 'cycles'):
        finite_number(p[key], key)
    if p['fps'] <= 0 or p['seconds'] <= 0:
        raise ValueError('probe fps and seconds must be positive')
    if p['fps'] * p['seconds'] < 2 or abs(p['fps'] * p['seconds'] - round(p['fps'] * p['seconds'])) > 1e-9:
        raise ValueError('probe fps * seconds must be an integer of at least two frames')
    for name, value in p.get('chains', {}).items():
        finite_number(value, f'chains.{name}')
    for name, jc in p.get('joints', {}).items():
        _require(jc, ('type',), f'probe joint {name}', optional=('axis', 'amp', 'cycles'))
        if jc['type'] not in _JOINT_TYPES:
            raise ValueError(f'probe joint {name}: type must be one of {_JOINT_TYPES}')
        if jc['type'] in ('spin', 'counter', 'translate') and 'amp' not in jc:
            raise ValueError(f'probe joint {name}: {jc["type"]} needs an explicit amp')
        if jc['type'] == 'translate' and ('axis' not in jc or 'cycles' not in jc):
            raise ValueError(f'probe joint {name}: translate needs explicit axis and cycles')
        if 'axis' in jc and (np.shape(jc['axis']) != (3,) or float(np.linalg.norm(jc['axis'])) <= 0):
            raise ValueError(f'probe joint {name}: axis must be a nonzero 3-vector')
    return p


def probe_motion(rig, config):
    """(times (T,), quaternions xyzw (T,J,4), translations {joint name: (T,3)})."""
    p = _validate(config)
    fps, seconds = float(p['fps']), float(p['seconds'])
    count = int(round(fps * seconds))
    t = np.arange(count) / fps
    joints = p.get('joints', {})
    chain_amp = p.get('chains', {})
    unknown = set(joints) - set(rig.joint_names)
    if unknown:
        raise ValueError(f'Unknown probe joints: {sorted(unknown)}')
    first, second = joint_axes(rig)
    chain_of = {j: c for c, idx in rig.chains.items() for j in idx}
    chain_ids = {c: i for i, c in enumerate(rig.chains)}
    rotations = np.tile(np.array([0., 0., 0., 1.]), (count, rig.num_joints, 1))
    translations = {}
    envelope = np.sin(np.pi * np.clip(t / seconds, 0, 1)) ** 2
    first_half = t < seconds / 2
    extent = float(np.ptp(rig.joints, axis=0).max())
    for j in range(rig.num_joints):
        name = rig.joint_names[j]
        jc = joints.get(name, {})
        if jc.get('type') == 'translate':
            axis = np.asarray(jc['axis'], float)
            axis = axis / np.linalg.norm(axis)
            base = rig.joints[j] - (rig.joints[rig.parents[j]] if rig.parents[j] >= 0 else 0)
            swing = np.sin(2 * np.pi * t / seconds * float(jc['cycles']))[:, None]
            translations[name] = base[None] + swing * float(jc['amp']) * extent * axis[None]
            continue
        has_kids = bool(np.any(rig.parents == j))
        if (rig.parents[j] < 0 or not has_kids) and not jc:
            continue
        chain = chain_of.get(j)
        amp = float(jc.get('amp', chain_amp.get(chain, p['amp'])))
        if 'axis' in jc:
            axis = np.asarray(jc['axis'], float)
            axes = np.tile(axis / np.linalg.norm(axis), (count, 1))
        else:
            axes = np.where(first_half[:, None], first[j], second[j])
        cycles = float(jc.get('cycles', p['cycles']))
        order = rig.chains[chain].index(j) if chain in rig.chains else 0
        phase = .6 * chain_ids.get(chain, 0) + .45 * order
        angle = np.radians(amp) * envelope * np.sin(2 * np.pi * cycles * t / seconds + phase)
        if jc.get('type') == 'spin':
            angle = np.radians(amp) * t / seconds
        elif jc.get('type') == 'counter':
            angle = -np.radians(amp) * t / seconds
        rotations[:, j] = Rotation.from_rotvec(axes * angle[:, None]).as_quat()
    return t, rotations, translations


def pose(rig, rotations, translations):
    """Skinning matrices (T,J,4,4) and joint world positions (T,J,3) of a probe motion."""
    count, joints = rotations.shape[:2]
    local_rotation = Rotation.from_quat(rotations.reshape(-1, 4)).as_matrix().reshape(count, joints, 3, 3)
    world = np.tile(np.eye(4), (count, joints, 1, 1))
    order, seen = [], set()

    def visit(j):
        if j in seen:
            return
        if rig.parents[j] >= 0:
            visit(int(rig.parents[j]))
        seen.add(j)
        order.append(j)
    for j in range(joints):
        visit(j)
    for j in order:
        parent = int(rig.parents[j])
        local = np.tile(np.eye(4), (count, 1, 1))
        local[:, :3, :3] = local_rotation[:, j]
        name = rig.joint_names[j]
        offset = rig.joints[j] - (rig.joints[parent] if parent >= 0 else 0)
        local[:, :3, 3] = translations[name] if name in translations else offset
        world[:, j] = local if parent < 0 else np.einsum('tij,tjk->tik', world[:, parent], local)
    inverse = np.tile(np.eye(4), (joints, 1, 1))
    inverse[:, :3, 3] = -rig.joints
    return np.einsum('tjik,jkl->tjil', world, inverse), world[:, :, :3, 3]


def lbs(vertices, indices, weights, skin_matrices):
    """Linear blend skinning with sparse influences: vertices (V,3), indices/weights (V,K), matrices (T,J,4,4)."""
    out = np.zeros((len(skin_matrices), len(vertices), 3))
    homogeneous = np.c_[vertices, np.ones(len(vertices))]
    for k in range(indices.shape[1]):
        matrices = skin_matrices[:, indices[:, k]]
        out += weights[None, :, k, None] * np.einsum('tvij,vj->tvi', matrices[..., :3, :], homogeneous)
    return out


def edge_stretch(rest, frames, edges):
    """p99 / p99.9 / max of max(l, 1/l) over all mesh edges (l = posed length / rest length) across frames."""
    reference = np.linalg.norm(rest[edges[:, 0]] - rest[edges[:, 1]], axis=1)
    valid = reference > 1e-9 * np.ptp(rest, 0).max()
    values = []
    for frame in frames:
        ratio = np.linalg.norm(frame[edges[valid, 0]] - frame[edges[valid, 1]], axis=1) / reference[valid]
        values.append(np.maximum(ratio, 1 / np.maximum(ratio, 1e-12)))
    stacked = np.concatenate(values)
    return {'p99': float(np.percentile(stacked, 99)), 'p999': float(np.percentile(stacked, 99.9)),
            'max': float(stacked.max())}


def probe_report(mesh, rig, skin: SkinWeights, config, *, influences=4):
    """Run the probe motion through LBS and return edge-stretch and displacement statistics."""
    integer(influences, 'influences', 1)
    times, rotations, translations = probe_motion(rig, config)
    matrices, _ = pose(rig, rotations, translations)
    indices, weights = skin.to_sparse(influences)
    posed = lbs(np.asarray(mesh.vertices, float), indices, weights, matrices)
    faces = np.asarray(mesh.faces)
    edges = np.unique(np.sort(np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]), axis=1), axis=0)
    scale = max(float(mesh.scale), np.finfo(float).eps)
    return {'frames': int(len(times)), 'edge_stretch': edge_stretch(np.asarray(mesh.vertices, float), posed, edges),
            'max_displacement_ratio': float(np.linalg.norm(posed - mesh.vertices[None], axis=-1).max() / scale),
            'finite': bool(np.isfinite(posed).all())}
