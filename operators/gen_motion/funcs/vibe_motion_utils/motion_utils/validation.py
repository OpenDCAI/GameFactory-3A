from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from .skeleton_templates import MotionClip, SkeletonPlan, bone_lengths

@dataclass
class Finding:
    """One check result."""
    check: str
    ok: bool
    severity: float
    detail: str
    frames: list[int] = field(default_factory=list)
    joints: list[int] = field(default_factory=list)

    def __str__(self) -> str:
        mark = 'OK  ' if self.ok else 'FAIL'
        loc = f' frames {self.frames[:4]}' if self.frames else ''
        return f'[{mark}] {self.check}: {self.detail}{loc}'

@dataclass
class ValidationReport:
    """A full validation report."""
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all((f.ok for f in self.findings))

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]

    @property
    def worst(self) -> float:
        return max((f.severity for f in self.findings), default=0.0)

def check_finite(clip: MotionClip) -> Finding:
    """Check that rotations, translations and FK results are all finite."""
    bad_q = ~np.isfinite(clip.quats).all(axis=(1, 2))
    bad_t = ~np.isfinite(clip.trans).all(axis=1)
    pos = clip.positions()
    bad_p = ~np.isfinite(pos).all(axis=(1, 2))
    bad = np.where(bad_q | bad_t | bad_p)[0]
    if len(bad) == 0:
        return Finding('finite', True, 0.0, 'rotations, translations and positions are all finite')
    return Finding('finite', False, 1.0, f'{len(bad)} frames contain nan/inf', bad[:8].tolist())

def check_bone_lengths(clip: MotionClip, *, tol_ratio: float=0.001) -> Finding:
    """Check that bone lengths stay equal to their rest pose values."""
    tpl = clip.template
    ref = bone_lengths(tpl.parents, tpl.rest)
    pos = clip.positions()
    scale = max(float(np.max(ref)), 1e-06)
    worst_ratio, worst_j, worst_t = (0.0, -1, -1)
    for j, p in enumerate(np.asarray(tpl.parents)):
        p = int(p)
        if p < 0:
            continue
        cur = np.linalg.norm(pos[:, j] - pos[:, p], axis=-1)
        err = np.abs(cur - ref[j]) / scale
        k = int(np.argmax(err))
        if float(err[k]) > worst_ratio:
            worst_ratio, worst_j, worst_t = (float(err[k]), j, k)
    ok = worst_ratio <= tol_ratio
    return Finding('bone_lengths', ok, min(worst_ratio / max(tol_ratio, 1e-09) * 0.1, 1.0) if not ok else 0.0, f'largest bone length drift {worst_ratio:.2e} (tolerance {tol_ratio:.0e})' + (f', joint {worst_j}' if not ok else ''), [worst_t] if not ok else [], [worst_j] if not ok else [])

def check_joint_limits(clip: MotionClip, plan: SkeletonPlan, *, max_bend_deg: float=165.0) -> Finding:
    """Check whether adjacent bones fold back on each other."""
    pos = clip.positions()
    limit = np.deg2rad(180.0 - float(max_bend_deg))
    worst_ang, worst = (np.pi, (-1, -1))
    for rp in plan.roles.values():
        for a, b, c in zip(rp.joints[:-2], rp.joints[1:-1], rp.joints[2:]):
            u = pos[:, a] - pos[:, b]
            v = pos[:, c] - pos[:, b]
            nu = np.linalg.norm(u, axis=-1)
            nv = np.linalg.norm(v, axis=-1)
            valid = (nu > 1e-06) & (nv > 1e-06)
            if not valid.any():
                continue
            cos = np.sum(u * v, axis=-1) / np.maximum(nu * nv, 1e-08)
            ang = np.arccos(np.clip(cos, -1.0, 1.0))
            ang = np.where(valid, ang, np.pi)
            k = int(np.argmin(ang))
            if float(ang[k]) < worst_ang:
                worst_ang, worst = (float(ang[k]), (b, k))
    ok = worst_ang >= limit
    return Finding('joint_limits', ok, 0.0 if ok else float(np.clip((limit - worst_ang) / max(limit, 1e-06), 0, 1)), f'smallest angle between adjacent bones {np.rad2deg(worst_ang):.1f} deg (limit {180.0 - max_bend_deg:.1f} deg)', [worst[1]] if not ok else [], [worst[0]] if not ok else [])

