"""Isolated Blender worker for wearable_fit. Coordinates here are Z-up metres."""
from __future__ import annotations

import json
import heapq
import math
from pathlib import Path
import sys
import time

import bpy
from mathutils import Matrix, Vector, Euler
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform
import numpy as np


def import_asset(path):
    before = set(bpy.data.objects)
    if Path(path).suffix.lower() == '.fbx':
        bpy.ops.import_scene.fbx(filepath=path, use_anim=False)
    else:
        bpy.ops.import_scene.gltf(filepath=path)
    return list(set(bpy.data.objects) - before)


def coords(obj, evaluated=False):
    mesh = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data if evaluated else obj.data
    values = np.empty((len(mesh.vertices), 3), dtype=np.float64)
    mesh.vertices.foreach_get('co', values.ravel())
    matrix = np.asarray(obj.matrix_world)
    return values @ matrix[:3, :3].T + matrix[:3, 3]


def set_coords(obj, world):
    inverse = np.asarray(obj.matrix_world.inverted())
    local = world @ inverse[:3, :3].T + inverse[:3, 3]
    obj.data.vertices.foreach_set('co', local.ravel())
    obj.data.update()


def bone(rig, name, side=None):
    aliases = {'arm': ('arm', 'upperarm'), 'forearm': ('forearm', 'lowerarm'),
               'upleg': ('upleg', 'upperleg', 'thigh'), 'leg': ('leg', 'lowerleg', 'calf'),
               'toebase': ('toebase', 'toe', 'toes')}
    for b in rig.pose.bones:
        key = b.name.lower().split(':')[-1].replace('mixamorig', '')
        key = key.replace('_', '').replace('.', '').replace('-', '')
        candidates = aliases.get(name, (name,))
        prefixes = (side, side[0]) if side else ('',)
        if any(key in (s + n, n + s) for n in candidates for s in prefixes):
            return b
    raise ValueError(f"Armature {rig.name} has no {side or ''}{name} bone")


def head(rig, name, side=None):
    return rig.matrix_world @ bone(rig, name, side).head


def transform_roots(objects, matrix):
    collection = set(objects)
    roots = [o for o in objects if o.parent not in collection]
    for obj in roots:
        obj.matrix_world = matrix @ obj.matrix_world
    bpy.context.view_layer.update()


def canonical_body(objects, rig, height):
    # Derive orientation from bones before normalizing standing height.
    feet = (head(rig, 'foot', 'left') + head(rig, 'foot', 'right')) * .5
    up = head(rig, 'head') - feet
    axis = max(range(3), key=lambda i: abs(up[i]))
    up = Vector(tuple((1 if up[i] >= 0 else -1) if i == axis else 0 for i in range(3)))
    forward = sum((head(rig, 'toebase', s) - head(rig, 'foot', s)
                   for s in ('left', 'right')), Vector())
    forward -= up * forward.dot(up)
    if forward.length < 1e-6:
        raise ValueError('Foot/toe bones do not establish a forward direction')
    forward.normalize()
    lateral = up.cross(forward).normalized()
    rotation = Matrix((lateral, -forward, up)).to_4x4()
    transform_roots(objects, rotation)
    points = np.concatenate([coords(o) for o in objects if o.type == 'MESH'])
    low, high = points.min(0), points.max(0)
    scale = height / (high[2] - low[2])
    origin = Vector(((low[0] + high[0]) * .5, (low[1] + high[1]) * .5, low[2]))
    transform_roots(objects, Matrix.Scale(scale, 4) @ Matrix.Translation(-origin))


def pose_sleeves(rig, pose):
    if pose == 't':
        directions = ((1, 0, 0), (1, 0, 0))
    elif pose == 'a':
        directions = ((.85, 0, -1), (.85, 0, -1))
    else:
        directions = ((.35, 0, -1), (-.12, 0, -1))
    for side in ('left', 'right'):
        # Determine handedness from the bone position.
        sign = 1 if head(rig, 'arm', side).x > 0 else -1
        for name, direction in zip(('arm', 'forearm'), directions):
            pb = bone(rig, name, side)
            world_head = rig.matrix_world @ pb.head
            current = (rig.matrix_world.to_3x3() @ (pb.tail - pb.head)).normalized()
            target = Vector((sign * direction[0], direction[1], direction[2])).normalized()
            rotation = current.rotation_difference(target).to_matrix().to_4x4()
            world = Matrix.Translation(world_head) @ rotation @ Matrix.Translation(-world_head)
            pb.matrix = rig.matrix_world.inverted() @ world @ rig.matrix_world @ pb.matrix
            bpy.context.view_layer.update()


