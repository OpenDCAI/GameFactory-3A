# 3AGameFactory game-generation agent guide

Read this file first when you are asked to use 3AGameFactory to create or improve
a game. It routes you to the minimum task-specific Skills and engine API context.
It is for **game-generation work**: generate assets, gameplay, UI, scenes, and
engine-ready projects. It is not a source-code API reference.

## Project goal

3AGameFactory helps a coding agent turn a game requirement into a playable game
slice. It supports image and T-pose preparation, 3D objects, 3D scenes, motion,
audio, CG video, gameplay mechanics, UI, and full-pipeline assembly for UE5,
Blender, Unity, Godot 4, and three.js.

## Path convention

Every repository path in this file, and in every Skill it routes you to, is
written from the repository root as `<REPO_PATH>/...`. Replace `<REPO_PATH>` with
your local checkout of `https://github.com/OpenDCAI/GameFactory-3A`.

This matters because the Skills and the engine reference code do **not** share a
parent directory:

| Written as | What it is |
|---|---|
| `<REPO_PATH>/agent_skills/engine_context/ue5_api.md` | a Skill, inside the Skills tree |
| `<REPO_PATH>/agent_skills/code_gen/mechanic/game_generation.md` | a Skill, inside the Skills tree |
| `<REPO_PATH>/engine_adapters/ue5/` | engine reference code — a **sibling** of `agent_skills/`, not inside it |
| `<REPO_PATH>/pipeline/`, `<REPO_PATH>/operators/`, `<REPO_PATH>/models/`, `<REPO_PATH>/scripts/`, `<REPO_PATH>/test/`, `<REPO_PATH>/test_data/`, `<REPO_PATH>/third_party/` | also siblings of `agent_skills/` |

So an `engine_context/...` or `code_gen/...` fragment written without a prefix is
relative to `agent_skills/`, while `engine_adapters/...` and the trees above are
relative to the repository root. Always resolve a path from `<REPO_PATH>/` before
opening it. Shell commands inside fenced code blocks stay repo-root-relative:
run them with `<REPO_PATH>` as the working directory.

## End-to-end game-generation workflow

Follow this order for every game request. Do not jump directly to code or asset
generation before the game has a plan.

1. **Clarify the brief.** Identify the target engine, game genre, player loop,
   target platform, requested deliverables, visual style, references, budget,
   and acceptance criteria. If the user did not specify an engine or visual
   style, ask for them before planning. If only one choice is missing, state a
   reasonable default and ask the user to confirm it before implementation.
2. **Plan the game.** Write a concise, testable plan: core loop; controls;
   camera; player and enemy/vehicle roles; level flow; UI; asset list; motion,
   audio, VFX, and lighting needs; engine integration; and validation scenes.
   Each planned asset must name its purpose, style, source route, and acceptance
   criteria. Keep the plan aligned with the user's requested style.
3. **Produce and review assets.** Generate the planned assets, then run the
   applicable asset QA before integration. For mature asset types, especially
   3D objects, prefer capable closed-source/cloud generation APIs when they are
   available and permitted by the user's budget and privacy constraints. For
   less mature generation types—especially motion and 3D scenes—prefer suitable
   licensed assets from the chosen engine's asset library when that produces a
   more reliable, shippable result. Record provenance and licence information.
4. **Build the game in the selected engine.** Read
   `<REPO_PATH>/agent_skills/engine_context/engine_overview.md`; it routes you to
   the applicable CodeGen Skill and then the one matching engine API. Use only
   that API and the minimum relevant same-engine reference code to create the
   scene, gameplay, UI, materials, animation, effects, and engine-specific
   project structure.
