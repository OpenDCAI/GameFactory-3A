# Blender (`bpy`) — engine context

API notes for an agent writing Blender Python. Blender is not one of the target
engines a game ships on; it is the **neutral step between a generator and an
engine** — the only place in this repo that reads `.ply` and `.usd`, fixes a
pivot or a scale the generator got wrong, and renders a picture of the result
without a project, a licence or a GPU.

`blender.playtest.*` is not a benchmark: it records what happened when the
game was played and makes no pass/fail claim of its own. See **Playtest**.

Reference implementations to extend rather than rewrite:
`<REPO_PATH>/engine_adapters/blender/`.

---

## 1. Where the code runs

Every script here executes **inside a `bpy` interpreter**, which is one of two
things and the code must not care which:

| | How it is launched | Notes |
|---|---|---|
| Blender application | `blender --background --factory-startup --python x.py -- ...` | Full app, headless |
| pip wheel | `pip install bpy` then `python x.py ...` | Same API; no GUI code paths, no ffmpeg, Cycles as an add-on |

The wheel pins the interpreter hard: `bpy` 5.0 and 4.x need **Python 3.11**, 3.6
needs 3.10, and there is no build for anything else. `pip install bpy` on 3.9
reports "no matching distribution" rather than anything about versions.

Three consequences you must write for:

**Argument parsing.** `blender --python x.py -- --src a` hands the *entire*
command line to the script; everything before the bare `--` is Blender's. Under
the pip wheel there is no separator. Handle both:

```python
def _script_argv() -> list:
    import sys
    if "--" in sys.argv:
        return sys.argv[sys.argv.index("--") + 1:]
    return [a for a in sys.argv[1:] if a != Path(__file__).name]
```

**Exit codes lie.** `blender --background --python x.py` exits `0` no matter what
the script returned or raised. A caller checking the return code sees success
after a total failure. Raise `SystemExit(code)` explicitly at the end — in this
repo, gated on `AAAGF_BLENDER_EXIT_ON_DONE` so an interactive run does not kill
the app.

**Long or quoted arguments are unreliable** across the `--` boundary on Windows.
Pass a **job file** instead, named by an environment variable (`AAAGF_IMPORT_JOB`
here) and read at startup. This is the same channel the UE5 importer uses, so the
host-side launcher builds one job shape for every engine.

**Defer the `bpy` import.** Put it behind a function so the module's constants
are readable by a host-side launcher and by `<REPO_PATH>/test/` with no Blender installed:

```python
def _bpy():
    try:
        import bpy
    except ImportError as e:
        raise RuntimeError("this module must run inside a bpy interpreter ...") from e
    return bpy
```

**Make every output path absolute.** This is the one that will cost you an hour.
Blender resolves a relative output path against the blend file, and a headless
run has no blend file. The glTF exporter happens to fall back to the working
directory, but `bpy.ops.render.render()` does not: it writes **nothing**, returns
`{'FINISHED'}`, logs no error, and the file is simply absent. Call `.resolve()`
on the destination, and confirm `path.is_file()` before putting it in a report.

**The pip wheel is not the application.** It has no FFMPEG writer at all — the
`file_format` enum lacks the entry — so any video path must degrade to a PNG
sequence rather than assume Blender "bundles ffmpeg". It also ships Cycles as an
add-on rather than a built-in; see §5.

---

## 2. The data model

`bpy.data` is the file's contents; `bpy.context` is what is selected and active;
`bpy.ops` is the operator layer, which acts on the context.

```python
bpy.data.objects["Cube"]          # an object: a transform + a link to data
bpy.data.objects["Cube"].data     # the mesh itself — vertices live here
bpy.data.meshes, .materials, .images, .actions, .armatures
```

An **object** and its **data** are different things, and confusing them is the
single most common source of exported assets that do not match what was on
screen. `obj.location = ...` moves the object; `obj.data.vertices[i].co += ...`
moves the mesh. Exporters bake object transforms, so a re-pivot done at object
level disappears on export. Move the vertices:

```python
for vertex in obj.data.vertices:
    vertex.co += delta          # survives export
```

Same reason `bpy.ops.object.transform_apply(scale=True)` follows a rescale.

### Operators need a context

`bpy.ops.object.join()` joins the *selected* objects into the *active* one. There
are no arguments for "which objects" — you set the state first:

```python
for other in bpy.data.objects:
    other.select_set(False)
for obj in group:
    obj.select_set(True)
bpy.context.view_layer.objects.active = group[0]
bpy.ops.object.join()
```

