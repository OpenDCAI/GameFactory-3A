'Export a fitted, skinned character and local quaternion animation as GLB.'
from __future__ import annotations

import json
import struct
import numpy as np


def animated_glb(mesh, rig, skin, clip) -> bytes:
    'Serialize matching mesh, weights, bind skeleton and MotionClip as glTF 2.0.'
    from ..bvh import validate_hierarchy

    parents, rest = validate_hierarchy(clip.template.parents, clip.template.rest)
    if not np.array_equal(parents, rig.parents) or not np.allclose(rest, rig.joints, atol=1e-6):
        raise ValueError("clip and rig must share their bind skeleton")
    if list(skin.joint_names) != list(clip.template.joint_names):
        raise ValueError("skin and clip joint names must match")
    v = np.asarray(mesh.vertices, dtype='<f4')
    f = np.asarray(mesh.faces, dtype='<u4')
    w = np.asarray(skin.weights, dtype=float)
    if w.shape != (len(v), len(rest)) or not np.isfinite(w).all() or np.any(w < 0) or not np.allclose(w.sum(1), 1):
        raise ValueError("weights must be finite normalized (vertices,joints)")
    if np.any(np.count_nonzero(w, axis=1) > 4) or len(rest) > 65535:
        raise ValueError("GLB export supports four influences and 65535 joints")
    q = np.asarray(clip.quats, dtype=float)
    trans = np.asarray(clip.trans, dtype=float)
    if q.ndim != 3 or q.shape[1:] != (len(rest), 4) or not len(q) or trans.shape != (len(q), 3):
        raise ValueError("clip rotation and translation shapes do not match")
    if not np.isfinite(q).all() or not np.isfinite(trans).all() or not np.isfinite(clip.fps) or clip.fps <= 0:
        raise ValueError("clip values must be finite and fps positive")
    norms = np.linalg.norm(q, axis=-1, keepdims=True)
    if np.any(norms < 1e-12):
        raise ValueError("zero quaternion in clip")
    q = q / norms
    for frame in range(1, len(q)):
        flip = np.sum(q[frame - 1] * q[frame], axis=-1) < 0
        q[frame, flip] *= -1

    data = bytearray()
    doc = {"asset": {"version": "2.0", "generator": "GameFactory/VibeMotion"},
           "scene": 0, "scenes": [{"nodes": [0, 1]}], "nodes": [],
           "bufferViews": [], "accessors": [], "meshes": [], "skins": [], "animations": []}

    def accessor(array, kind, component=5126, bounds=False):
        'Append a tightly packed, four-byte-aligned glTF accessor.'
        array = np.ascontiguousarray(array, dtype={5126: '<f4', 5125: '<u4', 5123: '<u2'}[component])
        data.extend(b'\x00' * (-len(data) % 4))
        view = len(doc['bufferViews'])
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(data), 'byteLength': array.nbytes})
        data.extend(array.tobytes())
        item = {'bufferView': view, 'componentType': component, 'count': len(array), 'type': kind}
        if bounds:
            item.update(min=np.atleast_1d(array.min(axis=0)).tolist(), max=np.atleast_1d(array.max(axis=0)).tolist())
        doc['accessors'].append(item)
        return len(doc['accessors']) - 1

    normals = np.zeros_like(v)
    fn = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    for i in range(3):
        np.add.at(normals, f[:, i], fn)
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-12)
    order = np.argsort(-w, axis=1, kind='stable')[:, :4]
    weights = np.take_along_axis(w, order, axis=1)
    if order.shape[1] < 4:
        pad = ((0, 0), (0, 4 - order.shape[1]))
        order, weights = np.pad(order, pad), np.pad(weights, pad)
    weights /= weights.sum(1, keepdims=True)
    attributes = {'POSITION': accessor(v, 'VEC3', bounds=True),
                  'NORMAL': accessor(normals, 'VEC3'),
                  'JOINTS_0': accessor(order, 'VEC4', 5123),
                  'WEIGHTS_0': accessor(weights, 'VEC4')}
    doc['meshes'] = [{'primitives': [{'attributes': attributes, 'indices': accessor(f.reshape(-1), 'SCALAR', 5125), 'material': 0}]}]
    doc['materials'] = [{'name': 'Preview surface', 'doubleSided': True,
                         'pbrMetallicRoughness': {'baseColorFactor': [.16, .65, .61, 1], 'metallicFactor': .08, 'roughnessFactor': .65}}]
    root = int(np.flatnonzero(parents == -1)[0])
    doc['nodes'] = [{'name': mesh.name, 'mesh': 0, 'skin': 0}, {'name': 'Skeleton', 'children': [root + 2]}]
    for j, parent in enumerate(parents):
        node = {'name': str(rig.joint_names[j]), 'translation': (rest[j] if parent < 0 else rest[j] - rest[parent]).tolist()}
        children = (np.flatnonzero(parents == j) + 2).tolist()
        if children:
            node['children'] = children
        doc['nodes'].append(node)
    inverse = np.tile(np.eye(4), (len(rest), 1, 1))
    inverse[:, :3, 3] = -rest
    doc['skins'] = [{'joints': list(range(2, len(rest) + 2)), 'skeleton': root + 2,
                      'inverseBindMatrices': accessor(inverse.transpose(0, 2, 1).reshape(-1, 16), 'MAT4')}]
    times = accessor(np.arange(len(q)) / clip.fps, 'SCALAR', bounds=True)
    animation = {'name': 'motion', 'samplers': [], 'channels': []}
    for j in range(len(rest)):
        values = accessor(q[:, j, [1, 2, 3, 0]], 'VEC4')
        animation['channels'].append({'sampler': len(animation['samplers']), 'target': {'node': j + 2, 'path': 'rotation'}})
        animation['samplers'].append({'input': times, 'output': values, 'interpolation': 'LINEAR'})
    values = accessor(rest[root] + trans, 'VEC3')
    animation['channels'].append({'sampler': len(animation['samplers']), 'target': {'node': root + 2, 'path': 'translation'}})
    animation['samplers'].append({'input': times, 'output': values, 'interpolation': 'LINEAR'})
    doc['animations'] = [animation]
    doc['buffers'] = [{'byteLength': len(data)}]
    encoded = json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    encoded += b' ' * (-len(encoded) % 4)
    data.extend(b'\x00' * (-len(data) % 4))
    return (struct.pack('<4sII', b'glTF', 2, 28 + len(encoded) + len(data))
            + struct.pack('<I4s', len(encoded), b'JSON') + encoded
            + struct.pack('<I4s', len(data), b'BIN\x00') + data)
