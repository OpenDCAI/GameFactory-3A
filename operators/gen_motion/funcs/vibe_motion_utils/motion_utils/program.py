"""Typed operators over arbitrary part IDs; action data is supplied by callers (JSON motions[*].program).

Time references, phases and beats: see timing.py. Lengths: root curves and effector/translate offsets in mean leg
lengths unless stated ('units'). Axes: body frame x = left, y = up, z = forward (program.forward). Angles in degrees.

Curve spec (any scalar track; values may be numbers or style references '$turn_degrees' / {'style', 'factor'}):
  {'keys': [t...], 'values': [v...], 'interp'?, 'eases'?, 'slopes'?}      key frames
        interp: pchip (default; monotone, velocity 0 at every extremum) | linear | smooth (quintic per interval:
        pose-to-pose holds) | cubic (C2 natural spline: momentum carries through keys, may overshoot) | step;
        eases: one ease name (or null) per key interval overriding single intervals (anticipation / overshoot /
        snap into one pose); slopes: Hermite tangents (exclusive with interp / eases).
  {'segments': [{'start'|'phase', 'end', 'to', 'from'?, 'ease'?, 'apex'?}, ...], 'initial'?: v}
        coarse states -> per-window endpoint values; the value holds between windows; apex adds 4*apex*p*(1-p).
  {'rate': <curve>, 'initial'?: v}     integral of a rate curve (units per second): spins in deg/s, travel speeds,
        accelerating / decelerating profiles; grid independent.
  {'add': [<curve>, ...]}              sum of curves (layering: base pose + pattern + noise ...).
  {'pattern': {'keys': [0..1], 'values', 'interp'?, 'eases'?}, 'start'|'phase', 'end', 'count' | 'period' (s),
   'ramp'? (s)}                        one keyed cycle repeated over a window (asymmetric wing down / up strokes,
        step bounce, hammering); values[0] outside the window, ramp fades the pattern in / out.
  {'initial': v} alone is a constant. Every form may add
  'oscillators': [{'start'|'phase', 'end', 'amplitude', 'cycles' | 'frequency' (Hz), 'frequency_end'?,
        'phase_degrees'?, 'bias'?, 'ramp'? (s), 'shape'? sine|triangle|square|saw|pulse|noise, 'width'? (pulse duty),
        'seed'? (noise), 'decay'?}] (summed on top; noise = smooth deterministic band-limited jitter for idle /
        tremble / organic sway) and 'clamp': [lo, hi].
  Eases: smooth (default), linear, cubic, in, out, in_cubic, out_cubic, sine, spring, step, back_in (anticipation),
  back_out (overshoot), back_in_out, elastic, bounce, snap (fast start).

Root operators: 'curves' {x, y, z, yaw, pitch?, roll?, pivot?, forward_speed?, lateral_speed?} (pitch/roll in the
body frame; pivot = rest point [x,y,z] (leg lengths, body frame, relative to the root) about which root rotations
turn, the x/y/z curves then move that point; forward_speed / lateral_speed = leg lengths per second along the CURRENT
heading, integrated along the yaw curve and added to x/z: curved paths / turns / circles without trigonometry) |
'ballistic' (unchanged).

Part operators:
  rotation   any category; params space (body | heading | parent | world), weights, pitch/twist/roll curves (YXZ).
  aim        any category: bone DIRECTIONS instead of Euler angles (key poses per bone in a frame, or look-at a
             joint / point), minimal-rotation aiming + twist about the bone (see validate_aim).
  arm_arc    3-node sided upper limb, polar hand target + bounded 4-DOF arm IK; optional 'swivel' curve (elbow
             plane over time, overrides preferred_swivel_degrees) and 'wrist' {space?, pitch?, twist?, roll?}
             (hand-joint orientation after the IK; parent space = relative to the forearm).
  foot_swing / contact_path   legacy 3/4-node contact IK.
  effector   any category, >= 3 IK nodes (+ optional rigid tip): end-point keys, plants, gait, spline keys,
             end_rotation, plant_orient (see validate_effector).
  spring     any category: optional driven rotation (as rotation) + damped follow-through of the chain; ground
             contact and capsule collision (see validate_spring).
  translate  any category, non-root joints: x/y/z offset curves; parent space (local offset) or world / heading /
             body placement after IK, orientation and follow (props, thrown / caught objects; validate_translate).
  rest       keep the bind pose.
Orientation order: root -> parent-space translate -> rotations / aims (hierarchy order) -> IK (effectors, arms,
wrists) -> post-IK pass: non-parent-space rotations / driven springs and aims that sit below an IK-driven limb, aims
at joint targets, world / heading / body translates -> springs -> refine.smooth. A world / body / heading orientation
written for a joint below an arm or leg therefore holds exactly after the limb is solved.
Refine: iterations, foot_margin_ratio, residual_weight, ground_weight, body_ground? {'touch'?: [[t0, t1], ...],
'lift'?: true, 'margin_ratio'?: 0, 'smooth_frames'?: 1.5} (root-height correction from the skinned mesh),
collision? / smooth? (see validate_refine_extras). Reports always include capsule_penetration_ratio (limb vs trunk /
head bone capsules, leg units) next to the IK residuals, ground penetration and foot contact speeds.
"""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from scipy.interpolate import CubicHermiteSpline, CubicSpline

from .skeleton import category
from .timing import interval, key_curve, smooth

STYLE_FIELDS = ('turn_degrees', 'travel_ratio', 'jump_ratio', 'crouch_ratio', 'arm_reach', 'arm_clearance')
EASES = ('smooth', 'linear', 'cubic', 'in', 'out', 'in_cubic', 'out_cubic', 'spring', 'step', 'sine',
         'back_in', 'back_out', 'back_in_out', 'elastic', 'bounce', 'snap')
INTERPS = ('pchip', 'linear', 'smooth', 'cubic', 'step')
SHAPES = ('sine', 'triangle', 'square', 'saw', 'pulse', 'noise')
FRAMES = ('root', 'body', 'parent', 'world')
ROTATION_SPACES = ('body', 'heading', 'parent', 'world')
CURVE_FORMS = ('keys', 'segments', 'rate', 'add', 'pattern')


def require_keys(value, keys, label, optional=()):
    if not isinstance(value, dict) or not set(keys) <= set(value) or set(value)-set(keys)-set(optional):
        raise ValueError(f'{label}: required {sorted(keys)}, optional {sorted(optional)}')


def scalar(value, style):
    if isinstance(value, str) and value.startswith('$'):
        name = value[1:]
        if name not in STYLE_FIELDS:
            raise ValueError(f'Unknown style reference: {value}')
        return float(getattr(style, name))
    if isinstance(value, dict):
        require_keys(value, ('style', 'factor'), 'scaled style reference')
        return scalar('$'+value['style'], style)*float(value['factor'])
    if isinstance(value, bool):
        raise ValueError('Boolean is not a numeric value')
    return float(value)


