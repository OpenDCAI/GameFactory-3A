# three.js Development Skill and Public API Reference

## Scope and navigation

Use for three.js project creation, gameplay, assets, Worlds, and validation.
Baseline: `ThreeClient` API `v1`, three.js r185 (`three@0.185.0`, compatible r185 peers), Node 20+, project Vite/Vitest versions.
Authority: `engine_adapters/three_js/`. Paths are repository-relative unless noted.
Sections 2 and 18 index public APIs; following sections define their contracts and limits, not private implementations.

| Task | Sections |
|---|---|
| Create a project or install gameplay | 1–2 |
| Import resources, inspect orientation, bind materials | 3 |
| Assemble or publish a World | 4 |
| Boot a browser game and configure rendering | 5–6 |
| Build terrain, props, paths, and backgrounds | 7 |
| Implement entities, input, sessions, or remote commands | 8–10 |
| Animate actors | 11 |
| Add wind, water, surface flow, or VFX | 12–13 |
| Implement collision and HUD | 14–15 |
| Validate, record, and release resources | 16–17 |
| Check the full public surface and source locations | 18 |

## 1. Public boundaries and project workflow

- Python: `from engine_adapters.three_js import ThreeClient`. JS: `@a3game/playable` for framework APIs, `three` for native types.
- Put gameplay in `packages/<game-name>/`; extend reusable code in the canonical plugin and install it through project tooling.
- Avoid Python `_internal`, direct namespace-client construction, and JS deep imports. Treat `examples/` as read-only references.
- Import assets/packages by registered task identity (section 3).
- Follow packet permissions: Mechanic generators write tests, not authoritative results; UI generators also respect screenshot restrictions and preserve Mechanic code.

```text
<resolved-project-directory>/
├── package.json                  three/vite/vitest; packages/* workspaces
├── vite.config.js
├── index.html                    viewport and HUD containers
├── src/main.js                   import and start the Gameplay Package
├── public/assets/manifest.json   maintained by asset import tooling
└── packages/
    ├── a3game-playable/          installed framework
    └── <game-name>/
        ├── src/index.js         startup, factory, system assembly
        ├── src/world.js         layout and static collision
        ├── src/player.js        concrete entity; name by game role
        ├── src/rules.js         rules and observable state
        └── tests/
```

Paths: `pipeline.common.paths.task_output_dir(game_id,task_kind,task_id,run_id='default',create=True)`;
`eval_output_dir` uses the same arguments. For queries use create=False; root override: AAAGF_OUTPUT_ROOT. No three.pipeline namespace.

Workflow: project.create → plugin.install_framework → implement gameplay/entrypoint → project.install_dependencies → project.validate → authorized build/browser validation.
The scaffold provides no concrete Character/Controller/GameMode/weapon/vehicle.

### Client configuration and results

```python
from engine_adapters.three_js import ThreeClient

three = ThreeClient(project_path=resolved_project_path)
info = three.get_environment_info()
if not info["ok"]:
    raise RuntimeError(info["errors"])
```

Constructor: `ThreeClient(project_path=None, three_root=None, api_version='v1', *, host=None, port=None,
runtime_host=None, runtime_port=None, package_manager=None, runtime_transport=None, node_root=None)`.
Read `three.api_version`; use `get_environment_info()` for effective paths and URLs instead of reaching into `three._config`.
`project_path` accepts a project directory or `package.json`. Invalid configuration can raise `ValueError` during construction.

| Setting | Default / configuration |
|---|---|
| API version | `v1` only |
| Dev host / port | Loopback host and adapter default port from `engine_adapters/three_js/config.py`; Python reads `A3GAME_THREE_HOST`, `A3GAME_THREE_PORT`, with `THREE_HOST`, `THREE_PORT` compatibility |
| Runtime endpoint | Separate loopback host/port defaults from the same module; `A3GAME_THREE_RUNTIME_HOST`, `A3GAME_THREE_RUNTIME_PORT` |
| Package manager | `npm`; supported `npm, pnpm, yarn`; `A3GAME_THREE_PACKAGE_MANAGER` |
| Runtime transport config | `http`; `http, websocket` accepted by Python config; see actual delivery limits in section 10 |
| Project / adapter roots | `A3GAME_THREE_PROJECT`, `A3GAME_THREE_ROOT` |
| Node toolchain | `node_root` / `A3GAME_NODE_ROOT` |
| Registry and evidence paths | `A3GAME_THREE_DATA_ROOT`, `A3GAME_THREE_ARTIFACT_REGISTRY`, `A3GAME_THREE_WORLD_REGISTRY_ROOT`, `A3GAME_THREE_PREVIEW_ROOT` |

Vite separately reads `A3GAME_DEV_HOST` and `A3GAME_DEV_PORT`; Python launch passes explicit --host/--port. Ports:1–65535; dev and runtime endpoints differ.
Do not hardcode host/port literals in generated code or commands: pass them explicitly or read the effective values from
`get_environment_info()`, the documented environment variables, or the CLI flags, so a busy port can be changed without editing sources.
Keep services on loopback/protected networks;0.0.0.0 and allowedHosts:true broaden access.

Results: `{ok,operation,artifacts,diagnostics,warnings,errors,payload}`. Check payload as well as ok (not gameplay proof).
`contracts/` defines ThreeDiagnostic/ThreeOperationResult with to_dict and success/failure helpers, not a three.contracts namespace.
Configuration/caller exceptions are not always wrapped.

## 2. Complete Python facade operation index

Use these 13 namespaces and the nested `runtime.sessions` API through the facade.
The index covers 57 operations including `get_environment_info()`, excluding constructors and the `api_version` property.
There are no `three.scene`, `three.schema`, `three.controller`, or `three.network` namespaces.
Signatures use Python keyword-only `*`; omitted `options` dictionaries are detailed in the relevant section.

### Projects, packages, builds, and services

| Operation | Signature / purpose |
|---|---|
| `get_environment_info()` | Effective project/toolchain/registry/runtime information |
| `project.get_info()` | Project information |
| `project.create(*, dry_run=False)` | Create at the configured project path |
| `project.install_dependencies(*, timeout=None, dry_run=False)` | Install using the configured package manager |
| `project.validate()` | Validate project structure/configuration |
| `plugin.install(source, *, replace_existing=False, dry_run=False)` | Install a registered Gameplay Package |
| `plugin.install_framework(*, dry_run=False)` | Install the canonical `@a3game/playable` package |
| `plugin.list()` | List installed packages |
| `build.project(*, target='build', configuration='production', clean=False, dry_run=False, timeout=None)` | Invoke the project build |
| `runtime.launch_dev_server(*, script='dev', world='', extra_args=(), wait_timeout=60.0, dry_run=False)` | Launch and wait for the dev service |
| `runtime.stop_dev_server(process_id)` | Stop a process managed by this client instance |
| `runtime.preview_bundle(*, script='preview', extra_args=(), wait_timeout=60.0, dry_run=False)` | Serve an existing build |
| `observe.check_status(*, timeout=5.0, check_runtime=True)` | Check toolchain, project, dependencies, dev server, and optional runtime endpoint |

- create rejects existing package.json. Package source needs a directory/top-level package.json with a name.
- An @a3game/playable dependency/peerDependency requests framework installation; install dependencies afterwards.
- Replacement synchronizes without guaranteed stale-file removal; asset overwrite differs (section 3).
- Dry-runs check prerequisites; preview requires dist/. Launch timeout does not stop the process automatically.
- check_runtime=False skips remote checks; readiness neither verifies WebGL nor necessarily fails on missing framework files.

### Assets, animation, bindings, and inspection

| Operation | Signature |
|---|---|
| `assets.import_asset` | `(source, asset_type, *, destination='', options=None)` |
| `assets.import_avatar` | `(source, *, destination='', options=None)` |
| `assets.import_scene` | `(source, *, destination='', options=None)` |
| `assets.import_prop` | `(source, *, destination='', options=None)` |
| `assets.import_weapon` | `(source, *, destination='', options=None)` |
| `assets.import_material` | `(source, *, destination='', options=None)` |
| `assets.import_texture` | `(source, *, destination='', options=None)` |
| `assets.import_effect` | `(source, *, destination='', options=None)` |
| `assets.import_audio` | `(source, *, destination='', options=None)` |
| `assets.import_motion` | `(source, *, skeleton='', destination='', avatar_name='', options=None)` |
| `assets.validate` | `(source, asset_type, *, destination='', options=None)` |
| `assets.resolve_source` | `(source, *, asset_type='')` |
| `assets.list` | `(asset_type='', *, root='assets/imported')` |
| `assets.list_registered` | `(asset_type='')` |
| `assets.get_metadata` | `(artifact_id)` |
| `assets.set_orientation` | `(reference, *, forward_axis='', up_axis='', yaw_offset_degrees=None, pitch_offset_degrees=None, roll_offset_degrees=None, scale_hint_metres=None, pivot='', verified_by='', notes='')` |
| `assets.get_orientation` | `(reference)` |
| `assets.analyze_orientation` | `(reference, *, asset_type='')` |
| `assets.write_manifest` | `()` |
| `animation.import_motion` | `(source, *, skeleton='', destination='', avatar_name='', options=None)` |
| `animation.resolve_skeleton` | `(artifact_id)` |
| `animation.validate_compatibility` | `(motion_artifact_id, skeleton_artifact_id)` |
| `bindings.bind_pbr_material` | `(*, asset_id, source, mesh_assets, destination='', options=None)` |
| `reflection.inspect_artifact` | `(artifact_id, *, refresh=False)` |
| `reflection.list_object_names` | `(artifact_id)` |
| `preview.render_artifact` | `(reference, *, views='all', size=384, target_samples=2200000, output_dir='')` |
| `preview.render_source` | `(source, *, asset_type='', views='all', size=384, target_samples=2200000, output_dir='')` |
| `preview.orientation_report` | `(reference, *, asset_type='', size=384, output_dir='')` |
| `preview.list_views` | `()` |

Use `import_asset(source, 'environment')` or `import_asset(source, 'static_mesh')`; no dedicated import_environment/import_static_mesh wrapper exists.
`animation.resolve_skeleton` reads skin metadata. Compatibility checks are metadata/clip/node checks, not bind-pose validation or full retargeting.
`reflection.list_object_names` exposes recorded animation/material names and geometry counts; do not assume a full scene-node-name enumeration.
`refresh=True` rereads staged content for inspection; do not assume that it rewrites the artifact registry.

### Worlds and sessions

