"""Skin-weight regions indexed by arbitrary part ID; approximate geometric feedback."""
from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import cKDTree


def vertex_regions(rig, blocks):
    dominant = rig.joints[rig.influences[np.arange(len(rig.vertices)), np.argmax(rig.weights, axis=1)]]
    regions = {'body': np.flatnonzero(np.isin(dominant, [*blocks.trunk, *blocks.head])), 'parts': {}}
    for key, part in blocks.parts.items():
        regions['parts'][key] = {'all': np.flatnonzero(np.isin(dominant, part.joints)),
                                 'distal': np.flatnonzero(np.isin(dominant, part.joints[1:])),
                                 'tip': np.flatnonzero(np.isin(dominant, part.joints[2:]))}
    return regions


def body_envelope(rig, blocks):
    regions = vertex_regions(rig, blocks)
    points = rig.rest_vertices[regions['body']]
    available = bool(len(points))
    if not available:
        points = blocks.rest[list(blocks.trunk)]
    local = (points-blocks.rest[blocks.root]) @ blocks.frame
    return {'lateral_radius': max(float(np.quantile(np.abs(local[:, 0]), .90)), .12*blocks.leg_length),
            'depth_radius': max(float(np.quantile(np.abs(local[:, 2]), .90)), .12*blocks.leg_length),
            'min_height': float(local[:, 1].min()), 'max_height': float(local[:, 1].max()),
            'ground': float(rig.rest_vertices[:, 1].min()), 'regions': regions, 'body_available': available}


def clearance_feedback(rig, blocks, root, world, vertices, envelope, *, arm_keys):
    corrections, penetration, proximity = {}, {}, {}
    torso_root = world[:, blocks.root, :3, 3]
    for key in arm_keys:
        if not envelope['body_available']:
            corrections[key] = np.zeros((len(world), 3))
            proximity[key] = None
            continue
        part = blocks.parts[key]
        js, length = part.joints, blocks.length(key)
        samples = np.linspace(.20, 1., 7)
        points = world[:, js[1], :3, 3][:, None]*(1-samples)[None, :, None]
        points += world[:, js[2], :3, 3][:, None]*samples[None, :, None]
        local = np.einsum('tji,tkj->tki', root['heading'], points-torso_root[:, None])
        rx, rz = envelope['lateral_radius']+.045*blocks.leg_length, envelope['depth_radius']+.045*blocks.leg_length
        ellipse = np.sqrt((local[:, :, 0]/rx)**2+(local[:, :, 2]/rz)**2)
        active = (local[:, :, 1] > envelope['min_height']) & (local[:, :, 1] < envelope['max_height'])
        violation = np.max(np.where(active, np.maximum(1-ellipse, 0.), 0.), axis=1)
        penetration[key] = float(violation.max())
        correction = np.zeros((len(world), 3))
        correction[:, 0] = (1 if part.side == 'L' else -1)*np.minimum(violation*.38*length, .16*length)
        corrections[key] = gaussian_filter1d(correction, 1.3, axis=0, mode='nearest')
        ids = envelope['regions']['parts'][key]['distal']
        distances = [float(cKDTree(vertices[i, envelope['regions']['body']]).query(vertices[i, ids])[0].min())
                     for i in range(len(vertices))] if len(ids) else []
        proximity[key] = min(distances) if distances else None
    return corrections, {'forearm_envelope_violation': penetration,
                         'body_envelope_available': envelope['body_available'],
                         'arm_body_nearest_vertex_distance': proximity,
                         'mesh_min_y': float(vertices[:, :, 1].min()),
                         'ground_penetration': max(0., envelope['ground']-float(vertices[:, :, 1].min()))}


def deformation_metrics(rig, vertices):
    edges = np.unique(np.sort(np.concatenate([rig.faces[:, [0, 1]], rig.faces[:, [1, 2]],
                                              rig.faces[:, [2, 0]]]), axis=1), axis=0)
    baseline = np.linalg.norm(rig.rest_vertices[edges[:, 1]]-rig.rest_vertices[edges[:, 0]], axis=-1)
    valid = baseline > 1e-6
    edges, baseline = edges[valid], baseline[valid]
    lengths = np.linalg.norm(vertices[:, edges[:, 1]]-vertices[:, edges[:, 0]], axis=-1)
    ratio = lengths/baseline
    return {'edge_stretch_p99': float(np.quantile(ratio, .99)),
            'edge_stretch_max': float(ratio.max()), 'edge_compression_p01': float(np.quantile(ratio, .01))}


