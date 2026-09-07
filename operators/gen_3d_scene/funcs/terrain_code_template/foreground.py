"""
operators/gen_3d_scene/funcs/terrain_code_template/foreground.py

What stands on the ground, one function per landform.

Each takes a `Ground` from `landforms.py` and returns ``(terrain, props)``.
The terrain comes back because some foregrounds cut the ground they stand on —
a pad under a hut, a level track — and the props have to be measured against
the cut ground, not the original. Ones that cut nothing return what they got.

The split is by question rather than by scene: `landforms` answers "what is
the ground", this answers "what is on it". A foreground reads the measurements
the ground reported — a basin's waterline, a city's street network — and never
re-derives them, so there is one answer to each.

Distribution follows the landform, because the two are not independent: a
settlement in a basin gathers on the terraces, the same settlement on a ridge
follows the high ground, and in a city it fills the blocks the streets leave.

Anything reusable lives in `terrain_code_edit` rather than here, so what is
left in each function is the part that is actually specific to the landform:

    te.scatter        loose scenery — vary the sizes, part-bury, turn each one
    te.fit_all        anything sized and positioned together, kept if it fits
    te.ring_wall      a closed wall of tangent segments
    te.road_network   paving for a set of runs, carried where it spans a cut
    te.interchange    two crossing decks and the ramps between them
    te.water_along    water panels down a channel at one surface level
    te.columns        supports grown from their own footing to a deck

| function      | distribution                                    |
|---------------|-------------------------------------------------|
| `plains`      | sparse scatter, no structure                    |
| `hills`       | split by height: ridge, hollow, flank           |
| `basin`       | hamlets on the terraces, shore, track to water  |
| `canyon`      | chain along the floor, debris on the walls      |
| `walled_town` | rampart on the rim, radial spokes inside        |
| `city`        | paving, stacked interchange, crowded quarters   |

Usage:
    from operators.gen_3d_scene.funcs.terrain_code_template import (
        foreground, landforms,
    )

    ground = landforms.basin()
    terrain, props = foreground.basin(ground)
"""
from __future__ import annotations

import math
import random

from .. import terrain_code_edit as te
from .landforms import Ground

Built = tuple[te.Terrain, list[te.Prop]]


# ── plains ───────────────────────────────────────────────────────────────────

def plains(
    ground: Ground, boulders: int = 14, thickets: int = 9, seed: int = 1
) -> Built:
    """Rock and thicket at low density — the reference the others depart from."""
    terrain, size = ground.terrain, ground.size

    rocks = te.scatter(
        "boulder", "sphere",
        te.scatter_spots(boulders, size * 0.85, seed=seed, min_gap=7.0),
        (2.8, 2.2, 2.8), spread=0.45, buried=0.28, seed=seed,
    )
    trees = _trees(
        te.clear_of(
            te.scatter_spots(thickets, size * 0.8, seed=seed + 7, min_gap=10.0),
            rocks, margin=3.5,
        ),
        seed=seed + 13,
    )
    return terrain, rocks + trees


def _trees(spots: list[te.Spot], seed: int) -> list[te.Prop]:
    """A trunk and a canopy per spot, sharing a group so they may touch."""
    sizes = te.varied_sizes(len(spots), (0.55, 3.6, 0.55), spread=0.3, seed=seed)
    props: list[te.Prop] = []
    for index, spot in enumerate(spots):
        height = sizes[index][1]
        group = f"tree-{index:02d}"
        props.append(te.Prop(f"trunk-{index:02d}", "cylinder", spot,
                             sizes[index], material="prop", group=group))
        props.append(te.Prop(f"canopy-{index:02d}", "sphere", spot,
                             (height * 1.35, height * 1.15, height * 1.3),
                             material="block", sink=-height * 0.92,
                             group=group))
    return props


# ── hills ────────────────────────────────────────────────────────────────────

