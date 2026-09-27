"""Fit spine and limb chains with explicitly configured sampling and scoring."""
from __future__ import annotations
import numpy as np
from .types import RigResult
from .templates import resolve_rig_config
from .sections import canonical_mesh


def _lift(points, axis, offset):
    result = np.empty((len(points), 3))
    result[:, axis] = offset
    result[:, [i for i in range(3) if i != axis]] = points
    return result


def _candidates(sections, axis, offset, p, notes, label):
    section = sections.section(axis, offset)
    if section.open_components:
        notes.add(f'{label}: open cross-section detected; non-closed parts ignored')
    points, radius = section.candidates(p['section_resolution'], keep=p['candidate_keep'],
                                        bounds=None, chunk_size=p['clearance_chunk_size'])
    if not len(points):
        raise ValueError(f'{label}: no closed interior cross-section at {offset}; adjust the span or repair the mesh')
    return _lift(points, axis, offset), radius


def simplify_chain(points, count, *, config):
    n = len(points)
    error = np.full((n, n), np.inf)
    min_gap = max(config['simplify_min_gap'], int(config['simplify_gap_ratio'] * (n - 1) / (count - 1)))
    ideal = (n - 1) / (count - 1)
    length = max(np.linalg.norm(points[-1] - points[0]), np.finfo(float).eps)
    for i in range(n - 1):
        for j in range(i + min_gap, n):
            edge = points[j] - points[i]
            delta = points[i:j + 1] - points[i]
            t = np.clip(np.einsum('ij,j->i', delta, edge) / max(np.dot(edge, edge), np.finfo(float).eps), 0.0, 1.0)
            error[i, j] = np.sum((delta - t[:, None] * edge) ** 2) / length ** 2 + config['simplify_length_weight'] * ((j - i) / ideal - 1) ** 2
    cost = np.full((count, n), np.inf)
    trace = np.full((count, n), -1, int)
    cost[0, 0] = 0.0
    for k in range(1, count):
        for j in range(k * min_gap, n):
            value = cost[k - 1, :j] + error[:j, j]
            i = int(np.argmin(value))
            cost[k, j], trace[k, j] = value[i], i
    if not np.isfinite(cost[-1, -1]):
        raise ValueError('not enough centreline samples for the requested joint count')
    idx = [n - 1]
    for k in range(count - 1, 0, -1):
        idx.append(int(trace[k, idx[-1]]))
    return points[idx[::-1]]


def _spine(sections, p, notes):
    axis = 1 if p['spine_axis'] == 'vertical' else 2
    vertices = sections.vertices
    lo, hi = vertices[:, axis].min(), vertices[:, axis].max()
    fractions = np.linspace(*p['spine_span'], p['spine_joints'])
    sets = []
    for fraction in fractions:
        points, radius = _candidates(sections, axis, lo + fraction * (hi - lo), p, notes, 'spine')
        score = radius - p['spine_center_weight'] * np.abs(points[:, 0])
        sets.append((points, score))
    cost, trace = -sets[0][1], []
    for (prev, _), (cur, score) in zip(sets, sets[1:]):
        distance = np.sum((cur[:, None] - prev[None, :]) ** 2, axis=-1)
        total = cost[None, :] + p['spine_continuity_weight'] * distance
        best = total.argmin(axis=1)
        cost = total[np.arange(len(cur)), best] - score
        trace.append(best)
    path = [int(cost.argmin())]
    for best in reversed(trace):
        path.append(int(best[path[-1]]))
    return np.stack([item[0][index] for item, index in zip(sets, path[::-1])])


