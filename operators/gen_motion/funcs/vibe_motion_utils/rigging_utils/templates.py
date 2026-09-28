"""Required configuration for calibrated, landmark-guided skeleton fitting."""
from copy import deepcopy
from numbers import Real
import numpy as np


def require_config(config, fields, label):
    if not isinstance(config, dict):
        raise ValueError(f'{label} config must be a dictionary')
    missing, unknown = set(fields) - set(config), set(config) - set(fields)
    if missing or unknown:
        raise ValueError(f'{label} config missing keys {sorted(missing)}, unknown keys {sorted(unknown)}')
    return deepcopy(config)


def finite_number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if value < 0 or (positive and value == 0):
        raise ValueError(f'{name} must be {"positive" if positive else "non-negative"}')
    return value


def integer(value, name, minimum):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def resolve_rig_config(config):
    from ..bvh import validate_hierarchy
    p = require_config(config, ('name', 'up', 'forward', 'names', 'parents', 'chains',
                                'cameras', 'observations', 'fit', 'reconstruction', 'geometry'), 'rig')
    names = p['names']
    if not isinstance(p['name'], str) or not p['name'].strip():
        raise ValueError('Rig name must be nonempty')
    if (not isinstance(names, list) or not names or any(not isinstance(n, str) or not n
            or any(c.isspace() or c in '{}' for c in n) for n in names) or len(set(names)) != len(names)):
        raise ValueError('Joint names must be unique nonempty BVH-safe strings')
    validate_hierarchy(p['parents'], np.zeros((len(names), 3)))
    if not isinstance(p['chains'], dict) or not p['chains']:
        raise ValueError('Explicit named chains are required')
    owned = set()
    for label, chain in p['chains'].items():
        if not isinstance(label, str) or not label or not isinstance(chain, list) or not chain:
            raise ValueError('Each chain needs a name and joint list')
        if any(n not in names for n in chain) or len(set(chain)) != len(chain) or owned.intersection(chain):
            raise ValueError('Chains must contain unique known joints without overlap')
        ids = [names.index(n) for n in chain]
        if any(p['parents'][b] != a for a, b in zip(ids, ids[1:])):
            raise ValueError('Chains must follow parent-child paths')
        owned.update(chain)
    if owned != set(names) or set(p['fit']) != set(names) or set(p['observations']) != set(names):
        raise ValueError('Chains, fit settings and observations must cover every joint')
    r = require_config(p['reconstruction'], ('max_reprojection_pixels', 'max_fitted_reprojection_pixels',
                                            'max_condition', 'axis_tolerance', 'conflict_policy'), 'reconstruction')
    for key in r.keys() - {'conflict_policy'}:
        finite_number(r[key], key, positive=True)
    if r['max_condition'] < 1 or r['conflict_policy'] not in ('reject', 'retain_annotation'):
        raise ValueError('Invalid reconstruction policy')
    g = require_config(p['geometry'], ('section_resolution', 'section_vertex_tolerance', 'section_plane_nudge',
                                      'section_weld_tolerance', 'clearance_chunk_size', 'prior_distance_gain',
                                      'open_quantiles', 'min_open_endpoints', 'epsilon'), 'geometry')
    for key in ('section_resolution', 'clearance_chunk_size', 'min_open_endpoints'):
        integer(g[key], key, 1)
    for key in ('section_vertex_tolerance', 'section_plane_nudge', 'section_weld_tolerance', 'epsilon'):
        finite_number(g[key], key, positive=True)
    finite_number(g['prior_distance_gain'], 'prior_distance_gain')
    q = np.asarray(g['open_quantiles'], float)
    if q.shape != (2,) or not np.isfinite(q).all() or not 0 <= q[0] < q[1] <= 1:
        raise ValueError('Open-section quantiles must increase within [0,1]')
    for name, hint in p['fit'].items():
        require_config(hint, ('radius', 'axis', 'strength', 'allow_open'), name)
        radius = np.asarray(hint['radius'], float)
        if radius.shape != (3,) or not np.isfinite(radius).all() or np.any(radius <= 0):
            raise ValueError(f'{name}: radius must be a positive 3D vector')
        if type(hint['axis']) is not int or hint['axis'] not in (0, 1, 2):
            raise ValueError(f'{name}: axis must be 0, 1 or 2')
        if finite_number(hint['strength'], 'strength') > 1 or type(hint['allow_open']) is not bool:
            raise ValueError(f'{name}: strength must lie in [0,1] and allow_open must be boolean')
    return p
