"""Build motion curves, rotate joints and solve limb IK constraints."""
from __future__ import annotations
import numpy as np
from .units import MotionClip, SkeletonPlan, between, fk, inverse, quat_from_axis_angle, quat_mul, quat_normalize, quat_to_matrix, root_index, unit

def smooth(t: np.ndarray) -> np.ndarray:
    """Quintic smoothstep, with zero first and second derivatives at both endpoints."""
    t = np.clip(np.asarray(t, dtype=float), 0.0, 1.0)
    return t ** 3 * (10.0 - 15.0 * t + 6.0 * t ** 2)

def curve(times, values, samples, modes=None) -> np.ndarray:
    """Interpolate over strictly increasing key times, supporting non-uniform impact/return curves so speed is not cut off abruptly at a segment boundary."""
    times, values, samples = map(lambda x: np.asarray(x, float), (times, values, samples))
    if times.ndim != 1 or len(times) < 2 or values.shape[0] != len(times):
        raise ValueError('at least two time/value keyframes are required')
    if not all((np.isfinite(x).all() for x in (times, values, samples))) or np.any(np.diff(times) <= 0):
        raise ValueError('key times must be finite and strictly increasing')
    modes = ['smooth'] * (len(times) - 1) if modes is None else list(modes)
    if len(modes) != len(times) - 1 or set(modes) - {'smooth', 'impact', 'return', 'linear'}:
        raise ValueError('the curve names or their count are invalid')
    i = np.clip(np.searchsorted(times, samples, side='right') - 1, 0, len(times) - 2)
    t = np.clip((samples - times[i]) / (times[i + 1] - times[i]), 0.0, 1.0)
    w = np.empty_like(t)
    for k, mode in enumerate(modes):
        mask = i == k
        v = t[mask]
        if mode == 'impact':
            v = v ** 2
        elif mode == 'return':
            v = 1.0 - (1.0 - v) ** 2
        w[mask] = v if mode == 'linear' else smooth(v)
    w = w.reshape(w.shape + (1,) * (values.ndim - 1))
    return values[i] + (values[i + 1] - values[i]) * w

def rotate_joint(clip: MotionClip, joint: int, axis, degrees, *, space='parent') -> None:
    """Apply an incremental rotation across all frames. ``local`` post-multiplies about the joint's own axis, while ``parent``/``world``/``body`` convert to the parent axis and pre-multiply."""
    if not 0 <= joint < clip.num_joints:
        raise ValueError('joint index is out of range')
    axis = np.broadcast_to(np.asarray(axis, float), (clip.num_frames, 3))
    degrees = np.broadcast_to(np.asarray(degrees, float), (clip.num_frames,))
    if not np.isfinite(axis).all() or not np.isfinite(degrees).all() or np.any(np.linalg.norm(axis, axis=-1) < 1e-10):
        raise ValueError('the rotation axis and angle must be finite, and the axis non-zero')
    if space in ('body', 'world'):
        _, global_q = fk(clip)
        if space == 'body':
            axis = np.einsum('tij,tj->ti', quat_to_matrix(global_q[:, root_index(clip.template)]), axis)
        parent = int(clip.template.parents[joint])
        if parent >= 0:
            axis = np.einsum('tji,tj->ti', quat_to_matrix(global_q[:, parent]), axis)
    elif space not in ('local', 'parent'):
        raise ValueError('space must be local/parent/body/world')
    dq = quat_from_axis_angle(axis, np.deg2rad(degrees))
    old = clip.quats[:, joint]
    clip.quats[:, joint] = quat_normalize(quat_mul(old, dq) if space == 'local' else quat_mul(dq, old))

