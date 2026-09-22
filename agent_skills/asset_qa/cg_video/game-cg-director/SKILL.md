---
name: game-cg-director
description: Convert game CG intent and optional media references into validated, model-specific directing envelopes for opening, cutscene, ultimate, or promo clips. Use for standalone CG prompt direction or as a child capability of a game-generation Harness. Supports T2VA/text-only, I2VA/first-frame image, FL2VA/first-and-last-frame images, and Ref2VA/image, video, or audio references; never calls a video-generation API itself.
---

# Game CG Director

Build one validated directing envelope per generated clip. For 3AGameFactory,
write it directly as a Harness task row; for standalone use, return the
envelope file. Never call a video-generation API or write generated media.

## Establish caller context

Require the caller to provide:

- an approved game plan, CG requirement, or standalone CG intent;
- the clip purpose: `opening`, `cutscene`, `ultimate`, or `promo`;
- one intended output clip per requested task, with acceptance criteria;
- optional material paths and the declared role of each material;
- the selected model (`h3` or `seedance`), or permission to use the default;
- optional duration, aspect ratio, seed, and stable `task_id`.

When 3AGameFactory invokes this Skill as a child capability, also require the
selected `game_id` and use its standard authoring workspace:

```text
test_data/test_samples/<game_id>/cg_video/
├── requirement.txt
├── ref_images/                 # optional source material
├── ref_videos/                 # optional source material
├── ref_audio/                  # optional source material
└── cg_tasks.jsonl              # written directly by this Skill
```

Do not choose the generated-artifact directory here. The parent Skill and
`<REPO_PATH>/pipeline/common/paths.py` own `run_id` and final output paths.

For a multi-clip sequence, create and validate one envelope per independently
generated clip. Keep task IDs unique and preserve continuity anchors between
envelopes. Do not represent several independently generated clips as one task.

## Establish input evidence

1. Inventory intent, settings, material paths, and each declared material role
   before choosing a mode. Preserve supplied paths and roles verbatim.
2. Inspect accessible materials. Use only observed facts or explicit user
   descriptions, and keep those sources distinct in working context.
3. Treat inaccessible material as opaque: preserve its path, never infer
   content from names or story context, and ask for one short description only
   when the requested motion or endpoint depends on an unknown fact.
4. For T2VA or content not fixed by a reference, add only compatible concrete
   detail needed for scene coherence, composition, continuity, timing, or
   sound; never present it as a reference fact.
5. Do not claim opaque media was inspected or add an evidence record to the
   output JSON.

## Route the request

1. Select exactly one scene: `opening`, `cutscene`, `ultimate`, or `promo`.
   Infer it from the requested purpose; ask only if the intent is ambiguous.
2. Select exactly one mode and use its JSON value:
   - no media input: T2VA → `text_to_video`;
   - one image explicitly used as the first frame: I2VA →
     `first_frame_to_video`;
   - two images explicitly used as first and last frames: FL2VA →
     `first_last_frame_to_video`;
   - one or more materials used as references rather than endpoints: Ref2VA →
     `reference_to_video`;
   - never select or mention the excluded last-frame-only mode.
3. Use the requested model, normalized to lowercase; otherwise use `h3`. Read
   `<REPO_PATH>/agent_skills/asset_qa/cg_video/game-cg-director/models/<model>.md`.
   If the profile does not exist, stop instead of inventing one.

## Read only the selected guidance

Treat the model profile as output dialect and the selected scene template as
the game-CG task layer. Read these files in order. Each path below is relative
to this Skill's directory,
`<REPO_PATH>/agent_skills/asset_qa/cg_video/game-cg-director/`, resolved from
`<REPO_PATH>/`:

1. `common/principles.md`
2. `common/camera.md`
3. `common/sound.md`
4. `common/style-mapping.md` only when the request contains a style term
5. exactly one `modes/<mode>.md`: `t2va`, `i2va`, `fl2va`, or `ref2va`
6. exactly one `templates/<scene>.md`
7. `<REPO_PATH>/agent_skills/asset_qa/cg_video/game-cg-director/models/<model>.md`
   — this Skill's own `models/`, not the repository-level `<REPO_PATH>/models/`