def snapshot_body(meshes):
    copies = []
    graph = bpy.context.evaluated_depsgraph_get()
    for obj in meshes:
        mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(graph), depsgraph=graph)
        copy = bpy.data.objects.new('FitSurface', mesh)
        bpy.context.collection.objects.link(copy)
        copy.matrix_world = obj.matrix_world.copy()
        for group in obj.vertex_groups:
            copy.vertex_groups.new(name=group.name)
        copies.append(copy)
    bpy.ops.object.select_all(action='DESELECT')
    for obj in copies:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = copies[0]
    bpy.ops.object.join()
    return copies[0]


def surface_tree(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    points = coords(obj)
    triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
    return {'tree': BVHTree.FromPolygons(points.tolist(), triangles, all_triangles=True),
            'points': points, 'triangles': np.asarray(triangles)}


def clearance_pass(obj, surface, clearance, height, iterations=2, protected_below=None):
    """Push cloth vertices out along the nearest body normal."""
    vertices = coords(obj)
    original = vertices.copy()
    max_search = height * .08
    initial = None
    moved = np.zeros(len(vertices), dtype=bool)
    tree = surface['tree']
    for step in range(iterations + 1):
        inside = near = 0
        next_vertices = vertices.copy()
        for i, point in enumerate(vertices):
            # Ignore nearest normals below the open footwear cut.
            if protected_below is not None and original[i, 2] < protected_below:
                continue
            hit, normal, _, distance = tree.find_nearest(Vector(point), max_search)
            if hit is None:
                continue
            signed = (Vector(point) - hit).dot(normal)
            inside += signed < -0.0005
            if signed < clearance - 0.0001:
                near += 1
                if step < iterations:
                    next_vertices[i] = np.asarray(hit + normal * clearance)
                    moved[i] = True
        if initial is None:
            initial = inside
        vertices = next_vertices
    set_coords(obj, vertices)
    return {'near_inside_before': int(initial), 'near_inside_after': int(inside),
            'clearance_violations_after': int(near), 'corrected_vertices': int(moved.sum()),
            'max_displacement_m': float(np.linalg.norm(vertices-original, axis=1).max()),
            'search_distance_m': max_search, 'sample_count': len(vertices)}



def refresh_normals(obj):
    """Recompute deformation normals across UV seams, retaining authored creases."""
    mesh = obj.data
    mesh.calc_loop_triangles()
    positions = np.empty((len(mesh.vertices), 3))
    mesh.vertices.foreach_get('co', positions.ravel())
    loops = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get('vertex_index', loops)
    old = np.empty((len(mesh.corner_normals), 3))
    mesh.corner_normals.foreach_get('vector', old.ravel())
    vertex_normals = np.zeros_like(positions)
    vertex_normals[loops] = old
    keys = np.column_stack((np.round(positions, 6), np.round(vertex_normals, 3)))
    unique, inverse = np.unique(keys, axis=0, return_inverse=True)
    triangles = np.array([tuple(t.vertices) for t in mesh.loop_triangles])
    a,b,c = (positions[triangles[:,i]] for i in range(3))
    face_normals = np.cross(b-a,c-a)
    normals = np.zeros((len(unique),3))
    for i in range(3):
        np.add.at(normals,inverse[triangles[:,i]],face_normals)
    normals /= np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-20)
    mesh.normals_split_custom_set_from_vertices(normals[inverse].tolist())



