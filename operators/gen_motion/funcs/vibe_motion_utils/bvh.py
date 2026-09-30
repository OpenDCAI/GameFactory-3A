'Serialize motion clips as Y-up BVH with ZXY rotation channels.'
from __future__ import annotations

from typing import Any

import numpy as np


ROOT_CHANNELS = "Xposition Yposition Zposition Zrotation Xrotation Yrotation"


JOINT_CHANNELS = "Zrotation Xrotation Yrotation"


def quats_to_zxy_degrees(quats: np.ndarray) -> np.ndarray:
    'Convert ``(..., 4)`` wxyz quaternions to intrinsic Z-X-Y Euler degrees.'
    q = np.asarray(quats, dtype=np.float64)
    if q.ndim < 1 or q.shape[-1] != 4 or not np.isfinite(q).all():
        raise ValueError(f"quaternions must be finite (...,4), got {q.shape}")
    scale = np.max(np.abs(q), axis=-1, keepdims=True)
    if np.any(scale == 0):
        raise ValueError("cannot convert a zero-length quaternion")
    q = q / scale
    norm = np.linalg.norm(q, axis=-1, keepdims=True)
    w, x, y, z = np.moveaxis(q / norm, -1, 0)


    sin_x = np.clip(2.0 * (w * x + y * z), -1.0, 1.0)
    cos_x = np.hypot(2.0 * (x * y - w * z), 1.0 - 2.0 * (x * x + z * z))
    angle_x = np.arctan2(sin_x, cos_x)
    gimbal = cos_x < 1e-10
    angle_z = np.where(
        gimbal,
        np.arctan2(2.0 * (x * y + w * z), 1.0 - 2.0 * (y * y + z * z)),
        np.arctan2(-2.0 * (x * y - w * z), 1.0 - 2.0 * (x * x + z * z)),
    )
    angle_y = np.where(
        gimbal,
        0.0,
        np.arctan2(-2.0 * (x * z - w * y), 1.0 - 2.0 * (x * x + y * y)),
    )
    return np.degrees(np.stack([angle_z, angle_x, angle_y], axis=-1))


def _joint_names(template: Any, count: int) -> list[str]:
    'Return BVH-safe joint names, generating placeholders when absent.'
    raw = getattr(template, "joint_names", None)
    names = [f"joint_{i}" for i in range(count)] if raw is None else list(raw)
    if len(names) != count:
        raise ValueError("joint_names length must match the skeleton")
    names = ["_".join(str(name).split()) for name in names]
    if any(not name or any(c in name for c in "{}") for name in names) or len(set(names)) != count:
        raise ValueError("joint names must be non-empty, unique BVH tokens")
    return names


def validate_hierarchy(parents: Any, rest: Any) -> tuple[np.ndarray, np.ndarray]:
    'Validate finite rest positions and a single connected integer-indexed tree.'
    parents = np.asarray(parents)
    rest = np.asarray(rest, dtype=np.float64)
    if parents.ndim != 1 or not len(parents) or parents.dtype.kind not in "iu":
        raise ValueError("parents must be a non-empty integer array")
    count = len(parents)
    if rest.shape != (count, 3) or not np.isfinite(rest).all():
        raise ValueError("rest positions must be finite (joints,3)")
    if np.any((parents < -1) | (parents >= count)):
        raise ValueError("parent index outside the skeleton")
    parents = parents.astype(np.int64)
    roots = np.flatnonzero(parents == -1)
    if len(roots) != 1:
        raise ValueError("skeleton needs exactly one root")
    children = _children(parents)
    order = [int(roots[0])]
    for joint in order:
        order.extend(children[joint])
    if len(order) != count:
        raise ValueError("skeleton must be one connected tree without cycles")
    return parents, rest


def _children(parents: np.ndarray) -> list[list[int]]:
    children: list[list[int]] = [[] for _ in parents]
    for joint, parent in enumerate(parents):
        if parent >= 0:
            children[int(parent)].append(joint)
    return children


def clip_to_bvh(clip: Any) -> str:
    'Serialise a ``MotionClip`` to BVH text.'
    template = clip.template
    parents, rest = validate_hierarchy(template.parents, template.rest)
    quats = np.asarray(clip.quats, dtype=np.float64)
    trans = np.asarray(clip.trans, dtype=np.float64)
    count = len(parents)
    if quats.ndim != 3 or not len(quats) or not np.isfinite(quats).all():
        raise ValueError("quaternions must be non-empty finite (frames,joints,4)")
    if not np.isfinite(trans).all():
        raise ValueError("translation must be finite")
    frames = int(quats.shape[0])

    roots = np.flatnonzero(parents == -1)
    if len(roots) != 1:
        raise ValueError(f"BVH needs exactly one root joint, found {len(roots)}")
    if rest.shape != (count, 3):
        raise ValueError(f"rest pose must be ({count},3), got {rest.shape}")
    if quats.shape != (frames, count, 4):
        raise ValueError(
            f"quaternions must be ({frames},{count},4), got {quats.shape}"
        )
    if trans.shape != (frames, 3):
        raise ValueError(f"translation must be ({frames},3), got {trans.shape}")
    fps = float(clip.fps)
    if not np.isfinite(fps) or fps <= 0:
        raise ValueError(f"fps must be finite and positive, got {clip.fps}")

    root = int(roots[0])
    names = _joint_names(template, count)
    children = _children(parents)
    euler = quats_to_zxy_degrees(quats)

    lines: list[str] = ["HIERARCHY"]
    order: list[int] = []

    def emit(joint: int, depth: int) -> None:
        pad = "\t" * depth
        offset = rest[joint] if joint == root else rest[joint] - rest[int(parents[joint])]
        label = "ROOT" if joint == root else "JOINT"
        lines.append(f"{pad}{label} {names[joint]}")
        lines.append(f"{pad}{{")
        lines.append(f"{pad}\tOFFSET {offset[0]:.6f} {offset[1]:.6f} {offset[2]:.6f}")
        channels = ROOT_CHANNELS if joint == root else JOINT_CHANNELS
        channel_count = 6 if joint == root else 3
        lines.append(f"{pad}\tCHANNELS {channel_count} {channels}")
        order.append(joint)
        if children[joint]:
            for child in children[joint]:
                emit(child, depth + 1)
        else:

            lines.append(f"{pad}\tEnd Site")
            lines.append(f"{pad}\t{{")
            lines.append(f"{pad}\t\tOFFSET 0.000000 0.000000 0.000000")
            lines.append(f"{pad}\t}}")
        lines.append(f"{pad}}}")

    emit(root, 0)
    if len(order) != count:
        raise ValueError(
            f"skeleton is not a single tree: reached {len(order)}/{count} joints"
        )

    interval = 1.0 / fps
    if not np.isfinite(interval) or interval <= 0:
        raise ValueError("fps must produce a finite positive frame interval")
    lines += ["MOTION", f"Frames: {frames}", f"Frame Time: {interval:.17g}"]
    for frame in range(frames):
        values: list[str] = []
        for joint in order:
            if joint == root:
                values += [f"{v:.6f}" for v in trans[frame]]
            values += [f"{v:.6f}" for v in euler[frame, joint]]
        lines.append(" ".join(values))
    return "\n".join(lines) + "\n"


def clip_to_bvh_bytes(clip: Any) -> bytes:
    'UTF-8 encoded :func:`clip_to_bvh` output.'
    return clip_to_bvh(clip).encode("utf-8")


__all__ = [
    "JOINT_CHANNELS",
    "ROOT_CHANNELS",
    "clip_to_bvh",
    "clip_to_bvh_bytes",
    "quats_to_zxy_degrees",
]
