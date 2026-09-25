"""Register assembly routing strategies and expose code-asset templates.

``routing`` selects composed, nested or surface strategies. ``compose``
builds specs; ``assembly`` resolves attachments. Domain templates provide
rigid assemblies and measured human equipment.

Importing this package registers the built-in strategies. ``fit_wearable``
loads its optional Blender bridge only when called.
"""

from __future__ import annotations

from . import assembly, compose, human_template, rigid_template, routing, surface

__all__ = [
    "assembly",
    "compose",
    "human_template",
    "rigid_template",
    "routing",
    "surface",
    "fit_wearable",
]


def fit_wearable(*, body: str, clothing: str, output: str,
                 coverage: str = "full_body", sleeve_pose: str = "down",
                 footwear_mode: str = "preserve", height_metres: float = 1.75,
                 clearance_metres: float = 0.008, headwear_offset_metres: float = 0.0,
                 blender_python: str | None = None, source_heights: dict[str, float] | None = None,
                 clothing_rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
                 save_blend: bool = True, timeout: float = 600.0):
    """Load the wearable worker only when requested."""
    from .human_template.wearable_fit import fit_wearable as worker
    return worker(body=body, clothing=clothing, output=output, coverage=coverage,
                  sleeve_pose=sleeve_pose, footwear_mode=footwear_mode,
                  height_metres=height_metres, clearance_metres=clearance_metres,
                  headwear_offset_metres=headwear_offset_metres,
                  blender_python=blender_python, source_heights=source_heights,
                  clothing_rotation=clothing_rotation, save_blend=save_blend,
                  timeout=timeout)
