"""Forward kinematics and geometric bases."""
from __future__ import annotations

import numpy as np
from .skeleton_templates import (
    MotionClip, SkeletonTemplate, matrix_to_quat, quat_from_axis_angle,
    quat_identity, quat_mul, quat_normalize, quat_to_matrix, topological_order,
)


def root_index(template):
    return int(topological_order(template.parents)[0])


def unit(vector, *, epsilon):
    vector = np.asarray(vector, float)
    norm = np.linalg.norm(vector, axis=-1, keepdims=True)
    if not np.isfinite(vector).all() or np.any(norm <= epsilon):
        raise ValueError('A direction must be finite and nonzero at the configured tolerance')
    return vector / norm


def basis(direction, pole, *, epsilon):
    x, pole = np.broadcast_arrays(unit(direction, epsilon=epsilon), np.asarray(pole, float))
    y = pole - np.sum(pole * x, axis=-1, keepdims=True) * x
    fallback = np.eye(3)[np.argmin(np.abs(x), axis=-1)]
    fallback -= np.sum(fallback * x, axis=-1, keepdims=True) * x
    y = unit(np.where(np.linalg.norm(y, axis=-1, keepdims=True) <= epsilon, fallback, y),
             epsilon=epsilon)
    return np.stack([x, y, np.cross(x, y)], axis=-1)


def fk(clip):
    """Return world positions and wxyz rotations without changing local bone offsets."""
    template = clip.template
    n, count = clip.num_frames, template.num_joints
    if n < 1 or np.shape(clip.quats) != (n, count, 4) or np.shape(clip.trans) != (n, 3):
        raise ValueError('Clip needs nonempty (T,J,4) rotations and (T,3) root displacement')
    if not np.isfinite(clip.trans).all() or not np.isfinite(clip.fps) or clip.fps <= 0:
        raise ValueError('Clip translation and positive fps must be finite')
    local = quat_normalize(clip.quats)
    world_q = np.empty_like(local)
    positions = np.empty((n, count, 3))
    for j in topological_order(template.parents):
        parent = template.parents[j]
        if parent < 0:
            positions[:, j] = template.rest[j] + clip.trans
            world_q[:, j] = local[:, j]
        else:
            world_q[:, j] = quat_mul(world_q[:, parent], local[:, j])
            positions[:, j] = positions[:, parent] + np.einsum(
                'tij,j->ti', quat_to_matrix(world_q[:, parent]), template.rest[j] - template.rest[parent])
    return positions, quat_normalize(world_q)


def set_world_rotation(clip, joint, desired):
    parent = clip.template.parents[joint]
    if parent >= 0:
        _, world_q = fk(clip)
        desired = np.swapaxes(quat_to_matrix(world_q[:, parent]), -1, -2) @ desired
    clip.quats[:, joint] = matrix_to_quat(desired)
