---
name: game-feel
description: Improve playable game feedback and cameras, or author uniform camera strategies for code-rendered video datasets. Use the CG director for prompts targeting video-generation models.
---

# Game Feel and Playable Cameras

Read for impact/action feedback, device vibration, screen dynamics, camera follow,
composition or transitions. For code-rendered video datasets, read
[the camera configuration guide](references/camera-view.md): every video uses the
same parameter schema with an explicit fixed, follow, follow_move, orbit or move strategy.
For video-model-generated CG clips use the CG director instead.
Paths below are repository-root-relative.

## Ownership and handoff

Keep `UI -> Mechanic -> runtime framework`. Mechanic owns confirmed events and
state; presentation consumes them. A swing/fire event is not a confirmed impact.
Publish the necessary events in `mechanic_contract.json` before UI binds them.
Do not introduce camera shake, HUD, vibration or global time scaling into rules.

For each player action record in the game's workspace:

| Field | Required decision |
|---|---|
| Trigger | Exact published event, actor/recipient scope, confirmed outcome |
| Cue | Animation/audio/VFX/UI cues and asset IDs; meaningful cue without motion/haptics |
| Motion | Translation in engine world units, FOV degrees, duration seconds, upper bound |
| Haptics | Opt-in setting, capability detection, duration with units, unsupported fallback |
| Repetition | Event identity/edge trigger, replace/coalesce policy, rate limit |
| Camera | Focus targets, offset, FOV/frustum, follow response, switch/cut policy, owner |
| Cleanup | Pause, focus loss, device loss, respawn/scene exit and disposal |
| Evidence | Native tests and observed play/recording; device test separately |

Use subtle motion defaults and independent feedback, intensity, haptics and
reduced-motion controls. Honor the platform's motion preference until the player
overrides it. Zero optional motion removes shake/bob/roll/FOV pulses while keeping
health, direction and action outcomes readable through static cues. Avoid flashes.

## Camera implementation

One final camera writer owns base pose plus transient offsets. Never integrate
last frame's shake back into the base pose. Use seconds and `1-exp(-response*dt)`
for follow damping, with explicit cuts for spawn/teleport. Keep player input and
physics independent of presentation; aiming/picking must use the intended base
aim, not a vibrating display pose. Frame the player and relevant action without
revealing hidden targets. Disable competing Orbit/PointerLock/cinematic writers.

Choose chase, fixed arena, over-shoulder, first-person or authored shots from the
activity. Define target weights/look-ahead, distance/FOV bounds and switch priority
before tuning. Resolve wall/ceiling/terrain obstruction with the selected engine's
collision query; test corners and near-plane clipping. A center ray alone cannot
prove clearance for the camera volume. Do not add an unrelated engine dependency.

## Existing Three.js implementation

Public exports: `A3GameFeedback`, `A3GameCameraRig` and
`A3GameActionCameraDirector` from `@a3game/playable`.
Read `engine_adapters/three_js/plugin/A3GamePlayable/src/engine/game-feel.js` and
its README contract before using them. The arena fighter boot example consumes
confirmed `damaged` events, follows the local player after simulation and exposes
settings/pause/resume/dispose. The reusable rig supports offset/FOV shot changes
and an optional center-line blocker query, not a full collision sweep.

For action encounters, consume confirmed Mechanic phase/state: approach,
wind-up, impact, airborne, landing and recovery. Frame both actor bounds including
weapons and vertical displacement; do not validate only a horizontal walk. Keep
same-side arcs bounded and return to a stable playable shot after a beat. Camera
shots must not steal movement/aim authority. Use recoil, animation, contact
effects, damage/health cues and optional audio together; shake alone is not hit
feel. Reduced motion suppresses optional arcs/FOV pulses/particles, while essential
movement and static outcome cues remain readable. Never freeze gameplay for drama.

`A3GameActionCameraDirector` selects bounded same-side shots from caller-owned
modes and fits a conservative two-actor sphere. Its distance cap can still limit
framing; inspect `framingLimited`. The original `boss-action-demo/` shows scripted
vertical combat and separate live move/jump/attack inputs with the public runtime.
Its authored action comparison is separate from the preserved independent
generation pair; do not label it as another generated-skill experiment.

`navigator.vibrate` is a best-effort local-browser capability, not guaranteed
hardware feedback. It may be absent, return false or throw. It uses milliseconds.
The implementation does not provide Gamepad actuator output or remote Browser
Serving vibration transport. Preserve audio/animation/static outcome cues when
unsupported; never claim a recording proves physical vibration. Other engines
must use their existing APIs and document units (e.g. Godot duration seconds),
capability detection and cancellation; do not claim those adapters are implemented.

## Compare Before / After and Tune

Keep baseline/candidate recordings with the same brief, model settings, assets,
scene, viewport and action trace. Preserve prompts and raw outputs for generation
comparisons. Report readability and comfort observations beside objective metrics;
passing tests alone do not establish better feel. Change one presentation setting
at a time, rerun the same trace, and update this skill from reproducible observations.
Use `engine_adapters/three_js/examples/camera-comparison/` for reference-camera
and generated-module comparisons; one generated pair does not establish general effectiveness.
Choose a lateral composition when player/target silhouettes obscure the action,
then verify both actors in the actual game. Numeric defaults stay game-specific.

## Acceptance

Test confirmed hit versus miss, event replay/overlap, limits and return to base
pose, 30/60/120 Hz duration/damping, spawn and shot changes, pause/resume, focus
loss and disposal, unsupported/rejected vibration, live reduced-motion changes,
and disabled feedback. Inspect camera walls/corners/ceilings/slopes in actual
play; record and review action readability and discomfort. Report missing hardware,
collision-volume validation or live play evidence instead of calling them passed.

Pause/feedback-off checks must inspect the camera immediately after the change,
while an impulse is active. Merely stopping future updates can freeze a residual
translation/FOV cue. Compare against the no-impact base pose at the same time;
clear optional offsets before suspending the writer.
