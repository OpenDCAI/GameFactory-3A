"""Define rigging presets and validate skeleton fitting parameter overrides."""
from copy import deepcopy
import numpy as np
from .limbs import LimbGroup
RIG_PRESETS = {'biped': {'spine_axis': 'vertical', 'spine_joints': 5, 'spine_span': (0.45, 0.88), 'root_at': 0.0, 'limb_groups': (LimbGroup('arm', 0.6, 3, 'out', 0.36, 0.1, span=(0.18, 0.85)), LimbGroup('leg', 0.0, 4, 'down', 0.1, 0.1, span=(0.1, 0.96)))}, 'quadruped': {'spine_axis': 'horizontal', 'spine_joints': 7, 'spine_span': (0.04, 0.94), 'root_at': 0.37, 'limb_groups': (LimbGroup('front', 0.69, 3, 'down', 0.12, 0.15, span=(0.1, 0.88)), LimbGroup('rear', 0.37, 4, 'down', 0.14, 0.15, span=(0.02, 0.97)))}, 'axial': {'spine_axis': 'horizontal', 'spine_joints': 9, 'spine_span': (0.04, 0.96), 'root_at': 0.5, 'limb_groups': ()}}

for _params in RIG_PRESETS.values():
    _params.update(section_resolution=25, chain_samples=41)


def resolve_rig_preset(name, **overrides):
    key = {'biped_armed': 'biped', 'serpentine': 'axial'}.get(name, name)
    if key not in RIG_PRESETS:
        raise ValueError(f'unknown preset {name!r}, available {sorted(RIG_PRESETS)}')
    p = deepcopy(RIG_PRESETS[key])
    unknown = overrides.keys() - p.keys()
    if unknown:
        raise ValueError(f'unknown parameters: {sorted(unknown)}')
    p.update(deepcopy(overrides))
    for name, lo, hi in (('spine_joints', 2, 32), ('section_resolution', 9, 65), ('chain_samples', 17, 101)):
        value = p[name]
        if isinstance(value, bool) or not isinstance(value, int) or (not lo <= value <= hi):
            raise ValueError(f'{name} must be an integer in [{lo},{hi}]')
    if p['spine_axis'] not in ('vertical', 'horizontal'):
        raise ValueError('spine_axis supports vertical/horizontal only; orientation comes from up/forward')
    span = np.asarray(p['spine_span'], float)
    if span.shape != (2,) or not np.isfinite(span).all() or (not 0 < span[0] < span[1] < 1):
        raise ValueError('spine_span must satisfy 0 < lo < hi < 1')
    if not np.isscalar(p['root_at']) or not np.isfinite(p['root_at']) or (not 0 <= p['root_at'] <= 1):
        raise ValueError('root_at must lie in [0,1]')
    p['spine_span'] = tuple(span)
    p['limb_groups'] = tuple((LimbGroup.coerce(g) for g in p['limb_groups']))
    if any((g.reach not in ('down', 'out') for g in p['limb_groups'])):
        raise ValueError('only down/out limbs are validated; other reach values are rejected rather than silently substituted')
    if len({g.name for g in p['limb_groups']}) != len(p['limb_groups']):
        raise ValueError('limb group names must be unique')
    if max([p['spine_joints']] + [g.joints for g in p['limb_groups']]) > p['chain_samples'] // 2:
        raise ValueError('chain_samples is too small for the joint count')
    return {'name': key, 'params': p}
