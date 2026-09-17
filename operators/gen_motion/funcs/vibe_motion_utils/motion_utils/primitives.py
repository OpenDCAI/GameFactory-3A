"""Apply reusable motion primitives to skeletal chains and root transforms."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable
import numpy as np
from .skeleton_templates import MotionClip, RolePlan, SkeletonPlan, global_rotations, quat_from_axis_angle, quat_identity, quat_slerp, resolve_role
EASES: tuple[str, ...] = ('linear', 'smooth', 'ease_in', 'ease_out', 'anticipate', 'impact', 'snap', 'overshoot', 'settle')
_BACK_S = 1.70158
_UP = np.array([0.0, 1.0, 0.0], dtype=np.float32)
_RIGHT = np.array([1.0, 0.0, 0.0], dtype=np.float32)
_FWD = np.array([0.0, 0.0, 1.0], dtype=np.float32)

@dataclass
class PrimitiveCall:
    """Record of one primitive call, a single line of the motion score, kept for review and diagnostics."""
    primitive: str
    role: str
    start_frame: int
    end_frame: int
    params: dict = field(default_factory=dict)

    def __str__(self) -> str:
        kv = ' '.join((f'{k}={v}' for k, v in self.params.items()))
        return f'{self.primitive}({self.role}) [{self.start_frame},{self.end_frame}] {kv}'

def ease_curve(n: int, mode: str='smooth') -> np.ndarray:
    """Interpolation curve ``(n,)`` rising from 0 to 1 across the segment, which is what gives the motion its sense of pace."""
    if mode not in EASES:
        raise ValueError(f'unknown ease {mode!r}, available {EASES}')
    if n <= 1:
        return np.ones(max(n, 1), dtype=np.float32)
    c = _ease_eval(np.linspace(0.0, 1.0, n, dtype=np.float32), mode)
    c[0], c[-1] = (0.0, 1.0)
    return c

def _ease_eval(t: np.ndarray, mode: str) -> np.ndarray:
    """Evaluate the curve at an arbitrary ``t`` in ``[0,1]``; :func:`ease_curve` is the special case on a uniform grid."""
    t = np.asarray(t, dtype=np.float32)
    if mode == 'smooth':
        c = t * t * (3.0 - 2.0 * t)
    elif mode == 'ease_in':
        c = t * t
    elif mode == 'ease_out':
        c = 1.0 - (1.0 - t) ** 2
    elif mode == 'anticipate':
        c = t * t * ((_BACK_S + 1.0) * t - _BACK_S)
    elif mode == 'impact':
        c = t ** 4
    elif mode == 'snap':
        c = 1.0 - (1.0 - t) ** 5
    elif mode == 'overshoot':
        u = t - 1.0
        c = 1.0 + u * u * ((_BACK_S + 1.0) * u + _BACK_S)
    elif mode == 'settle':
        c = 1.0 - (1.0 - t) * np.cos(2.0 * np.pi * 1.25 * t) * np.exp(-2.0 * t)
    else:
        c = t
    return np.asarray(c, dtype=np.float32)

def _keyframe_curve(n: int, keys: list[tuple[float, float]], eases: list[str] | None=None) -> np.ndarray:
    """Interpolate an ``(n,)`` curve through ``(time ratio, value)`` keyframes, allowing a different ease per segment."""
    if len(keys) < 2:
        raise ValueError(f'at least 2 keyframes are required, got {len(keys)}')
    ts = [float(k[0]) for k in keys]
    if any((b < a for a, b in zip(ts, ts[1:]))):
        raise ValueError(f'keyframe time ratios must increase, got {ts}')
    if eases is None:
        eases = ['smooth'] * (len(keys) - 1)
    if len(eases) != len(keys) - 1:
        raise ValueError(f'eases needs {len(keys) - 1} entries, got {len(eases)}')
    for e in eases:
        if e not in EASES:
            raise ValueError(f'unknown ease {e!r}, available {EASES}')
    if n <= 1:
        return np.full(max(n, 1), float(keys[-1][1]), dtype=np.float32)
    u = np.linspace(0.0, 1.0, n, dtype=np.float32)
    out = np.full(n, float(keys[0][1]), dtype=np.float32)
    for i, ((t0, v0), (t1, v1)) in enumerate(zip(keys, keys[1:])):
        m = (u >= t0) & (u <= t1) if i == len(keys) - 2 else (u >= t0) & (u < t1)
        if not m.any():
            continue
        local = (u[m] - t0) / max(t1 - t0, 1e-06)
        out[m] = v0 + (v1 - v0) * _ease_eval(local, eases[i])
    out[u > ts[-1]] = float(keys[-1][1])
    return out

def envelope(n: int, fade: int=4) -> np.ndarray:
    """Envelope ``(n,)`` for oscillating primitives: fading in and out at the ends and holding 1 in the middle."""
    if n <= 1:
        return np.ones(max(n, 1), dtype=np.float32)
    fade = int(max(0, min(fade, n // 2)))
    env = np.ones(n, dtype=np.float32)
    if fade > 0:
        ramp = ease_curve(fade, 'smooth')
        env[:fade] = ramp
        env[n - fade:] = ramp[::-1]
    return env

def _window(clip: MotionClip, start_frame: int, end_frame: int) -> np.ndarray:
    """Clamp the inclusive range ``[start, end]`` to the valid frames and return their indices."""
    t_num = clip.num_frames
    s, e = (int(start_frame), int(end_frame))
    if e < s:
        raise ValueError(f'invalid time range [{s}, {e}]: start must be <= end')
    if s >= t_num or e < 0:
        raise ValueError(f'time range [{s}, {e}] lies entirely outside the sequence [0, {t_num})')
    return np.arange(max(s, 0), min(e, t_num - 1) + 1, dtype=np.int64)

def resolve_axis(clip: MotionClip, role_plan: RolePlan, axis: str | np.ndarray | list | tuple='swing', *, frames: np.ndarray | None=None, joint: int | None=None, to_local: bool=True) -> np.ndarray:
    """Resolve an axis, given as a semantic name or an explicit vector, into an axis usable for local rotation."""
    _lift = role_plan.lift_axis if role_plan.lift_axis is not None else role_plan.swing_axis
    _flex = role_plan.flex_axis if role_plan.flex_axis is not None else _lift
    body_named = {'swing': role_plan.swing_axis, 'bend': role_plan.bend_axis, 'twist': role_plan.twist_axis, 'chain': role_plan.twist_axis, 'lift': _lift, 'flex': _flex, 'clearance': _flex if role_plan.flex_lifts else _lift}
    world_named = {'up': _UP, 'right': _RIGHT, 'forward': _FWD}
    idx = np.arange(clip.num_frames, dtype=np.int64) if frames is None else frames
    if isinstance(axis, str):
        key = axis.strip().lower()
        if key in body_named:
            vec = np.asarray(body_named[key], dtype=np.float32)
            n = float(np.linalg.norm(vec))
            if n < 1e-08:
                raise ValueError(f'the {key} axis of role {role_plan.role!r} has zero length')
            return np.broadcast_to(vec / n, (len(idx), 3)).astype(np.float32)
        if key not in world_named:
            raise ValueError(f'unknown axis name {axis!r}, available {sorted(body_named) + sorted(world_named)}, or pass a (3,) world vector')
        world = np.asarray(world_named[key], dtype=np.float32)
    else:
        world = np.asarray(axis, dtype=np.float32).reshape(-1)
        if world.shape != (3,):
            raise ValueError(f'axis must be a (3,) vector, got shape {np.shape(axis)}')
    n = float(np.linalg.norm(world))
    if n < 1e-08:
        raise ValueError(f'axis {axis!r} has zero length and cannot define a rotation')
    world = world / n
    if not to_local:
        return np.broadcast_to(world, (len(idx), 3)).astype(np.float32)
    j = int(role_plan.joints[0] if joint is None else joint)
    p = int(clip.template.parents[j])
    if p < 0:
        return np.broadcast_to(world, (len(idx), 3)).astype(np.float32)
    grot = global_rotations(clip.template.parents, clip.quats)
    local = np.einsum('tji,j->ti', grot[idx, p], world)
    ln = np.linalg.norm(local, axis=-1, keepdims=True)
    return (local / np.maximum(ln, 1e-08)).astype(np.float32)

def _taper_weights(n: int, taper: float) -> np.ndarray:
    """Weights ``(n,)`` distributing an angle along the chain, summing to 1."""
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    if n == 1:
        return np.ones(1, dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
    t = float(np.clip(taper, -1.0, 1.0))
    base = np.ones(n, dtype=np.float32) / n
    shaped = ramp if t >= 0 else ramp[::-1]
    shaped = shaped / max(float(shaped.sum()), 1e-08)
    w = (1.0 - abs(t)) * base + abs(t) * shaped
    return (w / max(float(w.sum()), 1e-08)).astype(np.float32)

def swing(clip: MotionClip, plan: SkeletonPlan, role: str, *, start_frame: int, end_frame: int, degrees: float=45.0, axis: str | np.ndarray='swing', ease: str='smooth', hold_after: bool=False, at: int=0) -> PrimitiveCall:
    """Rigid sweep of the chain from a chosen joint; the main primitive for large motions such as a tail whip, leg raise, sword swing or turning kick."""
    rid = resolve_role(clip.template, role)
    rp = plan[rid]
    frames = _window(clip, start_frame, end_frame)
    try:
        pivot = int(rp.joints[int(at)])
    except IndexError as err:
        raise IndexError(f'role {rid!r} has only {len(rp.joints)} joints, so at={at} is out of range') from err
    ang = np.deg2rad(float(degrees)) * ease_curve(len(frames), ease)
    ax = resolve_axis(clip, rp, axis, frames=frames, joint=pivot)
    clip.rotate(frames, pivot, quat_from_axis_angle(ax, ang))
    if hold_after and frames[-1] + 1 < clip.num_frames:
        tail = np.arange(frames[-1] + 1, clip.num_frames, dtype=np.int64)
        ax_t = resolve_axis(clip, rp, axis, frames=tail, joint=pivot)
        clip.rotate(tail, pivot, quat_from_axis_angle(ax_t, np.full(len(tail), ang[-1])))
    call = PrimitiveCall('swing', rid, int(frames[0]), int(frames[-1]), {'degrees': degrees, 'axis': axis if isinstance(axis, str) else 'vec', 'ease': ease, 'hold_after': hold_after, 'at': at})
    clip.log.append(str(call))
    return call

def bend(clip: MotionClip, plan: SkeletonPlan, role: str, *, start_frame: int, end_frame: int, degrees: float=60.0, axis: str | np.ndarray='bend', taper: float=0.4, ease: str='smooth', hold_after: bool=False) -> PrimitiveCall:
    """Bend distributed along the chain, for curling, arching, knee flexion or a whip-like coil."""
    rid = resolve_role(clip.template, role)
    rp = plan[rid]
    frames = _window(clip, start_frame, end_frame)
    ramp = ease_curve(len(frames), ease)
    weights = _taper_weights(len(rp.joints), taper)
    total = np.deg2rad(float(degrees))
    for w, j in zip(weights, rp.joints):
        ax = resolve_axis(clip, rp, axis, frames=frames, joint=j)
        clip.rotate(frames, j, quat_from_axis_angle(ax, total * float(w) * ramp))
        if hold_after and frames[-1] + 1 < clip.num_frames:
            tail = np.arange(frames[-1] + 1, clip.num_frames, dtype=np.int64)
            ax_t = resolve_axis(clip, rp, axis, frames=tail, joint=j)
            clip.rotate(tail, j, quat_from_axis_angle(ax_t, np.full(len(tail), total * float(w) * ramp[-1])))
    call = PrimitiveCall('bend', rid, int(frames[0]), int(frames[-1]), {'degrees': degrees, 'taper': taper, 'ease': ease})
    clip.log.append(str(call))
    return call

def twist(clip: MotionClip, plan: SkeletonPlan, role: str, *, start_frame: int, end_frame: int, degrees: float=30.0, taper: float=0.0, ease: str='smooth', hold_after: bool=False) -> PrimitiveCall:
    """Twist about the chain's own direction, for trunk rotation, wrist rotation or a spiralling tail."""
    rid = resolve_role(clip.template, role)
    rp = plan[rid]
    frames = _window(clip, start_frame, end_frame)
    ramp = ease_curve(len(frames), ease)
    weights = _taper_weights(len(rp.joints), taper)
    total = np.deg2rad(float(degrees))
    tail = np.arange(frames[-1] + 1, clip.num_frames, dtype=np.int64) if hold_after and frames[-1] + 1 < clip.num_frames else None
    for w, j in zip(weights, rp.joints):
        ax = resolve_axis(clip, rp, 'twist', frames=frames, joint=j)
        clip.rotate(frames, j, quat_from_axis_angle(ax, total * float(w) * ramp))
        if tail is not None:
            ax_t = resolve_axis(clip, rp, 'twist', frames=tail, joint=j)
            clip.rotate(tail, j, quat_from_axis_angle(ax_t, np.full(len(tail), total * float(w) * ramp[-1])))
    call = PrimitiveCall('twist', rid, int(frames[0]), int(frames[-1]), {'degrees': degrees, 'taper': taper, 'ease': ease})
    clip.log.append(str(call))
    return call

