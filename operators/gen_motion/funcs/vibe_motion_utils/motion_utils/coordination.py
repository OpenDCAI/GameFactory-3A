"""Per-part body/head/tail rotations, bone aiming, joint translations and polar tracks; no special part IDs."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from .dynamics import _align
from .kinematics import set_world_rotation, unit
from .program import ease, sample_curve
from .timing import interval, smooth

IK_OPERATORS = ('effector', 'arm_arc', 'foot_swing', 'contact_path')


def _rotating_parts(style):
    """rotation parts plus spring parts that carry a driven rotation (any of pitch/twist/roll), plus aim parts."""
    parts = dict(style.program.select('rotation'))
    for key, spec in style.program.select('spring').items():
        if any(k in spec['params'] for k in ('pitch', 'twist', 'roll')):
            parts[key] = spec
    parts.update(style.program.select('aim'))
    return parts


def ik_joints(style, blocks):
    return {j for key, spec in style.program.parts.items() if spec['operator'] in IK_OPERATORS
            for j in blocks.parts[key].joints}


def under_ik(rig, joints, ik):
    """True when any joint of the list has an ancestor that an IK operator re-orients."""
    for j in joints:
        a = int(rig.parents[j])
        while a >= 0:
            if a in ik:
                return True
            a = int(rig.parents[a])
    return False


def split_rotation_parts(rig, blocks, style):
    """(pre-IK keys, post-IK keys). Non-parent-space rotations / driven springs and aims below an IK-driven limb
    (and aims at a joint target) are applied after IK so the orientation they describe survives the solve;
    parent-space rotations keep their parent-relative meaning either way and stay before IK."""
    ik = ik_joints(style, blocks)
    pre, post = [], []
    for key, spec in _rotating_parts(style).items():
        p, js = spec['params'], blocks.parts[key].joints
        if spec['operator'] == 'aim':
            late = under_ik(rig, js, ik) or 'joint' in p.get('target', {})
        else:
            late = p.get('space', 'parent') != 'parent' and under_ik(rig, js, ik)
        (post if late else pre).append(key)
    return pre, post


def reference_frame(space, rig, blocks, root, world, joint, count):
    """(T,3,3) map from body axes to world for a rotation space at `joint` (parent space = the joint's parent; for
    the skeleton root joint parent space means the root body frame (root yaw / pitch / roll), not the static
    scene node above it)."""
    if space == 'parent' and rig.parents[joint] >= 0 and joint != blocks.root:
        parent = int(rig.parents[joint])
        return world[:, parent, :3, :3] @ rig.rest_world[parent, :3, :3].T @ blocks.frame
    if space == 'world':
        return np.broadcast_to(blocks.frame, (count, 3, 3))
    if space == 'heading':
        return root['heading']
    return root.get('body', root['heading'])


def euler_matrices(p, rhythm, style, times, weight=1.):
    zero = np.zeros(len(times))
    values = np.column_stack([sample_curve(p[k], rhythm, style, times) if k in p else zero for k in ('twist', 'pitch', 'roll')])
    return Rotation.from_euler('YXZ', values*weight, degrees=True).as_matrix()


def _slerp_dirs(a, b, w):
    """per-row slerp of unit vectors a -> b by w (T,)."""
    a, b = unit(a), unit(b)
    dot = np.clip(np.sum(a*b, -1), -1., 1.)
    ang = np.arccos(dot)
    small = ang < 1e-6
    s = np.where(small, 1., np.sin(ang))
    wa = np.where(small, 1-w, np.sin((1-w)*ang)/s)
    wb = np.where(small, w, np.sin(w*ang)/s)
    out = wa[:, None]*a+wb[:, None]*b
    opposite = dot < -1+1e-6
    if np.any(opposite):
        axis = unit(np.cross(a[opposite], np.where(np.abs(a[opposite][:, 1:2]) < .9, [[0., 1, 0]], [[1., 0, 0]])))
        th = (w[opposite]*np.pi)[:, None]
        out[opposite] = a[opposite]*np.cos(th)+np.cross(axis, a[opposite])*np.sin(th)
    return unit(out)


def aim_directions(p, rhythm, times, n):
    """(T, n, 3) body-axis key directions (NaN rows for null bones)."""
    keys = [(rhythm.seconds(k['t']), k) for k in p['keys']]
    out = np.full((len(times), n, 3), np.nan)
    for i in range(n):
        if keys[0][1]['dirs'][i] is None:
            continue
        d = np.tile(unit(np.asarray(keys[0][1]['dirs'][i], float)), (len(times), 1))
        for (t0, k0), (t1, k1) in zip(keys, keys[1:]):
            mask = times > t0
            w = ease(interval(times, t0, t1), k1.get('ease', 'smooth'))
            seg = _slerp_dirs(np.tile(unit(np.asarray(k0['dirs'][i], float)), (len(times), 1)),
                              np.tile(unit(np.asarray(k1['dirs'][i], float)), (len(times), 1)), w)
            d[mask] = seg[mask]
        out[:, i] = d
    return out


def _partial(rotations, w):
    """rotation matrices scaled toward identity by per-frame weights w in [0, 1]."""
    if np.allclose(w, 1.):
        return rotations
    return Rotation.from_rotvec(Rotation.from_matrix(rotations).as_rotvec()*np.clip(w, 0., 1.)[:, None]).as_matrix()


def _aim_child(rig, blocks, js, i, p, names):
    if i+1 < len(js):
        return js[i+1]
    if 'child' in p:
        if p['child'] not in names:
            raise ValueError(f'aim child {p["child"]!r} not found')
        c = names.index(p['child'])
        if rig.parents[c] != js[-1]:
            raise ValueError(f'aim child {p["child"]!r} is not a child of the last aimed joint')
        return c
    kids = np.flatnonzero(np.asarray(rig.parents) == js[-1])
    if not len(kids):
        raise ValueError('aim: the last joint has no child bone; aim a shorter chain or give child')
    return int(kids[0])


def _aim_target(p, rig, blocks, root, world, names, count):
    t = p['target']
    leg = blocks.leg_length
    if 'joint' in t:
        if t['joint'] not in names:
            raise ValueError(f'aim target joint {t["joint"]!r} not found')
        pos = world[:, names.index(t['joint']), :3, 3]
        return pos+np.einsum('tij,j->ti', root['heading'], np.asarray(t.get('offset', [0, 0, 0]), float)*leg)
    return None


def aim_point(p, rhythm, style, rig, blocks, root, world, times):
    t = p['target']
    leg = blocks.leg_length
    if isinstance(t['point'], dict):
        local = np.column_stack([sample_curve(t['point'][k], rhythm, style, times) if k in t['point'] else np.zeros(len(times))
                                 for k in ('x', 'y', 'z')])*leg
    else:
        local = np.tile(np.asarray(t['point'], float)*leg, (len(times), 1))
    frame = t.get('frame', 'heading')
    if frame == 'world':
        return blocks.rest[blocks.root]+local @ blocks.frame.T
    if frame == 'heading':
        return root['position']+np.einsum('tij,tj->ti', root['heading'], local)
    return world[:, blocks.root, :3, 3]+np.einsum('tij,tj->ti', root.get('body', root['heading']), local)


def apply_body_rotations(rig, blocks, rhythm, style, times, root, quats, translations, keys=None):
    """rotation / driven-spring / aim parts (all, or only `keys`) in hierarchy order."""
    names = [node.get('name', f'node_{i}') for i, node in enumerate(rig.nodes)]
    count = len(times)
    jobs = {}
    for key, spec in _rotating_parts(style).items():
        if keys is not None and key not in keys:
            continue
        p = spec['params']
        js = blocks.parts[key].joints
        if spec['operator'] == 'aim':
            n = len(js)
            data = {'p': p, 'js': js, 'dirs': aim_directions(p, rhythm, times, n) if 'keys' in p else None,
                    'twist': sample_curve(p['twist'], rhythm, style, times) if 'twist' in p else None,
                    'tw': np.asarray(p.get('twist_weights', [1.]+[0.]*(n-1)), float),
                    'weight': sample_curve(p['weight'], rhythm, style, times) if 'weight' in p else np.ones(count),
                    'point': None}
            for i, j in enumerate(js):
                jobs[j] = ('aim', data, i)
            continue
        values = np.column_stack([sample_curve(p[k], rhythm, style, times) if k in p else np.zeros(count)
                                  for k in ('twist', 'pitch', 'roll')])
        weights = p.get('weights', [1.]*len(spec['joints']))
        for j, weight in zip(js, weights):
            jobs[j] = ('rot', p.get('space', 'parent'), Rotation.from_euler('YXZ', values*weight, degrees=True).as_matrix())
    for j in rig.order:
        if j not in jobs:
            continue
        job = jobs[j]
        needs_world = job[0] == 'aim' or (job[1] == 'parent' and rig.parents[j] >= 0)
        world = rig.fk(quats, translations) if needs_world else None
        if job[0] == 'rot':
            _, space, rotation = job
            reference = reference_frame(space, rig, blocks, root, world, j, count)
            desired = reference @ rotation @ blocks.frame.T @ rig.rest_world[j, :3, :3]
            set_world_rotation(rig, quats, translations, j, desired)
            continue
        _, data, i = job
        p, js = data['p'], data['js']
        child = _aim_child(rig, blocks, js, i, p, names)
        here = world[:, j, :3, 3]
        bone = world[:, child, :3, 3]-here
        if data['dirs'] is not None:
            if np.isnan(data['dirs'][0, i, 0]):
                continue
            ref = reference_frame(p.get('frame', 'body'), rig, blocks, root, world, js[0], count)
            want = np.einsum('tij,tj->ti', ref, data['dirs'][:, i])
            rotation = _align(bone, want)
        else:
            tip_joint = _aim_child(rig, blocks, js, len(js)-1, p, names)
            if 'joint' in p['target']:
                target = _aim_target(p, rig, blocks, root, world, names, count)
            else:
                if data['point'] is None:
                    data['point'] = aim_point(p, rhythm, style, rig, blocks, root, world, times)
                target = data['point']
            rotation = _partial(_align(world[:, tip_joint, :3, 3]-here, target-here), np.full(count, 1./(len(js)-i)))
        rotation = _partial(rotation, data['weight'])
        if data['twist'] is not None and data['tw'][i] != 0:
            axis = unit(np.einsum('tij,tj->ti', rotation, bone))
            rotation = Rotation.from_rotvec(axis*np.deg2rad(data['twist']*data['tw'][i])[:, None]).as_matrix() @ rotation
        set_world_rotation(rig, quats, translations, j, rotation @ world[:, j, :3, :3])


def _frame_origin(space, rig, blocks, root, world, count):
    """(origin (T,3), rest origin (3,), delta (T,3,3)) for translate spaces world / heading / body."""
    if space == 'world':
        return np.tile(blocks.rest[blocks.root], (count, 1)), blocks.rest[blocks.root], np.tile(np.eye(3), (count, 1, 1))
    if space == 'heading':
        return root['position'], blocks.rest[blocks.root], root['rotation_delta']
    r = blocks.root
    return world[:, r, :3, 3], blocks.rest[r], world[:, r, :3, :3] @ rig.rest_world[r, :3, :3].T


def apply_world_translations(rig, blocks, rhythm, style, times, root, quats, translations):
    """post-IK pass for translate parts: world / heading / body placements, follow blends and orientations."""
    names = [node.get('name', f'node_{i}') for i, node in enumerate(rig.nodes)]
    count = len(times)
    for key, spec in style.program.select('translate').items():
        p = spec['params']
        space = p.get('space', 'parent')
        if space == 'parent' and 'orientation' not in p:
            continue
        joints = blocks.parts[key].joints
        offset = np.column_stack([sample_curve(p[k], rhythm, style, times) if k in p else np.zeros(count)
                                  for k in ('x', 'y', 'z')])
        follow = None
        if 'follow' in p:
            if p['follow']['joint'] not in names:
                raise ValueError(f'{key}: follow joint {p["follow"]["joint"]!r} not found')
            follow = (names.index(p['follow']['joint']), np.clip(sample_curve(p['follow']['weight'], rhythm, style, times), 0., 1.))
        orient = euler_matrices(p['orientation'], rhythm, style, times) if 'orientation' in p else None
        for j, weight in zip(sorted(joints, key=rig.order.index), p.get('weights', [1.]*len(joints))):
            world = rig.fk(quats, translations)
            parent = int(rig.parents[j])
            rotation = world[:, j, :3, :3]
            if orient is not None:
                ref = reference_frame(p['orientation'].get('space', 'parent'), rig, blocks, root, world, j, count)
                rotation = ref @ orient @ blocks.frame.T @ rig.rest_world[j, :3, :3]
            if space == 'parent':
                set_world_rotation(rig, quats, translations, j, rotation)
                continue
            unit_len = {'leg': blocks.leg_length, 'scale': blocks.scale,
                        'bone': float(np.linalg.norm(rig.local_t[j]))}[p.get('units', 'leg')]
            origin, rest_origin, delta = _frame_origin(space, rig, blocks, root, world, count)
            local = blocks.rest[j]-rest_origin+np.einsum('ij,tj->ti', blocks.frame, offset*unit_len*weight)
            position = origin+np.einsum('tij,tj->ti', delta, local)
            if follow is not None:
                f, w = follow
                grip = np.linalg.inv(rig.rest_world[parent]) @ rig.rest_world[j] if parent >= 0 else rig.rest_world[j]
                carried = world[:, f] @ grip
                position = (1-w)[:, None]*position+w[:, None]*carried[:, :3, 3]
                rel = Rotation.from_matrix(np.swapaxes(rotation, -1, -2) @ carried[:, :3, :3]).as_rotvec()
                rotation = rotation @ Rotation.from_rotvec(rel*w[:, None]).as_matrix()
            if parent >= 0:
                translations[:, j] = np.einsum('tji,tj->ti', world[:, parent, :3, :3], position-world[:, parent, :3, 3])
            else:
                translations[:, j] = position
            set_world_rotation(rig, quats, translations, j, rotation)


def apply_wrist(rig, blocks, rhythm, style, times, root, quats, translations, key):
    """arm_arc 'wrist': orientation of the hand (end) joint after the arm IK."""
    spec = style.program.parts[key]
    p = spec['params']['wrist']
    j = blocks.parts[key].joints[-1]
    world = rig.fk(quats, translations)
    ref = reference_frame(p.get('space', 'parent'), rig, blocks, root, world, j, len(times))
    set_world_rotation(rig, quats, translations, j,
                       ref @ euler_matrices(p, rhythm, style, times) @ blocks.frame.T @ rig.rest_world[j, :3, :3])


def apply_translations(rig, blocks, rhythm, style, times, translations):
    """translate parts: offsets (x left, y up, z forward of the rest body frame) added to each joint's local
    translation, so the joint slides relative to its parent (piston, track shoe, telescoping blade)."""
    for key, spec in style.program.select('translate').items():
        p = spec['params']
        joints = blocks.parts[key].joints
        if p.get('space', 'parent') != 'parent':
            if blocks.root in joints:
                raise ValueError(f'{key}: translate cannot move the skeleton root (use the root operator)')
            continue
        if blocks.root in joints:
            raise ValueError(f'{key}: translate cannot move the skeleton root (use the root operator)')
        offset = np.column_stack([sample_curve(p[k], rhythm, style, times) if k in p else np.zeros(len(times))
                                  for k in ('x', 'y', 'z')])
        for j, weight in zip(joints, p.get('weights', [1.]*len(joints))):
            unit = {'leg': blocks.leg_length, 'scale': blocks.scale,
                    'bone': float(np.linalg.norm(rig.local_t[j]))}[p.get('units', 'leg')]
            parent = int(rig.parents[j])
            to_local = rig.rest_world[parent, :3, :3].T if parent >= 0 else np.eye(3)
            translations[:, j] += np.einsum('ij,tj->ti', to_local @ blocks.frame, offset*unit*weight)


def foot_orientation(blocks, rhythm, style, times, root, key):
    p = style.program.parts[key]['params']
    phase = interval(times, rhythm.seconds(p['release']), rhythm.seconds(p['land']))
    yaw = root['final_yaw_degrees']*smooth(phase)
    pitch = p['pitch_degrees']*np.sin(np.pi*phase)**2
    local = Rotation.from_euler('YX', np.column_stack([yaw, pitch]), degrees=True).as_matrix()
    return blocks.frame[None] @ local @ blocks.frame.T


def hand_arc(blocks, rhythm, style, times, key):
    spec = style.program.parts[key]
    p = spec['params']
    angle = np.deg2rad(sample_curve(p['angle'], rhythm, style, times))
    radius = sample_curve(p['radius'], rhythm, style, times)
    lateral = sample_curve(p['lateral'], rhythm, style, times)
    if np.any(radius <= np.abs(lateral)):
        raise ValueError('Arc radius must exceed lateral offset')
    radial = np.sqrt(radius*radius-lateral*lateral)
    sign = 1. if spec['side'] == 'L' else -1.
    return np.column_stack([sign*lateral, radial*np.cos(angle), radial*np.sin(angle)])
