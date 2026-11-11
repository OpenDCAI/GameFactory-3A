# Code-video cameras

## 1. Choose a strategy

| Strategy | Parameters |
|---|---|
| `fixed` | Target, azimuth, elevation, distance |
| `follow` | Named subjects, weights, followed axes, anchor, world offset |
| `follow_move` | Follow settings, start/end world offsets, easing |
| `orbit` | Start/end targets, angles and distances, easing |
| `move` | Start/end camera positions and targets, easing |
| `recorded` | Timestamped positions and xyzw quaternions |

Use scene units, degrees and Y-up right-handed coordinates. Azimuth 0 is +Z;
+90 is +X. Lens settings stay constant within a clip. Easing is `linear` or
`smootherstep`; choose explicit orbit angles to control the rotation direction.

## 2. Write camera_view.json

Use the shared [configuration schema](view_template/camera-view.schema.json).
Name followed objects uniquely. Follow offsets use world coordinates; axes not
followed use `anchor`. For example:

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

## 3. Connect the camera

Install the framework with `ThreeClient.plugin.install_framework()` before
project dependencies. Use the project's Three.js r185 module and public exports:

```js
import * as THREE from 'three';
import {compileCameraStrategy, inspectCameraTargets} from '@a3game/playable';
import cameraView from './camera_view.json';

const rig = compileCameraStrategy(THREE, cameraView, {
  scene: host.scene, aspect: 1280 / 720, duration_sec: 8, fps: 24,
});
function applyCamera(time) {
  host.scene.updateMatrixWorld(true);
  rig.applyAt(Math.max(0, Math.min(8, time)));
}
```

Save the previous camera and review hook, then set `host.camera = rig.camera`.
Set `globalThis.__A3GAME_REVIEW__ = ({time}) => applyCamera(time)` for recording;
use `host.onRender(() => applyCamera(host.elapsedSeconds))` for preview. Apply t=0
before the first render. On cleanup, remove the preview subscription and restore
the previous camera and hook. Recompile after changing the JSON or viewport.

Use one camera writer, no Orbit/PointerLock controls, and no camera-dependent
aiming. Do not use `host.interpolateObject` on followed subjects. Advance scene
motion with `host.onTick`; the review hook only updates the camera.

## 4. Record and check

Publish `globalThis.__A3GAME_GAME__ = game` before starting the live loop. Call
`client.playtest.record(...)` with `mode='overview'`, `look='off'`, `warmup=0` and
explicit dimensions, fps and duration. Start scene and camera together. Encoded
frame k uses `(k+1)/fps`, including the endpoint; the init image uses t=0.

After updating world matrices and applying the camera, call
`inspectCameraTargets(THREE, host.scene, rig.camera, rig.view.framing.required_targets,
1280, 720, rig.view.framing.safe_margin)`. Check missing/empty/unsupported geometry,
finite values, subject size and safe-frame coverage. Review the full video for
occlusion and readability; framing checks do not detect occlusion or adjust shots.
Instanced geometry and shader deformation require a separate inspection adapter.

Save the video with its scene source and camera_view.json. Keep temporary frames
and logs outside the final pair.
