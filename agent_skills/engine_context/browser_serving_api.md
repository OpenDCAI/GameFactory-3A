# Browser Serving Agent API Reference

Status: implemented Browser Serving API version `v1`.

This file is a compact index of implemented public capabilities. It lists
public names and their functions only. Read the current source when exact
parameters, routes, or result payload fields are required.

## Browser/Backend API Boundary

Browser Serving is the engine-neutral browser mapping layer:

```text
Browser UI -> Browser Serving API -> EngineBackend -> Engine Client -> Engine
```

Browser UI and generated Browser Play source must not import concrete
backends, call Engine clients directly, or branch on Engine names. Registered
Backends and the Gateway composition root may access UE5 and Unity only
through their public clients:

```python
from engine_adapters.ue5 import UEClient
from engine_adapters.unity3d import UnityClient
```

The allowed call direction is:

```text
Browser Play HTTP/fetch
    -> Browser Serving Gateway
        -> registered EngineBackend
            -> public Engine Client
                -> Engine runtime
```

Browser Play never constructs an `EngineBackend`, `UEClient`, or
`UnityClient`. A game-specific backend or recording preset belongs in the
execution composition root that registers the backend, not in generated
Browser Play or an `<REPO_PATH>/engine_adapters/*/examples` directory.

Browser Serving exposes engine view, assets, Worlds, sessions, streams, and
generic input. It does not replace engine-native Mechanic UI.

## Result Contract

Public operations return JSON-serializable results with these stable fields:

- `ok` - whether the operation completed successfully;
- `operation` - stable operation identifier;
- `engine` - selected backend identifier;
- `artifacts` - produced or inspected artifacts;
- `warnings` - non-fatal problems;
- `errors` - fatal problems;
- `payload` - operation-specific result data.

## Public Entry Points And Ownership

Python Agents import the public API from:

```python
from engine_adapters.browser_serving import BrowserServingClient
```

`BrowserServingClient` is a host-side Python client for Admin, execution, and
integration tooling. Browser JavaScript uses the documented HTTP routes; it
does not import the Python client.

- `API_VERSION` - Reports the public Browser Serving API version.
- `BrowserServingClient` - Calls the public Browser Serving HTTP API.
- `BrowserServingConfig` - Resolves Gateway, Admin, Engine, stream, upload, and
  session configuration.
- `BrowserServingService` - Gateway/service composition API that delegates
  operations to a registered Engine backend. It is not a generated Browser
  Play dependency.
- `CgVideoGateway` - Queues a canonical CG-video task and records its
  generated artifact.
- `CgVideoGatewayProtocol` - Injection contract for a CG-video job gateway.
- `CgVideoError` - Reports a rejected or failed CG-video request.
- `EngineBackend` - Protocol implemented and registered by the Gateway
  composition root. Generated Browser Play treats it as opaque.
- `EngineCapabilities` - Declares supported browser-facing capabilities.
- `EngineDescriptor` - Describes a registered backend.
- `AssetImportRequest` - Carries a staged task artifact to a backend.
- `StagedUpload` - Describes a browser upload materialized as a canonical task
  artifact.
- `AssetRecord` - Describes an engine-neutral imported asset.
- `WorldRecord` - Describes an engine-neutral runtime World.
- `BrowserServingError` - Base public Browser Serving error.
- `UnknownEngineError` - Reports an unregistered Engine identifier.
- `EngineCapabilityError` - Reports an operation unsupported by a backend.
- `create_app` - Creates the Browser Serving FastAPI Gateway.

## Client

- `client.health` - Reports Gateway readiness and registered Engines.
- `client.engines` - Lists Engine descriptors and capabilities.
- `client.engine_status` - Reports readiness for one backend.

## Assets

- `client.assets.upload` - Stages an uploaded file as a canonical task artifact
  and imports it through the selected backend.
- `client.assets.import_descriptor` - Imports an existing generated task
  artifact through the selected backend.
- `client.assets.list` - Lists backend-visible assets.
- `client.assets.groups` - Groups assets by engine-neutral asset type.
- `client.assets.inspect` - Inspects one imported artifact.

Uploads use `pipeline.common.paths`. Cross-engine consumers select assets by
`artifact_id`; backend-native paths are metadata only.

## Worlds

- `client.worlds.upload` - Stages a Scene and invokes backend World
  build/publication.
- `client.worlds.list` - Lists backend-visible runtime World packages.

## Sessions

- `client.sessions.create` - Starts a browser-owned Engine/stream session.
- `client.sessions.list` - Lists active sessions.
- `client.sessions.get` - Reads one session.
- `client.sessions.recover` - Re-registers a still-reachable session.
- `client.sessions.catalog` - Lists runtime-ready Avatars, Motions, and Worlds.
- `client.sessions.configure` - Selects Avatar, idle Motion, move Motion, and
  character options.
