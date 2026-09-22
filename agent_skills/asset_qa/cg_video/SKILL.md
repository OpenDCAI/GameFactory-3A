# CG-video generation and QA Skill

Use this Skill for **text-to-video, first-frame-to-video, first/last-frame
transitions, and reference-image-conditioned CG-video generation**. Generate
video only after the game plan defines its narrative purpose, visual style,
shot, duration, and acceptance criteria.

## Chain, boundaries, and artifacts

```text
Game plan → game-cg-director → cg_tasks.jsonl
→ GenCGVideoOperator → VideoGenerationInput → video backend
→ MP4 bytes → video.mp4 + meta.json
```

| Layer | Location | Responsibility |
|---|---|---|
| Model | `<REPO_PATH>/models/gen_cg_video/` | Model-native inference, cloud transport, and lifecycle |
| Operator | `<REPO_PATH>/operators/gen_cg_video/` | Task fields, local image loading, artifact paths, and metadata |
| Pipeline | `<REPO_PATH>/pipeline/assets_gen/gen_cg_video/` | Backend selection, CLI, JSONL batches, and summaries |
| Harness | `<REPO_PATH>/test/harness/` | CPU-only, network-free chain validation |
| Director sub-Skill | `<REPO_PATH>/agent_skills/asset_qa/cg_video/game-cg-director/` | Model-specific storyboard prompts and validated task rows |

Paths in this Skill are written from the repository root. Resolve every
`<REPO_PATH>/...` from there, and run every `bash` and `python` command below
with `<REPO_PATH>/` as the working directory.

Standard artifact layout:

```text
test_data/outputs/<game_id>/<run_id>/assets/cg_video/<task_id>/
├── video.mp4
└── meta.json
```

A game that plays a clip through the Browser Serving gateway loads it by task
identity at play time, and that request serves a stored artifact rather than
creating one. Generate every clip named in the game plan during production, and
run the play-time gateway with `A3GAME_BROWSER_CG_VIDEO_PREBUILT_ONLY=1`, so a
clip that was never generated is reported as failed instead of being generated
inside a page load. Where the served bytes have to stay fixed, pin the producing
`run_id`: an unpinned lookup answers with the newest matching artifact.

Do not pass local image paths into a model directly. The task/JSONL uses local
paths; the operator resolves them and converts images to `PIL.Image.Image`.
Python callers with images already in memory may pass image objects, but never
provide both an image object and its corresponding path field.

## Directing sub-Skill and Harness handoff

Use `<REPO_PATH>/agent_skills/asset_qa/cg_video/game-cg-director/SKILL.md` when
the approved game plan defines the purpose of a CG clip but does not yet provide
a model-ready prompt. It is a child capability of this Skill, not an alternative
generation backend. The child creates and validates one directing envelope per
output clip and never invokes a model.

Read it and the files it selects, then validate every envelope it hands back
before that row is used — a non-zero exit is blocking:

```bash
python3 <REPO_PATH>/agent_skills/asset_qa/cg_video/game-cg-director/scripts/validate_output.py <envelope.json>
```

Validation is the only check between a prompt and a paid generation. The
pipeline and the Browser Serving gateway accept any row whose execution fields
are well-formed, so an envelope that never passed the validator reaches a
backend unchallenged.

Select the input workspace before calling the child:

```text
test_data/test_samples/<game_id>/cg_video/
├── requirement.txt
├── ref_images/                  # optional
├── ref_videos/                  # optional
├── ref_audio/                   # optional
└── cg_tasks.jsonl
```

Pass the child the selected `game_id`, one of `opening`, `cutscene`, `ultimate`,
or `promo`, the planned action and acceptance criteria, optional reference
roles and paths, model choice, duration, aspect ratio, seed, and task ID. For a
sequence requiring several independent generations, call the child once per
clip and preserve continuity anchors across the envelopes.

The child validates its directing envelope, adds the selected workspace
identity, and writes the complete object as one compact line directly to
`cg_tasks.jsonl`:

```text
game_id                                      ← selected workspace
task_id, mode, model, scene
duration_sec, aspect_ratio, prompt, meta
seed                                         ← when present
first_frame_path                             ← first-frame mode only
last_frame_path                              ← first/last-frame mode only
reference_image_paths                        ← reference mode when present
reference_video_paths, reference_audio_paths ← reference mode when present
```

The current Operator reads only its supported execution fields and safely
ignores the remaining directing metadata. Do not weaken the task row because
one backend supports fewer inputs. Preserve existing JSONL rows and replace a
matching `task_id` only for an explicit revision.

The Runner selects model and aspect ratio per process. Invoke one task at a time
when a JSONL contains mixed execution configurations:

| Envelope model | Runner selection |
|---|---|
| `h3` | `--backend minimax-h3 --minimax-runtime local --ckpt Comfy-Org/MiniMax-H3` |
| `seedance` | `--backend seedance` |

Pass Seedance aspect ratio with `--ratio`. For local H3, translate the ratio to
an explicitly selected `--width` and `--height` supported by the installation;
do not silently ignore the envelope value. Then run the standard pipeline with
an explicit task file and run ID:

```bash
python pipeline/assets_gen/gen_cg_video/run.py \
  --tasks test_data/test_samples/<game_id>/cg_video/cg_tasks.jsonl \
  --task-id <task_id> \
  --run-id <run_id> \
  <backend-and-format-options>
```

Video and audio references may be authored now, but the current Operator blocks
their execution until future support is implemented. The current pipeline
produces one `video.mp4` per task; assembling several clips into one edited
master is a separate post-production step.

## Shared modes

| Mode | Required image input | Use |
|---|---|---|
| `text_to_video` | none | Create a shot from a text prompt |
| `first_frame_to_video` | `first_frame` | Animate a planned opening composition |
| `first_last_frame_to_video` | `first_frame`, `last_frame` | Create a controlled keyframe-to-keyframe transition |
| `reference_to_video` | one or more ordered `reference_images` | Preserve character/environment visual references |

Reference-image order is meaningful. State the role of each image in the prompt
when composition depends on it. A backend rejects unsupported mode/backend pairs
rather than silently routing the request to a different model.

## Backend selection

| Backend/runtime | Text | First frame | First + last | Reference images | Best fit |
|---|---:|---:|---:|---:|---|
| Seedance 2.0 cloud API | yes | yes | yes | yes | High-quality cloud generation across all shared modes |
| MiniMax Hailuo 2.3 API | yes | yes | no | no | Cloud T2V and I2V only |
| MiniMax H3 local / ComfyUI | yes | yes | yes | yes | Local controlled generation with substantial hardware and storage |

Choose the route deliberately. Cloud calls may cost credits; the local MiniMax
path needs a capable NVIDIA GPU, a compatible CUDA/PyTorch stack, large RAM/VRAM,
and approximately 40 GiB free cache space for common modes (about 60 GiB if
reference-to-video is also required).

**Prefer the cloud APIs — Seedance 2.0, then MiniMax Hailuo 2.3** — with local
MiniMax H3 as the fallback for a declined budget or an offline requirement. Video
is the most expensive asset type here and is billed per generated second, so
before the first call **pause and follow *Paid cloud backend* in
`<REPO_PATH>/agent_skills/asset_qa/README.md`**: send the purchase/API-key page
(<https://console.volcengine.com/ark> for Seedance,
<https://platform.minimax.io/user-center/basic-information/interface-key> for
MiniMax), state the estimated cost as clips × duration × resolution including
retries, ask the user to buy access and supply `ARK_API_KEY` or
`MINIMAX_API_KEY`, and wait for an explicit answer.

## Common CLI usage

Text to video:

```bash
python pipeline/assets_gen/gen_cg_video/run.py \
  --backend seedance \
  --prompt "A paper dragon flies over misty mountains." \
  --mode text_to_video \
  --duration-sec 5 \
  --task-id paper_dragon \
  --game gameA_cyberpunk_shooter \
  --run-id auto \
  --cache-dir test_data/outputs/_api_cache
```

Frame and reference modes:

```bash
# First frame
python pipeline/assets_gen/gen_cg_video/run.py \
  --backend seedance \
  --mode first_frame_to_video \
  --first-frame /absolute/path/first.png \
  --prompt "The character walks forward." \
  --duration-sec 5

# First and last frame
python pipeline/assets_gen/gen_cg_video/run.py \
  --backend seedance \
  --mode first_last_frame_to_video \
  --first-frame /absolute/path/first.png \
  --last-frame /absolute/path/last.png \
  --prompt "Transition naturally between the two frames." \
  --duration-sec 5

# Ordered references; repeat the flag
python pipeline/assets_gen/gen_cg_video/run.py \
  --backend seedance \
  --mode reference_to_video \
  --reference-image /absolute/path/character.png \
  --reference-image /absolute/path/environment.png \
  --prompt "Keep the character and environment consistent." \
  --duration-sec 5
```

Batch mode:

```bash
python pipeline/assets_gen/gen_cg_video/run.py \
  --tasks /absolute/path/cg_tasks.jsonl \
  --run-id auto
```

Example JSONL row:

```json
{"game_id":"gameA_cyberpunk_shooter","task_id":"shot_001","mode":"first_frame_to_video","prompt":"The camera slowly moves closer.","duration_sec":5,"seed":42,"first_frame_path":"test_data/test_samples/gameA_cyberpunk_shooter/cg_video/ref_images/shot_001.png"}
```

Repository-relative image paths are resolved through `pipeline.common.paths`,
not the shell working directory.

## Seedance 2.0 cloud API

Install the shared dependency and set credentials:

```bash
bash scripts/asset_env_setup/cg_video/cloud_api_install.sh
export ARK_API_KEY="your-api-key"
export GAMEFACTORY3A_API_CACHE=test_data/outputs/_api_cache
```

Optional configuration:

