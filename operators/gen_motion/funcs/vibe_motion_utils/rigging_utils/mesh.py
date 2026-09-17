from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import numpy as np

@dataclass(frozen=True)
class CreatureMesh:
    'A creature mesh with welded vertices, whose topology supports graph smoothing.'
    name: str
    vertices: np.ndarray
    faces: np.ndarray
    weld_map: np.ndarray
    source: Path | None = None

    @property
    def num_vertices(self) -> int:
        return int(len(self.vertices))

    @property
    def bounds(self) -> np.ndarray:
        '``(2,3)`` bounding box.'
        return np.stack([self.vertices.min(0), self.vertices.max(0)])

    @property
    def extent(self) -> np.ndarray:
        lo, hi = self.bounds
        return hi - lo

    @property
    def scale(self) -> float:
        'Return the longest bounding-box dimension.'
        return float(self.extent.max())

    @property
    def center(self) -> np.ndarray:
        return self.bounds.mean(0)

    def adjacency(self) -> list[np.ndarray]:
        'Per-vertex neighbour table, undirected and de-duplicated.'
        return vertex_adjacency(self.faces, self.num_vertices)

    def face_areas(self) -> np.ndarray:
        a, b, c = (self.vertices[self.faces[:, i]] for i in range(3))
        return 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)

    def vertex_areas(self) -> np.ndarray:
        'Barycentric area weights, used for area-weighted statistics.'
        out = np.zeros(self.num_vertices)
        area = self.face_areas() / 3.0
        for i in range(3):
            np.add.at(out, self.faces[:, i], area)
        return out

@dataclass(frozen=True)
class BodyFrame:
    'Body axes and spine direction of one mesh.'
    up: np.ndarray
    forward: np.ndarray
    left: np.ndarray
    spine: np.ndarray
    origin: np.ndarray
    scale: float
    span: tuple[float, float]

    def project(self, points: np.ndarray) -> np.ndarray:
        'Project points onto the spine axis, normalised to ``[0,1]`` (0 = tail end, 1 = head end).'
        t = np.asarray(points, float) @ self.spine
        lo, hi = self.span
        return (t - lo) / max(hi - lo, 1e-12)

    def lateral(self, points: np.ndarray) -> np.ndarray:
        'Signed lateral offset of points, positive towards the creature left side.'
        return (np.asarray(points, float) - self.origin) @ self.left

def point_to_segment_distance(points: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    'Distance ``(N,)`` from points to segment ``ab``. A zero-length segment falls back to point distance.'
    p = np.asarray(points, float)
    a = np.asarray(a, float)
    ab = np.asarray(b, float) - a
    denom = float(ab @ ab)
    if denom < 1e-18:
        return np.linalg.norm(p - a, axis=-1)
    t = np.clip(np.einsum('ij,j->i', p - a, ab) / denom, 0.0, 1.0)
    return np.linalg.norm(p - (a + t[:, None] * ab), axis=-1)

def vertex_adjacency(faces: np.ndarray, num_vertices: int) -> list[np.ndarray]:
    'Build the per-vertex neighbour table from triangle faces.'
    tri = np.asarray(faces, np.int64).reshape(-1, 3)
    edges = np.concatenate([tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]])
    both = np.concatenate([edges, edges[:, ::-1]])
    order = np.argsort(both[:, 0], kind='stable')
    both = both[order]
    starts = np.searchsorted(both[:, 0], np.arange(num_vertices))
    ends = np.searchsorted(both[:, 0], np.arange(num_vertices), side='right')
    return [np.unique(both[s:e, 1]) for s, e in zip(starts, ends)]
