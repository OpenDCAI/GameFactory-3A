"""
test/test_3d_scene_code.py

Tests for the code-built `3d_scene` route: terrain, layout and sizing written
as Python, checked as a greybox, then detailed.

Nothing here needs weights, a GPU or a network, so it runs anywhere. The scene
geometry is compared against `models/common/glb_writer`, which is what actually
writes the file — a validator that disagrees with the writer passes scenes that
are wrong on disk.

Run from repo root:
    python test/test_3d_scene_code.py
    python test/test_3d_scene_code.py --video      # also record turntables
"""
from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from models.common.glb_writer import rotated_bounds  # noqa: E402
from operators.gen_3d_scene.funcs import terrain_code_edit as te  # noqa: E402
from operators.gen_3d_scene.funcs.terrain_code_template import (  # noqa: E402
    STAGES,
    TEMPLATES,
    build_scene,
    foreground,
    landforms,
)

OUT_DIR = _REPO_ROOT / "test_data" / "outputs" / "_test_3d_scene_code"
VIEWER_LIB = _REPO_ROOT / "test_data" / "outputs" / "_viewer_lib"


def glb_json(path: Path) -> dict:
    """The JSON chunk of a GLB, for checking what was actually written."""
    data = path.read_bytes()
    offset = 12
    while offset < len(data):
        length, kind = struct.unpack_from("<II", data, offset)
        if kind == 0x4E4F534A:
            return json.loads(data[offset + 8: offset + 8 + length])
        offset += 8 + length
    raise AssertionError(f"{path} has no JSON chunk")


# ── terrain ──────────────────────────────────────────────────────────────────

class TestTerrainShapes(unittest.TestCase):
    """Each terrain function has to produce the landform it is named for."""

    def test_terrain_flat_is_level_everywhere(self):
        ground = te.flat(40.0)
        for x, z in ((0.0, 0.0), (15.0, -12.0), (-19.0, 19.0)):
            self.assertEqual(te.ground_height(ground, x, z), 0.0)

    def test_terrain_flat_ripple_stays_within_its_amount(self):
        ground = te.flat(60.0, ripple=0.6, seed=4)
        heights = [
            te.ground_height(ground, x, z)
            for x in range(-25, 26, 5)
            for z in range(-25, 26, 5)
        ]
        self.assertLessEqual(max(abs(h) for h in heights), 0.6 + 1e-9)
        self.assertGreater(max(heights) - min(heights), 0.1,
                           "a ripple that never moves is not a ripple")

    def test_terrain_bowl_is_lowest_at_the_centre(self):
        ground = te.bowl(60.0, depth=8.0, roughness=0.0)
        centre = te.ground_height(ground, 0.0, 0.0)
        self.assertAlmostEqual(centre, -8.0, places=6)
        for radius in (10.0, 20.0, 29.0):
            self.assertGreater(te.ground_height(ground, radius, 0.0), centre)
        self.assertAlmostEqual(te.ground_height(ground, 30.0, 0.0), 0.0, places=6)

    def test_terrain_bowl_low_point_can_sit_off_centre(self):
        """A dish centred on the origin gives every radius one cross-section."""
        offset = (-12.0, 8.0)
        ground = te.bowl(80.0, depth=8.0, centre=offset, roughness=0.0)
        self.assertAlmostEqual(te.ground_height(ground, *offset), -8.0, places=6)
        self.assertGreater(te.ground_height(ground, 0.0, 0.0), -8.0)

    def test_terrain_mound_has_a_level_top(self):
        ground = te.mound(60.0, rise=6.0, flat_radius=12.0, roughness=0.0)
        for radius in (0.0, 6.0, 11.9):
            self.assertAlmostEqual(te.ground_height(ground, radius, 0.0), 6.0,
                                   places=6)
        self.assertLess(te.ground_height(ground, 20.0, 0.0), 6.0)
        self.assertAlmostEqual(te.ground_height(ground, 30.0, 0.0), 0.0, places=6)

    def test_terrain_mound_keeps_its_plateau_under_noise(self):
        """The noise is for the flanks; a settlement stands on the top."""
        ground = te.mound(60.0, rise=6.0, flat_radius=12.0, seed=2)
        for radius in (0.0, 6.0, 11.9):
            self.assertAlmostEqual(te.ground_height(ground, radius, 0.0), 6.0,
                                   places=6)
        flank = [te.ground_height(ground, r, 0.0) for r in (16.0, 20.0, 24.0)]
        bare = te.mound(60.0, rise=6.0, flat_radius=12.0, roughness=0.0)
        self.assertNotEqual(
            flank, [te.ground_height(bare, r, 0.0) for r in (16.0, 20.0, 24.0)]
        )

    def test_terrain_canyon_has_a_level_floor_between_walls(self):
        ground = te.canyon(70.0, depth=9.0, floor_width=16.0,
                           meander=0.0, roughness=0.0)
        for x in (-7.0, 0.0, 7.0):
            self.assertEqual(te.ground_height(ground, x, 5.0), 0.0)
        self.assertGreater(te.ground_height(ground, 20.0, 5.0), 0.0)
        self.assertAlmostEqual(te.ground_height(ground, 35.0, 5.0), 9.0, places=6)

    def test_terrain_canyon_channel_meanders(self):
        """A straight channel is two parallel ramps, not a canyon."""
        ground = te.canyon(78.0, depth=10.0, floor_width=15.0, seed=4)

        def floor_centre(z):
            xs = [x / 2.0 for x in range(-78, 79)
                  if te.ground_height(ground, x / 2.0, z) <= 1e-9]
            return sum(xs) / len(xs) if xs else None

        centres = [floor_centre(z) for z in (-30.0, -10.0, 10.0, 30.0)]
        self.assertTrue(all(c is not None for c in centres))
        self.assertGreater(max(centres) - min(centres), 2.0)

    def test_terrain_hills_do_not_repeat_on_a_lattice(self):
        """Crossed sines put every crest on a grid; noise must not."""
        ground = te.hills(80.0, amplitude=4.0, wavelength=30.0, seed=2)
        first = [te.ground_height(ground, x, 0.0) for x in range(-30, 0)]
        second = [te.ground_height(ground, x, 0.0) for x in range(0, 30)]
        self.assertNotEqual([round(v, 3) for v in first],
                            [round(v, 3) for v in second])

    def test_terrain_noise_is_reproducible_and_seed_dependent(self):
        same = (te.fractal_noise(3.0, 7.0, 20.0, seed=1),
                te.fractal_noise(3.0, 7.0, 20.0, seed=1))
        self.assertEqual(*same)
        self.assertNotEqual(te.fractal_noise(3.0, 7.0, 20.0, seed=1),
                            te.fractal_noise(3.0, 7.0, 20.0, seed=2))

    def test_terrain_noise_stays_in_range(self):
        values = [
            te.fractal_noise(x * 0.7, z * 1.3, 12.0, seed=5)
            for x in range(40) for z in range(40)
        ]
        self.assertLessEqual(max(values), 1.0)
        self.assertGreaterEqual(min(values), -1.0)

    def test_terrain_graded_brings_a_network_to_one_level(self):
        """Runs that cross have to agree, or the paving steps at the junction."""
        rolling = te.hills(90.0, amplitude=5.0, wavelength=25.0, seed=4)
        across = te.ways_along([(-40.0, 0.0), (40.0, 0.0)])
        down = te.ways_along([(0.0, -40.0), (0.0, 40.0)])
        network = te.graded(rolling, across + down, width=8.0, blend=6.0,
                            level=0.0)

        for x, z in ((-30.0, 0.0), (0.0, 0.0), (25.0, 0.0),
                     (0.0, -30.0), (0.0, 18.0)):
            self.assertAlmostEqual(te.ground_height(network, x, z), 0.0,
                                   places=6)

        # Well away from the runs the terrain is untouched.
        self.assertAlmostEqual(te.ground_height(network, 35.0, 35.0),
                               te.ground_height(rolling, 35.0, 35.0), places=6)

    def test_terrain_graded_defaults_to_the_ground_it_replaces(self):
        rolling = te.hills(80.0, amplitude=4.0, seed=2)
        ways = te.ways_along([(-20.0, -10.0), (20.0, 12.0)])
        network = te.graded(rolling, ways, width=6.0)

        level = te.ground_height(network, 0.0, 1.0)
        ends = [te.ground_height(rolling, *spot) for spot in
                ((-20.0, -10.0), (20.0, 12.0))]
        self.assertGreaterEqual(level, min(ends) - 1e-6)
        self.assertLessEqual(level, max(ends) + 1e-6)

    def test_terrain_carved_cuts_a_channel_that_follows_the_fall(self):
        """A flat trench ignores the terrain; a river keeps its gradient."""
        sloping = te.slope(90.0, rise=9.0, roughness=0.0)
        ways = te.ways_along([(0.0, -40.0), (0.0, 40.0)])
        channel = te.carved(sloping, ways, width=10.0, depth=4.0, banks=8.0)

        for z in (-30.0, 0.0, 30.0):
            self.assertAlmostEqual(
                te.ground_height(channel, 0.0, z),
                te.ground_height(sloping, 0.0, z) - 4.0, places=6,
            )
        # The bed still falls along its length.
        self.assertNotAlmostEqual(te.ground_height(channel, 0.0, -30.0),
                                  te.ground_height(channel, 0.0, 30.0))

        # Beyond the banks the ground is as it was.
        self.assertAlmostEqual(te.ground_height(channel, 25.0, 0.0),
                               te.ground_height(sloping, 25.0, 0.0), places=6)

    def test_terrain_carved_survives_a_graded_crossing(self):
        """Grading after carving dams the channel at every bridge."""
        river = te.ways_along([(0.0, -40.0), (0.0, 40.0)])
        street = te.ways_along([(-40.0, 0.0), (40.0, 0.0)])

        terrain = te.flat(90.0, ripple=1.0, seed=3)
        terrain = te.graded(terrain, street, width=10.0, level=0.0)
        terrain = te.carved(terrain, river, width=10.0, depth=4.0)

        # The channel is open where the street crosses it.
        self.assertLess(te.ground_height(terrain, 0.0, 0.0), -3.0)
        # And the street is still level to either side of the water.
        self.assertAlmostEqual(te.ground_height(terrain, 25.0, 0.0), 0.0,
                               places=6)

    def test_terrain_flattened_levels_a_path_and_eases_back(self):
        rolling = te.hills(60.0, amplitude=4.0, wavelength=20.0)
        path = [(0.0, z) for z in (-10.0, 0.0, 10.0)]
        levelled = te.flattened(rolling, path, width=4.0, blend=6.0)

        heights = {te.ground_height(levelled, 0.0, z) for z in (-10.0, 0.0, 10.0)}
        self.assertEqual(len(heights), 1, "the path itself must be level")

        far = 30.0
        self.assertAlmostEqual(
            te.ground_height(levelled, far, 0.0),
            te.ground_height(rolling, far, 0.0),
            places=6,
        )

    def test_terrain_levelled_at_cuts_a_pad_per_spot(self):
        rolling = te.hills(60.0, amplitude=5.0, wavelength=22.0, seed=3)
        spots = [(-14.0, 8.0), (11.0, -12.0)]
        padded = te.levelled_at(rolling, spots, radius=3.0, blend=4.0)

        for x, z in spots:
            level = te.ground_height(padded, x, z)
            self.assertAlmostEqual(level, te.ground_height(rolling, x, z),
                                   places=6)
            for dx, dz in ((2.5, 0.0), (0.0, -2.5), (-1.8, 1.8)):
                self.assertAlmostEqual(te.ground_height(padded, x + dx, z + dz),
                                       level, places=6)

        # Well away from every pad, the terrain is untouched.
        self.assertAlmostEqual(te.ground_height(padded, 26.0, 26.0),
                               te.ground_height(rolling, 26.0, 26.0), places=6)

    def test_terrain_relief_is_one_welded_surface(self):
        """A box per tile is a staircase; the ground has to be one part."""
        for name, ground in (
            ("bowl", te.bowl(60.0, depth=9.0, tiles=24)),
            ("canyon", te.canyon(70.0, depth=9.0, tiles=24)),
            ("hills", te.hills(60.0, amplitude=5.0, tiles=24)),
            ("mound", te.mound(60.0, rise=6.0, tiles=24)),
        ):
            with self.subTest(terrain=name):
                parts = te.terrain_parts(ground)
                self.assertEqual(len(parts), 1)
                self.assertEqual(parts[0]["kind"], "heightfield")

    def test_terrain_heightfield_spans_the_site_and_its_relief(self):
        ground = te.bowl(64.0, depth=8.0, tiles=24)
        part = te.terrain_parts(ground)[0]
        grid = part["heights"]
        self.assertEqual(len(grid), 25)
        self.assertEqual(len(grid[0]), 25)
        self.assertEqual(part["size"][0], 64.0)
        self.assertEqual(part["size"][2], 64.0)
        self.assertLess(part["skirt"], min(min(row) for row in grid))

    def test_terrain_flat_ground_stays_one_box(self):
        parts = te.terrain_parts(te.flat(40.0))
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["kind"], "box")


