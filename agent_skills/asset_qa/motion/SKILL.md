---
name: motion-asset-qa
description: Generate and validate character motion assets with Vibe Motion functions first, using Mixamo or model generation as fallbacks. Applies to rigging, skinning, procedural clips, retargeting and engine motion QA.
---

# Motion Generation Skills

How an agent turns a character mesh into a usable animated FBX — and how to
judge whether the result is shippable.

Motion assets are a special category: many clip / mesh / engine formats,
per-character skeletons, and unit conventions that static-mesh QA does not
cover. Prefer the `gen_motion` operator first; when a format or retarget
edge case is outside what the operator already handles, the agent **may
edit the retarget code** (`<REPO_PATH>/operators/gen_motion/funcs/retarget_utils/` and
related steps) rather than inventing a one-off workaround outside the
pipeline.

This skill covers the whole motion chain in 3AGameFactory:

```
character mesh (.glb/.obj/…)
        │
        ▼
   rig  (Vibe / Puppeteer)   →  rig.txt + skeleton.txt + mesh.obj
        │
        ▼
   motion (Vibe first)       →  motion.bvh; Mixamo / MoMask as fallbacks
        │
        ▼
   retarget (world-delta)    →  retargeted.fbx + animation.fbx + mapping.json
        │
        ▼
   import (Blender / UE5)    →  engine-ready skeletal asset
```

Entry point: `<REPO_PATH>/pipeline/assets_gen/gen_motion/run.py`.
Operator: `<REPO_PATH>/operators/gen_motion/operator.py`.
Code the agent should read before changing anything: this file, then the
module docstrings under `<REPO_PATH>/operators/gen_motion/funcs/`.

## Prefer Vibe Motion functions

**Use Vibe Motion first.** Compose motion with the functions in
`<REPO_PATH>/operators/gen_motion/funcs/vibe_motion_utils/`: reuse parameterized
skeleton, skinning, trajectory and IK operations across compatible characters.
Prefer this stable, controllable route over repeated prompting: timing, stride,
heading and contact targets are explicit parameters, with no learned weights or GPU.

1. Select a preset and a compatible skeleton; adjust parameters or compose the
   existing functions before choosing another source.
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
| `skeleton.fit_skeleton`, `skinning.skin_mesh` | Fit a rig and vertex weights to a mesh |
| `motion.build_plan`, `motion.generate_clip` | Generate a preset on a skeleton plan |
| `motion.concatenate_clips` | Join clips sharing the same template and fps |
| `motion_utils` | Local motion primitives: `rotate_joint`, `solve_two_bone`, `fit_feet`, `swing`, `strike` |
| `rigging_utils` | Local cross-section fitting, weight generation and LBS functions |
| `generate_vibe_motion` / `GenMotionOperator.run` | Produce BVH, joints, optional rig/OBJ and QA reports |

Use `task_type="vibe"` with `preset`, `num_frames`, `fps`, `heading_deg` and
`motion_overrides`. Supply either `target_mesh_path` or `creature="human"`;
with neither, use `template="biped_armed"` for a clip-only task. With geometry and
`skin=true`, `vibe` also returns `animated_glb_path`: a skinned animation that can
be previewed directly without Blender. Inspect that GLB on the target mesh.
For FBX, use `vibe_retarget` with geometry, `skin=true`, positive integer fps and
a configured bpy runtime; confirm the exported FBX with the import checks below.

Current motion presets: `walk`, `stride`, `boxing`, `jab`, `chop`, `turn_jump_chop`,
`turn_jump_chop_tuned`, `task_space`
(plus aliases such as `walking`, `step`, `punch`, `slash`). These presets cover
**biped humanoids**, not arbitrary
creature motion. `biped` supports walk/stride; arm actions require `biped_armed`.
Standalone skeleton fitting also supports `quadruped` and `axial`; do not treat that
as support for their gait generation. Clip concatenation does not preserve foot
contact through transitions. Numerical checks do not prove visual quality.

