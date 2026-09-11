import { afterEach, describe, it, expect, vi } from 'vitest';
import * as THREE from 'three';
import { A3GameAssetLibrary, A3GameCollisionProbe, createWaterSurface, disposeObject3D } from '../src/index.js';

afterEach(() => vi.unstubAllGlobals());
const v = (x, y, z) => new THREE.Vector3(x, y, z);
const box = (x, y, z) => new THREE.Mesh(new THREE.BoxGeometry(x, y, z), new THREE.MeshStandardMaterial());

describe('finite sphere collision contracts', () => {
  it('detects edge grazing missed by a centre ray without inventing wider hits', () => {
    const probe = new A3GameCollisionProbe({ targets: [box(0.1, 2, 2)] });
    const hit = probe.sweepSphere(v(-5, 0, 1.15), v(5, 0, 1.15), { radius: 0.2 });
    expect(hit.hit).toBe(true);
    expect(hit.point.z).toBeCloseTo(1);
    expect(hit.normal.z).toBeGreaterThan(0);
    expect(hit.distance).toBeLessThan(5);
    expect(probe.sweepSphere(v(-5, 0, 1.3), v(5, 0, 1.3), { radius: 0.2 }).hit).toBe(false);
  });
  it('does not tunnel through a thin wall at high speed', () => {
    const probe = new A3GameCollisionProbe({ targets: [box(0.1, 2, 2)] });
    const hit = probe.sweepSphere(v(-10000, 0, 0), v(10000, 0, 0), { radius: 0.1 });
    expect(hit.hit).toBe(true);
    expect(hit.centre.x).toBeCloseTo(-0.15, 5);
    expect(hit.point.x).toBeCloseTo(-0.05, 5);
    expect(hit.normal.x).toBeCloseTo(-1);
  });
  it('reports stationary initial overlap and respects ignored ancestors', () => {
    const parent = new THREE.Group();
    parent.add(box(0.1, 2, 2));
    const probe = new A3GameCollisionProbe({ targets: [parent] });
    const hit = probe.sweepSphere(v(-0.1, 0, 0), v(-0.1, 0, 0), { radius: 0.2 });
    expect(hit.timeOfImpact).toBe(0);
    expect(hit.penetrationDepth).toBeCloseTo(0.15);
    expect(probe.sweepSphere(v(-1, 0, 0), v(1, 0, 0), { ignore: [parent] }).hit).toBe(false);
  });
  it('queries each transformed instance with world-space radius', () => {
    const instances = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial(), 2);
    instances.setMatrixAt(0, new THREE.Matrix4().makeTranslation(0, 10, 0));
    instances.setMatrixAt(1, new THREE.Matrix4().compose(v(5, 0, 0), new THREE.Quaternion(), v(0.2, 2, 1)));
    const hit = new A3GameCollisionProbe({ targets: [instances] }).sweepSphere(v(0, 0, 0), v(10, 0, 0), { radius: 0.2 });
    expect(hit.instanceId).toBe(1);
    expect(hit.centre.x).toBeCloseTo(4.7);
  });
  it('blocks jumping into a low ceiling and lands again', () => {
    const ground = box(20, 0.2, 20); ground.position.y = -0.1;
    const ceiling = box(20, 0.1, 20); ceiling.position.y = 2.1;
    const probe = new A3GameCollisionProbe({ targets: [ground, ceiling] });
    const state = { position: v(0, 0, 0), velocityY: 0, grounded: true };
    let highest = 0;
    for (let i = 0; i < 120; i++) {
      probe.stepCharacter(state, v(0, 0, 0), 1 / 60, { height: 1.8, radius: 0.35, jump: i === 0 });
      highest = Math.max(highest, state.position.y);
    }
    expect(highest).toBeLessThanOrEqual(0.251);
    expect(state.position.y).toBeCloseTo(0, 3);
    expect(state.grounded).toBe(true);
  });
});

