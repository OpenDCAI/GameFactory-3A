"""Weight gating helpers: rigid component vote and bleed / fragment / unused-bone checks."""
from __future__ import annotations

import numpy as np
from scipy.sparse.csgraph import connected_components

__all__ = ['mesh_components', 'component_vote', 'related_joints', 'weight_checks']


def mesh_components(mesh):
    """(count, labels) of the edge-connected vertex components."""
    from .skin_generate import _edge_graph
    return connected_components(_edge_graph(mesh), directed=False)


def component_vote(mesh, weights, *, rigid=True, max_fraction=1.0, keep=None):
    """Assign whole connected components to one bone (rigid mechanical / separate parts).

    Components larger than ``max_fraction`` of the vertices keep their weights; ``keep`` lists vertex ids (anchors)
    whose rows are never replaced. Returns ``(weights, number of voted components)``.
    """
    out = np.array(weights, float)
    if not rigid:
        return out, 0
    count, labels = mesh_components(mesh)
    frozen = np.zeros(len(out), bool)
    if keep is not None:
        frozen[np.asarray(keep, int)] = True
    voted = 0
    for c in range(count):
        idx = np.flatnonzero(labels == c)
        if len(idx) > max_fraction * len(out):
            continue
        j = int(np.argmax(out[idx].sum(0)))
        free = idx[~frozen[idx]]
        out[free] = 0
        out[free, j] = 1
        voted += 1
    return out, voted


def related_joints(parents):
    """(J,J) bool: self, parent / child, siblings and grandparent / grandchild."""
    par = np.asarray(parents)
    related = np.zeros((len(par), len(par)), bool)
    for j in range(len(par)):
        related[j, j] = True
        p = par[j]
        if p >= 0:
            related[j, p] = related[p, j] = True
            for s in np.flatnonzero(par == p):
                related[j, s] = True
            if par[p] >= 0:
                related[j, par[p]] = related[par[p], j] = True
    return related


def weight_checks(mesh, rig, weights, *, bone_convention='outgoing'):
    """Report bleed into unrelated bones, fragmented bone regions, influence count and unused bones."""
    from .skin_generate import _edge_graph, bone_distances
    w = np.asarray(weights, float)
    graph = _edge_graph(mesh)
    scale = mesh.scale
    dist = bone_distances(mesh, rig, convention=bone_convention)
    dom = np.argmax(w, 1)
    near = np.argmin(dist, 1)
    bleed = ~related_joints(rig.parents)[dom, near]
    dd, dn = dist[np.arange(len(dom)), dom], dist[np.arange(len(dom)), near]
    severe = bleed & (dd > 2.5 * dn + 0.04 * scale)
    per = {rig.joint_names[j]: int(np.count_nonzero(dom[severe] == j)) for j in np.unique(dom[severe])}
    fragmented = {}
    for j in range(rig.num_joints):
        idx = np.flatnonzero(dom == j)
        if len(idx) < 4:
            continue
        n, lab = connected_components(graph[idx][:, idx], directed=False)
        if n > 1:
            sizes = np.sort(np.bincount(lab))[::-1]
            if sizes[1] >= max(30, 0.25 * sizes[0]):
                # only report pieces that are far apart on the surface (not hair strands / buttons)
                ci = [np.flatnonzero(lab == k) for k in np.argsort(-np.bincount(lab))[:2]]
                gap = float(np.linalg.norm(mesh.vertices[idx[ci[0]]].mean(0) - mesh.vertices[idx[ci[1]]].mean(0)))
                if gap > 0.08 * scale:
                    fragmented[rig.joint_names[j]] = {'sizes': sizes[:4].tolist(), 'centroid_gap_ratio': gap / scale}
    return {'severe_bleed_ratio': float(severe.mean()), 'severe_bleed_by_bone': per,
            'fragmented_bones': fragmented, 'max_influences': int((w > 0).sum(1).max()),
            'unused_bones': [rig.joint_names[j] for j in range(rig.num_joints) if not (w[:, j] > 0.05).any()]}