| Operation | Signature |
|---|---|
| `world.build` | `(source, *, options=None)` |
| `world.create_draft` | `(spec, *, draft_id='', project_id='', metadata=None)` |
| `world.validate_draft` | `(draft_id)` |
| `world.publish_draft` | `(draft_id)` |
| `world.list_packages` | `(*, project_id='', world_id='')` |
| `world.get_scene_graph` | `(draft_id)` |
| `runtime.sessions.join` | `(*, world_id='', participant_id='', user_id='', avatar_artifact_id='', idle_motion_artifact_id='', move_motion_artifact_id='', controller_kind='human', control_mode='exclusive', priority=0, transform=None, parameters=None)` |
| `runtime.sessions.leave` | `(*, participant_id='', controller_id='')` |
| `runtime.sessions.heartbeat` | `(controller_id)` |
| `runtime.sessions.apply_input` | `(controller_id, *, move_x=0.0, move_y=0.0, run=False, jump=False, yaw=0.0, pitch=0.0, seq=0)` |
| `runtime.sessions.snapshot` | `(*, world_id='')` |
| `runtime.sessions.reset_world` | `(*, world_id='')` |
| `runtime.sessions.clear_entity` | `(*, participant_id='', controller_id='', entity_id='', destroy_object=True)` |

### Testing and recording

`testing.run_automation_tests(*, runner='vitest', test_filter='', script='', report_path='', timeout=None, dry_run=False)`.

`playtest.record(*, output_dir, url='', action_plan=None, hold=None, warmup=None, look=None,
playwright_root=None, browser_executable=None, browsers_path=None, library_path=None, ffmpeg=None,
duration=14.0, fps=20, width=1280, height=720, timeout=900.0, dry_run=False,
mode='gameplay', preview=False, allow_partial_plan=False, source_hash=None)`.

Read section 16 before treating either result as validation evidence.

## 3. Resource identity, loading, orientation, and PBR binding

### Registered source descriptors

Pass a mapping, not a raw file path, to source-based imports, World build, source preview, material binding, and package installation:

```python
source = {
    "game_id": "example_game",
    "run_id": "default",
    "task_kind": "3d_object",
    "task_id": "example_crate",
    "artifact_key": "model_path",
}
```

- Replace example values with registered IDs. Require game_id/task_id; run_id defaults `default`.
- Inferred task_kind: scene→3d_scene, motion→motion, audio→audio, others→3d_object. Package installation requires explicit task_kind.
- Omit artifact_key only for exactly one non-empty `*_path` in meta.json. Metadata identities must match; paths must stay inside the task directory.
- Directories are accepted for generic scene/effect/environment imports; bindings/packages have separate rules.
- Prefer GLB/glTF; let importers maintain sidecars/registry/manifest. Check license, budgets, bounds, skins, and clips.
- Options include asset_id, category, replace_existing, orientation; arbitrary options may be metadata-only, not conversion or retargeting.
- **Asset replace_existing=False may warn and overwrite**; it is not the package installer's overwrite guard.

Distinguish these records:

| Record | Relevant fields / access |
|---|---|
| Registered artifact | `backend_class, backend_path, runtime_capabilities`; `assets.get_metadata(artifact_id)` returns records in `artifacts[]` |
| Runtime manifest entry | `artifact_id, asset_id, type, class, url, capabilities, orientation, material_bindings, animations, bounds, sun` |
| World graph asset references | Smaller `assets` mapping; not a replacement for loading the manifest into AssetLibrary |

### JavaScript asset library

`new A3GameAssetLibrary({manifestUrl, baseUrl, requireManifest, dracoDecoderPath, ktx2TranscoderPath, renderer})`.
Default manifest/decoder paths are `/assets/manifest.json`, `/draco/`, `/basis/`, scoped through baseUrl.
`baseUrl` defaults to the browser document directory and `/` in Node; keep it consistent with the build base and served page path.

| Method | Result / behavior |
|---|---|
| `resolveUrl(url)` | Rebase project resource paths; preserve already-prefixed paths, external protocols, data/blob URLs, and `//` URLs |
| `await load()` | Fetch/index manifest; return library; inspect `available, manifest, warnings` |
| `has(reference)`, `findEntry(reference)`, `requireEntry(reference)` | Boolean, entry/null, or entry/throw; candidate arrays choose the first registered candidate |
| `listByType(type)` | Matching manifest entries |
| `await loadArtifact(reference)` | Cached model/texture/audio buffer/JSON result; not every resource is instantiable |
| `await instantiate(reference)` | Strict independent model instance with cloned materials/skins and bindings; no orientation/height preparation |
| `await tryInstantiate(reference, options={})` | Prepared instance or null; loading failures enter warnings |
| `await instantiateOrBuild(reference, fallback, options={})` | Prepared asset or synchronous Object3D fallback; inspect `source` |
| `await tryLoadTexture(reference, options={})` | Configured texture or null; clones by default, `clone:false` modifies shared source state |
| `await applyMaterialBinding(object, bindingUrl)` | Apply a validated binding; throw on invalid targets/options |
| `await applyEnvironment(host, reference, options={})` | Environment result or null for unavailable assets; invalid arguments/host operations may throw |
| `await dispose()` | Release cached/library-owned resources |

Do not mutate a cached loadArtifact model as an independent entity.
Prepared results expose `object` for game transforms and `model` for the authored model root.
Use fallback geometry when an optional resource is absent; a fallback is not automatically re-normalized or reoriented.

```js
const loaded = await assets.tryInstantiate(['scene_crate', 'crate_fallback'], {
  height: 0.8, ground: true, envMapIntensity: 1,
});
if (loaded) host.add(loaded.object, 'environment');
```

Here and in subsequent snippets, obtain `assets, host, runtime, session, sceneLoader, hud` from the boot context unless explicitly constructed.

### Orientation and model utilities

Use metres, right-handed +Y up, and runtime local forward -Z. Verify authored orientation visually; bounds alone do not identify the face.
Author-axis conversion to -Z: `+z → 180°`, `-z → 0°`, `+x → 90°`, `-x → 270°`.
Store `forward_axis, up_axis, yaw_offset_degrees, pitch_offset_degrees, roll_offset_degrees, scale_hint_metres, pivot, verified_by, notes` through orientation APIs.
JS overrides use `forwardAxis, yawOffsetDegrees, pitchOffsetDegrees, rollOffsetDegrees`; `orient:false` disables orientation correction.
Use `height` to override scale_hint_metres; `ground:true` aligns the bounding-box bottom.
Do not apply the same correction in both metadata and gameplay. Prepared outer transforms preserve inner asset normalization.

| Utility | Contract |
|---|---|
| `measureObject(object)` | World-space `Box3` |
| `fitToHeight(object, metres)` | Resize in place; return object |
| `groundObject(object, {horizontal=true}={})` | Ground and optionally center in place |
| `forwardAxisYaw(forwardAxis, runtimeForwardAxis='-z')` | Radian axis conversion |
| `orientModel(object, options={})` | Add orientation corrections to existing Euler rotation; repeated calls accumulate |
| `prepareModel(object, options={})` | Orientation, scale, grounding, render flags; does not create a safe wrapper itself |
| `principalAxes(points)` | From Vector3 points, return centroid and axes with vector/deviation |
| `measureWeapon(object, options={})` | Geometry-based weapon-axis confidence measurement |
| `alignWeaponModel(object, options={})` | Measurement plus applied flag; replace quaternion when accepted |

Weapon measurement options: `lowerBandFraction, minElongation, maxThicknessRatio, minMuzzleMargin, stride`.
Alignment additionally accepts `requireConfident`; default acceptance requires weaponlike and confident geometry.
Do not infer a working `tipFraction` option from comments alone; it is not read by this implementation.

### Material bindings

Call `bindings.bind_pbr_material(asset_id=..., source=..., mesh_assets=[artifact_id, ...], options=...)`.
Use registered artifact IDs for mesh_assets, not mesh names, asset IDs, or file names.
Bindings resolve targets to asset URLs; runtime matching uses the loaded asset identity, not arbitrary material names.

Supported `options.type`: MeshStandardMaterial, MeshPhysicalMaterial, MeshBasicMaterial, MeshLambertMaterial,
MeshPhongMaterial, MeshMatcapMaterial, MeshToonMaterial.

| Option family | Supported binding keys |
|---|---|
| Texture slots | `map, normalMap, roughnessMap, metalnessMap, aoMap, emissiveMap, alphaMap, displacementMap, clearcoatMap, sheenColorMap` |
| Scalars | `roughness, metalness, clearcoat, clearcoatRoughness, sheen, sheenRoughness, transmission, ior, iridescence, emissiveIntensity, normalScale, aoMapIntensity, envMapIntensity, opacity` |
| Colors | `color, emissive, sheenColor, attenuationColor` |
| Flags | `transparent, side, flatShading, wireframe, depthWrite, vertexColors` |

Explicit texture paths override filename-based slot discovery; unknown options warn and are ignored.
Inspect returned `binding_path, binding_url, material_type, targets, textures, scalars, colors, flags`.
Python binding creation does not guarantee runtime acceptance: JS rejects zero matching targets, unsupported material properties, and invalid values.
Use sRGB for color/emissive maps and linear data for normal/roughness/metalness/AO; preserve glTF texture orientation.

### CPU preview and reflection

CPU preview renders GLB/glTF bind pose with NumPy/Pillow, not browser PBR, animation, or Draco/meshopt geometry.
Views: all=`+z,-z,+x,-x,+y`; horizontal=four horizontal axes; front_back=+z/-z; explicit lists support six axes.
Axis names locate the camera, not the model's front. Directory preview selects one sorted GLB, otherwise glTF.
Output defaults to `.a3game/previews`; payload contains views/contact_sheet/view_axes/render/source_path/output_dir.
orientation_report adds evidence/decision/reflection; commit reviewed orientation separately with set_orientation.

## 4. World drafts, publication, and browser loading

`world.create_draft(spec)` takes a complete World spec; no per-entity patch/merge API exists.
`world.build(source)` imports a registered Scene as collidable `environment_000`, publishing by default.
Options: world_id/project_id/publish/default_spawn_point/native_scene/replace_existing/environment_artifact_id/lights/camera/environment.
Unknown options warn; native_scene is metadata-only, and environment_artifact_id selects the scene entity, not HDRI.
A World package is scene JSON, not an npm Gameplay Package.

| Step | Read from the result |
|---|---|
| `create_draft` | `payload.draft` including draft_id, spec, status, metadata |
| `validate_draft` | Resolved artifacts and counts; warnings/errors |
| `get_scene_graph` | `payload.scene_graph` for `sceneLoader.buildWorld(graph)` |
| `publish_draft` | `payload.package.scene_url/scene_path`, revision/package identity |
| `list_packages` | Packages in `artifacts[]`; `payload.count` |

