"""Public calibrated skeleton fitting, enclosure diagnostics and artifact serialization."""
from __future__ import annotations
from typing import Any
import numpy as np
from . import rigging_utils as branch
from .rigging_utils import mesh as units, checks
from .rigging_utils.templates import finite_number, integer


def fit_skeleton(mesh: Any, *, config: dict) -> Any:
    """Delegate calibrated cameras, 2D observations, topology and fit windows; no legacy tracing."""
    return branch.rig_skeleton(mesh, config=config)


def evaluate_skeleton(mesh: Any, rig: Any, *, config: dict) -> dict:
    """Report solid certificates separately from open-mesh enclosure and parity evidence."""
    return checks.evaluate_rig(mesh, rig, config=config)


def mesh_from_arrays(vertices: np.ndarray, faces: np.ndarray, *, name: str) -> Any:
    verts = np.asarray(vertices, dtype=np.float64)
    tris = np.asarray(faces)
    if verts.ndim != 2 or verts.shape[1] != 3 or not len(verts) or not np.isfinite(verts).all():
        raise ValueError(f'vertices must be non-empty finite (V,3), got {verts.shape}')
    if tris.ndim != 2 or tris.shape[1] != 3 or not len(tris) or tris.dtype.kind not in 'iu':
        raise ValueError(f'faces must be non-empty integer (F,3) triangles, got {tris.shape}')
    if tris.min() < 0 or tris.max() >= len(verts):
        raise ValueError('faces index vertices outside the vertex array')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('mesh name must be a non-empty string')
    return units.CreatureMesh(name=name, vertices=verts, faces=tris.astype(np.int64),
                              weld_map=np.arange(len(verts)), source='arrays')


def rig_to_text(rig: Any, *, precision: int, sum_tolerance: float,
                max_influences: int, skin: Any | None = None) -> str:
    """Serialize a hierarchy and optional weights with explicit output policy."""
    from .bvh import _joint_names, validate_hierarchy

    parents, joints = validate_hierarchy(rig.parents, rig.joints)
    names = _joint_names(rig, len(parents))
    roots = np.flatnonzero(parents == -1)
    integer(precision, 'precision', 0)
    integer(max_influences, 'max_influences', 1)
    finite_number(sum_tolerance, 'sum_tolerance')
    fmt = f'{{:.{precision}f}}'
    lines = [f'joints {name} ' + ' '.join(fmt.format(v) for v in joints[index])
             for index, name in enumerate(names)]
    lines.append(f'root {names[int(roots[0])]}')
    lines += [f'hier {names[int(parent)]} {names[child]}'
              for child, parent in enumerate(parents) if parent >= 0]
    if skin is not None:
        weights = np.asarray(skin.weights, dtype=np.float64)
        if weights.ndim != 2 or weights.shape[1] != len(names):
            raise ValueError(f'skin weights must be (V,{len(names)}), got {weights.shape}')
        if (not len(weights) or not np.isfinite(weights).all() or np.any(weights < 0)
                or not np.allclose(weights.sum(axis=1), 1, atol=sum_tolerance, rtol=0)
                or np.any(np.count_nonzero(weights > 0, axis=1) > max_influences)):
            raise ValueError('skin weights must be finite, non-negative, normalized and within the influence limit')
        if _joint_names(skin, len(names)) != names:
            raise ValueError('skin joint order does not match the rig')
        for vertex, row in enumerate(weights):
            used = np.flatnonzero(row > 0.0)
            pairs = ' '.join(f'{names[j]} ' + fmt.format(row[j]) for j in used)
            lines.append(f'skin {vertex} {pairs}')
    return '\n'.join(lines) + '\n'


def skeleton_to_text(rig: Any, *, precision: int) -> str:
    """Serialize only the hierarchy; weight validation is not applicable."""
    from .bvh import _joint_names, validate_hierarchy

    parents, joints = validate_hierarchy(rig.parents, rig.joints)
    names = _joint_names(rig, len(parents))
    integer(precision, 'precision', 0)
    fmt = f'{{:.{precision}f}}'
    lines = [f'joints {name} ' + ' '.join(fmt.format(v) for v in joints[index])
             for index, name in enumerate(names)]
    lines.append(f'root {names[int(np.flatnonzero(parents == -1)[0])]}')
    lines += [f'hier {names[int(parent)]} {names[child]}'
              for child, parent in enumerate(parents) if parent >= 0]
    return '\n'.join(lines) + '\n'


def mesh_to_obj(mesh: Any, *, precision: int) -> str:
    """Serialize OBJ triangles in the input vertex order."""
    integer(precision, 'precision', 0)
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    lines = [f'v {x:.{precision}f} {y:.{precision}f} {z:.{precision}f}' for x, y, z in vertices]
    lines += [f'f {a + 1} {b + 1} {c + 1}' for a, b, c in faces]
    return '\n'.join(lines) + '\n'


__all__ = ['evaluate_skeleton', 'fit_skeleton', 'mesh_from_arrays', 'mesh_to_obj',
           'rig_to_text', 'skeleton_to_text']