def ease(p, kind):
    """0..1 -> 0..1 progress shapes. back_* overshoot / anticipate (~10 %), elastic / bounce settle at the end,
    snap = fast start (exponential out), sine = sinusoidal in-out."""
    p = np.clip(p, 0., 1.)
    if kind == 'smooth':
        return smooth(p)
    if kind == 'linear':
        return p
    if kind == 'cubic':
        return p*p*(3-2*p)
    if kind == 'in':
        return p*p
    if kind == 'out':
        return 1-(1-p)**2
    if kind == 'in_cubic':
        return p**3
    if kind == 'out_cubic':
        return 1-(1-p)**3
    if kind == 'spring':
        return (1-np.exp(-5*p)*np.cos(3*np.pi*p))/(1+np.exp(-5.))
    if kind == 'step':
        return (p >= 1).astype(float)
    if kind == 'sine':
        return .5-.5*np.cos(np.pi*p)
    c = 1.70158
    if kind == 'back_in':
        return (c+1)*p**3-c*p**2
    if kind == 'back_out':
        q = p-1
        return 1+(c+1)*q**3+c*q**2
    if kind == 'back_in_out':
        c2 = c*1.525
        return np.where(p < .5, (2*p)**2*((c2+1)*2*p-c2)/2, ((2*p-2)**2*((c2+1)*(2*p-2)+c2)+2)/2)
    if kind == 'elastic':
        return np.where(p >= 1, 1., 1-np.exp(-6.5*p)*np.cos(2*np.pi*1.6*p))
    if kind == 'bounce':
        def out(x):
            n, d = 7.5625, 2.75
            return np.where(x < 1/d, n*x*x, np.where(x < 2/d, n*(x-1.5/d)**2+.75,
                            np.where(x < 2.5/d, n*(x-2.25/d)**2+.9375, n*(x-2.625/d)**2+.984375)))
        return out(p)
    if kind == 'snap':
        return np.where(p >= 1, 1., (1-np.exp(-7*p))/(1-np.exp(-7.)))
    raise ValueError(f'Unknown ease {kind!r}; expected {EASES}')


def keyed(x, keys, values, interp='pchip', eases=None, slopes=None):
    """Interpolate (keys, values) at x (held outside [keys[0], keys[-1]]).
    interp: pchip (default, monotone, stops at extrema), linear, smooth (quintic ease per interval: velocity 0 at
    every key = pose-to-pose holds), cubic (C2 natural spline: keeps momentum through keys, may overshoot), step.
    eases: optional per-interval ease names (len(keys)-1, null = use interp) overriding single intervals.
    slopes: Hermite tangents (value per unit of key) instead of interp."""
    keys, values, x = np.asarray(keys, float), np.asarray(values, float), np.asarray(x, float)
    if keys.ndim != 1 or len(keys) < 2 or np.any(np.diff(keys) <= 0) or len(values) != len(keys):
        raise ValueError('Expected ordered keys and matching values')
    if not all(np.isfinite(a).all() for a in (keys, values, x)):
        raise ValueError('Non-finite trajectory')
    if interp not in INTERPS:
        raise ValueError(f'Unknown interp {interp!r}; expected {INTERPS}')
    xc = np.clip(x, keys[0], keys[-1])
    if slopes is not None:
        slopes = np.asarray(slopes, float)
        if slopes.shape != values.shape or not np.isfinite(slopes).all():
            raise ValueError('Hermite slopes must match values and be finite')
        out = CubicHermiteSpline(keys, values, slopes)(xc)
    elif interp == 'pchip':
        out = key_curve(xc, keys, values)
    elif interp == 'linear':
        out = np.interp(xc, keys, values)
    elif interp == 'cubic':
        out = CubicSpline(keys, values, bc_type='natural')(xc) if len(keys) > 2 else np.interp(xc, keys, values)
    else:
        i = np.clip(np.searchsorted(keys, xc, side='right')-1, 0, len(keys)-2)
        p = (xc-keys[i])/(keys[i+1]-keys[i])
        w = smooth(p) if interp == 'smooth' else (p >= 1-1e-12).astype(float)
        out = values[i]+(values[i+1]-values[i])*w
    if eases is not None:
        if not isinstance(eases, (list, tuple)) or len(eases) != len(keys)-1:
            raise ValueError('eases must list one ease name (or null) per key interval')
        i = np.clip(np.searchsorted(keys, xc, side='right')-1, 0, len(keys)-2)
        for k, name in enumerate(eases):
            if name is None:
                continue
            m = i == k
            if np.any(m):
                p = (xc[m]-keys[k])/(keys[k+1]-keys[k])
                out = np.array(out, float)
                out[m] = values[k]+(values[k+1]-values[k])*ease(p, name)
            else:
                ease(np.zeros(1), name)
    return np.asarray(out, float)


def _window(spec, rhythm, label):
    if 'phase' in spec:
        if 'start' in spec or 'end' in spec:
            raise ValueError(f'{label}: give either phase or start/end')
        return rhythm.window({'phase': spec['phase']})
    return rhythm.window(spec)


def _segments(spec, rhythm, style, times):
    segs = spec['segments']
    if not isinstance(segs, list) or not segs:
        raise ValueError('segments must be a nonempty list')
    windows = []
    for s in segs:
        require_keys(s, ('to',), 'curve segment', ('start', 'end', 'phase', 'from', 'ease', 'apex'))
        windows.append(_window(s, rhythm, 'curve segment'))
    if any(b[0] < a[1]-1e-9 for a, b in zip(windows, windows[1:])):
        raise ValueError('curve segments must be ordered and nonoverlapping')
    first = segs[0].get('from', spec.get('initial', 0.))
    current = scalar(spec.get('initial', first), style)
    out = np.full(np.shape(times), current, float)
    for s, (a, b) in zip(segs, windows):
        v0 = scalar(s['from'], style) if 'from' in s else current
        v1 = scalar(s['to'], style)
        apex = scalar(s.get('apex', 0.), style)
        p = interval(times, a, b)
        value = v0+(v1-v0)*ease(p, s.get('ease', 'smooth'))+4*apex*p*(1-p)
        mask = np.asarray(times) >= a
        out[mask] = value[mask]
        current = v1
    return out


