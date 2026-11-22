"""Rest-bend diagnostics, motion twist and bounded pre-bend calibration for limb chains."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

__all__ = ['limb_orientation', 'motion_twist', 'calibrate_limb_prebend']


def limb_orientation(rig, chain, forward, *, near_straight_degrees=12.):
    """Rest-bend diagnostics of a 3-joint parent-child chain against a preferred bend direction."""
    ids = np.asarray(chain)
    if ids.shape != (3,) or ids.dtype.kind not in 'iu' or len(set(ids)) != 3 or np.any((ids < 0) | (ids >= rig.num_joints)):
        raise ValueError('Orientation requires three distinct joint indices')
    a, b, c = ids
    if rig.parents[b] != a or rig.parents[c] != b:
        raise ValueError('Orientation requires a parent-child chain')
    points = np.asarray(rig.joints, float)[ids]
    edges = np.diff(points, axis=0)
    lengths = np.linalg.norm(edges, axis=1)
    if not np.isfinite(points).all() or lengths.min() < 1e-8 or np.linalg.norm(points[2] - points[0]) < 1e-8:
        raise ValueError('Degenerate limb geometry')
    axis = (points[2] - points[0]) / np.linalg.norm(points[2] - points[0])
    forward = np.asarray(forward, float)
    if forward.shape != (3,) or not np.isfinite(forward).all() or np.linalg.norm(forward) < 1e-8:
        raise ValueError('Expected finite nonzero rest-world bend direction')
    preferred = forward / np.linalg.norm(forward)
    preferred -= np.dot(preferred, axis) * axis
    if np.linalg.norm(preferred) < 1e-6:
        raise ValueError('Bend direction parallel to chain')
    preferred /= np.linalg.norm(preferred)
    bend = edges[0] - np.dot(edges[0], axis) * axis
    distance = float(np.linalg.norm(bend))
    cosine = float(np.dot(bend, preferred) / distance) if distance > 1e-8 else None
    flex = float(np.rad2deg(np.arccos(np.clip(np.dot(*edges) / np.prod(lengths), -1., 1.))))
    if not np.isfinite(near_straight_degrees) or not 0 < near_straight_degrees < 180:
        raise ValueError('Invalid near-straight threshold')
    return {'chain': ids.tolist(), 'flex_degrees': flex, 'prebend_distance': distance,
            'prebend_vector': bend.tolist(), 'preferred_direction': preferred.tolist(),
            'alignment': cosine, 'near_straight': flex < near_straight_degrees,
            'reversed': cosine is not None and cosine < 0,
            'ambiguous': bool(distance < 1e-5 * lengths.sum())}


def motion_twist(rig, world, rest_world, chain):
    """Swing/twist decomposition relative to bind axes; diagnostic, not a limit.

    At a 180-degree swing the twist is undefined and reported separately. Absolute world motion includes trunk
    motion; compare under the same action.
    """
    world, rest_world = np.asarray(world, float), np.asarray(rest_world, float)
    if world.ndim != 4 or world.shape[1:] != (rig.num_joints, 4, 4) or rest_world.shape != (rig.num_joints, 4, 4):
        raise ValueError('Expected joint-ordered world (T,J,4x4) and bind (J,4x4)')
    if not len(world) or not np.isfinite(world).all() or not np.isfinite(rest_world).all():
        raise ValueError('Motion transforms must be finite and nonempty')
    result = {}
    for j, child in zip(chain[:-1], chain[1:]):
        axis = rig.joints[child] - rig.joints[j]
        axis /= np.linalg.norm(axis)
        delta = world[:, j, :3, :3] @ rest_world[j, :3, :3].T
        q = Rotation.from_matrix(delta).as_quat()
        component = q[:, :3] @ axis
        valid = np.hypot(component, q[:, 3]) > 1e-8
        angle = np.rad2deg(2 * np.arctan2(np.abs(component[valid]), np.abs(q[valid, 3])))
        steps = ((Rotation.from_matrix(delta[1:]) * Rotation.from_matrix(delta[:-1]).inv()).magnitude()
                 if len(delta) > 1 else np.array([0.]))
        result[rig.joint_names[j]] = {'twist_p95_degrees': float(np.quantile(angle, .95)) if len(angle) else None,
                                      'undefined_samples': int((~valid).sum()),
                                      'max_rotation_step_degrees': float(np.rad2deg(steps).max())}
    return result


def calibrate_limb_prebend(joints, reference, chain, *, forward, proximal_blend=0., middle_blend=0.,
                           prebend_ratio=0.05, max_displacement_ratio=0.25, max_length_change_ratio=0.25):
    """Locally adjust a nearly straight 3-joint limb; keep its tip and all other joints.

    Ratios use the original two-segment length. ``reference`` is a soft guide (blended with ``proximal_blend`` /
    ``middle_blend``), not surface ground truth. No mesh projection is performed. Callers must recheck enclosure and
    recompute bind matrices before animating the result.
    """
    joints, reference = np.asarray(joints, float), np.asarray(reference, float)
    chain = np.asarray(chain)
    if joints.ndim != 2 or joints.shape[1] != 3 or reference.shape != joints.shape or not np.isfinite([joints, reference]).all():
        raise ValueError('Expected matching finite (J,3) joint arrays')
    if chain.shape != (3,) or chain.dtype.kind not in 'iu' or len(set(chain)) != 3 or np.any((chain < 0) | (chain >= len(joints))):
        raise ValueError('Expected three distinct valid joint indices')
    ratios = [proximal_blend, middle_blend, prebend_ratio, max_displacement_ratio, max_length_change_ratio]
    if not np.isfinite(ratios).all() or not 0 <= proximal_blend <= 1 or not 0 <= middle_blend <= 1:
        raise ValueError('Invalid finite blend ratios')
    if not 0 < prebend_ratio < .25 or not 0 < max_displacement_ratio < .5 or not 0 < max_length_change_ratio < .5:
        raise ValueError('Invalid pre-bend or correction budgets')
    direction = np.asarray(forward, float)
    if direction.shape != (3,) or not np.isfinite(direction).all() or np.linalg.norm(direction) < 1e-8:
        raise ValueError('forward must be a finite nonzero vector')
    a, b, c = chain
    original_lengths = np.linalg.norm(np.diff(joints[chain], axis=0), axis=1)
    if original_lengths.min() < 1e-8:
        raise ValueError('Degenerate limb')
    scale = float(original_lengths.sum())
    updated = joints.copy()
    updated[a] += proximal_blend * (reference[a] - joints[a])
    middle = joints[b] + middle_blend * (reference[b] - joints[b])
    axis = joints[c] - updated[a]
    length = np.linalg.norm(axis)
    if length < 1e-8:
        raise ValueError('Coincident limb endpoints')
    axis /= length
    bend = direction / np.linalg.norm(direction)
    bend -= np.dot(bend, axis) * axis
    if np.linalg.norm(bend) < 1e-6:
        raise ValueError('forward parallel to limb')
    bend /= np.linalg.norm(bend)
    along = float(np.dot(middle - updated[a], axis))
    if not 0 < along < length:
        raise ValueError('Middle joint projects outside limb')
    updated[b] = updated[a] + along * axis + prebend_ratio * scale * bend
    displacement = np.linalg.norm(updated - joints, axis=1)
    lengths = np.linalg.norm(np.diff(updated[chain], axis=0), axis=1)
    if displacement.max() > max_displacement_ratio * scale:
        raise ValueError('Joint correction exceeds displacement budget')
    if np.max(np.abs(lengths / original_lengths - 1)) > max_length_change_ratio:
        raise ValueError('Joint correction exceeds bone-length budget')
    old_axis = (joints[c] - joints[a]) / np.linalg.norm(joints[c] - joints[a])
    old_bend = joints[b] - joints[a]
    old_bend -= np.dot(old_bend, old_axis) * old_axis
    return updated, {'chain': chain.tolist(), 'before': joints[chain].tolist(), 'after': updated[chain].tolist(),
                     'displacement': displacement[chain].tolist(), 'original_lengths': original_lengths.tolist(),
                     'updated_lengths': lengths.tolist(), 'prebend_distance': prebend_ratio * scale,
                     'old_prebend_vector': old_bend.tolist(), 'new_prebend_vector': (prebend_ratio * scale * bend).tolist(),
                     'rest_pole': bend.tolist(), 'surface_projection': False}
