---
name: motion-asset-qa
description: Generate and validate character motion assets using mesh projections, sequential VLM joint estimation, 3D skeleton reconstruction, skinning and motion-guided bone orientation. Applies to unrigged character meshes, procedural motion, retargeting and engine QA; prefer Vibe Motion with Mixamo or model generation as fallbacks.
---

# Motion Generation Skills

How an agent turns a character mesh into a usable animated FBX — and how to
judge whether the result is shippable.

Replace `<...>` path placeholders with actual paths; `<repo_path>` denotes the repository root.

Motion assets are a special category: many clip / mesh / engine formats,
per-character skeletons, and unit conventions that static-mesh QA does not
cover. Prefer the `gen_motion` operator first; when a format or retarget
edge case is outside what the operator already handles, the agent **may
edit the retarget code** (`<repo_path>/operators/gen_motion/funcs/retarget_utils/` and
related steps) rather than inventing a one-off workaround outside the
pipeline.

This skill covers the whole motion chain in 3AGameFactory:

```
character mesh (.glb/.obj/…)
        │
        ▼
   calibrated projections  →  images + calibration.json
        │
        ▼
   VLM topology + per-chain labels  →  annotations.json (intermediate)
        │
        ▼
   3D reconstruction + skinning     →  rig + weights
        │
        ▼
   position/rhythm/IK motion + bone-orientation refinement → motion.bvh
        │
        ▼
   retarget (world-delta)    →  retargeted.fbx + animation.fbx + mapping.json
        │
        ▼
   import (Blender / UE5)    →  engine-ready skeletal asset
```

Entry point: `<repo_path>/pipeline/assets_gen/gen_motion/run.py`.
Operator: `<repo_path>/operators/gen_motion/operator.py`.
Code the agent should read before changing anything: this file, then the
module docstrings under `<repo_path>/operators/gen_motion/funcs/`.

## Prefer Vibe Motion functions

**Use Vibe Motion first.** Compose motion with the functions in
`<repo_path>/operators/gen_motion/funcs/vibe_motion_utils/`: reuse parameterized
skeleton, skinning, trajectory and IK operations across compatible characters.
Prefer this stable, controllable route over repeated prompting: timing, stride,
heading and contact targets are explicit parameters. Numerical rigging and IK need
no learned weights or GPU; the preceding visual-estimation stage uses an explicitly
configured VLM service and may incur API costs.

1. Supply explicit joint-position trajectories, named rhythm events, a rest
   skeleton and IK constraints. Adjust this program before choosing another source.
2. Check bone lengths, ground penetration, contact speed, IK residuals and skin
   weights; replay the result on the target mesh. Fix reproducible function bugs
   in `vibe_motion_utils` and rerun through `GenMotionOperator`.
3. If the topology/action is unsupported or the result still fails QA,
   use **Mixamo/local mocap** or **direct generation**
   (MoMask/Tripo). Record the fallback reason, parameters and source/licence.
   Do not silently replace the requested motion with another preset.

### Entry points and scope

| Function | Use |
|---|---|
| `skeleton.fit_skeleton(mesh, config=...)` | Fit a rig using complete, explicit fitting settings |
| `skinning.skin_mesh(mesh, rig, config=...)` | Generate weights using explicit kernel, bone convention and smoothing settings |
| `motion.build_plan(skeleton, rhythm=..., program=...)` | Validate a complete trajectory program and bind named joint chains |
| `motion.generate_clip(plan)` | Sample positions and solve joint motion through IK |
| `generate_vibe_motion(config=..., mesh=...)` | Produce BVH, joints, targets, residuals and optional rig/skin/GLB artifacts |

The old rotation recipes, presets, built-in skeletons and rotation-blended
`segments` interface have been removed. Express a multi-stage action as one
continuous position program with named events and explicit contact windows.
`program.action` is a label only; it never selects an implementation.

Use `task_type="vibe"` with a `config` object containing:

- `skeleton`: explicit `name`, `names`, `parents` and world-space `rest` positions.
- `rhythm`: `duration`, `fps`, and named `events` in normalized time. Duration times
  fps must be integral; clips include both endpoints, with `duration * fps + 1` frames.
