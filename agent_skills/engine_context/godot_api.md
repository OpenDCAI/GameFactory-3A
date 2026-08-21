# Godot Agent API Reference

Status: implemented `GodotClient` API version `v1` for Godot 4.x.

This is the executable capability index for an AI agent. It covers engine
installation first, then every supported public host call. The only supported
Python entry point is:

```python
from engine_adapters.godot import GodotClient
```

Do not import `engine_adapters.godot._internal`, call adapter-owned GDScript
directly, edit `.a3game` state, construct generated-output paths, or make a
generated game depend on `engine_adapters/godot/examples/` at runtime.

## 1. Discover, install, and validate Godot before project work

An agent must establish a verified Godot executable before creating a project,
importing an asset, or launching a game.

Linux/macOS:

```bash
scripts/engine_install/godot/install.sh --dry-run --json
scripts/engine_install/godot/install.sh --version 4.5.1 --json
```

Windows:

```bat
scripts\engine_install\godot\install.cmd --dry-run --json
scripts\engine_install\godot\install.cmd --version 4.5.1 --json
```

Require exit code zero, `ok=true`, and an exact `verified_version`. The default
is pinned to `4.5.1-stable`; `latest` is deliberately rejected. The installer:

- probes `--executable`, `A3GAME_GODOT_EXECUTABLE`, `A3GAME_GODOT`, legacy
  `AAAGF_GODOT`, then `godot4`/`godot`/`godot-mono` on PATH and reuses only the
  requested version;
- selects Linux x86-64/x86-32/arm64/arm32, universal macOS, or Windows
  x64/x86/arm64 official assets;
- downloads only the official GitHub Godot release over HTTPS, requires one
  exact asset entry in official `SHA512-SUMS.txt`, and fails on mismatch;
- rejects unsafe archive traversal, links, duplicate paths, and special nodes;
- extracts to a sibling staging directory, probes the staged binary, and only
  then publishes it by rename, restoring the preserved target if replacement
  fails;
- writes a version/platform manifest, a `godot4` PATH shim, machine-readable
  JSON, and sourceable `.env` or `.cmd` configuration without editing profiles;
- is idempotent (`action=reused-managed`); a new exact `--version` installs
  beside the old one, while `--force` replaces only that resolved target.

The installer and adapter use Python 3.8+ standard library and do not require
Python 3.12, pip packages, or an engine SDK package. Compatibility is gated by
the full Godot adapter suite on Python 3.8.10. Platform details, flags, and
failure semantics are in `scripts/engine_install/godot/README.md`.

Configure the returned paths and validate through the public client:

```bash
export A3GAME_GODOT_EXECUTABLE=/absolute/path/to/godot4
export A3GAME_GODOT_PROJECT=/projects/MyGame
scripts/engine_install/godot/create_project.sh --name MyGame
python3 -m engine_adapters.godot \
  --project "$A3GAME_GODOT_PROJECT" validate
```

## 2. Client configuration and result contract

```python
godot = GodotClient(
    project_path="/projects/MyGame",       # directory or project.godot
    godot_executable="/opt/godot/godot4", # optional after PATH setup
    api_version="v1",
    runtime_host="127.0.0.1",
    runtime_port=30050,
    editor_timeout=300,
    import_timeout=300,
)
```

Constructor precedence and state:

| Setting | Resolution |
| --- | --- |
| Project | argument → `A3GAME_GODOT_PROJECT` → legacy `AAAGF_GODOT_PROJECT` |
| Executable | argument → `A3GAME_GODOT_EXECUTABLE` → `A3GAME_GODOT` → legacy `AAAGF_GODOT` → PATH discovery |
| Runtime | arguments → `A3GAME_GODOT_RUNTIME_HOST` / `A3GAME_GODOT_RUNTIME_PORT` → `127.0.0.1:30050` |
| Timeouts | arguments → `A3GAME_GODOT_EDITOR_TIMEOUT` / `A3GAME_GODOT_IMPORT_TIMEOUT` → 300 seconds |
| Private state | `A3GAME_GODOT_DATA_ROOT` → `<project>/.a3game` |
| Artifact registry | `A3GAME_GODOT_ARTIFACT_REGISTRY` → `<data-root>/artifacts.json` |
| World registry | `A3GAME_GODOT_WORLD_REGISTRY_ROOT` → `<data-root>/worlds` |