- `client.sessions.play_preview_animation` - Plays one preview Motion.
- `client.sessions.load_world` - Selects a runtime World.
- `client.sessions.join` - Enters Play mode.
- `client.sessions.leave` - Leaves Play mode.
- `client.sessions.apply_input` - Sends normalized movement, look, run, and
  jump input.
- `client.sessions.apply_preview_camera` - Sends preview camera input.
- `client.sessions.stop` - Stops the Engine session and stream.

## CG Video

- `client.cg_video.generate` - Queues a CG-video task by its repository task
  identity and returns a request id.
- `client.cg_video.status` - Reads one queued, running, ready, or failed
  request.

The request resolves `test_data/test_samples/<game_id>/cg_video/cg_tasks.jsonl`
and never accepts a prompt or credential from the browser. A completed clip is
stored under the standard CG-video output directory and exposed through a
gateway-controlled media URL.

The request body contains `game_id`, `task_id`, optional `run_id`, `backend`,
`engine`, `session_id`, `trigger_id`, `idempotency_key`, `options`, and
`playback`. The payload reports `status` (`queued`, `running`, `ready`, or
`failed`); a ready response includes `video.artifact_id`, `video.media_type`,
and `video.url`, with the same public descriptor listed in `artifacts`.

Game-specific actions remain owned by the generated Mechanic contract.

## Browser HTTP API

Generated Browser Play calls the Gateway HTTP API rather than importing the
Python client or an Engine Client. Public routes map directly to the client
operations above:

- `GET /api/health` - Reports Gateway readiness.
- `GET /api/engines` - Lists registered Engines and capabilities.
- `GET /api/engines/{engine}/capabilities` - Reads one capability set.
- `GET /api/engines/{engine}/status` - Reads one backend's readiness.
- `POST /api/assets/upload` - Uploads and imports an asset.
- `POST /api/assets/import` - Imports a generated artifact descriptor.
- `GET /api/assets` - Lists assets.
- `GET /api/assets/groups` - Groups assets by type.
- `POST /api/assets/inspect` - Inspects an asset.
- `GET /api/worlds` - Lists runtime Worlds.
- `POST /api/sessions` - Creates a session.
- `GET /api/sessions` - Lists sessions.
- `GET /api/sessions/catalog` - Lists runtime-ready assets and Worlds.
- `POST /api/sessions/recover` - Recovers a session snapshot.
- `POST /api/sessions/runtime-event` - Applies an Engine readiness or runtime
  event to a session.
- `GET /api/sessions/{session_id}` - Reads a session.
- `POST /api/sessions/{session_id}/character` - Configures its character.
- `POST /api/sessions/{session_id}/preview-animation` - Plays a preview
  Motion.
- `POST /api/sessions/{session_id}/load-world` - Selects a World.
- `POST /api/sessions/{session_id}/join` - Enters Play mode.
- `POST /api/sessions/{session_id}/leave` - Leaves Play mode.
- `POST /api/sessions/{session_id}/input` - Applies normalized input.
- `POST /api/sessions/{session_id}/preview-camera` - Applies preview camera
  input.
- `DELETE /api/sessions/{session_id}` - Stops a session.
- `WS /api/sessions/{session_id}/input-ws` - Streams normalized input.
- `POST /api/cg-video` - Queues one CG-video task.
- `GET /api/cg-video/{request_id}` - Reads CG-video job status.
- `GET /api/media/cg-video/{artifact_id}` - Streams a completed MP4 artifact.

HTTP results use the Result Contract above. Browser code reads operation data
from `payload`, and reads the playable Engine URL from session
`payload.stream_url`.

## Capabilities And Streams

- `asset_upload` - Backend accepts browser-staged asset uploads.
- `asset_import` - Backend imports generated task descriptors.
- `asset_inspection` - Backend exposes imported artifact inspection.
- `world_build` - Backend builds or publishes Worlds.
- `world_catalog` - Backend lists runtime Worlds.
- `runtime_sessions` - Backend supports browser-owned sessions.
- `skeletal_animation` - Backend supports Avatar/Motion selection.
- `streaming` - Backend returns a browser-embeddable `stream_url`.
- `pixel_streaming` - Backend provides UE-compatible Pixel Streaming.
- `preview_camera` - Backend accepts preview camera controls.

Browser UI enables features from capabilities, not Engine names. Generated
Browser Play consumes `stream_url`; transport-specific URL aliases are not the
cross-Engine contract.
Passing `engine` as backend selection data is allowed; Engine-specific UI logic
is not. The Gateway, not the Browser page, chooses the registered backend.

## EngineBackend Contract

This is a backend implementation contract for the Gateway composition root,
not an API that generated Browser Play code implements or imports.