- `program`: `action`, horizontal `forward`, root position curves, named `parts`,
  `solver` settings and `quality` thresholds. Missing settings are errors, not defaults.

Curves contain `keys`, `values`, and an explicit `interpolation` mode: `pchip`,
`hermite`, `linear`, or `smooth`. Hermite additionally requires `slopes`, measured
per normalized time unit. Keys may reference rhythm events, `start`, or `end`.
All spatial scales are supplied explicitly. The coordinate contract is Y-up;
local animation quaternions use wxyz, matching the BVH/GLB adapters.

Supported part operators:

- `position`: world-, root-, or parent-frame end-effector trajectories, explicit
  pole/rest-pole directions and flexion bounds; three- or four-joint chains.
  `orientation` is `body`, `rest`, or an explicit scalar curve of Y-up yaw degrees
  relative to rest. Hold this curve constant during support to lock foot heading.
  Position/contact IK poles use root-body axes; aim poles use the selected target
  space. `rest_pole` always uses the original world-space rest basis.
- `contact_path`: independently timed support windows, explicit anchor sample
  times and one body-frame swing offset per gap. Contact orientation can be locked.
- `arm_arc`: task-space wrist arcs with bounded shoulder/elbow IK. Polar angles
  describe wrist positions, not authored joint rotations. All optimizer settings
  and joint bounds are explicit.
- `aim`: child-joint position tracks for axial chains, preserving bone lengths.
- `rest`: explicitly retain the local rest pose.

Chains are resolved from the supplied skeleton, not from hardcoded human names.
Biped and quadruped examples, including all motion parameters and synthetic
skeleton data, live in `<repo_path>/test/vibe_motion_examples/horse_gallop_stop_kick.json`
and `<repo_path>/test/vibe_motion_examples/turn_jump_chop.json`. The latter encodes flight
as explicit Hermite position keys rather than a built-in jump recipe. Load an
example JSON into `config` and pass it to `GenMotionOperator.run` together with
`task_type="vibe"`, `game_id` and `task_id`.

For a real mesh, supply `target_mesh_path`, `rig_quality` and `export.text`
(`precision`, `sum_tolerance`, `max_influences`). Either provide its skeleton or
use `skeleton=null` with a complete `rigging` config. To generate a skinned GLB,
additionally supply `skinning`, `skin_quality` and `export.glb` (`bind_tolerance`,
`sum_tolerance`, `material`, `interpolation`).
No creature geometry, rig preset or skin preset is loaded automatically. For FBX,
use `vibe_retarget` with the same mesh/skin settings, integer rhythm fps and a bpy
runtime. Mesh tests live in `<repo_path>/test/test_vibe_rigging.py`.

### Required visual rigging workflow

Follow this order for an unrigged model:

**3D model -> projection images -> sequential VLM identification and annotation
-> 3D skeleton -> skinning -> motion-evaluated bone orientation refinement.**

1. **Load the original mesh.** Normalize using the task input and record its
   digest, normalization and actual processed-geometry identity. Do not substitute
   a reference rig, strip a bound asset and call it unrigged, or modify the source.
2. **Render calibrated projections.** Call `rigging_utils.estimation.prepare_projection`.
   Compute the actual camera center, pixel origin and common pixels-per-unit from
   mesh bounds and the requested view axes, image dimensions and padding. Use
   depth-buffered original triangles. Save images and `projection/calibration.json`
   under the run output. Do not ask the VLM to guess camera matrices.
3. **Identify and annotate sequentially.** Call `estimate_rigging` with a real
   vision-capable estimator. Send the actual labeled view images and computed
   calibration, not only filenames or text. First infer parent-child connectivity
   under the requested control-chain constraints; then make one request per chain
   in input order. Pass validated earlier results to the next request. Estimate
   internal joint centers, preserve asymmetry, and record `pixel`, `confidence`,
   and `inferred` per observed view. Omit unsupported views; require at least two
   independent views per joint. Never invent visibility or mirror an occluded limb.
   Save each prompt and raw reply as `estimation_00.json`, `estimation_01.json`, etc.
   Reject refused, truncated, invalid, degenerate or inconsistent replies without
   falling back to old coordinates. Validate the topology, pixel bounds, weighted
   reconstruction rank/condition and every view's reprojection error before advancing.
   Publish `annotations.json` and `resolved_rigging.json` only after all stages pass.
