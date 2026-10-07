"""Validate explicit skinning strategy parameters without built-in presets."""
from .templates import finite_number, integer, require_config

KERNELS = ('inverse', 'gaussian', 'linear')
GATE_FIELDS = ('seed_mode', 'seed_ratio', 'gate_ratio', 'gate_abs_ratio', 'gate_limit_ratio', 'fallback_ratio',
               'radius_cap_ratio')
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
    p = require_config({k: v for k, v in config.items() if k != 'gate'}, fields, 'skin')
    p['gate'] = gate
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
    return p
