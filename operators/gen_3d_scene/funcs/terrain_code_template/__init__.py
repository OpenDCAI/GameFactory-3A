"""
operators/gen_3d_scene/funcs/terrain_code_template/

Greybox scenes, split by question rather than by scene.

    landforms.py    what the ground is
    foreground.py   what stands on it

A template is the pair applied in order. Two files rather than one per scene
because the two questions take different parameters and are worth changing
separately: a caller can keep a landform and re-roll what stands on it, or
put the same settlement on a different shape of ground.

Landforms are named for the shape, not for a level: `basin` is a bowl with
whatever a bowl implies, and any number of games can be set in one. The
foreground for a landform follows that shape, because the two are not
independent — a settlement in a basin gathers on the terraces, the same
settlement on a ridge follows the high ground.

The ground reports the measurements the foreground needs — where a basin
bottoms out, what level a street network was graded to — so those are derived
once, in the place that knows them.

Usage:
    from operators.gen_3d_scene.funcs.terrain_code_template import (
        TEMPLATES, build_scene, foreground, landforms,
    )

    scene = build_scene("basin")                # the pair, with defaults
    scene = TEMPLATES["basin"](size=70.0)       # ground parameters

    ground = landforms.basin(size=70.0)         # or drive the two by hand
    terrain, props = foreground.basin(ground, hamlets=3)
"""
from __future__ import annotations

from typing import Any

from .. import terrain_code_edit as te
from . import foreground, landforms

#: Landform name -> (ground function, foreground function).
STAGES = {
    name: (landforms.LANDFORMS[name], foreground.FOREGROUNDS[name])
    for name in landforms.LANDFORMS
}


def build_scene(
    name: str,
    ground_args: dict[str, Any] | None = None,
    foreground_args: dict[str, Any] | None = None,
    **kwargs: Any,
) -> te.Scene:
    """Build a scene by applying a landform and then its foreground.

    Bare keyword arguments go to the landform, since size and relief are what
    a caller usually wants to change. `foreground_args` reaches the second
    stage, and `ground_args` is there for a parameter whose name collides.
    """
    make_ground, populate = STAGES[name]
    ground = make_ground(**{**(ground_args or {}), **kwargs})
    terrain, props = populate(ground, **(foreground_args or {}))
    return te.Scene(name=name, terrain=terrain, props=props)


#: Templates by landform, so a task can pick one from a string. Each takes the
#: landform's own keyword arguments.
TEMPLATES = {
    name: (lambda _name=name, **kwargs: build_scene(_name, **kwargs))
    for name in STAGES
}

__all__ = ["STAGES", "TEMPLATES", "build_scene", "foreground", "landforms"]
