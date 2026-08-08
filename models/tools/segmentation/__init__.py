"""
`models.tools.segmentation` — semantic segmentation helpers.

Unlike `image_matting`, which separates a single foreground subject from its
background, these models label a semantic class wherever it appears in a scene:

- `SkySegmentationModel`: per-pixel sky probability, used by `gen_3d_scene` to
  keep sky out of the reconstructed mesh.

All of them inherit from `BaseToolModel` (see `models/tools/base.py`).
"""

from models.tools.segmentation.sky import SkySegmentationModel

__all__ = ["SkySegmentationModel"]
