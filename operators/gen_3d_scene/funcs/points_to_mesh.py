"""
operators/gen_3d_scene/funcs/points_to_mesh.py

Turn one frame's pointmap into a triangle mesh — the "面片" step.

Every pixel becomes a vertex and neighbouring pixels are stitched into quads, as
upstream also does. What differs is which faces survive.

Upstream (`create_image_mesh` driven by `infer.py::create_filter_mask`) deletes
the least-confident 10% of every frame, dilates its edge masks by 3x3, then keeps
a quad only when all four corners survived. Each step erodes further, so real
surfaces come out perforated — while the stretched triangles those filters were
aiming at largely survive anyway, because the edge masks are combined with `&`
and a pixel is dropped only when it trips both detectors at once.

This module keeps a vertex whenever its geometry is representable (finite,
positive depth) and enforces continuity per face instead:

  * Faces are culled on their own three edges, so the cut lands exactly on the
    occlusion boundary with no halo, and three valid corners still make a
    triangle.
  * An edge is continuous when both ends lie on the tangent plane implied by the
    other's normal. That residual is zero on a plane at any viewing angle, so a
    road receding to the horizon stays solid however steep its depth gradient;
    across an occlusion boundary the far point misses by hundreds of footprints.
    Depth difference alone cannot make that distinction — a grazing surface and
    an occluding edge look identical locally.
  * Faces must also agree with the normals they inherit. The depth head blurs an
    occlusion boundary into a ramp several pixels wide, so every individual step
    passes the edge test; the resulting sheet still betrays itself by lying
    edge-on to the surfaces it connects.
  * Pinholes are closed before meshing, and quads split along their shorter
    diagonal so the split follows slanted surfaces instead of cutting across.

Usage:
    from operators.gen_3d_scene.funcs.points_to_mesh import points_to_mesh
    mesh = points_to_mesh(points, colors, valid=mask, focal=intrinsics[0, 0])
"""
from __future__ import annotations

from typing import Optional

import numpy as np

#: How far, in local pixel footprints, one end of an edge may sit off the tangent
#: plane at the other end. Real curvature and normal noise stay within a
#: footprint or two while occlusion straddles land in the hundreds, so the
#: threshold is not delicate. Used whenever normals are available.
DEFAULT_MAX_OFFPLANE = 8.0

#: Fallback when no normals are available: how far an edge may stretch beyond the
#: local pixel footprint. Cruder — it cannot tell a near-horizon ground plane
#: from an occluding edge, so it eventually cuts the ground near the horizon.
DEFAULT_MAX_STRETCH = 12.0

#: Maximum angle, in degrees, between a face's geometric normal and the normals
#: at its corners. Real surface agrees with its own normals however oblique it is
#: to the camera; a sheet spanning an occlusion boundary does not.
DEFAULT_MAX_TILT = 75.0

#: Radius, in pixels, of the morphological closing that repairs pinholes before
#: meshing. 2 closes speckle and thin cracks while leaving genuine openings
#: (windows, sky gaps) alone.
DEFAULT_CLOSE_HOLES = 2

#: Connected components smaller than this are depth-noise floaters, discarded.
DEFAULT_MIN_COMPONENT_FACES = 64


