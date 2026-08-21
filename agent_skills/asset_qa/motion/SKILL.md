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
   rig  (Puppeteer)          →  rig.txt + skeleton.txt + mesh.obj
        │
        ▼
   motion (MoMask | Mixamo | …) →  motion.bvh / source.fbx
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

## Get the clip by download first

**Text-to-motion generation quality and controllability are not good enough yet.**
MoMask produces a plausible-looking clip from a sentence, but you cannot reliably
control timing, style, exact limb trajectories, foot contact, or how the clip
loops — and re-prompting rarely converges on what the plan asked for.

So for any real game deliverable, **download a clip first and prefer Mixamo**:

1. **Mixamo** — the default source. Broad, consistent, game-oriented humanoid
   library on one skeleton (`mixamorig:*`), so `SOURCE_SKELETONS` already knows
   it and retargeting is predictable. It is login-gated, so download by hand:
   FBX Binary, **Skin = Without Skin**, then use `task_type=retarget` with
   `motion_source=mixamo` and `global_scale=0.01` (centimetres).
2. **Other libraries** — MoCap Online, CMU BVH, or a local clip, per
   [Motion sources](#when-generation-quality-is-not-enough).
   Check each licence; Bandai-Namco is research-only.
3. **MoMask text-to-motion** — use it when no downloadable clip fits, when the
   motion is unusual enough that no library has it, or for a quick placeholder
   while the game is being assembled. Say that it is the generated route, and
   expect to review it harder.

Do not scrape login-gated sources; that violates the licence and the fetcher
refuses it on purpose. Always record provenance either way.

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
| `rig` | character mesh | `rig.txt`, `skeleton.txt`, `mesh.obj` |
| `text_to_motion` | text prompt | `motion.bvh` (+ raw/ik/preview) |
| `retarget` | source clip + mesh + rig | `retargeted.fbx`, `animation.fbx`, `mapping.json` |
| `humanoid` | mesh + prompt | all of the above, chained |

CLI demo (single task). Load the runtime first and pass the explicit model
arguments shown in [Runtime Environment](#6-runtime-environment)::

```bash
# Retarget a Mixamo download onto an existing rig — the preferred route
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

Reach for this only after [Get the clip by download first](#get-the-clip-by-download-first)
has been considered: generation is the weakest link in this chain, and a
downloaded Mixamo clip is usually the shorter path to a shippable animation.

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

### Motion sources (the preferred route)

Use `<REPO_PATH>/operators/gen_motion/funcs/fetch_motion.py` instead of fighting the prompt.

| Source | Access | Skeleton | Notes |
|---|---|---|---|
| `mixamo` | manual (login) | Mixamo | **Preferred.** Download FBX Binary, Skin=Without Skin |
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
3. **Feet.** Sliding feet → prefer IK BVH (`use_ik=True`) or a cleaner mocap clip.
4. **Scale.** Humanoid height ≈ 1.6–2.0 m after import. 180 or 0.018 means units
   were wrong (`global_scale`).
5. **Facing.** Pipeline exports Y-up / -Z forward. Record facing for the game
   asset if the character looks sideways in the first playable spawn (see
   `<REPO_PATH>/agent_skills/asset_qa/3d_object/orientation_review.md`).
6. **Licence.** Check the model, dataset, and source-motion terms before
   shipping. Mixamo / MoCap Online / Bandai each have separate terms; retain
   `*_motion_source.json` with the artifact.

## 6. Runtime Environment

Install the three isolated Linux environments and selected weights once:

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

1. Read this skill and `<REPO_PATH>/operators/gen_motion/funcs/retarget_utils/__init__.py`.
2. Prefer a downloaded clip — Mixamo first — and run `task_type=retarget`. Use
   `task_type=humanoid` with MoMask only when no library clip fits or a
   placeholder is enough.
3. `--list-motion-sources` to see the registry; download manual sources by hand,
   then set `fetch_motion` / `motion_source` on the task.
4. Never invent a bone map for a new Puppeteer rig — omit mapping and let
   `mapping_auto` run, or generate one with the bpy `mapping_auto` module.
5. If retarget/import fails for a real format or skeleton the operator should
   support → patch `retarget_utils` (and tests), then re-run through the
   operator.
6. After FBX lands, run Blender `--kind motion` import (or `inspect_fbx`) and
   refuse assets with `pose_animated=false`.
7. Import into UE only after Blender validation passes; use `--kind motion`.
8. Record licence / facing / scale notes next to the artifact.
