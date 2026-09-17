from __future__ import annotations
import numpy as np
from .validation import check_self_collision, validate_clip
from .units import fk

def metrics(clip, plan, *, contacts=None, targets=None) -> dict[str, object]:
    pos, _ = fk(clip)
    parents = np.asarray(plan.template.parents)
    js = np.flatnonzero(parents >= 0)
    lengths = np.linalg.norm(pos[:, js] - pos[:, parents[js]], axis=-1)
    rest = np.linalg.norm(plan.template.rest[js] - plan.template.rest[parents[js]], axis=-1)
    speed = np.linalg.norm(np.diff(pos, axis=0), axis=-1) * clip.fps
    values = []
    active = 0
    for role in plan.support_roles:
        tip = plan.roles[role].joints[-1]
        contact = pos[:, tip, 1] <= plan.ground + 0.01 * plan.support_length
        if contacts is not None:
            contact = np.asarray(contacts[role], bool)
        both = contact[1:] & contact[:-1]
        active += int(both.sum())
        values.extend(speed[both, tip].tolist())
    residual = []
    for role, target in (targets or {}).items():
        residual.extend(np.linalg.norm(pos[:, plan.roles[role].joints[-1]] - target, axis=-1).tolist())
    report = validate_clip(clip, plan)
    collision = check_self_collision(clip, plan, stride=1)
    failures = [f.check for f in report.failures if f.check != 'self_collision']
    if not collision.ok:
        failures.append('self_collision')
    return {'bone_error_max': float(np.max(np.abs(lengths - rest))), 'ground_penetration': float(max(0, plan.ground - pos[..., 1].min())), 'contact_speed_mean': float(np.mean(values)) if values else None, 'contact_pairs': active, 'target_error_max': float(max(residual, default=0)), 'failures': failures}


