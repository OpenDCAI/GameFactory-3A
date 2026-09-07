"""
operators/gen_3d_scene/funcs/terrain_code_edit.py

Build a scene from code in two stages: a greybox first, then detail.

Four concerns stay separate, and each is an ordinary value:

    terrain    what the ground is
    layout     where things stand on it
    sizing     how big each one is
    materials  what colour or texture it reads as

A template picks one of each; a caller overrides any single one without
touching the rest.

Stage 1 writes a greybox — primitives with flat colours — and `check_scene`
reports whether the arrangement is usable before any detail is paid for.
Stage 2 replaces individual props with generated meshes and stages ground
textures into a game project.

Relief comes from layered value noise and is written as one welded surface, so
a slope is a slope rather than a flight of steps, and no two hills are alike.
Layouts are jittered off their lattice for the same reason: a regular grid is
the one arrangement no real site has.

Usage:
    from operators.gen_3d_scene.funcs import terrain_code_edit as te

    scene = te.Scene(
        name="clearing",
        terrain=te.hills(48.0, amplitude=2.0),
        props=te.place("pillar", "cylinder",
                       te.ring_spots(6, 12.0), size=(1.2, 5.0, 1.2)),
    )
    print(te.check_scene(scene))
    te.write_scene(scene, "greybox.glb")
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

Spot = tuple[float, float]
Vec3 = tuple[float, float, float]

#: Spec dialect written by `models/common/glb_writer.py`.
UNITS = "metres"

#: Height of a standing human, used as the scale reference in `check_scene`.
HUMAN_HEIGHT = 1.8

#: Tallest structure a scene may hold, in metres. A scene is not an object:
#: a tower block is legitimately dozens of times a person, so the gate is set
#: where it still catches a unit mistake — a metre read as a centimetre — and
#: nothing else.
TALLEST_STRUCTURE = 120.0


# ── materials ────────────────────────────────────────────────────────────────

#: Greybox palette. Distinct enough to tell surfaces apart, flat enough that
#: nobody mistakes the result for finished art.
GREYBOX_MATERIALS: dict[str, dict[str, Any]] = {
    "ground": {"baseColor": [0.55, 0.56, 0.52, 1.0], "roughness": 0.95},
    "block": {"baseColor": [0.72, 0.72, 0.74, 1.0], "roughness": 0.80},
    "wall": {"baseColor": [0.62, 0.60, 0.58, 1.0], "roughness": 0.85},
    "prop": {"baseColor": [0.70, 0.58, 0.42, 1.0], "roughness": 0.75},
    "water": {"baseColor": [0.30, 0.48, 0.62, 0.65], "roughness": 0.15},
    "marker": {"baseColor": [0.85, 0.35, 0.30, 1.0], "roughness": 0.60},
}

#: Material name -> asset id in `scene_assets.SCENE_ASSETS`. Read by
#: `stage_scene_textures` to fetch the files a finished scene needs.
MATERIAL_TEXTURES: dict[str, str] = {
    "ground": "tex_grass_ground",
    "wall": "tex_brick_wall",
    "block": "tex_grain_noise",
    "water": "tex_water_normals",
}


# ── terrain ──────────────────────────────────────────────────────────────────

#: Octaves of value noise mixed into every relief terrain. Each halves in
#: amplitude and roughly doubles in frequency, so one pass gives the broad
#: landform and the later ones the erosion detail that keeps a slope from
#: reading as a ramp.
NOISE_OCTAVES = 4

#: Gain applied to the summed octaves. Interpolated value noise rarely
#: reaches its own extremes, and averaging octaves narrows it further: four
#: octaves span about ±0.6 of the nominal range, which makes `amplitude`
#: mean something other than what it says. Clamped afterwards.
NOISE_GAIN = 1.7

#: How much of a terrain's amplitude the noise carries, for the shapes that
#: are otherwise a formula. Below this a slope looks machined; far above it
#: the landform stops being recognisable.
NOISE_SHARE = 0.45


def _hash_unit(x: int, z: int, seed: int) -> float:
    """A repeatable 0..1 value for one lattice point."""
    h = (x * 374761393 + z * 668265263 + seed * 2147483647) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFFFF) / 0xFFFFFF


def _smooth_noise(x: float, z: float, seed: int) -> float:
    """Value noise in -1..1, smooth across lattice cells.

    Interpolated with a smoothstep curve rather than linearly: linear blending
    leaves a crease along every cell boundary, which on terrain reads as a
    grid of ridges.
    """
    x0, z0 = math.floor(x), math.floor(z)
    fx, fz = x - x0, z - z0
    ex = fx * fx * (3.0 - 2.0 * fx)
    ez = fz * fz * (3.0 - 2.0 * fz)

    c00 = _hash_unit(x0, z0, seed)
    c10 = _hash_unit(x0 + 1, z0, seed)
    c01 = _hash_unit(x0, z0 + 1, seed)
    c11 = _hash_unit(x0 + 1, z0 + 1, seed)

    top = c00 + (c10 - c00) * ex
    bottom = c01 + (c11 - c01) * ex
    return (top + (bottom - top) * ez) * 2.0 - 1.0


def fractal_noise(
    x: float,
    z: float,
    wavelength: float,
    seed: int = 0,
    octaves: int = NOISE_OCTAVES,
) -> float:
    """Layered value noise in -1..1, for terrain relief.

    `wavelength` is the span of the largest feature, in metres.
    """
    total = 0.0
    weight = 0.0
    amplitude = 1.0
    scale = 1.0 / max(wavelength, 1e-6)
    for octave in range(max(1, octaves)):
        total += amplitude * _smooth_noise(x * scale, z * scale, seed + octave * 977)
        weight += amplitude
        amplitude *= 0.5
        # Not exactly 2, so the octaves' lattices do not line up and reinforce
        # each other into a visible grid.
        scale *= 2.17
    return max(-1.0, min(1.0, NOISE_GAIN * total / max(weight, 1e-6)))


@dataclass(frozen=True)
class Terrain:
    """Ground the props stand on.

    A `height` of None is a flat slab. Any other callable is sampled on a
    `tiles` by `tiles` grid and written as one welded surface, so `tiles`
    controls how finely relief is resolved.
    """

    size: float = 48.0
    thickness: float = 0.4
    tiles: int = 1
    height: Callable[[float, float], float] | None = None
    material: str = "ground"


def flat(
    size: float = 48.0,
    material: str = "ground",
    ripple: float = 0.0,
    seed: int = 0,
) -> Terrain:
    """Level ground, optionally with `ripple` metres of undulation.

    A ripple is worth having on open ground: a perfectly level plane reads as
    a plane, and under a metre of movement is enough for it to read as a field
    while leaving anything still able to stand on it.
    """
    if ripple <= 0.0:
        return Terrain(size=size, material=material)

    def height(x: float, z: float) -> float:
        return ripple * fractal_noise(x, z, wavelength=size * 0.22, seed=seed)

    return Terrain(size=size, tiles=56, height=height, material=material)


def hills(
    size: float = 48.0,
    amplitude: float = 2.0,
    wavelength: float = 24.0,
    tiles: int = 64,
    material: str = "ground",
    seed: int = 0,
) -> Terrain:
    """Rolling ground from layered noise.

    Noise rather than crossed sine waves: two sines put every crest on a
    regular lattice, so the hills repeat at the wavelength and the layout
    filters built on them fall into rows.
    """

    def height(x: float, z: float) -> float:
        return amplitude * fractal_noise(x, z, wavelength, seed)

    return Terrain(size=size, tiles=tiles, height=height, material=material)


def slope(
    size: float = 48.0,
    rise: float = 6.0,
    axis: str = "z",
    tiles: int = 64,
    material: str = "ground",
    roughness: float = NOISE_SHARE,
    seed: int = 0,
) -> Terrain:
    """Ground climbing along one axis, broken up by noise.

    `roughness` is the share of `rise` the noise carries. At 0 the result is
    a plane, which is what makes a bare gradient look like a ramp.
    """

    def height(x: float, z: float) -> float:
        along = z if axis == "z" else x
        ramp = rise * (along / max(size, 1e-6) + 0.5)
        return ramp + rise * roughness * fractal_noise(
            x, z, wavelength=size * 0.3, seed=seed
        )

    return Terrain(size=size, tiles=tiles, height=height, material=material)


def bowl(
    size: float = 64.0,
    depth: float = 7.0,
    tiles: int = 72,
    material: str = "ground",
    roughness: float = NOISE_SHARE,
    rim_share: float = 0.8,
    centre: Spot = (0.0, 0.0),
    seed: int = 0,
) -> Terrain:
    """Ground dishing down to a low centre, level at the rim.

    The dish is radial and the noise is not, so the walls fall unevenly and
    the rim wanders — a basin rather than a bowl. `rim_share` is how much of
    the way to the edge the dish occupies; beyond it the ground is level, so
    the basin sits in a landscape rather than being the whole site.

    `centre` moves the low point off the origin. A basin whose deepest point
    is dead centre gives every radius the same cross-section, which is what
    makes anything arranged around it look set out rather than settled.
    """
    reach = max(size / 2.0 * rim_share, 1e-6)

    def height(x: float, z: float) -> float:
        radial = min(math.dist((x, z), centre) / reach, 1.0)
        dish = radial * radial - 1.0
        # The noise takes a share of the dish rather than being added on top,
        # so the basin does not end up deeper than `depth` says.
        broken = dish * (1.0 - roughness) + dish * roughness * (
            0.5 + 0.5 * fractal_noise(x, z, wavelength=size * 0.28, seed=seed)
        )
        return depth * broken

    return Terrain(size=size, tiles=tiles, height=height, material=material)


def mound(
    size: float = 64.0,
    rise: float = 6.0,
    flat_radius: float = 14.0,
    tiles: int = 72,
    material: str = "ground",
    roughness: float = NOISE_SHARE,
    seed: int = 0,
) -> Terrain:
    """A raised plateau with a level top and uneven flanks.

    The top stays level because a settlement stands on it; the flanks take the
    noise, so the hill is not a cone.
    """
    half = max(size / 2.0, 1e-6)
    run = max(half - flat_radius, 1e-6)

    def height(x: float, z: float) -> float:
        radial = math.hypot(x, z)
        if radial <= flat_radius:
            return rise
        fall = max(0.0, 1.0 - (radial - flat_radius) / run)
        # Strongest halfway down and gone at both ends, so neither the plateau
        # nor the surrounding ground is disturbed, and the flank never rises
        # above the plateau it descends from.
        blend = 4.0 * fall * (1.0 - fall)
        return rise * fall - rise * roughness * blend * 0.5 * (
            1.0 + fractal_noise(x, z, wavelength=size * 0.25, seed=seed)
        )

    return Terrain(size=size, tiles=tiles, height=height, material=material)


def canyon(
    size: float = 72.0,
    depth: float = 9.0,
    floor_width: float = 16.0,
    tiles: int = 80,
    material: str = "ground",
    meander: float = 0.18,
    roughness: float = NOISE_SHARE,
    rim_share: float = 0.45,
    seed: int = 0,
) -> Terrain:
    """A channel between two walls rising away from it.

    `meander` swings the channel's centreline across the site as a fraction of
    its width, so the floor curves and the walls are not a pair of parallel
    ramps. `rim_share` is how much of the distance to the edge the wall takes
    to reach full height; the rest is level rim, which is what gives the
    channel a lip instead of a shallow dish. The walls take noise on top of
    that; the floor is left level.
    """
    half = max(size / 2.0, 1e-6)
    run = max((half - floor_width / 2.0) * rim_share, 1e-6)
    swing = meander * size

    def height(x: float, z: float) -> float:
        centre = swing * fractal_noise(0.0, z, wavelength=size * 0.7, seed=seed)
        across = abs(x - centre) - floor_width / 2.0
        if across <= 0.0:
            return 0.0
        climb = min(across / run, 1.0)
        # Eased rather than linear: a straight rise meets the floor at a hard
        # crease, where a real wall has a talus slope at its foot.
        eased = climb * climb * (3.0 - 2.0 * climb)
        # The noise takes a share of the wall rather than being added on top,
        # so `depth` stays the height the wall actually reaches.
        broken = eased * (1.0 - roughness) + eased * roughness * (
            0.5 + 0.5 * fractal_noise(x, z, wavelength=size * 0.2, seed=seed + 31)
        )
        return depth * broken

    return Terrain(size=size, tiles=tiles, height=height, material=material)


def flattened(
    terrain: Terrain, spots: Sequence[Spot], width: float, blend: float = 6.0
) -> Terrain:
    """Level the ground along a path, easing back into the surrounding shape.

    What makes a road or a racing line usable on uneven terrain: the surface
    under it is flat, and the hills resume `blend` metres to either side.
    """
    base = terrain.height
    if base is None or not spots:
        return terrain
    level = sum(base(x, z) for x, z in spots) / len(spots)

    def height(x: float, z: float) -> float:
        near = min(math.dist((x, z), spot) for spot in spots)
        if near <= width:
            return level
        if near >= width + blend:
            return base(x, z)
        eased = (near - width) / blend
        return level + (base(x, z) - level) * eased * eased * (3.0 - 2.0 * eased)

    return replace(terrain, height=height)


def levelled_at(
    terrain: Terrain, spots: Sequence[Spot], radius: float, blend: float = 4.0
) -> Terrain:
    """Cut a level pad at each spot, easing back into the terrain around it.

    Each pad sits at the terrain's own height there, so a settlement on rough
    ground gets buildable ground under every structure without the site being
    flattened as a whole.
    """
    base = terrain.height
    if base is None or not spots:
        return terrain
    pads = [((x, z), base(x, z)) for x, z in spots]

    def height(x: float, z: float) -> float:
        near, level = min(pads, key=lambda pad: math.dist((x, z), pad[0]))
        distance = math.dist((x, z), near)
        if distance <= radius:
            return level
        if distance >= radius + blend:
            return base(x, z)
        eased = (distance - radius) / blend
        return level + (base(x, z) - level) * eased * eased * (3.0 - 2.0 * eased)

    return replace(terrain, height=height)


def ground_height(terrain: Terrain, x: float, z: float) -> float:
    """Surface height at one point."""
    if terrain.height is None:
        return 0.0
    return float(terrain.height(x, z))


def ground_normal(terrain: Terrain, x: float, z: float, step: float = 0.5) -> Vec3:
    """Upward surface normal at one point, for aligning something to a slope."""
    if terrain.height is None:
        return (0.0, 1.0, 0.0)
    dx = (ground_height(terrain, x + step, z)
          - ground_height(terrain, x - step, z)) / (2.0 * step)
    dz = (ground_height(terrain, x, z + step)
          - ground_height(terrain, x, z - step)) / (2.0 * step)
    length = math.sqrt(dx * dx + 1.0 + dz * dz)
    return (-dx / length, 1.0 / length, -dz / length)


def tile_centres(terrain: Terrain) -> list[Spot]:
    """Centre of every tile in the terrain grid."""
    step = terrain.size / terrain.tiles
    origin = -terrain.size / 2.0 + step / 2.0
    return [
        (origin + column * step, origin + row * step)
        for row in range(terrain.tiles)
        for column in range(terrain.tiles)
    ]


def height_grid(terrain: Terrain) -> list[list[float]]:
    """Elevations sampled on the terrain's grid, corner to corner."""
    span = terrain.tiles
    step = terrain.size / span
    start = -terrain.size / 2.0
    return [
        [
            ground_height(terrain, start + column * step, start + row * step)
            for column in range(span + 1)
        ]
        for row in range(span + 1)
    ]


