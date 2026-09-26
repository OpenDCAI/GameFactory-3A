"""Define skeleton templates, motion clips, role planning and quaternion operations."""
from __future__ import annotations
import re
from collections import OrderedDict
from dataclasses import dataclass, field
import numpy as np
QUAT_IDENTITY: np.ndarray = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)

def quat_identity(*shape: int) -> np.ndarray:
    """Array of identity quaternions shaped ``(*shape, 4)``."""
    out = np.zeros((*shape, 4), dtype=np.float32)
    out[..., 0] = 1.0
    return out

def quat_normalize(q: np.ndarray) -> np.ndarray:
    """Normalise each quaternion, falling back to identity on zero length rather than producing nan."""
    q = np.asarray(q, dtype=np.float32)
    n = np.linalg.norm(q, axis=-1, keepdims=True)
    out = np.where(n > 1e-08, q / np.maximum(n, 1e-08), QUAT_IDENTITY)
    return out.astype(np.float32)

def quat_from_axis_angle(axis: np.ndarray, angle: np.ndarray | float) -> np.ndarray:
    """Axis-angle to quaternion, with broadcasting."""
    axis = np.asarray(axis, dtype=np.float32)
    ang = np.asarray(angle, dtype=np.float32)
    n = np.linalg.norm(axis, axis=-1, keepdims=True)
    valid = n > 1e-08
    unit = np.divide(axis, n, out=np.zeros_like(axis), where=valid)
    half = (ang * 0.5)[..., None] if ang.ndim else np.float32(ang * 0.5)
    if not np.ndim(half):
        half = np.array(half, dtype=np.float32).reshape(1)
    w = np.cos(half)
    xyz = unit * np.sin(half)
    q = np.concatenate([np.broadcast_to(w, xyz[..., :1].shape), xyz], axis=-1)
    return np.where(np.broadcast_to(valid, q.shape), q, QUAT_IDENTITY).astype(np.float32)

def quat_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Quaternion product ``a * b`` satisfying ``R(a*b) = R(a) @ R(b)``, applying b first and then a."""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    aw, ax, ay, az = (a[..., 0], a[..., 1], a[..., 2], a[..., 3])
    bw, bx, by, bz = (b[..., 0], b[..., 1], b[..., 2], b[..., 3])
    return np.stack([aw * bw - ax * bx - ay * by - az * bz, aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx, aw * bz + ax * by - ay * bx + az * bw], axis=-1).astype(np.float32)

def quat_slerp(a: np.ndarray, b: np.ndarray, t: np.ndarray | float) -> np.ndarray:
    """Spherical linear interpolation along the shortest arc."""
    a = quat_normalize(a)
    b = quat_normalize(b)
    t = np.asarray(t, dtype=np.float32)
    if t.ndim < a.ndim - 1:
        t = t.reshape(t.shape + (1,) * (a.ndim - 1 - t.ndim))
    dot = np.sum(a * b, axis=-1, keepdims=True)
    b = np.where(dot < 0.0, -b, b)
    dot = np.abs(dot).clip(-1.0, 1.0)
    omega = np.arccos(dot)
    sin_omega = np.sin(omega)
    tt = t[..., None] if t.ndim == a.ndim - 1 else t
    near = sin_omega < 0.0001
    lin = a * (1.0 - tt) + b * tt
    s0 = np.divide(np.sin((1.0 - tt) * omega), sin_omega, out=np.zeros_like(lin), where=~near)
    s1 = np.divide(np.sin(tt * omega), sin_omega, out=np.zeros_like(lin), where=~near)
    sph = a * s0 + b * s1
    return quat_normalize(np.where(near, lin, sph))

def quat_to_matrix(q: np.ndarray) -> np.ndarray:
    """Quaternion to rotation matrix ``(..., 3, 3)``."""
    q = quat_normalize(q)
    w, x, y, z = (q[..., 0], q[..., 1], q[..., 2], q[..., 3])
    return np.stack([np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], -1), np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], -1), np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1)], axis=-2).astype(np.float32)

def topological_order(parents: np.ndarray) -> np.ndarray:
    """Joint order sorted by hierarchy depth, so a joint's parent is always resolved first."""
    parents = np.asarray(parents, dtype=np.int64)
    depth = np.zeros(len(parents), dtype=np.int64)
    for j in range(len(parents)):
        d, p, guard = (0, int(parents[j]), 0)
        while p >= 0:
            d += 1
            p = int(parents[p])
            guard += 1
            if guard > len(parents):
                raise ValueError(f'joint parents contain a cycle, starting at {j}')
        depth[j] = d
    return np.argsort(depth, kind='stable')

