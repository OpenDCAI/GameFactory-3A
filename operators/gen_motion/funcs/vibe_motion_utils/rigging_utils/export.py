'Export a fitted, skinned character and local quaternion animation as GLB.'
from __future__ import annotations

import json
import struct
import numpy as np


def animated_glb(mesh, rig, skin, clip, *, config) -> bytes:
    from ..bvh import validate_hierarchy
    from .templates import require_config, finite_number

    p = require_config(config, ('bind_tolerance', 'sum_tolerance', 'material', 'interpolation'), 'GLB export')
    finite_number(p['bind_tolerance'], 'bind_tolerance')
    finite_number(p['sum_tolerance'], 'sum_tolerance')
    if p['material'] is not None and not isinstance(p['material'], dict):
        raise ValueError('material must be a glTF material dictionary or None')
    if p['interpolation'] not in ('LINEAR', 'STEP'):
        raise ValueError('interpolation must be LINEAR or STEP')
    parents, rest = validate_hierarchy(clip.template.parents, clip.template.rest)
    if not np.array_equal(parents, rig.parents) or not np.allclose(rest, rig.joints, atol=p['bind_tolerance'], rtol=0):
        raise ValueError("clip and rig must share their bind skeleton")
    if list(skin.joint_names) != list(clip.template.joint_names):
        raise ValueError("skin and clip joint names must match")
    v = np.asarray(mesh.vertices, dtype='<f4')
    f = np.asarray(mesh.faces, dtype='<u4')
    w = np.asarray(skin.weights, dtype=float)
    if w.shape != (len(v), len(rest)) or not np.isfinite(w).all() or np.any(w < 0) or not np.allclose(w.sum(1), 1, atol=p['sum_tolerance'], rtol=0):
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
    if np.any(norms < np.finfo(float).eps):
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
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), np.finfo(float).eps)
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
    primitive = {'attributes': attributes, 'indices': accessor(f.reshape(-1), 'SCALAR', 5125)}
    if p['material'] is not None:
        primitive['material'] = 0
        doc['materials'] = [p['material']]
    doc['meshes'] = [{'primitives': [primitive]}]
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
        animation['samplers'].append({'input': times, 'output': values, 'interpolation': p['interpolation']})
    values = accessor(rest[root] + trans, 'VEC3')
    animation['channels'].append({'sampler': len(animation['samplers']), 'target': {'node': root + 2, 'path': 'translation'}})
    animation['samplers'].append({'input': times, 'output': values, 'interpolation': p['interpolation']})
    doc['animations'] = [animation]
    doc['buffers'] = [{'byteLength': len(data)}]
    encoded = json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    encoded += b' ' * (-len(encoded) % 4)
    data.extend(b'\x00' * (-len(data) % 4))
    return (struct.pack('<4sII', b'glTF', 2, 28 + len(encoded) + len(data))
            + struct.pack('<I4s', len(encoded), b'JSON') + encoded
            + struct.pack('<I4s', len(data), b'BIN\x00') + data)


def quantize_weights(mesh, weights):
    """Keep the four largest influences per raw vertex (via ``mesh.weld_map``), as written to JOINTS_0 / WEIGHTS_0.

    Returns ``(joints uint16 (V,4), weights float32 (V,4))``; weights below 1e-6 are zeroed, rows renormalised and the
    joint index of a zero weight is 0.
    """
    raw = np.asarray(weights, float)[np.asarray(mesh.weld_map)]
    order = np.argsort(-raw, axis=1, kind='stable')[:, :4]
    quantized = np.take_along_axis(raw, order, axis=1).astype(np.float32)
    if order.shape[1] < 4:
        pad = ((0, 0), (0, 4 - order.shape[1]))
        order, quantized = np.pad(order, pad), np.pad(quantized, pad)
    joints = order.astype(np.uint16)
    quantized[quantized < 1e-6] = 0
    quantized /= quantized.sum(axis=1, keepdims=True)
    joints[quantized == 0] = 0
    return joints, quantized


def verify_rig(glb: bytes, rig, expect_joints, expect_weights):
    """Read the GLB back and compare joint positions, inverse bind matrices, weights and joint indices.

    Expects one skin with identity rest rotations. Returns ``(report, joint indices, weights)`` read from the file.
    """
    from ..motion_utils.asset import accessor, read_glb
    document, binary = read_glb(glb)
    skins = document.get('skins', [])
    if len(skins) != 1:
        raise ValueError('expected exactly one skin')
    skin, nodes = skins[0], document['nodes']
    names = [nodes[i].get('name') for i in skin['joints']]
    if names != list(rig.joint_names):
        raise ValueError('joint order mismatch')
    ibm = accessor(document, binary, skin['inverseBindMatrices']).reshape(-1, 4, 4).transpose(0, 2, 1)
    parent = {c: i for i, n in enumerate(nodes) for c in n.get('children', [])}
    world = {}

    def position(i):
        if i not in world:
            if nodes[i].get('rotation') and not np.allclose(nodes[i]['rotation'], [0, 0, 0, 1]):
                raise ValueError('rest rotation must be identity')
            world[i] = np.asarray(nodes[i].get('translation', [0, 0, 0]), float) + (position(parent[i]) if i in parent else 0)
        return world[i]
    points = np.stack([position(i) for i in skin['joints']])
    joint_error = float(np.abs(points - rig.joints).max())
    bind = np.tile(np.eye(4), (len(points), 1, 1))
    bind[:, :3, 3] = points
    ibm_error = float(np.abs(np.einsum('nij,njk->nik', bind, ibm) - np.eye(4)).max())
    joint_rows, weight_rows = [], []
    for node in nodes:
        if 'mesh' not in node:
            continue
        if node.get('skin') != 0:
            raise ValueError('mesh node without skin')
        for primitive in document['meshes'][node['mesh']]['primitives']:
            joint_rows.append(accessor(document, binary, primitive['attributes']['JOINTS_0']).astype(np.int64))
            weight_rows.append(accessor(document, binary, primitive['attributes']['WEIGHTS_0']))
    found_joints, found_weights = np.concatenate(joint_rows), np.concatenate(weight_rows)
    expect_joints, expect_weights = np.asarray(expect_joints), np.asarray(expect_weights, float)
    if found_joints.shape != expect_joints.shape:
        raise ValueError('vertex count mismatch')
    weight_error = float(np.abs(found_weights - expect_weights).max())
    same_joints = bool(np.all((found_joints == expect_joints) | (found_weights == 0)))
    sums = float(np.abs(found_weights.sum(1) - 1).max())
    passed = bool(joint_error < 1e-4 * max(1, np.abs(points).max()) and ibm_error < 1e-4 and weight_error < 1e-6
                  and same_joints and sums < 1e-5)
    return {'joints': len(names), 'joint_position_max_error': joint_error, 'ibm_identity_max_error': ibm_error,
            'weights_max_error': weight_error, 'joint_indices_match': same_joints, 'weight_sum_max_error': sums,
            'max_joint_index': int(found_joints.max()), 'passed': passed}, found_joints, found_weights
