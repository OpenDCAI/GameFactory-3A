"""Evaluate skin weights and deformation with explicit sampling and thresholds."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .types import RigResult
from .mesh import CreatureMesh
from .templates import require_config, finite_number, integer
from .checks import validate_tree
from .skin_units import SkinWeights, axis_angle_matrix, deform, mesh_volume, pose_matrices, validate_weights


@dataclass
class Finding:
    check: str
    ok: bool
    value: float
    detail: str


@dataclass
class SkinReport:
    findings: list[Finding]

    @property
    def ok(self) -> bool:
        return all(f.ok for f in self.findings)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]


def bend_pose(joints: np.ndarray, parents: np.ndarray, *, degrees: float, seed: int,
              scale_span) -> dict[int, np.ndarray]:
    rng = np.random.default_rng(seed)
    joints = np.asarray(joints, float)
    parents = np.asarray(parents, np.int64)
    out = {}
    for j, parent in enumerate(parents):
        if parent < 0:
            continue
        bone = joints[j] - joints[parent]
        norm = float(np.linalg.norm(bone))
        if norm <= np.finfo(float).eps:
            continue
        direction = bone / norm
        helper = np.eye(3)[int(np.argmin(np.abs(direction)))]
        axis = np.cross(direction, helper)
        out[j] = axis_angle_matrix(axis, float(degrees) * rng.uniform(*scale_span))
    return out


def check_constraints(skin: SkinWeights, *, max_influences: int, sum_tolerance: float) -> Finding:
    issues = validate_weights(skin, max_influences=max_influences, sum_tolerance=sum_tolerance)
    worst = float(np.abs(skin.weights.sum(axis=1) - 1.0).max(initial=0.0))
    detail = f'max row-sum error {worst}, max influences {int(skin.influences.max(initial=0))}'
    return Finding('constraints', not issues, worst, '; '.join(issues) if issues else detail)


def check_smoothness(skin: SkinWeights, mesh: CreatureMesh, *, quantile: float,
                     limit: float) -> Finding:
    gaps = []
    w = skin.weights
    for i, nb in enumerate(mesh.adjacency()):
        if len(nb):
            gaps.append(float(np.abs(w[nb] - w[i]).sum(axis=1).max()))
    value = float(np.quantile(gaps, quantile)) if gaps else 0.0
    return Finding('smoothness', value <= limit, value,
                   f'edge weight L1 difference at quantile {quantile}: {value}; limit {limit}')


def check_deformation(skin: SkinWeights, mesh: CreatureMesh, rig: RigResult, *, config) -> Finding:
    rotations = bend_pose(rig.joints, rig.parents, degrees=config['bend_degrees'],
                          seed=config['bend_seed'], scale_span=config['bend_scale_span'])
    matrices = pose_matrices(rig.joints, rig.parents, rotations=rotations, translation=None)
    moved = deform(mesh.vertices, skin.weights, matrices)
    rest_volume = mesh_volume(mesh.vertices, mesh.faces)
    posed_volume = mesh_volume(moved, mesh.faces)
    ratio = posed_volume / max(rest_volume, np.finfo(float).eps)
    travel = float(np.linalg.norm(moved - mesh.vertices, axis=1).max()) / max(mesh.scale, np.finfo(float).eps)
    ok = bool(abs(ratio - 1.0) <= config['volume_tolerance'] and travel <= config['travel_tolerance'] and np.isfinite(moved).all())
    return Finding('deformation', ok, float(abs(ratio - 1.0)),
                   f'volume ratio {ratio}; tolerance {config["volume_tolerance"]}; displacement {travel}; limit {config["travel_tolerance"]}')


def validate_skin(skin: SkinWeights, mesh: CreatureMesh, rig: RigResult, *, config) -> SkinReport:
    fields = ('max_influences', 'sum_tolerance', 'smoothness_quantile', 'smoothness_limit',
              'bend_degrees', 'bend_seed', 'bend_scale_span', 'volume_tolerance', 'travel_tolerance')
    p = require_config(config, fields, 'skin validation')
    integer(p['max_influences'], 'max_influences', 1)
    integer(p['bend_seed'], 'bend_seed', 0)
    for name in ('sum_tolerance', 'smoothness_quantile', 'smoothness_limit', 'bend_degrees', 'volume_tolerance', 'travel_tolerance'):
        finite_number(p[name], name)
    if p['smoothness_quantile'] > 1:
        raise ValueError('smoothness_quantile must lie in [0,1]')
    span = np.asarray(p['bend_scale_span'], float)
    if span.shape != (2,) or not np.isfinite(span).all() or not 0 <= span[0] <= span[1]:
        raise ValueError('bend_scale_span must satisfy 0 <= lo <= hi')
    validate_tree(rig)
    if skin.weights.shape != (mesh.num_vertices, rig.num_joints):
        raise ValueError('skin dimensions do not match the mesh and rig')
    if list(skin.joint_names) != list(rig.joint_names):
        raise ValueError('skin joint order does not match the rig')
    return SkinReport([
        check_constraints(skin, max_influences=p['max_influences'], sum_tolerance=p['sum_tolerance']),
        check_smoothness(skin, mesh, quantile=p['smoothness_quantile'], limit=p['smoothness_limit']),
        check_deformation(skin, mesh, rig, config=p),
    ])


def format_skin_report(report: SkinReport, skin: SkinWeights, title: str) -> str:
    head = 'PASS' if report.ok else 'FAIL'
    lines = [f'[{head}] {title}: {skin.num_vertices} vertices / {skin.num_joints} joints, mean influences {skin.influences.mean():.2f}']
    lines.append('  Note: self-consistency checks only; they cannot prove the weights match artist intent')
    for finding in report.findings:
        lines.append(f"  {('PASS' if finding.ok else 'FAIL')} {finding.check:22} {finding.detail}")
    for note in skin.notes:
        lines.append(f'  ! {note}')
    return '\n'.join(lines)