def forward_kinematics(parents: np.ndarray, rest: np.ndarray, quats: np.ndarray, trans: np.ndarray | None=None) -> np.ndarray:
    """Compute global positions from per-joint local rotations."""
    parents = np.asarray(parents, dtype=np.int64)
    rest = np.asarray(rest, dtype=np.float32)
    quats = np.asarray(quats, dtype=np.float32)
    t_num, j_num = (quats.shape[0], quats.shape[1])
    if rest.shape[0] != j_num or parents.shape[0] != j_num:
        raise ValueError(f'joint counts disagree: quats={j_num} rest={rest.shape[0]} parents={parents.shape[0]}')
    rot = quat_to_matrix(quats)
    grot = np.zeros_like(rot)
    pos = np.zeros((t_num, j_num, 3), dtype=np.float32)
    tr = np.zeros((t_num, 3), np.float32) if trans is None else np.asarray(trans, np.float32)
    for j in topological_order(parents):
        j = int(j)
        p = int(parents[j])
        if p < 0:
            grot[:, j] = rot[:, j]
            pos[:, j] = rest[j][None] + tr
        else:
            grot[:, j] = np.einsum('tij,tjk->tik', grot[:, p], rot[:, j])
            offset = rest[j] - rest[p]
            pos[:, j] = pos[:, p] + np.einsum('tij,j->ti', grot[:, p], offset)
    return pos

def global_rotations(parents: np.ndarray, quats: np.ndarray) -> np.ndarray:
    """Per-joint global rotations ``(T,J,3,3)``."""
    parents = np.asarray(parents, dtype=np.int64)
    rot = quat_to_matrix(np.asarray(quats, dtype=np.float32))
    grot = np.zeros_like(rot)
    for j in topological_order(parents):
        j = int(j)
        p = int(parents[j])
        grot[:, j] = rot[:, j] if p < 0 else np.einsum('tij,tjk->tik', grot[:, p], rot[:, j])
    return grot

def bone_lengths(parents: np.ndarray, rest: np.ndarray) -> np.ndarray:
    """Rest-pose bone length ``(J,)`` from each joint to its parent, zero at the root."""
    parents = np.asarray(parents, dtype=np.int64)
    rest = np.asarray(rest, dtype=np.float32)
    out = np.zeros(len(parents), dtype=np.float32)
    for j in range(len(parents)):
        p = int(parents[j])
        if p >= 0:
            out[j] = float(np.linalg.norm(rest[j] - rest[p]))
    return out

@dataclass
class MotionClip:
    """One clip: per-frame, per-joint local rotations plus root translation."""
    template: object
    quats: np.ndarray
    trans: np.ndarray
    fps: float = 30.0
    log: list[str] = field(default_factory=list)

    @classmethod
    def rest_clip(cls, template, num_frames: int, fps: float=30.0) -> 'MotionClip':
        """Build a rest clip with identity rotations and zero translation, as the starting point for stacking primitives."""
        j_num = len(template.parents)
        return cls(template=template, quats=quat_identity(num_frames, j_num), trans=np.zeros((num_frames, 3), dtype=np.float32), fps=fps)

    @property
    def num_frames(self) -> int:
        return int(self.quats.shape[0])

    @property
    def num_joints(self) -> int:
        return int(self.quats.shape[1])

    @property
    def duration(self) -> float:
        """Duration in seconds."""
        return self.num_frames / max(self.fps, 1e-08)

    def copy(self) -> 'MotionClip':
        return MotionClip(template=self.template, quats=self.quats.copy(), trans=self.trans.copy(), fps=self.fps, log=list(self.log))

    def rotate(self, frames: np.ndarray, joint: int, quat: np.ndarray) -> None:
        """Compose ``quat`` onto the existing local rotation of ``joint`` on the given ``frames``."""
        if len(frames) == 0:
            return
        q = np.asarray(quat, dtype=np.float32)
        if q.ndim == 1:
            q = np.broadcast_to(q, (len(frames), 4))
        self.quats[frames, joint] = quat_normalize(quat_mul(q, self.quats[frames, joint]))

    def translate(self, frames: np.ndarray, delta: np.ndarray) -> None:
        """Accumulate root translation on the given ``frames``."""
        if len(frames) == 0:
            return
        self.trans[frames] += np.asarray(delta, dtype=np.float32)

    def positions(self) -> np.ndarray:
        """Forward kinematics to global joint positions ``(T,J,3)``."""
        return forward_kinematics(self.template.parents, self.template.rest, self.quats, self.trans)
MORPHOLOGIES: tuple[str, ...] = ('serpentine', 'biped', 'biped_armed', 'quadruped', 'octopod', 'myriapod', 'unknown')
_UP = np.array([0.0, 1.0, 0.0], dtype=np.float32)
_RIGHT = np.array([1.0, 0.0, 0.0], dtype=np.float32)
_FWD = np.array([0.0, 0.0, 1.0], dtype=np.float32)