def hills(
    ground: Ground,
    towers: int = 5,
    huts: int = 9,
    scrub: int = 22,
    seed: int = 2,
) -> Built:
    """Towers on the ridges, dwellings in the hollows, scrub on the flanks."""
    terrain = ground.terrain
    relief, reach = ground.marks["relief"], ground.marks["reach"]

    ridge = te.on_high_ground(
        te.scatter_spots(80, reach, seed=seed, min_gap=15.0),
        terrain, above=relief * 0.45,
    )[:towers]
    hollow = te.on_low_ground(
        te.scatter_spots(80, reach, seed=seed + 11, min_gap=11.0),
        terrain, below=-relief * 0.35,
    )[:huts]

    # Buildable ground under each structure, cut before anything is measured
    # against the terrain — a hut on a hillside otherwise stands on its lowest
    # corner with the rest of its base clear of the slope.
    terrain = te.levelled_at(terrain, ridge + hollow, radius=4.0, blend=5.0)

    watch = te.place(
        "tower", "cylinder", ridge,
        size=te.varied_sizes(len(ridge), (2.8, 8.0, 2.8), spread=0.2, seed=seed),
        material="wall",
    )
    dwellings = te.place(
        "hut", "box", hollow,
        size=te.varied_sizes(len(hollow), (4.8, 3.2, 4.2), spread=0.25,
                             seed=seed),
        material="prop",
        yaw=[index * 43.0 for index in range(len(hollow))],
    )
    bushes = te.scatter(
        "scrub", "sphere",
        te.clear_of(te.scatter_spots(scrub, reach, seed=seed + 23, min_gap=6.0),
                    watch + dwellings, margin=3.0),
        (2.4, 1.7, 2.4), spread=0.4, buried=0.2, seed=seed + 5,
    )
    return terrain, watch + dwellings + bushes


# ── basin ────────────────────────────────────────────────────────────────────

#: Footprint of an ordinary dwelling, and of a smaller one.
HOUSE = (5.0, 3.4, 4.2)
COTTAGE = (3.6, 2.8, 3.2)


def basin(ground: Ground, hamlets: int = 5, seed: int = 3) -> Built:
    """Hamlets on the terraces, a shore of reeds and stones, a track to water."""
    terrain, size = ground.terrain, ground.size
    depth = ground.marks["depth"]
    water = ground.marks["water"]
    surface = ground.marks["surface"]
    shore_radius = ground.marks["shore_radius"]
    rng = random.Random(seed)

    def buildable(spots, slack: float, headroom: float):
        """On the terraces: above the shore, below the rim, clear of the water."""
        return te.clear_circle(
            te.in_height_band(spots, terrain,
                              lowest=surface + depth * slack,
                              highest=-depth * headroom),
            centre=water, radius=shore_radius + 4.0,
        )

    # Hamlet seeds spread apart, so each reads as its own settlement. The band
    # runs from above the shore to the rim, so they sit at different heights
    # and so at different distances out.
    seeds = buildable(
        te.scatter_spots(80, size * 0.80, seed=seed, min_gap=15.0), 0.05, 0.04
    )[:hamlets]

    plots = [
        (spot, origin)
        for number, origin in enumerate(seeds)
        for spot in buildable(
            te.clustered_spots(1, 6, size * 0.88, spread=9.5,
                               seed=seed + number * 31, min_gap=6.4,
                               centres=[origin]),
            0.02, 0.02,
        )
    ]

    # A track from the hamlet nearest the water down to the shore, stopping at
    # the waterline rather than running into the pool.
    track_spots: list[te.Spot] = []
    track_yaw, track_tile = 0.0, 2.2
    if seeds:
        head = min(seeds, key=lambda spot: math.dist(spot, water))
        run = math.dist(head, water)
        share = min(max((shore_radius + 1.0) / max(run, 1e-6), 0.05), 0.9)
        toe = (water[0] + (head[0] - water[0]) * share,
               water[1] + (head[1] - water[1]) * share)
        track_spots, track_yaw, track_tile = te.strip_spots(head, toe, tile=2.2)

    # Every cut to the ground happens before a single prop is measured.
    terrain = te.levelled_at(
        terrain, [spot for spot, _origin in plots], radius=4.2, blend=4.5
    )

    # The disc's rim is buried in the bank, so the visible edge is the
    # waterline rather than a cylinder wall.
    pool = [
        te.Prop("pool", "cylinder", water,
                (ground.marks["pool_radius"] * 2.0,
                 surface - te.ground_height(terrain, *water) + 0.6,
                 ground.marks["pool_radius"] * 2.0),
                material="water", sink=0.6, group="shore"),
    ]
    # Separate slabs, each on its own ground, so the track steps down the
    # slope instead of being one tilted plank.
    track = te.place(
        "path", "box", track_spots, size=(track_tile, 0.18, 2.6),
        yaw=track_yaw, material="block", group="shore", sink=0.07,
    )

    holdings: list[te.Prop] = []
    for index, (spot, origin) in enumerate(plots):
        # Turned to follow the slope it stands on, so a hamlet's rooflines
        # follow the hillside rather than all pointing at the water.
        nx, _ny, nz = te.ground_normal(terrain, *origin)
        downhill = math.degrees(math.atan2(nx, nz))

        big = rng.random() < 0.35
        holdings.append(te.Prop(
            f"house-{index:02d}", "box", spot,
            tuple(v * rng.uniform(0.85, 1.25) for v in
                  (HOUSE if big else COTTAGE)),
            yaw=downhill + rng.uniform(-25.0, 25.0),
            material="wall" if big else "prop",
        ))
        # A shed beside some of them, which is what makes a plot a holding
        # rather than a single box.
        if rng.random() < 0.45:
            angle = rng.uniform(0.0, 2.0 * math.pi)
            holdings.append(te.Prop(
                f"shed-{index:02d}",
                "cylinder" if rng.random() < 0.3 else "box",
                (spot[0] + 4.6 * math.cos(angle),
                 spot[1] + 4.6 * math.sin(angle)),
                (2.2 * rng.uniform(0.8, 1.3), 2.0 * rng.uniform(0.8, 1.4),
                 2.0 * rng.uniform(0.8, 1.3)),
                yaw=rng.uniform(0.0, 90.0), material="prop",
            ))
    buildings = te.fit_all(terrain, holdings, pool + track, margin=0.9)

    # A shore in the band around the waterline, so the water meets the ground
    # unevenly instead of ending at a hard circle. Grouped with the pool
    # because they stand in the shallows on purpose.
    shore = te.clear_circle(
        te.in_height_band(
            te.scatter_spots(140, shore_radius * 4.0, seed=seed + 21,
                             min_gap=2.6, centre=water),
            terrain, lowest=surface - depth * 0.03,
            highest=surface + depth * 0.13,
        ),
        centre=water, radius=shore_radius * 0.85,
    )
    standing = buildings + track

    reeds = te.fit_all(terrain, te.scatter(
        "reed", "cone", te.clear_of(shore[::2], standing, margin=1.5),
        (1.3, 2.1, 1.3), material="prop", spread=0.45, seed=seed + 6,
    ), margin=0.4)
    for reed in reeds:
        reed.group, reed.sink = "shore", 0.3

    stones = te.fit_all(terrain, te.scatter(
        "stone", "sphere",
        te.clear_of(shore[1::2], standing + reeds, margin=1.2),
        (2.1, 1.6, 2.0), spread=0.5, buried=0.35, seed=seed + 7,
    ), reeds, margin=0.4)
    for stone in stones:
        stone.group = "shore"

    return terrain, pool + track + buildings + reeds + stones


