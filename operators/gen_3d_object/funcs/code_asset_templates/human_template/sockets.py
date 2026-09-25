"""Resolve caller-defined attachment points and grip parameters."""
from __future__ import annotations

from copy import deepcopy
import math
from typing import Any, Mapping, Sequence

from .armour_fit import slot_position


def vector3(value: Sequence[float], name: str) -> list[float]:
    """Validate a finite position, offset or Euler rotation."""
    try:
        if isinstance(value, (str, bytes)) or len(value) != 3:
            raise ValueError
        result = [float(v) for v in value]
        if not all(math.isfinite(v) for v in result):
            raise ValueError
        return result
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must contain three finite numbers") from exc


def sockets_for(
    landmarks: dict[str, Any], *, body_id: str,
    definitions: Mapping[str, Mapping[str, Any]],
    body_origin: Sequence[float] | None = None,
    slot_definitions: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Resolve explicit parent-local ``at`` or a measured ``slot``.

    Offsets are in the body's axes (metres). ``bone`` is an optional hint,
    not an automatic bone binding. No sockets are added implicitly.
    """
    records = []
    for name, row in definitions.items():
        if not isinstance(name, str) or not name:
            raise ValueError("socket ids must be nonempty strings")
        if ("at" in row) == ("slot" in row):
            raise ValueError(f"socket {name!r} needs exactly one of at or slot")
        offset = vector3(row.get("offset", (0, 0, 0)), f"{name}.offset")
        if "at" in row:
            at = vector3(row["at"], f"{name}.at")
            at = [a + b for a, b in zip(at, offset)]
        else:
            at = slot_position(row["slot"], landmarks, side=row.get("side"),
                               offset=offset, body_origin=body_origin,
                               slots=slot_definitions)
        at = vector3(at, f"{name}.at")
        record = {"id": name, "slot": row.get("slot"), "side": row.get("side"),
                  "parent": body_id, "at": [round(v, 6) for v in at],
                  "rotation": [0.0, 0.0, 0.0]}
        if "bone" in row:
            if row["bone"] is not None and not isinstance(row["bone"], str):
                raise ValueError(f"{name}.bone must be a string or None")
            record["bone"] = row["bone"]
        records.append(record)
    return records


def grip_for(kind: str, *, templates: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Copy a caller-supplied grip template; unknown names fail explicitly."""
    if kind not in templates:
        raise ValueError(f"unknown grip template {kind!r}; configured: {sorted(templates)}")
    return deepcopy(dict(templates[kind]))
