"""Define caller-supplied limb groups without implicit fitting policy."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np

REACH_DIRECTIONS = ('down', 'out')


@dataclass(frozen=True)
class LimbGroup:
    name: str
    at: float
    joints: int
    reach: str
    window: float
    lateral_min: float
    paired: bool
    span: tuple[float, float]

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('a limb group must have a non-empty string name')
        if not np.isfinite(self.at) or not 0 <= self.at <= 1:
            raise ValueError(f'{self.name}: at must lie in [0,1]')
        if type(self.joints) is not int or self.joints < 2:
            raise ValueError(f'{self.name}: joints must be an integer >= 2')
        if self.reach not in REACH_DIRECTIONS:
            raise ValueError(f'{self.name}: reach must be one of {REACH_DIRECTIONS}')
        if not np.isfinite(self.window) or self.window <= 0:
            raise ValueError(f'{self.name}: window must be positive and finite')
        if not np.isfinite(self.lateral_min) or not 0 <= self.lateral_min < 1:
            raise ValueError(f'{self.name}: lateral_min must lie in [0,1)')
        if type(self.paired) is not bool:
            raise ValueError(f'{self.name}: paired must be a boolean')
        span = np.asarray(self.span, float)
        if span.shape != (2,) or not np.isfinite(span).all() or not 0 <= span[0] < span[1] <= 1:
            raise ValueError(f'{self.name}: span must satisfy 0 <= lo < hi <= 1')

    @classmethod
    def coerce(cls, value: 'LimbGroup | dict') -> 'LimbGroup':
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise ValueError('a limb group must be a dict or LimbGroup')
        fields = set(cls.__dataclass_fields__)
        if set(value) != fields:
            raise ValueError(f'limb group missing keys {sorted(fields - set(value))}, unknown keys {sorted(set(value) - fields)}')
        return cls(**{**value, 'span': tuple(value['span'])})
