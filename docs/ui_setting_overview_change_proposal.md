# UI Setting Overview Change Proposal

Date: 2026-08-06

Status: proposed for maintainer review; not applied to
`agent_skills/setting_overview.md`.

## Reason

UI Code Generation now differs from Mechanic Code Generation in three
important ways:

1. every UI task must complete two ordered stages;
2. the Pipeline deterministically resolves two API documents;
3. Browser Play displays the engine-rendered result and must not reproduce the
   gameplay HUD.

The current setting overview still describes Mechanic and UI as selecting one
API document through the outer Agent. A maintainer should update it after
reviewing the wording below.

## Proposed Navigation Update

The existing game-content navigation may also be updated from the removed
legacy paths:

```text
operators/gen_mechanic/
operators/gen_ui/
```

to the current Pipeline paths:

```text
pipeline/code_gen/gen_mechanic/
pipeline/code_gen/gen_ui/
```

## Proposed UI Section

```markdown
## UI Code Generation

UI generation is a mandatory two-stage process:

finalized Mechanic artifact
    -> engine_native UI
    -> browser_play serving

Every UI task must complete both stages in order. A UI task is incomplete if
either stage is missing.

### Stage 1 - Engine-Native UI

Inputs:

- the finalized `mechanic_contract.json`;
- the Mechanic public runtime adapter paths;
- the Pipeline-selected target Engine API reference.

Output:

- native engine HUD, widget, menu, plugin, resources, and tests.

The native UI binds only through the public Mechanic contract/runtime adapter.
It must not read concrete gameplay classes or private gameplay fields.

### Stage 2 - Browser Play

Inputs:

- the completed engine-native UI result;
- the Pipeline-selected
  `engine_context/browser_serving_api.md`.

Output:

- the browser-playable serving layer.

The browser layer displays the running engine output, which already contains
the engine-native gameplay UI. It MUST NOT reproduce gameplay HUD, gameplay
state visualization, or Mechanic commands in HTML, CSS, or JavaScript.

For UI Code Generation, the Pipeline resolves exactly two non-empty API
documents before the outer Agent edits source:

engine_native -> engine_context/<engine>_api.md
browser_play  -> engine_context/browser_serving_api.md

The resolved paths are included in the prepared UI packet as read-only
`selected_api_contexts`. The Agent reads those exact documents and does not
select alternatives.
```

## Implemented Behavior Behind This Proposal

- `pipeline/code_gen/gen_ui/packet.py` resolves both API documents and records
  their paths and SHA-256 digests.
- UI finalization requires engine-native source/tests and Browser Play
  source/tests.
- UI finalization rejects modified API documents.
- UI finalization rejects detectable direct gameplay-class access.
- Browser Play receives no Mechanic bindings or public runtime-adapter paths.
- Browser Play source is rejected when it consumes or reproduces detectable
  Mechanic gameplay UI.
