# models/

Thin wrappers around individual generation models. **One file per model.**

Each wrapper should expose a uniform interface (e.g., `load()`, `infer()`,
`unload()`) so operators can swap backends without knowing implementation details.

Two contracts apply, both in `agent_skills/develop_harness/`:
`model_require.md` for local-weight models, plus `api_model_require.md` (R9) when
the model is a closed-source cloud API. Shared cloud plumbing (HTTP retry, error
classification, response cache, submit → poll → download) lives in
`models/common/cloud_api.py` — do not re-implement it per provider.

## Implemented wrappers

| Slot | Class | File | Kind | Needs |
|------|-------|------|------|-------|
| `gen_3d_object` | `Trellis2Model` | `gen_3d_object/trellis_2_model.py` | local weights | GPU + the o-voxel extension |
| `gen_3d_object` | `TripoModel` | `gen_3d_object/tripo_model.py` | cloud API | `$TRIPO_API_KEY` + `scripts/installing/cloud_api_install.sh` |
| `gen_3d_object` | `MeshyModel` | `gen_3d_object/meshy_model.py` | cloud API | `$MESHY_API_KEY` + `scripts/installing/cloud_api_install.sh` |
| `gen_3d_scene` | `WorldMirrorModel` | `gen_3d_scene/world_mirror_model.py` | local weights | GPU |
| `gen_3d_scene` | `WorldPlayModel` | `gen_3d_scene/world_play_model.py` | local weights | GPU + a checkout of HY-WorldPlay |
| `gen_image` | `QwenEditModel` | `gen_image/qwen_edit.py` | local weights | GPU |
| `tools/image_matting` | `RMBGModel`, `DepthAnythingModel` | `tools/image_matting/` | local weights | — |
| `tools/segmentation` | `SkySegmentationModel` | `tools/segmentation/sky.py` | local weights | `onnxruntime` (CPU is fine) |

All three `gen_3d_object` backends expose the same
`infer_and_save(image, output_path, seed, decimation_target, texture_size)`, so
`Gen3DObjectOperator` swaps between them without changing (R6). Pick one with
`python pipeline/assets_gen/gen_3d_object/run.py --backend {trellis2,tripo,meshy}`.

| | Tripo | Meshy |
|---|---|---|
| free tier | 2000 credits on sign-up | 100 credits / month |
| formats | GLB (conversion endpoint not wired) | GLB, FBX, OBJ, USDZ, STL |
| text-to-3D | one task | preview + refine (two billed tasks) |
| low poly | `smart_low_poly`, P-series models | `model_type="lowpoly"` |
| face budget | `face_limit` | `target_polycount`, 100-300 000 |

The two `gen_3d_scene` wrappers chain rather than substitute for each other.
`WorldPlayModel` flies a camera through a reference image to produce frames and
`WorldMirrorModel` reconstructs geometry from frames, so `Gen3DSceneOperator`
takes them in separate slots. Only the geometry slot is required — a task that
already has footage, or that is content with what a single view can see, needs
no world model at all. `world_mirror_utils/` holds the vendored HunyuanWorldMirror
source that backs the geometry wrapper; see its README for what was changed.

`SkySegmentationModel` is a third, smaller piece of the same chain. Depth heads
cannot express "infinitely far", so they place sky at a finite depth that no
threshold separates from real surface, and it gets meshed into a curtain over
the scene. Only segmentation finds it.

## Sub-modules

| Directory        | Purpose                              | Candidate models |
|------------------|--------------------------------------|------------------|
| `gen_3d_object/` | Single 3D asset generation           | TRELLIS.2, Hunyuan3D-2.1, TripoSG, Step1X-3D, Direct3D-S2, Craftsman3D, Michelangelo, Meshy, Tripo, Rodin, CSM, Luma Genie |
| `gen_3d_scene/`  | Whole-scene / world generation       | Hunyuan-WorldPlay2, FlashWorld, FantasyWorld |
| `gen_motion/`    | Text-to-motion                       | MoMask, MDM, MLD, T2M-GPT, MotionGPT |
| `gen_cg_video/`  | Cinematic / CG video generation      | LTX-2.3, HunyuanVideo, Wan, Mochi, CogVideoX, Open-Sora, Seedance 2, Kling 3, Veo 3, Sora 2, Runway Gen-4, Hailuo, Vidu |
| `gen_audio/`     | Character voice, dialogue, and game sound generation | Future speech, voice, and sound-effect backends |
| `retarget/`      | Skeleton motion retargeter           | Keemap-based, IK-based, learning-based |
| `reasoning/`     | LLMs / VLMs used by the pipeline     | Claude, GPT-5.5, GLM, Kimi, DeepSeek, Gemini, Qwen, Grok, Llama, Mistral |
| `tools/`         | Utility models (depth, RMBG, seg.)   | Depth-Anything, RMBG, SAM, etc.       |
| `unified_model/` | Composite / multimodal pipelines     | e.g., end-to-end asset+motion models  |
