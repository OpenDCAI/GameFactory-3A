"""
models/gen_3d_scene/

Scene-scale 3D generation backends, based on Tencent's Hunyuan WorldPlay stack.
Generating a scene asset is a two-stage chain and each stage is its own model:

| class              | file                    | stage                                    |
|--------------------|-------------------------|------------------------------------------|
| `WorldPlayModel`   | `world_play_model.py`   | image + prompt + camera path → RGB frames |
| `WorldMirrorModel` | `world_mirror_model.py` | frames → points, depth, normals, cameras  |

`WorldMirrorModel` is the one that matters for asset generation and works on
its own — feed it real footage or a rendered flythrough and skip stage one.
`WorldPlayModel` is only needed when the scene has to be *invented* from a
single reference image.

Both are resolved lazily: importing this package must stay cheap and must not
require the vendored network code, the HY-WorldPlay checkout, or `torch`.
"""
from __future__ import annotations

from typing import Any

__all__ = ["WorldMirrorModel", "WorldPlayModel"]


def __getattr__(name: str) -> Any:
    """Lazy re-export of the heavy backends (PEP 562)."""
    if name == "WorldMirrorModel":
        from .world_mirror_model import WorldMirrorModel

        return WorldMirrorModel
    if name == "WorldPlayModel":
        from .world_play_model import WorldPlayModel

        return WorldPlayModel
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
