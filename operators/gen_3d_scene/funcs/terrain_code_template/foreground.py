"""
operators/gen_3d_scene/funcs/terrain_code_template/foreground.py

What stands on the ground, one function per landform.

Each takes a `Ground` from `landforms.py` and returns ``(terrain, props)``.
The terrain comes back because some foregrounds cut the ground they stand on —
a pad under a hut, a level track — and the props have to be measured against
the cut ground, not the original. Ones that cut nothing return what they got.

The split is by question rather than by scene: `landforms` answers "what is
the ground", this answers "what is on it". A foreground reads the measurements
the ground reported — a basin's waterline, a city's street network, where two
roads cross a plain — and never re-derives them, so there is one answer to
each.

Four things separate a scene that reads as a place from one that reads as a
scatter of blocks, and every function here owes all four:

    circulation   a way through: a road, a trail, a stair, a bridge. Without
                  one, whatever is placed is furniture rather than a level
    a landmark    one thing taller and more distinct than the rest, so the
                  eye has somewhere to go and the site has a scale reference
    hierarchy     three sizes at least. A site of one-storey boxes has no
                  foreground and no background, whatever the layout does
    enclosure     something that divides the ground — walls, hedges, blocks,
                  terraces — so the open parts read as chosen rather than
                  as leftover

Distribution follows the landform, because the two are not independent: a
settlement in a basin gathers on the terraces, the same settlement on a ridge
follows the high ground, and in a city it fills the blocks the streets leave.

Anything reusable lives in `terrain_code_edit` rather than here, so what is
left in each function is the part that is actually specific to the landform:

    te.scatter        loose scenery — vary the sizes, part-bury, turn each one
    te.fit_all        anything sized and positioned together, kept if it fits
    te.ring_wall      a wall of tangent segments, with openings in it
    te.arch           two piers and a lintel: a gateway rather than a hole
    te.stairway       a flight that closes, along a line that may bend
    te.stepped_tower  a landmark with a profile instead of an extrusion
    te.ruin           a wall line with gaps: the cheapest history there is
    te.bridge         a deck carried level, on supports where it needs them
    te.road_network   paving for a set of runs, carried where it spans a cut
    te.interchange    two crossing decks and the ramps between them
    te.water_along    water panels down a channel at one surface level
    te.columns        supports grown from their own footing to a deck

| function      | circulation      | landmark      | what fills the rest    |
|---------------|------------------|---------------|------------------------|
| `plains`      | two roads        | watchtower    | field walls, copses    |
| `hills`       | ridge way        | beacons       | farmsteads, scrub      |
| `basin`       | track and stairs | lake hall     | hamlets, shore, jetty  |
| `canyon`      | floor trail,     | rim bridge    | camp, talus, pool      |
|               | switchback stair |               |                        |
| `walled_town` | gates, streets   | keep          | blocks, market, camp   |
| `city`        | streets, bridges | interchange,  | quarters, frontage,    |
|               | interchange      | tower core    | park                   |

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
from dataclasses import replace

from .. import terrain_code_edit as te
from .landforms import Ground, TRACK_WIDTH

Built = tuple[te.Terrain, list[te.Prop]]

#: Paving tile length. Short enough to follow a bend without gapping.
PAVER = 5.0


def _architecture(terrain: te.Terrain, props: list[te.Prop], urban: bool = False) -> list[te.Prop]:
    """Give occupied volumes a roofline while retaining the fitted envelopes."""
    built = []
    for prop in props:
        if prop.id.startswith(("house-", "shed-", "shop-", "tower-", "hall-",
                               "keep-", "watchtower-", "beacon")) and prop.kind == "box":
            flat = urban or prop.id.startswith(("keep-", "watchtower-", "beacon"))
            built.extend(te.building(terrain, prop, roof="flat" if flat else "gable"))
        elif prop.id.startswith("tent-"):
            built.append(replace(prop, kind="extrude",
                                 profile=((-0.5, -0.5), (0.5, -0.5), (0.0, 0.5))))
        elif prop.id.startswith("wall-") and prop.group == "rampart":
            built.extend(te.battlement(terrain, prop))
        elif prop.kind == "sphere":
            built.append(replace(prop, segments=8))
        else:
            built.append(prop)
    return built


def _trees(
    terrain: te.Terrain,
    spots: list[te.Spot],
    placed: list[te.Prop],
    seed: int,
    tall: float = 3.8,
) -> list[te.Prop]:
    """A trunk and a canopy per spot, kept or dropped as one.

    Tested as a pair rather than passed through `fit_all` singly, because the
    canopy is the wider of the two: fitted separately, a tight spot keeps the
    trunk and drops the crown, and what is left is a post. They share a group
    so they may touch each other.
    """
    sizes = te.varied_sizes(len(spots), (0.55, tall, 0.55), spread=0.3, seed=seed)
    props: list[te.Prop] = []
    for index, spot in enumerate(spots):
        height = sizes[index][1]
        group = f"tree-{index:02d}"
        pair = [
            te.Prop(f"trunk-{index:02d}", "cylinder", spot, sizes[index],
                    material="prop", group=group),
            te.Prop(f"canopy-{index:02d}", "sphere", spot,
                    (height * 1.25, height * 1.05, height * 1.2),
                    material="foliage", sink=-height * 0.86, group=group),
        ]
        if all(te.fits(terrain, one, placed + props, margin=0.5) for one in pair):
            props += pair
    return props


# ── plains ───────────────────────────────────────────────────────────────────

#: The way-station's watchtower. Nothing else on a plain reaches a fifth of
#: it, which is the point: open ground with no vertical in it has nothing to
#: judge its own extent against, and reads as a texture rather than a place.
WATCHTOWER = 16.0

#: A field wall. Low enough to see over, high enough to divide the ground.
FIELD_WALL = 1.2


def plains(
    ground: Ground,
    copses: int = 6,
    boulders: int = 16,
    seed: int = 1,
) -> Built:
    """A crossroads with a way-station on it, walled fields, copses between.

    Still the most open of the six — what changed is that there is somewhere
    to go. Everything is placed with respect to the junction the ground
    measured: the station on it, the fields around it, the copses in whatever
    the field walls leave.
    """
    terrain, size = ground.terrain, ground.size
    # Read rather than required. The pairing with `landforms.plains` is a
    # default, not a coupling: put this foreground on ground that reports no
    # roads and it builds no roads, sets the station at the origin, and the
    # fields and copses come out as they would anywhere.
    roads = ground.ways.get("roads", ())
    junction = ground.marks.get("junction", (0.0, 0.0))
    width = ground.marks.get("track_width", TRACK_WIDTH)
    rng = random.Random(seed)

    paving = [
        slab
        for name, line in ground.lines.items()
        for slab in te.paved(name, te.path_tiles(line, PAVER), width,
                             group="roads")
    ]

    # ── the way-station, in the quarters the crossing leaves ───────────────
    # Spaced round the ring rather than taken in order. The road filter
    # leaves the survivors bunched into the quadrants between the two runs,
    # so the first four in sequence are four positions in the same corner —
    # and on ground with no roads to filter by, they are four adjacent ones.
    plots: list[te.Spot] = []
    for spot in te.clear_of_ways(
        te.ring_spots(12, width * 2.6, centre=junction, start_degrees=18.0),
        roads, width * 1.25,
    ):
        if all(math.dist(spot, taken) > 15.0 for taken in plots):
            plots.append(spot)

    # Cut before anything is measured against the terrain: a station standing
    # on a ripple rests on its lowest corner with the rest clear of the ground.
    terrain = te.baked(te.levelled_at(terrain, plots[:4], radius=7.0, blend=6.0))

    candidates: list[te.Prop] = []
    if plots:
        candidates += te.stepped_tower(
            terrain, "watchtower", plots[0], (7.2, 7.2), WATCHTOWER,
            tiers=3, yaw=rng.uniform(0.0, 90.0), material="wall",
        )
    if len(plots) > 1:
        candidates += te.ruin("station", plots[1], 12.5, 8.5, 4.2,
                              yaw=rng.uniform(0.0, 180.0), standing=0.6,
                              seed=seed)
    for index, plot in enumerate(plots[2:4]):
        candidates.append(te.Prop(
            f"shed-{index:02d}", "box", plot,
            (5.6 * rng.uniform(0.85, 1.2), 3.6 * rng.uniform(0.9, 1.3),
             4.4 * rng.uniform(0.85, 1.2)),
            yaw=rng.uniform(0.0, 90.0), material="prop",
        ))
    # In priority order, so that on a cramped site the tower survives and the
    # outbuildings are what give way.
    built = te.fit_all(terrain, candidates, margin=0.8)

    # ── field walls: what turns open ground into fields ────────────────────
    hedges = te.fit_all(terrain, _field_walls(ground, seed), built + paving,
                        margin=1.0)
    standing = built + hedges

    # ── copses in the fields, and clearance heaps in the corners ───────────
    centres = te.clear_of_ways(
        te.scatter_spots(40, size * 0.78, seed=seed + 7, min_gap=21.0),
        roads, width * 2.4,
    )[:copses]
    wood = _trees(
        terrain,
        te.clustered_spots(1, 8, size * 0.84, spread=8.5, seed=seed + 11,
                           min_gap=5.4, centres=centres),
        standing + paving, seed=seed + 13,
    )
    rocks = te.fit_all(terrain, te.scatter(
        "boulder", "sphere",
        te.clear_of_ways(
            te.scatter_spots(boulders * 3, size * 0.86, seed=seed + 3,
                             min_gap=7.5),
            roads, width * 1.2,
        )[:boulders],
        (3.0, 2.3, 3.0), spread=0.45, buried=0.3, seed=seed,
    ), standing + wood + paving, margin=0.6)

    paving = [replace(prop, material="route") for prop in paving]
    return terrain, _architecture(terrain, paving + built + hedges + wood + rocks)


def _field_walls(ground: Ground, seed: int) -> list[te.Prop]:
    """Runs of low wall on irregular boundaries, broken where a road meets one.

    Fields are what open ground is divided into, and the division is what
    gives a plain a scale: without it the site is one surface of unknowable
    extent. The gaps are where the boundary meets a road, because that is
    where a field is entered — and a wall run straight across a carriageway
    reads as a fault rather than as enclosure.
    """
    size, roads = ground.size, ground.ways.get("roads", ())
    gap = ground.marks.get("track_width", TRACK_WIDTH) * 1.5
    walls: list[te.Prop] = []
    for axis in ("x", "z"):
        offsets = te.irregular_lines(size * 0.80, least=23.0, most=35.0,
                                     seed=seed + (0 if axis == "x" else 4))
        for number, offset in enumerate(offsets[1:-1]):
            line = te.winding_spots(
                6, span=size * 0.86, wander=size * 0.022, along=axis,
                seed=seed + 30 + number * 5,
                centre=(0.0, offset) if axis == "x" else (offset, 0.0),
            )
            for spot, yaw, length in te.path_tiles(line, tile=4.6):
                if te.way_distance(spot, roads) < gap:
                    continue
                walls.append(te.Prop(
                    f"hedge-{len(walls):03d}", "box", spot,
                    (length * 1.04, FIELD_WALL, 0.7), yaw=yaw,
                    material="prop", group="hedges",
                ))
    return walls


# ── hills ────────────────────────────────────────────────────────────────────

#: A beacon on the ridge line. Tall enough to be seen from the hollows, which
#: is what makes the high ground somewhere the low ground is oriented by.
BEACON = 13.0


def hills(
    ground: Ground,
    beacons: int = 3,
    farms: int = 7,
    scrub: int = 34,
    seed: int = 2,
) -> Built:
    """Beacons on the ridge, farmsteads in the hollows, a way between them."""
    terrain = ground.terrain
    reach = ground.marks["reach"]
    summit = ground.marks["summit"]
    # Bands taken off the fall the ground actually has rather than off the
    # relief it was asked for. The two are not the same number, and a
    # threshold set against the second selects nothing.
    floor = ground.marks["hollow_level"]
    fall = max(ground.marks["summit_level"] - floor, 1e-6)
    rng = random.Random(seed)

    high = te.on_high_ground(
        te.scatter_spots(140, reach, seed=seed, min_gap=13.0),
        terrain, above=floor + fall * 0.62,
    )
    # Held well inside the site: a farmstead is a house, a shed and a yard
    # wall around both, so its own reach is a good ten metres past the spot
    # it is placed on and the edge of the terrain arrives sooner than the
    # spacing suggests.
    hollow = [
        spot for spot in te.on_low_ground(
            te.scatter_spots(140, reach, seed=seed + 11, min_gap=12.0),
            terrain, below=floor + fall * 0.34,
        )
        if max(abs(spot[0]), abs(spot[1])) < ground.size / 2.0 - 13.0
    ]
    # The summit first, then high ground far enough from everything already
    # taken to be a separate hill. Measured against the whole set rather than
    # against the summit alone: two beacons a few metres apart stack into one
    # silhouette, and the pair reads as a single tower with a fault in it.
    tops = [summit]
    for spot in high:
        if all(math.dist(spot, taken) > reach * 0.26 for taken in tops):
            tops.append(spot)
        if len(tops) >= beacons:
            break
    steadings = hollow[:farms]

    # Buildable ground under everything, cut before a single prop is measured
    # against the terrain — a farmstead on a hillside otherwise stands on its
    # lowest corner with the rest of its base clear of the slope.
    terrain = te.baked(te.levelled_at(terrain, tops + steadings, radius=5.4, blend=6.5))

    watch: list[te.Prop] = []
    for index, spot in enumerate(tops):
        watch += te.stepped_tower(
            terrain, f"beacon{index}", spot, (5.4, 5.4),
            BEACON * rng.uniform(0.82, 1.15), tiers=3,
            yaw=rng.uniform(0.0, 90.0), material="wall",
        )
    if tops:
        watch.append(te.Prop("brazier", "cone", tops[0], (2.4, 2.6, 2.4),
                             material="marker",
                             sink=-BEACON * 0.98, group="beacon0"))

    steading_props: list[te.Prop] = []
    for index, spot in enumerate(steadings):
        steading_props += _farmstead(index, spot, rng, seed + index * 13)
    farmsteads = te.fit_all(terrain, steading_props, watch, margin=0.8)

    # ── the way: cairns from the summit round the farms and back to the ridge
    route = te.chained(tops[1:] + steadings, start=summit)
    waymarks = te.fit_all(terrain, te.place(
        "cairn", "cone", te.densified([summit] + route, step=7.0),
        size=(1.6, 2.2, 1.6), material="marker", group="way",
    ), watch + farmsteads, margin=0.8)

    standing = watch + farmsteads + waymarks
    bushes = te.fit_all(terrain, te.scatter(
        "scrub", "sphere",
        te.on_slope(
            te.scatter_spots(scrub * 4, reach, seed=seed + 23, min_gap=5.5),
            terrain, steeper_than=0.18,
        )[:scrub],
        (2.6, 1.8, 2.6), material="foliage", spread=0.4, buried=0.22,
        seed=seed + 5,
    ), standing, margin=0.5)

    return terrain, _architecture(terrain, standing + bushes)


def _farmstead(
    index: int, at: te.Spot, rng: random.Random, seed: int
) -> list[te.Prop]:
    """A house, a shed beside it, and a yard wall around the pair.

    One box on a hillside is a box. What makes it a holding is that something
    is enclosed: the yard is a `ruin` at full height with a piece missing,
    which is a wall with a gateway in it and costs nothing more than a wall.

    All three share a group, because a holding is one thing: its own shed
    standing against its own yard wall is how a farm is built, not a clash to
    be reported and dropped.
    """
    yaw = rng.uniform(0.0, 360.0)
    angle = math.radians(yaw)
    aside = (math.cos(angle) * 4.9, -math.sin(angle) * 4.9)
    steading = f"steading-{index:02d}"
    return [
        te.Prop(f"house-{index:02d}", "box", at,
                (6.4 * rng.uniform(0.85, 1.15), 4.2 * rng.uniform(0.9, 1.35),
                 4.9 * rng.uniform(0.85, 1.15)),
                yaw=yaw, material="wall", group=steading),
        te.Prop(f"shed-{index:02d}", "box",
                (at[0] + aside[0], at[1] + aside[1]),
                (3.2 * rng.uniform(0.8, 1.2), 2.5 * rng.uniform(0.8, 1.2),
                 2.8 * rng.uniform(0.8, 1.2)),
                yaw=yaw + rng.uniform(-30.0, 30.0), material="prop",
                group=steading),
    ] + te.ruin(f"yard-{index:02d}",
                (at[0] + aside[0] * 0.5, at[1] + aside[1] * 0.5),
                13.0, 10.5, 1.4, yaw=yaw, thickness=0.55, standing=0.8,
                seed=seed, material="prop", group=steading)


# ── basin ────────────────────────────────────────────────────────────────────

#: Footprint of an ordinary dwelling, and of a smaller one.
HOUSE = (5.4, 3.6, 4.4)
COTTAGE = (3.8, 2.9, 3.4)

#: The hall by the water. Four times a cottage and the only thing in the
#: basin with tiers, so it reads as the settlement's reason for being here.
HALL = 15.0


def basin(ground: Ground, hamlets: int = 6, seed: int = 3) -> Built:
    """Hamlets on the terraces, a hall and a jetty at the water, stairs down."""
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
        te.scatter_spots(80, size * 0.80, seed=seed, min_gap=16.0), 0.05, 0.04
    )[:hamlets]

    plots = [
        (spot, origin)
        for number, origin in enumerate(seeds)
        for spot in buildable(
            te.clustered_spots(1, 8, size * 0.88, spread=8.5,
                               seed=seed + number * 31, min_gap=6.2,
                               centres=[origin]),
            0.02, 0.02,
        )
    ]

    # The hamlet nearest the water is the one the hall and the landing belong
    # to, and the way down runs between them.
    head = min(seeds, key=lambda spot: math.dist(spot, water)) if seeds else water
    run = max(math.dist(head, water), 1e-6)
    outward = ((head[0] - water[0]) / run, (head[1] - water[1]) / run)
    share = min(max((shore_radius + 1.0) / run, 0.05), 0.9)
    toe = (water[0] + outward[0] * run * share,
           water[1] + outward[1] * run * share)
    # The hall stands back from the head of the path rather than on it.
    inland = (head[0] + outward[0] * 8.0, head[1] + outward[1] * 8.0)

    # Every cut to the ground happens before a single prop is measured.
    terrain = te.baked(te.levelled_at(
        terrain, [spot for spot, _origin in plots] + [inland],
        radius=4.4, blend=4.5,
    ))

    # The disc's rim is buried in the bank, so the visible edge is the
    # waterline rather than a cylinder wall.
    # Pads can change the shoreline. Re-measure the final terrain and allow
    # for the inscribed polygon used by the cylinder writer.
    pool_radius = te.contour_radius(terrain, water, surface, fit="cover", rays=96, steps=120)
    pool_radius /= math.cos(math.pi / 16)
    pool = [
        te.Prop("pool", "cylinder", water,
                (pool_radius * 2.0,
                 surface - te.ground_height(terrain, *water) + 0.6,
                 pool_radius * 2.0),
                material="water", sink=0.6, group="shore"),
    ]
    # A flight rather than a run of slabs. The bank is terraced, so a path
    # resting on it drops a whole riser between one slab and the next, and
    # crossing the risers is the entire job.
    descent = abs(te.ground_height(terrain, *head) - (surface + 0.15))
    tread = min(0.75, math.dist(head, toe) / (math.ceil(descent / 0.22) + 2))
    path = te.stairway(
        terrain, "path", [head, toe],
        bottom=te.ground_height(terrain, *head), top=surface + 0.15,
        width=3.0, tread=max(tread, 0.1), group="shore",
    )
    hall = te.stepped_tower(
        terrain, "hall", inland, (11.5, 8.5), HALL, tiers=3,
        yaw=te.facing(inland, water), material="wall", taper=0.7,
    )
    # A landing out over the water, which is what the flight is going to.
    # Grouped with the shore because its supports stand in the pool on
    # purpose, and a pier in water is not a collision.
    jetty = te.bridge(
        terrain, "jetty", toe,
        (water[0] + outward[0] * run * share * 0.3,
         water[1] + outward[1] * run * share * 0.3),
        level=surface + 0.55, width=3.2, tile=2.8, thickness=0.3,
        clearance=1.0, side=0.6, material="block", group="shore",
    )
    waterside = pool + path + hall + jetty

    holdings: list[te.Prop] = []
    for index, (spot, origin) in enumerate(plots):
        # Turned to follow the slope it stands on, so a hamlet's rooflines
        # follow the hillside rather than all pointing at the water.
        nx, _ny, nz = te.ground_normal(terrain, *origin)
        downhill = math.degrees(math.atan2(nx, nz))

        big = rng.random() < 0.35
        holdings.append(te.Prop(
            f"house-{index:02d}", "box", spot,
            tuple(v * rng.uniform(0.85, 1.3) for v in
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
                (spot[0] + 4.8 * math.cos(angle),
                 spot[1] + 4.8 * math.sin(angle)),
                (2.3 * rng.uniform(0.8, 1.3), 2.1 * rng.uniform(0.8, 1.5),
                 2.1 * rng.uniform(0.8, 1.3)),
                yaw=rng.uniform(0.0, 90.0), material="prop",
            ))
    buildings = te.fit_all(terrain, holdings, waterside, margin=0.9)

    # A shore in the band around the waterline, so the water meets the ground
    # unevenly instead of ending at a hard circle. Grouped with the pool
    # because they stand in the shallows on purpose.
    shore = te.clear_circle(
        te.in_height_band(
            te.scatter_spots(420, shore_radius * 4.5, seed=seed + 21,
                             min_gap=2.2, centre=water),
            # A hand's depth of water at most, and a stride above it at most:
            # reeds standing out in open water read as misplaced, and reeds
            # up on the bank read as scrub.
            terrain, lowest=surface - depth * 0.018,
            highest=surface + depth * 0.08,
        ),
        centre=water, radius=shore_radius * 0.85,
    )
    standing = waterside + buildings
    # What the shore has to keep off is the buildings, not the water. The
    # pool's disc reaches past the waterline by design — that is what buries
    # its rim — so measuring reeds against it would clear the whole shallows,
    # which is the one place they belong. Joining the group before they are
    # fitted rather than after is the same statement made to `fit_all`.
    dry = [prop for prop in standing if prop.group != "shore"]

    reeds = te.scatter(
        "reed", "cone", te.clear_of(shore[::2], dry, margin=1.5),
        (1.4, 2.2, 1.4), material="foliage", spread=0.45, seed=seed + 6,
    )
    for reed in reeds:
        reed.group, reed.sink = "shore", 0.3
    reeds = te.fit_all(terrain, reeds, standing, margin=0.4)

    stones = te.scatter(
        "stone", "sphere", te.clear_of(shore[1::2], dry, margin=1.2),
        (2.2, 1.7, 2.1), spread=0.5, buried=0.35, seed=seed + 7,
    )
    for stone in stones:
        stone.group = "shore"
    stones = te.fit_all(terrain, stones, standing + reeds, margin=0.4)

    return terrain, _architecture(terrain, standing + reeds + stones)


# ── canyon ───────────────────────────────────────────────────────────────────

def canyon(
    ground: Ground, waypoints: int = 8, debris: int = 24, seed: int = 4
) -> Built:
    """A trail and a camp on the floor, a stair to the rim, a bridge over it."""
    terrain, size = ground.terrain, ground.size
    floor_width = ground.marks["floor_width"]
    rim = ground.marks["rim"]
    rng = random.Random(seed)

    # Down the floor's own middle, offset across it, so the trail is a trail
    # and not a centreline with things bolted to it.
    trail = te.channel_spots(terrain, waypoints, along="z", wander=0.55,
                             seed=seed)
    markers = te.place(
        "marker", "cylinder", trail,
        size=te.varied_sizes(len(trail), (0.7, 2.8, 0.7), spread=0.3,
                             seed=seed + 1),
        material="marker",
    )

    # ── the crossing: what makes a gorge read as a gorge ────────────────────
    span = _rim_crossing(terrain, ground, rim)
    crossing: list[te.Prop] = []
    if span:
        start, end, level = span
        crossing = te.bridge(terrain, "span", start, end, level=level,
                             width=4.4, tile=3.6, thickness=0.55,
                             clearance=3.0, side=1.4)

    # ── and the way up to it ───────────────────────────────────────────────
    climb = _switchback(terrain, ground, span, seed)

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

    # ── a camp on the floor, which is what the trail is going to ───────────
    camp_at = trail[len(trail) // 3] if trail else (0.0, 0.0)
    camp: list[te.Prop] = []
    for index, spot in enumerate(te.clustered_spots(
        1, 9, size * 0.8, spread=6.5, seed=seed + 41, min_gap=2.9,
        centres=[camp_at],
    )):
        tent = rng.random() < 0.5
        camp.append(te.Prop(
            f"{'tent' if tent else 'crate'}-{index:02d}",
            "cone" if tent else "box", spot,
            (3.0 * rng.uniform(0.8, 1.2), 2.6 * rng.uniform(0.8, 1.2),
             3.0 * rng.uniform(0.8, 1.2)) if tent else
            (1.5 * rng.uniform(0.7, 1.4), 1.3 * rng.uniform(0.7, 1.5),
             1.4 * rng.uniform(0.7, 1.4)),
            yaw=rng.uniform(0.0, 90.0),
            material="prop" if tent else "block",
        ))

    structure = crossing + climb + markers + shoring
    pitched = te.fit_all(terrain, camp, structure, margin=0.7)

    # Talus at the wall feet, where fallen rock actually gathers, rather than
    # spread evenly over the walls it fell off.
    rocks = te.fit_all(terrain, te.scatter(
        "rock", "sphere",
        te.on_slope(
            te.scatter_spots(debris * 5, size * 0.88, seed=seed, min_gap=4.2),
            terrain, steeper_than=0.22,
        )[:debris],
        (2.4, 1.8, 2.4), spread=0.5, buried=0.3, seed=seed + 9,
    ), structure + pitched, margin=0.5)

    # Standing water off to one side of the trail and part way along. Kept
    # away from the canyon's own midpoint on purpose: water in the middle of
    # a channel reads as the thing the channel was drawn around, and it is
    # meant to be something the trail passes.
    pool_radius = floor_width * 0.24
    # Sampled the length of the floor and taken furthest out, rather than
    # filtered to a band: a hard cut-off can leave nothing at all once the
    # crossing and the climb have claimed the middle of the channel.
    wet = te.clear_of(
        te.channel_spots(terrain, 15, along="z", span=0.82, wander=1.0,
                         seed=seed + 55),
        structure + pitched, margin=pool_radius + 1.0,
    )
    pool = [
        te.Prop("pool", "cylinder", max(wet, key=lambda spot: abs(spot[1])),
                (pool_radius * 2.0, 0.5, pool_radius * 2.0),
                material="water", sink=0.4),
    ] if wet else []

    return terrain, _architecture(terrain, structure + pitched + rocks + pool)


def _rim_reach(
    terrain: te.Terrain, z: float, side: float, level: float
) -> float | None:
    """How far out the wall has climbed to `level`, at one point along z.

    Measured rather than computed from the profile, because the channel
    meanders: a crossing laid at a fixed offset lands on the wall on one side
    and out on the plain on the other.
    """
    edge = te.channel_edge(terrain, z, side, along="z")
    if edge is None:
        return None
    limit = terrain.size * 0.46
    step = terrain.size / 200.0
    reach = edge
    while abs(reach) < limit:
        reach += side * step
        if te.ground_height(terrain, reach, z) >= level:
            return reach
    return None


def _rim_crossing(
    terrain: te.Terrain, ground: Ground, rim: float
) -> tuple[te.Spot, te.Spot, float] | None:
    """Where a bridge can land on rock at both ends, and at what height.

    Both abutments have to be on ground that has finished climbing, or the
    deck ends part way up a wall with nothing under it. Of the places where
    that holds, the one taken is where the two rims are closest in height: a
    deck is level, so whatever they differ by is how far the lower abutment
    stands off its own ground.
    """
    wanted = rim * 0.92
    found = []
    for share in (0.18, -0.22, 0.34, -0.06, 0.26, -0.34, 0.10, -0.14):
        z = ground.size * share
        near = _rim_reach(terrain, z, -1.0, wanted)
        far = _rim_reach(terrain, z, 1.0, wanted)
        if near is None or far is None:
            continue
        start, end = (near - 2.4, z), (far + 2.4, z)
        heights = (te.ground_height(terrain, *start),
                   te.ground_height(terrain, *end))
        found.append((abs(heights[0] - heights[1]), start, end,
                      sum(heights) / 2.0 + 0.7))
    if not found:
        return None
    _difference, start, end, level = min(found, key=lambda place: place[0])
    return start, end, level


def _switchback(
    terrain: te.Terrain,
    ground: Ground,
    span: tuple[te.Spot, te.Spot, float] | None,
    seed: int,
) -> list[te.Prop]:
    """Two flights zig-zagging from the floor up to the bridge's near end.

    A gorge with a bridge over it and no way onto the bridge is two
    structures in one frame. Two flights rather than one because a single
    straight run at this height would be a ramp longer than the canyon is
    wide, and because a switchback is what a path up a wall actually does.
    """
    if span is None:
        return []
    start, _end, level = span
    z = start[1]
    foot = te.channel_edge(terrain, z, -1.0, along="z")
    if foot is None:
        return []

    # Both flights share the bridge's group: the top of the climb runs onto
    # the deck it is there to reach, and a stair that meets its own bridge is
    # one structure rather than two things clashing.
    turn = ((foot + start[0]) / 2.0, z - 11.0)
    lower = te.stairway(
        terrain, "climb0", [(foot + 1.6, z + 9.0), turn],
        bottom=ground.marks["floor"] + 0.2,
        top=te.ground_height(terrain, *turn), width=3.0, tread=1.9,
        group="bridge",
    )
    upper = te.stairway(
        terrain, "climb1", [turn, (start[0] + 1.0, z)],
        bottom=te.ground_height(terrain, *turn), top=level - 0.5,
        width=3.0, tread=1.9, group="bridge",
    )
    return lower + upper


# ── walled town ──────────────────────────────────────────────────────────────

#: The rampart, and the keep that stands over it. The keep is deliberately
#: half again the wall's height: a citadel no taller than its own curtain is
#: not a landmark, and the wall is the only other vertical on the site.
RAMPART = 4.8
KEEP = 17.0

#: The market square, which is the one piece of ground inside the wall that
#: is deliberately left empty. Without it a walled town is solid from the
#: gate to the keep, and nothing in it reads as public.
SQUARE = 11.0


def walled_town(
    ground: Ground,
    houses: int = 26,
    seed: int = 5,
) -> Built:
    """A gated rampart, streets from every gate to a market square, a keep."""
    terrain = ground.terrain
    wall_radius = ground.marks["wall_radius"]
    segments = ground.marks["segments"]
    gates = ground.marks["gates"]
    rng = random.Random(seed)

    rampart = te.ring_wall("wall", wall_radius, segments, height=RAMPART,
                           thickness=1.3, gates=len(gates),
                           gate_start=ground.marks.get("gate_start", 40.0))
    # A gatehouse in every opening, taller than the curtain either side of
    # it, so the way in is announced rather than merely available.
    for index, (spot, yaw) in enumerate(gates):
        rampart += te.arch(
            terrain, f"gate{index}", spot,
            span=2.0 * math.pi * wall_radius / segments * 0.82,
            height=RAMPART * 1.28, yaw=yaw, pier=2.0, thickness=1.8,
        )

    # ── streets from each gate in to the square ────────────────────────────
    # Sampled fine enough to pave. `path_tiles` lays at least one whole tile
    # per leg of the polyline, so a run described by a handful of points comes
    # out as a handful of slabs however short the tile asked for — which is a
    # pair of stepping stones, not a street.
    streets = te.spoke_lines((0.0, 0.0), [spot for spot, _yaw in gates],
                             points=13, bend=0.13, seed=seed)
    # Paved between the square and the gate, and no further either way: the
    # market is unpaved ground on purpose, and a street run into the gateway
    # would meet the piers standing in it.
    paving = [
        slab
        for index, line in enumerate(streets)
        for slab in te.paved(f"street{index}", [
            tile for tile in te.path_tiles(line, 3.4)
            if SQUARE * 0.62 < math.hypot(*tile[0]) < wall_radius - 2.8
        ], 5.6, group="streets")
    ]
    ways = [way for line in streets for way in te.ways_along(line)]

    # ── the keep, on the square's edge rather than its middle ──────────────
    # In a quarter between two gates, never on the line of one: a citadel
    # across a street is a road block, and the streets are where they are
    # because the gates are. The bisector is the right bearing but not, on
    # its own, a clear position — the streets bow, so which quarter and how
    # far out is measured rather than assumed.
    bearings = sorted(math.degrees(math.atan2(spot[1], spot[0])) % 360.0
                      for spot, _yaw in gates)
    quarters = [
        math.radians(before + (after - before) / 2.0)
        for before, after in zip(bearings, bearings[1:] + [bearings[0] + 360.0])
    ] or [math.radians(45.0)]

    keep: list[te.Prop] = []
    for bearing in quarters:
        for radius in (SQUARE * 1.35, SQUARE * 1.62, SQUARE * 1.9):
            standing = te.stepped_tower(
                terrain, "keep",
                (radius * math.cos(bearing), radius * math.sin(bearing)),
                (10.0, 9.0), KEEP, tiers=4,
                yaw=-math.degrees(bearing) + 14.0, material="block",
                taper=0.79,
            )
            if all(te.fits(terrain, tier, rampart + paving, margin=0.8)
                   for tier in standing):
                keep = standing
                break
        if keep:
            break

    well = [te.Prop("well", "cylinder", (0.0, 0.0), (2.6, 1.1, 2.6),
                    material="block")]

    # ── houses in the blocks the streets leave ─────────────────────────────
    plots = te.clear_circle(
        te.clear_of_ways(
            te.scatter_spots(320, (wall_radius - 2.0) * 2.0, seed=seed + 3,
                             min_gap=5.8),
            ways, 4.0,
        ),
        radius=SQUARE * 0.94,
    )
    plots = [spot for spot in plots
             if math.hypot(*spot) < wall_radius - 4.0][:houses * 3]

    dwellings = []
    for index, spot in enumerate(plots):
        storeys = 2 if rng.random() < 0.3 else 1
        dwellings.append(te.Prop(
            f"house-{index:02d}", "box", spot,
            (5.2 * rng.uniform(0.8, 1.25),
             3.2 * storeys * rng.uniform(0.9, 1.2),
             4.4 * rng.uniform(0.8, 1.25)),
            # Turned to face the square, then let off it, so the blocks have
            # a grain without every roofline agreeing.
            yaw=te.street_bearing(spot, ways) + rng.uniform(-8.0, 8.0),
            material="wall" if storeys > 1 else "prop",
        ))
    built = te.fit_all(terrain, dwellings, rampart + paving + keep + well,
                       margin=0.9)[:houses]

    # ── the approach, and what has gathered outside the wall ───────────────
    approach = ground.lines["approach"]
    steps = te.stairway(
        terrain, "approach", list(reversed(approach)),
        bottom=te.ground_height(terrain, *approach[-1]),
        top=te.ground_height(terrain, *approach[0]) + 0.1,
        width=4.4, tread=2.1,
    )
    outside = _outside_the_wall(terrain, ground, rampart + steps, seed)

    paving = [replace(prop, material="route") for prop in paving]
    return terrain, _architecture(terrain, rampart + paving + keep + well + built + steps + outside)


def _outside_the_wall(
    terrain: te.Terrain, ground: Ground, placed: list[te.Prop], seed: int
) -> list[te.Prop]:
    """A camp, a ruin and scrub on the flanks below the wall.

    Three quarters of the site is outside the rampart, and an empty three
    quarters makes the town read as a model on a tray. What goes there is
    what gathers outside a wall: the people who arrived after the gate shut.
    """
    size = ground.size
    wall_radius = ground.marks["wall_radius"]
    rng = random.Random(seed + 7)

    outer = [
        spot for spot in te.scatter_spots(120, size * 0.86, seed=seed + 17,
                                          min_gap=7.0)
        if math.hypot(*spot) > wall_radius + 8.0
    ]
    camp_at = min(outer, key=lambda spot: math.dist(
        spot, ground.lines["approach"][-1])) if outer else (0.0, 0.0)

    tents = [
        te.Prop(f"tent-{index:02d}", "cone", spot,
                (3.4 * rng.uniform(0.8, 1.2), 2.8 * rng.uniform(0.8, 1.2),
                 3.4 * rng.uniform(0.8, 1.2)),
                yaw=rng.uniform(0.0, 90.0), material="prop")
        for index, spot in enumerate(te.clustered_spots(
            1, 16, size * 0.86, spread=11.0, seed=seed + 23, min_gap=4.2,
            centres=[camp_at]))
    ]
    # Two ruins rather than one, at opposite ends of the site: a single one
    # reads as a feature placed there, and a pair reads as what is left.
    remains: list[te.Prop] = []
    for index, spot in enumerate(sorted(
        outer, key=lambda spot: -math.dist(spot, camp_at)
    )[:2]):
        remains += te.ruin(
            f"remains{index}", spot, 15.0, 9.5, 3.8,
            yaw=rng.uniform(0.0, 180.0), standing=0.5, seed=seed + 5 + index,
        )

    pens = te.fit_all(terrain, [
        prop
        for index, spot in enumerate(sorted(
            outer, key=lambda spot: math.dist(spot, camp_at))[6:12])
        for prop in te.ruin(f"pen{index}", spot, 11.0, 8.0, 1.3,
                            yaw=rng.uniform(0.0, 180.0), thickness=0.5,
                            standing=0.75, seed=seed + 31 + index,
                            material="prop")
    ], placed, margin=1.0)

    gathered = te.fit_all(terrain, tents + remains, placed + pens, margin=1.0)
    bushes = te.fit_all(terrain, te.scatter(
        "scrub", "sphere",
        te.clear_of([spot for spot in outer
                     if math.hypot(*spot) > wall_radius + 11.0][:44],
                    placed + gathered + pens, margin=2.5),
        (2.4, 1.7, 2.4), material="foliage", spread=0.4, buried=0.24,
        seed=seed + 9,
    ), placed + gathered + pens, margin=0.5)
    return pens + gathered + bushes


# ── city ─────────────────────────────────────────────────────────────────────

#: The two raised levels of the interchange, in metres above the streets.
#: Kept close together on purpose: a ramp is a flight of level slabs, so the
#: shorter the climb between decks the smaller the step between slabs and the
#: more the ramp reads as a road rather than as a stair.
LOWER_DECK = 6.0
UPPER_DECK = 10.5

#: Above this a tower is built with setbacks instead of as one extrusion.
#: Only the tall ones: a setback on a six-storey block is not visible, and
#: every tier is another prop for every other prop to be compared against.
SETBACK_ABOVE = 26.0


def city(
    ground: Ground,
    quarters: int = 10,
    core_height: float = 58.0,
    edge_height: float = 10.0,
    seed: int = 6,
) -> Built:
    """Paving, a stacked interchange, crowded quarters, and a park."""
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

    # ── the park: one block deliberately left unbuilt ──────────────────────
    # The deepest point in a block rather than the first one clear of a
    # street. A park is a disc, so what it needs is room on every side; set
    # on the first spot that merely misses the carriageway, half of it falls
    # in the road and half its trees are cut.
    park = max(
        in_the_blocks(
            te.scatter_spots(140, size * 0.62, seed=seed + 71, min_gap=22.0),
            avenue * 0.9, river_width,
        ),
        key=lambda spot: te.way_distance(spot, streets),
        default=None,
    )
    park_reach = size * 0.075

    def off_the_park(spots):
        if park is None:
            return list(spots)
        return te.clear_circle(spots, centre=park, radius=park_reach)

    # ── quarters: tight groups in the blocks the streets leave ─────────────
    towers: list[te.Prop] = []
    for number, centre in enumerate(off_the_park(in_the_blocks(
        te.scatter_spots(120, size * 0.74, seed=seed + 5, min_gap=34.0),
        avenue, river_width / 2.0 + river_banks * 0.6,
    ))[:quarters]):
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
                yaw=te.street_bearing(spot, streets) + rng.uniform(-3.0, 3.0), material="wall",
            )
            for slot, spot in enumerate(off_the_park(in_the_blocks(
                te.clustered_spots(1, 9, size * 0.84, spread=12.0,
                                   seed=seed + number * 17, min_gap=11.5,
                                   centres=[centre]),
                lane * 0.85, river_width * 0.75,
            )))
        ]
    buildings = _with_setbacks(terrain,
                               te.fit_all(terrain, towers, infrastructure,
                                          margin=0.6))

    # ── frontage and yard clutter in whatever room is left ─────────────────
    clutter = []
    for index, spot in enumerate(off_the_park(in_the_blocks(
        te.scatter_spots(200, size * 0.78, seed=seed + 31, min_gap=8.0),
        avenue * 0.8, river_width * 0.75,
    ))):
        low = rng.random() < 0.6
        clutter.append(te.Prop(
            f"{'shop' if low else 'yard'}-{index:03d}",
            "box" if low or rng.random() < 0.6 else "cylinder", spot,
            (rng.uniform(5.0, 9.0),
             rng.uniform(3.5, 9.0) if low else rng.uniform(1.6, 3.6),
             rng.uniform(5.0, 8.0)),
            yaw=te.street_bearing(spot, streets) + rng.uniform(-5.0, 5.0), material="prop",
        ))
    filler = te.fit_all(terrain, clutter, buildings + infrastructure,
                        margin=1.0)

    lamps = te.place(
        "lamp", "cylinder",
        te.clear_of_ways([prop.at for prop in paving[::11]], river, river_width),
        size=(0.35, 4.6, 0.35), material="marker", group="streets",
    )

    green: list[te.Prop] = []
    if park is not None:
        # Spaced wider than a crown, because park trees are the one scenery
        # in the scene that is bigger than the gap a scatter leaves: at seven
        # metres apart a six-metre canopy is rejected against its neighbour
        # and the park comes out as three trees.
        green = _trees(
            terrain,
            in_the_blocks(te.clustered_spots(
                1, 30, size * 0.9, spread=park_reach * 0.95, seed=seed + 77,
                min_gap=9.0, centres=[park]), lane, river_width),
            infrastructure + buildings + filler + lamps, seed=seed + 79,
            tall=6.0,
        )

    paving = [replace(prop, material="route") for prop in paving]
    return terrain, _architecture(terrain, paving + bridges + water + decks + supports
                                 + buildings + filler + lamps + green, urban=True)


def _with_setbacks(
    terrain: te.Terrain, towers: list[te.Prop]
) -> list[te.Prop]:
    """Rebuild the tall towers as stacks that step in as they rise.

    Applied after fitting rather than before, and only downwards: every tier
    stands inside the footprint the box already claimed, so nothing that
    passed can start clashing. What it buys is a skyline with a profile —
    plain extrusions of different heights are a bar chart from any angle.
    """
    built: list[te.Prop] = []
    for prop in towers:
        if prop.size[1] < SETBACK_ABOVE:
            built.append(prop)
            continue
        built += te.stepped_tower(
            terrain, prop.id, prop.at, (prop.size[0], prop.size[2]),
            prop.size[1], tiers=3, yaw=prop.yaw, taper=0.82,
            material=prop.material,
        )
    return built


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
