from __future__ import annotations
from dataclasses import dataclass
import numpy as np
from .types import RigResult
from .mesh import CreatureMesh, ReferenceRig
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
    reference: bool = False

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


def check_smoothness(skin: SkinWeights, mesh: CreatureMesh, *, quantile: float, limit: float | None = None,
                     baseline: float | None = None, margin: float = 1.25) -> Finding:
    """Neighbouring vertices should not jump in weight; a jump means a hard crease at the joint.

    The metric is a quantile (default 0.95 in the configs) of the L1 weight difference over all mesh edges, not the
    maximum: the maximum always occurs where separate limbs meet, which is meant to change abruptly.

    The scale depends on mesh resolution and artist style, so an absolute limit can misjudge. With ``baseline`` (the same
    metric on the artist skin) the criterion is "not clearly worse than the artist" (``baseline * margin``). Without a
    baseline, ``limit`` applies; with neither, the value is only reported.
    """
    gaps = []
    w = skin.weights
    for i, nb in enumerate(mesh.adjacency()):
        if len(nb):
            gaps.append(float(np.abs(w[nb] - w[i]).sum(axis=1).max()))
    if not gaps:
        return Finding('smoothness', True, 0.0, 'mesh has no connected edges; skipped')
    value = float(np.quantile(gaps, quantile))
    stats = (f'edge weight L1 difference at quantile {quantile}: {value:.3f} '
             f'(median {float(np.median(gaps)):.3f}, max {max(gaps):.3f})')
    if baseline is not None:
        bound = float(baseline) * float(margin)
        return Finding('smoothness', value <= bound, value, stats + f'; artist skin {baseline:.3f}, limit {bound:.3f}')
    if limit is None:
        return Finding('smoothness', True, value, stats + '; no reference skin or limit, informational only')
    return Finding('smoothness', value <= limit, value, stats + f'; limit {limit}')


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


def compare_to_reference_skin(mesh: CreatureMesh, reference: ReferenceRig, skinning, *, quality,
                              tolerance: float = 0.06) -> tuple[Finding, Finding]:
    """Skin the artist skeleton automatically and compare with the artist weights (weight L1 and deformation distance).

    The joints are identical, so differences come only from the weights, which separates skin quality from skeleton
    quality. Weight L1 measures "how alike the assignment is"; the deformation distance (fraction of mesh scale under the
    same bend pose) measures how much the motion differs and is what decides pass or fail.
    """
    from .skin_generate import skin_mesh
    if reference.weights is None:
        raise ValueError('The reference rig has no skin weights to compare with')
    proxy = RigResult('reference', np.asarray(reference.rest, float), np.asarray(reference.parents, np.int64),
                      list(reference.joint_names), {})
    ours = skin_mesh(mesh, proxy, config=skinning)
    l1 = float(np.abs(ours.weights - reference.weights).sum(axis=1).mean())
    rotations = bend_pose(proxy.joints, proxy.parents, degrees=quality['bend_degrees'], seed=quality['bend_seed'],
                          scale_span=quality['bend_scale_span'])
    matrices = pose_matrices(proxy.joints, proxy.parents, rotations=rotations, translation=None)
    a = deform(mesh.vertices, ours.weights, matrices)
    b = deform(mesh.vertices, reference.weights, matrices)
    gap = float(np.linalg.norm(a - b, axis=1).mean()) / max(mesh.scale, np.finfo(float).eps)
    return (Finding('weights_vs_reference', True, l1,
                    f'per-vertex weight L1 difference, mean {l1:.3f} (0 identical, 2 disjoint); informational, '
                    'different weights do not imply different deformation'),
            Finding('deform_vs_reference', gap <= tolerance, gap,
                    f'deformation difference on the same skeleton and pose, mean {gap * 100:.2f}% of scale '
                    f'(limit {tolerance * 100:.0f}%)'))


def validate_skin(skin: SkinWeights, mesh: CreatureMesh, rig: RigResult, *, config, reference: ReferenceRig | None = None,
                  skinning=None, margin: float = 1.25, tolerance: float = 0.06) -> SkinReport:
    """Run all checks. With ``reference`` the smoothness limit is the artist skin's value times ``margin``; with
    ``reference`` and ``skinning`` (the skin config) the artist comparison is appended."""
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
    has_weights = reference is not None and reference.weights is not None
    baseline = None
    if has_weights:
        baseline = check_smoothness(SkinWeights(reference.weights, list(reference.joint_names)), mesh,
                                    quantile=p['smoothness_quantile']).value
    findings = [
        check_constraints(skin, max_influences=p['max_influences'], sum_tolerance=p['sum_tolerance']),
        check_smoothness(skin, mesh, quantile=p['smoothness_quantile'], limit=p['smoothness_limit'],
                         baseline=baseline, margin=margin),
        check_deformation(skin, mesh, rig, config=p),
    ]
    compared = has_weights and skinning is not None
    if compared:
        findings.extend(compare_to_reference_skin(mesh, reference, skinning, quality=p, tolerance=tolerance))
    return SkinReport(findings, bool(compared))


def format_skin_report(report: SkinReport, skin: SkinWeights, title: str) -> str:
    head = 'PASS' if report.ok else 'FAIL'
    lines = [f'[{head}] {title}: {skin.num_vertices} vertices / {skin.num_joints} joints, mean influences {skin.influences.mean():.2f}']
    if not report.reference:
        lines.append('  Note: no reference skin; self-consistency checks only, they cannot prove the weights are correct')
    for finding in report.findings:
        lines.append(f"  {('PASS' if finding.ok else 'FAIL')} {finding.check:22} {finding.detail}")
    for note in skin.notes:
        lines.append(f'  ! {note}')
    return '\n'.join(lines)
