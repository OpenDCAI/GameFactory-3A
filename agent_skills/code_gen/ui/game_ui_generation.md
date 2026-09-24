# Game UI Generation

Generate one task's engine-native UI, then its Browser Play delivery, inside the
prepared workspace. UI owns presentation and public Mechanic binding; it does
not own gameplay rules, Browser backends, execution, or evaluation.

## Authority And Inputs

Read before editing:

- the prepared UI packet, requirement, acceptance criteria, design/general
  requirements, screens, viewports, constraints, and forbidden UI;
- the Pipeline-owned canonical Engine and read-only Engine Context;
- the finalized Mechanic artifact, contract, declared bindings, and only its
  public runtime-adapter paths;
- registered same-Engine Mechanic/UI Example roots, suggested paths, required
  Browser Play references, and the validated Browser Play handoff;
- every supplied reference image, this Skill, and the referenced Prompts.

Authority order is: finalized Mechanic contract for available bindings; UI task
and acceptance criteria for behavior; reference images for visual composition;
design/general requirements for product intent; Examples for implementation
structure. The Pipeline owns Engine identity, boundaries, validation, and
finalization. Do not change the Engine or mix APIs/Examples across Engines.

Examples are read-only engineering references, not base plugins, templates,
scaffolds, inheritance targets, runtime dependencies, genre/style constraints,
or capability limits. Inspect the smallest useful set below registered roots;
do not scan entire roots. Any same-Engine genre may teach module, build,
binding, test, or native UI patterns. No analogous gameplay or visual Example
is required.

## Delivery Workflow

Generate both stages in order:

```text
1. engine_native
2. browser_play
```

### Engine Native

Under `generated_ui/`, generate the real HUD, screens, widgets, resources,
layout, focus/input handling, feedback, bindings, fixtures, and native tests.

1. Read the matching Engine API.
2. Read the finalized Mechanic contract and only declared public adapter paths.
3. Inspect the minimum useful Mechanic and native UI references.
4. Query state, subscribe to events, and invoke commands only through the
   public Mechanic runtime adapter.
5. Implement every required screen and state, including relevant HUD,
   contextual prompts, menus, tabs, lists, dialogs, loading/empty/disabled/
   success/error states, focus transitions, and event/command feedback.
6. Generate the binding manifest and mocked Mechanic fixture for native tests.

One contract may drive multiple screens, but UI must not copy the Mechanic
contract, invent bindings, cast to concrete gameplay classes, inspect private
producer types, reimplement rules, or maintain a second gameplay state store.

### Browser Play

Generate task-owned Browser Play source and tests under:

```text
generated_ui/browser_play/
```

Use the Browser Serving API, registered capabilities, required Browser Play
Example, and repository-owned player frontend as read-only references. The page
must:

- health-check Browser Serving;
- recover a supplied session or create a generic session;
- consume the engine-neutral `stream_url`;
- preserve keyboard/mouse focus inside the streamed Engine frame;
- present truthful booting, ready, and error states;
- provide task-required stream/session controls and generic input behavior.

Do not rely on an undocumented external page to create the first session.
The Engine stream already contains the native gameplay UI. Browser Play must
not duplicate the HUD, consume Mechanic bindings, implement gameplay commands,
own transport, branch on Engine names, or generate/modify an `EngineBackend`.
The registered backend owns transport-specific URL preparation.

Generate `browser_play_manifest.json` using
`gamefactory3a.browser_play_manifest.v1` and a referenced thin task-owned launch
script. The script may set documented Browser Serving environment variables and
invoke `python -m engine_adapters.browser_serving`; it must not implement,
copy, or import a concrete backend. This is the only allowed process-lifecycle
exception.

## Visual Rules

Inspect every supplied reference image. Apply task requirements and forbidden
UI before visual similarity; use images for hierarchy, density, spacing,
color, typography, and style. Preserve higher-priority constraints and report
material deviations.

Generate complete interaction states rather than a screenshot-only shell.
Native UI owns gameplay presentation. Browser Play owns only delivery,
stream/session controls, and generic browser interaction.

## Outputs, Provenance, And Tests

Required task-owned outputs include:

- engine-native UI source/resources and native tests under `generated_ui/`;
- Browser Play source/tests under `generated_ui/browser_play/`;
- `generated_ui/ui_binding_manifest.json`;
- `generated_ui/Tests/fixtures/mechanic_contract_fixture.json`;
- `generated_ui/screenshot_plan.json`;
- `generated_ui/context_used.json`;
- `generated_ui/browser_play/browser_play_manifest.json` and its launch script.

`context_used.json` uses `gamefactory3a.context_used.v1` and records:

- the matching Engine API with stage `engine_native`;
- Browser Serving API with stage `browser_play`;
- required Browser references as `browser_play_example`;
- only consulted Mechanic/UI paths as `mechanic_example` or `ui_example`;
- at least one allowed engineering `purpose` per Example entry.

Record only repository-owned, allowed, same-Engine context. Do not record
root-only access, unconsulted paths, unrelated context, or generated output from
other tasks.

Native tests use the mocked Mechanic fixture and cover bindings, state, layout,
interaction, focus, and required screens. Browser tests use Browser Serving
fixtures and never consume the Mechanic fixture. Tests must fail when required
behavior is absent; avoid empty assertions and unconditional success.

