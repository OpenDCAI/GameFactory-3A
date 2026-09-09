"""Export reproducible whitebox GLBs and a validation manifest.

Called through test/test_3d_scene_code.py --export. Use --source and --variant
opus for a baseline checkout, or --variant gpt6 for the current checkout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def export_scenes(source: Path, output: Path, variant: str, seed: int | None = None):
    sys.path.insert(0, str(source.resolve()))
    from operators.gen_3d_scene.funcs import terrain_code_edit as te
    from operators.gen_3d_scene.funcs.terrain_code_template import TEMPLATES

    target = output / variant
    target.mkdir(parents=True, exist_ok=True)
    records = []
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    files = [source / "operators/gen_3d_scene/funcs/terrain_code_edit.py"]
    files += sorted((source / "operators/gen_3d_scene/funcs/terrain_code_template").glob("*.py"))
    digest = hashlib.sha256(b"".join(path.read_bytes() for path in files)).hexdigest()
    for name, build in TEMPLATES.items():
        scene = build(**({"seed": seed} if seed is not None else {}))
        problems = te.check_scene(scene)
        path = target / f"{name}.glb"
        te.write_scene(scene, path)
        record = dict(name=name, revision=revision, source_sha256=digest, variant=variant,
                      seed=seed, problems=problems, bytes=path.stat().st_size,
                      summary=te.scene_summary(scene))
        records.append(record)
        print(f"{variant}/{name}: {len(scene.props)} props, "
              f"{len(problems)} problems, {path.stat().st_size:,} bytes", flush=True)
    (target / "manifest.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    return int(any(record["problems"] for record in records))


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root)
    parser.add_argument("--output", type=Path, default=root / "test_data/outputs/terrain_whitebox")
    parser.add_argument("--variant", choices=("opus", "gpt6"), default="gpt6")
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    return export_scenes(args.source, args.output, args.variant, args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
