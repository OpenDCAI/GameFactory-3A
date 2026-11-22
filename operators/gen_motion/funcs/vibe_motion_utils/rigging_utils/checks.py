"""Separate conservative solid certification from open-mesh enclosure evidence."""
from __future__ import annotations
import numpy as np
from .templates import require_config, finite_number, integer


def validate_tree(rig):
    from ..bvh import validate_hierarchy
    validate_hierarchy(rig.parents, rig.joints)
    if len(rig.joint_names) != len(rig.parents) or len(set(rig.joint_names)) != len(rig.parents):
        raise ValueError('Joint names must be unique and match the hierarchy')
    for name, chain in rig.chains.items():
        if not chain or len(set(chain)) != len(chain) or any(type(i) not in (int, np.int64, np.int32) or i < 0 or i >= len(rig.parents) for i in chain):
            raise ValueError(f'{name}: invalid chain indices')
        if any(rig.parents[b] != a for a, b in zip(chain, chain[1:])):
            raise ValueError(f'{name}: chain must follow parent-child links')
    return True


def _ray_distances(triangles, point, direction, config):
    a, b, c = triangles.transpose(1, 0, 2)
    e1, e2, rel = b - a, c - a, point - a
    h = np.cross(direction, e2)
    det = np.sum(e1 * h, axis=1)
    active = np.abs(det) > config['ray_det_tolerance']
    inv = np.divide(1., det, out=np.zeros_like(det), where=active)
    u = np.sum(rel * h, axis=1) * inv
    q = np.cross(rel, e1)
    v, t = (q @ direction) * inv, np.sum(e2 * q, axis=1) * inv
    tol = config['ray_edge_tolerance']
    return np.sort(t[active & (u >= -tol) & (v >= -tol) & (u + v <= 1 + tol) & (t > config['ray_hit_tolerance'])])


def inside_mesh(mesh, points, *, config):
    """Parity vote diagnostic only; not a closed-solid certificate on open meshes."""
    rays = integer(config['rays'], 'rays', 1)
    triangles = ((mesh.vertices - mesh.center) / mesh.scale)[mesh.faces]
    k = np.arange(rays) + .5
    z, theta = 1 - 2 * k / rays, np.pi * (1 + np.sqrt(5)) * k
    directions = np.column_stack([np.sqrt(1 - z*z)*np.cos(theta), np.sqrt(1 - z*z)*np.sin(theta), z])
    votes = np.zeros(len(points), int)
    for direction in directions:
        for i, point in enumerate((np.asarray(points) - mesh.center) / mesh.scale):
            hits = _ray_distances(triangles, point, direction, config)
            votes[i] += (0 if not len(hits) else 1 + np.count_nonzero(np.diff(hits) > config['ray_merge_tolerance'])) % 2
    return votes > rays // 2