4. **Reconstruct and fit.** Pass the resolved result to `skeleton.fit_skeleton`.
   Fit within the input windows; explicitly report geometry/annotation conflicts.
   Separate closed-convex-manifold certification from open-mesh enclosure and
   heuristic parity evidence. Do not snap joints to skin to manufacture a pass.
5. **Generate skin weights.** Apply caller-selected influence sets and soft priors,
   then screened diffusion on actual mesh edges with exact anchors. Pass these as
   `allowed_bones`, `weight_bias`, `anchors` to `skinning.skin_mesh` or through
   `config.skin_constraints`. Keep `screening` and `edge_epsilon_ratio` explicit;
   never add proximity links across disconnected surfaces. Asset-specific object
   and seam selectors in the example are task constraints, not model predictions.
6. **Adjust bone orientation using the action.** With `--with-motion`, call
   `<repo_path>/test/test_vibe_motion.py` to generate the explicit position/rhythm/IK action.
   Hold skin weights and action settings fixed while comparing bounded rest-pole
   and pre-bend candidates. Accept only improvements that preserve geometric and
   reprojection budgets. Recompute bind matrices when rest joints change. Export
   and read back GLB geometry, weights and animation; preserve failed QA metrics.

Keep `<repo_path>/test/vibe_motion_examples/rigging_example.json` as a short task request:
view-generation settings, requested chain names/meanings, fitting and skinning
parameters, action constraints and tolerances. Requested chain names are an output
schema constraint, not an inferred skeleton. Do not store actual calibration,
estimated pixel coordinates, confidence, inferred topology or generated 3D joints
in this input directory. Store all of those intermediates, raw model replies,
resolved settings, weights, diagnostics and videos under `<repo_path>/test_data/outputs/`.
Retain old estimates only as clearly identified historical output; never label a
replay or a test stub as a fresh VLM run. VLM estimates are not anatomical truth.

### Run or inspect the estimation stage

Use the existing CLI, keeping each command on one shell line:

- Prepare images without a network call: `python -m test.test_vibe_rigging rig-mesh
  --input "<mesh_path>" --output-dir "<output_dir>" --prepare-only`.
  Report this as projection preparation, not completed estimation or rigging.
- Run real estimation and the downstream action: `python -m test.test_vibe_rigging
  rig-mesh --input "<mesh_path>" --output-dir "<output_dir>"
  --with-motion --ffmpeg "<ffmpeg_path>"`. Optionally supply
  `--config "<repo_path>/test/vibe_motion_examples/rigging_example.json"` with a short
  request. The CLI adds only the test-side QA/export settings already declared in
  `<repo_path>/test/test_vibe_rigging.py`; production functions do not supply asset defaults.
- Explicitly replay a completed estimate: add `--annotations "<annotations_path>"`.
  Substitute the actual completed annotation file path; no fixed parent directory
  is required. Keep its sibling `projection/` artifacts and choose a fresh
  `<output_dir>`. Verify source/processed mesh digests, normalization, task,
  projection settings, calibration and image hashes. Changing a model or a task
  requires new estimates. This is replay, not a new model call.

Configure `VIBE_VLM_BASE_URL` (HTTPS OpenAI-compatible API base including `/v1`
when required), `VIBE_VLM_MODEL` (a vision model supporting JSON-object chat output),
and `VIBE_VLM_API_KEY`. Override the first two with `--vlm-base-url`/`--vlm-model`;
use `--vlm-key-env` for an existing credential variable. Do not choose an arbitrary
provider/model, put keys in JSON, or print credential values. API transport uses
`models.common.cloud_api` and requires `requests`; projections require Pillow.
Obtain any necessary permission before uploading local projections or incurring
API costs. A normal API run uses one topology request plus one request per chain.
If configuration is absent, stop with an actionable error or use prepare-only;
do not claim real vision validation from offline tests. Keep partial replies on
failure and rerun into a new output directory after correction. Never overwrite
prior outputs. A downstream quality failure exits nonzero while retaining artifacts.