@dataclass(frozen=True)
class SkeletonTemplate:
    """Structural definition of one skeleton: rest pose plus semantic chains."""
    name: str
    morphology: str
    joint_names: list[str]
    parents: np.ndarray
    rest: np.ndarray
    roles: 'OrderedDict[str, list[int]]'
    has_weapon: bool = False

    @property
    def num_joints(self) -> int:
        return len(self.parents)

    @property
    def limb_roles(self) -> list[str]:
        """All limb role names, in the natural order of ``limb.{side}.{i}``."""
        return [r for r in self.roles if r.startswith('limb.')]

    @property
    def weapon_roles(self) -> list[str]:
        return [r for r in self.roles if r.startswith('weapon.')]

    @property
    def height(self) -> float:
        """Y extent of the rest pose. Informational only: it is not a threshold basis, since a resting serpentine is flat."""
        return float(self.rest[:, 1].max() - self.rest[:, 1].min())

    @property
    def scale(self) -> float:
        """Characteristic scale of the skeleton: the longest bounding box edge."""
        span = self.rest.max(axis=0) - self.rest.min(axis=0)
        return float(max(span.max(), 1e-06))

    @property
    def ground(self) -> float:
        """Y of the lowest rest-pose point, used as the ground height."""
        return float(self.rest[:, 1].min())

    def role_of_joint(self) -> np.ndarray:
        """``(J,)`` role index owning each joint, in ``roles`` order, or -1 when unassigned."""
        out = np.full(self.num_joints, -1, dtype=np.int64)
        for i, joints in enumerate(self.roles.values()):
            for j in joints:
                out[j] = i
        return out

def _chain(names: list[str], parents: list[int], rest: list[list[float]], start_parent: int, points: list[tuple[str, list[float]]]) -> list[int]:
    """Attach a sequence of (name, position) pairs as a chain and return the new joint indices."""
    idxs: list[int] = []
    prev = start_parent
    for nm, pt in points:
        names.append(nm)
        parents.append(prev)
        rest.append(pt)
        prev = len(names) - 1
        idxs.append(prev)
    return idxs

def _build_serpentine(num_segments: int=12, length: float=2.4) -> SkeletonTemplate:
    """Limbless segmented body: one spine chain plus a head, for snakes, dragons or a single tentacle."""
    names: list[str] = ['root']
    parents: list[int] = [-1]
    rest: list[list[float]] = [[0.0, 0.12, 0.0]]
    step = length / max(num_segments, 1)
    trunk = [0]
    trunk += _chain(names, parents, rest, 0, [(f'seg{i + 1}', [0.0, 0.12, -step * (i + 1)]) for i in range(num_segments)])
    head = _chain(names, parents, rest, 0, [('neck', [0.0, 0.14, step]), ('head', [0.0, 0.16, step * 2])])
    roles: 'OrderedDict[str, list[int]]' = OrderedDict()
    roles['trunk'] = trunk
    roles['head'] = head
    return SkeletonTemplate(name=f'serpentine{num_segments}', morphology='serpentine', joint_names=names, parents=np.asarray(parents, np.int64), rest=np.asarray(rest, np.float32), roles=roles)

def _leg(names: list[str], parents: list[int], rest: list[list[float]], host: int, hip: list[float], *, seg: list[float], tag: str) -> list[int]:
    """Attach a three-segment leg, hip to knee to ankle to toe; ``seg`` gives the per-segment offset."""
    x, y, z = hip
    pts = [(f'{tag}_hip', [x, y, z])]
    cy, cz = (y, z)
    for i, dy in enumerate(seg):
        cy += dy
        pts.append((f'{tag}_seg{i + 1}', [x, cy, cz]))
    pts.append((f'{tag}_toe', [x, cy - 0.04, cz + 0.1]))
    return _chain(names, parents, rest, host, pts)

def _build_biped(with_arms: bool) -> SkeletonTemplate:
    """Biped, with or without arms."""
    if with_arms:
        names = ['hips', 'L_hip', 'R_hip', 'spine1', 'L_knee', 'R_knee', 'spine2', 'L_ankle', 'R_ankle', 'spine3', 'L_foot', 'R_foot', 'neck', 'L_clavicle', 'R_clavicle', 'head', 'L_shoulder', 'R_shoulder', 'L_elbow', 'R_elbow', 'L_wrist', 'R_wrist']
        parents = np.array([-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19], dtype=np.int64)
        roles: 'OrderedDict[str, list[int]]' = OrderedDict()
        roles['trunk'] = [0, 3, 6, 9]
        roles['head'] = [12, 15]
        roles['limb.L.0'] = [13, 16, 18, 20]
        roles['limb.R.0'] = [14, 17, 19, 21]
        roles['limb.L.1'] = [1, 4, 7, 10]
        roles['limb.R.1'] = [2, 5, 8, 11]
        return SkeletonTemplate(name='biped_armed', morphology='biped_armed', joint_names=names, parents=parents, rest=REFERENCE_TPOSE.copy(), roles=roles)
    names, parents, rest = (['hips'], [-1], [[0.0, 0.8, 0.0]])
    trunk = [0] + _chain(names, parents, rest, 0, [('spine1', [0.0, 0.86, 0.22]), ('spine2', [0.0, 0.9, 0.44]), ('chest', [0.0, 0.92, 0.64])])
    head = _chain(names, parents, rest, trunk[-1], [('neck', [0.0, 0.98, 0.8]), ('head', [0.0, 1.02, 0.98])])
    tail = _chain(names, parents, rest, 0, [('tail1', [0.0, 0.78, -0.3]), ('tail2', [0.0, 0.74, -0.62]), ('tail3', [0.0, 0.7, -0.94])])
    roles = OrderedDict()
    roles['trunk'] = trunk
    roles['head'] = head
    roles['tail'] = tail
    for side, sx, tag in (('L', 1.0, 'L'), ('R', -1.0, 'R')):
        roles[f'limb.{side}.0'] = _leg(names, parents, rest, 0, [0.14 * sx, 0.74, 0.0], seg=[-0.32, -0.3], tag=tag)
    return SkeletonTemplate(name='biped', morphology='biped', joint_names=names, parents=np.asarray(parents, np.int64), rest=np.asarray(rest, np.float32), roles=roles)

