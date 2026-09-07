# Generate 3D Scene — Strategy Skill

Choose how to build a `3d_scene` asset. Do not default to one pipeline for every
task: what the user specified about the scene's appearance matters more than the
scene type, and generative reconstruction is the least stable route here.

## Decision

Ask first: **did the user specify the scene's 3D appearance?**

| Situation | Prefer | Why |
|---|---|---|
| **Appearance not specified** — no reference image, no described look | **Downloadable, licence-checked assets.** Search the selected engine's own asset library for a usable scene or environment kit: UE5 (Fab / Marketplace, Quixel), Unity (Asset Store, packages), Godot (Asset Library), Blender (bundled assets, CC0 libraries), three.js (curated CC0 packs — see `<REPO_PATH>/agent_skills/engine_context/three_js_api.md`) | Nothing constrains the look, so a licensed, artist-made scene is more shippable than anything generated, and it imports through a documented path. Record source and licence |
| **Appearance specified** — reference image or a described look | **Lay out the terrain/ground first, then add foreground objects** (see *Ground first, then objects* below, and *Scene construction code chain* for the mechanised form) | WorldPlay-style reconstruction is **not yet stable enough** to be the default. Ground + placed props is controllable, editable, and reproduces a requested look reliably |
| **Appearance specified, the space is indoor/enclosed, and the user needs high fidelity** | WorldPlay-style reconstruction | This is the one case where the reconstruction path earns its instability: an enclosed volume has no horizon or sky to break, and one reference image can carry the whole interior |

Prefer downloadable assets even when the appearance is specified, if a library
scene already matches the requested look — generation is not a goal in itself.

Do not silently switch strategies. State which row you selected and why, and if
the user asked for the reconstruction path outside the indoor high-fidelity case,
say that it is the unstable route before spending time on it.

### If the user is unhappy with scene consistency

Reconstruction-based scenes commonly come back inconsistent: geometry drifting
between frames, depth stretched at occlusion boundaries, sky or background pulled
into a curtain, holes and non-continuous surfaces, and a look that shifts across
the scene. This is a known limitation of the current generate-then-reconstruct
chain, not a misconfiguration.

When the user raises it, **briefly summarise the cause and offer the way out** —
do not keep re-rolling the same generation:

1. name the failure in one or two sentences (what is inconsistent, and that it
   comes from video/depth reconstruction rather than a bad setting);
2. recommend switching to ground-first composition, or to a downloadable library
   scene, so the look is authored rather than inferred;
3. keep only the parts of the reconstruction that were good, if any, as a
   reference for the layout.

## Scene type still decides the geometry strategy

Once the route is chosen, classify the space. If the task packet does not say,
infer from the reference image and requirement text: visible enclosing walls and
a finite volume → closed; ground that extends to the horizon or an open sky →
open.

| Scene type | Geometry strategy | Why |
|---|---|---|
| Closed / indoor / bounded (rooms, corridors, arenas with walls) | WorldPlay-style reconstruction, when the fidelity bar justifies it | One reference image can become multi-view footage, then a coherent scene mesh |
| Open / outdoor / unbounded (fields, roads, city blocks, terrain) | Base plane or terrain + place objects | Horizon and sky break depth-to-mesh; composition from ground + props is more controllable |

## Closed scenes — WorldPlay / point-cloud → mesh

Use when the playable space is enclosed, high fidelity is required, and most of
the geometry should come from one visual reference.

Typical chain in this repo:

1. Reference image (+ optional prompt / camera pose) → WorldPlay video frames
2. Frames → WorldMirror depth / point cloud
3. Point cloud → continuous mesh (`<REPO_PATH>/operators/gen_3d_scene`, sky cull + tangent-plane faces)
4. Export GLB / PLY under the `3d_scene` output path

When to use this path:

- Interior rooms, caves, tunnels, small arenas with clear walls/ceiling **and** a
  high-fidelity requirement — this is the only case where it is the recommended
  default
- The reference already shows the layout the player should inhabit
- You need a single fused scene mesh rather than separately authored props

Watch-outs:

- Stability: this chain is the least reliable route in this Skill. Expect
  inconsistency across the scene and be ready to fall back to ground-first
  composition or a library scene
- Occlusion boundaries and sky/background still need the meshing guards in
  `gen_3d_scene` (sky segmentation, tangent-plane continuity, normal-agreement cull)
- Do not expect clean infinite outdoor horizons from this path

Entry points: `<REPO_PATH>/pipeline/assets_gen/gen_3d_scene/{run,eval,render}.py`,
`<REPO_PATH>/test/test_3D_scene_gen.py`.

