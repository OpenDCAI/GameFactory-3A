"""Generate preset motions with foot contacts, hand targets and IK refinement."""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, field
import numpy as np
from .segments import realize_segment
from .primitives import _taper_weights, apply_primitive, polish
from .recipes import MotionSegment, RECIPES
from .generate import curve, fit_feet, rotate_joint, smooth, solve_two_bone, strike, swing
from .templates import resolve_preset
from .units import MotionClip, SkeletonPlan, fk, inverse, quat_from_axis_angle, quat_mul, quat_to_matrix, root_index

@dataclass
class MotionResult:
    clip: MotionClip
    baseline: MotionClip
    parameters: dict
    contacts: dict[str, np.ndarray] = field(default_factory=dict)
    foot_targets: dict[str, np.ndarray] = field(default_factory=dict)
    hand_targets: dict[str, np.ndarray] = field(default_factory=dict)
    residuals: dict[str, np.ndarray] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    timing: dict[str, float] = field(default_factory=dict)

def _foot_tracks(plan, n, steps, length, height, *, end=0.88):
    """Foot targets in world space stay exactly fixed through the support phase, with continuous position, velocity and acceleration through the swing phase. Lengths are ratios of limb length."""
    roles = list(plan.support_roles)
    if len(roles) != 2 or any((len(plan.roles[r].joints) != 4 for r in roles)):
        raise ValueError('this footstep recipe targets bipeds with four-joint legs; other topologies can call solve_two_bone directly')
    u = np.linspace(0, 1, n)
    targets = {r: np.repeat(plan.template.rest[plan.roles[r].joints[-1]][None], n, axis=0).astype(float) for r in roles}
    contacts = {r: np.ones(n, dtype=bool) for r in roles}
    front = max((targets[r][0, 2] for r in roles))
    for k in range(steps):
        role = roles[k % 2]
        a, b = (0.08 + (end - 0.08) * k / steps, 0.08 + (end - 0.08) * (k + 1) / steps)
        old_z = float(targets[role][-1, 2])
        front += length * plan.support_length
        phase = np.clip((u - a) / (b - a), 0, 1)
        targets[role][:, 2] += (front - old_z) * smooth(phase)
        targets[role][:, 1] += height * plan.support_length * 64 * phase ** 3 * (1 - phase) ** 3
        contacts[role] &= ~((u > a) & (u < b))
    for r in roles:
        targets[r][:, 1] += plan.ground - plan.template.rest[plan.roles[r].joints[-1], 1]
    return (targets, contacts)

def _apply(intents, plan, n, fps):
    clip = MotionClip.rest_clip(plan.template, n, fps=fps)
    ancestor = [i for i in intents if not i.role.startswith(('limb.', 'weapon.'))]
    limbs = [i for i in intents if i.role.startswith(('limb.', 'weapon.'))]
    for it in ancestor + limbs:
        if it.primitive == 'strike':
            params = dict(it.params, raise_ease='return', strike_ease='impact', recover_ease='smooth')
            strike(clip, plan, it.role, **params)
        elif it.primitive == 'swing':
            swing(clip, plan, it.role, **it.params)
        elif it.primitive in ('twist', 'bend'):
            params = dict(it.params)
            weights = _taper_weights(len(plan.roles[it.role].joints), params.pop('taper', 0.0))
            degrees = params.pop('degrees', 30.0)
            params.setdefault('axis', 'twist' if it.primitive == 'twist' else 'bend')
            if params.get('ease') == 'anticipate':
                params['ease'] = 'smooth'
            for at, weight in enumerate(weights):
                swing(clip, plan, it.role, **params, degrees=degrees * weight, at=at)
        else:
            apply_primitive(clip, plan, it.primitive, it.role, dict(it.params))
    return clip

