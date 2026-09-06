# Unity3D Agent API Reference

Status: implemented `UnityClient` API version `v1`.

Validated engine baseline: Unity 2022.3.62f3c1.

This file is a compact index of implemented public capabilities. It lists
public names and their functions only. Read the current source when exact
parameters or result payload fields are required.

> Gameplay-triggered audio, video CG, animation CG, and VFX are documented in
> the **Media Director** section below. Use that section together with the
> public Python API when a task includes runtime media.

## Hard API Boundary

The only supported Python entry point is:

```text
from engine_adapters.unity3d import UnityClient
```

Agents, generated code, Pipeline code, and platform Serving code must not:

- import `engine_adapters.unity3d._internal`;
- import namespace client implementation classes directly;
- call transports, services, dispatchers, registries, or Editor script
  builders;
- execute arbitrary Unity Editor C# through private transports;
- modify or reference internals of the adapter-owned `A3GameRuntime`
  framework;
- depend on optional Arena Fighter, FPS, or Racing example assemblies;
- construct generated-output paths manually.

Generated gameplay belongs in separate project-local mechanic and UI
assemblies (`.asmdef`).

## Execution Authority

The game-generation Agent generates engine-native test source. The Agent MUST
NOT invoke `unity.testing.*` or declare benchmark success.

Engine execution and evaluation code owns builds, Unity Test Framework
execution, runtime evidence, and benchmark results. A zero process return code
alone is not success; NUnit XML reports must contain matching passing tests.

## Result Contract

Public operations return JSON-serializable result dictionaries using these
stable top-level fields:

- `ok` - whether the operation completed successfully;
- `operation` - stable operation identifier;
- `artifacts` - produced or retained artifact descriptors;
- `diagnostics` - structured diagnostic records;
- `warnings` - non-fatal problems;
- `errors` - fatal problems;
- `payload` - operation-specific result data.

## Client

- `UnityClient` - Creates the public Unity environment client and its namespace
  clients.
- `unity.api_version` - Reports the active public UnityClient API version.
- `unity.get_environment_info` - Reports configured project, Unity Editor,
  transport, and runtime environment information.
- `unity.generate_game` - Runs the generated-game workflow for project setup,
  mechanic/UI installation, batch asset import, scene composition, build, and
  optional Editor Play Mode.

## Project

- `unity.project.get_info` - Reports the configured Unity project and Editor
  paths.
- `unity.project.create` - Creates a minimal Unity host project without
  concrete gameplay defaults.
- `unity.project.validate` - Checks project settings, package manifest, and
  required project structure.
- `unity.project.synchronize_packages` - Adds adapter-required Unity packages
  and built-in modules to a project manifest.

## Assets

- `unity.assets.import_asset` - Imports a registered task artifact using its
  declared asset type.
- `unity.assets.import_batch` - Imports multiple registered artifacts in one
  Unity Editor operation and dependency-aware order.
- `unity.assets.import_avatar` - Imports a registered character or avatar
  artifact and creates its Unity asset representation.
- `unity.assets.import_motion` - Imports registered animation data against an
  explicit or resolved target skeleton.
- `unity.assets.import_scene` - Imports a registered Unity scene or environment
  package artifact.
- `unity.assets.import_prop` - Imports a registered prop or generic mesh
  artifact.
- `unity.assets.import_weapon` - Imports a registered weapon mesh artifact.
- `unity.assets.import_material` - Imports a registered material artifact.
- `unity.assets.import_texture` - Imports a registered texture artifact.
- `unity.assets.import_effect` - Imports a supported registered effect artifact.
- `unity.assets.validate` - Validates a registered source artifact without
  importing it when possible.
- `unity.assets.resolve_source` - Resolves a repository task identity to its
  registered source artifact.
- `unity.assets.list` - Lists assets visible in the configured Unity project.
- `unity.assets.list_registered` - Lists artifacts recorded in the adapter
  registry.
- `unity.assets.get_metadata` - Reads metadata for one registered artifact.