# ── layout ───────────────────────────────────────────────────────────────────

class TestLayoutHelpers(unittest.TestCase):
    """Spot generators and the filters that tie a layout to the ground."""

    def test_layout_ring_spots_sit_on_the_circle(self):
        for spot in te.ring_spots(8, 12.0):
            self.assertAlmostEqual(math.hypot(*spot), 12.0, places=6)

    def test_layout_grid_spots_are_centred_and_evenly_spaced(self):
        spots = te.grid_spots(3, 3, 5.0)
        self.assertEqual(len(spots), 9)
        self.assertIn((0.0, 0.0), spots)
        xs = sorted({round(x, 6) for x, _ in spots})
        self.assertEqual(xs, [-5.0, 0.0, 5.0])

    def test_layout_scatter_spots_respect_the_minimum_gap(self):
        spots = te.scatter_spots(12, 40.0, seed=5, min_gap=6.0)
        for index, first in enumerate(spots):
            for second in spots[index + 1:]:
                self.assertGreaterEqual(math.dist(first, second), 6.0)

    def test_layout_scatter_spots_are_reproducible(self):
        self.assertEqual(
            te.scatter_spots(10, 30.0, seed=7),
            te.scatter_spots(10, 30.0, seed=7),
        )

    def test_layout_height_filters_split_by_elevation(self):
        ground = te.hills(60.0, amplitude=5.0, wavelength=25.0)
        spots = te.scatter_spots(60, 50.0, seed=3, min_gap=3.0)

        high = te.on_high_ground(spots, ground, above=2.0)
        low = te.on_low_ground(spots, ground, below=-2.0)

        self.assertTrue(high and low, "the filters must each keep something")
        self.assertFalse(set(high) & set(low))
        for spot in high:
            self.assertGreaterEqual(te.ground_height(ground, *spot), 2.0)
        for spot in low:
            self.assertLessEqual(te.ground_height(ground, *spot), -2.0)

    def test_layout_slope_filter_keeps_only_steep_ground(self):
        # A straight, unroughened canyon, so the two sets of spots are known
        # to be on the floor and on the wall rather than wherever the channel
        # happened to wander to.
        ground = te.canyon(70.0, depth=9.0, floor_width=16.0,
                           meander=0.0, roughness=0.0)
        floor = [(0.0, 0.0), (4.0, 10.0)]
        wall = [(13.0, 0.0), (-14.0, 8.0)]

        for x, z in wall:
            self.assertGreater(te.ground_height(ground, x, z), 0.0,
                               "the fixture's wall spots must be on the wall")
        self.assertEqual(te.on_slope(floor, ground, steeper_than=0.05), [])
        self.assertEqual(len(te.on_slope(wall, ground, steeper_than=0.05)), 2)

    def test_layout_clear_of_avoids_placed_props(self):
        blocker = te.Prop("blocker", "box", (0.0, 0.0), (8.0, 3.0, 8.0))
        spots = [(0.0, 0.0), (3.0, 3.0), (20.0, 20.0)]
        self.assertEqual(te.clear_of(spots, [blocker]), [(20.0, 20.0)])

    def test_layout_jitter_moves_every_spot_within_bounds(self):
        grid = te.grid_spots(4, 4, 10.0)
        moved = te.jittered(grid, 2.0, seed=1)
        self.assertEqual(len(moved), len(grid))
        for before, after in zip(grid, moved):
            self.assertLessEqual(abs(after[0] - before[0]), 2.0)
            self.assertLessEqual(abs(after[1] - before[1]), 2.0)
        self.assertNotEqual(grid, moved)

    def test_layout_jitter_is_reproducible(self):
        grid = te.grid_spots(3, 3, 8.0)
        self.assertEqual(te.jittered(grid, 1.5, seed=9),
                         te.jittered(grid, 1.5, seed=9))

    def test_layout_blocks_leave_gaps_and_offset_rows(self):
        full = te.blocks_spots(6, 6, 12.0)
        self.assertEqual(len(full), 36)

        sparse = te.blocks_spots(6, 6, 12.0, skip=0.3, seed=2)
        self.assertLess(len(sparse), 36)
        self.assertGreater(len(sparse), 12)

        staggered = te.blocks_spots(4, 4, 12.0, stagger=0.25)
        rows = {}
        for x, z in staggered:
            rows.setdefault(round(z, 6), []).append(round(x, 6))
        offsets = sorted({min(xs) for xs in rows.values()})
        self.assertEqual(len(offsets), 2, "alternate rows must be offset")

    def test_sizing_tiered_heights_fall_off_and_vary(self):
        spots = te.grid_spots(7, 7, 15.0)
        heights = te.tiered_heights(spots, tall=40.0, short=8.0, seed=3)
        self.assertEqual(len(heights), len(spots))

        near = [h for spot, h in zip(spots, heights)
                if math.hypot(*spot) < 20.0]
        far = [h for spot, h in zip(spots, heights)
               if math.hypot(*spot) > 40.0]
        self.assertGreater(sum(near) / len(near), sum(far) / len(far))

        # Every building the same height is a wall, not a skyline.
        self.assertGreater(len({round(h, 3) for h in near}), 1)

    def test_sizing_stepped_pairs_footprints_with_heights(self):
        sizes = te.stepped_sizes([(4.0, 0.0, 3.0), (5.0, 0.0, 5.0)], [9.0, 2.0])
        self.assertEqual(sizes, [(4.0, 9.0, 3.0), (5.0, 2.0, 5.0)])

    def test_layout_height_band_keeps_only_its_contour(self):
        ground = te.bowl(60.0, depth=9.0, roughness=0.0)
        spots = te.scatter_spots(60, 50.0, seed=1, min_gap=3.0)
        kept = te.in_height_band(spots, ground, lowest=-6.0, highest=-3.0)

        self.assertTrue(kept)
        self.assertLess(len(kept), len(spots))
        for spot in kept:
            self.assertLessEqual(-6.0, te.ground_height(ground, *spot))
            self.assertLessEqual(te.ground_height(ground, *spot), -3.0)

    def test_layout_lowest_spot_finds_an_off_centre_basin(self):
        """The pool follows the ground, so this cannot just return the origin."""
        offset = (-14.0, 9.0)
        ground = te.bowl(80.0, depth=9.0, centre=offset, roughness=0.0)
        found = te.lowest_spot(ground)
        self.assertLess(math.dist(found, offset), 4.0)

    def test_layout_lowest_spot_is_the_lowest_ground_it_can_resolve(self):
        """Sampled, so it lands in the low ground rather than exactly on it."""
        ground = te.bowl(80.0, depth=9.0, centre=(6.0, -11.0), seed=5)
        found = te.lowest_spot(ground, samples=96)
        here = te.ground_height(ground, *found)
        for spot in te.scatter_spots(120, 60.0, seed=3):
            self.assertLess(here, te.ground_height(ground, *spot) + 0.1)

    def test_layout_lowest_spot_on_flat_ground_is_the_origin(self):
        self.assertEqual(te.lowest_spot(te.flat(40.0)), (0.0, 0.0))

    def test_layout_channel_spots_stay_on_the_floor(self):
        ground = te.canyon(78.0, depth=10.0, floor_width=15.0, seed=4)
        spots = te.channel_spots(ground, 9, along="z", seed=1)

        self.assertEqual(len(spots), 9)
        for spot in spots:
            self.assertAlmostEqual(te.ground_height(ground, *spot), 0.0,
                                   places=6)

    def test_layout_channel_spots_follow_a_bend(self):
        """A fixed x would climb the wall as soon as the channel moves."""
        ground = te.canyon(78.0, depth=10.0, floor_width=15.0, seed=4)
        across = [x for x, _z in te.channel_spots(ground, 9, along="z")]
        self.assertGreater(max(across) - min(across), 1.0)

    def test_layout_channel_spots_wander_across_the_floor(self):
        """Down the exact middle is a centreline, which reads as laid out."""
        ground = te.canyon(78.0, depth=10.0, floor_width=15.0, seed=4)
        middle = te.channel_spots(ground, 9, along="z", wander=0.0)
        wandered = te.channel_spots(ground, 9, along="z", wander=0.8, seed=2)

        self.assertNotEqual([x for x, _z in middle],
                            [x for x, _z in wandered])
        for spot in wandered:
            self.assertAlmostEqual(te.ground_height(ground, *spot), 0.0,
                                   places=6)

    def test_layout_channel_edge_finds_the_foot_of_each_wall(self):
        ground = te.canyon(78.0, depth=10.0, floor_width=15.0,
                           meander=0.0, roughness=0.0)
        near = te.channel_edge(ground, 0.0, -1.0, along="z")
        far = te.channel_edge(ground, 0.0, 1.0, along="z")

        self.assertLess(near, 0.0)
        self.assertGreater(far, 0.0)
        for edge in (near, far):
            self.assertAlmostEqual(te.ground_height(ground, edge, 0.0), 0.0,
                                   places=6)
            # A step further out is on the wall.
            outward = edge + (2.0 if edge > 0 else -2.0)
            self.assertGreater(te.ground_height(ground, outward, 0.0), 0.0)

    def test_layout_channel_edge_reports_no_floor_rather_than_guessing(self):
        """No ground that low, so there is no edge to return."""
        ground = te.canyon(78.0, depth=10.0, floor_width=15.0)
        self.assertIsNone(te.channel_edge(ground, 0.0, 1.0, level=-5.0))
        self.assertIsNone(te.channel_edge(te.flat(40.0), 0.0, 1.0))

    def test_layout_drift_breaks_a_ring_unevenly(self):
        ring = te.ring_spots(12, 20.0)
        moved = te.drift(ring, 5.0, seed=4)

        self.assertEqual(len(moved), len(ring))
        radii = [math.hypot(*spot) for spot in moved]
        self.assertGreater(max(radii) - min(radii), 2.0,
                           "a drifted ring is still a ring")
        self.assertEqual(te.drift(ring, 5.0, seed=4), moved)

    def test_layout_winding_spots_bend_without_leaving_their_band(self):
        """A ruled line reads as drawn; the bend is what makes it built."""
        spots = te.winding_spots(9, span=100.0, wander=8.0, along="z", seed=2)
        self.assertEqual(len(spots), 9)

        across = [x for x, _z in spots]
        self.assertGreater(max(across) - min(across), 2.0, "the run is ruled")
        for value in across:
            self.assertLessEqual(abs(value), 8.0)

        # Evenly spaced along its axis, and reproducible.
        down = [z for _x, z in spots]
        self.assertAlmostEqual(down[0], -50.0, places=6)
        self.assertAlmostEqual(down[-1], 50.0, places=6)
        self.assertEqual(te.winding_spots(9, 100.0, 8.0, "z", seed=2), spots)

    def test_layout_winding_spots_follow_the_axis_asked_for(self):
        down = te.winding_spots(7, 80.0, 6.0, along="z", seed=1)
        across = te.winding_spots(7, 80.0, 6.0, along="x", seed=1)
        self.assertGreater(max(z for _x, z in down) - min(z for _x, z in down),
                           70.0)
        self.assertGreater(max(x for x, _z in across)
                           - min(x for x, _z in across), 70.0)

    def test_layout_ways_along_pairs_a_polyline(self):
        line = [(0.0, 0.0), (1.0, 2.0), (3.0, 5.0)]
        self.assertEqual(
            te.ways_along(line),
            [((0.0, 0.0), (1.0, 2.0)), ((1.0, 2.0), (3.0, 5.0))],
        )
        self.assertEqual(te.ways_along([(0.0, 0.0)]), [])

    def test_layout_way_distance_measures_to_a_segment(self):
        ways = te.ways_along([(-10.0, 0.0), (10.0, 0.0)])
        self.assertAlmostEqual(te.way_distance((0.0, 4.0), ways), 4.0, places=6)
        # Past the end, the nearest point is the endpoint itself.
        self.assertAlmostEqual(te.way_distance((16.0, 0.0), ways), 6.0, places=6)
        self.assertEqual(te.way_distance((0.0, 0.0), []), float("inf"))

    def test_layout_way_distance_follows_a_bend(self):
        """A straight-line measure would call the inside of a corner clear."""
        ways = te.ways_along([(-10.0, -10.0), (0.0, 0.0), (10.0, -10.0)])
        self.assertLess(te.way_distance((0.0, -3.0), ways), 3.0)

    def test_layout_clear_of_ways_drops_what_sits_on_a_run(self):
        ways = te.ways_along([(-20.0, 0.0), (20.0, 0.0)])
        spots = [(0.0, 0.0), (0.0, 3.0), (0.0, 12.0)]
        self.assertEqual(te.clear_of_ways(spots, ways, margin=5.0),
                         [(0.0, 12.0)])
        self.assertEqual(te.clear_of_ways(spots, [], margin=5.0), spots)

    def test_layout_irregular_lines_span_their_extent_unevenly(self):
        offsets = te.irregular_lines(200.0, least=25.0, most=45.0, seed=3)
        self.assertGreater(len(offsets), 4)
        self.assertAlmostEqual(offsets[0], -100.0, places=6)
        self.assertAlmostEqual(offsets[-1], 100.0, places=6)

        gaps = [b - a for a, b in zip(offsets, offsets[1:])]
        self.assertGreater(len({round(g, 1) for g in gaps}), 2,
                           "an even pitch gives identical blocks")
        for gap in gaps[:-1]:
            self.assertGreaterEqual(gap, 25.0)
            self.assertLessEqual(gap, 45.0)

    def test_layout_path_tiles_cover_a_bend_without_gaps(self):
        line = [(-10.0, 0.0), (0.0, 0.0), (0.0, 10.0)]
        tiles = te.path_tiles(line, tile=2.5)
        self.assertEqual(len(tiles), 8)

        # Each leg is covered by whole tiles, turned to that leg.
        yaws = {round(yaw) for _spot, yaw, _length in tiles}
        self.assertEqual(len(yaws), 2, yaws)
        for _spot, _yaw, length in tiles:
            self.assertAlmostEqual(length, 2.5, places=6)

    def test_layout_path_tiles_stretch_to_fit_each_leg(self):
        """Whole tiles per leg, so a corner has no gap and nothing overhangs."""
        tiles = te.path_tiles([(0.0, 0.0), (0.0, 7.0)], tile=2.0)
        total = sum(length for _spot, _yaw, length in tiles)
        self.assertAlmostEqual(total, 7.0, places=6)

    def test_layout_paved_builds_a_touching_run(self):
        tiles = te.path_tiles([(-12.0, 0.0), (12.0, 0.0)], tile=4.0)
        slabs = te.paved("road", tiles, width=7.0)

        self.assertEqual(len(slabs), 6)
        self.assertEqual({p.group for p in slabs}, {"paving"})
        for slab in slabs:
            self.assertEqual(slab.size[2], 7.0)
            self.assertLess(slab.size[1], 0.4)
        # Grouped, so a run that meets is not reported as a clash.
        self.assertEqual(
            te.check_scene(te.Scene("t", te.flat(60.0), slabs)), []
        )

    def test_layout_pinned_holds_a_level_while_the_ground_falls(self):
        """A deck placed like any other prop would sag with the terrain."""
        sloping = te.slope(80.0, rise=10.0, roughness=0.0)
        tiles = te.path_tiles([(0.0, -30.0), (0.0, 30.0)], tile=6.0)
        deck = te.pinned(
            sloping, te.paved("deck", tiles, width=6.0, sink=0.0), level=9.0
        )

        bases = [te.bounds(sloping, slab)[0][1] for slab in deck]
        for base in bases:
            self.assertAlmostEqual(base, 9.0, places=6)

        # The ground under it genuinely varies, so the test is not vacuous.
        under = [te.ground_height(sloping, *slab.at) for slab in deck]
        self.assertGreater(max(under) - min(under), 3.0)

    def test_layout_arc_spots_curve_between_two_bearings(self):
        """A slip road laid as straight segments is a chamfered corner."""
        spots = te.arc_spots((5.0, -3.0), 10.0, 0.0, 90.0, 7)
        self.assertEqual(len(spots), 7)

        for spot in spots:
            self.assertAlmostEqual(math.dist(spot, (5.0, -3.0)), 10.0, places=6)

        self.assertAlmostEqual(spots[0][0], 15.0, places=6)
        self.assertAlmostEqual(spots[-1][1], 7.0, places=6)

        # The turn is progressive, not a corner: no two segments share a
        # bearing and none of them reverses.
        bearings = [
            math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            for a, b in zip(spots, spots[1:])
        ]
        self.assertEqual(len({round(v, 3) for v in bearings}), len(bearings))
        for before, after in zip(bearings, bearings[1:]):
            self.assertGreater(after, before)

    def test_layout_sloped_spreads_a_climb_along_a_run(self):
        """A ramp pinned to one level is a step, not a ramp."""
        ground = te.flat(80.0)
        tiles = te.path_tiles([(-20.0, 0.0), (20.0, 0.0)], tile=5.0)
        ramp = te.sloped(
            ground, te.paved("ramp", tiles, width=6.0, sink=0.0),
            start=0.0, end=8.0,
        )

        bases = [te.bounds(ground, slab)[0][1] for slab in ramp]
        self.assertAlmostEqual(bases[0], 0.0, places=6)
        self.assertAlmostEqual(bases[-1], 8.0, places=6)

        # Evenly, so consecutive slabs meet.
        steps = [b - a for a, b in zip(bases, bases[1:])]
        self.assertLess(max(steps) - min(steps), 1e-6)

    def test_layout_sloped_handles_a_single_slab(self):
        ground = te.flat(40.0)
        one = te.sloped(ground, te.paved("r", te.path_tiles(
            [(0.0, 0.0), (0.0, 2.0)], tile=4.0), width=3.0, sink=0.0),
            start=1.0, end=5.0)
        self.assertEqual(len(one), 1)
        self.assertAlmostEqual(te.bounds(ground, one[0])[0][1], 1.0, places=6)

    def test_layout_scatter_varies_sizes_and_buries(self):
        """The pattern every landform's loose scenery wants."""
        spots = te.scatter_spots(10, 60.0, seed=1, min_gap=4.0)
        props = te.scatter("rock", "sphere", spots, (2.0, 1.6, 2.0),
                           spread=0.4, buried=0.3, seed=2)

        self.assertEqual(len(props), len(spots))
        self.assertGreater(len({round(p.size[0], 3) for p in props}), 1)
        for prop in props:
            self.assertAlmostEqual(prop.sink, prop.size[1] * 0.3, places=6)

        # No two consecutive props share a facing.
        yaws = [p.yaw for p in props]
        self.assertEqual(len(set(yaws)), len(yaws))

    def test_layout_scatter_leaves_sink_alone_when_not_buried(self):
        props = te.scatter("box", "box", [(0.0, 0.0)], (2.0, 2.0, 2.0))
        self.assertEqual(props[0].sink, 0.0)

    def test_layout_fit_all_keeps_what_fits_in_order(self):
        """Each kept candidate is what the next one is measured against."""
        ground = te.flat(80.0)
        row = [
            te.Prop(f"b-{n}", "box", (n * 4.0, 0.0), (6.0, 3.0, 6.0))
            for n in range(4)
        ]
        kept = te.fit_all(ground, row, margin=0.5)

        self.assertLess(len(kept), len(row))
        self.assertEqual(kept[0].id, "b-0", "the first candidate must win")
        self.assertEqual(
            te.check_scene(te.Scene("t", ground, kept)), []
        )

    def test_layout_fit_all_respects_what_is_already_placed(self):
        ground = te.flat(80.0)
        there = [te.Prop("there", "box", (0.0, 0.0), (10.0, 3.0, 10.0))]
        candidates = [te.Prop("here", "box", (4.0, 0.0), (10.0, 3.0, 10.0))]

        self.assertEqual(te.fit_all(ground, candidates, there), [])
        self.assertEqual(len(te.fit_all(ground, candidates)), 1)

    def test_layout_ring_wall_closes_around_its_circle(self):
        """Segments cut square leave a wedge at every joint on the outside."""
        wall = te.ring_wall("wall", radius=20.0, segments=16, height=4.0)
        self.assertEqual(len(wall), 16)

        for segment in wall:
            self.assertAlmostEqual(math.hypot(*segment.at), 20.0, places=6)

        # Each is longer than the chord it spans, so neighbours meet.
        chord = 2.0 * math.sin(math.pi / 16) * 20.0
        self.assertGreater(wall[0].size[0], chord)

        # Grouped, so a run that touches is not reported as clashing.
        self.assertEqual(
            te.check_scene(te.Scene("t", te.flat(60.0), wall)), []
        )

    def test_layout_ring_wall_has_a_parapet_line(self):
        """One unbroken top edge reads as a fence, not a rampart."""
        wall = te.ring_wall("wall", 20.0, 16, height=4.0, vary=0.6)
        self.assertGreater(len({round(s.size[1], 2) for s in wall}), 1)

        flat = te.ring_wall("wall", 20.0, 16, height=4.0, vary=0.0)
        self.assertEqual(len({round(s.size[1], 2) for s in flat}), 1)

    def test_layout_columns_reach_from_their_footing_to_the_deck(self):
        """One fixed height leaves a support short wherever the ground dips."""
        ground = te.slope(80.0, rise=10.0, roughness=0.0)
        tiles = te.path_tiles([(0.0, -25.0), (0.0, 25.0)], tile=10.0)
        piers = te.columns(ground, "pier", tiles, top=14.0, side=2.0)

        self.assertEqual(len(piers), len(tiles))
        for pier in piers:
            low, high = te.bounds(ground, pier)
            # Reaches past the deck, and stands on the ground under it —
            # `ground_under` rests it on its lowest corner, so the base is at
            # or below the height at its centre.
            self.assertGreater(high[1], 14.0)
            self.assertLessEqual(low[1],
                                 te.ground_height(ground, *pier.at) + 1e-6)
            self.assertGreater(low[1],
                               te.ground_height(ground, *pier.at) - 1.0)

        # The ground varies, so they cannot all be the same height.
        self.assertGreater(len({round(p.size[1], 2) for p in piers}), 1)

    def test_layout_water_along_holds_one_surface(self):
        """Rested panels follow the bed and come out as a staircase."""
        ground = te.slope(90.0, rise=8.0, roughness=0.0)
        panels = te.water_along(
            ground, "water", [(0.0, -35.0), (0.0, 35.0)], tile=10.0,
            width=12.0, depth=2.0, level=3.0,
        )
        self.assertGreater(len(panels), 4)

        tops = [te.bounds(ground, p)[1][1] for p in panels]
        self.assertLess(max(tops) - min(tops), 1e-6)
        self.assertAlmostEqual(max(tops), 3.0, places=6)

    def test_layout_road_network_carries_what_spans_a_cut(self):
        """A slab resting on a bank steps away from its neighbours."""
        river = te.ways_along([(0.0, -40.0), (0.0, 40.0)])
        street = [(-40.0, 0.0), (40.0, 0.0)]

        terrain = te.flat(90.0, ripple=1.0, seed=2)
        terrain = te.graded(terrain, te.ways_along(street), 10.0, level=0.0)
        terrain = te.carved(terrain, river, width=12.0, depth=4.0, banks=8.0)

        resting, carried = te.road_network(
            terrain, {"road": street}, {"road": 8.0}, tile=5.0, level=0.0,
            over=river, span=16.0, structure=8.0,
        )
        self.assertTrue(resting and carried)

        # Everything carried holds the street's level over the cut.
        for slab in carried:
            self.assertAlmostEqual(te.bounds(terrain, slab)[0][1], 0.0,
                                   places=6)
        # And only the part over the gap reads as structure; the approach
        # either side is embankment and stays paving.
        decks = [s for s in carried if s.material == "wall"]
        self.assertTrue(decks)
        self.assertLess(len(decks), len(carried))

    def test_layout_road_network_rests_when_there_is_nothing_to_span(self):
        terrain = te.flat(80.0, ripple=0.8, seed=3)
        line = [(-30.0, 0.0), (30.0, 0.0)]
        resting, carried = te.road_network(
            terrain, {"road": line}, {"road": 7.0}, tile=5.0, level=0.0,
        )
        self.assertEqual(carried, [])
        self.assertTrue(resting)

    def test_layout_interchange_stacks_two_routes_with_ramps(self):
        ground = te.flat(200.0, ripple=1.0, seed=4)
        decks, routes, ramps = te.interchange(
            ground, hub=(0.0, -20.0), size=200.0, lane=8.0,
            ground_level=0.0, levels=(7.0, 13.5), tile=7.0, seed=1,
        )
        self.assertEqual(len(routes), 2)
        self.assertTrue(decks and ramps)

        lower = [p for p in decks if p.id.startswith("flyover-")]
        upper = [p for p in decks if p.id.startswith("overpass-")]
        self.assertAlmostEqual(te.bounds(ground, lower[0])[0][1], 7.0, places=6)
        self.assertAlmostEqual(te.bounds(ground, upper[0])[0][1], 13.5, places=6)

        # Four quadrant loops and one link between the decks.
        names = {p.id.split("-")[0] for p in ramps}
        self.assertEqual(names, {"ramp0", "ramp1", "ramp2", "ramp3", "link"})

        for name in names:
            run = [p for p in ramps if p.id.startswith(name + "-")]
            levels = [te.bounds(ground, p)[0][1] for p in run]
            self.assertGreater(max(levels) - min(levels), 3.0,
                               f"{name} does not climb")

    def test_layout_fits_margin_cannot_hide_a_clash_on_a_slope(self):
        """Widening a footprint lowers where a prop rests.

        `ground_under` takes the lowest ground beneath the whole base, so the
        grown copy used for the margin sits further down than the real prop.
        On a slope that drop was enough to pass under a raised deck the real
        prop would hit.
        """
        ground = te.slope(80.0, rise=12.0, roughness=0.0)
        deck = te.pinned(
            ground,
            [te.Prop("deck", "box", (0.0, 0.0), (12.0, 0.9, 12.0))],
            level=6.0,
        )
        tall = te.Prop("tall", "box", (0.0, 0.0), (4.0, 9.0, 4.0))

        # It genuinely clashes, so no margin may report otherwise.
        self.assertTrue(te.overlap(ground, tall, deck[0]))
        for margin in (0.0, 0.5, 1.0, 2.0, 4.0):
            with self.subTest(margin=margin):
                self.assertFalse(te.fits(ground, tall, deck, margin=margin))

    def test_layout_fits_margin_still_widens_the_footprint(self):
        """The fix must not make the margin itself a no-op."""
        ground = te.flat(80.0)
        there = [te.Prop("there", "box", (0.0, 0.0), (4.0, 3.0, 4.0))]
        near = te.Prop("near", "box", (5.0, 0.0), (4.0, 3.0, 4.0))

        # One metre of clear ground between their walls.
        self.assertTrue(te.fits(ground, near, there))
        self.assertFalse(te.fits(ground, near, there, margin=1.5))

    def test_layout_clustered_gathers_points_into_groups(self):
        """A plain scatter is one density; building happens in hamlets."""
        spots = te.clustered_spots(4, 6, 80.0, spread=8.0, seed=1, min_gap=3.0)
        self.assertGreater(len(spots), 12)

        nearest = [
            min(math.dist(a, b) for b in spots if b is not a) for a in spots
        ]
        spread = max(math.dist(a, b) for a in spots for b in spots)
        self.assertLess(sum(nearest) / len(nearest), spread * 0.2)

        plain = te.scatter_spots(len(spots), 80.0, seed=1, min_gap=3.0)
        plain_nearest = [
            min(math.dist(a, b) for b in plain if b is not a) for a in plain
        ]
        self.assertLess(sum(nearest) / len(nearest),
                        sum(plain_nearest) / len(plain_nearest))

    def test_layout_clustered_stays_inside_its_extent(self):
        for spot in te.clustered_spots(5, 8, 40.0, spread=12.0, seed=2):
            self.assertLessEqual(abs(spot[0]), 20.0)
            self.assertLessEqual(abs(spot[1]), 20.0)

    def test_layout_clustered_accepts_chosen_centres(self):
        """Seeds picked against the terrain, not drawn at random."""
        centres = [(-20.0, 10.0), (18.0, -14.0)]
        spots = te.clustered_spots(0, 5, 80.0, spread=6.0, seed=3,
                                   min_gap=2.0, centres=centres)
        self.assertTrue(spots)
        for spot in spots:
            self.assertLess(min(math.dist(spot, c) for c in centres), 6.01)

    def test_layout_strip_lays_tiles_end_to_end(self):
        spots, yaw, length = te.strip_spots((-15.0, 0.0), (15.0, 0.0), tile=5.0)
        self.assertEqual(len(spots), 6)
        self.assertAlmostEqual(yaw, 0.0, places=6)
        self.assertEqual(length, 5.0)

        # Consecutive tiles meet rather than overlap or leave a gap.
        for before, after in zip(spots, spots[1:]):
            self.assertAlmostEqual(math.dist(before, after), 5.0, places=6)

    def test_layout_strip_turns_to_follow_its_line(self):
        _spots, yaw, _length = te.strip_spots((0.0, -10.0), (0.0, 10.0),
                                              tile=4.0)
        self.assertAlmostEqual(abs(yaw), 90.0, places=6)

    def test_layout_contour_radius_measures_the_ground(self):
        """Sizing water by guesswork either floats it or drowns the shore."""
        ground = te.bowl(80.0, depth=8.0, roughness=0.0, rim_share=1.0)
        # Halfway down the dish: r/R = sqrt(1 - level/depth) for this profile.
        radius = te.contour_radius(ground, (0.0, 0.0), -4.0)
        self.assertAlmostEqual(radius / 40.0, math.sqrt(0.5), places=1)

    def test_layout_contour_radius_follows_an_off_centre_basin(self):
        offset = (-12.0, 7.0)
        ground = te.bowl(80.0, depth=8.0, centre=offset, roughness=0.0)
        at_low = te.contour_radius(ground, offset, -6.0)
        at_origin = te.contour_radius(ground, (0.0, 0.0), -6.0)
        self.assertGreater(at_low, at_origin)

    def test_layout_contour_radius_on_flat_ground_is_zero(self):
        self.assertEqual(te.contour_radius(te.flat(40.0), (0.0, 0.0), 1.0), 0.0)

    def test_layout_fits_measures_the_candidate_own_footprint(self):
        """`clear_of` is given a bare point, so it cannot see a wide building."""
        ground = te.flat(80.0)
        placed = [te.Prop("there", "box", (0.0, 0.0), (10.0, 5.0, 10.0))]

        wide = te.Prop("wide", "box", (9.0, 0.0), (10.0, 5.0, 10.0))
        narrow = te.Prop("narrow", "box", (9.0, 0.0), (2.0, 5.0, 2.0))

        # The point is clear of the placed prop either way.
        self.assertTrue(te.clear_of([(9.0, 0.0)], placed))
        self.assertFalse(te.fits(ground, wide, placed))
        self.assertTrue(te.fits(ground, narrow, placed))

    def test_layout_fits_honours_margin_and_groups(self):
        ground = te.flat(80.0)
        placed = [te.Prop("there", "box", (0.0, 0.0), (4.0, 3.0, 4.0),
                          group="run")]
        candidate = te.Prop("here", "box", (5.0, 0.0), (4.0, 3.0, 4.0))

        # One metre of clear ground between them.
        self.assertTrue(te.fits(ground, candidate, placed))
        self.assertFalse(te.fits(ground, candidate, placed, margin=1.5))

        # A prop in the same group is meant to touch, so it is not a clash.
        joined = te.Prop("here", "box", (3.0, 0.0), (4.0, 3.0, 4.0),
                         group="run")
        self.assertTrue(te.fits(ground, joined, placed, margin=1.5))