def _build_quadruped() -> SkeletonTemplate:
    """Quadruped: horizontal trunk plus head, tail and two pairs of legs."""
    names, parents, rest = (['hips'], [-1], [[0.0, 0.7, -0.35]])
    trunk = [0] + _chain(names, parents, rest, 0, [('spine1', [0.0, 0.74, -0.1]), ('spine2', [0.0, 0.76, 0.16]), ('chest', [0.0, 0.74, 0.4])])
    head = _chain(names, parents, rest, trunk[-1], [('neck', [0.0, 0.8, 0.58]), ('head', [0.0, 0.82, 0.78])])
    tail = _chain(names, parents, rest, 0, [('tail1', [0.0, 0.7, -0.58]), ('tail2', [0.0, 0.68, -0.82]), ('tail3', [0.0, 0.66, -1.04])])
    roles: 'OrderedDict[str, list[int]]' = OrderedDict()
    roles['trunk'] = trunk
    roles['head'] = head
    roles['tail'] = tail
    for side, sx, tag in (('L', 1.0, 'L_front'), ('R', -1.0, 'R_front')):
        roles[f'limb.{side}.0'] = _leg(names, parents, rest, trunk[-1], [0.12 * sx, 0.66, 0.4], seg=[-0.3, -0.28], tag=tag)
    for side, sx, tag in (('L', 1.0, 'L_hind'), ('R', -1.0, 'R_hind')):
        roles[f'limb.{side}.1'] = _leg(names, parents, rest, 0, [0.12 * sx, 0.64, -0.35], seg=[-0.3, -0.28], tag=tag)
    return SkeletonTemplate(name='quadruped', morphology='quadruped', joint_names=names, parents=np.asarray(parents, np.int64), rest=np.asarray(rest, np.float32), roles=roles)

def _build_radial(num_pairs: int, morphology: str, name: str, body_len: float) -> SkeletonTemplate:
    """Generic many-legged build: one trunk plus ``num_pairs`` pairs of lateral legs, shared by octopods and myriapods."""
    names, parents, rest = (['root'], [-1], [[0.0, 0.3, body_len * 0.5]])
    seg_z = np.linspace(body_len * 0.5, -body_len * 0.5, num_pairs + 1)
    trunk = [0] + _chain(names, parents, rest, 0, [(f'body{i + 1}', [0.0, 0.3, float(seg_z[i + 1])]) for i in range(num_pairs)])
    head = _chain(names, parents, rest, 0, [('head', [0.0, 0.3, body_len * 0.5 + 0.14])])
    roles: 'OrderedDict[str, list[int]]' = OrderedDict()
    roles['trunk'] = trunk
    roles['head'] = head
    for i in range(num_pairs):
        host = trunk[i]
        z = float(rest[host][2])
        for side, sx, tag in (('L', 1.0, 'L'), ('R', -1.0, 'R')):
            roles[f'limb.{side}.{i}'] = _chain(names, parents, rest, host, [(f'{tag}{i + 1}_base', [0.1 * sx, 0.3, z]), (f'{tag}{i + 1}_mid', [0.3 * sx, 0.22, z]), (f'{tag}{i + 1}_tip', [0.46 * sx, 0.0, z])])
    return SkeletonTemplate(name=name, morphology=morphology, joint_names=names, parents=np.asarray(parents, np.int64), rest=np.asarray(rest, np.float32), roles=roles)
_BUILDERS: dict[str, object] = {'serpentine': lambda: _build_serpentine(12), 'biped': lambda: _build_biped(False), 'biped_armed': lambda: _build_biped(True), 'quadruped': _build_quadruped, 'octopod': lambda: _build_radial(4, 'octopod', 'octopod', 0.9), 'myriapod': lambda: _build_radial(10, 'myriapod', 'myriapod', 2.2)}
TEMPLATE_NAMES: tuple[str, ...] = tuple(_BUILDERS)

def build_template(name: str='biped_armed', *, weapon: bool=False) -> SkeletonTemplate:
    """Build a built-in skeleton template."""
    if name not in _BUILDERS:
        raise ValueError(f'unknown skeleton template {name!r}, available {TEMPLATE_NAMES}')
    tpl = _BUILDERS[name]()
    if weapon:
        host = 'limb.R.0' if 'limb.R.0' in tpl.roles else 'head' if 'head' in tpl.roles else 'trunk'
        tpl = attach_weapon(tpl, host)
    return tpl

