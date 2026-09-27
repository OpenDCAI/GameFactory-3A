"""Design root, contact, child-joint and end-effector trajectories before solving IK."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from .generate import solve_arm, solve_two_bone
from .timing import curve, smooth
from .units import basis, fk, quat_to_matrix, root_index, set_world_rotation


def design_root(plan, times):
    p = plan.program['root']['params']
    offsets = np.column_stack([curve(p[k], plan.rhythm, times, width=0) for k in ('x', 'y', 'z')])
    yaw = curve(p['yaw'], plan.rhythm, times, width=0)
    heading = plan.frame @ Rotation.from_euler('y', yaw, degrees=True).as_matrix()
    position = plan.template.rest[root_index(plan.template)] + offsets @ plan.frame.T * p['scale']
    return {'position': position, 'heading': heading, 'rotation': heading @ plan.frame.T, 'yaw': yaw}


def _position_frame(clip, plan, chain, root, space):
    count = clip.num_frames
    if space == 'world':
        return np.zeros((count, 3)), np.broadcast_to(np.eye(3), (count, 3, 3))
    if space == 'root':
        return root['position'], root['heading']
    positions, quats = fk(clip)
    parent = clip.template.parents[chain[0]]
    frame = root['heading'] if parent < 0 else quat_to_matrix(quats[:, parent]) @ plan.frame
    return positions[:, chain[0]], frame


def _contact_path(plan, spec, times, root):
    p, chain = spec['params'], spec['joints']
    rhythm = plan.rhythm
    windows = rhythm.windows(p['contacts'])
    anchor_times = np.asarray([rhythm.fraction(v) * rhythm.duration for v in p['anchor_times']])
    sampled = design_root(plan, anchor_times)
    offset = plan.template.rest[chain[-1]] - plan.template.rest[root_index(plan.template)]
    anchors = sampled['position'] + np.einsum('tij,j->ti', sampled['rotation'], offset)
    anchors[:, 1] = p['ground_height']
    target = np.broadcast_to(anchors[0], (len(times), 3)).copy()
    yaw = np.full(len(times), sampled['yaw'][0])
    fraction = times / rhythm.duration
    contact_ids = rhythm.contact_ids(times, p['contacts'])
    for index, (_, b) in enumerate(windows):
        mask = contact_ids == index
        target[mask], yaw[mask] = anchors[index], sampled['yaw'][index]
        if index == len(windows) - 1:
            continue
        end = windows[index + 1, 0]
        phase = np.clip((fraction - b) / (end - b), 0, 1)
        blend = smooth(phase)
        path = (1 - blend[:, None]) * anchors[index] + blend[:, None] * anchors[index + 1]
        path += np.einsum('tij,j->ti', root['heading'], p['arcs'][index]) * p['scale'] * np.sin(np.pi * phase)[:, None] ** 2
        mask = (contact_ids < 0) & (fraction > b) & (fraction < end)
        target[mask] = path[mask]
        yaw[mask] = ((1 - blend) * sampled['yaw'][index] + blend * sampled['yaw'][index + 1])[mask]
    rotation = plan.frame @ Rotation.from_euler('y', yaw, degrees=True).as_matrix() @ plan.frame.T
    return target, contact_ids, rotation


def solve_part(clip, plan, spec, times, root):
    """Position/contact IK poles use root-body axes; aim poles use target space.

    rest_pole always specifies the original world-space rest basis. Neither pole
    is a world-space point. These conventions match the positional limb model.
    """
    op, p, chain = spec['operator'], spec['params'], spec['joints']
    epsilon = plan.program['solver']['epsilon']
    if op == 'rest':
        return {}, {}, {}
    if op == 'aim':
        origin, frame = _position_frame(clip, plan, chain, root, p['space'])
        targets = [origin + np.einsum('tij,tj->ti', frame, curve(track, plan.rhythm, times, width=3) * p['scale'])
                   for track in p['targets']]
        pole = np.einsum('tij,j->ti', frame, p['pole'])
        tracks, diagnostics = {}, {}
        for joint, child, target in zip(chain, chain[1:], targets):
            positions, _ = fk(clip)
            direction = target - positions[:, joint]
            rest_direction = plan.template.rest[child] - plan.template.rest[joint]
            rotation = basis(direction, pole, epsilon=epsilon) @ basis(rest_direction, p['rest_pole'], epsilon=epsilon).T
            set_world_rotation(clip, joint, rotation)
            tracks[child] = target
        return tracks, {}, diagnostics
    if op == 'arm_arc':
        _, frame = _position_frame(clip, plan, chain, root, 'parent')
        positions, _ = fk(clip)
        angle = np.deg2rad(curve(p['angle'], plan.rhythm, times, width=0))
        radius = curve(p['radius'], plan.rhythm, times, width=0)
        lateral = curve(p['lateral'], plan.rhythm, times, width=0)
        sagittal = np.sqrt(radius * radius - lateral * lateral)
        sign = 1 if spec['side'] == 'L' else -1
        relative = p['scale'] * np.column_stack([sign * lateral, sagittal * np.cos(angle), sagittal * np.sin(angle)])
        target = positions[:, chain[0]] + np.einsum('tij,tj->ti', frame, relative)
        diagnostics = solve_arm(clip, chain, target, frame, side=spec['side'], limits=p['limits'],
                                preferred_swivel=p['preferred_swivel_degrees'], rest_pole=p['rest_pole'], epsilon=epsilon)
        return {chain[-1]: target}, {}, diagnostics
    if op == 'contact_path':
        target, contact, contact_rotation = _contact_path(plan, spec, times, root)
    else:
        origin, frame = _position_frame(clip, plan, chain, root, p['space'])
        target = origin + np.einsum('tij,tj->ti', frame, curve(p['trajectory'], plan.rhythm, times, width=3) * p['scale'])
        contact = plan.rhythm.contact_ids(times, p['contacts'])
        contact_rotation = None
    if isinstance(p['orientation'], dict):
        yaw = curve(p['orientation'], plan.rhythm, times, width=0)
        rotation = plan.frame @ Rotation.from_euler('y', yaw, degrees=True).as_matrix() @ plan.frame.T
    elif p['orientation'] == 'body':
        rotation = root['rotation']
    elif p['orientation'] == 'rest':
        rotation = np.broadcast_to(np.eye(3), (len(times), 3, 3))
    else:
        rotation = contact_rotation
    ankle_offset = plan.template.rest[chain[-1]] - plan.template.rest[chain[2]]
    ankle_target = target - np.einsum('tij,j->ti', rotation, ankle_offset)
    pole = np.einsum('tij,j->ti', root['heading'], p['pole'])
    diagnostics = solve_two_bone(clip, chain[:3], ankle_target, pole,
                                 flexion=p['flexion'], rest_pole=p['rest_pole'], epsilon=epsilon)
    set_world_rotation(clip, chain[2], rotation)
    positions, _ = fk(clip)
    diagnostics['residual'] = np.linalg.norm(positions[:, chain[-1]] - target, axis=-1)
    return {chain[-1]: target}, {chain[-1]: contact}, diagnostics