def _bone_child(rig, js, i):
    if i+1 < len(js):
        return int(js[i+1])
    kids = np.flatnonzero(np.asarray(rig.parents) == js[-1])
    return int(kids[0]) if len(kids) else None


def bone_capsules(rig, blocks, keys):
    """{part ID: [(joint, child, radius)]}: one capsule per bone of each part, radius = 70 % quantile of the distance
    of the joint's dominant skin vertices to the rest bone (skeleton-only rigs: 0.08 leg)."""
    skinned = all(hasattr(rig, a) for a in ('rest_vertices', 'weights', 'influences', 'vertices'))
    dominant = rig.joints[rig.influences[np.arange(len(rig.vertices)), np.argmax(rig.weights, axis=1)]] if skinned else None
    rest = rig.rest_world[:, :3, 3]
    out = {}
    for key in keys:
        js = blocks.parts[key].joints
        caps = []
        for i, j in enumerate(js):
            c = _bone_child(rig, js, i)
            if c is None:
                continue
            a, b = rest[j], rest[c]
            length = float(np.linalg.norm(b-a))
            if length < 1e-9:
                continue
            r = .08*blocks.leg_length
            if skinned:
                v = rig.rest_vertices[dominant == j]
                if len(v) >= 6:
                    u = np.clip((v-a) @ (b-a)/length**2, 0., 1.)
                    r = float(np.quantile(np.linalg.norm(v-(a+u[:, None]*(b-a)), axis=1), .7))
            caps.append((int(j), c, r))
        out[key] = caps
    return out


def capsule_depth(points, a, b, r):
    """points (T,P,3), capsules a/b (T,S,3), radii (S,) -> (depth (T,P) of the deepest capsule, outward push (T,P,3))."""
    ab = b-a
    rel = points[:, :, None]-a[:, None]
    u = np.clip(np.sum(rel*ab[:, None], -1)/np.maximum(np.sum(ab*ab, -1), 1e-18)[:, None], 0., 1.)
    q = a[:, None]+u[..., None]*ab[:, None]
    d = points[:, :, None]-q
    dist = np.linalg.norm(d, axis=-1)
    depth = r[None, None]-dist
    k = np.argmax(depth, axis=2)
    best = np.take_along_axis(depth, k[..., None], 2)[..., 0]
    normal = np.take_along_axis(d, k[..., None, None].repeat(3, -1), 2)[:, :, 0]
    normal = normal/np.maximum(np.linalg.norm(normal, axis=-1, keepdims=True), 1e-12)
    return best, normal*np.maximum(best, 0.)[..., None]


def limb_samples(rig, blocks, world, key, capsules, per_bone=4):
    """points along the distal bones of a limb part (T,P,3) with the limb's own radius per point (P,)."""
    pts, rad = [], []
    for j, c, r in capsules[key][1:] or capsules[key][-1:]:
        for f in np.linspace(.25, 1., per_bone):
            pts.append(world[:, j, :3, 3]*(1-f)+world[:, c, :3, 3]*f)
            rad.append(r)
    return np.stack(pts, axis=1), np.asarray(rad)


def collision_feedback(rig, blocks, world, contacts, capsules, limbs, obstacles, margin, exclude_parent=False):
    """per-limb world push (T,3) out of the obstacle capsules (zero while the limb is planted) and the max depth."""
    pushes, depths = {}, {}
    for key in limbs:
        if not capsules.get(key):
            continue
        pts, own = limb_samples(rig, blocks, world, key, capsules)
        base_parent = int(rig.parents[blocks.parts[key].joints[0]])
        obs = [c for k in obstacles if k != key for c in capsules.get(k, [])
               if not (exclude_parent and c[0] == base_parent)]
        if not obs:
            continue
        a = world[:, [o[0] for o in obs]][:, :, :3, 3]
        b = world[:, [o[1] for o in obs]][:, :, :3, 3]
        r = np.array([o[2] for o in obs])
        depth = np.zeros(pts.shape[:2])
        push = np.zeros(pts.shape)
        for p in range(pts.shape[1]):
            d, v = capsule_depth(pts[:, p:p+1], a, b, r+own[p]+margin)
            depth[:, p], push[:, p] = d[:, 0], v[:, 0]
        worst = np.argmax(depth, axis=1)
        vec = push[np.arange(len(push)), worst]
        if key in contacts:
            vec[contacts[key]] = 0.
        pushes[key] = vec
        depths[key] = float(max(depth.max(), 0.))
    return pushes, depths