The numerical runtime requires NumPy and SciPy; input mesh files additionally
require trimesh. No external source checkout, model weights or GPU is needed.
Run the motion regression suite with
`python -m unittest discover -s "<repo_path>/test" -t "<repo_path>" -p 'test_vibe_motion.py' -v`.

To export skeleton test videos, use
`python -m test.test_vibe_motion export-videos --ffmpeg "<ffmpeg_path>"`.
This explicit preview mode requires Pillow, leaves ordinary tests artifact-free,
and writes MP4 files and numerical reports under
`<repo_path>/test_data/outputs/_GPT6_astra_test/vibe_motion_refine260927`.
Use `--output-dir` to select a different test output location. These skeleton
previews are not skinned-character or physical-simulation validation.

Inspect `vibe_report_path` and, for mesh tasks, `skin_report_path` and
`rig_report_path`. Reports retain the full program, event timing, original-target
residuals, contact speed, bone-length error, ground penetration and optimizer
failures. Quality thresholds come from the input. Unreachable targets are
projected under joint limits and reported, never hidden by stretching bones.
These are kinematic checks, not balance, self-collision or visual-quality proof;
replay the animation on the actual mesh before accepting it.

### Fallback routes

- **Mixamo / local mocap:** choose when a matching clip is available. Download
  Mixamo FBX Binary, **Without Skin**, then use `task_type=retarget`,
  `motion_source=mixamo` and initially `global_scale=0.01` for centimetre clips.
- **MoMask / Tripo:** choose for a missing library motion or an accepted placeholder;
  check the generated pose, timing, contacts and looping before delivery.
- Respect access restrictions and licences; use manual downloads for login-gated
  sources. Record provenance for every fallback.

## Cloud fallback (Tripo)

`task_type=cloud_rig` / `cloud_humanoid` runs rigging and animation on the
TokenHub / Tripo backend: no weights, no Blender, no BVH step.
Code: `<repo_path>/operators/gen_motion/funcs/cloud_rig_animate.py`,
`<repo_path>/models/gen_motion/tripo_rigging_model.py`.

Use this route only after Vibe Motion is unsuitable or fails QA. Rigging can
vary between attempts, and animation uses a fixed preset library. Review the
rig and clip on the target mesh rather than assuming a successful API call
proves motion quality.

Constraints to plan around:

- `input` takes a **public http(s) URL** only; no upload endpoint, `data:` URIs
  are rejected.
- Animation chains off the rigging **task id** (expires in 24 h), not the file.
- `spec="mixamo"` cannot be animated — animate with `spec="tripo"`.
- `rig_type` (biped / quadruped / hexapod / octopod / avian / serpentine /
  aquatic) should come from a `rig-check` call; the preset must match it.
- Every call is billed, including refused meshes and extra `attempts`.

Gate the result before shipping: `inspect_rig` (limb chains resolved) and
`inspect_animation` (joints not flipped past 150°). Both are in
`<repo_path>/models/gen_motion/tripo_rigging_model.py`; the operator writes them next to the artifact.

## When To Run

- A task asks for a humanoid character that moves (walk, attack, idle, …).
- You have a downloaded Mixamo / mocap clip and need it on a generated rig.
- A generated clip looks wrong and you need a downloaded replacement.
- You have a retargeted FBX and need to prove Blender or Unreal can use it.

Do **not** use the static mesh importers
(`<repo_path>/engine_adapters/blender/import_generated/import_mesh.py` or
`<repo_path>/engine_adapters/ue5/import_generated/import_mesh.py`) on a motion FBX —
they join meshes and drop armatures, which destroys the animation.

## Formats (why motion is special)

| Stage | Common formats | Notes |
|---|---|---|
| Character mesh | `.glb` `.gltf` `.obj` `.ply` `.stl` (`.fbx` at retarget) | Vertex order must match the Puppeteer rig OBJ |
| Motion clip | `.bvh` `.fbx` | Mixamo FBX often cm-scale; MoMask BVH is metre-ish @ 20 fps |
| Mapping | JSON bone map | Derived per Puppeteer rig; not reusable across characters |
| Engine out | `.fbx` (full + anim-only) | Blender / UE skeletal import — not static mesh |