def terrain_parts(terrain: Terrain) -> list[dict[str, Any]]:
    """Spec parts for the ground: one box when flat, one surface when not.

    Relief is written as a single welded `heightfield` rather than a box per
    tile. A grid of boxes is a staircase: every tile has vertical walls at its
    edges and a level top, which is the "blocky" look, and it costs six faces
    per sample where the surface costs two triangles.
    """
    if terrain.height is None or terrain.tiles <= 1:
        return [{
            "id": "ground",
            "kind": "box",
            "at": (0.0, -terrain.thickness / 2.0, 0.0),
            "size": (terrain.size, terrain.thickness, terrain.size),
            "material": terrain.material,
        }]

    heights = height_grid(terrain)
    return [{
        "id": "ground",
        "kind": "heightfield",
        "at": (0.0, 0.0, 0.0),
        "size": (terrain.size, 1.0, terrain.size),
        "heights": heights,
        "skirt": min(min(row) for row in heights) - terrain.thickness,
        "material": terrain.material,
    }]


# ── layout ───────────────────────────────────────────────────────────────────

def grid_spots(
    rows: int, columns: int, spacing: float, centre: Spot = (0.0, 0.0)
) -> list[Spot]:
    """Evenly spaced rows and columns, centred on `centre`."""
    x0 = centre[0] - (columns - 1) * spacing / 2.0
    z0 = centre[1] - (rows - 1) * spacing / 2.0
    return [
        (x0 + column * spacing, z0 + row * spacing)
        for row in range(rows)
        for column in range(columns)
    ]


