"""Store generic skeleton fitting results without motion recognition."""
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
