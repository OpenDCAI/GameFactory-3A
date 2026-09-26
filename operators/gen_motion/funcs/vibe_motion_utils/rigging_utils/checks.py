"""Validate rig hierarchies and assess joint placement within mesh geometry."""
from __future__ import annotations
import numpy as np

def validate_tree(rig):
    'Validate that the rig is a single-rooted tree with finite joints and unique names.'
    n = len(rig.parents)
    if rig.joints.shape != (n, 3) or not np.isfinite(rig.joints).all():
        raise ValueError('joint positions are invalid')
    p = np.asarray(rig.parents)
    if not np.issubdtype(p.dtype, np.integer) or np.count_nonzero(p == -1) != 1 or np.any(p < -1) or np.any(p >= n):
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
        if not chain or any((i < 0 or i >= n for i in chain)):
            raise ValueError(f'{name}: chain indices are out of range')
    return True

def inside_mesh(mesh, points, rays=7):
    'Test containment by multi-direction ray voting, de-duplicating shared-edge hits. Diagnostic only on open meshes.'
    scale = mesh.scale
    v = (mesh.vertices - mesh.center) / scale
    points = (np.asarray(points) - mesh.center) / scale
    a, b, c = (v[mesh.faces[:, i]] for i in range(3))
    e1, e2 = (b - a, c - a)
    k = np.arange(rays) + 0.5
    z = 1 - 2 * k / rays
    theta = np.pi * (1 + np.sqrt(5)) * k
    directions = np.stack([np.sqrt(1 - z * z) * np.cos(theta), np.sqrt(1 - z * z) * np.sin(theta), z], axis=1)
    votes = np.zeros(len(points), int)
    for direction in directions:
        h = np.cross(direction, e2)
        det = np.sum(e1 * h, axis=1)
        active = np.abs(det) > 1e-12
        inv = np.divide(1.0, det, out=np.zeros_like(det), where=active)
        for j, point in enumerate(points):
            rel = point - a
            u = np.sum(rel * h, axis=1) * inv
            q = np.cross(rel, e1)
            w = np.einsum('ij,j->i', q, direction) * inv
            t = np.sum(e2 * q, axis=1) * inv
            hits = np.sort(t[active & (u >= -1e-10) & (w >= -1e-10) & (u + w <= 1 + 1e-10) & (t > 1e-10)])
            count = 0 if not len(hits) else 1 + np.count_nonzero(np.diff(hits) > 1e-08)
            votes[j] += count % 2
    return votes > rays // 2


def evaluate_rig(mesh, rig):
    'Report joint count, how much of the skeleton lies outside the mesh, and the shortest bone ratio.'
    validate_tree(rig)
    has = rig.parents >= 0
    parents = rig.joints[rig.parents[has]]
    children = rig.joints[has]
    u = np.linspace(0.05, 0.95, 11)
    samples = (parents[:, None] * (1 - u[None, :, None]) + children[:, None] * u[None, :, None]).reshape(-1, 3)
    inside = inside_mesh(mesh, np.concatenate([rig.joints, samples]))
    joint_inside, bone_inside = (inside[:rig.num_joints], inside[rig.num_joints:])
    lengths = np.linalg.norm(children - parents, axis=1) / mesh.scale
    return {'joints': rig.num_joints, 'outside_joint_ratio': float(1 - joint_inside.mean()), 'outside_joints': [rig.joint_names[i] for i in np.flatnonzero(~joint_inside)], 'outside_bone_sample_ratio': float(1 - bone_inside.mean()), 'min_bone_length_ratio': float(lengths.min()), 'notes': rig.notes}
