from __future__ import annotations
from dataclasses import dataclass
import numpy as np
MAX_GLTF_INFLUENCES = 4

@dataclass(frozen=True)
class SkinWeights:
    'Per-vertex bone binding weights.'
    weights: np.ndarray
    joint_names: list[str]
    notes: tuple[str, ...] = ()

    @property
    def num_vertices(self) -> int:
        return int(self.weights.shape[0])

    @property
    def num_joints(self) -> int:
        return int(self.weights.shape[1])

    @property
    def influences(self) -> np.ndarray:
        '``(V,)`` number of non-zero weights per vertex.'
        return (self.weights > 0.0).sum(axis=1)

    def to_sparse(self, count: int=MAX_GLTF_INFLUENCES) -> tuple[np.ndarray, np.ndarray]:
        'Convert to glTF-style ``(JOINTS_0, WEIGHTS_0)``: ``(V,count)`` indices plus weights.'
        if int(self.influences.max(initial=0)) > count:
            raise ValueError(f'a vertex is influenced by {int(self.influences.max())} bones, above {count}; prune with prune_to_k rather than dropping weights silently on export')
        order = np.argsort(-self.weights, axis=1)[:, :count]
        rows = np.arange(self.num_vertices)[:, None]
        return (order.astype(np.int64), self.weights[rows, order])

def axis_angle_matrix(axis: np.ndarray, degrees: float) -> np.ndarray:
    '3x3 rotation matrix about an arbitrary axis (Rodrigues).'
    a = np.asarray(axis, float)
    norm = float(np.linalg.norm(a))
    if norm < 1e-12:
        raise ValueError('the rotation axis cannot be a zero vector')
    k = a / norm
    t = np.deg2rad(float(degrees))
    kx = np.array([[0.0, -k[2], k[1]], [k[2], 0.0, -k[0]], [-k[1], k[0], 0.0]])
    return np.eye(3) + np.sin(t) * kx + (1.0 - np.cos(t)) * (kx @ kx)

def pose_matrices(joints: np.ndarray, parents: np.ndarray, rotations: dict[int, np.ndarray] | None=None, translation: np.ndarray | None=None) -> np.ndarray:
    'Skinning matrices ``(J,4,4)`` from rest positions plus per-joint local rotations. The rest transform is treated as pure translation, so the rest pose yields identity matrices.'
    joints = np.asarray(joints, float)
    parents = np.asarray(parents, np.int64)
    count = len(joints)
    if parents.shape != (count,) or np.any((parents < -1) | (parents >= count)):
        raise ValueError('parent index is out of range')
    order, seen = ([], np.zeros(count, bool))
    for i in range(count):
        stack, j = ([], i)
        while j >= 0 and (not seen[j]):
            if j in stack:
                raise ValueError('skeleton contains a cycle')
            stack.append(j)
            j = int(parents[j])
        for j in reversed(stack):
            seen[j] = True
            order.append(j)
    if len(order) != count:
        raise ValueError('skeleton contains a cycle or unreachable joints')
    rot = rotations or {}
    world = np.tile(np.eye(4), (count, 1, 1))
    for j in order:
        local = np.eye(4)
        local[:3, :3] = np.asarray(rot.get(j, np.eye(3)), float)
        p = int(parents[j])
        offset = joints[j] - (joints[p] if p >= 0 else np.zeros(3))
        move = np.eye(4)
        move[:3, 3] = offset
        node = move @ local
        world[j] = node if p < 0 else world[p] @ node
    if translation is not None:
        shift = np.eye(4)
        shift[:3, 3] = np.asarray(translation, float)
        world = shift[None] @ world
    inv_rest = np.tile(np.eye(4), (count, 1, 1))
    inv_rest[:, :3, 3] = -joints
    return world @ inv_rest

def deform(vertices: np.ndarray, weights: np.ndarray, matrices: np.ndarray) -> np.ndarray:
    "Linear blend skinning: ``v' = sum_j w_j * M_j * v``."
    v = np.asarray(vertices, float)
    w = np.asarray(weights, float)
    m = np.asarray(matrices, float)
    if w.shape[1] != len(m) or w.shape[0] != len(v):
        raise ValueError(f'weights {w.shape} do not match vertices {len(v)} / matrices {len(m)}')
    homo = np.concatenate([v, np.ones((len(v), 1))], axis=1)
    moved = np.einsum('jab,ib->ija', m, homo)
    return np.einsum('ij,ija->ia', w, moved)[:, :3]

def mesh_volume(vertices: np.ndarray, faces: np.ndarray) -> float:
    'Return the absolute signed volume of a triangular mesh.'
    v = np.asarray(vertices, float)
    f = np.asarray(faces, np.int64)
    a, b, c = (v[f[:, 0]], v[f[:, 1]], v[f[:, 2]])
    return float(np.abs(np.einsum('ij,ij->i', a, np.cross(b, c)).sum()) / 6.0)

def validate_weights(skin: SkinWeights, *, max_influences: int=MAX_GLTF_INFLUENCES, sum_tolerance: float=1e-09) -> list[str]:
    'Check every hard constraint and return the violations; an empty list means all passed.'
    issues: list[str] = []
    w = skin.weights
    if not np.isfinite(w).all():
        issues.append('weights contain NaN/Inf')
    if float(w.min(initial=0.0)) < -sum_tolerance:
        issues.append(f'negative weights present, minimum {float(w.min()):.3e}')
    err = float(np.abs(w.sum(axis=1) - 1.0).max(initial=0.0))
    if err > sum_tolerance:
        issues.append(f'per-vertex weight sums deviate from 1, largest error {err:.3e} (tolerance {sum_tolerance:.0e})')
    worst = int(skin.influences.max(initial=0))
    if worst > max_influences:
        issues.append(f'maximum influence count {worst} exceeds {max_influences}')
    return issues
