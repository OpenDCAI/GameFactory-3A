"""Measure generated geometry against explicitly supplied quality thresholds."""
from __future__ import annotations

import numpy as np
from .units import fk


def metrics(result, *, quality):
    clip = result.clip
    pos, _ = fk(clip)
    parents = clip.template.parents
    joints = np.flatnonzero(parents >= 0)
    rest = clip.template.rest
    lengths = np.linalg.norm(pos[:, joints] - pos[:, parents[joints]], axis=-1)
    reference = np.linalg.norm(rest[joints] - rest[parents[joints]], axis=-1)
    bone_error = float(np.max(np.abs(lengths - reference))) if len(joints) else 0.0
    speed = np.linalg.norm(np.diff(pos, axis=0), axis=-1) * clip.fps
    contact_speeds = []
    for key, ids in result.contact_ids.items():
        both = (ids[1:] >= 0) & (ids[1:] == ids[:-1])
        contact_speeds.extend(speed[both, result.target_joints[key]].tolist())
    target_error = max((float(np.max(v)) for v in result.residuals.values()), default=0.0)
    penetration = float(max(0, quality['ground_height'] - pos[..., 1].min()))
    limit_error = max((float(np.max(v['limit_violation_degrees'])) for v in result.diagnostics.values()), default=0.0)
    solver_failures = sum(int(np.count_nonzero(~v['solver_success'])) for v in result.diagnostics.values() if 'solver_success' in v)
    contact_max = max(contact_speeds, default=0.0)
    failures = []
    for failed, label in ((bone_error > quality['bone_length_tolerance'], 'bone_length'),
                          (target_error > quality['target_tolerance'], 'ik_target'),
                          (contact_max > quality['contact_tolerance'], 'foot_skate'),
                          (penetration > quality['ground_tolerance'], 'ground'),
                          (limit_error > quality['limit_tolerance'], 'joint_limits'),
                          (solver_failures > 0, 'ik_solver')):
        if failed:
            failures.append(label)
    return {'bone_error_max': bone_error, 'target_error_max': target_error,
            'ik_residual_max': target_error, 'ground_penetration': penetration,
            'contact_speed_max': contact_max,
            'contact_speed_mean': float(np.mean(contact_speeds)) if contact_speeds else None,
            'contact_pairs': len(contact_speeds), 'joint_speed_max': float(speed.max()),
            'limit_violation_degrees': limit_error, 'solver_failed_frames': solver_failures,
            'thresholds': dict(quality), 'failures': failures}