class MeshContainment:
    """Certify only original closed, oriented, convex manifold components; never snap."""
    def __init__(self, mesh, *, config):
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        self.mesh, self.config = mesh, config
        self.vertices = (mesh.vertices - mesh.center) / mesh.scale
        faces = np.asarray(mesh.faces)
        self.triangles = self.vertices[faces]
        normals = np.cross(self.triangles[:, 1] - self.triangles[:, 0], self.triangles[:, 2] - self.triangles[:, 0])
        self.valid = np.linalg.norm(normals, axis=1) > config['containment']['area_epsilon']
        if not self.valid.any():
            raise ValueError('No nondegenerate surface triangles')
        uses, rows, cols = {}, [], []
        for i, triangle in enumerate(faces):
            for a, b in zip(triangle, np.roll(triangle, -1)):
                uses.setdefault(tuple(sorted((a, b))), []).append((i, a, b))
        for edge in uses.values():
            for i, _, _ in edge[1:]:
                rows.extend([edge[0][0], i])
                cols.extend([i, edge[0][0]])
        graph = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(len(faces), len(faces))).tocsr()
        count, groups = connected_components(graph)
        self.components, self.halfspaces = [], []
        tol = config['containment']['halfspace_tolerance']
        for group in range(count):
            ids = np.flatnonzero(groups == group)
            tris = faces[ids]
            vertices = np.unique(tris)
            edges = {tuple(sorted((a, b))) for tri in tris for a, b in zip(tri, np.roll(tri, -1))}
            closed = all(len(uses[e]) == 2 for e in edges)
            oriented = closed and all(uses[e][0][1:] == uses[e][1][1:][::-1] for e in edges)
            manifold = True
            for vertex in vertices:
                link = {}
                for tri in tris[np.any(tris == vertex, axis=1)]:
                    others = tri[tri != vertex]
                    if len(others) != 2:
                        manifold = False
                        break
                    a, b = others
                    link.setdefault(a, set()).add(b)
                    link.setdefault(b, set()).add(a)
                seen, queue = set(), list(link)[:1]
                while queue:
                    item = queue.pop()
                    if item not in seen:
                        seen.add(item)
                        queue.extend(link[item] - seen)
                if not link or len(seen) != len(link) or any(len(v) != 2 for v in link.values()):
                    manifold = False
                    break
            eligible = closed and oriented and manifold and len(vertices) - len(edges) + len(ids) == 2 and self.valid[ids].all()
            certified = False
            if eligible:
                n = normals[ids] / np.linalg.norm(normals[ids], axis=1, keepdims=True)
                center = self.vertices[vertices].mean(0)
                n *= np.where(np.sum(n * (self.triangles[ids, 0] - center), axis=1) >= 0, 1, -1)[:, None]
                bounds = np.sum(n * self.triangles[ids, 0], axis=1)
                certified = bool(np.all(bounds - n @ center > tol) and all(np.max(self.vertices[vertices] @ a - b) <= tol for a, b in zip(n, bounds)))
                if certified:
                    self.halfspaces.append((n, bounds))
            self.components.append({'closed_edges': closed, 'oriented': oriented, 'manifold_vertices': manifold,
                                    'faces': len(ids), 'certified_convex_solid': certified})

    def _closest(self, point):
        """(nearest surface point, original face index, barycentric coordinates, distance), normalized units."""
        triangles = self.triangles[self.valid]
        face_ids = np.flatnonzero(self.valid)
        a, b, c = triangles.transpose(1, 0, 2)
        candidates, barycentrics = [], []
        for start, end, i, j in ((a, b, 0, 1), (b, c, 1, 2), (c, a, 2, 0)):
            edge = end - start
            fraction = np.clip(np.sum((point - start) * edge, axis=1) / np.sum(edge * edge, axis=1), 0., 1.)
            candidates.append(start + fraction[:, None] * edge)
            bary = np.zeros((len(a), 3))
            bary[:, i], bary[:, j] = 1 - fraction, fraction
            barycentrics.append(bary)
        ab, ac, ap = b - a, c - a, point - a
        d00, d01, d11 = np.sum(ab * ab, axis=1), np.sum(ab * ac, axis=1), np.sum(ac * ac, axis=1)
        d20, d21 = np.sum(ap * ab, axis=1), np.sum(ap * ac, axis=1)
        denominator = np.sum(np.cross(ab, ac) ** 2, axis=1)
        v, w = (d11 * d20 - d01 * d21) / denominator, (d00 * d21 - d01 * d20) / denominator
        bary = np.column_stack([1 - v - w, v, w])
        candidates.append(np.einsum('fi,fij->fj', bary, triangles))
        barycentrics.append(bary)
        candidates, barycentrics = np.stack(candidates), np.stack(barycentrics)
        distances = np.sum((candidates - point) ** 2, axis=2)
        distances[3, np.any(bary < 0., axis=1)] = np.inf
        choice, face = np.unravel_index(np.argmin(distances), distances.shape)
        return (candidates[choice, face], int(face_ids[face]), barycentrics[choice, face],
                float(np.sqrt(distances[choice, face])))

    def classify(self, points, names):
        points = np.asarray(points, float)
        if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
            raise ValueError('Expected nonempty finite (J,3) points')
        if len(names) != len(points) or len(set(names)) != len(names):
            raise ValueError('Names must uniquely cover all classified points')
        results, p, scale = {}, self.config['containment'], self.mesh.scale
        for name, point in zip(names, (points - self.mesh.center) / scale):
            closest, face, bary, surface = self._closest(point)
            component = next((i for i, (n, bounds) in enumerate(self.halfspaces)
                              if np.all(n @ point <= bounds - p['halfspace_tolerance'])), None)
            status = ('surface' if surface <= p['surface_tolerance_ratio']
                      else 'inside' if component is not None else 'uncertified')
            results[name] = {'passed': status != 'uncertified', 'status': status, 'surface_distance': surface * scale,
                             'nearest_face': face, 'nearest_barycentric': bary.tolist(),
                             'nearest_surface_point': (closest * scale + self.mesh.center).tolist(),
                             'certified_component': component,
                             'reason': 'actual_triangle_distance' if status == 'surface' else
                                       ('closed_convex_component_halfspaces' if status == 'inside' else
                                        'outside_or_no_certified_closed_component')}
        return {'joints': results, 'components': self.components, 'passed': all(v['passed'] for v in results.values()),
                'surface_tolerance': p['surface_tolerance_ratio'] * scale,
                'interior_policy': 'union_of_original_closed_convex_manifold_components',
                'scope': 'Original convex manifold components only; uncertified does not mean outside. No surface snapping.'}

    def require(self, points, names):
        report = self.classify(points, names)
        if not report['passed']:
            raise JointContainmentError(report)
        return report


