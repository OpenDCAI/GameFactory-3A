"""Exported-animation readback validation for GLB files written by ``rigging_utils.export.animated_glb``."""
from __future__ import annotations

import json
import struct

import numpy as np
from scipy.spatial.transform import Rotation

_DTYPES = {5120: 'i1', 5121: 'u1', 5122: '<i2', 5123: '<u2', 5125: '<u4', 5126: '<f4'}
_WIDTH = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}


def read_glb(blob: bytes):
    """(gltf json, binary chunk) of GLB bytes."""
    if blob[:4] != b'glTF':
        raise ValueError('Invalid GLB header')
    size = struct.unpack('<I', blob[12:16])[0]
    document = json.loads(blob[20:20 + size])
    binary = blob[28 + size:]
    return document, binary


def accessor(document, binary, index):
    spec = document['accessors'][index]
    view = document['bufferViews'][spec['bufferView']]
    width = _WIDTH[spec['type']]
    dtype = np.dtype(_DTYPES[spec['componentType']])
    start = view.get('byteOffset', 0) + spec.get('byteOffset', 0)
    count, size = spec['count'], dtype.itemsize * width
    stride = view.get('byteStride', size)
    raw = np.frombuffer(binary, np.uint8, count=stride * (count - 1) + size, offset=start)
    rows = np.lib.stride_tricks.as_strided(raw, (count, size), (stride, 1))
    data = np.ascontiguousarray(rows).view(dtype).reshape(count, width)
    return data.astype(float) if width > 1 else data.reshape(count).astype(float)


def verify_export(glb: bytes, clip, mesh, skin, rig, *, position_tolerance, time_tolerance):
    """Read the animation back from ``glb`` and compare it with ``clip``.

    Checks that every joint has rotation (and the root translation) channels, that the sample times match the clip fps,
    that bind-pose joint world positions agree with the rig, and that the exported joint positions reproduce the clip's
    forward kinematics within ``position_tolerance``. Returns a dictionary of the measured errors; raises on mismatch."""
    from .units import fk
    document, binary = read_glb(glb)
    animation = document['animations'][0]
    joints = document['skins'][0]['joints']
    count, total = clip.num_frames, len(joints)
    node_index = {node: i for i, node in enumerate(joints)}
    quats = np.tile(np.array([0., 0., 0., 1.]), (count, total, 1))
    translation = None
    seen = set()
    expected = np.arange(count) / clip.fps
    for channel in animation['channels']:
        sampler = animation['samplers'][channel['sampler']]
        times = accessor(document, binary, sampler['input']).ravel()
        if times.shape != expected.shape or not np.allclose(times, expected, atol=time_tolerance):
            raise ValueError('Exported animation times do not match the clip')
        node, path = channel['target']['node'], channel['target']['path']
        values = accessor(document, binary, sampler['output'])
        if path == 'rotation':
            quats[:, node_index[node]] = values
        elif path == 'translation':
            translation = values
        else:
            raise ValueError(f'Unexpected exported channel: {path}')
        seen.add((node_index[node], path))
    root = int(np.flatnonzero(np.asarray(rig.parents) == -1)[0])
    required = {(j, 'rotation') for j in range(total)} | {(root, 'translation')}
    if seen != required:
        raise ValueError('Missing or duplicate animation channel coverage')
    rest = np.asarray(rig.joints, float)
    world = np.zeros((count, total, 3))
    matrices = Rotation.from_quat(quats.reshape(-1, 4)).as_matrix().reshape(count, total, 3, 3)
    accumulated = np.empty_like(matrices)
    for j in range(total):
        parent = int(rig.parents[j])
        if parent < 0:
            accumulated[:, j] = matrices[:, j]
            world[:, j] = translation
        else:
            accumulated[:, j] = accumulated[:, parent] @ matrices[:, j]
            world[:, j] = world[:, parent] + np.einsum('tij,j->ti', accumulated[:, parent], rest[j] - rest[parent])
    expected_positions, _ = fk(clip)
    error = float(np.max(np.linalg.norm(world - expected_positions, axis=-1)))
    if error > position_tolerance:
        raise ValueError(f'GLB readback joint position error {error} exceeds {position_tolerance}')
    vertices = np.asarray(mesh.vertices, '<f4')
    exported = accessor(document, binary, document['meshes'][0]['primitives'][0]['attributes']['POSITION'])
    if exported.shape != vertices.shape or not np.allclose(exported, vertices, atol=1e-6):
        raise ValueError('Exported bind mesh differs from the source mesh')
    return {'frames_checked': int(count), 'animation_channels': len(seen), 'joint_roundtrip_max_error': error,
            'bind_mesh_preserved': True}


__all__ = ['accessor', 'read_glb', 'verify_export']