# ── canyon ───────────────────────────────────────────────────────────────────

def canyon(
    ground: Ground, waypoints: int = 9, debris: int = 22, seed: int = 4
) -> Built:
    """A chain of waypoints down the floor, debris caught on the walls."""
    terrain, size = ground.terrain, ground.size
    floor_width = ground.marks["floor_width"]

    # Down the floor's own middle, offset across it, so the trail is a trail
    # and not a centreline with things bolted to it.
    trail = te.channel_spots(terrain, waypoints, along="z", wander=0.55,
                             seed=seed)
    markers = te.place(
        "marker", "cylinder", trail,
        size=te.varied_sizes(len(trail), (0.7, 3.0, 0.7), spread=0.3,
                             seed=seed + 1),
        material="marker",
    )

    # Leaning posts along the walls' foot, on one side at a time, at uneven
    # spacing — a shored-up path, not a row of gates.
    shoring = []
    for index, (_x, z) in enumerate(trail):
        if index % 3 == 1:
            continue
        side = -1.0 if index % 2 else 1.0
        reach = te.channel_edge(terrain, z, side, along="z")
        if reach is None:
            continue
        shoring.append(
            te.Prop(f"post-{index:02d}", "box",
                    (reach - side * 1.4, z + (index % 4 - 1.5) * 1.2),
                    (1.0, 3.6 + (index % 3) * 0.8, 1.0),
                    yaw=index * 23.0, material="wall")
        )

    rocks = te.scatter(
        "rock", "sphere",
        te.on_slope(
            te.scatter_spots(debris * 4, size * 0.88, seed=seed, min_gap=4.5),
            terrain, steeper_than=0.25,
        )[:debris],
        (2.2, 1.7, 2.2), spread=0.5, buried=0.3, seed=seed + 9,
    )

    # Standing water off to one side of the trail and part way along, rather
    # than at the canyon's midpoint.
    pool_radius = floor_width * 0.24
    wet = te.clear_of(
        te.channel_spots(terrain, 8, along="z", span=0.72, wander=1.0,
                         seed=seed + 55),
        markers + shoring, margin=pool_radius + 1.0,
    )
    pool = [
        te.Prop("pool", "cylinder", max(wet, key=lambda spot: abs(spot[1])),
                (pool_radius * 2.0, 0.5, pool_radius * 2.0),
                material="water", sink=0.4),
    ] if wet else []

    return terrain, markers + shoring + rocks + pool