All managed path components must be ordinary directories and all leaves regular
files. Symbolic links, special nodes, path escapes, malformed strict JSON,
`NaN`, and infinity fail closed.

Except `api_version` and `runtime.sessions.probe`, every public operation returns
a strict-JSON dictionary with exactly these top-level fields:

| Field | Meaning |
| --- | --- |
| `ok` | Boolean success; never infer success from process exit alone |
| `operation` | Stable operation name |
| `artifacts` | Produced/selected artifact records |
| `diagnostics` | Structured engine diagnostics |
| `warnings` | Non-fatal limitations, including documented local runtime fallback |
| `errors` | Fatal messages; non-empty on failure |
| `payload` | Operation-specific values described below |

`runtime.sessions.probe(timeout)` is the low-level UDP reachability record and
returns `ok`, `reachable`, response/error details, and request matching evidence.

## 3. Complete public call index

The signatures below are authoritative for `v1`. `E0`–`E8` refer to runnable
examples in section 4.

### Root and Project

| Call | Purpose and successful return | Failure behavior | Example |
| --- | --- | --- | --- |
| `godot.api_version -> str` | Returns literal `v1` | Constructor rejects unsupported API versions | E0 |
| `godot.get_environment_info(*, probe_version=True)` | Project/executable paths, existence, engine version support, runtime endpoint and registry paths | Missing/wrong executable or unsafe state paths are represented in the result | E0 |
| `godot.project.get_info(*, probe_version=True)` | Project marker, main scene, executable and optional `--version` evidence | Probe errors are structured; no state mutation | E0 |
| `godot.project.create(project_path=None, *, project_name="", renderer="gl_compatibility", overwrite=False, dry_run=False)` | Creates a minimal Godot 4 `project.godot`, main scene and import roots; artifacts list written paths | Rejects unsupported renderer, linked/special targets, existing managed files without `overwrite`, partial/unsafe layouts | E1 |
| `godot.project.validate(*, check_engine=True)` | Validates marker/main scene; with engine check, Godot loads and instantiates the resolved `PackedScene` | Missing/unloadable/UID-invalid scene, non-Godot-4 executable, import/load diagnostics or unsafe paths fail | E1 |

### Assets

`source` is a mapping containing `{game_id, run_id, task_kind, task_id,
artifact_key}`. Each identity is one safe path component below repository
`OUTPUT_ROOT`; callers cannot provide an arbitrary generated-output path.
`options` is strict-JSON metadata forwarded to import handling; common options
include `replace_existing` and import hints.

| Call | Purpose and successful return | Failure behavior | Example |
| --- | --- | --- | --- |
| `godot.assets.import_asset(source, asset_type, *, destination="", options=None)` | Resolves, stages, imports, natively inspects and registers any supported type; artifact contains real `res://` path/class/capabilities | Unknown type/identity, source escape, format mismatch, conflict, import/load error or native contract mismatch rolls back files/cache/registry | E2 |
| `godot.assets.import_avatar(source, **kwargs)` | Typed `avatar` helper; accepts `destination` and `options`; returns registered skinned `PackedScene` | Requires mesh, Skeleton3D bones and a skeleton-linked skinned mesh | E2 |
| `godot.assets.import_motion(source, *, skeleton="", destination="", avatar_name="", options=None)` | Typed motion import with optional required live Skeleton3D path/avatar label | Requires a `PackedScene`, animation and bone-targeted track compatible with requested skeleton | E3 |
| `godot.assets.import_scene(source, **kwargs)` | Typed spawnable scene helper; accepts `destination`/`options` | Native resource must load and instantiate as `PackedScene` | E2 |
| `godot.assets.import_prop(source, **kwargs)` | Typed prop helper; accepts `destination`/`options` | Requires instantiable `PackedScene` with `MeshInstance3D`; bare OBJ fails | E2 |
| `godot.assets.import_weapon(source, **kwargs)` | Typed weapon helper; accepts `destination`/`options` | Same spawnable mesh contract as prop | E2 |
| `godot.assets.import_material(source, **kwargs)` | Typed Godot material/image import; accepts `destination`/`options` | Wrong native class, decode/import error or unsafe target fails | E2 |
| `godot.assets.import_texture(source, **kwargs)` | Typed `Texture2D` import; accepts `destination`/`options` | Corrupt/unsupported image, missing imported resource or wrong class fails | E2 |
| `godot.assets.import_effect(source, **kwargs)` | Typed effect resource import; accepts `destination`/`options` | Wrong source/target/native load contract fails | E2 |
| `godot.assets.import_audio(source, **kwargs)` | Typed `AudioStream` import; accepts `destination`/`options` | Decode/load/class mismatch fails | E2 |
| `godot.assets.validate(source, asset_type, *, destination="", options=None)` | Runs source identity, format, destination and conflict preflight without copying | Returns structured path/type/source errors and makes no changes | E2 |
| `godot.assets.resolve_source(source, *, asset_type="")` | Resolves trusted task metadata/artifact path and returns source details | Missing metadata/key/file, identity mismatch, link or OUTPUT_ROOT escape fails | E2 |
| `godot.assets.list(asset_type="", *, root="assets/imported")` | Lists in-project imported resources below a contained root | Traversal, external root, linked/special entries or unreadable project fails | E2 |
| `godot.assets.list_registered(asset_type="")` | Returns registry artifacts and filtered count | Malformed/linked/special/non-strict registry fails without replacement | E2 |
| `godot.assets.get_metadata(artifact_id)` | Returns one registered artifact and metadata | Unknown ID or invalid registry fails | E2 |
| `godot.assets.register_resource(*, resource_path, asset_type, asset_id, source_path="", backend_class="", spawnable=None, metadata=None)` | Natively loads an existing canonical in-project `res://` resource, derives class/spawnability, and registers it; optional claims act as assertions | Traversal/link/missing resource, wrong type/class/spawnability, load/instantiate failure or ambiguous registry upsert fails without writing | E2 |

