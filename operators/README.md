# operators/

Task **operators** — one per WorldFlex-GameBenchmark task type.

Each operator directory has a uniform layout:

```
operators/<task>/
├── __init__.py
├── operator.py       # Top-level class, e.g. Gen3DObjectOperator
├── funcs/            # Decoupled steps (one file per logical function)
└── metrics/          # Task-specific evaluation code
```

`metrics/` is co-located with each operator on purpose — the evaluation logic
is tightly coupled to that operator's outputs (e.g., CG needs temporal-consistency
metrics; 3D-object needs Chamfer + PBR checks; retarget needs foot-skate + jerk).

## Operators

| Operator         | Layer | Description                                                | Typical metrics                                |
|------------------|-------|------------------------------------------------------------|------------------------------------------------|
| `process_input`  | pre   | Parse text, preprocess image, extract character            | schema-conformance                             |
| `gen_3d_object`  | A     | Generate a single 3D asset from image / text               | Chamfer, CLIP, tri-count, PBR completeness     |
| `gen_3d_scene`   | A     | Reconstruct a whole 3D scene from a reference image or footage | boundary-edge ratio, largest-component share, stretch p99 |
| `gen_motion`     | A     | Generate skeletal animation                                | FID-motion, foot-skate, jerk, loop continuity  |
| `gen_cg_video`   | A     | Generate CG / cutscene video                               | temporal consistency, optical-flow, CLIP       |
| `gen_audio`      | A     | Generate character dialogue and game sound effects          | intelligibility, prompt alignment, fidelity, loudness |
| `retarget`       | A     | Retarget motion between skeletons                          | foot-skate, hand-drift, source-timing preservation |
| `gen_mechanic`   | B     | Generate mechanic code for UE5 / Unity3D                   | build-ok, trace-replay, rubric-judge           |
| `gen_ui`         | C     | Generate front-end / HUD code                              | resolution robustness, navigability, rubric-judge |

## Note on `gen_3d_scene`

Its `funcs/` split exists to fix the perforated meshes the upstream
HunyuanWorldMirror code produces. Upstream answers "is this pixel valid?" and
"is this pixel on a discontinuity?" with the same deletion, which punches holes
through solid surfaces because a discontinuity belongs to the boundary *between*
two pixels, not to either one. So `scene_mask.py` only judges pixels and
`points_to_mesh.py` only judges faces, using a tangent-plane test that keeps a
ground plane receding at a grazing angle intact while still cutting where a
foreground silhouette meets the background. On real reference images that lifts
retained coverage from roughly 82% of pixels to over 99%.

Two artefacts survive that and are handled separately, because neither is a
continuity problem. Sky is predicted at a finite depth and has to be segmented
out by the operator's `sky_model`. And where the depth head blurs an occlusion
boundary into a ramp, the sheet spanning it passes every per-edge test; it is
caught instead by comparing each face's own normal against the ones it inherits.

`gen_3d_scene`'s entry point is `hunyuan_worldplay_operator.py` rather than
`operator.py`, since the directory hosts two Hunyuan backends. Three pipeline
tools inspect its output without needing weights or a GPU:

| Command | What it tells you |
|---|---|
| `hunyuan_worldplay_eval.py --self-check` | Meshes synthetic scenes with known answers, against upstream's filtering |
| `hunyuan_worldplay_eval.py --contract-check` | Whether `WorldPlayModel`'s calls still match the HY-WorldPlay checkout |
| `hunyuan_worldplay_render.py scene.glb` | Rasterises the mesh offscreen, so holes and rubber sheets are visible |
