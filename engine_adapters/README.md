# engine_adapters/

Engine-side reference code — **fed to the LLM as context** when generating
mechanic / UI code, and used at runtime for RPC-style asset delivery.

## Sub-directories

| Directory   | Contents                                                   |
|-------------|------------------------------------------------------------|
| `ue5/`      | UE5 Blueprint templates, C++ modules, Python-remote scripts, importer helpers |
| `unity3d/`  | Unity3D C# templates, Editor scripts, PackageManager manifests |
| `godot/`    | Godot 4 public Client, full GDScript runtime plugin, import/export/test helpers, native gameplay references |
| `blender/`  | Blender Python (`bpy`) importers, rig / retarget helpers, headless render scripts, a playable session |
| `three_js/` | Web runtime: `ThreeClient` Python API, `A3GamePlayable` JS framework, glTF loaders, scene scaffolds, HUD overlays |

`ue5/`, `unity3d/`, `godot/`, and `three_js/` implement the full versioned
Client contract. Each exposes exactly one public Python entry point —
`UEClient`, `UnityClient`, `GodotClient`, or `ThreeClient` — with the same eleven namespaces and the
same `{ok, operation, artifacts, diagnostics, warnings, errors, payload}`
result shape, so Pipeline code can switch engines without branching.

Each also ships an adapter-owned runtime framework that generated
gameplay extends but never edits:

| Adapter | Framework | Generated gameplay lives in |
|---------|-----------|------------------------------|
| `ue5/` | `A3GamePlayable` UE plugin (C++ contracts) | a project-local Gameplay Plugin |
| `unity3d/` | `A3GameRuntime` Unity package (C# contracts) | project-local gameplay scripts and assemblies |
| `godot/` | `A3GamePlayable` Godot addon (GDScript contracts and UDP session bridge) | a project-local addon or game script tree |
| `three_js/` | `A3GamePlayable` npm package `@a3game/playable` | a project-local Gameplay Package under `packages/` |

See `three_js/MIGRATION_INVENTORY.md` for why the three.js framework also
owns renderer, frame loop, input, animation, and collision scaffolding
that Unreal supplies natively.

The LLM is expected to *reference / extend* these files rather than write engine
code from scratch, which improves compile-rate and reduces hallucinated APIs.

## Importing generated assets

Each engine has an `import_generated/` sub-directory: the bridge from what
`models/` produced to something the engine can actually use. It is deliberately
separate from the engine interface functions above — one is "how the engine
does X", the other is "how our artifacts get in".

| Path | Runs where |
|------|-----------|
| `ue5/import_generated/import_mesh.py` | Unreal's Python |
| `unity3d/import_generated/ImportGeneratedMesh.cs` | Unity Editor (`Assets/Editor/`) |
| `godot/` public `GodotClient.assets` API | host Python launches Godot 4 `--headless --import` after safe staging |
| `three_js/import_generated/import_mesh.mjs` | host Node, with the project's `three` installed |
| `blender/import_generated/import_mesh.py` | a `bpy` interpreter (Blender app or the pip wheel) |
| `scripts/import_generated_asset.py` | host Python — finds the editor, launches any importer, reads its JSON report |

```bash
python scripts/import_generated_asset.py --engine both \
    --summary test_data/outputs/<game>/<run>/3d_object_results_summary.json
```

`--engine both` is UE5 + Unity; `--engine all` adds Godot and Blender. The
compatibility launcher accepts the same
`--usage {asset,vfx_standalone,vfx_particle}` tier and writes an engine-specific
JSON report. Godot reports the staged `res://` resource, actual native class,
load/instantiation result, animations and skeleton paths, source size, available
GLB triangle data, warnings, and editor process evidence. See each directory's
README.

Prerequisites: Unity needs `com.unity.cloud.gltfast` in the project; UE needs the
`PythonScriptPlugin` enabled, and is driven through the full editor rather than a
commandlet (the UE README explains why). Blender needs no project or licence, and
`pip install bpy` satisfies it if no application is installed. Install/reuse a
pinned Godot 4 editor with `scripts/engine_install/godot/install.sh --json` or
`install.cmd --json`; it verifies the official SHA-512 and exact engine version.
Godot host code supports Python 3.8+ and does not require Python 3.12. Set
`A3GAME_GODOT_EXECUTABLE` and `A3GAME_GODOT_PROJECT`, or pass the corresponding
CLI options. The executable fallback order is `A3GAME_GODOT`, legacy
`AAAGF_GODOT`, then `PATH`.
Godot material binding also runs an adapter-owned SceneTree script and succeeds
only after bound `PackedScene` files with changed `MeshInstance3D` nodes are
reported and persisted.

## Worlds

A Hunyuan-WorldPlay export is a Gaussian-splat PLY plus loose polygon PLYs, not a
mesh file, and the polygons come out as a triangle soup: no shared vertices,
cracks between parts, mixed winding. `scripts/prepare_world_asset.py` fuses and
repairs it into one continuous `world.glb` that the importers above take like any
other asset. It needs no engine and no Blender.

```bash
python scripts/prepare_world_asset.py --src <export_dir> --out out/world --up z
```

## Walking around one

`blender/runtime/` is a Blender process that stays up and takes JSON commands
over UDP — spawn a character, drive it, trigger effects, render the scene as it
stands. It is for the questions an import report cannot settle: whether a
repaired world is passable, whether the floor is where the spawn point is.

```bash
python -m engine_adapters.blender.runtime.serve --port 30021
python -m engine_adapters.blender.runtime.send_command \
    engine_adapters/blender/runtime/examples/walk_a_generated_world.json
```

The server needs `bpy`; the sender needs nothing, so a session on a render box
can be driven from anywhere.

Per-engine API notes that go straight into the agent's context live separately in
`agent_skills/engine_context/{ue5,unity3d,godot,blender,three_js}_api.md`.
