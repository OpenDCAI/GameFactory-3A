# A3GamePlayable for Godot

This adapter-owned Godot 4 add-on supplies the reusable engine layer generated
games need: identity, normalized input, runtime sessions, entity binding, safe
scene instantiation, animation dispatch, collision queries, telemetry HUD, and
PBR/light helpers. Concrete movement, combat, vehicles, camera behavior, score
rules, and game-specific UI stay in generated gameplay.

Install it through the public API:

```python
from engine_adapters.godot import GodotClient

result = GodotClient(project_path="/projects/MyGame").plugin.install_framework()
assert result["ok"], result["errors"]
```

The installer validates `plugin.cfg` and the `@tool EditorPlugin` entry in an
isolated Godot project, copies only a regular non-linked tree, enables the add-on,
and registers `A3GameRuntime` as an autoload. Existing files/settings are kept
unless `replace_existing=True`; any validation or commit failure rolls back both
the add-on and `project.godot`.

## Capability matrix

This matrix was derived from the repository's UE, Unity, and Three.js runtime
plugins. “Native” means Godot already provides the capability directly and a
second adapter abstraction would only weaken its contract.

| Cross-engine capability | Godot implementation | Contract and failure behavior |
| --- | --- | --- |
| Identity component | `A3GameIdentity`, `A3GameRuntimeEntity` | Requires non-empty World/entity IDs; snapshots return stable identity and last-input state |
| Normalized input | `A3GameInputState.normalize()` | Type-checks booleans/sequence, rejects non-finite numerics, clamps movement and pitch; invalid packets are NACKed before state mutation |
| Runtime/session subsystem | `A3GameRuntime` autoload | UDP join/reconnect/leave/input/reset/clear, per-World snapshots, last-input lookup, explicit unsupported-operation errors |
| Controllable entity | `A3GameRuntimeEntity` | Group registration, identity configuration, normalized-input signal, rejected-input signal, snapshot and explicit clear hook |
| Scene loader | `A3GameSceneLoader.instantiate_scene()` | Only non-traversing `res://` `PackedScene` paths; missing/wrong resources return `{ok=false}` without attaching a node |
| Animation director | `A3GameAnimationDirector` | Finds an `AnimationPlayer`; missing clips, invalid blend, or zero/non-finite speed fail explicitly |
| Collision probe | `A3GameCollisionProbe` | Ray and sphere-overlap queries over the caller's `World3D`; invalid World/radius/result limits fail explicitly |
| HUD telemetry | `A3GameHudLayer` | Lightweight title/sorted status surface; generated UI remains game-owned |
| Material and lighting kit | `A3GameVisualKit` | Bounded PBR values plus shadowed sun/fill helpers; returns native Godot resources/nodes |
| Character/vehicle physics | Native `CharacterBody*`, `RigidBody*`, `move_and_slide` | Game-specific body shape, gravity, acceleration, and collision response remain generated code |
| Asset/scene graph | Native `ResourceLoader`, `PackedScene`, `Node` | Host-side provenance/import remains `GodotClient.assets`; runtime code receives only `res://` resources |
| VFX and audio | Native particles, shaders, `AudioStreamPlayer*` | Generated code chooses effect/audio semantics; no wrapper pretends to configure game content |

`capabilities.json` is the machine-readable form of this table. Every referenced
file is a real implementation exercised by the native framework smoke test.

## Runtime wire contract

`GodotClient.runtime.sessions` sends JSON messages to `A3GameRuntime`. The
endpoint defaults to `127.0.0.1:30050` and can be changed with
`A3GAME_GODOT_RUNTIME_HOST` / `A3GAME_GODOT_RUNTIME_PORT` or the matching
`a3game/runtime_host` / `a3game/runtime_port` project settings.

Normalized input contains:

| Field | Type / normalization |
| --- | --- |
| `move_x`, `move_y` | finite float clamped to `[-1, 1]` |
| `run`, `jump` | strict boolean |
| `yaw` | finite float |
| `pitch` | finite float clamped to `[-π/2, π/2]` |
| `seq` | integer |
| `timestamp` | finite float |

Generated gameplay subclasses or composes `A3GameRuntimeEntity` and handles its
`runtime_input` signal:

```gdscript
extends A3GameRuntimeEntity

func _ready() -> void:
	super()
	runtime_input.connect(_apply_input)

func _apply_input(input: Dictionary) -> void:
	# Acceleration, collision and animation rules are game-specific.
	velocity.x = float(input["move_x"]) * 6.0
```

`A3GameRuntime.bind_entity(node, entity_id)` provides explicit failures for
nodes that do not expose the required property/method contract.

## Sessions and Worlds

Sessions default to `world_001`. A World reset removes only matching native
session records and emits `world_reset`; gameplay may use that signal for its
own World cleanup. Entity clear always removes matching native session records.
It calls `clear_a3game_entity()` only when `destroy_actor` is `true`, allowing a
caller to detach runtime control while retaining the Godot node.

Leaving erases the native controller and deactivates its Python-side binding.
The departed controller cannot send input or be revived by a heartbeat; call
`join()` to create and register a fresh controller before resuming control.
Rejoining with the same participant ID preserves its entity ID and atomically
replaces the previous native controller binding, so the old controller cannot
continue sending input. Participant-based leave targets that current binding.

`session_joined` is an entity-creation request: it is emitted only when the
entity ID is not already present in the `a3game_runtime_entity` group. A
controller replacement emits
`session_reconnected(previous_session, session)` without a synthetic leave/join
pair. A join after explicit leave also emits `session_reconnected` when the
entity remains in the scene tree. Use `A3GameRuntime.find_entity(entity_id)` to
retrieve it. `session_left` means control detached; destruction belongs to
`clear_entity()` or game-owned World cleanup.

`sessions_snapshot(world_id)` returns defensive copies, while
`last_input_for(entity_id)` returns the latest accepted normalized input.
`world.reset` deletes only the matching World's state. `entity.clear` removes
every controller for that entity and calls `clear_a3game_entity()` only when
`destroy_actor=true`.

## Native helpers

```gdscript
var loaded := A3GameSceneLoader.instantiate_scene(
	"res://worlds/arena.tscn", get_tree().current_scene
)
if not loaded.ok:
	push_error(loaded.error)

var hit := A3GameCollisionProbe.raycast(
	get_world_3d(), global_position, global_position + -basis.z * 20.0
)
if hit.ok and hit.hit:
	print(hit.collider)

var played := A3GameAnimationDirector.play(self, &"Run", 0.12, 1.0)
if not played.ok:
	push_warning(played.error)
```

## Validation

After `install_framework()`, run the shipped native smoke script inside the
target project:

```bash
godot4 --headless --path /projects/MyGame --import
godot4 --headless --path /projects/MyGame \
  --script res://addons/a3game_playable/tests/framework_smoke.gd
```

Success prints `A3GAME_FRAMEWORK_SMOKE_OK capabilities=9`. The script loads and
executes every helper, checks negative/failure paths, instantiates a real
`PackedScene`, builds native UI/material/light objects, and exits nonzero on any
failed assertion.