def attach_weapon(template: SkeletonTemplate, host_role: str, *, length: float=0.7, num_joints: int=2, name: str='weapon') -> SkeletonTemplate:
    """Attach a weapon chain at the tip joint of the ``host_role`` chain and return a new template."""
    if host_role not in template.roles:
        raise ValueError(f'template {template.name!r} has no role {host_role!r}, available {list(template.roles)}')
    chain = template.roles[host_role]
    if len(chain) < 2:
        raise ValueError(f'role {host_role!r} has only {len(chain)} joints, so the weapon direction cannot be determined')
    tip = chain[-1]
    direction = template.rest[tip] - template.rest[chain[-2]]
    n = float(np.linalg.norm(direction))
    direction = direction / n if n > 1e-08 else _FWD
    names = list(template.joint_names)
    parents = template.parents.tolist()
    rest = template.rest.tolist()
    step = length / max(num_joints, 1)
    idxs = _chain(names, parents, rest, tip, [(f'{name}{i + 1}', (template.rest[tip] + direction * step * (i + 1)).tolist()) for i in range(num_joints)])
    roles = OrderedDict(template.roles)
    roles[f'weapon.{len(template.weapon_roles)}'] = idxs
    return SkeletonTemplate(name=f'{template.name}+weapon', morphology=template.morphology, joint_names=names, parents=np.asarray(parents, np.int64), rest=np.asarray(rest, np.float32), roles=roles, has_weapon=True)

@dataclass
class SkeletonAnalysis:
    """Recognition result for an unfamiliar skeleton."""
    morphology: str
    roles: 'OrderedDict[str, list[int]]'
    num_limbs: int
    trunk_axis: np.ndarray
    confidence: float
    notes: list[str] = field(default_factory=list)

def _children_of(parents: np.ndarray) -> list[list[int]]:
    kids: list[list[int]] = [[] for _ in range(len(parents))]
    for j, p in enumerate(parents):
        if int(p) >= 0:
            kids[int(p)].append(j)
    return kids

def _subtree_stats(kids: list[list[int]], rest: np.ndarray | None, root: int) -> tuple[np.ndarray, np.ndarray]:
    """Per joint: number of descendant leaves and the longest chain length in its subtree."""
    j_num = len(kids)
    leaves = np.zeros(j_num, dtype=np.int64)
    sublen = np.zeros(j_num, dtype=np.float32)
    order: list[int] = []
    stack = [root]
    while stack:
        j = stack.pop()
        order.append(j)
        stack.extend(kids[j])
    for j in reversed(order):
        if not kids[j]:
            leaves[j] = 1
            sublen[j] = 0.0
            continue
        leaves[j] = sum((int(leaves[c]) for c in kids[j]))
        best = 0.0
        for c in kids[j]:
            step = 1.0 if rest is None else float(np.linalg.norm(rest[c] - rest[j]))
            best = max(best, step + float(sublen[c]))
        sublen[j] = best
    return (leaves, sublen)

def _midline_trunk(root: int, kids: list[list[int]], rest: np.ndarray | None, leaves: np.ndarray, sublen: np.ndarray) -> list[int]:
    """Walk the trunk chain out from the root along the midline."""
    trunk = [root]
    cur = root
    while kids[cur]:
        cand = kids[cur]
        if rest is not None:
            xs = np.array([abs(float(rest[c][0])) for c in cand])
            lo = float(xs.min())
            cand = [c for c, x in zip(cand, xs) if x <= lo + 0.0001]
        nxt = max(cand, key=lambda c: (int(leaves[c]), float(sublen[c])))
        trunk.append(nxt)
        cur = nxt
    return trunk