Public asset methods consume repository task identities. They do not accept
arbitrary generated-output filesystem paths.

## Animation

- `unity.animation.import_motion` - Imports motion through the Animation
  namespace.
- `unity.animation.resolve_skeleton` - Resolves the skeleton associated with a
  registered avatar or imported asset.
- `unity.animation.validate_compatibility` - Checks whether motion and skeleton
  artifacts are compatible.

## Bindings

- `unity.bindings.bind_pbr_material` - Creates or updates a PBR material binding
  for an imported asset.

## World

- `unity.world.compose_scene` - Creates and saves a Unity scene from structured
  imported prefab, GameObject, component, and field references.
- `unity.world.build` - Imports a registered scene, registers its environment
  artifact, and creates a validated World draft and optional package.
- `unity.world.create_draft` - Creates a persistent editable World draft.
- `unity.world.validate_draft` - Validates a World draft and its referenced
  artifacts.
- `unity.world.publish_draft` - Publishes a validated draft as a registered World
  package.
- `unity.world.list_packages` - Lists registered World packages.

World operations preserve native Unity scenes and package content when a task
supplies them.

## Plugin

- `unity.plugin.install` - Installs a registered generated mechanic or UI
  assembly into a Unity project.
- `unity.plugin.install_framework` - Installs the adapter-owned
  `A3GameRuntime` Runtime Framework.
- `unity.plugin.list` - Lists installed project assemblies.

Generated gameplay assemblies may depend only on the public `A3GameRuntime`
API.

## Build

- `unity.build.project` - Builds a Unity Player target and returns structured
  command, artifact, and diagnostic evidence.

## Testing

- `unity.testing.run_automation_tests` - Runs Unity Test Framework tests,
  parses a fresh NUnit XML report, and returns authoritative matched, passed,
  and failed counts.

The game-generation Agent must not invoke this namespace.

## Runtime

- `unity.runtime.launch_editor` - Launches the configured Unity Editor for the
  project and optional scene.
- `unity.runtime.stop_editor` - Stops an Editor process launched by the same
  runtime client.
- `unity.runtime.launch_player` - Launches a native Unity Player artifact
  produced by `unity.build.project`.
- `unity.runtime.stop_player` - Stops a Player process launched by the same
  runtime client.

## Runtime Sessions

- `unity.runtime.sessions.join` - Creates or updates a generic participant,
  controller, entity, and control-binding session.
- `unity.runtime.sessions.leave` - Marks a participant and controller offline.
- `unity.runtime.sessions.heartbeat` - Refreshes participant liveness.
- `unity.runtime.sessions.apply_input` - Applies normalized control input to a
  bound runtime entity.
- `unity.runtime.sessions.snapshot` - Returns the current generic runtime
  session state.
- `unity.runtime.sessions.reset_world` - Requests a generic runtime World
  reset.
- `unity.runtime.sessions.clear_entity` - Removes an entity and its associated
  bindings from session state.

Runtime sessions are game-neutral and do not define Fighter, FPS, or Racing
commands. Native Editor and Player sessions use the runtime bridge; Unity WebGL
sessions receive keyboard and pointer input through the browser canvas.

## Playtest Recording

- `unity.playtest.record(...)` launches one dedicated GUI Editor with
  `-executeMethod GameFactory3APlayTestRecorder.Enter`: the editor-side
  recorder enters Play Mode, captures Game-view frames at the requested
  rate, writes per-frame state snapshots from `GetStateSnapshot()` runtime
  adapters into `diagnostics.jsonl`, and exits the Editor when the take
  ends. Parameters: `output_dir`, `scene`, `scenario` or `action_plan`,
  `duration`, `fps`, `warmup`, `timeout`, `ffmpeg`, and `dry_run`.
- The recording refuses a live GUI Editor on the same project (a dedicated
  instance is required) and resolves the play scene from `scene`, else the
  first `EditorBuildSettings` entry. Take directories are cleaned before
  reuse so stale frames never mix into a new video.