New distance-generated skin weights default to `bone_convention="outgoing"` so
upper-arm vertices follow the shoulder pivot, not the elbow. To reproduce legacy
weights, pass `skin_overrides={"bone_convention": "incoming"}`. This does not remap
artist-authored weights. The skin report records the convention; mesh-ground and
triangle-intersection review are still needed beyond skeleton-only metrics.

`turn_jump_chop` uses geometric flight. Optional `torso_twist_deg` (0–25),
`torso_lean_deg` (0–18) and `arm_clearance` (0–0.1 of skeleton scale) default to zero.

Use `preset="turn_jump_chop_tuned"` for a ballistic jump with task-space
wind-up/accelerating strike/recovery for both arms, outward elbow poles, wrist-led
blade direction, ballistic root flight, velocity-matched takeoff/landing and foot
tuck with planted contacts. It accepts `turn_deg`, `travel`, `takeoff_ratio`,
`jump_height`, `gravity_ratio`, `crouch`, `landing_crouch`, `torso_twist_deg`,
`torso_lean_deg`, `hand_clearance`, `strike_reach`, `foot_tuck`; see template defaults.
`hand_clearance` is in arm lengths, unlike the legacy `arm_clearance` in skeleton
scale. Do not mix old angle or landing/strike-ratio parameters into this preset:
they are rejected. Reports record derived seconds and gravity under `timing`.
Duration/sampling must accommodate the flight and recovery; impossible requests
raise rather than silently changing fps or timing. Blade control requires a wrist
attachment. This is analytic animation, not whole-body dynamics or collision solving.

For three-joint arms (shoulder/elbow/wrist), pass `rig_overrides={"motion_ready": False}`; the motion
adapter reuses the existing chest parent as role metadata without adding joints.
The historical default still fits four arm joints, 21 total. Different elbow
pivots can change skin collisions substantially: compare on the SAME rig/weights,
and inspect actual mesh intersections, not only skeleton QA. Neither topology
is universally collision-free; the procedural fixture can still fail QA.

For explicit comparison exports, call `export_refinement_preview` in
`test/test_vibe_motion.py` with `output_dir`, `mesh_path`, `stage` and `task_inputs`.
`MOTION_COMPARE_TASKS` defines `legacy_motion`, `partial_motion`, `full_motion` at
96 frames/30 fps with fixed outgoing weights. Compare stages once on the default
rig and again with the same 19-joint rig. All action settings come from task_inputs;
stage only names the output folder. Paths are repository-relative. Existing folders
are not overwritten. Normal unit tests do not write visualizations.

Use Python 3.10+ with NumPy; add trimesh for input mesh files. Implementations
live in `vibe_motion_utils/motion_utils/` and `vibe_motion_utils/rigging_utils/`;
no external source checkout, source-root environment variable or model weights are
needed. `creature` creates an untextured procedural fixture, not a downloaded
character. Use `target_mesh_path` for the actual game character. For example,
run from the repository root:

```python
from operators.gen_motion.operator import GenMotionOperator

result = GenMotionOperator(run_id="vibe_qa").run({
    "game_id": "my_game", "task_id": "walk", "task_type": "vibe",
    "preset": "walk", "template": "biped_armed", "num_frames": 96, "fps": 30,
    "motion_overrides": {"steps": 4, "step_length": 0.24, "foot_height": 0.12},
})
```

### Custom motion tracks

`preset="task_space"` accepts `root_positions`, `root_yaw`, `rotations`, `targets`
and `plant_feet` through `motion_overrides`. Curves use normalized `times` (0–1),
`values` and optional `modes`. Root positions use skeleton scale; target positions
use chain lengths relative to the shoulder, in body axes. Rotations use degrees.
See `GESTURE_TASKS` in the test for forward-reach and wave inputs. Conflicting
controls and overlapping or dependent IK chains are rejected.

### Parameterized sequences