Supported asset types are `avatar`, `motion`, `scene`, `environment`, `effect`,
`material`, `texture`, `prop`, `static_mesh`, `weapon`, and `audio`. glTF main
files and local buffers/images are one transaction. Failed native validation
restores replaced source files, adjacent `.import` files, matching
`.godot/imported` cache files, and the prior registry.

### Animation and material binding

| Call | Purpose and successful return | Failure behavior | Example |
| --- | --- | --- | --- |
| `godot.animation.import_motion(source, *, skeleton, destination="", avatar_name="", options=None)` | Imports a motion and requires the named live Skeleton3D NodePath | Empty/missing skeleton, absent animation/bone track or mismatched target fails/rolls back | E3 |
| `godot.animation.resolve_skeleton(avatar)` | Reloads registered avatar and returns live Skeleton3D paths/bones | Unknown/non-avatar/unloadable/unskinned resource fails | E3 |
| `godot.animation.validate_compatibility(motion, skeleton)` | Reloads motion and verifies animation plus bone track targeting the requested live skeleton | Structural mismatch fails; does not claim visual retargeting quality | E3 |
| `godot.bindings.bind_pbr_material(*, asset_id, source, mesh_assets, destination="assets/imported/materials", options=None)` | Imports/creates material, applies `material_override` through Godot to every target mesh, saves bound scenes, atomically retargets records and returns manifest/artifacts | Missing target/material, zero changed meshes, engine error, unsafe state, duplicate target or any commit failure restores material/scenes/texture/manifest/registry | E3 |

### Worlds

| Call | Purpose and successful return | Failure behavior | Example |
| --- | --- | --- | --- |
| `godot.world.build(source, *, options=None)` | Imports a scene then creates, validates and optionally publishes a World according to options | Any asset/draft/native validation failure is returned; no fabricated package | E4 |
| `godot.world.create_draft(spec, *, draft_id="", project_id="", metadata=None)` | Writes a versioned draft pointing to a ready registered scene; explicit IDs override spec and explicit metadata merges over spec | Wrong schema/JSON, unsafe registry, missing/non-spawnable/non-`PackedScene` scene or native load failure makes no valid draft | E4 |
| `godot.world.validate_draft(draft_id)` | Revalidates record/file identity, lifecycle and current registered/native scene | Unknown/damaged/version-mismatched/stale draft fails | E4 |
| `godot.world.publish_draft(draft_id)` | Atomically publishes a validated package and returns package artifact | Only validated drafts publish; linked/special paths, collision or stale scene fail | E4 |
| `godot.world.list_packages(*, project_id="", world_id="")` | Returns strict packages filtered by either/both IDs | One damaged record fails the read; records are never silently skipped/synthesized | E4 |