World schema fields use snake_case, but embedded JS option dictionaries retain their documented camelCase keys,
for example `environment.wind.gustStrength`. Runtime session messages use camelCase; do not mechanically rename either contract.

| World field | Input contract |
|---|---|
| Top level | `world_id, name, project_id, environment, camera, lights, entities, spawn_points, metadata`; world_id defaults world_001 |
| IDs | World/entity/light/water IDs use `[A-Za-z0-9][A-Za-z0-9_.-]*` |
| `entities[]` | `entity_id, role, artifact_id, category, collision, cast_shadow, receive_shadow, transform, behaviors, parameters` |
| Roles | environment, player_start, prop, npc, pickup, trigger, vehicle, weapon, effect |
| `transform` | `{position,rotation,scale}`; vectors normalize to `{x,y,z}`; defaults zero/zero/one |
| `lights[]` | Required type; AmbientLight, HemisphereLight, DirectionalLight, PointLight, SpotLight, RectAreaLight; light_id, color, intensity, position, target, cast_shadow |
| `behaviors[]` | Required type; animation/spin/orbit/float/path/audio; animation validation needs artifact_id or clip |
| `camera` | PerspectiveCamera/OrthographicCamera; fov=50, near=.1, far=2000, position, target, controls |
| `environment` | preset, sun, sky, background, environment/background artifact IDs, intensity/blur/rotation, show_sky, tone_mapping/exposure, shadows, fog, ground, wind, water |
| `spawn_points[]` | name, position, rotation |
| `environment.water[]` | Unique water_id; size, position, normal_artifact_id, terrain_entity_ids, options |

Collision defaults true only for environment/prop/vehicle roles. Shadow casting/receiving defaults true.
Python vector inputs accept object vectors or at least three list/tuple elements. Rotations are radians; scale is applied outside asset normalization.
Validation checks artifact readiness, duplicate entity IDs, and animation references; absent lights/spawns warn.
Validation does not execute behaviors, prove navigation, compile shaders, or verify gameplay.

**Current schema/loader boundaries:**

- `frustum_height`: dropped by Python; direct JS graphs apply it only when switching to orthographic. Use host.setFrustumHeight after loading.
- Controls: Python accepts none, OrbitControls, PointerLockControls, MapControls, FlyControls; JS installs only OrbitControls/PointerLockControls. Detach old controls explicitly.
- Light/Behavior extra keys become options; nested options can nest again on round-trip. Configure advanced lights in JS if needed.
- behaviors are metadata in `userData.a3gameWorldEntity`, not executed actions. World objects are not registered runtime entities; graph.assets does not populate AssetLibrary.

### A3GameSceneLoader

`new A3GameSceneLoader({host, assets})` exposes:

| Method / property | Contract |
|---|---|
| `await loadWorld(sceneUrl)` | Fetch then build; URL is not automatically rebased through AssetLibrary |
| `await buildWorld(sceneGraph)` | Dispose loader-owned previous World and build another |
| `getEntityObject(entityId)` | Object3D or null |
| `resolveSpawnTransform(index=0)` | Spawn transform with modulo indexing; pass a non-negative integer; empty list returns origin/unit scale |
| `sceneGraph` | Original loaded graph |
| `entityObjects, collisionTargets, spawnPoints, waterSurfaces, warnings` | Entity map, collider list, spawns, water map, diagnostic list |
| `dispose()` | Release loader-owned content and subscriptions |

Build/load returns `{worldId, entityCount, collisionTargetCount, spawnPoints, warnings}`, not the graph itself.
Ordinary entities without artifact_id are skipped; player_start contributes a spawn.
World rebuild does not reset session entities. Copying collisionTargets into another subsystem requires explicit updates after rebuilding.
For subpath hosting, pass a correctly resolved World URL, such as `assets.resolveUrl('/assets/worlds/example.json')`.

## 5. Browser boot, viewport, and host lifecycle

Provide sized viewport and HUD containers before startup. Do not create a renderer or access document at gameplay module import time.

```js
import { bootA3GameRuntime, createRoundedBox, createSunLight } from '@a3game/playable';

export async function startGame() {
  const context = await bootA3GameRuntime({
    container: '#a3game-viewport', hudContainer: '#a3game-hud',
    requireManifest: false, autoBeginPlay: false, autoStart: false,
    hostOptions: { fov: 50, fixedTimeStep: 1 / 60 },
  });
  const { host, runtime } = context;
  host.setEnvironment({ preset: 'gradient' });
  host.add(createSunLight({ radius: 20 }), 'environment');
  const ground = createRoundedBox({ width: 30, height: 0.2, depth: 30, preset: 'stone' });
  ground.position.y = -0.1;
  host.add(ground, 'environment');
  host.camera.position.set(0, 4, 8);
  host.camera.lookAt(0, 0, 0);
  runtime.onWorldBeginPlay();
  host.start();
  return context;
}
```

`bootA3GameRuntime` returns `{host,assets,sceneLoader,hud,session,runtime,world}`; world is a loader summary or null.
Options: `container/hudContainer/baseUrl/manifestUrl/worldUrl/worldId/hostOptions/requireManifest/createHud/
autoBeginPlay, autoStart, entityFactory`. autoBeginPlay/autoStart default true; requireManifest defaults false.
Providing hudContainer creates one HUD unless createHud=false; reuse it. Only worldUrl triggers World loading.
No input router, controller, or binding is created automatically. No aggregate context.dispose exists.
Use explicit construction of public classes for decoder, session, or channel options not forwarded by boot.

### A3GameRuntimeHost reference

Host defaults: antialias=true, shadows=true, pixelRatioCap=2, clearColor=0x101014,
cameraType=perspective, frustumHeight=12, fov=50, near=.1, far=2000,
fixedTimeStep=1/60, maxSubSteps=6, maxFrameDelta=.1, environmentUpdateInterval=0.
Accept container/hudContainer, toneMapping/toneMappingExposure, and wind options as needed.
Require finite non-negative fixedTimeStep/environmentUpdateInterval, positive maxFrameDelta, and positive integer maxSubSteps.

| Group | Public methods |
|---|---|
| Lifecycle | `await init()`, `start()`, `stop()`, `tick(forcedDelta?)`, `dispose()` |
| Scheduling | `onTick(listener)`, `onRender(listener)`, `onResize(listener)`; each returns unsubscribe |
| Interpolation | `interpolateObject(object)` returns unsubscribe; `resetInterpolation(object)` |
| Scene roots | `add(object, rootName='entities')`, `remove(object)`, `getRoot(rootName='entities')` |
| Projection | `usePerspectiveCamera({fov,near,far})`, `useOrthographicCamera({frustumHeight,near,far})`, `setFrustumHeight(height)` |
| Controls | `attachOrbitControls(options)`, `attachPointerLockControls()`, `detachControls()` |
| Pointer lock | `await requestPointerLock()`, `exitPointerLock()`, `isPointerLocked()` |
| Environment | `setEnvironment(options)`, `setFog(options)`, `setWind(config,immediate=false)` |
| Sun / IBL | `registerSunLight(light,distance?)`, `getSunDirection()`, `getSunPosition(distance=100)`, `refreshEnvironment()` |
| Queries / evidence | `raycastFromPointer(event,targets?)`, `raycast(origin,direction,{targets,near,far})`, `captureFrame()`, `getStats()` |

add returns the object; camera-switch methods return the current camera. remove detaches but does not dispose resources.
registerSunLight returns unregister. Rays return native THREE intersections: pointer query returns one/null, raycast returns an array.
Do not confuse these with CollisionProbe's `{hit,...}` results.

### Fixed simulation and displayed frames

- `onTick(dt,elapsed)`: simulation; register input/AI before session/entity consumption.
- `onRender(dt,alpha)`: display only, not rules or authoritative movement.
- tick advances and renders; negative/non-finite dt throws; excess catch-up increments droppedSeconds. fixedTimeStep=0 enables variable steps.
- interpolateObject restores simulation transforms after display; resetInterpolation after teleports.
- captureFrame runs onRender(0,alpha) and renders without advancing simulation.
- getStats: frameCount/elapsedSeconds/simulationSteps/interpolationAlpha/droppedSeconds/drawCalls/triangles/geometries/textures/programs/pixelRatio/size.
  simulationSteps is the latest frame's substep count.
- Drive cooldowns/smoothing with dt; no extra gameplay rAF or wall-clock timers.

### Viewport and cameras

Container dimensions control renderer/projection, with window fallback and automatic resize updates; onResize receives `{width,height}`.
Capture viewport means page size. captureFrame returns a canvas PNG data URL **without DOM HUD**; recorder page capture includes it.

| Setting | Contract |
|---|---|
| Perspective fov | Vertical degrees; visible height=`2*d*tan(fov*PI/360)`, width=height×aspect |
| Orthographic frustumHeight | Visible world height; width=height×aspect |
| Camera switch | Copies position/quaternion/parent and controls.object, not zoom/layers |
| Orbit options | target, enableDamping, maxPolarAngle, minDistance, maxDistance only |
| Ownership | One camera driver; do not combine controls with independent yaw/pitch writes |
| Pointer lock | Requires a user gesture |

## 6. Lighting, sky, materials, and texture scale

```js
host.setEnvironment({
  preset: 'gradient', sunPosition: { x: -0.5, y: 0.8, z: -0.3 },
  sky: { zenith: 0x2f6fbd, horizon: 0xd3e2ee, cloudCoverage: 0.44 },
  toneMapping: 'NeutralToneMapping', toneMappingExposure: 1,
});
host.setFog({ type: 'Fog', color: 0xd3e2ee, near: 70, far: 240 });
```

Presets: room (interior IBL), gradient (adjustable sky/clouds), sky (physical sky), none.
Combine IBL and direct light; emissive materials are not light sources. Default tone mapping: NeutralToneMapping.

| Control | Contract |
|---|---|
| sunPosition | Non-zero `{x,y,z}`/Vector3 toward the sun, not an array |
| `createSunLight({radius,mapSize,near,far,position,target,intensity,syncEnvironment})` | Main light/shadow region; host.add registers synchronization; false disables it |
| `createFillLight({skyColor,groundColor,intensity})` | HemisphereLight |
| getSunPosition | Origin-relative; registered directional lights synchronize around their own targets |
| setEnvironment({sunPosition}) | Update sky and registered lights |
| refreshEnvironment / environmentUpdateInterval | Rebuild procedural IBL manually/on simulated interval, not imported HDRI |
| assets.applyEnvironment(host,ref,options) | background:false preserves sky; options include environmentIntensity, backgroundReference, backgroundBlurriness, shared rotationDegrees |
| host.setEnvironment | Use here for separate backgroundRotationDegrees/environmentRotationDegrees or showSky:false + background |

