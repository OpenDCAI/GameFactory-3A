"""Generate skinning weights from bone distances with influence pruning and mesh smoothing."""
from __future__ import annotations
from typing import Any
import numpy as np
from .types import RigResult
from .mesh import CreatureMesh, point_to_segment_distance
from .skin_templates import resolve_skin_preset
from .skin_units import SkinWeights, validate_weights

def bone_distances(
    mesh: CreatureMesh, rig: RigResult, *, convention: str = "incoming",
) -> np.ndarray:
    """Per-vertex distances (V,J); incoming retains the historical helper API.

    Outgoing assigns each joint its child segments (leaves become points), matching
    the rotation pivots used by FK, LBS and glTF. It generates new distance weights;
    it must not be used to remap existing artist-authored weights.
    """
    if convention not in ("incoming", "outgoing"):
        raise ValueError("bone convention must be incoming or outgoing")
    joints = np.asarray(rig.joints, float)
    parents = np.asarray(rig.parents, np.int64)
    out = np.empty((mesh.num_vertices, len(joints)))
    for j in range(len(joints)):
        if convention == "incoming":
            p = int(parents[j])
            head = joints[p] if p >= 0 else joints[j]
            out[:, j] = point_to_segment_distance(mesh.vertices, head, joints[j])
        else:
            children = np.flatnonzero(parents == j)
            tips = children if len(children) else [j]
            out[:, j] = np.minimum.reduce([
                point_to_segment_distance(mesh.vertices, joints[j], joints[c])
                for c in tips
            ])
    return out

def distance_weights(distances: np.ndarray, *, kernel: str='inverse', falloff: float=4.0, radius: float=np.inf, floor: float=0.0) -> np.ndarray:
    'Turn distances into unnormalised weights ``(V,J)``.'
    d = np.asarray(distances, float)
    if falloff <= 0:
        raise ValueError(f'falloff must be positive, got {falloff}')
    scale = float(d.max(initial=1.0))
    eps = 1e-06 * max(scale, 1e-12)
    if kernel == 'inverse':
        w = 1.0 / np.power(d + eps, float(falloff))
    elif kernel == 'gaussian':
        sigma = max(float(radius) / max(float(falloff), 1e-06), 1e-12)
        w = np.exp(-np.square(d / sigma))
    elif kernel == 'linear':
        w = np.maximum(0.0, 1.0 - d / max(float(radius), 1e-12))
    else:
        raise ValueError(f'unknown kernel {kernel!r}, available inverse/gaussian/linear')
    w = np.where(d <= float(radius), w, 0.0)
    if floor > 0.0:
        peak = w.max(axis=1, keepdims=True)
        w = np.where(w >= float(floor) * peak, w, 0.0)
    return w

def prune_to_k(weights: np.ndarray, distances: np.ndarray, *, max_influences: int=4) -> tuple[np.ndarray, int]:
    'Keep the strongest ``max_influences`` bones per vertex and renormalise; returns ``(weights, fallback vertex count)``.'
    w = np.asarray(weights, float).copy()
    k = max(int(max_influences), 1)
    if w.shape[1] > k:
        cut = np.partition(w, -k, axis=1)[:, -k][:, None]
        w = np.where(w >= cut, w, 0.0)
        extra = (w > 0).sum(axis=1) - k
        for i in np.flatnonzero(extra > 0):
            live = np.flatnonzero(w[i] > 0)
            drop = live[np.argsort(distances[i, live])[k:]]
            w[i, drop] = 0.0
    total = w.sum(axis=1, keepdims=True)
    empty = np.flatnonzero(total[:, 0] <= 0.0)
    if len(empty):
        w[empty] = 0.0
        w[empty, np.argmin(np.asarray(distances, float)[empty], axis=1)] = 1.0
        total = w.sum(axis=1, keepdims=True)
    return (w / np.maximum(total, 1e-12), int(len(empty)))

def smooth_weights(weights: np.ndarray, adjacency: list[np.ndarray], *, iterations: int=6, rate: float=0.5, max_influences: int=4, distances: np.ndarray | None=None) -> np.ndarray:
    'Smooth weights over the mesh graph to remove hard creases at the joints.'
    w = np.asarray(weights, float).copy()
    if iterations <= 0 or rate <= 0.0:
        return w
    for _ in range(int(iterations)):
        nxt = w.copy()
        for i, nb in enumerate(adjacency):
            if len(nb):
                nxt[i] = (1.0 - rate) * w[i] + rate * w[nb].mean(axis=0)
        w = nxt
    dist = np.asarray(distances, float) if distances is not None else -w
    w, _ = prune_to_k(w, dist, max_influences=max_influences)
    return w

def skin_mesh(
    mesh: CreatureMesh, rig: RigResult, preset: str,
    *, bone_convention: str = "outgoing", **overrides: Any,
) -> SkinWeights:
    """Generate pivot-correct weights; incoming explicitly reproduces old weights."""
    spec = resolve_skin_preset(preset, **overrides)
    p = spec['params']
    radius = float(p['radius_scale']) * mesh.scale
    dist = bone_distances(mesh, rig, convention=bone_convention)
    raw = distance_weights(dist, kernel=p['kernel'], falloff=p['falloff'], radius=radius, floor=p['floor'])
    weights, fallback = prune_to_k(raw, dist, max_influences=p['max_influences'])
    notes: list[str] = [f'bone_convention={bone_convention}']
    if fallback:
        notes.append(f'{fallback}/{mesh.num_vertices} vertices fell outside every bone radius and were bound to the nearest bone; consider raising radius_scale')
    if p['smooth_iterations'] > 0:
        weights = smooth_weights(weights, mesh.adjacency(), iterations=p['smooth_iterations'], rate=p['smooth_rate'], max_influences=p['max_influences'], distances=dist)
    skin = SkinWeights(weights, list(rig.joint_names), tuple(notes))
    issues = validate_weights(skin, max_influences=p['max_influences'])
    if issues:
        raise ValueError('skin weights violate the hard constraints: ' + '；'.join(issues))
    return skin