Skeleton naming also differs by library (Mixamo `mixamorig:*`, UE mannequin
`pelvis` / `*_l`, CMU helpers, SMPL off-by-one names). Source profiles live
in `mapping_presets.SOURCE_SKELETONS`; identification for BVH is host-side,
FBX needs bpy / `mapping_auto`.

If the operator cannot ingest a legitimate clip format, scale convention, or
retarget quirk the task needs, extend `fetch_motion`, `formats`,
`mapping_auto`, or `world_delta` in-repo and keep the task on the pipeline
path — do not bypass with a hand-rolled Blender script that never lands in
`<repo_path>/operators/`.

## Task Types

| `task_type` | Needs | Produces |
|---|---|---|
| `vibe` | preset or segments + template or compatible mesh/creature | BVH, joints, QA report; rig/OBJ/skin report with geometry |
| `vibe_retarget` | preset or segments + compatible geometry + skin + bpy | Vibe artifacts + retargeted FBX/animation/mapping |
| `rig` | character mesh | `rig.txt`, `skeleton.txt`, `mesh.obj` |
| `text_to_motion` | text prompt | `motion.bvh` (+ raw/ik/preview) |
| `retarget` | source clip + mesh + rig | `retargeted.fbx`, `animation.fbx`, `mapping.json` |
| `humanoid` | mesh + prompt | all of the above, chained |
| `cloud_rig` | `mesh_url` | rigged mesh + `rig_report.json` |
| `cloud_humanoid` | `mesh_url` + preset | rigged + animated mesh + reports |

