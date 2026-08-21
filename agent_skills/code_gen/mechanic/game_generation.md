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
Task Packet -> Mechanic Generation -> Mechanic Artifact
            -> Assembly -> Playable Product -> Evaluation
```

All run-owned data belongs under:

```text
test_data/outputs/<game_id>/runs/<run_id>/
|-- run.json
|-- inputs.lock.json
|-- artifacts/mechanic/<task_id>/
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

The published Mechanic artifact is:

```text
artifacts/mechanic/<task_id>/
|-- native/
|-- contract/
|-- tests/
|-- traces/
|-- context_used.json
`-- manifest.json
```

`native/` is the cross-Engine boundary: for example
`native/Plugins/GameMechanic/` in Unreal,
`native/Assets/Mechanics/` in Unity,
`native/addons/game_mechanic/` in Godot, or
`native/src/mechanics/` in Three.js. Upper layers must not assume Unreal.
`native/` is the source of truth; product copies are read-only assembly output.
Keep `Binaries/`, `Intermediate/`, `Saved/`, Derived Data Cache,
`__pycache__/`, and other mutable output under `.tmp`.

Every published artifact includes `manifest.json` using
`gamefactory3a.artifact_manifest.v1` with:

- `artifact_version`;
- identity: `game_id`, `run_id`, `task_kind=mechanic`, and `task_id`;
- artifact path, `tree_sha256`, and file count;
- producer `git_sha` and `packet_sha256`.

Keep schema, artifact, public contract, and content versions distinct.
Calculate `tree_sha256` from sorted POSIX-relative paths plus each file's
SHA256 and byte size, excluding the manifest and mutable output. Publish only
run-relative paths, never machine-local absolute paths.

Assembly must record and recalculate the Mechanic manifest digest and
`tree_sha256` in an `gamefactory3a.assembly_manifest.v1` manifest, fail on
mismatch, and produce a new assembly/product digest when source changes.
Evaluation must pin `subject.product_manifest` and
`subject.product_manifest_sha256`; builds, tests, screenshots, logs, and
Browser Play evidence apply only to that product.

Track separate status:

```json
{
  "generation_status": "generated",
  "assembly_status": "not_run",
  "verification_status": "not_run"
}
```

Mechanic generation may set only generation status. Assembly alone sets
`assembled`; execution/evaluation alone sets `verified`. Static generation or
artifact-presence checks must not claim playability.

## Repair And Completion

For structured failures, identify the smallest root cause, modify only
game-owned source/tests, preserve the canonical Engine, contract, provenance,
unrelated working behavior, and failure evidence, and do not weaken tests.

Report changed files, acceptance-criteria coverage, generated-test coverage,
unresolved risks, and missing inputs. Return source changes and diagnostics;
Do not report authoritative build, test, playability, or benchmark success.