def solve_two_bone(clip: MotionClip, joints, target, pole) -> np.ndarray:
    """Two-bone IK holding the root position and bone lengths fixed, with ``pole`` as a world direction fixing the knee/elbow plane. Returns the residual between the reached tip and the requested target."""
    if len(joints) != 3 or len(set(joints)) != 3:
        raise ValueError('IK needs three distinct joints')
    a, b, c = map(int, joints)
    parents = np.asarray(clip.template.parents)
    if min(a, b, c) < 0 or max(a, b, c) >= clip.num_joints or parents[b] != a or (parents[c] != b):
        raise ValueError('the IK joints must form a continuous parent-child chain')
    target = np.broadcast_to(np.asarray(target, float), (clip.num_frames, 3))
    pole = np.broadcast_to(np.asarray(pole, float), target.shape)
    if not np.isfinite(target).all() or not np.isfinite(pole).all() or np.any(np.linalg.norm(pole, axis=-1) < 1e-10):
        raise ValueError('the IK target and pole must be finite, and the pole non-zero')
    rest = clip.template.rest
    l1, l2 = (np.linalg.norm(rest[b] - rest[a]), np.linalg.norm(rest[c] - rest[b]))
    if min(l1, l2) < 1e-08:
        raise ValueError('IK does not support zero-length bones')
    pos, global_q = fk(clip)
    delta = target - pos[:, a]
    raw_distance = np.linalg.norm(delta, axis=-1)
    direction = unit(np.where((raw_distance > 1e-09)[:, None], delta, pos[:, c] - pos[:, a]))
    if np.any(np.linalg.norm(direction, axis=-1) < 0.5):
        direction = np.where((np.linalg.norm(direction, axis=-1) < 0.5)[:, None], [0.0, 0.0, 1.0], direction)
    eps = (l1 + l2) * 1e-06
    distance = np.clip(raw_distance, abs(l1 - l2) + eps, l1 + l2 - eps)
    lateral = pole - np.sum(pole * direction, axis=-1, keepdims=True) * direction
    fallback = np.eye(3)[np.argmin(np.abs(direction), axis=-1)]
    fallback -= np.sum(fallback * direction, axis=-1, keepdims=True) * direction
    lateral = unit(np.where((np.linalg.norm(lateral, axis=-1) < 1e-08)[:, None], fallback, lateral))
    along = (l1 * l1 - l2 * l2 + distance * distance) / (2.0 * distance)
    height = np.sqrt(np.maximum(l1 * l1 - along * along, 0.0))
    mid = pos[:, a] + along[:, None] * direction + height[:, None] * lateral
    end = pos[:, a] + distance[:, None] * direction
    desired_global = quat_mul(between(pos[:, b] - pos[:, a], mid - pos[:, a]), global_q[:, a])
    clip.quats[:, a] = desired_global if parents[a] < 0 else quat_mul(inverse(global_q[:, parents[a]]), desired_global)
    pos, global_q = fk(clip)
    desired_global = quat_mul(between(pos[:, c] - pos[:, b], end - pos[:, b]), global_q[:, b])
    clip.quats[:, b] = quat_mul(inverse(global_q[:, a]), desired_global)
    final, _ = fk(clip)
    return np.linalg.norm(final[:, c] - target, axis=-1)

