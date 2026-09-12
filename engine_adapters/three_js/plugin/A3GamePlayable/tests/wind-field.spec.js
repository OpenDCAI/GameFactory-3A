import { describe, it, expect } from 'vitest';
import * as THREE from 'three';
import { A3GameWindField, bindVegetationWind, A3GameRuntimeHost, A3GameParticleSystem, createCloudLayer, createSkyGradient, createLightningArc } from '../src/index.js';

const host = config => { const h = new A3GameRuntimeHost({ wind: config }); h.scene = new THREE.Scene(); return h; };

describe('shared air velocity', () => {
  it('is seeded, bounded and changes smoothly', () => {
    const a = new A3GameWindField({ velocity: [3, 0, 0], gustStrength: 0.5, seed: 9 });
    const b = new A3GameWindField({ velocity: [3, 0, 0], gustStrength: 0.5, seed: 9 });
    expect(a.sample(new THREE.Vector3(1, 2, 3), 4).toArray()).toEqual(b.sample(new THREE.Vector3(1, 2, 3), 4).toArray());
    a.set({ velocity: [-3, 0, 0] });
    expect(a.velocity.x).toBe(3);
    a.update(1 / 60);
    expect(a.velocity.x).toBeGreaterThan(2.8);
    expect(() => a.set({ velocity: [Infinity, 0, 0] })).toThrow();
  });
  it.each([30, 60, 120])('uses the host simulation clock at %i Hz', hz => {
    const h = host({ velocity: [3, 0, 1], gustStrength: 0 });
    for (let i = 0; i < hz; i++) h.tick(1 / hz);
    expect(h.wind.elapsedSeconds).toBeCloseTo(1);
    expect(h.wind.displacement.toArray()[0]).toBeCloseTo(3);
  });
  it('advects particles towards air velocity, not relative to their emitter', () => {
    const h = host({ velocity: [4, 0, 0] });
    const p = new A3GameParticleSystem({ maxParticles: 1, lifetime: 10, speed: 0, windResponse: 2 });
    p.emit(1, { position: [0, 0, 0] });
    p.attachToHost(h);
    for (let i = 0; i < 60; i++) h.tick(1 / 60);
    expect(p.velocities[0]).toBeCloseTo(4 * (1 - Math.exp(-2)), 4);
    expect(p.positions[0]).toBeGreaterThan(2);
    p.dispose();
    expect(h.tickListeners.size).toBe(0);
  });
  it('moves both cloud axes while keeping plant roots stationary', () => {
    const h = host({ velocity: [2, 0, -1], heightShear: 0 });
    const clouds = createCloudLayer({ count: 1, radius: 100 });
    h.add(clouds);
    const start = clouds.children[0].position.clone();
    clouds.userData.attachToHost(h);
    const tree = new THREE.Group(); tree.position.set(1, 0, 2); h.add(tree);
    const stop = bindVegetationWind(tree, h);
    for (let i = 0; i < 60; i++) h.tick(1 / 60);
    expect(clouds.children[0].position.x - start.x).toBeCloseTo(2);
    expect(clouds.children[0].position.z - start.z).toBeCloseTo(-1);
    expect(tree.position.toArray()).toEqual([1, 0, 2]);
    expect(tree.quaternion.angleTo(new THREE.Quaternion())).toBeGreaterThan(0);
    stop(); clouds.userData.dispose();
    expect(h.tickListeners.size).toBe(0);
  });
  it('uses accumulated wind displacement for both sky noise layers', () => {
    const wind = new A3GameWindField({ velocity: [3, 0, 2] });
    wind.update(1);
    const sky = createSkyGradient();
    sky.userData.update(1, wind);
    expect(sky.userData.uniforms.uWindDriven.value).toBe(1);
    expect(sky.userData.uniforms.uWindOffset.value.x).toBeLessThan(0);
  });
  it('changes electrical paths without advecting bolt endpoints', () => {
    const arc = createLightningArc({ from: [-2, 3, 0], to: [2, 3, 0] });
    arc.userData.update(0.1);
    expect(arc.userData.getState().active).toBe(true);
    arc.userData.update(0.6);
    expect(arc.userData.getState().active).toBe(false);
    expect(arc.children).toHaveLength(48);
    arc.userData.dispose();
  });
});
