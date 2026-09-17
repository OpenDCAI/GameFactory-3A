from __future__ import annotations
import numpy as np
from .generate import smooth
from .units import MotionClip, fk, quat_from_axis_angle, quat_mul, quat_slerp, quat_to_matrix, root_index

def concatenate(
    clips: list[MotionClip],
    *,
    transition_frames=8,
    carry_facing=True,
    align_vertical=True,
) -> MotionClip:
    """Insert transition frames without dropping samples; carry only yaw.

    Set align_vertical=False to retain each clip's ground-relative height.
    The default preserves the historical full root-translation alignment.
    """
    if not clips or type(transition_frames) is not int or transition_frames < 0:
        raise ValueError('clips must be non-empty and transition_frames a non-negative integer')
    tpl, fps = (clips[0].template, clips[0].fps)
    for clip in clips:
        if clip.template is not tpl or not np.isclose(clip.fps, fps):
            raise ValueError('every clip must share the same template and fps')
        fk(clip)
    root = root_index(tpl)
    out = clips[0].copy()
    for source in clips[1:]:
        nxt = source.copy()
        facing = quat_to_matrix(out.quats[-1, root]) @ np.array([0.0, 0.0, 1.0])
        initial = quat_to_matrix(nxt.quats[0, root]) @ np.array([0.0, 0.0, 1.0])
        yaw = np.arctan2(facing[0], facing[2]) - np.arctan2(initial[0], initial[2]) if carry_facing else 0.0
        carry = quat_from_axis_angle(np.array([0.0, 1.0, 0.0]), yaw)
        nxt.trans = (nxt.trans - nxt.trans[0]) @ quat_to_matrix(carry).T + out.trans[-1]
        if not align_vertical:
            nxt.trans[:, 1] = source.trans[:, 1]
        nxt.quats[:, root] = quat_mul(carry, nxt.quats[:, root])
        w = smooth(np.arange(1, transition_frames + 1) / (transition_frames + 1))
        q = quat_slerp(np.broadcast_to(out.quats[-1], (transition_frames, out.num_joints, 4)), np.broadcast_to(nxt.quats[0], (transition_frames, out.num_joints, 4)), w[:, None])
        t = (1 - w[:, None]) * out.trans[-1] + w[:, None] * nxt.trans[0]
        out = MotionClip(tpl, np.concatenate([out.quats, q, nxt.quats]), np.concatenate([out.trans, t, nxt.trans]), fps, out.log + nxt.log)
    return out
