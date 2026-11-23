"""Category-validated part programs -> task targets -> IK/FK -> springs -> optional skin feedback."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any
import numpy as np
from scipy.ndimage import gaussian_filter1d

from .arm_constraints import DOF_NAMES, solve_arm
from .coordination import apply_body_rotations, apply_translations, apply_world_translations, apply_wrist, split_rotation_parts
from .dynamics import apply_springs
from .kinematics import set_root_position, set_world_rotation, solve_bone, solve_chain
from .mesh_checks import body_envelope, bone_capsules, clearance_feedback, collision_feedback, deformation_metrics
from .program import sample_curve
from .root_motion import MotionStyle, design_root
from .skeleton import partition_skeleton
from .targets import infer_target_positions
from .timing import Rhythm


def _ground(rig):
    vertices = getattr(rig, 'rest_vertices', None)
    if vertices is not None and len(vertices):
        return float(np.asarray(vertices)[:, 1].min())
    return float(np.asarray(rig.rest_world)[list(rig.joints), 1, 3].min())


def smooth_rotations(quats, joints, sigma, despike=None):
    """temporal filtering of local rotations: optional despike (single-frame jumps larger than despike degrees that
    return next frame are replaced by the neighbour slerp), then Gaussian smoothing of the hemisphere-aligned
    quaternions (sigma in frames) and renormalisation."""
    for j in joints:
        q = quats[:, j].copy()
        for i in range(1, len(q)):
            if np.dot(q[i], q[i-1]) < 0:
                q[i] = -q[i]
        if despike:
            limit = np.deg2rad(despike)
            for i in range(1, len(q)-1):
                step = 2*np.arccos(min(1., abs(float(np.dot(q[i], q[i-1])))))
                skip = 2*np.arccos(min(1., abs(float(np.dot(q[i+1], q[i-1])))))
                if step > limit and skip < limit:
                    m = q[i-1]+q[i+1]
                    q[i] = m/max(np.linalg.norm(m), 1e-12)
        if sigma > 0 and len(q) > 2:
            q = gaussian_filter1d(q, sigma, axis=0, mode='nearest')
        quats[:, j] = q/np.maximum(np.linalg.norm(q, axis=-1, keepdims=True), 1e-12)


def compute_node_positions(rig, blocks, rhythm, style, times, *, clearance=None, foot_lift=None, root_lift=None,
                           ground=None, target_offset=None, capsules=None) -> dict[str, Any]:
    times = np.atleast_1d(np.asarray(times, float))
    if times.ndim != 1 or not len(times) or not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
        raise ValueError('Expected one time or finite increasing sample times')
    if times[0] < 0 or times[-1] > rhythm.duration+1e-8:
        raise ValueError('Sample times outside action duration')
    style.program.validate(rhythm, style, blocks)
    count = len(times)
    quats = np.tile(np.asarray(rig.local_q, float), (count, 1, 1))
    translations = np.tile(np.asarray(rig.local_t, float), (count, 1, 1))
    root = design_root(blocks, rhythm, style, times)
    if root_lift is not None:
        root['position'] = root['position'].copy()
        root['position'][:, 1] += np.interp(times, root_lift[0], root_lift[1])
    if style.program.select('translate'):
        apply_translations(rig, blocks, rhythm, style, times, translations)
    set_root_position(rig, quats, translations, blocks.root, root['position'])
    set_world_rotation(rig, quats, translations, blocks.root, root['body_delta'] @ rig.rest_world[blocks.root, :3, :3])
    pre, post = split_rotation_parts(rig, blocks, style)
    apply_body_rotations(rig, blocks, rhythm, style, times, root, quats, translations, keys=pre)
    smoothing = style.program.refine.get('smooth')
    if smoothing:
        early = [k for k in smoothing['parts'] if k in pre]
        late = [k for k in smoothing['parts'] if k not in pre]
        sigma, despike = float(smoothing.get('sigma_frames', 1.5)), smoothing.get('despike_degrees')
        if early:
            smooth_rotations(quats, [j for k in early for j in blocks.parts[k].joints], sigma, despike)
    world = rig.fk(quats, translations)
    tracks = infer_target_positions(rig, blocks, rhythm, style, times, root, world, clearance,
                                    ground=_ground(rig) if ground is None else ground)
    residuals, flexion, projection, arm_constraints = {}, {}, {}, {}
    depth = {}
    for key in tracks.feet:
        j, d = blocks.parts[key].joints[0], 0
        while rig.parents[j] >= 0:
            j, d = int(rig.parents[j]), d+1
        depth[key] = d
    for key in sorted(tracks.feet, key=lambda k: (k in tracks.effectors, depth[k] if k in tracks.effectors else 0)):
        target = tracks.feet[key]
        js = blocks.parts[key].joints
        p = style.program.parts[key]['params']
        target = target.copy()
        target[:, 1] += (foot_lift or {}).get(key, 0.)
        if target_offset and key in target_offset:
            target += target_offset[key]
        tracks.feet[key] = target
        rest_pole = blocks.frame @ np.asarray(p['rest_pole'], float) if 'rest_pole' in p else None
        if key in tracks.effectors:
            info = tracks.effectors[key]
            chain, end_rotation = info['ik'], tracks.foot_rotations[key]
            if info['tip']:
                if end_rotation is None:
                    end_rotation = rig.fk(quats, translations)[:, chain[-1], :3, :3]
                target = target-np.einsum('tij,j->ti', end_rotation, rig.local_t[js[-1]])
            solved = solve_chain(rig, quats, translations, chain, target, tracks.poles[key], p['flexion'],
                                 bend_weights=p.get('bend_weights'))
            if end_rotation is not None:
                set_world_rotation(rig, quats, translations, chain[-1], end_rotation)
            flexion[key] = solved['bend_degrees']
        else:
            if len(js) == 4:
                target = target-np.einsum('tij,j->ti', tracks.foot_rotations[key], rig.local_t[js[3]])
            solved = solve_bone(rig, quats, translations, js[:3], target, tracks.poles[key], p['flexion'], rest_pole=rest_pole)
            set_world_rotation(rig, quats, translations, js[2], tracks.foot_rotations[key])
            flexion[key] = solved['flex_degrees']
        residuals[key], projection[key] = solved['residual'], solved['projected_distance']
    for key, target in tracks.hands.items():
        part = blocks.parts[key]
        p = style.program.parts[key]['params']
        swivel = sample_curve(p['swivel'], rhythm, style, times) if 'swivel' in p else p['preferred_swivel_degrees']
        solved = solve_arm(rig, quats, translations, part.joints, target, tracks.chest_frames[key],
                           part.side, p['limits'], swivel)
        residuals[key], flexion[key], projection[key] = solved['residual'], solved['flex_degrees'], solved['workspace_correction']
        arm_constraints[key] = solved
        if 'wrist' in p:
            apply_wrist(rig, blocks, rhythm, style, times, root, quats, translations, key)
    if post:
        apply_body_rotations(rig, blocks, rhythm, style, times, root, quats, translations, keys=post)
    if any(spec['params'].get('space', 'parent') != 'parent' or 'orientation' in spec['params']
           for spec in style.program.select('translate').values()):
        apply_world_translations(rig, blocks, rhythm, style, times, root, quats, translations)
    springs = style.program.select('spring')
    if springs:
        need = sorted({k for spec in springs.values() for k in spec['params'].get('collide', [])})
        if need and (capsules is None or any(k not in capsules for k in need)):
            capsules = {**(capsules or {}), **bone_capsules(rig, blocks, need)}
        apply_springs(rig, blocks, style, times, root, quats, translations,
                      ground=_ground(rig) if ground is None else ground, capsules=capsules)
    if smoothing and late:
        smooth_rotations(quats, [j for k in late for j in blocks.parts[k].joints], sigma, despike)
    for i in range(1, count):
        negative = np.sum(quats[i]*quats[i-1], axis=-1) < 0
        quats[i, negative] *= -1
    world = rig.fk(quats, translations)
    root['airborne'] = ~np.any(list(tracks.contacts.values()), axis=0) if tracks.contacts else np.ones(count, bool)
    return {'times': times, 'quats': quats, 'translations': translations, 'world': world,
            'positions': world[:, :, :3, 3], 'root': root, 'tracks': tracks,
            'residuals': residuals, 'flexion': flexion, 'projection': projection, 'arm_constraints': arm_constraints}


def motion_metrics(rig, blocks, result) -> dict[str, Any]:
    pos, times, tracks = result['positions'], result['times'], result['tracks']
    bone_error = 0.
    for j in rig.joints:
        parent = rig.parents[j]
        if parent in rig.joints:
            expected = np.linalg.norm(result['translations'][:, j], axis=-1)
            bone_error = max(bone_error, float(np.max(np.abs(np.linalg.norm(pos[:, j]-pos[:, parent], axis=-1)-expected))))
    foot_error, sliding = {}, {}
    for key, target in tracks.feet.items():
        actual = pos[:, blocks.parts[key].joints[-1]]
        foot_error[key] = float(np.linalg.norm(actual-target, axis=-1).max())
        both = tracks.contacts[key][1:] & tracks.contacts[key][:-1]
        speed = np.linalg.norm(np.diff(actual, axis=0), axis=-1)/np.diff(times)
        sliding[key] = float(speed[both].max()) if both.any() else 0.
    dots = np.abs(np.sum(result['quats'][1:]*result['quats'][:-1], axis=-1))
    angles = 2*np.arccos(np.clip(dots, 0., 1.))
    return {'bone_length_max_error': bone_error, 'foot_target_max_error': foot_error,
            'contact_foot_max_speed': sliding, 'controlled_contact_parts': list(tracks.feet),
            'ik_max_residual': {k: float(v.max()) for k, v in result['residuals'].items()},
            'flexion_range_degrees': {k: [float(v.min()), float(v.max())] for k, v in result['flexion'].items()},
            'arm_dof_ranges_degrees': {k: {name: [float(c['dofs_degrees'][:, i].min()), float(c['dofs_degrees'][:, i].max())]
                                          for i, name in enumerate(DOF_NAMES)} for k, c in result['arm_constraints'].items()},
            'arm_limit_max_violation_degrees': max((float(c['limit_violation_degrees'].max()) for c in result['arm_constraints'].values()), default=0.),
            'arm_solver_failed_samples': sum(int((~c['solver_success']).sum()) for c in result['arm_constraints'].values()),
            'max_joint_step_degrees': float(np.rad2deg(angles.max())) if len(angles) else 0.,
            'max_joint_speed_degrees_per_second': float(np.max(np.rad2deg(angles)/np.diff(times)[:, None])) if len(angles) else 0.,
            'root_endpoint_error': float(np.linalg.norm(pos[-1, blocks.root]-result['root']['position'][-1])),
            'joint_min_y': float(pos[:, rig.joints, 1].min()),
            'finite': bool(all(np.isfinite(result[k]).all() for k in ('quats', 'translations', 'positions')))}


def _body_ground_update(spec, rhythm, times, vertices, ground, leg_length, lift):
    """Root-height correction (world units, per frame) from the lowest skinned vertex: inside 'touch' windows the
    lowest point is moved onto the ground (+margin); elsewhere only penetration is removed (if 'lift')."""
    low = vertices[:, :, 1].min(axis=1)-ground
    margin = float(spec.get('margin_ratio', 0.))*leg_length
    touch = rhythm.contact_mask(times, spec.get('touch', [])) if spec.get('touch') else np.zeros(len(times), bool)
    delta = np.where(touch, margin-low, np.maximum(margin-low, 0.) if spec.get('lift', True) else 0.)
    sigma = float(spec.get('smooth_frames', 1.5))
    if sigma > 0:
        delta = gaussian_filter1d(delta, sigma, mode='nearest')
    return lift+delta, float(np.abs(delta).max())


def generate_motion(rig, *, rhythm: Rhythm, style: MotionStyle, mapping=None):
    style.program.validate(rhythm, style)
    refinement = style.program.refine
    names = [node.get('name', f'node_{i}') for i, node in enumerate(rig.nodes)]
    blocks = partition_skeleton(rig.parents, rig.rest_world, rig.joints, names,
                                style.program.binding() if mapping is None else mapping)
    style.program.validate(rhythm, style, blocks)
    skinned = callable(getattr(rig, 'skin', None))
    envelope: Any = body_envelope(rig, blocks) if skinned else None
    ground = _ground(rig)
    times = rhythm.sample()
    clearance = {key: np.zeros((len(times), 3)) for key in style.program.select('arm_arc')}
    foot_lift = {}
    collision = refinement.get('collision')
    limb_keys = [k for k, part in blocks.parts.items() if part.category in ('upper_limb', 'lower_limb')]
    obstacle_keys = [k for k, part in blocks.parts.items() if part.category in ('trunk', 'head')]
    if collision is not None:
        obstacle_keys_c = list(collision.get('obstacles', obstacle_keys))
    capsule_keys = sorted(set(limb_keys+obstacle_keys+(list(collision['parts'])+obstacle_keys_c if collision else [])))
    capsules = bone_capsules(rig, blocks, capsule_keys) if skinned else None
    target_offset = {key: np.zeros((len(times), 3)) for key in (collision or {}).get('parts', [])
                     if style.program.parts[key]['operator'] == 'effector'}
    body_ground = refinement.get('body_ground') if skinned else None
    lift = np.zeros(len(times))
    history, selected, best_score = [], None, float('inf')
    iterations = refinement['iterations'] if skinned else 0
    for iteration in range(iterations+1):
        result = compute_node_positions(rig, blocks, rhythm, style, times, clearance=clearance, foot_lift=foot_lift,
                                        root_lift=(times, lift) if body_ground is not None else None, ground=ground,
                                        target_offset=target_offset, capsules=capsules)
        metrics = motion_metrics(rig, blocks, result)
        geometry: dict[str, Any]
        if skinned:
            vertices = rig.skin(result['world'])
            correction, geometry = clearance_feedback(rig, blocks, result['root'], result['world'], vertices, envelope,
                                                       arm_keys=list(result['tracks'].hands))
            result['vertices'] = vertices
        else:
            correction, geometry = {}, {'ground_penetration': max(0., -metrics['joint_min_y']),
                                         'forearm_envelope_violation': {}}
        residual = max(metrics['ik_max_residual'].values(), default=0.)
        score = max(geometry['forearm_envelope_violation'].values(), default=0.)+refinement['residual_weight']*residual/blocks.leg_length
        score += refinement['ground_weight']*geometry['ground_penetration']/blocks.leg_length
        pushes = {}
        if skinned:
            _, depth = collision_feedback(rig, blocks, result['world'], result['tracks'].contacts, capsules, limb_keys,
                                          obstacle_keys, 0.)
            geometry['capsule_penetration_ratio'] = {k: v/blocks.leg_length for k, v in depth.items()}
            if collision is not None:
                margin = float(collision.get('margin', .02))*blocks.leg_length
                pushes, cdepth = collision_feedback(rig, blocks, result['world'], result['tracks'].contacts, capsules,
                                                    collision['parts'], obstacle_keys_c, margin)
                geometry['collision_depth_ratio'] = {k: v/blocks.leg_length for k, v in cdepth.items()}
                score += max(cdepth.values(), default=0.)/blocks.leg_length
        touch_error = 0.
        if body_ground is not None and body_ground.get('touch'):
            mask = rhythm.contact_mask(times, body_ground['touch'])
            if mask.any():
                touch_error = float(np.abs(vertices[mask, :, 1].min(axis=1)-ground).max())
                score += refinement['ground_weight']*touch_error/blocks.leg_length
        accepted = score < best_score-1e-9
        record = {'iteration': iteration, 'accepted': accepted, 'score': score,
                  'geometry': geometry, 'ik_max_residual': metrics['ik_max_residual'], 'foot_lift': foot_lift.copy()}
        if body_ground is not None:
            record['body_ground'] = {'max_root_lift': float(np.abs(lift).max()), 'touch_error': touch_error}
        history.append(record)
        if not accepted:
            break
        best_score, selected = score, (result, metrics, geometry, iteration)
        if iteration == iterations:
            break
        for key in clearance:
            clearance[key] += correction[key]
        if collision is not None:
            gain = float(collision.get('gain', .8))
            cap = float(collision.get('max_push', .6))*blocks.leg_length
            for key, push in pushes.items():
                step = gaussian_filter1d(gain*push, 1.5, axis=0, mode='nearest')
                if key in target_offset:
                    total = target_offset[key]+step
                    if key in result['tracks'].contacts:
                        total[result['tracks'].contacts[key]] = 0.
                    target_offset[key] = total
                    norm = np.linalg.norm(target_offset[key], axis=-1, keepdims=True)
                    target_offset[key] *= np.minimum(1., cap/np.maximum(norm, 1e-12))
                elif key in clearance:
                    frame = result['tracks'].chest_frames[key]
                    clearance[key] += np.einsum('tji,tj->ti', frame, step)
                    norm = np.linalg.norm(clearance[key], axis=-1, keepdims=True)
                    clearance[key] *= np.minimum(1., cap/np.maximum(norm, 1e-12))
        for key in result['tracks'].feet:
            info = result['tracks'].effectors.get(key)
            if info is not None and not info['plants']:
                continue
            ids = envelope['regions']['parts'][key]['tip']
            if len(ids):
                minimum = float(result['vertices'][:, ids, 1].min())
                foot_lift[key] = foot_lift.get(key, 0.)+max(0., envelope['ground']-minimum)+refinement['foot_margin_ratio']*blocks.leg_length
        if body_ground is not None:
            lift, _ = _body_ground_update(body_ground, rhythm, times, result['vertices'], ground, blocks.leg_length, lift)
    if selected is None:
        raise ValueError('No finite motion refinement candidate')
    result, metrics, geometry, chosen = selected
    if skinned:
        metrics.update(deformation_metrics(rig, result['vertices']))
    metrics.update(geometry)
    warnings = ['Kinematic animation, not a dynamics/balance simulation.',
                'Joint ranges are explicit procedural priors, not anatomy inferred from skin weights.',
                'Mesh envelopes do not certify triangle collision-free motion or inter-frame clearance.']
    if not skinned:
        warnings.append('Skeleton-only result: no real mesh/skin collision validation was performed.')
    elif not envelope['body_available']:
        warnings.append('No torso-dominant vertices: arm/body envelope feedback was skipped.')
    if max(metrics['ik_max_residual'].values(), default=0.) > .005*blocks.leg_length:
        warnings.append('Some targets were projected by IK; inspect per-part residuals.')
    if metrics['arm_solver_failed_samples']:
        warnings.append('Some bounded arm samples did not converge.')
    style_data = asdict(style)
    program_data = style_data.pop('program')
    report = {'action': style.program.action, 'frames': len(times), 'duration_seconds': float(times[-1]),
              'fps': float(1/np.diff(times).mean()), 'rhythm': asdict(rhythm), 'style': style_data,
              'program': program_data, 'events_seconds': {name: rhythm.seconds(name) for name in rhythm.events},
              'phases_seconds': {name: [rhythm.seconds(a), rhythm.seconds(b)] for name, (a, b) in rhythm.all_phases.items()},
              'blocks': blocks.as_dict(names), 'metrics': metrics, 'refinement': history,
              'selected_iteration': chosen, 'effective_gravity': result['root']['effective_gravity'],
              'skinned': skinned, 'warnings': warnings}
    return result, blocks, report
