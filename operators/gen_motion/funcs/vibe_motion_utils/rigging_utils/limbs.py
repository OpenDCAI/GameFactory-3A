"""Bounded rest-pole and pre-bend search evaluated against actual generated motion."""
from copy import deepcopy
import numpy as np
from .checks import MeshContainment, enclosure_evidence, validate_tree
from .generate import reprojection_report
from .templates import require_config, finite_number


def refine_orientation(mesh, rig, limbs, evaluate, *, config, quality):
    """Keep endpoints and skin weights fixed; accept only improved valid candidates."""
    p = require_config(config, ('candidate_prebend_ratios', 'max_displacement_ratio', 'max_length_change_ratio',
                                'min_enclosure_fraction', 'max_enclosure_drop', 'min_improvement',
                                'max_reprojection_pixels', 'epsilon'), 'orientation')
    for key in p.keys() - {'candidate_prebend_ratios'}:
        finite_number(p[key], key, positive=key == 'epsilon')
    ratios = p['candidate_prebend_ratios']
    if not isinstance(ratios, list) or not ratios or any(finite_number(r, 'prebend', positive=True) >= 1 for r in ratios):
        raise ValueError('Explicit pre-bend ratios must lie in (0,1)')
    if p['min_enclosure_fraction'] > 1 or p['max_enclosure_drop'] > 1 or not limbs:
        raise ValueError('Invalid enclosure budgets or missing probe limbs')
    validate_tree(rig)
    current, poles, history = deepcopy(rig), {}, []
    checker = MeshContainment(mesh, config=quality)
    certified = checker.classify(rig.joints, rig.joint_names)
    best = deepcopy(evaluate(deepcopy(current), {}))
    if not np.isfinite(best['score']):
        raise ValueError('Non-finite baseline score')
    initial = float(best['score'])
    for key, spec in limbs.items():
        require_config(spec, ('chain', 'forward'), 'orientation limb')
        ids = np.asarray(spec['chain'])
        if ids.shape != (3,) or ids.dtype.kind not in 'iu' or np.any((ids < 0) | (ids >= rig.num_joints)):
            raise ValueError('Orientation needs a three-joint chain')
        a, b, c = ids
        if rig.parents[b] != a or rig.parents[c] != b:
            raise ValueError('Orientation chain must follow hierarchy')
        anchor = deepcopy(current)
        lengths = np.linalg.norm(np.diff(anchor.joints[ids], axis=0), axis=1)
        axis = anchor.joints[c] - anchor.joints[a]
        if min(lengths.min(), np.linalg.norm(axis)) <= p['epsilon']:
            raise ValueError('Degenerate probe limb')
        axis /= np.linalg.norm(axis)
        preferred = np.asarray(spec['forward'], float)
        if preferred.shape != (3,) or not np.isfinite(preferred).all():
            raise ValueError('Invalid bend direction')
        preferred = preferred - np.dot(preferred, axis) * axis
        if np.linalg.norm(preferred) <= p['epsilon']:
            raise ValueError('Bend direction parallel to limb')
        preferred /= np.linalg.norm(preferred)
        baseline = enclosure_evidence(mesh, anchor.joints[ids], config=quality)
        for ratio in (None, *ratios):
            entry = {'part': key, 'prebend_ratio': ratio, 'accepted': False}
            try:
                candidate = deepcopy(anchor)
                if ratio is not None:
                    along = np.dot(anchor.joints[b] - anchor.joints[a], axis)
                    if not 0 < along < np.linalg.norm(anchor.joints[c] - anchor.joints[a]):
                        raise ValueError('Middle joint projects outside limb')
                    candidate.joints[b] = anchor.joints[a] + along * axis + ratio * lengths.sum() * preferred
                    displacement = np.linalg.norm(candidate.joints - anchor.joints, axis=1).max()
                    new_lengths = np.linalg.norm(np.diff(candidate.joints[ids], axis=0), axis=1)
                    if displacement > p['max_displacement_ratio'] * lengths.sum() or np.max(np.abs(new_lengths / lengths - 1)) > p['max_length_change_ratio']:
                        raise ValueError('Pre-bend exceeds geometry budget')
                coverage = enclosure_evidence(mesh, candidate.joints[ids], config=quality)
                if np.any(coverage < p['min_enclosure_fraction']) or np.any(coverage < baseline - p['max_enclosure_drop']):
                    raise ValueError('Candidate loses surface enclosure evidence')
                status = checker.classify(candidate.joints, candidate.joint_names)
                if any(v['passed'] and not status['joints'][name]['passed'] for name, v in certified['joints'].items()):
                    raise ValueError('Candidate loses certified containment')
                errors = reprojection_report(candidate, config=rig.params)
                if max(e for views in errors.values() for e in views.values()) > p['max_reprojection_pixels']:
                    raise ValueError('Candidate exceeds reprojection budget')
                proposed = {**poles, key: preferred.tolist()}
                trial = deepcopy(evaluate(deepcopy(candidate), deepcopy(proposed)))
                if not np.isfinite(trial['score']):
                    raise ValueError('Non-finite motion score')
                entry.update(score=float(trial['score']), metrics=trial.get('metrics', {}), enclosure=coverage.tolist())
                if trial['score'] < best['score'] - p['min_improvement']:
                    current, poles, best = candidate, proposed, trial
                    entry['accepted'] = True
            except ValueError as error:
                entry['rejected_reason'] = str(error)
            history.append(entry)
    return current, poles, best, {'initial_score': initial, 'final_score': float(best['score']), 'history': history,
                                 'rest_poles': poles, 'rest_positions_changed': bool(not np.array_equal(current.joints, rig.joints)),
                                 'scope': 'Finite motion-evaluated search; no surface snapping or anatomical guarantee.'}
