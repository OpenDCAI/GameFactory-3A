# Generate 3D Scene — Strategy Skill

Choose how to build a `3d_scene` asset. Do not default to one pipeline for every
task: what the user specified about the scene's appearance matters more than the
scene type, and generative reconstruction is the least stable route here.

## Decision

Ask first: **did the user specify the scene's 3D appearance?**

| Situation | Prefer | Why |
|---|---|---|
| **Appearance not specified** — no reference image, no described look | **Downloadable, licence-checked assets.** Search the selected engine's own asset library for a usable scene or environment kit: UE5 (Fab / Marketplace, Quixel), Unity (Asset Store, packages), Godot (Asset Library), Blender (bundled assets, CC0 libraries), three.js (curated CC0 packs — see `<REPO_PATH>/agent_skills/engine_context/three_js_api.md`) | Nothing constrains the look, so a licensed, artist-made scene is more shippable than anything generated, and it imports through a documented path. Record source and licence |
| **Appearance specified** — reference image or a described look | **Lay out the terrain/ground first, then add foreground objects** (see *Ground first, then objects* below) | WorldPlay-style reconstruction is **not yet stable enough** to be the default. Ground + placed props is controllable, editable, and reproduces a requested look reliably |
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

## Quick checklist

1. Did the user specify the appearance? **No** → search the selected engine's
   asset library for a downloadable, licence-checked scene; record source and
   licence.
2. **Yes** → terrain/ground first, then place foreground objects. Use the
   WorldPlay reconstruction path only for an indoor/enclosed space that needs high
   fidelity, and say that it is the unstable route.
3. Classify closed vs open (from task text / reference) and apply the matching
   geometry strategy.
4. Write artifacts to the paths `<REPO_PATH>/pipeline/common/paths.py` defines for `3d_scene`.
5. Visually check continuity (closed) or placement / scale on ground (open)
   before accepting the asset.
6. If the user reports inconsistent scene quality, summarise the reconstruction
   limitation and move to ground-first composition or a library scene rather than
   regenerating repeatedly.