def _oscillators(specs, rhythm, style, times):
    total = np.zeros(np.shape(times), float)
    if not isinstance(specs, list):
        raise ValueError('oscillators must be a list')
    for o in specs:
        require_keys(o, ('amplitude',), 'oscillator', ('start', 'end', 'phase', 'cycles', 'frequency', 'frequency_end',
                                                        'phase_degrees', 'bias', 'ramp', 'shape', 'decay', 'width', 'seed'))
        a, b = _window(o, rhythm, 'oscillator')
        if ('cycles' in o) == ('frequency' in o):
            raise ValueError('oscillator needs exactly one of cycles / frequency')
        f0 = scalar(o['cycles'], style)/(b-a) if 'cycles' in o else scalar(o['frequency'], style)
        f1 = scalar(o.get('frequency_end', f0), style) if 'frequency' in o else f0
        t = np.clip(np.asarray(times, float), a, b)-a
        cycles = f0*t+(f1-f0)*t*t/(2*(b-a))
        x = 2*np.pi*cycles+np.deg2rad(scalar(o.get('phase_degrees', 0.), style))
        shape = o.get('shape', 'sine')
        if shape == 'sine':
            wave = np.sin(x)
        elif shape == 'triangle':
            wave = 2/np.pi*np.arcsin(np.sin(x))
        elif shape == 'square':
            wave = np.tanh(4*np.sin(x))/np.tanh(4.)
        elif shape == 'saw':
            u = np.mod(x/(2*np.pi), 1.)
            wave = np.where(u < .9, 2*u/.9-1, 1-2*smooth((u-.9)/.1))
        elif shape == 'pulse':
            width = float(o.get('width', .25))
            if not 0 < width < 1:
                raise ValueError('pulse width must be in (0, 1)')
            u = np.mod(x/(2*np.pi), 1.)
            wave = np.where(u < width, np.sin(np.pi*u/width)**2, 0.)
        elif shape == 'noise':
            seed = o.get('seed', 0)
            if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)):
                raise ValueError('noise seed must be an integer')
            rng = np.random.default_rng(int(seed))
            ratios = np.array([.5, .77, 1., 1.31, 1.73, 2.29])
            offsets = rng.uniform(0., 2*np.pi, len(ratios))
            gains = 1/np.sqrt(ratios)
            wave = np.sum(gains[:, None]*np.sin(ratios[:, None]*np.atleast_1d(x)[None]+offsets[:, None]), axis=0)
            wave = wave.reshape(np.shape(x))/(.55*gains.sum())
        else:
            raise ValueError(f'Unknown oscillator shape {shape!r}; expected {SHAPES}')
        ramp = scalar(o.get('ramp', 0.), style)
        env = np.ones_like(t)
        if ramp > 0:
            env = smooth(np.minimum(t/ramp, (b-a-t)/ramp))
        decay = scalar(o.get('decay', 0.), style)
        amp = scalar(o['amplitude'], style)*np.exp(-decay*t)
        inside = (np.asarray(times) >= a) & (np.asarray(times) <= b)
        total += np.where(inside, env*(scalar(o.get('bias', 0.), style)+amp*wave), 0.)
    return total


def _fine_grid(rhythm):
    return np.linspace(0., rhythm.duration, max(2, int(np.ceil(rhythm.duration*240)))+1)


def _pattern(spec, rhythm, style, times):
    cyc = spec['pattern']
    require_keys(cyc, ('keys', 'values'), 'curve pattern', ('interp', 'eases', 'slopes'))
    a, b = _window(spec, rhythm, 'curve pattern')
    if ('count' in spec) == ('period' in spec):
        raise ValueError('curve pattern needs exactly one of count / period (s)')
    period = (b-a)/scalar(spec['count'], style) if 'count' in spec else scalar(spec['period'], style)
    if not np.isfinite(period) or period <= 0:
        raise ValueError('curve pattern period must be positive')
    keys = np.asarray([scalar(k, style) for k in cyc['keys']], float)
    values = np.asarray([scalar(v, style) for v in cyc['values']], float)
    if keys[0] < -1e-9 or keys[-1] > 1+1e-9:
        raise ValueError('curve pattern keys are cycle fractions in [0, 1]')
    t = np.asarray(times, float)
    cycles = (np.clip(t, a, b)-a)/period
    u = np.mod(cycles, 1.)
    u = np.where((cycles > 1e-9) & (np.abs(u) < 1e-9) & (t >= b-1e-9), 1., u)
    value = keyed(u, keys, values, cyc.get('interp', 'pchip'), cyc.get('eases'), cyc.get('slopes'))
    v0 = values[0]
    ramp = scalar(spec.get('ramp', 0.), style)
    if ramp > 0:
        env = smooth(np.minimum((np.clip(t, a, b)-a)/ramp, (b-np.clip(t, a, b))/ramp))
        value = v0+env*(value-v0)
    return np.where(t < a, v0, value)


def sample_curve(spec, rhythm, style, times):
    """Evaluate a curve spec (see module docstring) at times (seconds)."""
    if not isinstance(spec, dict):
        raise ValueError('curve must be an object')
    forms = [f for f in CURVE_FORMS if f in spec]
    if 'values' in spec and 'keys' not in forms:
        forms.append('keys')
    if len(forms) > 1:
        raise ValueError(f'curve must use one form, got {forms}')
    common = ('oscillators', 'clamp')
    if forms == ['keys']:
        require_keys(spec, ('keys', 'values'), 'curve', ('slopes', 'interp', 'eases')+common)
        if 'slopes' in spec and ('interp' in spec or 'eases' in spec):
            raise ValueError('curve: slopes (Hermite) cannot be combined with interp / eases')
        keys = np.array([rhythm.seconds(k)/rhythm.duration for k in spec['keys']])
        values = np.array([scalar(v, style) for v in spec['values']])
        samples = np.asarray(times)/rhythm.duration
        result = keyed(samples, keys, values, spec.get('interp', 'pchip'), spec.get('eases'), spec.get('slopes'))
    elif forms == ['segments']:
        require_keys(spec, ('segments',), 'curve', ('initial',)+common)
        result = _segments(spec, rhythm, style, times)
    elif forms == ['rate']:
        require_keys(spec, ('rate',), 'curve', ('initial',)+common)
        fine = _fine_grid(rhythm)
        r = sample_curve(spec['rate'], rhythm, style, fine)
        cum = np.concatenate([[0.], np.cumsum(.5*(r[1:]+r[:-1])*np.diff(fine))])
        result = scalar(spec.get('initial', 0.), style)+np.interp(np.asarray(times, float), fine, cum)
    elif forms == ['add']:
        require_keys(spec, ('add',), 'curve', common)
        if not isinstance(spec['add'], list) or not spec['add']:
            raise ValueError('curve add needs a nonempty list of curves')
        result = np.zeros(np.shape(times), float)
        for c in spec['add']:
            result = result+sample_curve(c, rhythm, style, times)
    elif forms == ['pattern']:
        require_keys(spec, ('pattern',), 'curve', ('start', 'end', 'phase', 'count', 'period', 'ramp')+common)
        result = _pattern(spec, rhythm, style, times)
    else:
        require_keys(spec, (), 'curve', ('initial',)+common)
        if not spec:
            raise ValueError('curve needs keys/values, segments, rate, add, pattern, initial or oscillators')
        result = np.full(np.shape(times), scalar(spec.get('initial', 0.), style), float)
    if 'oscillators' in spec:
        result = result+_oscillators(spec['oscillators'], rhythm, style, times)
    if 'clamp' in spec:
        lo, hi = (scalar(v, style) for v in spec['clamp'])
        if not lo < hi:
            raise ValueError('clamp needs lo < hi')
        result = np.clip(result, lo, hi)
    if not np.isfinite(result).all():
        raise ValueError('Non-finite curve')
    return result


