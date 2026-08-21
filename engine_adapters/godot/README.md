# Godot adapter

`engine_adapters.godot` is the versioned Godot 4 adapter. Its only public
Python entry point is:

```python
from engine_adapters.godot import GodotClient

godot = GodotClient(
    project_path="/projects/MyGame",
    godot_executable="/opt/godot/godot",
)
```

It exposes the same eleven namespaces as the other full adapters: `project`,
`assets`, `animation`, `bindings`, `world`, `plugin`, `build`, `testing`,
`runtime`, `reflection`, and `observe`. Every operation returns the shared
`{ok, operation, artifacts, diagnostics, warnings, errors, payload}` shape.

Install or reuse a pinned official editor before constructing the client:

```bash
scripts/engine_install/godot/install.sh --version 4.5.1 --json
```

The cross-platform installer is non-interactive, architecture-aware, verifies
the official SHA-512, stages atomically, probes the exact version, and emits
PATH/configuration output. It and this adapter support Python 3.8+; Python 3.12
is not required. See `scripts/engine_install/godot/README.md`.

## Configuration

- `A3GAME_GODOT_PROJECT`: project directory or `project.godot`; the legacy
  `AAAGF_GODOT_PROJECT` is used only when this variable is unset.
- `A3GAME_GODOT_EXECUTABLE`: preferred Godot 4 editor executable;
  `A3GAME_GODOT`, then legacy `AAAGF_GODOT`, are fallbacks. With none set,
  `godot4`, `godot`, and `godot-mono` are discovered on `PATH`.
- `A3GAME_GODOT_RUNTIME_HOST` / `A3GAME_GODOT_RUNTIME_PORT`: native runtime UDP
  bridge; defaults to `127.0.0.1:30050`.
- `A3GAME_GODOT_EDITOR_TIMEOUT` / `A3GAME_GODOT_IMPORT_TIMEOUT`: bounded editor
  subprocess timeouts in seconds.
- `A3GAME_GODOT_DATA_ROOT`: private, persistent adapter state (registries,
  bindings, reports, and the export ownership key); defaults to
  `<project>/.a3game`.
- `A3GAME_GODOT_ARTIFACT_REGISTRY`: optional artifact-registry file override.
  It must be an ordinary strict-JSON file, not a symbolic link or special node;
  non-standard `NaN` and infinity constants are rejected on reads and writes.
- `A3GAME_GODOT_WORLD_REGISTRY_ROOT`: optional World draft/package directory
  override; defaults to `<data-root>/worlds`.

Every adapter-managed state path is checked component by component. The data
root, `worlds`/`drafts`/`packages`, `bindings`, `reports`, and `build`
hierarchies must contain only ordinary directories, while managed leaves must
be regular files. A symbolic link or special node at any level fails before an
external file can be read, replaced, or used for rollback.

## Actual engine path

Project validation resolves `res://` and `uid://` main scenes and, when engine
checks are enabled, requires Godot itself to load and instantiate the resolved
resource as a `PackedScene`. With engine checks disabled, text scenes still
need a `[gd_scene]` header; binary UID resolution requires Godot.

Asset import copies a registered task artifact under `res://assets/imported/`,
then runs the documented `godot --headless --path <project> --import` lifecycle.
The adapter requires a Godot 4.x executable and rejects import/resource errors,
including corruption, parse, and dependency-image decoding diagnostics, even
when `--import` exits zero. It then asks Godot to load the resulting `res://`
resource. Spawnable resources must load as an instantiable `PackedScene`;
props, weapons, static meshes, and avatars must contain a `MeshInstance3D`, and
avatars must also expose a skinned mesh and Skeleton3D bones.
Motion scenes retain their real `PackedScene` class and must expose an animation,
a Skeleton3D, and a bone-targeted track. An artifact enters the registry only
after all of those native checks succeed.
Existing project resources can be added with `assets.register_resource()`. The
operation accepts only a canonical, in-project `res://` file, requires Godot 4
to load it and satisfy the selected asset type, derives its native class and
spawnability from that inspection, and returns the same structured operation
result as every other public API. The raw registry and adapter-owned transaction
write path are private implementation details.
Bare `.obj` files are rejected for spawnable types because Godot 4 loads them
as `ArrayMesh`; convert them to GLB/glTF or wrap them in a Godot scene first.
glTF main files and local sidecars are preflighted and committed as one
transaction, so the default no-replace mode cannot overwrite an existing
buffer or texture. Failed validation restores replaced sources, their adjacent
Godot `.import` metadata, and matching `.godot/imported` cache files; it removes
newly generated counterparts and does not update the artifact registry.

`bindings.bind_pbr_material()` creates/imports a material, invokes Godot to
apply it to every `MeshInstance3D`, saves bound `PackedScene` resources under
`res://assets/imported/material_bindings/`, and retargets the registered mesh
artifacts atomically. A manifest is retained for audit, while the bound scenes
are the actual resources used by later runtime operations.