## Boundaries And Ownership

- Write only inside the prepared workspace and keep task-owned output under
  `generated_ui/`.
- Treat packet inputs, Engine Context, Examples, Mechanic artifacts, reference
  images, Skills, Prompts, and framework frontends as read-only.
- Do not inspect, compare with, copy, or adapt generated implementation from
  other tasks or games under `<REPO_PATH>/test_data/outputs/` or a relocated output root.
- Do not modify Mechanic source, Pipeline metadata, framework backends, or the
  repository-owned Browser Serving frontend.
- Do not create replacement contracts, invented bindings, fallback state
  stores, runtime mocks/providers, `generated_adapters/`, backend factories,
  stream transport, or Engine input injection.
- Do not execute the generated launcher; it belongs to the later execution
  stage.
- Do not invoke execution/evaluation-only APIs, launch Engines,
  capture/fabricate screenshots, weaken tests, or claim
  build/playability/benchmark success.

Ownership is separated:

- the Agent owns engine-native UI, Browser Play, manifests, fixtures, generated
  tests, and UI-owned repairs;
- Mechanic owns gameplay rules, state/events/commands, and its public adapter;
- Browser Serving owns backend registration, transport, stream URL preparation,
  and Engine integration;
- the Code Generation Pipeline owns packets, boundaries, snapshots,
  finalization, and publication metadata;
- execution/evaluation owns authoritative builds/tests, rendering, screenshots,
  runtime evidence, Browser smoke tests, and scoring.

## Run And Publication Contract

A run is the smallest reproducible publication unit:

```text
Task Packet -> UI Generation (Engine Native + Browser Play)
            -> Finalized UI Workspace -> Evaluation
```

Every artifact is addressed by `(game_id, run_id, task_kind, task_id)` and
lands in the shared per-game output tree described by
`<REPO_PATH>/test_data/outputs/README.md`:

```text
test_data/outputs/<game_id>/
|-- latest -> <run_id>/             # symlink to the most recent run
`-- <run_id>/
    |-- run_meta.json               # run provenance, Pipeline-owned
    |-- ui_results_summary.json     # Pipeline-owned
    |-- ui/<task_id>/               # the prepared UI workspace
    `-- eval/ui/<task_id>/          # metrics.json, Pipeline-owned
```

`<REPO_PATH>/pipeline/common/paths.py` owns these paths; do not construct them
manually. The Pipeline prepares `ui/<task_id>/` and hands the Agent its path;
the Agent writes only inside that workspace.

The UI workspace is:

```text
ui/<task_id>/
|-- meta.json       # Pipeline-owned identity and status
|-- generated_ui/   # all task-owned UI output (see Outputs, Provenance, And Tests)
`-- demo_outputs/   # Pipeline-owned, reserved
    |-- code_gen/   # task_packet.json · instructions.md ·
    |               # workspace_snapshot.json · finalize_result.json
    `-- repairs/attempt_NN/  # re-prepared packet for repair attempt N
```

The engine-native UI inside `generated_ui/` keeps its Engine's own layout —
for example `Plugins/GameUI/` in Unreal, `Assets/UI/` in Unity,
`addons/game_ui/` in Godot, or `src/components/` in Three.js — so upper layers
must not assume Unreal. Framework Browser Serving code is never copied into a
game artifact. Keep `Binaries/`, `Intermediate/`, `Saved/`, Derived Data
Cache, `__pycache__/`, and other mutable output out of the workspace.

`meta.json`, `demo_outputs/`, and `evaluation/` are reserved: never create,
modify, or delete them. `workspace_snapshot.json` records the SHA256 and size
of every workspace file, split into `task_files` and `protected_files`;
finalization re-derives the manifest, rejects protected-path changes, and
reports the task-owned diff.

Published workspaces are immutable. A structured failure triggers a repair:
the Pipeline re-prepares the same workspace with the failure digest and
previous result under `demo_outputs/repairs/attempt_NN/`, and the Agent
changes only UI-owned source, manifests, fixtures, and tests. A fresh start
uses a new `run_id`; it never overwrites a published run. Evaluation scores
the finalized workspace at `eval/ui/<task_id>/` and records `metrics.json`
there.

Finalization alone writes `ui_results_summary.json`, refreshes
`run_meta.json`, and repoints `latest`; only the Pipeline updates `meta.json`
status. A finalized workspace proves generation only — source generation,
static validation, or artifact-presence checks must not claim playability;
only execution and evaluation evidence may.

## Repair And Completion

For structured failures, identify the smallest UI-owned root cause and preserve
the canonical Engine, Mechanic contract/version, public-adapter-only access,
provenance, empty Browser Mechanic bindings, the registered backend dependency,
Browser backend/frontend separation, unrelated working behavior, and failure
evidence. Repair only UI-owned source, manifests, fixtures, and tests without
changing gameplay or weakening tests.

Report changed files, covered screens/criteria, consumed Mechanic bindings,
reference-image deviations, native/Browser test coverage, unresolved risks, and
missing inputs. Do not report authoritative rendering, build, test,
playability, or benchmark success.