def _pair(values, low, high, label):
    pair = np.asarray(values, float)
    if pair.shape != (2,) or not np.isfinite(pair).all() or not low <= pair[0] < pair[1] <= high:
        raise ValueError(f'Invalid {label} limits')


def _vector(value, label, nonzero=False):
    v = np.asarray(value, float)
    if v.shape != (3,) or not np.isfinite(v).all() or (nonzero and np.linalg.norm(v) < 1e-8):
        raise ValueError(f'{label}: finite{" nonzero" if nonzero else ""} 3D vector required')
    return v


def validate_arm_limits(arm):
    require_keys(arm, ('shoulder_flexion', 'shoulder_abduction', 'shoulder_swivel', 'elbow_flexion',
                       'target_back_ratio', 'regularization', 'max_nfev'), 'arm limits')
    for name, bounds in {'shoulder_flexion': (-180, 180), 'shoulder_abduction': (-90, 90),
                         'shoulder_swivel': (-90, 90), 'elbow_flexion': (1, 179)}.items():
        _pair(arm[name], *bounds, name)
    if not isinstance(arm['max_nfev'], int) or arm['max_nfev'] < 10:
        raise ValueError('max_nfev must be an integer >=10')
    if not np.isfinite([arm['target_back_ratio'], arm['regularization']]).all() or arm['target_back_ratio'] < 0 or arm['regularization'] <= 0:
        raise ValueError('Invalid arm workspace/regularization')


def _flexion(value, count, label):
    f = np.asarray(value, float)
    if f.shape == (2,):
        f = np.tile(f, (count, 1))
    if f.shape != (count, 2) or not np.isfinite(f).all() or np.any(f[:, 0] >= f[:, 1]) or np.any(f <= -180) or np.any(f >= 180):
        raise ValueError(f'{label}: flexion must be [lo, hi] or {count} pairs within (-180, 180)')


def _rotation_params(p, joints, rhythm, style, key, required=True):
    names = ('space', 'weights', 'pitch', 'twist', 'roll')
    if required:
        missing = set(names)-set(p)
        if missing:
            raise ValueError(f'{key}: rotation requires {sorted(missing)}')
    if p.get('space', 'parent') not in ROTATION_SPACES:
        raise ValueError(f'{key}: space must be one of {ROTATION_SPACES}')
    weights = p.get('weights', [1.]*len(joints))
    if len(weights) != len(joints) or not np.isfinite(weights).all():
        raise ValueError(f'{key}: invalid rotation weights')
    for name in ('pitch', 'twist', 'roll'):
        if name in p:
            sample_curve(p[name], rhythm, style, rhythm.sample())


EFFECTOR_OPTIONAL = ('frame', 'units', 'keys', 'plants', 'lift', 'arcs', 'pole_frame', 'tip', 'orient', 'end_pitch',
                     'ground_clearance', 'bend_weights', 'gait', 'interp', 'end_rotation', 'plant_orient')
GAIT_KEYS = ('start', 'end', 'phase', 'period', 'cycles', 'duty', 'offset', 'anchor', 'height', 'ease', 'slide')


def gait_plants(p, rhythm):
    """stance windows generated from effector 'gait' specs: each spec (or list of specs) is a window with
    period (s) | cycles, duty (stance fraction, default 0.6) and offset (cycle fraction at which this limb's swing
    starts, default 0); swings that do not fit inside the window are dropped (the stance is extended). Stances are
    world-locked plants anchored at mid-stance (start / end when they touch the motion start / end) unless 'anchor'
    is given; height / ease / slide are copied to every generated plant."""
    specs = p.get('gait', [])
    if isinstance(specs, dict):
        specs = [specs]
    if not isinstance(specs, list):
        raise ValueError('effector gait must be an object or a list of objects')
    out = []
    for g in specs:
        require_keys(g, (), 'effector gait', GAIT_KEYS)
        a, b = _window(g, rhythm, 'effector gait')
        if ('period' in g) == ('cycles' in g):
            raise ValueError('effector gait needs exactly one of period (s) / cycles')
        period = float(g['period']) if 'period' in g else (b-a)/float(g['cycles'])
        duty, offset = float(g.get('duty', .6)), float(g.get('offset', 0.))
        if not np.isfinite([period, duty, offset]).all() or period <= 0 or not 0 < duty < 1 or not 0 <= offset < 1:
            raise ValueError('effector gait: period > 0, duty in (0, 1), offset in [0, 1)')
        swings, k = [], -1
        while a+(k+offset)*period < b:
            s = a+(k+offset)*period
            e = s+(1-duty)*period
            if s >= a-1e-9 and e <= b+1e-9:
                swings.append((max(s, a), min(e, b)))
            k += 1
        t = a
        extra = {n: g[n] for n in ('height', 'ease', 'slide') if n in g}
        for s, e in swings+[(b, b)]:
            if s-t > 1e-6:
                anchor = g.get('anchor', 'start' if t <= 1e-6 else ('end' if s >= rhythm.duration-1e-6 else 'mid'))
                out.append((t, s, {'anchor': anchor, **extra}))
            t = max(t, e)
    out.sort(key=lambda w: w[0])
    merged = []
    for w in out:
        if merged and w[0] <= merged[-1][1]+1e-6:
            a0, b0, w0 = merged[-1]
            merged[-1] = (a0, max(b0, w[1]), w0)
        else:
            merged.append(w)
    return merged


def effector_tip(spec):
    p = spec['params']
    return int(p.get('tip', 1 if category(spec['category']) == 'lower_limb' and len(spec['joints']) == 4 else 0))


