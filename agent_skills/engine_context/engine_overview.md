# Engine Context Routing

Read this file after `<REPO_PATH>/agent_skills/setting_overview.md` routes a task into Engine
game generation. The setting overview remains authoritative for the overall
game workflow. This file defines only CodeGen-to-Engine reading order and
cross-layer boundaries; exact workflows belong in the selected CodeGen Skill,
and exact APIs belong in the selected Engine API document.

Paths are written from the repository root as `<REPO_PATH>/...`; see the Path
convention section of `<REPO_PATH>/agent_skills/setting_overview.md`. Note that
`<REPO_PATH>/engine_adapters/` is a sibling of `<REPO_PATH>/agent_skills/`, not a
subdirectory of it.

## Read In This Order

1. Read `<REPO_PATH>/agent_skills/setting_overview.md`, the task packet, requirements, and
   acceptance criteria. The task packet owns the canonical Engine and filesystem
   boundaries.
2. Read this file to select the CodeGen route without opening every Engine API.
3. Read the task-specific Mechanic or UI Skill to establish workflow, ownership,
   required artifacts, and validation rules.
4. Read only the API document for the canonical Engine to establish the public
   host and Engine-runtime implementation boundary.
5. Read optional context only when the task requires it.
6. Read finalized upstream contracts and the minimum relevant Example files
   allowed by the task-specific Skill.

Do not preload or combine unrelated Skills, Engine APIs, Examples, or generated
outputs.

## Task Routing

| Task | Required context |
|---|---|
| Mechanic generation | `<REPO_PATH>/agent_skills/code_gen/mechanic/game_generation.md` -> selected Engine API |
| UI generation | `<REPO_PATH>/agent_skills/code_gen/ui/game_ui_generation.md` -> selected Engine API -> `<REPO_PATH>/agent_skills/engine_context/browser_serving_api.md` |
| Engine assembly, build, test, or runtime | selected Engine API |
| Browser delivery | `<REPO_PATH>/agent_skills/engine_context/browser_serving_api.md` + selected Engine API |
| VFX creation | `<REPO_PATH>/agent_skills/engine_context/create-vfx-effects/SKILL.md` + selected Engine API |
| Retargeting, rigging, mesh repair, or neutral asset preparation | `<REPO_PATH>/agent_skills/engine_context/blender_api.md` |

## Engine Selection

The task packet owns the canonical Engine identifier. Select exactly one primary
Engine Context:

| Identifier | API document | Public host entry point |
|---|---|---|
| `ue5` | `<REPO_PATH>/agent_skills/engine_context/ue5_api.md` | `from engine_adapters.ue5 import UEClient` |
| `unity3d` | `<REPO_PATH>/agent_skills/engine_context/unity3d_api.md` | `from engine_adapters.unity3d import UnityClient` |
| `godot` | `<REPO_PATH>/agent_skills/engine_context/godot_api.md` | `from engine_adapters.godot import GodotClient` |
| `three_js` | `<REPO_PATH>/agent_skills/engine_context/three_js_api.md` | `from engine_adapters.three_js import ThreeClient` |
| `blender` | `<REPO_PATH>/agent_skills/engine_context/blender_api.md` | documented `bpy` interpreter boundary |

When `blender` is selected, it is a neutral asset-processing context rather than
a shipped game runtime. Do not mix primary Engine APIs or Examples. Browser
Serving and VFX are supplemental contexts selected only when the task requires
them.

## Required Dependency Direction

```text
Task packet
  -> Mechanic generation and public Mechanic contract
  -> UI generation and contract bindings
  -> Execution / Assembly through the selected public Engine Client
  -> Browser Serving when browser delivery is required
```

Mechanic is finalized before UI. The native Engine product is prepared and
running before Browser Serving publishes a playable URL.

## Layer Ownership

| Layer | Owns | Must not own |
|---|---|---|
| Mechanic | gameplay rules, simulation, state, events, commands, native gameplay plugin, public Mechanic contract | UI, browser delivery, asset import, build, tests, runtime launch |
| UI | native Engine UI, Mechanic contract bindings, Browser Play delivery source | gameplay rules, duplicate gameplay state, Engine backend, asset import, build |
| Execution / Assembly | project preparation, descriptor resolution, plugin installation, import, build, tests, runtime evidence, product assembly | generated gameplay rules, private Engine internals, replacement import/build paths |
| Browser Serving | browser session, stream, generic input, registered backend lifecycle | gameplay rules, native UI duplication, game-specific browser commands |

## Public API Boundary

- For Client-backed target Engines, all host-side project, import, binding,
  build, test, Editor, runtime, World, and session operations go through the
  public Client named by the selected Engine API.
- Generated native Engine code uses only the native public boundary documented
  by the selected Engine API. It does not call the host-side Python Client.
- Exact capabilities and call signatures come from the selected API document.
  Do not invent methods or infer private behavior.
- Do not import adapter internals, call private transports, launch Engine
  binaries directly, or create parallel import/build/runtime implementations.
- Shell scripts may be human or CI entry points only when they delegate to the
  same public Client path.
- Reuse one configured Client and Engine session for a task batch when the
  selected API supports it.
- Blender code may import `bpy` only inside the interpreter boundary documented
  by `blender_api.md`; ordinary host code uses its documented adapter path.
- If a required capability is absent, stop and report a public API gap. Extend
  the owning adapter contract before generated game code depends on it.

## Browser Boundary

Browser Play uses only the public Browser Serving API. It must not import an
Engine Client, construct a concrete backend, branch on Engine names, duplicate
native Engine UI or gameplay state, or derive stream URLs from private rules.

A registered Browser backend may call the selected public Engine Client. Do not
publish a browser URL until the Engine runtime is ready.

## Artifact And Example Rules

- Examples are read-only references, not base projects, templates, runtime
  dependencies, or limits on generated features.
- Read only finalized upstream artifacts declared by the task packet.
- Use `<REPO_PATH>/pipeline/common/paths.py` for repository output paths.
- Record actual context use in `context_used.json` when the selected Skill
  requires it.
- Generation, assembly, execution, and evaluation have separate ownership.
  Source generation alone must not claim that a game is playable.

## Stop Conditions

Stop and report the violation or capability gap when an implementation:

- mixes APIs or Examples from different target Engines;
- places UI or execution responsibilities in Mechanic code;
- places gameplay rules or duplicate state in UI or Browser Play code;
- bypasses the selected public Client or documented Blender boundary;
- copies an Engine Example into a generated project as a runtime dependency;
- exposes a browser URL before runtime readiness; or
- claims build, test, runtime, or playability success without evidence owned by
  the corresponding execution or evaluation stage.
