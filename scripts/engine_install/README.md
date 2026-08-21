# Engine prerequisites for importing generated assets

What a UE5, Unity, or Godot project needs before
`scripts/import_generated_asset.py` can put a generated mesh into it. Nothing
here installs an engine; these are editor/project settings, done once per
project.

> The agent-facing summary of the engine interfaces belongs in
> `agent_skills/engine_context/{ue5,unity3d,godot}_api.md`; this file is the setup
> checklist that goes with the importers in
> `engine_adapters/{ue5,unity3d,godot}/import_generated/`.

## UE5

| Requirement | How |
|---|---|
| **Python Editor Script Plugin** enabled | `Edit ▸ Plugins ▸ Scripting ▸ Python Editor Script Plugin`, or add to the `.uproject`: `"Plugins": [{"Name": "PythonScriptPlugin", "Enabled": true}]` |
| glTF import | Interchange handles `.glb` / `.gltf` in UE 5.x with no extra plugin. If an import yields nothing, check that the Interchange plugins are enabled |
| Editor binary | `UnrealEditor.exe` (not `-Cmd`). The launcher finds it under `C:\Program Files\Epic Games\UE_*` or a bare `<drive>:\UE_*`; override with `--ue-editor` or `$AAAGF_UE_EDITOR` |

Without touching the `.uproject`, the plugin can be enabled for one run:

```bash
python scripts/import_generated_asset.py --src <glb> --engine ue5 \
    --uproject <project>.uproject \
    --ue-extra=-EnablePlugins=PythonScriptPlugin
```

Verified against UE 5.7. See `engine_adapters/ue5/import_generated/README.md`
for why the importer drives the full editor rather than a commandlet.

## Unity

| Requirement | How |
|---|---|
| **glTFast** | `Window ▸ Package Manager ▸ + ▸ Add package by name ▸ com.unity.cloud.gltfast`, or add `"com.unity.cloud.gltfast": "6.16.0"` to `Packages/manifest.json`. Unity has no built-in glTF importer |
| Editor script | `ImportGeneratedMesh.cs` must sit in a folder named `Editor`. The launcher copies it to `<project>/Assets/Editor/` automatically; `--no-install-editor-script` turns that off |
| Editor binary | Found under `C:\Program Files\Unity\Hub\Editor\<version>\Editor\Unity.exe`; override with `--unity` or `$AAAGF_UNITY` |

FBX and OBJ need nothing installed. If glTFast cannot be added to a project,
generate FBX instead: `MeshyModel(output_format="fbx")`.

Verified against Unity 6000.5.2f1 with glTFast 6.16.0, built-in render pipeline.
URP and HDRP material conversion is untested.

## Godot 4

| Requirement | How |
|---|---|
| Engine install | Run `scripts/engine_install/godot/install.sh --json` or `install.cmd --json`; the pinned official archive is SHA-512 verified, atomically installed/reused, version-probed, and emitted as PATH/config output |
| Project | Use a directory containing `project.godot`; after engine validation, create a minimal project with `scripts/engine_install/godot/create_project.sh` or `.cmd` |
| Editor binary | Set `A3GAME_GODOT_EXECUTABLE`; `A3GAME_GODOT` and legacy `AAAGF_GODOT` are fallbacks, followed by `godot4`, `godot`, or `godot-mono` on `PATH` |
| Import | Godot's built-in glTF/GLB importer needs no addon; the adapter stages the file under `res://` and runs `godot --headless --path <project> --import` |
| Python | Python 3.8+ standard library; the adapter does not require Python 3.12 or an engine SDK package |

Use GLB when practical. A `.gltf` file may reference sidecar buffers and images;
the public `GodotClient.assets` path and compatibility launcher validate and
stage those sidecars together. Successful compatibility imports also write the
project's Godot artifact registry, so they are immediately visible through
`GodotClient`, World, and Runtime; registry failure rolls the
filesystem import back.
See `scripts/engine_install/godot/README.md` and
`engine_adapters/godot/import_generated/README.md`.

## Both

**Close the project in its editor before running an import.** The importer
launches its own editor process; an editor that is already open keeps showing
its own view of the asset browser, and the import reads as having silently done
nothing.

## Checking the setup without an engine

```bash
# validates the artifact and prints the exact engine command, launching nothing
python scripts/import_generated_asset.py --src <glb> --engine both --dry-run
```