def traveling_wave(clip: MotionClip, plan: SkeletonPlan, role: str, *, start_frame: int, end_frame: int, amplitude_deg: float=22.0, cycles: float=2.0, wavelength: float=1.0, axis: str | np.ndarray='bend', phase: float=0.0, taper: float=0.3, fade: int=4) -> PrimitiveCall:
    """Sine wave travelling along the chain, the core primitive for limbless morphologies."""
    if abs(float(wavelength)) < 1e-06:
        raise ValueError('wavelength cannot be 0')
    rid = resolve_role(clip.template, role)
    rp = plan[rid]
    frames = _window(clip, start_frame, end_frame)
    n = len(frames)
    u = np.linspace(0.0, 1.0, n, dtype=np.float32) if n > 1 else np.zeros(1, np.float32)
    env = envelope(n, fade)
    weights = _taper_weights(len(rp.joints), taper) * len(rp.joints)
    amp = np.deg2rad(float(amplitude_deg))
    for i, j in enumerate(rp.joints):
        frac = i / max(len(rp.joints) - 1, 1)
        ph = 2.0 * np.pi * (float(cycles) * u - frac / float(wavelength) + float(phase))
        ang = amp * float(weights[i]) * env * np.sin(ph)
        ax = resolve_axis(clip, rp, axis, frames=frames, joint=j)
        clip.rotate(frames, j, quat_from_axis_angle(ax, ang))
    call = PrimitiveCall('traveling_wave', rid, int(frames[0]), int(frames[-1]), {'amplitude_deg': amplitude_deg, 'cycles': cycles, 'wavelength': wavelength, 'phase': phase, 'taper': taper})
    clip.log.append(str(call))
    return call

