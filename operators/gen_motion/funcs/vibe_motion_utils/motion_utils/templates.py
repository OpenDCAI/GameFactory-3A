"""Motion preset definitions and parameter validation."""
from __future__ import annotations
from copy import deepcopy
from typing import Any
import numpy as np

# Built-in motions. 'recipe' selects the generator, 'frames' is the default
# length, and 'params' holds the values a caller may override: lengths are
# ratios of limb length and angles are in degrees.
MOTION_PRESETS: dict[str, dict[str, Any]] = {
    'walk': {
        'recipe': 'walk',
        'frames': 96,
        'params': {
            'steps': 4,
            'step_length': 0.24,
            'foot_height': 0.12,
            'crouch': 0.015,
            'arm_gain': 0.8,
            'elbow_deg': 25.0,
        },
    },
    'stride': {
        'recipe': 'stride_forward',
        'frames': 60,
        'params': {
            'steps': 1,
            'step_length': 0.28,
            'foot_height': 0.16,
            'crouch': 0.015,
            'arm_gain': 0.8,
            'elbow_deg': 25.0,
        },
    },
    'boxing': {
        'recipe': 'boxing',
        'frames': 96,
        'params': {
            'combo': (0, 1),
            'reach': 0.9,
            'guard_height': 0.1,
            'guard_reach': 0.42,
            'crouch': 0.015,
        },
    },
    'jab': {
        'recipe': 'boxing_jab',
        'frames': 60,
        'params': {
            'combo': (0,),
            'reach': 0.9,
            'guard_height': 0.1,
            'guard_reach': 0.42,
            'crouch': 0.015,
        },
    },
    'chop': {
        'recipe': 'swing_weapon',
        'frames': 96,
        'params': {
            'raise_deg': 92.0,
            'strike_deg': 150.0,
            'strike_ratio': 0.26,
            'step_length': 0.24,
            'foot_height': 0.12,
            'crouch': 0.015,
        },
    },
    'turn_jump_chop': {
        'recipe': 'turn_jump_chop',
        'baseline_recipe': 'swing_weapon',
        'frames': 120,
        'params': {
            'turn_deg': 180.0,
            'jump_height': 0.28,
            'travel': 0.3,
            'foot_height': 0.12,
            'crouch': 0.1,
            'landing_crouch': 0.12,
            'takeoff_ratio': 0.24,
            'landing_ratio': 0.72,
            'raise_deg': 92.0,
            'strike_deg': 140.0,
            'elbow_deg': 52.0,
            'strike_start': 0.48,
            'strike_ratio': 0.18,
            'torso_twist_deg': 0.0,
            'torso_lean_deg': 0.0,
            'arm_clearance': 0.0,
        },
    },
    'turn_jump_chop_tuned': {
        'recipe': 'turn_jump_chop_tuned',
        'frames': 96,
        'params': {
            'turn_deg': 180.0,
            'travel': 0.3,
            'takeoff_ratio': 0.30,
            'jump_height': 0.24,
            'gravity_ratio': 6.5,
            'crouch': 0.14,
            'landing_crouch': 0.18,
            'torso_twist_deg': 16.0,
            'torso_lean_deg': 10.0,
            'hand_clearance': 0.24,
            'strike_reach': 0.86,
            'foot_tuck': 0.10,
            'push_seconds': 0.25,
            'settle_seconds': 0.22,
            'recover_seconds': 0.6,
            'attack_flight_ratio': 0.36,
            'impact_flight_ratio': 0.92,
            'primary_hand_keys': [
                [0.28, -0.42, 0.42], [1.0, 0.72, 0.12],
                [1.0, 0.72, 0.12], [0.20, -0.12, 1.0],
                [0.24, -0.40, 0.62], [0.28, -0.42, 0.42],
            ],
            'guard_hand_keys': [[0.32, -0.48, 0.24], [0.40, -0.28, 0.28],
                                [0.42, -0.36, 0.28], [0.32, -0.48, 0.24]],
            'blade_keys': [[0., .7, .7], [0., 1., -.1], [0., 1., -.1],
                           [0., -.3, .95], [0., .7, .7]],
            'elbow_pole': [1., -.20, -.30],
        },
    },
    'task_space': {
        'recipe': 'task_space',
        'frames': 96,
        'params': {
            'root_positions': None,
            'root_yaw': {'times': [0., 1.], 'values': [0., 0.]},
            'rotations': [],
            'targets': [],
            'plant_feet': False,
        },
    },
}

