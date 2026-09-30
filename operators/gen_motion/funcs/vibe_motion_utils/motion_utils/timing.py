"""Sample position curves using named events and interpolation modes."""
from __future__ import annotations

from dataclasses import dataclass
from numbers import Real
import numpy as np
from scipy.interpolate import CubicHermiteSpline, PchipInterpolator


def fields(value, required, *, optional=(), label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    missing = set(required) - value.keys()
    extra = value.keys() - set(required) - set(optional)
    if missing or extra:
        raise ValueError(f'{label}: missing fields {sorted(missing)}, unknown fields {sorted(extra)}')


def number(value, *, label, positive=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not np.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    if positive and value <= 0 or nonnegative and value < 0:
        raise ValueError(f'{label} has an invalid sign')
    return float(value)


def numbers(value, shape, *, label):
    array = np.asarray(value)
    if array.shape != shape or array.dtype.kind not in 'iuf' or not np.isfinite(array).all():
        raise ValueError(f'{label} must contain finite numbers with shape {shape}')
    return array.astype(float)


def smooth(value):
    x = np.clip(value, 0, 1)
    return x ** 3 * (10 + x * (-15 + 6 * x))


@dataclass(frozen=True)
class Rhythm:
    duration: float
    fps: float
    events: dict

    def __post_init__(self):
        number(self.duration, label='rhythm.duration', positive=True)
        number(self.fps, label='rhythm.fps', positive=True)
        count = self.duration * self.fps
        tolerance = np.spacing(max(count, 1.0))
        if not np.isfinite(count) or round(count) < 1 or abs(count - round(count)) > tolerance:
            raise ValueError('duration * fps must be a positive integer so exported cadence matches event timing')
        if not isinstance(self.events, dict):
            raise ValueError('rhythm.events must be an object')
        for key, value in self.events.items():
            if not isinstance(key, str) or not key.strip() or key in ('start', 'end'):
                raise ValueError('Event names must be nonempty; start/end are reserved')
            self.fraction(number(value, label=f'event {key}'))

    def fraction(self, value):
        if isinstance(value, str):
            if value == 'start':
                return 0.0
            if value == 'end':
                return 1.0
            if value not in self.events:
                raise ValueError(f'Unknown timing event: {value}')
            value = self.events[value]
        value = number(value, label='time reference')
        if not 0 <= value <= 1:
            raise ValueError('Time references must be fractions in [0,1]')
        return value

    def sample(self):
        return np.arange(round(self.duration * self.fps) + 1) / self.fps

    def windows(self, values):
        if not isinstance(values, list):
            raise ValueError('Contact windows must be a list')
        result = []
        for window in values:
            if not isinstance(window, (list, tuple)) or len(window) != 2:
                raise ValueError('Each contact window needs two time references')
            a, b = map(self.fraction, window)
            if a > b or result and a <= result[-1][1]:
                raise ValueError('Contact windows must be ordered and separated')
            result.append((a, b))
        return np.asarray(result, float).reshape(-1, 2)

    def contact_ids(self, times, windows):
        """Closed contact windows with one-ULP protection for boundary arithmetic."""
        times = np.asarray(times, float)
        result = np.full(times.shape, -1, dtype=int)
        for index, (a, b) in enumerate(self.windows(windows)):
            lower = np.nextafter(a * self.duration, -np.inf)
            upper = np.nextafter(b * self.duration, np.inf)
            result[(times >= lower) & (times <= upper)] = index
        return result

    def contact_mask(self, times, windows):
        return self.contact_ids(times, windows) >= 0


def curve(track, rhythm, times, *, width):
    fields(track, ('keys', 'values', 'interpolation'), optional=('slopes',), label='curve')
    if not isinstance(track['keys'], list) or len(track['keys']) < 2:
        raise ValueError('Curves need at least two keys')
    keys = np.asarray([rhythm.fraction(k) for k in track['keys']])
    if np.any(np.diff(keys) <= 0):
        raise ValueError('Curve keys must be strictly increasing after event resolution')
    shape = (len(keys), width) if width else (len(keys),)
    values = numbers(track['values'], shape, label='curve.values')
    samples = np.clip(np.asarray(times, float) / rhythm.duration, keys[0], keys[-1])
    mode = track['interpolation']
    if mode == 'hermite':
        if 'slopes' not in track:
            raise ValueError('Hermite curves require explicit slopes in normalized-time units')
        slopes = numbers(track['slopes'], shape, label='curve.slopes')
        return CubicHermiteSpline(keys, values, slopes, axis=0)(samples)
    if 'slopes' in track:
        raise ValueError('Slopes are only valid for Hermite interpolation')
    if mode == 'pchip':
        return PchipInterpolator(keys, values, axis=0)(samples)
    if mode not in ('linear', 'smooth'):
        raise ValueError('Interpolation must be pchip, hermite, linear or smooth')
    index = np.clip(np.searchsorted(keys, samples, side='right') - 1, 0, len(keys) - 2)
    weight = (samples - keys[index]) / (keys[index + 1] - keys[index])
    if mode == 'smooth':
        weight = smooth(weight)
    if width:
        weight = weight[..., None]
    return values[index] + weight * (values[index + 1] - values[index])
