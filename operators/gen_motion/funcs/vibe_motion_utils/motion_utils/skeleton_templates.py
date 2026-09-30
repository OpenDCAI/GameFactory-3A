"""Explicit rest skeletons and animation data; no built-in skeletons or motion presets."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy.spatial.transform import Rotation

from ..bvh import _joint_names, validate_hierarchy


def quat_identity(*shape):
    q = np.zeros((*shape, 4), dtype=float)
    q[..., 0] = 1
    return q


def quat_normalize(q):
    q = np.asarray(q, dtype=float)
    norm = np.linalg.norm(q, axis=-1, keepdims=True)
    if q.shape[-1] != 4 or not np.isfinite(q).all() or np.any(norm == 0):
        raise ValueError('Quaternions must be finite nonzero wxyz vectors')
    return q / norm


def quat_to_matrix(q):
    q = quat_normalize(q)
    shape = q.shape[:-1]
    return Rotation.from_quat(q[..., [1, 2, 3, 0]].reshape(-1, 4)).as_matrix().reshape(*shape, 3, 3)


def matrix_to_quat(matrix):
    matrix = np.asarray(matrix, float)
    q = Rotation.from_matrix(matrix.reshape(-1, 3, 3)).as_quat()
    return q[:, [3, 0, 1, 2]].reshape(*matrix.shape[:-2], 4)


def quat_mul(a, b):
    a, b = np.broadcast_arrays(np.asarray(a, float), np.asarray(b, float))
    aw, av, bw, bv = a[..., :1], a[..., 1:], b[..., :1], b[..., 1:]
    return np.concatenate([aw * bw - np.sum(av * bv, axis=-1, keepdims=True),
                           aw * bv + bw * av + np.cross(av, bv)], axis=-1)


def quat_from_axis_angle(axis, angle):
    axis = np.asarray(axis, float)
    norm = np.linalg.norm(axis, axis=-1, keepdims=True)
    if np.any(norm == 0) or not np.isfinite(axis).all():
        raise ValueError('Rotation axis must be finite and nonzero')
    vector = axis / norm * np.asarray(angle)[..., None]
    q = Rotation.from_rotvec(vector.reshape(-1, 3)).as_quat()
    return q[:, [3, 0, 1, 2]].reshape(*vector.shape[:-1], 4)


def topological_order(parents):
    parents = np.asarray(parents)
    children = [[] for _ in parents]
    roots = np.flatnonzero(parents == -1)
    if len(roots) != 1 or np.any((parents < -1) | (parents >= len(parents))):
        raise ValueError('Skeleton needs one root and valid parent indices')
    for joint, parent in enumerate(parents):
        if parent >= 0:
            children[parent].append(joint)
    order = [int(roots[0])]
    for joint in order:
        order.extend(children[joint])
    if len(order) != len(parents):
        raise ValueError('Skeleton must be a connected acyclic tree')
    return np.asarray(order)


@dataclass(frozen=True)
class SkeletonTemplate:
    name: str
    joint_names: list[str]
    parents: np.ndarray
    rest: np.ndarray
    roles: dict[str, list[int]]

    def __post_init__(self):
        parents, rest = validate_hierarchy(self.parents, self.rest)
        object.__setattr__(self, 'parents', parents.copy())
        object.__setattr__(self, 'rest', rest.copy())
        object.__setattr__(self, 'joint_names', _joint_names(self, len(parents)))

    @property
    def num_joints(self):
        return len(self.parents)


@dataclass
class MotionClip:
    """Local wxyz rotations and root displacement in the supplied rest basis."""
    template: SkeletonTemplate
    quats: np.ndarray
    trans: np.ndarray
    fps: float

    @classmethod
    def rest_clip(cls, template, num_frames, *, fps):
        return cls(template, quat_identity(num_frames, template.num_joints),
                   np.zeros((num_frames, 3)), fps)

    @property
    def num_frames(self):
        return len(self.quats)

    @property
    def num_joints(self):
        return self.template.num_joints

    @property
    def duration(self):
        return (self.num_frames - 1) / self.fps

    def copy(self):
        return MotionClip(self.template, self.quats.copy(), self.trans.copy(), self.fps)

    def positions(self):
        from .units import fk
        return fk(self)[0]
