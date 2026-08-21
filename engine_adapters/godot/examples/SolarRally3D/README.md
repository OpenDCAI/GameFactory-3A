# Solar Rally 3D

A complete third-person arcade-racing reference. It builds a lit 3D circuit
from Godot meshes, physical static geometry, a `CharacterBody3D` vehicle,
ordered `Area3D` checkpoints, emissive PBR materials, a chase camera, telemetry,
lap state, and a win loop.

Run interactively with `godot4 --path . -- --manual`. The default deterministic
driver provides unattended gameplay for smoke checks and recording:

```bash
godot4 --headless --path . --import
godot4 --headless --path . --script res://scripts/smoke.gd
```

The reviewer demonstration copy is generated at
`my_code/AAAGameForge/test_data/outputs/game202/godot/`; it mirrors this tree
apart from Godot's ignored `.godot/` import cache.
