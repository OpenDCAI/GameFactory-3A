"""Bounded N-node hinge-chain IK (closed form for two bones) and bounded shoulder/elbow IK."""
from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares

from .units import basis, fk, set_world_rotation, unit


DOF_NAMES = ('shoulder_flexion', 'shoulder_abduction', 'shoulder_swivel', 'elbow_flexion')


def _orient_bones(clip, chain, upper, lower, normal, rest_pole, epsilon):
    a, b, c = chain
    rest = clip.template.rest
    old_upper, old_lower = rest[b] - rest[a], rest[c] - rest[b]
    rest_normal = basis(old_upper, rest_pole, epsilon=epsilon)[:, 2]
    for joint, old, desired in ((a, old_upper, upper), (b, old_lower, lower)):
        tangent = np.cross(normal, desired)
        rest_tangent = np.cross(rest_normal, old)
        rotation = basis(desired, tangent, epsilon=epsilon) @ basis(old, rest_tangent, epsilon=epsilon).T
        set_world_rotation(clip, joint, rotation)


def _two_bone_closed_form(clip, chain, targets, poles, *, flexion, rest_pole, epsilon):
    """Closed-form branch of ``solve_chain_ik`` for one hinge bent toward the pole.

    Unreachable targets are projected to the flexion-limited shell; bones never stretch.
    """
    a, b, c = chain
    rest = clip.template.rest
    l1 = np.linalg.norm(rest[b] - rest[a])
    l2 = np.linalg.norm(rest[c] - rest[b])
    positions, _ = fk(clip)
    origin = positions[:, a]
    delta = targets - origin
    distance_requested = np.linalg.norm(delta, axis=-1)
    fallback = rest[c] - rest[a]
    if np.linalg.norm(fallback) <= epsilon:
        fallback = rest[b] - rest[a]
    direction = unit(np.where((distance_requested > epsilon)[:, None], delta, fallback), epsilon=epsilon)
    lateral = basis(direction, poles, epsilon=epsilon)[:, :, 1]
    low, high = np.deg2rad(flexion)
    minimum = np.sqrt(l1 * l1 + l2 * l2 + 2 * l1 * l2 * np.cos(high))
    maximum = np.sqrt(l1 * l1 + l2 * l2 + 2 * l1 * l2 * np.cos(low))
    distance = np.clip(distance_requested, minimum, maximum)
    if minimum <= epsilon:
        raise ValueError('Flexion limits allow a numerically singular distance; tighten bounds or epsilon')
    along = (l1 * l1 - l2 * l2 + distance * distance) / (2 * distance)
    height = np.sqrt(np.maximum(l1 * l1 - along * along, 0))
    upper = along[:, None] * direction + height[:, None] * lateral
    lower = distance[:, None] * direction - upper
    normal = unit(np.cross(direction, lateral), epsilon=epsilon)
    _orient_bones(clip, chain, upper, lower, normal, rest_pole, epsilon)
    actual, _ = fk(clip)
    flex = np.degrees(np.arccos(np.clip((distance * distance - l1 * l1 - l2 * l2) / (2 * l1 * l2), -1, 1)))
    return {'residual': np.linalg.norm(actual[:, c] - targets, axis=-1),
            'flex_degrees': flex, 'projected_distance': np.abs(distance_requested - distance),
            'limit_violation_degrees': np.maximum(np.maximum(flexion[0] - flex, flex - flexion[1]), 0)}


def chain_flexion_bounds(flexion, interior):
    """One signed (low, high) pair shared by every interior hinge, or one pair per hinge."""
    bounds = np.asarray(flexion, float)
    if bounds.shape == (2,):
        bounds = np.tile(bounds, (interior, 1))
    if bounds.shape != (interior, 2) or not np.isfinite(bounds).all():
        raise ValueError(f'Chain flexion needs one bound pair or {interior} pairs')
    if np.any(bounds[:, 0] <= -180) or np.any(bounds[:, 1] >= 180) or np.any(bounds[:, 0] >= bounds[:, 1]):
        raise ValueError('Chain flexion bounds must be increasing pairs inside (-180,180)')
    return bounds


def _planar_end(params, lengths):
    angles = params[0] - np.concatenate([[0.], np.cumsum(params[1:])])
    return angles, np.array([lengths @ np.cos(angles), lengths @ np.sin(angles)])