def recognize_skeleton(parents: np.ndarray, rest: np.ndarray | None=None, joint_names: list[str] | None=None) -> SkeletonAnalysis:
    """Recognise the morphology and semantic chains of an unfamiliar skeleton."""
    parents = np.asarray(parents, dtype=np.int64)
    roots = np.where(parents < 0)[0]
    if len(roots) != 1:
        raise ValueError(f'exactly one root joint is required, found {len(roots)}: {roots.tolist()}')
    root = int(roots[0])
    kids = _children_of(parents)
    notes: list[str] = []
    leaves, sublen = _subtree_stats(kids, rest, root)
    trunk = _midline_trunk(root, kids, rest, leaves, sublen)
    on_trunk = set(trunk)
    if rest is not None and len(trunk) >= 2:
        vec = rest[trunk[-1]] - rest[trunk[0]]
        n = float(np.linalg.norm(vec))
        trunk_axis = (vec / n).astype(np.float32) if n > 1e-08 else _FWD.copy()
    else:
        trunk_axis = _FWD.copy()
        notes.append('no rest pose supplied, so the trunk axis is assumed to be +Z')

    def longest_branch(start: int) -> list[int]:
        """Take the longest chain from ``start``, following the longest subtree at each step."""
        path = [start]
        cur = start
        while kids[cur]:
            cur = max(kids[cur], key=lambda c: (float(sublen[c]), int(leaves[c])))
            path.append(cur)
        return path
    branches: list[tuple[int, list[int]]] = []
    for host in trunk:
        for c in kids[host]:
            if c in on_trunk:
                continue
            branches.append((host, longest_branch(c)))
    limbs: list[tuple[int, list[int]]] = []
    axial: list[tuple[float, list[int]]] = []
    for host, chain in branches:
        if rest is None:
            limbs.append((host, chain))
            continue
        vec = rest[chain[-1]] - rest[host]
        n = float(np.linalg.norm(vec))
        unit = vec / n if n > 1e-08 else _UP
        lateral = float(abs(unit[0]))
        downward = float(-unit[1])
        if downward > 0.35 or lateral > 0.35:
            limbs.append((host, chain))
        else:
            axial.append((float(unit[2]), chain))
    head: list[int] = []
    tail: list[int] = []
    for z, chain in sorted(axial, key=lambda x: -abs(x[0])):
        if z >= 0.0 and len(chain) > len(head):
            head = chain
        elif z < 0.0 and len(chain) > len(tail):
            tail = chain
    num_limbs = len(limbs)
    confidence = 0.9
    if num_limbs == 0:
        morphology = 'serpentine'
    elif num_limbs == 2:
        morphology = 'biped'
    elif num_limbs == 4:
        vertical = float(abs(np.dot(trunk_axis, _UP)))
        if rest is None:
            morphology, confidence = ('quadruped', 0.4)
            notes.append('four limbs but no rest pose, so biped and quadruped cannot be told apart; treated as quadruped')
        elif vertical > 0.6:
            morphology = 'biped_armed'
            notes.append(f'trunk is close to vertical (|axis.up|={vertical:.2f}), classified as an armed biped')
        else:
            morphology = 'quadruped'
            notes.append(f'trunk is close to horizontal (|axis.up|={vertical:.2f}), classified as a quadruped')
    elif num_limbs == 8:
        morphology = 'octopod'
    elif num_limbs > 8:
        morphology = 'myriapod'
    else:
        morphology, confidence = ('unknown', 0.3)
        notes.append(f'limb count {num_limbs} matches no known morphology')
    roles: 'OrderedDict[str, list[int]]' = OrderedDict()
    vertical_trunk = rest is not None and float(abs(np.dot(trunk_axis, _UP))) > 0.6

    def sort_key(item: tuple[int, list[int]]) -> float:
        if rest is None:
            return float(item[0])
        anchor = rest[item[1][0]]
        return -float(anchor[1]) if vertical_trunk else -float(anchor[2])
    left = sorted([x for x in limbs if rest is None or rest[x[1][0]][0] >= 0], key=sort_key)
    right = sorted([x for x in limbs if not (rest is None or rest[x[1][0]][0] >= 0)], key=sort_key)
    weapons: list[list[int]] = []
    for i in range(min(len(left), len(right))):
        lc, rc = (left[i][1], right[i][1])
        keep = min(len(lc), len(rc))
        if len(lc) != len(rc):
            longer, name = (lc, 'L') if len(lc) > len(rc) else (rc, 'R')
            weapons.append(longer[keep:])
            notes.append(f'limb pair {i} has {len(longer) - keep} extra tip joints on the {name} side, treated as an attachment such as a weapon')
            if len(lc) > len(rc):
                left[i] = (left[i][0], lc[:keep])
            else:
                right[i] = (right[i][0], rc[:keep])
    if not head and len(trunk) >= 3:
        branch_hosts = {h for h, _ in branches}
        last = max((k for k, j in enumerate(trunk) if j in branch_hosts), default=-1)
        remainder = trunk[last + 1:] if last >= 0 else []
        if remainder and len(remainder) <= max(2, int(0.4 * len(trunk))):
            head = remainder
            trunk = trunk[:last + 1]
            notes.append(f'the last {len(head)} trunk joints sit beyond the final branch point and were split off as head')
    roles['trunk'] = trunk
    if head:
        roles['head'] = head
    if tail:
        roles['tail'] = tail
    for side, group in (('L', left), ('R', right)):
        for i, (_host, chain) in enumerate(group):
            roles[f'limb.{side}.{i}'] = chain
    for i, ch in enumerate(weapons):
        roles[f'weapon.{i}'] = ch
    if len(left) != len(right):
        confidence = min(confidence, 0.6)
        notes.append(f'left and right limb counts are asymmetric ({len(left)} left / {len(right)} right), so recognition may be wrong')
    if joint_names is not None and len(joint_names) != len(parents):
        notes.append(f'joint_names length {len(joint_names)} does not match the joint count {len(parents)}, so it was ignored')
    return SkeletonAnalysis(morphology=morphology, roles=roles, num_limbs=num_limbs, trunk_axis=trunk_axis, confidence=confidence, notes=notes)

def template_from_skeleton(parents: np.ndarray, rest: np.ndarray, joint_names: list[str] | None=None, name: str='recognized') -> SkeletonTemplate:
    """Recognise an unfamiliar skeleton and wrap it into a usable template."""
    analysis = recognize_skeleton(parents, rest, joint_names)
    names = list(joint_names) if joint_names is not None and len(joint_names) == len(parents) else [f'J{i}' for i in range(len(parents))]
    return SkeletonTemplate(name=name, morphology=analysis.morphology, joint_names=names, parents=np.asarray(parents, np.int64), rest=np.asarray(rest, np.float32), roles=analysis.roles, has_weapon=bool(analysis.roles and any((r.startswith('weapon.') for r in analysis.roles))))