def ring_spots(
    count: int, radius: float, centre: Spot = (0.0, 0.0), start_degrees: float = 0.0
) -> list[Spot]:
    """Points spread evenly around a circle."""
    spots = []
    for index in range(count):
        angle = math.radians(start_degrees + 360.0 * index / max(count, 1))
        spots.append(
            (centre[0] + radius * math.cos(angle), centre[1] + radius * math.sin(angle))
        )
    return spots


def line_spots(
    count: int,
    spacing: float,
    angle_degrees: float = 0.0,
    centre: Spot = (0.0, 0.0),
) -> list[Spot]:
    """Points along a straight line through `centre`."""
    angle = math.radians(angle_degrees)
    dx, dz = math.cos(angle), math.sin(angle)
    offset = (count - 1) * spacing / 2.0
    return [
        (
            centre[0] + (index * spacing - offset) * dx,
            centre[1] + (index * spacing - offset) * dz,
        )
        for index in range(count)
    ]


def scatter_spots(
    count: int,
    extent: float,
    seed: int = 0,
    min_gap: float = 2.0,
    centre: Spot = (0.0, 0.0),
    attempts: int = 40,
) -> list[Spot]:
    """Random points at least `min_gap` apart.

    Returns fewer than `count` when the area cannot hold that many at the
    requested spacing.
    """
    rng = random.Random(seed)
    half = extent / 2.0
    spots: list[Spot] = []
    for _ in range(count):
        for _ in range(attempts):
            spot = (
                centre[0] + rng.uniform(-half, half),
                centre[1] + rng.uniform(-half, half),
            )
            if all(math.dist(spot, other) >= min_gap for other in spots):
                spots.append(spot)
                break
    return spots


def jittered(
    spots: Iterable[Spot], amount: float, seed: int = 0
) -> list[Spot]:
    """Push each spot up to `amount` metres off where it was.

    What turns a lattice into a settlement: the rows survive as rows but no
    two are aligned, so the arrangement reads as built up over time rather
    than set out at once.
    """
    rng = random.Random(seed)
    return [
        (x + rng.uniform(-amount, amount), z + rng.uniform(-amount, amount))
        for x, z in spots
    ]


def blocks_spots(
    rows: int,
    columns: int,
    pitch: float,
    centre: Spot = (0.0, 0.0),
    skip: float = 0.0,
    stagger: float = 0.0,
    seed: int = 0,
) -> list[Spot]:
    """A street grid with gaps and offset rows.

    `skip` is the share of plots left empty, and `stagger` shifts alternate
    rows along by that fraction of the pitch. Both exist because a full,
    perfectly aligned lattice is the one arrangement no real district has.
    """
    rng = random.Random(seed)
    x0 = centre[0] - (columns - 1) * pitch / 2.0
    z0 = centre[1] - (rows - 1) * pitch / 2.0
    spots = []
    for row in range(rows):
        offset = pitch * stagger if row % 2 else 0.0
        for column in range(columns):
            if skip > 0.0 and rng.random() < skip:
                continue
            spots.append((x0 + column * pitch + offset, z0 + row * pitch))
    return spots


def contour_radius(
    terrain: Terrain,
    centre: Spot,
    level: float,
    rays: int = 24,
    steps: int = 60,
    fit: str = "inside",
) -> float:
    """How far the ground around `centre` stays at or below `level`.

    Water sized by guesswork either floats above the ground at its edge or
    swallows the shore, so this measures the contour. Which radius to take
    depends on what the disc is for:

    ``inside``  the shortest reach — the largest disc that stays under the
                contour on every side. For a platform or a pad.
    ``cover``   the longest reach — a disc that runs past the waterline on
                every side, so its rim is buried in the rising bank instead
                of standing up out of it as a visible wall. For water.
    """
    if terrain.height is None:
        return 0.0
    limit = terrain.size / 2.0
    step = limit / steps
    reach = []
    for ray in range(rays):
        angle = 2.0 * math.pi * ray / rays
        dx, dz = math.cos(angle), math.sin(angle)
        distance = 0.0
        for index in range(1, steps + 1):
            probe = (centre[0] + dx * index * step, centre[1] + dz * index * step)
            if ground_height(terrain, *probe) > level:
                break
            distance = index * step
        reach.append(distance)
    return max(reach) if fit == "cover" else min(reach)


def _memoised(fn: Callable[[float, float], float]) -> Callable[[float, float], float]:
    """Cache a height function against the points it is asked for.

    Terrain functions compose — ripple, then a river, then graded streets —
    and every sample runs the whole chain. `check_scene` compares each prop
    against every other, so the same handful of points is evaluated tens of
    thousands of times. Without this the cost is quadratic in props times the
    length of the chain.
    """
    seen: dict[tuple[float, float], float] = {}

    def cached(x: float, z: float) -> float:
        key = (x, z)
        value = seen.get(key)
        if value is None:
            value = fn(x, z)
            seen[key] = value
        return value

    return cached


def _distance_to_way(point: Spot, start: Spot, end: Spot) -> float:
    """Shortest distance from a point to a line segment."""
    px, pz = point
    ax, az = start
    dx, dz = end[0] - ax, end[1] - az
    span = dx * dx + dz * dz
    if span < 1e-12:
        return math.dist(point, start)
    along = max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / span))
    return math.dist(point, (ax + along * dx, az + along * dz))


def _nearest_way(point: Spot, ways: Sequence[tuple[Spot, Spot]]) -> float:
    return min(_distance_to_way(point, start, end) for start, end in ways)


def way_distance(point: Spot, ways: Sequence[tuple[Spot, Spot]]) -> float:
    """How far a point is from the nearest run in a network.

    What a layout needs to keep off a road or a river without knowing the
    shape of either: the runs are segments, so this works for a line that
    bends as well as for a straight one.
    """
    return _nearest_way(point, ways) if ways else float("inf")


def clear_of_ways(
    spots: Iterable[Spot],
    ways: Sequence[tuple[Spot, Spot]],
    margin: float,
) -> list[Spot]:
    """Drop the spots within `margin` of any run in a network."""
    if not ways:
        return list(spots)
    return [spot for spot in spots if _nearest_way(spot, ways) > margin]


def winding_spots(
    count: int,
    span: float,
    wander: float,
    along: str = "z",
    seed: int = 0,
    centre: Spot = (0.0, 0.0),
) -> list[Spot]:
    """Points down an axis, pushed sideways by noise so the run bends.

    For a river or a road that has to cross a site without being a ruled
    line. `wander` is how far off the axis the run may stray, in metres.
    """
    spots = []
    for index in range(count):
        share = index / max(count - 1, 1)
        position = -span / 2.0 + span * share
        offset = wander * fractal_noise(
            position, seed * 3.7, wavelength=span * 0.5, seed=seed
        )
        spots.append(
            (centre[0] + offset, centre[1] + position) if along == "z"
            else (centre[0] + position, centre[1] + offset)
        )
    return spots


def ways_along(spots: Sequence[Spot]) -> list[tuple[Spot, Spot]]:
    """Consecutive pairs of a polyline, as segments."""
    return [(spots[index], spots[index + 1]) for index in range(len(spots) - 1)]


def graded(
    terrain: Terrain,
    ways: Sequence[tuple[Spot, Spot]],
    width: float,
    blend: float = 5.0,
    level: float | None = None,
) -> Terrain:
    """Level the ground along a network of runs, easing back either side.

    What a road needs that `flattened` does not give: the whole network is
    brought to one level, so runs that cross agree with each other, and the
    surface under a run of paving is flat along its length. Paving laid on
    unlevelled ground rests each slab on its own lowest corner, which leaves
    steps between neighbours — the potholes.

    `level` defaults to the mean height of the runs' own endpoints, so the
    network sits in the terrain rather than on top of it.
    """
    base = terrain.height
    if not ways:
        return terrain
    if base is None:
        return terrain

    if level is None:
        ends = [spot for way in ways for spot in way]
        level = sum(base(*spot) for spot in ends) / len(ends)

    half = width / 2.0
    flat_to = half + blend
    settled = float(level)

    def height(x: float, z: float) -> float:
        near = _nearest_way((x, z), ways)
        if near <= half:
            return settled
        if near >= flat_to:
            return base(x, z)
        eased = (near - half) / blend
        return settled + (base(x, z) - settled) * eased * eased * (3.0 - 2.0 * eased)

    return replace(terrain, height=_memoised(height))