def effector_events(p, rhythm):
    """(authored keys, plants) with times in seconds, validated; plants include the gait-generated stances."""
    keys = []
    for k in p.get('keys', []):
        require_keys(k, ('t',), 'effector key', ('offset', 'frame', 'height', 'ease', 'arc'))
        t = rhythm.seconds(k['t'])
        if 'offset' in k:
            _vector(k['offset'], 'effector key offset')
        if 'arc' in k:
            _vector(k['arc'], 'effector key arc')
        if k.get('frame', 'root') not in FRAMES:
            raise ValueError(f'effector key frame must be one of {FRAMES}')
        if 'height' in k and (isinstance(k['height'], bool) or not np.isfinite(k['height'])):
            raise ValueError('effector key height must be a number')
        ease(np.zeros(1), k.get('ease', 'smooth'))
        keys.append((t, k))
    if any(b[0] <= a[0] for a, b in zip(keys, keys[1:])):
        raise ValueError('effector keys must have strictly increasing times')
    plants = []
    for w in p.get('plants', []):
        require_keys(w, (), 'effector plant', ('start', 'end', 'phase', 'anchor', 'height', 'offset', 'slide', 'ease'))
        a, b = _window(w, rhythm, 'effector plant')
        plants.append((a, b, w))
    generated = gait_plants(p, rhythm)
    for a, b, _ in generated:
        if any(a < y-1e-9 and x < b-1e-9 for x, y, _ in plants):
            raise ValueError('effector gait stances overlap authored plants')
    plants = sorted(plants, key=lambda w: w[0])
    for g in generated:                         # a stance touching an authored plant continues that contact
        touching = [i for i, (x, y, _) in enumerate(plants) if abs(y-g[0]) <= 1e-6 or abs(g[1]-x) <= 1e-6]
        if touching:
            i = touching[0]
            x, y, w = plants[i]
            plants[i] = (min(x, g[0]), max(y, g[1]), w if x <= g[0] else {**w, 'anchor': g[2]['anchor']})
        else:
            plants.append(g)
    plants = sorted(plants, key=lambda w: w[0])
    for a, b, w in plants:
        if w.get('anchor', 'mid') not in ('start', 'mid', 'end'):
            raise ValueError('plant anchor must be start/mid/end')
        h = w.get('height', 'rest')
        if not (h in ('rest', 'free') or (not isinstance(h, bool) and isinstance(h, (int, float)) and np.isfinite(h))):
            raise ValueError("plant height must be 'rest', 'free' or a number")
        for name in ('offset', 'slide'):
            if name in w:
                _vector(w[name], f'plant {name}')
        ease(np.zeros(1), w.get('ease', 'linear'))
    if any(b[0] <= a[1]+1e-9 for a, b in zip(plants, plants[1:])):
        raise ValueError('effector plants must be ordered and separated')
    gaps = [(a[1], b[0]) for a, b in zip(plants, plants[1:])]
    for arc in p.get('arcs', []):
        require_keys(arc, ('start', 'end', 'offset'), 'effector arc')
        pair = (rhythm.seconds(arc['start']), rhythm.seconds(arc['end']))
        if not any(np.isclose(pair, g).all() for g in gaps):
            raise ValueError('effector arc must identify a gap between two plants')
        _vector(arc['offset'], 'effector arc offset')
    return keys, plants


def validate_effector(key, spec, rhythm, style):
    """effector params (end point of the chain; '[]' = optional):
      pole, flexion         bend side ([x,y,z] body axes or 'rest' = the chain's own bind bend direction) and extra
                            bend per interior joint relative to the bind shape, degrees: [lo, hi] or one pair per
                            interior joint (+ = toward the pole); [bend_weights] distributes the bend (signed:
                            a negative weight bends that joint the other way, e.g. [1, -1] Z-folding fore legs).
      [frame]               default key frame: root (position + yaw), body (skeleton root joint, incl. root
                            pitch/roll and pelvis rotation), parent (chain parent joint), world (initial frame).
      [units]               leg (default) | limb | scale, for offsets / heights / lift / arcs.
      [keys]                [{t, offset?: [x,y,z] from the bind end position in that frame, frame?, height?
                            (absolute above ground), ease? (into this key), arc? [x,y,z] (heading frame, peak)}]
      [plants]              [{start/end | phase, anchor? start|mid|end, height? 'rest'|'free'|number, offset?,
                            slide? [x,y,z] displacement during the window, ease?}] -> world-locked contacts.
      [lift], [arcs]        swing arc between consecutive plants (default lift) / per-gap offsets.
      [gait]                {start/end | phase, period (s) | cycles, duty?, offset?, anchor?, height?, ease?,
                            slide?} or a list of them -> generated stance plants (see gait_plants); combine with
                            plants outside the gait windows (a stance touching an authored plant extends it);
                            give each limb its own offset (trot, gallop, ...).
      [plant_orient]        follow (default: the end node keeps following its orient frame while planted) | lock
                            (orientation frozen while planted, blended back during swings: no toe slide in turns).
      [interp]              'ease' (default: keys blend with their own ease, velocity 0 at each key) | 'spline'
                            (Catmull-Rom through the free keys: continuous velocity for arcs, circles, swings).
      [pole_frame]          frame that carries the pole (default root); [tip] 0|1 rigid end node (default 1
                            for a 4-node lower limb); [orient] frame for the end node rotation (default root with
                            a tip, else free), [end_pitch] curve, or [end_rotation] {pitch?, twist?, roll?} curves
                            (degrees, YXZ in the orient frame, body axes); [ground_clearance] minimum target height.
    """
    p = spec['params']
    require_keys(p, ('pole', 'flexion'), key, EFFECTOR_OPTIONAL)
    tip = effector_tip(spec)
    if tip not in (0, 1):
        raise ValueError(f'{key}: tip must be 0 or 1')
    n_ik = len(spec['joints'])-tip
    if n_ik < 3:
        raise ValueError(f'{key}: effector needs >= 3 IK nodes (+ tip)')
    _flexion(p['flexion'], n_ik-2, key)
    if 'bend_weights' in p:
        w = np.asarray(p['bend_weights'], float)
        if w.shape != (n_ik-2,) or not np.isfinite(w).all() or np.abs(w).sum() <= 0:
            raise ValueError(f'{key}: bend_weights must be {n_ik-2} numbers with a nonzero total magnitude')
    if p['pole'] != 'rest':
        _vector(p['pole'], f'{key} pole', nonzero=True)
    for name, allowed in (('frame', FRAMES), ('pole_frame', FRAMES), ('orient', FRAMES+('free',)),
                          ('units', ('leg', 'limb', 'scale'))):
        if name in p and p[name] not in allowed:
            raise ValueError(f'{key}: {name} must be one of {allowed}')
    for name in ('lift', 'ground_clearance'):
        if name in p and (isinstance(p[name], bool) or not np.isfinite(p[name])):
            raise ValueError(f'{key}: {name} must be a number')
    if 'end_pitch' in p:
        sample_curve(p['end_pitch'], rhythm, style, rhythm.sample())
    if 'end_rotation' in p:
        if 'end_pitch' in p:
            raise ValueError(f'{key}: give end_pitch or end_rotation, not both')
        require_keys(p['end_rotation'], (), f'{key} end_rotation', ('pitch', 'twist', 'roll'))
        for c in p['end_rotation'].values():
            sample_curve(c, rhythm, style, rhythm.sample())
    if (('end_pitch' in p or 'end_rotation' in p) and p.get('orient', 'root' if tip else 'free') == 'free'):
        raise ValueError(f'{key}: end_pitch / end_rotation need an orient frame (not free)')
    if p.get('interp', 'ease') not in ('ease', 'spline'):
        raise ValueError(f'{key}: interp must be ease or spline')
    if p.get('plant_orient', 'follow') not in ('follow', 'lock'):
        raise ValueError(f'{key}: plant_orient must be follow or lock')
    effector_events(p, rhythm)