def prepare_garment(objects, config, target, rig):
    meshes = [o for o in objects if o.type == 'MESH']
    if not meshes:
        raise ValueError('Clothing has no meshes')
    # Bake transforms and modifiers while retaining UVs and material slots.
    graph = bpy.context.evaluated_depsgraph_get()
    copies = []
    for obj in meshes:
        mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(graph), depsgraph=graph)
        copy = bpy.data.objects.new('Clothing', mesh)
        bpy.context.collection.objects.link(copy)
        copy.matrix_world = obj.matrix_world.copy()
        copies.append(copy)
    bpy.ops.object.select_all(action='DESELECT')
    for obj in copies:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = copies[0]
    bpy.ops.object.join()
    garment = copies[0]
    vertices = coords(garment)
    rotation = np.asarray(Euler(tuple(math.radians(v) for v in config['clothing_rotation']), 'XYZ').to_matrix())
    vertices = vertices @ rotation.T
    low, high = vertices.min(0), vertices.max(0)
    span = high[2] - low[2]
    if span < 1e-8:
        raise ValueError('Clothing has no vertical extent; check clothing_rotation')
    vertices[:, :2] -= (low[:2] + high[:2]) * .5
    vertices[:, 2] = (vertices[:, 2] - low[2]) / span
    source = config['source_heights']
    names = list(source)
    source_z = [0.] + [source[n] for n in names] + [1.]
    bottom = 0. if config['coverage'] == 'full_body' else target['hip'] - .01 * config['height_metres']
    top = config['height_metres'] if config['coverage'] == 'full_body' else target['neck'] + .02 * config['height_metres']
    target_z = [bottom] + [target[n] for n in names] + [top]
    if any(a >= b for a, b in zip(target_z, target_z[1:])):
        raise ValueError(f'Body landmarks are not vertically ordered: {dict(zip(names,target_z[1:-1]))}')
    normalized = vertices.copy()
    normalized[:, :2] /= span
    vertices[:, :2] *= (top - bottom) / span
    vertices[:, 2] = np.interp(vertices[:, 2], source_z, target_z)
    # Align garment depth with the torso center.
    vertices[:, 1] += target['depth']
    arm_regions = align_sleeves(vertices, normalized, garment.data, config, rig, source_z, target_z, top - bottom, target['depth'])
    vertices[:,2] += config.get('headwear_offset_metres',0.) * smoothstep((normalized[:,2]-source['neck'])/.03)
    garment.parent = None
    garment.matrix_world = Matrix.Identity(4)
    set_coords(garment, vertices)
    garment.name = 'FittedClothing'
    garment.vertex_groups.clear()
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    return garment, arm_regions, {'source_height_fractions': source, 'target_heights_m': dict(zip(names, target_z[1:-1]))}


def smoothstep(value):
    value = np.clip(value, 0, 1)
    return value * value * (3 - 2 * value)


def geodesic_sleeves(source, mesh, config, shoulder_x, shoulder_z, wrist_z):
    """Separate sleeves from vest flaps along the surface, across UV seams."""
    unique, inverse = np.unique(np.round(source, 6), axis=0, return_inverse=True)
    edges = np.empty((len(mesh.edges), 2), dtype=np.int64)
    mesh.edges.foreach_get('vertices', edges.ravel())
    edges = np.unique(np.sort(inverse[edges], axis=1), axis=0)
    lengths = np.linalg.norm(unique[edges[:, 0]] - unique[edges[:, 1]], axis=1)
    graph = [[] for _ in unique]
    for (a, b), length in zip(edges, lengths):
        if a != b:
            graph[a].append((int(b), float(length)))
            graph[b].append((int(a), float(length)))
    ax, z = abs(unique[:, 0]), unique[:, 2]
    core = (ax < shoulder_x * .75) | (z > shoulder_z + .02)
    if config['coverage'] == 'full_body':
        core |= z < .45
    sleeves = (ax > shoulder_x * 1.30) & (z < shoulder_z - .015) & (z > wrist_z - .10)
    if config['sleeve_pose'] == 't':
        sleeves = (ax > shoulder_x * 1.30) & (abs(z - shoulder_z) < .18)
    def distances(seeds):
        out = np.full(len(unique), np.inf)
        ids = np.flatnonzero(seeds).tolist()
        out[ids] = 0
        queue = [(0., i) for i in ids]
        heapq.heapify(queue)
        while queue:
            distance, i = heapq.heappop(queue)
            if distance > out[i]:
                continue
            for j, length in graph[i]:
                candidate = distance + length
                if candidate < out[j]:
                    out[j] = candidate
                    heapq.heappush(queue, (candidate, j))
        return out
    core_d, sleeve_d = distances(core), distances(sleeves)
    blend = np.zeros(len(unique))
    both = np.isfinite(core_d) & np.isfinite(sleeve_d)
    blend[both] = smoothstep(.5 + (core_d[both] - sleeve_d[both]) / .08)
    blend[np.isfinite(sleeve_d) & ~np.isfinite(core_d)] = 1
    return blend[inverse]