Operators raise `RuntimeError` when their poll fails (wrong mode, nothing active,
add-on disabled). Catch it and record it; do not let a failed decimation abort an
import that otherwise worked.

### Counting triangles

`len(mesh.polygons)` undercounts a quad mesh by half and ignores modifiers. Ask
the evaluated mesh for its loop triangles — this is the number the engine will
see:

```python
depsgraph = bpy.context.evaluated_depsgraph_get()
evaluated = obj.evaluated_get(depsgraph)
mesh = evaluated.to_mesh()
try:
    mesh.calc_loop_triangles()
    count = len(mesh.loop_triangles)
finally:
    evaluated.to_mesh_clear()   # leaks memory across a batch otherwise
```

---

## 3. Operator names moved in 4.0

Blender 4.x replaced the Python OBJ / PLY / STL importers with C++ ones under
different names and kept the old ones around for a while. Which exists depends on
the build, so probe both rather than hardcoding either:

| Format | 4.x | pre-4.0 |
|--------|-----|---------|
| `.obj` | `wm.obj_import` | `import_scene.obj` |
| `.ply` | `wm.ply_import` | `import_mesh.ply` |
| `.stl` | `wm.stl_import` | `import_mesh.stl` |
| `.glb` / `.gltf` | `import_scene.gltf` | same |
| `.fbx` | `import_scene.fbx` | same |
| `.usd*` | `wm.usd_import` | same |
| `.abc` | `wm.alembic_import` | same |

```python
def _resolve_operator(bpy, dotted: str):
    group, _, operator = dotted.partition(".")
    namespace = getattr(bpy.ops, group, None)
    if namespace is None or not hasattr(namespace, operator):
        return None
    return getattr(namespace, operator)
```

`getattr(bpy.ops.wm, "ply_import")` on a build without it raises `AttributeError`
at call time, not at lookup, so check with `hasattr` and fail with a message
naming both candidates. On 5.0 the whole legacy `bpy.ops.import_mesh` namespace
is empty, so probing is not hypothetical.

Render-engine ids moved too, twice: EEVEE is `BLENDER_EEVEE` before 4.2,
`BLENDER_EEVEE_NEXT` in 4.2–4.4, and `BLENDER_EEVEE` again from 4.5. List both
and try assigning each, catching `TypeError` — do **not** decide by reading the
enum, for the reason in §5.

## 3a. Animation moved in 4.4

`action.fcurves` is gone; actions are slotted, and the curves live under
layers → strips → channelbags. Inserting keyframes is unchanged, so only code
that walks the curves afterwards breaks:

```python
def _action_fcurves(action) -> list:
    if hasattr(action, "fcurves"):          # pre-4.4
        return list(action.fcurves)
    return [fc
            for layer in getattr(action, "layers", ())
            for strip in getattr(layer, "strips", ())
            for bag in getattr(strip, "channelbags", ())
            for fc in bag.fcurves]
```

`World.use_nodes` is deprecated in 5.0 and slated for removal in 6.0; a new world
already has its shader tree. Guard the assignment with `if world.node_tree is
None:` rather than setting it unconditionally.

---

## 4. Units and axes

| | Unit | Up | Forward |
|---|---|---|---|
| Blender | metre | **Z** | −Y |
| glTF / GLB | metre | **Y** | +Z |
| UE5 | **centimetre** | Z | +X |
| Unity | metre | Y | +Z |

The glTF importer and exporter convert in both directions, so `glb → Blender →
glb` is **identity**. That is precisely what makes Blender safe to insert between
a generator and UE5. For FBX, set the axes explicitly:

```python
bpy.ops.export_scene.fbx(filepath=..., use_selection=True,
                         apply_scale_options="FBX_SCALE_ALL",
                         axis_forward="-Z", axis_up="Y")
```

The `.ply` importer applies **no** conversion, because a PLY carries no axis
metadata to convert from. A Z-up world export must be rotated once, up front, by
`scripts/prepare_world_asset.py --up z` — not repeatedly by whoever opens it.

Because Blender is Z-up, `"bottom"` pivot means **min Z**, and
`--normalize-scale` targets **1.0** (the UE5 importer says 100 for the same
thing, being in centimetres).

---

## 5. Headless rendering

The pip `bpy` wheel drives EEVEE and Workbench through a GL/EGL context. A
machine without a display has none, and the failure is a `libEGL` **abort** —
the process dies, there is no exception to catch. So:

- Default to **Cycles on CPU** (`scene.render.engine = "CYCLES"`,
  `scene.cycles.device = "CPU"`). Slow, but it always works.
- Enable it defensively: `if "cycles" not in bpy.context.preferences.addons:
  bpy.ops.preferences.addon_enable(module="cycles")`.
- **`CYCLES` is not in the `engine` enum**, because add-on engines register as
  `RenderEngine` subclasses rather than as static enum members. Under the 5.0
  wheel the enum reads `['BLENDER_EEVEE']` and yet `scene.render.engine =
  "CYCLES"` succeeds and `scene.cycles.device` exists. Decide availability by
  *assigning* and catching `TypeError`; reading the enum will tell you Cycles is
  missing when it is not.
- **Grease-pencil objects render through GL even under Cycles** and take the
  process down uncatchably. Hide them from the render before starting and restore
  them after — `render_preview.hide_gl_only_objects`, shared with the runtime,
  because two copies of this rule means one of them is eventually wrong.
- `mp4` needs an FFMPEG writer, which the application bundles and the pip wheel
  does not. Setting `file_format = "FFMPEG"` under the wheel raises, because the
  enum has no such member. Catch it and write a PNG sequence.
- A render to a relative path writes nothing and reports success — see §1.

Cameras look down their **−Z** axis:

```python
direction = target - camera.location
camera.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
```

`mathutils` only exists inside `bpy`, so import it inside the function too.

---

## 6. Rigs and retargeting

Retarget by **world-space rotation delta**, not by copying local rotations:

```
delta_ws  = src_pose_ws @ src_rest_ws⁻¹
target_ws = delta_ws @ dst_rest_ws
```

Local-frame retargeting inherits the destination bone's roll and produces arm
drift and backward knees. Transferring world rotations makes the result
independent of it, and makes the source interchangeable — a Mixamo FBX and a
MoMask BVH both work, the importer picked by extension and the root bones read
from a mapping JSON.

Practical points when building an armature from joints and skin weights:

- Create bones in **Edit mode** (`armature.edit_bones`), pose them in **Pose
  mode**. `edit_bones` is invalid outside Edit mode and reading it there throws.
- A zero-length bone is silently discarded on leaving Edit mode. Give leaf joints
  a small tail offset.
- Skin weights go into vertex groups named exactly after the bones; the Armature
  modifier matches by name, and a typo means a limb that does not move rather
  than an error.
- Bake to keyframes before exporting; constraint-driven poses do not survive FBX.

`<REPO_PATH>/operators/gen_motion/funcs/retarget_utils/` (via `retarget_motion.py`)
implements Puppeteer rig import, auto bone mapping, and world-delta retarget.
Validate the resulting FBX with
`<REPO_PATH>/engine_adapters/blender/import_generated/import_motion.py` (`--kind motion`).

---

## 7. Reporting is the contract

Never make a caller scrape the log. Every entry point returns and writes a dict:

```json
{
  "ok": true,
  "object": "Sword_001",
  "tris": 24418, "vertices": 12907, "source_tris": 24418,
  "bounds": {"min": [...], "max": [...]},
  "dimensions": [0.31, 0.08, 1.04],
  "materials": ["Material_0"],
  "exports": {"glb": "/out/library/Sword_001.glb"},
  "preview": "/out/library/Sword_001_preview.png",
  "warnings": []
}
```

Rules that keep this useful:

- Every degraded path appends to `warnings` and continues — a missing preview is
  not a failed import.
- Every lossy operation warns even when it succeeded. Decimation always warns,
  because generating low-poly (`TripoModel(low_poly=True)`) keeps UVs and normals
  and decimating afterwards does not.
- Record what the caller measured (`source_tris`) alongside what Blender measured
  (`tris`), so conditioning that changed the mesh is visible rather than inferred.
- In a batch, one bad asset lands an error in its own entry and the rest continue.

---

## 8. Usage tiers

Shared verbatim with the UE5 and Unity importers, so one `--usage` means the same
thing whichever engine receives the asset:

| `--usage` | For | Triangles | Pivot | Scale |
|-----------|-----|-----------|-------|-------|
| `asset` (default) | props, weapons, characters | untouched | untouched | untouched |
| `vfx_standalone` | one mesh, no particles | untouched | re-centred | optional |
| `vfx_particle` | a mesh instanced by a particle system | budget = per-mesh tris × instances | re-centred | normalized to 1 m |

