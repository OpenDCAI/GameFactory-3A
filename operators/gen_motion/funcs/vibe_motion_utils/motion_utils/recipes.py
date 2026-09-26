"""Define action intents and assemble them into reusable motion recipes."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable
import numpy as np
from .primitives import swing_flex_deg
from .skeleton_templates import SkeletonPlan

@dataclass
class ActionIntent:
    """One action intent, equal to a single primitive call."""
    primitive: str
    role: str
    params: dict = field(default_factory=dict)

    def __str__(self) -> str:
        kv = ' '.join((f'{k}={v}' for k, v in self.params.items() if k not in ('start_frame', 'end_frame')))
        s, e = (self.params.get('start_frame', 0), self.params.get('end_frame', 0))
        return f'{self.primitive}({self.role})[{s},{e}] {kv}'

@dataclass
class MotionSegment:
    """One motion segment, a single semantic action unit such as a spinning kick."""
    name: str
    num_frames: int
    intents: list[ActionIntent] = field(default_factory=list)
Recipe = Callable[[SkeletonPlan, int], list[ActionIntent]]

def _support_or_all_limbs(plan: SkeletonPlan) -> list[str]:
    """Prefer support limbs, falling back to every limb when none carry weight."""
    if plan.support_roles:
        return list(plan.support_roles)
    return list(plan.template.limb_roles)

def _has(plan: SkeletonPlan, role: str) -> bool:
    return role in plan.roles

def _arm_roles(plan: SkeletonPlan) -> list[str]:
    """Non-support limbs, meaning arms. Returns two upper limbs for humanoids and nothing for quadrupeds, myriapods or serpentines."""
    support = set(plan.support_roles)
    return [r for r in plan.template.limb_roles if r not in support]
_SHOULDER_EXT_ROT_DEG = 90.0

def _swing_knee_deg(plan: SkeletonPlan, role: str) -> float:
    """Swing-phase flexion in degrees for one limb; the rule lives in :func:`~generate.swing_flex_deg`."""
    rp = plan.roles.get(role)
    return 30.0 if rp is None else swing_flex_deg(rp)
_ARM_REST_DOWN_DEG = -80.0

def _arm_swing_intents(plan: SkeletonPlan, n: int, *, cycles: float, leg_phase: dict[str, float], amplitude: float=24.0, elbow_deg: float=22.0, elbow_hold_deg: float=34.0) -> list[ActionIntent]:
    """Arm swing while walking: lower the arms, hold a bent elbow, then swing out of phase with the leg on the same side while the elbow flexes with each step."""
    arms = _arm_roles(plan)
    if not arms:
        return []
    legs = _support_or_all_limbs(plan)
    out: list[ActionIntent] = []
    for arm in arms[:2]:
        rp = plan.roles[arm]
        side = '.L.' if '.L.' in arm else '.R.'
        same_side_leg = next((r for r in legs if side in r), None)
        base = leg_phase.get(same_side_leg, 0.0 if side == '.L.' else 0.5)
        out.append(ActionIntent('swing', arm, {'start_frame': 0, 'end_frame': max(int(n * 0.22), 1), 'degrees': _ARM_REST_DOWN_DEG, 'axis': 'lift', 'at': 1, 'ease': 'ease_out', 'hold_after': True}))
        if len(rp.joints) > 2:
            out.append(ActionIntent('swing', arm, {'start_frame': 0, 'end_frame': max(int(n * 0.25), 1), 'degrees': float(elbow_hold_deg), 'axis': 'flex', 'at': 2, 'ease': 'ease_out', 'hold_after': True}))
        out.append(ActionIntent('step', arm, {'start_frame': 0, 'end_frame': n - 1, 'cycles': cycles, 'stride_deg': float(amplitude), 'lift_deg': float(elbow_deg), 'axis': 'right', 'phase': (base + 0.5) % 1.0, 'fade': 6, 'at': 1, 'mid_axis': 'flex', 'foot_level': 0.0}))
    return out

def _recipe_walk(plan: SkeletonPlan, n: int, *, speed: float=1.0) -> list[ActionIntent]:
    """Walk or crawl. Phases are spread over however many support limbs exist, so one recipe covers all five morphologies."""
    limbs = _support_or_all_limbs(plan)
    cycles = max(1.0, round(2.0 * speed * n / 40.0))
    stride = min(26.0 * (0.6 + 0.4 * speed), _max_stride_deg(plan))
    out: list[ActionIntent] = []
    if not limbs:
        out.append(ActionIntent('traveling_wave', 'trunk', {'start_frame': 0, 'end_frame': n - 1, 'amplitude_deg': 20.0, 'cycles': cycles, 'wavelength': 1.0, 'axis': 'bend', 'taper': 0.3}))
        out.append(ActionIntent('root_translate', 'trunk', {'start_frame': 0, 'end_frame': n - 1, 'offset': [0.0, 0.0, 0.9 * speed]}))
        return out
    left = [r for r in limbs if '.L.' in r]
    right = [r for r in limbs if '.R.' in r]
    if len(limbs) == 4 and len(left) == 2 and (len(right) == 2):
        phases = {left[0]: 0.0, right[1]: 0.0, right[0]: 0.5, left[1]: 0.5}
    elif len(limbs) == 2:
        phases = {limbs[0]: 0.0, limbs[1]: 0.5}
    else:
        phases = {}
        for group in (left, right):
            for i, r in enumerate(group):
                phases[r] = i / max(len(group), 1) % 1.0
        for r in right:
            phases[r] = (phases[r] + 0.5) % 1.0
    for role, ph in phases.items():
        out.append(ActionIntent('step', role, {'start_frame': 0, 'end_frame': n - 1, 'cycles': cycles, 'stride_deg': stride, 'lift_deg': _swing_knee_deg(plan, role), 'phase': ph}))
    out.extend(_arm_swing_intents(plan, n, cycles=cycles, leg_phase=phases, amplitude=20.0 + 10.0 * speed))
    out.append(ActionIntent('root_translate', 'trunk', {'start_frame': 0, 'end_frame': n - 1, 'offset': [0.0, 0.0, 0.7 * speed * cycles]}))
    out.append(ActionIntent('root_bob', 'trunk', {'start_frame': 0, 'end_frame': n - 1, 'amplitude': 0.02 * plan.scale, 'cycles': 2.0 * cycles}))
    return out

def _support_spacing(plan: SkeletonPlan) -> float:
    """Minimum horizontal spacing between adjacent support limb tips in the rest pose."""
    limbs = _support_or_all_limbs(plan)
    tips = [plan.roles[r].joints[-1] for r in limbs if plan.roles.get(r)]
    if len(tips) < 2:
        return float('inf')
    xz = plan.template.rest[tips][:, [0, 2]]
    d = np.linalg.norm(xz[:, None] - xz[None], axis=-1)
    np.fill_diagonal(d, np.inf)
    return float(d.min())

def _max_stride_deg(plan: SkeletonPlan, cap_deg: float=32.0, use: float=0.8) -> float:
    """Stride limit in degrees, bounded by the spacing between adjacent legs."""
    lim = plan.support_length
    if lim <= 1e-06:
        return cap_deg
    max_disp = min(use * _support_spacing(plan), 2.0 * lim * np.sin(np.deg2rad(cap_deg) * 0.5))
    return float(np.rad2deg(2.0 * np.arcsin(np.clip(max_disp / (2.0 * lim), 0.0, 1.0))))

def _recipe_pounce(plan: SkeletonPlan, n: int) -> list[ActionIntent]:
    """Pounce or leap: crouch to prepare, launch, then land."""
    prep, air = (int(n * 0.3), int(n * 0.75))
    out = [ActionIntent('bend', 'trunk', {'start_frame': 0, 'end_frame': prep, 'degrees': 25.0, 'axis': 'swing', 'taper': -0.2, 'ease': 'ease_in', 'hold_after': True}), ActionIntent('bend', 'trunk', {'start_frame': air, 'end_frame': n - 1, 'degrees': -25.0, 'axis': 'swing', 'taper': -0.2, 'ease': 'ease_out'}), ActionIntent('root_bob', 'trunk', {'start_frame': 0, 'end_frame': prep, 'amplitude': -0.08 * plan.scale, 'cycles': 0.5}), ActionIntent('root_translate', 'trunk', {'start_frame': prep, 'end_frame': air, 'offset': [0.0, 0.35 * plan.scale, 1.1], 'ease': 'ease_out'}), ActionIntent('root_translate', 'trunk', {'start_frame': air, 'end_frame': n - 1, 'offset': [0.0, -0.35 * plan.scale, 0.35], 'ease': 'ease_in'})]
    for r in _support_or_all_limbs(plan):
        out.append(ActionIntent('swing', r, {'start_frame': prep, 'end_frame': air, 'degrees': -45.0, 'axis': 'swing', 'ease': 'ease_out', 'hold_after': True}))
        out.append(ActionIntent('swing', r, {'start_frame': air, 'end_frame': n - 1, 'degrees': 45.0, 'axis': 'swing', 'ease': 'ease_out'}))
    return out

def _recipe_swing_weapon(plan: SkeletonPlan, n: int, *, strike_deg: float=165.0, raise_deg: float=80.0, strike_ratio: float=0.26, step_in: bool=True) -> list[ActionIntent]:
    """Stepping chop: raise the weapon overhead while stepping forward, accelerate down, then recover."""
    raise_ratio, hold_ratio = (0.34, 0.1)
    strike_start = raise_ratio + hold_ratio
    arms = _arm_roles(plan)
    has_weapon = _has(plan, 'weapon.0')
    holder, at = (None, 0)
    if arms:
        if has_weapon:
            host = plan.roles['weapon.0'].host
            holder = next((r for r in arms if host in plan.roles[r].joints), arms[0])
        else:
            holder = arms[0]
        at = 1 if len(plan.roles[holder].joints) > 1 else 0
    elif has_weapon:
        holder = 'weapon.0'
    else:
        holder = (plan.template.limb_roles or ['trunk'])[0]
    out: list[ActionIntent] = []
    if holder in arms and len(plan.roles[holder].joints) > 2:
        out.append(ActionIntent('swing', holder, {'start_frame': 0, 'end_frame': max(int(n * raise_ratio), 1), 'degrees': _SHOULDER_EXT_ROT_DEG, 'axis': 'twist', 'at': at, 'ease': 'ease_out', 'hold_after': True}))
    out.append(ActionIntent('strike', holder, {'start_frame': 0, 'end_frame': n - 1, 'raise_deg': 92.0, 'strike_deg': strike_deg, 'recover_deg': 0.14 * strike_deg, 'raise_ratio': raise_ratio, 'hold_ratio': hold_ratio, 'strike_ratio': strike_ratio, 'axis': 'lift', 'strike_axis': 'right', 'at': at, 'raise_ease': 'ease_out', 'strike_ease': 'impact', 'recover_ease': 'overshoot'}))
    for other in arms:
        if other != holder:
            out.append(ActionIntent('swing', other, {'start_frame': 0, 'end_frame': int(n * 0.6), 'degrees': -58.0, 'axis': 'lift', 'at': 1, 'ease': 'smooth', 'hold_after': True}))
            if len(plan.roles[other].joints) > 2:
                out.append(ActionIntent('swing', other, {'start_frame': 0, 'end_frame': int(n * 0.6), 'degrees': 46.0, 'axis': 'flex', 'at': 2, 'ease': 'smooth', 'hold_after': True}))
    if holder in arms and len(plan.roles[holder].joints) > 2:
        strike_end = strike_start + strike_ratio
        out.append(ActionIntent('swing', holder, {'start_frame': 0, 'end_frame': max(int(n * strike_start), 1), 'degrees': 52.0, 'axis': 'flex', 'at': 2, 'ease': 'ease_out', 'hold_after': True}))
        out.append(ActionIntent('swing', holder, {'start_frame': int(n * strike_start), 'end_frame': int(n * strike_end), 'degrees': -52.0, 'axis': 'flex', 'at': 2, 'ease': 'impact', 'hold_after': True}))
        out.append(ActionIntent('swing', holder, {'start_frame': int(n * strike_end), 'end_frame': n - 1, 'degrees': 16.0, 'axis': 'flex', 'at': 2, 'ease': 'smooth', 'hold_after': True}))
    legs = _support_or_all_limbs(plan)
    if step_in and legs:
        front = legs[0]
        out.append(ActionIntent('stride', front, {'start_frame': 0, 'end_frame': n - 1, 'reach_deg': 34.0, 'lift_deg': 58.0, 'prep_deg': -14.0, 'lift_ratio': 0.3, 'plant_ratio': max(strike_start - 0.02, 0.35)}))
    out.append(ActionIntent('twist', 'trunk', {'start_frame': 0, 'end_frame': max(int(n * strike_start), 1), 'degrees': -26.0, 'taper': 0.4, 'ease': 'anticipate', 'hold_after': True}))
    out.append(ActionIntent('twist', 'trunk', {'start_frame': int(n * strike_start), 'end_frame': n - 1, 'degrees': 34.0, 'taper': 0.4, 'ease': 'impact'}))
    out.append(ActionIntent('bend', 'trunk', {'start_frame': int(n * strike_start), 'end_frame': int(n * (strike_start + strike_ratio)), 'degrees': 16.0, 'axis': 'swing', 'taper': -0.4, 'ease': 'impact', 'hold_after': True}))
    return out
_GUARD_SHOULDER_DEG, _GUARD_DROP_DEG, _GUARD_ELBOW_DEG = (40.0, -52.0, 92.0)

def _guard_intents(plan: SkeletonPlan, arms: list[str], upto: int) -> list[ActionIntent]:
    """Raise a boxing guard: both hands beside the chin with bent elbows, held to the end of the sequence."""
    out: list[ActionIntent] = []
    for arm in arms[:2]:
        rp = plan.roles[arm]
        end = max(upto, 1)
        out.append(ActionIntent('swing', arm, {'start_frame': 0, 'end_frame': end, 'degrees': _GUARD_DROP_DEG, 'axis': 'lift', 'at': 1, 'ease': 'ease_out', 'hold_after': True}))
        out.append(ActionIntent('swing', arm, {'start_frame': 0, 'end_frame': end, 'degrees': _GUARD_SHOULDER_DEG, 'axis': 'swing', 'at': 1, 'ease': 'ease_out', 'hold_after': True}))
        if len(rp.joints) > 2:
            out.append(ActionIntent('swing', arm, {'start_frame': 0, 'end_frame': end, 'degrees': _GUARD_ELBOW_DEG, 'axis': 'flex', 'at': 2, 'ease': 'ease_out', 'hold_after': True}))
    return out

def _recipe_boxing(plan: SkeletonPlan, n: int, *, combo: tuple[int, ...]=(0, 1), hook: bool=False) -> list[ActionIntent]:
    """Boxing: raise the guard, throw the punches in order, then return to guard. The trunk and rear leg contribute power."""
    arms = _arm_roles(plan)
    if not arms:
        return _recipe_pounce(plan, n)
    settle = max(int(n * 0.16), 2)
    out = _guard_intents(plan, arms, settle)
    legs = _support_or_all_limbs(plan)
    for i, leg in enumerate(legs[:2]):
        out.append(ActionIntent('swing', leg, {'start_frame': 0, 'end_frame': settle, 'degrees': 14.0 if i == 0 else -12.0, 'axis': 'swing', 'ease': 'ease_out', 'hold_after': True}))
    out.append(ActionIntent('root_bob', 'trunk', {'start_frame': 0, 'end_frame': n - 1, 'amplitude': 0.012 * plan.scale, 'cycles': max(len(combo), 1) * 1.0}))
    span = max((n - settle) / max(len(combo), 1), 6.0)
    for k, which in enumerate(combo):
        arm = arms[which % len(arms)]
        s = int(settle + k * span)
        e = min(int(settle + (k + 1) * span) - 1, n - 1)
        if e - s < 4:
            continue
        is_hook = hook and k == len(combo) - 1
        out.append(ActionIntent('punch', arm, {'start_frame': s, 'end_frame': e, 'at': 1, 'extend_deg': 30.0 if is_hook else 48.0, 'elbow_release_deg': 30.0 if is_hook else _GUARD_ELBOW_DEG - 6.0, 'out_ratio': 0.24, 'hold_ratio': 0.08, 'back_ratio': 0.34, 'out_ease': 'impact', 'back_ease': 'snap'}))
        sign = -1.0 if '.R.' in arm else 1.0
        peak = int(s + 0.32 * (e - s))
        amp = 30.0 if is_hook else 20.0
        out.append(ActionIntent('twist', 'trunk', {'start_frame': s, 'end_frame': peak, 'degrees': sign * amp, 'taper': 0.35, 'ease': 'impact', 'hold_after': True}))
        out.append(ActionIntent('twist', 'trunk', {'start_frame': peak, 'end_frame': e, 'degrees': -sign * amp, 'taper': 0.35, 'ease': 'snap', 'hold_after': True}))
        if len(legs) > 1:
            out.append(ActionIntent('swing', legs[1], {'start_frame': s, 'end_frame': peak, 'degrees': -10.0, 'axis': 'swing', 'ease': 'impact', 'hold_after': True}))
            out.append(ActionIntent('swing', legs[1], {'start_frame': peak, 'end_frame': e, 'degrees': 10.0, 'axis': 'swing', 'ease': 'snap', 'hold_after': True}))
    return out

def _recipe_stride_forward(plan: SkeletonPlan, n: int) -> list[ActionIntent]:
    """Lift one leg and plant it ahead in a single long stride or lunge, while the other leg pushes off and the arms follow."""
    legs = _support_or_all_limbs(plan)
    if not legs:
        return _recipe_walk(plan, n, speed=0.8)
    out = [ActionIntent('stride', legs[0], {'start_frame': 0, 'end_frame': n - 1, 'reach_deg': 42.0, 'lift_deg': 66.0, 'prep_deg': -16.0, 'lift_ratio': 0.4, 'plant_ratio': 0.8})]
    if len(legs) > 1:
        out.append(ActionIntent('swing', legs[1], {'start_frame': int(n * 0.35), 'end_frame': n - 1, 'degrees': -18.0, 'axis': 'swing', 'ease': 'ease_out', 'hold_after': True}))
    out.append(ActionIntent('root_translate', 'trunk', {'start_frame': 0, 'end_frame': n - 1, 'offset': [0.0, 0.0, 0.55 * plan.support_length], 'ease': 'ease_out'}))
    out.append(ActionIntent('root_bob', 'trunk', {'start_frame': 0, 'end_frame': n - 1, 'amplitude': 0.035 * plan.scale, 'cycles': 1.0}))
    for arm in _arm_roles(plan)[:2]:
        out.append(ActionIntent('swing', arm, {'start_frame': 0, 'end_frame': max(int(n * 0.25), 1), 'degrees': _ARM_REST_DOWN_DEG, 'axis': 'lift', 'at': 1, 'ease': 'ease_out', 'hold_after': True}))
        out.append(ActionIntent('swing', arm, {'start_frame': 0, 'end_frame': n - 1, 'degrees': 28.0 if '.L.' in arm else -28.0, 'axis': 'right', 'at': 1, 'ease': 'smooth', 'hold_after': True}))
    return out
RECIPES: dict[str, Recipe] = {'walk': _recipe_walk, 'swing_weapon': _recipe_swing_weapon, 'stride_forward': _recipe_stride_forward, 'boxing': _recipe_boxing}