def carved(
    terrain: Terrain,
    ways: Sequence[tuple[Spot, Spot]],
    width: float,
    depth: float,
    banks: float = 8.0,
    tiles: int | None = None,
) -> Terrain:
    """Cut a channel along a network of runs, with sloping banks.

    The bed is `depth` below whatever the ground was, so a river follows the
    terrain's own fall instead of being a flat trench dropped into it.
    """
    if not ways:
        return terrain
    base = terrain.height or (lambda _x, _z: 0.0)
    half = width / 2.0
    reach = half + banks

    def height(x: float, z: float) -> float:
        here = base(x, z)
        near = _nearest_way((x, z), ways)
        if near <= half:
            return here - depth
        if near >= reach:
            return here
        eased = (near - half) / banks
        return here - depth * (1.0 - eased * eased * (3.0 - 2.0 * eased))

    return replace(
        terrain,
        height=_memoised(height),
        tiles=max(terrain.tiles, tiles or terrain.tiles, 2),
    )


def irregular_lines(
    extent: float, least: float, most: float, seed: int = 0
) -> list[float]:
    """Offsets across `extent` at irregular spacing, for a street grid.

    An even pitch gives identical blocks. Real districts have blocks of
    different depths, which is what leaves some buildings crowded together
    and others with room around them.
    """
    rng = random.Random(seed)
    half = extent / 2.0
    offsets = [-half]
    while True:
        following = offsets[-1] + rng.uniform(least, most)
        if following > half - least:
            break
        offsets.append(following)
    offsets.append(half)
    return offsets


def pinned(
    terrain: Terrain, props: Sequence["Prop"], level: float
) -> list["Prop"]:
    """Rest each prop at `level` instead of on the ground under it.

    What a deck needs: a bridge or a flyover holds its own line while the
    ground falls away beneath it, which is the opposite of how every other
    prop is placed.
    """
    for prop in props:
        prop.sink = ground_under(terrain, prop) - level
    return list(props)


def arc_spots(
    centre: Spot,
    radius: float,
    start_degrees: float,
    end_degrees: float,
    count: int,
) -> list[Spot]:
    """Points along an arc, for a connector that curves between two runs.

    A slip road between levels of an interchange is a curve; laid as straight
    segments it reads as a chamfered corner rather than a ramp.
    """
    spots = []
    for index in range(max(count, 2)):
        share = index / (max(count, 2) - 1)
        angle = math.radians(start_degrees + (end_degrees - start_degrees) * share)
        spots.append((centre[0] + radius * math.cos(angle),
                      centre[1] + radius * math.sin(angle)))
    return spots


def sloped(
    terrain: Terrain,
    props: Sequence["Prop"],
    start: float,
    end: float,
) -> list["Prop"]:
    """Carry a run of props from `start` up to `end`, evenly along its length.

    A ramp between two levels of an interchange. `pinned` holds one height, so
    a ramp built from it would be a step; this spreads the climb across the
    run so the slabs meet.
    """
    total = max(len(props) - 1, 1)
    for index, prop in enumerate(props):
        level = start + (end - start) * index / total
        prop.sink = ground_under(terrain, prop) - level
    return list(props)


def clustered_spots(
    clusters: int,
    per_cluster: int,
    extent: float,
    spread: float,
    seed: int = 0,
    min_gap: float = 3.0,
    centres: Sequence[Spot] | None = None,
    attempts: int = 30,
) -> list[Spot]:
    """Random points gathered into a few loose groups.

    A plain scatter spreads everything at one density, which reads as evenly
    sown rather than settled. Real building goes up in hamlets and terraces
    with empty ground between, which is what this produces: each group holds
    up to `per_cluster` points within `spread` metres of its centre.

    `centres` places the groups explicitly — for seeds already chosen against
    the terrain. Without it, `clusters` centres are drawn at random from
    `extent`. Either way no point leaves `extent`.
    """
    rng = random.Random(seed)
    half = extent / 2.0
    seeds = list(centres) if centres is not None else [
        (rng.uniform(-half, half), rng.uniform(-half, half))
        for _ in range(clusters)
    ]

    spots: list[Spot] = []
    for origin in seeds:
        for _ in range(per_cluster):
            for _ in range(attempts):
                angle = rng.uniform(0.0, 2.0 * math.pi)
                distance = spread * math.sqrt(rng.random())
                spot = (origin[0] + distance * math.cos(angle),
                        origin[1] + distance * math.sin(angle))
                if abs(spot[0]) > half or abs(spot[1]) > half:
                    continue
                if all(math.dist(spot, other) >= min_gap for other in spots):
                    spots.append(spot)
                    break
    return spots


def strip_spots(
    start: Spot, end: Spot, tile: float, gap: float = 0.0
) -> tuple[list[Spot], float, float]:
    """Positions, yaw and length for tiles laid end to end along a line.

    Returns ``(spots, yaw_degrees, tile_length)``, so a road or kerb can be
    built as a run of boxes that meet, rather than one long box that ignores
    the ground under it.
    """
    span = math.dist(start, end)
    pitch = tile + gap
    count = max(int(span / pitch), 1)
    yaw = -math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))
    dx = (end[0] - start[0]) / span if span else 0.0
    dz = (end[1] - start[1]) / span if span else 0.0

    spots = []
    for index in range(count):
        along = pitch * (index + 0.5)
        spots.append((start[0] + dx * along, start[1] + dz * along))
    return spots, yaw, tile


def path_tiles(
    spots: Sequence[Spot], tile: float, gap: float = 0.0
) -> list[tuple[Spot, float, float]]:
    """Tiles following a polyline, each turned to its own segment.

    Returns ``(position, yaw_degrees, length)`` per tile. Each segment is
    covered by whole tiles slightly stretched to fit it, so a bend has no gap
    at the corner and no slab sticking out past the turn.
    """
    laid: list[tuple[Spot, float, float]] = []
    for start, end in ways_along(spots):
        span = math.dist(start, end)
        if span < 1e-9:
            continue
        count = max(int(round(span / (tile + gap))), 1)
        length = span / count
        yaw = -math.degrees(math.atan2(end[1] - start[1], end[0] - start[0]))
        dx = (end[0] - start[0]) / span
        dz = (end[1] - start[1]) / span
        for index in range(count):
            along = length * (index + 0.5)
            laid.append(
                ((start[0] + dx * along, start[1] + dz * along), yaw, length)
            )
    return laid


def paved(
    prefix: str,
    tiles: Sequence[tuple[Spot, float, float]],
    width: float,
    thickness: float = 0.16,
    material: str = "block",
    group: str = "paving",
    sink: float = 0.06,
) -> list["Prop"]:
    """Props for a run of paving from `path_tiles` output."""
    return [
        Prop(
            id=f"{prefix}-{index:03d}",
            kind="box",
            at=spot,
            size=(length, thickness, width),
            yaw=yaw,
            material=material,
            group=group,
            sink=sink,
        )
        for index, (spot, yaw, length) in enumerate(tiles)
    ]


def clear_circle(
    spots: Iterable[Spot], centre: Spot = (0.0, 0.0), radius: float = 4.0
) -> list[Spot]:
    """Drop the spots inside a circle, leaving room for spawn or play space."""
    return [spot for spot in spots if math.dist(spot, centre) > radius]


def on_high_ground(
    spots: Iterable[Spot], terrain: Terrain, above: float
) -> list[Spot]:
    """Keep the spots where the ground stands at least `above` metres up.

    Ties a layout to the shape under it, so a settlement claims the ridges and
    a basin's rim rather than being scattered without regard to relief.
    """
    return [spot for spot in spots if ground_height(terrain, *spot) >= above]


def on_low_ground(
    spots: Iterable[Spot], terrain: Terrain, below: float
) -> list[Spot]:
    """Keep the spots where the ground sits at or under `below` metres."""
    return [spot for spot in spots if ground_height(terrain, *spot) <= below]