The LLM plans the action order and parameters from the mesh and requirements.
Send the complete plan to one `GenMotionOperator.run` call. The operator delegates
sequence generation and blending to `funcs/vibe_motion_utils`; it fits the rig and
skin once, generates every segment on the same plan, then exports one final clip.
Do not implement production sequencing or blending in a test script.

For either `vibe` or `vibe_retarget`, pass a non-empty `segments` list instead of
top-level `preset`, `num_frames` or `motion_overrides` (these are mutually exclusive).
Each segment accepts only `preset`, optional `num_frames`, `heading_deg`, and
`motion_overrides`. Omitted motion parameters come from that preset's template.
All segments share global `fps`, geometry, rig and skin settings.

Optional `transitions` has exactly one entry per adjacent pair. Each entry accepts
`transition_frames` (non-negative integer, default 8) and `carry_facing` (boolean,
default true). Omit the list to use these defaults at every join.

- Transitions **insert** frames; total frames = sum of segment frames + sum of
  transition frames. Zero means a hard cut; discontinuities may fail QA.
- Global `heading_deg` defaults the world heading. With `carry_facing=true`, the
  next segment inherits the previous endpoint yaw, including any generated turn.
  An explicit later segment heading requires `carry_facing=false` on its incoming
  transition; ambiguous combinations raise instead of silently losing the heading.
- Horizontal root position is aligned. Each segment keeps its ground-relative
  height, with pose/height interpolation between segments. No foot-lock or velocity
  continuity guarantee is made. Authored contact masks and foot targets are aligned
  with their segments; transition contacts are inferred from joint height.

```python
result = GenMotionOperator(run_id="vibe_qa").run({
    "game_id": "my_game",
    "task_id": "walk_then_boxing",
    "task_type": "vibe",
    "target_mesh_path": "character.glb",
    "fps": 30,
    "segments": [
        {"preset": "walk", "num_frames": 120,
         "motion_overrides": {"steps": 3, "step_length": 0.35}},
        {"preset": "boxing", "num_frames": 96,
         "motion_overrides": {"combo": [0, 1], "reach": 0.9}},
    ],
    "transitions": [{"transition_frames": 12, "carry_facing": True}],
})
```

Use the Python operator or a task JSONL for Vibe-specific fields; do not assume
one CLI flag exists for each field. Tests configure `MOTION_TASKS` directly in
`test/test_vibe_motion.py` and call the same operator; no external task file is
needed. Single-preset calls and their report fields remain compatible.

Inspect `vibe_report_path`, plus `skin_report_path` and `rig_report_path` for mesh
tasks. Sequence reports use `preset="sequence"`, `parameters=null`, and `segments`
with canonical presets, resolved parameters, effective heading, diagnostics and
zero-based **end-exclusive** frame ranges. `transitions` records ranges and maximum
root/joint displacement per frame across each seam, including both endpoints.
Final `metrics` evaluates the blended clip, including continuity, ground,
self-collision and inferred foot skate; segment failures also propagate to the final
failure list. Foot-target error above 0.001 metres adds `ik_target`; the threshold is
recorded as `target_error_tolerance`. Target error and IK residuals are explicitly
scoped to authored segments, not unsolved transitions. A successful export is not
visual QA approval. Do not reuse artifacts from an earlier task run.

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
Code: `<REPO_PATH>/operators/gen_motion/funcs/cloud_rig_animate.py`,
`<REPO_PATH>/models/gen_motion/tripo_rigging_model.py`.

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
`tripo_rigging_model.py`; the operator writes them next to the artifact.

## When To Run

- A task asks for a humanoid character that moves (walk, attack, idle, …).
- You have a downloaded Mixamo / mocap clip and need it on a generated rig.
- A generated clip looks wrong and you need a downloaded replacement.
- You have a retargeted FBX and need to prove Blender or Unreal can use it.

