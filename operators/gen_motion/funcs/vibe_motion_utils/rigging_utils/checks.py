"""Validate rig hierarchies and evaluate mesh placement with explicit policy."""
from __future__ import annotations
import numpy as np
from .templates import require_config, finite_number, integer


def validate_tree(rig):
    n = len(rig.parents)
    if rig.joints.shape != (n, 3) or not np.isfinite(rig.joints).all():
        raise ValueError('joint positions are invalid')
    p = np.asarray(rig.parents)
    if p.shape != (n,) or not np.issubdtype(p.dtype, np.integer) or np.count_nonzero(p == -1) != 1 or np.any(p < -1) or np.any(p >= n):
        raise ValueError('parents must form a single-rooted tree of valid indices')
    for j in range(n):
        seen = set()
        while j != -1:
            if j in seen:
                raise ValueError('parent hierarchy contains a cycle')
            seen.add(j)
            j = int(p[j])
    if len(rig.joint_names) != n or len(set(rig.joint_names)) != n:
        raise ValueError('joint names are missing or duplicated')
    for name, chain in rig.chains.items():
        if not chain or any(i < 0 or i >= n for i in chain):
            raise ValueError(f'{name}: chain indices are out of range')
    return True


def inside_mesh(mesh, points, *, config):
    """Test containment with explicitly sampled multi-direction ray voting."""
    rays = integer(config['rays'], 'rays', 1)
    scale = mesh.scale
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError('mesh scale must be positive and finite')
    v = (mesh.vertices - mesh.center) / scale
    points = (np.asarray(points) - mesh.center) / scale
    a, b, c = (v[mesh.faces[:, i]] for i in range(3))
    e1, e2 = b - a, c - a
    k = np.arange(rays) + 0.5
    z = 1 - 2 * k / rays
    theta = np.pi * (1 + np.sqrt(5)) * k
    directions = np.stack([np.sqrt(1 - z * z) * np.cos(theta), np.sqrt(1 - z * z) * np.sin(theta), z], axis=1)
    votes = np.zeros(len(points), int)
    edge_tol = config['ray_edge_tolerance']
    for direction in directions:
        h = np.cross(direction, e2)
        det = np.sum(e1 * h, axis=1)
        active = np.abs(det) > config['ray_det_tolerance']
        inv = np.divide(1.0, det, out=np.zeros_like(det), where=active)
        for j, point in enumerate(points):
            rel = point - a
            u = np.sum(rel * h, axis=1) * inv
            q = np.cross(rel, e1)
            w = np.einsum('ij,j->i', q, direction) * inv
            t = np.sum(e2 * q, axis=1) * inv
            hits = np.sort(t[active & (u >= -edge_tol) & (w >= -edge_tol) & (u + w <= 1 + edge_tol) & (t > config['ray_hit_tolerance'])])
            count = 0 if not len(hits) else 1 + np.count_nonzero(np.diff(hits) > config['ray_merge_tolerance'])
            votes[j] += count % 2
    return votes > rays // 2


def evaluate_rig(mesh, rig, *, config):
    fields = ('rays', 'ray_det_tolerance', 'ray_edge_tolerance', 'ray_hit_tolerance',
              'ray_merge_tolerance', 'bone_sample_span', 'bone_samples',
              'max_outside_joint_ratio', 'max_outside_bone_ratio', 'min_bone_length_ratio')
    p = require_config(config, fields, 'rig evaluation')
    integer(p['rays'], 'rays', 1)
    integer(p['bone_samples'], 'bone_samples', 1)
    for name in fields:
        if name not in ('rays', 'bone_sample_span', 'bone_samples'):
            finite_number(p[name], name)
    for name in ('max_outside_joint_ratio', 'max_outside_bone_ratio'):
        if p[name] > 1:
            raise ValueError(f'{name} must lie in [0,1]')
    span = np.asarray(p['bone_sample_span'], float)
    if span.shape != (2,) or not np.isfinite(span).all() or not 0 <= span[0] <= span[1] <= 1:
        raise ValueError('bone_sample_span must satisfy 0 <= lo <= hi <= 1')
    validate_tree(rig)
    has = rig.parents >= 0
    parents = rig.joints[rig.parents[has]]
    children = rig.joints[has]
    sample_count = int(p['bone_samples'])
    u = np.linspace(float(span[0]), float(span[1]), sample_count)
    samples = (parents[:, None] * (1 - u[None, :, None]) + children[:, None] * u[None, :, None]).reshape(-1, 3)
    inside = inside_mesh(mesh, np.concatenate([rig.joints, samples]), config=p)
    joint_inside, bone_inside = inside[:rig.num_joints], inside[rig.num_joints:]
    lengths = np.linalg.norm(children - parents, axis=1) / mesh.scale
    joint_ratio = float(1 - joint_inside.mean())
    bone_ratio = float(1 - bone_inside.mean()) if len(bone_inside) else 0.0
    minimum = float(lengths.min()) if len(lengths) else 0.0
    return {'joints': rig.num_joints, 'outside_joint_ratio': joint_ratio,
            'outside_joints': [rig.joint_names[i] for i in np.flatnonzero(~joint_inside)],
            'outside_bone_sample_ratio': bone_ratio, 'min_bone_length_ratio': minimum,
            'ok': bool(joint_ratio <= p['max_outside_joint_ratio'] and bone_ratio <= p['max_outside_bone_ratio']
                       and minimum >= p['min_bone_length_ratio']), 'notes': rig.notes}