def generate_motion(name: str, plan: SkeletonPlan, *, num_frames=None, fps=30.0, heading_deg=0.0, **overrides) -> MotionResult:
    """Parameters must be declared by the matching preset. Unknown parameters raise, and the result is an independent copy."""
    spec = resolve_preset(name, num_frames=num_frames, fps=fps, heading_deg=heading_deg, **overrides)
    name, n, p = (spec['name'], spec['frames'], spec['params'])
    if name == 'task_space':
        from .task_space import generate_task_space
        return MotionResult(**generate_task_space(
            plan, num_frames=n, fps=fps, heading_deg=heading_deg, parameters=p,
        ))
    if name == 'turn_jump_chop_tuned':
        from .fine_tune import generate_tuned_jump_chop
        return MotionResult(**generate_tuned_jump_chop(
            plan, num_frames=n, fps=fps, heading_deg=heading_deg, parameters=p,
        ))
    arms = [r for r in plan.template.limb_roles if r not in plan.support_roles]
    if len(plan.support_roles) != 2 or any((len(plan.roles[r].joints) != 4 for r in plan.support_roles)):
        raise ValueError('these recipes support bipeds with four-joint legs only; other morphologies are unverified')
    if name in ('boxing', 'jab', 'chop', 'turn_jump_chop') and (len(arms) != 2 or any((len(plan.roles[r].joints) < 4 for r in arms))):
        raise ValueError('this motion needs two arms with shoulder, elbow and wrist')
    if name in ('boxing', 'jab'):
        if plan.template.weapon_roles:
            raise ValueError('the boxing presets do not support skeletons holding a weapon')
        intents = RECIPES['boxing'](plan, n, combo=tuple(p['combo']))
    else:
        intents = RECIPES[spec.get('baseline_recipe', spec['recipe'])](plan, n)
    baseline = realize_segment(MotionSegment(name, n, deepcopy(intents)), plan, fps=fps)
    polish(baseline, plan)
    if name == 'chop':
        for it in intents:
            if it.primitive == 'strike':
                it.params.update({k: p[k] for k in ('raise_deg', 'strike_deg', 'strike_ratio')})
            else:
                for key in ('start_frame', 'end_frame'):
                    if it.params.get(key) == int(n * 0.7):
                        it.params[key] = int(n * (0.44 + p['strike_ratio']))
        clip = _apply(intents, plan, n, fps)
    elif name == 'turn_jump_chop':
        clip, targets, contacts = _turn_jump_chop(plan, arms, n, fps, p)
    else:
        clip = baseline.copy()
    result = MotionResult(clip, baseline, deepcopy(p))
    root = root_index(plan.template)
    if name == 'turn_jump_chop':
        result.notes.append('baseline contains a stepping chop, not the generated jump.')
        result.notes.append('the airborne trajectory is smooth geometric animation, not a gravity or collision simulation')
    else:
        if name in ('boxing', 'jab'):
            targets, contacts = _foot_tracks(plan, n, 0, 0.1, 0.1)
        else:
            steps = 1 if name == 'chop' else p['steps']
            targets, contacts = _foot_tracks(plan, n, steps, p['step_length'], p['foot_height'], end=0.42 if name == 'chop' else 0.88)
        clip.trans[:] = 0
        clip.quats[:, root] = [1, 0, 0, 0]
        initial_z = np.mean([plan.template.rest[plan.roles[r].joints[-1], 2] for r in targets])
        clip.trans[:, 2] = np.mean([v[:, 2] for v in targets.values()], axis=0) - initial_z
        clip.trans[:, 1] = -p['crouch'] * plan.support_length
    result.contacts, result.foot_targets = (contacts, targets)
    result.residuals = fit_feet(clip, plan, targets)
    if name in ('boxing', 'jab'):
        _fit_boxing(result, plan, arms, p)
    elif name in ('walk', 'stride'):
        _fit_walk_arms(clip, plan, arms, p)
    elif name == 'turn_jump_chop' and p['arm_clearance'] > 0:
        _refine_jump_chop_arms(result, plan, arms, p)
    if heading_deg:
        q = quat_from_axis_angle(np.array([0.0, 1.0, 0.0]), np.deg2rad(heading_deg))
        rot = quat_to_matrix(q)
        center = plan.template.rest[root]
        for c in (clip, baseline):
            c.trans[:] = c.trans @ rot.T
            c.quats[:, root] = quat_mul(q, c.quats[:, root])
        for tracks in (result.foot_targets, result.hand_targets):
            for role in tracks:
                tracks[role] = (tracks[role] - center) @ rot.T + center
    clip.log.append(f'refined:{name} {p}')
    if any((np.max(v) > 0.001 for v in result.residuals.values())):
        result.notes.append('some IK targets were unreachable and were projected; check residuals, as the constraints are not met exactly')
    return result

