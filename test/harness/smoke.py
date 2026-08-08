#!/usr/bin/env python3
"""
test/harness/smoke.py

End-to-end smoke test for an asset-generation chain, driven by **stub models**
so it runs on CPU in milliseconds with no weights and no network.

Contracts being checked live in `agent_skills/develop_harness/*.md`.

What it proves:
  1. the operator can be constructed and `run()` executes,
  2. artifacts land at exactly `paths.task_output_dir(game, kind, task_id, run_id)`,
  3. `meta.json` is written in per-game mode,
  4. the returned dict carries every key the contract requires,
  5. legacy flat mode (`output_dir=...`) still produces the historical filenames,
  6. `paths.write_results_summary()` groups results per game project.

Everything is written under `run_id="_smoke"` and removed again on success, so a
real run is never touched.

Requires only `pillow`, `numpy` (and `scipy` for the tpose chain) — no torch, no
CUDA. Exits 3 with an install hint when they are missing.

Usage:
    python test/harness/smoke.py                    # all stubbed kinds
    python test/harness/smoke.py --kind tpose
    python test/harness/smoke.py --keep             # inspect artifacts
    python test/harness/smoke.py --kind 3d_object --backend tripo   # API backend
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
# Repo root for `pipeline.*` / `operators.*`; own dir for `stubs` — imported by
# plain name on purpose, since `test.harness` would collide with the stdlib
# `test` package.
for _p in (str(_REPO_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pipeline.common import paths                                    # noqa: E402

try:
    import stubs                                                     # noqa: E402
except ModuleNotFoundError as _e:                                    # pragma: no cover
    print(
        f"[smoke] Missing CPU-only dependency: {_e.name}\n"
        f"        The harness needs no GPU, just:\n"
        f"            python -m pip install pillow numpy scipy\n",
        file=sys.stderr,
    )
    sys.exit(3)

SMOKE_RUN_ID = "_smoke"
SMOKE_GAME = "_smoke_game"

#: Required keys in an operator's result dict (operatar_require.md R3).
REQUIRED_RESULT_KEYS = ("task_id", "elapsed_sec", "game_id", "task_kind", "output_dir")

#: Extra per-task input fields needed by specific kinds.
EXTRA_TASK_FIELDS: dict[str, dict] = {
    "tpose": {"description": "A smoke-test character.", "steps": 2, "target_size": 64},
    "3d_object": {"decimation_target": 1000, "texture_size": 64},
    "3d_scene": {"prompt": "A smoke-test scene.", "num_frames": 2, "video_length": 5},
}


class SmokeFailure(AssertionError):
    pass


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SmokeFailure(msg)


# ── Per-kind checks ───────────────────────────────────────────────────────────

def smoke_per_game_mode(kind: str, task_id: str = "smoke_task_001",
                        backend: str | None = None) -> dict:
    """Default mode: artifacts grouped under the game project."""
    print(f"  [per-game] building operator for {kind!r} with stub models"
          + (f" (backend={backend})" if backend else ""))
    op = stubs.build_operator(kind, run_id=SMOKE_RUN_ID, model_key=backend)

    task = {
        "game_id": SMOKE_GAME,
        "task_id": task_id,
        "image": stubs.make_ref_image(size=128),
        "seed": 7,
        **EXTRA_TASK_FIELDS.get(kind, {}),
    }
    result = op.run(task)

    missing = [k for k in REQUIRED_RESULT_KEYS if k not in result]
    _require(not missing, f"{kind}: result dict missing keys {missing}")

    artifact_keys = [k for k in result if k.endswith("_path")]
    _require(bool(artifact_keys), f"{kind}: result dict has no '*_path' key")

    _require(result["game_id"] == SMOKE_GAME,
             f"{kind}: game_id is {result['game_id']!r}, expected {SMOKE_GAME!r}")
    _require(result["task_kind"] == kind,
             f"{kind}: task_kind is {result['task_kind']!r}")
    _require(isinstance(result["elapsed_sec"], (int, float)),
             f"{kind}: elapsed_sec is not numeric")

    expected_dir = paths.task_output_dir(
        SMOKE_GAME, kind, task_id, run_id=SMOKE_RUN_ID, create=False
    ).resolve()
    _require(Path(result["output_dir"]).resolve() == expected_dir,
             f"{kind}: output_dir is {result['output_dir']!r}, "
             f"expected {expected_dir}")
    print(f"             output_dir → {paths.rel_to_repo(expected_dir)}")

    for key in artifact_keys:
        value = result[key]
        if value is None:            # optional artifact — allowed
            continue
        p = Path(value)
        _require(p.exists(), f"{kind}: {key} does not exist on disk: {p}")
        _require(p.stat().st_size > 0, f"{kind}: {key} is empty: {p}")
        _require(p.parent.resolve() == expected_dir,
                 f"{kind}: {key} sits in {p.parent}, expected {expected_dir}")
        print(f"             {key} → {p.name} ({p.stat().st_size} B)")

    meta = expected_dir / "meta.json"
    _require(meta.exists(), f"{kind}: meta.json not written to {expected_dir}")
    meta_data = json.loads(meta.read_text(encoding="utf-8"))
    for key in ("run_id", "task_id", "game_id", "task_kind"):
        _require(key in meta_data, f"{kind}: meta.json missing {key!r}")
    _require(meta_data["run_id"] == SMOKE_RUN_ID,
             f"{kind}: meta.json run_id is {meta_data['run_id']!r}")
    print(f"             meta.json ok ({len(meta_data)} fields)")

    return result


def smoke_legacy_flat_mode(kind: str, tmp_dir: Path,
                           task_id: str = "smoke_legacy_001",
                           backend: str | None = None) -> dict:
    """Backward compatibility: an explicit output_dir keeps the old flat naming."""
    print(f"  [legacy]   output_dir={paths.rel_to_repo(tmp_dir)}")
    op = stubs.build_operator(kind, run_id=SMOKE_RUN_ID, output_dir=str(tmp_dir),
                              model_key=backend)

    result = op.run({
        "task_id": task_id,
        "image": stubs.make_ref_image(size=128),
        "seed": 7,
        **EXTRA_TASK_FIELDS.get(kind, {}),
    })

    for key in (k for k in result if k.endswith("_path")):
        if result[key] is None:
            continue
        p = Path(result[key])
        _require(p.parent.resolve() == tmp_dir.resolve(),
                 f"{kind}: legacy mode wrote outside output_dir: {p}")
        _require(p.name.startswith(task_id),
                 f"{kind}: legacy filename {p.name!r} must start with the task_id "
                 f"(historical behaviour)")
        print(f"             {key} → {p.name}")

    _require(not (tmp_dir / "meta.json").exists(),
             f"{kind}: legacy mode must not write meta.json into the flat dir")
    print("             no meta.json in legacy dir (as expected)")
    return result


def smoke_summaries(kind: str, results: list[dict]) -> None:
    """Results must be grouped per game project."""
    written = paths.write_results_summary(results, kind, SMOKE_RUN_ID)
    _require(bool(written), f"{kind}: write_results_summary wrote nothing")
    for p in written:
        _require(p.exists(), f"{kind}: summary missing: {p}")
        data = json.loads(p.read_text(encoding="utf-8"))
        _require(isinstance(data, list) and data,
                 f"{kind}: summary is not a non-empty list: {p}")
        print(f"  [summary]  {paths.rel_to_repo(p)} ({len(data)} task(s))")

    meta = paths.run_meta_path(SMOKE_GAME, SMOKE_RUN_ID)
    _require(meta.exists(), f"{kind}: run_meta.json not written")
    meta_data = json.loads(meta.read_text(encoding="utf-8"))
    for key in ("game_id", "run_id", "task_kind", "created_at", "argv"):
        _require(key in meta_data, f"{kind}: run_meta.json missing {key!r}")
    print(f"  [summary]  run_meta.json ok (git_sha={meta_data.get('git_sha')})")


def smoke_kind(kind: str, keep: bool, backend: str | None = None) -> None:
    print(f"\n\033[1m▶ {kind}\033[0m")
    # --backend applies only to slots that actually have alternative backends,
    # so `--backend tripo` with no --kind still smokes every other kind.
    backend = backend if kind in stubs.STUB_BACKENDS else None
    tmp_flat = paths.OUTPUT_ROOT / f"_smoke_legacy_{kind}"
    try:
        result = smoke_per_game_mode(kind, backend=backend)
        smoke_legacy_flat_mode(kind, tmp_flat, backend=backend)
        smoke_summaries(kind, [result])
    finally:
        if not keep:
            shutil.rmtree(tmp_flat, ignore_errors=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kind", action="append", default=None,
                    help="Task kind to smoke (repeatable). Default: every stubbed kind.")
    ap.add_argument("--keep", action="store_true",
                    help="Keep the _smoke artifacts for inspection")
    ap.add_argument("--backend", default=None,
                    help="Stub backend for slots with several models, e.g. "
                         f"{sorted(stubs.STUB_BACKENDS.get('3d_object', {}))}")
    args = ap.parse_args()

    kinds = args.kind or sorted(stubs.STUB_OPERATOR_KWARGS)
    unknown = [k for k in kinds if k not in stubs.STUB_OPERATOR_KWARGS]
    if unknown:
        print(f"No stub registered for {unknown}. Add one to STUB_OPERATOR_KWARGS "
              f"in stubs.py. Available: {sorted(stubs.STUB_OPERATOR_KWARGS)}",
              file=sys.stderr)
        return 2

    print("\033[1mAAAGameForge develop_harness — chain smoke test (stub models)\033[0m")
    print(f"output root: {paths.rel_to_repo(paths.OUTPUT_ROOT)}")
    print(f"run_id: {SMOKE_RUN_ID}   game_id: {SMOKE_GAME}   kinds: {', '.join(kinds)}"
          + (f"   backend: {args.backend}" if args.backend else ""))

    failures: list[tuple[str, str]] = []
    for kind in kinds:
        try:
            smoke_kind(kind, keep=args.keep, backend=args.backend)
        except SmokeFailure as e:
            print(f"  \033[31mFAIL\033[0m {e}")
            failures.append((kind, str(e)))
        except Exception:
            print(f"  \033[31mERROR\033[0m unexpected exception:")
            traceback.print_exc()
            failures.append((kind, "unexpected exception"))

    if not args.keep:
        shutil.rmtree(paths.game_output_dir(SMOKE_GAME), ignore_errors=True)

    print("\n" + "─" * 66)
    if failures:
        print(f"\033[31mFAILED\033[0m  {len(failures)}/{len(kinds)} kind(s)")
        for kind, msg in failures:
            print(f"  · {kind}: {msg}")
        return 1

    print(f"\033[32mOK\033[0m  {len(kinds)} kind(s) passed"
          + ("  (artifacts kept)" if args.keep else "  (artifacts cleaned up)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
