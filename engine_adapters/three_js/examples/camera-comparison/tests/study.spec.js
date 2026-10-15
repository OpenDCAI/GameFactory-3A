import { describe, it, expect } from 'vitest';
import { factories, groups, createTake, step, measure, lifecycle, study } from '../evaluate.js';

describe('reference camera trace', () => {
  for (const fps of [30, 60, 120]) {
    it(`keeps the moving player framed at ${fps} Hz`, () => {
      for (const reducedMotion of [false, true]) {
        const options = { fps, reducedMotion };
        const before = measure(groups.reference.static, options).report;
        const after = measure(groups.reference.chase, options).report;
        expect(before.playerFullyInFrameFraction).toBeLessThan(0.8);
        expect(after.playerFullyInFrameFraction).toBeGreaterThan(0.99);
        expect(after.meanPlayerFocusErrorNdc).toBeLessThan(before.meanPlayerFocusErrorNdc / 5);
        expect(after.allPosesFinite).toBe(true);
      }
    });
  }
  it('rejects invalid sample rates and time reversal', () => {
    expect(() => measure(groups.reference.chase, { fps: 0 })).toThrow();
    const take = createTake(groups.reference.chase);
    step(take, 1);
    expect(() => step(take, 0.5)).toThrow();
    expect(() => step(take, NaN)).toThrow();
    take.controller.dispose();
  });
  it('clears chase feedback on pause and prevents writes after disposal', () => {
    expect(lifecycle(groups.reference.chase))
      .toEqual({ pausePreventsWrites: true, resumeMoves: true, disposePreventsWrites: true });
    const pulse = createTake(groups.reference.chase), base = createTake(groups.reference.chase);
    for (let i = 0; i <= 180; i++) { step(pulse, i / 60); step(base, i / 60, false); }
    expect(pulse.camera.fov).not.toBe(base.camera.fov);
    pulse.controller.pause(); base.controller.pause();
    expect(pulse.camera.position.toArray()).toEqual(base.camera.position.toArray());
    expect(pulse.camera.fov).toBe(base.camera.fov);
    pulse.controller.dispose(); base.controller.dispose();
  });
});

describe('unchanged generated camera pair', () => {
  it('keeps both actors in frame with finite poses at 30/60/120 Hz', () => {
    for (const factory of Object.values(factories)) for (const fps of [30, 60, 120]) {
      const { report } = measure(factory, { fps });
      expect(report.allPosesFinite).toBe(true);
      expect(report.bothFullyInFrameFraction).toBeGreaterThan(0.99);
    }
  });
  it('has frame-rate independent final framing within 0.1 mm', () => {
    for (const factory of Object.values(factories)) {
      const a = measure(factory, { fps: 30 }).report.finalPosition;
      const b = measure(factory, { fps: 120 }).report.finalPosition;
      expect(Math.hypot(...a.map((x, i) => x - b[i]))).toBeLessThan(0.0001);
    }
  });
  it('suppresses impact motion and honors pause/disposal', () => {
    for (const factory of Object.values(factories)) {
      expect(measure(factory, { reducedMotion: true }).samples)
        .toEqual(measure(factory, { reducedMotion: true, impacts: false }).samples);
      expect(lifecycle(factory)).toEqual({ pausePreventsWrites: true, resumeMoves: true, disposePreventsWrites: true });
    }
  });
  it('clears treatment pulses and preserves control failures', () => {
    const { conditions } = study();
    for (const field of ['pauseClearsImpact', 'preferencesClearImpact']) {
      expect(conditions.treatment[field].clearsImmediately).toBe(true);
      expect(conditions.control[field].clearsImmediately).toBe(false);
    }
  });
});
