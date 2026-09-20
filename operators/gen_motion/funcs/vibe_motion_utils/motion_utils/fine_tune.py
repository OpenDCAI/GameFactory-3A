"""Parameterized jump choreography with ballistic flight and limb IK."""
from __future__ import annotations

from copy import deepcopy
import numpy as np

from .generate import curve, fit_feet, rotate_joint, smooth, solve_two_bone
from .units import MotionClip, between, fk, inverse, quat_mul, quat_to_matrix, root_index


def _hermite(t, a, b, y0, y1, v0=0.0, v1=0.0):
    u = np.clip((t - a) / (b - a), 0.0, 1.0)
    return (
        (2 * u**3 - 3 * u**2 + 1) * y0
        + (u**3 - 2 * u**2 + u) * (b - a) * v0
        + (-2 * u**3 + 3 * u**2) * y1
        + (u**3 - u**2) * (b - a) * v1
    )


def generate_tuned_jump_chop(plan, *, num_frames, fps, heading_deg, parameters):
    """Evaluate the jump preset without modifying the skeleton.

    Lengths use limb scale; gravity uses skeleton scale/second squared.
    Primary keys 1/2 X multiply hand_clearance; key 3 Z multiplies strike_reach.
    Key X coordinates and elbow-pole X mirror with the selected arm.
    """
    p = parameters
    template = plan.template
    legs = list(plan.support_roles)
    arms = [r for r in template.limb_roles if r not in legs]
    if len(legs) != 2 or any(len(plan.roles[r].joints) != 4 for r in legs) or len(arms) != 2:
        raise ValueError('tuned jump needs two four-joint legs and two shoulder-elbow-wrist arms')
    for role in legs + arms:
        js = plan.roles[role].joints[:3] if role in legs else plan.roles[role].joints[-3:]
        if len(js) != 3 or any(template.parents[b] != a for a, b in zip(js, js[1:])):
            raise ValueError(f'{role}: tuned jump needs a continuous IK chain')
    rest, n = template.rest, num_frames
    root = root_index(template)
    length = plan.support_length
    duration = (n - 1) / fps
    t = np.arange(n) / fps
    height, gravity = p['jump_height'] * length, p['gravity_ratio'] * plan.scale
    flight = float(np.sqrt(8 * height / gravity))
    takeoff = p['takeoff_ratio'] * duration
    land = takeoff + flight
    load, settle = takeoff - p['push_seconds'], land + p['settle_seconds']
    finish = settle + p['recover_seconds']
    if load <= 0 or finish >= duration:
        raise ValueError('clip is too short for ballistic flight and recovery; increase num_frames or lower fps')
    hit_start = takeoff + p['attack_flight_ratio'] * flight
    hit = takeoff + p['impact_flight_ratio'] * flight
    if min(load, p['push_seconds'], p['settle_seconds'], p['recover_seconds'], hit - hit_start) * fps < 2 or flight * fps < 6:
        raise ValueError('not enough samples for takeoff, flight, strike or landing; increase fps and duration together')
    clip = MotionClip.rest_clip(template, n, fps=fps)
    baseline = clip.copy()
    base_y, launch_y = -0.045 * length, -0.015 * length
    crouch_y, landing_y = -p['crouch'] * length, -p['landing_crouch'] * length
    speed = gravity * flight / 2
    y = _hermite(t, 0, load, base_y, crouch_y)
    y = np.where(t >= load, _hermite(t, load, takeoff, crouch_y, launch_y, 0, speed), y)
    dt = t - takeoff
    y = np.where((t >= takeoff) & (t < land), launch_y + speed * dt - 0.5 * gravity * dt**2, y)
    y = np.where(t >= land, _hermite(t, land, settle, launch_y, landing_y, -speed, 0), y)
    y = np.where(t >= settle, _hermite(t, settle, finish, landing_y, base_y), y)
    phase = np.clip((t - takeoff) / flight, 0, 1)
    yaw = heading_deg + p['turn_deg'] * smooth(phase)
    rotate_joint(clip, root, [0, 1, 0], yaw)
    rotation = quat_to_matrix(clip.quats[:, root])
    end_yaw = np.deg2rad(heading_deg + p['turn_deg'])
    displacement = p['travel'] * length * np.array([np.sin(end_yaw), 0, np.cos(end_yaw)])
    clip.trans[:] = smooth(phase)[:, None] * displacement
    clip.trans[:, 1] = -0.015 * length + y
    times = [0, load, takeoff, hit_start, hit, settle, finish]
    twist = curve(times, [0, -p['torso_twist_deg'], -0.6 * p['torso_twist_deg'], 0, 0.4 * p['torso_twist_deg'], 0, 0], t)
    lean = curve(times, [2, p['torso_lean_deg'], -3, -3, 0.8 * p['torso_lean_deg'], p['torso_lean_deg'], 2], t)
    trunk = [j for j in plan.roles['trunk'].joints if j != root]
    for joint in trunk:
        rotate_joint(clip, joint, [0, 1, 0], twist / len(trunk), space='body')
        rotate_joint(clip, joint, [1, 0, 0], lean / len(trunk), space='body')
    if 'head' in plan.roles:
        head = plan.roles['head'].joints[0]
        rotate_joint(clip, head, [0, 1, 0], -0.35 * twist, space='body')
        rotate_joint(clip, head, [1, 0, 0], -0.35 * lean, space='body')
    feet, contacts = {}, {}
    arc = 64 * phase**3 * (1 - phase)**3
    for index, role in enumerate(legs):
        tip = plan.roles[role].joints[-1]
        track = rest[root] + np.einsum('tij,j->ti', rotation, rest[tip] - rest[root]) + clip.trans
        vertical = np.maximum(y - launch_y, 0) * (t >= takeoff) * (t <= land)
        track[:, 1] = rest[tip, 1] + vertical + p['foot_tuck'] * length * arc
        shift = np.array([0, 0, (0.025 if index == 0 else -0.025) * length])
        track += np.einsum('tij,j->ti', rotation, shift) * arc[:, None]
        feet[role] = track
        contacts[role] = (t <= takeoff) | (t >= land)
    residuals = fit_feet(clip, plan, feet)
    primary = arms[0]
    if template.weapon_roles:
        host = plan.roles[template.weapon_roles[0]].host
        primary = next((role for role in arms if host in plan.roles[role].joints), None)
        if primary is None or host != plan.roles[primary].joints[-1]:
            raise ValueError('the primary weapon must be attached to the wrist for blade control')
    hands = {}
    for role in arms:
        shoulder, elbow, wrist = js = plan.roles[role].joints[-3:]
        side = 1.0 if '.L.' in role else -1.0
        arm_length = np.linalg.norm(rest[elbow] - rest[shoulder]) + np.linalg.norm(rest[wrist] - rest[elbow])
        if role == primary:
            keys = np.array(p['primary_hand_keys'], dtype=float, copy=True)
            keys[1:3, 0] *= p['hand_clearance']
            keys[3, 2] *= p['strike_reach']
            times = [0, takeoff, hit_start, hit, settle, finish]
            modes = ['smooth', 'smooth', 'impact', 'return', 'smooth']
        else:
            keys = np.array(p['guard_hand_keys'], dtype=float, copy=True)
            times, modes = [0, takeoff, hit, finish], None
        keys[:, 0] *= side
        body = arm_length * curve(times, keys, t, modes)
        pos, _ = fk(clip)
        target = pos[:, shoulder] + np.einsum('tij,tj->ti', rotation, body)
        pole_body = np.asarray(p['elbow_pole'], float) * [side, 1, 1]
        pole = np.einsum('tij,j->ti', rotation, pole_body)
        residuals[role] = solve_two_bone(clip, js, target, pole)
        hands[role] = target
        if role == primary and template.weapon_roles:
            _, global_q = fk(clip)
            weapon = plan.roles[template.weapon_roles[0]]
            rest_dir = rest[weapon.joints[-1]] - rest[wrist]
            blade = curve(
                [0, takeoff, hit_start, hit, finish],
                p['blade_keys'], t,
                ['smooth', 'smooth', 'impact', 'smooth'],
            )
            desired = quat_mul(clip.quats[:, root], between(rest_dir, blade))
            clip.quats[:, wrist] = quat_mul(inverse(global_q[:, elbow]), desired)
    timing = {
        'takeoff_seconds': float(takeoff), 'landing_seconds': float(land),
        'strike_start_seconds': float(hit_start), 'impact_seconds': float(hit),
        'settle_seconds': float(settle), 'finish_seconds': float(finish),
        'flight_seconds': flight, 'gravity': float(gravity),
    }
    notes = [
        'Full task-space jump chop: ballistic root, matched landing velocity, both-arm IK and blade direction.',
        'baseline is a rest-pose reference, not an ablation. Discrete IK is not whole-body dynamics or a collision guarantee.',
    ]
    if max(float(v.max()) for v in residuals.values()) > 0.001:
        notes.append('Unreachable IK targets were projected; inspect residuals before accepting the motion.')
    return {
        'clip': clip, 'baseline': baseline, 'parameters': deepcopy(p),
        'contacts': contacts, 'foot_targets': feet, 'hand_targets': hands,
        'residuals': residuals, 'timing': timing, 'notes': notes,
    }
