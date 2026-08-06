# UI Code Generation Migration

Date: 2026-08-06

Status: implementation and focused contract tests complete. Real generated-game
runtime validation remains pending.

## Target Flow

One UI task uses a fixed ordered delivery plan:

```text
completed Mechanic artifact
    -> pipeline/code_gen/gen_ui/run.py prepare
    -> outer Agent reads UI Skill, Prompts, requirements, and Examples
    -> engine_native
         Mechanic contract + Public paths + declared bindings
         + Agent-selected Engine API document
         -> engine-native UI plugin/source/tests
    -> browser_play
         completed engine-rendered game
         + Agent-selected Browser Serving API document
         -> generated_ui/browser_play Web Play mapping
    -> pipeline/code_gen/gen_ui/run.py finalize
```

`browser_play` is not a standalone `ui_target`. The task declares the requested
engine runtime target; Browser Play is appended automatically after the native
UI stage.

## API Discovery

The Pipeline provides:

- the target Engine identifier;
- the read-only Engine Context directory;
- ordered API Context targets for the Engine and `browser_serving`.

It never provides a concrete API document path. The outer Agent lists the
directory and selects the two matching non-empty documents itself.

The task may provide read-only engine Example plugin paths. The Agent uses them
for plugin/module layout, Mechanic contract consumption, lifecycle, input, and
native UI test patterns. Examples remain references, not generated-game
dependencies.

## Mechanic Boundary

The completed Mechanic artifact is mandatory and read-only.

- `engine_native` receives the declared state/event/command bindings and
  contract-specific Public paths.
- `browser_play` receives empty Mechanic bindings and empty Public paths.
- Browser Play maps the engine-rendered frame, sessions, assets, Play state,
  and generic input through Browser Serving. It does not generate a second Web
  gameplay HUD.

## Output

Task-owned output remains under:

```text
generated_ui/
    engine-native UI plugin/source/resources
    engine-native UI tests
    ui_binding_manifest.json
    Tests/fixtures/mechanic_contract_fixture.json
    screenshot_plan.json
    browser_play/
        Browser Play source/resources/tests
```

Finalization requires both native UI source and Browser Play source. Actual
engine builds, Pixel Streaming startup, screenshots, and authoritative
runtime evaluation remain separate execution-stage responsibilities.

## Shared Code-Generation Lifecycle

Mechanic and UI reuse task-neutral helpers from:

```text
pipeline/common/artifacts.py
pipeline/common/prepare.py
pipeline/common/finalize.py
pipeline/common/code_gen.py
```

`pipeline/common/code_gen.py` owns only generic parsing, Example resolution,
repair payload validation, read-only boundary checks, Engine Context root
checks, and task selection. Mechanic and UI semantics remain in their
respective `run.py` modules.

## Migration Inventory

- added `agent_skills/code_gen/ui`;
- added `pipeline/code_gen/gen_ui/{run.py,eval.py}`;
- added shared `pipeline/common/code_gen.py`;
- removed legacy `operators/gen_ui` and `pipeline/ui`;
- removed UI Operator registration from the asset harness;
- added deterministic delivery-plan and Browser Play output checks.

## Validation

Focused checks passed on 2026-08-06:

- 15 Mechanic CodeGen tests;
- 24 UI CodeGen tests;
- 3 Browser Serving API documentation tests;
- Python compilation for the shared helper and both CodeGen runners.

No README file was changed.

## Remaining Validation

Select one real completed Mechanic artifact and execute:

```text
Mechanic prepare/finalize
    -> UI prepare
    -> real outer-Agent native UI + Browser Play edits
    -> UI finalize
    -> engine project integration/build
    -> Browser Serving with real Pixel Streaming
    -> frontend Play and normalized input evidence
```