def _solve_planar(lengths, distance, bounds, preferred, solver):
    """Bounded planar hinge chain: reach ``distance`` along +x, prefer the bind-pose flexion."""
    scale = lengths.sum()
    goal = np.array([distance, 0.])
    low = np.r_[-np.pi, bounds[:, 0]]
    high = np.r_[np.pi, bounds[:, 1]]

    def position(x):
        return (_planar_end(x, lengths)[1] - goal) / scale

    def position_jacobian(x):
        angles = _planar_end(x, lengths)[0]
        tangent = lengths[:, None] * np.column_stack([-np.sin(angles), np.cos(angles)])
        tail = np.cumsum(tangent[::-1], axis=0)[::-1]
        return np.column_stack([tail[0], -tail[1:].T]) / scale

    def regularized(x):
        return np.r_[position(x), solver['regularization'] * (x[1:] - preferred)]

    def regularized_jacobian(x):
        prior = np.zeros((len(preferred), len(x)))
        prior[:, 1:] = solver['regularization'] * np.eye(len(preferred))
        return np.vstack([position_jacobian(x), prior])

    tolerance = solver['tolerance']
    best = None
    for shape in (preferred, (bounds[:, 0] + bounds[:, 1]) / 2):
        start = np.r_[0., np.clip(shape, bounds[:, 0], bounds[:, 1])]
        start[0] = -np.arctan2(*_planar_end(start, lengths)[1][::-1])
        first = least_squares(regularized, np.clip(start, low, high), jac=regularized_jacobian, bounds=(low, high),
                              max_nfev=solver['max_nfev'], xtol=tolerance, ftol=tolerance, gtol=tolerance)
        final = least_squares(position, first.x, jac=position_jacobian, bounds=(low, high),
                              max_nfev=solver['max_nfev'], xtol=tolerance, ftol=tolerance, gtol=tolerance)
        error = float(np.linalg.norm(position(final.x)))
        key = (round(error, 12), float(np.linalg.norm(final.x[1:] - preferred)))
        # At zero residual SciPy may stop on max_nfev; a reached target counts as converged.
        reached = error <= np.sqrt(tolerance)
        if best is None or key < best[0]:
            best = (key, final.x, bool(first.success and (final.success or reached)))
    return best[1], best[2]


def solve_chain_ik(clip, chain, targets, poles, *, flexion, rest_pole, solver, epsilon):
    """Bounded hinge-chain IK for three or more contiguous nodes; bone lengths never change.

    Interior nodes bend in the pole plane. Two bones use a closed form with one ``flexion``
    pair in (0, 180) and ``solver=None``. Longer chains are solved per sample with ``solver``
    and signed bounds (positive toward the pole), preferring the rest-pose flexion.
    Unreachable targets return the nearest bounded pose; ``residual`` is measured against
    the requested target. Twist and out-of-plane bending are not modelled.
    """
    chain = [int(j) for j in chain]
    if len(chain) == 3:
        if solver is not None:
            raise ValueError('Two-bone chains use the closed form; omit chain_solver')
        low, high = np.asarray(flexion, float)
        if not 0 < low < high < 180:
            raise ValueError('Two-bone flexion bounds must lie inside (0,180)')
        return _two_bone_closed_form(clip, chain, targets, poles, flexion=flexion,
                                     rest_pole=rest_pole, epsilon=epsilon)
    if solver is None:
        raise ValueError('Chains with three or more bones need explicit solver settings')
    rest = clip.template.rest
    segments = rest[chain[1:]] - rest[chain[:-1]]
    lengths = np.linalg.norm(segments, axis=1)
    if len(chain) < 3 or lengths.min() <= epsilon:
        raise ValueError('Chain IK needs at least three nodes and nonzero bones')
    bounds = np.deg2rad(chain_flexion_bounds(flexion, len(chain) - 2))
    rest_axis = unit(rest[chain[-1]] - rest[chain[0]], epsilon=epsilon)
    rest_frame = basis(rest_axis, rest_pole, epsilon=epsilon)
    rest_lateral, rest_normal = rest_frame[:, 1], rest_frame[:, 2]
    planar = np.column_stack([segments @ rest_axis, segments @ rest_lateral])
    angles = np.arctan2(planar[:, 1], planar[:, 0])
    preferred = np.clip(angles[:-1] - angles[1:], bounds[:, 0], bounds[:, 1])
    positions, _ = fk(clip)
    origin = positions[:, chain[0]]
    delta = targets - origin
    requested = np.linalg.norm(delta, axis=-1)
    direction = unit(np.where((requested > epsilon)[:, None], delta, rest_axis), epsilon=epsilon)
    lateral = basis(direction, poles, epsilon=epsilon)[:, :, 1]
    count = len(targets)
    flex = np.zeros((count, len(chain) - 2))
    points = np.zeros((len(chain), count, 3))
    points[0] = origin
    success = np.zeros(count, bool)
    for i in range(count):
        params, success[i] = _solve_planar(lengths, requested[i], bounds, preferred, solver)
        theta = _planar_end(params, lengths)[0]
        steps = lengths[:, None] * (np.cos(theta)[:, None] * direction[i] + np.sin(theta)[:, None] * lateral[i])
        points[1:, i] = origin[i] + np.cumsum(steps, axis=0)
        flex[i] = params[1:]
    normal = unit(np.cross(direction, lateral), epsilon=epsilon)
    for k, joint in enumerate(chain[:-1]):
        desired = points[k + 1] - points[k]
        rest_tangent = np.cross(rest_normal, segments[k] / lengths[k])
        rotation = (basis(desired, np.cross(normal, unit(desired, epsilon=epsilon)), epsilon=epsilon)
                    @ basis(segments[k], rest_tangent, epsilon=epsilon).T)
        set_world_rotation(clip, joint, rotation)
    actual, _ = fk(clip)
    violation = np.maximum(bounds[:, 0] - flex, flex - bounds[:, 1])
    return {'residual': np.linalg.norm(actual[:, chain[-1]] - targets, axis=-1),
            'flex_degrees': np.degrees(flex), 'projected_distance': np.linalg.norm(points[-1] - targets, axis=-1),
            'solver_success': success,
            'limit_violation_degrees': np.degrees(np.maximum(violation, 0)).max(axis=1)}