5. **Validate, play, and iterate.** Build, launch, play, record, and review the
   game, then fix and repeat. This step is not optional and is not satisfied by a
   successful compile. Follow the dedicated section
   [Validate, play, and iterate](#validate-play-and-iterate) below.

## Start with the task requirement

Before writing or running anything, identify:

1. **Target engine** — `ue5`, `blender`, `unity3d`, `godot`, or `three_js`.
2. **Requested deliverables** — assets, motion, audio, CG video, gameplay, UI,
   a scene, or a full playable slice.
3. **Acceptance criteria** — visual style, player interactions, platforms,
   performance limits, and the evidence required to call the result complete.
4. **Existing inputs** — requirement text, concept images, reference videos,
   generated artifacts, and any selected engine project.

Do not mix engine APIs. Select one target engine unless the requirement explicitly
asks for multiple independent deliverables.

## Required reading by task

| Work | Read first | Then read when needed |
|---|---|---|
| Generate or select a 3D object | `<REPO_PATH>/agent_skills/asset_qa/3d_object/SKILL.md` | `<REPO_PATH>/agent_skills/asset_qa/3d_object/orientation_review.md` for imported mesh facing and scale |
| Generate a 3D scene | `<REPO_PATH>/agent_skills/asset_qa/3d_scene/SKILL.md` | the selected engine API after the scene strategy is chosen |
| Rig, generate, fetch, or retarget motion | `<REPO_PATH>/agent_skills/asset_qa/motion/SKILL.md` | the selected engine API before importing the motion |
| Prepare a character image or T-pose | `<REPO_PATH>/agent_skills/asset_qa/image/SKILL.md` | the selected 3D-object or motion skill after preprocessing |
| Generate dialogue or sound effects | `<REPO_PATH>/agent_skills/asset_qa/audio/SKILL.md` | the selected engine API before in-game integration |
| Generate CG video | `<REPO_PATH>/agent_skills/asset_qa/cg_video/SKILL.md` | the selected engine API when the video is used in-game |
| Generate gameplay mechanics | `<REPO_PATH>/agent_skills/engine_context/engine_overview.md` | `<REPO_PATH>/agent_skills/code_gen/mechanic/game_generation.md`, then the selected engine API |
| Generate UI or browser play | `<REPO_PATH>/agent_skills/engine_context/engine_overview.md` | `<REPO_PATH>/agent_skills/code_gen/ui/game_ui_generation.md`, then the selected engine API and `<REPO_PATH>/agent_skills/engine_context/browser_serving_api.md` |
| Build a full game slice | this file, then `<REPO_PATH>/agent_skills/engine_context/engine_overview.md` | the required asset Skills, CodeGen Skills, and selected engine context routed by those documents |

## Select exactly one engine context

For Mechanic, UI, or full Engine integration, do not select an API directly
from this table before reading
`<REPO_PATH>/agent_skills/engine_context/engine_overview.md`. That routing
document selects the applicable CodeGen Skill first and then exactly one matching
API context:

| Engine identifier | Required API context | Reference code |
|---|---|---|
| `ue5` | `<REPO_PATH>/agent_skills/engine_context/ue5_api.md` | `<REPO_PATH>/engine_adapters/ue5/` |
| `blender` | `<REPO_PATH>/agent_skills/engine_context/blender_api.md` | `<REPO_PATH>/engine_adapters/blender/` |
| `unity3d` | `<REPO_PATH>/agent_skills/engine_context/unity3d_api.md` | `<REPO_PATH>/engine_adapters/unity3d/` |
| `godot` | `<REPO_PATH>/agent_skills/engine_context/godot_api.md` | `<REPO_PATH>/engine_adapters/godot/` |
| `three_js` | `<REPO_PATH>/agent_skills/engine_context/three_js_api.md` | `<REPO_PATH>/engine_adapters/three_js/` |

Use public client APIs and documented launch paths only. Treat engine reference
projects as read-only implementation references unless the task explicitly grants
permission to edit them.

## Asset decision policy

For every requested asset, follow this order:

1. **Generate it** from the requirement. Prefer a suitable cloud model when it
   is available; use a local/open model when the requirement, budget, privacy,
   or offline execution calls for it. A paid cloud backend requires the user's
   approval first: pause, send the purchase/API-key page, state the estimated
   cost, ask for the key, and wait — see *Paid cloud backend* in
   `<REPO_PATH>/agent_skills/asset_qa/README.md`.
2. **Use a licensed source or fallback** when generation quality is not suitable.
   Preserve source and licence/provenance information with the artifact. Do not
   bypass login-gated or licensed sources with scraping.
3. **Report the gap** when neither generation nor an allowed source can provide
   a shippable asset. State the missing capability, attempted route, and a safe
   fallback for the game.

Run the relevant visual QA skill for generated or imported 3D content before
claiming it is ready for a game. A structurally valid mesh can still face the
wrong direction, have implausible scale, or be visually unusable.

<a id="validate-play-and-iterate"></a>

## Validate, play, and iterate

Step 5 of the workflow, and the only stage that produces playability evidence.
"It compiles", "it launches", or "no errors in the log" is **not** validation.

1. **Build, launch, and actually play.** Use the selected engine's documented
   public client / launch path. Execute the **majority of the intended player
   operations**, not only startup: movement, jump/dash, camera, attack or fire,
   interact/pickup, vehicle driving, damage and death, respawn, win/lose, pause,
   and every promised UI screen. Run the core loop end to end at least once, plus
   the edge cases the plan named.
2. **Record a gameplay video.** Capture those operations via browser serving or
   the engine's capture path — small-resolution and short, evidence rather than a
   trailer. Cover idle, locomotion, turning, the main verb, the vehicle if any, a
   VFX trigger, a full UI pass, and a scene transition. Watch it; never judge the
   game from source code or one screenshot.
3. **Review the recording** against the table below. Every row is a failure mode
   that a compile and a structural asset check cannot catch.
4. **Fix and iterate.** Log each finding as: symptom in the video → owning layer
   (asset, import metadata, mechanic, UI, scene, lighting, VFX) → fix →
   re-verified in a new recording. Fix the cause in its owning layer; never patch
   an asset problem inside gameplay code. Repeat build → play → record → review
   until the acceptance criteria and every row below pass. Retain or report the
   final review video, the operations exercised, and the findings fixed. Report a
   capability gap instead of declaring success on unverified flows.

| Review area | Look for |
|---|---|
| Image quality | Mesh silhouette and topology artefacts, texture resolution, material response, seams, LOD pops. Scene: composition, ground/wall continuity, holes, missing collision floor, draw distance, fog |
| Lighting | Exposure, harsh or absent shadows, light leaking through geometry, lightmap/GI artefacts, unlit-black or blown-out surfaces, light direction inconsistent between characters and environment |
| Style match | Palette, contrast, post-processing, and mood must match the requested style (cyberpunk neon, low-poly stylised, photoreal, …). A clean frame in the wrong style is a failure |
| Dynamic effects | VFX fire on the right event and stop correctly (no stuck loop, one-frame flash, or effect left after death); plausible scale/density/intensity; spawned at the right socket and following the object; impact, footstep, engine, and UI feedback present and synchronised with animation and audio |
| Facing while moving | Character faces its movement direction — not walking or firing backwards; animation forward axis agrees with controller and camera-relative input. Weapon muzzle/blade points forward and away from the body, projectiles leave along that axis. Vehicle drives on its front, steers the correct way, no forward animation while reversing. Arrows, missiles, drones, mounts: travel direction matches visual forward. Re-check after every retarget, re-import, or axis change, and fix it in asset/import metadata rather than compensating rotations in gameplay code |
| Combined parts | Weapon held at the grip, **in front of / beside the character**, not inside the torso or floating off the palm, correct hand and socket. Vehicle wheels **under the chassis** at the four wheel wells, touching ground, neither sunk in the body nor hovering outside it, rotating on the correct axis and steering at the front. Feet on the floor, not floating or sunk; same for props on tables, racks, walls. Helmet, backpack, shield, turret on their sockets and following the skeleton. Plausible relative scale of character vs. weapon vs. vehicle vs. door vs. room. No interpenetration during animation, not only in the idle pose |
| Playability | Collision and physics (no falling through the floor, no getting stuck, no jitter); camera not clipping into walls or the character and framing usable; controls responsive and no inverted axis; UI readable, anchored at the tested resolution, bound to real gameplay state; no stutter that makes the tested operations unplayable |

## Completion rules

- Preserve the task's acceptance criteria; do not substitute a different game
  or engine because it is easier to run.
- Use `<REPO_PATH>/pipeline/common/paths.py` for generated artifact paths; do not
  hand-build paths below `<REPO_PATH>/test_data/outputs/`.
- Keep generated assets, gameplay, UI, and engine integration as separate
  deliverables until their required validation has passed.
- Never claim that a game is playable solely because source code was written.
  Build, launch, representative player-operation checks, and visual review are
  separate evidence.
- For a game-delivery task, complete
  [Validate, play, and iterate](#validate-play-and-iterate) and retain or report
  the final low-resolution review video and its reviewed flows. Verify mesh
  orientation, transforms, attachment points, animation, collision, camera,
  controls, VFX, UI, lighting, and style before declaring the game ready.
- Keep credentials in environment variables or local secret stores. Never commit
  API keys, tokens, or private media.