def _segment_distance(p1, q1, p2, q2) -> np.ndarray:
    """Batched closest distance between two sets of segments; ``p*``/``q*`` are shaped ``(N,3)``."""
    d1, d2 = (q1 - p1, q2 - p2)
    r = p1 - p2
    a = np.sum(d1 * d1, axis=-1)
    e = np.sum(d2 * d2, axis=-1)
    f = np.sum(d2 * r, axis=-1)
    b = np.sum(d1 * d2, axis=-1)
    c = np.sum(d1 * r, axis=-1)
    denom = np.maximum(a * e - b * b, 1e-12)
    s = np.clip((b * f - c * e) / denom, 0.0, 1.0)
    t = (b * s + f) / np.maximum(e, 1e-12)
    t_cl = np.clip(t, 0.0, 1.0)
    s = np.clip((b * t_cl - c) / np.maximum(a, 1e-12), 0.0, 1.0)
    c1 = p1 + d1 * s[..., None]
    c2 = p2 + d2 * t_cl[..., None]
    return np.linalg.norm(c1 - c2, axis=-1)

def check_self_collision(clip: MotionClip, plan: SkeletonPlan, *, radius_ratio: float=0.035, stride: int=2, margin_ratio: float=0.005, fail_ratio: float=0.01) -> Finding:
    """Skeleton-level self-collision: treat each bone as a capsule of fixed radius and test non-adjacent bones for interpenetration."""
    tpl = clip.template
    parents = np.asarray(tpl.parents)
    bones = [(int(p), j) for j, p in enumerate(parents) if int(p) >= 0]
    if len(bones) < 2:
        return Finding('self_collision', True, 0.0, 'fewer than 2 bones, nothing to check')
    pairs = [(i, k) for i in range(len(bones)) for k in range(i + 1, len(bones)) if not set(bones[i]) & set(bones[k])]
    if not pairs:
        return Finding('self_collision', True, 0.0, 'all bone pairs are adjacent, nothing to check')
    radius = float(radius_ratio) * plan.scale
    margin = float(margin_ratio) * plan.scale
    ia = np.array([bones[i][0] for i, _ in pairs])
    ib = np.array([bones[i][1] for i, _ in pairs])
    ka = np.array([bones[k][0] for _, k in pairs])
    kb = np.array([bones[k][1] for _, k in pairs])
    rest = tpl.rest
    base_d = _segment_distance(rest[ia], rest[ib], rest[ka], rest[kb])
    base_pen = np.maximum(2.0 * radius - base_d, 0.0)
    pos = clip.positions()[::max(int(stride), 1)]
    worst_pen, worst_t, worst_pair = (0.0, -1, (-1, -1))
    hit_frames: list[int] = []
    for t in range(pos.shape[0]):
        d = _segment_distance(pos[t, ia], pos[t, ib], pos[t, ka], pos[t, kb])
        pen = 2.0 * radius - d - base_pen - margin
        k = int(np.argmax(pen))
        if float(pen[k]) > 0:
            hit_frames.append(t * max(int(stride), 1))
        if float(pen[k]) > worst_pen:
            worst_pen = float(pen[k])
            worst_t = t * max(int(stride), 1)
            worst_pair = (int(ib[k]), int(kb[k]))
    ok = worst_pen <= 0.0
    ratio = worst_pen / max(plan.scale, 1e-06)
    n_base = int((base_pen > 0).sum())
    base_note = f', {n_base}/{len(pairs)} pairs already overlap in the rest pose and were discounted' if n_base else ''
    if not ok and ratio < float(fail_ratio):
        return Finding('self_collision', True, 0.0, f'only a light graze of {worst_pen:.4f} ({ratio:.2%} of the characteristic scale, below {fail_ratio:.0%} and within the capsule radius uncertainty) across {len(hit_frames)} frames{base_note}', hit_frames[:8])
    return Finding('self_collision', ok, float(np.clip(ratio * 8.0, 0, 1)) if not ok else 0.0, f'the motion causes a deepest interpenetration of {worst_pen:.4f} ({ratio:.2%} of the characteristic scale) across {len(hit_frames)} frames{base_note}' if not ok else f'no motion-induced bone interpenetration (capsule radius {radius:.3f}{base_note})', hit_frames[:8], list(worst_pair) if not ok else [])

def check_ground(clip: MotionClip, plan: SkeletonPlan, *, tol_ratio: float=0.02) -> Finding:
    """Ground check: whether the lowest joint sits significantly below the ground."""
    pos = clip.positions()
    lowest = pos[..., 1].min(axis=1)
    depth = plan.ground - lowest
    worst = float(depth.max())
    tol = float(tol_ratio) * plan.scale
    ok = worst <= tol
    frames = np.where(depth > tol)[0]
    return Finding('ground', ok, float(np.clip(worst / max(plan.scale, 1e-06) * 5.0, 0, 1)) if not ok else 0.0, f'deepest ground penetration {worst:.4f} (tolerance {tol:.4f}, characteristic scale {plan.scale:.2f})', frames[:8].tolist())