def swing_flex_deg(role_plan: RolePlan) -> float:
    """Swing-phase flexion in degrees for one limb. Vertebrate legs with a foot bone need more flexion."""
    has_foot = len(role_plan.joints) >= 4 and role_plan.flex_lifts
    return 58.0 if has_foot else 30.0

def step(clip: MotionClip, plan: SkeletonPlan, role: str, *, start_frame: int, end_frame: int, cycles: float=2.0, stride_deg: float=28.0, lift_deg: float | None=None, phase: float=0.0, duty: float=0.6, axis: str | np.ndarray='swing', fade: int=4, at: int=0, foot_level: float=1.0, mid_axis: str | np.ndarray='clearance') -> PrimitiveCall:
    """Step cycle for one limb: the building block of a gait, usable by any limb of any morphology."""
    rid = resolve_role(clip.template, role)
    rp = plan[rid]
    k = int(at)
    if len(rp.joints) < k + 2:
        raise ValueError(f'role {rid!r} has {len(rp.joints)} joints, and at least 2 are required from at={k}')
    beta = float(duty)
    if not 0.0 < beta < 1.0:
        raise ValueError(f'duty must lie in (0,1), got {duty}')
    j_swing, j_lift = (int(rp.joints[k]), int(rp.joints[k + 1]))
    lift_deg = swing_flex_deg(rp) if lift_deg is None else float(lift_deg)
    frames = _window(clip, start_frame, end_frame)
    n = len(frames)
    u = np.linspace(0.0, 1.0, n, dtype=np.float32) if n > 1 else np.zeros(1, np.float32)
    env = envelope(n, fade)
    cyc = np.mod(float(cycles) * u + float(phase), 1.0)
    half = np.deg2rad(stride_deg) * 0.5
    stance = cyc < beta
    hip = np.empty(n, dtype=np.float32)
    hip[stance] = half - 2.0 * half * (cyc[stance] / beta)
    sw = (cyc[~stance] - beta) / (1.0 - beta)
    hip[~stance] = -half + 2.0 * half * (sw * sw * (3.0 - 2.0 * sw))
    ax_hip = resolve_axis(clip, rp, axis, frames=frames, joint=j_swing)
    clip.rotate(frames, j_swing, quat_from_axis_angle(ax_hip, env * hip))
    lift = np.zeros(n, dtype=np.float32)
    lift[~stance] = np.deg2rad(lift_deg) * np.sin(np.pi * sw)
    ax_mid = resolve_axis(clip, rp, mid_axis, frames=frames, joint=j_lift)
    clip.rotate(frames, j_lift, quat_from_axis_angle(ax_mid, env * lift))
    _dorsiflex(clip, rp, frames, k, env * lift, ax_mid, foot_level)
    call = PrimitiveCall('step', rid, int(frames[0]), int(frames[-1]), {'cycles': cycles, 'stride_deg': stride_deg, 'lift_deg': lift_deg, 'phase': phase, 'duty': duty, 'axis': axis if isinstance(axis, str) else 'vec', 'at': at, 'mid_axis': mid_axis if isinstance(mid_axis, str) else 'vec'})
    clip.log.append(str(call))
    return call