def align_sleeves(vertices, source, mesh, config, rig, source_z, target_z, scale, depth):
    """Map sleeve joints before transferring weights; blend across sewn seams."""
    full = config['coverage'] == 'full_body'
    shoulder = config['source_heights']['shoulder']
    pose = config['sleeve_pose']
    extent_x = source[:, 0].max() - source[:, 0].min()
    if pose == 't':
        sx, ex, wx = extent_x * .14, extent_x * .32, extent_x * .47
        ez = wz = shoulder
    else:
        sx = extent_x * (.28 if pose == 'down' else .17)
        ex = extent_x * (.40 if pose == 'down' else .32)
        wx = extent_x * (.37 if pose == 'down' else .47)
        ez = shoulder - (.15 if full else .36)
        wz = shoulder - (.30 if full else .72)
    sleeve_blend = geodesic_sleeves(source, mesh, config, sx, shoulder, wz)
    result = np.zeros((len(vertices), 2))
    for j, side in enumerate(('left', 'right')):
        sign = 1 if head(rig, 'arm', side).x > 0 else -1
        ax = source[:, 0] * sign
        anchors = []
        for index, (x, z) in enumerate(((sx, shoulder), (ex, ez), (wx, wz))):
            if pose != 't' and index > 0:
                band = source[(abs(source[:, 2] - z) < .025) & (ax > sx * 1.08)]
                if len(band) >= 8:
                    x = float((np.quantile(band[:, 0] * sign, .1) + np.quantile(band[:, 0] * sign, .9)) * .5)
                    y = float((np.quantile(band[:, 1], .1) + np.quantile(band[:, 1], .9)) * .5)
                else:
                    y = 0.
            else:
                y = 0.
            anchors.append(np.array((sign * x * scale, y * scale + depth, np.interp(z, source_z, target_z))))
        targets = [np.asarray(head(rig, name, side)) for name in ('arm', 'forearm', 'hand')]
        mapped = []
        for i in range(2):
            a, b = anchors[i:i+2]
            ta, tb = targets[i:i+2]
            axis = (b - a) / np.linalg.norm(b - a)
            rotation = np.asarray(Vector(axis).rotation_difference(Vector(tb - ta).normalized()).to_matrix())
            relative = vertices - a
            along = relative @ axis
            relative += along[:, None] * axis * (np.linalg.norm(tb - ta) / np.linalg.norm(b - a) - 1)
            mapped.append(relative @ rotation.T + ta)
        elbow_blend = smoothstep((ax - ex + .04) / .08) if pose == 't' else smoothstep((ez - source[:, 2] + .035) / .07)
        corrected = mapped[0] * (1 - elbow_blend[:, None]) + mapped[1] * elbow_blend[:, None]
        # Follow mesh connectivity across UV seams to include low cuffs.
        blend = sleeve_blend * (ax > 0)
        result[:, j] = blend
        vertices[:] += (corrected - vertices) * blend[:, None]
    return result


