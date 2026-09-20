"""Generate motions from normalized-time root, joint and limb-target tracks."""
from __future__ import annotations

from copy import deepcopy
import numpy as np

from .generate import curve, fit_feet, rotate_joint, solve_two_bone
from .units import MotionClip, fk, quat_to_matrix, root_index


def _numbers(value, shape, label):
    raw = np.asarray(value)
    if raw.shape != shape or raw.dtype.kind not in 'iuf' or not np.isfinite(raw).all():
        raise ValueError(f'{label} must contain finite numbers with shape {shape}')
    return raw.astype(float)


def _validate_curve(track, width, n, label):
    if not isinstance(track, dict):
        raise ValueError(f'{label} must be an object')
    times = np.asarray(track.get('times'))
    if times.ndim != 1 or len(times) < 2:
        raise ValueError(f'{label}.times needs at least two keys')
    times = _numbers(track['times'], times.shape, f'{label}.times')
    if times[0] != 0 or times[-1] != 1 or np.any(np.diff(times) * (n - 1) < 1):
        raise ValueError(f'{label}.times must span [0,1] with at least one frame between keys')
    shape = (len(times), width) if width else (len(times),)
    _numbers(track.get('values'), shape, f'{label}.values')
    curve(times, track['values'], np.array([0.]), track.get('modes'))


def validate_task_space(parameters, n):
    """Validate track data before skeleton fitting."""
    if type(parameters['plant_feet']) is not bool:
        raise ValueError('plant_feet must be a boolean')
    for key, width in (('root_positions', 3), ('root_yaw', 0)):
        track = parameters[key]
        if track is not None:
            _validate_curve(track, width, n, key)
            if track.keys() - {'times', 'values', 'modes'}:
                raise ValueError(f'{key} has unknown fields')
    for key in ('rotations', 'targets'):
        tracks = parameters[key]
        if not isinstance(tracks, list):
            raise ValueError(f'{key} must be a list')
        for index, track in enumerate(tracks):
            label = f'{key}[{index}]'
            _validate_curve(track, 0 if key == 'rotations' else 3, n, label)
            allowed = {'role', 'times', 'values', 'modes'} | ({'at', 'axis'} if key == 'rotations' else {'pole'})
            if track.keys() - allowed:
                raise ValueError(f'{label} has unknown fields')
            if not isinstance(track.get('role'), str) or not track['role']:
                raise ValueError(f'{label}.role must name a skeleton role')
            vector = 'axis' if key == 'rotations' else 'pole'
            value = _numbers(track.get(vector), (3,), f'{label}.{vector}')
            if np.linalg.norm(value) < 1e-8:
                raise ValueError(f'{label}.{vector} cannot be zero')
            if key == 'rotations' and (type(track.get('at')) is not int or track['at'] < 0):
                raise ValueError(f'{label}.at must be a non-negative integer')
    if not any(parameters[key] for key in ('rotations', 'targets', 'root_positions', 'root_yaw')):
        raise ValueError('task_space needs at least one motion track')


def generate_task_space(plan, *, num_frames, fps, heading_deg, parameters):
    """Evaluate rotations before IK; targets use chain lengths in root-body axes.

    Root positions use skeleton scale. Planted feet retain their rest positions.
    IK targets address non-support limb tips relative to their shoulder/chain root.
    """
    p = parameters
    root = root_index(plan.template)
    controlled = set()
    targets = []
    for track in p['targets']:
        role = track['role']
        if role not in plan.template.limb_roles or role in plan.support_roles:
            raise ValueError(f'{role}: targets require a non-support limb')
        js = plan.roles[role].joints[-3:]
        if len(js) != 3 or any(plan.template.parents[b] != a for a, b in zip(js, js[1:])):
            raise ValueError(f'{role}: targets need a continuous three-joint chain')
        if controlled.intersection(js):
            raise ValueError('target chains must not overlap')
        controlled.update(js)
        targets.append((track, js))
    for _, js in targets:
        ancestor = int(plan.template.parents[js[0]])
        while ancestor >= 0:
            if ancestor in controlled:
                raise ValueError('target chains must not depend on another target chain')
            ancestor = int(plan.template.parents[ancestor])
    if p['plant_feet'] and (not plan.support_roles or any(len(plan.roles[r].joints) not in (3, 4) for r in plan.support_roles)):
        raise ValueError('plant_feet needs support chains with three or four joints')
    planted = {j for r in plan.support_roles for j in plan.roles[r].joints} if p['plant_feet'] else set()
    rotations = []
    for track in p['rotations']:
        role, at = track['role'], track['at']
        if role not in plan.roles or at >= len(plan.roles[role].joints):
            raise ValueError(f'{role}: joint index is outside the role')
        joint = plan.roles[role].joints[at]
        if joint == root or joint in controlled or joint in planted:
            raise ValueError('rotation track conflicts with root or IK control')
        rotations.append((track, joint))
    clip = MotionClip.rest_clip(plan.template, num_frames, fps=fps)
    baseline = clip.copy()
    u = np.linspace(0, 1, num_frames)
    def sample(track):
        return curve(track['times'], track['values'], u, track.get('modes'))
    yaw = heading_deg + (sample(p['root_yaw']) if p['root_yaw'] is not None else 0)
    rotate_joint(clip, root, [0, 1, 0], yaw)
    root_rotation = quat_to_matrix(clip.quats[:, root])
    angle = np.deg2rad(heading_deg)
    heading = np.array([[np.cos(angle), 0, np.sin(angle)], [0, 1, 0], [-np.sin(angle), 0, np.cos(angle)]])
    if p['root_positions'] is not None:
        clip.trans[:] = (sample(p['root_positions']) * plan.scale) @ heading.T
    for track, joint in rotations:
        rotate_joint(clip, joint, track['axis'], sample(track), space='body')
    feet, hands, contacts, residuals = {}, {}, {}, {}
    if p['plant_feet']:
        center = plan.template.rest[root]
        for role in plan.support_roles:
            tip = plan.roles[role].joints[-1]
            position = (plan.template.rest[tip] - center) @ heading.T + center
            feet[role] = np.broadcast_to(position, (num_frames, 3)).copy()
            contacts[role] = np.ones(num_frames, bool)
        residuals.update(fit_feet(clip, plan, feet))
    for track, js in targets:
        position, _ = fk(clip)
        length = sum(np.linalg.norm(plan.template.rest[b] - plan.template.rest[a]) for a, b in zip(js, js[1:]))
        target = position[:, js[0]] + np.einsum('tij,tj->ti', root_rotation, sample(track) * length)
        pole = np.einsum('tij,j->ti', root_rotation, track['pole'])
        role = track['role']
        residuals[role] = solve_two_bone(clip, js, target, pole)
        hands[role] = target
    positions, _ = fk(clip)
    for role in plan.support_roles:
        if role not in contacts:
            contacts[role] = positions[:, plan.roles[role].joints[-1], 1] <= plan.ground + .01 * plan.support_length
    return dict(clip=clip, baseline=baseline, parameters=deepcopy(p), contacts=contacts,
                foot_targets=feet, hand_targets=hands, residuals=residuals)