### Plugin

| Call | Purpose and successful return | Failure behavior | Example |
| --- | --- | --- | --- |
| `godot.plugin.install(source, *, install_dir="", replace_existing=False, enable=True, dry_run=False)` | Installs a registered generated add-on, natively validates `plugin.cfg`/entry, optionally enables it and returns copied files | Source escape/link/special node, unsafe name/script, missing metadata, non-`@tool EditorPlugin`, existing target or ambiguous settings fails atomically | E5 |
| `godot.plugin.install_framework(*, replace_existing=False, enable=True, dry_run=False)` | Installs adapter-owned `A3GamePlayable` v1.1.0, enables plugin and `A3GameRuntime` autoload; payload lists copied implementation/test files | Same atomic validation, target and settings rules; conflicting autoload requires explicit replacement | E5 |
| `godot.plugin.list()` | Lists safe installed add-on descriptors; framework gets distinct artifact type | Unreadable/linked entries are skipped with warnings; unsafe project root fails | E5 |

The framework's machine-readable capability matrix and native smoke test live at
`engine_adapters/godot/plugin/A3GamePlayable/`. It implements identity,
normalized input, sessions/entity binding, scene loading, animation dispatch,
ray/sphere collision probes, HUD telemetry, and material/light helpers. Native
Godot character/rigid-body physics, VFX/audio, and game rules stay gameplay-owned.

### Build and test

| Call | Purpose and successful return | Failure behavior | Example |
| --- | --- | --- | --- |
| `godot.build.project(*, preset, output_path, debug=False, pack_only=False, extra_args=(), allow_external_output=False, timeout=None, dry_run=False)` | Runs named `--export-release`, `--export-debug` or `--export-pack`; returns full committed sibling set and ownership evidence | Missing preset/output, timeout, engine diagnostic, protected/external/unmanaged/tampered output, bad manifest/proof or nested link fails while preserving prior build | E6 |
| `godot.testing.run_automation_tests(test_filter="", *, script="", test_root="res://tests", report_path="", timeout=None, dry_run=False)` | Runs adapter/default or explicit SceneTree runner; returns matched/passed/failed/skipped counts and validated cases | Unsafe runner/report/root, timeout, stale/missing/non-strict report, invalid schema/status or any failed case makes operation fail | E6 |

Native `test_*.gd` files under the selected root must extend a constructible
Godot type and return `bool` or a dictionary with boolean `ok` from `run_test()`.
Truthiness is never used for out-of-contract values. Reports publish from a
private sibling staging path only after schema validation.

### Runtime and sessions

| Call | Purpose and successful return | Failure behavior | Example |
| --- | --- | --- | --- |
| `godot.runtime.launch_editor(*, scene_path="", extra_args=(), dry_run=False)` | Starts configured editor in its own process group; returns managed process ID | Missing project/executable, unsafe scene or launch error fails | E7 |
| `godot.runtime.stop_editor(process_id)` | Stops only an editor launched by this client | Unknown/wrong process ID or bounded termination failure returns error | E7 |
| `godot.runtime.launch_game(*, scene_path="", headless=False, extra_args=(), dry_run=False)` | Starts project/main or selected scene; returns managed process ID | Same ownership/path/launch checks | E7 |
| `godot.runtime.stop_game(process_id)` | Stops only a managed game process group | Unknown/wrong PID or stop failure returns error | E7 |
| `godot.runtime.launch_player(build_path, *, extra_args=(), dry_run=False)` | Launches a regular exported executable/file and returns managed process ID | Linked/special/missing/non-executable build or launch error fails | E7 |
| `godot.runtime.stop_player(process_id)` | Stops only a managed exported player | Unknown/wrong PID or stop failure returns error | E7 |
| `godot.runtime.sessions.join(*, world_id="", participant_id="", user_id="", avatar_artifact_id="", idle_motion_artifact_id="", move_motion_artifact_id="", controller_kind="human", transform=None, parameters=None, require_runtime=False)` | Resolves optional assets, creates/reconnects controller/entity and returns session plus bridge evidence | Bad mapping/JSON/artifact/type, participant bound to different entity, reachable NACK/protocol error, or required unreachable runtime fails without local registration | E7 |
| `godot.runtime.sessions.leave(*, participant_id="", controller_id="")` | Deactivates current binding and returns session/bridge record | Unknown session or reachable NACK fails without false deactivation | E7 |
| `godot.runtime.sessions.heartbeat(controller_id)` | Updates/returns an active local session | Unknown, inactive or offline controller fails | E7 |
| `godot.runtime.sessions.apply_input(controller_id, *, move_x=0.0, move_y=0.0, run=False, jump=False, yaw=0.0, pitch=0.0, seq=0, require_runtime=False)` | Sends finite normalized input and records it only after acceptable bridge outcome | Non-finite values, unknown/inactive controller, reachable NACK/malformed/mismatched response or required unreachable bridge fails without state mutation | E7 |
| `godot.runtime.sessions.snapshot(*, world_id="")` | Returns defensive session list, count and active count, optionally one World | Invalid World conversion/registry state fails | E7 |
| `godot.runtime.sessions.reset_world(*, world_id="")` | Removes only resolved World (`world_001` default) locally/natively; idempotent counts returned | Reachable native rejection fails without local removal | E7 |
| `godot.runtime.sessions.clear_entity(*, participant_id="", controller_id="", entity_id="", destroy_actor=True)` | Removes all bindings for resolved entity; native result distinguishes matched and destroy-queued nodes | Non-boolean flag, unknown entity or reachable rejection fails without removal | E7 |
| `godot.runtime.sessions.probe(timeout=0.25)` | Raw UDP status/reachability/protocol evidence, not the seven-field result envelope | Timeout/unreachable/malformed/mismatched response is explicit in returned record | E7 |

