# UI Generation Task

Workspace:

```text
{{WORKSPACE}}
```

Project name:

```text
{{PROJECT_NAME}}
```

Game-owned Mechanic module:

```text
{{GAMEPLAY_MODULE_NAME}}
```

Generated UI module:

```text
{{UI_MODULE_NAME}}
```

UI target:

```text
{{UI_TARGET}}
```

Delivery mode:

```text
{{DELIVERY_MODE}}
```

Ordered delivery plan:

```json
{{DELIVERY_PLAN}}
```

Game UI Generation Skill:

```text
{{UI_GENERATION_SKILL_PATH}}
```

Prepared task packet:

```text
{{TASK_PACKET_PATH}}
```

Task:

```json
{{TASK_JSON}}
```

General game requirement:

```text
{{GENERAL_REQUIREMENT}}
```

Game design document:

```text
{{DESIGN_DOCUMENT}}
```

UI requirement:

```text
{{REQUIREMENT}}
```

Acceptance criteria:

```json
{{ACCEPTANCE_CRITERIA}}
```

Target Engine identifier:

```text
{{ENGINE}}
```

API Context targets:

```json
{{API_CONTEXT_TARGETS}}
```

Engine-native API Context target:

```text
{{ENGINE_API_CONTEXT_TARGET}}
```

Browser Play API Context target:

```text
{{BROWSER_API_CONTEXT_TARGET}}
```

Pipeline-selected API Contexts:

```json
{{SELECTED_API_CONTEXTS}}
```

Engine-native API document:

```text
{{ENGINE_API_CONTEXT_PATH}}
```

Browser Play API document:

```text
{{BROWSER_API_CONTEXT_PATH}}
```

Engine Context directory:

```text
{{ENGINE_CONTEXT_PATH}}
```

The Pipeline has already selected both API documents. Read the exact paths
above before implementing their stages. Do not select alternatives.

Delivery boundary:

- First, complete `engine_native`: generate the real Mechanic-facing
  engine-native UI. Read the generated Mechanic contract/Public paths and
  inspect every supplied engine Example plugin before implementation. Generate
  and review the native plugin and native UI tests before continuing.
- Second, complete `browser_play`: map the completed engine game into the Web
  Play surface
  through the selected Browser Serving API. Generate stream, assets, sessions,
  preview, Play, leave, stop, and generic input UI only. Do not duplicate the
  engine-native gameplay HUD in Web code.
- The `browser_play` entry in the delivery plan must contain empty Mechanic
  Public paths and empty state/event/command binding lists. Stop if it violates
  this rule.
- Do not finish the task after generating only the engine-native plugin.

Completed Mechanic artifact:

```text
{{MECHANIC_ARTIFACT_PATH}}
```

Mechanic metadata:

```json
{{MECHANIC_META_JSON}}
```

Mandatory Mechanic contract file:

```text
{{MECHANIC_CONTRACT_PATH}}
```

Mechanic contract:

```json
{{MECHANIC_CONTRACT_JSON}}
```

Mechanic contract-specific Public source paths (`engine_native` only):

```json
{{MECHANIC_PUBLIC_PATHS}}
```

Mechanic runtime adapter (`engine_native` only):

```json
{{MECHANIC_RUNTIME_ADAPTER}}
```

Stable state bindings (`engine_native` only):

```json
{{STATE_BINDINGS}}
```

Stable event bindings (`engine_native` only):

```json
{{EVENT_BINDINGS}}
```

Stable command bindings (`engine_native` only):

```json
{{COMMAND_BINDINGS}}
```

Resolved binding definitions (`engine_native` only):

```json
{{RESOLVED_BINDINGS}}
```

Required screens and states:

```json
{{SCREENS}}
```

View and layout constraints:

```json
{{VIEW_CONSTRAINTS}}
```

Required viewports:

```json
{{VIEWPORTS}}
```

Forbidden UI:

```json
{{FORBIDDEN_UI}}
```

Reference image directory:

```text
{{REFERENCE_IMAGE_DIR}}
```

Reference image files:

```json
{{REFERENCE_IMAGE_PATHS}}
```

Optional read-only examples:

```json
{{OPTIONAL_EXAMPLE_PATHS}}
```

For `engine_native`, these paths are not generic style inspiration. Inspect
the supplied matching Mechanic Example and UI Example code to learn the
selected engine's plugin/module structure, public contract binding, UI
lifecycle, and native test patterns. The finalized generated Mechanic remains
authoritative and Examples remain read-only.

Generate the complete game-owned UI delivery under `generated_ui/`. Include:

- engine-native plugin/source/resources and meaningful native UI tests;
- `generated_ui/ui_binding_manifest.json`;
- `generated_ui/Tests/fixtures/mechanic_contract_fixture.json`;
- Browser Play source/resources and Browser Serving API tests under
  `generated_ui/browser_play/`;
- `generated_ui/screenshot_plan.json`.

Use the Mechanic fixture only in engine-native binding/layout/interaction
tests. Browser Play tests must use Browser Serving API fixtures and must not
consume that Mechanic fixture. Browser Play must use the `browser_play` stage
of the delivery plan, where Mechanic bindings and Public paths are empty.

Read every supplied reference image before implementation and treat it as a
high-priority visual target. Satisfy the UI requirement, design document,
general requirement, acceptance criteria, screens, viewports, view
constraints, and forbidden UI.

Use only the declared state, event, and command bindings. Do not inspect
arbitrary Mechanic implementation source, cast to concrete gameplay classes,
dereference contract `source` metadata as a gameplay property path, or
reimplement gameplay behavior. Query state, subscribe to events, and invoke
commands only through the supplied public runtime adapter. Do not create an
empty or replacement Mechanic interface, duplicate contract, placeholder
provider, fallback state store, or runtime mock. Mocked contract data is
test-only.

Browser Play displays the running engine output with the completed
engine-native UI. Do not reproduce gameplay HUD, gameplay state visualization,
or Mechanic commands in HTML, CSS, or JavaScript.

Do not modify the Mechanic artifact. Do not fabricate screenshots. Real
rendering and screenshot capture belong to execution and evaluation.
