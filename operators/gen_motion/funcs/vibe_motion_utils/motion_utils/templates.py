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

    # Every parameter except the punch pattern must be a finite real scalar.
    for key, value in p.items():
        if key == 'combo':
            continue
        if (
            isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, float, np.integer, np.floating))
            or not np.isfinite(value)
        ):
            raise ValueError(f'{key} must be a finite numeric scalar')

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
        ):
            raise ValueError('not enough frames for the punches, or reach/guard_height out of range')

    if name in ('chop', 'turn_jump_chop'):
        if (
            not 0 < p['raise_deg'] <= 130
            or not 30 <= p['strike_deg'] <= 180
            or not 0.12 <= p['strike_ratio'] <= 0.4
        ):
            raise ValueError('chop angles or durations fall outside the validated range of this preset')

    if name == 'turn_jump_chop':
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