Do not preload sibling modes or scenes. Write prompt prose in English. Preserve
user-supplied dialogue, lyrics, and visible scene text verbatim and format them
according to the model profile.

## Direct before writing

Make a compact private scene plan; never add it to the JSON or prompt. Record
only what the request needs:

1. continuity anchors: recurring subjects, required count, distinguishing
   appearance or reference role, and carried state;
2. stage map: relative positions, entrances or boundaries, facing, goal or
   target, and the main line of action;
3. performance beats: start → preparation → action → reaction or result → end;
4. shot purpose: new information, camera side and direction, dominant movement,
   and ending composition;
5. sound progression: established sources, dialogue timing, audible peak, and
   music change when applicable.

Fit indispensable beats inside the available duration before adding decorative
beats. Preserve recurring identities and counts, causal order, spatial
direction, readable performance, and the requested endpoint.

## Resolve settings

- Preserve a supplied `aspect_ratio`; otherwise use `16:9` and record
  `aspect_ratio:16:9` in `meta.defaults_applied`.
- Preserve duration within model limits. Cap or raise it to a model limit when
  necessary and record the adjustment in `meta.defaults_applied`.
- If duration is absent, use `opening=10`, `cutscene=12`, `ultimate=6`, or
  `promo=10`, then apply the model limit and record the final default.
- When the model is absent, record `model:h3` in `meta.defaults_applied`.
- Preserve a supplied `task_id`; otherwise create a short stable ID from the
  subject, scene, mode, and a numeric suffix.
- Preserve a supplied integer `seed`; otherwise omit it.

## Build and hand off each task

Follow the output schema,
`<REPO_PATH>/agent_skills/asset_qa/cg_video/game-cg-director/schemas/output.schema.json`.
Keep the envelope compact:

- Put the complete prompt prose directly in `prompt`.
- Keep `model`, `mode`, `scene`, material paths, and `meta` at the top level.
- Add `first_frame_path` for I2VA and both endpoint paths for FL2VA.
- For Ref2VA, add only applicable reference path arrays.
- Never place metadata, Skill names, provider names, or vendor names in
  `prompt`.
- Do not add `game_id` or `run_id` before envelope validation. The schema
  intentionally validates directing fields independently of Harness identity.

For an 3AGameFactory child call:

1. Build the envelope without `game_id` or `run_id`.
2. Write it to a unique temporary JSON file outside the repository and run the
   bundled validator on that file.
3. After validation passes, add only the selected `game_id` and write the full
   object as one compact line directly to
   `<REPO_PATH>/test_data/test_samples/<game_id>/cg_video/cg_tasks.jsonl`.
4. Preserve row order and unrelated tasks. Reject a duplicate `task_id` unless
   the caller explicitly requested a revision; then replace that row in place.
5. Remove the temporary validation file. Never create a persistent `director/`
   workspace directory.

For standalone use, write to the caller's requested path. If no path is
requested, write `game-cg-<scene>-<mode>-<task_id>.json` in the current working
directory.

## Validate until it passes

Validate each envelope before adding `game_id` or writing an 3AGameFactory task
row. For standalone use, validate the written envelope. Run:

```bash
python3 <REPO_PATH>/agent_skills/asset_qa/cg_video/game-cg-director/scripts/validate_output.py <envelope-json-path>
```

Validation is mandatory. If it fails, change only the reported violations and
rerun the same command until it exits with code 0. Never call an LLM from the
validator and never substitute visual inspection for validation.

## Hand off to the parent Skill

For 3AGameFactory, return the `cg_tasks.jsonl` path, ordered task IDs, and
validation status. The parent
`<REPO_PATH>/agent_skills/asset_qa/cg_video/SKILL.md` selects backend and
aspect-ratio runner options and invokes the generation pipeline. For standalone
use, return the ordered envelope paths and validation status.

Do not choose an output `run_id`, invoke a model, spend cloud credits, or claim
that a generated video exists.
