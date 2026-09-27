"""Validate explicit skeleton fitting configuration without supplying presets."""
from copy import deepcopy
import numpy as np
from .limbs import LimbGroup


def require_config(config, fields, label):
    if not isinstance(config, dict):
        raise ValueError(f'{label} config must be a dictionary')
    missing = set(fields) - set(config)
    unknown = set(config) - set(fields)
    if missing or unknown:
        raise ValueError(f'{label} config missing keys {sorted(missing)}, unknown keys {sorted(unknown)}')
    return deepcopy(config)


def finite_number(value, name, *, positive=False):
    if isinstance(value, (bool, str)) or not np.isscalar(value) or not np.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if value < 0 or (positive and value == 0):
        raise ValueError(f'{name} must be {"positive" if positive else "non-negative"}')
    return value


def integer(value, name, minimum):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def resolve_rig_config(config):
    fields = (
        'name', 'up', 'forward', 'spine_axis', 'spine_joints', 'spine_span',
        'root_at', 'limb_groups', 'section_resolution', 'chain_samples',
        'candidate_keep', 'spine_center_weight', 'spine_continuity_weight',
        'simplify_min_gap', 'simplify_gap_ratio', 'simplify_length_weight',
        'limb_tangent_step_limit', 'limb_anchor_weight', 'limb_anchor_power',
        'limb_anchor_fraction', 'limb_radius_window', 'limb_step_window',
        'limb_initial_distance_weight', 'limb_radius_cap', 'limb_continuity_weight',
        'section_vertex_tolerance', 'section_plane_nudge', 'section_weld_tolerance',
        'clearance_chunk_size',
    )
    p = require_config(config, fields, 'rig')
    if not isinstance(p['name'], str) or not p['name'].strip():
        raise ValueError('name must be a non-empty string')
    for name in ('spine_joints', 'chain_samples'):
        integer(p[name], name, 2)
    for name in ('section_resolution', 'candidate_keep', 'simplify_min_gap', 'clearance_chunk_size'):
        integer(p[name], name, 1)
    for name in (
        'spine_center_weight', 'spine_continuity_weight', 'simplify_gap_ratio',
        'simplify_length_weight', 'limb_tangent_step_limit', 'limb_anchor_weight',
        'limb_anchor_power', 'limb_radius_window', 'limb_step_window',
        'limb_initial_distance_weight', 'limb_continuity_weight',
    ):
        finite_number(p[name], name)
    for name in ('limb_radius_cap', 'section_vertex_tolerance', 'section_plane_nudge', 'section_weld_tolerance'):
        finite_number(p[name], name, positive=True)
    finite_number(p['limb_anchor_fraction'], 'limb_anchor_fraction', positive=True)
    if p['limb_anchor_fraction'] > 1:
        raise ValueError('limb_anchor_fraction must be in (0,1]')
    if p['spine_axis'] not in ('vertical', 'horizontal'):
        raise ValueError('spine_axis must be vertical or horizontal')
    span = np.asarray(p['spine_span'], float)
    if span.shape != (2,) or not np.isfinite(span).all() or not 0 < span[0] < span[1] < 1:
        raise ValueError('spine_span must satisfy 0 < lo < hi < 1')
    finite_number(p['root_at'], 'root_at')
    if p['root_at'] > 1:
        raise ValueError('root_at must lie in [0,1]')
    p['spine_span'] = tuple(span)
    p['limb_groups'] = tuple(LimbGroup.coerce(g) for g in p['limb_groups'])
    if any(g.reach not in ('down', 'out') for g in p['limb_groups']):
        raise ValueError('only down/out limb tracing is supported')
    if len({g.name for g in p['limb_groups']}) != len(p['limb_groups']):
        raise ValueError('limb group names must be unique')
    for group in p['limb_groups']:
        gap = max(p['simplify_min_gap'], int(p['simplify_gap_ratio'] * (p['chain_samples'] - 1) / (group.joints - 1)))
        if (group.joints - 1) * gap >= p['chain_samples']:
            raise ValueError('chain_samples is too small for the requested limb joint count and gap')
    return p
