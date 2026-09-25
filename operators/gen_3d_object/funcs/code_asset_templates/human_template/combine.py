"""Compose rigid meshes around measured anatomy using caller-defined attachments.

Sockets are metadata; this route does not bind meshes to individual bones.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Sequence

from . import armour_fit, figure_fit, segment, sockets
from .. import compose as compose_mod


class CombineError(ValueError):
    """A body, a harness or a weapon that cannot be worn together."""


def _drop_unpaired(pieces: dict[str, dict[str, Any]],
                   warnings: list[str]) -> dict[str, dict[str, Any]]:
    """Drop unpaired lateral pieces and record a warning."""

    kept = dict(pieces)
    for region, mesh in list(pieces.items()):
        side = mesh.get("side")
        if side not in ("l", "r"):
            continue
        other = region[:-1] + ("r" if side == "l" else "l")
        if other not in pieces:
            warnings.append(
                f"dropped {region}: no {other} to pair with, and a one-sided "
                "lateral piece fails chirality"
            )
            kept.pop(region, None)
    return kept


def _weapon_parts(
    weapons: Sequence[dict[str, Any]],
    *,
    body_id: str,
    socket_at: dict[str, Sequence[float]],
    grip_templates: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Place rigid assets using explicit parameters or caller-supplied grips."""

    parts: list[dict[str, Any]] = []
    for index, weapon in enumerate(weapons):
        source = weapon.get("source")
        if not source:
            raise CombineError(
                f"weapon {index} has no `source`. A weapon without a mesh "
                "cannot be held."
            )
        kind = weapon.get("kind")
        grip = sockets.grip_for(kind, templates=grip_templates or {}) if kind else {}
        settings = {**grip, **weapon}
        socket_id = settings.get("socket")
        if socket_id not in socket_at:
            raise CombineError(f"weapon {index}: unknown socket {socket_id!r}")
        try:
            length = float(settings["length"])
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise CombineError(f"weapon {index}: length must be finite and positive") from exc
        if not math.isfinite(length) or length <= 0:
            raise CombineError(f"weapon {index}: length must be finite and positive")
        axis = settings.get("long_axis")
        if axis not in ("x", "y", "z"):
            raise CombineError(f"weapon {index}: long_axis must be x, y or z")
        offset = sockets.vector3(settings.get("offset", (0, 0, 0)), "weapon offset")
        rotation = sockets.vector3(settings.get("rotation", (0, 0, 0)), "weapon rotation")
        at = [
            float(socket_at[socket_id][axis]) + float(offset[axis])
            for axis in range(3)
        ]
        part_id = str(weapon.get("id") or f"weapon-{index}")
        parts.append({
            "id": part_id,
            "kind": "mesh",
            "source": str(source),
            "size": [length, length, length],
            "at": at,
            "rotation": list(rotation),
            "material": settings.get("material", "steel"),
            "parent": body_id,
            "profile": None,
            "long_axis": axis,
        })
    return parts


