# UI Repair Task

Workspace:

```text
{{WORKSPACE}}
```

Repair attempt:

```text
{{REPAIR_ATTEMPT}} / {{MAX_REPAIR_ATTEMPTS}}
```

Structured execution or evaluation failures:

```json
{{FAILURES_JSON}}
```

Previous finalized result:

```json
{{PREVIOUS_RESULT_JSON}}
```

Use the failures and existing workspace to identify the smallest UI-owned root
cause. Repair generated UI source, resources, bindings, layout, interaction,
and UI test source.

Requirements:

- preserve the prepared ordered delivery plan;
- preserve the Mechanic artifact and all read-only context;
- preserve the Mechanic contract version and declared engine-native
  state/event/command binding manifest;
- preserve empty Mechanic binding lists in the Browser Play delivery stage and
  do not consume the engine-native Mechanic fixture from Browser Play source;
- preserve the two Pipeline-selected API document paths;
- preserve public runtime-adapter-only Mechanic access and do not add direct
  gameplay class/property access;
- do not add gameplay HUD, gameplay state visualization, or Mechanic commands
  to Browser Play source;
- preserve unrelated working UI behavior and failure evidence;
- do not reimplement or modify Mechanic rules;
- do not add direct gameplay-class casts or private Mechanic dependencies;
- do not repair missing bindings by creating a local, empty, copied, stubbed,
  or fallback Mechanic interface;
- re-check the UI requirement, design document, general requirement,
  acceptance criteria, and supplied reference images before changing layout;
- keep mocked Mechanic fixtures consistent with the read-only contract;
- do not remove forbidden-UI checks or weaken failing tests;
- do not invoke execution or evaluation-only APIs;
- do not fabricate screenshots or declare success;
- report changed files, addressed failures, and unresolved issues.
