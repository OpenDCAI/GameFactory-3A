# three.js Reference Gameplay Packages

These packages are optional concrete examples built on the public
`@a3game/playable` contract:

| Package | Camera | Demonstrates |
| --- | --- | --- |
| `fps-example` | First person | Pointer-lock first-person movement, hitscan fire, reload, crosshair HUD |
| `arena-fighter-example` | Second person | Facing-locked duel movement, light/heavy attacks, arena rules, health HUD |
| `racing-example` | Third person (chase) | Arcade vehicle steering, boost, handbrake drift, lap and speed HUD |
| `explorer-example` | Third person (orbit) | Camera-relative movement over a height field, follow camera, charged bow, stamina |
| `rts-example` | Top-down (orthographic) | Order-driven units, box selection, pan/zoom rig, fog of war, minimap |
| `motion-vfx-example` | — | Getting motion onto a generated character, and batched particles, beams, and trails |

## Pick the example by camera, not by genre

**The camera decides more of a game's code than its genre does.** Two
shooters with different cameras share almost nothing; a racing game and an
exploration RPG with the same camera share their whole input and camera
layer. So when adapting these, start from the perspective the brief asks
for:

- **First person** — `fps-example`. The camera *is* the entity: it is
  parented to the player pivot, aiming comes straight from the input
  frame's yaw/pitch, and there is no character to see. A first-person game
  needs a **view model** (the weapon in the corner of the screen), and that
  view model is the most closely inspected asset in the whole project — it
  gets the largest texture budget and full-resolution normal maps.
- **Second person** — `arena-fighter-example`. Two fighters, a camera that
  frames *both*, and movement locked to the axis between them. What makes
  it its own case is that the camera is owned by the *match* rather than by
  either player, so neither entity may move it.
- **Third person** — `racing-example` (chase) or `explorer-example`
  (orbit). The camera is an independent object that trails the subject, so
  it needs lag, and movement must be **camera-relative**: pressing forward
  means "away from the camera", never "along the character's facing". The
  two sub-cases differ in who owns the yaw — a chase camera derives it from
  the vehicle's heading, an orbit camera owns it and the character reads it.
- **Top-down strategy** — `rts-example`. The camera belongs to *nobody*: it
  is a window onto the map, and no unit reads it. This family is also the
  only one that changes the **input contract**. The others are
  frame-driven — an input frame *is* the intent, and one controller drives
  one entity — which does not survive fifty units, because a player has one
  mouse and cannot author fifty continuous intents. Here input produces
  discrete orders that are stored on units and outlive the click, while
  `moveX/moveY` pan the camera. Read it before writing anything where the
  player commands a group rather than inhabiting a body; a squad shooter and
  a city builder have the same problem, whatever their camera. §9 of
  [`three_js_api.md`](../../../agent_skills/engine_context/three_js_api.md)
  states the contract.

Everything else follows from that choice. `explorer-example` is also the
one to read for **walking on non-flat ground**: its `terrainHeight` is a
plain function used by both the visible mesh and every ground query, which
is the only arrangement in which the player cannot walk through a hill.

They are never installed automatically. Generated games should adapt the
relevant patterns inside their own Gameplay Package rather than depending
on or extending an example package.

`motion-vfx-example` is the odd one out: it contains no game, only the two
patterns that every genre needs and that are easy to get wrong —
`assets.tryInstantiate` is correct for a downloaded character and silently
wrong for a generated one, which has no skeleton and no clips, and one
`Sprite` per spark is one draw call per spark. Read it before writing an
entity that swaps in imported art or an effect of any kind.

Each example owns its concrete gameplay classes, its spawner, its rules,
and its HUD. `@a3game/playable` owns only normalized input, session,
binding, entity, world, and observation contracts.

## Reading order

Each example names its modules after **what the thing is in that game** —
the same vocabulary the generated games under `test_data/outputs/` use, so
a pattern can be carried across without renaming it. There is no
`entity.js` or `factory.js` in a generated game: the controllable thing is
`vehicle.js` / `explorer.js` / `player.js` / `fighter.js`, the world is
`track.js` / `world.js` / `arena.js` / `stage.js`, and the spawner sits in
`index.js` next to the boot function it serves.

1. `src/<world>.js` - the space the game happens in. Read this first when
   the ground is not flat, because then it is a gameplay system rather than
   scenery.
2. `src/<subject>.js` - how the controllable thing implements
   `A3GameControllableEntity` and turns a normalized input frame into
   movement.
3. `src/index.js` - how the game boots: world load, HUD, input router,
   spawner registration, camera rig.
4. `tests/` - how a generated game proves it works, using the local
   runtime bridge instead of screenshots.

`explorer-example` mirrors `test_data/outputs/game_archer_explorer`
module for module: `world.js`, `explorer.js`, `arrow.js`, `index.js`.

`arena-fighter-example` predates this convention and still uses
`entity.js` / `factory.js`; treat `explorer-example` as the model.

`rts-example` splits step 2, being order-driven: `commands.js` (what an order
is), `unit.js` (how a unit executes one) and `selection.js` (who receives it).
Read `commands.js` first.

## Tests

`arena-fighter-example`, `explorer-example` and `rts-example` ship a spec under
`tests/`. An example carries no runner of its own; the plugin's config already
includes `../../examples/**/tests/**/*.spec.js`, so they run with the framework
suite:

```bash
cd engine_adapters/three_js/plugin/A3GamePlayable && npm test
```

They cover gameplay logic against the real session/runtime contracts using
synthetic DOM events, and validate neither WebGL output nor a browser playtest.

`rts-example` scope: real-time and continuous-position, using open terrain and
direct steering — **not** a pathfinder or crowd-avoidance solver, and grid
helpers are for placement only. Fog shades terrain and hides live enemies in
rendering, targeting and the minimap; there is no terrain occlusion, last-seen
marker system, economy, or network order protocol. Runtime snapshots are full
diagnostic state, not fog-filtered client messages. The rig clamps its focus,
not the entire view footprint. Imported Worlds need matching ground queries.

## Installing one as a starting point

```python
from engine_adapters.three_js import ThreeClient

three = ThreeClient(project_path="/path/to/project")
three.plugin.install(
    {
        "game_id": "my_game",
        "run_id": "default",
        "task_kind": "mechanic",
        "task_id": "gameplay_package",
    }
)
```

`plugin.install` consumes a repository task identity, not a path inside
this directory. Copying an example by hand bypasses the registry and the
dependency synchronization that `plugin.install` performs.

## Optional camera fixtures

| Directory | Purpose |
|---|---|
| `camera-comparison/` | Shared preview, measurements and tests for static/chase and generated camera pairs |
| `boss-action-demo/` | Authored combat prototype with action camera, live input and recording |

These are not required runtime packages. See each README for replay commands;
framing tests do not establish general SKILL effectiveness or visual comfort.