# ── geometry ─────────────────────────────────────────────────────────────────

class TestSceneGeometry(unittest.TestCase):
    """What the checker measures must be what the writer writes."""

    def test_geom_place_carries_group_and_sink(self):
        """A paved run has to be markable as touching, or it reads as clashing."""
        spots = [(0.0, 0.0), (4.0, 0.0)]
        props = te.place("road", "box", spots, size=(4.0, 0.2, 6.0),
                         material="block", group="streets", sink=0.05)

        self.assertEqual([p.group for p in props], ["streets"] * 2)
        self.assertEqual([p.sink for p in props], [0.05] * 2)
        self.assertEqual(te.check_scene(te.Scene("t", te.flat(40.0), props)), [])

    def test_geom_corners_match_the_writer_at_every_yaw(self):
        ground = te.flat(60.0)
        for yaw in (0, 30, 45, 60, 90, 144, 210, -60):
            with self.subTest(yaw=yaw):
                prop = te.Prop("p", "box", (5.0, 3.0), (4.2, 2.0, 3.6),
                               yaw=float(yaw))
                corners = te.ground_corners(ground, prop)
                mine = (
                    min(c[0] for c in corners), max(c[0] for c in corners),
                    min(c[1] for c in corners), max(c[1] for c in corners),
                )
                part = te.prop_part(ground, prop)
                low, high = rotated_bounds(
                    part["size"], part["at"],
                    part.get("rotation", (0.0, 0.0, 0.0)), kind="box",
                )
                for got, want in zip(mine, (low[0], high[0], low[2], high[2])):
                    self.assertAlmostEqual(got, want, places=9)

    def test_geom_props_rest_on_the_terrain(self):
        ground = te.hills(60.0, amplitude=4.0)
        for spot in ((0.0, 0.0), (11.0, -7.0), (-14.0, 13.0)):
            prop = te.Prop("p", "box", spot, (2.0, 3.0, 2.0))
            low, _ = te.bounds(ground, prop)
            self.assertAlmostEqual(low[1], te.ground_under(ground, prop),
                                   places=6)

    def test_geom_a_wide_base_rests_on_its_lowest_corner(self):
        """Sampling only the centre leaves a wide prop floating on a slope."""
        ground = te.slope(60.0, rise=8.0, roughness=0.0)
        prop = te.Prop("hall", "box", (0.0, 0.0), (12.0, 4.0, 12.0))

        centre = te.ground_height(ground, 0.0, 0.0)
        resting = te.ground_under(ground, prop)
        self.assertLess(resting, centre)

        # No corner of the base may be above the ground it stands on.
        low, _ = te.bounds(ground, prop)
        for x, z in te.ground_corners(ground, prop):
            self.assertLessEqual(low[1] - 1e-9, te.ground_height(ground, x, z))

    def test_geom_flat_ground_needs_no_lowest_corner(self):
        ground = te.flat(40.0)
        prop = te.Prop("p", "box", (5.0, 5.0), (6.0, 2.0, 6.0))
        self.assertEqual(te.ground_under(ground, prop), 0.0)

    def test_geom_ground_normal_is_up_on_the_level_and_tilts_on_a_slope(self):
        self.assertEqual(te.ground_normal(te.flat(40.0), 3.0, 3.0),
                         (0.0, 1.0, 0.0))
        tilted = te.ground_normal(te.slope(60.0, rise=10.0, roughness=0.0),
                                  0.0, 0.0)
        self.assertGreater(tilted[1], 0.0)
        self.assertGreater(abs(tilted[2]), 1e-6)

    def test_geom_overlap_measures_the_turned_shape(self):
        ground = te.flat(50.0)

        def box(name, at, size, yaw=0.0):
            return te.Prop(name, "box", at, size, yaw=yaw)

        clear = te.overlap(ground, box("a", (0, 0), (2, 2, 2)),
                           box("b", (5, 0), (2, 2, 2)))
        self.assertIsNone(clear)

        through = te.overlap(ground, box("a", (0, 0), (2, 2, 2)),
                             box("b", (1, 0), (2, 2, 2)))
        self.assertIsNotNone(through)
        self.assertAlmostEqual(through[0], 1.0, places=6)

        touching = te.overlap(ground, box("a", (0, 0), (2, 2, 2)),
                              box("b", (2, 0), (2, 2, 2)))
        self.assertIsNone(touching)

        # An upright box around either shape would report a clash here.
        turned = te.overlap(ground, box("a", (0, 0), (4, 3, 3), 45.0),
                            box("b", (4.6, 0), (4, 3, 3), 45.0))
        self.assertIsNone(turned)

    def test_geom_stacked_props_are_separation_not_collision(self):
        ground = te.flat(40.0)
        base = te.Prop("base", "box", (0.0, 0.0), (4.0, 3.0, 4.0))
        on_top = te.Prop("top", "cone", (0.0, 0.0), (4.0, 2.0, 4.0), sink=-3.0)
        self.assertIsNone(te.overlap(ground, base, on_top))

    def test_geom_grouped_props_may_touch(self):
        ground = te.flat(40.0)
        scene = te.Scene("run", ground, [
            te.Prop("a", "box", (0.0, 0.0), (4.0, 1.0, 2.0), group="road"),
            te.Prop("b", "box", (3.5, 0.0), (4.0, 1.0, 2.0), group="road"),
        ])
        self.assertEqual(te.check_scene(scene), [])

        scene.props[1].group = "other"
        self.assertEqual(len(te.check_scene(scene)), 1)