Use sRGB color maps and linear data maps; no double gamma or display tone mapping in PMREM.

| API | Important parameters / ownership |
|---|---|
| `createMaterial(preset, overrides={})` | Standard/Physical PBR; unknown preset throws |
| `createRoundedBox(options)` | width/height/depth/radius/segments/material/preset/render flags; visible bevels do not replace simple colliders |
| `createSurfaceTextures(options)` | pattern, size, repeat, color, jointColor, roughness, cells, contrast, normalStrength, seed, renderer, anisotropy |
| `createSurfaceMaterial(options)` | Surface textures plus normalScale, metalness, envMapIntensity; additional material overrides in options.material |
| `createTilingTexture(texture, options)` | In-place repeat, rotation, srgb, colorSpace, anisotropy setup |
| `createRadialGradientTexture({resolution=128,color})` | Canvas in browser, DataTexture fallback without DOM; use a CSS color string for browser color |
| `createContactShadow({radius,opacity,...})` | Visual contact-shadow mesh; keep outside collision targets |

`A3GameMaterialPreset` names: METAL, GUNMETAL, PAINTED_METAL, PLASTIC, RUBBER, CLOTH, LEATHER, WOOD,
STONE, CONCRETE, TARMAC, GRASS, SAND, GLASS, EMISSIVE (values are corresponding lowercase names).
`A3GameSurfacePattern`: CONCRETE, STEEL_PLATE, BLOCKWORK, PAINTED_PANEL.
Surface size is pixels; repeat is a scalar or `[u,v]`, not metres. Derive repeat from actual mesh dimensions and desired tile size.
Texture sets return `{map,normalMap,roughnessMap,height,size,dispose}`. Use the shared height field for consistent relief.
The set is also available as `material.userData.surface`; material.dispose alone does not dispose its textures.
Normals affect lighting, not geometry or collision. Material roughness multiplies roughnessMap values.

`createSkyGradient(options)` supports zenith/horizon/ground, sunDirection, sunColor, sunSize, sunGlow,
cloudCoverage, cloudOpacity, cloudScale, cloudSpeed, cloudColor, cloudShadow, cloudHeight, haze, and windField.
Use its userData.update(dt,wind?) and setSunDirection(direction) only when not already host-driven.
Disabling the solar disc uses sunSize=0; disc size, glow falloff, and haze are separate controls.

## 7. Scene composition and reusable layout helpers

- Design circulation, landmarks, hierarchy, and enclosure; keep spawns/objectives/camera routes clear.
- Share one height source across terrain, feet, props, paths, and water. Match visual/collider transforms and dimensions.
- Use createSeededRandom(seed); validate rotated footprints, gaps, slopes, bounds, and full divider spans.
- Test jump reach with the actual solver; inspect both player-eye and overview frames for occlusion.
- Separate foreground/midground/background without fogging out distant silhouettes.

| Helper | Contract |
|---|---|
| `directionToYaw(direction)` | Accept `{x,z}`/Vector3; return atan2(-x,-z); reject zero XZ or non-finite components |
| `yawToDirection(yaw,pitch=0,target?)` | Unit runtime forward Vector3; positive pitch looks up |
| `footprintCorners({x,z,width,depth,rotation},margin=0)` | Four `{x,z}` corners using THREE Y-rotation; margin expands each side |
| `distanceToPolyline(point,points,closed=false)` | XZ distance to finite segments/endpoints; empty path returns Infinity |
| `createGroundRibbon(points,options={})` | Terrain-conforming mesh; not automatically a collider |
| `createFacadeTexture(options={})` | Seeded sRGB DataTexture, no Canvas requirement |
| `createDistantRange({radius,height,baseY,color,topColor,segments,roughness,seed})` | Seam-connected distant mountain ring |
| `createCloudLayer(options={})` | Sprite cloud group; count, radius, height, size, texture, color, opacity, seed, speed, windField |
| `createInstancedFromModel(source,count,options={})` | Single non-skinned mesh only; unsupported hierarchy returns null |

```js
import { createGroundRibbon } from '@a3game/playable';

const trail = createGroundRibbon([[0, 0], [0, -8], [5, -16]], {
  width: 2, heightAt: terrainHeight, lift: 0.03, tileLength: 2, segments: 8,
});
host.add(trail, 'environment');
```

Ribbon points accept Vector3, `{x,y?,z}`, `[x,z]`, or `[x,y,z]`.
width, lift, tileLength are metres; segments is subdivisions per source segment, maximum 1024.
Provide at least two distinct XZ points for an open path, three for a closed path.
UVs use metres/tileLength; `userData.pathLength` is XZ centerline length.
heightAt projects both sides; without it, interpolate path y. Options also include closed/material/name.
Supply enough source points for a smooth curve; sharp cusps/self-intersections need application-level handling.

Facade defaults: width=256, height=512 pixels; columns=6, rows=12, seed=1, litRatio=.45.
Set wallColor, windowColor, litColor; dimensions must be 16–2048 and cells at least four pixels.
Lit windows are baked color, not light sources; use restrained emissive material settings if desired.

Cloud userData exposes update, attachToHost, dispose; attach once and do not manually update it again.
Wind-driven clouds use the wind field rather than fixed speed drift.
Instancing clones/bakes geometry but shares material; prepare templates at the origin before baking transforms.
Set instanceMatrix.needsUpdate after setMatrixAt and refresh instance bounds when placements change.
Options include castShadow, receiveShadow, frustumCulled; groups and shadow passes can still add draw calls.
Instancing reduces calls, not triangles; use distance/detail budgets for high-poly repeated props.

## 8. Runtime wire data, interfaces, and identity

Runtime messages use camelCase plain objects. Data factories normalize known fields; they are not full validators and drop unknown top-level fields.
Use explicit identity and increasing sequences in tests; timestampSeconds defaults to performance time, not host simulation time.
Except `createVector3(source,fallback={x:0,y:0,z:0})`, the data factories accept `(source={})`.

| Factory | Output fields |
|---|---|
| `createVector3` | Plain `{x,y,z}` from an object or at least three array elements; not THREE.Vector3 |
| `createTransform` | `position`, `rotation`, `scale`; defaults zero/zero/one |
| `createRuntimeInputState` | `worldId`, `participantId`, `controllerId`, `entityId`, `moveX`, `moveY`, `run`, `jump`, `yaw`, `pitch`, `sequence`, `timestampSeconds` |
| `createEntitySpawnRequest` | `worldId`, `participantId`, `entityId`, `transform`, `parameters` |
| `createParticipantInfo` | `participantId`, `worldId`, `userId`, `entityId`, `online`, `lastSeenSeconds` |
| `createControllerState` | `controllerId`, `participantId`, `worldId`, `kind`, `online`, `lastSeenSeconds` |
| `createControlBinding` | `controllerId`, `entityId`, `worldId`, `mode`, `priority`, `active` |
| `createEntitySnapshot` | `entityId`, `objectName`, `position`, `rotation`, `locomotionState`, `motionState`, `persistent`, `lastInputTimeSeconds` |
| `locomotionStateFromInput` | jump first, then run/walk/idle using movement threshold 1e-3 |

Clamp moveX/moveY independently to [-1,1], then limit the combined movement vector in gameplay to prevent faster diagonal movement.
Factories truncate sequence/priority to integers but do not enforce monotonicity. parameters is shallow-copied.
Controller kind defaults human; online/active/persistent default true. Add game-specific health/score state outside the fixed generic snapshot factory.

`A3GameControlMode`: EXCLUSIVE='exclusive', PRIORITY='priority', ASSISTED='assisted', OBSERVING='observing'.
`A3GameLocomotionState`: IDLE='idle', WALK='walk', RUN='run', JUMP='jump'.
`A3GameRuntimeCommand`: SYNC_SESSION='sync_session', LEAVE_SESSION='leave_session', APPLY_INPUT='apply_input',
WORLD_SNAPSHOT='world_snapshot', RESET_WORLD='reset_world', CLEAR_ENTITY='clear_entity'.

### Entity, factory, and message-handler contracts

Implement through inheritance or duck typing:

| Interface | Required contract |
|---|---|
| `A3GameControllableEntity` | getRuntimeEntityId(), setRuntimeEntityId(entityId), applyRuntimeInput(inputState) → boolean, getRuntimeSnapshot() → serializable object |
| Optional entity hooks | tick(deltaSeconds), dispose() |
| `A3GameEntityFactory` | spawnRuntimeEntity(request,{host,assets,session}) → entity or Promise of entity |
| `A3GameRuntimeMessageHandler` | handleRuntimeMessage(messageType,payload) → synchronous boolean |

`CONTROLLABLE_ENTITY_METHODS, ENTITY_FACTORY_METHODS, RUNTIME_MESSAGE_HANDLER_METHODS` list required methods.
`isControllableEntity, isEntityFactory, isRuntimeMessageHandler` check method presence;
`assertControllableEntity/assertEntityFactory/assertRuntimeMessageHandler(candidate,label?)` return the candidate or throw TypeError.
Do not use an async message handler: runtime extension dispatch does not await handlers.
Factories own model creation, scene attachment, and gameplay assembly; the framework does not automatically move Object3D instances.

### Identity and runtime components

`A3GAME_USER_DATA_KEY='a3game'`; `A3GameIdentityComponent(object,{participantId,entityId})` stores identity in object.userData.a3game.
Static attach reuses/updates a component; get checks the object; findInParents checks ancestors.
setRuntimeIdentity(participantId,entityId) updates it; toJSON returns participantId/entityId/objectName/objectUuid.

`A3GameRuntimeEntityComponent(object,{entityId,participantId,persistent})` exposes static get, setRuntimeEntityId,
onRuntimeInput(listener) → unsubscribe, applyRuntimeInput(rawInputState), setMotionState, getRuntimeSnapshot, dispose.
It stores input, not movement; wrap it in an entity implementing getRuntimeEntityId. Snapshots use local position/rotation.
Sequence rejection is component-wide and applies only to positive non-increasing values, not zero/negative values or authentication.
dispose clears input state, not identity or Object3D/GPU resources.

## 9. Input routing and session ownership

Use +Y up, metres, radians, seconds, and metres/second. Runtime yaw=0 faces -Z; yaw=PI/2 faces -X.
Forward is `(-sin(yaw),0,-cos(yaw))`; right is `(cos(yaw),0,-sin(yaw))`.
moveY=1 means forward and moveX=1 means right. Multiply normalized movement by speed and dt.
Choose input/camera/body yaw according to FPS, free third-person, or vehicle movement rather than forcing one convention on every game.

