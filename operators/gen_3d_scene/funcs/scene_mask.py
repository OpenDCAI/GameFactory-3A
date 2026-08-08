"""
operators/gen_3d_scene/funcs/scene_mask.py

Decide which pixels of a WorldMirror prediction carry usable geometry.

Deliberately permissive. Upstream's `create_filter_mask` conflates "is this
pixel's position meaningful?" with "does this pixel sit on a discontinuity?" and
answers both by deleting the pixel. The second is what perforates the mesh, since
a discontinuity belongs to the boundary between two pixels rather than to either
one. `points_to_mesh` handles it there, on the faces, so this module answers only
the first question. Versus upstream:

  * No edge masks. `depth_edge` and `normals_edge` 3x3 max-pool their result, so
    every flagged pixel took a halo of good surface with it.
  * No confidence percentile by default — deleting the bottom decile of every
    frame is a relative threshold on an absolute question. Kept as a knob.
  * Sky removal stays when a mask is supplied: sky genuinely has no depth.

Usage:
    from operators.gen_3d_scene.funcs.scene_mask import scene_mask
    masks = scene_mask(prediction, sky_masks=None)   # (S, H, W) bool
"""
from __future__ import annotations

from typing import Optional

import numpy as np

#: Fraction of each frame's lowest-confidence pixels to delete. Upstream uses
#: 10.0; the default here is 0.0 because the percentile is a leading cause of
#: holes in otherwise solid surfaces. Raise it only for very noisy footage.
DEFAULT_CONFIDENCE_PERCENTILE = 0.0

#: Absolute confidence floor. WorldMirror's confidence is unbounded above with
#: ~1.0 meaning "no better than chance", so this removes genuinely unusable
#: pixels without touching the distribution's shape the way a percentile does.
DEFAULT_MIN_CONFIDENCE = 1.0


def scene_mask(
    prediction: dict[str, np.ndarray],
    sky_masks: Optional[np.ndarray] = None,
    confidence_percentile: float = DEFAULT_CONFIDENCE_PERCENTILE,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> np.ndarray:
    """
    Build a per-pixel validity mask for every frame of a prediction.

    Args:
        prediction: A `WorldMirrorModel.infer` result. Reads `points`, `depth`
            and, when thresholding, `confidence`.
        sky_masks: (S, H, W) bool where True means *non-sky*, matching
            upstream's polarity. `None` keeps every pixel.
        confidence_percentile: Drop this percentage of each frame's least
            confident pixels. 0 disables it.
        min_confidence: Drop pixels whose confidence falls below this absolute
            value. 0 disables it.

    Returns:
        (S, H, W) bool array — True where the pixel's geometry is usable.
    """
    points = prediction["points"]
    depth = prediction["depth"]

    # Representability: a position must be finite and in front of the camera.
    valid = np.isfinite(points).all(axis=-1)
    valid &= np.isfinite(depth) & (depth > 0)

    confidence = prediction.get("confidence")
    if confidence is not None:
        if min_confidence > 0:
            valid &= confidence >= min_confidence
        if confidence_percentile > 0:
            # Per frame, matching upstream's behaviour when the knob is used.
            for frame in range(confidence.shape[0]):
                threshold = np.quantile(
                    confidence[frame], confidence_percentile / 100.0
                )
                valid[frame] &= confidence[frame] >= threshold

    if sky_masks is not None:
        valid &= sky_masks.astype(bool)

    return valid
