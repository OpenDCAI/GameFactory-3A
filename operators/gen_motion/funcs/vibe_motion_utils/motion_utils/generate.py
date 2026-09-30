"""Analytic two-bone IK and bounded shoulder/elbow IK."""
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


def solve_two_bone_ik(clip, chain, targets, poles, *, flexion, rest_pole, epsilon):
    """Project unreachable targets to a flexion-limited shell, never stretch bones."""
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