def points_to_mesh(
    points: np.ndarray,
    colors: np.ndarray,
    valid: Optional[np.ndarray] = None,
    depth: Optional[np.ndarray] = None,
    normals: Optional[np.ndarray] = None,
    focal: Optional[float] = None,
    max_offplane: float = DEFAULT_MAX_OFFPLANE,
    max_stretch: float = DEFAULT_MAX_STRETCH,
    max_tilt: Optional[float] = DEFAULT_MAX_TILT,
    close_holes: int = DEFAULT_CLOSE_HOLES,
    min_component_faces: int = DEFAULT_MIN_COMPONENT_FACES,
    return_intermediate: bool = False,
):
    """
    Mesh a single frame's pointmap.

    Args:
        points: (H, W, 3) float — world-space position per pixel.
        colors: (H, W, 3) uint8 — RGB per pixel.
        valid: (H, W) bool — pixels whose geometry is usable. Non-finite points
            are dropped regardless. Defaults to everything.
        depth: (H, W) float — per-view depth, used for the footprint scale. When
            omitted it is taken as the distance from the points' centroid, which
            is good enough for the ratios both tests need.
        normals: (H, W, 3) float — surface normals in the same frame as `points`.
            Their sign is irrelevant. Supplying them selects the tangent-plane
            test, which is the one that survives grazing angles.
        focal: Focal length in pixels. When omitted it is estimated from the
            median observed pixel footprint, which self-calibrates on any frame
            whose surfaces are mostly continuous.
        max_offplane: Tangent-plane cull threshold, in local pixel footprints.
            Used when `normals` is given. `None` disables the cull entirely.
        max_stretch: Edge-length cull threshold, in local pixel footprints. Used
            only when `normals` is absent.
        max_tilt: Cull a face whose geometric normal sits more than this many
            degrees away from the normals predicted at its corners. Needs
            `normals`. `None` disables it.
        close_holes: Radius of the pinhole-closing pass. 0 disables it.
        min_component_faces: Drop connected components below this face count.
            0 keeps everything.
        return_intermediate: Return a dict of named stages instead of the mesh.

    Returns:
        A `trimesh.Trimesh` with vertex colours, or — when `return_intermediate`
        is set — a dict with keys `mesh`, `valid` (the mask after closing),
        `faces_before_cull` and `faces_after_cull`.
    """
    import trimesh

    height, width = points.shape[:2]
    points = np.ascontiguousarray(points, dtype=np.float64)

    mask = np.ones((height, width), dtype=bool) if valid is None else valid.copy()
    mask &= np.isfinite(points).all(axis=-1)

    if depth is None:
        depth = np.linalg.norm(points - points[mask].mean(axis=0), axis=-1)
    depth = np.asarray(depth, dtype=np.float64)
    mask &= np.isfinite(depth) & (depth > 0)

    if close_holes > 0:
        mask, points, depth = _close_pinholes(mask, points, depth, close_holes)

    faces = _grid_faces(mask, points)
    faces_before = len(faces)

    if max_offplane is not None and len(faces):
        if focal is None:
            focal = _estimate_focal(points, depth, mask)
        footprint = depth.reshape(-1) / max(focal, 1e-6)
        if normals is not None:
            keep = _on_same_surface(faces, points, normals, footprint, max_offplane)
        else:
            keep = _within_stretch(faces, points, footprint, max_stretch)
        faces = faces[keep]

    if max_tilt is not None and normals is not None and len(faces):
        faces = faces[_agrees_with_normals(faces, points, normals, max_tilt)]

    mesh = _build_trimesh(faces, points, colors, trimesh)

    if min_component_faces > 0 and len(mesh.faces):
        mesh = _drop_small_components(mesh, min_component_faces, trimesh)

    if return_intermediate:
        return {
            "mesh": mesh,
            "valid": mask,
            "faces_before_cull": faces_before,
            "faces_after_cull": len(faces),
        }
    return mesh


# ── validity repair ───────────────────────────────────────────────────────────


