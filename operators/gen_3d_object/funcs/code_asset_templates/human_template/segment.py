"""Split armour meshes into anatomical regions using measured landmarks.

Classification uses triangle centroids in normalized Y-up coordinates.
Triangles are retained whole, with their UVs and materials.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Sequence

from .armour_fit import SLOTS

Vec3 = tuple[float, float, float]

#: Region name -> (armour slot, side).
REGION_SLOTS: dict[str, tuple[str, str | None]] = {
    "head": ("head", None),
    "neck": ("neck", None),
    "torso": ("torso", None),
    "waist": ("waist", None),
    "hip": ("hip", None),
    "shoulder-l": ("shoulder", "l"),
    "shoulder-r": ("shoulder", "r"),
    "upperarm-l": ("upperarm", "l"),
    "upperarm-r": ("upperarm", "r"),
    "forearm-l": ("forearm", "l"),
    "forearm-r": ("forearm", "r"),
    "hand-l": ("hand", "l"),
    "hand-r": ("hand", "r"),
    "thigh-l": ("thigh", "l"),
    "thigh-r": ("thigh", "r"),
    "shin-l": ("shin", "l"),
    "shin-r": ("shin", "r"),
    "foot-l": ("foot", "l"),
    "foot-r": ("foot", "r"),
}

#: Expected longest axis for each slot.
LONG_AXIS: dict[str, str] = {
    "head": "y", "neck": "y", "torso": "y", "waist": "y", "hip": "y",
    "shoulder": "x", "upperarm": "x", "forearm": "x", "hand": "x",
    "thigh": "y", "shin": "y", "foot": "z",
}

#: Slots sized by body girth.
WRAP_SLOTS = frozenset({"torso", "waist", "hip", "neck"})


class SegmentError(ValueError):
    """A mesh that cannot be cut into wearable pieces."""


def _unusable_for_cut(measured: dict[str, Any]) -> str | None:
    """Return a reason if measured landmarks are unsuitable for segmentation."""

    fractions = measured["fractions"]
    if fractions["shoulder_y"] < 0.50:
        return (
            f"shoulder_y={fractions['shoulder_y']:.3f} of height; "
            "a cut needs shoulders in the upper half"
        )
    if fractions["neck_y"] < 0.58:
        return (
            f"neck_y={fractions['neck_y']:.3f} of height; "
            "the helmet ate the torso"
        )
    return None


def _ctx_from_measured(asset: dict[str, Any], measured: dict[str, Any]
                       ) -> dict[str, Any]:
    """Map measured landmark fractions onto the asset's centered Y-span."""

    positions = asset["positions"]
    low = min(point[1] for point in positions)
    high = max(point[1] for point in positions)
    span = high - low

    def at_y(fraction: float) -> float:
        return low + fraction * span

    fractions = measured["fractions"]
    reach = measured["arm_reach_fraction"] * span
    shoulder_x = measured["shoulder_x_fraction"] * span
    # Use the fallback wrist fraction when the profile lacks a hand flare.
    wrist_along = measured["wrist_along_arm"]
    if wrist_along < 0.55 or wrist_along > 0.95:
        wrist_along = 0.86
    wrist_x = reach * wrist_along
    chest_y = at_y(fractions["chest_y"])
    waist_y = at_y(fractions["waist_y"])
    # Enable cape separation when the mesh extends behind the chest.
    chest_band = [
        point for point in positions
        if waist_y <= point[1] <= chest_y and abs(point[0]) < shoulder_x
    ]
    chest_zs = sorted(point[2] for point in chest_band) or [0.0]
    chest_back = chest_zs[max(0, int(0.10 * (len(chest_zs) - 1)))]
    zmin = min(point[2] for point in positions)
    drop_cape = (chest_back - zmin) > span * 0.04
    return {
        "asset": asset,
        "span": span,
        "low": low,
        "high": high,
        "has_arms": True,
        "shoulder_y": at_y(fractions["shoulder_y"]),
        "neck_y": at_y(fractions["neck_y"]),
        "neck_bottom": at_y(measured["neck_bottom_fraction"]),
        "chest_y": chest_y,
        "waist_y": waist_y,
        "hip_y": at_y(fractions["hip_y"]),
        "crotch_y": at_y(fractions["crotch_y"]),
        "knee_y": at_y(fractions["knee_y"]),
        "ankle_y": at_y(fractions["ankle_y"]),
        "shoulder_x": shoulder_x,
        "arm_reach": reach,
        "wrist_x": wrist_x,
        "elbow_x": (shoulder_x + wrist_x) / 2.0,
        "chest_back": chest_back,
        "drop_cape": drop_cape,
    }


