"""Extract mesh cross sections and sample interior candidates for joint placement."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .mesh import BodyFrame, CreatureMesh

@dataclass
class Section:
    segments: np.ndarray
    loops: list[np.ndarray]
    open_components: int = 0

    def contains(self, points: np.ndarray) -> np.ndarray:
        p = np.asarray(points, float).reshape(-1, 2)
        inside = np.zeros(len(p), bool)
        for loop in self.loops:
            a, b = (loop, np.roll(loop, -1, axis=0))
            dy = b[:, 1] - a[:, 1]
            crossing = (a[None, :, 1] > p[:, None, 1]) != (b[None, :, 1] > p[:, None, 1])
            x = a[None, :, 0] + (p[:, None, 1] - a[None, :, 1]) * (b - a)[None, :, 0] / np.where(dy != 0, dy, 1.0)
            inside ^= np.count_nonzero(crossing & (p[:, None, 0] < x), axis=1) % 2 == 1
        return inside

    def clearance(self, points: np.ndarray) -> np.ndarray:
        p = np.asarray(points, float).reshape(-1, 2)
        if not self.loops:
            return np.zeros(len(p))
        segments = np.concatenate([np.stack([v, np.roll(v, -1, axis=0)], axis=1) for v in self.loops])
        a, b = (segments[:, 0], segments[:, 1])
        edge = b - a
        den = np.sum(edge * edge, axis=1)
        result = []
        for block in np.array_split(p, max(1, int(np.ceil(len(p) / 256)))):
            delta = block[:, None] - a
            t = np.clip(np.sum(delta * edge, axis=-1) / np.maximum(den, 1e-30), 0.0, 1.0)
            result.append(np.sqrt(np.min(np.sum((delta - t[..., None] * edge) ** 2, axis=-1), axis=1)))
        return np.concatenate(result)

    def candidates(self, resolution: int=25, keep: int=32, bounds=None):
        'Sample each closed loop separately, so a thin limb is not missed by a grid sized for the trunk bounding box.'
        points = []
        for loop in self.loops:
            lo, hi = (loop.min(0), loop.max(0))
            if bounds is not None:
                lo, hi = (np.maximum(lo, bounds[0]), np.minimum(hi, bounds[1]))
            if np.any(hi <= lo):
                continue
            axes = [np.linspace(lo[i], hi[i], resolution + 2)[1:-1] for i in range(2)]
            grid = np.stack(np.meshgrid(*axes, indexing='ij'), axis=-1).reshape(-1, 2)
            grid = grid[self.contains(grid)]
            if not len(grid):
                continue
            radius = self.clearance(grid)
            order = np.argsort(-radius, kind='stable')[:keep]
            points.append(grid[order])
        if not points:
            return (np.empty((0, 2)), np.empty(0))
        points = np.unique(np.concatenate(points), axis=0)
        return (points, self.clearance(points))

class MeshSections:
    'Axis-aligned slices of a normalised mesh, nudged by a consistent epsilon when the plane passes through a vertex.'

    def __init__(self, vertices, faces):
        self.vertices = np.asarray(vertices, float)
        self.faces = np.asarray(faces, np.int64)
        self.triangles = self.vertices[self.faces]
        self.low = self.triangles.min(axis=1)
        self.high = self.triangles.max(axis=1)
        self.cache = {}

    def section(self, axis: int, offset: float) -> Section:
        key = (axis, float(offset))
        if key in self.cache:
            return self.cache[key]
        coord = self.vertices[:, axis]
        if np.any(np.abs(coord - offset) < 1e-10):
            offset += 1e-09
        tri = self.triangles[(self.low[:, axis] < offset) & (self.high[:, axis] > offset)]
        if not len(tri):
            result = Section(np.empty((0, 2, 2)), [])
            self.cache[key] = result
            return result
        a, b = (tri, np.roll(tri, -1, axis=1))
        da, db = (a[..., axis] - offset, b[..., axis] - offset)
        crossing = (da > 0) != (db > 0)
        rows, edges = np.nonzero(crossing)
        t = da[rows, edges] / (da[rows, edges] - db[rows, edges])
        hit = a[rows, edges] + t[:, None] * (b[rows, edges] - a[rows, edges])
        other = [i for i in range(3) if i != axis]
        segments = hit[:, other].reshape(-1, 2, 2)
        keys = np.round(segments.reshape(-1, 2) / 1e-08).astype(np.int64)
        _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
        vertices = segments.reshape(-1, 2)[first]
        edges = np.unique(np.sort(inverse.reshape(-1, 2), axis=1), axis=0)
        edges = edges[edges[:, 0] != edges[:, 1]]
        adjacency = [set() for _ in vertices]
        for i, j in edges:
            adjacency[i].add(int(j))
            adjacency[j].add(int(i))
        seen, loops, opened = (set(), [], 0)
        for start in range(len(vertices)):
            if start in seen or not adjacency[start]:
                continue
            component, queue = ([], [start])
            seen.add(start)
            while queue:
                i = queue.pop()
                component.append(i)
                for j in sorted(adjacency[i]):
                    if j not in seen:
                        seen.add(j)
                        queue.append(j)
            if any((len(adjacency[i]) != 2 for i in component)):
                opened += 1
                continue
            order, prev, cur = ([], -1, min(component))
            while cur not in order:
                order.append(cur)
                nxt = min(adjacency[cur] - {prev})
                prev, cur = (cur, nxt)
            if len(order) >= 3:
                loops.append(vertices[order])
        result = Section(segments, loops, opened)
        self.cache[key] = result
        return result

def canonical_mesh(mesh: CreatureMesh, up=(0.0, 1.0, 0.0), forward=(0.0, 0.0, 1.0), *, vertical=False):
    'Normalise a mesh to its local bounding box along explicit body axes; equivariant under translation, uniform scaling and rotation of those axes.'
    v = np.asarray(mesh.vertices, float)
    faces = np.asarray(mesh.faces)
    if v.ndim != 2 or v.shape[1] != 3 or len(v) < 4 or (not np.isfinite(v).all()):
        raise ValueError('vertices must be a finite (V,3) array')
    if faces.ndim != 2 or faces.shape[1] != 3 or (not np.issubdtype(faces.dtype, np.integer)) or (not len(faces)) or np.any(faces < 0) or np.any(faces >= len(v)):
        raise ValueError('faces must be valid triangle indices')
    u, f = (np.asarray(up, float), np.asarray(forward, float))
    if u.shape != (3,) or f.shape != (3,) or (not np.isfinite([u, f]).all()) or (np.linalg.norm(u) < 1e-12):
        raise ValueError('up/forward must be finite non-zero 3D vectors')
    u = u / np.linalg.norm(u)
    f = f - np.dot(f, u) * u
    if np.linalg.norm(f) < 1e-12:
        raise ValueError('up/forward must not be collinear')
    f /= np.linalg.norm(f)
    axes = np.stack([np.cross(u, f), u, f])
    local = np.einsum('ij,kj->ik', v - v[0], axes)
    center = (local.min(0) + local.max(0)) * 0.5
    scale = float(np.ptp(local, axis=0).max())
    if scale < 1e-12:
        raise ValueError('mesh scale is degenerate')
    origin = np.einsum('i,ij->j', center, axes) + v[0]
    local = (local - center) / scale
    spine = u if vertical else f
    projection = np.einsum('ij,j->i', v, spine)
    frame = BodyFrame(u, f, axes[0], spine, origin, scale, (float(projection.min()), float(projection.max())))
    return (MeshSections(local, faces), frame, axes)
