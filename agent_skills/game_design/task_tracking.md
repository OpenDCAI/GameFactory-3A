# Task Tracking

Maintain one structured task list per game project. Let the model read it directly
and let the generation page display the same state; do not maintain separate
model-only and page-only checklists.

## Minimum Task Record

| Field | Purpose |
|---|---|
| `id`, `title` | Stable identity and a concrete result |
| `parent_id`, `chapter_id`, `beat_ids` | Chapter/beat ownership and feature grouping |
| `requirement_ids`, `asset_ids`, `screen_ids` | Traceability to requested behavior and chapter resources |
| `stage` | Story/design, asset, Mechanic, UI, assembly or validation |
| `design_revision` | Design version the task implements |
| `depends_on` | IDs of prerequisite tasks |
| `owner` | Agent or person responsible for the task |
| `inputs`, `outputs` | References to requirements, contracts and artifacts |
| `acceptance` | Observable conditions for completion |
| `status`, `blocker` | Current state and reason work cannot proceed |
| `evidence` | Test, build, screenshot or replay references, as appropriate |

Store concrete records and design packets in the game's task-owned workspace,
not in `agent_skills`. Reuse existing project path conventions. Keep this document
as a protocol; do not assume a particular storage service or page API exists.

## State Transitions

Use `needs_detail -> ready -> in_progress -> review -> done`, with `blocked` for
unresolved dependencies, missing inputs or execution failures.

- Mark `ready` only when inputs, acceptance criteria and dependencies are satisfied.
- Record ownership before starting work; avoid conflicting file edits.
- Move to `review` after recording outputs and verification evidence.
- Mark `done` only when the task's acceptance criteria pass. On failure, record the
  issue and return to `in_progress`, or use `blocked` when progress is impossible.
- Reopen affected tasks after design or contract changes. Recheck downstream
  results rather than trusting evidence from an older revision.

Track generation, assembly and validation as separate tasks. Never label a slice
playable merely because its source or assets were generated. Complete a parent
milestone only after required children and end-to-end play acceptance pass.

## Model and Page Behavior

Before each task, read its chapter goal, relevant story beats, current record,
asset/screen references, dependency results and latest blockers. After execution,
update outputs, evidence and status before selecting the next ready task. Group
the workbench by chapter and show which assets are new, reused or still missing;
keep narrative play order separate from parallel production scheduling.

Show the task tree, current work, blockers and evidence links in the generation
workbench. Synchronize updates through the same canonical records. Until this
integration exists, update the project records explicitly and report the UI gap;
do not claim the page is synchronized or require screenshots to recover task state.