def regional_surfaces(surface, rig):
    points = coords(surface)
    surface.data.calc_loop_triangles()
    names = {g.index: g.name for g in surface.vertex_groups}
    arm_names = {}
    for side in ('left', 'right'):
        root = bone(rig, 'arm', side)
        arm_names[side] = {root.name} | {b.name for b in root.children_recursive}
    weights = []
    labels = []
    for vertex in surface.data.vertices:
        row = {names[g.group]: g.weight for g in vertex.groups if g.weight > 1e-6 and names[g.group] in rig.pose.bones}
        weights.append(row)
        totals = {side: sum(w for n, w in row.items() if n in group) for side, group in arm_names.items()}
        labels.append(max(totals, key=totals.get) if max(totals.values()) > .5 else 'core')
    groups = {name: [] for name in ('core', 'left', 'right')}
    for tri in surface.data.loop_triangles:
        ids = tuple(tri.vertices)
        label = max(groups, key=lambda name: sum(labels[i] == name for i in ids))
        groups[label].append(ids)
    return {name: (BVHTree.FromPolygons(points.tolist(), tris, all_triangles=True), tris)
            for name, tris in groups.items()}, points, weights


def expand_envelopes(garment, surface, rig, regions, target, height, clearance):
    """Expand garment cross sections using a shared radial scale for all layers."""
    _, body, weights = regional_surfaces(surface, rig)
    vertices = coords(garment)
    original = vertices.copy()
    arm_names = {}
    for side in ('left', 'right'):
        pb = bone(rig, 'arm', side)
        arm_names[side] = {pb.name} | {b.name for b in pb.children_recursive}
    arm_amounts = np.array([[sum(w for name,w in row.items() if name in arm_names[side])
                             for side in ('left','right')] for row in weights])
    core_body = arm_amounts.sum(1) < .25
    core_cloth = regions.sum(1) < .1
    levels = np.linspace(target['hip'], target['neck'], 20)
    angles = np.linspace(-math.pi, math.pi, 24, endpoint=False)
    depth = np.full(len(levels), target['depth'])
    scales = np.ones((len(levels), len(angles)))
    for k,z in enumerate(levels):
        bp = body[core_body & (abs(body[:,2]-z) < height*.025)]
        cp = vertices[core_cloth & (abs(vertices[:,2]-z) < height*.025)]
        if len(bp)<8 or len(cp)<8:
            continue
        depth[k] = (bp[:,1].min()+bp[:,1].max())*.5
        br = bp[:,:2] - (0,depth[k]); cr = cp[:,:2] - (0,depth[k])
        ba,ca = np.arctan2(br[:,1],br[:,0]),np.arctan2(cr[:,1],cr[:,0])
        bd,cd = np.linalg.norm(br,axis=1),np.linalg.norm(cr,axis=1)
        for j,angle in enumerate(angles):
            bm = abs(np.angle(np.exp(1j*(ba-angle)))) < math.pi/8
            cm = abs(np.angle(np.exp(1j*(ca-angle)))) < math.pi/8
            if np.count_nonzero(bm)>=3 and np.count_nonzero(cm)>=3:
                scales[k,j] = np.clip((np.quantile(bd[bm],.95)+clearance*1.5) /
                                      max(np.quantile(cd[cm],.10),height*.015),1,1.45)
    # Neighboring maxima fill sparse slices; bilinear interpolation smooths the scale.
    scales = np.maximum.reduce((scales,np.roll(scales,1,1),np.roll(scales,-1,1)))
    scales[1:-1] = np.maximum.reduce((scales[1:-1],scales[:-2],scales[2:]))
    z = vertices[:,2]
    cy = np.interp(z,levels,depth)
    radial = vertices[:,:2] - np.column_stack((np.zeros(len(vertices)),cy))
    az = (np.arctan2(radial[:,1],radial[:,0])+math.pi)/(2*math.pi)*len(angles)
    j = np.floor(az).astype(int)%len(angles); f=az-np.floor(az)
    ratios = np.ones(len(vertices))
    for k in range(len(angles)):
        select=j==k
        ratios[select] = (np.interp(z[select],levels,scales[:,k])*(1-f[select])+
                          np.interp(z[select],levels,scales[:,(k+1)%len(angles)])*f[select])
    fade = smoothstep((z-target['hip'])/(height*.03)) * (1-smoothstep((z-target['neck']+height*.02)/(height*.04)))
    amount = (ratios-1)*fade*(1-regions.sum(1))
    vertices[:,:2] += radial*amount[:,None]
    for side_index,side in enumerate(('left','right')):
        anchors = np.array([head(rig,name,side) for name in ('arm','forearm','hand')])
        def axial(points):
            choices=[]
            for a,b in zip(anchors,anchors[1:]):
                axis=b-a; t=np.clip((points-a)@axis/np.dot(axis,axis),0,1)
                centre=a+t[:,None]*axis
                choices.append((t,centre,np.linalg.norm(points-centre,axis=1)))
            segment=(choices[1][2]<choices[0][2]).astype(int)
            t=np.where(segment==0,choices[0][0],choices[1][0])
            centre=np.where(segment[:,None]==0,choices[0][1],choices[1][1])
            radius=np.linalg.norm(points-centre,axis=1)
            return segment+t,centre,radius
        bt,_,bd=axial(body); ct,centres,cd=axial(vertices)
        steps=np.linspace(0,2,32); factors=np.ones(len(steps))
        for k,t in enumerate(steps):
            bm=(arm_amounts[:,side_index]>.5)&(abs(bt-t)<.12)
            cm=(regions[:,side_index]>.8)&(abs(ct-t)<.12)
            if bm.sum()>=3 and cm.sum()>=3:
                factors[k]=np.clip((np.quantile(bd[bm],.90)+clearance*1.5)/max(np.quantile(cd[cm],.10),height*.01),1,1.45)
        factors[1:-1]=np.maximum.reduce((factors[1:-1],factors[:-2],factors[2:]))
        amount=(np.interp(ct,steps,factors)-1)*regions[:,side_index]
        vertices += (vertices-centres)*amount[:,None]
    set_coords(garment,vertices)
    return {'expanded_vertices':int(np.count_nonzero(np.linalg.norm(vertices-original,axis=1)>.0001)),
            'max_displacement_m':float(np.linalg.norm(vertices-original,axis=1).max())}