With `require_runtime=False`, no UDP response allows documented local-only state
with a warning. Once any response arrives, NACK or malformed/mismatched protocol
data is fatal and cannot mutate local state. Reconnect preserves entity ID,
replaces the old controller, and emits `session_reconnected`; `session_left`
means control detached, not entity destruction.

### Reflection and observation

| Call | Purpose and successful return | Failure behavior | Example |
| --- | --- | --- | --- |
| `godot.reflection.inspect_artifact(artifact_id, *, live=True, timeout=60.0)` | Returns registry metadata; live mode asks Godot for resource class, PackedScene nodes, skeletons, skins, animations/tracks and load evidence | Unknown artifact, bad registry, timeout, unloadable/corrupt/wrong resource or malformed native report fails | E8 |
| `godot.observe.check_status(*, timeout=5.0, check_runtime=False)` | Reports project/executable/version and optional UDP runtime readiness | Invalid environment or requested runtime failure is explicit; no mutation | E8 |

## 4. Runnable call patterns

### E0 — environment

```python
godot = GodotClient(project_path="/projects/MyGame")
assert godot.api_version == "v1"
environment = godot.get_environment_info(probe_version=True)
assert environment["ok"], environment["errors"]
```

### E1 — create and validate a project

```python
created = godot.project.create(project_name="MyGame", renderer="gl_compatibility")
assert created["ok"], created["errors"]
validated = godot.project.validate(check_engine=True)
assert validated["ok"], validated["errors"]
```

### E2 — resolve, validate, import, query, or register an asset

```python
source = {
    "game_id": "game101",
    "run_id": "run_001",
    "task_kind": "3d_object",
    "task_id": "crate",
    "artifact_key": "glb_path",
}
assert godot.assets.resolve_source(source, asset_type="prop")["ok"]
assert godot.assets.validate(source, "prop")["ok"]
imported = godot.assets.import_prop(source, destination="assets/imported/props")
assert imported["ok"], imported["errors"]
artifact_id = imported["artifacts"][0]["artifact_id"]
assert godot.assets.get_metadata(artifact_id)["ok"]
assert godot.assets.list_registered("prop")["ok"]

existing = godot.assets.register_resource(
    resource_path="res://scenes/arena.tscn",
    asset_type="scene",
    asset_id="arena",
    backend_class="PackedScene",  # assertion, never trusted inference
    spawnable=True,
)
assert existing["ok"], existing["errors"]
```

### E3 — motion and material binding

```python
motion = godot.animation.import_motion(
    source,
    skeleton="Character/Skeleton3D",
    avatar_name="hero",
)
assert motion["ok"], motion["errors"]
assert godot.animation.resolve_skeleton("hero_avatar")["ok"]
assert godot.animation.validate_compatibility(
    motion["artifacts"][0]["artifact_id"], "Character/Skeleton3D"
)["ok"]

bound = godot.bindings.bind_pbr_material(
    asset_id="hero_material",
    source=source,
    mesh_assets=["hero_avatar"],
)
assert bound["ok"], bound["errors"]
```