def _limb(sections, spine, group, side, p, notes):
    position = group.at * (len(spine) - 1)
    i = int(position)
    attach = spine[i] + (position - i) * (spine[min(i + 1, len(spine) - 1)] - spine[i])
    axis, sign = (0, side) if group.reach == 'out' else (1, -1)
    vertices = sections.vertices
    spine_axis = 1 if p['spine_axis'] == 'vertical' else 2
    window = group.window * np.linalg.norm(spine[-1] - spine[0])
    mask = side * vertices[:, 0] > 0
    if axis != spine_axis:
        mask &= np.abs(vertices[:, spine_axis] - attach[spine_axis]) <= window
    mask &= sign * (vertices[:, axis] - attach[axis]) >= 0
    if not mask.any():
        raise ValueError(f'{group.name}: no limb found along the requested direction')
    reach = np.max(sign * (vertices[mask, axis] - attach[axis]))
    if reach <= np.finfo(float).eps:
        raise ValueError(f'{group.name}: limb reach is degenerate')
    fractions = np.linspace(*group.span, p['chain_samples'])
    offsets = attach[axis] + sign * reach * fractions
    other = [j for j in range(3) if j != axis]
    low, high = np.full(3, -np.inf), np.full(3, np.inf)
    if axis != 0:
        lateral = group.lateral_min * np.max(side * vertices[mask, 0])
        if side > 0:
            low[0] = lateral
        else:
            high[0] = -lateral
    if axis != spine_axis:
        low[spine_axis], high[spine_axis] = attach[spine_axis] - window, attach[spine_axis] + window
    bounds = low[other], high[other]
    path, radii = [], []
    step = abs(offsets[1] - offsets[0])
    for offset in offsets[::-1]:
        search_bounds = bounds
        if path:
            predicted = path[-1].copy()
            if len(path) >= 2:
                tangent = path[-1] - path[-2]
                predicted += tangent * np.minimum(1.0, p['limb_tangent_step_limit'] * step / max(np.linalg.norm(tangent), np.finfo(float).eps))
            if group.reach == 'down' and axis != spine_axis:
                anchor = attach.copy()
                anchor[0] = float(np.median(np.asarray(path)[:max(1, int(len(path) * p['limb_anchor_fraction'])), 0]))
                progress = sign * (offset - attach[axis]) / reach
                predicted += p['limb_anchor_weight'] * (1 - progress) ** p['limb_anchor_power'] * (anchor - predicted)
            predicted[axis] = offset
            cap = max(float(np.median(radii)), step)
            half_width = p['limb_radius_window'] * cap + p['limb_step_window'] * step
            search_bounds = np.maximum(bounds[0], predicted[other] - half_width), np.minimum(bounds[1], predicted[other] + half_width)
        section = sections.section(axis, offset)
        if section.open_components:
            notes.add(f'{group.name}: open cross-section detected; non-closed parts ignored')
        points, radius = section.candidates(p['section_resolution'], keep=None, bounds=search_bounds,
                                            chunk_size=p['clearance_chunk_size'])
        if not len(points):
            raise ValueError(f'{group.name}: limb tracing left the closed solid; adjust the partition or span')
        points = _lift(points, axis, offset)
        if not path:
            score = radius - p['limb_initial_distance_weight'] * np.linalg.norm(points - attach, axis=1)
        else:
            distance = np.sum((points - predicted) ** 2, axis=1)
            score = np.minimum(radius / cap, p['limb_radius_cap']) - p['limb_continuity_weight'] * distance / (cap ** 2 + step ** 2)
        index = int(np.argmax(score))
        path.append(points[index])
        radii.append(radius[index])
    return simplify_chain(np.stack(path[::-1]), group.joints, config=p), int(round(position))


def rig_skeleton(mesh, *, config):
    """Fit the explicit topology without morphology inference or reference data."""
    p = resolve_rig_config(config)
    sections, frame, axes = canonical_mesh(mesh, p['up'], p['forward'],
                                           vertical=p['spine_axis'] == 'vertical', config=p)
    notes = {'fitted from geometric cross-sections; no reference rig or skin weights were used'}
    spine = _spine(sections, p, notes)
    root = int(round(p['root_at'] * (len(spine) - 1)))
    joints = list(spine)
    parents = [-1 if i == root else i - 1 if i > root else i + 1 for i in range(len(spine))]
    names = [f'spine.{i}' for i in range(len(spine))]
    chains = {'spine': list(range(len(spine)))}
    for group in p['limb_groups']:
        for side, tag in ((1, 'L'), (-1, 'R')) if group.paired else ((1, 'C'),):
            points, anchor = _limb(sections, spine, group, side, p, notes)
            label = f'{group.name}.{tag}'
            start = len(joints)
            joints.extend(points)
            parents.extend([anchor] + list(range(start, start + len(points) - 1)))
            names.extend(f'{label}.{i}' for i in range(len(points)))
            chains[label] = list(range(start, start + len(points)))
    world = np.einsum('ij,jk->ik', np.asarray(joints) * frame.scale, axes) + frame.origin
    return RigResult(p['name'], world, np.array(parents, np.int64), names, chains, frame,
                     {**p, 'up': tuple(frame.up), 'forward': tuple(frame.forward)}, sorted(notes))
