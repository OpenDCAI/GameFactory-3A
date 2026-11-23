"""Skinned rig adapter: a ``CreatureMesh`` plus ``SkinWeights`` behind the interface of ``SkeletonRig``.

``generate_motion`` runs its skin-feedback refinement (envelopes, capsules, body ground, collision) whenever the rig
has a ``skin`` method; this adapter supplies ``vertices`` / ``faces`` / ``weights`` / ``influences`` / ``rest_vertices``
and linear blend skinning in the rest basis (rest joints are pure translations, so the inverse bind matrices are
translations).
"""
from __future__ import annotations

import numpy as np

from .skeleton import SkeletonRig

_CHUNK = 16


class MeshRig(SkeletonRig):
    def __init__(self, parents, rest, names, mesh, skin):
        super().__init__(parents, rest, names)
        vertices = np.asarray(mesh.vertices, float)
        weights = np.asarray(skin.weights, float)
        if weights.shape != (len(vertices), len(self.parents)) or not np.isfinite(weights).all():
            raise ValueError('Skin weights must be finite (V,J) for the mesh and skeleton')
        count = max(1, int(skin.influences.max(initial=1)))
        self.influences, self.weights = skin.to_sparse(count)
        self.vertices = vertices
        self.faces = np.asarray(mesh.faces, int)
        self.ibm = np.tile(np.eye(4), (len(self.parents), 1, 1))
        self.ibm[:, :3, 3] = -np.asarray(rest, float)
        self.rest_vertices = self.skin(self.rest_world)

    def skin(self, world):
        world = np.asarray(world, float)
        batch = world.shape[:-3]
        flat = world.reshape((-1,) + world.shape[-3:])
        homogeneous = np.column_stack([self.vertices, np.ones(len(self.vertices))])
        out = np.zeros((len(flat), len(homogeneous), 3))
        for start in range(0, len(flat), _CHUNK):
            palette = flat[start:start + _CHUNK] @ self.ibm
            for k in range(self.influences.shape[1]):
                m = palette[:, self.influences[:, k], :3, :]
                out[start:start + _CHUNK] += np.einsum('fvij,vj->fvi', m, homogeneous) * self.weights[:, k, None]
        return out.reshape(batch + out.shape[1:])
