"""Root trajectory operators independent of body-part names and limb count."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy.interpolate import CubicHermiteSpline
from scipy.spatial.transform import Rotation

from .program import MotionProgram, sample_curve
from .timing import interval, smooth


@dataclass(frozen=True)
class MotionStyle:
    turn_degrees: float
    travel_ratio: float
    jump_ratio: float
    crouch_ratio: float
    arm_reach: float
    arm_clearance: float
    program: MotionProgram

    def __post_init__(self):
        limits = {'turn_degrees': (-3600., 3600.), 'travel_ratio': (0., 100.),
                  'jump_ratio': (0., 10.), 'crouch_ratio': (0., 1.),
                  'arm_reach': (.01, .99), 'arm_clearance': (0., .99)}
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if not np.isfinite(value) or not low <= value <= high:
                raise ValueError(f'{name} must be finite and within [{low}, {high}]')


def heading_travel(p, rhythm, style, times):
    """(T, 3) body-axis offsets (leg units) from integrating forward_speed / lateral_speed (leg lengths per second,
    + = forward / left in the current heading) along the yaw curve; independent of the sampling grid."""
    fine = np.linspace(0., rhythm.duration, max(2, int(np.ceil(rhythm.duration*240)))+1)
    yaw = np.deg2rad(sample_curve(p['yaw'], rhythm, style, fine))
    fwd = sample_curve(p['forward_speed'], rhythm, style, fine) if 'forward_speed' in p else np.zeros_like(fine)
    lat = sample_curve(p['lateral_speed'], rhythm, style, fine) if 'lateral_speed' in p else np.zeros_like(fine)
    vx = fwd*np.sin(yaw)+lat*np.cos(yaw)
    vz = fwd*np.cos(yaw)-lat*np.sin(yaw)
    out = np.zeros((len(np.atleast_1d(times)), 3))
    for k, v in ((0, vx), (2, vz)):
        cum = np.concatenate([[0.], np.cumsum(.5*(v[1:]+v[:-1])*np.diff(fine))])
        out[:, k] = np.interp(np.atleast_1d(times), fine, cum)
    return out


def design_root(blocks, rhythm, style, times):
    """Root position / heading. 'heading' is yaw only (used by contact targets and the 'heading' frames); 'body' also
    includes the optional root pitch/roll curves (body frame, degrees) and is used to orient the root joint. With
    a 'pivot' (rest point, leg lengths, body frame relative to the root) the x/y/z curves move that point and the
    root turns about it."""
    times = np.atleast_1d(np.asarray(times, float))
    p = style.program.root['params']
    length = blocks.leg_length
    yaw = sample_curve(p['yaw'], rhythm, style, times)
    final_yaw = float(sample_curve(p['yaw'], rhythm, style, [rhythm.duration])[0])
    heading = blocks.frame[None] @ Rotation.from_euler('y', yaw, degrees=True).as_matrix()
    final_heading = blocks.frame @ Rotation.from_euler('y', final_yaw, degrees=True).as_matrix()
    delta = heading @ blocks.frame.T
    tilted = style.program.root['operator'] == 'curves' and ('pitch' in p or 'roll' in p)
    if tilted:
        pitch = sample_curve(p['pitch'], rhythm, style, times) if 'pitch' in p else np.zeros_like(times)
        roll = sample_curve(p['roll'], rhythm, style, times) if 'roll' in p else np.zeros_like(times)
        body = heading @ Rotation.from_euler('XZ', np.column_stack([pitch, roll]), degrees=True).as_matrix()
    else:
        pitch = roll = np.zeros_like(times)
        body = heading
    body_delta = body @ blocks.frame.T
    gravity = None
    if style.program.root['operator'] == 'curves':
        offsets = np.column_stack([sample_curve(p[k], rhythm, style, times) for k in ('x', 'y', 'z')])*length
        final_offset = np.array([sample_curve(p[k], rhythm, style, [rhythm.duration])[0] for k in ('x', 'y', 'z')])*length
        if 'forward_speed' in p or 'lateral_speed' in p:
            offsets = offsets+heading_travel(p, rhythm, style, times)*length
            final_offset = final_offset+heading_travel(p, rhythm, style, [rhythm.duration])[0]*length
        root = blocks.rest[blocks.root]+np.einsum('ij,tj->ti', blocks.frame, offsets)
        final_position = blocks.rest[blocks.root]+blocks.frame @ final_offset
        vertical = offsets[:, 1]
        if 'pivot' in p:
            arm = blocks.frame @ (np.asarray(p['pivot'], float)*length)
            root = root+arm-np.einsum('tij,j->ti', body_delta, arm)
            final_body = body_delta[-1] if np.isclose(times[-1], rhythm.duration) else design_root(
                blocks, rhythm, style, [rhythm.duration])['body_delta'][0]
            final_position = final_position+arm-final_body @ arm
    else:
        takeoff, landing = rhythm.seconds(p['takeoff']), rhythm.seconds(p['landing'])
        flight, height = landing-takeoff, style.jump_ratio*length
        start_y, end_y = p['takeoff_height']*length, p['landing_height']*length
        slope = (end_y-start_y)/flight
        v0, v1 = slope+4*height/flight, slope-4*height/flight
        crouch = rhythm.seconds(p['anticipation'])
        absorb = rhythm.seconds(p['absorb'])+p['absorb_delay']*rhythm.duration
        before = CubicHermiteSpline([0., crouch, takeoff], [0., -style.crouch_ratio*length, start_y], [0., 0., v0])
        after = CubicHermiteSpline([landing, absorb, rhythm.seconds(p['recover']), rhythm.duration],
                                   [end_y, -style.crouch_ratio*length, p['recover_height']*length, 0.], [v1, 0., 0., 0.])
        phase = interval(times, takeoff, landing)
        airborne_y = (1-phase)*start_y+phase*end_y+4*height*phase*(1-phase)
        vertical = np.where(times < takeoff, before(np.minimum(times, takeoff)),
                            np.where(times > landing, after(np.maximum(times, landing)), airborne_y))
        progress = smooth(interval(times, crouch, landing))
        shift = sample_curve(p['lateral_shift'], rhythm, style, times)*length
        root = blocks.rest[blocks.root]+progress[:, None]*style.travel_ratio*length*final_heading[:, 2]
        root += shift[:, None]*blocks.frame[:, 0]
        root[:, 1] += vertical
        final_position = blocks.rest[blocks.root]+style.travel_ratio*length*final_heading[:, 2]
        gravity = 8*height/flight**2
    return {'position': root, 'heading': heading, 'rotation_delta': delta, 'yaw_degrees': yaw,
            'body': body, 'body_delta': body_delta, 'pitch_degrees': pitch, 'roll_degrees': roll,
            'final_position': final_position, 'final_heading': final_heading, 'final_yaw_degrees': final_yaw,
            'effective_gravity': gravity, 'vertical_offset': vertical}
