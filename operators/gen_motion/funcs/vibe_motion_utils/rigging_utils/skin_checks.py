from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .types import RigResult
from .mesh import CreatureMesh
from .skin_units import SkinWeights, axis_angle_matrix, deform, mesh_volume, pose_matrices, validate_weights

@dataclass
class Finding:
    'Result of a single skin check.'
    check: str
    ok: bool
    value: float
    detail: str

@dataclass
class SkinReport:
    'Collected findings for one set of skin weights.'
    findings: list[Finding]

    @property
    def ok(self) -> bool:
        return all((f.ok for f in self.findings))

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]

def bend_pose(joints: np.ndarray, parents: np.ndarray, *, degrees: float=35.0, seed: int=0) -> dict[int, np.ndarray]:
    'Build a deterministic bent pose, rotating each non-root joint about an axis perpendicular to its own bone.'
    rng = np.random.default_rng(seed)
    joints = np.asarray(joints, float)
    parents = np.asarray(parents, np.int64)
    out: dict[int, np.ndarray] = {}
    for j in range(len(joints)):
        p = int(parents[j])
        if p < 0:
            continue
        bone = joints[j] - joints[p]
        norm = float(np.linalg.norm(bone))
        if norm < 1e-12:
            continue
        helper = np.array([1.0, 0.0, 0.0]) if abs(bone[0] / norm) < 0.9 else np.array([0.0, 0.0, 1.0])
        axis = np.cross(bone / norm, helper)
        out[j] = axis_angle_matrix(axis, float(degrees) * rng.uniform(0.6, 1.0))
    return out

def check_constraints(skin: SkinWeights, *, max_influences: int=4) -> Finding:
    'Check the hard constraints: non-negative, rows summing to one, bounded influence count.'
    issues = validate_weights(skin, max_influences=max_influences)
    worst = float(np.abs(skin.weights.sum(axis=1) - 1.0).max(initial=0.0))
    return Finding('constraints', not issues, worst, f'non-negative / rows sum to 1 / influence count within limit (max row-sum error {worst:.1e}, max influences {int(skin.influences.max(initial=0))})' if not issues else '; '.join(issues))

def check_smoothness(skin: SkinWeights, mesh: CreatureMesh) -> Finding:
    'Report weight variation across mesh edges; large jumps mean a hard crease at the joint.'
    adjacency = mesh.adjacency()
    gaps: list[float] = []
    w = skin.weights
    for i, nb in enumerate(adjacency):
        if len(nb):
            gaps.append(float(np.abs(w[nb] - w[i]).sum(axis=1).max()))
    if not gaps:
        return Finding('smoothness', True, 0.0, 'mesh has no connected edges, skipped')
    p95 = float(np.quantile(gaps, 0.95))
    stats = f'edge weight L1 difference, 95th percentile {p95:.3f} (median {float(np.median(gaps)):.3f}, max {max(gaps):.3f})'
    return Finding('smoothness', True, p95, stats + '; reported for information only, with no pass threshold')

def check_deformation(skin: SkinWeights, mesh: CreatureMesh, rig: RigResult, *, degrees: float=35.0, volume_tolerance: float=0.45, travel_tolerance: float=1.5) -> Finding:
    'Pose the mesh in a bent pose and check that volume and displacement stay within tolerance.'
    matrices = pose_matrices(rig.joints, rig.parents, bend_pose(rig.joints, rig.parents, degrees=degrees))
    moved = deform(mesh.vertices, skin.weights, matrices)
    rest_volume = mesh_volume(mesh.vertices, mesh.faces)
    posed_volume = mesh_volume(moved, mesh.faces)
    ratio = posed_volume / max(rest_volume, 1e-18)
    travel = float(np.linalg.norm(moved - mesh.vertices, axis=1).max()) / max(mesh.scale, 1e-12)
    ok = abs(ratio - 1.0) <= volume_tolerance and travel <= travel_tolerance and np.isfinite(moved).all()
    return Finding('deformation', ok, float(abs(ratio - 1.0)), f'volume ratio {ratio:.3f} (tolerance +/-{volume_tolerance}), max displacement {travel:.2f}x scale (limit {travel_tolerance})')


def validate_skin(skin: SkinWeights, mesh: CreatureMesh, rig: RigResult, *, max_influences: int=4) -> SkinReport:
    'Run every check. These are self-consistency checks and do not prove the weights match an artist intent.'
    return SkinReport([check_constraints(skin, max_influences=max_influences), check_smoothness(skin, mesh), check_deformation(skin, mesh, rig)])

def format_skin_report(report: SkinReport, skin: SkinWeights, title: str) -> str:
    'Render a report as plain text.'
    head = 'PASS' if report.ok else 'FAIL'
    lines = [f'[{head}] {title}: {skin.num_vertices} vertices / {skin.num_joints} joints, mean influences {skin.influences.mean():.2f}']
    lines.append('  Note: self-consistency checks only; they cannot prove the weights are correct')
    for f in report.findings:
        lines.append(f"  {('PASS' if f.ok else 'FAIL')} {f.check:22} {f.detail}")
    for note in skin.notes:
        lines.append(f'  ! {note}')
    return '\n'.join(lines)