def transfer_weights(garment, surface, rig, regions):
    trees, points, body_weights = regional_surfaces(surface, rig)
    weights = []
    zero, one, two = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
    for i, point in enumerate(coords(garment)):
        row = {}
        amounts = (max(0., 1 - regions[i].sum()), regions[i, 0], regions[i, 1])
        for region, amount in zip(('core', 'left', 'right'), amounts):
            if amount < 1e-6:
                continue
            tree, triangles = trees[region]
            hit, _, index, _ = tree.find_nearest(Vector(point))
            if hit is None:
                raise ValueError(f'Body has no {region} surface for weight transfer')
            ids = triangles[index]
            bary = barycentric_transform(hit, *(Vector(points[n]) for n in ids), zero, one, two)
            for vertex, coefficient in zip(ids, bary):
                for name, value in body_weights[vertex].items():
                    row[name] = row.get(name, 0.) + max(0., coefficient) * value * amount
        kept = sorted(row.items(), key=lambda item: item[1], reverse=True)[:4]
        total = sum(w for _, w in kept)
        if total < 1e-8:
            raise ValueError(f'Clothing vertex {i} received no bone weights')
        weights.append([(name, weight / total) for name, weight in kept])
    garment.vertex_groups.clear()
    groups = {name: garment.vertex_groups.new(name=name) for name in dict.fromkeys(n for row in weights for n, _ in row)}
    for i, row in enumerate(weights):
        for name, value in row:
            groups[name].add([i], value, 'REPLACE')
    return weights


def inverse_skin(garment, rig, weights, rest):
    world = rig.matrix_world
    matrices = {b.name: np.asarray(world @ b.matrix @ rest[b.name].inverted() @ world.inverted())
                for b in rig.pose.bones}
    posed = coords(garment)
    transforms = np.zeros((len(posed), 4, 4))
    for i, row in enumerate(weights):
        for name, weight in row:
            transforms[i] += matrices[name] * weight
    determinants = np.linalg.det(transforms[:, :3, :3])
    if np.any(np.abs(determinants) < 1e-7):
        raise ValueError('Skin transform is singular; sleeve pose is too far from the rest pose')
    homogeneous = np.column_stack((posed, np.ones(len(posed))))
    unposed = np.linalg.solve(transforms, homogeneous[..., None])[..., 0]
    set_coords(garment, unposed[:, :3])
    recovered = np.einsum('nij,nj->ni', transforms, unposed)[:, :3]
    return float(np.max(np.linalg.norm(recovered - posed, axis=1)))


