import { afterEach, describe, expect, it, vi } from 'vitest';
import { Group, ShaderLib } from 'three';
import { createSurfaceFlow, createSurfaceFlowTerrain } from '../src/engine/surface-flow.js';
import { disposeObject3D } from '../src/engine/runtime-host.js';

const resources = [];
function flow(options = {}) {
  const value = createSurfaceFlow({ size: [6, 6], resolution: 24, ...options });
  resources.push(value);
  return value;
}
afterEach(() => { for (const object of resources.splice(0)) object.userData.dispose(); });

function expectConserved(state, precision = 10) {
  expect(state.volume + state.outflow).toBeCloseTo(state.initialVolume + state.totalInjected, precision);
  expect(state.minDepth).toBeGreaterThanOrEqual(0);
  expect([...state.depth].every(Number.isFinite)).toBe(true);
}

describe('viscous heightfield transport', () => {
  it('advances real geometry downhill while conserving finite source volume', () => {
    const liquid = flow({ preset: 'blood', heightMap: (x) => -x * 0.22 });
    liquid.userData.addSource({ x: -1.7, z: 0, radius: 0.5, volume: 0.35 });
    const before = liquid.userData.getState();
    const vertices = liquid.geometry.attributes.position.array.slice();
    liquid.userData.update(3);
    const after = liquid.userData.getState();
    expect(after.centerOfMass.x).toBeGreaterThan(before.centerOfMass.x + 0.3);
    expect(after.wetCells).toBeGreaterThan(before.wetCells);
    expect(liquid.geometry.attributes.position.array).not.toEqual(vertices);
    expect(after.outflow).toBe(0);
    expectConserved(after);
  });

  it('collects liquid in a depression instead of moving only texture coordinates', () => {
    const liquid = flow({ preset: 'blood', heightMap: (x, z) => 0.15 * (x * x + z * z) });
    liquid.userData.addSource({ x: -1.8, z: 0, radius: 0.45, volume: 0.3 });
    const before = liquid.userData.getState();
    liquid.userData.update(6);
    const after = liquid.userData.getState();
    expect(Math.abs(after.centerOfMass.x)).toBeLessThan(Math.abs(before.centerOfMass.x) * 0.6);
    expect(after.depth[12 * 24 + 12]).toBeGreaterThan(0.001);
    expectConserved(after);
  });

  it('does not overdraw cells under steep terrain, concentrated sources and high mobility', () => {
    const liquid = flow({ preset: 'blood', mobility: 10000, heightMap: (x, z) => x * 30 + Math.sin(z * 9) * 10 });
    liquid.userData.addSource({ x: 1, z: 0, radius: 0, volume: 10 });
    for (let i = 0; i < 40; i++) { liquid.userData.update(0.05); expectConserved(liquid.userData.getState(), 9); }
  });

  it('injects precisely the requested instantaneous and timed source volumes', () => {
    const liquid = flow({ mobility: 0 });
    liquid.userData.addSource({ volume: 0.1, rate: 0.2, duration: 0.75, radius: 0.4 });
    expect(liquid.userData.getState().volume).toBeCloseTo(0.1, 12);
    liquid.userData.update(2);
    let state = liquid.userData.getState();
    expect(state.totalInjected).toBeCloseTo(0.25, 12);
    expect(state.activeSources).toBe(0);
    const stop = liquid.userData.addSource({ rate: 0.3 });
    liquid.userData.update(0.5); stop(); liquid.userData.update(1);
    state = liquid.userData.getState();
    expect(state.totalInjected).toBeCloseTo(0.4, 12);
    expectConserved(state);
  });

  it('includes initial liquid in the mass balance without counting it as a source', () => {
    const liquid = flow({ initialDepth: (x) => x < 0 ? 0.1 : 0 });
    liquid.userData.update(1);
    const state = liquid.userData.getState();
    expect(state.initialVolume).toBeCloseTo(1.8, 12);
    expect(state.totalInjected).toBe(0);
    expectConserved(state);
  });

  it('records open-boundary outflow explicitly and never leaks a closed boundary', () => {
    const open = flow({ preset: 'blood', boundary: 'open', initialDepth: 0.1 });
    const closed = flow({ preset: 'blood', boundary: 'closed', initialDepth: 0.1 });
    open.userData.update(3); closed.userData.update(3);
    const openState = open.userData.getState(), closedState = closed.userData.getState();
    expect(openState.outflow).toBeGreaterThan(0.1);
    expect(openState.volume).toBeLessThan(closedState.volume);
    expect(closedState.outflow).toBe(0);
    expectConserved(openState); expectConserved(closedState);
  });

  it('makes the low-viscosity red material move faster than lava', () => {
    const settings = { coolingRate: 0, heightMap: (x) => -x * 0.2, sources: [{ x: -1.6, radius: 0.5, volume: 0.3 }] };
    const lava = flow({ ...settings, preset: 'lava' });
    const red = flow({ ...settings, preset: 'blood' });
    lava.userData.update(2); red.userData.update(2);
    expect(red.userData.getState().centerOfMass.x).toBeGreaterThan(lava.userData.getState().centerOfMass.x + 0.15);
    expect(red.material.roughness).toBeLessThan(lava.material.roughness);
    expect(lava.material.emissiveIntensity).toBeGreaterThan(0);
    expect(red.material.emissiveIntensity).toBe(0);
  });

  it('is independent of render frame cadence and preserves fractional time', () => {
    const settings = { preset: 'blood', heightMap: (x) => -x * 0.1, sources: [{ x: -1, rate: 0.2, duration: 0.71 }] };
    const a = flow(settings), b = flow(settings);
    for (let i = 0; i < 60; i++) a.userData.update(1 / 60);
    for (let i = 0; i < 25; i++) b.userData.update(0.04);
    expect(a.userData.getState().depth).toEqual(b.userData.getState().depth);
    expect(a.userData.getState().totalInjected).toBeCloseTo(0.142, 12);
    const before = a.userData.getState().elapsed;
    a.userData.update(1 / 240);
    expect(a.userData.getState().elapsed).toBe(before);
    a.userData.update(1 / 240);
    expect(a.userData.getState().elapsed).toBeCloseTo(before + 1 / 120, 12);
  });

  it('transports heat with mass, cools lava and leaves crust volume intact', () => {
    const liquid = flow({ coolingRate: 0.3, sources: [{ volume: 0.3, radius: 0.5 }] });
    liquid.userData.update(4);
    const state = liquid.userData.getState();
    expect(state.meanTemperature).toBeCloseTo(Math.exp(-1.2), 9);
    expect([...state.temperature].every((value) => value >= 0 && value <= 1)).toBe(true);
    expectConserved(state);
  });

  it('samples world-space bed heights and returns an independent matching bed geometry', () => {
    const liquid = flow({ position: [10, 3, -5], heightMap: (x, z) => 2 + x * 0.1 + z * 0.05, initialDepth: 0.04 });
    const state = liquid.userData.getState(), position = liquid.geometry.attributes.position;
    const terrain = createSurfaceFlowTerrain(liquid); resources.push(terrain);
    for (let i = 0; i < position.count; i++) {
      const worldBed = 2 + (position.getX(i) + 10) * 0.1 + (position.getZ(i) - 5) * 0.05;
      expect(state.bed[i]).toBeCloseTo(worldBed, 6);
      expect(position.getY(i) + 3).toBeCloseTo(state.bed[i] + state.depth[i], 6);
      expect(terrain.geometry.attributes.position.getY(i) + 3).toBeCloseTo(state.bed[i], 6);
    }
    expect(terrain.position).toEqual(liquid.position);
    expect(terrain.geometry).not.toBe(liquid.geometry);
    state.depth.fill(999);
    expect(liquid.userData.getState().maxDepth).toBeCloseTo(0.04, 12);
    liquid.userData.addSource({ x: 10, z: -5, volume: 0.1 });
    expectConserved(liquid.userData.getState());
  });

  it.each(['lava', 'blood'])('retains r185 lighting/color and masks dry visible/shadow fragments for %s', (preset) => {
    const liquid = flow({ preset });
    expect([...liquid.geometry.attributes.a3FlowDepth.array].every((value) => value === 0)).toBe(true);
    const shader = { uniforms: {}, vertexShader: ShaderLib.standard.vertexShader, fragmentShader: ShaderLib.standard.fragmentShader };
    liquid.material.onBeforeCompile(shader);
    expect(shader.fragmentShader).toContain('#include <colorspace_fragment>');
    expect(shader.fragmentShader).toContain('#include <tonemapping_fragment>');
    expect(shader.fragmentShader).toContain('#include <lights_fragment_begin>');
    expect(shader.fragmentShader).toContain('if (vA3FlowDepth <= a3FlowMinDepth) discard;');
    expect(shader.fragmentShader.includes('totalEmissiveRadiance *= a3Glow')).toBe(preset === 'lava');
    expect(shader.vertexShader).toContain('vA3FlowDepth = a3FlowDepth;');
    expect(shader.vertexShader).toContain('vA3FlowXZ = a3FlowCoord;');
    expect(shader.vertexShader).not.toContain('vA3FlowXZ = position.xz;');
    expect(shader.fragmentShader.includes('dFdx(a3Relief)')).toBe(preset === 'lava');
    expect(shader.fragmentShader.includes('a3Molten')).toBe(preset === 'lava');
    expect(shader.uniforms.a3FlowMinDepth.value).toBeGreaterThan(0);
    for (const [material, library] of [[liquid.customDepthMaterial, ShaderLib.depth], [liquid.customDistanceMaterial, ShaderLib.distance]]) {
      const shadow = { uniforms: {}, vertexShader: library.vertexShader, fragmentShader: library.fragmentShader };
      material.onBeforeCompile(shadow);
      expect(shadow.fragmentShader).toContain('if (vA3FlowDepth <= a3FlowMinDepth) discard;');
      expect(shadow.uniforms.a3FlowMinDepth.value).toBe(shader.uniforms.a3FlowMinDepth.value);
    }
    liquid.userData.addSource({ volume: 0.2 });
    expect(Math.max(...liquid.geometry.attributes.a3FlowDepth.array)).toBeGreaterThan(shader.uniforms.a3FlowMinDepth.value);
    expect(liquid.receiveShadow).toBe(true);
  });

  it('detaches host ticks and disposes all owned rendering resources exactly once', () => {
    const liquid = flow(), parent = new Group(); parent.add(liquid);
    const remove = vi.fn(); let tick;
    const host = { onTick: (callback) => { tick = callback; return remove; } };
    liquid.userData.attachToHost(host);
    const geometries = vi.fn(), materials = vi.fn(), depths = vi.fn(), distances = vi.fn();
    liquid.geometry.addEventListener('dispose', geometries);
    liquid.material.addEventListener('dispose', materials);
    liquid.customDepthMaterial.addEventListener('dispose', depths);
    liquid.customDistanceMaterial.addEventListener('dispose', distances);
    tick(0.1); expect(liquid.userData.getState().elapsed).toBeCloseTo(0.1, 12);
    liquid.userData.dispose(); liquid.userData.dispose(); tick(1);
    expect(liquid.parent).toBe(parent); expect(remove).toHaveBeenCalledTimes(1);
    expect(liquid.userData.disposed).toBe(true);
    for (const event of [geometries, materials, depths, distances]) expect(event).toHaveBeenCalledTimes(1);
    expect(liquid.userData.getState().elapsed).toBeCloseTo(0.1, 12);
    expect(liquid.userData.getState().disposed).toBe(true);
  });

  it('lets generic disposal traverse every sibling and release each fluid/bed resource once', () => {
    const parent = new Group(), root = new Group(); parent.add(root);
    const liquids = [flow(), flow({ preset: 'blood' })];
    const events = [];
    for (const liquid of liquids) {
      const terrain = createSurfaceFlowTerrain(liquid); resources.push(terrain);
      root.add(liquid, terrain);
      for (const resource of [liquid.geometry, liquid.material, liquid.customDepthMaterial,
        liquid.customDistanceMaterial, terrain.geometry, terrain.material]) {
        const event = vi.fn(); events.push(event); resource.addEventListener('dispose', event);
      }
    }
    const objects = root.children.slice();
    disposeObject3D(root); disposeObject3D(root);
    expect(root.children).toHaveLength(0);
    expect(root.parent).toBeNull();
    for (const object of objects) { expect(object.userData.disposed).toBe(true); object.userData.dispose(); }
    for (const event of events) expect(event).toHaveBeenCalledTimes(1);
  });

  it('holds a thin lava film below yield while a thicker layer can push downhill', () => {
    const settings = { coolingRate: 0, heightMap: x => -x * 0.15 };
    const thin = flow({ ...settings, initialDepth: 0.02 });
    const thick = flow({ ...settings, initialDepth: 0.35 });
    const before = thin.userData.getState();
    thin.userData.update(2); thick.userData.update(2);
    expect(thin.userData.getState().depth).toEqual(before.depth);
    expect(thick.userData.getState().centerOfMass.x).toBeGreaterThan(0.001);
    expectConserved(thin.userData.getState()); expectConserved(thick.userData.getState());
  });

  it('increases lava resistance with cooling instead of retaining a fixed minimum speed', () => {
    const settings = { coolingRate: 0, heightMap: x => -x * 0.2 };
    const hot = flow(settings), cool = flow(settings);
    for (const [liquid, temperature] of [[hot, 1], [cool, 0.5]]) {
      liquid.userData.addSource({ x: -1.6, radius: 0.5, volume: 0.6, temperature });
      liquid.userData.update(3);
      expectConserved(liquid.userData.getState());
    }
    expect(hot.userData.getState().centerOfMass.x).toBeGreaterThan(cool.userData.getState().centerOfMass.x + 0.01);
    expect(cool.userData.getState().maxDepth).toBeGreaterThan(hot.userData.getState().maxDepth);
  });

  it('preserves solidified mass and allows incoming hot material to soften it', () => {
    const liquid = flow({ coolingRate: 0, heightMap: x => -x * 0.3 });
    liquid.userData.addSource({ x: -1, volume: 0.4, radius: 0.5, temperature: 0.15 });
    const frozen = liquid.userData.getState();
    liquid.userData.update(2);
    expect(liquid.userData.getState().depth).toEqual(frozen.depth);
    liquid.userData.addSource({ x: -1, volume: 0.6, radius: 0.5, temperature: 1 });
    const reheated = liquid.userData.getState();
    liquid.userData.update(2);
    expect(liquid.userData.getState().depth).not.toEqual(reheated.depth);
    expectConserved(liquid.userData.getState());
  });

  it('advects depth-weighted material labels conservatively and returns isolated snapshots', () => {
    const liquid = flow({ coolingRate: 0, heightMap: x => -x * 0.2 });
    liquid.userData.addSource({ x: -1.6, volume: 0.6, radius: 0.5 });
    const before = liquid.userData.getState();
    const moment = (state, axis) => state.depth.reduce((sum, depth, i) => sum + depth * state.materialCoords[2 * i + axis] * state.cellArea, 0);
    liquid.userData.update(3);
    const after = liquid.userData.getState();
    for (const axis of [0, 1]) expect(moment(after, axis)).toBeCloseTo(moment(before, axis), 10);
    expect([...after.materialCoords].every(Number.isFinite)).toBe(true);
    expect(after.materialCoords).not.toEqual(before.materialCoords);
    expect(liquid.geometry.attributes.a3FlowCoord.itemSize).toBe(2);
    const terrain = createSurfaceFlowTerrain(liquid); resources.push(terrain);
    expect(terrain.geometry.attributes.a3FlowCoord).toBeUndefined();
    after.materialCoords.fill(999);
    expect(liquid.userData.getState().materialCoords[0]).not.toBe(999);
    expectConserved(after);
  });

  it('keeps lava rheology, cooling and material motion identical at 30 and 120 Hz', () => {
    const settings = { heightMap: x => -x * 0.2, sources: [{ x: -1, volume: 0.4, rate: 0.2, duration: 0.7 }] };
    const slow = flow(settings), fast = flow(settings);
    for (let i = 0; i < 30; i++) slow.userData.update(1 / 30);
    for (let i = 0; i < 120; i++) fast.userData.update(1 / 120);
    const a = slow.userData.getState(), b = fast.userData.getState();
    expect(a.depth).toEqual(b.depth);
    expect(a.temperature).toEqual(b.temperature);
    expect(a.materialCoords).toEqual(b.materialCoords);
    expectConserved(a); expectConserved(b);
  });

  it('rejects invalid simulation inputs without admitting NaN or negative mass', () => {
    expect(() => flow({ preset: 'unknown' })).toThrow();
    expect(() => flow({ resolution: 4.5 })).toThrow();
    expect(() => flow({ heightMap: () => NaN })).toThrow();
    expect(() => flow({ viscosity: 0 })).toThrow();
    const liquid = flow();
    expect(() => liquid.userData.addSource({ volume: -1 })).toThrow();
    expect(() => liquid.userData.addSource({ x: 99, volume: 1 })).toThrow();
    for (const dt of [NaN, Infinity, -1, 0]) liquid.userData.update(dt);
    expect(liquid.userData.getState().elapsed).toBe(0);
    expect(liquid.userData.getState().volume).toBe(0);
  });
});
