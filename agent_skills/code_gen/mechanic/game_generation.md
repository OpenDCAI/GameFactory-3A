# Game Mechanic Generation

Generate one task's game-owned Mechanic implementation inside the prepared
workspace. The result owns gameplay behavior and a presentation-independent
public contract; it does not own UI, execution, or evaluation.

## Authority And Inputs

Read before editing:

- the prepared task packet, task requirement, acceptance criteria, and optional
  general requirement;
- generated asset and motion descriptors;
- the Pipeline-owned canonical Engine identifier and read-only Engine Context;
- registered same-Engine Mechanic Example roots and task-suggested paths;
- this Skill and the referenced Prompts.

Authority order is: task packet and requirements, this Skill, the matching
Engine API, then Examples. Select exactly one non-empty Engine API matching the
canonical Engine. Do not change the Engine, mix APIs, or invent an API when the
matching document is missing.

Examples teach only same-Engine API usage, module/plugin structure, build
configuration, public-adapter design, and native-test patterns. Inspect the
smallest useful set, including at least one file or plugin directory below an
allowed root; never scan an entire root by default. An Example is not a base,
template, inheritance target, scaffold, runtime dependency, genre constraint,
or capability limit. Any same-Engine genre may teach structure.
No analogous Example is required.

## Workflow

1. Inspect the workspace and preserve compatible task-owned work.
2. Map every acceptance criterion to generated source, observable behavior,
   contract state/events/commands, and at least one meaningful native test.
3. Convert presentation wording into Mechanic signals. For example, a health
   bar requires current/maximum health state; a victory screen requires victory
   state/event; a restart button requires a restart command.
4. Read the matching Engine API and the minimum useful Example references.
   If the task includes audio, video CG, animation CG, or VFX triggers, use the
   matching Engine API's **Media Director** section.
5. Design a task-appropriate, internally modular architecture without using or
   modifying framework adapter internals.
6. Consume generated inputs only through supplied descriptors.
7. Generate gameplay source, build/configuration files, native tests, the
   public runtime adapter, and task-required launch/replay/trace source.
8. Publish `mechanic_contract.json` and `context_used.json` at the workspace
   root.
9. Review every acceptance criterion, deterministic/replay requirement,
   artifact, contract entry, and test before finalization.

## Scope And Architecture

One Mechanic task may implement a cohesive playable vertical slice containing
multiple systems absent from every Example, such as interaction, capture,
party, quest, inventory, dialogue, combat, and save-facing state. Keep complex
systems modular behind one public runtime adapter. Do not force an Example's
genre or architecture onto the task, and do not claim a commercial-scale game
when the acceptance criteria define a smaller vertical slice.

The dependency direction is:

```text
UI -> Mechanic -> runtime framework
```

The reverse dependency is forbidden. Mechanic must compile, test, and be
evaluated without a UI module, HUD, widget, menu, renderer, or screenshot.
Never generate UMG, Slate, Canvas, crosshairs, bars, telemetry, visual layout,
styling, feedback, or a concrete game HUD assignment.

## Mechanic Contract

Publish `mechanic_contract.json` with schema
`gamefactory3a.mechanic_contract.v1`.

| Field | Requirement |
|---|---|
| `contract_version` | Positive public contract revision |
| `gameplay_module` | Exact generated game-owned module name |
| `state` | Non-empty observable values exposed to UI |
| `events` | Non-empty gameplay transitions/notifications |
| `commands` | Non-empty actions UI or runtime may invoke |
| `public_api_paths` | Non-empty workspace-relative paths to generated adapter source |

Entries must represent real generated behavior, not placeholders. The adapter
must support state queries, event subscription, and command invocation without
exposing private Pawn, Character, Controller, or implementation types. Internal
systems may change without changing the UI-facing dependency direction.

## Outputs, Provenance, And Tests

Generate only task-owned artifacts:

- engine-native gameplay source and engine-native gameplay test source;
- public runtime-adapter source;
- `mechanic_contract.json` and `context_used.json`;
- required build/configuration and launch/replay/trace source.

Do not generate or modify prepared packets, workspace snapshots, `meta.json`,
`demo_outputs/`, evaluation artifacts, authoritative reports, benchmark
scores, or Pipeline result metadata.

`context_used.json` uses `gamefactory3a.context_used.v1` and must record:

- the repository-owned matching Engine API;
- only actually consulted paths below allowed same-Engine Example roots;
- Example role `mechanic_example`;
- at least one allowed engineering `purpose` per Example entry.

Do not record root-only access, unrelated context, cross-Engine paths, or other
tasks' generated outputs.

Generated tests are repair evidence, not benchmark authority. They must fail
when required behavior is absent or incorrect, exercise observable state
transitions and configured values, and provide useful diagnostics. Avoid empty
assertions, unconditional success, construction-only checks, and constant-only
checks. Never weaken, delete, skip, or replace a failing test to make a repair
appear successful.

## Boundaries And Ownership

- Write only inside the prepared workspace.
- Treat task inputs, descriptors, Skills, Prompts, Engine Context, Examples,
  and finalized upstream artifacts as read-only.