def _close_pinholes(
    mask: np.ndarray,
    points: np.ndarray,
    depth: np.ndarray,
    radius: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Morphologically close the validity mask and fill in the recovered pixels.

    Dilating then eroding by the same radius reinstates gaps up to `2 * radius`
    across while leaving larger openings untouched. Reinstated pixels usually
    carry unreliable geometry, so their position and depth are replaced by the
    mean of their valid neighbours — that keeps the patch flush with the surface
    around it rather than spiking into the scene.
    """
    closed = _erode(_dilate(mask, radius), radius) | mask
    filled = closed & ~mask
    if not filled.any():
        return closed, points, depth

    points = points.copy()
    depth = depth.copy()

    # Grow the known values outwards one ring at a time so wide gaps converge.
    known = mask.copy()
    for _ in range(radius):
        target = filled & ~known
        if not target.any():
            break
        neighbour_sum, neighbour_count = _neighbour_totals(points, known)
        depth_sum, _ = _neighbour_totals(depth[..., None], known)

        fillable = target & (neighbour_count > 0)
        counts = neighbour_count[fillable][:, None]
        points[fillable] = neighbour_sum[fillable] / counts
        depth[fillable] = depth_sum[fillable][:, 0] / counts[:, 0]
        known |= fillable

    return known, points, depth


def _neighbour_totals(
    values: np.ndarray, known: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Sum and count of the 4-neighbourhood, restricted to `known` pixels."""
    weighted = np.where(known[..., None], values, 0.0)
    total = np.zeros_like(values)
    count = np.zeros(known.shape, dtype=np.float64)
    for source, destination in _SHIFTS:
        total[destination] += weighted[source]
        count[destination[:2]] += known[source[:2]]
    return total, count


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Binary dilation by `radius` steps of the 4-neighbourhood."""
    out = mask
    for _ in range(radius):
        grown = out.copy()
        for source, destination in _SHIFTS:
            grown[destination[:2]] |= out[source[:2]]
        out = grown
    return out


def _erode(mask: np.ndarray, radius: int) -> np.ndarray:
    """Binary erosion by `radius` steps. The border is treated as filled so the
    frame's outer edge is not eaten away."""
    padded = np.pad(mask, radius, mode="constant", constant_values=True)
    out = ~_dilate(~padded, radius)
    return out[radius:-radius, radius:-radius]


#: (source_slice, destination_slice) pairs shifting an array by one pixel in each
#: of the four cardinal directions. Indexed with `[:2]` for 2-D arrays.
_SHIFTS = [
    ((slice(1, None), slice(None), Ellipsis), (slice(0, -1), slice(None), Ellipsis)),
    ((slice(0, -1), slice(None), Ellipsis), (slice(1, None), slice(None), Ellipsis)),
    ((slice(None), slice(1, None), Ellipsis), (slice(None), slice(0, -1), Ellipsis)),
    ((slice(None), slice(0, -1), Ellipsis), (slice(None), slice(1, None), Ellipsis)),
]


# ── face construction ─────────────────────────────────────────────────────────


def _grid_faces(mask: np.ndarray, points: np.ndarray) -> np.ndarray:
    """
    Triangulate the pixel grid over every 2×2 cell with at least three valid
    corners.

    Corners are labelled by image position — `a` top-left, `b` top-right,
    `c` bottom-right, `d` bottom-left. Winding is chosen so face normals point
    back towards the camera under the OpenCV convention (x right, y down,
    z forward).

    Returns:
        (N, 3) int32 array of vertex indices into the flattened H×W grid.
    """
    height, width = mask.shape
    index = np.arange(height * width, dtype=np.int32).reshape(height, width)

    a, b = index[:-1, :-1], index[:-1, 1:]
    c, d = index[1:, 1:], index[1:, :-1]
    va, vb = mask[:-1, :-1], mask[:-1, 1:]
    vc, vd = mask[1:, 1:], mask[1:, :-1]

    faces = []

    # Four valid corners: two triangles. Splitting along the shorter 3D diagonal
    # keeps the seam within the surface; the fixed diagonal upstream uses cuts
    # across slanted geometry and exaggerates the very stretch we then measure.
    full = va & vb & vc & vd
    if full.any():
        flat = points.reshape(-1, 3)
        diagonal_ac = np.linalg.norm(flat[a[full]] - flat[c[full]], axis=-1)
        diagonal_bd = np.linalg.norm(flat[b[full]] - flat[d[full]], axis=-1)
        prefer_ac = diagonal_ac <= diagonal_bd

        for corners, selected in (
            (((a, c, b), (a, d, c)), prefer_ac),
            (((a, d, b), (b, d, c)), ~prefer_ac),
        ):
            if not selected.any():
                continue
            cell = np.zeros_like(full)
            cell[full] = selected
            for p, q, r in corners:
                faces.append(np.stack([p[cell], q[cell], r[cell]], axis=1))

    # Exactly three valid corners: one triangle. This is what upstream throws
    # away, and it is why its surfaces come out serrated along every boundary.
    for missing, (p, q, r) in (
        (~va & vb & vc & vd, (b, d, c)),
        (va & ~vb & vc & vd, (a, d, c)),
        (va & vb & ~vc & vd, (a, d, b)),
        (va & vb & vc & ~vd, (a, c, b)),
    ):
        if missing.any():
            faces.append(np.stack([p[missing], q[missing], r[missing]], axis=1))

    if not faces:
        return np.zeros((0, 3), dtype=np.int32)
    return np.concatenate(faces, axis=0).astype(np.int32)


# ── continuity ────────────────────────────────────────────────────────────────


def _estimate_focal(
    points: np.ndarray, depth: np.ndarray, mask: np.ndarray
) -> float:
    """
    Recover a focal length from the geometry itself.

    For a fronto-parallel surface, neighbouring pixels sit `depth / focal` apart
    in 3D. Taking the median of `depth / spacing` over all valid horizontal
    neighbours inverts that relation. The median is dominated by continuous
    surface, so occlusion boundaries — the very edges we are about to cut — do
    not skew it.
    """
    pair = mask[:, :-1] & mask[:, 1:]
    if not pair.any():
        return 1.0

    spacing = np.linalg.norm(points[:, 1:] - points[:, :-1], axis=-1)[pair]
    near = np.minimum(depth[:, :-1], depth[:, 1:])[pair]
    usable = spacing > 0
    if not usable.any():
        return 1.0
    return float(np.median(near[usable] / spacing[usable]))


#: Rotates a face's corners by one, pairing each with the next to walk its edges.
_NEXT_CORNER = np.array([1, 2, 0])


def _on_same_surface(
    faces: np.ndarray,
    points: np.ndarray,
    normals: np.ndarray,
    footprint: np.ndarray,
    max_offplane: float,
) -> np.ndarray:
    """
    Flag the faces whose three edges all stay on one surface.

    For each edge, project the offset between its endpoints onto the normal at
    each end: how far one endpoint sits off the tangent plane at the other. That
    is zero for any planar surface regardless of viewing angle. Scaling by the
    local footprint makes the threshold resolution- and scale-free.

    The two ends are combined with `min`, so a single unreliable normal — common
    on thin structures and at creases — cannot punch a hole on its own, while an
    occlusion straddle still fails both ways.
    """
    flat_points = points.reshape(-1, 3)
    flat_normals = normals.reshape(-1, 3)
    flat_normals = flat_normals / (
        np.linalg.norm(flat_normals, axis=-1, keepdims=True) + 1e-12
    )

    corner = flat_points[faces]  # (N, 3, 3)
    corner_normal = flat_normals[faces]
    corner_footprint = footprint[faces]

    offset = corner[:, _NEXT_CORNER] - corner
    from_here = np.abs((offset * corner_normal).sum(axis=-1))
    from_there = np.abs((offset * corner_normal[:, _NEXT_CORNER]).sum(axis=-1))

    scale = np.minimum(corner_footprint[:, _NEXT_CORNER], corner_footprint)
    residual = np.minimum(from_here, from_there) / np.maximum(scale, 1e-12)
    return (residual <= max_offplane).all(axis=1)


def _agrees_with_normals(
    faces: np.ndarray,
    points: np.ndarray,
    normals: np.ndarray,
    max_tilt: float,
) -> np.ndarray:
    """
    Flag the faces whose own orientation matches the normals they inherit.

    A triangle on real surface points the same way as the normals at its
    corners. One spanning the ramp the depth head interpolates across an
    occlusion boundary lies edge-on to both sides it connects, so its geometric
    normal turns roughly perpendicular to the ones it inherits.

    `_on_same_surface` cannot see this: it walks single edges, and along a
    several-pixel ramp every step is small enough to pass. Judging the face as a
    whole catches it.

    Winding is arbitrary and the predicted normals carry no reliable sign, so the
    comparison uses `|cos|`. Degenerate faces have no meaningful normal and are
    kept; they render as nothing either way.
    """
    corner = points.reshape(-1, 3)[faces]
    geometric = np.cross(corner[:, 1] - corner[:, 0], corner[:, 2] - corner[:, 0])
    length = np.linalg.norm(geometric, axis=-1, keepdims=True)

    flat_normals = normals.reshape(-1, 3)[faces].astype(np.float64)
    flat_normals /= np.linalg.norm(flat_normals, axis=-1, keepdims=True) + 1e-12
    predicted = flat_normals.mean(axis=1)
    predicted /= np.linalg.norm(predicted, axis=-1, keepdims=True) + 1e-12

    cosine = np.abs((geometric / (length + 1e-12) * predicted).sum(axis=-1))
    return (cosine >= np.cos(np.radians(max_tilt))) | (length[:, 0] <= 1e-12)


def _within_stretch(
    faces: np.ndarray,
    points: np.ndarray,
    footprint: np.ndarray,
    max_stretch: float,
) -> np.ndarray:
    """
    Flag the faces whose edges are no longer than `max_stretch` footprints.

    The fallback when no normals are available. Using the *nearer* endpoint's
    footprint matters: at an occlusion boundary the background depth would
    inflate the allowance and let the straddling face through.
    """
    corner = points.reshape(-1, 3)[faces]
    corner_footprint = footprint[faces]

    edge_length = np.linalg.norm(corner[:, _NEXT_CORNER] - corner, axis=-1)
    scale = np.minimum(corner_footprint[:, _NEXT_CORNER], corner_footprint)
    return (edge_length <= max_stretch * scale).all(axis=1)


# ── assembly ──────────────────────────────────────────────────────────────────


def _build_trimesh(faces: np.ndarray, points: np.ndarray, colors: np.ndarray, trimesh):
    """Compact the referenced vertices and wrap them in a `trimesh.Trimesh`."""
    if not len(faces):
        return trimesh.Trimesh(
            vertices=np.zeros((0, 3)), faces=np.zeros((0, 3), dtype=np.int64)
        )

    used, remapped = np.unique(faces, return_inverse=True)
    return trimesh.Trimesh(
        vertices=points.reshape(-1, 3)[used],
        faces=remapped.astype(np.int64).reshape(-1, 3),
        vertex_colors=colors.reshape(-1, colors.shape[-1])[used],
        process=False,
    )


def _drop_small_components(mesh, min_faces: int, trimesh):
    """Remove disconnected fragments below `min_faces`, keeping vertex colours."""
    components = mesh.split(only_watertight=False)
    if len(components) <= 1:
        return mesh

    kept = [part for part in components if len(part.faces) >= min_faces]
    if not kept:
        return max(components, key=lambda part: len(part.faces))
    if len(kept) == len(components):
        return mesh
    return trimesh.util.concatenate(kept)