`new A3GameInputRouter({target,controllerId,keyBindings,actionBindings,pointerSensitivity=.0025,
invertPitch=false,maxPitch=PI/2-.05,lookMode='pointer-lock',gamepadIndex=null})`.
Use host.container as target: the default window target does not install pointer listeners.
keyBindings merges DEFAULT_KEY_BINDINGS (WASD/arrows, Shift, Space); default mouse actions are primary/secondary.
`A3GameLookMode`: POINTER_LOCK, DRAG, ALWAYS. Set startup/respawn view with setLook(yaw,pitch).

| Method | Contract |
|---|---|
| `enable()/disable()/reset()` | Manage listeners/held input; do not reset yaw, pitch, or sequence |
| `setLook(yaw,pitch=this.pitch)` | Set absolute look with pitch clamping |
| `onAction(listener)` | Local `(action,phase)` with pressed/released; returns unsubscribe |
| `isActionHeld(action)` | Current held state |
| `sample(identity={})` | Runtime input frame; increment sequence; jump is a press edge |
| `pipeToSession(session,host,identity)` | Add input sampling before runtime consumption; return unsubscribe |

pipeToSession does not enable the router, create controllers, or bind entities.
Named action events are not included in the movement wire frame; implement command transport explicitly for remote game actions.
reset clears held actions without emitting all release callbacks; disable plus saved unsubscribe functions replaces a nonexistent router.dispose().
Gamepad: axes0/1 move, axes2/3 look, button10 run, button0 jump, button7 primary, button6 secondary; deadzone=.15.
Gamepad polling occurs on sample, overrides keyboard movement/run, and applies a fixed .04 look scale per sample rather than dt scaling.

### Participants, controllers, entities, bindings

A participant identifies a user, a controller produces input, an entity implements gameplay, and a binding connects controller to entity.
`new A3GameWorldSessionSubsystem({worldId='world_001',inputConsumeHz=60})` exposes:

| Public method | Result / action |
|---|---|
| `registerParticipant(participantId,userId='')` | Participant record |
| `markParticipantOffline(participantId)` | Mark participant/controllers offline, deactivate bindings; keep entity |
| `createController(participantId,controllerId='',kind='human')` | Controller record |
| `registerEntity(entityId,entity,participantId='')` | Resolved entity ID |
| `getEntity(entityId)` | Entity or null |
| `removeEntity(entityId,disposeEntity=true)` | Remove and optionally dispose entity |
| `bindControllerToEntity(controllerId,entityId,mode=EXCLUSIVE,priority=0)` | Establish binding |
| `unbindController(controllerId)` | Remove binding |
| `await syncSession(payload,spawnEntity)` | Register/synchronize and call factory; return identity |
| `enqueueInputState(rawInputState)` | Accept into latest-input queue, not immediate movement |
| `consumeLatestInputs(deltaSeconds)` | Deliver queued frames at configured interval; return delivered count |
| `getWorldStateSnapshot()` | Entity snapshot array |
| `getSessionSnapshot()` | worldId, participants, controllers, bindings, entities |
| `resetWorld(disposeEntities=true)` | Clear session and return cleared entity IDs; do not reload static World |

syncSession payload: `{participant:{participantId,userId},controller:{controllerId,kind},binding:{mode,priority},spawnRequest:{entityId,transform,parameters}}`.
Return identity is `{worldId,participantId,controllerId,entityId}`. No transactional rollback is guaranteed on factory failure.
Use explicit IDs for deterministic testing; generated IDs use time/randomness.

```js
import { A3GameInputRouter } from '@a3game/playable';

// Start from a context booted with autoBeginPlay:false and autoStart:false.
runtime.setEntityFactory(factory);
session.registerParticipant('local-player');
const entity = await runtime.spawnEntity({
  entityId: 'player-1', participantId: 'local-player',
  transform: sceneLoader.resolveSpawnTransform(0),
});
const entityId = session.registerEntity('player-1', entity, 'local-player');
const controller = session.createController('local-player', 'keyboard-1');
session.bindControllerToEntity(controller.controllerId, entityId);
const input = new A3GameInputRouter({ target: host.container, controllerId: controller.controllerId }).enable();
const stopInput = input.pipeToSession(session, host, { controllerId: controller.controllerId });
runtime.onWorldBeginPlay();
host.start();
```

Supply the factory from section 8. Session delivery rules:
- Keep the last arriving frame/controller; overwritten edges are lost. Active bindings set worldId/entityId; no authentication or sequence sorting.
- EXCLUSIVE removes other entity bindings; OBSERVING sends no input. Others all deliver by descending priority, without winner selection or blending.
- Consume one batch per interval and reset the accumulator; no replay of missed batches.

## 10. Runtime orchestration, commands, and transport

`new A3GameRuntimeSubsystem({host,session,assets,channel,autoConnect})`; autoConnect defaults true.
Public API: setEntityFactory(factory), registerMessageHandler(handler), unregisterMessageHandler(handler),
getSessionSubsystem(), onWorldBeginPlay(), deinitialize(), spawnEntity(rawRequest),
handleRuntimeCommand(command,payload={}), dispatchExtensionMessage(messageType,payload).
registerMessageHandler returns unsubscribe. spawnEntity and handleRuntimeCommand are async.
spawnEntity invokes the factory only; registration/binding happens separately or through syncSession.
onWorldBeginPlay is idempotent and consumes input before ticking registered entities.
Do not also tick those entities manually. deinitialize unhooks ticks/channel, resets session entities, and clears handlers, not the host/assets/HUD.
Unknown messages are offered synchronously to every registered handler, not stopped at the first true result.

| Command value | Payload / result |
|---|---|
| sync_session | Session synchronization payload from section 9; return identity |
| leave_session | controllerIds array and/or participantId; unbind/mark offline, keep entities |
| apply_input | Runtime input frame; accepted means queued |
| world_snapshot | Return complete session snapshot |
| reset_world | Clear runtime session and report clearedEntities |
| clear_entity | entityId, destroyObject; false skips entity.dispose but still removes registration |
| Other strings | Offer to extension handlers; inspect handled_by and ok |

### Browser channel and local bridge

`new A3GameRuntimeChannel({onCommand,host,port,transport='local',pollIntervalMs=250,globalName='__A3GAME_RUNTIME__'})`.
Omitted `host`/`port` fall back to the loopback runtime defaults in `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/runtime-channel.js`.
Public API: baseUrl getter, connect(), disconnect(), await dispatch(command,payload={}), getHistory().
Precedence: options → VITE_A3GAME_RUNTIME_* → A3GAME_RUNTIME_* → defaults.
connect installs the local bridge; websocket/polling require explicit selection and a relay (not provided by Vite).
No fallback, reconnect, or access control. connected is not relay health; history retains200 wall-clock durationMs/at records.

`globalThis.__A3GAME_RUNTIME__`: channel/commands/dispatch/syncSession/applyInput/snapshot/resetWorld/clearEntity/history.
Commands are async; history is synchronous. Use dispatch('leave_session',payload); no leaveSession helper exists.
dispatch catches errors; direct handleRuntimeCommand can reject. Polling: GET /pending, POST /result.
WebSocket: `{id,command,payload}` → `{id,result}`.

### Python runtime sessions

- Convert snake_case arguments to JS wire fields. join defaults world_001; require ready web-backed avatar/prop and motion artifacts.
- leave unbinds/keeps entities; clear_entity removes registration; reset_world clears the session.
- heartbeat and snapshot may reflect Python-local state. Check payload.runtime_delivery.delivered/response even if outer ok=True.
- Delivery is HTTP POST /command, including when config accepts websocket; configure the compatible browser relay separately.
- Sequence values are only forwarded; enforce ordering and game-action semantics in the integration.

## 11. Animation and motion libraries

Separate the gameplay/collision root from its replaceable animated visual.
A motion resource is not a visible character. Preserve procedural fallback when no usable animated actor is available.

```js
import { createAnimatedActor } from '@a3game/playable';

const actor = await createAnimatedActor(assets, ['hero', 'hero_fallback'], {
  height: 1.8, states: ['idle', 'walk', 'run'], defaultState: 'idle',
});
const stopAnimation = actor?.animator?.attachToHost(host);
```

Attach actor.object to the visual root; unsubscribe separately from animator disposal.
Results: `motionSource` is one of `clips`, `imported_motion`, `rigged_asset`, `auto_rig`, `none`, plus rig, measurement and warnings.
Options: `ground`, `motionReferences`, `motionLibrary`, `clipSpeed`, `shoulderWidth`, `minAspect`, `maxAspect`, `requireMotion`, `autoRig`.
requireMotion rejects motionless results; autoRig:false disables generated-rig fallback, not clips/imported motion.
clipSpeed affects generated clips only; states is not a strict filter. Defaults: ground=true, envMapIntensity=1, frustumCulled=false.

### A3GameAnimationDirector

`new A3GameAnimationDirector(root,clips=[],{defaultFade=.2})`:

| API | Behavior |
|---|---|
| `addClip`, `addClips`, `listClipNames` | Add and enumerate clips |
| `mapState(state,clipName)`, `mapStates(mapping)` | Bind states; batch returns missing names |
| `mapStateChain(state,candidates)`, `mapStateChains(mapping)` | Select available candidates; batch returns bound/missing |
| `play(state,options={})` | fade, loop, timeScale, clampWhenFinished, restart; return action or null |
| `playOnce(state,{fade,timeScale})` | Promise resolved through mixer updates |
| `stopAll(fade=0)`, `update(dt)`, `attachToHost(host)` | Control playback; attachment returns unsubscribe |
| `getState()`, `dispose()` | Inspect and release mixer state |

Name matching is exact, case-insensitive exact, then substring. Replaying the same state without restart does not reapply all parameters.
Keep the mixer updating for playOnce completion. Director disposal does not automatically invoke previously returned host unsubscribe functions.
Do not infer a complete additive-layer or skeletal retargeting system from state-name crossfading.

### Motion library and lower-level tools

`new A3GameMotionLibrary({assets})`: available getter, listMotions(), await loadClips(reference),
await loadForCharacter(character,{references,states,rename}) → `{clips,sources}`; inspect warnings/cache. No public dispose method.

