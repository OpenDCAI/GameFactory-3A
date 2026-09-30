"""Generate position-first motion and optional mesh artifacts from a complete input program."""
from __future__ import annotations

from copy import deepcopy
import json
import numpy as np

from . import motion
from .bvh import clip_to_bvh_bytes
from .motion_utils.timing import fields


def _json(value):
    def convert(item):
        if isinstance(item, np.ndarray):
            return item.tolist()
        if isinstance(item, np.generic):
            return item.item()
        raise TypeError(f'Cannot serialize {type(item).__name__}')
    return json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False, default=convert)


def generate_vibe_motion(*, config, mesh=None):
    """Accept explicit skeleton/rhythm/program data; never select or merge a preset.

    With a mesh, rig_quality and export.text are required. skeleton=null also
    requires rigging. Supplying skinning opts into skin weights and GLB export
    and requires skin_quality and export.glb. No mesh settings have defaults.
    """
    fields(config, ('skeleton', 'rhythm', 'program'),
           optional=('description', 'units', 'rigging', 'skinning', 'rig_quality', 'skin_quality', 'export', 'skin_constraints'), label='config')
    config = deepcopy(config)
    mesh_keys = {'rigging', 'skinning', 'rig_quality', 'skin_quality', 'export', 'skin_constraints'}
    if mesh is None and mesh_keys.intersection(config):
        raise ValueError('Rigging, skinning, mesh quality and export settings require a mesh')
    if mesh is not None:
        if not {'rig_quality', 'export'} <= config.keys():
            raise ValueError('A mesh requires explicit rig_quality and export settings')
        fields(config['export'], ('text', 'glb') if 'skinning' in config else ('text',), label='export')
        fields(config['export']['text'], ('precision', 'sum_tolerance', 'max_influences'), label='export.text')
    if ('skinning' in config) != ('skin_quality' in config):
        raise ValueError('skinning and skin_quality must be supplied together')
    if 'skin_constraints' in config and 'skinning' not in config:
        raise ValueError('skin_constraints requires skinning configuration')
    rig = None
    if config['skeleton'] is None:
        if mesh is None or 'rigging' not in config:
            raise ValueError('An explicit skeleton or a mesh with rigging config is required')
        from .skeleton import fit_skeleton
        rig = fit_skeleton(mesh, config=config['rigging'])
        config['skeleton'] = {'name': rig.name, 'names': list(rig.joint_names),
                              'parents': rig.parents.tolist(), 'rest': rig.joints.tolist()}
    elif 'rigging' in config:
        raise ValueError('Provide either an explicit skeleton or rigging, not both')
    plan = motion.build_plan(config['skeleton'], rhythm=config['rhythm'], program=config['program'])
    result = motion.generate_clip(plan)
    metrics = motion.clip_metrics(result, plan)
    artifacts = {'bvh_bytes': clip_to_bvh_bytes(result.clip), 'fps': float(result.clip.fps),
                 'joints': motion.joint_positions(result.clip), 'clip': result.clip,
                 'targets': result.targets, 'target_joints': result.target_joints,
                 'contacts': result.contacts, 'contact_ids': result.contact_ids,
                 'diagnostics': result.diagnostics,
                 'metrics': metrics, 'residuals': result.residuals,
                 'residual_summary': motion.residual_summary(result),
                 'action': plan.program['action'], 'frames': result.clip.num_frames,
                 'notes': list(result.notes)}
    if mesh is not None:
        from . import skeleton, skinning
        from .rigging_utils.types import RigResult
        if rig is None:
            template = plan.template
            rig = RigResult(name=template.name, joints=template.rest.copy(), parents=template.parents.copy(),
                            joint_names=list(template.joint_names), chains=deepcopy(template.roles))
        weights = None
        if 'skinning' in config:
            constraints = config.get('skin_constraints', {})
            fields(constraints, (), optional=('allowed_bones', 'weight_bias', 'anchors'), label='skin_constraints')
            weights = skinning.skin_mesh(mesh, rig, config=config['skinning'], **constraints)
            report = skinning.validate_skin(weights, mesh, rig, config=config['skin_quality'])
            artifacts['skin_report_json'] = _json({
                'parameters': config['skinning'], 'passed': skinning.report_passed(report),
                'stats': skinning.weight_stats(weights),
                'findings': [{'check': f.check, 'ok': bool(f.ok), 'value': f.value, 'detail': f.detail}
                             for f in report.findings]})
            from .rigging_utils.export import animated_glb
            artifacts['animated_glb_bytes'] = animated_glb(mesh, rig, weights, result.clip, config=config['export']['glb'])
        text = config['export']['text']
        artifacts.update(rig_text=skeleton.rig_to_text(rig, skin=weights, **text),
                         skeleton_text=skeleton.skeleton_to_text(rig, precision=text['precision']),
                         mesh_obj_bytes=skeleton.mesh_to_obj(mesh, precision=text['precision']).encode('utf-8'),
                         rig_report_json=_json(skeleton.evaluate_skeleton(mesh, rig, config=config['rig_quality'])))
        artifacts['notes'].extend(rig.notes)
    artifacts['vibe_report_json'] = _json({
        'action': artifacts['action'], 'strategy': 'position_trajectories_rhythm_ik',
        'frames': artifacts['frames'], 'fps': artifacts['fps'], 'timing': result.timing,
        'skeleton': config['skeleton'], 'program': config['program'],
        'targets': result.targets, 'target_joints': result.target_joints,
        'contacts': result.contacts, 'contact_ids': result.contact_ids,
        'metrics': metrics, 'residuals': artifacts['residuals'],
        'residual_summary': artifacts['residual_summary'],
        'diagnostics': result.diagnostics, 'notes': artifacts['notes']})
    return artifacts


__all__ = ['generate_vibe_motion']