- Use only public APIs documented by the selected Engine API.
- For media integration, use the canonical `media_director` /
  `A3GameMediaDirector` naming contract and keep native file naming
  conventions; do not create parallel `MediaManager` or `CutsceneManager`
  surfaces.
- Do not import, copy, or modify adapter internals; invent asset paths; bypass
  descriptors; inherit Example gameplay classes; copy Example gameplay; or
  depend on Example plugins at runtime.
- Do not inspect, compare with, copy, or adapt generated implementation from
  other tasks or games under `<REPO_PATH>/test_data/outputs/` or a relocated output root.
- Do not make Mechanic depend on UI or expose UI-facing state through casts to
  private/incidental runtime types.
- Do not invoke execution/evaluation-only APIs, run authoritative tests, launch
  the Engine, assign a benchmark score, or claim build/playability success.

Ownership is separated:

- the Agent owns game-owned Mechanic source, generated tests, contracts, and
  repair changes;
- the Code Generation Pipeline owns task/context composition, Prompt rendering,
  boundaries, packets, snapshots, finalization, and metadata;
- execution/evaluation owns Engine preparation, asset import, authoritative
  builds/tests, runtime evidence, screenshots, and benchmark scoring.

For later asset import, execution must resolve supplied descriptors through the
selected public Engine API, reuse one configured Engine client/session for the
task, check readiness at the session boundary, and preserve structured results
and logs. A repository launcher may manage lifecycle only when the Engine API
documents it. Import or map-load success alone is not proof of playability.

## Run And Publication Contract

A run is the smallest reproducible publication unit:

```text
Task Packet -> Mechanic Generation -> Finalized Mechanic Workspace
            -> Evaluation
```

Every artifact is addressed by `(game_id, run_id, task_kind, task_id)` and
lands in the shared per-game output tree described by
`<REPO_PATH>/test_data/outputs/README.md`:

```text
test_data/outputs/<game_id>/
|-- latest -> <run_id>/                 # symlink to the most recent run
`-- <run_id>/
    |-- run_meta.json                   # run provenance, Pipeline-owned
    |-- mechanic_results_summary.json   # Pipeline-owned
    |-- mechanic/<task_id>/             # the prepared Mechanic workspace
    `-- eval/mechanic/<task_id>/        # metrics.json, Pipeline-owned
```

`<REPO_PATH>/pipeline/common/paths.py` owns these paths; do not construct them
manually. The Pipeline prepares `mechanic/<task_id>/` and hands the Agent its
path; the Agent writes only inside that workspace.

The Mechanic workspace is:

```text
mechanic/<task_id>/
|-- meta.json                # Pipeline-owned identity and status
|-- mechanic_contract.json   # published by the Agent at the workspace root
|-- context_used.json        # published by the Agent at the workspace root
|-- launch*                  # task-required launch/replay/trace source
|-- project/                 # engine-native gameplay source, adapter, tests
`-- demo_outputs/            # Pipeline-owned, reserved
    |-- code_gen/            # task_packet.json · instructions.md ·
    |                        # workspace_snapshot.json · finalize_result.json
    `-- repairs/attempt_NN/  # re-prepared packet for repair attempt N
```

The engine-native project keeps its Engine's own layout — for example
`Plugins/GameMechanic/` in Unreal, `Assets/Mechanics/` in Unity,
`addons/game_mechanic/` in Godot, or `src/mechanics/` in Three.js — so upper
layers must not assume Unreal. Keep `Binaries/`, `Intermediate/`, `Saved/`,
Derived Data Cache, `__pycache__/`, and other mutable engine output out of
the workspace.

`meta.json`, `demo_outputs/`, and `evaluation/` are reserved: never create,
modify, or delete them. `workspace_snapshot.json` records the SHA256 and size
of every workspace file, split into `task_files` and `protected_files`;
finalization re-derives the manifest, rejects protected-path changes, and
reports the task-owned diff.

Published workspaces are immutable. A structured failure triggers a repair:
the Pipeline re-prepares the same workspace with the failure digest and
previous result under `demo_outputs/repairs/attempt_NN/`, and the Agent
changes only game-owned source and tests. A fresh start uses a new `run_id`;
it never overwrites a published run. Evaluation scores the finalized workspace
at `eval/mechanic/<task_id>/` and records `metrics.json` there.

Finalization alone writes `mechanic_results_summary.json`, refreshes
`run_meta.json`, and repoints `latest`; only the Pipeline updates `meta.json`
status. A finalized workspace proves generation only — static generation or
artifact-presence checks must not claim playability; only execution and
evaluation evidence may.

## Repair And Completion

For structured failures, identify the smallest root cause, modify only
game-owned source/tests, preserve the canonical Engine, contract, provenance,
unrelated working behavior, and failure evidence, and do not weaken tests.

Report changed files, acceptance-criteria coverage, generated-test coverage,
unresolved risks, and missing inputs. Return source changes and diagnostics;
Do not report authoritative build, test, playability, or benchmark success.
