"""Store rigging results and convert fitted skeletons into motion templates."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import numpy as np
from .mesh import BodyFrame

@dataclass
class RigResult:
    'Result of one rigging pass.'
    name: str
    joints: np.ndarray
    parents: np.ndarray
    joint_names: list[str]
    chains: dict[str, list[int]] = field(default_factory=dict)
    frame: BodyFrame | None = None
    params: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def num_joints(self) -> int:
        return int(len(self.parents))

    def bone_vectors(self) -> np.ndarray:
        '``(K,3)`` vector from each joint to its parent; zero for the root.'
        out = np.zeros_like(self.joints)
        has = self.parents >= 0
        out[has] = self.joints[has] - self.joints[self.parents[has]]
        return out

def to_motion_template(rig: RigResult, *, name: str | None=None):
    'Convert to a motion ``SkeletonTemplate`` so the rig can drive motion generation.'
    from ..motion_utils.skeleton_templates import template_from_skeleton
    return template_from_skeleton(np.asarray(rig.parents, np.int64), np.asarray(rig.joints, np.float32), list(rig.joint_names), name=name or rig.name)
