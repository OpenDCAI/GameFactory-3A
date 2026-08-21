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
Task Packet -> UI Generation -> Native UI And Browser Play
            -> Assembly -> Playable Product -> Evaluation
```

All run-owned data belongs under:

```text
test_data/outputs/<game_id>/runs/<run_id>/
|-- run.json
|-- inputs.lock.json
|-- artifacts/ui/<task_id>/
|-- products/<pipeline_task_id>/
|   `-- {native,browser_play,launch,assembly_manifest.json,product_manifest.json}
|-- evaluation/<pipeline_task_id>/
|   `-- {build,tests,screenshots,browser_smoke,logs,result.json}
`-- _pipeline/{packets,attempts,prompts,snapshots}/
```

`<REPO_PATH>/pipeline/common/paths.py` owns these paths; do not construct them manually.
Published runs are immutable. A content repair creates a new run and records
`parent_run_id`, `repair_of`, and the failure digest. Keep unpublished retries
under `_pipeline/attempts/` and promote only the selected attempt.

The published UI artifact is:

```text
artifacts/ui/<task_id>/
|-- native/
|-- browser_play/
|-- bindings/
|-- fixtures/
|-- tests/
|-- screenshot_plan.json
|-- context_used.json
`-- manifest.json
```

`native/` is the cross-Engine UI boundary: for example
`native/Plugins/GameUI/` in Unreal, `native/Assets/UI/` in Unity, or
`native/addons/game_ui/` in Godot, or `native/src/components/` in Three.js.
Upper layers must not assume Unreal.
`native/` and `browser_play/` are source artifacts; product copies are
read-only assembly output. Framework Browser Serving code is never copied into
a game artifact. Keep `Binaries/`, `Intermediate/`, `Saved/`, Derived Data
Cache, `__pycache__/`, and other mutable output under `.tmp`.

Every published artifact includes `manifest.json` using
`gamefactory3a.artifact_manifest.v1` with:

- `artifact_version`;
- identity: `game_id`, `run_id`, `task_kind=ui`, and `task_id`;
- artifact path, `tree_sha256`, and file count;
- producer `git_sha` and `packet_sha256`.

Keep schema, artifact, contract/binding, and content versions distinct.
Calculate `tree_sha256` from sorted POSIX-relative paths plus each file's
SHA256 and byte size, excluding the manifest and mutable output. Publish only
run-relative paths, never machine-local absolute paths.

Assembly must record and recalculate both finalized Mechanic and UI manifest
digests and `tree_sha256` values in an
`gamefactory3a.assembly_manifest.v1` manifest, fail on mismatch, and produce a
new assembly/product digest when Mechanic, native UI, or Browser Play source
changes. Evaluation must pin `subject.product_manifest` and
`subject.product_manifest_sha256`; native builds/tests, screenshots, logs, and
Browser smoke evidence apply only to that product.

Track separate status:

```json
{
  "generation_status": "generated",
  "assembly_status": "not_run",
  "verification_status": "not_run"
}
```

UI generation may set only generation status. Assembly alone sets `assembled`;
execution/evaluation alone sets `verified`. Source generation, static
validation, or artifact-presence checks must not claim playability.

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
