"""Define and validate limb group parameters for skeleton fitting."""
from __future__ import annotations
from dataclasses import dataclass
REACH_DIRECTIONS = ('down', 'up', 'out', 'back', 'forward')

@dataclass(frozen=True)
class LimbGroup:
    'One limb group; with ``paired=True`` it stands for a left/right symmetric pair.'
    name: str
    at: float
    joints: int
    reach: str = 'down'
    window: float = 0.12
    lateral_min: float = 0.15
    paired: bool = True
    span: tuple[float, float] = (0.0, 1.0)

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError('a limb group must have a name')
        if not 0.0 <= self.at <= 1.0:
            raise ValueError(f'{self.name}: at must lie in [0,1], got {self.at}')
        if not isinstance(self.joints, int) or isinstance(self.joints, bool) or self.joints < 2:
            raise ValueError(f'{self.name}: joints must be an integer >= 2, got {self.joints!r}')
        if self.reach not in REACH_DIRECTIONS:
            raise ValueError(f'{self.name}: unknown reach {self.reach!r}, available {REACH_DIRECTIONS}')
        if not 0.0 < self.window <= 0.5:
            raise ValueError(f'{self.name}: window must lie in (0,0.5], got {self.window}')
        if not 0.0 <= self.lateral_min < 1.0:
            raise ValueError(f'{self.name}: lateral_min must lie in [0,1), got {self.lateral_min}')
        lo, hi = self.span
        if not 0.0 <= lo < hi <= 1.0:
            raise ValueError(f'{self.name}: span must satisfy 0 <= lo < hi <= 1, got {self.span}')

    @classmethod
    def coerce(cls, value: 'LimbGroup | dict') -> 'LimbGroup':
        'Build from a dict, rejecting unknown keys so a misspelled parameter name fails instead of being silently ignored.'
        if isinstance(value, LimbGroup):
            return value
        if not isinstance(value, dict):
            raise ValueError(f'a limb group must be a dict or LimbGroup, got {type(value).__name__}')
        allowed = set(cls.__dataclass_fields__)
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f'limb group has unknown parameters {sorted(unknown)}, available {sorted(allowed)}')
        data = dict(value)
        if 'span' in data:
            data['span'] = tuple((float(x) for x in data['span']))
        return cls(**data)
