# pipeline/

Full-chain runners, organized by function. Each task directory contains two
entry points:

- **`run.py`** — generation only (demo / production)
- **`eval.py`** — evaluation only (benchmark scoring)

Contracts and a GPU-free conformance checker live in
`agent_skills/develop_harness/pipeline_require.md`.

## Structure

```
pipeline/
├── common/                                  # Shared, task-agnostic helpers
│   └── paths.py                             #   single source of truth for all I/O paths
│
├── assets_gen/                              # Asset generation tasks
│   ├── gen_3d_object/{run.py, eval.py}      #   image / text → 3D object
│   ├── gen_tpose_image/{run.py, eval.py}    #   character image → T-pose RGBA
│   ├── gen_3d_scene/{hunyuan_worldplay_*.py}  #   run / eval / render
│   ├── gen_motion/{run.py, eval.py}         #   text + skeleton → animation
│   ├── gen_cg_video/{run.py, eval.py}       #   text / frame → CG video
│   ├── gen_audio/{run.py, eval.py}          #   text / reference → dialogue or game SFX
│   └── retarget/{run.py, eval.py}           #   motion + skeleton → retargeted motion
│
├── mechanic/{run.py, eval.py}               # spec + engine template → code + trace
├── ui/{run.py, eval.py}                     # UI spec → UI code + screenshots
└── full_pipeline/{run.py, eval.py}          # design doc → playable vertical slice
```

## Responsibilities

### `run.py`
1. Load required models (from `models/`)
2. Instantiate operator (from `operators/`)
3. Take a single input → produce a single output artifact
4. No scoring, no metric computation

### `eval.py`
1. Iterate the test set from `test_data/test_samples/<game>/<task>/*_tasks.jsonl`
   (or the cross-game `*_collect.jsonl`)
2. Reuse `run.py`'s generation function (`from .run import generate`)
3. Invoke `operators/<task>/metrics/` on each output
4. Write per-task scores to `paths.eval_output_dir(...)` and the aggregate to
   `paths.eval_summary_path(...)`

## Paths — always via `common/paths.py`

Outputs are grouped **per generated game project**, mirroring the test set.
Never concatenate an output path by hand:

```python
from pipeline.common import paths

paths.resolve_tasks_path(kind, args.tasks, args.game)   # inputs
paths.iter_tasks(tasks_path, game_filter=args.game)     # (task, game_id) pairs
paths.task_output_dir(game, kind, task_id, run_id)      # artifacts
paths.eval_output_dir(game, kind, task_id, run_id)      # scores
paths.write_results_summary(results, kind, run_id)      # per-game summaries
```

Adding a task kind means one entry in each of the four tables in `paths.py`
(`TASK_LAYER`, `TASK_INPUT_DIR`, `TASK_JSONL`, `TASK_COLLECT_JSONL`) — there is
no other registration point.

## Standard CLI flags

Every `run.py` exposes the same knobs:

| Flag | Default | Meaning |
|------|---------|---------|
| `--game <game_id>` | `None` | Restrict to one game project (and prefer its own `*_tasks.jsonl`) |
| `--tasks <jsonl>`  | `None` | Explicit task list, overrides the `--game` lookup |
| `--run-id <name>`  | `default` | Run directory name; `auto` for a timestamp |
| `--out-dir <dir>`  | `None` | Legacy flat output; bypasses the per-game layout |
| `--device`         | `cuda` | |

## Convention

```python
# pipeline/assets_gen/gen_3d_object/run.py
TASK_KIND = "3d_object"

def load_model(ckpt, device="cuda", **kw): ...
def make_operator(model, output_dir=None, run_id=..., default_game_id=None): ...
def generate(inp: dict, operator) -> dict: ...
def run_from_jsonl(tasks_path, operator, game_filter=None) -> list[dict]: ...

# pipeline/assets_gen/gen_3d_object/eval.py
from .run import generate
from operators.gen_3d_object.metrics import evaluate
```

These five names are an API — `eval.py` and `test/` import them.
