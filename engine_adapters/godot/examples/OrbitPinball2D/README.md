# Orbit Pinball 2D

A complete physics-pinball reference. It demonstrates a continuously simulated
`RigidBody2D`, static collision geometry, animated `AnimatableBody2D` flippers,
collision-driven impulses and scoring, procedural trails, HUD telemetry,
combos, lives, success state, and reset behavior.

Run interactively with `godot4 --path . -- --manual`. The default deterministic
flipper driver makes the same project suitable for unattended validation:

```bash
godot4 --headless --path . --import
godot4 --headless --path . --script res://scripts/smoke.gd
```

The reviewer demonstration copy is generated at
`my_code/AAAGameForge/test_data/outputs/game303/godot/`; it mirrors this tree
apart from Godot's ignored `.godot/` import cache.
