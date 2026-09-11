import { describe, it, expect } from 'vitest';
import * as THREE from 'three';
import { A3GameRuntimeHost } from '../src/engine/runtime-host.js';
import { A3GameSceneLoader } from '../src/engine/scene-loader.js';

function setup() {
  const host = new A3GameRuntimeHost();
  host.scene = new THREE.Scene();
  host.camera = new THREE.PerspectiveCamera();
  const loader = new A3GameSceneLoader({ host, assets: { tryLoadTexture: async () => null } });
  return { host, loader };
}
const world = () => ({
  world_id: 'water-test',
  camera: { controls: 'none' },
  environment: {
    ground: { size: 20 },
    sun: { x: 1, y: 1, z: 0 },
    water: [{ water_id: 'pond', size: 10, position: { x: 1, y: 2, z: 1 }, options: { waveHeight: 0 } }],
  },
  lights: [{ light_id: 'sun', type: 'DirectionalLight', color: '#ffffff', intensity: 2, position: { x: 0, y: 10, z: 0 } }],
});

describe('world realism integration', () => {
  it('builds water against actual ground and advances it once per simulation step', async () => {
    const { host, loader } = setup();
    await loader.buildWorld(world());
    const water = loader.waterSurfaces.get('pond');
    expect(water).toBeDefined();
    expect(water.userData.sampleDepth(1, 1)).toBeCloseTo(2);
    expect(water.userData.sampleHeight(1, 1)).toBeCloseTo(2);
    expect(loader.collisionTargets).not.toContain(water);
    host.tick(1 / 30);
    expect(water.userData.getState().elapsedSeconds).toBeCloseTo(1 / 30);
    const light = [...host.sunLights.keys()][0];
    expect(light.position.clone().normalize().distanceTo(host.getSunDirection())).toBeLessThan(1e-8);
    loader.dispose();
    expect(water.userData.getState().disposed).toBe(true);
    expect(host.tickListeners.size).toBe(0);
    expect(host.sunLights.size).toBe(0);
  });
  it('does not accumulate surfaces, lights or subscriptions on rebuild', async () => {
    const { host, loader } = setup();
    await loader.buildWorld(world());
    const previous = loader.waterSurfaces.get('pond');
    await loader.buildWorld(world());
    expect(previous.userData.getState().disposed).toBe(true);
    expect(loader.waterSurfaces.size).toBe(1);
    expect(host.tickListeners.size).toBe(1);
    expect(host.sunLights.size).toBe(1);
    loader.dispose();
  });
  it('rejects missing required water textures', async () => {
    const { loader } = setup();
    const spec = world();
    spec.environment.water[0].normal_artifact_id = 'missing';
    await expect(loader.buildWorld(spec)).rejects.toThrow('Missing water normal');
    loader.dispose();
  });
  it('respects an explicitly independent directional light', async () => {
    const { host, loader } = setup();
    const spec = world();
    spec.lights[0].options = { sync_environment: false };
    await loader.buildWorld(spec);
    expect(host.sunLights.size).toBe(0);
    const light = [...loader.ownedObjects].find(object => object.isDirectionalLight);
    expect(light.position.toArray()).toEqual([0, 10, 0]);
    loader.dispose();
  });
});