def validate_kit(
    spec: dict[str, Any],
    landmarks: dict[str, Any],
) -> dict[str, Any]:
    """Check landmark placement and approximate weapon/torso overlap."""

    from operators.gen_3d_object.funcs.code_asset import part_bounds, validate_spec

    placed = validate_spec(spec)
    by_id = {part["id"]: part for part in placed["parts"]}
    warnings: list[str] = []
    checks: list[dict[str, Any]] = []

    slot_height = {
        "head": landmarks["head_y"],
        "neck": landmarks["neck_y"],
        "torso": landmarks["chest_y"],
        "waist": landmarks["waist_y"],
        "hip": landmarks["hip_y"],
        "shoulder": landmarks["shoulder_y"],
        "upperarm": landmarks["shoulder_y"],
        "forearm": landmarks["shoulder_y"],
        "hand": landmarks["shoulder_y"],
        "thigh": (landmarks["crotch_y"] + landmarks["knee_y"]) / 2.0,
        "shin": (landmarks["knee_y"] + landmarks["ankle_y"]) / 2.0,
        "foot": landmarks.get("foot_height", landmarks["ankle_y"]) * 0.5,
    }

    for part in placed["parts"]:
        if not part["id"].startswith("armour-"):
            continue
        region = part["id"][len("armour-"):]
        slot = region.rsplit("-", 1)[0] if region[-2:] in ("-l", "-r") else region
        expected_y = slot_height.get(slot)
        if expected_y is None:
            continue
        low, high = part_bounds(part)
        centre_y = (low[1] + high[1]) / 2.0
        distance = abs(centre_y - expected_y)
        size = max(high[axis] - low[axis] for axis in range(3))
        far = distance > 0.18
        if far:
            warnings.append(
                f"{part['id']} centre y {centre_y:.3f} is {distance:.3f} m "
                f"from the {slot} landmark at {expected_y:.3f}; the plate "
                "is probably on the wrong limb"
            )
        checks.append({
            "id": part["id"], "slot": slot,
            "distance_m": round(distance, 4),
            "size_m": round(size, 4),
            "far": far,
        })

    # AABB overlap is a placement warning, not a collision test.
    figure = by_id.get("figure")
    torso_box = None
    if figure is not None:
        low, high = part_bounds(figure)
        chest_y = landmarks["chest_y"]
        torso_box = (
            (low[0] * 0.35, chest_y - 0.18, low[2] * 0.4),
            (high[0] * 0.35, chest_y + 0.18, high[2] * 0.4),
        )
    for part in placed["parts"]:
        if not part["id"].startswith("weapon-"):
            continue
        box = part_bounds(part)
        if torso_box is None:
            continue
        overlap = 1.0
        for axis in range(3):
            lo = max(box[0][axis], torso_box[0][axis])
            hi = min(box[1][axis], torso_box[1][axis])
            overlap *= max(0.0, hi - lo)
        volume = 1.0
        for axis in range(3):
            volume *= max(1e-6, box[1][axis] - box[0][axis])
        fraction = overlap / volume
        if fraction > 0.35:
            warnings.append(
                f"{part['id']} overlaps the torso by {fraction:.0%} of its "
                "volume; the grip angle is probably wrong"
            )
        checks.append({
            "id": part["id"], "torso_overlap": round(fraction, 3),
        })

    return {
        "ok": not any(
            item.get("far") or item.get("oversized") for item in checks
        ),
        "warnings": warnings,
        "checks": checks,
    }