The default conditions nothing. Do not silently "improve" a generated asset.

---

## 9. Worlds

A Hunyuan-WorldPlay export is not a mesh file. It is a Gaussian-splat PLY (the
visual) plus one or more polygon PLYs (the collider), and the polygons arrive as
a **triangle soup**: no shared vertices, cracks between parts, mixed winding.
Importing the raw PLY into Blender works and is useful for looking at what the
generator produced, but it is not an asset.

Run `<REPO_PATH>/scripts/prepare_world_asset.py` first — it fuses the parts and repairs the
surface into one continuous `world.glb`, needing neither Blender nor an engine —
then import that like anything else. The repair itself lives in
`<REPO_PATH>/models/common/mesh_repair.py`; `<REPO_PATH>/models/README.md` explains what each stage does
and why hole-filling has to distinguish a hole from an open perimeter.

---

## 10. A live session

`<REPO_PATH>/engine_adapters/blender/runtime/` is the one part of this adapter that is not
batch: a Blender process that stays up and takes JSON commands over UDP, so a
world can be walked around before an engine is involved. Reach for it when the
question is about *behaviour in space* — is that doorway passable, is the floor
where the character spawns — which no import report can answer.

**The rule that matters: `bpy` is not thread-safe.** The socket thread must
never touch Blender. Parse the datagram, put it on a queue, and let the main
loop drain it — that is the only place a command may reach `bpy.ops`. In this
repo the receiver does not even import `bpy`, so the boundary is structural
rather than a convention someone has to remember.

```python
def _enqueue(self, command, payload):   # receiver thread: queue only
    self._pending.put((command, payload))

def drain_pending(self, max_ops=32):    # main thread: bpy lives here
    ...
```

Cap the drain. A burst of imports can each take seconds, and an uncapped drain
stalls the tick. Measure elapsed time per tick rather than assuming the nominal
step, and clamp it: a tick that spent a second importing a world will teleport
every character mid-jump if you integrate it as one.

Three findings measured against the 5.0.1 wheel, each of which looks like a bug
in your own code until you check:

- **A Mantaflow domain plus a flow object breaks Blender's exit.** Teardown
  faults *after* all work is done, so a successful run reports a crashed exit
  code. Deleting the objects does not undo it, nor does removing the modifiers;
  `bpy.ops.wm.read_factory_settings(use_empty=True)` does. Wipe the file before
  the interpreter closes if a fluid ever existed.
- **`os._exit` is not a safe way to skip a messy teardown.** With `bpy` loaded
  it faults on Windows, so it *creates* the crash it was meant to avoid. Exit
  normally and remove whatever teardown cannot handle.
- **Grease pencil changed generation in 4.3.** Strokes moved off the frame
  (`frame.strokes.new()`, points with `.co`) and onto a drawing the frame owns
  (`frame.drawing.add_strokes([n, ...])`, points with `.position`); 5.0 dropped
  the old API. Detect with `hasattr(frame, "drawing")` rather than by version.

Command payloads are hand-written as often as generated: take **degrees** for
rotations and **metres** for distances, default every key that sensibly can be,
and make spawn commands idempotent by id — UDP has no delivery guarantee, and a
sender that retries must not end up with two characters.

---

## 11. Playtest

- `blender.playtest.record` - Drives a `game.py` through discovered actions
  and writes `frames/`, `video.mp4`, and `report.json`.

This answers a question the game's own `--no-render` run cannot: *does the
game play*. That run is driven by the unattended policy; a playtest presses
the keys the game itself declares it listens for and records what happened.
Use it to confirm a generated mechanic works, and to produce a clip a human
can watch. It is evidence, not an authoritative benchmark — `checks` in a
later evaluation report say the recording is intact, and `game_state` is
what says the game responded.

```bash
python -m pipeline.code_gen.playtest.run \
    --engine blender \
    --project test_data/outputs/<game>/<run>/mechanic/<task> \
    --duration 10 --fps 20 \
    --no-render

python -m pipeline.code_gen.playtest.eval --report <out_dir>/report.json
```

`run.py` records and scores nothing; `eval.py` reads a written report and
records nothing. `--no-render` skips Cycles; drop it for `video.mp4`.
The adapter `blender.playtest.record` is still callable directly.

### This Is Not Screen Recording

