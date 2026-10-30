# Cameras for native code-video projects

Use one Camera View 2.0 JSON format across generated videos. Choose a shot from
the brief; a moving camera or interactive parameter editor is optional. Keep
playable cameras and video-model CG prompts on their existing routes.

## One implementation and one schema

Use these public exports from `@a3game/playable`:

- `validateCameraStrategy(input)` validates and clones the configuration.
- `compileCameraStrategy(THREE, input, {scene, aspect, duration_sec, fps})`
  returns `{camera, view, poseAt(time), applyAt(time)}`.
- `inspectCameraTargets(THREE, scene, camera, names, width, height, padding)`
  reports visible-geometry framing after world matrices have been updated.

The implementation lives in the framework's `src/engine/camera-view.js` and
`camera-framing.js`. The only schema is `src/engine/camera-view.schema.json`,
shipped beside the implementation for generation-time tools to read. Runtime
validation does not require loading an external validator. Install the framework
through `ThreeClient.plugin.install_framework()` before project dependencies.
Use the project's own Three.js r185 module; no adapter deep imports are needed.

## Select a strategy

| Strategy | Use | Main parameters |
|---|---|---|
| `fixed` | Clear overview or uninterrupted action | Target, azimuth, elevation, distance |
| `follow` | Keep a moving subject readable | Subject weights, axes, anchor, world offset |
| `follow_move` | Follow while changing the viewing direction | Focus plus start/end world offsets and easing |
| `orbit` | Reveal spatial relationships | Start/end spherical poses and easing |
| `move` | Lateral, rising or dolly shot | Start/end camera positions and targets, easing |
| `recorded` | Replay an existing track | Timestamped positions and xyzw quaternions |

Prefer native parameters for new scenes. Lens settings are constant within a
clip; a wide lens is independent of camera travel. Values use scene units,
Y-up right-handed world coordinates and degrees. Azimuth 0 points along +Z;
+90 points along +X. Example values below are illustrative, not defaults:

```json
{
  "schema_version": "2.0",
  "coordinate_system": "Y_UP_RIGHT_HANDED",
  "projection": {"type":"perspective","fov_y_deg":50,"near":0.1,"far":100},
  "strategy": {
    "type":"follow",
    "focus":{"subjects":[{"id":"subject","weight":1}],"axes":["x","y","z"],"anchor":[0,0,0]},
    "offset":[-3,3,-6]
  },
  "framing":{"required_targets":["subject"],"safe_margin":0.05}
}
```

Follow uses weighted object-origin world positions. Name subjects uniquely before
compilation; missing/duplicate names are errors. Followed axes use these positions;
omitted axes use `anchor`. Offsets remain in world space rather than rotating
with the subject. Use meaningful object origins and explicit framing targets.
Environment props are not automatically primary subjects. Record role changes
instead of silently removing a failed required target.

`follow_move` replaces `offset` with `start_offset`, `end_offset` and `easing`.
Orbit/move/follow_move support `linear` or `smootherstep`. They use absolute clip
time, with no stateful damping, animated lens or online obstacle avoidance.
Avoid orbit endpoints that unintentionally cross the +/-180 angle boundary.

## Native project integration

Keep the native ThreeClient project, framework host and existing recorder.
Build motion on `host.scene` and advance it through `host.onTick`; keep rules
separate from camera presentation. The following profile assumes no controls,
camera-dependent aiming or competing camera writer, and a fixed viewport:

```js
import * as THREE from 'three';
import {compileCameraStrategy} from '@a3game/playable';
import cameraView from './camera_view.json';

const previousCamera = host.camera;
const previousReview = globalThis.__A3GAME_REVIEW__;
const rig = compileCameraStrategy(THREE, cameraView, {
  scene: host.scene, aspect: 1280 / 720, duration_sec: 8, fps: 24,
});
host.camera = rig.camera;
function applyCamera(time) {
  host.scene.updateMatrixWorld(true);
  rig.applyAt(Math.max(0, Math.min(8, time)));
}
const review = ({time}) => applyCamera(time);
globalThis.__A3GAME_REVIEW__ = review;
const removePreview = host.onRender(() => applyCamera(host.elapsedSeconds));
applyCamera(0);

function disposeCamera() {
  removePreview();
  if (globalThis.__A3GAME_REVIEW__ === review) {
    if (previousReview === undefined) delete globalThis.__A3GAME_REVIEW__;
    else globalThis.__A3GAME_REVIEW__ = previousReview;
  }
  if (host.camera === rig.camera) host.camera = previousCamera;
}
```

Publish `globalThis.__A3GAME_GAME__ = game` before starting a live loop, and invoke
`disposeCamera()` during cleanup. The synchronous review hook must not step the
scene. Record using the existing public `client.playtest.record(...)` with
`mode='overview', look='off', warmup=0` and explicit output dimensions/fps/duration.
The tested profile is 8 seconds, 1280x720, 24fps. Native encoded frame k uses
`(k+1)/fps`, including the endpoint; the separate init image uses t=0. Inspect
`initial_simulated_seconds` and begin scene and camera together.

The review hook runs after host interpolation has been restored. Do not register
`host.interpolateObject` for followed subjects in this profile. `onRender` runs
before `controls.update`, so live preview requires controls to be absent. Recompile
or update the lens/aspect for live resize. A seekable camera does not make an
incremental simulation seekable: reset/replay that simulation when needed.

## Verify and preserve evidence

Check all encoded times for finite camera/scene matrices, unique named subjects,
actual scene motion and framing. Inspect `missing`, `finite`, `supported` and
`hasVisibleGeometry` before reading framing flags. Inspection streams visible
ordinary-mesh vertices; empty subjects fail, hidden descendants are excluded,
and instanced geometry is flagged unsupported. Custom shader deformation needs
its own inspection adapter. Empty box corners must not be mistaken for clipping.

Framing is not an occlusion or aesthetic score. Measure the story detail as well
as the whole assembly, inspect important beats and decode the complete video.
Larger subjects and more camera motion are not universal improvements. When
occlusion affects the action, verify finalists throughout the encoded clip;
sparse samples can miss brief crossings. Keep task-specific quality thresholds
outside the camera configuration.

Freeze scene/geometry/material/light/action source for camera-only comparisons;
record camera and scene fixes separately. Preserve original generations, rejected
candidates and repair reasons. Hash saved file bytes consistently, and retain
actual configuration, runtime versions, video settings and provenance. Keep raw
logs, temporary frames and caches in a separate audit area instead of repeatedly
collecting them into final pairs.

The native study verified fixed/orbit/follow projects with the unchanged recorder;
portable tests also cover follow_move, move and recorded evaluation. Full
Mechanic/UI task-packet integration, complete session collection and batch-scale
reliability remain separate work. Earlier unpublished study formats and their
runtime copies stay in study archives rather than adding compatibility layers
to this feature.
