# Importing generated assets into Godot

Godot imports supported source resources when they are placed under `res://`.
The adapter therefore stages a registered 3AGameFactory task artifact inside
the configured project and invokes Godot's import-only editor mode:

```text
godot --headless --path <project> --import
```

Use the public task-identity API from automation:

```python
from engine_adapters.godot import GodotClient

client = GodotClient("/projects/MyGame")
result = client.assets.import_prop(
    {
        "game_id": "demo",
        "run_id": "default",
        "task_kind": "3d_object",
        "task_id": "crate",
        "artifact_key": "glb_path",
    }
)
```

For a one-off filesystem artifact, the compatibility launcher applies the same
transactional staging, native resource checks, and artifact registration, then
writes their JSON report:

```bash
python scripts/import_generated_asset.py --engine godot --src model.glb \
  --godot-project /projects/MyGame
```

`--godot-project`, `A3GAME_GODOT_PROJECT`, and the legacy
`AAAGF_GODOT_PROJECT` fallback accept either the project directory or its
`/projects/MyGame/project.godot` marker. The explicit flag wins; when both
environment variables are set, `A3GAME_GODOT_PROJECT` wins.
Godot executable resolution is `--godot`, `A3GAME_GODOT_EXECUTABLE`,
`A3GAME_GODOT`, legacy `AAAGF_GODOT`, then `PATH`, in that order.

Local buffer/image files referenced by a `.gltf` are preflighted, copied, and
rolled back with the main document. Meshes default to
`res://assets/imported/props`; `--kind motion` defaults to
`res://assets/imported/motions`. Override either with `--godot-dest`.

A zero `--import` exit code alone is not success: both import-error output and a
resource that Godot cannot subsequently load cause rollback. Props must be
instantiable `PackedScene` resources. Motions must additionally contain an
animation, a Skeleton3D, and a bone-targeted track; imported glTF/GLB motion is
reported with its actual `PackedScene` class. Rollback also removes or restores
Godot's adjacent `.import` metadata and matching `.godot/imported` cache files.
The successful resource is registered in the same
`A3GAME_GODOT_ARTIFACT_REGISTRY` used by `GodotClient`, Browser Serving, World,
and Runtime. A registry or report-write failure restores the previous registry
bytes and rolls back the imported resource, sidecars, and cache as one
transaction.

Prefer GLB/glTF for meshes and animation. FBX support depends on the installed
Godot 4 release and importer configuration.
