"""
operators/gen_3d_scene/funcs/terrain_code_template/landforms.py

The ground, one function per landform.

Each returns a `Ground`: the terrain itself plus the few measurements the
foreground needs to place anything sensibly on it. Those measurements have to
come from here because they are properties of the shape — where a basin
actually bottoms out, what level a street network was graded to. Re-deriving
them in the foreground would mean two answers to the same question.

Nothing here places a prop. A landform returns ground and facts about ground;
`foreground.py` decides what stands on it.

| function      | ground                       | what it reports          |
|---------------|------------------------------|--------------------------|
| `plains`      | level with a ripple          | reach                    |
| `hills`       | rolling noise                | relief, reach            |
| `basin`       | dished, off-centre low point | water spot, radii, level |
| `canyon`      | meandering channel           | floor level, depth       |
| `walled_town` | plateau with rough flanks    | plateau radius, rise     |
| `city`        | graded streets, carved river | street network, river    |

Usage:
    from operators.gen_3d_scene.funcs.terrain_code_template import landforms

    ground = landforms.basin(size=96.0, depth=10.0)
    ground.terrain      # a `Terrain` for `write_scene`
    ground.marks        # {"water": (-3.5, 18.5), "surface": -8.4, ...}
"""
from __future__ import annotations

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

def plains(size: float = 80.0, ripple: float = 1.4, seed: int = 1) -> Ground:
    """Open, near-level ground.

    A ripple rather than a plane: under a metre of movement is enough for the
    ground to read as a field while leaving anything still able to stand on
    it.
    """
    return Ground(te.flat(size, ripple=ripple, seed=seed), size,
                  {"reach": size * 0.85})


def hills(size: float = 90.0, relief: float = 5.5, seed: int = 2) -> Ground:
    """Rolling ground from layered noise.

    The wavelength is a sixth of the site, so several separate hills fit
    across it; at the site's own scale the whole thing is one dome.
    """
    return Ground(
        te.hills(size, amplitude=relief, wavelength=size * 0.17, seed=seed),
        size, {"relief": relief, "reach": size * 0.80},
    )


# ── cut and raised ───────────────────────────────────────────────────────────

def basin(size: float = 96.0, depth: float = 10.0, seed: int = 3) -> Ground:
    """Ground dishing to an off-centre low point, with a waterline measured.

    The low point is found by sampling rather than assumed to be the centre,
    because the noise moves it. The two radii are the same contour measured
    two ways: `pool_radius` runs past the waterline on every side so a water
    disc has its rim buried in the bank, and `shore_radius` is where the
    water is actually visible.
    """
    terrain = te.bowl(size, depth, centre=(-size * 0.09, size * 0.07), seed=seed)
    water = te.lowest_spot(terrain, samples=96)
    floor = te.ground_height(terrain, *water)
    surface = floor + depth * 0.14

    return Ground(terrain, size, {
        "depth": depth,
        "water": water,
        "floor": floor,
        "surface": surface,
        "pool_radius": te.contour_radius(terrain, water, surface, fit="cover"),
        "shore_radius": te.contour_radius(terrain, water, surface),
    })


def canyon(
    size: float = 78.0,
    depth: float = 14.0,
    floor_width: float = 20.0,
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
    """
    return Ground(
        te.canyon(size, depth, floor_width, meander=meander,
                  rim_share=wall_run, seed=seed),
        size, {
            "depth": depth,
            "floor_width": floor_width,
            "floor": 0.0,
            "reach": size * 0.88,
        },
    )


def walled_town(
    size: float = 96.0,
    rise: float = 7.0,
    wall_radius: float = 22.0,
    seed: int = 5,
) -> Ground:
    """A plateau with rough flanks, level out to just past the wall line.

    The plateau ends outside where the wall will stand, leaving the rest of
    the site for the flanks — a mound whose top reaches the edge has no
    visible slope.
    """
    plateau = wall_radius + 4.0
    return Ground(
        te.mound(size, rise, flat_radius=plateau, seed=seed), size,
        {"rise": rise, "wall_radius": wall_radius, "plateau": plateau},
    )


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
    terrain = te.graded(terrain, ways["streets"], AVENUE, blend=10.0,
                        level=STREET_LEVEL)
    terrain = te.carved(terrain, ways["river"], RIVER_WIDTH, RIVER_DEPTH,
                        banks=RIVER_BANKS, tiles=112)

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