- Supported action names are `move`, `look`, `jump`, `attack`, `interact`,
  `dash`, `pause`, `restart`, and `wait`. Every action has a positive integer
  `duration_ms`; scenario plans must be non-empty and fit within `duration`.
  Input injection is implemented for macOS (`System Events`) and posts real
  keyboard events after the `play_started.json` marker; on other platforms
  the client fails the take rather than record without player input.
- The output layout is shared with the other adapters: `frames/`,
  `actions.jsonl`, `report.json`, optional `diagnostics.jsonl`
  (engine-side state snapshots, privileged), and `video.mp4` when FFmpeg is
  available. The report schema is
  `gamefactory3a.unity3d.playtest_report.v1`; `recorded_seconds` uses the
  editor capture log because Play Mode is throttled when idle.
- Missing FFmpeg is non-fatal: frames, the action trace, and the report are
  retained without the video.

## Unity Media Director: audio, video CG, animation CG, and VFX

Use the engine-native `A3GameMediaDirector` component for media that is
triggered by gameplay. The cross-engine logical component name is
`media_director`, while Unity/C# uses the class-aligned file and public type:

```text
A3GameMediaDirector.cs
public sealed class A3GameMediaDirector : MonoBehaviour
```

Do **not** rename the Unity file to `media_director.cs`; keeping the
MonoBehaviour file and public class aligned is the stable Unity convention.
The Mechanic owns **when** a gameplay event occurs and supplies a stable
snake_case event key such as `hit_confirmed` or `ultimate_cg`. The Media
Director owns Unity playback objects and media evidence. It must not own damage
rules, attack timing, UI layout, Pipeline orchestration, Browser Play
transport, or benchmark scoring.

### Public operations

| Operation | Purpose | Native binding |
|---|---|---|
| `RegisterAudio(eventKey, clip)` | Register a Unity `AudioClip` | `AudioSource.PlayOneShot()` |
| `TriggerAudio(eventKey, triggerSource="gameplay")` | Play a registered audio event and return an evidence record | `AudioSource` |
| `RegisterCG(eventKey, videoUrl)` | Register a local/accessible video URL | `VideoPlayer` + `RenderTexture` |
| `TriggerCG(eventKey, triggerSource="gameplay")` | Set the URL, play video CG, and acquire the gameplay pause lock | `VideoPlayer.Play()` |
| `StopCG(eventKey, triggerSource="gameplay")` | Stop CG and release the gameplay pause lock | `VideoPlayer.Stop()` |
| `NotifyCGFinished(success)` | Release the pause lock from a `loopPointReached` or error callback | `VideoPlayer` callback |
| `RegisterAnimation(eventKey, animator, stateName, layer=0)` | Bind an Animator state | `Animator.Play()` |
| `TriggerAnimation(eventKey, triggerSource="gameplay")` | Play a registered animation CG | `Animator` |
| `RegisterVFX(eventKey, effect)` | Bind a `ParticleSystem` | `ParticleSystem` |
| `TriggerVFX(eventKey, triggerSource="gameplay")` | Restart and play the registered VFX | `ParticleSystem.Play()` |
| `StopVFX(eventKey, triggerSource="gameplay")` | Stop and clear the registered VFX | `ParticleSystem.Stop()` |
| `GetEventLog()` | Return the media runtime records | In-memory evidence |

Registration returns `false` for empty keys, null assets, or invalid bindings.
Trigger operations return a `MediaEvent` with
`playback_call_issued=false` when a binding or native player is unavailable;
do not treat a successful method return as proof that media was visible or
audible in a running game.

### Pause and completion contract

`GameplayPauseChanged` is raised when an interruptive CG acquires or releases
the combat-only pause lock; `IsGameplayPaused` exposes the current lock state.

- On an accepted `TriggerCG`, the director raises `GameplayPauseChanged(true)`.
- Game-owned Mechanic code must disable combat actions and damage while the lock
  is held. It should not use `Time.timeScale = 0` merely to pause combat,
  because video and audio must continue.