| Function | Contract |
|---|---|
| `createHumanoidSkeleton({height,centre,baseY,shoulderWidth})` | Return root, bones, byName, skeleton, radii; no visible character |
| `measureHumanoid(object,{minAspect,maxAspect})` | Proportion assessment, size, centre, baseY, reason; not semantic face detection |
| `autoRigHumanoid(object,options)` | Approximate binding or null; height, centre, baseY, shoulderWidth, maxBones, castShadow |
| `findRiggedHumanoid(object,minimumBones=12)` | Inspect the first skeleton for canonical bone names and hips |
| `createHumanoidClip(state,{height,speed,hipsRest})` | Generated clip; unknown state returns null |
| `createHumanoidClipSet(states?,options)` | Default/empty states selects full set; unknown states ignored |
| `retargetClipToSkeleton(clip,target,{boneMap,strict})` | Return clip/mapped/unmapped; track-name mapping, not bind-pose/limb-length correction |

`A3GameMotionState`: IDLE, WALK, RUN, JUMP, BLOCK, PUNCH, KICK, SLASH, HIT, DEATH, AIM, SHOOT, RELOAD, DRAW.
Values are lowercase. A3GameHumanoidBone defines canonical bones; A3GameSourceBoneAliases lists imported aliases;
A3GAME_HUMANOID_CLIP_NAMES lists generated clip names.
autoRigHumanoid replaces static meshes and can dispose their original geometry: do not apply it as non-destructive analysis on shared assets.
Use at most four influences through maxBones; four-weight storage does not support arbitrary higher counts.
Use offline retargeting for differing rest poses/axes/bone proportions; strict name mapping is not a compatibility proof.

## 12. Shared wind, water, buoyancy, and surface flow

### Wind

`host.setWind({velocity:[3,0,1],gustStrength:.5,gustPeriod:6,spatialScale:30,response:1.5,seed:7}, immediate=false)`.
Velocity is downwind world m/s (array/object); host advances the field once per simulation step.
`A3GameWindField(config)`: set(config,immediate=false), sample(position,time?,target?), update(dt), getState().
sample requires x/y/z; getState reports velocity/target/gust/time/displacement, not full config.
Limits: heightShear defaults .05; gustStrength/heightShear≥0, gustPeriod/spatialScale≥.1, response≥.01. No obstacle-resolved flow.

`bindVegetationWind(object,host,{flexibility,maxBend,response,phase})` rotates about the authored root; detach restores rest pose.
Wind response uses `1-exp(-dt/response)`; vegetation uses `1-exp(-response*dt)`. Flutter may exceed maxBend slightly.
Keep fixed colliders separate; attach effects once without additional manual updates.

### Water surface

```js
import { createWaterSurface } from '@a3game/playable';

const water = createWaterSurface({
  size: [30, 20], position: [0, 0, 0], quality: 'standard',
  terrainHeight: (x, z) => -2, waveHeight: 0.07,
});
host.add(water, 'environment');
water.userData.attachToHost(host);
```

standard uses GPU analytic waves; low uses bounded CPU geometry and disables planar reflection/refraction.
Water is not a solid gameplay floor. terrainHeight returns world bottom height; absent callback uses constant depth (default 5m).
The surface does not carve terrain. waveHeight/amplitude is not mean water level.

| Options | Meaning / bounds |
|---|---|
| size/position/quality | Plane extent, location, standard/low |
| waves | Up to four `{direction:[u,v],wavelength,amplitude,speed,phase}`; directions use local surface axes, speed is wave phase motion |
| segments | standard default64/max192; low default24/max32 |
| waveHeight/windInfluence | Amplitude 0–20; influence default .15, clamped 0–1 |
| depth, depthResolution, terrainHeight | Depth field; resolution default32, range2–128 |
| normalMap, repeat, normalScale | Detail texture; the supplied map is configured and its offset animated in place |
| color, shallowColor, deepColor, absorption | Surface/depth appearance; absorption has three components |
| roughness, opacity, envMapIntensity | Optical material controls |
| foamWidth, foamStrength, distortion | Shoreline and refraction appearance |
| reflection/refraction | Both default false; refraction requires perspective projection |
| reflectionResolution/reflectionUpdateRate | Defaults256/30 simulated Hz; limits64–1024 / 1–60 |
| current | World vector or `(position,time,target)=>Vector3`; not the same as wind or texture flowSpeed |
| rippleCapacity, rippleLifetime, maxRippleStrength | Bounded ripples; capacity standard8/low4, maximum8 |

Public water.userData methods:

| Method | Contract |
|---|---|
| `update(dt)` | Advance waves/detail/ripples |
| `sampleHeight(x,z)`, `sampleNormal(x,z,target?)` | World surface height/normal |
| `sampleDepth(x,z)`, `sampleBottom(x,z)` | World depth/bottom; dry depth is zero |
| `sampleVelocity(position,time?,target?)` | Current plus vertical wave velocity; also supports position,target |
| `addRipple(x,z,strength=.12,radius=1,{speed,frequency,decay}={})` | Boolean acceptance; radius at least .05; full pool overwrites |
| `computeBuoyancy(settings={})` | force, torque, submergedFraction, point results |
| `applyBuoyancy(body,settings={})` | Call body.applyForce(force,worldPoint) and return computed result |
| `refreshDepth()` | Refresh after bottom changes |
| `attachToHost(host)` | Return unsubscribe |
| `getState()`, `dispose()` | Inspect and release owned effect resources |

Surface queries outside the footprint return null; sampleVelocity also returns null on dry ground.
Buoyancy settings: points/centerOfMass/velocity/angularVelocity/volume/draft/density/gravity/damping.
Use at most 64 equal-volume column-bottom sample points; buried portions do not displace water.
applyBuoyancy does not integrate movement; require the caller's applyForce implementation.
Water disposal does not own an externally supplied normalMap.

World water bottom priority: explicit terrain_entity_ids → entities with parameters.waterTerrain=true → World ground.
Bridges and roofs are not automatically water bottoms. SceneLoader owns these surfaces through waterSurfaces.get(water_id).

### A3GameWaterBody

`new A3GameWaterBody({water,object,mass=500,volume,size=[1,1,1],velocity,onEnterWater,
density=1000,gravity=9.81,damping,angularDamping=1.5,groundFriction=4,fixedStep=1/120})`.
Use object origin as center of mass and metre/kilogram/second units. damping defaults mass*4.
Public methods: update(dt), attachToHost(host), dispose(). Observe velocity/angularVelocity/force/torque/submergedFraction/grounded/elapsedSeconds/disposed.
There are no getState, reset, applyForce methods; this class is not itself the applyBuoyancy body interface.
Attach water before the body. fixedStep is limited to 1/240–1/30; each update caps input dt at .25 seconds.
onEnterWater receives `{body,object,position,velocity,impactSpeed}` on a new entry, not merely construction underwater.
Dispose detaches driving but does not dispose object/water. This is a light box-body model, not a general rigid-body or naval hydrodynamics engine.

### Surface flow

`createSurfaceFlow({preset,size,position,resolution,heightMap,initialDepth,viscosity,mobility,sources,...})` returns a Mesh.
Presets are lava/blood; position is `[x,y,z]`, and heightMap returns absolute world height.
resolution is integer4–256, default56; initialDepth may be a number or `(worldX,worldZ)=>number`.
Additional options: coolingRate/yieldSlope/solidificationTemperature/thermalViscosity/referenceDepth/fixedStep/
minVisibleDepth/boundary/color/roughness/emissiveIntensity.
Viscosity is at least .0001; mobility, coolingRate, yieldSlope, thermalViscosity are non-negative; solidificationTemperature is in [0,1).

userData: update(dt), addSource(source), getBedGeometry(), attachToHost(host), getState(), dispose().
- Sources: world `{x,z,radius,volume,rate,duration,temperature}`; immediate volume, rate in volume/s, temperature0–1. Outside bounds throws.
- Omit duration for continuous injection; Infinity is invalid. addSource returns a stop function for future injection only.
- Closed boundary conserves volume; open records outflow. Inspect massError. fixedStep=1/120; update has no catch-up cap.
- getState copies typed arrays; avoid full-grid runtime snapshots. getBedGeometry returns an owned clone.
- `createSurfaceFlowTerrain(flow,materialOptions)` creates a separately owned mesh. Do not transform a created flow or attach twice.
- Temperature/viscosity are game coefficients, not calibrated SI; no full 3D overturning/splash solver.

## 13. Particle, beam, trail, and lightning effects

Use visual effects for feedback, not as authoritative gameplay hit detection.

```js
import { createVfxDirector, A3GameVfxPreset } from '@a3game/playable';

const vfx = createVfxDirector({
  host, seed: 7,
  presets: { dust: { ...A3GameVfxPreset.IMPACT_DUST, windResponse: 0.6 } },
});
vfx.play('dust', { position: [0, 0.1, 0], direction: [0, 1, 0], count: 12 });
```

createVfxDirector with host attaches by default; attach:false disables it. new A3GameVfxDirector requires explicit attachment.
Custom presets replace the factory defaults; append with register/registerAll.
Default registered names are muzzle_flash, bullet_impact, impact_dust, blood_hit, melee_impact, shock_ring, block_spark, foot_dust,
not every constant in A3GameVfxPreset. Unregistered play returns zero.

| Constant | Members |
|---|---|
| `A3GameEmitterShape` | POINT, BOX, SPHERE, CONE, DISK, EDGE |
| `A3GameParticleAppearance` | DEFAULT, GRADIENT, CIRCULAR, RING |
| `A3GameParticleBlending` | NORMAL, ADDITIVE, MULTIPLY |
| `A3GameParticleRenderMode` | BILLBOARD, STRETCHED, MESH |
| `A3GameVfxPreset` | MUZZLE_FLASH, BULLET_IMPACT, IMPACT_DUST, BLOOD_HIT, MELEE_IMPACT, SHOCK_RING, BLOCK_SPARK, FOOT_DUST, LIGHT_ARROW_CORE, LIGHT_ARROW_MOTES, LIGHT_ARROW_IMPACT, BLADE_SLASH, PICKUP_SPARKLE, SMOKE_PLUME, EXPLOSION, FIRE_PLUME, TYRE_SMOKE, SCRAPE_SPARK, BOOST_FLAME |

### Particle system

`new A3GameParticleSystem(config={})`; public methods start/stop/clear/emit/burst/update/attachToHost/getState/dispose.
emit(count=1,options={}) returns emitted count; burst defaults to pool capacity. update(dt,camera=null) advances simulation.

