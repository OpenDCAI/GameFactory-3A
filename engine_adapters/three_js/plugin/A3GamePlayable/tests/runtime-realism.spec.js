import { describe, it, expect, vi } from 'vitest';
import * as THREE from 'three';
import { A3GameRuntimeHost, disposeObject3D } from '../src/engine/runtime-host.js';
import { createSkyGradient, createSunLight } from '../src/engine/visual-kit.js';

const hostDouble = (options = {}) => {
  const host = new A3GameRuntimeHost(options);
  host.scene = new THREE.Scene();
  host.camera = new THREE.PerspectiveCamera();
  return host;
};

describe('fixed simulation and rendering', () => {
  it.each([30, 60, 120])('advances the same simulation at %i Hz', (hz) => {
    const host = hostDouble();
    const times = [];
    let position = 0;
    host.onTick((dt, time) => { position += dt * 3; times.push(time); });
    for (let i = 0; i < hz * 2; i++) host.tick(1 / hz);
    expect(position).toBeCloseTo(6, 10);
    expect(times).toHaveLength(120);
    times.forEach((time, index) => expect(time).toBeCloseTo((index + 1) / 60, 10));
  });
  it('rejects invalid timing and bounds catch-up work', () => {
    const host = hostDouble({ maxSubSteps: 2 });
    expect(() => host.tick(Infinity)).toThrow(RangeError);
    expect(() => host.tick(NaN)).toThrow(RangeError);
    expect(() => host.tick(-1)).toThrow(RangeError);
    expect(() => hostDouble({ fixedTimeStep: Infinity })).toThrow(RangeError);
    host.tick(2);
    expect(host.lastSubSteps).toBe(2);
    expect(host.elapsedSeconds).toBeCloseTo(2 / 60);
    expect(host.droppedSeconds).toBeGreaterThan(1.9);
    expect(host.interpolationAlpha).toBeGreaterThanOrEqual(0);
    expect(host.interpolationAlpha).toBeLessThan(1);
  });
  it('keeps explicit variable timestep compatibility', () => {
    const host = hostDouble({ fixedTimeStep: 0 });
    const callback = vi.fn();
    host.onTick(callback);
    host.tick(0.04);
    expect(callback).toHaveBeenCalledWith(0.04, 0.04);
  });
  it('interpolates only the displayed transform and uses it for screenshots', () => {
    const host = hostDouble();
    const mesh = new THREE.Object3D();
    host.add(mesh);
    host.interpolateObject(mesh);
    host.onTick(() => { mesh.position.x += 1; });
    const rendered = [];
    host.renderer = { render: () => rendered.push(mesh.position.x), domElement: { toDataURL: () => 'png' } };
    host.tick(1 / 40);
    expect(rendered[0]).toBeCloseTo(0.5);
    expect(mesh.position.x).toBe(1);
    expect(host.captureFrame()).toBe('png');
    expect(rendered[1]).toBeCloseTo(0.5);
    expect(mesh.position.x).toBe(1);
    mesh.position.x = 10;
    host.resetInterpolation(mesh);
    host.captureFrame();
    expect(rendered[2]).toBe(10);
  });
  it('restores simulation transforms even when rendering throws', () => {
    const host = hostDouble();
    const object = new THREE.Object3D();
    host.interpolateObject(object);
    host.onTick(() => { object.position.y = 5; });
    host.renderer = { render: () => { throw new Error('GPU'); } };
    expect(() => host.tick(1 / 40)).toThrow('GPU');
    expect(object.position.y).toBe(5);
  });
  it('runs display callbacks once after multiple simulation steps', () => {
    const host = hostDouble();
    const order = [];
    host.onTick(() => order.push('physics'));
    const unsubscribe = host.onRender(() => order.push('render'));
    host.tick(1 / 30);
    expect(order).toEqual(['physics', 'physics', 'render']);
    unsubscribe();
    host.tick(0);
    expect(order).toHaveLength(3);
  });
});

describe('sky and directional light agreement', () => {
  it('includes standard output conversion without manual gamma', () => {
    const sky = createSkyGradient();
    expect(sky.material.fragmentShader).toContain('#include <tonemapping_fragment>');
    expect(sky.material.fragmentShader).toContain('#include <colorspace_fragment>');
    expect(sky.material.fragmentShader).not.toContain('smoothstep(0.0, -0.10');
    disposeObject3D(sky);
  });
  it('updates an existing sky and marked sun while preserving other lights', () => {
    const host = hostDouble();
    host.skyDome = createSkyGradient();
    const sun = createSunLight({ position: { x: 0, y: 10, z: 0 } });
    const fill = new THREE.DirectionalLight();
    fill.position.set(1, 2, 3);
    host.add(sun);
    host.add(fill);
    host.setEnvironment({ sunPosition: { x: 1, y: 1, z: 0 } });
    const direction = host.getSunDirection();
    expect(sun.position.clone().normalize().distanceTo(direction)).toBeLessThan(1e-8);
    expect(host.skyDome.userData.uniforms.uSunDirection.value.distanceTo(direction)).toBeLessThan(1e-8);
    expect(fill.position.toArray()).toEqual([1, 2, 3]);
    expect(() => host.setEnvironment({ sunPosition: { x: 0, y: 0, z: 0 } })).toThrow();
  });
  it('honours translated targets and transformed parents', () => {
    const host = hostDouble();
    const parent = new THREE.Group();
    parent.position.set(3, 2, 1);
    parent.rotation.y = 0.5;
    parent.scale.set(2, 1, 3);
    const sun = createSunLight({ position: { x: 0, y: 10, z: 0 }, target: { x: 4, y: 0, z: -2 } });
    parent.add(sun);
    host.add(parent);
    host.setEnvironment({ sunPosition: { x: -1, y: 2, z: 1 } });
    const actual = sun.getWorldPosition(new THREE.Vector3()).sub(sun.target.getWorldPosition(new THREE.Vector3())).normalize();
    expect(actual.distanceTo(host.getSunDirection())).toBeLessThan(1e-8);
  });
  it('refreshes procedural IBL only at the configured interval', () => {
    const host = hostDouble({ environmentUpdateInterval: 0.05 });
    host.generatedEnvironment = new THREE.Texture();
    const refresh = vi.spyOn(host, 'refreshEnvironment').mockImplementation(() => { host.environmentAge = 0; return host; });
    host.tick(1 / 30);
    expect(refresh).not.toHaveBeenCalled();
    host.tick(1 / 60);
    expect(refresh).toHaveBeenCalledOnce();
  });
  it('invokes effect lifecycle disposal', () => {
    const root = new THREE.Group();
    const child = new THREE.Object3D();
    child.userData.dispose = vi.fn();
    root.add(child);
    disposeObject3D(root);
    expect(child.userData.dispose).toHaveBeenCalledOnce();
  });
});
