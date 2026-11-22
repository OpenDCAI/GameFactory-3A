"""Validate explicit skinning strategy parameters without built-in presets."""
from .templates import finite_number, integer, require_config

KERNELS = ('inverse', 'gaussian', 'linear')
GATE_FIELDS = ('seed_mode', 'seed_ratio', 'gate_ratio', 'gate_abs_ratio', 'gate_limit_ratio', 'fallback_ratio',
               'radius_cap_ratio')
SKIN_RANGES = {'falloff': (0.1, 16.0), 'radius_scale': (0.01, 4.0), 'max_influences': (1, 12), 'floor': (0.0, 0.5),
               'smooth_iterations': (0, 64), 'smooth_rate': (0.0, 1.0)}
RAY_FIELDS = ('ray_samples', 'ray_directions', 'ray_reach_ratio')


def resolve_gate_config(gate):
    """Optional surface-geodesic gate; ``None`` keeps unrestricted distance weights.

    ``seed_mode`` is ``nearest`` (nearest-bone partition) or ``visible`` (first surface hit by
    rays cast around each bone, which also needs ``ray_samples``, ``ray_directions`` and
    ``ray_reach_ratio``).
    """
    if gate is None:
        return None
    if not isinstance(gate, dict) or gate.get('seed_mode') not in ('nearest', 'visible'):
        raise ValueError('skin.gate.seed_mode must be nearest or visible')
    fields = GATE_FIELDS + (RAY_FIELDS if gate['seed_mode'] == 'visible' else ())
    g = require_config(gate, fields, 'skin.gate')
    for name in fields[1:]:
        finite_number(g[name], name, positive=True)
    if min(g['seed_ratio'], g['gate_ratio'], g['fallback_ratio'], g['radius_cap_ratio']) < 1:
        raise ValueError('seed_ratio, gate_ratio, fallback_ratio and radius_cap_ratio must be at least one')
    if g['seed_mode'] == 'visible':
        integer(g['ray_samples'], 'ray_samples', 1)
        integer(g['ray_directions'], 'ray_directions', 3)
    return g


def resolve_skin_config(config):
    fields = (
        'kernel', 'falloff', 'radius_scale', 'max_influences', 'floor',
        'smooth_iterations', 'smooth_rate', 'bone_convention',
        'distance_epsilon_ratio', 'sum_tolerance', 'screening', 'edge_epsilon_ratio',
    )
    if not isinstance(config, dict):
        raise ValueError('skin config must be a dictionary')
    gate = resolve_gate_config(config.get('gate'))
    optional = ('gate', 'component_rigid', 'component_max_fraction')
    p = require_config({k: v for k, v in config.items() if k not in optional}, fields, 'skin')
    p['gate'] = gate
    p['component_rigid'] = config.get('component_rigid', False)
    p['component_max_fraction'] = config.get('component_max_fraction', 1.0)
    if not isinstance(p['component_rigid'], bool):
        raise ValueError('component_rigid must be a boolean')
    finite_number(p['component_max_fraction'], 'component_max_fraction', positive=True)
    if p['component_max_fraction'] > 1:
        raise ValueError('component_max_fraction must lie in (0,1]')
    if p['kernel'] not in KERNELS:
        raise ValueError(f'unknown kernel {p["kernel"]!r}, available {KERNELS}')
    if p['bone_convention'] not in ('incoming', 'outgoing'):
        raise ValueError('bone_convention must be incoming or outgoing')
    for name in ('falloff', 'radius_scale', 'distance_epsilon_ratio', 'screening', 'edge_epsilon_ratio'):
        finite_number(p[name], name, positive=True)
    for name in ('floor', 'smooth_rate', 'sum_tolerance'):
        finite_number(p[name], name)
    if p['floor'] > 1 or p['smooth_rate'] > 1:
        raise ValueError('floor and smooth_rate must lie in [0,1]')
    integer(p['max_influences'], 'max_influences', 1)
    integer(p['smooth_iterations'], 'smooth_iterations', 0)
    for name, (low, high) in SKIN_RANGES.items():
        if not low <= p[name] <= high:
            raise ValueError(f'{name} must lie in [{low},{high}], got {p[name]}')
    return p