SPRING_OPTIONAL = ('gravity', 'limit_degrees', 'wind', 'space', 'weights', 'pitch', 'twist', 'roll', 'ground', 'collide',
                   'radius')


def _number(value, label, low=-np.inf, high=np.inf):
    if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)) or not np.isfinite(value) \
            or not low <= value <= high:
        raise ValueError(f'{label} must be a finite number in [{low}, {high}]')
    return float(value)


def _part_list(value, parts, label):
    if not isinstance(value, list) or not all(isinstance(k, str) for k in value):
        raise ValueError(f'{label} must be a list of part IDs')
    if parts is not None:
        missing = [k for k in value if k not in parts]
        if missing:
            raise ValueError(f'{label}: unknown parts {missing}')


def validate_spring(key, spec, rhythm, style, parts=None):
    """spring params: frequency (Hz), damping (ratio); [gravity] (x earth g), [wind] (body frame, legs/s^2),
    [limit_degrees] max deviation from the driven pose; [space, weights, pitch, twist, roll] driven rotation (as the
    rotation operator); [ground] true | margin (leg units): particles stay above the ground (cloth / tails / ribbons
    rest on the floor instead of sinking in); [collide] [part IDs]: particles are pushed out of those parts' bone
    capsules (radius from the skin; cape vs torso / legs, tail vs legs), [radius] particle radius (leg units, 0.03)."""
    p = spec['params']
    require_keys(p, ('frequency', 'damping'), key, SPRING_OPTIONAL)
    if not np.isfinite([p['frequency'], p['damping']]).all() or p['frequency'] <= 0 or p['damping'] < 0:
        raise ValueError(f'{key}: spring frequency > 0 and damping >= 0 required')
    if 'gravity' in p and (isinstance(p['gravity'], bool) or not np.isfinite(p['gravity'])):
        raise ValueError(f'{key}: gravity must be a number')
    if 'limit_degrees' in p and (not np.isfinite(p['limit_degrees']) or not 0 < p['limit_degrees'] <= 180):
        raise ValueError(f'{key}: limit_degrees in (0, 180]')
    if 'wind' in p:
        _vector(p['wind'], f'{key} wind')
    if 'ground' in p and not isinstance(p['ground'], bool):
        _number(p['ground'], f'{key} ground margin', 0., 10.)
    if 'collide' in p:
        _part_list(p['collide'], parts, f'{key} collide')
    if 'radius' in p:
        _number(p['radius'], f'{key} radius', 0., 1.)
    _rotation_params(p, spec['joints'], rhythm, style, key, required=False)


TRANSLATE_SPACES = ('parent', 'world', 'heading', 'body')


def _orientation(o, rhythm, style, label):
    require_keys(o, (), label, ('space', 'pitch', 'twist', 'roll'))
    if o.get('space', 'parent') not in ROTATION_SPACES:
        raise ValueError(f'{label}: space must be one of {ROTATION_SPACES}')
    for name in ('pitch', 'twist', 'roll'):
        if name in o:
            sample_curve(o[name], rhythm, style, rhythm.sample())


def validate_translate(key, spec, rhythm, style):
    """translate params: x/y/z offset curves; space parent (default: local translation offset in the rest body
    axes, moving with the parent, applied before IK) | world | heading | body (the joints' WORLD position = rest
    position + offset in that frame, applied after IK, i.e. exact placement of props / detached parts regardless of
    the parent); units leg | scale | bone; weights. [orientation] {space?, pitch?, twist?, roll?}: rotation of the
    same joints (as the rotation operator, applied after IK). [follow] {joint, weight (curve 0..1)}: blend the
    joints' world transform toward riding on another joint with the grip they have on their own parent at rest
    (hand-over of props, catching)."""
    p = spec['params']
    require_keys(p, (), key, ('x', 'y', 'z', 'space', 'units', 'weights', 'orientation', 'follow'))
    space = p.get('space', 'parent')
    if space not in TRANSLATE_SPACES:
        raise ValueError(f'{key}: translate space must be one of {TRANSLATE_SPACES}')
    if p.get('units', 'leg') not in ('leg', 'scale', 'bone'):
        raise ValueError(f'{key}: translate units must be leg, scale or bone')
    weights = p.get('weights', [1.]*len(spec['joints']))
    if len(weights) != len(spec['joints']) or not np.isfinite(weights).all():
        raise ValueError(f'{key}: invalid translate weights')
    for name in ('x', 'y', 'z'):
        if name in p:
            sample_curve(p[name], rhythm, style, rhythm.sample())
    if 'orientation' in p:
        _orientation(p['orientation'], rhythm, style, f'{key} orientation')
    if 'follow' in p:
        if space == 'parent':
            raise ValueError(f'{key}: follow needs a world / heading / body translate space')
        require_keys(p['follow'], ('joint', 'weight'), f'{key} follow')
        if not isinstance(p['follow']['joint'], str):
            raise ValueError(f'{key}: follow joint must be a joint name')
        sample_curve(p['follow']['weight'], rhythm, style, rhythm.sample())


AIM_OPTIONAL = ('frame', 'keys', 'target', 'twist', 'twist_weights', 'weight', 'child')