# ── walled town ──────────────────────────────────────────────────────────────

#: Footprint of one house. Width runs along the spoke, depth across it.
TOWN_HOUSE = (4.2, 3.6)

#: Side of the square keep at the centre.
KEEP = 6.5


def walled_town(
    ground: Ground,
    segments: int = 22,
    spokes: int = 7,
    rings: int = 3,
    seed: int = 5,
) -> Built:
    """A rampart on the plateau's rim, with radial spokes of houses inside.

    `rings` is the most rings of houses to lay along each spoke; fewer are
    placed when the wall is too close to hold them all.
    """
    wall_radius = ground.marks["wall_radius"]
    rng = random.Random(seed)
    thickness = 1.1

    rampart = te.ring_wall("wall", wall_radius, segments, height=4.2,
                           thickness=thickness)
    # Towers stand in the wall rather than behind it, so they share its group.
    rampart += te.place(
        "gate-tower", "cylinder",
        te.ring_spots(4, wall_radius, start_degrees=45.0),
        size=te.graded_sizes(4, (2.8, 6.5, 2.8), (2.8, 7.7, 2.8)),
        material="marker", group="rampart",
    )

    # Spoke spacing follows the house: its width runs along the spoke, so that
    # sets the gap between rings, and the innermost ring has to clear the keep.
    along = TOWN_HOUSE[0]
    first = KEEP * math.sqrt(2.0) / 2.0 + along / 2.0 + 1.0
    step = along * 1.30
    limit = wall_radius - thickness / 2.0 - along / 2.0 - 1.0

    houses = []
    for spoke in range(spokes):
        angle = 2.0 * math.pi * spoke / spokes
        # Each spoke starts a little further out than the last, so the rings
        # do not line up into concentric circles.
        stagger = (spoke % 3) * step * 0.30
        for ring in range(rings):
            radius = first + stagger + ring * step
            if radius > limit:
                break
            if rng.random() < 0.18:
                continue
            swing = rng.uniform(-0.055, 0.055)
            houses.append(te.Prop(
                f"house-{spoke}-{ring}", "box",
                (radius * math.cos(angle + swing),
                 radius * math.sin(angle + swing)),
                (TOWN_HOUSE[0], 3.2 + rng.uniform(0.0, 1.8), TOWN_HOUSE[1]),
                yaw=-math.degrees(angle) + rng.uniform(-7.0, 7.0),
                material="prop",
            ))

    keep = [te.Prop("keep", "box", (0.0, 0.0), (KEEP, 9.0, KEEP),
                    yaw=12.0, material="block")]
    return ground.terrain, rampart + houses + keep


# ── city ─────────────────────────────────────────────────────────────────────

#: Paving tile length. Short enough to follow a bend without gapping.
PAVER = 5.0

#: The two raised levels of the interchange, in metres above the streets.
#: Kept close together on purpose: a ramp is a flight of level slabs, so the
#: shorter the climb between decks the smaller the step between slabs and the
#: more the ramp reads as a road rather than as a stair.
LOWER_DECK = 6.0
UPPER_DECK = 10.5