## Ground first, then objects — plane / terrain + objects

The **default route whenever the user specified a look**, and the right route for
any large, ground-driven, or reusable-asset world.

Recommended strategy:

1. Establish the terrain/ground first — flat plane, heightmap terrain, or a simple
   road/ground kit mesh — so the scene's footprint and silhouette are settled
   before anything is placed on it
2. Generate or select individual foreground objects with `gen_3d_object`
   (buildings, props, characters, vehicles), or take them from the engine's asset
   library
3. Place those objects on the base surface according to the task layout
   (spawn points, lanes, cover, landmarks)
4. Keep the scene as a composed assembly (ground + instances), not one baked
   WorldPlay mesh of the whole horizon

When to use this path:

- The user described or referenced a look and it is not an indoor high-fidelity case
- Outdoor maps, racing circuits, open battlefields, city blocks with sky
- Layout is defined by gameplay (lanes, spawn areas) more than by one photo
- You need editable / swappable props rather than a single reconstructed shell

Watch-outs:

- Do not feed a wide outdoor reference into WorldPlay and expect a clean
  continuous mesh to the horizon — depth stretching and sky curtains are common
- Prefer explicit ground + object placement over trying to “fix” open-world
  reconstruction with post-filters alone

## Scene construction code chain — greybox first, then detail

The mechanised form of *ground first, then objects*. Use it when the layout
matters — gameplay space, a described site, anything that will be edited or
reviewed before it is dressed — and when you want the arrangement checked before
any generation is paid for.

Two stages, in this order:

1. **Greybox.** Build terrain, place primitives, and validate. Nothing is
   generated, so a wrong layout costs nothing to discard.
2. **Detail.** Swap individual primitives for generated meshes and stage ground
   textures, keeping the placement the greybox already proved.

Four concerns are separate values, so any one can be changed without the others:

| concern | functions in `<REPO_PATH>/operators/gen_3d_scene/funcs/terrain_code_edit.py` |
|---|---|
| terrain | `flat` `hills` `slope` `bowl` `mound` `canyon`, reshaped by `flattened` `levelled_at` `graded` `carved` |
| layout | `grid_spots` `ring_spots` `line_spots` `scatter_spots` `clustered_spots` `winding_spots` `arc_spots` `blocks_spots` `channel_spots`, filtered by `clear_circle` `clear_of` `clear_of_ways` `in_height_band` `on_high_ground` `on_low_ground` `on_slope` `fits` |
| sizing | `uniform_sizes` `varied_sizes` `graded_sizes` `tiered_heights` `stepped_sizes` |
| materials | `GREYBOX_MATERIALS` `swap_material` `MATERIAL_TEXTURES` |

Relief is written as one welded `heightfield`, not a box per tile: a grid of
boxes is a staircase with a vertical wall at every tile edge, which is what
makes a slope read as blocky. Ground shapes are driven by `fractal_noise`
rather than by sine waves, since crossed sines put every crest on a regular
lattice and the layout filters built on them then fall into rows.

### The Two Stages Of A Template

`<REPO_PATH>/operators/gen_3d_scene/funcs/terrain_code_template/` holds two
files, split by question rather than by scene:

| file | question | returns |
|---|---|---|
| `landforms.py` | what is the ground | a `Ground`: terrain plus the measurements taken from it |
| `foreground.py` | what stands on it | `(terrain, props)` |

The ground reports what the foreground needs — where a basin actually bottoms
out, what level a street network was graded to — so each measurement is
derived once, where it is known. A foreground that re-derived them would give
a second answer to the same question.

The foreground returns terrain as well because some of them cut the ground
they stand on: a pad under a hut, a level track. **Every cut has to happen
before any prop is measured**, or a prop is placed against ground that later
moves under it.

Landforms are classified by **landform, not by level name**, because the
terrain dictates the distribution — a settlement in a basin gathers on the
terraces, the same settlement on a ridge follows the high ground.

| landform | ground | distribution |
|---|---|---|
| `plains` | level with a ripple | sparse scatter, no structure |
| `hills` | rolling noise | split by height: towers on ridges, dwellings in hollows |
| `basin` | dished, off-centre low point | hamlets on the terraces, shore, track to water |
| `canyon` | meandering channel | chain along the floor, debris on the walls |
| `walled_town` | plateau with rough flanks | rampart on the rim, radial spokes inside |
| `city` | graded streets, carved river | paving, stacked interchange, crowded quarters |

Start from the nearest landform and override, rather than writing a scene from
nothing.

Typical chain:

