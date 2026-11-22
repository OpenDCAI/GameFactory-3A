"""Damped follow-through for attachment chains (cloth, hair, ribbons, tails, wings, antennae, tentacles).

A spring part's chain is first posed kinematically (its optional driven rotation and everything upstream). Each
bone tip is then a particle pulled toward that driven position by a damped spring (frequency Hz, damping ratio),
plus optional gravity (fraction of earth gravity, assuming a 0.9 m leg) and wind (leg lengths/s^2 in the body
frame); bone lengths are kept by projection and the deviation from the driven direction is capped by limit_degrees.
The joints are finally rotated so the chain passes through the simulated points. Deterministic and sampled on the
given time grid (generate_motion uses rhythm.sample()).
"""
from __future__ import annotations

import math

import numpy as np

from .kinematics import set_world_rotation, unit


def _align(a, b):
    """Batched minimal rotation matrices taking directions a -> b."""
    a, b = unit(a), unit(b)
    v = np.cross(a, b)
    c = np.clip(np.sum(a*b, axis=-1), -1., 1.)
    s = np.linalg.norm(v, axis=-1)
    k = np.zeros(a.shape[:-1]+(3, 3))
    k[..., 0, 1], k[..., 0, 2], k[..., 1, 0] = -v[..., 2], v[..., 1], v[..., 2]
    k[..., 1, 2], k[..., 2, 0], k[..., 2, 1] = -v[..., 0], -v[..., 1], v[..., 0]
    factor = np.where(s > 1e-9, (1-c)/np.maximum(s*s, 1e-18), .5)
    out = np.eye(3)+k+k @ k*factor[..., None, None]
    flip = c < -1+1e-9
    if np.any(flip):
        axis = unit(np.cross(a[flip], np.where(np.abs(a[flip][:, :1]) < .9, [[1., 0, 0]], [[0, 1., 0]])))
        out[flip] = 2*np.einsum('ti,tj->tij', axis, axis)-np.eye(3)
    return out


def _cap(direction, reference, limit):
    """Rotate direction toward reference so the angle between them is <= limit (radians)."""
    d, r = unit(direction), unit(reference)
    angle = np.arccos(np.clip(np.sum(d*r, axis=-1), -1., 1.))
    over = angle > limit
    if not np.any(over):
        return d
    axis = np.cross(r[over], d[over])
    norm = np.linalg.norm(axis, axis=-1, keepdims=True)
    axis = np.where(norm > 1e-9, axis/np.maximum(norm, 1e-12), unit(np.cross(r[over], [[0., 1, 0]])+1e-9))
    rr = r[over]
    rotated = rr*math.cos(limit)+np.cross(axis, rr)*math.sin(limit)+axis*np.sum(axis*rr, axis=-1, keepdims=True)*(1-math.cos(limit))
    d = d.copy()
    d[over] = rotated
    return d


def _push_out(point, capsules, radius):
    """move one point out of capsules [(a (3,), b (3,), r)] (closest-point projection, a few passes)."""
    for a, b, r in capsules:
        ab = b-a
        u = float(np.clip(np.dot(point-a, ab)/max(float(ab @ ab), 1e-18), 0., 1.))
        q = a+u*ab
        d = point-q
        n = float(np.linalg.norm(d))
        need = r+radius
        if n < need:
            if n < 1e-9:
                d = np.cross(ab, [0., 1., 0.]) if np.linalg.norm(np.cross(ab, [0., 1., 0.])) > 1e-9 else np.array([1., 0, 0])
                n = float(np.linalg.norm(d))
            point = q+d/n*need
    return point


def apply_springs(rig, blocks, style, times, root, quats, translations, ground=None, capsules=None):
    """capsules: {part ID: [(joint, child, radius)]} used by spring 'collide' lists; ground: floor height."""
    leg = blocks.leg_length
    for key, spec in style.program.select('spring').items():
        p = spec['params']
        chain = list(blocks.parts[key].joints)
        kids = [int(c) for c in np.flatnonzero(np.asarray(rig.parents) == chain[-1])]
        nodes = chain+kids[:1]
        if len(nodes) < 2:
            continue
        world = rig.fk(quats, translations)
        driven = world[:, nodes][:, :, :3, 3]
        rest = rig.rest_world[:, :3, 3]
        lengths = np.linalg.norm(np.diff(rest[nodes], axis=0), axis=-1)
        omega = 2*math.pi*float(p['frequency'])
        zeta = float(p['damping'])
        gravity = np.array([0., -float(p.get('gravity', 0.))*9.81/0.9*leg, 0.])
        wind_body = np.asarray(p.get('wind', [0., 0., 0.]), float)*leg
        limit = math.radians(float(p.get('limit_degrees', 75.)))
        floor = None
        if p.get('ground', False) is not False and ground is not None:
            floor = ground+(0. if p['ground'] is True else float(p['ground']))*leg
        solids = [c for k in p.get('collide', []) for c in (capsules or {}).get(k, [])]
        radius = float(p.get('radius', .03))*leg
        constrained = floor is not None or bool(solids)
        x = driven[0].copy()
        v = np.zeros_like(x)
        sim = np.empty_like(driven)
        sim[0] = x
        for i in range(1, len(times)):
            dt = float(times[i]-times[i-1])
            steps = max(1, int(math.ceil(dt*float(p['frequency'])*8)))
            h = dt/steps
            wind = root['heading'][i] @ wind_body
            here = [(world[i, a, :3, 3], world[i, b, :3, 3], r) for a, b, r in solids]
            for s in range(1, steps+1):
                a = s/steps
                target = (1-a)*driven[i-1]+a*driven[i]
                before = x.copy()
                acc = omega*omega*(target-x)-2*zeta*omega*v+gravity+wind
                x = x+(v+acc*h)*h
                x[0] = target[0]
                for k in range(1, len(nodes)):
                    seg = _cap(x[k:k+1]-x[k-1:k], target[k:k+1]-target[k-1:k], limit)[0]
                    x[k] = x[k-1]+seg*lengths[k-1]
                    if constrained:
                        for _ in range(2):
                            if floor is not None and x[k, 1] < floor+radius:
                                x[k, 1] = floor+radius
                            if here:
                                x[k] = _push_out(x[k], here, radius)
                            x[k] = x[k-1]+unit(x[k]-x[k-1])*lengths[k-1]
                v = (x-before)/h
                v[0] = 0.
            sim[i] = x
        for k in range(len(nodes)-1):
            world = rig.fk(quats, translations)
            joint, child = nodes[k], nodes[k+1]
            current = world[:, child, :3, 3]-world[:, joint, :3, 3]
            desired = sim[:, k+1]-world[:, joint, :3, 3]
            rotation = _align(current, desired)
            set_world_rotation(rig, quats, translations, joint, rotation @ world[:, joint, :3, :3])
