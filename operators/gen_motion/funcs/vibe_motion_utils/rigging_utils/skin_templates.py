from __future__ import annotations
from copy import deepcopy
from typing import Any
KERNELS = ('inverse', 'gaussian', 'linear')
SKIN_PRESETS = {'rigid': {'params': {'kernel': 'inverse',
                      'falloff': 4.0,
                      'radius_scale': 1.0,
                      'max_influences': 1,
                      'floor': 0.0,
                      'smooth_iterations': 0,
                      'smooth_rate': 0.0}},
 'smooth': {'params': {'kernel': 'inverse',
                       'falloff': 4.0,
                       'radius_scale': 0.35,
                       'max_influences': 4,
                       'floor': 0.0001,
                       'smooth_iterations': 6,
                       'smooth_rate': 0.5}},
 'compact': {'params': {'kernel': 'inverse',
                        'falloff': 6.0,
                        'radius_scale': 0.18,
                        'max_influences': 4,
                        'floor': 0.0001,
                        'smooth_iterations': 8,
                        'smooth_rate': 0.45}},
 'axial': {'params': {'kernel': 'gaussian',
                      'falloff': 1.0,
                      'radius_scale': 0.6,
                      'max_influences': 3,
                      'floor': 0.0001,
                      'smooth_iterations': 10,
                      'smooth_rate': 0.5}}}
SKIN_PRESET_FOR_MORPHOLOGY: dict[str, str] = {'quadruped': 'smooth', 'octopod': 'compact', 'myriapod': 'compact', 'biped': 'compact', 'biped_armed': 'compact', 'serpentine': 'axial', 'unknown': 'smooth'}
_RANGES: dict[str, tuple[float, float]] = {'falloff': (0.1, 16.0), 'radius_scale': (0.01, 4.0), 'max_influences': (1, 12), 'floor': (0.0, 0.5), 'smooth_iterations': (0, 64), 'smooth_rate': (0.0, 1.0)}
_INTEGERS = ('max_influences', 'smooth_iterations')

def resolve_skin_preset(name: str, **overrides: Any) -> dict[str, Any]:
    'Return an independent copy of one skin preset with overrides merged in.'
    key = name if name in SKIN_PRESETS else SKIN_PRESET_FOR_MORPHOLOGY.get(name, '')
    if key not in SKIN_PRESETS:
        raise ValueError(f'unknown skin preset {name!r}, available {sorted(SKIN_PRESETS)} or morphologies {sorted(SKIN_PRESET_FOR_MORPHOLOGY)}')
    spec = deepcopy(SKIN_PRESETS[key])
    params = spec['params']
    unknown = set(overrides) - set(params)
    if unknown:
        raise ValueError(f'unknown parameters {sorted(unknown)}; {key} accepts {sorted(params)}')
    params.update(deepcopy(overrides))
    if params['kernel'] not in KERNELS:
        raise ValueError(f"unknown kernel {params['kernel']!r}, available {KERNELS}")
    for field, (lo, hi) in _RANGES.items():
        value = params[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f'{field} must be a number, got {value!r}')
        if not lo <= value <= hi:
            raise ValueError(f'{field} must lie in [{lo},{hi}], got {value}')
    for field in _INTEGERS:
        if not isinstance(params[field], int) or isinstance(params[field], bool):
            raise ValueError(f'{field} must be an integer, got {params[field]!r}')
    spec['name'] = key
    return spec