describe('water sampling and buoyancy contracts', () => {
  it('keeps GPU vertices fixed while analytic waves advance', () => {
    const water = createWaterSurface({ quality: 'standard', size: 10 });
    const vertices = water.geometry.attributes.position.array.slice();
    const before = water.userData.sampleHeight(1, 1);
    water.userData.update(0.5);
    expect(water.geometry.attributes.position.array).toEqual(vertices);
    expect(water.userData.sampleHeight(1, 1)).not.toBe(before);
    expect(water.material.metalness).toBe(0);
    expect(water.material.ior).toBeCloseTo(1.333);
    disposeObject3D(water);
  });
  it('samples transformed water and rejects coordinates outside its bounds', () => {
    const water = createWaterSurface({ size: 10, waveHeight: 0, terrainHeight: () => 1 });
    water.position.set(20, 4, 30);
    water.scale.set(2, 3, 1);
    expect(water.userData.sampleHeight(20, 30)).toBeCloseTo(4);
    expect(water.userData.sampleDepth(20, 30)).toBeCloseTo(3);
    expect(water.userData.sampleNormal(20, 30).y).toBeCloseTo(1);
    expect(water.userData.sampleHeight(100, 100)).toBeNull();
    disposeObject3D(water);
  });
  it('has finite ripple capacity and decaying ripples', () => {
    const water = createWaterSurface({ rippleCapacity: 3 });
    for (let i = 0; i < 10; i++) water.userData.addRipple(0, 0);
    expect(water.userData.getState().activeRipples).toBe(3);
    water.userData.update(10);
    expect(water.userData.getState().activeRipples).toBe(0);
    disposeObject3D(water);
  });
  it('computes symmetric displaced volume forces without inventing horizontal thrust', () => {
    const water = createWaterSurface({ waveHeight: 0 });
    const force = water.userData.computeBuoyancy({ points: [v(-1, -1, 0), v(1, -1, 0)], centerOfMass: v(0, -1, 0), volume: 1, draft: 0.5 });
    expect(force.force.y).toBeCloseTo(9810);
    expect(force.force.x).toBe(0);
    expect(force.torque.length()).toBeCloseTo(0);
    expect(force.submergedFraction).toBe(1);
    disposeObject3D(water);
  });
});

function cachedLibrary(binding = false) {
  const library = new A3GameAssetLibrary();
  const entry = { artifact_id: 'asset-1', asset_id: 'crate', url: '/crate.glb', representation: 'gltf_binary', orientation: { scale_hint_metres: 2 }, material_bindings: binding ? ['/binding.json'] : [] };
  const object = box(10, 100, 10);
  library.byArtifactId.set(entry.artifact_id, entry);
  library.byAssetId.set(entry.asset_id, [entry]);
  library.cache.set(entry.artifact_id, Promise.resolve({ object, animations: [], entry }));
  return { library, object };
}

describe('asset realism contracts', () => {
  it('preserves normalization under later world transform assignment', async () => {
    const { library } = cachedLibrary();
    const instance = await library.tryInstantiate('crate', { ground: true });
    instance.object.scale.set(1, 1, 1);
    expect(new THREE.Box3().setFromObject(instance.object).getSize(v(0, 0, 0)).y).toBeCloseTo(2);
    instance.object.scale.set(2, 2, 2);
    expect(new THREE.Box3().setFromObject(instance.object).getSize(v(0, 0, 0)).y).toBeCloseTo(4);
    expect(new THREE.Box3().setFromObject(instance.object).min.y).toBeCloseTo(0);
    disposeObject3D(instance.object);
    await library.dispose();
  });
  it('automatically upgrades material types with proper map spaces and glTF orientation', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ material_type: 'MeshPhysicalMaterial', targets: ['/crate.glb'], textures: { map: '/color.png', normalMap: '/normal.png' }, scalars: { clearcoat: 1, normalScale: 0.4 } }) })));
    const { library, object } = cachedLibrary(true);
    library.textureLoader.loadAsync = async () => new THREE.Texture();
    const instance = await library.instantiate('crate');
    expect(instance.object.material.isMeshPhysicalMaterial).toBe(true);
    expect(instance.object.material.normalScale.toArray()).toEqual([0.4, 0.4]);
    expect(instance.object.material.map.colorSpace).toBe(THREE.SRGBColorSpace);
    expect(instance.object.material.normalMap.colorSpace).toBe(THREE.NoColorSpace);
    expect(instance.object.material.map.flipY).toBe(false);
    expect(object.material.type).toBe('MeshStandardMaterial');
    disposeObject3D(instance.object);
    await library.dispose();
  });
  it('isolates instance materials and does not release shared geometry on unloading one', async () => {
    const { library, object } = cachedLibrary();
    const first = await library.instantiate('crate');
    const second = await library.instantiate('crate');
    first.object.material.roughness = 0.1;
    expect(second.object.material.roughness).not.toBe(0.1);
    const released = vi.fn();
    object.geometry.addEventListener('dispose', released);
    disposeObject3D(first.object);
    expect(released).not.toHaveBeenCalled();
    disposeObject3D(second.object);
    await library.dispose();
    expect(released).toHaveBeenCalledOnce();
  });
});
