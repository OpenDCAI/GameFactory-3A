'Procedural, untextured mesh fixtures for local rigging examples.'
from __future__ import annotations

import numpy as np
from .mesh import CreatureMesh


def _ellipsoid(center, radii, rings=16, segments=20):
    'Generate a closed triangulated ellipsoid with shared ring vertices.'
    center, radii = np.asarray(center), np.asarray(radii)
    vertices = [center + radii * [0, 1, 0]]
    for row in range(1, rings):
        a = np.pi * row / rings
        for column in range(segments):
            b = 2 * np.pi * column / segments
            vertices.append(center + radii * [np.sin(a) * np.cos(b), np.cos(a), np.sin(a) * np.sin(b)])
    vertices.append(center + radii * [0, -1, 0])
    bottom = len(vertices) - 1
    faces = []
    for column in range(segments):
        nxt = (column + 1) % segments
        faces.append([0, 1 + nxt, 1 + column])
        for row in range(rings - 2):
            a, b = 1 + row * segments + column, 1 + row * segments + nxt
            faces.extend([[a, b, a + segments], [b, b + segments, a + segments]])
        start = 1 + (rings - 2) * segments
        faces.append([bottom, start + column, start + nxt])
    return np.asarray(vertices), np.asarray(faces, dtype=np.int64)


def creature_mesh(name: str) -> CreatureMesh:
    'Build a geometric human/fox/fish fixture without files or downloads.'
    if name == "human":
        parts = [((0, 1.12, 0), (.23, .38, .13)), ((0, 1.67, 0), (.13, .17, .13)),
                 ((0, 1.47, 0), (.08, .12, .08))]
        for side in [-1, 1]:
            parts.extend([((side * .48, 1.37, 0), (.31, .078, .08)),
                          ((side * .13, .48, 0), (.087, .46, .09)),
                          ((side * .13, .065, .055), (.09, .065, .15))])
    elif name == "fox":
        parts = [((0, .65, 0), (.25, .23, .55)), ((0, .8, .63), (.18, .2, .25)),
                 ((0, .66, -.7), (.11, .11, .36))]
        parts += [((x, .29, z), (.075, .28, .075)) for x in [-.18, .18] for z in [-.35, .35]]
    elif name == "fish":
        parts = [((0, 0, 0), (.13, .24, .6))]
    else:
        raise ValueError("creature must be human, fox or fish; supply a mesh file for other characters")
    vertices, faces, offset = [], [], 0
    for center, radii in parts:
        v, f = _ellipsoid(center, radii)
        vertices.append(v)
        faces.append(f + offset)
        offset += len(v)
    v = np.concatenate(vertices)
    return CreatureMesh(name + "_procedural", v, np.concatenate(faces), np.arange(len(v)))
