"""Calibrated multiview reconstruction followed by bounded landmark-guided fitting."""
from __future__ import annotations
import numpy as np
from .types import RigResult
from .templates import resolve_rig_config, require_config, finite_number, integer
from .sections import canonical_mesh


def camera_matrices(cameras, *, tolerance):
    if not isinstance(cameras, dict) or not cameras:
        raise ValueError('Calibrated cameras are required; no automatic landmarks are inferred')
    output = {}
    for name, camera in cameras.items():
        c = require_config(camera, ('width', 'height', 'right', 'up', 'center', 'pixels_per_unit', 'pixel_origin'), 'camera')
        integer(c['width'], 'width', 1)
        integer(c['height'], 'height', 1)
        finite_number(c['pixels_per_unit'], 'pixels_per_unit', positive=True)
        r, u, center, origin = [np.asarray(c[k], float) for k in ('right', 'up', 'center', 'pixel_origin')]
        if any(v.shape != (3,) for v in (r, u, center)) or origin.shape != (2,) or not all(np.isfinite(v).all() for v in (r, u, center, origin)):
            raise ValueError(f'{name}: invalid camera vectors')
        if not np.allclose([r @ r, u @ u, r @ u], [1, 1, 0], atol=tolerance, rtol=0):
            raise ValueError(f'{name}: camera axes must be orthonormal')
        matrix = c['pixels_per_unit'] * np.stack([r, -u])
        output[name] = matrix, origin - matrix @ center, np.array([c['width'], c['height']])
    return output


def reconstruct_joints(cameras, observations, *, config):
    matrices = camera_matrices(cameras, tolerance=config['axis_tolerance'])
    if not isinstance(observations, dict) or not observations:
        raise ValueError('Nonempty joint observations required')
    positions, reports = {}, {}
    for name, views in observations.items():
        if not isinstance(views, dict) or len(views) < 2:
            raise ValueError(f'{name}: at least two named views are required')
        lhs, rhs = [], []
        for view, item in views.items():
            if view not in matrices:
                raise ValueError(f'{name}: unknown camera {view}')
            require_config(item, ('pixel', 'confidence', 'inferred'), 'observation')
            pixel = np.asarray(item['pixel'], float)
            matrix, offset, size = matrices[view]
            confidence = finite_number(item['confidence'], 'confidence', positive=True)
            if confidence > 1 or type(item['inferred']) is not bool:
                raise ValueError('Confidence must lie in (0,1] and inferred must be boolean')
            if pixel.shape != (2,) or not np.isfinite(pixel).all() or np.any(pixel < 0) or np.any(pixel > size - 1):
                raise ValueError(f'{name}/{view}: pixel outside calibrated image')
            lhs.append(matrix * np.sqrt(confidence))
            rhs.append((pixel - offset) * np.sqrt(confidence))
        point, _, rank, singular = np.linalg.lstsq(np.concatenate(lhs), np.concatenate(rhs), rcond=None)
        if rank != 3 or singular[0] / singular[-1] > config['max_condition']:
            raise ValueError(f'{name}: degenerate or near-parallel views')
        residual = {v: float(np.linalg.norm(matrices[v][0] @ point + matrices[v][1] - item['pixel'])) for v, item in views.items()}
        if max(residual.values()) > config['max_reprojection_pixels']:
            raise ValueError(f'{name}: inconsistent calibrated annotations')
        positions[name] = point
        reports[name] = {'reprojection_pixels': residual, 'condition': float(singular[0] / singular[-1]),
                         'inferred_views': [v for v in views if views[v]['inferred']]}
    return positions, reports


def reprojection_report(rig, *, config):
    matrices = camera_matrices(config['cameras'], tolerance=config['reconstruction']['axis_tolerance'])
    return {name: {v: float(np.linalg.norm(matrices[v][0] @ point + matrices[v][1] - item['pixel']))
                   for v, item in config['observations'][name].items()}
            for name, point in zip(rig.joint_names, rig.joints)}