_COMMON_ALIASES: dict[str, str] = {'trunk': 'trunk', 'body': 'trunk', 'spine': 'trunk', 'root': 'trunk', 'head': 'head', 'neck': 'head', 'tail': 'tail', 'weapon': 'weapon.0', 'sword': 'weapon.0'}
_LIMB_ALIASES: dict[str, dict[str, str]] = {'biped_armed': {'left_arm': 'limb.L.0', 'left_hand': 'limb.L.0', 'right_arm': 'limb.R.0', 'right_hand': 'limb.R.0', 'left_leg': 'limb.L.1', 'left_foot': 'limb.L.1', 'right_leg': 'limb.R.1', 'right_foot': 'limb.R.1'}, 'biped': {'left_leg': 'limb.L.0', 'left_foot': 'limb.L.0', 'right_leg': 'limb.R.0', 'right_foot': 'limb.R.0'}, 'quadruped': {'left_front_leg': 'limb.L.0', 'left_front': 'limb.L.0', 'right_front_leg': 'limb.R.0', 'right_front': 'limb.R.0', 'left_hind_leg': 'limb.L.1', 'left_hind': 'limb.L.1', 'right_hind_leg': 'limb.R.1', 'right_hind': 'limb.R.1'}}

def resolve_role(template: SkeletonTemplate, name: str) -> str:
    """Resolve a body-part name into a role id of the template."""
    key = str(name).strip()
    if key in template.roles:
        return key
    low = key.lower().replace('-', '_').replace(' ', '_')
    table = dict(_COMMON_ALIASES)
    table.update(_LIMB_ALIASES.get(template.morphology, {}))
    role = table.get(key) or table.get(low)
    if role is None:
        m = re.fullmatch('limb[._]?([lrc])[._]?(\\d+)', low)
        if m:
            role = f'limb.{m.group(1).upper()}.{int(m.group(2))}'
    if role is None:
        m = re.fullmatch('weapon[._]?(\\d+)', low)
        if m:
            role = f'weapon.{int(m.group(1))}'
    if role is None:
        raise ValueError(f'cannot resolve {name!r} to a role; template {template.name!r}({template.morphology}) offers {list(template.roles)}')
    if role not in template.roles:
        raise ValueError(f'{name!r} resolves to {role!r}, but template {template.name!r}({template.morphology}) has no such role. Available: {list(template.roles)}')
    return role

@dataclass
class RolePlan:
    """Planning result for one semantic chain."""
    role: str
    kind: str
    joints: list[int]
    host: int
    rest_dir: np.ndarray
    length: float
    swing_axis: np.ndarray
    bend_axis: np.ndarray
    twist_axis: np.ndarray
    lift_axis: np.ndarray | None = None
    flex_axis: np.ndarray | None = None
    flex_lifts: bool = False

@dataclass
class SkeletonPlan:
    """Plan for a whole skeleton: one :class:`RolePlan` per chain plus global scale information."""
    template: SkeletonTemplate
    roles: 'OrderedDict[str, RolePlan]'
    scale: float
    height: float
    ground: float
    support_roles: list[str] = field(default_factory=list)
    support_length: float = 0.0
    notes: list[str] = field(default_factory=list)

    def __getitem__(self, role: str) -> RolePlan:
        return self.roles[role]

    def resolve(self, name: str) -> RolePlan:
        """Look up a :class:`RolePlan` by alias, via :func:`resolve_role`."""
        return self.roles[resolve_role(self.template, name)]

def _kind_of(role: str) -> str:
    if role.startswith('limb.'):
        return 'limb'
    if role.startswith('weapon.'):
        return 'weapon'
    return role if role in ('trunk', 'head', 'tail') else 'other'
REFERENCE_TPOSE: np.ndarray = np.array([[0.0, 0.0, 0.0], [0.06, -0.09, 0.0], [-0.06, -0.09, 0.0], [0.0, 0.12, 0.0], [0.1, -0.48, 0.0], [-0.1, -0.48, 0.0], [0.0, 0.25, 0.0], [0.1, -0.9, 0.02], [-0.1, -0.9, 0.02], [0.0, 0.38, 0.0], [0.11, -0.96, 0.12], [-0.11, -0.96, 0.12], [0.0, 0.55, 0.0], [0.08, 0.47, 0.0], [-0.08, 0.47, 0.0], [0.0, 0.66, 0.03], [0.18, 0.46, 0.0], [-0.18, 0.46, 0.0], [0.43, 0.46, 0.0], [-0.43, 0.46, 0.0], [0.68, 0.46, 0.0], [-0.68, 0.46, 0.0]], dtype=np.float32)

def _snap_axis(axis: np.ndarray, tol_deg: float=20.0) -> np.ndarray:
    """Snap an axis that lies close to a principal body direction (+/-X, +/-Y, +/-Z) onto that direction."""
    thresh = float(np.cos(np.deg2rad(tol_deg)))
    for e in (_RIGHT, _UP, _FWD):
        d = float(np.dot(axis, e))
        if abs(d) >= thresh:
            return (e * np.sign(d)).astype(np.float32)
    return axis
_POLE_MIN_RATIO = 0.15