def _dorsiflex(clip: MotionClip, rp: RolePlan, frames: np.ndarray, at: int, knee_ang: np.ndarray, axis: np.ndarray, amount: float) -> None:
    """Ankle dorsiflexion: counter-rotate the tip bone, the sole, as the knee flexes so it stays roughly level."""
    if amount <= 0.0 or len(rp.joints) < at + 3:
        return
    ankle = int(rp.joints[at + 2])
    clip.rotate(frames, ankle, quat_from_axis_angle(axis, -float(amount) * knee_ang))

def strike(clip: MotionClip, plan: SkeletonPlan, role: str, *, start_frame: int, end_frame: int, raise_deg: float=80.0, strike_deg: float=165.0, recover_deg: float=22.0, raise_ratio: float=0.34, hold_ratio: float=0.1, strike_ratio: float=0.26, axis: str | np.ndarray='lift', strike_axis: str | np.ndarray | None='right', raise_ease: str='ease_out', strike_ease: str='impact', recover_ease: str='overshoot', hold_after: bool=True, at: int=0) -> PrimitiveCall:
    """Raise, wind up, accelerate down, then recover: one primitive covering a whole power move."""
    r1 = float(raise_ratio)
    r2 = r1 + float(hold_ratio)
    r3 = r2 + float(strike_ratio)
    if r3 >= 1.0:
        raise ValueError(f'raise + hold + strike ratios sum to {r3:.2f}, which must stay below 1 to leave time for the recovery')
    rid = resolve_role(clip.template, role)
    rp = plan[rid]
    frames = _window(clip, start_frame, end_frame)
    n = len(frames)
    try:
        pivot = int(rp.joints[int(at)])
    except IndexError as err:
        raise IndexError(f'role {rid!r} has only {len(rp.joints)} joints, so at={at} is out of range') from err
    up = abs(float(raise_deg))
    down = abs(float(strike_deg))
    back = abs(float(recover_deg))
    if strike_axis is None:
        curves = [(axis, _keyframe_curve(n, [(0.0, 0.0), (r1, up), (r2, up), (r3, up - down), (1.0, up - down + back)], [raise_ease, 'linear', strike_ease, recover_ease]))]
    else:
        curves = [(axis, _keyframe_curve(n, [(0.0, 0.0), (r1, up), (1.0, up)], [raise_ease, 'linear'])), (strike_axis, _keyframe_curve(n, [(0.0, 0.0), (r2, 0.0), (r3, down), (1.0, down - back)], ['linear', strike_ease, recover_ease]))]
    for ax_name, curve in curves:
        ax = resolve_axis(clip, rp, ax_name, frames=frames, joint=pivot)
        clip.rotate(frames, pivot, quat_from_axis_angle(ax, np.deg2rad(curve)))
        if hold_after and frames[-1] + 1 < clip.num_frames:
            tail = np.arange(frames[-1] + 1, clip.num_frames, dtype=np.int64)
            ax_t = resolve_axis(clip, rp, ax_name, frames=tail, joint=pivot)
            clip.rotate(tail, pivot, quat_from_axis_angle(ax_t, np.full(len(tail), np.deg2rad(curve[-1]))))
    call = PrimitiveCall('strike', rid, int(frames[0]), int(frames[-1]), {'raise_deg': raise_deg, 'strike_deg': strike_deg, 'recover_deg': recover_deg, 'strike_ease': strike_ease, 'axis': axis if isinstance(axis, str) else 'vec', 'strike_axis': strike_axis if isinstance(strike_axis, str) else 'vec' if strike_axis is not None else None, 'at': at})
    clip.log.append(str(call))
    return call

