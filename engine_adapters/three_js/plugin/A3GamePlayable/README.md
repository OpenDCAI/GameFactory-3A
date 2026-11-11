# A3GamePlayable (three.js)

Adapter-owned runtime for generated three.js Gameplay Packages. Gameplay owns
entities, input mappings, combat and scoring; the framework owns rendering,
sessions, assets, input transport, animation, queries and presentation.

## Install and import

Install through the adapter, not by copying files:

```python
from engine_adapters.three_js import ThreeClient

three = ThreeClient(project_path="<project_path>")
three.plugin.install_framework()
```

```js
import { A3GameRuntimeHost, bootA3GameRuntime } from '@a3game/playable';
```

Use public exports, not deep `src/` imports. `three` is a host-supplied peer
dependency. The export list is in `engine_adapters/three_js/plugin/A3GamePlayable/src/index.js`;
API guidance is in `agent_skills/engine_context/three_js_api.md`.
Runnable Gameplay Packages are in `engine_adapters/three_js/examples/`.

## Collision queries

`A3GameCollisionProbe` provides queries, not a physics engine:

| Query | Method |
|---|---|
| Ground height | `sampleGround` |
| Character movement | `resolveMove`, `stepCharacter` |
| Instant shot | `hitscan` |
| Nearby targets/triggers | `overlapSphere` |
| Projectile segment | `sweepSphere` |

`overlapSphere` treats geometry-free markers as points. `sweepSphere` tests the
travelled segment. Update world matrices before querying newly moved objects.

## Cameras and lighting

The host supports perspective and orthographic cameras (`cameraType`,
`frustumHeight`, `usePerspectiveCamera`, `useOrthographicCamera`, `setFrustumHeight`).
For custom first-person yaw/pitch, use `requestPointerLock`; do not attach another
control that also writes the camera.

`setEnvironment({ preset: 'sky' })` or `'room'` generates environment lighting
without an HDR asset. Use `createSunLight` with a map-sized shadow radius,
`createFillLight`, and `A3GameMaterialPreset` for primitive materials.

## Models and instancing

`A3GameAssetLibrary.tryInstantiate` and `instantiateOrBuild` call `prepareModel`.
Set `height` for scale and `ground` for placement. Model orientation comes from
manifest `orientation.forward_axis`; override with `forwardAxis` or disable with
`orient: false`. Runtime local forward is -Z. Orientation correction stays inside
a wrapper so gameplay yaw does not undo it; unannotated assets are not rotated.
Set `frustumCulled: false` on skinned characters if bind-pose bounds cause clipping.

`createInstancedFromModel` bakes source transforms and returns `null` for skinned
or multi-part models. Keep separate collision proxies: invisible meshes can still
participate in raycasts. A missing manifest is allowed; check `assets.available`
and `warnings`, or set `requireManifest: true`.

## Lifecycle

- Register `input.pipeToSession()` and AI input producers before
  `A3GameRuntimeSubsystem.onWorldBeginPlay()`. Use `autoBeginPlay: false` to control ordering.
- Entities implement runtime identity, input and snapshot contracts; factories
  implement spawning. See the example packages rather than duplicating them here.
- Release owned geometry, materials and textures with `disposeObject3D(root)`;
  shared resources need a separate owner. Unsubscribe callbacks on scene exit.
- `A3GameCinematicPlayer` owns video playback through the configured Browser
  Serving gateway; gameplay owns the surrounding state transitions.

## Feedback and camera ownership

`A3GameFeedback` consumes confirmed events and returns camera-local offset/FOV
pulses; it does not mutate simulation or the camera. `A3GameCameraRig` owns the
final pose. Use an unparented camera and detach other controls.

```js
import { A3GameFeedback, A3GameCameraRig } from '@a3game/playable';

host.detachControls();
const feedback = new A3GameFeedback({ haptics: false, intensity: 0.6 });
const rig = new A3GameCameraRig(host.camera);
const offRender = host.onRender(dt => {
  rig.update(dt, playerFocusWorldPosition(), {
    ...feedback.update(dt), reducedMotion: feedback.reducedMotion,
  });
});
```

Trigger feedback only after confirmed local gameplay outcomes. `setShot` changes
base framing; `cut` handles teleports. Before pause, suspend feedback and clear the
rig; on resume, unsuspend it. On exit, unsubscribe events/render callbacks,
`feedback.dispose()` and `rig.clear()`.

Times use seconds, offsets world units, FOV degrees and vibration milliseconds.
Pulses replace rather than stack: translation ≤0.3 per axis, FOV ≤4°, duration
≤0.5 s, vibration ≤100 ms with a 0.1 s retrigger limit. Intensity is clamped to 0–1.
Disabled feedback clears pulses. Reduced motion suppresses optional motion and
cuts framing changes; opt-in haptics are independent. OS preferences are tracked
unless overridden. Blur/hidden pages clear pulses; manual suspension persists.

`navigator.vibrate` is optional local-browser vibration, not Gamepad rumble or
remote transport; device acceptance needs live testing. Keep static health,
animation or audio cues when motion is disabled.

The rig supports perspective/orthographic pose; orthographic size belongs to the
host. Optional `occlusionTargets` use a center-line ray with `clearance`, not a
camera-volume sweep or corner/backface guarantee. Gameplay aiming/picking must
use a separate base aim, not the shaken camera.

## Action camera

`A3GameActionCameraDirector` uses an unparented PerspectiveCamera. Configure actor
heights, distance bounds, response and blockers; call
`update(dt, playerPosition, targetPosition, { mode, cut, reducedMotion, offset, fovKick })`
after simulation. Modes: `follow`, `windup`, `impact`, `airborne`, `landing`, `recovery`.
Gameplay owns mode/target selection. Repeated modes do not restart transitions.

Use `cut` on spawn/teleport, `clear` before pause and stop updates while paused.
`dispose` is idempotent. The director fits two actors with bounded same-side arcs;
check `framingLimited` when distance caps prevent a fit. Reduced motion uses stable
follow framing. Blockers retain the center-ray limitation; no launch physics or
automatic target discovery is provided. See `engine_adapters/three_js/examples/boss-action-demo/`.

## Tests

Run `npm ci` then `npm test` from this plugin directory. Tests cover runtime and
example logic without WebGL; rendering, comfort and physical haptics need live review.

## Code-video cameras

Use `compileCameraStrategy`, `validateCameraStrategy` and `inspectCameraTargets`
from `@a3game/playable`. Follow the [game-feel Skill](../../../../agent_skills/game-feel/SKILL.md)
in the source repository for configuration templates and recording steps.
