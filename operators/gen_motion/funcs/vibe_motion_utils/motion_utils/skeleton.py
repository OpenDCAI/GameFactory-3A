"""Arbitrary named body parts with validated categories and original node indices."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from numpy.typing import NDArray

CATEGORIES = frozenset(('head', 'trunk', 'upper_limb', 'lower_limb', 'tail'))
CATEGORY_ALIASES = {'\u5934': 'head', '\u8eaf\u5e72': 'trunk', '\u4e0a\u534a\u80a2\u4f53': 'upper_limb',
                    '\u4e0b\u534a\u80a2\u4f53': 'lower_limb', '\u5c3e\u90e8': 'tail'}


def category(value):
    if not isinstance(value, str):
        raise ValueError('Body category must be a string')
    value = CATEGORY_ALIASES.get(value, value)
    if value not in CATEGORIES:
        raise ValueError(f'Unknown body category {value!r}; expected {sorted(CATEGORIES)}')
    return value


@dataclass(frozen=True)
class BodyPart:
    category: str
    joints: tuple[int, ...]
    side: str | None = None


@dataclass
class BodyBlocks:
    root: int
    parts: dict[str, BodyPart]
    remainder: tuple[int, ...]
    frame: NDArray[np.float64]
    rest: NDArray[np.float64]
    height: float
    notes: list[str]

    def select(self, kind):
        return {key: part.joints for key, part in self.parts.items() if part.category == kind}

    @property
    def trunk(self):
        return tuple(j for js in self.select('trunk').values() for j in js)

    @property
    def head(self):
        return tuple(j for js in self.select('head').values() for j in js)

    @property
    def arms(self):
        return self.select('upper_limb')

    @property
    def legs(self):
        return self.select('lower_limb')

    def length(self, key):
        js = self.parts[key].joints[:3]
        return float(np.linalg.norm(np.diff(self.rest[list(js)], axis=0), axis=1).sum())

    @property
    def scale(self):
        return max(float(np.ptp(self.rest, axis=0).max()), 1e-6)

    @property
    def leg_length(self):
        values = [self.length(k) for k in self.legs if self.length(k) > 1e-8]
        return float(np.mean(values)) if values else self.scale

    @property
    def arm_length(self):
        values = [self.length(k) for k in self.arms if self.length(k) > 1e-8]
        return float(np.mean(values)) if values else self.scale

    def as_dict(self, names):
        def describe(ids):
            return [{'node': int(j), 'name': names[j]} for j in ids]
        return {'root': describe([self.root])[0],
                'parts': {k: {'category': p.category, 'side': p.side, 'joints': describe(p.joints)} for k, p in self.parts.items()},
                'trunk': describe(self.trunk), 'head': describe(self.head),
                'arms': {k: describe(js) for k, js in self.arms.items()},
                'legs': {k: describe(js) for k, js in self.legs.items()},
                'remainder': describe(self.remainder), 'body_frame_columns': self.frame.tolist(),
                'leg_length': self.leg_length, 'arm_length': self.arm_length,
                'height': self.height, 'notes': self.notes}


def partition_skeleton(parents, rest_world, joints, names, mapping=None):
    """Bind arbitrary part IDs to chains; category and chain validity are independent.

    mapping={parts: {any_id: {category, joints, side?}}, forward: [x,0,z]?}.
    Multiple trunk/head/tail parts and any number of limbs are allowed. Unknown
    unbound nodes are reported, never silently interpreted as additional legs.
    """
    parents, world = np.asarray(parents, int), np.asarray(rest_world, float)
    joints = list(map(int, joints))
    n = len(parents)
    if parents.shape != (n,) or world.shape != (n, 4, 4) or len(names) != n or not np.isfinite(world).all():
        raise ValueError('Expected finite world matrices (N,4,4), parents and names')
    if not joints or len(set(joints)) != len(joints) or any(j < 0 or j >= n for j in joints):
        raise ValueError('Invalid skeleton joint indices')
    if np.any((parents < -1) | (parents >= n)):
        raise ValueError('Invalid parent indices')
    for j in range(n):
        seen = set()
        while j >= 0:
            if j in seen:
                raise ValueError('Cyclic skeleton')
            seen.add(j)
            j = int(parents[j])
    joint_set = set(joints)
    roots = [j for j in joints if parents[j] not in joint_set]
    if len(roots) != 1:
        raise ValueError('Expected a connected single-root skeleton')
    root = roots[0]
    if not isinstance(mapping, dict) or set(mapping)-{'parts', 'root', 'forward'} or not mapping.get('parts'):
        raise ValueError('Explicit named parts mapping is required')
    def index(value):
        if isinstance(value, str):
            found = [j for j in joints if names[j] == value]
            if len(found) != 1:
                raise ValueError(f'Ambiguous or missing joint: {value}')
            return found[0]
        if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or value not in joint_set:
            raise ValueError(f'Invalid joint index: {value}')
        return int(value)
    if index(mapping.get('root', root)) != root:
        raise ValueError('Mapping root differs from hierarchy root')
    parts, used = {}, set()
    for key, spec in mapping['parts'].items():
        if not isinstance(key, str) or not key.strip() or not isinstance(spec, dict):
            raise ValueError('Each body part needs a nonempty string ID and an object')
        if set(spec)-{'category', 'joints', 'side'} or not {'category', 'joints'} <= set(spec):
            raise ValueError(f'Invalid binding fields for {key}')
        kind = category(spec['category'])
        chain = tuple(index(j) for j in spec['joints'])
        if not chain or len(set(chain)) != len(chain) or any(parents[b] != a for a, b in zip(chain, chain[1:])):
            raise ValueError(f'{key}: expected a nonempty contiguous chain')
        if used.intersection(chain):
            raise ValueError(f'{key}: overlapping body parts')
        side = spec.get('side')
        if side not in (None, 'L', 'R'):
            raise ValueError(f'{key}: side, when provided, must be L/R')
        used.update(chain)
        parts[key] = BodyPart(kind, chain, side)
    if not any(p.category == 'trunk' and root in p.joints for p in parts.values()):
        raise ValueError('Skeleton root must belong to a trunk part')
    rest = world[:, :3, 3]
    if 'forward' in mapping:
        forward = np.asarray(mapping['forward'], float)
    else:
        reference = [p for p in parts.values() if p.category == 'lower_limb']
        if not {'L', 'R'} <= {p.side for p in reference}:
            reference = list(parts.values())
        left = [rest[p.joints[0]] for p in reference if p.side == 'L']
        right = [rest[p.joints[0]] for p in reference if p.side == 'R']
        if not left or not right:
            raise ValueError('Provide forward when no bilateral reference is available')
        lateral = np.mean(left, axis=0)-np.mean(right, axis=0)
        forward = np.cross(lateral, [0., 1., 0.])
    if forward.shape != (3,) or not np.isfinite(forward).all() or abs(forward[1]) > 1e-5 or np.linalg.norm(forward) < 1e-6:
        raise ValueError('forward must be a finite nonzero horizontal 3D vector (Y-up)')
    forward = forward/np.linalg.norm(forward)
    up = np.array([0., 1., 0.])
    frame = np.column_stack([np.cross(up, forward), up, forward])
    remainder = tuple(j for j in joints if j not in used)
    notes = ['Explicit category bindings; body part IDs have no semantic restrictions']
    if remainder:
        notes.append(f'{len(remainder)} unbound nodes retain their local rest pose')
    return BodyBlocks(root, parts, remainder, frame, rest.copy(), float(np.ptp(rest[joints, 1])), notes)


class SkeletonRig:
    """Mesh-free adapter; positions-only input uses identity local bind rotations."""

    def __init__(self, parents, rest, names=None):
        self.parents = np.asarray(parents, int).copy()
        rest = np.asarray(rest, float)
        n = len(self.parents)
        if self.parents.shape != (n,) or rest.shape != (n, 3) or not np.isfinite(rest).all():
            raise ValueError('Expected parents (J,) and finite world rest positions (J,3)')
        if n == 0 or np.any((self.parents < -1) | (self.parents >= n)):
            raise ValueError('Invalid skeleton parents')
        roots = np.flatnonzero(self.parents == -1)
        if len(roots) != 1:
            raise ValueError('Expected a single skeleton root')
        self.order = [int(roots[0])]
        for joint in self.order:
            self.order.extend(map(int, np.flatnonzero(self.parents == joint)))
            if len(self.order) > n:
                raise ValueError('Cyclic skeleton')
        if len(self.order) != n:
            raise ValueError('Disconnected or cyclic skeleton')
        names = list(names) if names is not None else [f'joint_{i}' for i in range(n)]
        if len(names) != n:
            raise ValueError('Joint name count mismatch')
        self.nodes = [{'name': name} for name in names]
        self.joints = np.arange(n)
        self.local_q = np.tile([0., 0., 0., 1.], (n, 1))
        self.local_t = rest.copy()
        for j in self.order:
            if self.parents[j] >= 0:
                self.local_t[j] -= rest[self.parents[j]]
        self.rest_world = self.fk()

    def fk(self, quats=None, translations=None):
        from scipy.spatial.transform import Rotation
        q = self.local_q if quats is None else np.asarray(quats, float)
        t = self.local_t if translations is None else np.asarray(translations, float)
        n, batch = len(self.nodes), q.shape[:-2]
        if q.shape[-2:] != (n, 4) or t.shape != batch+(n, 3):
            raise ValueError('Pose array shape mismatch')
        if not np.isfinite(q).all() or not np.isfinite(t).all():
            raise ValueError('Non-finite pose')
        world = np.broadcast_to(np.eye(4), batch+(n, 4, 4)).copy()
        world[..., :3, :3] = Rotation.from_quat(q.reshape(-1, 4)).as_matrix().reshape(batch+(n, 3, 3))
        world[..., :3, 3] = t
        for j in self.order:
            if self.parents[j] >= 0:
                world[..., j, :, :] = world[..., self.parents[j], :, :] @ world[..., j, :, :]
        return world
