"""Validate complete trajectory programs without supplying motion or solver defaults."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import numpy as np

from .skeleton_templates import SkeletonTemplate, topological_order
from .timing import Rhythm, curve, fields, number, numbers
from .units import unit


@dataclass(frozen=True)
class MotionPlan:
    template: SkeletonTemplate
    rhythm: Rhythm
    program: dict
    parts: dict
    frame: np.ndarray


def _direction(value, label, epsilon):
    return unit(numbers(value, (3,), label=label), epsilon=epsilon)


def _validate_params(spec, rhythm, times, epsilon):
    op, p, chain = spec['operator'], spec['params'], spec['joints']
    if op == 'rest':
        fields(p, (), label='rest.params')
        return
    if op == 'aim':
        fields(p, ('targets', 'space', 'scale', 'pole', 'rest_pole'), label='aim.params')
        if len(chain) < 2 or not isinstance(p['targets'], list) or len(p['targets']) != len(chain) - 1:
            raise ValueError('Aim needs one child-position curve for each bone in the chain')
        for track in p['targets']:
            curve(track, rhythm, times, width=3)
    elif op == 'position':
        fields(p, ('trajectory', 'space', 'scale', 'contacts', 'pole', 'rest_pole',
                   'flexion', 'orientation'), label='position.params')
        curve(p['trajectory'], rhythm, times, width=3)
        rhythm.windows(p['contacts'])
    elif op == 'contact_path':
        fields(p, ('contacts', 'anchor_times', 'arcs', 'scale', 'ground_height',
                   'pole', 'rest_pole', 'flexion', 'orientation'), label='contact_path.params')
        windows = rhythm.windows(p['contacts'])
        if len(windows) < 2 or windows[0, 0] != 0 or windows[-1, 1] != 1:
            raise ValueError('Contact paths need at least two windows covering the start and end')
        if not isinstance(p['anchor_times'], list) or len(p['anchor_times']) != len(windows):
            raise ValueError('Each contact window needs an explicit anchor time')
        for value, (a, b) in zip(p['anchor_times'], windows):
            if not a <= rhythm.fraction(value) <= b:
                raise ValueError('An anchor time must lie inside its contact window')
        numbers(p['arcs'], (len(windows) - 1, 3), label='contact_path.arcs')
        number(p['ground_height'], label='contact_path.ground_height')
    elif op == 'arm_arc':
        fields(p, ('angle', 'radius', 'lateral', 'scale', 'preferred_swivel_degrees',
                   'rest_pole', 'limits'), label='arm_arc.params')
        if spec['category'] != 'upper_limb' or spec.get('side') not in ('L', 'R') or len(chain) != 3:
            raise ValueError('Arm arcs need an upper_limb, an L/R side and three joints')
        for key in ('angle', 'radius', 'lateral'):
            curve(p[key], rhythm, times, width=0)
        radius = curve(p['radius'], rhythm, times, width=0)
        lateral = curve(p['lateral'], rhythm, times, width=0)
        if np.any(radius <= np.abs(lateral)):
            raise ValueError('Arm radius must exceed the absolute lateral offset')
        number(p['preferred_swivel_degrees'], label='preferred_swivel_degrees')
        limits = p['limits']
        dofs = ('shoulder_flexion', 'shoulder_abduction', 'shoulder_swivel', 'elbow_flexion')
        fields(limits, (*dofs, 'target_back_ratio', 'regularization', 'max_nfev',
                        'ftol', 'xtol', 'gtol'), label='arm limits')
        for key in dofs:
            low, high = numbers(limits[key], (2,), label=key)
            if low >= high:
                raise ValueError('Arm DOF bounds must be increasing')
        if not -90 < limits['shoulder_abduction'][0] < limits['shoulder_abduction'][1] < 90:
            raise ValueError('Shoulder abduction must remain inside its nonsingular (-90,90) chart')
        if not 0 < limits['elbow_flexion'][0] < limits['elbow_flexion'][1] < 180:
            raise ValueError('Elbow flexion bounds must lie inside (0,180)')
        if type(limits['max_nfev']) is not int or limits['max_nfev'] <= 0:
            raise ValueError('max_nfev must be a positive integer')
        for key in ('regularization', 'ftol', 'xtol', 'gtol'):
            number(limits[key], label=key, positive=True)
        if min(limits[k] for k in ('ftol', 'xtol', 'gtol')) <= np.finfo(float).eps:
            raise ValueError('Optimizer tolerances must exceed floating-point machine epsilon')
        number(limits['target_back_ratio'], label='target_back_ratio', nonnegative=True)
    else:
        raise ValueError(f'Unsupported trajectory operator: {op}')
    number(p['scale'], label='trajectory.scale', positive=True)
    _direction(p['rest_pole'], 'rest_pole', epsilon)
    if op != 'arm_arc':
        _direction(p['pole'], 'pole', epsilon)
    if op in ('position', 'aim') and p['space'] not in ('world', 'root', 'parent'):
        raise ValueError('Position space must be world, root or parent')
    if op in ('position', 'contact_path'):
        if len(chain) not in (3, 4):
            raise ValueError('Position IK needs three or four continuous joints')
        low, high = numbers(p['flexion'], (2,), label='flexion')
        if not 0 < low < high < 180:
            raise ValueError('Flexion bounds must lie inside (0,180)')
        orientations = ('body', 'rest', 'contact') if op == 'contact_path' else ('body', 'rest')
        if op == 'position' and isinstance(p['orientation'], dict):
            curve(p['orientation'], rhythm, times, width=0)
        elif p['orientation'] not in orientations:
            raise ValueError(f'Orientation must be one of {orientations} or an explicit position-target yaw curve')


def build_plan(skeleton, *, rhythm, program):
    skeleton, rhythm, program = deepcopy((skeleton, rhythm, program))
    fields(skeleton, ('name', 'names', 'parents', 'rest'), label='skeleton')
    fields(rhythm, ('duration', 'fps', 'events'), label='rhythm')
    clock = Rhythm(**rhythm)
    fields(program, ('action', 'forward', 'root', 'parts', 'solver', 'quality'), label='program')
    if not isinstance(program['action'], str) or not program['action'].strip():
        raise ValueError('program.action must be a nonempty label, not a preset selector')
    if not isinstance(skeleton['name'], str) or not skeleton['name'].strip():
        raise ValueError('skeleton.name must be nonempty')
    fields(program['solver'], ('epsilon',), label='solver')
    epsilon = number(program['solver']['epsilon'], label='solver.epsilon', positive=True)
    if epsilon >= 1:
        raise ValueError('solver.epsilon must be smaller than a unit direction')
    quality = program['quality']
    fields(quality, ('target_tolerance', 'contact_tolerance', 'bone_length_tolerance',
                     'ground_height', 'ground_tolerance', 'limit_tolerance'), label='quality')
    for key, value in quality.items():
        number(value, label=f'quality.{key}', nonnegative=key != 'ground_height')
    forward = _direction(program['forward'], 'program.forward', epsilon)
    if abs(forward[1]) > epsilon:
        raise ValueError('The motion and BVH coordinate contract is Y-up; forward must be horizontal')
    up = np.eye(3)[1]
    forward[1] = 0
    forward = unit(forward, epsilon=epsilon)
    frame = np.column_stack([np.cross(up, forward), up, forward])
    names = skeleton['names']
    if (not isinstance(names, list) or not names
            or any(not isinstance(name, str) or not name or any(c.isspace() or c in '{}' for c in name) for name in names)
            or len(set(names)) != len(names)):
        raise ValueError('skeleton.names must contain explicit unique, nonempty BVH-safe strings')
    template = SkeletonTemplate(skeleton['name'], names,
                                np.asarray(skeleton['parents']), np.asarray(skeleton['rest']), {})
    if not isinstance(program['parts'], dict) or not program['parts']:
        raise ValueError('program.parts must be a nonempty object')
    times = clock.sample()
    fields(program['root'], ('operator', 'params'), label='root')
    if program['root']['operator'] != 'curves':
        raise ValueError('Root motion requires explicit position curves; encode flight and rhythm in the input')
    p = program['root']['params']
    fields(p, ('x', 'y', 'z', 'yaw', 'scale'), label='root.params')
    number(p['scale'], label='root.scale', positive=True)
    for key in ('x', 'y', 'z', 'yaw'):
        curve(p[key], clock, times, width=0)
    owned, parts = set(), {}
    names = template.joint_names
    for key, spec in program['parts'].items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError('Part IDs must be nonempty strings')
        fields(spec, ('category', 'joints', 'operator', 'params'), optional=('side',), label=f'parts.{key}')
        if spec['category'] not in ('trunk', 'head', 'tail', 'upper_limb', 'lower_limb'):
            raise ValueError('Unknown body-part category')
        if 'side' in spec and spec['side'] not in ('L', 'R'):
            raise ValueError('Side must be L or R')
        if not isinstance(spec['joints'], list) or not spec['joints']:
            raise ValueError('Each part needs an explicit nonempty joint chain')
        chain = []
        for joint in spec['joints']:
            if isinstance(joint, str) and joint in names:
                chain.append(names.index(joint))
            elif type(joint) is int and 0 <= joint < len(names):
                chain.append(joint)
            else:
                raise ValueError(f'Unknown joint in part {key}: {joint}')
        if len(set(chain)) != len(chain) or owned.intersection(chain):
            raise ValueError('Body-part chains must not overlap or repeat joints')
        if any(template.parents[b] != a for a, b in zip(chain, chain[1:])):
            raise ValueError('Body-part chains must be continuous parent-child paths')
        if any(np.linalg.norm(template.rest[b] - template.rest[a]) <= epsilon for a, b in zip(chain, chain[1:])):
            raise ValueError('Controlled bones must have nonzero length at the configured tolerance')
        spec = {**spec, 'joints': chain}
        _validate_params(spec, clock, times, epsilon)
        owned.update(chain)
        parts[key] = spec
    order = topological_order(template.parents).tolist()
    root = order[0]
    if not any(root in s['joints'] and s['category'] == 'trunk' and s['operator'] in ('rest', 'aim')
               for s in parts.values()):
        raise ValueError('The root must belong to an explicit trunk rest/aim part')
    parts = dict(sorted(parts.items(), key=lambda item: order.index(item[1]['joints'][0])))
    template.roles.update({key: spec['joints'] for key, spec in parts.items()})
    return MotionPlan(template, clock, program, parts, frame)
