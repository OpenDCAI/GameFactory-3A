# three.js Agent API Reference

Status: implemented `ThreeClient` API version `v1`.

Validated engine baseline: three.js r185 (`three@0.185.0`), Node 20,
Vite 6.

This file is a compact index of implemented public capabilities. It lists
public names and their functions only. Read the current source when exact
parameters or result payload fields are required.

## Toolchain

The three.js adapter has no engine installation. Its only external
requirement is a **Node 20+** toolchain on `PATH`, because the adapter
drives Vite, Vitest, and the package manager through its private Node
transport.

`<REPO_PATH>/scripts/engine_install/three_js/setup_env.sh` provisions that
toolchain as a conda environment for machines with no system Node:

```bash
scripts/engine_install/three_js/setup_env.sh  # create or verify
source <conda_root>/etc/profile.d/conda.sh
conda activate threejs
```

`<REPO_PATH>/scripts/engine_install/three_js/README.md` documents the launchers
and their environment variables. Every launcher is a thin wrapper over the public
`ThreeClient`.

## Hard API Boundary

The only supported Python entry point is:

```text
from engine_adapters.three_js import ThreeClient
```

The only supported JavaScript entry point for generated gameplay is:

```text
import { ... } from '@a3game/playable';
```

Agents, generated code, Pipeline code, and platform Serving code must not:

- import `engine_adapters.three_js._internal`;
- import namespace client implementation classes directly;
- import any `_internal` subpackage of a namespace;
- call transports, services, registries, resolvers, or inspectors;
- run arbitrary Node or npm commands through private transports;
- modify the adapter-owned `A3GamePlayable` framework;
- deep-import `@a3game/playable/src/...` paths;
- depend on optional Arena Fighter, FPS, Racing, or Explorer example
  packages;
- hard-code asset URLs, `dist/` paths, or `public/` paths;
- construct generated-output paths manually.

Generated gameplay belongs in a separate project-local Gameplay Package
under `packages/`.

## Execution Authority

The game-generation Agent generates engine-native test source. The Agent
MUST NOT invoke `three.testing.*` or declare benchmark success.

Engine execution and evaluation code owns bundle builds, test execution,
runtime evidence, and benchmark results. A zero process return code alone
is not success; the parsed test report must contain matching passing
tests.

`three.preview.*` is not execution and the Agent may call it freely: it
renders an artifact on the CPU to inform a decision, produces no
benchmark claim, and writes nothing a player downloads.

`three.playtest.*` is likewise not a benchmark: it records what happened
when the game was played and makes no pass/fail claim of its own. See
**Playtest**.

## Result Contract

Public operations return JSON-serializable result dictionaries using
these stable top-level fields:

- `ok` - whether the operation completed successfully;
- `operation` - stable operation identifier;
- `artifacts` - produced or retained artifact paths;
- `diagnostics` - structured diagnostic records;
- `warnings` - non-fatal problems;
- `errors` - fatal problems;
- `payload` - operation-specific result data.

## Client

- `ThreeClient` - Creates the public three.js environment client and its
  namespace clients.
- `three.api_version` - Reports the active public ThreeClient API version.
- `three.get_environment_info` - Reports configured project, three.js
  baseline, dev server, runtime channel, and registry information.

Constructor arguments: `project_path`, `three_root`, `api_version`, and
keyword-only `host`, `port`, `runtime_host`, `runtime_port`,
`package_manager`, `runtime_transport`, `node_root`. Every argument also
resolves from `A3GAME_THREE_*` environment variables.

## Project

- `three.project.get_info` - Reports the configured project and three.js
  baseline paths.
- `three.project.create` - Creates a minimal Vite + three.js host project
  without concrete gameplay defaults.
- `three.project.install_dependencies` - Installs Node dependencies with
  the configured package manager.
- `three.project.validate` - Checks project configuration, descriptor,
  entry document, static root, and installed dependencies.

`project.create` writes `package.json`, `vite.config.js`, `index.html`,
`src/main.js`, the `public/assets/imported/*` roots, `packages/`,
`tests/`, and the `.a3game-three.json` descriptor. It installs no
gameplay.

## Assets

- `three.assets.import_asset` - Stages a registered task artifact using
  its declared asset type.
- `three.assets.import_avatar` - Stages a registered character or avatar
  artifact.
- `three.assets.import_motion` - Stages registered animation data,
  optionally against a target skeleton artifact.
- `three.assets.import_scene` - Stages a registered Scene artifact.
- `three.assets.import_prop` - Stages a registered prop or generic mesh
  artifact.
- `three.assets.import_weapon` - Stages a registered weapon mesh
  artifact.
- `three.assets.import_material` - Stages a registered material
  artifact.
- `three.assets.import_texture` - Stages a registered texture artifact.
- `three.assets.import_effect` - Stages a registered Effect package or
  native Effect content.
- `three.assets.import_audio` - Stages a registered audio artifact.
- `three.assets.validate` - Validates a registered source artifact
  without staging it and without a Node process.
- `three.assets.resolve_source` - Resolves a repository task identity to
  its registered source artifact.
- `three.assets.list` - Lists staged asset files under the project static
  root.
- `three.assets.list_registered` - Lists artifacts recorded in the
  adapter registry.
- `three.assets.get_metadata` - Reads metadata for one registered
  artifact.
- `three.assets.write_manifest` - Rewrites the runtime asset manifest
  from the registry.
- `three.assets.set_orientation` - Records which way a staged model faces
  and how big it is meant to be.
- `three.assets.get_orientation` - Reads back one artifact's recorded
  orientation.
- `three.assets.analyze_orientation` - Reports what geometry alone says
  about a model's facing, for a staged artifact or an unstaged task
  identity.

Public asset methods consume repository task identities. They do not
accept arbitrary generated-output filesystem paths.

### Asset Orientation

glTF fixes the up axis (+Y) and the unit (metre) and says nothing binding
about **facing**. A model authored facing +Z and the same model facing -Z
have identical bounding boxes, node trees and animation clips, so no
amount of loading can tell them apart. The runtime convention is:

```text
local forward = -Z·   local up = +Y
```

That is what a yaw of zero means to generated gameplay, which derives
facing with `Math.atan2(-x, -z)`. A model that disagrees is not broken —
it is unlabelled, and the label is carried in the artifact record:

```python
three.assets.import_prop(source, options={
    "asset_id": "chest", "forward_axis": "+z",
    "scale_hint_metres": 0.8, "verified_by": "agent_vision",
})
# or afterwards, without re-staging the file:
three.assets.set_orientation("chest", forward_axis="+z",
                             scale_hint_metres=0.8,
                             verified_by="agent_vision")
```

`forward_axis` names the axis the model faces *as authored*; the adapter
derives the yaw, so no caller computes an angle. `+z → 180°`,
`-z → 0°`, `+x → 90°`, `-x → 270°`. Also available:
`yaw_offset_degrees` for a model facing no cardinal axis,
`pitch_offset_degrees` for content authored Z-up, `scale_hint_metres`,
`pivot`, `verified_by` (`unverified`/`heuristic`/`declared`/
`agent_vision`/`human`) and `notes`.

Importing a directional asset without a `forward_axis` succeeds and
returns a warning: the runtime then places the model exactly as authored,
which is a coin flip. Determining the axis requires looking at the model —
see **Preview** below and
`<REPO_PATH>/agent_skills/asset_qa/3d_object/orientation_review.md`.

All three models in the curated CC0 pack face **+Z**, so every one of
them needs a 180° correction.

## Preview

- `three.preview.render_artifact` - Renders a staged artifact from named
  axes and writes labelled PNGs plus one contact sheet.
- `three.preview.render_source` - Renders a repository task artifact
  before it is imported.
- `three.preview.orientation_report` - Renders the review sheet and
  returns it with the geometric evidence and the exact call that records
  a decision.
- `three.preview.list_views` - Reports the view sets and what each
  rendered axis means.

The renderer is pure `numpy` and `Pillow`: no GPU, no display, no
browser, no Node process, and about two seconds per asset. Views are
**orthographic** and named after the axis the camera sits on — a view
labelled `+z` shows the model's +Z side — because naming a view "front"
would presuppose the answer being sought. Each image carries its axis and
its screen-right axis burnt into the frame, since a filename is invisible
to a reader handed only pixels.

Renders land under the project's `.a3game/previews/`, never in `public/`:
they are review evidence, not shipped content.

Geometry is decoded in the **bind pose**, with a skinned mesh's node
transform ignored as the specification requires, so the review measures
the model rather than a frame of its walk cycle. Draco- and
meshopt-compressed artifacts cannot be decoded without a GPU decoder and
report that instead of drawing nonsense.

### Import Lifecycle

The web has no engine-side import step: a `.glb` file is already the
runtime format. `import_asset` therefore stages the file into
`public/<destination>/`, copies any `.gltf` sidecar buffers and images,
records an artifact, and rewrites the manifest.

`<REPO_PATH>/scripts/engine_install/three_js/import_asset.sh` is a lifecycle
wrapper around the same public `ThreeClient`; it is not a second or faster asset
API.

Asset and World operations need no live browser. Execution code should
reuse one `ThreeClient` for a task batch.

### Asset Manifest

Every import rewrites `public/assets/manifest.json`. This file is the
only supported way for generated gameplay to find an asset:

```json
{
  "manifest_version": 1, "api_version": "v1", "backend": "web",
  "engine": "three_js", "engine_version": "0.185.0",
  "assets": {
    "<artifact_id>": {
      "artifact_id": "...", "asset_id": "hero", "package_id": "hero",
      "type": "avatar", "category": "", "representation": "gltf_binary",
      "class": "SkinnedMesh", "url": "/assets/imported/avatars/hero.glb",
      "capabilities": {
        "renderable": true, "spawnable": true, "collidable": false,
        "playable": false, "animated": true, "skinned": true
      },
      "animations": ["idle", "walk"],
      "bounds": { "min": [], "max": [], "size": [], "center": [] },
      "orientation": {
        "forward_axis": "+z", "up_axis": "+y",
        "yaw_offset_degrees": 0, "runtime_forward_axis": "-z",
        "runtime_yaw_degrees": 180, "scale_hint_metres": 1.8,
        "verified_by": "agent_vision", "needs_vision_check": false
      }
    }
  }
}
```

`orientation` is what `A3GameAssetLibrary` applies at instantiation. An
absent or empty block means nobody recorded a facing, and the runtime
then leaves the model exactly as authored — so annotating an asset can
never make an already-correct game worse.

### Default Destinations