# ── ground surface ───────────────────────────────────────────────────────────

class TestHeightfield(unittest.TestCase):
    """The ground is the largest thing in the scene and has to be solid.

    A terrain that is inside-out or has holes in it renders identically in a
    viewer that does not cull backfaces, and shows the fault first in an
    engine — the far side of the handoff.
    """

    TERRAINS = (
        ("hills", te.hills(60.0, amplitude=4.5, tiles=20, seed=2)),
        ("bowl", te.bowl(64.0, depth=8.0, tiles=20, seed=3)),
        ("canyon", te.canyon(70.0, depth=9.0, tiles=20, seed=4)),
        ("mound", te.mound(64.0, rise=6.0, tiles=20, seed=5)),
        ("ripple", te.flat(60.0, ripple=0.5, seed=6)),
    )

    @staticmethod
    def measure(part):
        """Signed volume, boundary edges and normal agreement, in one pass."""
        from models.common.glb_writer import build_part

        positions, normals, indices = build_part(part)
        volume = 0.0
        opposed = 0
        edges: dict = {}
        for offset in range(0, len(indices), 3):
            corners = [positions[indices[offset + n]] for n in range(3)]
            a, b, c = corners
            volume += (
                a[0] * (b[1] * c[2] - b[2] * c[1])
                + a[1] * (b[2] * c[0] - b[0] * c[2])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            ) / 6.0

            u = [b[n] - a[n] for n in range(3)]
            v = [c[n] - a[n] for n in range(3)]
            facing = (u[1] * v[2] - u[2] * v[1],
                      u[2] * v[0] - u[0] * v[2],
                      u[0] * v[1] - u[1] * v[0])
            stored = [
                sum(normals[indices[offset + n]][axis] for n in range(3)) / 3.0
                for axis in range(3)
            ]
            if sum(facing[n] * stored[n] for n in range(3)) < 0.0:
                opposed += 1

            keys = [tuple(round(value, 6) for value in point) for point in corners]
            for first, second in ((0, 1), (1, 2), (2, 0)):
                edge = frozenset((keys[first], keys[second]))
                edges[edge] = edges.get(edge, 0) + 1

        return {
            "volume": volume,
            "boundary": sum(1 for count in edges.values() if count == 1),
            "opposed": opposed,
            "triangles": len(indices) // 3,
        }

    def test_field_is_a_closed_outward_facing_solid(self):
        for name, ground in self.TERRAINS:
            with self.subTest(terrain=name):
                report = self.measure(te.terrain_parts(ground)[0])
                self.assertGreater(report["volume"], 0.0,
                                   f"{name} is inside-out: {report}")
                self.assertEqual(report["boundary"], 0,
                                 f"{name} has holes: {report}")
                self.assertEqual(report["opposed"], 0,
                                 f"{name} normals disagree: {report}")

    def test_field_surface_matches_the_terrain_it_was_sampled_from(self):
        """The written ground has to be the ground the layout was placed on."""
        from models.common.glb_writer import build_part

        ground = te.hills(48.0, amplitude=4.0, tiles=16, seed=7)
        part = te.terrain_parts(ground)[0]
        positions, _normals, _indices = build_part(part)

        tops = {}
        for x, y, z in positions:
            key = (round(x, 4), round(z, 4))
            tops[key] = max(tops.get(key, float("-inf")), y)

        step = 48.0 / 16
        for row in range(0, 17, 4):
            for column in range(0, 17, 4):
                x = -24.0 + column * step
                z = -24.0 + row * step
                self.assertAlmostEqual(
                    tops[(round(x, 4), round(z, 4))],
                    te.ground_height(ground, x, z),
                    places=4,
                )

    def test_field_bound_agrees_with_the_written_surface(self):
        """A gate that measures the ground differently passes a wrong scene."""
        from models.common.glb_writer import build_part, rotated_bounds

        for name, ground in self.TERRAINS:
            with self.subTest(terrain=name):
                part = te.terrain_parts(ground)[0]
                if part["kind"] != "heightfield":
                    continue
                positions, _n, _i = build_part(part)
                low, high = rotated_bounds(
                    part["size"], part["at"], (0.0, 0.0, 0.0),
                    kind="heightfield",
                    heights=part["heights"], skirt=part["skirt"],
                )
                for axis in range(3):
                    self.assertLessEqual(
                        low[axis] - 1e-6, min(p[axis] for p in positions))
                    self.assertGreaterEqual(
                        high[axis] + 1e-6, max(p[axis] for p in positions))

    def test_field_refuses_a_grid_too_small_to_be_a_surface(self):
        from models.common.glb_writer import build_part

        with self.assertRaises(ValueError):
            build_part({"id": "g", "kind": "heightfield", "size": (10.0, 1.0, 10.0),
                        "at": (0.0, 0.0, 0.0), "heights": [[0.0]]})

    def test_field_costs_far_less_than_a_box_per_tile(self):
        """The staircase it replaced was also six faces per sample."""
        ground = te.hills(60.0, amplitude=4.0, tiles=32)
        report = self.measure(te.terrain_parts(ground)[0])
        self.assertLess(report["triangles"], 32 * 32 * 12)