def validate_aim(key, spec, rhythm, style):
    """aim params (bone directions instead of Euler angles; each joint's bone = joint -> next chain joint, the last
    joint -> its first child or 'child'):
      [frame]     body (default) | heading | parent | world: frame of the key directions (body axes x left, y up,
                  z forward; parent = carried by the chain parent joint).
      [keys]      [{t, dirs: [[x,y,z] | null per joint], ease?}]: per-bone direction key poses, slerped (ease into
                  each key, default smooth); null keeps that bone's inherited (parent-relative bind) direction.
      [target]    {joint: name, offset?: [x,y,z] (leg units, body axes)} or {point: [x,y,z] | {x,y,z: curves}
                  (leg units, relative to the rest root), frame?: world|heading|body (default heading)}:
                  look-at; the chain turns its tip toward the target, shared equally over the joints (CCD).
      [twist]     curve (degrees) about the aimed bone, distributed by [twist_weights] (default all on the first
                  joint, inherited by the rest); [weight] curve 0..1 blends from the inherited pose to the aim.
    Each bone turns by the minimal rotation from its inherited pose, so no Euler unwrapping / gimbal issues. Aims
    under IK-driven limbs (arm_arc / effector) or with a joint target are applied after IK."""
    p = spec['params']
    require_keys(p, (), key, AIM_OPTIONAL)
    if ('keys' in p) == ('target' in p):
        raise ValueError(f'{key}: aim needs exactly one of keys / target')
    if p.get('frame', 'body') not in ROTATION_SPACES:
        raise ValueError(f'{key}: aim frame must be one of {ROTATION_SPACES}')
    n = len(spec['joints'])
    if 'keys' in p:
        if not isinstance(p['keys'], list) or not p['keys']:
            raise ValueError(f'{key}: aim keys must be a nonempty list')
        times = []
        for k in p['keys']:
            require_keys(k, ('t', 'dirs'), f'{key} aim key', ('ease',))
            times.append(rhythm.seconds(k['t']))
            if not isinstance(k['dirs'], list) or len(k['dirs']) != n:
                raise ValueError(f'{key}: aim key dirs need one entry per joint ({n})')
            for d in k['dirs']:
                if d is not None:
                    _vector(d, f'{key} aim direction', nonzero=True)
            ease(np.zeros(1), k.get('ease', 'smooth'))
        if any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError(f'{key}: aim keys must have strictly increasing times')
        for i in range(n):
            if [k['dirs'][i] is None for k in p['keys']].count(True) not in (0, len(p['keys'])):
                raise ValueError(f'{key}: aim joint {i} must be null in all keys or in none')
    else:
        t = p['target']
        require_keys(t, (), f'{key} aim target', ('joint', 'offset', 'point', 'frame'))
        if ('joint' in t) == ('point' in t):
            raise ValueError(f'{key}: aim target needs exactly one of joint / point')
        if 'offset' in t:
            _vector(t['offset'], f'{key} aim target offset')
        if 'point' in t:
            if isinstance(t['point'], dict):
                require_keys(t['point'], (), f'{key} aim point', ('x', 'y', 'z'))
                for c in t['point'].values():
                    sample_curve(c, rhythm, style, rhythm.sample())
            else:
                _vector(t['point'], f'{key} aim point')
        if t.get('frame', 'heading') not in ('world', 'heading', 'body'):
            raise ValueError(f'{key}: aim target frame must be world, heading or body')
    for name in ('twist', 'weight'):
        if name in p:
            sample_curve(p[name], rhythm, style, rhythm.sample())
    if 'twist_weights' in p:
        w = np.asarray(p['twist_weights'], float)
        if w.shape != (n,) or not np.isfinite(w).all():
            raise ValueError(f'{key}: twist_weights must be {n} numbers')
    if 'child' in p and not isinstance(p['child'], str):
        raise ValueError(f'{key}: child must be a joint name')


REFINE_OPTIONAL = ('body_ground', 'collision', 'smooth')


def validate_refine_extras(refine, rhythm, parts):
    """refine.collision {parts: [effector / arm_arc IDs], obstacles?: [part IDs] (default every trunk + head part),
    margin? (leg units, 0.02), gain? (0.8), max_push? (leg units, 0.6)}: capsule push-out of the limb targets
    (skipped while a limb is planted), needs iterations >= 1. refine.smooth {parts: [IDs], sigma_frames? (1.5),
    despike_degrees?}: temporal filtering of those parts' joint rotations (removes IK flips / jitter). Parts posed
    before IK (rotation / aim / driven springs upstream of the limbs, e.g. trunk, neck) are filtered before the IK,
    so planted feet stay exact; IK-driven and post-IK parts are filtered at the end (avoid listing planted legs,
    their feet could slide)."""
    if 'collision' in refine:
        c = refine['collision']
        require_keys(c, ('parts',), 'refine.collision', ('obstacles', 'margin', 'gain', 'max_push'))
        _part_list(c['parts'], parts, 'refine.collision parts')
        bad = [k for k in c['parts'] if parts[k]['operator'] not in ('effector', 'arm_arc')]
        if bad:
            raise ValueError(f'refine.collision parts must be effector / arm_arc parts: {bad}')
        if 'obstacles' in c:
            _part_list(c['obstacles'], parts, 'refine.collision obstacles')
        for name, hi in (('margin', 1.), ('gain', 2.), ('max_push', 2.)):
            if name in c:
                _number(c[name], f'refine.collision {name}', 0., hi)
    if 'smooth' in refine:
        sm = refine['smooth']
        require_keys(sm, ('parts',), 'refine.smooth', ('sigma_frames', 'despike_degrees'))
        _part_list(sm['parts'], parts, 'refine.smooth parts')
        if 'sigma_frames' in sm:
            _number(sm['sigma_frames'], 'refine.smooth sigma_frames', 0., 30.)
        if 'despike_degrees' in sm:
            _number(sm['despike_degrees'], 'refine.smooth despike_degrees', 1., 180.)


def validate_body_ground(spec, rhythm):
    require_keys(spec, (), 'refine.body_ground', ('touch', 'lift', 'margin_ratio', 'smooth_frames'))
    for window in spec.get('touch', []):
        if not isinstance(window, (list, tuple)) or len(window) != 2:
            raise ValueError('body_ground touch windows must be [start, end]')
    rhythm.contact_mask(rhythm.sample(), spec.get('touch', []))
    if not isinstance(spec.get('lift', True), bool):
        raise ValueError('body_ground lift must be boolean')
    for name in ('margin_ratio', 'smooth_frames'):
        if name in spec and (not np.isfinite(spec[name]) or spec[name] < 0):
            raise ValueError(f'body_ground {name} must be >= 0')


