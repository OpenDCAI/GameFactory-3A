"""Task-space motion generation with explicit rhythm, body blocks and original-rig IK.

=================  ==================================================================
module              role
=================  ==================================================================
``timing``          Rhythm: events, phases (coarse action states), beats (generated sub-phases), time references
``program``         MotionProgram: curve specs (keys+interp/eases, segments, rate, add, pattern, oscillators incl.
                    noise), typed operators, validation (the JSON schema reference)
``skeleton``        category bindings of joint chains -> BodyBlocks; mesh-free SkeletonRig
``skinned_rig``     MeshRig: SkeletonRig plus a skinned mesh (enables the skin-feedback refinement)
``root_motion``     MotionStyle and root operators (``curves`` with optional pitch/roll/pivot / ``ballistic``)
``coordination``    rotations / bone aiming (any category), pre/post-IK ordering, joint translations and world
                    placements (props, follow), arm wrists, foot orientation, hand polar arcs
``targets``         task-space targets: ``foot_swing`` / ``contact_path`` / ``effector`` (keys, spline keys, plants,
                    gaits, end rotation, planted orientation lock) and hands
``kinematics``      batched analytic / bounded numeric limb IK in the rest-rotation basis
``arm_constraints`` 4-DOF bounded arm IK
``dynamics``        ``spring`` follow-through for attachment chains (gravity, wind, ground, capsule collision)
``mesh_checks``     skin-region envelopes, bone capsules, clearance / collision feedback, deformation metrics
``generate``        compute_node_positions / generate_motion (skin feedback refinement, body_ground, collision,
                    temporal smoothing)
``asset``           exported-animation readback verification
=================  ==================================================================

Operators: root ``curves`` | ``ballistic``; parts ``rotation`` | ``aim`` | ``arm_arc`` | ``foot_swing`` |
``contact_path`` | ``effector`` | ``spring`` | ``translate`` | ``rest``. Categories: head, trunk, upper_limb, lower_limb, tail
(Chinese aliases accepted). Parameter schema: see program.py.
"""
from .generate import compute_node_positions, generate_motion, motion_metrics
from .program import MotionProgram
from .root_motion import MotionStyle
from .skeleton import BodyBlocks, BodyPart, CATEGORIES, SkeletonRig, partition_skeleton
from .skeleton_templates import MotionClip, SkeletonTemplate
from .skinned_rig import MeshRig
from .timing import Rhythm

__all__ = ['BodyBlocks', 'BodyPart', 'CATEGORIES', 'MeshRig', 'MotionClip', 'MotionProgram', 'MotionStyle', 'Rhythm',
           'SkeletonRig', 'SkeletonTemplate', 'compute_node_positions', 'generate_motion', 'motion_metrics',
           'partition_skeleton']
