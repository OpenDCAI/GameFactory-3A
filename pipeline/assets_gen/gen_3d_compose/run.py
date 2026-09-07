"""
pipeline/assets_gen/gen_3d_compose/run.py

Compose a generated figure with segmented armour and socketed weapons.

Generation of the individual assets is a previous step. This runner reads
those files, cuts a fused harness into anatomical pieces, fits each piece
to the body's measured landmarks, hangs weapons on named sockets, and
writes one GLB plus a sockets.json the motion / Unity Humanoid pass can
reparent.

Usage:
    # Synthetic dataset: primitive T-pose bodies × harnesses × weapons
    python pipeline/assets_gen/gen_3d_compose/run.py --synth

    # One combination from files (GLB or OBJ)
    python pipeline/assets_gen/gen_3d_compose/run.py \
        --body path/to/tpose.glb --armour path/to/armour.glb \
        --weapon path/to/sword.glb --weapon-kind sword \
        --height 1.72 --task-id knight_001

    # Overlay the harness whole, as a control (no cut)
    python pipeline/assets_gen/gen_3d_compose/run.py \
        --body tpose.glb --armour armour.glb --no-segment

    # Batch from jsonl
    python pipeline/assets_gen/gen_3d_compose/run.py \
        --tasks test_data/test_samples/3D_compose_collect.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.common import paths  # noqa: E402

TASK_KIND = "3d_compose"


def make_operator(run_id: str = paths.DEFAULT_RUN_ID,
                  default_game_id: str | None = None,
                  output_dir: str | None = None):
    from operators.gen_3d_object.operator import Gen3DObjectOperator
    return Gen3DObjectOperator(
        model=None, run_id=run_id, default_game_id=default_game_id,
        output_dir=output_dir,
    )


def _compose_task(
    *,
    body: str,
    armour: str | None,
    weapons: list[dict],
    height: float,
    task_id: str,
    game_id: str | None,
    subject: str,
    segment: bool,
) -> dict:
    return {
        "game_id": game_id,
        "task_id": task_id,
        "task_kind": TASK_KIND,
        "compose": {
            "body": body,
            "armour": armour,
            "weapons": weapons,
            "height_metres": height,
            "subject": subject,
            "segment_armour": segment,
        },
    }


def run_synth(operator, *, game_id: str, out_library: Path) -> list[dict]:
    """Build the primitive library and cross-combine every pair."""

    from operators.gen_3d_object.funcs.code_asset_templates.human_template import (
        synth)

    print(f"[run] writing synthetic library → {out_library}")
    library = synth.write_library(str(out_library))
    (out_library / "library.json").write_text(
        json.dumps(library, indent=2), encoding="utf-8",
    )

    results = []
    for body_name, body_path in library["bodies"].items():
        height = synth.BODIES[body_name]["height"]
        for armour_name, armour_path in library["armours"].items():
            for segment in (True, False):
                tag = "seg" if segment else "overlay"
                weapons = [{
                    "source": library["weapons"]["sword"],
                    "kind": "sword",
                }]
                # One combo also carries the shield, so the socket on the
                # other hand is exercised.
                if body_name == "tall" and armour_name == "short_plate" and segment:
                    weapons.append({
                        "source": library["weapons"]["shield"],
                        "kind": "shield",
                    })
                task_id = f"{body_name}__{armour_name}__{tag}"
                task = _compose_task(
                    body=body_path, armour=armour_path, weapons=weapons,
                    height=height, task_id=task_id, game_id=game_id,
                    subject=f"{body_name} in {armour_name} ({tag})",
                    segment=segment,
                )
                print(f"[run] {task_id}")
                result = operator.run(task)
                glb = result.get("glb_path")
                print(f"       → {glb}  ok={result.get('spec_ok')}  "
                      f"parts={len(result.get('part_ids') or ())}")
                for warning in (result.get("compose") or {}).get("warnings") or ():
                    print(f"       ! {warning}")
                results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Compose a figure with segmented armour and socketed weapons.")
    parser.add_argument("--synth", action="store_true",
                        help="Build the primitive body/armour/weapon library "
                             "and cross-combine it")
    parser.add_argument("--body", default=None, help="T-pose figure GLB or OBJ")
    parser.add_argument("--armour", default=None,
                        help="Fused harness GLB or OBJ (cut, then fitted)")
    parser.add_argument("--weapon", action="append", default=[],
                        help="Weapon GLB/OBJ; repeatable. Pair with --weapon-kind")
    parser.add_argument("--weapon-kind", action="append", default=[],
                        help="Kind for each --weapon, in order (sword, shield, ...)")
    parser.add_argument("--height", type=float, default=1.72,
                        help="Placed height of the figure, metres")
    parser.add_argument("--no-segment", action="store_true",
                        help="Overlay the harness whole, as a control")
    parser.add_argument("--subject", default="composed avatar")
    parser.add_argument("--game", default=None)
    parser.add_argument("--task-id", default="compose")
    parser.add_argument("--tasks", default=None, help="jsonl of compose tasks")
    parser.add_argument("--run-id", default=paths.DEFAULT_RUN_ID)
    parser.add_argument("--out-dir", default=None,
                        help="Legacy flat output dir")
    parser.add_argument("--library-dir", default=None,
                        help="Where --synth writes the fixture library")
    args = parser.parse_args()

    run_id = paths.new_run_id() if args.run_id == "auto" else args.run_id
    game_id = args.game or paths.UNASSIGNED_GAME
    operator = make_operator(run_id=run_id, default_game_id=game_id,
                             output_dir=args.out_dir)

    if args.synth:
        library_dir = Path(args.library_dir) if args.library_dir else (
            paths.task_output_dir(game_id, TASK_KIND, "_library", run_id=run_id)
        )
        results = run_synth(operator, game_id=game_id, out_library=library_dir)
    elif args.tasks:
        tasks_path = paths.resolve_tasks_path(TASK_KIND, args.tasks, args.game)
        results = []
        for task, inferred_game in paths.iter_tasks(
                str(tasks_path), game_filter=args.game):
            task.setdefault("game_id", inferred_game)
            task.setdefault("task_kind", TASK_KIND)
            print(f"[run] {task.get('task_id')}")
            results.append(operator.run(task))
    elif args.body:
        kinds = list(args.weapon_kind)
        weapons = []
        for index, source in enumerate(args.weapon):
            kind = kinds[index] if index < len(kinds) else "sword"
            weapons.append({"source": source, "kind": kind})
        task = _compose_task(
            body=args.body, armour=args.armour, weapons=weapons,
            height=args.height, task_id=args.task_id, game_id=game_id,
            subject=args.subject, segment=not args.no_segment,
        )
        print(f"[run] {args.task_id}")
        results = [operator.run(task)]
        print(f"       → {results[0].get('glb_path')}")
    else:
        parser.error("pass --synth, --body, or --tasks")
        return

    if not results:
        print("[run] nothing composed")
        return
    for written in paths.write_results_summary(results, TASK_KIND, run_id):
        print(f"[run] Wrote summary → {paths.rel_to_repo(written)}")


if __name__ == "__main__":
    main()
