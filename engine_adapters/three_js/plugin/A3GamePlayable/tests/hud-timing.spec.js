import { afterEach, describe, expect, it, vi } from 'vitest';
import { A3GameHudLayer } from '../src/engine/hud-layer.js';

function setup() {
  const element = { style: {}, dataset: {}, remove: vi.fn() };
  const hud = Object.create(A3GameHudLayer.prototype);
  Object.assign(hud, { widgets: new Map([['help', { element, kind: 'panel' }]]), slots: new Map(), fadeTasks: new Map(), root: { remove: vi.fn() } });
  const listeners = new Set();
  const host = { onRender(fn) { listeners.add(fn); return () => listeners.delete(fn); } };
  const tick = (dt) => { for (const listener of [...listeners]) listener(dt); };
  return { hud, element, host, tick, listeners };
}

afterEach(() => vi.useRealTimers());

describe('deterministic HUD fades', () => {
  it('advances only with supplied host time, even during a slow recording', () => {
    vi.useFakeTimers();
    const { hud, element, host, tick, listeners } = setup();
    hud.autoHide('help', { host, after: 2, fade: 1 });
    vi.advanceTimersByTime(30000);
    expect(element.style.opacity).toBe('1');
    tick(2.5);
    expect(Number(element.style.opacity)).toBeCloseTo(0.5);
    tick(0.5);
    expect(hud.getState().help.visible).toBe(false);
    expect(listeners.size).toBe(0);
    expect(hud.fadeTasks.size).toBe(0);
  });
  it.each([20, 30, 60])('finishes at the same simulated time at %i fps', (fps) => {
    const { hud, host, tick } = setup();
    hud.autoHide('help', { host, after: 1, fade: 1 });
    for (let i = 0; i < fps * 2; i++) tick(1 / fps);
    expect(hud.getState().help.visible).toBe(false);
  });
  it('can show the panel again and cancel the obsolete fade', () => {
    const { hud, element, host, tick, listeners } = setup();
    hud.autoHide('help', { host, after: 0, fade: 1 });
    tick(0.5);
    hud.setVisible('help', true);
    tick(2);
    expect(element.style.opacity).toBe('1');
    expect(hud.getState().help.visible).toBe(true);
    expect(listeners.size).toBe(0);
  });
  it('replaces old tasks and unsubscribes on remove/dispose', () => {
    const { hud, host, listeners } = setup();
    hud.autoHide('help', { host });
    hud.autoHide('help', { host });
    expect(listeners.size).toBe(1);
    hud.remove('help');
    expect(listeners.size).toBe(0);
    hud.dispose();
    expect(hud.root.remove).toHaveBeenCalledOnce();
  });
  it('supports standalone HUDs and cancels wall-clock work', () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'performance'] });
    const { hud } = setup();
    hud.autoHide('help', { after: 0.1, fade: 0.1 });
    vi.advanceTimersByTime(224);
    expect(hud.getState().help.visible).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
    hud.autoHide('help');
    hud.dispose();
    expect(vi.getTimerCount()).toBe(0);
  });
  it('rejects invalid timing without creating a task', () => {
    const { hud, host } = setup();
    expect(() => hud.autoHide('help', { host, after: NaN })).toThrow(RangeError);
    expect(() => hud.autoHide('help', { host, fade: -1 })).toThrow(RangeError);
    expect(hud.fadeTasks.size).toBe(0);
  });
});
