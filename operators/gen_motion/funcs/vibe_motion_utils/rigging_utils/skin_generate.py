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
            tied = np.flatnonzero(w[i] == cut[i, 0])
            slots = k - np.count_nonzero(w[i] > cut[i, 0])
            drop = tied[np.argsort(d[i, tied], kind='stable')[slots:]]
            w[i, drop] = 0.0
    total = w.sum(axis=1, keepdims=True)
    empty = np.flatnonzero(total[:, 0] <= 0.0)
    if len(empty):
        if np.any(~np.isfinite(d[empty]).any(axis=1)):
            raise ValueError('Empty vertex has no allowed fallback bone')
        w[empty] = 0.0
        w[empty, np.argmin(d[empty], axis=1)] = 1.0
        total = w.sum(axis=1, keepdims=True)
    return w / total, int(len(empty))


def anchored_weights(mesh, prior, ids, values, allowed, *, config):
    """Screened diffusion on real triangle edges; anchors and forbidden entries are exact."""
    from scipy.sparse import coo_matrix, diags
    from scipy.sparse.linalg import spsolve
    p, n = config, mesh.num_vertices
    prior, allowed = np.asarray(prior, float), np.asarray(allowed)
    if prior.ndim != 2 or prior.shape[0] != n or not prior.shape[1] or not np.isfinite(prior).all() or np.any(prior < 0):
        raise ValueError('Prior must be a finite nonnegative (V,J) array')
    if not np.allclose(prior.sum(1), 1, atol=p['sum_tolerance'], rtol=0):
        raise ValueError('Prior rows must be normalized')
    if allowed.shape != prior.shape or allowed.dtype.kind != 'b' or np.any(~allowed.any(1)):
        raise ValueError('Each vertex needs at least one allowed influence')
    ids, values = np.asarray(ids), np.asarray(values, float)
    if ids.shape == (0,) and values.size == 0:
        ids, values = np.empty(0, int), np.empty((0, prior.shape[1]))
    if ids.ndim != 1 or ids.dtype.kind not in 'iu' or len(set(ids)) != len(ids) or np.any((ids < 0) | (ids >= n)):
        raise ValueError('Anchor IDs must be unique valid vertex indices')
    if values.shape != (len(ids), prior.shape[1]) or not np.isfinite(values).all() or np.any(values < 0):
        raise ValueError('Invalid anchor weight array')
    if not np.allclose(values.sum(1), 1, atol=p['sum_tolerance'], rtol=0) or np.any((values > 0).sum(1) > p['max_influences']):
        raise ValueError('Anchors must be normalized and obey the influence count; they are never silently pruned')
    if np.any(values[~allowed[ids]] != 0):
        raise ValueError('Anchor conflicts with forbidden influences')
    edges = np.unique(np.sort(mesh.faces[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1), axis=0)
    edges = edges[edges[:, 0] != edges[:, 1]]
    length = np.linalg.norm(mesh.vertices[edges[:, 0]] - mesh.vertices[edges[:, 1]], axis=1) / mesh.scale
    conductance = 1 / np.maximum(length, p['edge_epsilon_ratio'])
    a, b = edges.T
    graph = coo_matrix((np.tile(conductance, 2), (np.r_[a, b], np.r_[b, a])), shape=(n, n)).tocsr()
    degree = np.asarray(graph.sum(1)).ravel()
    screen = p['screening'] * np.maximum(degree, 1)
    matrix = (diags(degree + screen) - graph).tocsr()
    fixed, output = np.zeros(n, bool), np.zeros_like(prior)
    fixed[ids], output[ids] = True, values
    for j in range(prior.shape[1]):
        free = np.flatnonzero(~fixed & allowed[:, j])
        if len(free):
            rhs = screen[free] * prior[free, j] - matrix[free][:, ids] @ values[:, j]
            output[free, j] = spsolve(matrix[free][:, free], rhs)
    if not np.isfinite(output).all() or np.any(output < -p['sum_tolerance']):
        raise ValueError('Invalid screened diffusion solution')
    output = np.where(allowed, np.maximum(output, 0), 0)
    if np.any(output.sum(1) <= 0):
        raise ValueError('Allowed region has no positive prior or anchor support')
    output, _ = prune_to_k(output, np.where(allowed, -output, np.inf), max_influences=p['max_influences'])
    output[ids] = values
    return output


def skin_mesh(mesh: CreatureMesh, rig: RigResult, *, config: dict,
              allowed_bones=None, weight_bias=None, anchors=None) -> SkinWeights:
    """One constrained prior/diffusion path; no unrestricted post-smoothing fallback."""
    p = resolve_skin_config(config)
    dist = bone_distances(mesh, rig, convention=p['bone_convention'])
    allowed = np.ones(dist.shape, bool) if allowed_bones is None else np.asarray(allowed_bones)
    bias = np.ones_like(dist) if weight_bias is None else np.asarray(weight_bias, float)
    if allowed.shape != dist.shape or allowed.dtype.kind != 'b':
        raise ValueError('allowed_bones must be a boolean (V,J) array')
    if bias.shape != dist.shape or not np.isfinite(bias).all() or np.any(bias < 0):
        raise ValueError('weight_bias must be a finite nonnegative (V,J) array')
    allowed = allowed & (bias > 0)
    if np.any(~allowed.any(1)):
        raise ValueError('Each vertex needs a positive allowed influence')
    raw = distance_weights(dist, kernel=p['kernel'], falloff=p['falloff'], radius=p['radius_scale'] * mesh.scale,
                           floor=0, distance_epsilon_ratio=p['distance_epsilon_ratio'])
    raw = np.where(allowed, raw * bias, 0)
    raw = np.where(raw >= p['floor'] * raw.max(1, keepdims=True), raw, 0)
    distance = np.where(allowed, dist, np.inf)
    weights, fallback = prune_to_k(raw, distance, max_influences=p['max_influences'])
    adjacency = mesh.adjacency()
    for _ in range(p['smooth_iterations']):
        nxt = weights.copy()
        for i, neighbors in enumerate(adjacency):
            if len(neighbors):
                nxt[i] = (1 - p['smooth_rate']) * weights[i] + p['smooth_rate'] * weights[neighbors].mean(0)
        weights, _ = prune_to_k(np.where(allowed, nxt, 0), distance, max_influences=p['max_influences'])
    if anchors is None:
        ids, values = np.empty(0, int), np.empty((0, rig.num_joints))
    else:
        from .templates import require_config
        require_config(anchors, ('ids', 'values'), 'anchors')
        ids, values = anchors['ids'], anchors['values']
    weights = anchored_weights(mesh, weights, ids, values, allowed, config=p)
    if np.any(weights[~allowed] != 0):
        raise ValueError('Forbidden influence escaped constrained diffusion')
    notes = [f'bone_convention={p["bone_convention"]}; topology diffusion with {len(ids)} exact anchors']
    if fallback:
        notes.append(f'{fallback} vertices bound to the nearest bone within the allowed set')
    skin = SkinWeights(weights, list(rig.joint_names), tuple(notes))
    issues = validate_weights(skin, max_influences=p['max_influences'], sum_tolerance=p['sum_tolerance'])
    if issues:
        raise ValueError('; '.join(issues))
    return skin