def _turn_jump_chop(plan, arms, n, fps, p):
    """Crouch, turn while airborne, chop downward, then absorb the landing. Foot targets follow the body in the air and lock once grounded."""
    clip = MotionClip.rest_clip(plan.template, n, fps=fps)
    root = root_index(plan.template)
    center = plan.template.rest[root]
    length = plan.support_length
    u = np.linspace(0.0, 1.0, n)
    takeoff, landing = (p['takeoff_ratio'], p['landing_ratio'])
    attack, hit = (p['strike_start'], p['strike_start'] + p['strike_ratio'])
    phase = np.clip((u - takeoff) / (landing - takeoff), 0.0, 1.0)
    arc = 64 * phase ** 3 * (1 - phase) ** 3
    yaw = p['turn_deg'] * smooth((u - takeoff) / (attack - takeoff))
    rotate_joint(clip, root, [0.0, 1.0, 0.0], yaw)
    rotation = quat_to_matrix(clip.quats[:, root])
    final_rotation = quat_to_matrix(quat_from_axis_angle(np.array([0.0, 1.0, 0.0]), np.deg2rad(p['turn_deg'])))
    direction = final_rotation @ np.array([0.0, 0.0, 1.0])
    travel = p['travel'] * length * smooth(phase)[:, None] * direction
    clip.trans[:] = travel
    compression = curve([0.0, takeoff * 0.55, takeoff, landing, landing + (1 - landing) * 0.35, 1.0], [0.015, p['crouch'], 0.025, 0.025, p['landing_crouch'], 0.015], u)
    clip.trans[:, 1] = length * (p['jump_height'] * arc - compression)
    targets, contacts = ({}, {})
    for role in plan.support_roles:
        tip = plan.roles[role].joints[-1]
        offset = (plan.template.rest[tip] - center).copy()
        target = center + np.einsum('tij,j->ti', rotation, offset) + travel
        target[:, 1] = plan.ground + length * (p['jump_height'] + p['foot_height']) * arc
        targets[role] = target
        contacts[role] = (u <= takeoff) | (u >= landing)
    weapon_roles = plan.template.weapon_roles
    holder = arms[0]
    if weapon_roles:
        host = plan.roles[weapon_roles[0]].host
        holder = next((r for r in arms if host in plan.roles[r].joints), None)
        if holder is None:
            raise ValueError('the turning jump chop needs its weapon attached to a usable arm')
    rp = plan.roles[holder]
    ready = takeoff * 0.9
    rotate_joint(clip, rp.joints[1], rp.twist_axis, 90.0 * smooth(u / ready))
    strike(clip, plan, holder, at=1, raise_deg=p['raise_deg'], strike_deg=p['strike_deg'], recover_deg=0.14 * p['strike_deg'], raise_ratio=ready, hold_ratio=attack - ready, strike_ratio=p['strike_ratio'], raise_ease='return', strike_ease='impact', recover_ease='smooth')
    elbow = curve([0.0, ready, attack, hit, 1.0], [0.0, p['elbow_deg'], p['elbow_deg'], 0.0, 12.0], u, ['return', 'smooth', 'impact', 'smooth'])
    rotate_joint(clip, rp.joints[2], rp.flex_axis, elbow)
    for arm in arms:
        if arm == holder:
            continue
        rp = plan.roles[arm]
        rotate_joint(clip, rp.joints[1], rp.lift_axis, -58.0 * smooth(u / ready))
        rotate_joint(clip, rp.joints[2], rp.flex_axis, 46.0 * smooth(u / ready))
    if p['torso_twist_deg'] or p['torso_lean_deg']:
        settle = landing + (1 - landing) * 0.35
        times = [0.0, takeoff * 0.55, takeoff, attack, hit, settle, 1.0]
        twist = p['torso_twist_deg'] * np.sign(p['turn_deg']) * curve(
            times, [0.0, -1.0, -0.6, 0.0, 0.4, 0.0, 0.0], u,
        )
        lean = p['torso_lean_deg'] * curve(
            times, [0.0, 1.0, -0.3, -0.3, 0.8, 1.0, 0.0], u,
        )
        trunk = [j for j in plan.roles['trunk'].joints if j != root]
        for joint in trunk:
            rotate_joint(clip, joint, [0, 1, 0], twist / len(trunk), space='body')
            rotate_joint(clip, joint, [1, 0, 0], lean / len(trunk), space='body')
        if trunk and 'head' in plan.roles:
            head = plan.roles['head'].joints[0]
            rotate_joint(clip, head, [0, 1, 0], -0.35 * twist, space='body')
            rotate_joint(clip, head, [1, 0, 0], -0.35 * lean, space='body')
    return (clip, targets, contacts)