def fit_joint_prior(sections, target, hint, *, config):
    axis, radius, strength = hint['axis'], np.asarray(hint['radius']), hint['strength']
    other = [i for i in range(3) if i != axis]
    low, high = target[other] - radius[other], target[other] + radius[other]
    section = sections.section(axis, float(target[axis]))
    points, clearance = section.candidates(config['section_resolution'], keep=None, bounds=(low, high), chunk_size=config['clearance_chunk_size'])
    if len(points):
        distance = np.sum(((points - target[other]) / radius[other]) ** 2, axis=1)
        score = (1 + config['prior_distance_gain'] * strength) * distance - (1 - strength) * clearance / radius[other].max()
        chosen, mode = points[int(score.argmin())], 'closed_section'
    elif hint['allow_open']:
        hits = []
        for a, b in section.segments:
            edge, lo, hi = b - a, 0., 1.
            for k in range(2):
                if abs(edge[k]) <= config['epsilon']:
                    if a[k] < low[k] or a[k] > high[k]:
                        hi = -1.
                        break
                else:
                    t0, t1 = sorted(((low[k] - a[k]) / edge[k], (high[k] - a[k]) / edge[k]))
                    lo, hi = max(lo, t0), min(hi, t1)
            if lo <= hi:
                hits.extend([a + lo * edge, a + hi * edge])
        if len(hits) < config['min_open_endpoints']:
            raise ValueError('No surface evidence in the configured landmark window')
        center = np.quantile(hits, config['open_quantiles'], axis=0).mean(axis=0)
        chosen, mode = np.clip(strength * target[other] + (1 - strength) * center, low, high), 'open_surface_evidence'
    else:
        raise ValueError('No closed section in the landmark window; open fitting is disabled')
    point = target.copy()
    point[other] = chosen
    return point, mode


def rig_skeleton(mesh, *, config):
    """Topology and visual observations are mandatory; the old tracing fallback is removed."""
    p = resolve_rig_config(config)
    raw, triangulation = reconstruct_joints(p['cameras'], p['observations'], config=p['reconstruction'])
    sections, frame, axes = canonical_mesh(mesh, p['up'], p['forward'], vertical=True, config=p['geometry'])
    joints, modes = [], {}
    for name in p['names']:
        target = (raw[name] - frame.origin) @ axes.T / frame.scale
        point, modes[name] = fit_joint_prior(sections, target, p['fit'][name], config=p['geometry'])
        joints.append(point @ axes * frame.scale + frame.origin)
    rig = RigResult(p['name'], np.asarray(joints), np.asarray(p['parents'], int), p['names'],
                    {k: [p['names'].index(n) for n in ns] for k, ns in p['chains'].items()}, frame, p)
    errors, conflicts = reprojection_report(rig, config=p), {}
    for i, name in enumerate(rig.joint_names):
        if max(errors[name].values()) > p['reconstruction']['max_fitted_reprojection_pixels']:
            if p['reconstruction']['conflict_policy'] == 'reject':
                raise ValueError(f'{name}: geometric fit exceeds reprojection tolerance')
            conflicts[name] = {'rejected_fit': rig.joints[i].tolist(), 'reprojection_pixels': errors[name]}
            rig.joints[i] = raw[name]
            if max(triangulation[name]['reprojection_pixels'].values()) > p['reconstruction']['max_fitted_reprojection_pixels']:
                raise ValueError(f'{name}: retained annotation also violates fitted tolerance')
    rig.params = {**p, 'diagnostics': {'triangulation': triangulation, 'fit_modes': modes, 'fit_conflicts': conflicts}}
    rig.notes = ['Calibrated visual landmarks and explicit topology; no reference rig or unguided tracing.',
                 'Open-section fits and retained annotations do not certify solid containment. No surface snapping.']
    return rig
