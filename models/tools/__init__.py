"""
This sub-package contains *tool* models — utility / auxiliary models that
support the main generation pipelines but are not themselves the primary
content generator. Examples:

- depth estimation (DepthAnything)
- foreground segmentation / background removal (RMBG-1.4, SAM, ...)
- matting / alpha refinement
- keypoint / pose detection helpers

All tool models inherit from `BaseToolModel` (see `base.py`) so they share a
consistent constructor, device handling, and `__call__` / `predict` API.

The concrete wrappers are resolved lazily, so importing this package stays cheap
and pulls in only the backend you actually name. Eagerly re-exporting them would
drag `transformers` and `torchvision` into every caller, including ones that only
want a small ONNX model.
"""
from __future__ import annotations

from typing import Any

from models.tools.base import BaseToolModel

__all__ = ["BaseToolModel", "DepthAnythingModel", "RMBGModel", "SkySegmentationModel"]

_LAZY = {
    "DepthAnythingModel": "models.tools.image_matting",
    "RMBGModel": "models.tools.image_matting",
    "SkySegmentationModel": "models.tools.segmentation",
}


def __getattr__(name: str) -> Any:
    """Lazy re-export of the tool wrappers (PEP 562)."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module), name)