def on_slope(
    spots: Iterable[Spot], terrain: Terrain, steeper_than: float, step: float = 1.0
) -> list[Spot]:
    """Keep the spots where the ground falls faster than `steeper_than`.

    Measured as metres of fall per metre travelled, sampled `step` metres out
    in x and z.
    """
    kept = []
    for x, z in spots:
        here = ground_height(terrain, x, z)
        gradient = max(
            abs(ground_height(terrain, x + step, z) - here),
            abs(ground_height(terrain, x, z + step) - here),
        ) / step
        if gradient > steeper_than:
            kept.append((x, z))
    return kept


def in_height_band(
    spots: Iterable[Spot],
    terrain: Terrain,
    lowest: float,
    highest: float,
) -> list[Spot]:
    """Keep the spots whose ground lies between two heights.

    What a contour band gives that a ring does not: the spots follow whatever
    shape the terrain has at that elevation, so a settlement wraps the basin
    unevenly instead of sitting on a circle drawn around its centre.
    """
    return [
        spot for spot in spots
        if lowest <= ground_height(terrain, *spot) <= highest
    ]


def lowest_spot(terrain: Terrain, samples: int = 48) -> Spot:
    """Where the terrain bottoms out, found by sampling.

    Water belongs wherever the ground actually drains to, which after the
    noise is applied is not the centre the landform was built around.
    """
    if terrain.height is None:
        return (0.0, 0.0)
    step = terrain.size / samples
    start = -terrain.size / 2.0 + step / 2.0
    best = (0.0, 0.0)
    best_height = float("inf")
    for row in range(samples):
        for column in range(samples):
            spot = (start + column * step, start + row * step)
            here = ground_height(terrain, *spot)
            if here < best_height:
                best, best_height = spot, here
    return best


def channel_spots(
    terrain: Terrain,
    count: int,
    along: str = "z",
    level: float = 0.0,
    span: float = 0.9,
    wander: float = 0.0,
    seed: int = 0,
) -> list[Spot]:
    """Points down the middle of the low ground, one per step along an axis.

    Measured from the terrain rather than computed from the shape that made
    it, so the points follow whatever channel came out — including the noise.
    `wander` offsets each point across the channel by that fraction of its
    local width, so a trail reads as a trail rather than as a centreline.
    """
    rng = random.Random(seed)
    reach = terrain.size * span
    spacing = reach / max(count - 1, 1)
    samples = max(terrain.tiles, 32)
    step = terrain.size / samples
    start = -terrain.size / 2.0

    spots = []
    for index in range(count):
        position = -reach / 2.0 + index * spacing
        crossing = [
            start + n * step for n in range(samples + 1)
            if ground_height(
                terrain,
                *((start + n * step, position) if along == "z"
                  else (position, start + n * step)),
            ) <= level
        ]
        if not crossing:
            continue
        middle = sum(crossing) / len(crossing)
        width = max(crossing) - min(crossing)
        offset = rng.uniform(-wander, wander) * width / 2.0
        across = max(min(middle + offset, max(crossing)), min(crossing))
        spots.append((across, position) if along == "z" else (position, across))
    return spots


def channel_edge(
    terrain: Terrain,
    position: float,
    side: float,
    along: str = "z",
    level: float = 0.0,
) -> float | None:
    """Where the low ground ends on one side, at one point along an axis.

    `side` is -1 for the near edge and +1 for the far one. Returns None where
    the terrain has no low ground at that point. Lets something be set against
    the foot of a wall that moves, rather than at a fixed offset from centre.
    """
    if terrain.height is None:
        return None
    samples = max(terrain.tiles, 32)
    step = terrain.size / samples
    start = -terrain.size / 2.0
    crossing = [
        start + n * step for n in range(samples + 1)
        if ground_height(
            terrain,
            *((start + n * step, position) if along == "z"
              else (position, start + n * step)),
        ) <= level
    ]
    if not crossing:
        return None
    return max(crossing) if side > 0 else min(crossing)


def drift(
    spots: Iterable[Spot],
    amount: float,
    seed: int = 0,
    inward: Spot | None = None,
) -> list[Spot]:
    """Push each spot off its position, optionally biased towards a point.

    Unlike `jittered`, the offset is polar and may be biased, so a ring can be
    broken up without the result still reading as a ring: some plots move up
    the slope and some down, by different amounts.
    """
    rng = random.Random(seed)
    moved = []
    for x, z in spots:
        angle = rng.uniform(0.0, 2.0 * math.pi)
        distance = amount * math.sqrt(rng.random())
        nx, nz = x + distance * math.cos(angle), z + distance * math.sin(angle)
        if inward is not None:
            pull = rng.uniform(-amount, amount)
            towards = math.atan2(inward[1] - z, inward[0] - x)
            nx += pull * math.cos(towards)
            nz += pull * math.sin(towards)
        moved.append((nx, nz))
    return moved


def fits(
    terrain: Terrain,
    candidate: Prop,
    placed: Sequence[Prop],
    margin: float = 0.0,
) -> bool:
    """Whether a prop can be added without clashing with what is there.

    Measures the candidate's own turned footprint, which `clear_of` cannot do
    because it is given a bare position. What a template needs when it is
    choosing sizes and positions together, one building at a time.

    Both the true footprint and the grown one are tested, because widening a
    footprint lowers where the prop rests — `ground_under` takes the lowest
    ground beneath the whole base — so on sloping ground the grown copy can
    sit far enough down to pass under something the real prop would hit.
    """
    grown = replace(candidate, size=(
        candidate.size[0] + margin * 2.0,
        candidate.size[1],
        candidate.size[2] + margin * 2.0,
    ))
    return not any(
        overlap(terrain, candidate, other) or overlap(terrain, grown, other)
        for other in placed
        if not (candidate.group and candidate.group == other.group)
    )


def clear_of(
    spots: Iterable[Spot],
    props: Sequence["Prop"],
    margin: float = 0.0,
) -> list[Spot]:
    """Drop the spots that land on something already placed.

    Compared against each prop's yawed footprint plus `margin`, so scenery can
    be scattered over a whole site and still miss the buildings.
    """
    kept = []
    for spot in spots:
        if all(not _inside(spot, prop, margin) for prop in props):
            kept.append(spot)
    return kept


def _inside(spot: Spot, prop: "Prop", margin: float) -> bool:
    half_x, half_z = footprint(prop)
    return (
        abs(spot[0] - prop.at[0]) <= half_x + margin
        and abs(spot[1] - prop.at[1]) <= half_z + margin
    )


# ── sizing ───────────────────────────────────────────────────────────────────

def uniform_sizes(count: int, size: Vec3) -> list[Vec3]:
    """The same size for every prop."""
    return [tuple(float(v) for v in size)] * count  # type: ignore[return-value]


def varied_sizes(
    count: int, base: Vec3, spread: float = 0.25, seed: int = 0
) -> list[Vec3]:
    """Sizes jittered around `base` by up to `spread` either way."""
    rng = random.Random(seed)
    sizes = []
    for _ in range(count):
        scale = 1.0 + rng.uniform(-spread, spread)
        sizes.append(tuple(float(v) * scale for v in base))
    return sizes  # type: ignore[return-value]


def graded_sizes(count: int, near: Vec3, far: Vec3) -> list[Vec3]:
    """Sizes stepping from `near` to `far` across the run."""
    if count == 1:
        return [tuple(float(v) for v in near)]  # type: ignore[return-value]
    sizes = []
    for index in range(count):
        t = index / (count - 1)
        sizes.append(tuple(float(a) + (float(b) - float(a)) * t
                           for a, b in zip(near, far)))
    return sizes  # type: ignore[return-value]


def tiered_heights(
    spots: Sequence[Spot],
    tall: float,
    short: float,
    centre: Spot = (0.0, 0.0),
    reach: float | None = None,
    seed: int = 0,
    spread: float = 0.45,
) -> list[float]:
    """Heights falling off from a centre, with each one varied around that.

    A skyline needs both: the trend, so there is a core and outskirts, and the
    scatter, so neighbours differ. A pure falloff gives concentric rings of
    identical buildings, and pure noise gives no skyline at all.

    `spread` is how far a single height may sit from its tier, as a fraction.
    """
    rng = random.Random(seed)
    if reach is None:
        reach = max((math.dist(spot, centre) for spot in spots), default=1.0)
    heights = []
    for spot in spots:
        # Squared, so the tall core is compact and the fall to the edge is
        # gradual — the shape of a real downtown against its suburbs.
        away = min(math.dist(spot, centre) / max(reach, 1e-6), 1.0)
        tier = tall + (short - tall) * away * away
        heights.append(max(short * 0.5, tier * (1.0 + rng.uniform(-spread, spread))))
    return heights


