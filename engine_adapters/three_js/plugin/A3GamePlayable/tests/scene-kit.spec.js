import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import {
  directionToYaw, yawToDirection, footprintCorners, distanceToPolyline,
  createGroundRibbon, createFacadeTexture, createSurfaceTextures, createSkyGradient, createDistantRange,
} from '../src/index.js';

const v = (x, y, z) => new THREE.Vector3(x, y, z);

describe('scene coordinate helpers', () => {
  it.each([[0, 0, -1], [1, 0, 0], [-1, 0, 0], [0, 0, 1], [40, 0, -34]])('faces direction (%s,%s,%s)', (x, y, z) => {
    const direction = v(x, y, z).normalize();
    const yaw = directionToYaw(direction);
    expect(yawToDirection(yaw).distanceTo(direction)).toBeLessThan(1e-8);
  });
  it('uses positive pitch for upward aim and rejects missing direction', () => {
    expect(yawToDirection(0, Math.PI / 6).y).toBeCloseTo(0.5);
    expect(() => directionToYaw(v(0, 2, 0))).toThrow(RangeError);
    expect(() => yawToDirection(NaN)).toThrow(RangeError);
  });
  it('matches THREE rotation rather than a sign-reversed 2D convention', () => {
    const spec = { x: 5, z: -3, width: 4, depth: 1, rotation: Math.PI / 3 };
    const corners = footprintCorners(spec);
    const matrix = new THREE.Matrix4().makeRotationY(spec.rotation);
    const expected = [[-2, -0.5], [2, -0.5], [2, 0.5], [-2, 0.5]].map(([x, z]) => v(x, 0, z).applyMatrix4(matrix).add(v(spec.x, 0, spec.z)));
    corners.forEach((point, i) => {
      expect(point.x).toBeCloseTo(expected[i].x);
      expect(point.z).toBeCloseTo(expected[i].z);
    });
  });
  it('measures finite segments, endpoints, closed loops and degenerate paths', () => {
    expect(distanceToPolyline([5, 3], [[0, 0], [10, 0]])).toBe(3);
    expect(distanceToPolyline([12, 0], [[0, 0], [10, 0]])).toBe(2);
    expect(distanceToPolyline([0, 1], [[0, 0], [0, 0]])).toBe(1);
    expect(distanceToPolyline([1, 1], [[0, 0], [2, 0], [2, 2]], true)).toBe(0);
    expect(distanceToPolyline([1, 1], [])).toBe(Infinity);
  });
});

describe('terrain-conforming ground ribbons', () => {
  it('projects both edges onto the same height function used by gameplay', () => {
    const heightAt = (x, z) => x * 0.2 + Math.sin(z);
    const path = createGroundRibbon([[0, 0], [8, 0], [8, -6]], { width: 2, segments: 4, heightAt, lift: 0.04 });
    const position = path.geometry.attributes.position;
    for (let i = 0; i < position.count; i++) {
      expect(position.getY(i)).toBeCloseTo(heightAt(position.getX(i), position.getZ(i)) + 0.04, 5);
    }
    expect(path.userData.pathLength).toBeCloseTo(14);
    path.geometry.dispose(); path.material.dispose();
  });
  it('faces upward with a single-sided material and uses metre-scaled UVs', () => {
    const path = createGroundRibbon([[0, 0], [10, 0]], { width: 4, segments: 2, tileLength: 2 });
    const normals = path.geometry.attributes.normal;
    for (let i = 0; i < normals.count; i++) expect(normals.getY(i)).toBeGreaterThan(0.99);
    const uv = path.geometry.attributes.uv;
    expect(uv.getX(1)).toBe(2);
    expect(uv.getY(uv.count - 1)).toBe(5);
    path.geometry.dispose(); path.material.dispose();
  });
  it('closes a loop without duplicating a zero-length segment', () => {
    const path = createGroundRibbon([[0, 0], [10, 0], [10, 10], [0, 0]], { closed: true });
    const p = path.geometry.attributes.position;
    for (let axis = 0; axis < 3; axis++) {
      expect(p.array[axis]).toBeCloseTo(p.array[(p.count - 2) * 3 + axis]);
    }
    path.geometry.dispose(); path.material.dispose();
  });
  it('rejects invalid paths and non-finite heights', () => {
    expect(() => createGroundRibbon([[0, 0], [0, 0]])).toThrow(RangeError);
    expect(() => createGroundRibbon([[0, 0], [1, 0]], { width: -2 })).toThrow(RangeError);
    expect(() => createGroundRibbon([[0, 0], [1, 0]], { heightAt: () => NaN })).toThrow(RangeError);
  });
});

describe('distant range continuity', () => {
  it('shares peak heights across segments and the closing seam', () => {
    const segments = 96;
    const range = createDistantRange({ segments, seed: 19, roughness: 0.8 });
    const p = range.geometry.attributes.position;
    for (let i = 0; i < segments; i++) {
      const right = v().fromBufferAttribute(p, i * 6 + 2);
      const nextLeft = v().fromBufferAttribute(p, ((i + 1) % segments) * 6 + 5);
      expect(right.distanceTo(nextLeft)).toBeLessThan(1e-5);
    }
    range.geometry.dispose(); range.material.dispose();
  });
});

describe('filtered procedural colours', () => {
  it('encodes surface albedo once in sRGB and leaves numeric maps linear', () => {
    const set = createSurfaceTextures({ size: 32, color: 0x808080, jointColor: 0x808080, contrast: 0 });
    const index = 16 * 32 + 16;
    const expected = new THREE.Color(0x808080).multiplyScalar(0.72 + set.height[index] * 0.38);
    const byte = set.map.image.data[index * 4] / 255;
    const decoded = new THREE.Color().setRGB(byte, byte, byte, THREE.SRGBColorSpace);
    expect(decoded.r).toBeCloseTo(expected.r, 2);
    expect(set.normalMap.colorSpace).toBe(THREE.NoColorSpace);
    expect(set.roughnessMap.colorSpace).toBe(THREE.NoColorSpace);
    for (const map of [set.map, set.normalMap, set.roughnessMap]) {
      expect(map.generateMipmaps).toBe(true);
      expect(map.minFilter).toBe(THREE.LinearMipmapLinearFilter);
      expect(map.magFilter).toBe(THREE.LinearFilter);
    }
    set.dispose();
  });
  it('builds repeatable facade windows without the DOM', () => {
    const a = createFacadeTexture({ width: 32, height: 64, columns: 4, rows: 8, seed: 3 });
    const b = createFacadeTexture({ width: 32, height: 64, columns: 4, rows: 8, seed: 3 });
    const c = createFacadeTexture({ width: 32, height: 64, columns: 4, rows: 8, seed: 9 });
    expect(a.image.data).toEqual(b.image.data);
    expect(a.image.data).not.toEqual(c.image.data);
    expect(a.colorSpace).toBe(THREE.SRGBColorSpace);
    expect(a.generateMipmaps).toBe(true);
    expect(() => createFacadeTexture({ width: Infinity })).toThrow(RangeError);
    expect(() => createFacadeTexture({ columns: 10000 })).toThrow(RangeError);
    a.dispose(); b.dispose(); c.dispose();
  });
  it('guards a disabled sun disc against equal smoothstep bounds', () => {
    const sky = createSkyGradient({ sunSize: 0 });
    expect(sky.material.fragmentShader).toContain('if (uSunSize > 0.0)');
    sky.geometry.dispose(); sky.material.dispose();
  });
});