def city(
    ground: Ground,
    quarters: int = 10,
    core_height: float = 42.0,
    edge_height: float = 10.0,
    seed: int = 6,
) -> Built:
    """Paving, a stacked interchange, and crowded quarters in the blocks."""
    terrain, size, marks = ground.terrain, ground.size, ground.marks
    streets, river = ground.ways["streets"], ground.ways["river"]
    level, lane, avenue = marks["street_level"], marks["lane"], marks["avenue"]
    river_width, river_banks = marks["river_width"], marks["river_banks"]
    river_depth = marks["river_depth"]
    rng = random.Random(seed)

    def in_the_blocks(spots, off_street: float, off_river: float):
        """The ground the streets and the river leave for building on."""
        return te.clear_of_ways(
            te.clear_of_ways(spots, streets, off_street), river, off_river
        )

    paving, bridges = te.road_network(
        terrain,
        {name: line for name, line in ground.lines.items() if name != "river"},
        marks["street_widths"], tile=PAVER, level=level,
        over=river,
        # The carve pulls the ground down across the banks as well as the
        # channel, so the whole width is carried; the slab's own reach past
        # its centre is allowed for on top.
        span=river_width / 2.0 + river_banks + PAVER,
        structure=river_width / 2.0 + 2.0,
    )
    decks, routes, ramps = te.interchange(
        terrain, hub=(0.0, -size * 0.14), size=size, lane=lane,
        ground_level=level, levels=(LOWER_DECK, UPPER_DECK),
        tile=PAVER * 1.4, seed=seed,
    )
    water = te.water_along(
        terrain, "water", ground.lines["river"], tile=PAVER * 2.0,
        width=river_width * 0.94, depth=river_depth * 0.8,
        level=level - river_depth * 0.45,
    )
    supports = _piers(terrain, routes, ramps, streets, river, river_width,
                      avenue)
    infrastructure = paving + bridges + decks + supports + water

    # ── quarters: tight groups in the blocks the streets leave ──────────────
    towers: list[te.Prop] = []
    for number, centre in enumerate(in_the_blocks(
        te.scatter_spots(120, size * 0.74, seed=seed + 5, min_gap=34.0),
        avenue, river_width / 2.0 + river_banks * 0.6,
    )[:quarters]):
        away = min(math.hypot(*centre) / marks["reach"], 1.0)
        tier = core_height + (edge_height - core_height) * away * away

        # Centres 11.5 m apart with 9–11 m footprints, so neighbours stand
        # one to two metres from one another — a quarter, not separate plots
        # on a lattice. The spread is tight for the same reason: a wide one
        # spaces the group out until it reads as a scatter again.
        towers += [
            te.Prop(
                f"tower-{number:02d}-{slot}", "box", spot,
                (rng.uniform(9.0, 11.0),
                 max(5.0, tier * rng.uniform(0.55, 1.45)),
                 rng.uniform(9.0, 11.0)),
                yaw=rng.uniform(-6.0, 6.0), material="wall",
            )
            for slot, spot in enumerate(in_the_blocks(
                te.clustered_spots(1, 9, size * 0.84, spread=12.0,
                                   seed=seed + number * 17, min_gap=11.5,
                                   centres=[centre]),
                lane * 0.85, river_width * 0.75,
            ))
        ]
    buildings = te.fit_all(terrain, towers, infrastructure, margin=0.6)

    # ── frontage and yard clutter in whatever room is left ──────────────────
    clutter = []
    for index, spot in enumerate(in_the_blocks(
        te.scatter_spots(200, size * 0.78, seed=seed + 31, min_gap=8.0),
        avenue * 0.8, river_width * 0.75,
    )):
        low = rng.random() < 0.6
        clutter.append(te.Prop(
            f"{'shop' if low else 'yard'}-{index:03d}",
            "box" if low or rng.random() < 0.6 else "cylinder", spot,
            (rng.uniform(5.0, 9.0),
             rng.uniform(3.5, 9.0) if low else rng.uniform(1.6, 3.6),
             rng.uniform(5.0, 8.0)),
            yaw=rng.uniform(0.0, 90.0), material="prop",
        ))
    filler = te.fit_all(terrain, clutter, buildings + infrastructure,
                        margin=1.0)

    lamps = te.place(
        "lamp", "cylinder",
        te.clear_of_ways([prop.at for prop in paving[::11]], river, river_width),
        size=(0.35, 4.6, 0.35), material="marker", group="streets",
    )

    return terrain, (paving + bridges + water + decks + supports
                     + buildings + filler + lamps)


def _piers(terrain, routes, ramps, streets, river, river_width, avenue,
           side: float = 2.4) -> list[te.Prop]:
    """Supports under whichever deck slabs have room for one.

    Clear of the river the decks span, clear of the streets they pass over —
    a pier in the roadway is a column through the carriageway — and clear of
    the ramps, which occupy the ground around the hub. The ramps are measured
    against rather than kept a fixed distance from, since they are where
    they are.
    """
    candidates: list[te.Prop] = []
    for name, tiles, top in (("pier", routes[0], LOWER_DECK),
                             ("column", routes[1], UPPER_DECK)):
        room = [
            tile for tile in tiles
            if te.way_distance(tile[0], river) > river_width * 0.7
            and te.way_distance(tile[0], streets) > avenue / 2.0 + side
        ]
        candidates += te.columns(terrain, name, room, top, side=side)
    return te.fit_all(terrain, candidates, ramps, margin=1.2)


FOREGROUNDS = {
    "plains": plains,
    "hills": hills,
    "basin": basin,
    "canyon": canyon,
    "walled_town": walled_town,
    "city": city,
}

__all__ = [
    "FOREGROUNDS",
    "basin",
    "canyon",
    "city",
    "hills",
    "plains",
    "walled_town",
]