### E4 — World lifecycle

```python
draft = godot.world.create_draft({
    "draft_id": "arena_draft",
    "world_id": "arena",
    "scene_artifact_id": "arena",
})
assert draft["ok"], draft["errors"]
assert godot.world.validate_draft("arena_draft")["ok"]
assert godot.world.publish_draft("arena_draft")["ok"]
packages = godot.world.list_packages(world_id="arena")
assert packages["ok"], packages["errors"]
```

### E5 — framework and generated add-ons

```python
framework = godot.plugin.install_framework()
assert framework["ok"], framework["errors"]

generated_addon = godot.plugin.install({
    "game_id": "game101",
    "run_id": "run_001",
    "task_kind": "mechanic",
    "task_id": "gameplay_addon",
})
assert generated_addon["ok"], generated_addon["errors"]
assert godot.plugin.list()["ok"]
```

### E6 — native tests and export

```python
tests = godot.testing.run_automation_tests(
    test_root="res://tests",
    report_path="reports/godot-tests.json",
)
assert tests["ok"], tests["errors"]

build = godot.build.project(
    preset="Linux/X11",
    output_path="builds/game.x86_64",
)
assert build["ok"], build["errors"]
```

### E7 — runtime lifecycle and control

```python
launched = godot.runtime.launch_game(headless=False)
assert launched["ok"], launched["errors"]

joined = godot.runtime.sessions.join(
    world_id="arena",
    participant_id="player_1",
    avatar_artifact_id="hero_avatar",
)
assert joined["ok"], joined["errors"]
controller = joined["payload"]["controller_id"]
assert godot.runtime.sessions.apply_input(
    controller, move_y=1.0, run=True, yaw=0.2, seq=1
)["ok"]
assert godot.runtime.sessions.heartbeat(controller)["ok"]
assert godot.runtime.sessions.snapshot(world_id="arena")["ok"]
assert godot.runtime.sessions.leave(controller_id=controller)["ok"]
assert godot.runtime.stop_game(launched["payload"]["process_id"])["ok"]
```

### E8 — inspect and observe

```python
inspection = godot.reflection.inspect_artifact("hero_avatar", live=True)
assert inspection["ok"], inspection["errors"]
status = godot.observe.check_status(check_runtime=True)
assert status["ok"], status["errors"]
```

## 5. Native lifecycle details

### Import

Imports copy only validated task artifacts under `res://assets/imported/`, run
`godot --headless --path <project> --import`, reject non-Godot-4 binaries,
nonzero exits and known corruption/parse/dependency-image errors even at exit
zero, then load the result with Godot. Spawnable mesh types must instantiate as
`PackedScene` with `MeshInstance3D`; avatars additionally need real skeleton
bones and linked skin. Bare OBJ loads as `ArrayMesh`, so use GLB/glTF or wrap it
in a scene.

### Builds and reports

Exports and test reports are staged privately, validated, and atomically
published. Builds record a signed ownership manifest and hashes of the complete
sibling set; the key stays under private adapter state. A later build may replace
only unchanged authenticated members. Project inputs, state keys, altered or
unmanaged outputs, edited manifests, links and special nodes fail closed.

### Examples and proof projects

`engine_adapters/godot/examples/` contains three complete native projects:

- `NeonDodge2D`: arcade survival, input, UI, monitored collisions and state loop;
- `SolarRally3D`: chase-camera racing, physical track, checkpoints, PBR materials,
  directional/omni light, lap/win loop;
- `OrbitPinball2D`: rigid-body pinball, static colliders, animated flippers,
  impulses, combo/lives loop.

Each has a real main `PackedScene`, deterministic unattended driver, manual
keyboard mode, and a Godot smoke script that advances live physics. Their
`mechanic_contract.json` maps to reviewer copies under
`my_code/AAAGameForge/test_data/outputs/gameXXX/godot/`.

## 6. Coordinate system

Godot 3D is right-handed, Y-up, with `-Z` forward. glTF shares Y-up and metre
units. Record source facing/scale explicitly; do not apply a blanket conversion
after Godot has already imported the source format.