def stride(clip: MotionClip, plan: SkeletonPlan, role: str, *, start_frame: int, end_frame: int, reach_deg: float=38.0, lift_deg: float=62.0, prep_deg: float=-14.0, lift_ratio: float=0.42, plant_ratio: float=0.78, hold_after: bool=True, foot_level: float=1.0) -> PrimitiveCall:
    """Lift one leg, plant it ahead and land: a single stride rather than a cyclic gait."""
    rid = resolve_role(clip.template, role)
    rp = plan[rid]
    if len(rp.joints) < 2:
        raise ValueError(f'role {rid!r} has only {len(rp.joints)} joints, and stride requires at least 2')
    frames = _window(clip, start_frame, end_frame)
    n = len(frames)
    prep_t = min(0.18, float(lift_ratio) * 0.5)
    hip = _keyframe_curve(n, [(0.0, 0.0), (prep_t, float(prep_deg)), (float(plant_ratio), float(reach_deg)), (1.0, float(reach_deg))], ['smooth', 'ease_out', 'linear'])
    ax_hip = resolve_axis(clip, rp, 'swing', frames=frames, joint=rp.joints[0])
    clip.rotate(frames, rp.joints[0], quat_from_axis_angle(ax_hip, np.deg2rad(hip)))
    knee = _keyframe_curve(n, [(0.0, 0.0), (prep_t, 0.25 * float(lift_deg)), (float(lift_ratio), float(lift_deg)), (float(plant_ratio), 0.0), (1.0, 0.0)], ['smooth', 'ease_out', 'smooth', 'linear'])
    ax_knee = resolve_axis(clip, rp, 'clearance', frames=frames, joint=rp.joints[1])
    clip.rotate(frames, rp.joints[1], quat_from_axis_angle(ax_knee, np.deg2rad(knee)))
    _dorsiflex(clip, rp, frames, 0, np.deg2rad(knee), ax_knee, foot_level)
    if hold_after and frames[-1] + 1 < clip.num_frames:
        tail = np.arange(frames[-1] + 1, clip.num_frames, dtype=np.int64)
        for joint, val, name in ((rp.joints[0], hip[-1], 'swing'), (rp.joints[1], knee[-1], 'clearance')):
            ax_t = resolve_axis(clip, rp, name, frames=tail, joint=joint)
            clip.rotate(tail, joint, quat_from_axis_angle(ax_t, np.full(len(tail), np.deg2rad(val))))
    call = PrimitiveCall('stride', rid, int(frames[0]), int(frames[-1]), {'reach_deg': reach_deg, 'lift_deg': lift_deg, 'prep_deg': prep_deg})
    clip.log.append(str(call))
    return call

def punch(clip: MotionClip, plan: SkeletonPlan, role: str, *, start_frame: int, end_frame: int, extend_deg: float=48.0, elbow_release_deg: float=87.0, rise_deg: float=38.0, out_ratio: float=0.26, hold_ratio: float=0.08, back_ratio: float=0.34, out_ease: str='impact', back_ease: str='snap', at: int=1) -> PrimitiveCall:
    """Punch: extend from the guard, hold briefly at full reach, then snap back to the guard."""
    r1 = float(out_ratio)
    r2 = r1 + float(hold_ratio)
    r3 = r2 + float(back_ratio)
    if r3 > 1.0:
        raise ValueError(f'out + hold + back ratios sum to {r3:.2f}, which must stay at or below 1')
    rid = resolve_role(clip.template, role)
    rp = plan[rid]
    k = int(at)
    if len(rp.joints) < k + 2:
        raise IndexError(f'role {rid!r} has {len(rp.joints)} joints, and at least 2 are required from at={k} for the shoulder and elbow')
    frames = _window(clip, start_frame, end_frame)
    n = len(frames)
    shoulder, elbow = (int(rp.joints[k]), int(rp.joints[k + 1]))
    keys_out = [(0.0, 0.0), (r1, 1.0), (r2, 1.0), (r3, 0.0), (1.0, 0.0)]
    profile = _keyframe_curve(n, keys_out, [out_ease, 'linear', back_ease, 'linear'])
    ax_sh = resolve_axis(clip, rp, 'swing', frames=frames, joint=shoulder)
    clip.rotate(frames, shoulder, quat_from_axis_angle(ax_sh, np.deg2rad(abs(float(extend_deg)) * profile)))
    if abs(float(rise_deg)) > 1e-06:
        ax_up = resolve_axis(clip, rp, 'right', frames=frames, joint=shoulder)
        clip.rotate(frames, shoulder, quat_from_axis_angle(ax_up, np.deg2rad(-abs(float(rise_deg)) * profile)))
    ax_el = resolve_axis(clip, rp, 'flex', frames=frames, joint=elbow)
    clip.rotate(frames, elbow, quat_from_axis_angle(ax_el, np.deg2rad(-abs(float(elbow_release_deg)) * profile)))
    call = PrimitiveCall('punch', rid, int(frames[0]), int(frames[-1]), {'extend_deg': extend_deg, 'elbow_release_deg': elbow_release_deg, 'rise_deg': rise_deg, 'out_ratio': out_ratio, 'out_ease': out_ease, 'back_ease': back_ease, 'at': at})
    clip.log.append(str(call))
    return call

