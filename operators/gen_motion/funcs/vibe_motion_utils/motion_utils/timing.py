"""Named event timing, action phases and any number of independent contact schedules.

Time references (accepted everywhere a time is expected: curve keys, segments, windows, ballistic stages):
  number            fraction of the duration (time_unit='fraction', default) or seconds (time_unit='seconds')
  'start' / 'end'   0 / duration
  '<event>'         a named event (same unit as numbers)
  '<phase>.start' | '<phase>.end' | '<phase>.mid' | '<phase>@<f>'   phase boundaries / fraction f in [0,1] of a phase
  '<t>s'            absolute seconds, e.g. '1.25s'
  any of the above + '+<x>s' / '-<x>s'   offset in seconds, e.g. 'jump.end-0.1s'
Phases: {name: [start_ref, end_ref]} describe the coarse action states (e.g. crouch / flight / land); detail inside a
phase is then designed with per-window endpoint values (curve segments, effector keys / plants).
Beats: {name: {start, count, period (s) | end, swing? in [-0.45, 0.45]}} subdivide a stretch of time into `count`
consecutive generated phases 'name[1]' ... 'name[count]' (usable wherever a phase is: {'phase': 'stomp[2]'},
'stomp[2].mid', 'stomp[2]@0.3', 'stomp[3].start-0.05s'). swing > 0 lengthens odd beats and shortens even ones
(long-short groove); start / end may reference events and phases.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

import numpy as np
from scipy.interpolate import PchipInterpolator

_SECONDS = re.compile(r'^(?P<t>\d*\.?\d+)s$')
_OFFSET = re.compile(r'^(?P<base>.+?)(?P<off>[+-]\d*\.?\d+)s$')
_PHASE = re.compile(r'^(?P<phase>[^@]+?)(?:\.(?P<edge>start|end|mid)|@(?P<frac>\d*\.?\d+))$')


def smooth(value):
    x = np.clip(value, 0., 1.)
    return x*x*x*(10.+x*(-15.+6.*x))


def interval(t, start, end):
    if not np.isfinite([start, end]).all() or end <= start:
        raise ValueError('Time interval must be finite and increasing')
    return np.clip((np.asarray(t)-start)/(end-start), 0., 1.)


def key_curve(t, keys, values):
    keys, values, t = np.asarray(keys, float), np.asarray(values, float), np.asarray(t, float)
    if keys.ndim != 1 or len(keys) < 2 or np.any(np.diff(keys) <= 0) or len(values) != len(keys):
        raise ValueError('Expected ordered keys and matching values')
    if not all(np.isfinite(a).all() for a in (keys, values, t)):
        raise ValueError('Non-finite trajectory')
    return PchipInterpolator(keys, values, axis=0)(np.clip(t, keys[0], keys[-1]))


@dataclass(frozen=True)
class Rhythm:
    duration: float
    fps: float
    events: dict[str, float]
    phases: dict = field(default_factory=dict)
    time_unit: str = 'fraction'
    beats: dict = field(default_factory=dict)

    def __post_init__(self):
        if not np.isfinite([self.duration, self.fps]).all() or self.duration <= 0 or not 1 <= self.fps <= 240:
            raise ValueError('Positive duration and fps in [1,240] required')
        if round(self.duration*self.fps) < 1 or not isinstance(self.events, dict):
            raise ValueError('Invalid sampling duration/events')
        if self.time_unit not in ('fraction', 'seconds'):
            raise ValueError("time_unit must be 'fraction' or 'seconds'")
        top = 1. if self.time_unit == 'fraction' else self.duration
        for name, value in self.events.items():
            if not isinstance(name, str) or not name.strip() or name in ('start', 'end'):
                raise ValueError('Event IDs must be nonempty; start/end are reserved')
            if isinstance(value, bool) or not np.isfinite(value) or not 0 <= value <= top+1e-9:
                raise ValueError(f'Event {name} must be a finite time in [0,{top:g}] ({self.time_unit})')
        if not isinstance(self.phases, dict):
            raise ValueError('phases must be a mapping {name: [start, end]}')
        object.__setattr__(self, '_all_phases', dict(self.phases))
        for name, window in self.phases.items():
            if not isinstance(name, str) or not name.strip() or name in self.events or name in ('start', 'end') \
                    or any(c in name for c in '.@+[]'):
                raise ValueError(f'Invalid phase name {name!r} (unique, not an event, no ".", "@", "+", "[", "]")')
            if not isinstance(window, (list, tuple)) or len(window) != 2:
                raise ValueError(f'Phase {name} needs [start, end]')
            a, b = (self.seconds(v) for v in window)
            if b <= a:
                raise ValueError(f'Phase {name} must have end > start')
        if not isinstance(self.beats, dict):
            raise ValueError('beats must be a mapping {name: {start, count, period | end, swing?}}')
        for name, spec in self.beats.items():
            self._add_beats(name, spec)

    def _add_beats(self, name, spec):
        if not isinstance(name, str) or not name.strip() or name in self.events or name in self._all_phases \
                or name in ('start', 'end') or any(c in name for c in '.@+[]'):
            raise ValueError(f'Invalid beat name {name!r}')
        if not isinstance(spec, dict) or set(spec)-{'start', 'end', 'period', 'count', 'swing'} \
                or not {'start', 'count'} <= set(spec) or ('end' in spec) == ('period' in spec):
            raise ValueError(f'Beat {name}: needs start, count and exactly one of end / period (s), optional swing')
        count = spec['count']
        if isinstance(count, bool) or not isinstance(count, (int, np.integer)) or not 1 <= count <= 400:
            raise ValueError(f'Beat {name}: count must be an integer in [1, 400]')
        swing = float(spec.get('swing', 0.))
        if not np.isfinite(swing) or abs(swing) > .45:
            raise ValueError(f'Beat {name}: swing in [-0.45, 0.45]')
        a = self.seconds(spec['start'])
        weights = np.array([1.+swing if i % 2 == 0 else 1.-swing for i in range(count)])
        if 'end' in spec:
            b = self.seconds(spec['end'])
            if b <= a:
                raise ValueError(f'Beat {name}: end must be after start')
            period = (b-a)/weights.sum()
        else:
            period = spec['period']
            if isinstance(period, bool) or not np.isfinite(period) or period <= 0:
                raise ValueError(f'Beat {name}: period must be positive seconds')
        edges = a+np.concatenate([[0.], np.cumsum(weights*float(period))])
        if edges[-1] > self.duration+1e-6:
            raise ValueError(f'Beat {name}: beats end after the duration ({edges[-1]:.3f}s)')
        for i in range(count):
            self._all_phases[f'{name}[{i+1}]'] = [f'{edges[i]:.9f}s', f'{min(edges[i+1], self.duration):.9f}s']

    @property
    def all_phases(self):
        """authored phases plus the generated beat phases."""
        return self._all_phases

    def sample(self):
        return np.linspace(0., self.duration, round(self.duration*self.fps)+1)

    def _number(self, value):
        if isinstance(value, bool) or not np.isfinite(value):
            raise ValueError('Time reference must be finite')
        t = float(value)*self.duration if self.time_unit == 'fraction' else float(value)
        if not -1e-9 <= t <= self.duration*(1+1e-9)+1e-9:
            raise ValueError('Time reference outside [0, duration]')
        return min(max(t, 0.), self.duration)

    def _ref(self, text):
        if text in self.events:
            return self._number(self.events[text])
        if text == 'start':
            return 0.
        if text == 'end':
            return self.duration
        match = _SECONDS.match(text)
        if match:
            return float(match['t'])
        match = _PHASE.match(text)
        if match and match['phase'] in self._all_phases:
            a, b = (self.seconds(v) for v in self._all_phases[match['phase']])
            f = {'start': 0., 'end': 1., 'mid': .5}[match['edge']] if match['edge'] else float(match['frac'])
            if not 0 <= f <= 1:
                raise ValueError(f'Phase fraction outside [0,1]: {text}')
            return a+f*(b-a)
        match = _OFFSET.match(text)
        if match:
            return self._ref(match['base'])+float(match['off'])
        raise ValueError(f'Unknown timing event: {text}')

    def fraction(self, name):
        return self.seconds(name)/self.duration

    def seconds(self, value):
        if isinstance(value, str):
            t = self._ref(value.strip())
            if not -1e-9 <= t <= self.duration+1e-9:
                raise ValueError(f'Time reference outside [0, duration]: {value}')
            return min(max(t, 0.), self.duration)
        if isinstance(value, bool) or not isinstance(value, (int, float, np.integer, np.floating)):
            raise ValueError(f'Invalid time reference: {value!r}')
        return self._number(value)

    def window(self, spec):
        """(start_s, end_s) of a window given as {'phase': name} or {'start': ref, 'end': ref} (end > start)."""
        if 'phase' in spec:
            if spec['phase'] not in self._all_phases:
                raise ValueError(f'Unknown phase: {spec["phase"]}')
            a, b = (self.seconds(v) for v in self._all_phases[spec['phase']])
        else:
            a, b = self.seconds(spec['start']), self.seconds(spec['end'])
        if b <= a:
            raise ValueError('Window end must be after start')
        return a, b

    def contact_mask(self, times, windows):
        mask = np.zeros(np.asarray(times).shape, bool)
        last = -1.
        for start, end in windows:
            a, b = self.seconds(start), self.seconds(end)
            if b < a or a <= last:
                raise ValueError('Contact intervals must be ordered, nonoverlapping and increasing')
            mask |= (np.asarray(times) >= a) & (np.asarray(times) <= b)
            last = b
        return mask