# ── validation ───────────────────────────────────────────────────────────────

class TestSceneChecks(unittest.TestCase):
    """`check_scene` has to catch what makes a greybox unusable."""

    def test_check_reports_a_prop_off_the_terrain(self):
        scene = te.Scene("t", te.flat(20.0),
                         [te.Prop("far", "box", (30.0, 0.0), (2.0, 2.0, 2.0))])
        self.assertIn("terrain", " ".join(te.check_scene(scene)))

    def test_check_reports_duplicate_ids(self):
        scene = te.Scene("t", te.flat(40.0), [
            te.Prop("same", "box", (-8.0, 0.0), (2.0, 2.0, 2.0)),
            te.Prop("same", "box", (8.0, 0.0), (2.0, 2.0, 2.0)),
        ])
        self.assertIn("duplicate id", " ".join(te.check_scene(scene)))

    def test_check_reports_sizes_that_misread_against_a_person(self):
        giant = te.Scene("t", te.flat(600.0),
                         [te.Prop("g", "box", (0.0, 0.0), (2.0, 400.0, 2.0))])
        self.assertIn("check the units", " ".join(te.check_scene(giant)))

        speck = te.Scene("t", te.flat(40.0),
                         [te.Prop("s", "box", (0.0, 0.0), (0.05, 0.05, 0.05))])
        self.assertIn("twentieth", " ".join(te.check_scene(speck)))

    def test_check_allows_a_tower_block(self):
        """A scene is not an object: a tower is legitimately dozens of people."""
        tower = te.Scene("t", te.flat(200.0),
                         [te.Prop("t", "box", (0.0, 0.0), (12.0, 60.0, 12.0))])
        self.assertEqual(te.check_scene(tower), [])

    def test_check_reports_a_non_positive_size(self):
        scene = te.Scene("t", te.flat(40.0),
                         [te.Prop("flat", "box", (0.0, 0.0), (2.0, 0.0, 2.0))])
        self.assertIn("positive", " ".join(te.check_scene(scene)))


# ── the two stages ───────────────────────────────────────────────────────────