- `descriptor` - Returns backend identity and capabilities.
- `status` - Reports backend readiness.
- `import_asset` - Imports a staged artifact.
- `inspect_asset` - Inspects one artifact.
- `list_assets` - Lists assets.
- `list_worlds` - Lists runtime Worlds.
- `build_world` - Builds or publishes a World.
- `create_session` - Creates an Engine/browser session.
- `list_sessions` - Lists sessions.
- `get_session` - Reads one session.
- `recover_session` - Recovers one session.
- `session_catalog` - Lists runtime-ready assets and Worlds.
- `configure_session` - Configures the session character.
- `play_preview_animation` - Plays a preview animation.
- `load_world` - Selects a World.
- `join_world` - Enters Play mode.
- `leave_world` - Leaves Play mode.
- `apply_input` - Applies normalized input.
- `apply_preview_camera` - Applies preview camera input.
- `handle_runtime_event` - Applies Engine readiness and runtime events.
- `stop_session` - Stops one session and its owned processes.
- `debug` - Runs supported developer controls without exposing Engine
  internals to frontend code.

## Bundled Backends

- `create_ue5_example_backend` - Creates the UE5 backend, maps operations to
  `UEClient`, and exposes UE Pixel Streaming through `stream_url`.
- `create_unity3d_example_backend` - Creates the Unity3D backend, maps
  operations to `UnityClient`, and exposes a Unity WebGL page through
  `stream_url`.

UE5 sessions use Pixel Streaming and deliver normalized input through the UE
runtime session. Unity browser sessions use `runtime_kind=unity_webgl`,
`streaming_transport=unity_webgl_http`, and
`input_transport=browser_canvas`; keyboard and pointer events are delivered
directly to the Unity canvas, not through UE-compatible Pixel Streaming.

Bundled backends are implementation references and registered backend
implementations. Generated Browser UI must not import them. A project-specific
backend may subclass or compose an implementation only in the project or
execution composition root, and it must still use the selected public Engine
Client.

## Frontends

- `build_admin_app` - Creates the asset and session administration UI.
- `launch_admin` - Runs the Admin UI.
- `run_gateway` - Runs the Gateway, API, player, and mounted Browser Play.
- `run_all` - Runs the Gateway and Admin UI.
- `frontend/player` - Discovers Engines, manages sessions, presents
  `stream_url`, and handles generic input.
- `BrowserPlayExample` - Read-only reference for session creation/recovery,
  stream presentation, focus, fullscreen, and error handling.

Generated Browser Play reads the engine-neutral `stream_url`, keeps input in
the Engine frame, and reports session or stream errors. It does not configure
backends, call Engine Clients, inject game-specific commands, or duplicate
engine-native Mechanic UI.

## UI Generation Boundary

Engine-native UI uses the selected Engine API and generated Mechanic contract.
Browser Play uses this Browser Serving API for stream, session, focus,
fullscreen, error, and generic input controls only.

Browser Serving does not expose a versioned Mechanic state/event/command bridge
to Web. Generated Browser Play must not invent health, ammo, score, objective,
pause, victory, or game-specific command APIs. Game-specific actions stay in
the native Engine UI and the Mechanic contract. CG-video generation is the
single generic media operation exposed by the Browser Serving API; gameplay
still decides when a clip is meaningful and how it is presented.

## Launch

- `python -m engine_adapters.browser_serving all` - Runs Admin and Gateway.
- `python -m engine_adapters.browser_serving gateway` - Runs the Gateway only.
- `python -m engine_adapters.browser_serving admin` - Runs the Admin UI only.
- `A3GAME_BROWSER_PLAY_DIR` - Selects the mounted generated Browser Play
  directory.
- `A3GAME_BROWSER_ENGINE` - Selects the default backend.
- `A3GAME_UE_PROJECT` / `A3GAME_UE_ROOT` - Configure the UE5 backend.
- `A3GAME_UNITY_PROJECT` / `A3GAME_UNITY_ROOT` - Configure the Unity backend.
- `A3GAME_UNITY_WEBGL_BUILD` - Selects an existing Unity WebGL build.
- `A3GAME_BROWSER_DRY_RUN` - Validates Serving lifecycle without real Engine
  rendering.
- `A3GAME_BROWSER_CG_VIDEO_ENABLED` - Enables the CG-video gateway (default
  `true`).
- `A3GAME_BROWSER_CG_VIDEO_ALLOW_CLOUD` - Explicitly permits cloud video
  requests (default `false`).
- `A3GAME_BROWSER_CG_VIDEO_MAX_WORKERS` - Maximum concurrent CG-video jobs
  (default `1`).

Default ports are `7860` for Admin, `7870` for Gateway, `18080+` for session
pages, and `18888+` for UE streamer WebSockets. Unity WebGL does not use the UE
streamer WebSocket ports.

Launch scripts may set these documented environment variables and delegate to
the Browser Serving Gateway. They must not implement a replacement backend,
launch an Engine outside the registered backend lifecycle, or return a browser
URL before the Engine session reports ready.