def aim(clip: MotionClip, plan: SkeletonPlan, role: str, *, start_frame: int, end_frame: int, direction: np.ndarray | list | tuple=(0.0, 0.0, 1.0), ease: str='smooth', hold_after: bool=False) -> PrimitiveCall:
    """Point the whole chain along a direction, for looking, pointing or aiming."""
    rid = resolve_role(clip.template, role)
    rp = plan[rid]
    tgt = np.asarray(direction, dtype=np.float32).reshape(-1)
    if tgt.shape != (3,):
        raise ValueError(f'direction must be a (3,) vector, got {np.shape(direction)}')
    n = float(np.linalg.norm(tgt))
    if n < 1e-08:
        raise ValueError('direction has zero length and cannot define an aim')
    tgt = tgt / n
    src = rp.rest_dir
    axis_w = np.cross(src, tgt)
    an = float(np.linalg.norm(axis_w))
    dot = float(np.clip(np.dot(src, tgt), -1.0, 1.0))
    if an < 1e-06:
        if dot > 0:
            call = PrimitiveCall('aim', rid, int(start_frame), int(end_frame), {'direction': tgt.tolist(), 'note': 'already aligned, no operation'})
            clip.log.append(str(call))
            return call
        axis_w = np.cross(src, _UP)
        if float(np.linalg.norm(axis_w)) < 1e-06:
            axis_w = np.cross(src, _RIGHT)
    angle = float(np.arccos(dot))
    frames = _window(clip, start_frame, end_frame)
    ramp = ease_curve(len(frames), ease)
    ax = resolve_axis(clip, rp, axis_w, frames=frames)
    clip.rotate(frames, rp.joints[0], quat_from_axis_angle(ax, angle * ramp))
    if hold_after and frames[-1] + 1 < clip.num_frames:
        tail = np.arange(frames[-1] + 1, clip.num_frames, dtype=np.int64)
        ax_t = resolve_axis(clip, rp, axis_w, frames=tail)
        clip.rotate(tail, rp.joints[0], quat_from_axis_angle(ax_t, np.full(len(tail), angle)))
    call = PrimitiveCall('aim', rid, int(frames[0]), int(frames[-1]), {'direction': tgt.tolist(), 'degrees': float(np.rad2deg(angle))})
    clip.log.append(str(call))
    return call

def root_turn(clip: MotionClip, plan: SkeletonPlan, role: str='trunk', *, start_frame: int, end_frame: int, degrees: float=90.0, axis: str | np.ndarray='up', ease: str='smooth', hold_after: bool=True) -> PrimitiveCall:
    """Turn the whole body by applying yaw at the root joint."""
    rid = resolve_role(clip.template, role)
    rp = plan[rid]
    root = int(np.where(np.asarray(clip.template.parents) < 0)[0][0])
    frames = _window(clip, start_frame, end_frame)
    ang = np.deg2rad(float(degrees)) * ease_curve(len(frames), ease)
    ax = resolve_axis(clip, rp, axis, frames=frames, joint=root)
    clip.rotate(frames, root, quat_from_axis_angle(ax, ang))
    if hold_after and frames[-1] + 1 < clip.num_frames:
        tail = np.arange(frames[-1] + 1, clip.num_frames, dtype=np.int64)
        ax_t = resolve_axis(clip, rp, axis, frames=tail, joint=root)
        clip.rotate(tail, root, quat_from_axis_angle(ax_t, np.full(len(tail), ang[-1])))
    call = PrimitiveCall('root_turn', rid, int(frames[0]), int(frames[-1]), {'degrees': degrees, 'ease': ease})
    clip.log.append(str(call))
    return call