def stepped_sizes(
    footprints: Sequence[Vec3], heights: Sequence[float]
) -> list[Vec3]:
    """Pair footprints with heights, one size per prop."""
    return [
        (float(footprint[0]), float(height), float(footprint[2]))
        for footprint, height in zip(footprints, heights)
    ]


# ── props and scene ──────────────────────────────────────────────────────────

@dataclass
class Prop:
    """One object standing on the terrain.

    `at` is the ground-plane position; the height comes from the terrain, so
    moving a prop never leaves it floating. `source` replaces the primitive
    with a generated GLB and is what stage 2 fills in. Props sharing a `group`
    are expected to touch — road segments, wall runs, floor tiles — and are not
    reported against each other as collisions.
    """

    id: str
    kind: str = "box"
    at: Spot = (0.0, 0.0)
    size: Vec3 = (1.0, 1.0, 1.0)
    yaw: float = 0.0
    material: str = "block"
    source: str | None = None
    sink: float = 0.0
    group: str = ""


@dataclass
class Scene:
    """A terrain plus everything standing on it."""

    name: str
    terrain: Terrain = field(default_factory=Terrain)
    props: list[Prop] = field(default_factory=list)
    forward: str = "+z"
    materials: dict[str, dict[str, Any]] = field(
        default_factory=lambda: dict(GREYBOX_MATERIALS)
    )


def place(
    prefix: str,
    kind: str,
    spots: Sequence[Spot],
    size: Vec3 | Sequence[Vec3] = (1.0, 1.0, 1.0),
    material: str = "block",
    yaw: float | Sequence[float] = 0.0,
    face_centre: bool = False,
    group: str = "",
    sink: float = 0.0,
) -> list[Prop]:
    """Turn positions into props, one per spot.

    `size` and `yaw` take either one value for all of them or one per spot.
    `face_centre` turns each prop to look at the origin, which is what a ring
    of seats or walls wants. `group` marks a run that is meant to touch —
    paving, kerbs, a wall — so `check_scene` does not report it as clashing.
    """
    sizes = list(size) if _is_sequence_of_vec3(size) else [size] * len(spots)
    if face_centre:
        yaws = [math.degrees(math.atan2(x, z)) for x, z in spots]
    elif isinstance(yaw, (int, float)):
        yaws = [float(yaw)] * len(spots)
    else:
        yaws = list(yaw)

    return [
        Prop(
            id=f"{prefix}-{index:02d}",
            kind=kind,
            at=(float(spot[0]), float(spot[1])),
            size=tuple(float(v) for v in sizes[index]),  # type: ignore[arg-type]
            yaw=float(yaws[index]),
            material=material,
            group=group,
            sink=float(sink),
        )
        for index, spot in enumerate(spots)
    ]


def scatter(
    prefix: str,
    kind: str,
    spots: Sequence[Spot],
    size: Vec3,
    material: str = "block",
    spread: float = 0.4,
    buried: float = 0.0,
    turn: float = 37.0,
    seed: int = 0,
) -> list[Prop]:
    """Scenery: one prop per spot, sizes varied and optionally part-buried.

    The pattern every landform's loose scenery wants — boulders, scrub, reeds,
    debris. `buried` is the share of a prop's height to sink, which is what
    makes a rock sit *in* the ground rather than rest on it, and `turn` is the
    yaw step between consecutive props so no two share a facing.
    """
    props = place(
        prefix, kind, spots,
        size=varied_sizes(len(spots), size, spread=spread, seed=seed),
        material=material,
        yaw=[index * turn for index in range(len(spots))],
    )
    if buried:
        for prop in props:
            prop.sink = prop.size[1] * buried
    return props


def fit_all(
    terrain: Terrain,
    candidates: Iterable[Prop],
    placed: Sequence[Prop] = (),
    margin: float = 0.0,
) -> list[Prop]:
    """Keep the candidates that fit, adding each to what the next is measured
    against.

    The pattern for anything sized and positioned together — a building whose
    footprint is only known once its size is drawn. Order matters: earlier
    candidates win, so pass the ones that must be placed first.
    """
    kept: list[Prop] = []
    for candidate in candidates:
        if fits(terrain, candidate, [*placed, *kept], margin=margin):
            kept.append(candidate)
    return kept


def ring_wall(
    prefix: str,
    radius: float,
    segments: int,
    height: float,
    thickness: float = 1.1,
    vary: float = 0.55,
    material: str = "wall",
    group: str = "rampart",
    overlap: float = 1.08,
) -> list[Prop]:
    """A closed wall of segments around a circle, each turned tangent.

    Segments are cut slightly long (`overlap`) so they meet on the outside of
    the curve rather than leaving a wedge at every joint, and `vary` steps the
    height along the run, which is what gives a rampart a parapet line instead
    of one unbroken top edge.
    """
    chord = 2.0 * math.pi * radius / max(segments, 1)
    props = []
    for index in range(segments):
        angle = 2.0 * math.pi * index / max(segments, 1)
        props.append(Prop(
            id=f"{prefix}-{index:02d}",
            kind="box",
            at=(radius * math.cos(angle), radius * math.sin(angle)),
            size=(chord * overlap, height + (index % 3) * vary, thickness),
            yaw=-math.degrees(angle + math.pi / 2.0),
            material=material,
            group=group,
        ))
    return props


def water_along(
    terrain: Terrain,
    prefix: str,
    spots: Sequence[Spot],
    tile: float,
    width: float,
    depth: float,
    level: float,
    material: str = "water",
    group: str = "river",
) -> list[Prop]:
    """Water panels down a polyline, all at one surface level.

    Pinned rather than rested: rested, each panel would follow its own stretch
    of bed and the surface would come out as a flight of steps. `level` is the
    waterline, and the panels hang `depth` below it.
    """
    return pinned(
        terrain,
        [
            Prop(id=f"{prefix}-{index:03d}", kind="box", at=spot,
                 size=(length, depth, width), yaw=yaw,
                 material=material, group=group)
            for index, (spot, yaw, length) in enumerate(
                path_tiles(spots, tile)
            )
        ],
        level=level - depth,
    )


def columns(
    terrain: Terrain,
    prefix: str,
    tiles: Sequence[tuple[Spot, float, float]],
    top: float,
    side: float = 2.4,
    embed: float = 0.9,
    material: str = "wall",
    group: str = "flyover",
) -> list[Prop]:
    """Supports reaching from their own footing up to `top`.

    Each is grown from the ground beneath it rather than given one height, so
    none stops short where the ground sits low. `embed` is how far it carries
    past the deck, so the joint is closed.
    """
    props = []
    for index, (spot, yaw, _length) in enumerate(tiles):
        footing = ground_height(terrain, *spot)
        props.append(Prop(
            id=f"{prefix}-{index:02d}",
            kind="box",
            at=spot,
            size=(side, top - footing + embed, side),
            yaw=yaw,
            material=material,
            group=group,
        ))
    return props


def road_network(
    terrain: Terrain,
    lines: dict[str, Sequence[Spot]],
    widths: dict[str, float],
    tile: float,
    level: float,
    over: Sequence[tuple[Spot, Spot]] = (),
    span: float = 0.0,
    structure: float = 0.0,
) -> tuple[list[Prop], list[Prop]]:
    """Paving for a set of runs, split into what rests and what is carried.

    Returns ``(resting, carried)``. A slab within `span` of a run in `over` is
    pinned to `level` instead of resting on the ground, which is what carries
    a street across a channel cut beneath it — resting, each slab would follow
    its own patch of bank and step away from its neighbours. Slabs within
    `structure` are marked as deck, since only the part actually spanning the
    gap reads as a bridge; the rest is embankment.
    """
    resting: list[Prop] = []
    carried: list[Prop] = []
    for name, line in lines.items():
        for slab in paved(name, path_tiles(line, tile), widths[name],
                          group="streets"):
            near = way_distance(slab.at, over) if over else float("inf")
            if near < span:
                carried.append(slab)
                if near < structure:
                    slab.material = "wall"
            else:
                resting.append(slab)
    pinned(terrain, carried, level=level)
    return resting, carried


