"""
operators/gen_3d_scene/metrics/

Score a generated scene mesh, reading artifacts only.

The metrics are chosen to make mesh continuity measurable rather than a matter
of eyeballing a viewport. Three of them target the failure modes that
`funcs/points_to_mesh.py` exists to fix:

  * `boundary_edge_ratio` — share of edges used by exactly one face. Every hole,
    crack and serrated border contributes; a frame's outer rim sets an
    irreducible floor, so compare runs against each other rather than zero. This
    is the number that moves when the pinhole and all-four-corners fixes work.
  * `largest_component_face_ratio` — how much of the mesh hangs together as one
    surface. Fragmentation shows up here.
  * `stretch_p99` — 99th-percentile edge length over the median. Rises when
    stretched triangles bridge occlusion boundaries.

Usage:
    from operators.gen_3d_scene.metrics import evaluate
    scores = evaluate(result, task)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

__all__ = ["evaluate"]

#: Every metric this module can report, so a failed run still returns a full row.
METRIC_NAMES = (
    "num_vertices",
    "num_faces",
    "num_components",
    "boundary_edge_ratio",
    "largest_component_face_ratio",
    "stretch_p99",
    "surface_area",
)


def evaluate(result: dict, task: dict) -> dict[str, Any]:
    """
    Score the mesh produced by `Gen3DSceneOperator`.

    Args:
        result: The operator's return dict. Only `glb_path` is read.
        task: The original task dict. Unused today, accepted for interface
            consistency and future reference-based metrics.

    Returns:
        Flat, JSON-serializable metrics. On a missing or unreadable artifact
        every value is `0.0` and an `"error"` key explains why.
    """
    glb_path = result.get("glb_path")
    if not glb_path or not Path(glb_path).is_file():
        return _failed(f"glb_path missing or not a file: {glb_path!r}")

    try:
        import numpy as np
        import trimesh

        loaded = trimesh.load(glb_path, force="mesh", process=False)
    except Exception as exc:  # noqa: BLE001 - a bad artifact is a score, not a crash
        return _failed(f"could not load {glb_path}: {exc}")

    faces = np.asarray(loaded.faces)
    vertices = np.asarray(loaded.vertices)
    if not len(faces):
        return _failed(f"{glb_path} contains no faces")

    components = loaded.split(only_watertight=False)
    largest = max((len(part.faces) for part in components), default=len(faces))

    return {
        "num_vertices": float(len(vertices)),
        "num_faces": float(len(faces)),
        "num_components": float(max(len(components), 1)),
        "boundary_edge_ratio": _boundary_edge_ratio(faces, np),
        "largest_component_face_ratio": float(largest / len(faces)),
        "stretch_p99": _stretch_p99(vertices, faces, np),
        "surface_area": float(loaded.area),
    }


def _boundary_edge_ratio(faces, np) -> float:
    """Share of undirected edges referenced by exactly one face."""
    edges = np.sort(
        np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]]),
        axis=1,
    )
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return float((counts == 1).sum() / len(counts))


def _stretch_p99(vertices, faces, np) -> float:
    """99th-percentile edge length divided by the median, or 0 when degenerate."""
    corner = vertices[faces]
    lengths = np.linalg.norm(corner[:, [1, 2, 0]] - corner, axis=-1).ravel()
    lengths = lengths[lengths > 0]
    if not len(lengths):
        return 0.0
    median = float(np.median(lengths))
    if median <= 0:
        return 0.0
    return float(np.percentile(lengths, 99) / median)


def _failed(message: str) -> dict[str, Any]:
    """A full metric row of zeros plus the reason, per the metrics contract."""
    scores: dict[str, Any] = {name: 0.0 for name in METRIC_NAMES}
    scores["error"] = message
    return scores
