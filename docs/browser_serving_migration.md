# Browser Serving Migration

Date: 2026-08-06

## Scope

Migrated the browser-facing Serving implementation from:

```text
D:\Desktop\game\OpenWL_Avatar\serving
```

to:

```text
engine_adapters/browser_serving
```

The target is engine-agnostic. `UE5ExampleBackend` is only the first backend
example.

No README file was changed.

## Architecture

```text
7860 Admin / 7870 Player / BrowserServingClient
    -> public Browser Serving API
    -> EngineBackend
    -> public engine client
```

The UE example depends only on:

```python
from engine_adapters.ue5 import UEClient
```

Engine process, stream, import, World, and runtime behavior belongs in a
backend. Frontends use capabilities and do not branch on engine names.

## Migrated Surface

- FastAPI Gateway and player frontend;
- asset administration UI;
- Python HTTP SDK;
- engine capabilities, contracts, and registry;
- upload staging through standard AAAGameForge task artifacts;
- asset list, groups, inspection, and import;
- World build/publish and catalog;
- session create, recover, configure, preview, Play, leave, input, and stop;
- input WebSocket;
- Pixel Streaming signalling lifecycle;
- UE5 first backend example;
- ordered UI CodeGen Browser Play context;
- UI Skill/Prompt native-plugin-then-Browser-Play delivery rules.

OpenWL UE-private editor/services code and Blender runtime code were not copied
into Browser Serving.

## Important Behavior

- Browser Serving is only the engine-to-Web mapping layer.
- Real Mechanic-facing HUD/widgets/menus remain engine-native UI generated
  from the concrete Engine API, finalized Mechanic contract/Public source, and
  matching engine Examples.
- One UI task generates the engine-native plugin first and then generates its
  Browser Play mapping under `generated_ui/browser_play/`.
- The Agent selects both the concrete Engine API and Browser Serving API from
  the Engine Context directory. No concrete API document path is injected.
- The Browser Play delivery stage receives empty Mechanic bindings and Public
  paths.
- Browser uploads are staged as task artifacts before backend import.
- Game-specific commands remain Mechanic-owned.
- The current Serving API does not expose a versioned Mechanic bridge to Web.

## Changed Files

Implementation:

```text
engine_adapters/browser_serving/**
```

Agent context and UI generation:

```text
agent_skills/engine_context/browser_serving_api.md
agent_skills/code_gen/ui/**
pipeline/code_gen/gen_ui/run.py
test_data/test_samples/gameA_cyberpunk_shooter/ui/ui_tasks.jsonl
test_data/test_samples/gameB_stylized_countryside_drive/ui/ui_tasks.jsonl
test_data/test_samples/ui_collect.jsonl
```

Tests:

```text
test/test_browser_serving.py
test/test_browser_serving_client.py
test/test_browser_serving_api_documentation.py
test/test_code_gen_ui.py
```

Durable state:

```text
.codex/memory.md
.codex/state.md
.codex/changelog.md
.codex/decisions.md
.codex/issues.md
.codex/next_tasks.md
```

## Validation

- 37 focused Browser Serving/UI tests passed.
- UI delivery-boundary and documentation tests passed.
- Python compile, JavaScript syntax, and scoped diff checks passed.
- 7860 and 7870 started successfully in dry-run mode.
- Desktop browser pages rendered successfully.
- Session create, query, and stop passed.

Dry-run validation proves Serving/frontend lifecycle, not real UE rendering.

The later UI CodeGen delivery-plan update passed an additional 42 focused
Mechanic/UI/API-documentation checks. Real combined
Mechanic + native UI + Browser Play + Pixel Streaming validation remains
pending.

Broader UE discovery has 3 unrelated stale assertions that still require
`AHUD` inside Mechanic Example plugins after the Mechanic/UI split.
