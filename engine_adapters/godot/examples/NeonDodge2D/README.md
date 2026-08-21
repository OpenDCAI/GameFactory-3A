# Neon Dodge 2D

A complete procedural arcade-survival reference. It demonstrates a moving
`CharacterBody2D`, monitored `Area2D` hazards and pickups, keyboard input,
animated visuals, HUD updates, score/shield state, win/failure states, and a
restart loop without external assets.

Run interactively with `godot4 --path . -- --manual`. The default deterministic
demo mode is suitable for unattended validation and recording. Run the native
smoke probe with:

```bash
godot4 --headless --path . --import
godot4 --headless --path . --script res://scripts/smoke.gd
```

The reviewer demonstration copy is generated at
`my_code/AAAGameForge/test_data/outputs/game101/godot/`; the two trees must be
byte-for-byte equivalent apart from Godot's ignored `.godot/` import cache.
