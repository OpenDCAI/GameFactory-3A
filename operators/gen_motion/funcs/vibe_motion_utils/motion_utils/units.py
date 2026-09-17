from __future__ import annotations
import numpy as np
from .skeleton_templates import (
    MotionClip, SkeletonPlan, SkeletonTemplate, build_template, plan_skeleton,
    quat_from_axis_angle, quat_mul, quat_normalize, quat_slerp, quat_to_matrix,
    template_from_skeleton,
)

def unit(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float64)
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)

def inverse(q: np.ndarray) -> np.ndarray:
    return quat_normalize(q) * np.array([1.0, -1.0, -1.0, -1.0])

def root_index(template: SkeletonTemplate) -> int:
    roots = np.flatnonzero(np.asarray(template.parents) == -1)
    if len(roots) != 1:
        raise ValueError('exactly one root joint is required')
    return int(roots[0])

def between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Shortest-arc rotation between two directions. Opposite vectors pick a deterministic orthogonal axis; zero vectors are rejected."""
    a, b = np.broadcast_arrays(np.asarray(a, float), np.asarray(b, float))
    if a.shape[-1] != 3 or not np.isfinite([a, b]).all():
        raise ValueError('rotation directions must be finite (...,3) vectors')
    if np.any(np.linalg.norm(a, axis=-1) < 1e-10) or np.any(np.linalg.norm(b, axis=-1) < 1e-10):
        raise ValueError('a zero vector cannot define a rotation')
    a, b = (unit(a), unit(b))
    dot = np.sum(a * b, axis=-1, keepdims=True)
    q = np.concatenate([1.0 + dot, np.cross(a, b)], axis=-1)
    basis = np.eye(3)[np.argmin(np.abs(a), axis=-1)]
    opposite = np.concatenate([np.zeros_like(dot), unit(np.cross(a, basis))], axis=-1)
    return quat_normalize(np.where(dot < -1.0 + 1e-07, opposite, q))

def fk(clip: MotionClip) -> tuple[np.ndarray, np.ndarray]:
    """Return joint positions and global quaternions from a single topological pass, computing each joint across all frames at once."""
    parents = np.asarray(clip.template.parents, dtype=np.int64)
    rest = np.asarray(clip.template.rest, dtype=np.float64)
    q = np.asarray(clip.quats)
    count = len(parents)
    if q.shape != (clip.num_frames, count, 4) or rest.shape != (count, 3):
        raise ValueError('pose and rest skeleton sizes disagree')
    if clip.num_frames < 1 or clip.trans.shape != (clip.num_frames, 3):
        raise ValueError('the clip must be non-empty with translation shaped (T,3)')
    if not all((np.isfinite(x).all() for x in (q, rest, clip.trans))):
        raise ValueError('pose contains NaN/Inf')
    if not np.isfinite(clip.fps) or clip.fps <= 0:
        raise ValueError('fps must be finite and positive')
    if np.any((parents < -1) | (parents >= count)):
        raise ValueError('parent joint index is out of range')
    root = root_index(clip.template)
    children = [[] for _ in parents]
    for j, p in enumerate(parents):
        if p >= 0:
            children[p].append(j)
    order = [root]
    for j in order:
        order.extend(children[j])
    if len(order) != count:
        raise ValueError('skeleton contains a cycle or unreachable joints')
    local = quat_normalize(q)
    global_q = np.zeros_like(local)
    pos = np.empty((clip.num_frames, count, 3), dtype=np.float64)
    for j in order:
        p = int(parents[j])
        if p < 0:
            global_q[:, j] = local[:, j]
            pos[:, j] = rest[j] + clip.trans
        else:
            global_q[:, j] = quat_mul(global_q[:, p], local[:, j])
            pos[:, j] = pos[:, p] + np.einsum('tij,j->ti', quat_to_matrix(global_q[:, p]), rest[j] - rest[p])
    return (pos, global_q)