def _fold_axis(rest: np.ndarray, joints: list[int], *, support: bool) -> np.ndarray | None:
    """Find the flexion axis of the chain's own hinge, so a positive rotation about it folds the chain the anatomically correct way."""
    if len(joints) < 3:
        return None
    prox, mid, tip = (rest[joints[0]], rest[joints[1]], rest[joints[-1]])
    r = tip - mid
    r_len = float(np.linalg.norm(r))
    if r_len < 1e-06:
        return None
    r = r / r_len
    span = tip - prox
    span_len = float(np.linalg.norm(span))
    pole = np.zeros(3, dtype=np.float32)
    if span_len > 1e-06:
        u = span / span_len
        v = mid - prox
        pole = (v - float(np.dot(v, u)) * u).astype(np.float32)
    seg = 0.5 * (float(np.linalg.norm(mid - prox)) + r_len)
    pole_ratio = float(np.linalg.norm(pole)) / max(seg, 1e-08)
    if pole_ratio > _POLE_MIN_RATIO:
        toward = prox - tip
        d = toward - float(np.dot(toward, r)) * r
    else:
        d = (-_FWD if support else _FWD).copy()
        d = d - float(np.dot(d, r)) * r
    if float(np.linalg.norm(d)) < 1e-06:
        return None
    d = d / float(np.linalg.norm(d))
    a = np.cross(r, d)
    n = float(np.linalg.norm(a))
    if n < 1e-06:
        return None
    return _snap_axis((a / n).astype(np.float32))
_FLEX_PROBE_DEG = 40.0

def _rotated_tip_height(rest: np.ndarray, joints: list[int], axis: np.ndarray, degrees: float) -> float:
    """Height (Y) of the chain tip after rotating the distal segment about ``joints[1]`` by ``degrees``."""
    pivot = rest[joints[1]]
    v = (rest[joints[-1]] - pivot).astype(np.float64)
    k = np.asarray(axis, dtype=np.float64)
    k = k / max(float(np.linalg.norm(k)), 1e-12)
    th = float(np.deg2rad(degrees))
    rot = v * np.cos(th) + np.cross(k, v) * np.sin(th) + k * float(np.dot(k, v)) * (1.0 - np.cos(th))
    return float(pivot[1] + rot[1])

def plan_skeleton(template: SkeletonTemplate) -> SkeletonPlan:
    """Plan the skeleton: derive direction, length and recommended axes for each chain."""
    rest = template.rest
    plans: 'OrderedDict[str, RolePlan]' = OrderedDict()
    notes: list[str] = []
    support: list[str] = []
    ground = template.ground
    scale = max(template.scale, 1e-06)
    height = max(template.height, 1e-06)
    for role, joints in template.roles.items():
        if not joints:
            notes.append(f'role {role!r} has an empty joint chain and was skipped')
            continue
        host = int(template.parents[joints[0]])
        if len(joints) >= 2:
            vec = rest[joints[-1]] - rest[joints[0]]
        else:
            anchor = rest[host] if host >= 0 else rest[joints[0]]
            vec = rest[joints[-1]] - anchor
        n = float(np.linalg.norm(vec))
        if n < 1e-06:
            rest_dir = _UP.copy()
            notes.append(f'role {role!r} has near-zero rest length, so its direction falls back to +Y')
        else:
            rest_dir = (vec / n).astype(np.float32)
        length = float(sum((np.linalg.norm(rest[b] - rest[a]) for a, b in zip([host if host >= 0 else joints[0]] + joints[:-1], joints))))
        swing = np.cross(rest_dir, _FWD)
        if float(np.linalg.norm(swing)) < 0.001:
            swing = _RIGHT.copy()
        swing = _snap_axis((swing / max(float(np.linalg.norm(swing)), 1e-08)).astype(np.float32))
        bend = np.cross(rest_dir, _RIGHT)
        if float(np.linalg.norm(bend)) < 0.001:
            bend = _UP.copy()
        bend = _snap_axis((bend / max(float(np.linalg.norm(bend)), 1e-08)).astype(np.float32))
        lift = np.cross(rest_dir, _UP)
        if float(np.linalg.norm(lift)) < 0.001:
            lift = _RIGHT.copy()
        lift = _snap_axis((lift / max(float(np.linalg.norm(lift)), 1e-08)).astype(np.float32))
        is_support = float(rest[joints[-1]][1] - ground) < 0.12 * scale
        flex = _fold_axis(rest, joints, support=is_support)
        flex_lifts = False
        if flex is not None:
            flex_lifts = bool(_rotated_tip_height(rest, joints, flex, _FLEX_PROBE_DEG) > float(rest[joints[-1]][1]) + 0.0001)
        kind = _kind_of(role)
        plans[role] = RolePlan(role=role, kind=kind, joints=list(joints), host=host, rest_dir=rest_dir, length=length, swing_axis=swing, bend_axis=bend, twist_axis=rest_dir.copy(), lift_axis=lift, flex_axis=flex, flex_lifts=flex_lifts)
        if kind == 'limb' and float(rest[joints[-1]][1] - ground) < 0.12 * scale:
            support.append(role)
    if not support and template.limb_roles:
        notes.append('no limb tip is near the ground, so gait and foot-locking primitives are meaningless on this skeleton')
    support_length = float(np.mean([plans[r].length for r in support])) if support else 0.0
    return SkeletonPlan(template=template, roles=plans, scale=scale, height=height, ground=ground, support_roles=support, support_length=support_length, notes=notes)
