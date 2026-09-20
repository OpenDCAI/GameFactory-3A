# Chapters, Assets and Generation Tasks

Decompose by the player's chronological experience, not by asset type alone.
Map the complete chapter flow coarsely; detail the next chapter or playable slice
before generation. A chapter may contain several testable slices.

## 1. Map Requirements and Script Beats

Assign stable requirement and script-beat IDs. Mark each item required, optional,
or proposed. Map every required item to a chapter and acceptance condition.
Flag contradictions and missing transitions; never silently drop a script beat.
Use `agent_skills/game_design/narrative_design.md` when a story must be written or adapted.

## 2. Build the Chapter Timeline

For each chapter record:

| Field | Required design decision |
|---|---|
| `id`, `order`, `title` | Stable identity and default play order |
| `requirement_ids`, `beat_ids` | Why the chapter exists |
| `play_time_minutes` | Estimated start/end window on the default playthrough |
| `story_time` | Time within the fiction; distinguish flashbacks and time jumps |
| `entry_conditions` | Prior outcomes, inventory and knowledge required |
| `goal`, `beats` | Ordered setup, player actions, discovery and payoff |
| `mechanics` | Introduced or reused interactions and difficulty progression |
| `exit_conditions`, `next` | Completion, branch conditions and destination IDs |
| `retry`, `checkpoint` | Failure recovery and state retained on retry |
| `asset_refs`, `screen_refs` | Everything that must exist for this chapter |
| `acceptance` | Playable, narrative, visual and technical checks |

Treat play-time windows as estimates, not forced cutscene timers. Give branches
separate durations and explicit reconvergence or endings; do not add mutually
exclusive branches together. Preserve consistent state across chapter boundaries.
For a non-narrative game, use rounds, sessions or progression stages instead of
forcing a plot.

## 3. Derive a Per-Chapter Asset Manifest

Walk every beat and ask what the player sees, manipulates and hears. Inventory
characters, environments, props, motion, VFX, audio, UI graphics and cinematics
only where the beat requires them.

Give each asset a stable ID, type, purpose, first-use chapter, consuming beats,
source route (`generate`, `reuse`, `licensed` or `procedural`), style constraints,
variants, dependencies and acceptance criteria. Record existing artifacts when
reusing them. Define shared assets once; reference their IDs from later chapters.

Keep stateful variants explicit: full/empty cup, closed/open door, daytime/night
lighting, outfit or expression changes. A prop variant is not necessarily a new
mesh; specify whether material, animation or runtime state supplies it. Keep
runtime behaviors as Mechanic tasks, linked to the required assets.

## 4. Derive Pages and Screen States

For every chapter, list entry screens, HUDs, contextual interactions, dialogue,
pause/recovery and exit screens that are actually needed. Give each screen an ID,
a layout-template ID, entry/exit conditions, content slots and required bindings.
Reuse screens with chapter-specific content instead of duplicating implementations.
Specify shared shell pages such as title/settings separately from chapter screens.
Use `agent_skills/game_design/interaction_design.md` and the genre templates in
`agent_skills/game_design/game_layout_templates/`.

## 5. Schedule Work in Dependency Order

Create chapter-linked tasks: story/design -> assets and Mechanic contract -> UI
bindings -> assembly -> play validation. Allow independent work in parallel;
require the finalized Mechanic contract before UI bindings are implemented.
Use `agent_skills/game_design/task_tracking.md` for the task record and status rules.

Finish the current chapter's playable loop before expanding implementation.
On a story change, invalidate affected chapter, asset, screen and downstream task
references. Keep later chapters at outline fidelity, but never omit their expected
asset and page needs from the coarse plan.

## 6. Mandatory Item-by-Item Checklist

Produce explicit requirement, chapter, asset, screen and task lists. For each row,
record `item_id`, `chapter_id`, `check`, `status`, `reference` and `issue`.
Use `confirmed`, `missing` or `not_applicable`; require a reason for not_applicable.
Treat design confirmation as distinct from implementation or playtest completion.

- [ ] Map each required requirement/script beat to a chapter and acceptance check.
- [ ] Confirm each chapter's time window, story time, goal and ordered beats.
- [ ] Confirm every branch destination, entry/exit condition and retained state.
- [ ] Check each chapter's characters, scenes, props, motion, audio, VFX and UI assets.
- [ ] Confirm each asset's source, reuse, variants, dependencies and acceptance.
- [ ] Define the whitebox color legend; distinguish major functional/attribute classes with consistent colors plus labels, symbols or silhouettes.
- [ ] Verify each relevant asset's semantic color in both the scene and its display page, without conflicting ownership/state cues or leaking hidden information.
- [ ] Check login/entry, in-game HUD, pause, recovery and exit screens as applicable.
- [ ] Check character/unit archives and equipment/loadout inspection: full-body or object framing, authorized stats, slot compatibility, comparison, discovery and missing-model states.
- [ ] Confirm each screen's genre template, states, bindings and viewport readability.
- [ ] Confirm every task's inputs, outputs, dependencies and acceptance evidence.
- [ ] Resolve missing items for the next chapter before marking its design ready.

Expand these checks per item and chapter. Report unresolved IDs explicitly;
do not replace individual confirmation with a blanket claim that all work is covered.