class TestStages(unittest.TestCase):
    """The split into ground and foreground has to hold both ways.

    Two files rather than one per scene, so the two questions take separate
    parameters and can be changed separately. That only pays off if the pair
    really is composable — a landform usable with a different foreground, and
    a foreground reading the ground's measurements rather than re-deriving
    them.
    """

    def test_stages_cover_every_template(self):
        self.assertEqual(set(STAGES), set(TEMPLATES))
        self.assertEqual(set(landforms.LANDFORMS), set(foreground.FOREGROUNDS))

    def test_stages_landform_returns_ground_and_no_props(self):
        """A landform answers what the ground is, and nothing else."""
        for name, make_ground in landforms.LANDFORMS.items():
            with self.subTest(landform=name):
                ground = make_ground()
                self.assertIsInstance(ground, landforms.Ground)
                self.assertIsInstance(ground.terrain, te.Terrain)
                self.assertEqual(ground.terrain.size, ground.size)

    def test_stages_foreground_returns_terrain_and_props(self):
        """One shape for all of them, including the ones that cut the ground."""
        for name in STAGES:
            with self.subTest(landform=name):
                make_ground, populate = STAGES[name]
                terrain, props = populate(make_ground())
                self.assertIsInstance(terrain, te.Terrain)
                self.assertTrue(props)
                self.assertTrue(all(isinstance(p, te.Prop) for p in props))

    def test_stages_ground_parameters_reach_the_landform(self):
        scene = build_scene("basin", size=70.0)
        self.assertEqual(scene.terrain.size, 70.0)
        self.assertEqual(te.check_scene(scene), [])

    def test_stages_foreground_parameters_reach_the_foreground(self):
        sparse = build_scene("plains", foreground_args={"boulders": 3,
                                                        "thickets": 2})
        dense = build_scene("plains", foreground_args={"boulders": 12,
                                                       "thickets": 8})
        self.assertLess(len(sparse.props), len(dense.props))

    def test_stages_a_landform_takes_a_different_foreground(self):
        """The pairing is a default, not a coupling."""
        ground = landforms.hills(size=90.0, relief=5.0)
        terrain, props = foreground.plains(ground)

        self.assertTrue(props)
        self.assertEqual(
            te.check_scene(te.Scene("mixed", terrain, props)), []
        )

    def test_stages_measurements_are_taken_where_they_are_known(self):
        """Re-deriving a measurement downstream means two answers to one thing."""
        ground = landforms.basin()
        water = ground.marks["water"]
        surface = ground.marks["surface"]

        # The reported low point really is the low ground.
        here = te.ground_height(ground.terrain, *water)
        for spot in te.scatter_spots(60, ground.size * 0.7, seed=5):
            self.assertLess(here, te.ground_height(ground.terrain, *spot) + 0.2)
        self.assertGreater(surface, here)

        # And the foreground puts the water there rather than at the origin.
        _terrain, props = foreground.basin(ground)
        pool = next(p for p in props if p.id == "pool")
        self.assertEqual(pool.at, water)

    def test_stages_city_reports_its_networks(self):
        """The foreground needs the runs the ground was graded and carved to."""
        ground = landforms.city()
        self.assertIn("streets", ground.ways)
        self.assertIn("river", ground.ways)
        self.assertGreater(len(ground.ways["streets"]), 20)

        # Every street line has a width, so paving can be laid to it.
        for name in ground.lines:
            if name == "river":
                continue
            self.assertIn(name, ground.marks["street_widths"])

        # The ground really is graded to the level it reports.
        level = ground.marks["street_level"]
        for start, _end in ground.ways["streets"][:12]:
            if te.way_distance(start, ground.ways["river"]) < 30.0:
                continue
            self.assertAlmostEqual(
                te.ground_height(ground.terrain, *start), level, places=6
            )


# ── templates ────────────────────────────────────────────────────────────────

