# Interaction and Presentation Design

Design mechanics and player-facing presentation together; implement them in
separate layers. Treat UI as part of the playable experience, not decoration
added after the rules are finished.

## Define the Complete Interaction

For each feature, specify:

- **Intent:** what the player wants and how the action becomes discoverable.
- **Rules:** prerequisites, valid actions, costs and outcomes.
- **Input:** trigger, targeting, confirmation, cancellation and interruption.
- **States:** unavailable, ready, active, success, failure and recovery, as relevant.
- **Feedback:** world cues, HUD, camera, animation, sound and VFX for each transition.
- **Acceptance:** how a player can understand, complete and repeat the interaction.

Map every actionable UI element to a required command and every display to a
required state or event. Define disabled and error behavior; avoid decorative
buttons with no effect.

## Establish a Coherent Visual Direction

Specify screen hierarchy, layout, typography, palette, spacing, motion and visual
references. Coordinate UI with the game world, camera and feedback timing.
Choose world-space or screen-space cues deliberately. Avoid generic dashboard
layouts and debug panels in the player experience unless the game requires them.

Review the full interaction, not only a still screenshot. Include viewport
adaptation, readable text, focus, input capture and relevant accessibility needs.

## Semantic Colors for Whitebox Generation

Prefer different flat colors when objects differ substantially in function or in
attributes that change player decisions: for example, a resource node versus a
production structure, a scout versus a heavy unit, or physical versus elemental
equipment. Do not assign a unique hue to every small numeric difference.

Record a project-local legend with `semantic_id`, `meaning`, `color`,
`secondary_cue`, `applies_to` and `visibility`. Associate each relevant asset with
its semantic ID; use the same mapping in world placeholders, roster thumbnails,
equipment viewers and property badges. Keep the legend with the game design,
not as a universal rule that overwrites every genre's art palette.

- Use neutral, lower-emphasis colors for non-interactive environment geometry.
- Choose a small, clearly separated functional palette. With many factions or
  classes, combine colors with silhouettes, patterns, symbols and text rather than
  relying on fourteen subtly different hues.
- Separate visual channels: functional tint on the model, ownership on a ring or
  banner, temporary state on an outline or icon, and selection on its own highlight.
  Do not overload one color with contradictory meanings or reuse reserved cues.
- Pair significant color differences with a non-color cue. Check contrast against
  the scene background and readability at the actual camera distance and UI size.
- Respect discovery and visibility rules. Hidden infection, disguise or unrevealed
  attributes must not change player-visible colors; author-only diagnostic overlays
  must be separate and labelled.
- Keep exact values in the detail panel. A category color is not a substitute for
  stats, nor does it imply rarity, ownership or an unlocked/equipped state.
- Replace prototype materials when final art arrives, preserving important cues.

Verify the mapping in both the game scene and asset display pages before accepting
the whitebox. These rules guide generation; they do not imply that an existing
preview automatically consumes semantic IDs or implements a diagnostic overlay.

## Choose and Adapt a JSON Layout

Read the matching genre file directly from
`agent_skills/game_design/game_layout_templates/`; no separate catalog is required. Name files by genre and template IDs
as `<genre>_<screen>`; never use a game title, franchise or character name.

- `agent_skills/game_design/game_layout_templates/rts.json`: isometric world view, a resource strip, and
  a bottom dock containing minimap, portrait, unit statistics, inventory and commands.
- `agent_skills/game_design/game_layout_templates/action_rpg.json`: rear third-person camera,
  unobstructed center, lower-left vitals/items and lower-right skills; show an
  encounter bar only when relevant.

Each genre includes `login`, `in_game`, `roster` and `equipment`. Keep the roster
and equipment pages dedicated to player-facing asset inspection, not production
asset management. Use `rts_roster` / `rts_equipment` for unit archives and modular
loadouts, and `action_rpg_roster` / `action_rpg_equipment` for full-body characters
and inventory comparison.

Design login as a game entry/title
screen over an environment, with continue/new-game/settings and optional account
entry. Do not force a website sign-in form on an offline single-player game.
Study reference games for spatial hierarchy and interaction patterns, not for
branded art or names. A genre is a starting point, not a limit on final art style.

Design the camera, terrain, architecture, player/unit silhouettes and UI together.
Do not replace the game scene with a storyboard, chapter dashboard or production
TODO list. Keep the world dominant in action games; reserve stable command space
where strategic unit control needs it.

For each chapter screen record `screen_id`, `chapter_ids`, `beat_ids`,
`template_id`, entry/exit conditions, content and required contract bindings.
Reuse a genre layout across chapters with different environments and state.

Each genre file declares its own 1600x900 reference canvas. `rect` is `[x, y, width, height]`
in percentages; `layer` controls stacking. Genre files contain palettes, scene
composition and UI regions. `protected_view` reserves world visibility.
`visible_when` names a preview flag such as `combat`. Region `action` values in
the preview are local demonstrations only, never gameplay or authentication code.
Treat `required_bindings` as design requirements until the real contract exists.

Review the flat-color composition before producing final art: protect the world
view, prioritize the current goal and action, keep text legible, and prevent
controls from overlapping. Replace sample content with chapter-specific content;
confirm keyboard focus and unavailable/error states before implementation.
The preview scales a reference canvas; it is not proof of a mobile-ready layout.
Design separate arrangements and test real target viewports when mobile is needed.

## Character and Equipment Inspection Checklist

- [ ] Provide an explicit entry to the character archive and equipment screen.
- [ ] Link the browser selection, full-body/object viewer and detail panel by asset ID.
- [ ] Frame large, mounted, flying and non-corporeal assets appropriately; do not force all entities into a humanoid portrait.
- [ ] Specify search/filter, no-results, unknown, locked, incompatible and missing-model states.
- [ ] Show base versus effective attributes with units and provenance; reveal only authorized information.
- [ ] Keep hidden infection, disguised ownership and unexplored enemy data out of public inspection bindings.
- [ ] Compare compatible slots for the same owner; do not assume a positive delta is always beneficial.
- [ ] Treat inspecting, comparing, equipping, recruiting, buying and unlocking as separate actions.
- [ ] Include optional companion or assembly slots only when required by the game's mechanics.
- [ ] Specify fit-to-bounds, reset, zoom and optional orbit controls for the real model viewer. The current preview uses clearly labelled 2D silhouettes, not loaded 3D assets.

`inspection_samples` contains generic layout fixtures only. Import actual project
stats through a separate authorized data adapter; never treat the sample values
as the source game's balance data. Read-only previews must not claim equipment
or faction changes were saved.

## Preserve Implementation Boundaries

Keep `UI -> Mechanic -> runtime framework`. Let Mechanic own rules and canonical
state; let UI own layout, feedback and public bindings. Request contract changes
for missing bindings instead of implementing a second gameplay state store.

Keep the generation workbench's chapter/TODO panel separate from the game's HUD
and menus. Keep Browser Play focused on session and stream delivery rather than
recreating engine-native HUD elements. Show chapter progress, asset readiness and
blocked tasks in the workbench, not as debug information inside the game.