def interchange(
    terrain: Terrain,
    hub: Spot,
    size: float,
    lane: float,
    ground_level: float,
    levels: tuple[float, float],
    tile: float,
    seed: int = 0,
    ramp_tile: float = 4.5,
) -> tuple[list[Prop], list[tuple[Spot, float, float]], list[Prop]]:
    """Two crossing roads at different heights, joined by curved ramps.

    What makes this an interchange rather than a flyover: the roads cross at
    different levels and traffic can move between them, so four quadrant
    loops connect the two decks and a slip road climbs to the lower one from
    the ground.

    Returns ``(decks, deck_tiles, ramps)`` — `deck_tiles` being the two
    through routes' tiles, for putting supports under.
    """
    lower, upper = levels
    radius = lane * 3.2

    routes = []
    decks: list[Prop] = []
    for name, along, centre, width, height, key in (
        ("flyover", "x", hub, lane * 1.1, lower, seed + 21),
        ("overpass", "z", (hub[0], 0.0), lane * 1.05, upper, seed + 22),
    ):
        # Nearly straight through the hub, and centred on it across its own
        # axis but on the origin along it, so neither route runs off the site.
        # A route that wandered far would leave the ramps' ends short of it,
        # since the ramps are anchored on the hub's own axes.
        tiles = path_tiles(
            winding_spots(7, span=size * 0.9, wander=size * 0.012,
                          along=along, seed=key, centre=centre),
            tile,
        )
        routes.append(tiles)
        decks += pinned(
            terrain,
            paved(name, tiles, width, thickness=0.9, material="wall",
                  group="flyover", sink=0.0),
            level=height,
        )

    # The arcs are sampled at roughly one point per tile: `path_tiles` lays at
    # least one whole tile per leg, so a finely sampled polyline yields tiles
    # far shorter than asked for — and a slab wider than it is long reads as a
    # rung rather than as road surface, so the width is kept near the tile
    # length. A `Prop` has yaw but no pitch, so a ramp is always a flight of
    # level slabs; what makes it read as a road is a small step between them,
    # which is why the decks are best kept close together.
    def ramp(name: str, spots, start: float, end: float) -> list[Prop]:
        return sloped(
            terrain,
            paved(name, path_tiles(spots, ramp_tile), lane * 0.55,
                  thickness=1.0, material="wall", group="flyover", sink=0.0),
            start=start, end=end,
        )

    # A quadrant loop in each corner, each a quarter turn from a point on the
    # lower deck's line to a point on the upper deck's line, so both ends land
    # on the roads they connect rather than stopping in mid air.
    ramps: list[Prop] = []
    for quadrant, (sx, sz) in enumerate(((1, 1), (1, -1), (-1, 1), (-1, -1))):
        centre = (hub[0] + sx * radius, hub[1] + sz * radius)
        on_lower = -sz * 90.0
        on_upper = 180.0 if sx > 0 else 0.0
        # The short way round, so the arc is the quarter turn and not the
        # three quarters going the other way.
        while on_upper - on_lower > 180.0:
            on_upper -= 360.0
        while on_upper - on_lower < -180.0:
            on_upper += 360.0

        ramps += ramp(
            f"ramp{quadrant}",
            arc_spots(centre, radius, on_lower, on_upper, 10),
            lower, upper,
        )

    # And a slip road up to the lower deck, so the structure is reachable from
    # the ground. It ends on the lower deck's line, further out than the loops.
    slip = (hub[0] - 3.0 * radius, hub[1] + radius)
    ramps += ramp("link", arc_spots(slip, radius, 90.0, -90.0, 19),
                  ground_level + 0.4, lower)

    return decks + ramps, routes, ramps


def _is_sequence_of_vec3(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and bool(value)
        and isinstance(value[0], (list, tuple))
    )


def placed_height(prop: Prop) -> float:
    """How tall the prop ends up once written.

    A primitive is exactly its declared height. A mesh is fitted into `size`
    by a single factor to keep its proportions, so it reaches the declared
    height only on its longest axis and is shorter on the other two.
    """
    if not prop.source:
        return float(prop.size[1])

    from models.common.glb_writer import mesh_unit_extent

    return mesh_unit_extent(prop.source)[1] * max(float(v) for v in prop.size)


def ground_under(terrain: Terrain, prop: Prop) -> float:
    """The height a prop rests at, given the ground across its whole base.

    The lowest point under the footprint, not the height at the centre. On
    uneven ground a wide base sampled only at its centre leaves one corner
    hanging in the air, and the gap is exactly the relief the terrain was
    given for.

    Measured on the upright footprint rather than the turned corners, since
    those come from `bounds`, which needs the resting height this returns.
    """
    x, z = prop.at
    if terrain.height is None:
        return 0.0
    half_x, half_z = footprint(prop)
    return min(
        ground_height(terrain, x + dx, z + dz)
        for dx in (-half_x, 0.0, half_x)
        for dz in (-half_z, 0.0, half_z)
    )


def prop_part(terrain: Terrain, prop: Prop) -> dict[str, Any]:
    """Spec part for one prop, resting on the terrain."""
    x, z = prop.at
    base = ground_under(terrain, prop) - prop.sink
    part: dict[str, Any] = {
        "id": prop.id,
        "kind": "mesh" if prop.source else prop.kind,
        "at": (x, base + placed_height(prop) / 2.0, z),
        "size": tuple(float(v) for v in prop.size),
        "material": prop.material,
    }
    if prop.yaw:
        part["rotation"] = (0.0, float(prop.yaw), 0.0)
    if prop.source:
        part["source"] = prop.source
    return part


def build_spec(scene: Scene) -> dict[str, Any]:
    """Assemble the scene into a spec `write_spec_glb` can write."""
    return {
        "subject": scene.name,
        "units": UNITS,
        "forward": scene.forward,
        "materials": scene.materials,
        "parts": terrain_parts(scene.terrain)
        + [prop_part(scene.terrain, prop) for prop in scene.props],
    }


def write_scene(scene: Scene, out_path: str | Path) -> str:
    """Write the scene as a GLB and return the path."""
    from models.common.glb_writer import write_spec_glb

    return write_spec_glb(build_spec(scene), out_path)


# ── validation ───────────────────────────────────────────────────────────────

def footprint(prop: Prop) -> tuple[float, float]:
    """Half-extents in x and z of the upright box around the turned prop.

    Used to keep scattered spots off what is already placed. `overlap` measures
    the turned shape itself, so this only needs to be an outer bound.
    """
    half_x, half_z = prop.size[0] / 2.0, prop.size[2] / 2.0
    angle = math.radians(prop.yaw)
    cos, sin = abs(math.cos(angle)), abs(math.sin(angle))
    return (half_x * cos + half_z * sin, half_x * sin + half_z * cos)


def bounds(terrain: Terrain, prop: Prop) -> tuple[Vec3, Vec3]:
    """World-space minimum and maximum corners of a placed prop.

    A prop carrying a `source` is measured by the writer's own
    `rotated_bounds`, because a mesh is fitted into `size` by a single factor
    and so fills the box on its longest axis only. Measuring it as the full
    box would validate an arrangement that is not the one written.
    """
    part = prop_part(terrain, prop)
    if prop.source:
        from models.common.glb_writer import rotated_bounds

        return rotated_bounds(
            part["size"], part["at"], part.get("rotation", (0.0, 0.0, 0.0)),
            kind="mesh", source=prop.source,
        )

    x, z = prop.at
    half_x, half_z = footprint(prop)
    base = ground_under(terrain, prop) - prop.sink
    return (
        (x - half_x, base, z - half_z),
        (x + half_x, base + prop.size[1], z + half_z),
    )


def ground_corners(terrain: Terrain, prop: Prop) -> list[Spot]:
    """The prop's four ground-plane corners, turned by its yaw.

    Turned with the writer's own `euler_matrix`, so the shape measured here is
    the shape written. A prop carrying a `source` is squared off instead, since
    the writer reports a mesh's extent as an axis-aligned box.
    """
    if prop.source:
        low, high = bounds(terrain, prop)
        return [
            (low[0], low[2]), (high[0], low[2]),
            (high[0], high[2]), (low[0], high[2]),
        ]

    from models.common.glb_writer import apply_matrix, euler_matrix

    matrix = euler_matrix((0.0, float(prop.yaw), 0.0))
    half_x, half_z = prop.size[0] / 2.0, prop.size[2] / 2.0
    corners = []
    for dx, dz in ((-half_x, -half_z), (half_x, -half_z),
                   (half_x, half_z), (-half_x, half_z)):
        turned = apply_matrix(matrix, (dx, 0.0, dz))
        corners.append((prop.at[0] + turned[0], prop.at[1] + turned[2]))
    return corners


