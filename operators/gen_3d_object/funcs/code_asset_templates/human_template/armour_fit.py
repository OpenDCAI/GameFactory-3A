"""Size and place rigid armour pieces from measured body landmarks.

Parts are parented to the figure; this module does not bind them to bones.
"""
from __future__ import annotations

from typing import Any, Sequence


def span_to_wrap(source: str, *, girth: float, axis: str = "z",
                 clearance: float = 1.10,
                 trim: Sequence[float] | None = None) -> float:
    """Compute a uniform mesh span that encloses the requested girth.

    ``girth`` is a size along ``axis`` or a mapping of axes to sizes.
    Use the largest required scale and multiply by ``clearance``.
    """

    from models.common.glb_writer import load_mesh_asset

    extent = load_mesh_asset(source, trim)["unit_extent"]
    index = {"x": 0, "y": 1, "z": 2}
    wanted = girth if isinstance(girth, dict) else {axis: girth}

    spans = []
    for name, measure in wanted.items():
        across = extent[index[name]]
        if across <= 0:
            raise ValueError(
                f"{source}: no extent along {name}, so nothing to wrap")
        # Convert cross-section size to longest-axis span.
        spans.append(measure * clearance * max(extent) / across)
    return max(spans)


#: Slot coordinate formulas; midline and limb use measured body profiles.
SLOTS: dict[str, dict[str, Any]] = {
    # --- legs -------------------------------------------------------------
    "shin":     {"lateral": "limb",   "height": ("knee_y", "ankle_y"),
                 "depth": "midline"},
    "thigh":    {"lateral": "limb",   "height": ("crotch_y", "knee_y"),
                 "depth": "midline"},
    "knee":     {"lateral": "limb",   "height": "knee_y", "depth": "midline"},
    # Use measured instep height and foot center.
    "foot":     {"lateral": "foot_x", "height": "instep_y", "depth": "foot_z"},
    # --- midline ----------------------------------------------------------
    "torso":    {"lateral": None, "height": "chest_y",  "depth": "midline"},
    "waist":    {"lateral": None, "height": "waist_y",  "depth": "midline"},
    "hip":      {"lateral": None, "height": "hip_y",    "depth": "midline"},
    "head":     {"lateral": None, "height": "head_y",   "depth": "midline"},
    "neck":     {"lateral": None, "height": "neck_y",   "depth": "midline"},
    # --- arms: a T-pose runs them along x, so a position is a distance out --
    "shoulder": {"lateral": ("shoulder_x", 1.15), "height": "shoulder_y",
                 "depth": "arm_z"},
    "upperarm": {"lateral": ("shoulder_x", "elbow_x"), "height": "shoulder_y",
                 "depth": "arm_z"},
    "elbow":    {"lateral": "elbow_x", "height": "shoulder_y", "depth": "arm_z"},
    "forearm":  {"lateral": ("elbow_x", "wrist_x"), "height": "shoulder_y",
                 "depth": "arm_z"},
    "hand":     {"lateral": "wrist_x", "height": "shoulder_y", "depth": "arm_z"},
}


def _resolve(spec: Any, landmarks: dict[str, Any]) -> float:
    """Resolve a constant, landmark name, midpoint pair or (name, scale) pair."""

    if spec is None:
        return 0.0
    if isinstance(spec, (int, float)):
        return float(spec)
    if isinstance(spec, str):
        return float(landmarks[spec])
    first, second = spec
    if isinstance(second, (int, float)) and not isinstance(second, bool):
        return float(landmarks[first]) * float(second)
    return (float(landmarks[first]) + float(landmarks[second])) / 2.0


def slot_position(
    slot: str, landmarks: dict[str, Any], *, side: str | None = None,
    offset: Sequence[float] | None = None,
    body_origin: Sequence[float] | None = None,
    slots: dict[str, dict[str, Any]] | None = None,
) -> tuple[float, float, float]:
    """Resolve a measured slot into the body's local coordinates."""
    from .figure_fit import depth_at, leg_x_at

    if side not in (None, "l", "r"):
        raise ValueError("side must be l, r or None")
    row = {**SLOTS, **(slots or {})}.get(slot)
    if row is None:
        raise ValueError(f"unknown slot {slot!r}")
    marks = dict(landmarks)
    marks.setdefault("instep_y", marks.get("ankle_y", 0.0) * 0.6)
    height = _resolve(row.get("height"), marks)
    lateral = row.get("lateral")
    x = leg_x_at(marks, height) if lateral == "limb" else _resolve(lateral, marks)
    depth = row.get("depth")
    z = depth_at(marks, height) if depth == "midline" else _resolve(depth, marks)
    at = ((-x if side == "l" else x), height, z)
    origin = body_origin if body_origin is not None else (0.0, 0.0, 0.0)
    delta = offset if offset is not None else (0.0, 0.0, 0.0)
    return tuple(float(at[i]) + float(delta[i]) - float(origin[i]) for i in range(3))


def fit_armour(
    *,
    body_id: str,
    landmarks: dict[str, Any],
    pieces: Sequence[dict[str, Any]],
    body_origin: Sequence[float] | None = None,
    slots: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Place pieces in the body's local frame using measured landmarks.

    Each piece supplies ``id``, ``slot`` and geometry; mesh pieces also need
    ``source`` and ``span`` in metres. Optional ``side``, ``offset``, ``rotation``
    and ``mirror`` control placement. ``slots`` overrides or adds slot formulas.
    ``body_origin`` is subtracted from the measured world position.
    Unknown slots raise ValueError.
    """

    placed: list[dict[str, Any]] = []
    for piece in pieces:
        at = slot_position(piece["slot"], landmarks, side=piece.get("side"),
                           offset=piece.get("offset"), body_origin=body_origin,
                           slots=slots)

        part: dict[str, Any] = {
            "id": piece["id"],
            "material": piece.get("material", "steel"),
            "at": list(at),
            "rotation": list(piece.get("rotation") or (0.0, 0.0, 0.0)),
            "parent": body_id,
        }

        if piece.get("source"):
            span = float(piece["span"])
            part.update({
                "kind": "mesh",
                "source": piece["source"],
                # Uniform scaling preserves mesh proportions.
                "size": [span, span, span],
                "profile": None,
            })
            if piece.get("long_axis"):
                part["long_axis"] = piece["long_axis"]
            if piece.get("trim"):
                part["trim"] = list(piece["trim"])
            # Mirror geometry as requested, in addition to its placement.
            if piece.get("mirror"):
                part["mirror"] = piece["mirror"]
        else:
            part.update({
                "kind": piece.get("kind", "cylinder"),
                "size": list(piece["size"]),
                "segments": piece.get("segments", 16),
            })
            for field in ("profile", "chamfer"):
                if piece.get(field) is not None:
                    part[field] = piece[field]

        placed.append(part)

    return placed


def body_part(source: str, *, part_id: str = "figure", height_metres: float,
              material: str = "skin",
              trim: Sequence[float] | None = None) -> dict[str, Any]:
    """Create a figure mesh part with its base at Y=0."""

    from models.common.glb_writer import load_mesh_asset

    asset = load_mesh_asset(source, trim)
    # Raise the centered mesh by half its placed height.
    half = asset["unit_extent"][1] * height_metres / 2.0
    return {
        "id": part_id,
        "kind": "mesh",
        "source": source,
        "size": [height_metres, height_metres, height_metres],
        "at": [0.0, half, 0.0],
        "rotation": [0.0, 0.0, 0.0],
        "material": material,
        "long_axis": "y",
        "profile": None,
        **({"trim": list(trim)} if trim else {}),
    }