def fit_feet(clip: MotionClip, plan: SkeletonPlan, targets: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Solve foot targets given at the toe tip. On a four-joint chain the sole keeps its rest orientation: the hip and knee are solved first, then the ankle."""
    _, global_q = fk(clip)
    heading = global_q[:, root_index(clip.template)].copy()
    rot = quat_to_matrix(heading)
    residuals = {}
    for role, target in targets.items():
        js = plan.roles[role].joints
        if len(js) not in (3, 4):
            raise ValueError(f'{role}: foot IK supports continuous three- or four-joint chains')
        target = np.asarray(target, float)
        offset = clip.template.rest[js[-1]] - clip.template.rest[js[2]]
        ankle_target = target - np.einsum('tij,j->ti', rot, offset)
        pole = np.einsum('tij,j->ti', rot, np.array([0.0, 0.0, 1.0]))
        solve_two_bone(clip, js[:3], ankle_target, pole)
        if len(js) == 4:
            _, q = fk(clip)
            clip.quats[:, js[2]] = quat_mul(inverse(q[:, js[1]]), heading)
        pos, _ = fk(clip)
        residuals[role] = np.linalg.norm(pos[:, js[-1]] - target, axis=-1)
    return residuals

def swing(clip: MotionClip, plan: SkeletonPlan, role: str, *, start_frame: int, end_frame: int, degrees=45.0, axis='swing', ease='smooth', hold_after=False, at=0) -> None:
    """Parameterised swing with an endpoint-smooth curve inside the window and defined behaviour outside it."""
    if not 0 <= start_frame <= end_frame < clip.num_frames:
        raise ValueError('the swing time window is invalid')
    modes = {'smooth': 'smooth', 'linear': 'linear', 'ease_out': 'return', 'ease_in': 'impact', 'impact': 'impact', 'snap': 'return', 'return': 'return'}
    if ease not in modes:
        raise ValueError(f'swing does not support curve {ease!r}')
    t = (np.arange(clip.num_frames) - start_frame) / max(end_frame - start_frame, 1)
    angle = degrees * curve([0, 1], [0, 1], t, [modes[ease]])
    if end_frame == start_frame:
        angle[start_frame:] = degrees
    if not hold_after:
        angle[end_frame + 1:] = 0
    rp = plan.roles[role]
    j = rp.joints[at]
    axes = {'lift': rp.lift_axis, 'swing': rp.swing_axis, 'bend': rp.bend_axis, 'flex': rp.flex_axis, 'twist': rp.twist_axis}
    if isinstance(axis, str) and axis in axes:
        rotate_joint(clip, j, axes[axis], angle)
    elif isinstance(axis, str) and axis in ('right', 'up', 'forward'):
        rotate_joint(clip, j, {'right': [1, 0, 0], 'up': [0, 1, 0], 'forward': [0, 0, 1]}[axis], angle, space='body')
    else:
        rotate_joint(clip, j, axis, angle, space='world')

def strike(clip: MotionClip, plan: SkeletonPlan, role: str, **params) -> None:
    """Smooth two-axis rotation. Curves are smooth/impact/return/linear, and the lateral axis follows the body."""
    allowed = {'start_frame', 'end_frame', 'raise_deg', 'strike_deg', 'recover_deg', 'raise_ratio', 'hold_ratio', 'strike_ratio', 'axis', 'strike_axis', 'raise_ease', 'strike_ease', 'recover_ease', 'hold_after', 'at'}
    if params.keys() - allowed:
        raise ValueError(f'unknown strike parameters: {sorted(params.keys() - allowed)}')
    s, e = (params.get('start_frame', 0), params.get('end_frame', clip.num_frames - 1))
    if not 0 <= s < e < clip.num_frames:
        raise ValueError('the strike time window is out of range or shorter than two frames')
    r1, h, duration = [float(params.get(k, d)) for k, d in (('raise_ratio', 0.34), ('hold_ratio', 0.1), ('strike_ratio', 0.26))]
    if not (r1 > 0 and h >= 0 and (duration > 0) and (r1 + h + duration < 1)):
        raise ValueError('the strike phase ratios are invalid')
    up, down, back = [float(params.get(k, d)) for k, d in (('raise_deg', 92.0), ('strike_deg', 165.0), ('recover_deg', 23.0))]
    if min(up, down, back) < 0 or not np.isfinite([up, down, back]).all():
        raise ValueError('strike angles must be finite and non-negative')
    t = np.clip((np.arange(clip.num_frames) - s) / (e - s), 0, 1)
    raised = curve([0, r1, 1], [0, up, up], t, [params.get('raise_ease', 'smooth'), 'smooth'])
    dropped = curve([0, r1 + h, r1 + h + duration, 1], [0, 0, down, down - back], t, ['smooth', params.get('strike_ease', 'impact'), params.get('recover_ease', 'smooth')])
    if not params.get('hold_after', True):
        raised[e + 1:] = 0
        dropped[e + 1:] = 0
    rp = plan.roles[role]
    j = rp.joints[int(params.get('at', 0))]
    axes = {'lift': rp.lift_axis, 'swing': rp.swing_axis, 'flex': rp.flex_axis, 'twist': rp.twist_axis}

    def apply(axis, angle):
        if isinstance(axis, str) and axis in axes:
            rotate_joint(clip, j, axes[axis], angle)
        elif isinstance(axis, str) and axis == 'right':
            rotate_joint(clip, j, [1, 0, 0], angle, space='body')
        else:
            rotate_joint(clip, j, axis, angle, space='world')
    axis = params.get('axis', 'lift')
    strike_axis = params.get('strike_axis', 'right')
    if strike_axis is None:
        apply(axis, raised - dropped)
    else:
        apply(axis, raised)
        apply(strike_axis, dropped)


