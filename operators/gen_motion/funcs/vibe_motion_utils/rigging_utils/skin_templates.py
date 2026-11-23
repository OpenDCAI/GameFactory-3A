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


def _bone_list(bones, label):
    if not isinstance(bones, list) or not bones or not all(isinstance(b, str) and b for b in bones):
        raise ValueError(f'{label}: bones must be a non-empty list of joint names')
    return list(bones)


def resolve_part_bones(part_bones):
    """``{"<node>[,<node>...]": [bone names]}``: vertices of those scene nodes may only take these bones."""
    if not isinstance(part_bones, dict):
        raise ValueError('part_bones must map scene-node ids to bone lists')
    out = {}
    for key, bones in part_bones.items():
        try:
            nodes = tuple(int(x) for x in str(key).split(','))
        except ValueError:
            raise ValueError(f'part_bones key {key!r} must be comma-separated scene-node ids') from None
        out[nodes] = _bone_list(bones, f'part_bones[{key}]')
    return out


REGION_SHAPES = {'box': 4, 'circle': 3, 'segment': 5, 'abs_range': 3}


def resolve_regions(regions):
    """View-space restrictions, applied in order (later regions override earlier ones).

    Each region: ``view`` (a camera name), ``box`` ``[u0, v0, u1, v1]`` pixels, ``bones``; optional ``circle``
    ``[u, v, radius]`` or ``segment`` ``[u0, v0, u1, v1, half_width]`` replacing the box, and ``abs_range``
    ``[axis, lo, hi]`` (world ``|coordinate|`` range) intersecting it.
    """
    if not isinstance(regions, list):
        raise ValueError('regions must be a list')
    out = []
    for i, r in enumerate(regions):
        label = f'regions[{i}]'
        if not isinstance(r, dict) or not {'view', 'box', 'bones'} <= set(r) or set(r) - {'view', 'bones', *REGION_SHAPES}:
            raise ValueError(f'{label}: required view, box, bones; optional circle, segment, abs_range')
        if not isinstance(r['view'], str):
            raise ValueError(f'{label}: view must be a camera name')
        region = {'view': r['view'], 'bones': _bone_list(r['bones'], label)}
        for key, size in REGION_SHAPES.items():
            if key in r:
                values = r[key]
                if not isinstance(values, list) or len(values) != size:
                    raise ValueError(f'{label}: {key} needs {size} numbers')
                for value in values:
                    finite_number(value, f'{label}.{key}')
                region[key] = [float(v) for v in values]
        if 'abs_range' in region and region['abs_range'][0] not in (0., 1., 2.):
            raise ValueError(f'{label}: abs_range axis must be 0, 1 or 2')
        out.append(region)
    return out


def resolve_skin_config(config):
    fields = (
        'kernel', 'falloff', 'radius_scale', 'max_influences', 'floor',
        'smooth_iterations', 'smooth_rate', 'bone_convention',
        'distance_epsilon_ratio', 'sum_tolerance', 'screening', 'edge_epsilon_ratio',
    )
    if not isinstance(config, dict):
        raise ValueError('skin config must be a dictionary')
    gate = resolve_gate_config(config.get('gate'))
    optional = ('gate', 'component_rigid', 'component_max_fraction', 'part_bones', 'regions', 'cameras')
    p = require_config({k: v for k, v in config.items() if k not in optional}, fields, 'skin')
    p['gate'] = gate
    p['part_bones'] = resolve_part_bones(config.get('part_bones', {}))
    p['regions'] = resolve_regions(config.get('regions', []))
    p['cameras'] = config.get('cameras')
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