def _plane_overlap(first: list[Spot], second: list[Spot]) -> float:
    """Least distance the two quads would move to separate, or 0 when apart.

    Tested against every edge normal of both shapes, so a turned box is
    measured as itself rather than as the larger upright box around it — which
    would report clashes between neighbours that are comfortably clear.
    """
    least = float("inf")
    for corners in (first, second):
        for index in range(len(corners)):
            x1, z1 = corners[index]
            x2, z2 = corners[(index + 1) % len(corners)]
            length = math.hypot(x2 - x1, z2 - z1)
            if length < 1e-9:
                continue
            axis = (-(z2 - z1) / length, (x2 - x1) / length)

            reach_a = [x * axis[0] + z * axis[1] for x, z in first]
            reach_b = [x * axis[0] + z * axis[1] for x, z in second]
            gap = min(max(reach_a) - min(reach_b), max(reach_b) - min(reach_a))
            if gap <= 0.0:
                return 0.0
            least = min(least, gap)
    return 0.0 if least == float("inf") else least


def overlap(terrain: Terrain, first: Prop, second: Prop) -> tuple[float, float] | None:
    """How far two props interpenetrate on the ground and in height.

    Returns None when they are clear in either, so a roof resting on a house or
    a canopy above a trunk reads as separation rather than a collision.
    """
    low_a, high_a = bounds(terrain, first)
    low_b, high_b = bounds(terrain, second)
    vertical = min(high_a[1], high_b[1]) - max(low_a[1], low_b[1])
    if vertical <= 0.0:
        return None

    plane = _plane_overlap(
        ground_corners(terrain, first), ground_corners(terrain, second)
    )
    return (plane, vertical) if plane > 0.0 else None


def check_scene(scene: Scene, tolerance: float = 0.01) -> list[str]:
    """Report what would make the greybox unusable.

    Catches the faults that survive into a finished scene: props off the
    terrain, props inside one another, sizes that read wrong against a human,
    and duplicate ids that would collapse two nodes into one after export.
    `tolerance` is how much interpenetration counts as touching rather than
    clashing.
    """
    problems: list[str] = []
    half = scene.terrain.size / 2.0

    seen: set[str] = set()
    for prop in scene.props:
        if prop.id in seen:
            problems.append(f"{prop.id}: duplicate id")
        seen.add(prop.id)

        if any(value <= 0 for value in prop.size):
            problems.append(f"{prop.id}: size {prop.size} must be positive in every axis")
            continue

        low, high = bounds(scene.terrain, prop)
        if min(low[0], low[2]) < -half or max(high[0], high[2]) > half:
            problems.append(
                f"{prop.id}: spans x {low[0]:.1f}..{high[0]:.1f}, "
                f"z {low[2]:.1f}..{high[2]:.1f}, past the "
                f"{scene.terrain.size:.0f} m terrain"
            )

        height = prop.size[1]
        if height > TALLEST_STRUCTURE:
            problems.append(
                f"{prop.id}: {height:.1f} m tall, over {TALLEST_STRUCTURE:.0f} m "
                "— check the units"
            )
        elif height < HUMAN_HEIGHT / 20:
            problems.append(
                f"{prop.id}: {height:.2f} m tall, under a twentieth of a person"
            )

    for index, prop in enumerate(scene.props):
        for other in scene.props[index + 1:]:
            if prop.group and prop.group == other.group:
                continue
            depths = overlap(scene.terrain, prop, other)
            if depths and min(depths) > tolerance:
                problems.append(
                    f"{prop.id} and {other.id}: overlap by {depths[0]:.2f} m "
                    f"on the ground, {depths[1]:.2f} m in height"
                )

    return problems


def scene_summary(scene: Scene) -> dict[str, Any]:
    """Counts and extents, for a report or a test assertion."""
    heights = [prop.size[1] for prop in scene.props] or [0.0]
    return {
        "name": scene.name,
        "terrain_size": scene.terrain.size,
        "terrain_tiles": scene.terrain.tiles,
        "props": len(scene.props),
        "kinds": sorted({prop.kind for prop in scene.props}),
        "materials": sorted({prop.material for prop in scene.props}),
        "tallest": max(heights),
        "generated_parts": sum(1 for prop in scene.props if prop.source),
    }


# ── stage 2: detail ──────────────────────────────────────────────────────────

def swap_mesh(
    scene: Scene, prop_id: str, source: str | Path, height: float | None = None
) -> Scene:
    """Replace one greybox prop with a generated mesh, keeping its placement.

    The position is what the greybox already validated, so the mesh drops into
    the spot the block occupied.

    `height` is how tall the mesh should end up. Without it the prop's declared
    box is used, and because a mesh is fitted by a single factor that means its
    *largest* dimension — swapping a figure into a wide flat dais would scale
    the figure to the dais's width. State the height whenever the mesh is not
    the same shape as the block it replaces.
    """
    if not any(prop.id == prop_id for prop in scene.props):
        raise KeyError(f"no prop {prop_id!r} in scene {scene.name!r}")

    size = None
    if height is not None:
        from models.common.glb_writer import mesh_unit_extent

        extent = mesh_unit_extent(str(source))[1]
        if extent <= 0:
            raise ValueError(f"{source} has no height to scale")
        scale = float(height) / extent
        size = (scale, scale, scale)

    return replace(
        scene,
        props=[
            replace(prop, source=str(source), **({"size": size} if size else {}))
            if prop.id == prop_id
            else prop
            for prop in scene.props
        ],
    )


def swap_material(scene: Scene, material: str, colour: Sequence[float],
                  roughness: float = 0.8, metallic: float = 0.0) -> Scene:
    """Recolour one material across the whole scene."""
    colours = list(colour)
    if len(colours) == 3:
        colours.append(1.0)
    materials = dict(scene.materials)
    materials[material] = {
        "baseColor": [float(v) for v in colours],
        "roughness": float(roughness),
        "metallic": float(metallic),
    }
    return replace(scene, materials=materials)


def texture_ids(scene: Scene) -> list[str]:
    """Catalogue asset ids covering the materials this scene uses."""
    used = {scene.terrain.material} | {prop.material for prop in scene.props}
    return sorted({MATERIAL_TEXTURES[name] for name in used if name in MATERIAL_TEXTURES})


def stage_scene_textures(
    project_dir: str | Path, scene: Scene, cache_dir: str | Path | None = None
) -> list[dict[str, Any]]:
    """Copy this scene's ground and wall textures into a game project.

    Returns one record per staged file, as `scene_assets.stage` does.
    """
    from operators.gen_3d_scene.funcs import scene_assets

    return [
        scene_assets.stage(Path(project_dir), asset_id,
                           cache_dir=Path(cache_dir) if cache_dir else None)
        for asset_id in texture_ids(scene)
    ]


if __name__ == "__main__":  # pragma: no cover - operator convenience
    import argparse

    from operators.gen_3d_scene.funcs.terrain_code_template import TEMPLATES

    parser = argparse.ArgumentParser(description="Write greybox scenes as GLB.")
    parser.add_argument("--templates", nargs="*", default=sorted(TEMPLATES))
    parser.add_argument(
        "--out",
        default="test_data/outputs/greybox_scenes",
        help="directory to write into, relative to the repository root",
    )
    options = parser.parse_args()

    directory = Path(options.out)
    directory.mkdir(parents=True, exist_ok=True)
    failures = 0
    for name in options.templates:
        scene = TEMPLATES[name]()
        problems = check_scene(scene)
        path = write_scene(scene, directory / f"{name}.glb")
        summary = scene_summary(scene)
        print(
            f"{name:<12} {summary['props']:>3} props  "
            f"{Path(path).stat().st_size / 1024:>7.0f} KB  "
            f"{len(problems)} problem(s)"
        )
        for line in problems:
            print(f"    {line}")
        failures += len(problems)
    raise SystemExit(1 if failures else 0)
