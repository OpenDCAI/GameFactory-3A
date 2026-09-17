'Fit a mesh skeleton and serialize retarget-compatible rig and OBJ artifacts.'
from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from . import rigging_utils as branch
from .rigging_utils import mesh as units, templates, checks
from .rigging_utils.fixtures import creature_mesh


RIG_PRESETS = ("biped", "quadruped", "axial")


CREATURES = ("human", "fox", "fish")



MOTION_READY_ARM_JOINTS = 4


def list_presets() -> tuple[str, ...]:
    'Return the available rig preset names.'
    return RIG_PRESETS


def list_creatures() -> tuple[str, ...]:
    'Return the available built-in creature mesh names.'
    return CREATURES


def arm_joint_override(preset: str, joints: int = MOTION_READY_ARM_JOINTS) -> dict:
    """Return a ``limb_groups`` override that widens the arm chains.

    The motion presets need a shoulder-elbow-wrist chain, so a rig meant to be
    animated has to be fitted with wider arms than the preset default.
    """
    from dataclasses import replace

    if preset not in templates.RIG_PRESETS:
        raise ValueError(
            f"Unknown rig preset {preset!r}. Available: "
            + ", ".join(sorted(templates.RIG_PRESETS))
        )
    if joints < 2:
        raise ValueError(f"arm chains need at least 2 joints, got {joints}")
    groups = templates.RIG_PRESETS[preset]["limb_groups"]
    arms = [group for group in groups if group.name == "arm"]
    if not arms:
        raise ValueError(f"rig preset {preset!r} has no arm limb group")
    return {
        "limb_groups": tuple(
            replace(group, joints=joints) if group.name == "arm" else group
            for group in groups
        )
    }


def fit_skeleton(
    mesh: Any,
    *,
    preset: str = "biped",
    motion_ready: bool = True,
    up: Iterable[float] = (0.0, 1.0, 0.0),
    forward: Iterable[float] = (0.0, 0.0, 1.0),
    **overrides: Any,
) -> Any:
    """Fit a skeleton to ``mesh`` and return a ``RigResult``.

    Joints are fitted to real mesh cross sections. ``motion_ready`` widens the
    arm chains to :data:`MOTION_READY_ARM_JOINTS` so the result can drive the
    motion presets; an explicit ``limb_groups`` override wins.
    """
    if preset not in RIG_PRESETS:
        raise ValueError(
            f"Unknown rig preset {preset!r}. Available: " + ", ".join(RIG_PRESETS)
        )
    if type(motion_ready) is not bool:
        raise ValueError("motion_ready must be a boolean")
    if motion_ready and preset == "biped" and "limb_groups" not in overrides:
        overrides = {**arm_joint_override(preset), **overrides}
    return branch.rig_skeleton(
        mesh,
        preset,
        up=tuple(float(v) for v in up),
        forward=tuple(float(v) for v in forward),
        **overrides,
    )


def evaluate_skeleton(mesh: Any, rig: Any) -> dict:
    'Return rig quality findings for ``rig`` against ``mesh``.'
    return checks.evaluate_rig(mesh, rig)


def to_motion_template(rig: Any, *, name: str | None = None) -> Any:
    'Convert a ``RigResult`` into a motion ``SkeletonTemplate``.'
    return branch.to_motion_template(rig, name=name)


def mesh_from_arrays(
    vertices: np.ndarray,
    faces: np.ndarray,
    *,
    name: str = "input",
) -> Any:
    'Build a ``CreatureMesh`` from raw arrays, for meshes loaded elsewhere.'
    verts = np.asarray(vertices, dtype=np.float64)
    tris = np.asarray(faces)
    if verts.ndim != 2 or verts.shape[1] != 3 or not len(verts) or not np.isfinite(verts).all():
        raise ValueError(f"vertices must be non-empty finite (V,3), got {verts.shape}")
    if tris.ndim != 2 or tris.shape[1] != 3 or not len(tris) or tris.dtype.kind not in "iu":
        raise ValueError(f"faces must be non-empty integer (F,3) triangles, got {tris.shape}")
    if tris.min() < 0 or tris.max() >= len(verts):
        raise ValueError("faces index vertices outside the vertex array")
    tris = tris.astype(np.int64)
    return units.CreatureMesh(
        name=str(name),
        vertices=verts,
        faces=tris,
        weld_map=np.arange(len(verts)),
        source="arrays",
    )


def rig_to_text(
    rig: Any,
    *,
    skin: Any | None = None,
    precision: int = 6,
) -> str:
    'Serialise a ``RigResult`` as Puppeteer-style rig text.'
    from .bvh import _joint_names, validate_hierarchy

    parents, joints = validate_hierarchy(rig.parents, rig.joints)
    names = _joint_names(rig, len(parents))
    roots = np.flatnonzero(parents == -1)
    if type(precision) is not int or not 6 <= precision <= 17:
        raise ValueError("precision must be an integer between 6 and 17")
    fmt = f"{{:.{precision}f}}"
    lines = [
        f"joints {name} " + " ".join(fmt.format(v) for v in joints[index])
        for index, name in enumerate(names)
    ]
    lines.append(f"root {names[int(roots[0])]}")
    lines += [
        f"hier {names[int(parent)]} {names[child]}"
        for child, parent in enumerate(parents)
        if parent >= 0
    ]

    if skin is not None:
        weights = np.asarray(skin.weights, dtype=np.float64)
        if weights.ndim != 2 or weights.shape[1] != len(names):
            raise ValueError(
                f"skin weights must be (V,{len(names)}), got {weights.shape}"
            )
        if (not len(weights) or not np.isfinite(weights).all() or np.any(weights < 0)
                or not np.allclose(weights.sum(axis=1), 1, atol=1e-6, rtol=0)
                or np.any(np.count_nonzero(weights > 0, axis=1) > 4)):
            raise ValueError("skin weights must be finite, non-negative, normalized and at most four per vertex")
        skin_names = _joint_names(skin, len(names))
        if skin_names != names:
            raise ValueError(
                "skin joint order does not match the rig; weights would be "
                "assigned to the wrong joints"
            )
        for vertex, row in enumerate(weights):
            used = np.flatnonzero(row > 0.0)
            if not len(used):
                continue
            pairs = " ".join(
                f"{names[j]} " + fmt.format(row[j]) for j in used
            )
            lines.append(f"skin {vertex} {pairs}")
    return "\n".join(lines) + "\n"


def skeleton_to_text(rig: Any) -> str:
    'Serialise joint positions, root and hierarchy in Puppeteer skeleton format.'
    return rig_to_text(rig)


def mesh_to_obj(mesh: Any) -> str:
    'Serialise a ``CreatureMesh`` as OBJ, preserving vertex order.'
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    lines = [f"v {x:.6f} {y:.6f} {z:.6f}" for x, y, z in vertices]
    lines += [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in faces]
    return "\n".join(lines) + "\n"


__all__ = [
    "CREATURES",
    "MOTION_READY_ARM_JOINTS",
    "RIG_PRESETS",
    "arm_joint_override",
    "creature_mesh",
    "evaluate_skeleton",
    "fit_skeleton",
    "list_creatures",
    "list_presets",
    "mesh_from_arrays",
    "mesh_to_obj",
    "rig_to_text",
    "skeleton_to_text",
    "to_motion_template",
]