`avatar` → `assets/imported/avatars`, `motion` → `.../motions`,
`scene` → `.../scenes`, `environment` → `.../environments`,
`effect` → `.../effects`, `material` → `.../materials`,
`texture` → `.../textures`, `prop` and `static_mesh` → `.../props`,
`weapon` → `.../weapons`, `audio` → `.../audio`.

### Runtime Formats

`glb`/`gltf` are the only supported mesh formats for shipped content;
`png`, `jpeg`, `webp`, `ktx2`, `basis`, `hdr` for images; `mp3`, `ogg`,
`wav` for audio. `fbx`, `obj`, `stl`, `ply`, and `usdz` load but validate
with a warning and should be converted. Draco and meshopt compression are
supported when the runtime configures the matching decoder.

## Animation

- `three.animation.import_motion` - Stages motion through the Animation
  namespace.
- `three.animation.resolve_skeleton` - Reports the skinned hierarchy
  carried by a registered artifact.
- `three.animation.validate_compatibility` - Checks whether motion clips
  can drive a target avatar skeleton and whether retargeting is required.

Staged motion lands in `public/assets/imported/motions/` and appears in
the manifest as `type: "motion"`. A motion artifact carries clips and
usually a skeleton but **no renderable body**, so it is not something
`instantiate()` can return — `A3GameMotionLibrary` is what resolves it at
runtime.

Convert a retargeted FBX to glTF before staging it: `fbx` loads in the
browser but validates with a warning, and `glb` is the runtime format. The
generation chain that produces the clip in the first place — Puppeteer rig,
MoMask or a licensed clip, Blender `world_delta` retarget, `inspect_fbx`
verification — is documented in
`<REPO_PATH>/agent_skills/asset_qa/motion/SKILL.md`.

### Motion On A Generated Character

This is the single most consequential difference between a downloaded
character and a generated one, and it is invisible from the call site.
`assets.tryInstantiate` is correct for the first and silently wrong for the
second: image-to-3D produces **one fused body**, so its manifest entry
reports `animations: []` and `skinned: false`. A game that swaps its
procedural body for such a model ends up with a good-looking character that
never moves — strictly worse than the capsule it replaced, because the
primitive limbs that *were* being posed went away with the swap.

There are exactly three ways a character can move, and `createAnimatedActor`
tries them in order:

| Route | `motionSource` | When it applies |
|---|---|---|
| The model's own clips | `clips` | A CC0 character, or a generated one re-exported by `gen_motion` |
| Imported motion, retargeted by bone name | `imported_motion` | `type: 'motion'` artifacts have been staged |
| A skeleton the rigging pipeline produced | `rigged_asset` | The asset is skinned and its bones carry canonical names |
| A fitted skeleton plus authored clips | `auto_rig` | A static generated humanoid with no rig |
| Nothing usable | `none` | The mesh is not shaped like a standing figure |

### The `rigged_asset` Route

This is the route to prefer, and the one to produce. `<REPO_PATH>/operators/gen_motion`
runs Puppeteer, which predicts the skeleton *and* the skinning weights from the
mesh — a real answer, where `autoRigHumanoid` is a browser-side approximation.
What comes back is skinned and bound but has **no clips**, because rigging and
animating are separate stages.

Two things make that asset usable with no retargeting at all:

1. **The exporter renames the predicted joints to `A3GameHumanoidBone` names.**
   Puppeteer emits `joint0..jointN` in prediction order, which carries no
   anatomy — `joint23` is a hip on one character and a finger on the next. The
   labels are derived from topology: the root is the pelvis, the subtree
   reaching highest is the spine, the two reaching lowest are the legs, and the
   spine node where two chains branch sideways is the chest. Authored clips
   address tracks as `` `${boneName}.quaternion` ``, so canonical names bind
   directly.
2. **The rest pose is identity rotations plus parent-to-child offsets**, which
   is how `createHumanoidSkeleton` builds the template. A clip swings a thigh
   about local X, and that only means "forward" when a bone's local axes
   coincide with the world's at rest. Proportions may then differ freely between
   characters, because a rotation is scale-invariant.

`findRiggedHumanoid` performs the check and runs **before** the humanoid gate.
That order matters: the gate reads a bounding-box aspect ratio, which says
nothing about a model that already carries a labelled skeleton, and it rejects
legitimate assets — an archer holding a bow measures wider than tall.

```js
const rigged = findRiggedHumanoid(object);  // null when not usable
// -> { skeleton, bones: Map<name, Bone>, height, matched, missing }
```

`height` comes from the bound geometry, not from the topmost bone: a clip's
stride and hip travel are fractions of the character's height, and the head
bone sits inside the skull rather than on top of it.

### Reconstruction Debris Sets The Bounding Box

Worth knowing before concluding that a generated character has bad proportions.
Three of the four characters in the sample projects measured *wider than tall*
and were refused by the humanoid gate. The bodies were fine — welded, the
largest component of one was 98% of the faces and measured a perfectly ordinary
0.38 x 0.73 x 0.33. **Four stray faces**, left floating by the reconstruction,
were setting the bounding box.

Every proportion derived from that box is then wrong: `fitToHeight` scales the
character so the *debris field* is 1.8 m, `ground: true` puts the lowest speck
on the floor, and the gate reads 0.74 and reports "not a person". Deleting the
specks is the fix; regenerating the asset is not.

Two traps when doing that in Python: `trimesh.load(..., process=False)` leaves a
glTF unwelded, so `split()` returns one component per face and says nothing
about the shape; and `merge_vertices()` preserves UV and normal seams by
default, which for a glTF means it merges almost nothing. Pass
`merge_tex=True, merge_norm=True`.

```js
const actor = await createAnimatedActor(assets, 'arena_trooper', {
  height: 1.8,
  ground: true,   // place by the feet: a generated origin is arbitrary
  states: ['idle', 'walk', 'run', 'aim', 'shoot', 'hit', 'death'],
  defaultState: 'idle',
});
if (!actor || actor.motionSource === 'none') {
  // Keep the procedural body. It moves, and this one does not.
} else {
  entity.setVisual(actor.object, actor.animations, {
    animator: actor.animator, motionSource: actor.motionSource,
  });
}
```

`motionSource: 'none'` is a real answer that must be honoured. It is
returned by `measureHumanoid` for a mesh wider than it is tall — the
characteristic output of a reconstruction that inferred a ground plane —
and rigging such a mesh produces a writhing lump rather than a fighter.
Looking correct while frozen is a worse outcome than looking plain and
moving.

So hand the preference list to **`createAnimatedActor`**, not to
`tryInstantiate`. `tryInstantiate` answers "is it staged" and stops at the
first candidate; `createAnimatedActor` tries each in order and returns the
first that ends up with clips, keeping a loaded-but-motionless one only as
a last resort. Only the second question decides whether a swap is an
improvement, and it cannot be answered before the rig is attempted.

```js
const actor = await createAnimatedActor(
  assets, ['explorer_hero', 'explorer_ranger', 'robot_expressive'], { height: 1.8 });
```

- `A3GameHumanoidBone` / `A3GameMotionState` - the canonical bone and
  state names. Left is `+x`, up is `+y`, forward is `-z`, and every bone
  rests with an identity rotation, which is what makes one authored clip
  valid for every character.
- `createHumanoidSkeleton` - the template skeleton at a given height, as
  fractions of that height rather than metres.
- `measureHumanoid` - proportions, ground offset, horizontal centre, and
  whether the mesh is a standing figure at all.
- `autoRigHumanoid` - converts every mesh in a subtree into a
  `SkinnedMesh` bound to one fitted skeleton, with weights from
  distance to each bone's segment limited by that bone's influence
  radius. A browser-side approximation of what `<REPO_PATH>/operators/gen_motion`
  does properly with Puppeteer; it returns `null` for an
  already-skinned model.
- `createHumanoidClip` / `createHumanoidClipSet` /
  `A3GAME_HUMANOID_CLIP_NAMES` - authored keyframe clips for `idle`,
  `walk`, `run`, `jump`, `block`, `punch`, `kick`, `slash`, `hit`,
  `death`, `aim`, `shoot`, `reload`, `draw`. Pass `hipsRest` from the
  rig — a clip built against the template's rest position would throw
  away the fitted skeleton's ground offset and horizontal centre on its
  first frame.
- `retargetClipToSkeleton` / `A3GameSourceBoneAliases` - renames a clip's
  tracks onto another skeleton, resolving `mixamorig:` prefixes and
  common library names. This is the *name* half of retargeting, which is
  all a browser can do; reconciling rest poses and bone lengths needs the
  source bind pose and belongs to `<REPO_PATH>/operators/gen_motion`.
- `A3GameMotionLibrary` - `listMotions`, `loadClips`,
  `loadForCharacter`, `available`, `warnings`. Renames clips to the state
  they represent when the artifact declares one, because a game maps
  states and `retargeted_003` names nothing.
- `A3GameAnimationDirector.mapStateChain` / `mapStateChains` - bind a
  state to the first clip name that exists. Necessary because the
  authored set calls the shooting stance `aim` and the CC0
  `robot_expressive` avatar has fourteen clips and none of them is
  called that; a single name means one source silently plays nothing.

## Visual Effects

three.js ships no particle system: `Points` is a draw call and a point
size, `Sprite` is one quad per object, and neither simulates or pools
anything. A game that reaches for them directly ends up with one object
per spark, a `requestAnimationFrame` of its own, and nothing disposed — so
a firefight degrades the frame rate in proportion to how well it is going.

The kit follows the two libraries that solved this for three.js already,
without depending on either: **three.quarks** contributes the batched
renderer, declarative systems, and render modes; **Three-VFX** contributes
the parameter vocabulary (`size`, `colorStart`/`colorEnd`, `fadeSize`,
`fadeOpacity`, `emitterShape`, `startPositionAsDirection`, `turbulence`,
`friction`, `stretchBySpeed`, `blending`, `appearance`, `intensity`).
Re-implementing it is what keeps a generated project buildable offline,
and the parts that matter are a pooled CPU simulation feeding one
instanced draw call.

- `A3GameVfxDirector` / `createVfxDirector` - the batched owner of every
  effect in a game: `register`, `registerAll`, `get`, `play`, `follow`,
  `registerBeam`, `fireBeam`, `update`, `attachToHost`, `getState`,
  `dispose`.
- `A3GameParticleSystem` - one pooled effect, one draw call: `emit`,
  `burst`, `start`, `stop`, `clear`, `update`, `attachToHost`,
  `getState`, `dispose`, plus `emitterPosition` for a moving emitter.