Do **not** use the static mesh importers (`import_mesh.py`) on a motion FBX —
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
`<REPO_PATH>/operators/`.

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
python pipeline/assets_gen/gen_motion/run.py \
  --task-type retarget \
  --source-motion walk.fbx \
  --target-mesh character.glb \
  --target-rig character_rig.txt \
  --motion-source mixamo \
  --global-scale 0.01

# Full chain with generated motion: mesh → rig → MoMask → FBX
python pipeline/assets_gen/gen_motion/run.py \
  --task-type humanoid \
  --target-mesh character.glb \
  --prompt "A person walks forward and waves." \
  --in-place
```

Registries (no models, no Blender)::

```bash
python pipeline/assets_gen/gen_motion/run.py --list-mappings
python pipeline/assets_gen/gen_motion/run.py --list-motion-sources
```

## 1. Rigging

Prefer Vibe `fit_skeleton` + `skin_mesh` for compatible geometry. Use the
Puppeteer route below when procedural fitting does not meet the task.

**Model:** `<REPO_PATH>/models/gen_motion/puppeteer_model.py` (CUDA required for real runs).
**Step:** `<REPO_PATH>/operators/gen_motion/funcs/rig_character.py`.

Accepted mesh formats: `.glb`, `.gltf`, `.obj`, `.ply`, `.stl` (and `.fbx` at
retarget time). The operator accepts both `target_mesh_path` and the legacy
`target_glb_path` key.

**Contract that must not break:** Puppeteer's `skin` lines address vertices by
index in the mesh it consumed. The rig artifacts therefore include that exact
OBJ. Retargeting binds weights against the same vertex order — any conversion
that reorders vertices between rig and retarget silently ruins the skin.

Stub-test without CUDA: inject `StubPuppeteerModel` from `<REPO_PATH>/test/harness/stubs.py`.

## 2. Motion Generation

**Model:** `<REPO_PATH>/models/gen_motion/momask_model.py`.
**Step:** `<REPO_PATH>/operators/gen_motion/funcs/generate_motion.py`.

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

Use `<REPO_PATH>/operators/gen_motion/funcs/fetch_motion.py` instead of fighting the prompt.

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
  "source_motion_path": "downloads/Walking.fbx",
  "target_mesh_path": "character.glb",
  "target_rig_path": "character_rig.txt",
  "global_scale": 0.01,
  "fps": 30
}
```

## 3. Retargeting And Bone Mapping

**Host driver:** `<REPO_PATH>/operators/gen_motion/funcs/retarget_motion.py`.
**Blender package:** `<REPO_PATH>/operators/gen_motion/funcs/retarget_utils/`.

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
`mapping_presets.py`. Omit mapping and let `mapping_auto` derive a map, or pass
an explicit `mapping_path` / `--mapping` for a one-off.

Default path when the task names no mapping: auto-generate → write
`mapping.json` next to the FBX → run world-delta twice (full + anim-only).

### When the operator cannot cover a retarget case

Motion retarget has many legitimate edge cases (odd BVH hierarchies, engine
axis packs, IK feet, non-humanoid props, new mocap libraries). If
`mapping_auto` / `world_delta` / import fails for a real asset and the gap is
in our code — not bad input — the agent should **patch the retarget stack**
under `<REPO_PATH>/operators/gen_motion/funcs/` (and tests under `<REPO_PATH>/test/test_gen_motion.py`
/ `<REPO_PATH>/test/test_rigging_retarget.py`) so the next run goes through the operator.
Keep format constants in `retarget_utils/formats.py` in sync with fetch /
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
python scripts/import_generated_asset.py \
  --src outputs/.../retargeted.fbx \
  --engine blender --kind motion \
  --blender "$A3GF_RETARGET_BPY_PYTHON"

# Or call the importer directly
python engine_adapters/blender/import_generated/import_motion.py \
  --src retargeted.fbx --dest out/ --name Walk --report report.json