class TestTemplates(unittest.TestCase):
    """Every landform template, and the differences between them."""

    def test_template_every_scene_is_clean(self):
        for name, build in TEMPLATES.items():
            with self.subTest(template=name):
                problems = te.check_scene(build())
                self.assertEqual(problems, [], f"{name}: {problems}")

    def test_template_names_match_the_scene_they_build(self):
        for name, build in TEMPLATES.items():
            with self.subTest(template=name):
                self.assertEqual(build().name, name)

    def test_template_is_reproducible(self):
        for name, build in TEMPLATES.items():
            with self.subTest(template=name):
                first = te.scene_summary(build())
                second = te.scene_summary(build())
                self.assertEqual(first, second)

    def test_template_terrain_profiles_differ(self):
        """The classification is by landform, so the ground must actually differ."""
        shapes = {}
        for name, build in TEMPLATES.items():
            ground = build().terrain
            shapes[name] = tuple(
                round(te.ground_height(ground, x, 0.0), 3)
                for x in (-20.0, -10.0, 0.0, 10.0, 20.0)
            )
        self.assertEqual(len(set(shapes.values())), len(shapes), shapes)

    def test_template_no_landform_is_perfectly_level(self):
        """Every template's ground has to move, including the flat ones."""
        for name, build in TEMPLATES.items():
            with self.subTest(template=name):
                ground = build().terrain
                step = ground.size / 20.0
                heights = [
                    te.ground_height(ground, x * step, z * step)
                    for x in range(-9, 10) for z in range(-9, 10)
                ]
                self.assertGreater(max(heights) - min(heights), 0.1)

    def test_template_flat_landforms_differ_by_layout(self):
        """`plains` and `city` are both near-level, so their layouts must diverge.

        Compared by whether the ground carries a network: a district's paving
        is a long chain of touching slabs, while a plain has nothing laid on
        it at all. Spacing does not separate them, since a city holds both
        touching paving and wide-open blocks.
        """
        city = TEMPLATES["city"]()
        plains = TEMPLATES["plains"]()

        paved = [p for p in city.props if p.id.startswith("road")]
        self.assertGreater(len(paved), 100)

        # Each slab has a neighbour it meets, which is what a run is.
        touching = 0
        for prop in paved:
            gap = min(math.dist(prop.at, other.at)
                      for other in paved if other is not prop)
            if gap < prop.size[0] * 1.2:
                touching += 1
        self.assertGreater(touching, len(paved) * 0.9)

        # Nothing in the plains is laid in a run. Trunk and canopy share a
        # spot on purpose, so pairs in one group do not count.
        for prop in plains.props:
            others = [
                other for other in plains.props
                if other is not prop
                and not (prop.group and prop.group == other.group)
            ]
            self.assertGreater(min(math.dist(prop.at, o.at) for o in others),
                               1.0)

    def test_template_city_is_built_to_streets(self):
        """Buildings on a plane are objects; a district needs roads."""
        scene = TEMPLATES["city"]()
        roads = [p for p in scene.props if p.id.startswith("road")]
        self.assertGreater(len(roads), 100)

        # Paving is thin and flush, not a set of kerbs to climb.
        for road in roads:
            self.assertLess(road.size[1], 0.4)

        # Runs on both axes, so it is a network and not one strip.
        axes = {round(p.yaw / 45.0) for p in roads}
        self.assertGreater(len(axes), 1, axes)

        # One street is wider than the rest, so the network has a spine.
        widths = {round(p.size[2], 1) for p in roads}
        self.assertGreater(len(widths), 1, "every street is the same width")

    def test_template_city_streets_are_not_ruled_lines(self):
        """A dead-straight run reads as drawn rather than built."""
        scene = TEMPLATES["city"]()
        runs: dict[str, list[float]] = {}
        for prop in scene.props:
            if not prop.id.startswith("road"):
                continue
            runs.setdefault(prop.id.split("-")[0], []).append(prop.yaw)

        self.assertGreater(len(runs), 4)
        bending = [
            name for name, yaws in runs.items()
            if max(yaws) - min(yaws) > 3.0
        ]
        self.assertGreater(len(bending), len(runs) * 0.6,
                           "most streets should bend across the site")

    def test_template_city_streets_do_not_step_into_potholes(self):
        """Slabs each resting on their own ground leave steps between them.

        The whole network is graded to one level for exactly this reason: on
        unlevelled ground a slab can sit 20 cm above its neighbour, which is
        more than its own thickness and so a hole every few metres. The bar
        is half the thickness, below which no gap can show.
        """
        scene = TEMPLATES["city"]()
        runs: dict[str, list[te.Prop]] = {}
        for prop in scene.props:
            if prop.id.startswith("road"):
                runs.setdefault(prop.id.split("-")[0], []).append(prop)

        self.assertTrue(runs)
        for name, slabs in runs.items():
            with self.subTest(street=name):
                limit = min(slab.size[1] for slab in slabs) / 2.0
                bases = [te.bounds(scene.terrain, slab)[0][1] for slab in slabs]
                for before, after in zip(bases, bases[1:]):
                    self.assertLessEqual(
                        abs(after - before), limit,
                        f"{name} steps between neighbouring slabs",
                    )

    def test_template_city_has_a_river_that_survives_its_crossings(self):
        """Grading the streets last would dam the channel at every bridge."""
        scene = TEMPLATES["city"]()
        water = [p for p in scene.props if p.id.startswith("water-")]
        self.assertGreater(len(water), 8)

        # The channel is cut below the streets along its whole length.
        for slab in water:
            self.assertLess(te.ground_height(scene.terrain, *slab.at), -2.0)

        # And it wanders rather than running straight down an axis.
        self.assertGreater(
            max(p.at[0] for p in water) - min(p.at[0] for p in water), 8.0
        )

    def test_template_city_river_surface_is_level(self):
        """Panels resting on their own stretch of bed make water a staircase."""
        scene = TEMPLATES["city"]()
        water = [p for p in scene.props if p.id.startswith("water-")]
        self.assertGreater(len(water), 8)

        tops = [te.bounds(scene.terrain, p)[1][1] for p in water]
        self.assertLess(max(tops) - min(tops), 0.01)

        # And the surface is below the streets it passes under.
        self.assertLess(max(tops), 0.0)

    def test_template_city_bridges_carry_the_street_over_the_water(self):
        scene = TEMPLATES["city"]()
        bridges = [
            p for p in scene.props
            if p.id.startswith("road") and p.material == "wall"
        ]
        self.assertGreater(len(bridges), 4)

        for slab in bridges:
            deck = te.bounds(scene.terrain, slab)[0][1]
            bed = te.ground_height(scene.terrain, *slab.at)
            self.assertGreater(deck - bed, 2.0,
                               f"{slab.id} is a ford, not a bridge")

    def test_template_city_flyover_holds_its_line_on_piers(self):
        """A deck placed like every other prop would follow the ground."""
        scene = TEMPLATES["city"]()
        deck = [p for p in scene.props if p.id.startswith("flyover-")]
        piers = [p for p in scene.props if p.id.startswith("pier-")]
        self.assertGreater(len(deck), 8)
        self.assertGreater(len(piers), 3)

        levels = [te.bounds(scene.terrain, slab)[0][1] for slab in deck]
        self.assertLess(max(levels) - min(levels), 0.05,
                        "the deck sags with the ground")
        self.assertGreater(min(levels), 5.0, "the deck is not raised")

        # The piers reach from the ground up to the deck.
        for pier in piers:
            low, high = te.bounds(scene.terrain, pier)
            self.assertGreater(high[1], min(levels) - 0.5)
            self.assertLess(low[1], 1.0)

    def test_template_city_interchange_is_stacked(self):
        """One raised road is a flyover; an interchange crosses itself."""
        scene = TEMPLATES["city"]()
        lower = [p for p in scene.props if p.id.startswith("flyover-")]
        upper = [p for p in scene.props if p.id.startswith("overpass-")]
        self.assertGreater(len(lower), 8)
        self.assertGreater(len(upper), 8)

        low_level = te.bounds(scene.terrain, lower[0])[0][1]
        high_level = te.bounds(scene.terrain, upper[0])[0][1]
        self.assertGreater(high_level - low_level, 4.0,
                           "the two decks are at the same height")

        # The two routes run on different axes, so they genuinely cross.
        def axis(run):
            spread_x = max(p.at[0] for p in run) - min(p.at[0] for p in run)
            spread_z = max(p.at[1] for p in run) - min(p.at[1] for p in run)
            return "x" if spread_x > spread_z else "z"

        self.assertNotEqual(axis(lower), axis(upper))

        # And they overlap in plan, which is what makes it a junction.
        self.assertLess(
            min(math.dist(a.at, b.at) for a in lower for b in upper), 12.0
        )

    def test_template_city_interchange_ramps_climb_between_levels(self):
        """Slip roads pinned to one height would be steps, not ramps."""
        scene = TEMPLATES["city"]()

        loops = {}
        for prop in scene.props:
            name = prop.id.split("-")[0]
            if name.startswith("ramp") or name == "link":
                loops.setdefault(name, []).append(prop)

        # Four quadrant loops between the decks, and one slip road up to them.
        self.assertEqual(len(loops), 5, sorted(loops))

        for name, slabs in loops.items():
            with self.subTest(ramp=name):
                levels = [te.bounds(scene.terrain, s)[0][1] for s in slabs]
                self.assertGreater(max(levels) - min(levels), 3.0,
                                   f"{name} does not climb")

                # Evenly, and by less than a slab's own thickness, so
                # consecutive slabs overlap instead of stepping apart into a
                # ladder.
                thickness = min(s.size[1] for s in slabs)
                ordered = sorted(levels)
                steps = [b - a for a, b in zip(ordered, ordered[1:])]
                self.assertLess(max(steps), thickness)

        # The upper deck is reachable: a loop runs the whole way up to it.
        upper = max(
            te.bounds(scene.terrain, p)[0][1] for p in scene.props
            if p.id.startswith("overpass-")
        )
        tops = [
            max(te.bounds(scene.terrain, s)[0][1] for s in slabs)
            for name, slabs in loops.items() if name != "link"
        ]
        self.assertAlmostEqual(max(tops), upper, delta=0.5,
                               msg="the upper deck is not reachable")

    def test_template_city_interchange_ramps_meet_the_decks(self):
        """A ramp ending in mid air reads as two unrelated structures."""
        scene = TEMPLATES["city"]()
        decks = [
            p for p in scene.props
            if p.id.startswith(("flyover-", "overpass-"))
        ]
        self.assertTrue(decks)

        for name in ("ramp0", "ramp1", "ramp2", "ramp3", "link"):
            run = [p for p in scene.props if p.id.startswith(name + "-")]
            self.assertTrue(run, name)

            # Ordered along the climb, so the ends are the first and last.
            run.sort(key=lambda p: te.bounds(scene.terrain, p)[0][1])
            with self.subTest(ramp=name):
                # The high end meets the deck it climbs to.
                high = run[-1]
                level = te.bounds(scene.terrain, high)[0][1]
                landing = [
                    d for d in decks
                    if abs(te.bounds(scene.terrain, d)[0][1] - level) < 1.5
                ]
                self.assertTrue(landing, f"{name} climbs to no deck")
                self.assertLess(
                    min(math.dist(high.at, d.at) for d in landing),
                    max(high.size[0], high.size[2]) * 2.0,
                    f"{name} stops short of the deck it climbs to",
                )

    def test_template_city_ramp_slabs_read_as_road(self):
        """A slab wider than it is long reads as a rung, not a surface."""
        scene = TEMPLATES["city"]()
        ramps = [
            p for p in scene.props
            if p.id.split("-")[0].startswith("ramp")
            or p.id.startswith("link-")
        ]
        self.assertTrue(ramps)

        for slab in ramps:
            self.assertGreater(slab.size[0], slab.size[2] * 0.6,
                               f"{slab.id} is a rung")

    def test_template_city_piers_stay_out_of_the_roadway(self):
        """A column through the carriageway is not a support."""
        scene = TEMPLATES["city"]()
        supports = [
            p for p in scene.props
            if p.id.startswith(("pier-", "column-"))
        ]
        roads = [
            p for p in scene.props
            if p.id.startswith("road") and p.group == "streets"
        ]
        self.assertTrue(supports and roads)

        for pier in supports:
            self.assertTrue(
                te.fits(scene.terrain, pier, roads),
                f"{pier.id} stands in the roadway",
            )

    def test_template_city_blocks_hold_a_mixture(self):
        """A tower per plot is a model village; a block has several things."""
        scene = TEMPLATES["city"]()
        kinds = {p.id.split("-")[0].rstrip("0123456789") for p in scene.props}
        for expected in ("roadx", "roadz", "tower", "shop", "yard", "lamp",
                         "water", "pier", "flyover"):
            self.assertIn(expected, kinds)

        towers = [p for p in scene.props if p.id.startswith("tower-")]
        shops = [p for p in scene.props if p.id.startswith("shop-")]
        self.assertGreater(len(towers), 10)
        self.assertGreater(len(shops), 5)

        # The frontage is lower than the towers it stands beside.
        self.assertLess(max(p.size[1] for p in shops),
                        max(p.size[1] for p in towers))

    def test_template_city_towers_stand_in_crowded_quarters(self):
        """Evenly spread towers read as a lattice, not as a city."""
        scene = TEMPLATES["city"]()
        towers = [p for p in scene.props if p.id.startswith("tower-")]
        self.assertGreater(len(towers), 12)

        nearest = [
            min(math.dist(a.at, b.at) for b in towers if b is not a)
            for a in towers
        ]
        widest = max(max(p.size[0], p.size[2]) for p in towers)

        # A neighbour within a couple of metres of the wall, on average.
        self.assertLess(sum(nearest) / len(nearest), widest * 1.7)

        # And the site is not filled at that density: there is open ground.
        spread = max(math.dist(a.at, b.at) for a in towers for b in towers)
        self.assertGreater(spread, widest * 12.0)

    def test_template_city_has_no_ring_at_its_centre(self):
        """A circle of props around the middle reads as a ritual formation."""
        scene = TEMPLATES["city"]()
        near = [
            p for p in scene.props
            if math.hypot(*p.at) < scene.terrain.size * 0.16
            and not p.id.startswith("road")
        ]
        if len(near) < 4:
            return

        radii = [math.hypot(*p.at) for p in near]
        mean = sum(radii) / len(radii)
        deviation = (sum((r - mean) ** 2 for r in radii) / len(radii)) ** 0.5
        self.assertGreater(deviation / mean, 0.15,
                           "the central props sit on a circle")

    def test_template_city_has_a_skyline(self):
        """Buildings of one height are a wall; a linear taper is concentric rings."""
        scene = TEMPLATES["city"]()
        towers = [p for p in scene.props if p.id.startswith("tower")]
        self.assertGreater(len(towers), 12)

        heights = [p.size[1] for p in towers]
        self.assertGreater(max(heights) / min(heights), 2.0)

        # The trend: the core is taller than the outskirts.
        radii = [math.hypot(*p.at) for p in towers]
        inner = min(radii) + (max(radii) - min(radii)) * 0.4
        outer = max(radii) - (max(radii) - min(radii)) * 0.4
        core = [p.size[1] for p, r in zip(towers, radii) if r < inner]
        edge = [p.size[1] for p, r in zip(towers, radii) if r > outer]
        self.assertTrue(core and edge)
        self.assertGreater(sum(core) / len(core), sum(edge) / len(edge))

        # And the scatter: neighbours at a similar distance still differ, or
        # the falloff would give concentric rings of identical buildings.
        paired = sorted(zip(radii, heights))
        varied = sum(
            1 for (r1, h1), (r2, h2) in zip(paired, paired[1:])
            if abs(r2 - r1) < 12.0 and abs(h2 - h1) > 1.0
        )
        self.assertGreater(varied, 3)

    def test_template_city_towers_are_set_in_their_blocks(self):
        """Buildings standing in the road means the blocks are not respected."""
        scene = TEMPLATES["city"]()
        roads = [p for p in scene.props if p.id.startswith("road")]
        standing = [
            p for p in scene.props
            if p.id.startswith(("tower-", "shop-", "yard-"))
        ]
        self.assertTrue(roads and standing)

        for prop in standing:
            self.assertTrue(
                te.clear_of([prop.at], roads),
                f"{prop.id} stands in the roadway",
            )

    def test_template_hills_layout_follows_elevation(self):
        """Towers take the ridges and dwellings the hollows, or the split is fake."""
        scene = TEMPLATES["hills"]()
        ground = scene.terrain

        towers = [p for p in scene.props if p.id.startswith("tower")]
        huts = [p for p in scene.props if p.id.startswith("hut")]
        self.assertTrue(towers and huts)

        highest_hut = max(te.ground_height(ground, *p.at) for p in huts)
        lowest_tower = min(te.ground_height(ground, *p.at) for p in towers)
        self.assertGreater(lowest_tower, highest_hut)

    def test_template_hills_structures_stand_on_level_pads(self):
        """A building on a hillside needs ground cut for it, not a tilted base."""
        scene = TEMPLATES["hills"]()
        for prop in scene.props:
            if not prop.id.startswith(("hut", "tower")):
                continue
            with self.subTest(prop=prop.id):
                level = te.ground_height(scene.terrain, *prop.at)
                for x, z in te.ground_corners(scene.terrain, prop):
                    self.assertAlmostEqual(
                        te.ground_height(scene.terrain, x, z), level, places=4
                    )

    def test_template_canyon_debris_sits_on_the_walls(self):
        scene = TEMPLATES["canyon"]()
        rocks = [p for p in scene.props if p.id.startswith("rock")]
        self.assertTrue(rocks)
        for rock in rocks:
            self.assertGreater(te.ground_height(scene.terrain, *rock.at), 0.0)

    def test_template_canyon_waypoints_follow_the_floor(self):
        """A straight line of markers climbs the wall once the channel bends."""
        scene = TEMPLATES["canyon"]()
        markers = [p for p in scene.props if p.id.startswith("marker")]
        self.assertTrue(markers)
        for marker in markers:
            self.assertAlmostEqual(
                te.ground_height(scene.terrain, *marker.at), 0.0, places=6
            )
        self.assertGreater(
            max(p.at[0] for p in markers) - min(p.at[0] for p in markers), 1.0,
            "the waypoints should wander with the channel",
        )

    def test_template_canyon_posts_do_not_stand_in_matched_pairs(self):
        """Posts mirrored across the trail read as a row of ritual gates."""
        scene = TEMPLATES["canyon"]()
        posts = [p for p in scene.props if p.id.startswith("post-")]
        self.assertTrue(posts)

        # No two posts share a z, so none of them is another's twin.
        depths = [round(p.at[1], 1) for p in posts]
        self.assertEqual(len(set(depths)), len(depths))

        # And they are not all against the same wall. Measured against the
        # channel's own middle at each post, since the channel meanders and a
        # fixed x=0 would call one wall both sides.
        sides = set()
        for post in posts:
            near = te.channel_edge(scene.terrain, post.at[1], -1.0)
            far = te.channel_edge(scene.terrain, post.at[1], 1.0)
            self.assertIsNotNone(near)
            sides.add(post.at[0] > (near + far) / 2.0)
        self.assertEqual(len(sides), 2)

        self.assertGreater(len({round(p.size[1], 2) for p in posts}), 1)

    def test_template_basin_houses_are_gathered_into_hamlets(self):
        """Evenly sown dwellings read as scattered, not as settled."""
        scene = TEMPLATES["basin"]()
        houses = [p for p in scene.props if p.id.startswith("house-")]
        self.assertGreater(len(houses), 12)

        # Each dwelling has a close neighbour, and the site has empty ground:
        # the nearest-neighbour distance is small against the overall spread.
        nearest = [
            min(math.dist(p.at, q.at) for q in houses if q is not p)
            for p in houses
        ]
        spread = max(math.dist(p.at, q.at) for p in houses for q in houses)
        self.assertLess(sum(nearest) / len(nearest), spread * 0.12)

    def test_template_basin_houses_are_not_a_ring(self):
        """A ring of dwellings around one focus reads as a ritual formation."""
        scene = TEMPLATES["basin"]()
        houses = [p for p in scene.props if p.id.startswith("house-")]
        pool = next(p for p in scene.props if p.id == "pool")

        radii = [math.dist(p.at, pool.at) for p in houses]
        mean = sum(radii) / len(radii)
        deviation = (sum((r - mean) ** 2 for r in radii) / len(radii)) ** 0.5
        self.assertGreater(deviation / mean, 0.15,
                           "the dwellings sit at too uniform a distance")

        # Nor are they evenly spread in angle, which a ring also gives.
        angles = sorted(math.degrees(math.atan2(p.at[1] - pool.at[1],
                                                p.at[0] - pool.at[0]))
                        for p in houses)
        gaps = [b - a for a, b in zip(angles, angles[1:])]
        self.assertGreater(max(gaps) / (sum(gaps) / len(gaps)), 2.0)

    def test_template_basin_holds_more_than_one_kind_of_thing(self):
        """A slope of identical boxes is a greybox nobody can read."""
        scene = TEMPLATES["basin"]()
        kinds = {p.id.split("-")[0] for p in scene.props}
        for expected in ("pool", "house", "shed", "reed", "stone", "path"):
            self.assertIn(expected, kinds)

        houses = [p for p in scene.props if p.id.startswith("house-")]
        self.assertGreater(len({round(p.size[0], 1) for p in houses}), 4,
                           "every dwelling is the same size")

    def test_template_basin_water_meets_its_own_shore(self):
        """Water sized by guesswork floats above the ground at its edge.

        The disc is deliberately wider than the waterline so its rim is
        buried in the bank, which is what hides the cylinder wall. What has
        to hold is that the surface is over the ground in the middle and
        under it at the rim, so the waterline falls on the disc rather than
        outside it.
        """
        scene = TEMPLATES["basin"]()
        pool = next(p for p in scene.props if p.id == "pool")
        _low, high = te.bounds(scene.terrain, pool)
        surface = high[1]
        radius = pool.size[0] / 2.0

        self.assertLess(te.ground_height(scene.terrain, *pool.at), surface,
                        "the water has no depth at its centre")

        # At the rim the ground has risen past the surface on every side, so
        # no part of the cylinder wall is left standing in the open.
        for ray in range(16):
            angle = 2.0 * math.pi * ray / 16
            rim = (pool.at[0] + radius * math.cos(angle),
                   pool.at[1] + radius * math.sin(angle))
            self.assertGreaterEqual(
                te.ground_height(scene.terrain, *rim), surface - 0.1,
                "the pool's edge stands proud of the ground",
            )

    def test_template_basin_shore_stands_at_the_waterline(self):
        """Reeds out in open water, or up on the rim, read as misplaced."""
        scene = TEMPLATES["basin"]()
        pool = next(p for p in scene.props if p.id == "pool")
        _low, high = te.bounds(scene.terrain, pool)
        surface = high[1]

        shore = [p for p in scene.props if p.id.startswith(("reed-", "stone-"))]
        self.assertGreater(len(shore), 8)
        for prop in shore:
            here = te.ground_height(scene.terrain, *prop.at)
            self.assertGreater(here, surface - 0.5)
            self.assertLess(here, surface + 2.0)

    def test_template_basin_track_steps_down_the_slope(self):
        """One long slab on a hillside is a plank, not a path."""
        scene = TEMPLATES["basin"]()
        path = [p for p in scene.props if p.id.startswith("path-")]
        self.assertGreater(len(path), 1)

        bases = [te.bounds(scene.terrain, p)[0][1] for p in path]
        self.assertGreater(max(bases) - min(bases), 0.3,
                           "every slab rests at the same height")

    def test_template_basin_houses_stand_on_buildable_ground(self):
        """The height band is what keeps them off the pool and off the rim."""
        scene = TEMPLATES["basin"]()
        houses = [p for p in scene.props if p.id.startswith("house-")]
        heights = [te.ground_height(scene.terrain, *p.at) for p in houses]
        self.assertLess(max(heights), 0.0, "a dwelling has climbed to the rim")
        self.assertGreater(len({round(h, 1) for h in heights}), 5,
                           "the dwellings are all on one contour")

    def test_template_basin_water_sits_at_the_low_point(self):
        """Water belongs where the ground drains to, not at the origin."""
        scene = TEMPLATES["basin"]()
        pool = next(p for p in scene.props if p.id == "pool")
        self.assertGreater(math.hypot(*pool.at), 4.0,
                           "the pool is pinned to the centre")

        here = te.ground_height(scene.terrain, *pool.at)
        for x, z in te.scatter_spots(80, scene.terrain.size * 0.8, seed=99):
            self.assertLessEqual(here - 1e-6,
                                 te.ground_height(scene.terrain, x, z))

    def test_template_canyon_water_is_not_at_the_midpoint(self):
        scene = TEMPLATES["canyon"]()
        pool = next(p for p in scene.props if p.id == "pool")
        self.assertGreater(abs(pool.at[1]), scene.terrain.size * 0.1)
        self.assertAlmostEqual(
            te.ground_height(scene.terrain, *pool.at), 0.0, places=6
        )

    def test_template_walled_town_wall_encloses_the_houses(self):
        scene = TEMPLATES["walled_town"]()
        wall = [p for p in scene.props if p.id.startswith("wall-")]
        houses = [p for p in scene.props if p.id.startswith("house-")]
        self.assertTrue(wall and houses)

        inner = min(math.hypot(*p.at) for p in wall)
        self.assertLess(max(math.hypot(*p.at) for p in houses), inner)

        # A rampart of one height is a fence; the parapet line has to vary.
        self.assertGreater(len({round(p.size[1], 2) for p in wall}), 1)


