"""Fit a garment and bind it to a character skeleton in a separate Blender worker.

Blender dependencies are loaded only in the worker process.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


class WearableFitError(ValueError):
    """Invalid fit inputs or a Blender worker that could not finish."""


def fit_wearable(
    *, body: str, clothing: str, output: str,
    coverage: str = "full_body", sleeve_pose: str = "down",
    height_metres: float = 1.75, clearance_metres: float = 0.008,
    headwear_offset_metres: float = 0.0,
    footwear_mode: str = "preserve",
    blender_python: str | None = None,
    source_heights: dict[str, float] | None = None,
    clothing_rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    save_blend: bool = True, timeout: float = 600.0,
) -> dict[str, Any]:
    """Fit and skin a garment, exporting GLB and a fit report.

    ``coverage`` selects full/upper-body anchors. ``sleeve_pose`` describes
    the source pose (down/a/t); output uses the character's rest pose.
    The body needs a humanoid armature with Mixamo-style or equivalent names.

    ``footwear_mode="replace"`` requires closed shoes covering the ankles.
    It removes covered feet from the output, retaining an uncut weight donor.
    ``preserve`` leaves the original feet intact.

    ``source_heights`` contains normalized garment heights.
    ``clothing_rotation`` is XYZ degrees in Blender's Z-up frame.
    The worker preserves UVs/materials and transfers interpolated skin weights.

    ``blender_python`` requires bpy and numpy; defaults to BLENDER_PYTHON
    or this interpreter. The worker runs in a separate process.
    """
    if coverage not in ("full_body", "upper_body"):
        raise WearableFitError("coverage must be 'full_body' or 'upper_body'")
    if footwear_mode not in ("preserve", "replace"):
        raise WearableFitError("footwear_mode must be preserve or replace")
    if footwear_mode == "replace" and coverage != "full_body":
        raise WearableFitError("footwear replacement requires full_body coverage")
    if sleeve_pose not in ("down", "a", "t"):
        raise WearableFitError("sleeve_pose must be 'down', 'a' or 't'")
    for name, value in (("height_metres", height_metres), ("timeout", timeout)):
        if not math.isfinite(value) or value <= 0:
            raise WearableFitError(f"{name} must be finite and positive")
    if not math.isfinite(clearance_metres) or not 0 <= clearance_metres < height_metres * .05:
        raise WearableFitError("clearance_metres must be in [0, 5% of height)")
    if not math.isfinite(headwear_offset_metres) or abs(headwear_offset_metres) > height_metres * .1:
        raise WearableFitError('headwear_offset_metres must be within 10% of body height')
    if coverage == 'upper_body' and headwear_offset_metres:
        raise WearableFitError('headwear_offset_metres applies only to full_body garments')
    if len(clothing_rotation) != 3 or not all(math.isfinite(v) for v in clothing_rotation):
        raise WearableFitError("clothing_rotation must contain three finite degrees")
    paths = {}
    for name, source in (("body", body), ("clothing", clothing)):
        path = Path(source).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() not in (".fbx", ".glb", ".gltf"):
            raise WearableFitError(f"{name} must be an existing FBX, GLB or glTF: {path}")
        paths[name] = str(path)
    out = Path(output).expanduser().resolve()
    if out.suffix.lower() != ".glb" or str(out) in paths.values():
        raise WearableFitError("output must be a new .glb path, separate from both inputs")
    allowed = {"ankle", "knee", "hip", "waist", "shoulder", "neck"}
    anchors = dict(source_heights or {})
    if set(anchors) - allowed or any(not math.isfinite(v) or not 0 < v < 1 for v in anchors.values()):
        raise WearableFitError(f"source_heights needs fractions in (0, 1) for {sorted(allowed)}")
    defaults = ({"ankle": .05, "knee": .27, "hip": .49, "waist": .60,
                 "shoulder": .81, "neck": .88} if coverage == "full_body" else
                {"hip": .01, "waist": .20, "shoulder": .82, "neck": .97})
    if set(anchors) - set(defaults):
        raise WearableFitError("upper_body source_heights cannot include ankle or knee")
    defaults.update(anchors)
    if any(a >= b for a, b in zip(defaults.values(), list(defaults.values())[1:])):
        raise WearableFitError("source_heights must increase from ankle/hip to neck")
    out.parent.mkdir(parents=True, exist_ok=True)
    config = {**paths, "output": str(out), "coverage": coverage,
              "sleeve_pose": sleeve_pose, "height_metres": height_metres,
              "clearance_metres": clearance_metres, "headwear_offset_metres": headwear_offset_metres,
              "source_heights": defaults, "footwear_mode": footwear_mode,
              "clothing_rotation": clothing_rotation, "save_blend": save_blend}
    worker = Path(__file__).with_name("_wearable_blender.py")
    with tempfile.TemporaryDirectory(prefix="wearable-fit-") as directory:
        config_path = Path(directory) / "config.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        try:
            result = subprocess.run(
                [blender_python or os.environ.get("BLENDER_PYTHON") or sys.executable,
                 "-I", str(worker), str(config_path)],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise WearableFitError(f"Blender clothing fit failed: {exc}") from exc
        # Trust the completion record: bpy may crash after a successful export.
        done = Path(directory) / "done.json"
        if not done.is_file():
            raise WearableFitError(
                f"Blender clothing fit failed (exit {result.returncode}). "
                f"Use a Python environment with bpy and numpy.\n"
                f"{(result.stdout + result.stderr)[-5000:]}"
            )
        report = json.loads(done.read_text(encoding="utf-8"))
    return report