| Configuration group | Fields |
|---|---|
| Pool and lifetime | maxParticles, lifetime, size, sizeOverLife, opacityOverLife, seed |
| Appearance | colorStart, colorEnd, intensity, appearance, blending, map, renderMode, geometry, material, depthTest, renderOrder, name |
| Motion | speed, direction, gravity, drag, turbulence, windResponse, windField, rotation, rotationSpeed, stretchBySpeed, orientToDirection |
| Emitter | position, emitterShape, emitterRadius, emitterAngle, emitterHeight, emitterDirection, emitterSize, surfaceOnly, startPositionAsDirection |
| Continuous emission | emissionOverTime, looping, autoStart |

Per-emission overrides: position/direction/spread/speed/size/lifetime/colorStart/colorEnd/scale.
- Ranges are scalars/endpoints, not curves; scale affects offset/size/speed, not lifetime.
- windResponse defaults0 (1/s); SMOKE_PLUME/FIRE_PLUME enable it. Full pools overwrite; totalEmitted differs from activeCount.
- stop preserves live particles; clear does not stop emission; looping:false stops after the first effective update.
- With direction specified, spread=0 falls back to .45.
- attachToHost does not insert object3D; add it explicitly or use the director. System disposal owns custom geometry/material.

### Director, beams, and trails

| Class | Public contract |
|---|---|
| `A3GameVfxDirector({host,root='effects',seed,scale=1})` | register(name,config), registerAll(presets), get(name), play(name,options), follow(name,target,options), registerBeam(name,options), fireBeam(name,from,to,options), update(dt,camera), attachToHost(host), getState(), dispose() |
| `A3GameBeamEffect({count=8,color,lifetime=.08,blending,name})` | fire(from,to,{lifetime,color}), update(dt), getState(), dispose() |
| `A3GameTrailRibbon({segments=24,width=.09,color,endColor,opacity=.9,taper=true,blending,name})` | reset(position?), push(position,camera=null), dispose() |

- register/registerBeam reuses existing names without reconfiguration.
- follow(name,target,{offset,rate}) returns `{stop}`; offset is world-space. One system shares one emitterPosition; unparented non-camera targets stop following.
- Beams use x/y/z endpoints and overwrite full pools; configurable thick-line width is unsupported.
- Trails require manual push; history counts world-space samples, not seconds. No update, attachToHost, getState methods.
- Align impacts with world normals; unsubscribe on teardown.

`createLightningArc({from,to,segments,period,seed,width,color})` returns a Group.
Endpoints are arrays, segments limited4–64; userData provides update/attachToHost/getState/dispose.
Endpoints remain fixed while shape/flicker changes; wind does not advect the arc.

## 14. Collision and character motion

`A3GameCollisionProbe` is a simplified static-geometry solver, not a general physics engine.
Defaults: targets=[], radius=.4, stepHeight=.6, groundOffset=0, gravity=-18, maxFallSpeed=-40.
setTargets(targets=[]), addTarget(target), removeTarget(target) return the probe. Constructor/setTargets copy their arrays.
Changing a SceneLoader collider array later does not automatically update a probe.
`resolveEntityId(object)` walks parents for a3game.entityId or a3gameWorldEntity.entityId; return empty string when absent.

| Query | Signature |
|---|---|
| Ground | sampleGround(position,{maxDrop,probeHeight,ignore,targets}) |
| Sliding movement | resolveMove(position,displacement,{height,radius,ignore,targets}) |
| Gravity/jump | stepCharacter(state,displacement,dt,{height,radius,jump,jumpImpulse,ignore,targets}) |
| Ray | hitscan(origin,direction,{range,ignore,targets}) |
| Area | overlapSphere(center,radius,{targets,ignore,requireEntityId}) |
| Continuous sweep | sweepSphere(from,to,{radius,ignore,targets}) |

state is persistent `{position:Vector3,velocityY,grounded}` with position at the feet; stepCharacter mutates it.
Displacement is a step distance, not velocity. resolveMove returns move/blocked/normal/contacts; apply move explicitly.

```js
collision.stepCharacter(motion, displacement, dt, {
  height: 1.8, radius: 0.35, jump: inputFrame.jump, ignore: [player],
});
player.updateMatrixWorld(true);
```

- Ignore the moving entity's own root; exclude cosmetics. Update matrixWorld before same-frame queries and needsUpdate after geometry edits.
- hitscan: hit/point/normal/object/distance/entityId. overlapSphere tests bounds/markers, not exact meshes; ignore matches actual targets.
- sweepSphere tests static faces/edges/vertices/instances; returns centre/point/normal/distance/timeOfImpact/penetrationDepth. Miss distance/TOI=Infinity.
  Meshes are surfaces, not filled volumes; invisible geometry still collides and overriding raycast does not bypass sweeps.
- Characters use overlapping spheres with sliding/ceiling checks. No automatic climbing, stacking, joints, or full moving-platform dynamics.
  stepHeight controls ground probing. Use simple colliders, not animated skins.

## 15. HUD and observable state

Reuse context.hud or construct `new A3GameHudLayer({container})` once.
Anchors: top-left/top-center/top-right/center/bottom-left/bottom-center/bottom-right; widgets at the same anchor stack vertically.

| API | Contract |
|---|---|
| addText/addPanel(name,{anchor,value,className}) | Stringify values; return HTMLElement |
| addBanner(name,{anchor,value,visible}) | Result/announcement banner; return HTMLElement |
| addBar(name,{anchor,value,label}) | Clamp normalized value to0–1; return HTMLElement |
| addCrosshair(name='crosshair') | Centered crosshair |
| setValue(name,value), setValues(values) | Update one or many widgets |
| setVisible(name,visible), remove(name) | Show/hide/remove named widgets |
| autoHide(name,{host,after=6,fade=.8}) | Cancelable delayed fade |
| getState(), dispose() | Observable widget state and cleanup |

```js
hud.addPanel('controls', { anchor: 'bottom-left', value: 'WASD move' });
const cancelHide = hud.autoHide('controls', { host, after: 4, fade: 0.6 });
```

Pass host for deterministic fading: it uses explicit onRender dt. Without host it uses wall-clock time.
after/fade must be finite non-negative seconds. setVisible cancels fades; true restores opacity; remove/dispose cancel pending work.
Inspect getState and data-a3game-value/data-a3game-visible for assertions rather than OCR.
Keep persistent HUD concise and legible at target viewport sizes. Do not hide gameplay UI only to make a recording appear successful.

## 16. Test execution, recording, and evidence

### Tests and authority

Use Node host doubles, real colliders, seeds, dt, and explicit IDs/sequences. Cover spawn/reset, self-collision, speed/frame-rate variation,
input ownership, fallback, and cleanup; assert state/events. Separately verify dev/production browser startup, framing, occlusion, and routes.

`testing.run_automation_tests`: vitest/playwright; scripts test/test:e2e; filters -t/--grep.
Reports default to `.a3game/reports/vitest-report.json` or playwright-report.json.
Inspect matched_count, passed_count, failed_count, skipped_count, cases, failed_cases plus command status.
Missing/stale/malformed/empty/failed reports are rejected, but **all-skipped reports or nonzero/timeout commands can still yield ok=True**.
Require successful execution, actual passes, and expected coverage; screenshots, readiness, and dry-runs are not benchmark results.

### Declared gameplay plans

Expose `globalThis.__A3GAME_GAME__ = game` with host, input, getState; return serializable gameplay state, not a THREE graph.
Expose `__A3GAME_PLAYTEST__` (or game.playtestActions) to avoid irrelevant generic actions.

```js
window.__A3GAME_PLAYTEST__ = {
  warmup: 1, look: 'off',
  actions: [
    { id: 'approach', keys: ['KeyW'], duration: 2 },
    { id: 'interact', taps: ['KeyE'], duration: 0.5 },
    { id: 'observe', duration: 1 },
  ],
};
```

Use actual bindings. Discovery: declared plan → actionBindings/keyBindings → DOM → fallback; declared actions may be a function.
External action_plan is a JSON path containing an array or `{actions,sustained,warmup,look}`; use sustained, not the declaration-only hold alias.

| Field / rule | Behavior |
|---|---|
| keys / taps / hold | keys hold the slot; taps fire together for one frame; hold:true extends taps/mouse to slot end |
| Conflicts | No keys/taps or sustained/taps overlap; repeated discrete actions need separate slots |
| mouse / click | Primary pointer / DOM selector |
| duration / seconds | Positive; round cumulative boundaries; reject sub-frame actions |
| Budget | Unspecified durations share remainder; short plans add idle; overflow requires allow-partial-plan |
| sustained | Begins in warmup; for capture-only holds, repeat keys in each action |
| look | off/pan/auto; false/'false'=off; auto is off for drag/no mode, otherwise bounded pan via input.setLook |

Keep probe/final fps equal. Cover the full moving-vehicle tail; idle does not mean stopped.
Demonstrate real input, not teleports, collision bypasses, or fabricated state.

### CLI, Python wrapper, modes, and media

CLI entry: `engine_adapters/three_js/playtest/record.mjs`; required --url and --output-dir.
Defaults:1280×720,20fps,14seconds. Set --duration/--fps/--width/--height; use positive even integer dimensions.
For consistent Python/CLI use, choose a positive integer fps; the CLI validates positive finite fps while Python also enforces integrality.
Other flags: --action-plan/--hold/--warmup/--look/--playwright-root/--browser-executable/--source-hash/--self-test.
source-hash is an opaque label recorded as source_hash; the recorder does not calculate or verify source content.

| Mode / flag | Behavior |
|---|---|
| --mode gameplay | Default: execute the input plan |
| --mode overview | Tick gameplay but omit input plan; optionally apply a camera review hook |
| --allow-partial-plan | Explicit prefix/probe capture; record unexecuted actions/frames; not full-plan evidence |
| --preview / --poster | Equivalent PNG-only capture; no plan discovery, default pre-roll, or video encoding |
| --self-test | Recorder's own contract checks, not gameplay benchmark validation |

Optional `__A3GAME_REVIEW__({mode,time,frame,dt,duration})` runs synchronously after tick; adjust only the camera, with no awaited/serialized return.
Overview requires look off; absent hook warns and keeps the game camera. Preview advances only explicit warmup and returns preview_completed.

Python signature: section 2. warmup/look=None inherit the plan;0/False override it. source_hash is non-empty string or None.
playwright_root must contain node_modules/playwright; other environment overrides are browser_executable/browsers_path/library_path/ffmpeg.
Timeout defaults900s. Preserve URL trailing slashes. payload.output_dir=BASE, take_dir=new take, report_path=last stdout JSON path (None for dry-run).
Validate contained new-take paths, source label, mode/URL, browser errors, and video metadata; never reuse BASE/report.json. Preview validates PNG separately.

### Recording time and output contracts