def root_translate(clip: MotionClip, plan: SkeletonPlan, role: str='trunk', *, start_frame: int, end_frame: int, offset: np.ndarray | list | tuple=(0.0, 0.0, 1.0), ease: str='smooth', hold_after: bool=True) -> PrimitiveCall:
    """Translate the whole body, easing the root translation from 0 to ``offset`` across the segment."""
    rid = resolve_role(clip.template, role)
    off = np.asarray(offset, dtype=np.float32).reshape(-1)
    if off.shape != (3,):
        raise ValueError(f'offset must be a (3,) vector, got {np.shape(offset)}')
    frames = _window(clip, start_frame, end_frame)
    ramp = ease_curve(len(frames), ease)
    clip.translate(frames, off[None] * ramp[:, None])
    if hold_after and frames[-1] + 1 < clip.num_frames:
        tail = np.arange(frames[-1] + 1, clip.num_frames, dtype=np.int64)
        clip.translate(tail, np.broadcast_to(off, (len(tail), 3)))
    call = PrimitiveCall('root_translate', rid, int(frames[0]), int(frames[-1]), {'offset': off.tolist(), 'ease': ease})
    clip.log.append(str(call))
    return call

def root_bob(clip: MotionClip, plan: SkeletonPlan, role: str='trunk', *, start_frame: int, end_frame: int, amplitude: float=0.04, cycles: float=2.0, phase: float=0.0, axis: np.ndarray | list | tuple=(0.0, 1.0, 0.0), fade: int=4) -> PrimitiveCall:
    """Periodic root motion, for the bob of a walk, breathing or a hovering drift."""
    rid = resolve_role(clip.template, role)
    ax = np.asarray(axis, dtype=np.float32).reshape(-1)
    if ax.shape != (3,):
        raise ValueError(f'axis must be a (3,) vector, got {np.shape(axis)}')
    frames = _window(clip, start_frame, end_frame)
    n = len(frames)
    u = np.linspace(0.0, 1.0, n, dtype=np.float32) if n > 1 else np.zeros(1, np.float32)
    env = envelope(n, fade)
    wave = float(amplitude) * env * np.sin(2.0 * np.pi * (float(cycles) * u + float(phase)))
    clip.translate(frames, ax[None] * wave[:, None])
    call = PrimitiveCall('root_bob', rid, int(frames[0]), int(frames[-1]), {'amplitude': amplitude, 'cycles': cycles, 'phase': phase})
    clip.log.append(str(call))
    return call

@dataclass(frozen=True)
class PrimitiveSpec:
    """A primitive function together with the keyword names it accepts."""
    name: str
    params: frozenset
    fn: Callable
_COMMON = ('role', 'start_frame', 'end_frame')

def _spec(name: str, fn: Callable, *params: str) -> PrimitiveSpec:
    """Register one primitive, adding the keywords every primitive accepts."""
    return PrimitiveSpec(name, frozenset(_COMMON + params), fn)
PRIMITIVES: dict[str, PrimitiveSpec] = {spec.name: spec for spec in (
    _spec('swing', swing, 'degrees', 'axis', 'ease', 'hold_after', 'at'),
    _spec('strike', strike, 'raise_deg', 'strike_deg', 'recover_deg', 'raise_ratio',
          'hold_ratio', 'strike_ratio', 'axis', 'strike_axis', 'at', 'raise_ease',
          'strike_ease', 'recover_ease', 'hold_after'),
    _spec('punch', punch, 'extend_deg', 'elbow_release_deg', 'rise_deg', 'out_ratio',
          'hold_ratio', 'back_ratio', 'out_ease', 'back_ease', 'at'),
    _spec('stride', stride, 'reach_deg', 'lift_deg', 'prep_deg', 'lift_ratio',
          'plant_ratio', 'foot_level', 'hold_after'),
    _spec('bend', bend, 'degrees', 'axis', 'taper', 'ease', 'hold_after'),
    _spec('twist', twist, 'degrees', 'taper', 'ease', 'hold_after'),
    _spec('traveling_wave', traveling_wave, 'amplitude_deg', 'cycles', 'wavelength',
          'axis', 'phase', 'taper', 'fade'),
    _spec('step', step, 'cycles', 'stride_deg', 'lift_deg', 'phase', 'duty', 'axis',
          'fade', 'at', 'foot_level', 'mid_axis'),
    _spec('aim', aim, 'direction', 'ease', 'hold_after'),
    _spec('root_turn', root_turn, 'degrees', 'axis', 'ease', 'hold_after'),
    _spec('root_translate', root_translate, 'offset', 'ease', 'hold_after'),
    _spec('root_bob', root_bob, 'amplitude', 'cycles', 'phase', 'axis', 'fade'),
)}

def apply_primitive(clip: MotionClip, plan: SkeletonPlan, name: str, role: str, params: dict) -> PrimitiveCall:
    """Call a primitive by name, rejecting unknown keyword names."""
    if name not in PRIMITIVES:
        raise ValueError(f'unknown primitive {name!r}, available {sorted(PRIMITIVES)}')
    spec = PRIMITIVES[name]
    allowed = set(spec.params) - {'role'}
    unknown = set(params) - allowed
    if unknown:
        raise ValueError(f'primitive {name!r} rejects parameters {sorted(unknown)}; accepted parameters: {sorted(allowed)}')
    if 'start_frame' not in params or 'end_frame' not in params:
        raise ValueError(f'primitive {name!r} is missing start_frame / end_frame')
    return spec.fn(clip, plan, role, **params)


