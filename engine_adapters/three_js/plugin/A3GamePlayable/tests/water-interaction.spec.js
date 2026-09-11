import { afterEach, describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';
import { createWaterSurface } from '../src/engine/visual-kit.js';
import { A3GameWaterBody } from '../src/engine/water-body.js';
import { A3GameSceneLoader } from '../src/engine/scene-loader.js';
import { A3GameRuntimeHost } from '../src/engine/runtime-host.js';

const resources = [];
afterEach(() => { for (const resource of resources.splice(0).reverse()) resource.dispose(); });
const surface = options => {
  const mesh = createWaterSurface({ waveHeight: 0, segments: 2, depthResolution: 4, ...options });
  resources.push(mesh.userData);
  return mesh;
};
const body = options => {
  const result = new A3GameWaterBody(options);
  resources.push(result);
  return result;
};
const hostStub = () => {
  const ticks = new Set();
  return { ticks, onTick: callback => { ticks.add(callback); return () => ticks.delete(callback); } };
};
const entity = (id, y, parameters = {}) => ({
  entity_id: id, artifact_id: id, collision: true, parameters,
  transform: { position: { x: 0, y, z: 0 } },
});
async function scene({ entities = [], water = {}, ground = { size: 20 } } = {}) {
  const host = new A3GameRuntimeHost();
  host.scene = new THREE.Scene();
  host.camera = new THREE.PerspectiveCamera();
  const loader = new A3GameSceneLoader({ host, assets: {
    tryInstantiate: async () => ({ object: new THREE.Mesh(new THREE.BoxGeometry(10, 1, 10), new THREE.MeshBasicMaterial()) }),
  } });
  resources.push(loader);
  await loader.buildWorld({ camera: { controls: 'none' }, entities,
    environment: { ground, water: [{ water_id: 'pond', size: 8, position: { y: 2 }, options: { waveHeight: 0, depthResolution: 4 }, ...water }] },
  });
  return { host, loader, water: loader.waterSurfaces.get('pond') };
}

describe('finite water depth and currents', () => {
  it('does not mistake a collision bridge for the river bed', async () => {
    const { loader, water } = await scene({ entities: [entity('bridge', 4)] });
    expect(loader.collisionTargets).toContain(loader.getEntityObject('bridge'));
    expect(water.userData.sampleDepth(0, 0)).toBeCloseTo(2);
    expect(loader.terrainTargets).toHaveLength(1);
  });
  it('prefers marked terrain to ground and explicitly selected terrain to marked terrain', async () => {
    const entities = [entity('marked-bed', -1.5, { waterTerrain: true }), entity('selected-bed', -2.5), entity('bridge', 4)];
    const marked = await scene({ entities });
    expect(marked.water.userData.sampleBottom(0, 0)).toBeCloseTo(-1);
    const explicit = await scene({ entities, water: { terrain_entity_ids: ['selected-bed'], position: { y: 2 }, options: { waveHeight: 0, depthResolution: 4 } } });
    expect(explicit.water.userData.sampleDepth(0, 0)).toBeCloseTo(4);
    explicit.loader.getEntityObject('selected-bed').position.y = 5.5;
    explicit.water.userData.refreshDepth();
    expect(explicit.water.userData.sampleBottom(0, 0)).toBeCloseTo(6);
    expect(explicit.water.userData.sampleDepth(0, 0)).toBe(0);
  });
  it('defaults omitted position components and rejects missing explicit terrain', async () => {
    const { water } = await scene({ ground: {}, water: { position: { x: 5 }, options: { waveHeight: 0, depth: 3, depthResolution: 4 } } });
    expect(water.position.toArray()).toEqual([5, 0, 0]);
    expect(water.userData.sampleDepth(5, 0)).toBeCloseTo(3);
    const direct = surface({ position: { z: 7 }, depth: 2 });
    expect(direct.position.toArray()).toEqual([0, 0, 7]);
    expect(direct.userData.sampleDepth(0, 7)).toBeCloseTo(2);
    await expect(scene({ water: { terrain_entity_ids: ['missing'] } })).rejects.toThrow('Missing water terrain');
  });
  it('clips displaced columns against both terrain and surface', () => {
    const water = surface({ terrainHeight: () => -1 });
    const sample = (y, draft) => water.userData.computeBuoyancy({ points: [[0, y, 0]], draft, damping: 0 });
    expect(sample(-3, 1).force.length()).toBe(0);
    expect(sample(-1.5, 1).submergedFraction).toBeCloseTo(0.5);
    expect(sample(-2, 4).submergedFraction).toBeCloseTo(0.25);
    expect(sample(1, 1).force.length()).toBe(0);
    const outside = water.userData.computeBuoyancy({ points: [[1000, -1, 0]] });
    expect(outside.force.length()).toBe(0);
    expect(water.userData.computeBuoyancy({ points: [] }).submergedFraction).toBe(0);
    const invalid = water.userData.computeBuoyancy({ points: [[0, -0.5, 0]], velocity: [NaN, Infinity, 0], volume: Infinity, damping: NaN });
    expect(invalid.force.toArray().every(Number.isFinite)).toBe(true);
  });
  it('samples world currents independently of texture flow and damps relative to the current', () => {
    const water = surface({ current: [2, 0, -1], flowSpeed: 999 });
    water.rotation.z = 0.8;
    expect(water.userData.sampleVelocity(new THREE.Vector3()).toArray()).toEqual([2, 0, -1]);
    const force = water.userData.computeBuoyancy({ points: [[0, -1, 0]], velocity: [2, 0, -1] });
    expect(force.force.x).toBeCloseTo(0);
    expect(force.force.z).toBeCloseTo(0);
    const still = water.userData.computeBuoyancy({ points: [[0, -1, 0]] });
    expect(still.force.x).toBeGreaterThan(0);
    expect(still.force.z).toBeLessThan(0);
    expect(water.userData.sampleVelocity(new THREE.Vector3(1000, 0, 0))).toBeNull();
    const callback = vi.fn((position, time, target) => target.set(position.x + time, 0, 1));
    const field = surface({ current: callback });
    const target = new THREE.Vector3();
    expect(field.userData.sampleVelocity(new THREE.Vector3(1, 0, 0), 3, target)).toBe(target);
    expect(target.toArray()).toEqual([4, 0, 1]);
  });
});

describe('lightweight floating body', () => {
  it('integrates light bodies to equilibrium and heavy bodies to the bottom without surface snapping', () => {
    const water = surface({ depth: 4, rippleCapacity: 0 });
    const lightObject = new THREE.Object3D(); lightObject.position.set(-2, 2, 0);
    const heavyObject = new THREE.Object3D(); heavyObject.position.set(2, 2, 0);
    const light = body({ water, object: lightObject, mass: 400 });
    const heavy = body({ water, object: heavyObject, mass: 1500 });
    light.update(1 / 60);
    expect(lightObject.position.y).toBeGreaterThan(1.99);
    for (let i = 0; i < 1200; i += 1) { light.update(1 / 60); heavy.update(1 / 60); }
    expect(lightObject.position.y).toBeCloseTo(0.1, 2);
    expect(heavyObject.position.y).toBeCloseTo(-3.5, 3);
    expect(heavy.grounded).toBe(true);
    expect(light.velocity.length()).toBeLessThan(0.01);
  });
  it('applies configurable exponential horizontal friction only during bottom contact', () => {
    const water = surface({ depth: 4, rippleCapacity: 0 });
    const groundedObject = new THREE.Object3D(); groundedObject.position.y = -3.5;
    const frictionlessObject = groundedObject.clone();
    const airborneObject = new THREE.Object3D(); airborneObject.position.y = 5;
    const grounded = body({ water, object: groundedObject, mass: 1500, damping: 0, velocity: [2, 0, -1] });
    const frictionless = body({ water, object: frictionlessObject, mass: 1500, damping: 0, velocity: [2, 0, -1], groundFriction: 0 });
    const airborne = body({ water, object: airborneObject, mass: 1500, damping: 0, velocity: [2, 0, -1] });
    grounded.update(1 / 120); frictionless.update(1 / 120); airborne.update(1 / 120);
    expect(grounded.grounded).toBe(true);
    expect(grounded.velocity.x).toBeCloseTo(2 * Math.exp(-4 / 120), 10);
    expect(grounded.velocity.z).toBeCloseTo(-Math.exp(-4 / 120), 10);
    expect(grounded.velocity.y).toBe(0);
    expect(frictionless.velocity.toArray()).toEqual([2, 0, -1]);
    expect(airborne.grounded).toBe(false);
    expect(airborne.velocity.x).toBe(2);
    expect(airborne.velocity.z).toBe(-1);
    expect(grounded.force.y).toBeCloseTo(frictionless.force.y);
    expect(grounded.submergedFraction).toBe(frictionless.submergedFraction);
  });
  it('reduces bottom sliding under a steady current while preserving bottom contact', () => {
    const water = surface({ depth: 4, current: [0.3, 0, 0.08], rippleCapacity: 0 });
    const roughObject = new THREE.Object3D(); roughObject.position.y = -3.5;
    const smoothObject = roughObject.clone();
    const rough = body({ water, object: roughObject, mass: 1500 });
    const smooth = body({ water, object: smoothObject, mass: 1500, groundFriction: 0 });
    for (let i = 0; i < 600; i += 1) { rough.update(1 / 60); smooth.update(1 / 60); }
    expect(rough.grounded).toBe(true);
    expect(rough.velocity.x).toBeLessThan(smooth.velocity.x * 0.75);
    expect(rough.velocity.z).toBeLessThan(smooth.velocity.z * 0.75);
    expect(roughObject.position.x).toBeLessThan(smoothObject.position.x * 0.75);
    for (const object of [roughObject, smoothObject]) {
      const box = new THREE.Box3(new THREE.Vector3(-0.5, -0.5, -0.5), new THREE.Vector3(0.5, 0.5, 0.5));
      box.applyMatrix4(object.matrixWorld);
      expect(box.min.y).toBeCloseTo(-4, 6);
    }
  });
  it('emits one ripple and callback on entry rather than every submerged step', () => {
    const water = surface({ depth: 5 });
    const ripple = vi.spyOn(water.userData, 'addRipple');
    const entered = vi.fn();
    const object = new THREE.Object3D(); object.position.y = 1.2;
    const floating = body({ water, object, mass: 1500, onEnterWater: entered });
    for (let i = 0; i < 180; i += 1) { water.userData.update(1 / 60); floating.update(1 / 60); }
    expect(entered).toHaveBeenCalledOnce();
    expect(ripple).toHaveBeenCalledOnce();
    expect(entered.mock.calls[0][0].impactSpeed).toBeGreaterThan(0);
    expect(water.userData.getState().activeRipples).toBe(1);
  });
  it('uses torque and box inertia and remains frame-partition independent', () => {
    const water = surface({ rippleCapacity: 0 });
    const first = new THREE.Object3D(); first.rotation.z = 0.15;
    const second = first.clone();
    const a = body({ water, object: first, mass: 500, size: [2, 1, 2] });
    const b = body({ water, object: second, mass: 500, size: [2, 1, 2] });
    a.update(1 / 120);
    expect(a.angularVelocity.z).toBeLessThan(0);
    for (let i = 0; i < 59; i += 1) a.update(1 / 120);
    for (let i = 0; i < 15; i += 1) b.update(1 / 30);
    expect(first.position.distanceTo(second.position)).toBeLessThan(1e-9);
    expect(first.quaternion.angleTo(second.quaternion)).toBeLessThan(1e-7);
  });
  it('supports transformed parents and detaches without disposing shared water', () => {
    const water = surface({ rippleCapacity: 0 });
    const parent = new THREE.Group(); parent.position.set(5, 2, 0); parent.rotation.y = 0.5;
    const object = new THREE.Object3D(); parent.add(object);
    const floating = body({ water, object });
    const host = hostStub();
    floating.attachToHost(host);
    floating.attachToHost(host);
    expect(host.ticks.size).toBe(1);
    for (const tick of host.ticks) tick(1 / 60);
    expect(object.getWorldPosition(new THREE.Vector3()).y).toBeLessThan(2);
    floating.dispose(); floating.dispose();
    expect(host.ticks.size).toBe(0);
    expect(water.userData.getState().disposed).toBe(false);
  });
});

describe('wind coupling and paused reflections', () => {
  it('smooths local wind without rotating swell or jumping phase at large elapsed times', () => {
    const water = surface({ waveHeight: 0.12, normalMap: new THREE.Texture() });
    const host = hostStub();
    let direction = new THREE.Vector3(30, 0, 0);
    host.wind = { elapsedSeconds: 42, sample: vi.fn((position, time, target) => target.copy(direction)) };
    water.userData.update(5000);
    water.userData.attachToHost(host);
    const waves = water.material.userData.waterUniforms.a3WaterWaves.value;
    const swell = waves.slice(0, 2).map(wave => wave.toArray());
    const before = water.userData.sampleHeight(1, 1);
    water.userData.update(0);
    expect(water.userData.sampleHeight(1, 1)).toBe(before);
    water.userData.update(0.0001);
    expect(Math.abs(water.userData.sampleHeight(1, 1) - before)).toBeLessThan(0.001);
    for (let i = 0; i < 600; i += 1) water.userData.update(1 / 60);
    const height = water.userData.sampleHeight(1, 1);
    const normal = water.material.normalMap.offset.clone();
    direction = new THREE.Vector3(-30, 0, 0);
    water.userData.update(0.0001);
    expect(Math.abs(water.userData.sampleHeight(1, 1) - height)).toBeLessThan(0.001);
    expect(water.material.normalMap.offset.distanceTo(normal)).toBeLessThan(0.001);
    expect(waves.slice(0, 2).map(wave => wave.toArray())).toEqual(swell);
    expect(host.wind.sample.mock.calls[0][1]).toBe(42);
    expect(waves[2].z).toBeGreaterThan(0.12 * 0.35);
    expect(water.userData.sampleVelocity(new THREE.Vector3()).x).toBe(0);
  });
  it('refreshes paused reflection when the camera moves and restores render state', () => {
    const water = surface({ reflection: true });
    const world = new THREE.Scene(); world.add(water);
    const camera = new THREE.PerspectiveCamera(); camera.position.set(0, 3, 5); camera.lookAt(0, 0, 0);
    const renderer = { xr: { enabled: true }, shadowMap: { autoUpdate: true },
      getRenderTarget: () => null, setRenderTarget: vi.fn(), autoClear: true,
      state: { buffers: { depth: { setMask: vi.fn() } } }, render: vi.fn(),
    };
    water.onBeforeRender(renderer, world, camera);
    expect(water.userData.getState().reflectionPasses).toBe(1);
    water.onBeforeRender(renderer, world, camera);
    expect(water.userData.getState().reflectionPasses).toBe(1);
    camera.position.x += 1;
    water.onBeforeRender(renderer, world, camera);
    expect(water.userData.getState().reflectionPasses).toBe(2);
    expect(water.visible).toBe(true);
    expect(renderer.xr.enabled).toBe(true);
    expect(renderer.shadowMap.autoUpdate).toBe(true);
    expect(water.userData.getState().elapsedSeconds).toBe(0);
    water.userData.update(0.01);
    camera.position.x += 1;
    water.onBeforeRender(renderer, world, camera);
    expect(water.userData.getState().reflectionPasses).toBe(2);
    water.onBeforeRender(renderer, world, camera);
    expect(water.userData.getState().reflectionPasses).toBe(3);
  });
});