- `A3GameVfxPreset` - tuned definitions: `MUZZLE_FLASH`,
  `BULLET_IMPACT`, `IMPACT_DUST`, `BLOOD_HIT`, `MELEE_IMPACT`,
  `SHOCK_RING`, `BLOCK_SPARK`, `FOOT_DUST`, `LIGHT_ARROW_CORE`,
  `LIGHT_ARROW_MOTES`, `LIGHT_ARROW_IMPACT`, `BLADE_SLASH`,
  `PICKUP_SPARKLE`, `SMOKE_PLUME`, `EXPLOSION`, `TYRE_SMOKE`,
  `SCRAPE_SPARK`, `BOOST_FLAME`. Each is a plain object, so tuning one
  is a spread rather than a fork.
- `A3GameBeamEffect` - pooled fading lines for tracers and laser sights.
  A tracer is not a particle effect: it exists for two frames and has a
  definite start and end.
- `A3GameTrailRibbon` - a continuous surface through the positions a
  projectile actually occupied. Particles alone cannot draw a streak,
  because a spark stops moving the instant it is emitted and the eye sees
  a dotted line.
- `A3GameEmitterShape` (`POINT`, `BOX`, `SPHERE`, `CONE`, `DISK`,
  `EDGE`), `A3GameParticleBlending` (`NORMAL`, `ADDITIVE`, `MULTIPLY`),
  `A3GameParticleAppearance` (`DEFAULT`, `GRADIENT`, `CIRCULAR`, `RING`),
  `A3GameParticleRenderMode` (`BILLBOARD`, `STRETCHED`, `MESH`).

Choosing the right primitive:

| Need | Primitive |
|---|---|
| A burst at a point (impact, muzzle, hit) | `play(name, { position, direction })` |
| A stream that tracks something (trail, exhaust, smoke) | `follow(name, object3D, { rate })` |
| An instantaneous line (tracer, laser) | `registerBeam` + `fireBeam` |
| A continuous streak behind a projectile | `A3GameTrailRibbon` |

Three placement rules decide whether an effect reads at all:

1. **A burst points along the surface normal**, not along the shot.
   Sparks that continue into the wall cannot be seen, which is why
   `A3GameCollisionProbe.hitscan` now also returns a world-space
   `normal`.
2. **A tracer starts at the muzzle**, not at the camera: a line drawn
   from the eye is inside the near plane and invisible.
3. **A trail is not parented to what it follows.** A parented trail is
   dragged along by the object, so the streak never forms — `follow()`
   moves the *emitter* and leaves emitted particles in world space.

Three properties are contractual, because they are what hand-rolled
particle code gets wrong: one draw call per system; a **fixed** pool, so
emission never allocates and a busy fight cannot leak; and no `document`,
canvas, or GPU at construction time — the shape mask is computed in the
fragment shader, so every class here can be constructed and stepped by a
headless `vitest` test. Effects are decoration and must never be
load-bearing: `play()` on an unregistered name returns `0` rather than
throwing, so a missing effect can never be why a hit stops registering.

Reference implementation of both halves:
`<REPO_PATH>/engine_adapters/three_js/examples/motion-vfx-example/`.

## Bindings

- `three.bindings.bind_pbr_material` - Stages a PBR texture set and
  writes a runtime material binding for registered mesh artifacts.

Bindings are written to `public/assets/bindings/<asset_id>.json` and
applied at runtime by `A3GameAssetLibrary.applyMaterialBinding`. Texture
slots are inferred from file names (`_basecolor`, `_normal`,
`_roughness`, `_metallic`, `_ao`, `_emissive`, `_alpha`, `_height`).

## World

- `three.world.build` - Builds or imports a World from a registered Scene
  artifact.
- `three.world.create_draft` - Creates a persistent editable World draft.
- `three.world.validate_draft` - Validates a World draft and its
  referenced artifacts.
- `three.world.publish_draft` - Publishes a validated draft as a
  registered World package and writes its runtime scene graph.
- `three.world.list_packages` - Lists registered World packages.
- `three.world.get_scene_graph` - Returns the runtime scene graph
  document for one draft without publishing it.

Publishing writes `public/assets/worlds/<world_id>.json`. That document,
not a hand-built scene, is what `A3GameSceneLoader` consumes.

### World Spec Schema

Coordinates are right-handed, Y-up, metres; rotations are radians.

- `world_id`, `name`, `project_id`, `metadata`;
- `environment` - `preset` (`room`/`sky`/`gradient`/`none`), `sun`
  (`{x,y,z}`, also aims the painted sun), `sky` (palette and cloud
  settings for the `gradient` preset — see `createSkyGradient`),
  `background`, `environment_artifact_id`, `background_artifact_id`,
  `environment_intensity`, `background_intensity`,
  `background_blurriness`, `tone_mapping`, `tone_mapping_exposure`,
  `shadows`, `fog` (`type`/`color`/`near`/`far`/`density`), `ground`
  (`size`/`color`/`texture_artifact_id`/`normal_artifact_id`/
  `roughness_artifact_id`/`texture_repeat`);
  a `preset` and an imported HDRI are complementary, not exclusive: the
  preset can keep the visible sky while the HDRI supplies the lighting.
  An unknown `preset` is rejected when the world is published rather than
  showing up as a silently unlit scene in the browser;
- `camera` - `type`, `fov`, `near`, `far`, `position`, `target`,
  `controls`;
- `lights[]` - `light_id`, `type`, `color`, `intensity`, `position`,
  `target`, `cast_shadow`, plus type-specific `options`;
- `entities[]` - `entity_id`, `role`, `artifact_id`, `category`,
  `collision`, `cast_shadow`, `receive_shadow`, `transform`
  (`position`/`rotation`/`scale`), `behaviors[]`, `parameters`;
- `spawn_points[]` - `name`, `position`, `rotation`.

Supported light types: `AmbientLight`, `HemisphereLight`,
`DirectionalLight`, `PointLight`, `SpotLight`, `RectAreaLight`.
Supported camera controls: `none`, `OrbitControls`,
`PointerLockControls`, `MapControls`, `FlyControls`.
Supported entity roles: `environment`, `player_start`, `prop`, `npc`,
`pickup`, `trigger`, `vehicle`, `weapon`, `effect`.
Supported behavior types: `animation`, `spin`, `orbit`, `float`, `path`,
`audio`.

## Plugin

- `three.plugin.install` - Installs a registered generated Gameplay
  Package into a project and synchronizes declared framework
  dependencies.
- `three.plugin.install_framework` - Installs the adapter-owned
  `A3GamePlayable` Runtime Framework as `@a3game/playable`.
- `three.plugin.list` - Lists installed project packages.

Installation copies the package into `packages/<name>`, adds a
`file:./packages/<name>` dependency, and ensures a `packages/*` workspace
entry. Generated Gameplay Packages may depend only on the
`@a3game/playable` public export surface. Declaring `@a3game/playable`
makes `plugin.install` install the framework automatically.

## Build

- `three.build.project` - Builds the project's web bundle and returns
  structured command and diagnostic evidence.

## Testing

- `three.testing.run_automation_tests` - Runs generated web tests with
  `vitest` or `playwright`, parses a fresh report, and returns
  authoritative matched, passed, failed, and skipped counts.

The runner deletes any prior report, refuses to score a stale report,
and fails when the report matches zero tests. The game-generation Agent
must not invoke this namespace.

## Playtest

- `three.playtest.record` - Drives a running game through discovered
  actions and writes `frames/`, `video.mp4`, and `report.json`.

This answers a question `vitest` cannot: *does the game play*. A unit test
asserts a method returns what it should; a playtest presses the keys the
game declares it listens for and records what happened. It is evidence,
not a benchmark — `checks` say the recording is intact, `game_state` says
the game responded.

```bash
python -m pipeline.code_gen.playtest.run \
    --project test_data/outputs/<game>/<run>/mechanic/<task> \
    --url http://127.0.0.1:5188 \
    --recorder-root <recorder_root> \
    --browser-executable <recorder_root>/browsers/chromium-1234/chrome-linux64/chrome \
    --ffmpeg /opt/conda/envs/torch-base/bin/ffmpeg \
    --duration 10 --fps 20

python -m pipeline.code_gen.playtest.eval --report <out_dir>/report.json
```

`run.py` records and scores nothing; `eval.py` reads a written report and
records nothing. The engine switch is in `run.py`, so a second engine adds
a branch there rather than anything to the three.js adapter.

### This Is Not Screen Recording

There is no display and no GPU render node, so WebGL runs on
**SwiftShader** — 614 ms/frame for a first-person arena at 640x360, up to
6 s/frame for a long view. `xvfb` + `ffmpeg` has no desktop to capture,
and real-time recording yields a slideshow.

So the simulation is driven at a **fixed timestep**, exactly one image is
captured per step, and frames are encoded at that same rate. The video is
smooth at the target frame rate however long each frame actually took — a
10 s take costs about 2 minutes of wall clock. This is what
`A3GameRuntimeHost.tick(forcedDelta)` is for.

### Five Constraints That Are Not Negotiable

Each was found by failing without it.

1. **Only one flag combination reaches SwiftShader.** Chromium finds the
   NVIDIA card, tries it, and fails *without* falling back to software.
   `--use-angle=swiftshader --enable-unsafe-swiftshader --in-process-gpu
   --override-use-software-gl-for-tests` — without `--in-process-gpu`
   there is no WebGL context at all.
2. **`--disable-features=CDPScreenshotNewSurface` or the renderer dies.**
   Playwright enables it by default, and with `--in-process-gpu` it kills
   the renderer after 40-60 screenshots — fatal, since no separate GPU
   process is left to restart. `--disable-gpu-watchdog` covers the other
   end: a SwiftShader frame over a second looks like a hang.
3. **Stop the host loop, tick inside rAF, capture one frame later.**
   `host.stop()` hands the frame loop to the recorder, but a stopped rAF
   loop means the compositor no longer commits and every screenshot
   blocks ~1.25 s on an internal timeout. Ticking inside a
   `requestAnimationFrame` and waiting one further frame before capture:
   **1335 ms/frame becomes 355 ms/frame**, same picture.
4. **Capture through raw CDP, not `page.screenshot()`.** Playwright waits
   for a stability condition a stopped rAF loop never satisfies;
   `Page.captureScreenshot` returns immediately.
5. **`libopenh264`, bitrate-driven.** No `libx264` here, so no `-crf` and
   no `-preset`; use `-b:v 2500k`. `-r` must equal the simulation rate or
   the video plays fast or slow, and `-start_number` stops `ffmpeg`
   halting at the first gap.

**Chromium is also missing three system libraries** —
`libatk-bridge-2.0`, `libgbm`, `libatspi` — which cannot be installed on
this image. They come from a conda prefix prepended to
`LD_LIBRARY_PATH`, which is what `--recorder-root` wires up. Omitting it
fails at browser launch and the error names Chromium, not the library.