@dataclass(frozen=True)
class MotionProgram:
    action: str
    root: dict
    parts: dict
    refine: dict
    forward: list[float] | None = None

    def binding(self):
        mapping = {'parts': {key: {k: spec[k] for k in ('category', 'joints', 'side') if k in spec}
                             for key, spec in self.parts.items()}}
        if self.forward is not None:
            mapping['forward'] = self.forward
        return mapping

    def select(self, operator):
        return {key: spec for key, spec in self.parts.items() if spec['operator'] == operator}

    def validate(self, rhythm, style, blocks=None):
        if not isinstance(self.action, str) or not self.action.strip() or not isinstance(self.parts, dict) or not self.parts:
            raise ValueError('Action name and nonempty named parts are required')
        if self.forward is not None:
            forward = np.asarray(self.forward, float)
            if forward.shape != (3,) or not np.isfinite(forward).all() or abs(forward[1]) > 1e-5 or np.linalg.norm(forward) < 1e-6:
                raise ValueError('Program forward must be a nonzero horizontal 3D vector')
        require_keys(self.root, ('operator', 'params'), 'root')
        root = self.root['params']
        if self.root['operator'] == 'ballistic':
            require_keys(root, ('takeoff_height', 'landing_height', 'recover_height', 'absorb_delay',
                                'yaw', 'lateral_shift', 'anticipation', 'takeoff', 'landing', 'absorb', 'recover'), 'ballistic root')
            stages = [rhythm.seconds(root[k]) for k in ('anticipation', 'takeoff', 'landing', 'absorb', 'recover')]
            if np.any(np.diff([0., *stages, rhythm.duration]) <= 0):
                raise ValueError('Ballistic stages must be strictly ordered')
            numbers = [root[k] for k in ('takeoff_height', 'landing_height', 'recover_height', 'absorb_delay')]
            if not np.isfinite(numbers).all() or not 0 <= root['absorb_delay']*rhythm.duration < stages[-1]-stages[-2]:
                raise ValueError('Invalid ballistic heights/absorption delay')
            for name in ('yaw', 'lateral_shift'):
                sample_curve(root[name], rhythm, style, rhythm.sample())
        elif self.root['operator'] == 'curves':
            require_keys(root, ('x', 'y', 'z', 'yaw'), 'root curves', ('pitch', 'roll', 'pivot', 'forward_speed', 'lateral_speed'))
            for name, spec in root.items():
                if name == 'pivot':
                    _vector(spec, 'root pivot')
                else:
                    sample_curve(spec, rhythm, style, rhythm.sample())
        else:
            raise ValueError(f'Unknown root operator: {self.root["operator"]}')
        require_keys(self.refine, ('iterations', 'foot_margin_ratio', 'residual_weight', 'ground_weight'), 'refine',
                     REFINE_OPTIONAL)
        if not isinstance(self.refine['iterations'], int) or not 0 <= self.refine['iterations'] <= 5:
            raise ValueError('Refine iterations must be 0..5')
        if any(not np.isfinite(self.refine[k]) or self.refine[k] < 0 for k in ('foot_margin_ratio', 'residual_weight', 'ground_weight')):
            raise ValueError('Invalid refine weights')
        if 'body_ground' in self.refine:
            validate_body_ground(self.refine['body_ground'], rhythm)
        validate_refine_extras(self.refine, rhythm, self.parts)
        if blocks is not None and set(blocks.parts) != set(self.parts):
            raise ValueError('Program and skeleton must cover the same part IDs')
        for key, spec in self.parts.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError('Part IDs must be nonempty strings')
            require_keys(spec, ('category', 'joints', 'operator', 'params'), key, ('side',))
            kind, op, p = category(spec['category']), spec['operator'], spec['params']
            if not isinstance(spec['joints'], list) or not spec['joints']:
                raise ValueError(f'{key}: nonempty joint list required')
            if spec.get('side') not in (None, 'L', 'R'):
                raise ValueError(f'{key}: invalid optional side')
            if blocks is not None:
                if kind != blocks.parts[key].category or spec.get('side') != blocks.parts[key].side:
                    raise ValueError(f'{key}: binding category/side mismatch')
                if len(spec['joints']) != len(blocks.parts[key].joints):
                    raise ValueError(f'{key}: mapped chain length differs from operator configuration')
            if op == 'rotation':
                require_keys(p, ('pitch', 'twist', 'roll', 'weights', 'space'), key)
                _rotation_params(p, spec['joints'], rhythm, style, key)
            elif op == 'arm_arc':
                if kind != 'upper_limb' or len(spec['joints']) != 3 or spec.get('side') not in ('L', 'R'):
                    raise ValueError(f'{key}: arm_arc needs a sided 3-node upper limb')
                require_keys(p, ('angle', 'radius', 'lateral', 'preferred_swivel_degrees', 'limits'), key, ('wrist', 'swivel'))
                validate_arm_limits(p['limits'])
                if 'wrist' in p:
                    _orientation(p['wrist'], rhythm, style, f'{key} wrist')
                if 'swivel' in p:
                    sample_curve(p['swivel'], rhythm, style, rhythm.sample())
                for name in ('angle', 'radius', 'lateral'):
                    sample_curve(p[name], rhythm, style, rhythm.sample())
                radius = sample_curve(p['radius'], rhythm, style, rhythm.sample())
                lateral = sample_curve(p['lateral'], rhythm, style, rhythm.sample())
                if np.any(radius <= np.abs(lateral)) or np.any(radius >= 1) or not np.isfinite(p['preferred_swivel_degrees']):
                    raise ValueError(f'{key}: invalid polar arc')
            elif op in ('foot_swing', 'contact_path'):
                if kind not in ('upper_limb', 'lower_limb') or len(spec['joints']) not in (3, 4):
                    raise ValueError(f'{key}: contact IK needs a 3/4-node limb')
                common = ('pole', 'flexion')
                if op == 'foot_swing':
                    require_keys(p, (*common, 'release', 'land', 'stance_ratio', 'lift_ratio', 'sweep_ratio',
                                     'pitch_degrees', 'follow_in', 'follow_out'), key, ('rest_pole',))
                    release, land = rhythm.seconds(p['release']), rhythm.seconds(p['land'])
                    if release >= land or min(p['follow_in'], p['follow_out']) <= 0 or max(p['follow_in'], p['follow_out'])*rhythm.duration >= land-release:
                        raise ValueError(f'{key}: invalid swing timing')
                    vals = [p[k] for k in ('stance_ratio', 'lift_ratio', 'sweep_ratio', 'pitch_degrees', 'follow_in', 'follow_out')]
                    if not np.isfinite(vals).all() or p['lift_ratio'] < 0:
                        raise ValueError(f'{key}: invalid swing values')
                else:
                    require_keys(p, (*common, 'contacts', 'lift_ratio', 'arcs'), key, ('rest_pole',))
                    rhythm.contact_mask(rhythm.sample(), p['contacts'])
                    if len(p['contacts']) < 2 or rhythm.seconds(p['contacts'][0][0]) != 0 or rhythm.seconds(p['contacts'][-1][1]) != rhythm.duration:
                        raise ValueError(f'{key}: contact path must cover both endpoints')
                    if not np.isfinite(p['lift_ratio']) or p['lift_ratio'] < 0:
                        raise ValueError(f'{key}: invalid swing lift')
                    gaps = [(rhythm.seconds(a[1]), rhythm.seconds(b[0])) for a, b in zip(p['contacts'], p['contacts'][1:])]
                    for arc in p['arcs']:
                        require_keys(arc, ('start', 'end', 'offset'), key+' arc')
                        pair = (rhythm.seconds(arc['start']), rhythm.seconds(arc['end']))
                        if pair not in gaps or np.asarray(arc['offset']).shape != (3,) or not np.isfinite(arc['offset']).all():
                            raise ValueError(f'{key}: arc must identify a contact gap with a finite 3D offset')
                pole = np.asarray(p['pole'], float)
                if pole.shape != (3,) or not np.isfinite(pole).all() or np.linalg.norm(pole) < 1e-8:
                    raise ValueError(f'{key}: finite nonzero pole required')
                if 'rest_pole' in p:
                    rest_pole = np.asarray(p['rest_pole'], float)
                    if rest_pole.shape != (3,) or not np.isfinite(rest_pole).all() or np.linalg.norm(rest_pole) < 1e-8:
                        raise ValueError(f'{key}: finite nonzero body-space rest_pole required')
                _pair(p['flexion'], 1, 179, key)
            elif op == 'effector':
                validate_effector(key, spec, rhythm, style)
            elif op == 'spring':
                validate_spring(key, spec, rhythm, style, self.parts)
            elif op == 'aim':
                validate_aim(key, spec, rhythm, style)
            elif op == 'translate':
                validate_translate(key, spec, rhythm, style)
            elif op == 'rest':
                require_keys(p, (), key)
            else:
                raise ValueError(f'{key}: unknown operator {op!r}')