```bash
export ARK_API_BASE=https://ark.cn-beijing.volces.com/api/v3
export SEEDANCE_MODEL=doubao-seedance-2-0-260128
export SEEDANCE_RESOLUTION=720p
export SEEDANCE_RATIO=16:9
export SEEDANCE_GENERATE_AUDIO=1
export SEEDANCE_WATERMARK=0
export SEEDANCE_TASK_TIMEOUT=1800
export SEEDANCE_POLL_INTERVAL=3
export SEEDANCE_MAX_RETRIES=3
```

`ARK_API_KEY` is required on the first non-cached request. An Ark endpoint ID is
not an API key. Never place keys in JSONL, metadata, cache keys, or committed
command examples. The shared cache key includes model, mode, prompt, image
hashes, and generation parameters, but excludes API keys and raw image bytes.

Useful explicit runner options:

```bash
python pipeline/assets_gen/gen_cg_video/run.py \
  --backend seedance \
  --ckpt doubao-seedance-2-0-260128 \
  --resolution 720p \
  --ratio 16:9 \
  --no-generate-audio \
  --no-watermark \
  --timeout 1800 \
  --poll-interval 3 \
  --max-retries 3 \
  --cache-dir test_data/outputs/_api_cache \
  --prompt "A cinematic establishing shot of a floating city."
```

## MiniMax H3 / Hailuo 2.3

### API runtime

Hailuo 2.3 supports `text_to_video` and `first_frame_to_video` only.

```bash
bash scripts/asset_env_setup/cg_video/cloud_api_install.sh
export MINIMAX_API_KEY="your-key"

python pipeline/assets_gen/gen_cg_video/run.py \
  --backend minimax-h3 \
  --minimax-runtime api \
  --mode text_to_video \
  --prompt "A knight crosses a rain-soaked neon plaza." \
  --duration-sec 6 \
  --resolution 1080P
```

The Hailuo API supports 6 s or 10 s at 768P and 6 s at 1080P. It does not expose
a seed; the shared seed is accepted and recorded as ignored.

### Local MiniMax H3 runtime

Install the ComfyUI/checkpoint environment:

```bash
bash scripts/asset_env_setup/cg_video/minimax_h3_install.sh
```

Then choose the local runtime and a Hugging Face id or complete local directory:

```bash
python pipeline/assets_gen/gen_cg_video/run.py \
  --backend minimax-h3 \
  --minimax-runtime local \
  --ckpt Comfy-Org/MiniMax-H3 \
  --mode first_last_frame_to_video \
  --first-frame /data/first.png \
  --last-frame /data/last.png \
  --prompt "The camera completes a slow orbit." \
  --duration-sec 5
```

The local default is 864×480 at 24 fps. Both dimensions must be positive
multiples of 32. Common presets are 832×480 (480P), 1344×736 (720P),
1920×1088 (1080P/1K), and 2560×1440 (2K). Use
`MINIMAX_LOCAL_FILES_ONLY=1` only after a complete checkpoint is available.
Relevant controls include
`COMFYUI_PATH`, `HUGGINGFACE_HUB_CACHE`, `MINIMAX_WIDTH`, `MINIMAX_HEIGHT`,
`MINIMAX_FPS`, `MINIMAX_STEPS`, `MINIMAX_SCHEDULER`, and
`MINIMAX_REF_IMAGE_SIZE`. Keep downloaded checkpoints outside source control,
for example under `<REPO_PATH>/third_party/` or a configured Hugging Face cache.

## Tests, QA, and cost controls

Run a free contract check first:

```bash
python test/harness/smoke.py --kind cg_video --backend seedance
python test/harness/smoke.py --kind cg_video --backend minimax-h3
```

Use `<REPO_PATH>/test/test_cg_video_gen.py` for real API or local checkpoint generation only
after explicitly selecting backend, runtime, task file, output directory, and
cache. A paid Seedance example:

```bash
export ARK_API_KEY="your-api-key"
export CG_VIDEO_BACKEND=seedance
export CG_VIDEO_TEST_TASKS=/absolute/path/to/cg_tasks.jsonl
export CG_VIDEO_TEST_OUT_DIR=/absolute/path/to/output
export GAMEFACTORY3A_API_CACHE=/absolute/path/to/api_cache
python test/test_cg_video_gen.py
```

The test validates tasks before contacting a provider. Set
`CG_VIDEO_TEST_TASK_ID=<task_id>` to reproduce one row. Do not enable paid tests
in CI or run all modes just to validate a code change.

After generation, review the MP4 at normal speed and in the target game context:

1. verify prompt adherence, temporal consistency, character identity, and
   camera motion;
2. inspect for flicker, warped anatomy/props, impossible transitions, unreadable
   action, unwanted text/watermarks, or incorrect aspect ratio;
3. ensure the clip’s lighting, effects, composition, duration, and audio choice
   match the game’s requested style;
4. retain provider/model, prompt, mode, image rights, parameter set, cache state,
   and human or vision-review decision in metadata;
5. iterate with corrected frames, prompt, duration, or backend rather than
   accepting a visually broken video because the MP4 is valid.
