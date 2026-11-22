"""Task-space trajectory operators over arbitrary named body parts."""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
from scipy.spatial.transform import Rotation

from .coordination import foot_orientation, hand_arc
from .program import effector_events, effector_tip, ease, sample_curve
from .root_motion import design_root
from .timing import interval, smooth


@dataclass
class TargetTracks:
    feet: dict
    hands: dict
    foot_rotations: dict
    poles: dict
    contacts: dict
    chest_frames: dict
    effectors: dict = field(default_factory=dict)


def frame_track(kind, rig, blocks, root, world, chain):
    """(origin (T,3), rest origin (3,), world delta rotation (T,3,3)) of an effector frame.
    world: fixed initial body frame at the rest root; root: root position + heading (yaw); body: the skeleton root
    joint (includes root pitch/roll and pelvis rotations); parent: the chain's parent joint."""
    count = len(root['position'])
    if kind == 'world':
        return np.tile(blocks.rest[blocks.root], (count, 1)), blocks.rest[blocks.root], np.tile(np.eye(3), (count, 1, 1))
    if kind == 'root':
        return root['position'], blocks.rest[blocks.root], root['rotation_delta']
    joint = blocks.root if kind == 'body' else int(rig.parents[chain[0]])
    if joint < 0:
        joint = blocks.root
    delta = world[:, joint, :3, :3] @ rig.rest_world[joint, :3, :3].T
    return world[:, joint, :3, 3], blocks.rest[joint], delta


