---
name: game-design
description: Turn requirements or a script into an original playable story, a chronological chapter plan, chapter-scoped assets and UI layouts, and traceable generation tasks.
---

# Game Design

Turn supplied requirements and scripts into a playable story and chronological
chapter plan. Assume engine selection and top-level routing are already provided;
focus here on what happens, when it happens, and what each chapter needs.

## Design Sequence

1. Extract required experiences, plot facts, constraints and open questions from
   the brief and script. Keep stable requirement/beat IDs for traceability.
2. Write or adapt the story using `agent_skills/game_design/narrative_design.md`.
   Preserve supplied canon; label new plot material as proposed rather than
   pretending it came from the user.
3. Map chapters along estimated play time and fictional story time using
   `agent_skills/game_design/decomposition.md`. Give every chapter a player goal,
   ordered beats, entry/exit conditions and explicit branch destinations.
4. Derive each chapter's characters, scenes, props, motion, audio, VFX and UI
   needs. Define shared assets once and reference them across chapters.
5. Specify chapter screens and interaction states using
   `agent_skills/game_design/interaction_design.md` and the genre JSON files in
   `agent_skills/game_design/game_layout_templates/`. Select or adapt
   a layout by player activity, not by whichever template is easiest to fill.
6. Convert the current chapter into dependency-aware tasks using
   `agent_skills/game_design/task_tracking.md`. Generate, assemble and play a complete
   loop before detailing later chapters. Revise the story and dependencies from
   actual play evidence.

## Asset Display within Each Genre

Keep player-facing asset display pages inside the matching genre template, alongside
login and in-game layouts; do not create a separate genre or production dashboard.

- `agent_skills/game_design/game_layout_templates/rts.json`: use `rts_roster` for
  units/heroes and `rts_equipment` for equipment, companions and modular loadouts.
- `agent_skills/game_design/game_layout_templates/action_rpg.json`: use
  `action_rpg_roster` for characters/forms and `action_rpg_equipment` for inventory
  inspection and compatible comparisons.

Link the list, model preview and attributes by asset ID. Keep viewing separate
from equipping, recruiting or unlocking; mark unavailable models honestly.

## Whitebox Readability

During whitebox generation, prefer distinct, consistent flat colors for objects
with substantially different functions or gameplay-relevant attributes, not an
undifferentiated white or grey scene. Define the color-to-meaning legend before
building the prototype and reuse it in the scene, thumbnails and asset viewers.
Follow the semantic-color rules in `agent_skills/game_design/interaction_design.md`.
Pair colors with labels, icons or silhouettes; keep faction ownership and temporary
state distinguishable from functional class. Never reveal hidden information
through diagnostic colors in player-facing views. Treat these colors as prototype
communication aids, not a required final art style or implemented renderer feature.

## Mandatory Item-by-Item Review

List the requirements, chapters, assets, screens and tasks explicitly. Review each
row using the checklist in `agent_skills/game_design/decomposition.md`; record
its status, evidence/reference and unresolved issue. Do not replace item-level
confirmation with a blanket statement that everything is covered. Resolve missing
items for the next chapter before marking its design ready.

## Minimum Design Handoff

Provide a short premise and synopsis, a chapter timeline, a shared asset catalog,
per-chapter asset/screen references, and executable tasks for the next chapter.
Every required beat must map to player behavior, visible feedback and acceptance.
Check continuity of location, inventory, character knowledge and unresolved goals.

Keep gameplay and presentation jointly designed but separately implemented:
`UI -> Mechanic -> runtime framework`. Treat binding names in design documents
as requirements until the Mechanic contract actually publishes them.

Store game-specific stories, chapter plans and TODO state in the game's workspace,
not in this skill. The genre JSON templates are reusable design data; its preview contains
illustrative content, not a generated game or a connected generation service.