### Actions Are Discovered, Not Configured

A recorder told what to press would need a new script per game. Instead
the running game is asked, in this order, and `report.action_source` says
which answered:

| Source | What it yields |
|---|---|
| `__A3GAME_PLAYTEST__` | A plan the game declares for itself |
| `input.actionBindings` | The game's own verbs — `fire`, `reload`, `draw` |
| `input.keyBindings` | Movement — `forward`, `left`, `run`, `jump` |
| `[data-game-action]` | Menu-driven games |
| built-in fallback | WASD + Space + click, so an unannotated game still records |

`A3GameInputRouter` publishing both tables is what makes this work: they
are the tables the running game dispatches on, so they cannot drift from
what it actually listens for. A binding's **`event.code`** is what gets
sent (`Digit1`, `ShiftLeft`), which is also what Playwright accepts.

Beyond the verb list, three things vary per game and are read from the
same plan:

- **`sustained`** — what stays pressed for the whole take. A racer whose
  throttle is released between actions records a car twitching on the
  start line; the throttle is the condition the rest is demonstrated
  under, not one of the things being demonstrated.
- **`warmup`** — how long to tick before capturing. Countdowns, spawn-ins,
  and a brawler that drops attacks until its round reaches FIGHT would
  otherwise consume the take on inputs the game ignores.
- **`look`** — whether sweeping the camera means anything. A static camera
  in a first-person game records a wall; a side-scroller derives its
  camera from the fighters and ignores a sweep; under drag-look a sweep
  fights the game's own opening framing.

Two rules the recorder applies, because getting either wrong records a
game that appears not to respond:

- **Movement is held; a verb is tapped.** One frame of `forward` moves a
  character by centimetres, and a charge-and-release verb does nothing if
  released the same frame. Conversely a discrete verb must be released —
  a semi-automatic weapon will not fire twice, and a held key auto-repeats.
- **Duplicate actions are dropped.** The defaults bind `KeyW` *and*
  `ArrowUp` to `forward`; pressing each would spend half the take twice.

A sustained input also constrains what can be shown alongside it:
anything that negates it is dropped and named in `excluded_actions`
(reverse under a held throttle demonstrates neither), and anything that
discards progress is moved to the end rather than dropped (a respawn is
worth seeing, one second in it throws away the run).

A game with a specific story to tell should declare
`window.__A3GAME_PLAYTEST__ = { actions: [...], sustained?, warmup?,
look? }`, with actions shaped `{id, keys?, taps?, mouse?, click?,
duration?}`. This is strictly better than discovery because only the game
knows where its enemies are. Discovery is the floor, not the ceiling.

### Input Must Be Real

Keys go to `window`, where `A3GameInputRouter` listens; pointer events go
to `host.container`; aiming goes through the router's public `setLook`.
**Nothing reaches in and moves an entity** — a recording that positioned
things directly would be evidence of tweening, not of a playable game.
`report.game_state` makes the distinction checkable: a real take shows
`shotsFired`/`shotsHit` moving and the player taking damage.

One consequence: a synthetic `pointerdown` does not produce a real
`click`, so a recording **never enters pointer lock**, and a first-person
game would sit under its own "click to lock the mouse" prompt for the
whole video. That prompt is correct for a human, so the game is not
changed; the recorder hides that one widget for the take, and a result
banner still re-shows itself through `onStateChanged`. **Change the
recording, never the game, to make a video look right.**

### A Silent Early Return Will Waste A Whole Take

The most expensive failure here was not a crash. An autopilot read
`entities.get('player_car')?.vehicle` where the factory stores the vehicle
itself, so the lookup returned `undefined`, the script early-returned
`{keys: []}` every frame, and the throttle was never touched. No error, no
warning — a 45-minute recording of a car sitting on the start line while
its position dropped from P1 to P4.

So: **anything that degrades silently needs a path that makes it loud.**
The recorder reports `executed_actions[].ok` and `frames` per action, and
fails the run when nothing was captured. A custom plan should do the same
— return a diagnostic field and `throw` if a required entity is still
missing after the first few frames (allow a couple: entities spawn a frame
or two after boot).

**Do a 2-3 s test take before a real one.** It costs a minute and catches
an empty video, which otherwise costs the whole recording.

### Cost

640x360 at 20 fps on SwiftShader, measured:

| Game | Per frame | 10 s of video |
|---|---|---|
| First-person arena | ~0.6 s | ~2 min |
| Racing (track, 4 cars, long view) | ~6 s | ~20 min |

Frames are ~43 KB each and are the bulk of the output; the video is ~0.3
MB/s. 1280x720 is four times the pixels and roughly four times the cost,
not worth it for review footage. Default `timeout` is therefore 900 s.

## Runtime

- `three.runtime.launch_dev_server` - Starts the configured Vite dev
  server and waits until it answers.
- `three.runtime.stop_dev_server` - Stops dev server processes started by
  the same runtime client.
- `three.runtime.preview_bundle` - Serves the built bundle from `dist/`
  for evidence capture.

## Runtime Sessions

- `three.runtime.sessions.join` - Creates or updates a generic
  participant, controller, entity, and control-binding session.
- `three.runtime.sessions.leave` - Removes a participant from the runtime
  session.
- `three.runtime.sessions.heartbeat` - Refreshes participant liveness.
- `three.runtime.sessions.apply_input` - Applies normalized control input
  to a bound runtime entity.
- `three.runtime.sessions.snapshot` - Returns the current generic runtime
  session state.
- `three.runtime.sessions.reset_world` - Requests a generic runtime World
  reset.
- `three.runtime.sessions.clear_entity` - Removes an entity and its
  associated bindings from session state.

Runtime sessions are game-neutral and do not define Fighter, FPS, or
Racing commands. Each call also forwards a generic command to the browser
runtime control channel; the returned `runtime_delivery` field reports
whether delivery succeeded, so session bookkeeping stays usable when no
browser is attached.

## Reflection

- `three.reflection.inspect_artifact` - Inspects a registered staged
  artifact through glTF document reflection and returns structured
  metadata.
- `three.reflection.list_object_names` - Lists animation clip and
  material names carried by an artifact.

## Observation

- `three.observe.check_status` - Reports Node toolchain, package manager,
  project, dependency, framework, dev server, and runtime channel
  readiness.

## A3GamePlayable Public JavaScript Contract

Generated Gameplay Packages may import only from:

```text
@a3game/playable
```

### Enums

- `A3GameControlMode` - Identifies the generic control mode assigned to
  an entity: `EXCLUSIVE`, `PRIORITY`, `ASSISTED`, `OBSERVING`.
- `A3GameLocomotionState` - Represents generic locomotion state for
  runtime snapshots: `IDLE`, `WALK`, `RUN`, `JUMP`.
- `A3GameRuntimeCommand` - Names the generic runtime commands:
  `SYNC_SESSION`, `LEAVE_SESSION`, `APPLY_INPUT`, `WORLD_SNAPSHOT`,
  `RESET_WORLD`, `CLEAR_ENTITY`.

### Data Types

Factory functions normalize and clamp their input, so the same payload is
valid in Python, on the wire, and in the browser.

- `createRuntimeInputState` - Carries normalized movement, look, action,
  and input timing state (`moveX`, `moveY`, `run`, `jump`, `yaw`,
  `pitch`, `sequence`, `timestampSeconds`).
- `createEntitySpawnRequest` - Describes a generic entity spawn request.
- `createParticipantInfo` - Describes one runtime participant.
- `createControllerState` - Describes one generic controller.
- `createControlBinding` - Connects a participant, controller, and
  entity.
- `createEntitySnapshot` - Reports observable generic entity state.
- `createTransform` / `createVector3` - Serializable transform helpers.
- `locomotionStateFromInput` - Derives a locomotion state from one input
  frame.

### Interfaces

Each contract is an abstract base class plus a duck-type validator, so
generated gameplay may extend it or implement the methods on any object.

- `A3GameControllableEntity` - Contract implemented by game-owned
  controllable entities: `getRuntimeEntityId`, `setRuntimeEntityId`,
  `applyRuntimeInput`, `getRuntimeSnapshot`, optional `tick`, `dispose`.
- `A3GameEntityFactory` - Contract implemented by game-owned entity
  factories: `spawnRuntimeEntity(request, { host, assets, session })`.
- `A3GameRuntimeMessageHandler` - Contract for game-owned runtime message
  handling: `handleRuntimeMessage(messageType, payload)`.
- `isControllableEntity` / `assertControllableEntity`,
  `isEntityFactory` / `assertEntityFactory`,
  `isRuntimeMessageHandler` / `assertRuntimeMessageHandler`.

### Components

three.js has no component system, so components attach through
`object.userData.a3game`.

- `A3GameIdentityComponent` - Stores stable runtime identity on a
  game-owned `Object3D`; `attach`, `get`, `findInParents`,
  `setRuntimeIdentity`.
- `A3GameRuntimeEntityComponent` - Connects a game-owned `Object3D` to
  runtime entity state and control; `setRuntimeEntityId`,
  `applyRuntimeInput`, `onRuntimeInput`, `setMotionState`,
  `getRuntimeSnapshot`, `dispose`. Drops out-of-order input frames.

### Subsystems

- `A3GameRuntimeSubsystem` - Registers game-owned factories and
  coordinates generic runtime entity creation; `setEntityFactory`,
  `registerMessageHandler`, `unregisterMessageHandler`,
  `getSessionSubsystem`, `onWorldBeginPlay`, `deinitialize`,
  `spawnEntity`, `handleRuntimeCommand`, `dispatchExtensionMessage`.
- `A3GameWorldSessionSubsystem` - Owns generic participant, controller,
  entity, binding, input, and snapshot session state;
  `registerParticipant`, `markParticipantOffline`, `createController`,
  `registerEntity`, `getEntity`, `removeEntity`,
  `bindControllerToEntity`, `unbindController`, `syncSession`,
  `enqueueInputState`, `consumeLatestInputs`, `getWorldStateSnapshot`,
  `getSessionSnapshot`, `resetWorld`.

### Engine Scaffolding

Unreal supplies these natively; on the web the framework must own them.
They are game-neutral.

- `A3GameRuntimeHost` - Owns renderer, scene, camera, controls, and frame
  loop; `init`, `start`, `stop`, `tick`, `onTick`, `onResize`, `add`,
  `remove`, `getRoot`, `usePerspectiveCamera`, `useOrthographicCamera`,
  `setFrustumHeight`, `attachOrbitControls`, `attachPointerLockControls`,
  `requestPointerLock`, `exitPointerLock`, `isPointerLocked`,
  `detachControls`, `setEnvironment`, `setFog`, `getSunDirection`,
  `getSunPosition`, `raycastFromPointer`,
  `raycast`, `captureFrame`, `getStats`, `dispose`.
  `setEnvironment` also takes `backgroundTexture` (an equirectangular
  image as the visible sky, with the mapping set for you),
  `backgroundBlurriness`, `backgroundRotationDegrees`,
  `environmentRotationDegrees` and `environmentIntensity`.
