# Godot core gameplay references

These are complete, independent Godot 4 projects, organized like the repository's
Three.js examples: choose a reference by camera and mechanic, copy the relevant
pattern into generated code, and never depend on the example directory at
runtime.

| Project | Camera / genre | Core systems |
| --- | --- | --- |
| `NeonDodge2D/` | Fixed 2D arcade survival | `CharacterBody2D`, keyboard input, monitored hazards/pickups, HUD, score/shield, win/failure/restart state |
| `SolarRally3D/` | Third-person chase racing | `CharacterBody3D`, static collision, ordered checkpoints, PBR/emissive materials, directional/omni lighting, chase camera, laps/win state |
| `OrbitPinball2D/` | Fixed 2D physics pinball | `RigidBody2D`, static colliders, animated flippers, contact impulses, combo/lives state and procedural trails |

This split is intentional: camera and physics ownership change the core code far
more than cosmetic genre labels. The examples use no downloaded assets, so a
reviewer can reproduce them without credentials or an asset pipeline.

## Native validation

Each project contains a real `project.godot`, main `PackedScene`, deterministic
demo driver, interactive keyboard mode, and `res://scripts/smoke.gd`. Run all
three with an installed Godot 4 editor:

```bash
for project in NeonDodge2D SolarRally3D OrbitPinball2D; do
  godot4 --headless --path "engine_adapters/godot/examples/$project" --import
  godot4 --headless --path "engine_adapters/godot/examples/$project" \
    --script res://scripts/smoke.gd
done
```

The smoke scripts instantiate the actual main scene, advance live physics, and
assert motion plus game-specific entity/state contracts. A static file check
cannot produce `A3GAME_SMOKE_OK`.

## Generated-output traceability

`mechanic_contract.json` in each project records its exact reviewer output path:

| Reference | Generated demonstration |
| --- | --- |
| `NeonDodge2D` | `my_code/AAAGameForge/test_data/outputs/game101/godot/` |
| `SolarRally3D` | `my_code/AAAGameForge/test_data/outputs/game202/godot/` |
| `OrbitPinball2D` | `my_code/AAAGameForge/test_data/outputs/game303/godot/` |

Those delivery trees mirror the corresponding reference (excluding the ignored
`.godot/` cache). `work/gan/artifacts/game-provenance.json` records their content
hashes alongside video evidence.