def _refine_jump_chop_arms(result, plan, arms, params):
    """Refine only the guard arm; preserve the primary arm's authored swing.

    Weapon attachment selects the primary arm. Clearance uses skeleton scale;
    wrist orientation, root motion and foot targets are preserved.
    """
    clip = result.clip
    source_pos, source_q = fk(clip)
    rotation = quat_to_matrix(source_q[:, root_index(clip.template)])
    ready = smooth(np.linspace(0, 1, clip.num_frames) / (params['takeoff_ratio'] * 0.9))
    primary = arms[0]
    if plan.template.weapon_roles:
        host = plan.roles[plan.template.weapon_roles[0]].host
        primary = next(role for role in arms if host in plan.roles[role].joints)
    for role in arms:
        if role == primary:
            continue
        shoulder, elbow, wrist = js = plan.roles[role].joints[-3:]
        side = 1.0 if '.L.' in role else -1.0
        delta = source_pos[:, wrist] - source_pos[:, shoulder]
        body = np.einsum('tji,tj->ti', rotation, delta)
        rest = clip.template.rest
        reach = np.linalg.norm(rest[elbow] - rest[shoulder]) + np.linalg.norm(rest[wrist] - rest[elbow])
        available = np.sqrt(np.maximum((reach * (1 - 2e-6)) ** 2 - np.sum(body[:, 1:] ** 2, axis=-1), 0.0))
        clearance = np.minimum(params['arm_clearance'] * plan.scale, available)
        offset = np.maximum(clearance - side * body[:, 0], 0.0)
        shift = np.zeros_like(body)
        shift[:, 0] = side * offset * ready
        target = source_pos[:, wrist] + np.einsum('tij,tj->ti', rotation, shift)
        direction = delta / np.maximum(np.linalg.norm(delta, axis=-1, keepdims=True), 1e-10)
        original_pole = source_pos[:, elbow] - source_pos[:, shoulder]
        original_pole -= np.sum(original_pole * direction, axis=-1, keepdims=True) * direction
        norm = np.linalg.norm(original_pole, axis=-1, keepdims=True)
        outward = np.einsum('tij,j->ti', rotation, np.array([side, -0.35, 0.25]))
        original_pole = np.where(norm > 1e-8, original_pole / np.maximum(norm, 1e-8), outward)
        pole = (1 - ready[:, None]) * original_pole + ready[:, None] * outward
        clip.quats[:, [shoulder, elbow]] = [1, 0, 0, 0]
        result.residuals[role] = solve_two_bone(clip, js, target, pole)
        _, global_q = fk(clip)
        clip.quats[:, wrist] = quat_mul(inverse(global_q[:, elbow]), source_q[:, wrist])
        result.hand_targets[role] = target
    result.notes.append('Arm clearance and outward elbow IK reduce axial twist; mesh collisions still require visual review.')


def _fit_walk_arms(clip, plan, arms, params):
    """Drive the counter-swinging arms from the actual thigh swing angle on the same side, so changing the step count does not keep a stale cadence."""
    pos, _ = fk(clip)
    ready = smooth(np.linspace(0, 1, clip.num_frames) / 0.08)
    for arm in arms:
        if len(plan.roles[arm].joints) < 4:
            raise ValueError('the arm swing recipe needs a shoulder-elbow-wrist chain')
        side = '.L.' if '.L.' in arm else '.R.'
        leg = next((r for r in plan.support_roles if side in r))
        hip, knee = plan.roles[leg].joints[:2]
        thigh = pos[:, knee] - pos[:, hip]
        angle = np.rad2deg(np.arctan2(thigh[:, 2], -thigh[:, 1]))
        rp = plan.roles[arm]
        shoulder, elbow = rp.joints[1:3]
        clip.quats[:, [shoulder, elbow]] = [1, 0, 0, 0]
        rotate_joint(clip, shoulder, rp.lift_axis, -80 * ready)
        rotate_joint(clip, shoulder, [1, 0, 0], params['arm_gain'] * angle * ready, space='body')
        rotate_joint(clip, elbow, rp.flex_axis, params['elbow_deg'] * ready)

def _fit_boxing(result, plan, arms, p):
    clip, n = (result.clip, result.clip.num_frames)
    root = root_index(plan.template)
    root_pos, _ = fk(clip)
    u = np.linspace(0, 1, n)
    for side, role in enumerate(arms):
        js = plan.roles[role].joints[1:4]
        sh, elbow, wrist = js
        length = np.linalg.norm(plan.template.rest[elbow] - plan.template.rest[sh]) + np.linalg.norm(plan.template.rest[wrist] - plan.template.rest[elbow])
        offset = plan.template.rest[sh] - plan.template.rest[root]
        guard = offset + np.array([-0.025 if side == 0 else 0.025, p['guard_height'], 0.21])
        target = np.repeat(guard[None], n, axis=0)
        ready = smooth(u / 0.16)
        start = plan.template.rest[wrist] - plan.template.rest[root]
        target = start + ready[:, None] * (target - start)
        for k, which in enumerate(p['combo']):
            if which != side:
                continue
            a, b = (0.2 + 0.8 * k / len(p['combo']), 0.2 + 0.8 * (k + 1) / len(p['combo']))
            profile = curve([0, 0.28, 0.34, 0.86, 1], [0, 1, 1, 0, 0], np.clip((u - a) / (b - a), 0, 1), ['impact', 'smooth', 'return', 'smooth'])
            target[:, 2] += (p['reach'] * length - 0.21) * profile
        target += root_pos[:, root]
        pole = np.array([0.2 if side == 0 else -0.2, -1.0, 0.0])
        result.hand_targets[role] = target
        result.residuals[role] = solve_two_bone(clip, js, target, pole)
