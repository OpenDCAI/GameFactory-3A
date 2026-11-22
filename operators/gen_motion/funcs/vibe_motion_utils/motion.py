"""Public motion generation: skeleton (+ optional skinned mesh) and a rhythm / style / program -> ``MotionClip``."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

import numpy as np

from .motion_utils import MeshRig, MotionClip, MotionProgram, MotionStyle, Rhythm, SkeletonRig, SkeletonTemplate
from .motion_utils.generate import generate_motion
from .motion_utils.units import fk


@dataclass
class MotionResult:
    clip: MotionClip
    result: dict
    blocks: object
    report: dict
    rhythm: Rhythm
    style: MotionStyle


def build_rig(skeleton, *, mesh=None, skin=None):
    """Mesh-free ``SkeletonRig`` from ``skeleton`` {name, names, parents, rest}; ``MeshRig`` when a mesh is given."""
    names = skeleton.get('names')
    if (not isinstance(names, list) or not names or len(set(names)) != len(names)
            or any(not isinstance(n, str) or not n or any(c.isspace() or c in '{}' for c in n) for n in names)):
        raise ValueError('skeleton.names must be unique, nonempty BVH-safe strings')
    if mesh is None:
        return SkeletonRig(skeleton['parents'], skeleton['rest'], names)
    if skin is None:
        raise ValueError('A skinned mesh rig needs skin weights')
    return MeshRig(skeleton['parents'], skeleton['rest'], names, mesh, skin)


def build_motion(*, rhythm, style, program):
    """(Rhythm, MotionStyle) from plain dictionaries (the JSON ``rhythm`` / ``style`` / ``program`` objects)."""
    clock = Rhythm(**deepcopy(rhythm))
    return clock, MotionStyle(**deepcopy(style), program=MotionProgram(**deepcopy(program)))


def generate_clip(skeleton, *, rhythm, style, program, mesh=None, skin=None):
    """Run the reference generator and wrap the result as a wxyz ``MotionClip`` (root translation = world offset)."""
    rig = build_rig(skeleton, mesh=mesh, skin=skin)
    clock, motion_style = build_motion(rhythm=rhythm, style=style, program=program)
    result, blocks, report = generate_motion(rig, rhythm=clock, style=motion_style)
    template = SkeletonTemplate(skeleton['name'], list(skeleton['names']), np.asarray(skeleton['parents']),
                                np.asarray(skeleton['rest'], float),
                                {key: list(part.joints) for key, part in blocks.parts.items()})
    root = blocks.root
    clip = MotionClip(template, result['quats'][..., [3, 0, 1, 2]].copy(),
                      result['translations'][:, root] - template.rest[root], float(clock.fps))
    return MotionResult(clip, result, blocks, report, clock, motion_style)


def joint_positions(clip):
    return fk(clip)[0]


__all__ = ['MotionResult', 'build_motion', 'build_rig', 'generate_clip', 'joint_positions']