def follow_through(clip: MotionClip, plan: SkeletonPlan, *, roles: list[str] | None=None, lag_frames: int=3, strength: float=0.7) -> MotionClip:
    """Follow-through lag: the further along the chain a joint sits, the later its rotation happens."""
    if roles is None:
        roles = [r for r, rp in plan.roles.items() if rp.kind in ('trunk', 'tail', 'limb', 'weapon')]
    s = float(np.clip(strength, 0.0, 1.0))
    if s <= 0.0 or lag_frames <= 0:
        return clip
    for role in roles:
        rp = plan.roles.get(role)
        if rp is None or len(rp.joints) < 2:
            continue
        n = len(rp.joints)
        for i, j in enumerate(rp.joints):
            lag = int(round(lag_frames * i / (n - 1)))
            if lag <= 0:
                continue
            src = clip.quats[:, j]
            shifted = np.concatenate([np.repeat(src[:1], lag, axis=0), src[:-lag]], axis=0)
            clip.quats[:, j] = quat_slerp(src, shifted, s)
    clip.log.append(f'refine.follow_through lag={lag_frames} strength={s}')
    return clip

def counter_rotate(clip: MotionClip, plan: SkeletonPlan, *, strength: float=0.25, trunk_role: str='trunk', max_angle_deg: float=150.0) -> MotionClip:
    """Counter-rotation: the trunk rotates slightly against the limb motion."""
    rp = plan.roles.get(trunk_role)
    s = float(np.clip(strength, 0.0, 1.0))
    if rp is None or s <= 0.0:
        return clip
    root = int(np.where(np.asarray(clip.template.parents) < 0)[0][0])
    ident = quat_identity(clip.num_frames)
    limit = float(np.cos(np.deg2rad(max_angle_deg) * 0.5))
    skipped = 0
    for j in rp.joints:
        if j == root:
            continue
        q = clip.quats[:, j]
        safe = np.abs(q[:, 0]) >= limit
        skipped += int((~safe).sum())
        mixed = quat_slerp(q, ident, s)
        clip.quats[:, j] = np.where(safe[:, None], mixed, q)
    note = f' skipped {skipped} frames with large turn angles' if skipped else ''
    clip.log.append(f'refine.counter_rotate strength={s}{note}')
    return clip

def plant_feet(clip: MotionClip, plan: SkeletonPlan, *, contact_ratio: float=0.04, strength: float=1.0, drive: bool=True) -> MotionClip:
    """Drive translation from the steps themselves, removing foot skate at its source."""
    support = plan.support_roles
    s = float(np.clip(strength, 0.0, 1.0))
    if not support or s <= 0.0 or clip.num_frames < 2:
        return clip
    tips = [plan.roles[r].joints[-1] for r in support if plan.roles.get(r)]
    if not tips:
        return clip
    if drive:
        authored = clip.trans[:, [0, 2]].copy()
        clip.trans[:, 0] = 0.0
        clip.trans[:, 2] = 0.0
    pos = clip.positions()
    basis = plan.support_length if plan.support_length > 1e-06 else plan.scale
    thresh = plan.ground + float(contact_ratio) * basis
    contact = pos[:, tips, 1] < thresh
    tip_idx = np.asarray(tips, dtype=np.int64)
    corr = np.zeros((clip.num_frames, 3), dtype=np.float32)
    acc = np.zeros(3, dtype=np.float32)
    for t in range(1, clip.num_frames):
        both = np.where(contact[t] & contact[t - 1])[0]
        if both.size:
            js = tip_idx[both]
            slip = (pos[t, js] - pos[t - 1, js]).mean(axis=0)
            acc = acc - s * np.array([slip[0], 0.0, slip[2]], dtype=np.float32)
        corr[t] = acc
    clip.trans = (clip.trans + corr).astype(np.float32)
    if drive:
        travelled = float(np.linalg.norm(clip.trans[-1, [0, 2]] - clip.trans[0, [0, 2]]))
        wanted = float(np.linalg.norm(authored[-1] - authored[0]))
        clip.log.append(f'refine.plant_feet drive=True stride-driven travel {travelled:.3f} (requested {wanted:.3f})')
    else:
        clip.log.append(f'refine.plant_feet drive=False strength={s}')
    return clip

def clamp_ground(clip: MotionClip, plan: SkeletonPlan, *, allow_airborne: bool=True) -> MotionClip:
    """Remove ground penetration by lifting the whole clip until the lowest joint sits on or above the ground."""
    pos = clip.positions()
    lowest = float(pos[..., 1].min())
    delta = plan.ground - lowest
    if allow_airborne and delta <= 0.0:
        return clip
    clip.trans = (clip.trans + np.array([0.0, delta, 0.0], np.float32)).astype(np.float32)
    clip.log.append(f'refine.clamp_ground lift={delta:.4f}')
    return clip

def polish(clip: MotionClip, plan: SkeletonPlan, *, lag_frames: int=3, follow_strength: float=0.6, counter_strength: float=0.2, plant_strength: float=1.0, drive_locomotion: bool=True, ground: bool=True) -> MotionClip:
    """Run the full refinement set in the recommended order."""
    follow_through(clip, plan, lag_frames=lag_frames, strength=follow_strength)
    if counter_strength > 0:
        counter_rotate(clip, plan, strength=counter_strength)
    if plant_strength > 0:
        plant_feet(clip, plan, strength=plant_strength, drive=drive_locomotion)
    if ground:
        clamp_ground(clip, plan)
    return clip