```python
from operators.gen_3d_scene.funcs import terrain_code_edit as te
from operators.gen_3d_scene.funcs.terrain_code_template import (
    build_scene, foreground, landforms,
)

scene = build_scene("basin", size=70.0)     # stage 1: the pair
problems = te.check_scene(scene)            # must be empty before going on
te.write_scene(scene, "greybox.glb")

scene = te.swap_mesh(scene, "house-00", "model.glb", height=3.4)   # stage 2
scene = te.swap_material(scene, "wall", [0.5, 0.47, 0.44])
te.stage_scene_textures(project_dir, scene)
```

To keep a landform and re-roll what stands on it, or put one settlement on a
different shape of ground, drive the two stages directly:

```python
ground = landforms.hills(size=90.0, relief=5.0)
terrain, props = foreground.plains(ground)      # the pairing is a default
scene = te.Scene("mixed", terrain, props)
```

### Placement Rules Worth Knowing

- `ground_under` rests a prop on the **lowest ground under its whole
  footprint**, not the height at its centre. A wide base sampled only at the
  centre leaves a corner hanging in the air on any slope.
- `fits` measures the **candidate's own turned footprint**; `clear_of` is
  given a bare position and cannot see how wide a building is. Use `fits` when
  choosing size and position together.
- `pinned` holds a run at one level while the ground falls away — a bridge or
  a flyover deck. `sloped` spreads a climb along a run, for a ramp between
  two levels. A ramp built from `pinned` would be a step.
- Paving is a run of short tiles from `path_tiles` / `paved`, not one long
  box, so it follows a bend and follows the ground. **Grade the ground under
  a whole network to one level** (`graded`) or neighbouring slabs each rest on
  their own patch and step against each other — a slab can sit further above
  its neighbour than the slab is thick, which is a pothole every few metres.
- Order matters when reshaping ground: grade the streets, **then** carve the
  river. In the other order the streets dam the channel at every crossing.

**Do not skip `check_scene`, and do not loosen a template to silence it.** It
reports props off the terrain, props inside one another, sizes that misread
against a 1.8 m person, and duplicate ids that would collapse two nodes on
export. Props that are meant to touch — road segments, a wall run, reeds in
the shallows — share a `group` and are exempt; that is the intended way to
express contact, not a larger tolerance.

Geometry is measured with the writer's own `rotated_bounds` and `euler_matrix`
from `<REPO_PATH>/models/common/glb_writer.py`. A checker that rotates or scales
differently from the writer will pass scenes that are wrong on disk.

Two behaviours worth knowing before using stage 2:

- A `mesh` part is fitted into its box by **one** factor, so it fills only its
  longest axis. Pass `height=` to `swap_mesh` whenever the mesh is not the same
  shape as the block it replaces, or a figure dropped into a wide dais will be
  scaled to the dais's width.
- Terrain dipping below zero (`bowl`, `canyon`) is a landform, not a fault. The
  tiles extend down to a shared floor so the ground stays solid.

Entry points:

- `python -m operators.gen_3d_scene.funcs.terrain_code_edit` — write every
  template as a GLB and print its problem count
- `python <REPO_PATH>/test/test_3d_scene_code.py` — 40 tests, no weights, no GPU,
  no network
- `python <REPO_PATH>/test/test_3d_scene_code.py --video` — record a turntable
  per landform for review

Review artifacts land in `<REPO_PATH>/test_data/outputs/_test_3d_scene_code/`,
recorded by `<REPO_PATH>/test_data/outputs/_viewer_lib/scene_recorder.html`. That
page is separate from `recorder.html`: a site is wide and flat, so it is framed
against both frustum axes and viewed from above, and it does not add a grid or
report sub-zero geometry as a fault.

## Quick checklist

1. Did the user specify the appearance? **No** → search the selected engine's
   asset library for a downloadable, licence-checked scene; record source and
   licence.
2. **Yes** → terrain/ground first, then place foreground objects. For a layout
   that matters, use the *Scene construction code chain*: greybox and
   `check_scene` before anything is generated. Use the WorldPlay reconstruction
   path only for an indoor/enclosed space that needs high fidelity, and say that
   it is the unstable route.
3. Classify closed vs open (from task text / reference) and apply the matching
   geometry strategy.
4. Write artifacts to the paths `<REPO_PATH>/pipeline/common/paths.py` defines for `3d_scene`.
5. Visually check continuity (closed) or placement / scale on ground (open)
   before accepting the asset. For a code-built scene, `check_scene` must be
   empty and the recorded turntable must show the landform it claims.
6. If the user reports inconsistent scene quality, summarise the reconstruction
   limitation and move to ground-first composition or a library scene rather than
   regenerating repeatedly.
