"""Generate motion and optional mesh artifacts from a skeleton and a rhythm / style / program description."""
from __future__ import annotations

from copy import deepcopy
import json
import numpy as np

from . import motion
from .bvh import clip_to_bvh_bytes

REQUIRED = ('skeleton', 'rhythm', 'style', 'program')
OPTIONAL = ('name', 'description', 'video', 'rigging', 'skinning', 'rig_quality', 'skin_quality', 'export',
            'skin_constraints')
MESH_KEYS = {'rigging', 'skinning', 'rig_quality', 'skin_quality', 'export', 'skin_constraints'}


def _json(value):
    def convert(item):
        if isinstance(item, np.ndarray):
            return item.tolist()
        if isinstance(item, np.generic):
            return item.item()
        raise TypeError(f'Cannot serialize {type(item).__name__}')
    return json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False, default=convert)


def _keys(value, required, optional, label):
    if not isinstance(value, dict) or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        raise ValueError(f'{label}: required {sorted(required)}, optional {sorted(optional)}')


def normalize_config(config):
    """Accept the motion spec directly or wrapped as {base?, motion: {...}, skeleton?}; ``base`` is only a label."""
    if isinstance(config, dict) and isinstance(config.get('motion'), dict):
        inner = {key: value for key, value in config.items() if key not in ('motion', 'base')}
        inner.update(config['motion'])
        return inner
    return config


def _chains(skeleton, program):
    names = list(skeleton['names'])
    chains = {}
    for key, spec in program['parts'].items():
        chains[key] = [names.index(j) if isinstance(j, str) else int(j) for j in spec['joints']]
    return chains


def generate_vibe_motion(*, config, mesh=None):
    """Generate a clip (BVH, joint positions, report) and, with a mesh, rig / skin / GLB / OBJ artifacts.

    The program follows ``motion_utils/program.py`` (rhythm, style, program, refine). With a mesh, ``rig_quality`` and
    ``export.text`` are required; ``skeleton=null`` also requires ``rigging``. Supplying ``skinning`` opts into skin
    weights, the skin-feedback refinement of the motion and the animated GLB, and requires ``skin_quality`` and
    ``export.glb``. ``video`` (render options) is accepted and ignored.
    """
    config = deepcopy(normalize_config(config))
    _keys(config, REQUIRED, OPTIONAL, 'config')
    if mesh is None and MESH_KEYS.intersection(config):
        raise ValueError('Rigging, skinning, mesh quality and export settings require a mesh')
    if mesh is not None:
        if not {'rig_quality', 'export'} <= config.keys():
            raise ValueError('A mesh requires rig_quality and export settings')
        _keys(config['export'], ('text', 'glb') if 'skinning' in config else ('text',), (), 'export')
        _keys(config['export']['text'], ('precision', 'sum_tolerance', 'max_influences'), (), 'export.text')
    if ('skinning' in config) != ('skin_quality' in config):
        raise ValueError('skinning and skin_quality must be supplied together')
    if 'skin_constraints' in config and 'skinning' not in config:
        raise ValueError('skin_constraints requires skinning configuration')
    rig = None
    if config['skeleton'] is None:
        if mesh is None or 'rigging' not in config:
            raise ValueError('A skeleton or a mesh with rigging config is required')
        from .skeleton import fit_skeleton
        rig = fit_skeleton(mesh, config=config['rigging'])
        config['skeleton'] = {'name': rig.name, 'names': list(rig.joint_names),
                              'parents': rig.parents.tolist(), 'rest': rig.joints.tolist()}
    elif 'rigging' in config:
        raise ValueError('Provide either a skeleton or rigging, not both')
    skeleton, program = config['skeleton'], config['program']
    weights, artifacts = None, {}
    if mesh is not None:
        from . import skeleton as rigging, skinning
        from .rigging_utils.types import RigResult
        if rig is None:
            rig = RigResult(name=skeleton['name'], joints=np.asarray(skeleton['rest'], float),
                            parents=np.asarray(skeleton['parents']), joint_names=list(skeleton['names']),
                            chains=_chains(skeleton, program))
        if 'skinning' in config:
            constraints = config.get('skin_constraints', {})
            _keys(constraints, (), ('allowed_bones', 'weight_bias', 'anchors'), 'skin_constraints')
            weights = skinning.skin_mesh(mesh, rig, config=config['skinning'], **constraints)
            report = skinning.validate_skin(weights, mesh, rig, config=config['skin_quality'])
            artifacts['skin_report_json'] = _json({
                'parameters': config['skinning'], 'passed': skinning.report_passed(report),
                'stats': skinning.weight_stats(weights),
                'findings': [{'check': f.check, 'ok': bool(f.ok), 'value': f.value, 'detail': f.detail}
                             for f in report.findings]})
    result = motion.generate_clip(skeleton, rhythm=config['rhythm'], style=config['style'], program=program,
                                  mesh=mesh if weights is not None else None, skin=weights)
    clip, report = result.clip, result.report
    extent = max(float(np.ptp(np.asarray(skeleton['rest'], float), axis=0).max()), np.finfo(float).eps)
    metrics = report['metrics']
    metrics['failures'] = ([] if metrics['finite'] else ['non_finite_motion']) + (
        ['bone_length'] if metrics['bone_length_max_error'] > 1e-6 * extent else [])
    positions = result.result['positions']
    artifacts.update(bvh_bytes=clip_to_bvh_bytes(clip), fps=float(clip.fps), joints=positions, clip=clip,
                     metrics=report['metrics'], warnings=list(report['warnings']), action=report['action'],
                     frames=clip.num_frames, notes=list(report['warnings']), motion=result)
    if mesh is not None:
        text = config['export']['text']
        if weights is not None:
            from .rigging_utils.export import animated_glb
            from .motion_utils.asset import verify_export
            glb = animated_glb(mesh, rig, weights, clip, config=config['export']['glb'])
            artifacts['animated_glb_bytes'] = glb
            report['export'] = verify_export(glb, clip, mesh, weights, rig, position_tolerance=1e-5 * max(
                1., float(np.ptp(np.asarray(skeleton['rest'], float), axis=0).max())), time_tolerance=1e-6)
        artifacts.update(rig_text=rigging.rig_to_text(rig, skin=weights, **text),
                         skeleton_text=rigging.skeleton_to_text(rig, precision=text['precision']),
                         mesh_obj_bytes=rigging.mesh_to_obj(mesh, precision=text['precision']).encode('utf-8'),
                         rig_report_json=_json(rigging.evaluate_skeleton(mesh, rig, config=config['rig_quality'])))
        artifacts['notes'].extend(rig.notes)
    report.update(name=config.get('name', ''), description=config.get('description', ''), skeleton=skeleton,
                  skinned=weights is not None)
    artifacts['vibe_report_json'] = _json(report)
    return artifacts


__all__ = ['generate_vibe_motion', 'normalize_config']