def _mesh_space_landmarks(source: str, *, trim: Sequence[float] | None = None
                          ) -> dict[str, Any]:
    """Return landmarks in the centered, longest-axis-normalized mesh frame."""

    from models.common.glb_writer import load_mesh_asset
    from . import figure_fit

    asset = load_mesh_asset(source, trim)
    measured = figure_fit.measure_figure(source, trim=trim)
    return _ctx_from_measured(asset, measured)


def classify_point(point: Sequence[float], ctx: dict[str, Any]) -> str:
    """Which region a point of a T-pose mesh belongs to."""

    x, y, z = float(point[0]), float(point[1]), float(point[2])
    span = ctx["span"]
    side = "l" if x < 0.0 else "r"

    # Exclude cape triangles behind the torso and below the waist.
    if (ctx.get("drop_cape")
            and y < ctx["waist_y"]
            and y > ctx["ankle_y"]
            and z < float(ctx.get("chest_back", 0.0)) - span * 0.015
            and abs(x) < ctx["shoulder_x"] * 0.70):
        return "_cape"

    if y >= ctx["neck_y"]:
        return "head"
    if (y >= ctx["neck_bottom"]
            and abs(x) < ctx["shoulder_x"] * 0.55):
        return "neck"

    # T-pose arms lie outside the torso within the shoulder-height band.
    if (ctx.get("has_arms", True)
            and abs(x) > ctx["shoulder_x"] * 1.12
            and abs(y - ctx["shoulder_y"]) < span * 0.14):
        ax = abs(x)
        if ax > ctx["wrist_x"]:
            return f"hand-{side}"
        if ax > ctx["elbow_x"]:
            return f"forearm-{side}"
        if ax > ctx["shoulder_x"] * 1.35:
            return f"upperarm-{side}"
        return f"shoulder-{side}"

    # A-pose arms extend below the shoulder-height band.
    if (ctx.get("has_arms", True)
            and abs(x) > ctx["shoulder_x"] * 1.12
            and y < ctx["shoulder_y"]
            and y > ctx["crotch_y"]):
        ax = abs(x)
        if ax > ctx["wrist_x"]:
            return f"hand-{side}"
        if ax > ctx["elbow_x"]:
            return f"forearm-{side}"
        if ax > ctx["shoulder_x"] * 1.35:
            return f"upperarm-{side}"
        return f"shoulder-{side}"

    if y < ctx["ankle_y"] + span * 0.02:
        return f"foot-{side}"
    if y < ctx["knee_y"]:
        return f"shin-{side}"
    if y < ctx["crotch_y"]:
        return f"thigh-{side}"
    if y < ctx["hip_y"] + span * 0.03:
        return "hip"
    if y < ctx["waist_y"] + (ctx["chest_y"] - ctx["waist_y"]) * 0.25:
        return "waist"
    return "torso"


