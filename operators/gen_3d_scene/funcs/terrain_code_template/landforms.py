"""
operators/gen_3d_scene/funcs/terrain_code_template/landforms.py

The ground, one function per landform.

Each returns a `Ground`: the terrain itself plus the few measurements the
foreground needs to place anything sensibly on it. Those measurements have to
come from here because they are properties of the shape — where a basin
actually bottoms out, what level a street network was graded to, where two
roads cross. Re-deriving them in the foreground would mean two answers to the
same question.

Nothing here places a prop. A landform returns ground and facts about ground;
`foreground.py` decides what stands on it.

Three things are done to nearly every landform before it is handed on, and
they are what separate one from a noise field:

    warped noise    every relief shape reads `warped_noise`, so slopes run
                    and hollows are not circles
    terracing       worked ground is folded into treads, which is both what
                    a farmed or built-on hillside looks like and what gives
                    a greybox slope a set of lines to be read against — and
                    what anything standing on it has to stand on
    baking          the height is sampled onto the grid the surface is
                    written from, so what the foreground measures and what
                    `check_scene` validates is the ground the GLB carries
                    rather than a formula the exported mesh only samples

| function      | ground                          | what it reports         |
|---------------|---------------------------------|-------------------------|
| `plains`      | rippled, two roads crossing it  | roads, junction, reach  |
| `hills`       | ridged and terraced             | relief, summit, terrace |
| `basin`       | dished and terraced, low point  | water, radii, levels,   |
|               | off centre                      | terrace step            |
| `canyon`      | meandering channel              | floor, depth, rim       |
| `walled_town` | terraced motte                  | plateau, rise, gates    |
| `city`        | graded streets, carved river    | street network, river   |

Usage:
    from operators.gen_3d_scene.funcs.terrain_code_template import landforms

    ground = landforms.basin(size=96.0, depth=14.0)
    ground.terrain      # a `Terrain` for `write_scene`
    ground.marks        # {"water": (-3.5, 18.5), "surface": -8.4, ...}
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .. import terrain_code_edit as te


@dataclass
class Ground:
    """A terrain and the measurements taken from it.

    `marks` holds single values — a level, a radius, a spot. `ways` holds
    networks of runs, which is what a road or a river is: a list of segments
    the foreground can measure distance to. `lines` keeps those runs as
    polylines, for paving along.
    """

    terrain: te.Terrain
    size: float
    marks: dict[str, Any] = field(default_factory=dict)
    ways: dict[str, list[tuple[te.Spot, te.Spot]]] = field(default_factory=dict)
    lines: dict[str, list[te.Spot]] = field(default_factory=dict)


# ── open and rolling ─────────────────────────────────────────────────────────

#: Width of a country road, and the level the pair of them are graded to.
TRACK_WIDTH = 6.5
TRACK_LEVEL = 0.0


def plains(size: float = 116.0, ripple: float = 1.7, seed: int = 1) -> Ground:
    """Open, near-level ground with two roads crossing it.

    A ripple rather than a plane: a couple of metres of movement is enough
    for the ground to read as a field while leaving anything still able to
    stand on it.

    The roads are cut here rather than left to the foreground because the
    ground has to be graded to them, and because on open ground the junction
    is the one position with a reason behind it. Everything a plain holds is
    placed with respect to the crossing, so the crossing is a measurement and
    not a decision — and it has to be measured, since both runs wander and
    neither ends up where it was aimed.

    The grid is fine enough to carry a road: graded across a coarse one, a
    six-metre run falls between two grid lines and comes out as a dent.
    """
    lines = {
        "trackx": te.winding_spots(7, span=size * 0.99, wander=size * 0.055,
                                   along="x", seed=seed + 2,
                                   centre=(0.0, -size * 0.07)),
        "trackz": te.winding_spots(7, span=size * 0.99, wander=size * 0.045,
                                   along="z", seed=seed + 5,
                                   centre=(size * 0.05, 0.0)),
    }
    ways = {"roads": [way for line in lines.values()
                      for way in te.ways_along(line)]}

    terrain = te.flat(size, ripple=ripple, seed=seed, tiles=104)
    terrain = te.graded(terrain, ways["roads"], TRACK_WIDTH, blend=9.0,
                        level=TRACK_LEVEL)
    terrain = te.baked(terrain)

    return Ground(terrain, size, {
        "reach": size * 0.86,
        "track_width": TRACK_WIDTH,
        "track_level": TRACK_LEVEL,
        "junction": te.crossing(lines["trackx"], lines["trackz"]) or (0.0, 0.0),
    }, ways, lines)


def hills(size: float = 100.0, relief: float = 9.5, seed: int = 2) -> Ground:
    """Rolling ground with a ridge line, terraced where it is worked.

    `crest` is what puts a summit on the high ground. Fractal noise on its
    own gives dunes, whose tops are as round as their hollows and so have
    nowhere to stand; folded towards ridges, the high ground has an edge —
    which is what a beacon is set on and what a track along the tops follows.

    The wavelength is under a third of the site, so several separate hills fit
    across it. At the site's own scale the whole thing would be one dome.
    """
    terrace = relief * 0.20
    terrain = te.hills(size, amplitude=relief, wavelength=size * 0.30,
                       crest=0.45, tiles=112, seed=seed)
    # Lightly: the treads should read as worked ground on the flanks, not
    # turn the landform into a ziggurat.
    terrain = te.terraced(terrain, step=terrace, share=0.5)
    terrain = te.baked(terrain)

    # The summit and the hollow are reported as levels, not as a fraction of
    # `relief`, because they are not the same number. Layered noise reaches
    # nowhere near its nominal extremes — four octaves of it span about two
    # thirds of the amplitude, and mixing two kinds narrows that again — so a
    # threshold set at "four tenths of the relief" lands above the ninetieth
    # percentile of the ground and selects nothing at all. What the
    # foreground wants is a share of the fall that is actually there.
    summit = te.highest_spot(terrain, samples=96)
    hollow = te.lowest_spot(terrain, samples=96)
    return Ground(terrain, size, {
        "relief": relief,
        "reach": size * 0.82,
        "terrace": terrace,
        "summit": summit,
        "summit_level": te.ground_height(terrain, *summit),
        "hollow": hollow,
        "hollow_level": te.ground_height(terrain, *hollow),
    })


# ── cut and raised ───────────────────────────────────────────────────────────

def basin(size: float = 88.0, depth: float = 22.0, seed: int = 3) -> Ground:
    """Ground dishing to an off-centre low point, terraced, waterline measured.

    The low point is found by sampling rather than assumed to be the centre,
    because the noise moves it. The two radii are the same contour measured
    two ways: `pool_radius` runs past the waterline on every side so a water
    disc has its rim buried in the bank, and `shore_radius` is where the
    water is actually visible.

    Terracing comes before either is measured, and before the low point is
    found. A smooth dish has no level ground anywhere between the water and
    the rim, which is what makes a settlement on one a slope of tilted boxes;
    the treads are what the holdings stand on, and the risers are what the
    stairs between them climb.
    """
    terrace = depth * 0.18
    terrain = te.bowl(size, depth, centre=(-size * 0.09, size * 0.07),
                      tiles=112, seed=seed)
    terrain = te.terraced(terrain, step=terrace, share=0.74, tread=0.64)
    terrain = te.baked(terrain)

    water = te.lowest_spot(terrain, samples=104)
    floor = te.ground_height(terrain, *water)
    surface = floor + depth * 0.17

    return Ground(terrain, size, {
        "depth": depth,
        "terrace": terrace,
        "water": water,
        "floor": floor,
        "surface": surface,
        "pool_radius": te.contour_radius(terrain, water, surface, fit="cover"),
        "shore_radius": te.contour_radius(terrain, water, surface),
    })


def canyon(
    size: float = 88.0,
    depth: float = 17.0,
    floor_width: float = 22.0,
    wall_run: float = 0.5,
    meander: float = 0.12,
    seed: int = 4,
) -> Ground:
    """A meandering channel between walls that rise away from it.

    The three numbers are set against the site rather than picked freely,
    because a channel that is a small share of a wide site reads as a mesa —
    the flat plateau beyond the walls fills the frame and the channel is a
    slot in it. So the floor takes about a quarter of the width, `wall_run`
    spends half the remaining distance climbing, and only a narrow rim is
    left level. `meander` is kept under that rim so the channel wanders
    without one wall running off the edge.

    The rim level is reported because a crossing has to start there: what
    makes a gorge read as a gorge is something spanning it, and the height
    that matters for that is the one the walls reach, not the one the floor
    sits at.
    """
    terrain = te.canyon(size, depth, floor_width, meander=meander,
                        rim_share=wall_run, tiles=104, seed=seed)
    terrain = te.baked(terrain)

    return Ground(terrain, size, {
        "depth": depth,
        "floor_width": floor_width,
        "floor": 0.0,
        "rim": depth,
        "reach": size * 0.88,
    })


#: How the rampart is cut up, and where its openings fall. Named here because
#: both sides build to them: the ground reports the gates, and the foreground
#: hangs a gatehouse and a street off every one.
WALL_SEGMENTS = 26
WALL_GATES = 4
GATE_START = 40.0


def walled_town(
    size: float = 106.0,
    rise: float = 9.5,
    wall_radius: float = 27.0,
    seed: int = 5,
) -> Ground:
    """A terraced motte with a level top, out to just past the wall line.

    The plateau ends outside where the wall will stand, leaving the rest of
    the site for the flanks — a mound whose top reaches the edge has no
    visible slope. The flanks are terraced, which reads as the bank of a
    motte and, more usefully, gives the approach something to climb in
    stages instead of one unbroken ramp.

    The approach is reported as a line rather than cut into the ground.
    Grading it would level the bank into a shelf, and the point of a motte is
    that getting up it takes effort; the foreground builds a flight of steps
    along this line instead.
    """
    plateau = wall_radius + 5.0
    terrain = te.mound(size, rise, flat_radius=plateau, tiles=112, seed=seed)
    terrain = te.terraced(terrain, step=rise * 0.26, share=0.44)
    terrain = te.baked(terrain)

    gates = te.gate_spots(wall_radius, WALL_GATES, WALL_SEGMENTS,
                          start_degrees=GATE_START)
    head = gates[0][0]
    bearing = max(math.hypot(*head), 1e-6)
    foot = (head[0] / bearing * size * 0.37, head[1] / bearing * size * 0.37)

    return Ground(terrain, size, {
        "rise": rise,
        "wall_radius": wall_radius,
        "plateau": plateau,
        "segments": WALL_SEGMENTS,
        "gates": gates,
        "gate_start": GATE_START,
        "terrace": rise * 0.26,
        "reach": size * 0.47,
    }, {}, {"approach": te.spoke_lines(head, [foot], points=6, bend=0.14,
                                       seed=seed)[0]})


# ── built ────────────────────────────────────────────────────────────────────

#: Street widths, in metres. Named here because the ground is graded to the
#: wider of them and the foreground paves to both.
LANE = 8.0
AVENUE = 13.0

#: Level the street network is graded to.
STREET_LEVEL = 0.0

#: The river channel and the ground either side of it.
RIVER_WIDTH = 17.0
RIVER_DEPTH = 5.0
RIVER_BANKS = 13.0


def city(size: float = 260.0, seed: int = 6) -> Ground:
    """Ground for a district: streets graded level, a river cut through them.

    The street network is generated here rather than in the foreground because
    the ground has to be graded to it. Grading the whole network to one level
    is what stops paved runs stepping against each other into potholes — a
    slab resting on its own patch of unlevelled ground can sit further above
    its neighbour than the slab is thick.

    The river is carved *after* the grading. In the other order the streets
    would dam the channel at every crossing.
    """
    lines: dict[str, list[te.Spot]] = {}
    widths: dict[str, float] = {}
    for axis in ("x", "z"):
        # Irregular spacing, so no two blocks are the same depth, and every
        # run bends, so none of them is a ruled line.
        offsets = te.irregular_lines(size * 0.84, least=44.0, most=68.0,
                                     seed=seed + (0 if axis == "x" else 9))
        key = 0 if axis == "x" else 500
        for index, offset in enumerate(offsets):
            name = f"road{axis}{index:02d}"
            lines[name] = te.winding_spots(
                7, span=size * 0.98, wander=size * 0.055, along=axis,
                seed=seed + 50 + index * 7 + key,
                centre=(0.0, offset) if axis == "x" else (offset, 0.0),
            )
            widths[name] = AVENUE if index == len(offsets) // 2 else LANE

    lines["river"] = te.winding_spots(9, span=size * 0.98, wander=size * 0.09,
                                      along="z", seed=seed + 3,
                                      centre=(size * 0.22, 0.0))
    ways = {
        "streets": [way for name, line in lines.items() if name != "river"
                    for way in te.ways_along(line)],
        "river": te.ways_along(lines["river"]),
    }

    terrain = te.flat(size, ripple=1.2, seed=seed)
    # Graded half again wider than the widest street. A slab rests on the
    # lowest ground under its whole width, so an avenue graded to exactly its
    # own width has its outer edge in the blend, picks up the fall there, and
    # steps against the slab in front of it — which the level middle of the
    # run would have hidden.
    terrain = te.graded(terrain, ways["streets"], AVENUE * 1.55, blend=10.0,
                        level=STREET_LEVEL)
    terrain = te.carved(terrain, ways["river"], RIVER_WIDTH, RIVER_DEPTH,
                        banks=RIVER_BANKS, tiles=112)
    # A district stacks three shaping passes and every height sample runs all
    # of them; across several hundred props compared against each other that
    # is the whole chain evaluated hundreds of thousands of times.
    terrain = te.baked(terrain)

    return Ground(terrain, size, {
        "street_level": STREET_LEVEL,
        "street_widths": widths,
        "lane": LANE,
        "avenue": AVENUE,
        "river_width": RIVER_WIDTH,
        "river_depth": RIVER_DEPTH,
        "river_banks": RIVER_BANKS,
        "reach": size * 0.47,
    }, ways, lines)


LANDFORMS = {
    "plains": plains,
    "hills": hills,
    "basin": basin,
    "canyon": canyon,
    "walled_town": walled_town,
    "city": city,
}

__all__ = [
    "Ground",
    "LANDFORMS",
    "basin",
    "canyon",
    "city",
    "hills",
    "plains",
    "walled_town",
]