def _effector(rig, blocks, rhythm, style, times, root, world, key, ground):
    spec = style.program.parts[key]
    p = spec['params']
    js = blocks.parts[key].joints
    tip = effector_tip(spec)
    unit = {'leg': blocks.leg_length, 'limb': float(np.linalg.norm(np.diff(blocks.rest[list(js)], axis=0), axis=1).sum()),
            'scale': blocks.scale}[p.get('units', 'leg')]
    end = js[-1]
    frames = {}

    def track(kind):
        if kind not in frames:
            frames[kind] = frame_track(kind, rig, blocks, root, world, js)
        return frames[kind]

    def point(offset, kind, height=None):
        origin, rest_origin, delta = track(kind)
        local = blocks.rest[end]-rest_origin+blocks.frame @ (np.asarray(offset, float)*unit)
        out = origin+np.einsum('tij,j->ti', delta, local)
        if height is not None:
            out[:, 1] = ground+float(height)*unit
        return out

    default = p.get('frame', 'root')
    keys, plants = effector_events(p, rhythm)
    authored = [(t, point(k.get('offset', [0, 0, 0]), k.get('frame', default), k.get('height')), k) for t, k in keys]

    spline = p.get('interp', 'ease') == 'spline'

    def blend(entries):
        """entries: [(t, (T,3) positions, ease, arc-vector-or-None[, fixed])] -> (T,3) target; hold before first /
        after last. With interp 'spline' free keys get Catmull-Rom tangents (fixed = plant boundary: zero tangent)."""
        if not entries:
            return point([0, 0, 0], default)
        out = entries[0][1].copy()
        tangents = [None]*len(entries)
        if spline:
            for i in range(1, len(entries)-1):
                if not (len(entries[i]) > 4 and entries[i][4]):
                    dt = entries[i+1][0]-entries[i-1][0]
                    tangents[i] = (entries[i+1][1]-entries[i-1][1])/max(dt, 1e-9)
        for i, ((t0, a, *_), (t1, b, kind, arc, *_)) in enumerate(zip(entries, entries[1:])):
            mask = times > t0
            phase = interval(times, t0, t1) if t1 > t0 else np.ones_like(times)
            if spline:
                u = phase[:, None]
                dt = t1-t0
                m0 = tangents[i] if tangents[i] is not None else 0.
                m1 = tangents[i+1] if tangents[i+1] is not None else 0.
                seg = (2*u**3-3*u**2+1)*a+(u**3-2*u**2+u)*dt*m0+(-2*u**3+3*u**2)*b+(u**3-u**2)*dt*m1
            else:
                w = ease(phase, kind)[:, None]
                seg = (1-w)*a+w*b
            if arc is not None:
                seg = seg+np.einsum('tij,j->ti', root['heading'], arc*unit)*np.sin(np.pi*phase)[:, None]**2
            out[mask] = seg[mask]
        return out

    free = blend([(t, pos, k.get('ease', 'smooth'), np.asarray(k['arc'], float) if 'arc' in k else None)
                  for t, pos, k in authored])
    rest_height = (blocks.rest[end, 1]-ground)/unit
    entries, windows = [], []
    for a, b, w in plants:
        anchor = {'start': a, 'mid': (a+b)/2, 'end': b}[w.get('anchor', 'mid')]
        i = int(np.argmin(np.abs(times-anchor)))
        heading = root['heading'][i]
        p0 = free[i].copy()
        if 'offset' in w:
            p0 += heading @ (np.asarray(w['offset'], float)*unit)
        h = w.get('height', 'rest')
        if h != 'free':
            p0[1] = ground+(rest_height if h == 'rest' else float(h))*unit
        p1 = p0+heading @ (np.asarray(w.get('slide', [0, 0, 0]), float)*unit)
        if 'slide' in w and h != 'free':
            p1[1] = p0[1]+heading[1] @ (np.asarray(w['slide'], float)*unit)
        windows.append((a, b, p0, p1, w.get('ease', 'linear')))
    if not windows:
        target = free
    else:
        gap_arcs = {(round(rhythm.seconds(x['start']), 6), round(rhythm.seconds(x['end']), 6)): np.asarray(x['offset'], float)
                    for x in p.get('arcs', [])}
        lift = float(p.get('lift', 0.))
        for t, pos, k in authored:
            if not any(a-1e-9 <= t <= b+1e-9 for a, b, *_ in windows):
                entries.append((t, pos, k.get('ease', 'smooth'), np.asarray(k['arc'], float) if 'arc' in k else None, 'key'))
        for a, b, p0, p1, kind in windows:
            entries.append((a, np.tile(p0, (len(times), 1)), 'smooth', None, 'plant_start'))
            entries.append((b, np.tile(p1, (len(times), 1)), kind, None, 'plant_end'))
        entries.sort(key=lambda e: (e[0], e[4] != 'plant_end'))
        merged = []
        for i, e in enumerate(entries):
            t, pos, kind, arc, tag = e
            if tag == 'plant_start' and i > 0 and entries[i-1][4] == 'plant_end' and arc is None:
                gap = (round(entries[i-1][0], 6), round(t, 6))
                arc = gap_arcs.get(gap, np.array([0., lift, 0.]) if lift else None)
            merged.append((t, pos, kind, arc, tag != 'key'))
        target = blend(merged)
        for a, b, p0, p1, kind in windows:
            inside = (times >= a) & (times <= b)
            w = ease(interval(times, a, b), kind)[:, None]
            target[inside] = ((1-w)*p0+w*p1)[inside]
    if 'ground_clearance' in p:
        target[:, 1] = np.maximum(target[:, 1], ground+float(p['ground_clearance'])*unit)
    pole_origin, _, pole_delta = track(p.get('pole_frame', 'root'))
    if isinstance(p['pole'], str):
        chain_rest = blocks.rest[list(js[:len(js)-tip])]
        axis = chain_rest[-1]-chain_rest[0]
        axis = axis/max(np.linalg.norm(axis), 1e-12)
        offsets = chain_rest[1:-1]-chain_rest[0]
        bend = (offsets-np.outer(offsets @ axis, axis)).mean(axis=0)
        if np.linalg.norm(bend) < 1e-6*max(blocks.scale, 1e-9):
            bend = np.cross(axis, blocks.frame[:, 0])
        poles = np.einsum('tij,j->ti', pole_delta, bend/np.linalg.norm(bend))
    else:
        poles = np.einsum('tij,j->ti', pole_delta, blocks.frame @ np.asarray(p['pole'], float))
    orient = p.get('orient', 'root' if tip else 'free')
    ik = js[:len(js)-tip]
    rotation = None
    if orient != 'free':
        _, _, delta = track(orient)
        if 'end_pitch' in p:
            pitch = sample_curve(p['end_pitch'], rhythm, style, times)
            local = blocks.frame[None] @ Rotation.from_euler('x', pitch, degrees=True).as_matrix() @ blocks.frame.T[None]
            delta = delta @ local
        elif 'end_rotation' in p:
            er = p['end_rotation']
            zero = np.zeros(len(times))
            values = np.column_stack([sample_curve(er[k], rhythm, style, times) if k in er else zero
                                      for k in ('twist', 'pitch', 'roll')])
            local = blocks.frame[None] @ Rotation.from_euler('YXZ', values, degrees=True).as_matrix() @ blocks.frame.T[None]
            delta = delta @ local
        rotation = delta @ rig.rest_world[ik[-1], :3, :3]
        if p.get('plant_orient', 'follow') == 'lock' and windows:
            rotation = _lock_rotation(rotation, times, windows)
    contact = np.zeros(len(times), bool)
    for a, b, *_ in windows:
        contact |= (times >= a-1e-9) & (times <= b+1e-9)
    info = {'ik': ik, 'tip': tip, 'plants': bool(windows), 'unit': unit}
    return target, contact, poles, rotation, info