```

`ok=True` requires: armature + action + keyframes + **pose change** (root
travel alone is not enough — a sliding T-pose would otherwise pass).

Also useful for a quick structural check without the full import path::

```bash
"$A3GF_RETARGET_BPY_PYTHON" \
  -m operators.gen_motion.funcs.retarget_utils.inspect_fbx \
  --input retargeted.fbx --output fbx_inspection.json
```

Look for `pose_animated=true`, `skinned=true`, `height_m ≈ 1.5–2.0` for a
humanoid.

### Unreal Engine 5

UE is not available in every CI box; the importer is ready for a machine that
has an editor::

```bash
python scripts/import_generated_asset.py \
  --src outputs/.../retargeted.fbx \
  --engine ue5 --kind motion \
  --uproject /path/to/MyGame.uproject \
  --ue-motion-dest /Game/Generated/Motion

# Anim-only FBX onto an existing Skeleton
python scripts/import_generated_asset.py \
  --src outputs/.../animation.fbx \
  --engine ue5 --kind motion --ue-anim-only \
  --ue-skeleton /Game/Generated/Motion/Walk_Skeleton \
  --uproject /path/to/MyGame.uproject
```

Engine script: `<REPO_PATH>/engine_adapters/ue5/import_generated/import_motion.py`.
It forces `import_as_skeletal=True` and `import_animations=True` — the static
`import_mesh.py` path must not be used here.

After import, confirm in Content Browser:

1. A `SkeletalMesh` (full FBX) or only an `AnimSequence` (anim-only).
2. A `Skeleton` asset, or the animation targeting the `--ue-skeleton` you named.
3. Play the AnimSequence in the asset editor — the pose must change, not just
   the root.

Higher-level UE client: `ue.animation.import_motion(...)` in
`<REPO_PATH>/engine_adapters/ue5/animation/client.py`.

### Godot 4

Use the public Client with the generated task descriptor; it stages the FBX or
glTF/GLB under `res://` and requires a successful real Godot `--import` run:

```python
from engine_adapters.godot import GodotClient

godot = GodotClient(
    project_path="/path/to/MyGame",
    godot_executable="/path/to/godot4",
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
   `<REPO_PATH>/agent_skills/asset_qa/3d_object/orientation_review.md`).
6. **Licence.** Check the model, dataset, and source-motion terms before
   shipping. Mixamo / MoCap Online / Bandai each have separate terms; retain
   `*_motion_source.json` with the artifact.

## 6. Runtime Environment

For Vibe-only BVH generation, use the Python/NumPy source setup above; no model
weights or GPU environment is needed. Configure bpy additionally for FBX retargeting.
Install the following Linux environments only for the model-backed fallback and
retarget routes:

```bash
bash scripts/asset_env_setup/gen_motion/install.sh

# Install sources and environments only; download weights later if needed.
bash scripts/asset_env_setup/gen_motion/install.sh --skip-weights

source scripts/asset_env_setup/gen_motion/runtime_env.sh
```

The installer creates `gamefactory3a-puppeteer`, `gamefactory3a-momask`, and
`gamefactory3a-retarget-bpy`. `runtime_env.sh` exports:

- `A3GF_PUPPETEER_MODEL_PATH`
- `A3GF_PUPPETEER_PYTHON`
- `A3GF_MOMASK_MODEL_PATH`
- `A3GF_MOMASK_PYTHON`
- `A3GF_RETARGET_BPY_PYTHON`

Pass them explicitly to the pipeline so the command does not depend on legacy
environment-variable aliases:

```bash
python pipeline/assets_gen/gen_motion/run.py \
  --task-type humanoid \
  --target-mesh character.glb \
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
  scripts/asset_env_setup/gen_motion/create_humanoid_glb.py \
  /tmp/gamefactory3a_humanoid.glb
```

Synthetic humanoid fixture (mesh + Mixamo-named BVH + matching Puppeteer
rig), for local repro without licensed assets::

```python
from test_rigging_retarget import build_all  # under test/
build_all("/tmp/mofix", mesh_format=".glb")
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