class JointContainmentError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__('Joint containment failed: ' + ', '.join(k for k, v in report['joints'].items() if not v['passed']))


def enforce_joint_containment(mesh, rig, *, config, policy='reject', max_correction_ratio=0.):
    """Certify rest joints; optionally project failures onto actual triangles, bounded by mesh scale.

    ``config`` is the rig evaluation config (``containment`` section). Default is fail closed. ``report_only`` keeps
    the joint centers and records directional enclosure evidence. ``project_surface`` is an explicit fallback, not an
    anatomical goal. Returns ``(rig, report)``.
    """
    from dataclasses import replace
    if policy not in ('reject', 'project_surface', 'report_only'):
        raise ValueError('Unknown containment policy')
    if not np.isfinite(max_correction_ratio) or max_correction_ratio < 0:
        raise ValueError('max_correction_ratio must be finite and nonnegative')
    checker = MeshContainment(mesh, config=config)
    before = checker.classify(rig.joints, rig.joint_names)
    if policy == 'report_only':
        report = {**before, 'policy': policy, 'corrections': {}, 'before': before,
                  'enclosure_evidence': enclosure_report(mesh, rig.joints, rig.joint_names, config=config)}
        notes = list(rig.notes) + ['Joint centers preserved; open-mesh enclosure is directional evidence, not certified interior.']
        return replace(rig, joints=rig.joints.copy(), notes=notes), report
    if policy == 'reject' and not before['passed']:
        raise JointContainmentError(before)
    joints, corrections, notes = rig.joints.copy(), {}, list(rig.notes)
    for i, name in enumerate(rig.joint_names):
        item = before['joints'][name]
        if item['passed']:
            continue
        if item['surface_distance'] > max_correction_ratio * mesh.scale:
            item['reason'] = 'surface_projection_exceeds_correction_budget'
            raise JointContainmentError(before)
        joints[i] = item['nearest_surface_point']
        corrections[name] = {'from': rig.joints[i].tolist(), 'to': joints[i].tolist(),
                             'distance': item['surface_distance'], 'face': item['nearest_face']}
        notes.append(f'{name}: uncertified interior; projected to actual triangle {item["nearest_face"]}')
    after = checker.require(joints, rig.joint_names)
    after.update(policy=policy, corrections=corrections, before=before)
    return replace(rig, joints=joints, notes=notes), after


def containment_summary(mesh, rig, *, config):
    """Status counts of the containment certificate; failures are reported, never fatal."""
    try:
        report = MeshContainment(mesh, config=config).classify(rig.joints, rig.joint_names)
    except Exception as error:  # noqa: BLE001 - diagnostics must not abort a build
        return {'error': repr(error)}
    out = {'passed': report['passed'], 'counts': {}, 'joints': {}}
    for name, item in report['joints'].items():
        out['counts'][item['status']] = out['counts'].get(item['status'], 0) + 1
        out['joints'][name] = {'status': item['status'], 'surface_distance': item['surface_distance']}
    return out


def enclosure_evidence(mesh, points, *, config):
    p = config['containment']
    pairs = integer(p['direction_pairs'], 'direction_pairs', 4)
    k = np.arange(pairs) + .5
    y, angle = k / pairs, k*np.pi*(3-np.sqrt(5))
    directions = np.column_stack([np.sqrt(1-y*y)*np.cos(angle), y, np.sqrt(1-y*y)*np.sin(angle)])
    triangles = ((mesh.vertices-mesh.center)/mesh.scale)[mesh.faces]
    coverage = []
    for point in (np.asarray(points)-mesh.center)/mesh.scale:
        hits = []
        for direction in np.concatenate([directions, -directions]):
            distances = _ray_distances(triangles, point, direction, config)
            hits.append(bool(len(distances) and distances[0] <= p['radius_ratio']))
        coverage.append(float((np.asarray(hits[:pairs]) & hits[pairs:]).mean()))
    return np.asarray(coverage)