# Alternative spellings accepted by resolve_preset, so a caller need not know
# the exact preset key. Canonical names always take precedence.
ALIASES: dict[str, str] = {
    'walking': 'walk',
    'step': 'stride',
    'lunge': 'stride',
    'punch': 'boxing',
    'combo_punch': 'boxing',
    'slash': 'chop',
    'overhead_chop': 'chop',
    'turn_jump_slash': 'turn_jump_chop',
    'spin_jump_chop': 'turn_jump_chop',
}


def resolve_preset(
    name: str,
    *,
    num_frames: int | None = None,
    fps: float = 30.0,
    heading_deg: float = 0.0,
    **overrides: Any,
) -> dict[str, Any]:
    """Merge and validate parameters, returning an independent copy of the template.

    Lengths are ratios of limb length and angles are in degrees. Unknown
    parameter names raise rather than being ignored, so a typo cannot pass
    silently. The declared ranges are the ones these presets were verified
    against, not the full range the generators can survive.
    """
    name = ALIASES.get(name, name)
    if name not in MOTION_PRESETS:
        raise ValueError(f'unknown motion {name!r}, available {sorted(MOTION_PRESETS)}')
    spec = deepcopy(MOTION_PRESETS[name])
    p = spec['params']
    if overrides.keys() - p.keys():
        raise ValueError(f'unknown parameters: {sorted(overrides.keys() - p.keys())}')
    p.update(deepcopy(overrides))

    n = spec['frames'] if num_frames is None else num_frames
    if isinstance(n, bool) or not isinstance(n, (int, np.integer)) or n < 24:
        raise ValueError('num_frames must be an integer of at least 24')
    if not np.isfinite([fps, heading_deg]).all() or fps <= 0:
        raise ValueError('fps/heading_deg are invalid')

    if name == 'task_space':
        from .task_space import validate_task_space
        validate_task_space(p, int(n))
        spec.update(name=name, frames=int(n))
        return spec

    arrays = {'primary_hand_keys': (6, 3), 'guard_hand_keys': (4, 3),
              'blade_keys': (5, 3), 'elbow_pole': (3,)}
    for key, value in p.items():
        if name == 'turn_jump_chop_tuned' and key in arrays:
            raw = np.asarray(value)
            if raw.shape != arrays[key] or raw.dtype.kind not in 'iuf' or not np.isfinite(raw).all():
                raise ValueError(f'{key} must contain finite numbers with shape {arrays[key]}')
            if key == 'elbow_pole' and np.linalg.norm(raw) < 1e-8:
                raise ValueError('elbow_pole must be nonzero')
            if key == 'blade_keys' and np.any(np.linalg.norm(raw, axis=1) < 1e-8):
                raise ValueError('blade_keys must contain nonzero directions')
            continue
        if key == 'combo':
            continue
        if (
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, float, np.integer, np.floating))
            or not np.isfinite(value)
        ):
            raise ValueError(f'{key} must be a finite numeric scalar')

    if name == 'turn_jump_chop_tuned':
        ranges = {
            'turn_deg': (-360.0, 360.0), 'travel': (0.0, 0.6),
            'takeoff_ratio': (0.2, 0.4), 'jump_height': (0.12, 0.4),
            'gravity_ratio': (4.0, 10.0), 'crouch': (0.05, 0.2),
            'landing_crouch': (0.07, 0.24), 'torso_twist_deg': (0.0, 25.0),
            'torso_lean_deg': (0.0, 18.0), 'hand_clearance': (0.16, 0.42),
            'strike_reach': (0.65, 0.93), 'foot_tuck': (0.02, 0.16),
            'push_seconds': (0.05, 1.0), 'settle_seconds': (0.05, 1.0),
            'recover_seconds': (0.05, 2.0),
            'attack_flight_ratio': (0.0, 1.0), 'impact_flight_ratio': (0.0, 1.0),
        }
        for key, (low, high) in ranges.items():
            if not low <= p[key] <= high:
                raise ValueError(f'{key} must lie in [{low},{high}]')
        if not p['attack_flight_ratio'] < p['impact_flight_ratio'] < 1:
            raise ValueError('attack_flight_ratio must precede impact_flight_ratio before landing')
        duration = (int(n) - 1) / fps
        if n < 60 or p['takeoff_ratio'] * duration <= p['push_seconds']:
            raise ValueError('tuned jump needs at least 60 frames and enough time for anticipation')
        spec.update(name=name, frames=int(n))
        return spec

    if not 0 <= p['crouch'] <= 0.2:
        raise ValueError('crouch must lie in [0,.2]')
    for k in ('step_length', 'foot_height'):
        if k in p and not 0 < p[k] <= 0.5:
            raise ValueError(f'{k} must lie in (0,.5]')

    if name in ('walk', 'stride'):
        if type(p['steps']) is not int or p['steps'] < 1 or n / p['steps'] < 12:
            raise ValueError('steps must be a positive integer with at least 12 frames per step')
        if not 0 <= p['arm_gain'] <= 2 or not 0 <= p['elbow_deg'] <= 90:
            raise ValueError('arm_gain/elbow_deg are out of range')

    if name in ('boxing', 'jab'):
        combo = p['combo']
        if (
            not isinstance(combo, (list, tuple))
            or not combo
            or any(type(i) is not int or i not in (0, 1) for i in combo)
        ):
            raise ValueError('combo must be non-empty and contain only 0/1')
        if (
            n * 0.8 / len(combo) < 10
            or not 0.5 <= p['reach'] <= 0.95
            or not 0 <= p['guard_height'] <= 0.2
            or not 0.2 <= p['guard_reach'] < p['reach']
        ):
            raise ValueError('not enough frames for the punches, or reach/guard_height/guard_reach out of range')

    if name in ('chop', 'turn_jump_chop'):
        if (
            not 0 < p['raise_deg'] <= 130
            or not 30 <= p['strike_deg'] <= 180
            or not 0.12 <= p['strike_ratio'] <= 0.4
        ):
            raise ValueError('chop angles or durations fall outside the validated range of this preset')

    if name == 'turn_jump_chop':
        for key, upper in (('torso_twist_deg', 25.0), ('torso_lean_deg', 18.0), ('arm_clearance', 0.1)):
            if not 0 <= p[key] <= upper:
                raise ValueError(f'{key} must lie in [0,{upper}]')
        if (
            not -360 <= p['turn_deg'] <= 360
            or not 0.08 <= p['jump_height'] <= 0.6
            or not 0 <= p['travel'] <= 0.6
        ):
            raise ValueError('turn_deg/jump_height/travel are out of range')
        if not 0 <= p['landing_crouch'] <= 0.2 or not 0 <= p['elbow_deg'] <= 90:
            raise ValueError('landing_crouch/elbow_deg are out of range')
        takeoff, landing = (p['takeoff_ratio'], p['landing_ratio'])
        start, end = (p['strike_start'], p['strike_start'] + p['strike_ratio'])
        if not 0.15 <= takeoff < start < end < landing <= 0.85:
            raise ValueError('phase order must be takeoff < strike start < strike end < landing, and landing no later than .85')
        # Each phase needs at least two samples, or the curve degenerates.
        if min(start - takeoff, p['strike_ratio'], landing - end, 1 - landing) * (n - 1) < 2:
            raise ValueError('not enough samples in the turn, strike or landing phases; raise num_frames or adjust the ratios')

    spec.update(name=name, frames=int(n))
    return spec
