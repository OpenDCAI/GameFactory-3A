"""Generate skin weights from explicitly configured distance kernels and smoothing."""
from __future__ import annotations
import numpy as np
from .types import RigResult
from .mesh import CreatureMesh, point_to_segment_distance
from .skin_templates import resolve_skin_config
from .templates import finite_number, integer
from .skin_units import SkinWeights, validate_weights
from .checks import validate_tree


def bone_distances(mesh: CreatureMesh, rig: RigResult, *, convention: str) -> np.ndarray:
    """Measure distance to incoming or outgoing joint segments as requested."""
    if convention not in ('incoming', 'outgoing'):
        raise ValueError('bone convention must be incoming or outgoing')
    validate_tree(rig)
    joints = np.asarray(rig.joints, float)
    parents = np.asarray(rig.parents, np.int64)
    out = np.empty((mesh.num_vertices, len(joints)))
    for j in range(len(joints)):
        if convention == 'incoming':
            p = int(parents[j])
            head = joints[p] if p >= 0 else joints[j]
            out[:, j] = point_to_segment_distance(mesh.vertices, head, joints[j])
        else:
            children = np.flatnonzero(parents == j)
            tips = children if len(children) else [j]
            out[:, j] = np.minimum.reduce([
                point_to_segment_distance(mesh.vertices, joints[j], joints[c]) for c in tips
            ])
    return out


def distance_weights(distances: np.ndarray, *, kernel: str, falloff: float,
                     radius: float, floor: float, distance_epsilon_ratio: float) -> np.ndarray:
    """Turn distances into relative weights without implicit kernel policy."""
    d = np.asarray(distances, float)
    if d.ndim != 2 or not d.shape[1] or not np.isfinite(d).all() or np.any(d < 0):
        raise ValueError('distances must be finite non-negative (V,J) with at least one joint')
    for name, value in (('falloff', falloff), ('radius', radius), ('distance_epsilon_ratio', distance_epsilon_ratio)):
        finite_number(value, name, positive=True)
    finite_number(floor, 'floor')
    if floor > 1:
        raise ValueError('floor must lie in [0,1]')
    eps = distance_epsilon_ratio * max(float(d.max(initial=0.0)), np.finfo(float).eps)
    if kernel == 'inverse':
        log_weights = -float(falloff) * np.log(d + eps)
        log_weights -= log_weights.max(axis=1, keepdims=True)
        w = np.exp(log_weights)
    elif kernel == 'gaussian':
        sigma = max(float(radius) / float(falloff), np.finfo(float).tiny)
        w = np.exp(-np.square(d / sigma))
    elif kernel == 'linear':
        w = np.maximum(0.0, 1.0 - d / float(radius))
    else:
        raise ValueError(f'unknown kernel {kernel!r}, available inverse/gaussian/linear')
    w = np.where(d <= float(radius), w, 0.0)
    if floor > 0.0:
        peak = w.max(axis=1, keepdims=True)
        w = np.where(w >= float(floor) * peak, w, 0.0)
    return w


def prune_to_k(weights: np.ndarray, distances: np.ndarray, *, max_influences: int) -> tuple[np.ndarray, int]:
    """Keep the requested influence count; empty rows bind to the nearest joint."""
    w = np.asarray(weights, float).copy()
    d = np.asarray(distances, float)
    k = integer(max_influences, 'max_influences', 1)
    if w.ndim != 2 or not w.shape[1] or d.shape != w.shape or not np.isfinite(w).all() or np.any(w < 0):
        raise ValueError('weights and distances must have matching (V,J) shapes and weights must be finite non-negative')
    if w.shape[1] > k:
        cut = np.partition(w, -k, axis=1)[:, -k][:, None]
        w = np.where(w >= cut, w, 0.0)
        extra = (w > 0).sum(axis=1) - k
        for i in np.flatnonzero(extra > 0):
            live = np.flatnonzero(w[i] > 0)
            drop = live[np.argsort(d[i, live], kind='stable')[k:]]
            w[i, drop] = 0.0
    total = w.sum(axis=1, keepdims=True)
    empty = np.flatnonzero(total[:, 0] <= 0.0)
    if len(empty):
        w[empty] = 0.0
        w[empty, np.argmin(d[empty], axis=1)] = 1.0
        total = w.sum(axis=1, keepdims=True)
    return w / total, int(len(empty))


def smooth_weights(weights: np.ndarray, adjacency: list[np.ndarray], *, iterations: int,
                   rate: float, max_influences: int, distances: np.ndarray) -> np.ndarray:
    w = np.asarray(weights, float).copy()
    integer(iterations, 'iterations', 0)
    finite_number(rate, 'rate')
    if rate > 1:
        raise ValueError('rate must lie in [0,1]')
    if len(adjacency) != len(w):
        raise ValueError('adjacency must match the weight vertex count')
    for _ in range(iterations):
        nxt = w.copy()
        for i, nb in enumerate(adjacency):
            if len(nb):
                nxt[i] = (1.0 - rate) * w[i] + rate * w[nb].mean(axis=0)
        w = nxt
    w, _ = prune_to_k(w, distances, max_influences=max_influences)
    return w


def skin_mesh(mesh: CreatureMesh, rig: RigResult, *, config: dict) -> SkinWeights:
    p = resolve_skin_config(config)
    radius = float(p['radius_scale']) * mesh.scale
    dist = bone_distances(mesh, rig, convention=p['bone_convention'])
    raw = distance_weights(dist, kernel=p['kernel'], falloff=p['falloff'], radius=radius,
                           floor=p['floor'], distance_epsilon_ratio=p['distance_epsilon_ratio'])
    weights, fallback = prune_to_k(raw, dist, max_influences=p['max_influences'])
    notes = [f'bone_convention={p["bone_convention"]}']
    if fallback:
        notes.append(f'{fallback}/{mesh.num_vertices} vertices were outside every bone radius and bound to the nearest bone')
    if p['smooth_iterations'] > 0:
        weights = smooth_weights(weights, mesh.adjacency(), iterations=p['smooth_iterations'],
                                 rate=p['smooth_rate'], max_influences=p['max_influences'], distances=dist)
    skin = SkinWeights(weights, list(rig.joint_names), tuple(notes))
    issues = validate_weights(skin, max_influences=p['max_influences'], sum_tolerance=p['sum_tolerance'])
    if issues:
        raise ValueError('skin weights violate the constraints: ' + '; '.join(issues))
    return skin