def _lock_rotation(rotation, times, windows):
    """world-locked end orientation while planted (value at each plant start), slerped back to the following
    orientation during each swing so the next plant starts continuous."""
    out = rotation.copy()
    held = None
    last_end = None
    for a, b, *_ in windows:
        i0 = int(np.argmin(np.abs(times-a)))
        if held is not None:
            gap = (times > last_end) & (times < a)
            if np.any(gap):
                w = smooth(interval(times[gap], last_end, a))
                rel = Rotation.from_matrix(np.swapaxes(np.broadcast_to(held, rotation[gap].shape), -1, -2) @ rotation[gap]).as_rotvec()
                out[gap] = held @ Rotation.from_rotvec(rel*w[:, None]).as_matrix()
        held = rotation[i0].copy()
        inside = (times >= a-1e-9) & (times <= b+1e-9)
        out[inside] = held
        last_end = b
    after = times > last_end
    if np.any(after):
        w = smooth(interval(times[after], last_end, min(last_end+.25, times[-1]+1e-6)))
        rel = Rotation.from_matrix(np.swapaxes(np.broadcast_to(held, rotation[after].shape), -1, -2) @ rotation[after]).as_rotvec()
        out[after] = held @ Rotation.from_rotvec(rel*w[:, None]).as_matrix()
    return out


def _contact_path(blocks, rhythm, style, times, root, key):
    p = style.program.parts[key]['params']
    js = blocks.parts[key].joints
    windows = np.array([[rhythm.seconds(a), rhythm.seconds(b)] for a, b in p['contacts']])
    length = blocks.length(key)
    anchors = []
    for i, (a, b) in enumerate(windows):
        t = 0. if i == 0 else (rhythm.duration if i == len(windows)-1 else (a+b)/2)
        r = design_root(blocks, rhythm, style, [t])
        anchor = r['position'][0]+r['rotation_delta'][0] @ (blocks.rest[js[-1]]-blocks.rest[blocks.root])
        anchor[1] = blocks.rest[js[-1], 1]
        anchors.append(anchor)
    target = np.tile(anchors[0], (len(times), 1))
    for i, (a, b) in enumerate(windows):
        mask = (times >= a) & (times <= b)
        target[mask] = anchors[i]
        if i == len(windows)-1:
            continue
        c = windows[i+1, 0]
        mask = (times > b) & (times < c)
        phase = interval(times, b, c)
        blend = smooth(phase)
        arc = np.array([0., p['lift_ratio'], 0.])
        for special in p['arcs']:
            if np.isclose(rhythm.seconds(special['start']), b) and np.isclose(rhythm.seconds(special['end']), c):
                arc = np.asarray(special['offset'], float)
        path = (1-blend[:, None])*anchors[i]+blend[:, None]*anchors[i+1]
        path += np.einsum('tij,j->ti', root['heading'], arc)*length*np.sin(np.pi*phase)[:, None]**2
        target[mask] = path[mask]
    return target, rhythm.contact_mask(times, p['contacts'])