def arm_vectors(angles, side):
    flex, abduct, swivel, elbow = np.moveaxis(np.asarray(angles), -1, 0)
    sign = 1 if side == 'L' else -1
    upper = np.stack([sign * np.sin(abduct), -np.cos(abduct) * np.cos(flex),
                      np.cos(abduct) * np.sin(flex)], axis=-1)
    tangent = np.stack([np.zeros_like(flex), np.sin(flex), np.cos(flex)], axis=-1)
    bend = tangent * np.cos(swivel)[..., None] + sign * np.cross(upper, tangent) * np.sin(swivel)[..., None]
    lower = upper * np.cos(elbow)[..., None] + bend * np.sin(elbow)[..., None]
    return upper, lower, bend


def solve_arm_ik(clip, chain, targets, frame, *, side, limits, preferred_swivel, rest_pole, epsilon):
    """Bounded shoulder/elbow IK; report original target error and optimizer failures."""
    a, b, c = chain
    positions, _ = fk(clip)
    local = np.einsum('tji,tj->ti', frame, targets - positions[:, a])
    rest = clip.template.rest
    l1, l2 = np.linalg.norm(rest[b] - rest[a]), np.linalg.norm(rest[c] - rest[b])
    length = l1 + l2
    bounds = np.deg2rad(np.asarray([limits[name] for name in DOF_NAMES]))
    workspace = local.copy()
    workspace[:, 2] = np.maximum(workspace[:, 2], -limits['target_back_ratio'] * length)
    preferred = np.clip(np.deg2rad(preferred_swivel), *bounds[2])
    solutions, success = [], []
    for target in workspace / length:
        distance = np.linalg.norm(target)
        direction = target / distance if distance > epsilon else np.zeros(3)
        flex = np.arctan2(direction[2], -direction[1])
        abduct = np.arcsin(np.clip((1 if side == 'L' else -1) * direction[0], -1, 1))
        elbow = np.arccos(np.clip(((distance * length) ** 2 - l1 * l1 - l2 * l2) / (2 * l1 * l2), -1, 1))
        initial = np.clip([flex - elbow * l2 / length, abduct, preferred, elbow], bounds[:, 0], bounds[:, 1])

        def residual(angles):
            upper, lower, _ = arm_vectors(angles, side)
            endpoint = (l1 * upper + l2 * lower) / length
            return np.r_[endpoint - target, limits['regularization'] * (angles[2] - preferred)]

        solution = least_squares(residual, initial, bounds=(bounds[:, 0], bounds[:, 1]),
                                 max_nfev=limits['max_nfev'], ftol=limits['ftol'],
                                 xtol=limits['xtol'], gtol=limits['gtol'])
        solutions.append(solution.x)
        success.append(bool(solution.success))
    angles = np.asarray(solutions)
    upper, lower, bend = arm_vectors(angles, side)
    upper_world = np.einsum('tij,tj->ti', frame, upper)
    lower_world = np.einsum('tij,tj->ti', frame, lower)
    bend_world = np.einsum('tij,tj->ti', frame, bend)
    normal = unit(np.cross(upper_world, bend_world), epsilon=epsilon)
    _orient_bones(clip, chain, upper_world, lower_world, normal, rest_pole, epsilon)
    actual, _ = fk(clip)
    violation = np.maximum(bounds[:, 0] - angles, angles - bounds[:, 1])
    return {'residual': np.linalg.norm(actual[:, c] - targets, axis=-1),
            'flex_degrees': np.degrees(angles[:, 3]), 'dofs_degrees': np.degrees(angles),
            'workspace_correction': np.linalg.norm(workspace - local, axis=-1),
            'solver_success': np.asarray(success),
            'limit_violation_degrees': np.degrees(np.maximum(violation, 0)).max(axis=1)}
