"""Validate explicit skinning strategy parameters without built-in presets."""
from .templates import finite_number, integer, require_config

KERNELS = ('inverse', 'gaussian', 'linear')


def resolve_skin_config(config):
    fields = (
        'kernel', 'falloff', 'radius_scale', 'max_influences', 'floor',
        'smooth_iterations', 'smooth_rate', 'bone_convention',
        'distance_epsilon_ratio', 'sum_tolerance', 'screening', 'edge_epsilon_ratio',
    )
    p = require_config(config, fields, 'skin')
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