def infer_target_positions(rig, blocks, rhythm, style, times, root, world, clearance=None, ground=None):
    times = np.asarray(times)
    feet, hands, rotations, poles, contacts, frames, effectors = {}, {}, {}, {}, {}, {}, {}
    if ground is None:
        ground = float(blocks.rest[list(rig.joints), 1].min())
    for key, spec in style.program.parts.items():
        op, p, js = spec['operator'], spec['params'], blocks.parts[key].joints
        if op == 'effector':
            feet[key], contacts[key], poles[key], rotations[key], effectors[key] = _effector(
                rig, blocks, rhythm, style, times, root, world, key, ground)
            continue
        if op in ('foot_swing', 'contact_path'):
            if op == 'contact_path':
                target, contacts[key] = _contact_path(blocks, rhythm, style, times, root, key)
                rotation_delta = root['rotation_delta']
            else:
                release, land = rhythm.seconds(p['release']), rhythm.seconds(p['land'])
                phase = interval(times, release, land)
                blend = smooth(phase)
                initial = blocks.rest[js[-1]].copy()
                offset = initial-blocks.rest[blocks.root]
                final = root['final_position']+root['final_heading'] @ blocks.frame.T @ offset
                sign = 1. if spec.get('side') == 'L' else (-1. if spec.get('side') == 'R' else 0.)
                final += p['stance_ratio']*blocks.leg_length*sign*root['final_heading'][:, 0]
                final[1] = initial[1]
                target = (1-blend[:, None])*initial+blend[:, None]*final
                lift = p['lift_ratio']*blocks.leg_length*np.sin(np.pi*phase)**2
                release_height = design_root(blocks, rhythm, style, [release])['position'][0, 1]
                follow = np.maximum(root['position'][:, 1]-release_height, 0.)
                follow *= smooth(interval(times, release, release+p['follow_in']*rhythm.duration))
                follow *= 1-smooth(interval(times, land-p['follow_out']*rhythm.duration, land))
                target[:, 1] += lift+follow
                sweep = p['sweep_ratio']*blocks.leg_length*np.sin(np.pi*phase)**2
                target += sweep[:, None]*root['heading'][:, :, 2]
                rotation_delta = foot_orientation(blocks, rhythm, style, times, root, key)
                contacts[key] = (times <= release) | (times >= land)
            feet[key] = target
            rotations[key] = rotation_delta @ rig.rest_world[js[2], :3, :3]
            poles[key] = np.einsum('tij,j->ti', root['heading'], p['pole'])
        elif op == 'arm_arc':
            relative = hand_arc(blocks, rhythm, style, times, key)*blocks.length(key)
            if clearance is not None:
                relative += clearance.get(key, np.zeros_like(relative))
            parent = int(rig.parents[js[0]])
            reference = root['heading'] if parent < 0 else world[:, parent, :3, :3] @ rig.rest_world[parent, :3, :3].T @ blocks.frame
            frames[key] = reference
            hands[key] = world[:, js[0], :3, 3]+np.einsum('tij,tj->ti', reference, relative)
    return TargetTracks(feet, hands, rotations, poles, contacts, frames, effectors)