def validate_motion(garment, rig, weights, rest, regions, target):
    """Check Blender's actual modifier evaluation against the fitted skin."""
    base = coords(garment)
    sample = np.arange(0,len(base),max(1,len(base)//10000))
    saved = {b.name:b.matrix_basis.copy() for b in rig.pose.bones}
    reports=[]
    try:
        for pose in ('a','down'):
            for pb in rig.pose.bones:
                pb.matrix_basis=Matrix.Identity(4)
            bpy.context.view_layer.update()
            pose_sleeves(rig,pose)
            actual=coords(garment,evaluated=True)[sample]
            world=rig.matrix_world
            matrices={b.name:np.asarray(world @ b.matrix @ rest[b.name].inverted() @ world.inverted()) for b in rig.pose.bones}
            expected=np.zeros_like(actual)
            for k,i in enumerate(sample):
                point=np.append(base[i],1.)
                for name,value in weights[i]:
                    expected[k] += (matrices[name] @ point)[:3]*value
            error=float(np.linalg.norm(actual-expected,axis=1).max())
            if error>1e-4:
                raise ValueError(f'{pose} skin evaluation differs from the fit by {error:.6f} m')
            core=(base[sample,2]<target['hip']) & (regions[sample].sum(1)<.01)
            core_motion=float(np.linalg.norm(actual[core]-base[sample][core],axis=1).max()) if core.any() else 0.
            reports.append({'pose':pose,'sample_count':len(sample),'skin_error_m':error,
                            'lower_core_motion_m':core_motion,
                            'moving_samples':int(np.count_nonzero(np.linalg.norm(actual-base[sample],axis=1)>.001))})
    finally:
        for pb in rig.pose.bones:
            pb.matrix_basis=saved[pb.name]
        bpy.context.view_layer.update()
    return reports


def replace_body_footwear(meshes, garment, rig, height):
    """Cut covered feet while retaining the separate skin-weight donor.

    The cut is 2% of body height above the ankle. Triangle bisection
    interpolates UVs and deform weights. Requires explicit footwear replacement.
    """
    import bmesh
    points = coords(garment)
    ankle = min(head(rig, 'foot', side).z for side in ('left', 'right'))
    for side in ('left', 'right'):
        foot = head(rig, 'foot', side)
        low = (points[:, 2] < ankle * .75) & (points[:, 0] * foot.x > 0)
        if np.count_nonzero(low) < 20:
            raise ValueError('Footwear replacement requires garment shoes on both feet')
    cutoff = ankle + height * .02
    removed = 0
    for obj in meshes:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        world = obj.matrix_world.copy()
        for vertex in bm.verts:
            vertex.co = world @ vertex.co
        bmesh.ops.bisect_plane(bm, geom=list(bm.verts)+list(bm.edges)+list(bm.faces),
                              dist=1e-7, plane_co=(0, 0, cutoff), plane_no=(0, 0, 1))
        faces = [f for f in bm.faces if f.calc_center_median().z < cutoff - 1e-7]
        removed += len(faces)
        bmesh.ops.delete(bm, geom=faces, context='FACES')
        inverse = world.inverted()
        for vertex in bm.verts:
            vertex.co = inverse @ vertex.co
        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
    return {'mode': 'replace', 'cut_height_m': cutoff, 'removed_faces': removed,
            'skin_donor': 'original uncut body',
            'requires': 'closed garment shoes and ankle-covering cuffs'}


def run(config):
    started = time.monotonic()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    body_objects = import_asset(config['body'])
    rigs = [o for o in body_objects if o.type == 'ARMATURE']
    meshes = [o for o in body_objects if o.type == 'MESH']
    if len(rigs) != 1 or not meshes:
        raise ValueError('Body must contain one humanoid armature and at least one mesh')
    rig = rigs[0]
    rig.animation_data_clear()
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    canonical_body(body_objects, rig, config['height_metres'])
    rest = {b.name: b.matrix.copy() for b in rig.pose.bones}
    average = lambda name: sum((head(rig, name, s).z for s in ('left', 'right'))) / 2
    hip = average('upleg')
    shoulder = average('arm')
    target = {'ankle': average('foot'), 'knee': average('leg'), 'hip': hip,
              'waist': hip + (shoulder - hip) * .30, 'shoulder': shoulder,
              'neck': head(rig, 'head').z, 'depth': head(rig, 'hips').y}
    clothing_objects = import_asset(config['clothing'])
    pose_sleeves(rig, config['sleeve_pose'])
    garment, regions, mapping = prepare_garment(clothing_objects, config, target, rig)
    surface = snapshot_body(meshes)
    footwear = {'mode': 'preserve'}
    collision_surface = surface
    if config.get('footwear_mode', 'preserve') == 'replace':
        footwear = replace_body_footwear(meshes, garment, rig, config['height_metres'])
        collision_surface = snapshot_body(meshes)
    envelope = expand_envelopes(garment, collision_surface, rig, regions, target, config['height_metres'], config['clearance_metres'])
    tree = surface_tree(collision_surface)
    fit_metrics = clearance_pass(garment, tree, config['clearance_metres'], config['height_metres'],
                                 protected_below=footwear.get('cut_height_m'))
    weights = transfer_weights(garment, surface, rig, regions)
    if collision_surface != surface:
        bpy.data.objects.remove(collision_surface, do_unlink=True)
    error = inverse_skin(garment, rig, weights, rest)
    for pb in rig.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    bpy.context.view_layer.update()
    bpy.data.objects.remove(surface, do_unlink=True)
    rest_surface = snapshot_body(meshes)
    rest_metrics = clearance_pass(garment, surface_tree(rest_surface), config['clearance_metres'], config['height_metres'],
                                  protected_below=footwear.get('cut_height_m'))
    bpy.data.objects.remove(rest_surface, do_unlink=True)
    refresh_normals(garment)
    # Preserve world coordinates when parenting; avoid applying the transform twice.
    garment.parent = rig
    garment.matrix_parent_inverse = rig.matrix_world.inverted()
    modifier = garment.modifiers.new('Character skeleton', 'ARMATURE')
    modifier.object = rig
    modifier.use_deform_preserve_volume = False
    bpy.context.view_layer.update()
    motion_checks = validate_motion(garment, rig, weights, rest, regions, target)
    out = Path(config['output'])
    bpy.ops.object.select_all(action='DESELECT')
    for obj in body_objects + [garment]:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = rig
    report = {'glb_path': str(out), 'body': config['body'], 'clothing': config['clothing'],
              'coverage': config['coverage'], 'sleeve_pose': config['sleeve_pose'],
              'height_metres': config['height_metres'], 'clearance_metres': config['clearance_metres'],
              'headwear_offset_metres': config.get('headwear_offset_metres',0.),
              'vertices': len(garment.data.vertices), 'faces': len(garment.data.polygons),
              'material_slots': len(garment.data.materials), 'bone_groups': len(garment.vertex_groups),
              'footwear': footwear, 'envelope_fit': envelope, 'pose_fit': fit_metrics, 'rest_fit': rest_metrics,
              'inverse_skin_max_error_m': error, 'motion_checks': motion_checks, **mapping,
              'limitations': ['Clearance uses the nearest oriented surface within 8% of body height; it is not a cloth simulation.',
                              'Source anatomical heights and sleeve pose must match the garment; inspect loose cuffs, collars and accessories.',
                              'Animation collision handling is not added to the exported asset.']}
    if config['save_blend']:
        blend = out.with_suffix('.blend')
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        report['blend_path'] = str(blend)
    bpy.ops.export_scene.gltf(filepath=str(out), export_format='GLB', use_selection=True,
                              export_animations=False, export_skins=True, export_all_influences=False)
    report['elapsed_seconds'] = round(time.monotonic() - started, 2)
    report_path = out.with_suffix('.fit.json')
    report['report_path'] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    config_path = Path(sys.argv[1])
    result = run(json.loads(config_path.read_text(encoding='utf-8')))
    config_path.with_name('done.json').write_text(json.dumps(result), encoding='utf-8')
    print(json.dumps(result), flush=True)