- `A3GameEnvironmentPreset` - Procedural image-based lighting selected by
  `setEnvironment({ preset })`: `ROOM` (interiors), `GRADIENT` (**the
  outdoor default** — horizon gradient, sun, drifting clouds, and an
  art-directable palette, convolved into the environment map so
  reflections match the visible sky), `SKY` (the Preetham physical model:
  correct, and therefore cloudless), `NONE`. Generated on the GPU at boot,
  so no `.hdr` is downloaded and no licence applies. Whichever preset
  paints a sky records where it put the sun, so `getSunPosition()` places
  a `DirectionalLight` that agrees with it.
- `disposeObject3D` - Recursively disposes geometries, materials, and
  textures.
- **Visual kit** - the look-and-feel building blocks, all game-neutral:
  `createMaterial` with `A3GameMaterialPreset` (metal, painted_metal,
  gunmetal, plastic, rubber, cloth, leather, wood, stone, concrete,
  tarmac, grass, sand, glass, emissive), `createRoundedBox`,
  `createSunLight` (shadow camera fitted to a radius), `createFillLight`,
  `createContactShadow`, `createRadialGradientTexture` (falls back to a
  `DataTexture` with no DOM, so headless world tests can build scenery),
  `createSeededRandom`, `createInstancedFromModel` (one draw call for many
  copies of one generated body), the outdoor dressing set
  `createSkyGradient`, `createDistantRange` (closes the hard edge where a
  ground plane stops), `createCloudLayer` (parallax the dome cannot give),
  `createWaterSurface` (near-mirror reflections of `scene.environment`,
  no second render pass), `createTilingTexture` (fixes clamped
  wrapping, anisotropy 1 and the colour space), and the procedural PBR
  surface pair `createSurfaceTextures` / `createSurfaceMaterial` with
  `A3GameSurfacePattern` (`CONCRETE`, `STEEL_PLATE`, `BLOCKWORK`,
  `PAINTED_PANEL`) — colour, **normal** and roughness maps derived from one
  shared height field, seamlessly tiling, computed into typed arrays so
  they work headless. Reach for these on any large flat surface: the
  realism of a floor or a wall is carried almost entirely by its normal
  map, because a colour map alone cannot respond to a light the player
  walks past, and a floor lit by a moving lamp stays visibly flat without
  one. Deriving all three maps from the same height field is the point —
  joints that are darker but not also recessed read as a printed pattern.
  Plus the imported-model
  utilities `prepareModel`,
  `orientModel`, `forwardAxisYaw`, `A3GameForwardAxis`,
  `A3GAME_RUNTIME_FORWARD_AXIS`, `fitToHeight`, `groundObject`, and
  `measureObject`. Every factory that animates exposes
  `userData.update(delta)`; only the sky dome is ticked by the host.
- `A3GameAssetLibrary` - Loads the asset manifest and resolves artifacts;
  `load`, `has`, `findEntry`, `requireEntry`, `listByType`,
  `loadArtifact`, `instantiate`, `tryInstantiate`, `instantiateOrBuild`,
  `tryLoadTexture`, `applyEnvironment`, `applyMaterialBinding`, `dispose`.
  `tryLoadTexture` and `applyEnvironment` return `null` rather than
  throwing when nothing was staged, exactly like `tryInstantiate`,
  because a game must run before its art arrives.
- `A3GameSceneLoader` - Builds a scene from a published world scene
  graph; `loadWorld`, `buildWorld`, `getEntityObject`,
  `resolveSpawnTransform`, plus `collisionTargets` and `spawnPoints`.
- `A3GameInputRouter` - Converts keyboard, pointer, and gamepad events
  into normalized input frames; `enable`, `disable`, `reset`, `onAction`,
  `isActionHeld`, `setLook`, `sample`, `pipeToSession`.
  `DEFAULT_KEY_BINDINGS` maps WASD/arrows, Shift, and Space.
- `A3GameLookMode` - Selects how mouse movement becomes look input:
  `POINTER_LOCK` (default, first person), `DRAG` (third person, cursor
  stays usable), `ALWAYS` (debug and headless tests).
- `A3GameAnimationDirector` - Wraps `AnimationMixer`; `addClip`,
  `addClips`, `listClipNames`, `mapState`, `mapStates`, `mapStateChain`,
  `mapStateChains`, `play`, `playOnce`, `stopAll`, `update`,
  `attachToHost`, `getState`, `dispose`.
- **Motion kit** - `createAnimatedActor`, `findRiggedHumanoid`,
  `A3GameMotionLibrary`, `autoRigHumanoid`, `createHumanoidSkeleton`,
  `createHumanoidClip`, `createHumanoidClipSet`, `retargetClipToSkeleton`,
  `measureHumanoid`, `A3GameHumanoidBone`, `A3GameMotionState`,
  `A3GameSourceBoneAliases`, `A3GAME_HUMANOID_CLIP_NAMES`. See **Animation**
  above: a generated character has no skeleton and no clips, and this is what
  makes it move.
