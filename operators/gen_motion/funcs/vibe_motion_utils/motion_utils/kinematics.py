"""Batched IK in the original GLB rest-rotation basis. Quaternions are xyzw."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation


def unit(v):
    v = np.asarray(v, float)
    return v/np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-12)


def _basis(direction, pole):
    x, pole = np.broadcast_arrays(unit(direction), np.asarray(pole, float))
    y = pole - (pole*x).sum(-1, keepdims=True)*x
    fallback = np.eye(3)[np.argmin(np.abs(x), axis=-1)]
    fallback -= (fallback*x).sum(-1, keepdims=True)*x
    y = unit(np.where((np.linalg.norm(y, axis=-1) < 1e-7)[..., None], fallback, y))
    return np.stack([x, y, np.cross(x, y)], axis=-1)


def set_world_rotation(rig, quats, translations, joint, desired):
    world = rig.fk(quats, translations)
    parent = int(rig.parents[joint])
    local = desired if parent < 0 else np.swapaxes(world[:, parent, :3, :3], -1, -2) @ desired
    quats[:, joint] = Rotation.from_matrix(local).as_quat()


def set_root_position(rig, quats, translations, root, position):
    world = rig.fk(quats, translations)
    parent = int(rig.parents[root])
    if parent < 0:
        translations[:, root] = position
    else:
        translations[:, root] = np.einsum('tji,tj->ti', world[:, parent, :3, :3],
                                          position-world[:, parent, :3, 3])


def _chain_limits(flex_limits, count):
    limits = np.asarray(flex_limits, float)
    if limits.shape == (2,):
        limits = np.tile(limits, (count, 1))
    if limits.shape != (count, 2) or not np.isfinite(limits).all():
        raise ValueError(f'Flexion bounds must be one pair or {count} pairs')
    if np.any(limits[:, 0] <= -180.) or np.any(limits[:, 1] >= 180.) or np.any(limits[:, 0] >= limits[:, 1]):
        raise ValueError('Invalid flexion bounds')
    return limits


def _bind_normal(rest, chain, poles, rest_pole):
    """Return rest axis, intrinsic bend side (for pose preference) and twist normal."""
    axis = unit(rest[chain[-1]]-rest[chain[0]])
    offsets = rest[chain[1:-1]]-rest[chain[0]]
    geometric = (offsets-np.outer(offsets@axis, axis)).mean(0)
    if rest_pole is None:
        bind_pole = geometric
        if np.linalg.norm(bind_pole) < 1e-6:
            bind_pole = poles[0]
    else:
        bind_pole = np.asarray(rest_pole, float)
        if bind_pole.shape != (3,) or not np.isfinite(bind_pole).all() or np.linalg.norm(bind_pole) < 1e-8:
            raise ValueError('rest_pole must be a finite nonzero rest-world direction')
        bind_pole = unit(bind_pole)
        if np.linalg.norm(np.cross(axis, bind_pole)) < 1e-6:
            raise ValueError('rest_pole must not be parallel to the rest chain')
    frame = _basis(axis, bind_pole)
    side = geometric if np.linalg.norm(geometric) >= 1e-6 else bind_pole
    return axis, _basis(axis, side)[:, 1], frame[:, 2]


def _planar_end(params, lengths):
    angles = params[0]-np.concatenate([[0.], np.cumsum(params[1:])])
    return angles, np.array([lengths@np.cos(angles), lengths@np.sin(angles)])


def _solve_planar(lengths, distance, limits, preferred, regularization, max_nfev):
    from scipy.optimize import least_squares
    scale = lengths.sum()
    target = np.array([distance, 0.])
    low = np.r_[-np.pi, limits[:, 0]]
    high = np.r_[np.pi, limits[:, 1]]

    def position(x):
        return (_planar_end(x, lengths)[1]-target)/scale

    def position_jac(x):
        angles = _planar_end(x, lengths)[0]
        tangent = lengths[:, None]*np.column_stack([-np.sin(angles), np.cos(angles)])
        tail = np.cumsum(tangent[::-1], axis=0)[::-1]
        return np.column_stack([tail[0], -tail[1:].T])/scale

    def regularized(x):
        return np.r_[position(x), regularization*(x[1:]-preferred)]

    def regularized_jac(x):
        prior = np.zeros((len(preferred), len(x)))
        prior[:, 1:] = regularization*np.eye(len(preferred))
        return np.vstack([position_jac(x), prior])

    best = None
    for shape in (preferred, .5*(limits[:, 0]+limits[:, 1])):
        start = np.r_[0., np.clip(shape, limits[:, 0], limits[:, 1])]
        start[0] = -np.arctan2(*_planar_end(start, lengths)[1][::-1])
        first = least_squares(regularized, np.clip(start, low, high), jac=regularized_jac,
                              bounds=(low, high), max_nfev=max_nfev, xtol=1e-14, ftol=1e-14, gtol=1e-14)
        final = least_squares(position, first.x, jac=position_jac, bounds=(low, high),
                              max_nfev=max_nfev, xtol=1e-15, ftol=1e-15, gtol=1e-15)
        error = float(np.linalg.norm(position(final.x)))
        key = (round(error, 12), float(np.linalg.norm(final.x[1:]-preferred)))
        if best is None or key < best[0]:
            best = (key, final.x, bool(first.success and final.success))
    return best[1], best[2]


def solve_bone(rig, quats, translations, chain, targets, poles, flex_limits=(5., 155.), *,
               rest_pole=None, method='auto', regularization=1e-3, max_nfev=200):
    """Solve a contiguous N-node limb (N-1 bones) toward per-sample end targets.

    Each interior joint is a hinge in the pole plane; flexion is the signed
    bend toward the pole, so negative bounds permit alternating bends
    (e.g. stifle/hock). Bone lengths come from the bind pose and never change.
    Three-node chains with positive bounds keep the original analytic solve;
    longer chains use deterministic bounded least squares, solved per sample
    independently. Redundancy is resolved toward the bind-pose flexion, then
    refined for reach. Out-of-plane/ball DOFs and end orientation are not
    modeled. Unreachable targets return the nearest feasible pose; residuals
    are always measured against the original target.
    """
    chain = [int(j) for j in chain]
    if len(chain) < 3 or len(set(chain)) != len(chain):
        raise ValueError('IK requires at least three different nodes')
    if any(rig.parents[child] != parent for parent, child in zip(chain[:-1], chain[1:])):
        raise ValueError('IK requires a contiguous parent-child chain')
    if method not in ('auto', 'analytic', 'numeric'):
        raise ValueError('Unknown IK method')
    if not np.isfinite(regularization) or regularization < 0 or not isinstance(max_nfev, int) or max_nfev < 10:
        raise ValueError('Invalid IK regularization or iteration budget')
    limits = _chain_limits(flex_limits, len(chain)-2)
    analytic = len(chain) == 3 and limits[0, 0] > 0
    if method == 'analytic' and not analytic:
        raise ValueError('Analytic IK requires three nodes and positive flexion bounds')
    analytic = analytic and method != 'numeric'
    targets = np.broadcast_to(np.asarray(targets, float), (len(quats), 3))
    poles = np.broadcast_to(np.asarray(poles, float), targets.shape)
    if not np.isfinite(targets).all() or not np.isfinite(poles).all() or np.any(np.linalg.norm(poles, axis=-1) < 1e-8):
        raise ValueError('IK targets/poles must be finite; poles must be nonzero')
    rest = rig.rest_world[:, :3, 3]
    segments = rest[chain[1:]]-rest[chain[:-1]]
    lengths = np.linalg.norm(segments, axis=1)
    if lengths.min() < 1e-8:
        raise ValueError('Zero-length IK bone')
    rest_axis, rest_lateral, rest_normal = _bind_normal(rest, chain, poles, rest_pole)
    world = rig.fk(quats, translations)
    origin = world[:, chain[0], :3, 3]
    delta = targets-origin
    raw_distance = np.linalg.norm(delta, axis=-1)
    direction = unit(np.where((raw_distance > 1e-8)[:, None], delta, rest[chain[-1]]-rest[chain[0]]))
    lateral = _basis(direction, poles)[:, :, 1]
    count = len(quats)
    if analytic:
        l1, l2 = lengths
        low, high = np.deg2rad(limits[0])
        dmin = np.sqrt(l1*l1+l2*l2+2*l1*l2*np.cos(high))
        dmax = np.sqrt(l1*l1+l2*l2+2*l1*l2*np.cos(low))
        distance = np.clip(raw_distance, dmin, dmax)
        along = (l1*l1-l2*l2+distance*distance)/(2*distance)
        height = np.sqrt(np.maximum(l1*l1-along*along, 0.))
        points = [origin, origin+along[:, None]*direction+height[:, None]*lateral, origin+distance[:, None]*direction]
        flex = np.rad2deg(np.arccos(np.clip((distance*distance-l1*l1-l2*l2)/(2*l1*l2), -1., 1.)))
        projected = np.abs(raw_distance-distance)
        success = np.ones(count, bool)
    else:
        planar = np.column_stack([segments@rest_axis, segments@rest_lateral])
        angles = np.arctan2(planar[:, 1], planar[:, 0])
        bounds = np.deg2rad(limits)
        preferred = np.clip(angles[:-1]-angles[1:], bounds[:, 0], bounds[:, 1])
        flex = np.zeros((count, len(chain)-2))
        points = np.zeros((len(chain), count, 3))
        points[0] = origin
        success = np.zeros(count, bool)
        for i in range(count):
            params, success[i] = _solve_planar(lengths, raw_distance[i], bounds, preferred, regularization, max_nfev)
            theta = _planar_end(params, lengths)[0]
            steps = lengths[:, None]*(np.cos(theta)[:, None]*direction[i]+np.sin(theta)[:, None]*lateral[i])
            points[1:, i] = origin[i]+np.cumsum(steps, axis=0)
            flex[i] = np.rad2deg(params[1:])
        projected = np.linalg.norm(points[-1]-targets, axis=-1)
        points = list(points)
    plane_normal = unit(np.cross(direction, lateral))
    for k, joint in enumerate(chain[:-1]):
        desired = points[k+1]-points[k]
        tangent = np.cross(plane_normal, unit(desired))
        rest_tangent = np.cross(rest_normal, unit(segments[k]))
        rotation = _basis(desired, tangent) @ _basis(segments[k], rest_tangent).T
        set_world_rotation(rig, quats, translations, joint, rotation @ rig.rest_world[joint, :3, :3])
    actual = rig.fk(quats, translations)[:, chain[-1], :3, 3]
    return {'residual': np.linalg.norm(actual-targets, axis=-1), 'flex_degrees': flex,
            'projected_distance': projected, 'solver_success': success,
            'method': 'analytic' if analytic else 'numeric'}


def _rodrigues(axis, angle):
    """Rotation matrices (..., 3, 3) about unit axes (..., 3) by angles (...) in radians."""
    axis = unit(axis)
    c, s = np.cos(angle)[..., None, None], np.sin(angle)[..., None, None]
    k = np.zeros(axis.shape[:-1]+(3, 3))
    k[..., 0, 1], k[..., 0, 2], k[..., 1, 0] = -axis[..., 2], axis[..., 1], axis[..., 2]
    k[..., 1, 2], k[..., 2, 0], k[..., 2, 1] = -axis[..., 0], -axis[..., 1], axis[..., 0]
    return np.eye(3)+s*k+(1-c)*(k @ k)


def solve_chain(rig, quats, translations, chain, targets, poles, flex_limits, *, bend_weights=None, grid=241,
                continuity=0.02):
    """Shape-preserving IK for a contiguous N-node chain (N >= 3), used by the effector operator.

    The chain keeps its current parent-relative (bind) shape: every interior joint bends about one shared hinge
    axis (pole x chain direction) by bend_weights-distributed extra flexion within flex_limits (degrees relative to
    the bind shape, + = interior joints move toward the pole; a negative weight bends that joint the opposite way,
    e.g. [1, -1] for a Z-folding quadruped fore leg (elbow back, wrist forward)), the total bend being chosen per frame so the
    base->tip distance matches the target (grid search with a small continuity preference); the chain is then
    rotated as a whole so the tip points at the target and the bend side faces the pole. Bone twist and
    non-planar bind shapes are preserved (no per-bone plane re-basing). Unreachable targets keep the closest
    reachable distance; residuals are measured against the original targets.
    """
    chain = [int(j) for j in chain]
    if len(chain) < 3 or any(rig.parents[c] != p for p, c in zip(chain[:-1], chain[1:])):
        raise ValueError('solve_chain needs >= 3 contiguous nodes')
    count = len(quats)
    interior = len(chain)-2
    limits = np.deg2rad(_chain_limits(flex_limits, interior))
    weights = np.ones(interior) if bend_weights is None else np.asarray(bend_weights, float)
    if weights.shape != (interior,) or not np.isfinite(weights).all() or np.abs(weights).sum() <= 0:
        raise ValueError(f'bend_weights must be {interior} numbers with a nonzero total magnitude')
    weights = weights/np.abs(weights).sum()
    targets = np.broadcast_to(np.asarray(targets, float), (count, 3))
    poles = np.broadcast_to(np.asarray(poles, float), (count, 3))
    world = rig.fk(quats, translations)
    points = world[:, chain][:, :, :3, 3]
    rotations = world[:, chain][:, :-1, :3, :3].copy()
    bones = np.diff(points, axis=1)
    origin = points[:, 0]
    direction = unit(bones.sum(axis=1))
    hinge = np.cross(poles, direction)
    weak = np.linalg.norm(hinge, axis=-1) < 1e-8
    if np.any(weak):
        fallback = np.cross(direction[weak], np.where(np.abs(direction[weak][:, 1:2]) < .9, [[0., 1, 0]], [[1., 0, 0]]))
        hinge[weak] = fallback
    hinge = unit(hinge)
    active = weights != 0
    safe = np.where(active, weights, 1.)
    low_i, high_i = np.minimum(limits[:, 0]/safe, limits[:, 1]/safe), np.maximum(limits[:, 0]/safe, limits[:, 1]/safe)
    lo = np.max(np.where(active, low_i, -np.inf))
    hi = np.min(np.where(active, high_i, np.inf))
    lo, hi = min(lo, 0.), max(hi, 0.)
    thetas = np.linspace(lo, hi, grid)
    bends = np.clip(thetas[:, None]*weights[None], limits[:, 0], limits[:, 1])           # (G, interior)
    cumulative = np.concatenate([np.zeros((grid, 1)), np.cumsum(bends, axis=1)], axis=1)  # (G, bones)
    wanted = np.linalg.norm(targets-origin, axis=-1)
    scale = max(float(np.linalg.norm(bones[0], axis=-1).sum()), 1e-9)
    chosen = np.zeros(count)
    previous = 0.
    step = (hi-lo)/(grid-1) if grid > 1 else 0.

    def reach_of(i, theta):
        bend = np.clip(theta*weights, limits[:, 0], limits[:, 1])
        cum = np.concatenate([[0.], np.cumsum(bend)])
        rot = _rodrigues(np.broadcast_to(hinge[i], cum.shape+(3,)), cum)
        return float(np.linalg.norm(np.einsum('bij,bj->i', rot, bones[i])))
    for i in range(count):
        rot = _rodrigues(np.broadcast_to(hinge[i], cumulative.shape+(3,)), cumulative)    # (G, bones, 3, 3)
        reach = np.linalg.norm(np.einsum('gbij,bj->gi', rot, bones[i]), axis=-1)
        cost = np.abs(reach-wanted[i])/max(wanted[i], 1e-9*scale+1e-12)+continuity*np.abs(thetas-previous)
        g = int(np.argmin(cost))
        theta = thetas[g]
        if step > 0:
            a, b = max(lo, theta-step), min(hi, theta+step)
            fa, fb = reach_of(i, a)-wanted[i], reach_of(i, b)-wanted[i]
            if fa*fb < 0:                       # bracketed root: bisection to sub-grid precision
                for _ in range(30):
                    m = .5*(a+b)
                    fm = reach_of(i, m)-wanted[i]
                    if fa*fm <= 0:
                        b, fb = m, fm
                    else:
                        a, fa = m, fm
                theta = .5*(a+b)
        chosen[i] = previous = theta
    bends = np.clip(chosen[:, None]*weights[None], limits[:, 0], limits[:, 1])
    cumulative = np.concatenate([np.zeros((count, 1)), np.cumsum(bends, axis=1)], axis=1)  # (T, bones)
    bend_rot = _rodrigues(np.broadcast_to(hinge[:, None], cumulative.shape+(3,)), cumulative)
    tip = np.einsum('tbij,tbj->ti', bend_rot, bones)
    reference = np.cross(tip, hinge)
    align = _basis(targets-origin, poles) @ np.swapaxes(_basis(tip, reference), -1, -2)
    for k, joint in enumerate(chain[:-1]):
        set_world_rotation(rig, quats, translations, joint, align @ bend_rot[:, k] @ rotations[:, k])
    actual = rig.fk(quats, translations)[:, chain[-1], :3, 3]
    return {'residual': np.linalg.norm(actual-targets, axis=-1), 'flex_degrees': np.rad2deg(bends),
            'projected_distance': np.abs(np.linalg.norm(tip, axis=-1)-wanted), 'bend_degrees': np.rad2deg(chosen)}


def solve_two_bone(rig, quats, translations, chain, targets, poles, flex_limits=(5., 155.), *, rest_pole=None):
    """Backward-compatible three-node entry point; see solve_bone."""
    if len(chain) != 3 or len(set(chain)) != 3:
        raise ValueError('IK requires three different nodes')
    low, high = flex_limits
    if not 0. < low < high < 180.:
        raise ValueError('Invalid flexion bounds')
    return solve_bone(rig, quats, translations, chain, targets, poles, flex_limits, rest_pole=rest_pole, method='analytic')