Builds require a named preset in `export_presets.cfg` and run
`--export-release`, `--export-debug`, or `--export-pack`; a zero exit code with
no requested output is a failure. Every export is written to an isolated
staging directory first, so a failed Web export cannot overwrite or leave
partial `.html`, `.wasm`, `.pck`, `.js`, or image companions in an existing
build. The adapter records content proofs for the committed sibling set in a
signed ownership manifest; its signing key lives under the configured adapter
data root (`A3GAME_GODOT_DATA_ROOT`, or `<project>/.a3game` by default). A later
build may replace surviving members only when they are unchanged and the
managed set is authenticated. A missing recorded member may be regenerated
from a newly staged build when the signed manifest and every surviving member
remain valid. Editing the manifest, replacing a surviving recorded output, or
encountering an existing output or companion without valid ownership fails
closed; paths that alias
`project.godot`, `export_presets.cfg`, or the ownership key are also rejected.
Directory exports are inspected recursively before commit and may not contain
symbolic links, including links to project inputs, adapter state, or external
mutable content.
Keep the adapter data root private and persistent, and choose a new output path
instead of pointing a build at project source or an externally managed artifact.

Native test reports are likewise written to a private sibling staging path,
schema-validated, and atomically published. Report paths that contain symlinks,
use special filesystem nodes, or alias `project.godot`, the selected runner, or
a discovered native test script are rejected before Godot starts. Native
`run_test()` results must be a boolean or a dictionary with a boolean `ok`;
other values fail explicitly instead of being coerced by truthiness.

World drafts accept only ready, spawnable `scene` records whose registered
`res://` path matches a real Godot scene/importable resource and whose backend
class is `PackedScene`. Godot also loads and instantiates the native resource;
draft creation, validation, and publication recheck that contract. Persistent
drafts and packages use strict versioned schemas. Unknown versions, missing or
mistyped fields, illegal lifecycle states, non-matching record/file IDs, and
malformed JSON fail the public operation; package listing never silently skips
or synthesizes an artifact from a damaged record.
`world.create_draft(spec, *, draft_id="", project_id="", metadata=None)` matches
the other full adapters: non-empty explicit IDs override values in `spec`, and
explicit metadata is merged over spec metadata. `world.list_packages(*,
project_id="", world_id="")` applies both optional filters.

`plugin.install_framework()` installs `A3GamePlayable` as a project add-on. Its
autoload receives game-neutral session and normalized input messages. The add-on
also implements identity/entity binding, scene loading, animation dispatch,
collision probes, telemetry HUD, and PBR/light helpers, with a capability matrix
and native smoke test. Generated gameplay owns concrete movement, combat, camera,
game-specific UI, vehicles, score, and rules. Custom add-ons
must declare the Godot-required `name`, `author`, `version`, `description`, and
`script` strings in `plugin.cfg`, with one safe, source-local script path. Before
copying or enabling an add-on, the configured Godot executable parses that
descriptor and loads the entry script in an isolated project, verifying that it
is an instantiable, `@tool` `EditorPlugin`.
Missing, traversing, or invalid entry scripts leave both `addons/` and
`project.godot` unchanged.

Runtime joins use `world_001` when no World is supplied. `reset_world()` clears
only the requested World (or `world_001` by default) from both Python and native
session state and preserves unrelated Worlds. Rejoining an existing participant
reuses its persistent entity, replaces its native controller binding, emits
`session_reconnected(previous_session, session)` without a synthetic
`session_left` / `session_joined` pair, and leaves the old Python controller
offline for audit. `session_joined` is reserved for requesting an entity only
when its ID is absent from the scene tree; gameplay can retrieve a retained node
with `A3GameRuntime.find_entity(entity_id)`. `session_left` detaches control and
must not be treated as entity destruction. `snapshot().payload.active_count`
therefore reports active bindings separately from the total audit-record count.
`clear_entity()` removes all controller records for the matching entity;
`destroy_actor=False` retains the Godot node, while the default `True` calls its
`clear_a3game_entity()` hook. Native acknowledgements report removed-session and
matched/destroy-queued node counts. `leave()` by participant resolves the current
active controller and deactivates it; later input and heartbeats fail until the
participant explicitly joins again. When no UDP
response arrives, operations may use documented local-only fallback with a
warning (unless `require_runtime=True`). Once a response is received, a NACK or
malformed/mismatched protocol response fails without registering, deactivating,
removing, or updating local session state.

Three complete, engine-validated gameplay references live under `examples/`:
2D arcade survival, 3D chase-camera racing, and 2D rigid-body pinball. Each is a
standalone project with interactive input, an unattended demo driver, UI/state,
meaningful collision/physics, and a native dynamic smoke script.

Use `python -m engine_adapters.godot --help` for the CLI and
`scripts/engine_install/godot/` for verified installation and launch wrappers.