CLI demo (single task). Load the runtime first and pass the explicit model
arguments shown in [Runtime Environment](#6-runtime-environment)::

```bash
# Fallback: retarget a Mixamo download onto an existing rig
python "<repo_path>/pipeline/assets_gen/gen_motion/run.py" \
  --task-type retarget \
  --source-motion "<source_motion_path>" \
  --target-mesh "<mesh_path>" \
  --target-rig "<rig_path>" \
  --motion-source mixamo \
  --global-scale 0.01

# Full chain with generated motion: mesh → rig → MoMask → FBX
python "<repo_path>/pipeline/assets_gen/gen_motion/run.py" \
  --task-type humanoid \
  --target-mesh "<mesh_path>" \
  --prompt "A person walks forward and waves." \
  --in-place
```

Registries (no models, no Blender)::

```bash
python "<repo_path>/pipeline/assets_gen/gen_motion/run.py" --list-mappings
python "<repo_path>/pipeline/assets_gen/gen_motion/run.py" --list-motion-sources
```

## 1. Rigging

Prefer Vibe `fit_skeleton` + `skin_mesh` for compatible geometry. Use the
Puppeteer route below when procedural fitting does not meet the task.

**Model:** `<repo_path>/models/gen_motion/puppeteer_model.py` (CUDA required for real runs).
**Step:** `<repo_path>/operators/gen_motion/funcs/rig_character.py`.

Accepted mesh formats: `.glb`, `.gltf`, `.obj`, `.ply`, `.stl` (and `.fbx` at
retarget time). The operator accepts both `target_mesh_path` and the legacy
`target_glb_path` key.

**Contract that must not break:** Puppeteer's `skin` lines address vertices by
index in the mesh it consumed. The rig artifacts therefore include that exact
OBJ. Retargeting binds weights against the same vertex order — any conversion
that reorders vertices between rig and retarget silently ruins the skin.

Stub-test without CUDA: inject `StubPuppeteerModel` from `<repo_path>/test/harness/stubs.py`.

## 2. Motion Generation

**Model:** `<repo_path>/models/gen_motion/momask_model.py`.
**Step:** `<repo_path>/operators/gen_motion/funcs/generate_motion.py`.

Use MoMask as a fallback after [Vibe Motion](#prefer-vibe-motion-functions)
cannot meet the task after parameter tuning and QA. Choose a matching mocap clip
instead when it offers the required performance.

- Native rate is **20 fps**. Pass that through to retarget; exporting a 20 fps
  clip as 30 fps plays too fast without looking "broken".
- Prefer HumanML3D-style sentences ("a person walks forward and waves"), not
  tag lists.
- `in_place=True` when the game drives locomotion and the clip only has to
  look like walking.
- Do not expect prompt-level control over timing, style, foot contact, or
  looping. If the plan needs a specific performance, download it instead of
  re-rolling seeds.

<a id="when-generation-quality-is-not-enough"></a>

### Motion sources (fallback after Vibe Motion)

Use `<repo_path>/operators/gen_motion/funcs/fetch_motion.py` instead of fighting the prompt.

| Source | Access | Skeleton | Notes |
|---|---|---|---|
| `mixamo` | manual (login) | Mixamo | Preferred library fallback; download FBX Binary, Skin=Without Skin |
| `mocap_online` | manual | UE5 mannequin | Free sample packs |
| `cmu_bvh` | direct URL | CMU BVH | Free; quality uneven |
| `bandai_namco` | direct URL | — | CC BY-NC-ND — research only |
| `local` | path on disk | identified if BVH | Escape hatch |

Login-gated sources **refuse to be scraped** (`PermissionError` with download
instructions). That is intentional: scraping Mixamo violates the licence.

Always record provenance (`*_motion_source.json`). A retargeted FBX looks the
same whether it came from MoMask or Mixamo; "can we ship this" is asked later.

**Units:** Mixamo is centimetres → start with `global_scale=0.01` against a
metre-scale Puppeteer rig. Prefer
`fetch_motion.suggest_global_scale(clip, rig)` for BVH; it measures both
skeletons. Wrong scale does not break the pose — the character moon-walks or
vibrates in place, which is why it survives visual review.

Task fields for an external clip::

```json
{
  "task_type": "retarget",
  "motion_source": "mixamo",
  "source_motion_path": "<source_motion_path>",
  "target_mesh_path": "<mesh_path>",
  "target_rig_path": "<rig_path>",
  "global_scale": 0.01,
  "fps": 30
}
```

## 3. Retargeting And Bone Mapping

**Host driver:** `<repo_path>/operators/gen_motion/funcs/retarget_motion.py`.
**Blender package:** `<repo_path>/operators/gen_motion/funcs/retarget_utils/`.

| Module | Runs in | Role |
|---|---|---|
| `validate_mapping` | any Python | reject a bad mapping early |
| `mapping_presets` | any Python | source-skeleton registry (clip-side names) |
| `mapping_auto` | bpy | derive a mapping from topology |
| `world_delta` | bpy | retarget + FBX export |
| `rig_io` | bpy | Puppeteer `.txt` → armature |
| `inspect_fbx` | bpy | prove the FBX animates after re-import |

### Why mapping is usually derived, not reused

Puppeteer names joints `joint0…jointN` in **prediction order**. Those names
carry no anatomy: `joint23` is hips on one character and a finger on the next.
A bone map is therefore only valid for the single rig it was written for — this
repo does **not** ship Mixamo/MoMask → Puppeteer preset JSONs.

What *is* reusable is the **source** half (Mixamo always uses
`mixamorig:Hips`). That lives in `SOURCE_SKELETONS` inside
`<repo_path>/operators/gen_motion/funcs/retarget_utils/mapping_presets.py`. Omit mapping and let `mapping_auto` derive a map, or pass
an explicit `mapping_path` / `--mapping` for a one-off.

Default path when the task names no mapping: auto-generate → write
`mapping.json` next to the FBX → run world-delta twice (full + anim-only).

### When the operator cannot cover a retarget case

Motion retarget has many legitimate edge cases (odd BVH hierarchies, engine
axis packs, IK feet, non-humanoid props, new mocap libraries). If
`mapping_auto` / `world_delta` / import fails for a real asset and the gap is
in our code — not bad input — the agent should **patch the retarget stack**
under `<repo_path>/operators/gen_motion/funcs/` (and tests under `<repo_path>/test/test_gen_motion.py`
/ `<repo_path>/test/test_rigging_retarget.py`) so the next run goes through the operator.
Keep format constants in `<repo_path>/operators/gen_motion/funcs/retarget_utils/formats.py` in sync with fetch /
rig / CLI validation.

### Mapping JSON shape

```json
{
  "root_bones": {"source": "mixamorig:Hips", "puppeteer": "joint0"},
  "bone_map": {"mixamorig:Hips": "joint0", "...": "..."},
  "retarget_chains": {
    "spine": {"source": [...], "puppeteer": [...]},
    "left_arm": {"source": [...], "puppeteer": [...]},
    "right_arm": {"source": [...], "puppeteer": [...]},
    "left_leg": {"source": [...], "puppeteer": [...]},
    "right_leg": {"source": [...], "puppeteer": [...]}
  }
}
```

Legacy keys `mixamo` / `target` are normalised on load.

## 4. Import Into Engines

### Blender (verified on this repo's bpy 4.2 wheel)

```bash
# Via host launcher
python "<repo_path>/scripts/import_generated_asset.py" \
  --src "<retargeted_fbx_path>" \
  --engine blender --kind motion \
  --blender "$A3GF_RETARGET_BPY_PYTHON"

# Or call the importer directly
python "<repo_path>/engine_adapters/blender/import_generated/import_motion.py" \
  --src "<retargeted_fbx_path>" --dest "<output_dir>" --name Walk --report "<output_dir>/report.json"
```

`ok=True` requires: armature + action + keyframes + **pose change** (root
travel alone is not enough — a sliding T-pose would otherwise pass).

Also useful for a quick structural check without the full import path::

```bash
"$A3GF_RETARGET_BPY_PYTHON" \
  -m operators.gen_motion.funcs.retarget_utils.inspect_fbx \
  --input "<retargeted_fbx_path>" --output "<output_dir>/fbx_inspection.json"
```

Look for `pose_animated=true`, `skinned=true`, `height_m ≈ 1.5–2.0` for a
humanoid.

### Unreal Engine 5

UE is not available in every CI box; the importer is ready for a machine that
has an editor::

```bash
python "<repo_path>/scripts/import_generated_asset.py" \
  --src "<retargeted_fbx_path>" \
  --engine ue5 --kind motion \
  --uproject "<uproject_path>" \
  --ue-motion-dest /Game/Generated/Motion

# Anim-only FBX onto an existing Skeleton
python "<repo_path>/scripts/import_generated_asset.py" \
  --src "<animation_fbx_path>" \
  --engine ue5 --kind motion --ue-anim-only \
  --ue-skeleton "<ue_skeleton_asset_path>" \
  --uproject "<uproject_path>"
```

Engine script: `<repo_path>/engine_adapters/ue5/import_generated/import_motion.py`.
It forces `import_as_skeletal=True` and `import_animations=True` — the static
`<repo_path>/engine_adapters/ue5/import_generated/import_mesh.py` path must not be used here.

After import, confirm in Content Browser:

1. A `SkeletalMesh` (full FBX) or only an `AnimSequence` (anim-only).
2. A `Skeleton` asset, or the animation targeting the `--ue-skeleton` you named.
3. Play the AnimSequence in the asset editor — the pose must change, not just
   the root.

Higher-level UE client: `ue.animation.import_motion(...)` in
`<repo_path>/engine_adapters/ue5/animation/client.py`.

### Godot 4

Use the public Client with the generated task descriptor; it stages the FBX or
glTF/GLB under `res://` and requires a successful real Godot `--import` run:

```python
from engine_adapters.godot import GodotClient

godot = GodotClient(
    project_path="<godot_project_path>",
    godot_executable="<godot_executable_path>",
)
result = godot.animation.import_motion(
    {
        "game_id": "my_game",
        "run_id": "run_001",
        "task_kind": "motion",
        "task_id": "walk",
        "artifact_key": "retargeted_fbx_path",
    },
    skeleton="Character/Armature/Skeleton3D",
)
```

`result.ok` proves Godot 4 loaded the imported resource, found an animation, a
Skeleton3D and a bone-targeted track, and matched the requested live Skeleton3D
path before registration. glTF/GLB motion remains a `PackedScene`; the adapter
does not mislabel it as `AnimationLibrary`. Run the Blender structural check
first, then inspect/play the imported animation in Godot: these native checks do
not prove a good visible pose or complete retargeting quality.

## 5. Quality Checklist (What Code Cannot Decide Alone)

Run these after `inspect_fbx` / Blender import report `ok=True`:

1. **Pose, not just root.** Legs and arms swing. A character that only translates
   while holding a T-pose means the bone map dropped limb chains.
2. **Sides.** Left arm must not drive the right. Auto-mapping uses world-X sign;
   if the source was mirrored, pass `--left-sign` / re-derive.
3. **Feet.** For Vibe, inspect contact targets and IK residuals, then correct the
   parameters or transition. Use MoMask IK (`use_ik=True`) or mocap as fallback.
4. **Scale.** Humanoid height ≈ 1.6–2.0 m after import. Check source units before
   setting `global_scale`; Vibe BVH uses metres, not Mixamo centimetres.
5. **Facing.** Vibe BVH preserves its template axes (Y-up, +Z forward by default).
   Verify exported FBX and engine facing separately; record any correction (see
   `<repo_path>/agent_skills/asset_qa/3d_object/orientation_review.md`).
6. **Licence.** Check the model, dataset, and source-motion terms before
   shipping. Mixamo / MoCap Online / Bandai each have separate terms; retain
   `*_motion_source.json` with the artifact.

## 6. Runtime Environment

For Vibe-only BVH generation, use the Python/NumPy source setup above; no model
weights or GPU environment is needed. Configure bpy additionally for FBX retargeting.
Install the following Linux environments only for the model-backed fallback and
retarget routes:

```bash
bash "<repo_path>/scripts/asset_env_setup/gen_motion/install.sh"

# Install sources and environments only; download weights later if needed.
bash "<repo_path>/scripts/asset_env_setup/gen_motion/install.sh" --skip-weights

source "<repo_path>/scripts/asset_env_setup/gen_motion/runtime_env.sh"
```

The installer creates `gamefactory3a-puppeteer`, `gamefactory3a-momask`, and
`gamefactory3a-retarget-bpy`. `<repo_path>/scripts/asset_env_setup/gen_motion/runtime_env.sh` exports:

- `A3GF_PUPPETEER_MODEL_PATH`
- `A3GF_PUPPETEER_PYTHON`
- `A3GF_MOMASK_MODEL_PATH`
- `A3GF_MOMASK_PYTHON`
- `A3GF_RETARGET_BPY_PYTHON`

Pass them explicitly to the pipeline so the command does not depend on legacy
environment-variable aliases:

```bash
python "<repo_path>/pipeline/assets_gen/gen_motion/run.py" \
  --task-type humanoid \
  --target-mesh "<mesh_path>" \
  --prompt "A person walks forward and waves." \
  --puppeteer-model-path "$A3GF_PUPPETEER_MODEL_PATH" \
  --puppeteer-python "$A3GF_PUPPETEER_PYTHON" \
  --momask-model-path "$A3GF_MOMASK_MODEL_PATH" \
  --momask-python "$A3GF_MOMASK_PYTHON" \
  --bpy-python "$A3GF_RETARGET_BPY_PYTHON" \
  --in-place
```

Tests::

```bash
# Unit + stub integration (no GPU)
python -m unittest test.test_gen_motion

# Create an unlicensed, single-mesh T-pose fixture for a real local run.
"$A3GF_MOMASK_PYTHON" \
  "<repo_path>/scripts/asset_env_setup/gen_motion/create_humanoid_glb.py" \
  "<output_dir>/humanoid.glb"
```

Synthetic humanoid fixture (mesh + Mixamo-named BVH + matching Puppeteer
rig), for local repro without licensed assets. Use
`<repo_path>/test/test_rigging_retarget.py` from the repository root:

```python
from test.test_rigging_retarget import build_all
build_all("<output_dir>", mesh_format=".glb")
```

## 7. What An Agent Should Do, In Order

1. Read this skill and the required functions in `vibe_motion_utils`.
2. Try Vibe Motion first: choose a compatible plan, generate or compose the motion,
   tune parameters and inspect numerical reports plus target-mesh playback.
3. Fix function defects in-repo and rerun. If Vibe remains unsuitable or fails QA,
   select Mixamo/local mocap or direct generation and record the fallback reason.
4. Run retarget through the operator when FBX is required. Derive the bone map for
   the actual source and target; do not reuse an unrelated character's mapping.
5. Validate the exported pose with `inspect_fbx` or Blender motion import, then
   inspect playback in the target engine. Do not treat root travel as limb motion.
6. Save source, parameters, reports, licence, facing and scale beside the artifact.
