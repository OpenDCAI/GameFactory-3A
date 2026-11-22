"""Four-DOF arm IK: bounded shoulder flexion/abduction/swivel and a one-way elbow."""
from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares

from .kinematics import _basis, set_world_rotation, unit

DOF_NAMES = ('shoulder_flexion', 'shoulder_abduction', 'shoulder_swivel', 'elbow_flexion')


def arm_vectors(angles, side):
    """Chest-frame directions. Swivel rotates the elbow plane around the upper arm.

    This convention is an explicit procedural joint model, not medical calibration.
    The transported tangent avoids a pole singularity when the arm points forward.
    """
    flex, abduct, swivel, elbow = np.moveaxis(np.asarray(angles), -1, 0)
    sign = 1. if side == 'L' else -1.
    upper = np.stack([sign*np.sin(abduct), -np.cos(abduct)*np.cos(flex),
                      np.cos(abduct)*np.sin(flex)], axis=-1)
    tangent = np.stack([np.zeros_like(flex), np.sin(flex), np.cos(flex)], axis=-1)
    bend = tangent*np.cos(swivel)[..., None]+sign*np.cross(upper, tangent)*np.sin(swivel)[..., None]
    lower = upper*np.cos(elbow)[..., None]+bend*np.sin(elbow)[..., None]
    return upper, lower, bend


def measure_arm_dofs(world, chain, chest_frame, side):
    a, b, c = chain
    upper = unit(np.einsum('tji,tj->ti', chest_frame, world[:, b, :3, 3]-world[:, a, :3, 3]))
    lower = unit(np.einsum('tji,tj->ti', chest_frame, world[:, c, :3, 3]-world[:, b, :3, 3]))
    sign = 1. if side == 'L' else -1.
    flex = np.arctan2(upper[:, 2], -upper[:, 1])
    abduct = np.arcsin(np.clip(sign*upper[:, 0], -1., 1.))
    elbow = np.arccos(np.clip(np.sum(upper*lower, axis=-1), -1., 1.))
    bend = unit(lower-upper*np.cos(elbow)[:, None])
    tangent = np.column_stack([np.zeros_like(flex), np.sin(flex), np.cos(flex)])
    swivel = np.arctan2(sign*np.sum(bend*np.cross(upper, tangent), axis=-1), np.sum(bend*tangent, axis=-1))
    return np.rad2deg(np.column_stack([flex, abduct, swivel, elbow]))


def solve_arm(rig, quats, translations, chain, targets, chest_frame, side, limits, preferred_swivel):
    """Solve within bounds, prioritizing legal joints over impossible wrist targets. preferred_swivel: degrees, a
    number or one value per sample (elbow-plane direction curve).

    A fixed seed per target preserves single-time/fps-independent evaluation.
    Returned residuals include workspace correction and any remaining reach error.
    """
    if side not in ('L', 'R') or len(chain) != 3:
        raise ValueError('Expected L/R and shoulder-elbow-wrist chain')
    a, b, c = chain
    if rig.parents[b] != a or rig.parents[c] != b:
        raise ValueError('Arm IK requires contiguous joints')
    count = len(quats)
    requested = np.broadcast_to(np.asarray(targets, float), (count, 3))
    frame = np.broadcast_to(np.asarray(chest_frame, float), (count, 3, 3))
    if not np.isfinite(requested).all() or not np.isfinite(frame).all():
        raise ValueError('Non-finite arm target/frame')
    bounds = np.deg2rad(np.array([limits[name] for name in DOF_NAMES], float))
    if bounds.shape != (4, 2) or not np.isfinite(bounds).all() or np.any(bounds[:, 0] >= bounds[:, 1]):
        raise ValueError('Invalid arm DOF bounds')
    world = rig.fk(quats, translations)
    origin = world[:, a, :3, 3]
    local = np.einsum('tji,tj->ti', frame, requested-origin)
    rest = rig.rest_world[:, :3, 3]
    upper_rest, lower_rest = rest[b]-rest[a], rest[c]-rest[b]
    l1, l2 = np.linalg.norm(upper_rest), np.linalg.norm(lower_rest)
    length = l1+l2
    if min(l1, l2) < 1e-8:
        raise ValueError('Zero-length arm bone')
    workspace = local.copy()
    workspace[:, 2] = np.maximum(workspace[:, 2], -limits['target_back_ratio']*length)
    solved, successful = [], []
    regularization = float(limits['regularization'])
    preferred_all = np.broadcast_to(np.clip(np.deg2rad(np.asarray(preferred_swivel, float)), bounds[2, 0], bounds[2, 1]), (count,))
    for target, preferred in zip(workspace/length, preferred_all):
        direction = unit(target)
        flex = np.arctan2(direction[2], -direction[1])
        abduct = np.arcsin(np.clip((1 if side == 'L' else -1)*direction[0], -1., 1.))
        distance = np.linalg.norm(target)*length
        elbow = np.arccos(np.clip((distance*distance-l1*l1-l2*l2)/(2*l1*l2), -1., 1.))
        initial = np.clip([flex-elbow*l2/length, abduct, preferred, elbow], bounds[:, 0]+1e-7, bounds[:, 1]-1e-7)
        def residual(angles, preferred=preferred):
            upper, lower, _ = arm_vectors(angles, side)
            endpoint = (l1*upper+l2*lower)/length
            return np.r_[endpoint-target, regularization*(angles[2]-preferred)]
        solution = least_squares(residual, initial, bounds=(bounds[:, 0], bounds[:, 1]),
                                 max_nfev=int(limits['max_nfev']), ftol=1e-10, xtol=1e-10, gtol=1e-10)
        solved.append(solution.x)
        successful.append(bool(solution.success))
    angles = np.asarray(solved)
    upper, lower, bend = arm_vectors(angles, side)
    upper_world = np.einsum('tij,tj->ti', frame, upper)
    lower_world = np.einsum('tij,tj->ti', frame, lower)
    bend_world = np.einsum('tij,tj->ti', frame, bend)
    rest_bend = lower_rest-np.dot(lower_rest, unit(upper_rest))*unit(upper_rest)
    if np.linalg.norm(rest_bend) < 1e-7:
        rest_bend = frame[0, :, 2]
    normal = unit(np.cross(upper_world, bend_world))
    rest_normal = unit(np.cross(upper_rest, rest_bend))
    for joint, old, new in [(a, upper_rest, upper_world), (b, lower_rest, lower_world)]:
        tangent = np.cross(normal, new)
        rest_tangent = np.cross(rest_normal, old)
        rotation = _basis(new, tangent) @ _basis(old, rest_tangent).T
        set_world_rotation(rig, quats, translations, joint, rotation @ rig.rest_world[joint, :3, :3])
    final = rig.fk(quats, translations)
    actual = final[:, c, :3, 3]
    dofs = measure_arm_dofs(final, chain, frame, side)
    violation = np.maximum(bounds[:, 0]-np.deg2rad(dofs), np.deg2rad(dofs)-bounds[:, 1])
    return {'residual': np.linalg.norm(actual-requested, axis=-1), 'flex_degrees': dofs[:, 3],
            'dofs_degrees': dofs, 'workspace_correction': np.linalg.norm(workspace-local, axis=-1),
            'limited_targets': actual.copy(), 'solver_success': np.asarray(successful),
            'limit_violation_degrees': np.rad2deg(np.maximum(violation, 0.)).max(axis=1)}