There is no display on these machines, so EEVEE is not an option: it needs
a GL/EGL context and fails with a `libEGL` abort rather than an exception.
The simulation is driven at a **fixed timestep** (that is what `Game.run`
already does), one tick is one baked frame, and Cycles then renders the
baked range. The video is smooth at the target frame rate however long each
frame actually took.

`--play` is a different question: it needs a real window. A playtest does
not open one. It fills `controls.ScriptedSource` and runs the same loop
`--replay-input` uses.

### Constraints That Are Not Negotiable

Each was found by failing without it. `playtest/record.py` carries them.

1. **Input is real `Controls`.** Keys go through `ScriptedSource` /
   `from_held`, which is the table a keyboard session dispatches on.
   **Nothing reaches in and moves an actor**, because a recording that
   positioned things directly would be evidence of tweening, not of a
   playable game. `report.json` `game_state` is what makes the distinction
   checkable — a real take shows `shots_fired` / `kills` moving, or a car
   leaving the start line.
2. **The clock is the tick.** One captured frame is one simulation step.
   Encoding at that same rate keeps the video honest however long Cycles
   took. Reading the wall clock to advance the sim is how a slow render
   teleports everything.
3. **Exit codes lie.** `blender --background --python x.py` exits 0
   whatever the script did. The report on disk is the result.
   `AAAGF_BLENDER_EXIT_ON_DONE` makes the shell see it too.
4. **Output paths are absolute.** Blender resolves a relative render path
   against the blend file, and a headless run has none: the render then
   writes nothing, returns `FINISHED`, and looks like a success.
5. **The pip wheel has no FFMPEG writer.** A video path must degrade to a
   PNG sequence rather than assume Blender "bundles ffmpeg". JPEG `frames/`
   are extracted from the MP4 when `ffmpeg` is on PATH.

**Change the recording, never the game, to make a video look right.** A
first-person take that stares at a wall needs a look sweep in the recorder,
not a `player.yaw =` in `tick()`.

### Actions Are Discovered, Not Configured

A recorder that had to be told what to press would need a new script per
game. Instead the running game is asked, in this order, and
`report.action_source` says which answered:

| Source | What it yields |
|---|---|
| `Game.playtest_actions` | A plan the game declares for itself |
| `AXIS_BINDINGS` / `BUTTON_BINDINGS` for `genre` | The tables `from_held` dispatches on |
| built-in fallback | WASD + Space + click, so an unannotated game still records |

Two rules the recorder applies, because getting either wrong records a
game that appears not to respond:

- **Movement is held; a verb is tapped.** One frame of `forward` moves a
  character by centimetres. Conversely a discrete verb must be released —
  a semi-automatic weapon will not fire twice without it.
- **Duplicate actions are dropped.** Bindings list `W` *and* `UP_ARROW`
  for forward; pressing each in turn would record the same thing twice.

A game with a specific story to tell should declare `playtest_actions` on
the `Game` subclass — `{id, keys?, taps?, mouse?, duration?}` — which is
strictly better than discovery because only the game knows where its
enemies are. Discovery is the floor, not the ceiling.

### A Silent Early Return Will Waste A Whole Take

Anything that degrades silently needs a path that makes it loud. The
recorder reports `executed_actions[].ok` and `frames` per action, and
fails the run when nothing was captured. A custom plan should do the same.

**Do a `--no-render` test take before a real one.** It costs seconds and
catches an empty `game_state`, which otherwise costs the whole Cycles
recording.

### Cost

640x360 at 20 fps on Cycles CPU, review samples (8 spp):

| Game | Per frame (order of) | 10 s of video |
|---|---|---|
| First-person arena | ~1 s | ~few min |
| Racing / forest (long view, more geo) | longer | longer |

Default `timeout` is therefore 900 s. `--no-render` is the path that
belongs in a tight loop.

---

## 12. Quick reference

```python
# Empty the file. --factory-startup still ships the default cube.
bpy.ops.wm.read_factory_settings(use_empty=True)

# World-space bounding box of an object.
corners = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]

# Object dimensions, after transforms.
obj.dimensions            # a Vector, in metres

# Materials actually assigned.
[slot.material.name for slot in obj.material_slots if slot.material]

# Apply a modifier by name (raises RuntimeError when the poll fails).
bpy.context.view_layer.objects.active = obj
bpy.ops.object.modifier_apply(modifier="AAAGF_Decimate")

# Export the selection only.
bpy.ops.export_scene.gltf(filepath=p, export_format="GLB", use_selection=True)
```