def check_foot_skate(clip: MotionClip, plan: SkeletonPlan, *, contact_ratio: float=0.04, max_skate_per_sec: float=0.3) -> Finding:
    """Foot skate: horizontal sliding speed of grounded limb tips, normalised by the skeleton scale into body lengths per second."""
    if not plan.support_roles or clip.num_frames < 2:
        return Finding('foot_skate', True, 0.0, 'no support limbs or too few frames, skipped')
    tips = [plan.roles[r].joints[-1] for r in plan.support_roles]
    pos = clip.positions()
    basis = plan.support_length if plan.support_length > 1e-06 else plan.scale
    thresh = plan.ground + float(contact_ratio) * basis
    foot_xz = pos[:, tips][:, :, [0, 2]]
    foot_v = np.linalg.norm(np.diff(foot_xz, axis=0), axis=-1) * clip.fps
    root_v = np.linalg.norm(np.diff(pos[:, 0][:, [0, 2]], axis=0), axis=-1) * clip.fps
    down = pos[:, tips, 1] < thresh
    contact = down[1:] & down[:-1]
    if not contact.any():
        return Finding('foot_skate', True, 0.0, f'no continuous ground contact detected (height threshold {thresh:.3f})')
    skate = float(foot_v[contact].mean())
    norm = skate / max(plan.scale, 1e-06)
    root_mean = float(root_v.mean())
    ratio = skate / root_mean if root_mean > 1e-06 else float('inf')
    ok = norm <= max_skate_per_sec
    return Finding('foot_skate', ok, float(np.clip(norm / max(max_skate_per_sec, 1e-06) - 1.0, 0, 1)) if not ok else 0.0, f'grounded foot slides {norm:.2f} body lengths/s (threshold {max_skate_per_sec}; absolute {skate:.3f}, mean root speed {root_mean:.3f}, ratio {ratio:.2f})')

def check_continuity(clip: MotionClip, plan: SkeletonPlan, *, spike_factor: float=3.0, large_factor: float=3.0, min_frames: int=5) -> Finding:
    """Temporal continuity: whether a large isolated single-frame jump appears, which is the seam left by a bad segment join."""
    if clip.num_frames < min_frames:
        return Finding('continuity', True, 0.0, 'too few frames, skipped')
    pos = clip.positions()
    step = np.linalg.norm(np.diff(pos, axis=0), axis=-1).max(axis=1)
    med = float(np.median(step))
    if med < 1e-08:
        worst = float(step.max())
        ok = worst < 1e-06 * max(plan.scale, 1e-06)
        return Finding('continuity', ok, 0.0 if ok else 1.0, f'the clip is nearly static yet shows a single-frame displacement of {worst:.4f}' if not ok else 'the clip is static, with no jumps', [int(np.argmax(step))] if not ok else [])
    if len(step) < 3:
        return Finding('continuity', True, 0.0, 'too few frames to judge isolation, skipped')
    mid = step[1:-1]
    neighbor = np.maximum(np.maximum(step[:-2], step[2:]), med * 0.1)
    iso = mid / neighbor
    large = mid > float(large_factor) * med
    if not large.any():
        return Finding('continuity', True, 0.0, f'no frame stands out in magnitude (largest {float(step.max()) / med:.1f}x the median), so no seam')
    masked = np.where(large, iso, -np.inf)
    k = int(np.argmax(masked))
    ratio = float(iso[k])
    ok = ratio <= spike_factor
    return Finding('continuity', ok, float(np.clip((ratio - spike_factor) / spike_factor, 0, 1)) if not ok else 0.0, f'largest isolated jump {ratio:.2f}x its neighbours (threshold {spike_factor}x, counting only frames above {large_factor}x the median; that frame moves {float(mid[k]):.4f}, median {med:.4f})' + (f', the seam is probably between frames {k + 1} and {k + 2}' if not ok else ''), [k + 1] if not ok else [])

def validate_clip(clip: MotionClip, plan: SkeletonPlan, *, expect_locomotion: bool=False, max_bend_deg: float=165.0, radius_ratio: float=0.035) -> ValidationReport:
    """Run the full set of checks."""
    report = ValidationReport()
    fin = check_finite(clip)
    report.findings.append(fin)
    if not fin.ok:
        report.findings.append(Finding('aborted', False, 1.0, 'remaining checks skipped because nan/inf is present'))
        return report
    report.findings.append(check_bone_lengths(clip))
    report.findings.append(check_joint_limits(clip, plan, max_bend_deg=max_bend_deg))
    report.findings.append(check_self_collision(clip, plan, radius_ratio=radius_ratio))
    report.findings.append(check_ground(clip, plan))
    report.findings.append(check_continuity(clip, plan))
    if expect_locomotion:
        report.findings.append(check_foot_skate(clip, plan))
    else:
        report.findings.append(Finding('foot_skate', True, 0.0, 'not a locomotion clip, so the foot skate check is skipped by convention'))
    return report