def enclosure_report(mesh, points, names, *, config):
    """Per-joint local antipodal-ray evidence (NOT an inside/outside predicate); positions are never changed."""
    p = config['containment']
    pairs = integer(p['direction_pairs'], 'direction_pairs', 4)
    k = np.arange(pairs) + .5
    y, angle = k / pairs, k * np.pi * (3 - np.sqrt(5))
    directions = np.column_stack([np.sqrt(1 - y * y) * np.cos(angle), y, np.sqrt(1 - y * y) * np.sin(angle)])
    directions = np.concatenate([directions, -directions])
    triangles = ((mesh.vertices - mesh.center) / mesh.scale)[mesh.faces]
    result = {}
    for name, point in zip(names, (np.asarray(points, float) - mesh.center) / mesh.scale):
        distances = np.full(len(directions), np.inf)
        for i, direction in enumerate(directions):
            hits = _ray_distances(triangles, point, direction, config)
            if len(hits) and hits[0] <= p['radius_ratio']:
                distances[i] = hits[0]
        found = np.isfinite(distances)
        both = found[:pairs] & found[pairs:]
        near, far = distances[:pairs][both], distances[pairs:][both]
        balance = np.minimum(near, far) / np.maximum(near, far)
        result[name] = {'ray_hit_fraction': float(found.mean()), 'opposed_pair_fraction': float(both.mean()),
                        'opposed_distance_balance_mean': float(balance.mean()) if len(balance) else 0.,
                        'nearest_ray_distance': float(distances[found].min() * mesh.scale) if found.any() else None,
                        'certified_inside': False}
    return {'direction_pairs': pairs, 'search_radius': p['radius_ratio'] * mesh.scale,
            'joint_positions_changed': False, 'joints': result,
            'scope': 'Local surface enclosure evidence only; not anatomical truth or a closed-volume certificate.'}


def evaluate_rig(mesh, rig, *, config):
    p = require_config(config, ('rays', 'ray_det_tolerance', 'ray_edge_tolerance', 'ray_hit_tolerance',
                                'ray_merge_tolerance', 'bone_sample_span', 'bone_samples', 'max_outside_joint_ratio',
                                'max_outside_bone_ratio', 'min_bone_length_ratio', 'containment'), 'rig evaluation')
    c = require_config(p['containment'], ('surface_tolerance_ratio', 'area_epsilon', 'halfspace_tolerance',
                                        'direction_pairs', 'radius_ratio', 'policy'), 'containment')
    for key in c.keys() - {'policy', 'direction_pairs'}:
        finite_number(c[key], key, positive=True)
    if c['policy'] not in ('report_only', 'require_certified'):
        raise ValueError('Containment policy must preserve joint centers')
    integer(p['rays'], 'rays', 1)
    integer(p['bone_samples'], 'bone_samples', 1)
    for key in p.keys() - {'rays', 'bone_samples', 'bone_sample_span', 'containment'}:
        finite_number(p[key], key)
    span = np.asarray(p['bone_sample_span'], float)
    if span.shape != (2,) or not 0 <= span[0] <= span[1] <= 1:
        raise ValueError('Invalid bone sample span')
    validate_tree(rig)
    child = np.flatnonzero(rig.parents >= 0)
    u = np.linspace(float(span[0]), float(span[1]), p['bone_samples'])
    samples = (rig.joints[rig.parents[child], None]*(1-u[None, :, None]) + rig.joints[child, None]*u[None, :, None]).reshape(-1, 3)
    votes = inside_mesh(mesh, np.concatenate([rig.joints, samples]), config=p)
    joint_ratio = float(1-votes[:rig.num_joints].mean())
    bone_ratio = float(1-votes[rig.num_joints:].mean()) if len(samples) else 0.
    lengths = np.linalg.norm(rig.joints[child]-rig.joints[rig.parents[child]], axis=1)/mesh.scale
    minimum = float(lengths.min()) if len(lengths) else 0.
    certificate = MeshContainment(mesh, config=p).classify(rig.joints, rig.joint_names)
    coverage = enclosure_evidence(mesh, rig.joints, config=p)
    ok = joint_ratio <= p['max_outside_joint_ratio'] and bone_ratio <= p['max_outside_bone_ratio'] and minimum >= p['min_bone_length_ratio']
    return {'joints': rig.num_joints, 'outside_joint_ratio': joint_ratio, 'outside_bone_sample_ratio': bone_ratio,
            'outside_joints': [rig.joint_names[i] for i in np.flatnonzero(~votes[:rig.num_joints])],
            'min_bone_length_ratio': minimum, 'containment': certificate,
            'enclosure': dict(zip(rig.joint_names, coverage.tolist())), 'joint_positions_changed': False,
            'ok': bool(ok and (c['policy'] == 'report_only' or certificate['passed'])),
            'notes': rig.notes + ['Parity ratios are heuristic diagnostics on open meshes, not certified outside classifications.']}
