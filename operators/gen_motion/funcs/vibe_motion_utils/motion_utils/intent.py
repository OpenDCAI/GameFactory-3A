"""Generate a clip exclusively from an explicit position/rhythm/IK program."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .skeleton_templates import MotionClip
from .task_space import design_root, solve_part
from .units import fk, root_index, set_world_rotation


@dataclass
class MotionResult:
    clip: MotionClip
    targets: dict
    target_joints: dict
    contacts: dict
    contact_ids: dict
    residuals: dict
    diagnostics: dict
    timing: dict
    notes: list


def generate_motion(plan):
    rhythm = plan.rhythm
    times = rhythm.sample()
    clip = MotionClip.rest_clip(plan.template, len(times), fps=rhythm.fps)
    root = design_root(plan, times)
    clip.trans[:] = root['position'] - plan.template.rest[root_index(plan.template)]
    set_world_rotation(clip, root_index(plan.template), root['rotation'])
    targets, target_joints, contacts, contact_ids, diagnostics = {}, {}, {}, {}, {}
    for key, spec in plan.parts.items():
        tracks, masks, report = solve_part(clip, plan, spec, times, root)
        for joint, target in tracks.items():
            label = key if joint == spec['joints'][-1] else f'{key}:{plan.template.joint_names[joint]}'
            if label in targets:
                raise ValueError('Target labels collide; rename the body parts')
            targets[label], target_joints[label] = target, joint
            if joint in masks:
                contact_ids[label] = masks[joint]
                contacts[label] = masks[joint] >= 0
        if report:
            diagnostics[key] = report
    for frame in range(1, clip.num_frames):
        flip = np.sum(clip.quats[frame] * clip.quats[frame - 1], axis=-1) < 0
        clip.quats[frame, flip] *= -1
    positions, _ = fk(clip)
    residuals = {key: np.linalg.norm(positions[:, target_joints[key]] - target, axis=-1)
                 for key, target in targets.items()}
    if not np.isfinite(positions).all():
        raise ValueError('The trajectory program produced non-finite joint positions')
    notes = ['Kinematic synthesis preserves bone lengths; it does not simulate balance or mesh collisions.']
    if any(np.max(v) > plan.program['quality']['target_tolerance'] for v in residuals.values()):
        notes.append('Some authored positions are unreachable under the supplied IK limits; inspect residuals.')
    timing = {'duration': rhythm.duration, 'fps': rhythm.fps,
              'events_seconds': {key: value * rhythm.duration for key, value in rhythm.events.items()}}
    return MotionResult(clip, targets, target_joints, contacts, contact_ids, residuals, diagnostics, timing, notes)