def combine_avatar(
    *,
    body: str,
    armour: str | None = None,
    weapons: Sequence[dict[str, Any]] | None = None,
    height_metres: float,
    parts_dir: str | Path,
    subject: str = "composed avatar",
    segment_armour: bool = True,
    min_triangles: int = 8,
    materials: dict[str, dict[str, Any]] | None = None,
    trim_body: Sequence[float] | None = None,
    trim_armour: Sequence[float] | None = None,
    socket_definitions: dict[str, dict[str, Any]] | None = None,
    grip_templates: dict[str, dict[str, Any]] | None = None,
    slot_definitions: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a rigid composition spec and placement report.

    ``socket_definitions`` supplies named attachment points with a measured
    slot or parent-local position. ``grip_templates`` supplies optional weapon
    presets; each weapon may override their fields. No sockets or grips are
    implicit. ``slot_definitions`` overrides anatomical placement formulas.
    See test/test_3d_object_compose.py for editable example settings.
    """

    parts_path = Path(parts_dir)
    parts_path.mkdir(parents=True, exist_ok=True)

    marks = figure_fit.landmarks_for(body, height_metres, trim=trim_body)
    figure = armour_fit.body_part(
        body, part_id="figure", height_metres=height_metres,
        material="skin", trim=trim_body,
    )
    origin = figure["at"]
    socket_records = sockets.sockets_for(
        marks, body_id="figure", body_origin=origin,
        definitions=socket_definitions or {}, slot_definitions=slot_definitions,
    )
    socket_at = {row["id"]: row["at"] for row in socket_records}

    warnings: list[str] = []
    worn: list[dict[str, Any]] = []
    segment_counts: dict[str, int] = {}
    segment_paths: dict[str, str] = {}
    cut_info: dict[str, Any] = {}

    if armour:
        if segment_armour:
            try:
                pieces = segment.segment_mesh(
                    armour, trim=trim_armour, min_triangles=min_triangles,
                    guide=body, info=cut_info,
                )
            except (segment.SegmentError, ValueError) as exc:
                raise CombineError(
                    f"could not cut armour {armour}: {exc}"
                ) from exc
            if cut_info.get("cut_guide") == "body":
                warnings.append(
                    "armour self-measure could not drive a cut "
                    f"({cut_info.get('cut_guide_reason', 'unusable T-pose')}); "
                    "sliced on the body's landmarks instead"
                )
            pieces = _drop_unpaired(pieces, warnings)
            segment_counts = {
                region: mesh["triangles"] for region, mesh in pieces.items()
            }
            segment_paths = segment.write_segments(
                pieces, str(parts_path / "armour"),
            )
            kit: list[dict[str, Any]] = []
            for region, path in segment_paths.items():
                slot, side = segment.REGION_SLOTS[region]
                stretch = armour_fit.piece_stretch(slot, marks, path)
                kit.append({
                    "id": f"armour-{region}",
                    "source": path,
                    "slot": slot,
                    "side": side,
                    "span": max(stretch),
                    "stretch": stretch,
                    "material": "steel",
                    "long_axis": pieces[region].get("long_axis") or segment.LONG_AXIS[slot],
                })
            worn.extend(armour_fit.fit_armour(
                body_id="figure", landmarks=marks, pieces=kit,
                body_origin=origin, slots=slot_definitions,
            ))
        else:
            # Overlay the complete shell at the body origin.
            worn.append({
                "id": "armour-whole",
                "kind": "mesh",
                "source": armour,
                "size": [height_metres, height_metres, height_metres],
                "at": [0.0, 0.0, 0.0],
                "rotation": [0.0, 0.0, 0.0],
                "material": "steel",
                "parent": "figure",
                "long_axis": "y",
                "profile": None,
                **({"trim": list(trim_armour)} if trim_armour else {}),
            })

    if weapons:
        worn.extend(_weapon_parts(
            weapons, body_id="figure", socket_at=socket_at,
            grip_templates=grip_templates,
        ))

    spec = compose_mod.compose(
        subject=subject,
        body=[figure],
        worn=worn,
        height_metres=height_metres,
        asset_type="avatar",
        materials=materials,
        notes=(
            "Rigid composition using measured landmarks. "
            "Sockets are recorded in extras, not as geometry."
        ),
    )

    report = {
        "body": str(Path(body).resolve()),
        "armour": str(Path(armour).resolve()) if armour else None,
        "height_metres": height_metres,
        "segmented": bool(armour and segment_armour),
        "landmarks": {key: (round(value, 4) if isinstance(value, (int, float))
                            else value)
                      for key, value in marks.items()
                      if not isinstance(value, list)},
        "segments": segment_counts,
        "segment_paths": segment_paths,
        "cut_guide": cut_info.get("cut_guide"),
        "stripped_cape_triangles": cut_info.get("stripped_cape_triangles"),
        "sockets": socket_records,
        "warnings": warnings,
    }
    try:
        report["validation"] = validate_kit(spec, marks)
        report["warnings"].extend(report["validation"].get("warnings") or ())
    except Exception as exc:  # noqa: BLE001 — validation must not sink a build
        report["warnings"].append(f"validation skipped: {exc}")

    spec.setdefault("extras", {})
    spec["extras"]["sockets"] = socket_records
    spec["extras"]["compose"] = {
        "segmented": report["segmented"],
        "segments": segment_counts,
        "cut_guide": cut_info.get("cut_guide"),
        "bone_bindings": {
            row["id"]: row["bone"] for row in socket_records if row.get("bone")
        },
    }
    return {"spec": spec, "report": report}


def write_report(report: dict[str, Any], path: str | Path) -> str:
    """Write the compose report as JSON next to the mesh."""

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return str(out)