def _extract(asset: dict[str, Any],
             keep: Callable[[Vec3], bool]) -> dict[str, Any] | None:
    """Triangles whose centroid passes ``keep``, re-indexed."""

    positions: Sequence[Vec3] = asset["positions"]
    normals = asset["normals"]
    uvs = asset.get("uvs") or [(0.0, 0.0)] * len(positions)
    indices = asset["indices"]

    remap: dict[int, int] = {}
    new_positions: list[Vec3] = []
    new_normals: list[Vec3] = []
    new_uvs: list[tuple[float, float]] = []
    new_indices: list[int] = []

    for triangle in range(len(indices) // 3):
        corners = indices[triangle * 3:triangle * 3 + 3]
        centroid = tuple(
            sum(positions[index][axis] for index in corners) / 3.0
            for axis in range(3)
        )
        if not keep(centroid):  # type: ignore[arg-type]
            continue
        for index in corners:
            if index not in remap:
                remap[index] = len(new_positions)
                new_positions.append(positions[index])
                new_normals.append(normals[index])
                new_uvs.append(uvs[index])
            new_indices.append(remap[index])

    if len(new_indices) < 9:
        return None
    _ensure_outward(new_positions, new_normals, new_indices)
    xs = [p[0] for p in new_positions]
    ys = [p[1] for p in new_positions]
    zs = [p[2] for p in new_positions]
    extent = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    long_axis = "xyz"[extent.index(max(extent))]
    extracted: dict[str, Any] = {
        "positions": new_positions,
        "normals": new_normals,
        "uvs": new_uvs,
        "indices": new_indices,
        "triangles": len(new_indices) // 3,
        "has_uvs": any(u != 0.0 or v != 0.0 for u, v in new_uvs),
        "long_axis": long_axis,
        "materials": list(asset.get("materials") or []),
        "material_runs": [{
            "start": 0,
            "count": len(new_indices),
            "material": 0,
        }],
    }
    return extracted


def _ensure_outward(positions: list[Vec3], normals: list[Vec3],
                    indices: list[int]) -> None:
    """Reverse triangle winding when the subset has negative signed volume."""

    volume = 0.0
    for triangle in range(len(indices) // 3):
        ia, ib, ic = indices[triangle * 3:triangle * 3 + 3]
        a, b, c = positions[ia], positions[ib], positions[ic]
        volume += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            + a[1] * (b[2] * c[0] - b[0] * c[2])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        )
    if volume >= 0.0:
        return
    for triangle in range(len(indices) // 3):
        base = triangle * 3
        indices[base + 1], indices[base + 2] = indices[base + 2], indices[base + 1]
    for index, vector in enumerate(normals):
        normals[index] = (-vector[0], -vector[1], -vector[2])


def _dot3(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross3(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _norm3(vector: Sequence[float]) -> Vec3:
    length = math.sqrt(_dot3(vector, vector)) or 1.0
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _principal_axis(points: Sequence[Vec3]) -> Vec3:
    """Largest-variance direction of ``points``, via power iteration."""

    count = len(points)
    if count < 8:
        return (1.0, 0.0, 0.0)
    mx = sum(p[0] for p in points) / count
    my = sum(p[1] for p in points) / count
    mz = sum(p[2] for p in points) / count
    cxx = cyy = czz = cxy = cxz = cyz = 0.0
    for x, y, z in points:
        x, y, z = x - mx, y - my, z - mz
        cxx += x * x
        cyy += y * y
        czz += z * z
        cxy += x * y
        cxz += x * z
        cyz += y * z
    inv = 1.0 / count
    cov = (
        (cxx * inv, cxy * inv, cxz * inv),
        (cxy * inv, cyy * inv, cyz * inv),
        (cxz * inv, cyz * inv, czz * inv),
    )
    # Seed the power iteration on the largest-variance axis.
    if cyy >= cxx and cyy >= czz:
        axis = (0.0, 1.0, 0.0)
    elif czz >= cxx and czz >= cyy:
        axis = (0.0, 0.0, 1.0)
    else:
        axis = (1.0, 0.0, 0.0)
    for _ in range(24):
        nx = cov[0][0] * axis[0] + cov[0][1] * axis[1] + cov[0][2] * axis[2]
        ny = cov[1][0] * axis[0] + cov[1][1] * axis[1] + cov[1][2] * axis[2]
        nz = cov[2][0] * axis[0] + cov[2][1] * axis[1] + cov[2][2] * axis[2]
        axis = _norm3((nx, ny, nz))
    return axis


def _rotation_mapping(src: Vec3, dst: Vec3) -> tuple[Vec3, Vec3, Vec3]:
    """Rodrigues rotation that maps unit ``src`` onto unit ``dst``."""

    a = _norm3(src)
    b = _norm3(dst)
    cosine = _dot3(a, b)
    if cosine > 0.9995:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    if cosine < -0.9995:
        ortho = (1.0, 0.0, 0.0) if abs(a[0]) < 0.9 else (0.0, 1.0, 0.0)
        axis = _norm3(_cross3(a, ortho))
        return (
            (2 * axis[0] * axis[0] - 1.0, 2 * axis[0] * axis[1],
             2 * axis[0] * axis[2]),
            (2 * axis[1] * axis[0], 2 * axis[1] * axis[1] - 1.0,
             2 * axis[1] * axis[2]),
            (2 * axis[2] * axis[0], 2 * axis[2] * axis[1],
             2 * axis[2] * axis[2] - 1.0),
        )
    v = _cross3(a, b)
    k = 1.0 / (1.0 + cosine)
    vx00, vx01, vx02 = 0.0, -v[2], v[1]
    vx10, vx11, vx12 = v[2], 0.0, -v[0]
    vx20, vx21, vx22 = -v[1], v[0], 0.0
    s00 = vx00 * vx00 + vx01 * vx10 + vx02 * vx20
    s01 = vx00 * vx01 + vx01 * vx11 + vx02 * vx21
    s02 = vx00 * vx02 + vx01 * vx12 + vx02 * vx22
    s10 = vx10 * vx00 + vx11 * vx10 + vx12 * vx20
    s11 = vx10 * vx01 + vx11 * vx11 + vx12 * vx21
    s12 = vx10 * vx02 + vx11 * vx12 + vx12 * vx22
    s20 = vx20 * vx00 + vx21 * vx10 + vx22 * vx20
    s21 = vx20 * vx01 + vx21 * vx11 + vx22 * vx21
    s22 = vx20 * vx02 + vx21 * vx12 + vx22 * vx22
    return (
        (1.0 + vx00 + s00 * k, vx01 + s01 * k, vx02 + s02 * k),
        (vx10 + s10 * k, 1.0 + vx11 + s11 * k, vx12 + s12 * k),
        (vx20 + s20 * k, vx21 + s21 * k, 1.0 + vx22 + s22 * k),
    )


def _apply_linear(matrix: tuple[Vec3, Vec3, Vec3], vector: Sequence[float]
                  ) -> Vec3:
    return (
        matrix[0][0] * vector[0] + matrix[0][1] * vector[1]
        + matrix[0][2] * vector[2],
        matrix[1][0] * vector[0] + matrix[1][1] * vector[1]
        + matrix[1][2] * vector[2],
        matrix[2][0] * vector[0] + matrix[2][1] * vector[1]
        + matrix[2][2] * vector[2],
    )


_ARM_SLOTS = frozenset({"shoulder", "upperarm", "forearm", "hand"})


def align_limb_piece(mesh: dict[str, Any], slot: str, side: str | None
                     ) -> dict[str, Any]:
    """Rotate an arm cut so its principal axis matches the T-pose bone."""

    if slot not in _ARM_SLOTS:
        return mesh
    positions: list[Vec3] = list(mesh["positions"])
    if len(positions) < 8:
        return mesh
    axis = _principal_axis(positions)
    outward = -1.0 if side == "l" else 1.0
    target = (outward, 0.0, 0.0)
    if _dot3(axis, target) < 0.0:
        axis = (-axis[0], -axis[1], -axis[2])
    if _dot3(axis, target) > 0.90:
        return mesh
    rotation = _rotation_mapping(axis, target)
    cx = sum(p[0] for p in positions) / len(positions)
    cy = sum(p[1] for p in positions) / len(positions)
    cz = sum(p[2] for p in positions) / len(positions)
    centre = (cx, cy, cz)
    mesh["positions"] = [
        tuple(
            centre[axis_i] + value
            for axis_i, value in enumerate(
                _apply_linear(rotation, (
                    p[0] - centre[0], p[1] - centre[1], p[2] - centre[2],
                ))
            )
        )
        for p in positions
    ]
    mesh["normals"] = [
        _norm3(_apply_linear(rotation, n)) for n in mesh["normals"]
    ]
    xs = [p[0] for p in mesh["positions"]]
    ys = [p[1] for p in mesh["positions"]]
    zs = [p[2] for p in mesh["positions"]]
    extent = (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    mesh["long_axis"] = "xyz"[extent.index(max(extent))]
    mesh["aligned"] = True
    return mesh


def segment_mesh(
    source: str,
    *,
    trim: Sequence[float] | None = None,
    min_triangles: int = 8,
    regions: Sequence[str] | None = None,
    guide: str | None = None,
    info: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return region meshes with positions, normals, UVs, indices and triangle counts.

    Omit regions below ``min_triangles``. If source landmarks are unusable,
    apply ``guide`` landmark fractions to the source Y-span; fail without a guide.
    """

    from models.common.glb_writer import load_mesh_asset
    from . import figure_fit

    asset = load_mesh_asset(source, trim)
    measured = None
    reason = None
    try:
        measured = figure_fit.measure_figure(source, trim=trim)
        reason = _unusable_for_cut(measured)
        if reason is not None:
            measured = None
    except ValueError as exc:
        reason = str(exc)

    cut_guide = "armour"
    if measured is None:
        if not guide:
            raise SegmentError(
                f"{source}: {reason or 'could not measure a T-pose'}"
            )
        measured = figure_fit.measure_figure(guide)
        cut_guide = "body"
    if info is not None:
        info["cut_guide"] = cut_guide
        if reason:
            info["cut_guide_reason"] = reason

    ctx = _ctx_from_measured(asset, measured)
    if info is not None:
        info["drop_cape"] = bool(ctx.get("drop_cape"))
        if ctx.get("drop_cape"):
            positions = asset["positions"]
            indices = asset["indices"]
            cape = 0
            for triangle in range(len(indices) // 3):
                corners = indices[triangle * 3:triangle * 3 + 3]
                centroid = tuple(
                    sum(positions[index][axis] for index in corners) / 3.0
                    for axis in range(3)
                )
                if classify_point(centroid, ctx) == "_cape":
                    cape += 1
            info["stripped_cape_triangles"] = cape
    wanted = set(regions) if regions is not None else set(REGION_SLOTS)
    unknown = wanted - set(REGION_SLOTS)
    if unknown:
        raise SegmentError(
            f"unknown region(s) {sorted(unknown)}. "
            f"Known: {sorted(REGION_SLOTS)}"
        )

    pieces: dict[str, dict[str, Any]] = {}
    for region in sorted(wanted):
        extracted = _extract(
            ctx["asset"],
            lambda point, name=region: classify_point(point, ctx) == name,
        )
        if extracted is None or extracted["triangles"] < min_triangles:
            continue
        extracted["region"] = region
        extracted["slot"], extracted["side"] = REGION_SLOTS[region]
        align_limb_piece(extracted, extracted["slot"], extracted["side"])
        extracted["long_axis"] = (
            extracted.get("long_axis") or LONG_AXIS[extracted["slot"]]
        )
        pieces[region] = extracted

    if not pieces:
        raise SegmentError(
            f"{source}: no region held {min_triangles} triangles. "
            "The mesh is too coarse to cut, or it is not a standing figure."
        )
    return pieces


def write_segments(
    pieces: dict[str, dict[str, Any]],
    out_dir: str,
    *,
    material: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Write each segmented piece as a GLB and return region -> path."""

    from pathlib import Path

    from models.common.glb_writer import write_raw_mesh_glb

    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    steel = material or {
        "baseColor": [0.62, 0.64, 0.68, 1.0],
        "metallic": 1.0,
        "roughness": 0.24,
    }
    for region, mesh in pieces.items():
        textured = any(
            (entry or {}).get("image")
            for entry in (mesh.get("materials") or ())
        )
        written[region] = write_raw_mesh_glb(
            mesh, directory / f"{region}.glb",
            name=region, material=None if textured else steel,
        )
    return written


def piece_span(slot: str, landmarks: dict[str, Any], source: str,
               *, clearance: float = 1.08) -> float:
    """Compute piece span in metres from body girth or limb length."""

    from .armour_fit import span_to_wrap

    height = landmarks["height"]
    if slot in WRAP_SLOTS:
        girth = {
            "torso": {"x": landmarks["chest_width"], "z": landmarks["chest_depth"]},
            "waist": {"x": landmarks["waist_width"], "z": landmarks["waist_depth"]},
            "hip": {"x": landmarks["hip_width"], "z": landmarks["hip_depth"]},
            "neck": {"x": landmarks["neck_width"], "z": landmarks["neck_depth"]},
        }[slot]
        # Clamp girth estimates that include the wingspan.
        girth = {
            "x": min(float(girth["x"]), height * 0.38),
            "z": min(float(girth["z"]), height * 0.28),
        }
        return min(span_to_wrap(source, girth=girth, clearance=clearance),
                   height * 0.45)

    lengths = {
        "head": landmarks["height"] - landmarks["neck_y"],
        "shin": landmarks["knee_y"] - landmarks["ankle_y"],
        "thigh": landmarks["crotch_y"] - landmarks["knee_y"],
        "foot": max(landmarks.get("foot_height", 0.0), landmarks["ankle_y"]),
        "upperarm": abs(landmarks["elbow_x"] - landmarks["shoulder_x"]),
        "forearm": abs(landmarks["wrist_x"] - landmarks["elbow_x"]),
        "hand": min(0.12, max(0.08, abs(landmarks["arm_reach"] - landmarks["wrist_x"]))),
        "shoulder": max(0.10, landmarks["shoulder_x"] * 0.9),
    }
    span = float(lengths[slot])
    if span <= 0.02:
        raise SegmentError(
            f"slot {slot!r} has a {span:.4f} m span on this figure; "
            "a plate that small vanishes."
        )
    return span


# Check region slots at import time.
_MISSING_SLOTS = sorted({
    slot for slot, _side in REGION_SLOTS.values() if slot not in SLOTS
})
if _MISSING_SLOTS:
    raise RuntimeError(
        f"segment.REGION_SLOTS names slots armour_fit does not have: "
        f"{_MISSING_SLOTS}"
    )