__A3GAME_RECORDING__ stops the published loop; each output frame advances1/fps via internal fixed steps or bounded external steps.
Check initial/advanced simulation time and dropped_seconds; wall time is not simulated time or real-time performance.
Snapshots preserve shared references and mark cycles/THREE objects; non-finite numbers become null.
Limits: depth8, budget2048,128 entries/object or array,512 characters/string; not a lossless state dump.

| Evidence | Contract |
|---|---|
| Directory | New unique take under BASE; preserve previous evidence |
| Video files | report.json, manifest.json, init.png, poster.png, frames/fNNNNNN.jpg, video.mp4 |
| Preview | png_frames=1, no video; status preview_completed |
| Paths | Report/stdout absolute; all manifest media/report paths relative to BASE |
| completed | MP4 plus ffprobe-verified frames/size/fps/duration and no page errors; only this updates BASE/manifest.json |
| Gameplay | Inspect executed_actions, unexecuted_actions, partial_plan and actual game_state progress |
| Playback | Test target-browser codecs; offer WebM when H.264 is unavailable |

### Pipeline integration boundaries

`pipeline.code_gen.playtest.run.record_playtest` differs from the adapter: defaults12s/640×360/warmup0/look auto;
not all mode, preview, source_hash options are forwarded. Use the adapter for declared-plan defaults.
recorder_root maps to Playwright root, root/browsers, root/deps/lib.
`pipeline.code_gen.playtest.eval.evaluate_report` validates report/action/browser structure only, not media/hash/gameplay or preview/overview; authoritative_validation=False.

UI viewports are positive-size objects/pairs, not automatic captures. `gamefactory3a.ui_screenshot_plan.v1` validates declared screens, not rendered viewport coverage.
Mechanic requires schema_version, positive contract_version, matching gameplay_module, non-empty state/events/commands and workspace-contained public_api_paths.

## 17. Cleanup and delivery checks

1. Disable input; unsubscribe actions, pipes, ticks, renders, and animation callbacks.
2. runtime.deinitialize disconnects the channel and disposes session entities; avoid double disposal.
3. Dispose World/HUD/VFX/water/animation resources, then await assets.dispose and dispose host.

`disposeObject3D(root)` invokes userData.dispose, detaches/clears the root, and releases resources respecting asset-ownership markers.
It is not a general reference counter. Detaching is not GPU cleanup; release shared resources only after their users.

Delivery checks: public imports, production boot, resource paths/fallbacks, visual/collider agreement, input/dt/sequence,
camera/UI, fresh reports, complete plans, and playable media.

## 18. Complete JavaScript exports and source map

The following index covers all 108 named exports from `engine_adapters/three_js/plugin/A3GamePlayable/src/index.js`.
Keep generated imports at `@a3game/playable`; packaged subpaths are not needed for normal gameplay.

| Group | Root exports |
|---|---|
| Version / boot | `A3GAME_PLAYABLE_API_VERSION`, `A3GAME_PLAYABLE_ENGINE`, `bootA3GameRuntime` |
| Wire data | `A3GameControlMode`, `A3GameLocomotionState`, `A3GameRuntimeCommand`, `createControlBinding`, `createControllerState`, `createEntitySnapshot`, `createEntitySpawnRequest`, `createParticipantInfo`, `createRuntimeInputState`, `createTransform`, `createVector3`, `locomotionStateFromInput` |
| Interfaces | `A3GameControllableEntity`, `A3GameEntityFactory`, `A3GameRuntimeMessageHandler`, `CONTROLLABLE_ENTITY_METHODS`, `ENTITY_FACTORY_METHODS`, `RUNTIME_MESSAGE_HANDLER_METHODS`, `assertControllableEntity`, `assertEntityFactory`, `assertRuntimeMessageHandler`, `isControllableEntity`, `isEntityFactory`, `isRuntimeMessageHandler` |
| Components / sessions | `A3GAME_USER_DATA_KEY`, `A3GameIdentityComponent`, `A3GameRuntimeEntityComponent`, `A3GameRuntimeSubsystem`, `A3GameWorldSessionSubsystem` |
| Runtime systems | `A3GameRuntimeHost`, `A3GameEnvironmentPreset`, `A3GameAssetLibrary`, `A3GameSceneLoader`, `A3GameInputRouter`, `A3GameLookMode`, `DEFAULT_KEY_BINDINGS`, `A3GameRuntimeChannel`, `A3GameHudLayer`, `A3GameCollisionProbe`, `resolveEntityId`, `disposeObject3D` |
| Layout | `directionToYaw`, `yawToDirection`, `footprintCorners`, `distanceToPolyline`, `createGroundRibbon`, `createFacadeTexture` |
| Model / material utilities | `A3GAME_RUNTIME_FORWARD_AXIS`, `A3GameForwardAxis`, `A3GameMaterialPreset`, `A3GameSurfacePattern`, `alignWeaponModel`, `measureWeapon`, `principalAxes`, `measureObject`, `fitToHeight`, `groundObject`, `forwardAxisYaw`, `orientModel`, `prepareModel` |
| Visual construction | `createCloudLayer`, `createContactShadow`, `createDistantRange`, `createFillLight`, `createInstancedFromModel`, `createMaterial`, `createRadialGradientTexture`, `createRoundedBox`, `createSeededRandom`, `createSkyGradient`, `createSunLight`, `createSurfaceMaterial`, `createSurfaceTextures`, `createTilingTexture`, `createWaterSurface` |
| Motion | `A3GAME_HUMANOID_CLIP_NAMES`, `A3GameHumanoidBone`, `A3GameMotionState`, `A3GameSourceBoneAliases`, `A3GameAnimationDirector`, `A3GameMotionLibrary`, `autoRigHumanoid`, `createAnimatedActor`, `createHumanoidClip`, `createHumanoidClipSet`, `createHumanoidSkeleton`, `findRiggedHumanoid`, `measureHumanoid`, `retargetClipToSkeleton` |
| Wind / fluids | `A3GameWindField`, `bindVegetationWind`, `A3GameWaterBody`, `createSurfaceFlow`, `createSurfaceFlowTerrain`, `createLightningArc` |
| VFX | `A3GameEmitterShape`, `A3GameParticleAppearance`, `A3GameParticleBlending`, `A3GameParticleRenderMode`, `A3GameVfxPreset`, `A3GameParticleSystem`, `A3GameBeamEffect`, `A3GameTrailRibbon`, `A3GameVfxDirector`, `createVfxDirector` |

### Implementation lookup

| Path | Authority / usage |
|---|---|
| `engine_adapters/three_js/__init__.py`, `engine_adapters/three_js/three_client.py`, `engine_adapters/three_js/config.py`, `engine_adapters/three_js/contracts/` | Python entry, namespace construction, configuration, operation envelopes |
| `engine_adapters/three_js/project/client.py`, `engine_adapters/three_js/plugin/client.py`, `engine_adapters/three_js/build/client.py`, `engine_adapters/three_js/runtime/client.py` | Project/package/build/server workflows |
| `engine_adapters/three_js/runtime/sessions.py`, `engine_adapters/three_js/observe/client.py` | Python session delivery and readiness checks |
| `engine_adapters/three_js/assets/client.py`, `engine_adapters/three_js/bindings/client.py`, `engine_adapters/three_js/animation/client.py`, `engine_adapters/three_js/reflection/client.py`, `engine_adapters/three_js/preview/client.py` | Source import, bindings, compatibility, metadata, CPU preview |
| `engine_adapters/three_js/world/client.py` | Draft/build/validation/publication facade; read its schema implementation for serialized-field limits, not as a public import |
| `engine_adapters/three_js/testing/client.py`, `engine_adapters/three_js/playtest/client.py`, `engine_adapters/three_js/playtest/record.mjs` | Test execution, unique-take wrapper, recorder and self-tests |
| `engine_adapters/three_js/plugin/A3GamePlayable/src/index.js`, `engine_adapters/three_js/plugin/A3GamePlayable/package.json` | Runtime exports, boot, supported package/version contract |
| `engine_adapters/three_js/plugin/A3GamePlayable/src/data-types/runtime-types.js`, `engine_adapters/three_js/plugin/A3GamePlayable/src/interfaces/contracts.js` | Wire records and duck-typed contracts |
| `engine_adapters/three_js/plugin/A3GamePlayable/src/components/`, `engine_adapters/three_js/plugin/A3GamePlayable/src/subsystems/` | Identity, input-state component, entity/session lifecycle |
| `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/runtime-host.js`, `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/runtime-channel.js` | Renderer/scheduling and command transport |
| `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/asset-library.js`, `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/scene-loader.js`, `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/scene-kit.js` | Runtime assets, Worlds, generic layout helpers |
| `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/visual-kit.js`, `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/wind-field.js`, `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/water-body.js`, `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/surface-flow.js` | Materials/sky/water/wind/fluid implementation limits |
| `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/motion-kit.js`, `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/animation-director.js` | Rigging/motion and mixer control |
| `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/vfx-kit.js`, `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/lightning-effect.js`, `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/collision-probe.js`, `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/input-router.js`, `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/hud-layer.js` | Effects, queries, input, UI |
| `engine_adapters/three_js/plugin/A3GamePlayable/tests/*.spec.js`, `engine_adapters/three_js/plugin/A3GamePlayable/vitest.config.js` | Framework regression evidence; configuration also includes selected example tests |
| `engine_adapters/three_js/plugin/A3GamePlayable/tests/realism.html`, `engine_adapters/three_js/plugin/A3GamePlayable/tests/wind-fluid.html`, `engine_adapters/three_js/plugin/A3GamePlayable/tests/wind-fluid-demo.js` | Reference browser harnesses for real-WebGL checks of lighting, water, wind and surface flow; not part of Vitest runs |
| `engine_adapters/three_js/cli.py` | Public Python CLI with create-project/import-asset/run subcommands |
| `engine_adapters/three_js/import_generated/import_mesh.mjs` | Node GLTFLoader inspection, not staging/registry import |
| `engine_adapters/three_js/examples/` | Read-only fps-example, arena-fighter-example, racing-example, explorer-example, motion-vfx-example |

Mesh inspector: --source, --usage asset, vfx_standalone, vfx_particle; optional --report/--draco-decoder.
Reports loadability/geometry/materials/textures/animation/bounds/budgets without staging or registry updates.
Install scripts: `scripts/engine_install/three_js/`, not `scripts/three_js/`. Verify wrapper cwd/environment; keep the repository root importable.

### Maintaining coverage

Compare Python public methods and JS root exports against sections 2/18 after API changes.
Update signatures, contracts, limits, and indexes together; exclude implementation algorithms and investigation history.