- The director releases the lock through `NotifyCGFinished`, `StopCG`, or the
  game's explicit error/fallback cleanup path, then raises
  `GameplayPauseChanged(false)`.
- The director cannot infer the correct hit window, projectile release, or
  animation transition; those remain game-owned and require native playtest
  verification.

### Runtime evidence

Every trigger returns a `MediaEvent` with schema
`gamefactory3a.media_runtime_event.v1` and fields equivalent to:

```json
{
  "schema_version": "gamefactory3a.media_runtime_event.v1",
  "seq": 1,
  "t_monotonic_ms": 1234,
  "event_type": "audio_triggered|cg_triggered|cg_animation_triggered|vfx_triggered",
  "event_key": "ultimate_cg",
  "trigger_source": "gameplay",
  "playback_call_issued": true,
  "asset_path": "Assets/Media/ultimate.mp4"
}
```

The record distinguishes the requested event, trigger source, native playback
call, monotonic runtime ordering, and the registered clip/video/effect
identity. Keep the event log with the native playtest trace; visual/audio
success still requires observing the running Unity project. Unity game code
should depend only on this public component surface, never on adapter-private
registries, transports, editor scripts, or generated-output paths.

## Reflection

- `unity.reflection.inspect_artifact` - Inspects a registered imported artifact
  through Unity reflection and returns structured metadata.

## Observation

- `unity.observe.check_status` - Reports Editor transport, project, runtime,
  and observation readiness.

## A3GameRuntime Public C# Contract

Generated gameplay assemblies may reference the public API under:

```text
A3GameRuntime/Runtime/
```

### Enums

- `A3GameControlMode` - Identifies the generic control mode assigned to an
  entity.
- `A3GameLocomotionState` - Represents generic locomotion state in runtime
  snapshots.

### Data Types

- `A3GameRuntimeInputState` - Carries normalized movement, look, action, and
  timing state.
- `A3GameEntitySpawnRequest` - Describes a generic entity spawn request.
- `A3GameParticipantInfo` - Describes one runtime participant.
- `A3GameControllerState` - Describes one generic controller.
- `A3GameControlBinding` - Connects a participant, controller, and entity.
- `A3GameEntitySnapshot` - Reports observable generic entity state.

### Interfaces

- `IA3GameControllableEntity` - Contract implemented by game-owned controllable
  entities.
- `IA3GameEntityFactory` - Contract implemented by game-owned entity factories.
- `IA3GameRuntimeMessageHandler` - Contract for game-owned runtime message
  handling.

### Components

- `A3GameIdentityComponent` - Stores stable runtime identity on a game-owned
  GameObject.
- `A3GameRuntimeEntityComponent` - Connects a game-owned GameObject to runtime
  entity state and control input.

### Subsystems

- `A3GameRuntimeSubsystem` - Registers game-owned factories and coordinates
  generic runtime entity creation.
- `A3GameWorldSessionSubsystem` - Owns generic participant, controller, entity,
  binding, input, and snapshot session state.
- `A3GameRuntimeInputReceiver` - Receives runtime input messages and forwards
  them to the runtime subsystem.

## Framework Boundaries

`A3GameRuntime` provides runtime contracts and coordination components only.
It does not provide a concrete character, controller, movement implementation,
weapon, vehicle, combat rule, HUD, or game-specific input mapping.

Generated projects own concrete gameplay implementation. Optional
ArenaFighterExample, FPSExample, and RacingExample assemblies are read-only
references and are not dependencies or success criteria.

## Transport

Unity does not have Unreal's Python Remote Execution or HTTP Remote Control.
The `UnityClient` uses a subprocess transport. Every Editor invocation sets
the subprocess working directory to the generated Unity project root. This is
required because Unity Editor scripts use project-relative paths such as
`Assets/Imported/Weapons`; without that cwd contract, a relative file operation
could write outside the project and AssetDatabase would not see the import:

1. Copies the required bundled C# Editor script into the project's
   `Assets/Editor/` folder when needed
2. Writes operation arguments to a temporary JSON job file
3. Invokes `Unity -batchmode -quit -projectPath <proj> -executeMethod
   <Class.Method> --job <job.json> --report <report.json>`
4. The C# script writes a JSON report to a temporary file
5. The Python transport reads the JSON report and returns it

### Unity licensing prerequisite

Unity licensing is an external host prerequisite, not an 3AGameFactory
operation. Before invoking a mutating client method, the selected Editor must
be installed and activated through the matching Unity Hub/Tuanjie Hub account,
or an already-open licensed Editor must be available. `UnityClient` does not
discover credentials, activate seats, or replace the Hub licensing daemon.

The direct batch transport deliberately does not force `-licensingIpc`: Unity
and its Hub choose the correct local LicensingClient channel. If the host has
conflicting Hub daemons or no activation, Unity can exit before loading the
project (commonly exit code 199). The transport returns
`blocked=true`, `blocked_stage="licensing"`, `license_status`, and the log
tail in that case, so callers fail fast with the external prerequisite rather
than claiming that import, compilation, or build succeeded.

Use `<REPO_PATH>/scripts/engine_install/unity/` for Unity project creation, game
generation, asset import, and runtime launch commands. The top-level
`import-batch` public client command imports batches of assets. Use
`generate-game` for the complete pipeline so one Editor session owns plugin
installation, asset import, material remapping, scene composition, compile,
build, and optional Play Mode.

### Runtime Input Transport

For runtime sessions, a `RuntimeUDPBridge` sends JSON datagrams to the
`A3GameRuntimeInputReceiver` C# component (UDP port 30030), mirroring UE5's
UDP bridge to `A3GameRuntimeInputPort` (port 30020).

## Coordinate System

glTF and Unity are Y-up and use metres, but their handedness conventions
differ. Unity's model importer performs the format conversion; adapter or
gameplay code should not add a second blanket axis conversion. FBX authoring
axes and units can vary, so imported orientation, scale, rig, and weapon
forward direction still require inspection or importer configuration.

# Unity VFX API

For smoke, fire, explosion, dust, and particle lifecycle work, use the
[`create-vfx-effects`](create-vfx-effects/SKILL.md) skill and
`<REPO_PATH>/engine_adapters/unity3d/vfx/Runtime/A3Game_VFX.cs`.

Prefer an existing reviewed particle or VFX Graph prefab through `SpawnPrefab`.
The named ParticleSystem functions are no-asset fallbacks. Their positions use
Unity world-space meters.

The procedural style fallbacks are `SpawnInkSmoke`, `SpawnFrostFire`, and
`SpawnCyberFire`. Prefer authored prefabs when available; the style functions are
layered fallbacks and still require visual approval.

## Import Generated Meshes

Use the host launcher for generated GLB, FBX, or OBJ files:

```bash
python scripts/import_generated_asset.py --engine unity \
    --src <model> --unity-project <project>
```

The launcher installs
`<REPO_PATH>/engine_adapters/unity3d/import_generated/ImportGeneratedMesh.cs` under the
project's `Assets/Editor/` directory and invokes `ImportGeneratedMesh.RunFromCLI`.
Use `--usage asset` for ordinary meshes, `vfx_standalone` for a single effect
mesh, and `vfx_particle` for meshes instanced by a particle system.

Treat the JSON import report as the result contract. Check `ok`, `assetPath`,
`prefabPath`, triangle and material counts, bound textures, bounds, and warnings
before referencing the prefab. GLB import requires `com.unity.cloud.gltfast`;
the full project setup is in `<REPO_PATH>/scripts/engine_install/README.md`.
Generated projects own concrete gameplay implementation. Optional Arena
Fighter, FPS, and Racing examples are read-only references and are not runtime
dependencies or success criteria.