- **Weapon orientation** - `measureWeapon`, `alignWeaponModel`,
  `principalAxes`. A weapon's facing cannot come from the manifest:
  `forward_axis` is a heuristic written for characters ("generated from a front
  view, so it faces the camera") that is recorded for *every* artifact and can
  only express quarter turns. Applied to a gun it is a coin flip whose losing
  side points the barrel at the player's own face, and it cannot fix a mesh
  authored at an angle — the staged pistol's barrel runs 31 degrees off +Z.
  `alignWeaponModel` derives the barrel line from the first principal axis and
  finds the muzzle end from the one feature every gun has: a stretch of **bare
  barrel** that nothing hangs below, since grip, magazine and stock are all in
  the lower silhouette. It refuses a mesh that is not weapon-shaped, because a
  procedural box that points the right way beats an unrecognisable blob that
  does not. Call it with `orient: false` on `tryInstantiate` so the manifest
  heuristic does not fight it.
- **VFX kit** - `createVfxDirector`, `A3GameVfxDirector`,
  `A3GameParticleSystem`, `A3GameVfxPreset`, `A3GameBeamEffect`,
  `A3GameTrailRibbon`, `A3GameEmitterShape`, `A3GameParticleBlending`,
  `A3GameParticleAppearance`, `A3GameParticleRenderMode`. See **Visual
  Effects** above.
- `A3GameCollisionProbe` - Raycast and volume primitives that stand in
  for the physics engine three.js does not ship; `setTargets`,
  `addTarget`, `removeTarget`, `sampleGround`, `resolveMove`,
  `stepCharacter`, `hitscan`, `overlapSphere`, `sweepSphere`. `hitscan`
  reports `hit`, `point`, `object`, `distance`, `entityId`, and a
  world-space `normal` for impact effects.
- `resolveEntityId` - Walks up an `Object3D` hierarchy for the nearest
  runtime entity id, which is how every probe names what it found.
- `A3GameHudLayer` - DOM overlay HUD; `addText`, `addBar`, `addPanel`,
  `addBanner`, `addCrosshair`, `setValue`, `setValues`, `setVisible`,
  `remove`, `getState`, `dispose`. Widgets sharing an anchor stack in a
  column instead of overlapping. Every widget writes `data-a3game-*`
  attributes so end-to-end tests assert HUD state without screenshots.
- `A3GameCinematicPlayer` - Requests a canonical CG-video task from the
  Browser Serving gateway, waits for its media URL, and manages one temporary
  HTML video element; gameplay owns the trigger and surrounding state.
- `A3GameRuntimeChannel` - Browser side of the runtime control channel;
  `connect`, `disconnect`, `dispatch`, `getHistory`. It always installs
  `globalThis.__A3GAME_RUNTIME__`, which is how generated tests drive
  commands deterministically.
- `bootA3GameRuntime` - Boots host, assets, world, HUD, session, and
  runtime in the conventional order.

### Choosing a Collision Primitive

Picking the wrong one is the most common gameplay bug in a generated
three.js game.

| Question | Primitive |
|---|---|
| What is under me? | `sampleGround` |
| Can I move there? | `resolveMove` / `stepCharacter` |
| What did I shoot, instantly? | `hitscan` |
| What is **near** me? (pickups, melee arcs, chests, checkpoints, triggers) | `overlapSphere` |
| Where did my projectile go this frame? | `sweepSphere` |

`overlapSphere` treats a target with no geometry as a point at its world
position, so bare `Object3D` markers work as triggers. `sweepSphere`
tests the whole travelled segment, which is what stops a fast projectile
tunnelling through a thin wall.

### Boot Options

`bootA3GameRuntime` accepts `container`, `hudContainer`, `manifestUrl`,
`worldUrl`, `worldId`, `hostOptions`, `entityFactory`, and:

- `requireManifest` - fail instead of warning when no asset manifest
  exists. Default `false`;
- `createHud` - suppress the built-in HUD layer. Default: create one when
  `hudContainer` is given;
- `autoBeginPlay` / `autoStart` - hand tick ordering and loop start to
  the game. Both default `true`.

It returns `{ host, assets, sceneLoader, hud, session, runtime, world }`.
Reuse `context.hud`; constructing a second `A3GameHudLayer` on the same
container stacks two overlay roots and makes `getState()` ambiguous.

### Making a Generated Game Look Good

three.js ships **no 3D models** in its npm package — only loaders and
procedural helpers. So a generated game's appearance is decided by the
framework calls below, not by finding a model to load. In rough order of
visible effect per line of code:

1. **An environment map.** `host.setEnvironment({ preset: 'room' })` for
   an interior, `{ preset: 'gradient' }` for outdoors. Both are generated
   on the GPU at boot — no `.hdr` download, no licence. Without one, every
   PBR material has nothing to reflect and reads as flat plastic however
   many lights are added. This is the single largest factor.
   Prefer `gradient` over `sky` outdoors: `sky` is the Preetham model, so
   it is physically correct and completely **cloudless**, and an empty
   gradient overhead is the clearest single tell of a generated scene.
   See *Sky, IBL and Outdoor Dressing* below.
2. **Filmic tone mapping.** `toneMapping: 'ACESFilmicToneMapping'` with
   an exposure near 0.7 outdoors. The default clips highlights to white.
3. **A fitted shadow camera.** `createSunLight({ radius })`. The stock
   `DirectionalLight` shadow camera is a 10-metre box at the origin, so a
   200-metre track gets no shadow at all.
4. **Bevelled edges.** `createRoundedBox()` instead of `BoxGeometry`. A
   mathematically sharp edge cannot catch a highlight; nothing
   manufactured has one.
5. **Honest materials.** `createMaterial('painted_metal')` and friends.
   `metalness: 0.5` describes no real substance — a surface is a
   conductor or it is not. Car paint is a dielectric with a clearcoat.
6. **Contact shadows.** `createContactShadow()` grounds an object for one
   draw call, and works with shadow maps switched off.

Only then does imported art help, and it only helps if it is
*game-ready*. Two things decide that, and both are recorded per artifact
rather than guessed at runtime: it has to be the right size, and it has
to face the right way. A generated mesh at the wrong scale reads as a
toy; one facing the wrong way makes the character strafe for its entire
walk cycle. Neither is a rendering problem, and no lighting change hides
either.

### Sky, IBL and Outdoor Dressing

An outdoor scene loses its realism in four specific places, and none of
them is the geometry. Fix them in this order.

**1. The sky.** `preset: 'gradient'` installs a stylised dome — horizon
gradient, sun disc and glow, and drifting fbm clouds — and convolves the
*same* dome into the environment map, so what a car bonnet reflects is
what the player sees above it. The host ticks the cloud clock itself.

```js
host.setEnvironment({
  preset: 'gradient',
  sunPosition: sunDirection,           // also aims the painted sun
  sky: {
    zenith: 0x2f6fbd, horizon: 0xd3e2ee, ground: 0x5d6a52,
    sunColor: 0xfff0cf, sunSize: 0.006, sunGlow: 220,
    cloudCoverage: 0.44,               // lower = more cloud
    cloudOpacity: 0.92, cloudScale: 1.35, cloudSpeed: 0.008,
    haze: 0.4,                         // forward scattering near the sun
  },
  toneMapping: 'ACESFilmicToneMapping',
  toneMappingExposure: 0.78,
});
host.setFog({ type: 'Fog', color: 0xc2d7e4, near: 70, far: 240 });
```

Tint the fog to `sky.horizon`. Fog in any other colour puts a grey band
in front of the sky instead of dissolving distance into it.

**One sun direction, used four times** — the dome, the
`DirectionalLight`, the environment map and the fog. `host.getSunPosition(d)`
and `host.getSunDirection()` return what the installed sky actually
painted, so a light can be placed from it instead of guessing. Shadows
falling towards the light source is the most common lighting bug in a
generated outdoor scene, and it has no other cause.

**2. Real captured lighting.** A 1k HDRI supplies bounces no light rig
reproduces — a forest floor throwing green up onto a character's chin.
Keep the dome as the *visible* sky and import only the lighting:

```js
await assets.applyEnvironment(host, 'env_forest_sunrise', {
  background: false,             // the gradient dome keeps the sky
  environmentIntensity: 0.85,
});
```

`applyEnvironment` also reads the entry's `sun: {elevation, azimuth}`
hint and aligns `host.sunDirection` to it. To use a photograph *as* the
backdrop instead, pass `background: true` (or a separate
`backgroundReference`), and reach for `backgroundBlurriness` to keep
sharp reflections without the player reading the JPEG artefacts.

**3. Surfaces.** `assets.tryLoadTexture(ref, { repeat: 34 })` returns
`null` when nothing was staged and otherwise fixes the three settings
that are wrong by default: clamped wrapping (so `repeat` only stretches
the last pixel), `anisotropy: 1` (so a ground plane is mush at every
angle a player actually looks from), and the colour space. Multiply a
photograph over existing vertex colours rather than replacing them — the
height-and-slope palette is what makes the terrain read as terrain, and
the texture is what gives a sense of speed.

**4. The horizon and the water.**

```js
host.add(createDistantRange({ radius: 300, height: 96, color: 0x5c6f86,
                              topColor: 0xa9bccf, seed: 91 }), 'environment');
const clouds = createCloudLayer({ count: 16, radius: 260, height: 110 });
host.onTick((d) => clouds.userData.update(d));

const lake = createWaterSurface({ size: 55, normalMap, waveHeight: 0.07 });
host.onTick((d) => lake.userData.update(d));
```

`createDistantRange` closes the hard edge where a ground plane stops —
fog alone cannot, because it fades the ground into the sky and leaves
nothing behind it. `createCloudLayer` parallaxes against the dome's
clouds, which sit at infinity and never move. `createWaterSurface` is a
near-mirror `MeshStandardMaterial` reflecting `scene.environment`, which
costs nothing next to the `Water` addon's second render pass; assign
`material.normalMap` whenever the texture arrives, including after
construction.

**Carve water into the height field, not on top of it.** Gameplay asks
the terrain function where the floor is; a blue plane laid over an
unchanged height field puts the shoreline in a different place for the
player than for the physics.

#### Acquiring the files

`<REPO_PATH>/operators/gen_3d_scene/funcs/scene_assets.py` is a vetted catalogue of
CC0/MIT equirectangular HDRIs, sky photographs, tiling ground textures,
VFX sprites and open-source scene geometry, with a downloader and a
stager that writes `manifest.json` entries the asset library already
knows how to load.

```bash
python -m operators.gen_3d_scene.funcs.scene_assets \
    --games game_archer_explorer --assets env_forest_sunrise tex_grass_ground
```

Licences travel into the manifest as `licence` and `attribution`.
Anything CC-BY **must** display its credit — `scene_assets.attribution_lines(project)`
returns exactly what a project owes. CC-BY-NC and bespoke licences
(DamagedHelmet, Duck, Sponza) were rejected and are not in the
catalogue.

### Upgrading Appearance With Meshy or Tripo

TRELLIS.2 reconstructs a shape from **one** view, so on a synthetic
reference the silhouette is right and everything the camera could not see
is guessed. In-game the guess reads as holes: a character missing the back
of its hood, a pistol whose slide dissolves into the grip, trees with a
bite out of the canopy. No lighting change hides an unclosed mesh.

Meshy and Tripo are text-to-3D services with a multi-view prior and a
texture pass, so for "make this prop look good" they are strictly better.
`<REPO_PATH>/operators/gen_3d_scene/funcs/appearance_assets.py` holds the plan,
generation, GLB optimisation and staging:

```bash
export MESHY_API_KEY=...        # or TRIPO_API_KEY, with --provider tripo
python -m operators.gen_3d_scene.funcs.appearance_assets \
    --games game_arcade_racer --workers 4
```

Entries re-generate an `asset_id` a game **already references**, so
gameplay needs no change to benefit, and the existing `artifact_id` is
kept so anything that resolved it keeps working. The previous mesh is
saved beside it as `*.glb.trellis.bak`.

Four things about this are worth carrying to the next job:

* **Prompt for the whole object, and forbid what must not be in it.**
  "single object", "complete", "standing upright", "no character", "no
  hand". A bow prompt without the last one returns an archer about a
  third of the time.
* **Ask for the shell when the thing has moving parts.** A fused car
  cannot steer or spin a wheel, so `racer_car_body` is prompted with "no
  wheels, hollow open wheel arches" and `racer_wheel` is generated
  separately. The shell carries the shape and the paint; the wheels go
  inside the pivots the handling code already drives. Generate the shell
  and the moving part as two assets — never one.
* **Shrink the textures — but by how close the player gets.** Both
  services return 2048² PBR sets, so one tree arrives as 9 MB and a
  dressed forest is a 60 MB download before the first frame.
  `appearance_assets.optimise()` resizes to `TEXTURE_BUDGET_BY_ROLE` and
  stubs the all-black emissive map these services emit, which also removes
  the self-lit look that `emissiveFactor: [1, 1, 1]` gives everything.
  Two rules that a blanket "halve everything" gets wrong:
  - **A first-person view model is not scenery.** It is 20 cm from the
    camera and fills a quarter of the screen for the whole match, so
    `weapon` and `vehicle` are budgeted at 1536 while `prop` is 1024.
    Measured on the pistol at quality 92: 2048 costs 6.2 MB, 1536 costs
    1.0 MB, 1024 costs 0.5 MB — so 1536 buys 2.25x the pixels of the 1024
    that looked soft, for a megabyte.
  - **Never be the one who introduces the loss.** A resize is a *second*
    lossy generation, and that is what artifacts come from — the original
    softness was quality 88 on top of a halved resolution, not JPEG as
    such. So re-encode once, at 92, and preserve the source's format
    class: keep a data map (normal, metallic-roughness, occlusion)
    lossless only if the service sent it lossless. Converting Meshy's
    already-JPEG normal map to PNG cannot undo the artifacts already in it
    and cost 3.3x the bytes to prove it.
* **Declare the height from the chassis, not from reality.** A generated
  mesh arrives normalised into a unit box, so `scale_hint_metres` is the
  only thing preventing every prop being one metre tall — and it must
  match what the simulation assumes. The racer's wheel is authored at
  0.68 m because that is twice the `TYRE_RADIUS` its pivot heights and its
  rolling rate are built from, not because a tyre is that big.
* **Do not declare a `forward_axis` you cannot know.** A text-to-3D car
  arrives on an arbitrary axis — the staged shell came out lying along X —
  so a declared forward axis is a guess, and a wrong guess is worse than
  none: `orientModel` applies it, the game's own alignment then measures
  the already-turned mesh and applies a second correction, and the two
  compose into a third direction. Leave it empty and let exactly one thing
  own the orientation.
* **A cue that measured nothing must not get a vote.** Where orientation
  *is* derived at runtime, score each cue by how much signal it found and
  let the strongest decide. The racer's old rule — "the roof sits behind
  the middle of the wheelbase" — is true of front-engined cars and
  degenerate on the mid-engine shell the prompt asks for, where the roof
  and body centroids agree to within 0.1 mm. It was deciding the car's
  facing from that 0.1 mm. The nose-is-lower-than-the-tail cue measured
  0.29 on the same mesh, and holds for saloons and supercars alike.

**A character is the exception.** A raw generated humanoid has no
skeleton, so it can only be auto-rigged, and `autoRigHumanoid` refuses a
mesh whose height/width is under about 1.45. Prompt for a **relaxed
A-pose with empty hands** — a figure reconstructed holding a bow measured
0.78 and could never be rigged. Pass a candidate list and let the runtime
decide:

```js
createAnimatedActor(assets, ['explorer_hero', 'explorer_ranger', 'robot_expressive'])
```

An array is tried **in order**, and the first candidate that ends up with
clips wins; a loaded-but-motionless one is kept only as a last resort.
That is deliberately different from `assets.findEntry`, which stops at
the first staged candidate: for a prop "whatever was staged" is right,
but for a character "the better-looking mesh, unless it cannot be made to
move" is only answerable after trying to rig it, and a character that
cannot move is worse than a plainer one that can.

### Sourcing Imported Models

three.js ships no models, so there are exactly two ways to get one, and
they are not interchangeable.

**Generate it** — `Gen3DObjectOperator.run_art_plan`, driving TRELLIS.2
through `<REPO_PATH>/models/gen_3d_object/trellis_2_model.py`. This is the default for
a game's own content, because it produces the props the design asked for
rather than the three that happen to be free:

```python
from models.gen_3d_object.trellis_2_model import Trellis2Model
from operators.gen_3d_object.operator import Gen3DObjectOperator

op = Gen3DObjectOperator(model=Trellis2Model(model_path="…/TRELLIS.2-4B"))
op.run_art_plan("game_archer_explorer", image_model=qwen_edit)
```

Each entry of the art plan
(`<REPO_PATH>/operators/gen_3d_object/funcs/art_plan.py`) names a subject, an asset
type, **a height in metres**, and a **role**. The operator generates a
concept image, reconstructs it, strips the floor the reconstruction
invents, writes an ordinary `3d_object` task output, imports it, and
renders the review sheet.

Its budgets differ from the model wrapper's showcase defaults, and the
role is what picks them — because what costs a frame is how *often* a
thing is drawn, not what it is:

| Role | Triangles | Texture |
|---|---|---|
| `avatar` | 40 000 | 2048 |
| `weapon` / `prop` / `landmark` | 20 000 | 1024 |
| `scenery` (repeated dozens of times) | 8 000 | 1024 |
| *model wrapper default* | *1 000 000* | *4096* |

A million-triangle chair spends a frame budget where nobody looks, and a
4K atlas is 16 MB of download per prop. Get this right **before**
generating: a textured mesh cannot be decimated afterwards without
throwing its UVs away, because TRELLIS.2 bakes the texture after it
decimates.

`forward_axis` is recorded as `+z` with `verified_by="heuristic"`, so
`needs_vision_check` stays true until an agent looks. A guess labelled as
verified is worse than no record, because it stops anyone checking.

Expect a **floor** in the raw output. Image-to-3D crops its input to the
subject's silhouette, so a standing figure touches the frame edge and the
model infers a ground plane — a fifth of the triangle budget, and a 2.4 m
grey pancake once scaled to a character. `funcs/mesh_cleanup.py` removes
it as part of every run and reports what it took; it refuses when the
diagnosis would cost more than 40% of the mesh, on the grounds that the
object is then genuinely a flat thing.

Prompts must describe **one object, facing the camera, on a plain
background, uncropped, evenly lit**. That is not taste. Single-image
reconstruction turns the photographed side into a fixed axis of the
output, bakes cast shadows into the albedo permanently, invents whatever
the image did not show, and simply ends the mesh where a crop ended the
subject.

Generation is the wrong tool for anything that must **articulate**: the
output is one fused body, so a car's wheels cannot spin and a chest's lid
cannot open. Generate the shell, keep moving parts as primitives driven
by gameplay, and swap only the visual.

A humanoid is the one exception, and only because the runtime works around
it: `autoRigHumanoid` fits a skeleton to a static generated body and skins
it, so a generated character can play the authored clip set. That is an
approximation of `<REPO_PATH>/operators/gen_motion`, not a replacement — and it refuses
a mesh whose proportions are not a standing figure. See **Animation**.

**Download it** — `<REPO_PATH>/operators/gen_3d_object/funcs/asset_pack.py`, a
curated, licence-checked pack of three CC0 models:

```python
from operators.gen_3d_object.funcs import fetch_asset_pack
fetch_asset_pack(games=["game_archer_explorer"])
```

Two upstream sources have terms clear enough to use: the
`mrdoob/three.js` example models, and `KhronosGroup/glTF-Sample-Assets`.
Judge a candidate on three things before staging it:

| Check | Why it decides the answer |
|---|---|
| Licence | CC0 and CC-BY are usable, with attribution recorded. CC-BY-**NC**, SCEA, and CryEngine terms are not. Mixamo-derived characters carry terms that do not travel with the file. |
| File size | A 17 MB prop with a 4K texture set that occupies fifty pixels is all download and no gain. A good character is a few hundred KB. |
| Node hierarchy | A model fused into **one mesh** cannot be animated by gameplay. A "car" whose wheels are part of the body can never steer or spin them, and looks worse than a bevelled primitive that can. |

Verified examples: `RobotExpressive.glb` (CC0, 456 KB, 14 clips, faces
+Z, 1.8 m subject) is an excellent character; `ToyCar.glb` (CC0, 5.4 MB)
is a beautiful *prop* and a useless vehicle, because it is a single mesh.

Both paths end the same way: an asset task output, a public
`ThreeClient.assets` import carrying `forward_axis` and
`scale_hint_metres`, and a review sheet. Neither copies a file into
`public/` itself.

### Loading Imported Models

Two calls cover every case, and both leave the game playable with an
empty manifest:

```js
// Content whose visual is built from scratch.
const { object, animations, source } = await assets.instantiateOrBuild(
  'robot_expressive',
  () => buildPrimitiveBody(),      // used when nothing is staged
  { height: 1.8, ground: true, envMapIntensity: 1 },
);

// Content that already exists and is being upgraded.
const loaded = await assets.tryInstantiate('fox', { height: 0.85 });
if (loaded) entity.setVisual(loaded.object, loaded.animations);
```

Both run `prepareModel`, which handles the three things every imported
model needs and no glTF can express:

- **Facing.** The manifest's `orientation.forward_axis` is applied by
  rotating the model inside a wrapper `Group`, and that wrapper is what is
  returned. The split matters: gameplay code and world specs both assign
  `rotation.y` on the object they are handed, so putting the correction on
  the same node would undo it on the first frame. Pass
  `{ forwardAxis: '+x' }` to override the manifest, or `{ orient: false }`
  to opt out.
- **Scale.** Every model arrives in whatever unit its author chose. Of
  three CC0 models staged for these games, one is 4.5 units tall, one is
  79, and one is 0.07. Pass `height`, or let the recorded
  `scale_hint_metres` supply it — never a magic constant.
- **Shadows.** glTF has no notion of casting shadows, so `GLTFLoader`
  leaves `castShadow` false on every mesh. A model that lights correctly
  but floats shadowless above the floor is the most common "why does my
  imported asset look wrong" symptom.

`A3GameSceneLoader` goes through the same path, so a published World gets
the orientation correction too.

Also set `frustumCulled: false` on a skinned character: it is culled on
its bind-pose bounds and otherwise blinks out at the screen edge.

Pass `ground: true` for anything placed by its feet. A generated model's
origin is wherever the reconstruction left it, and skipping this is why an
imported character stands with its shins in the floor or floats above it.

**A character needs one more step than a prop.** `tryInstantiate` answers
"did a file exist"; a character also has to answer "can it move". Use
`createAnimatedActor` for anything animated and branch on its
`motionSource` — see **Animation**. Swapping in a clipless model destroys
the procedural limbs the entity was posing, so the failure mode is a
handsome statue rather than a visible error.

Separate the gameplay transform from the drawn body — `entity.object`
carries position, facing, and the hit volume; `entity.visual` is a child
that `setVisual` can replace. Swapping a model then disturbs no physics,
and the procedural body remains the fallback that headless tests use.

Name **a list** of candidates, not one asset, wherever a game reaches for
art: `assets.has(['explorer_ranger', 'robot_expressive'])` prefers the
model generated for this game, accepts the CC0 stand-in, and falls through
to the procedural body. Which of the three happens is not a decision that
belongs in gameplay code, and a list keeps it out.

### Dressing A Scene With Generated Art

Swapping a *character* is one model. Dressing a *scene* is seventy-four
trees, and there the naive approach — one instantiated model per
placement — is what makes a generated game unplayable rather than
merely plain: seventy-four draw calls and seventy-four copies of the
geometry.

`createInstancedFromModel(model, count)` turns one prepared, non-skinned
model into an `InstancedMesh`, so a whole wood costs one draw call and one
copy of the geometry, with the per-copy transforms on the GPU. It returns
`null` for skinned or multi-part models rather than silently flattening
them, so a caller can fall back to what it was already drawing.

Two rules make this safe to retrofit into a finished game:

1. **Keep the primitive as the collision volume.** Clear its `visible`
   flag and leave it in the collision list. `Raycaster` does not test
   visibility and `Mesh.raycast` only requires a material, so an invisible
   box still stops an arrow — more cheaply than a 20 000-triangle prop
   would, and more fairly: cover that looks solid on screen *is* solid in
   the simulation.
2. **Never add a collider that was not there before.** Pure decoration —
   roadside palms, backdrop street furniture — must stay out of the
   collision list, or an art pass silently retunes the gameplay.

The four generated games each keep this in one function
(`dressScenery`, `dressArena`, `dressStage`, `dressTrack`) called straight
after the world is built, and each returns a count per kind so a test can
assert what happened.

### Building Without Imported Assets

A mechanic or prototype task often has no `.glb` to import. That is a
supported configuration:

- pass `requireManifest: false`, and check `assets.available` before
  reaching for an artifact;
- build content from three.js primitives (`createRoundedBox`,
  `CapsuleGeometry`, `CylinderGeometry`, `ConeGeometry`, extruded
  ribbons, displaced `PlaneGeometry`);
- draw textures into a `<canvas>` and wrap them in `CanvasTexture` rather
  than hard-coding an image URL — but keep that inside the build
  function, so headless tests can import the module;
- generate scenery from a **seeded** pseudo-random sequence
  (`createSeededRandom`), never `Math.random`, so the world is identical
  on every reload and in tests;
- still declare lights. A world with no light renders every PBR material
  black regardless of how the geometry was made.

A procedurally built game also skips `A3GameSceneLoader`: it owns its own
`buildX(host)` function and returns its own collision-target list.

## Framework Boundaries

`A3GamePlayable` provides runtime contracts and engine scaffolding only.
It does not provide a concrete Character, Pawn, Controller, GameMode, HUD
content, weapon, vehicle, combat rule, scoring rule, or game-specific
input mapping.

Generated projects own concrete gameplay implementation. The Arena
Fighter, FPS, Racing, Explorer, and Motion/VFX example packages are
read-only references and are not dependencies or success criteria.
`motion-vfx-example` contains no game — only the two patterns every genre
needs and that are easy to get wrong.

### Choosing A Reference Example By Camera Perspective

`<REPO_PATH>/engine_adapters/three_js/examples/` holds four playable references, and
they are indexed by **camera perspective rather than by genre**, because
the camera decides far more of a game's code than the genre does. Two
shooters with different cameras share almost nothing; a racing game and an
exploration RPG with the same camera share their entire input and camera
layer. Read the one whose perspective matches the brief.

| Perspective | Example | Owns the camera | Movement basis |
| --- | --- | --- | --- |
| First person | `fps-example` | The entity *is* the camera | Entity yaw, from the input frame |
| Second person | `arena-fighter-example` | The match, not either fighter | The axis between the two fighters |
| Third person, chase | `racing-example` | The vehicle's heading | Vehicle-relative |
| Third person, orbit | `explorer-example` | The camera rig, which publishes yaw | **Camera-relative** |

What each perspective obliges:

- **First person** (`fps-example`). The camera is parented to the player
  pivot, aiming comes from the input frame's yaw/pitch, and no character
  is visible. Two consequences: attaching `PointerLockControls` *as well*
  makes both the controls and `tick()` write the same camera every frame,
  and the game needs a **view model**. That view model is the most closely
  inspected asset in the project — it earns a larger texture budget
  (`TEXTURE_BUDGET_BY_ROLE.weapon` is 1536, not 1024). See *Upgrading
  Appearance With Meshy or Tripo*.
- **Second person** (`arena-fighter-example`). Both fighters must stay
  framed, so the camera belongs to the match and neither entity may move
  it. Movement is locked to the axis between the two, which is what makes
  a fighting game read as a duel rather than as two people in a field.
- **Third person** (`racing-example`, `explorer-example`). The camera is
  an independent object that trails its subject, so it needs **positional
  lag** — without it, every small correction shakes the whole screen — and
  movement must be **camera-relative**: pressing forward means "away from
  the camera", never "along the character's facing". The two sub-cases
  differ only in who owns the yaw, and that is not a detail: whoever owns
  it must *publish* it, because two objects deriving it separately drift
  apart within seconds.

`explorer-example` is additionally the reference for **walking on
non-flat ground**. Its `terrainHeight(x, z)` is a plain exported function
used by the mesh builder, the character, the camera, every arrow and every
prop placement. One function with many callers is the only arrangement in
which the player cannot walk through a hill, and it is why the example has
a `src/world.js` ahead of its `src/explorer.js`.

### Name Modules After The Game, Not After The Framework

Every generated game under `<REPO_PATH>/test_data/outputs/` names its modules for
**what the thing is in that game**, and none of them contains an
`entity.js` or a `factory.js`:

| Role | arcade-racer | archer-explorer | fps-pistol-arena | sidescroll-brawler |
| --- | --- | --- | --- | --- |
| The controllable | `vehicle.js` | `explorer.js` | `player.js` | `fighter.js` |
| The space | `track.js` | `world.js` | `arena.js` | `stage.js` |
| The opposition | `rivals.js` | — | `enemy.js` | `ai.js` |
| Other systems | — | `arrow.js`, `chest.js` | — | — |
| Rules | `rules.js` | `rules.js` | `rules.js` | `rules.js` |
| Boot + spawner + camera | `index.js` | `index.js` | `index.js` | `index.js` |

Follow it, for two reasons that outlast taste:

- **`factory.js` and `entity.js` name a framework interface, not a thing
  in the game.** `A3GameEntityFactory` is one method; a file named after
  it says nothing about what gets spawned, and every game would have an
  identically-named file with completely different contents. The spawner
  is small and exists only to hand the runtime one object, so it lives in
  `index.js` beside the boot function it serves — which is also where a
  reader looking for "how does this game start" will already be.
- **A pattern should port without renaming.** An adapted module keeps its
  name from example to game, so a diff stays readable and nobody has to
  work out that `entity.js` and `fighter.js` are the same idea.

Split a module out when the thing **outlives or ticks independently of**
whatever created it — `arrow.js` exists because arrows outlive the shot and
are stepped separately from the character. Do not split by which framework
interface a class happens to implement.

`explorer-example` mirrors `game_archer_explorer` module for module and is
the current model; `arena-fighter-example` predates the convention.

## Web-Specific Obligations

These have no UE5 equivalent and are the most common failure modes in
generated three.js games:

1. **Dispose explicitly.** three.js never frees GPU memory. Every entity
   must dispose its geometries, materials, and textures; use
   `disposeObject3D(root)`.
2. **Never hard-code URLs.** Resolve every asset through
   `A3GameAssetLibrary` and every world through `A3GameSceneLoader`.
3. **Frame-rate independence.** Multiply by the tick delta. Never assume
   60Hz. For smoothing and decay use `Math.exp(-k * delta)` rather than a
   per-frame constant such as `value *= 0.9`.
4. **Colour space.** Colour textures need `SRGBColorSpace`; data textures
   (normal, roughness, metalness, AO) must not.
5. **Lighting is required.** A world with no light and no environment map
   renders PBR materials black. A world with lights but no environment map
   renders them flat: `setEnvironment({ preset })` is what supplies
   reflections.
6. **Pointer lock needs a gesture.** Call `host.requestPointerLock()`
   from a click or key handler, never at boot.
7. **One camera author.** `PointerLockControls` writes the camera every
   pointer event. A game that derives `yaw`/`pitch` from the input frame
   must use `requestPointerLock()` instead of
   `attachPointerLockControls()`, or the two fight each frame and the view
   jitters.
8. **One render loop.** Subscribe with `host.onTick`; never call
   `requestAnimationFrame` in gameplay code.
9. **Update the world matrix before probing.** A raycast reads
   `matrixWorld`, and the renderer only refreshes it at render time. An
   object that spawns or moves and is probed in the same frame must call
   `object.updateMatrixWorld(true)` first, otherwise it is tested at the
   origin. `overlapSphere` and `sweepSphere` handle their targets, but a
   moving entity must maintain its own.
10. **Tick order decides input latency.** The runtime subsystem's tick
    consumes queued input. Register `input.pipeToSession()` and any AI
    that enqueues frames *before* `runtime.onWorldBeginPlay()`; boot with
    `autoBeginPlay: false` when the game needs that control. Otherwise
    every input frame arrives one frame late.
11. **Speed caps are not forces.** Terminal speed comes from the
    force/drag balance, so a power-up that only raises a `maxSpeed` clamp
    does nothing. Change the acceleration too.
12. **An imported model is never scene-ready.** glTF carries no scale
    convention, no shadow flags, and no facing convention, so a raw
    `instantiate()` yields a skyscraper or a speck, casting no shadow,
    pointing in an arbitrary direction. Go through
    `tryInstantiate`/`instantiateOrBuild` with a `height`, and record the
    facing axis with `three.assets.set_orientation` — never with a
    `rotation.y += Math.PI` inside an entity, which is invisible to every
    other consumer of that asset and is erased the moment the entity
    assigns its own facing.
13. **Testability over screenshots.** Assert `getRuntimeSnapshot()`,
    `hud.getState()`, and rule state; drive input through
    `globalThis.__A3GAME_RUNTIME__`.
14. **A model with no clips is not an upgrade.** Generated characters
    arrive with `animations: []` and no skeleton, and swapping one in
    destroys the procedural limbs the entity was posing. Go through
    `createAnimatedActor` and keep the procedural body when it reports
    `motionSource: 'none'`.
15. **Effects are batched or they are a leak.** One `Sprite` per spark is
    one draw call per spark and a second render loop. Register effects on
    an `A3GameVfxDirector`, which pools them, ticks them once, and frees
    them in `dispose()`.

## Generated Test Pattern

Generated tests run under `vitest` in the `node` environment: no GPU, no
canvas, no screenshots. The reliable shape is:

1. build the entity directly against a small **host double** that
   implements only `add`, `getRoot`, `onTick`, and `camera`;
2. build an `A3GameCollisionProbe` over plain geometry, and call
   `updateMatrixWorld(true)` on it;
3. drive the entity with `createRuntimeInputState({ ..., sequence })` —
   the sequence must increase, or the frame is dropped as out of order;
4. step with an explicit delta;
5. assert the observable snapshot and rule state, and assert that state
   survives `JSON.stringify`.

Make test geometry generously larger than the distance the entity can
travel during the test. A car that drives off the edge of its test plane
silently becomes "airborne", and every surface assertion then reports the
wrong answer.

The framework ships one test of its own,
`packages/a3game-playable/tests/orientation.spec.js`, installed by
`three.plugin.install_framework`. It appears in every generated project's
report and asserts the orientation contract — including that a game
assigning `visual.rotation.y` cannot undo a recorded facing correction.
Generated tests do not need to duplicate it; they must not break it.

## Generated Output Layout

A generated playable game is a self-contained Vite project written to the
task output directory resolved by `<REPO_PATH>/pipeline/common/paths.py`:

```text
test_data/outputs/<game_id>/<run_id>/mechanic/<task_id>/
├── package.json          three + vite + vitest, `packages/*` workspace
├── vite.config.jsaliases @a3game/playable and the game package
├── index.html            #a3game-viewport, #a3game-hud, boot overlay
├── src/main.js           imports the Gameplay Package, nothing else
├── launch.sh             dev | build | preview | test
├── public/assets/        manifest.json (empty when nothing is imported)
└── packages/
    ├── a3game-playable/  installed by three.plugin.install_framework
    └── <game-name>/      the generated Gameplay Package + tests
```

Create the project with `three.project.create` (or
`<REPO_PATH>/scripts/engine_install/three_js/create_project.sh`) and let
`three.plugin.install_framework` place the framework. Never copy the
framework by hand, and never build these paths by string concatenation.

## Serving a Game for Review

The dev server binds `127.0.0.1`, which is invisible from any other
machine — the usual reason a reviewer "cannot open the game". Generated
projects therefore read two environment variables:

- `A3GAME_DEV_HOST` — set to `0.0.0.0` to accept outside connections;
- `A3GAME_DEV_PORT` — override the per-game port.

`vite.config.js` also sets `allowedHosts: true`, because Vite 6 rejects a
request whose `Host` header it does not recognise, which is exactly what
an SSH forward or tunnel produces. Through the adapter the same switch is
`ThreeClient(host="0.0.0.0")`, or `run.sh --dev-host 0.0.0.0`.

Nothing about this needs a GPU or a display on the server: WebGL runs in
the reviewer's browser, and the server only serves modules. Headless
verification of *correctness* is therefore `vitest` plus `vite build`,
never a screenshot.

A **playtest recording** is the separate question of whether the game
plays, and it does run WebGL on the server — on SwiftShader, at a fraction
of real time. See **Playtest** above. Keep the two apart: a recording is
review evidence and a `game_state` snapshot, not a benchmark result.