# ── writing ──────────────────────────────────────────────────────────────────

class TestSceneWriting(unittest.TestCase):
    """The GLB on disk has to match the scene that was checked."""

    def test_write_produces_a_readable_glb(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(te.write_scene(TEMPLATES["plains"](),
                                       Path(directory) / "scene.glb"))
            self.assertTrue(path.is_file())
            self.assertEqual(path.read_bytes()[:4], b"glTF")
            document = glb_json(path)
            self.assertTrue(document.get("meshes"))
            self.assertTrue(document.get("nodes"))

    def test_write_includes_terrain_and_every_prop(self):
        scene = TEMPLATES["city"]()
        expected = len(te.terrain_parts(scene.terrain)) + len(scene.props)
        self.assertEqual(len(te.build_spec(scene)["parts"]), expected)

    def test_write_carries_the_ground_as_a_single_node(self):
        """One welded surface, so the terrain is one thing an engine can move."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(te.write_scene(TEMPLATES["hills"](),
                                       Path(directory) / "scene.glb"))
            names = [node.get("name") for node in glb_json(path)["nodes"]]
            self.assertEqual(names.count("ground"), 1)
            self.assertFalse([n for n in names if str(n).startswith("ground-")])

    def test_write_every_template(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, build in TEMPLATES.items():
                with self.subTest(template=name):
                    path = Path(te.write_scene(build(),
                                               Path(directory) / f"{name}.glb"))
                    self.assertGreater(path.stat().st_size, 1000)


# ── detail ───────────────────────────────────────────────────────────────────

class TestSceneDetail(unittest.TestCase):
    """Stage 2: meshes and materials replacing greybox stand-ins."""

    MESH = (_REPO_ROOT / "test_data" / "outputs" / "game_knight_demo" / "default"
            / "assets" / "3d_object" / "knight_hybrid_001" / "model.glb")

    def setUp(self):
        if not self.MESH.is_file():
            self.skipTest(f"no generated mesh at {self.MESH}")

    def test_detail_swap_keeps_the_position(self):
        scene = TEMPLATES["plains"]()
        target = scene.props[0]
        swapped = te.swap_mesh(scene, target.id, self.MESH, height=2.0)
        replaced = next(p for p in swapped.props if p.id == target.id)
        self.assertEqual(replaced.at, target.at)
        self.assertEqual(replaced.source, str(self.MESH))

    def test_detail_swap_honours_the_requested_height(self):
        scene = TEMPLATES["plains"]()
        target = scene.props[0].id
        for height in (1.2, 2.5, 4.0):
            with self.subTest(height=height):
                swapped = te.swap_mesh(scene, target, self.MESH, height=height)
                prop = next(p for p in swapped.props if p.id == target)
                self.assertAlmostEqual(te.placed_height(prop), height, places=4)

    def test_detail_swapped_mesh_rests_on_the_ground(self):
        scene = TEMPLATES["hills"]()
        target = scene.props[0]
        swapped = te.swap_mesh(scene, target.id, self.MESH, height=3.0)
        prop = next(p for p in swapped.props if p.id == target.id)
        low, high = te.bounds(swapped.terrain, prop)
        self.assertAlmostEqual(low[1], te.ground_height(swapped.terrain, *prop.at),
                               places=4)
        self.assertAlmostEqual(high[1] - low[1], 3.0, places=4)

    def test_detail_swap_rejects_an_unknown_prop(self):
        with self.assertRaises(KeyError):
            te.swap_mesh(TEMPLATES["plains"](), "not-a-prop", self.MESH)

    def test_detail_material_swap_applies_everywhere(self):
        scene = te.swap_material(TEMPLATES["city"](), "wall", [0.2, 0.3, 0.4])
        self.assertEqual(scene.materials["wall"]["baseColor"],
                         [0.2, 0.3, 0.4, 1.0])

    def test_detail_texture_ids_cover_the_materials_used(self):
        for name, build in TEMPLATES.items():
            with self.subTest(template=name):
                scene = build()
                used = {scene.terrain.material} | {p.material for p in scene.props}
                expected = {te.MATERIAL_TEXTURES[m] for m in used
                            if m in te.MATERIAL_TEXTURES}
                self.assertEqual(set(te.texture_ids(scene)), expected)

    def test_detail_summary_counts_generated_parts(self):
        scene = TEMPLATES["plains"]()
        self.assertEqual(te.scene_summary(scene)["generated_parts"], 0)
        swapped = te.swap_mesh(scene, scene.props[0].id, self.MESH, height=2.0)
        self.assertEqual(te.scene_summary(swapped)["generated_parts"], 1)


# ── video ────────────────────────────────────────────────────────────────────

def record_videos(out_dir: Path = OUT_DIR, frames: int = 150) -> int:
    """Write every template as a GLB and record a turntable of each.

    Needs the compiled `turntable` helper in `test_data/outputs/_viewer_lib`
    and a local HTTP server rooted at `test_data/outputs`.
    """
    turntable = VIEWER_LIB / "turntable"
    if not turntable.is_file():
        print(f"no turntable helper at {turntable.relative_to(_REPO_ROOT)}")
        print("build it with:  cd test_data/outputs/_viewer_lib && "
              "swiftc -O -o turntable turntable.swift")
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    videos = out_dir / "videos"
    videos.mkdir(exist_ok=True)

    outputs_root = _REPO_ROOT / "test_data" / "outputs"
    relative = out_dir.relative_to(outputs_root)

    server = subprocess.Popen(
        [sys.executable, "-m", "http.server", "8765", "--bind", "127.0.0.1"],
        cwd=outputs_root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        import time

        time.sleep(2)
        for name, build in TEMPLATES.items():
            scene = build()
            problems = te.check_scene(scene)
            te.write_scene(scene, out_dir / f"{name}.glb")
            summary = te.scene_summary(scene)

            page = (
                "http://127.0.0.1:8765/_viewer_lib/scene_recorder.html"
                f"?model=../{relative}/{name}.glb"
                f"&label={name}"
                f"&note={summary['props']}+props+%C2%B7+"
                f"{len(problems)}+problem(s)"
                f"&frames={frames}"
            )
            subprocess.run(
                [str(turntable), "--url", page,
                 "--out", str(videos / f"{name}.mp4"),
                 "--frames", str(frames), "--fps", "30"],
                check=False,
            )
            written = videos / f"{name}.mp4"
            size = written.stat().st_size if written.is_file() else 0
            print(f"{name:<12} {summary['props']:>3} props  "
                  f"{len(problems)} problem(s)  {size / 1024:>7.0f} KB")
    finally:
        server.terminate()
        server.wait(timeout=5)

    print(f"\nvideos: {videos.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    if "--video" in sys.argv:
        sys.argv.remove("--video")
        raise SystemExit(record_videos())
    unittest.main(verbosity=2)
