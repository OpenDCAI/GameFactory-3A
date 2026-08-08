"""
operators/gen_3d_scene/funcs/build_scene_mesh.py

Assemble a whole WorldMirror prediction into one scene mesh.

`points_to_mesh` fixes continuity *within* a frame. This module fixes it
*across* frames. Upstream's `convert_predictions_to_glb_scene` adds each frame
as its own independent `Trimesh`, so a sixteen-frame flythrough of one room
yields sixteen complete, overlapping copies of that room. They z-fight, they
multiply the triangle count by the number of views, and every slight depth
disagreement between views shows up as a visible double surface.

Here the frames are merged incrementally instead. Frame 0 is meshed in full;
each later frame is reprojected into the frames already accepted and contributes
only the pixels that are genuinely new — the regions disoccluded by camera
motion. The kept region is then dilated by a couple of pixels so new geometry
overlaps the old rather than butting up against it, which is what would leave a
crack along the join.

Finally the result is rotated from OpenCV's y-down world into the y-up frame
glTF expects, so the exported GLB is upright in every engine.

Usage:
    from operators.gen_3d_scene.funcs.build_scene_mesh import build_scene_mesh
    mesh = build_scene_mesh(prediction)
    mesh.export("scene.glb")
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from operators.gen_3d_scene.funcs.points_to_mesh import (
    DEFAULT_CLOSE_HOLES,
    DEFAULT_MAX_OFFPLANE,
    DEFAULT_MAX_TILT,
    DEFAULT_MIN_COMPONENT_FACES,
    _dilate,
    points_to_mesh,
)
from operators.gen_3d_scene.funcs.scene_mask import scene_mask

#: Relative depth agreement below which a reprojected pixel counts as already
#: covered by an earlier frame. Loose enough to absorb per-view depth noise,
#: tight enough that a genuinely disoccluded surface behind an object still
#: reads as new.
DEFAULT_DEDUP_TOLERANCE = 0.05

#: Pixels of overlap kept along the seam between a new frame's contribution and
#: the geometry already accepted. Without it the two meshes meet exactly and any
#: sub-pixel disagreement opens a crack.
DEFAULT_DEDUP_OVERLAP_PX = 2

#: OpenCV world (x right, y down, z forward) → glTF world (x right, y up,
#: z backward). A 180° rotation about x.
_OPENCV_TO_GLTF = np.diag([1.0, -1.0, -1.0, 1.0])


def build_scene_mesh(
    prediction: dict[str, np.ndarray],
    valid: Optional[np.ndarray] = None,
    sky_masks: Optional[np.ndarray] = None,
    max_offplane: float = DEFAULT_MAX_OFFPLANE,
    max_tilt: float = DEFAULT_MAX_TILT,
    close_holes: int = DEFAULT_CLOSE_HOLES,
    min_component_faces: int = DEFAULT_MIN_COMPONENT_FACES,
    dedup: bool = True,
    dedup_tolerance: float = DEFAULT_DEDUP_TOLERANCE,
    dedup_overlap_px: int = DEFAULT_DEDUP_OVERLAP_PX,
    frame_stride: int = 1,
    to_gltf_axes: bool = True,
    return_intermediate: bool = False,
):
    """
    Mesh every frame of a prediction and merge them into one scene.

    Args:
        prediction: A `WorldMirrorModel.infer` result.
        valid: (S, H, W) bool validity per pixel. Computed with `scene_mask`
            when omitted.
        sky_masks: Passed through to `scene_mask` when `valid` is not given.
        max_offplane: Face-continuity threshold — see `points_to_mesh`.
        max_tilt: Normal-agreement threshold — see `points_to_mesh`.
        close_holes: Pinhole-closing radius — see `points_to_mesh`.
        min_component_faces: Floater removal threshold, applied per frame.
        dedup: Contribute only newly visible geometry from each frame after the
            first. Turning this off reproduces upstream's stacked sheets.
        dedup_tolerance: Relative depth agreement for "already covered".
        dedup_overlap_px: Seam overlap in pixels.
        frame_stride: Use every Nth frame. Consecutive video frames overlap
            almost completely, so a stride of 2–4 cuts cost with little loss.
        to_gltf_axes: Rotate the result into glTF's y-up convention.
        return_intermediate: Return a dict of named stages instead of the mesh.

    Returns:
        A single `trimesh.Trimesh`, or — when `return_intermediate` is set — a
        dict with keys `mesh`, `per_frame_faces`, `frames_used` and `valid`.
    """
    import trimesh

    if valid is None:
        valid = scene_mask(prediction, sky_masks=sky_masks)

    points = prediction["points"]
    depth = prediction["depth"]
    colors = prediction["colors"]
    normals = prediction.get("normals")
    poses = prediction.get("camera_poses")
    intrinsics = prediction.get("intrinsics")

    frames = list(range(0, points.shape[0], max(frame_stride, 1)))
    can_dedup = dedup and poses is not None and intrinsics is not None

    meshes = []
    per_frame_faces = []
    accepted: list[int] = []

    for frame in frames:
        contribution = valid[frame]
        if can_dedup and accepted:
            covered = _already_covered(
                points[frame],
                accepted,
                depth,
                valid,
                poses,
                intrinsics,
                dedup_tolerance,
            )
            contribution = contribution & ~covered
            if dedup_overlap_px > 0:
                contribution = _dilate(contribution, dedup_overlap_px) & valid[frame]

        if not contribution.any():
            per_frame_faces.append(0)
            continue

        mesh = points_to_mesh(
            points[frame],
            colors[frame],
            valid=contribution,
            depth=depth[frame],
            normals=normals[frame] if normals is not None else None,
            focal=float(intrinsics[frame][0, 0]) if intrinsics is not None else None,
            max_offplane=max_offplane,
            max_tilt=max_tilt,
            close_holes=close_holes,
            min_component_faces=min_component_faces,
        )
        per_frame_faces.append(len(mesh.faces))
        if len(mesh.faces):
            meshes.append(mesh)
            accepted.append(frame)

    if meshes:
        scene = trimesh.util.concatenate(meshes)
    else:
        scene = trimesh.Trimesh(
            vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64)
        )

    if to_gltf_axes and len(scene.vertices):
        scene.apply_transform(_OPENCV_TO_GLTF)

    if return_intermediate:
        return {
            "mesh": scene,
            "per_frame_faces": per_frame_faces,
            "frames_used": accepted,
            "valid": valid,
        }
    return scene


def _already_covered(
    points: np.ndarray,
    accepted: list[int],
    depth: np.ndarray,
    valid: np.ndarray,
    poses: np.ndarray,
    intrinsics: np.ndarray,
    tolerance: float,
) -> np.ndarray:
    """
    Mark the pixels of a frame whose surface an earlier frame already meshed.

    Each point is transformed into every accepted camera and projected. It counts
    as covered when it lands inside that image, on a valid pixel, at a depth
    matching what the earlier frame recorded there — that is, both views agree
    they are looking at the same surface. Landing in front of the stored depth
    means this frame sees something new that was occluded before, so it is kept.
    """
    height, width = points.shape[:2]
    covered = np.zeros((height, width), dtype=bool)
    flat = points.reshape(-1, 3)

    for other in accepted:
        world_to_camera = np.linalg.inv(poses[other])
        camera_points = flat @ world_to_camera[:3, :3].T + world_to_camera[:3, 3]
        z = camera_points[:, 2]

        in_front = z > 1e-6
        if not in_front.any():
            continue

        intrinsic = intrinsics[other]
        u = np.full(len(flat), -1.0)
        v = np.full(len(flat), -1.0)
        u[in_front] = (
            intrinsic[0, 0] * camera_points[in_front, 0] / z[in_front] + intrinsic[0, 2]
        )
        v[in_front] = (
            intrinsic[1, 1] * camera_points[in_front, 1] / z[in_front] + intrinsic[1, 2]
        )

        column = np.round(u).astype(np.int64)
        row = np.round(v).astype(np.int64)
        inside = (
            in_front
            & (column >= 0)
            & (column < width)
            & (row >= 0)
            & (row < height)
        )
        if not inside.any():
            continue

        stored = depth[other][row[inside], column[inside]]
        agrees = np.abs(z[inside] - stored) <= tolerance * np.maximum(stored, 1e-6)
        agrees &= valid[other][row[inside], column[inside]]

        hit = np.zeros(len(flat), dtype=bool)
        hit[np.flatnonzero(inside)[agrees]] = True
        covered |= hit.reshape(height, width)

    return covered
