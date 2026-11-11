import { describe, it, expect } from 'vitest';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import * as T from 'three';
import {
  compileCameraStrategy,
  validateCameraStrategy,
  inspectCameraTargets,
} from '../src/index.js';
import * as engine from '../src/engine/index.js';

const common = {
  schema_version: '2.0',
  coordinate_system: 'Y_UP_RIGHT_HANDED',
  projection: { type: 'perspective', fov_y_deg: 50, near: 0.1, far: 100 },
};
const focus = {
  subjects: [{ id: 'subject', weight: 1 }],
  axes: ['x', 'y', 'z'],
  anchor: [0, 0, 0],
};
const view = (strategy) => ({
  ...structuredClone(common),
  strategy: structuredClone(strategy),
});
const context = (scene) => ({
  scene,
  aspect: 16 / 9,
  duration_sec: 8,
  fps: 24,
});
const fixture = () => {
  const scene = new T.Scene(),
    parent = new T.Group(),
    subject = new T.Object3D();
  subject.name = 'subject';
  parent.position.set(10, 2, 3);
  subject.position.set(2, 1, -1);
  parent.add(subject);
  scene.add(parent);
  scene.updateMatrixWorld(true);
  return { scene, parent, subject };
};
const close = (actual, expected) =>
  assert.ok(actual.distanceTo(new T.Vector3(...expected)) < 1e-10);

describe('public camera strategy contract', () => {
  it('exposes one camera implementation from the package and engine entry', () => {
    expect(engine.compileCameraStrategy).toBe(compileCameraStrategy);
    expect(engine.validateCameraStrategy).toBe(validateCameraStrategy);
    expect(engine.inspectCameraTargets).toBe(inspectCameraTargets);
  });

  it('keeps a fixed spherical view unchanged and preserves the input', () => {
    const input = view({
      type: 'fixed',
      pose: {
        target: [1, 2, 3],
        azimuth_deg: 0,
        elevation_deg: 0,
        distance: 10,
      },
    });
    const before = structuredClone(input),
      rig = compileCameraStrategy(T, input, context(new T.Scene()));
    rig.applyAt(0);
    close(rig.camera.position, [1, 2, 13]);
    const matrix = rig.camera.matrixWorld.toArray();
    for (const time of [8, 2, 6, 2]) {
      rig.applyAt(time);
      expect(rig.camera.matrixWorld.toArray()).toEqual(matrix);
    }
    expect(input).toEqual(before);
    input.strategy.pose.azimuth_deg = 90;
    const right = compileCameraStrategy(T, input, context(new T.Scene()));
    right.applyAt(0);
    close(right.camera.position, [11, 2, 3]);
  });

  it('follows weighted world positions and keeps unfollowed axes anchored', () => {
    const { scene, subject } = fixture(),
      anchor = new T.Object3D();
    anchor.name = 'anchor';
    scene.add(anchor);
    scene.updateMatrixWorld(true);
    const weighted = {
      ...focus,
      subjects: [
        { id: 'subject', weight: 0.7 },
        { id: 'anchor', weight: 0.3 },
      ],
      axes: ['x', 'z'],
      anchor: [0, 4, 0],
    };
    const rig = compileCameraStrategy(
      T,
      view({ type: 'follow', focus: weighted, offset: [3, 5, 7] }),
      context(scene),
    );
    rig.applyAt(2);
    close(rig.camera.position, [11.4, 9, 8.4]);
    subject.position.x += 2;
    scene.updateMatrixWorld(true);
    rig.applyAt(2);
    close(rig.camera.position, [12.8, 9, 8.4]);
  });

  it('interpolates relative follow offsets at absolute time without changing the lens', () => {
    const { scene, subject } = fixture();
    const rig = compileCameraStrategy(
      T,
      view({
        type: 'follow_move',
        focus,
        start_offset: [3, 4, 5],
        end_offset: [-2, 4, 5],
        easing: 'smootherstep',
      }),
      context(scene),
    );
    for (const [time, offset] of [
      [0, [3, 4, 5]],
      [4, [0.5, 4, 5]],
      [8, [-2, 4, 5]],
    ]) {
      rig.applyAt(time);
      close(
        rig.camera.position
          .clone()
          .sub(subject.getWorldPosition(new T.Vector3())),
        offset,
      );
      expect(rig.camera.fov).toBe(50);
    }
    rig.applyAt(2);
    const matrix = rig.camera.matrixWorld.toArray();
    rig.applyAt(7);
    rig.applyAt(2);
    expect(rig.camera.matrixWorld.toArray()).toEqual(matrix);
  });

  it('interpolates an orbit, including its look target and endpoint pose', () => {
    const rig = compileCameraStrategy(
      T,
      view({
        type: 'orbit',
        easing: 'linear',
        start: {
          target: [0, 1, 0],
          azimuth_deg: 0,
          elevation_deg: 0,
          distance: 10,
        },
        end: {
          target: [2, 1, 0],
          azimuth_deg: 90,
          elevation_deg: 0,
          distance: 6,
        },
      }),
      context(new T.Scene()),
    );
    rig.applyAt(0);
    close(rig.camera.position, [0, 1, 10]);
    rig.applyAt(4);
    close(rig.camera.position, [1 + 8 / Math.sqrt(2), 1, 8 / Math.sqrt(2)]);
    rig.applyAt(8);
    close(rig.camera.position, [8, 1, 0]);
  });

  it('moves position and target while keeping a wide lens independent of movement', () => {
    const input = view({
      type: 'move',
      easing: 'linear',
      start: { position: [3, 4, 8], target: [0, 1, 0] },
      end: { position: [-3, 6, 6], target: [2, 1, 0] },
    });
    input.projection.fov_y_deg = 60;
    const rig = compileCameraStrategy(T, input, context(new T.Scene()));
    const pose = rig.applyAt(4);
    close(rig.camera.position, [0, 5, 7]);
    expect(pose.target).toEqual([1, 1, 0]);
    expect(rig.camera.fov).toBe(60);
  });

  it('replays position/quaternion tracks and rejects incomplete coverage', () => {
    const input = view({
      type: 'recorded',
      frames: [
        { time_sec: 0, position: [0, 1, 4], quaternion: [0, 0, 0, 1] },
        { time_sec: 8, position: [8, 1, 4], quaternion: [0, 1, 0, 0] },
      ],
    });
    const rig = compileCameraStrategy(T, input, context(new T.Scene()));
    rig.applyAt(4);
    close(rig.camera.position, [4, 1, 4]);
    expect(Math.abs(rig.camera.quaternion.y - Math.SQRT1_2)).toBeLessThan(
      1e-12,
    );
    input.strategy.frames[1].time_sec = 1;
    expect(() =>
      compileCameraStrategy(T, input, context(new T.Scene())),
    ).toThrow(/cover/);
  });

  it('constructs perspective and orthographic lenses from the same contract', () => {
    const input = view({
      type: 'fixed',
      pose: {
        target: [1, 2, 3],
        azimuth_deg: 35,
        elevation_deg: 25,
        distance: 10,
      },
    });
    const perspective = compileCameraStrategy(T, input, {
      ...context(new T.Scene()),
      aspect: 2,
    });
    perspective.applyAt(0);
    expect(perspective.camera.isPerspectiveCamera).toBe(true);
    expect(perspective.camera.fov).toBe(50);
    expect(perspective.camera.aspect).toBe(2);
    input.projection = {
      type: 'orthographic',
      vertical_size: 12,
      near: 0,
      far: 100,
    };
    const orthographic = compileCameraStrategy(T, input, {
      ...context(new T.Scene()),
      aspect: 2,
    });
    orthographic.applyAt(0);
    expect(
      orthographic.camera.position.distanceTo(perspective.camera.position),
    ).toBeLessThan(1e-10);
    expect([
      orthographic.camera.left,
      orthographic.camera.right,
      orthographic.camera.top,
      orthographic.camera.bottom,
    ]).toEqual([-12, 12, 6, -6]);
  });

  it('rejects missing and ambiguous object names instead of choosing an arbitrary actor', () => {
    const { scene } = fixture(),
      input = view({ type: 'follow', focus, offset: [1, 2, 3] });
    const duplicate = new T.Object3D();
    duplicate.name = 'subject';
    scene.add(duplicate);
    expect(() => compileCameraStrategy(T, input, context(scene))).toThrow(
      /Ambiguous follow subject/,
    );
    expect(() =>
      compileCameraStrategy(T, input, context(new T.Scene())),
    ).toThrow(/Missing follow subject/);
  });

  it('rejects invalid weights, axes, fields and singular relative camera paths', () => {
    const input = view({
      type: 'follow_move',
      focus,
      start_offset: [3, 4, 5],
      end_offset: [-2, 4, 5],
      easing: 'linear',
    });
    for (const mutate of [
      (x) => (x.strategy.focus.subjects[0].weight = 0),
      (x) => (x.strategy.focus.axes = ['x', 'x']),
      (x) => (x.strategy.end_offset = [-3, 4, -5]),
      (x) => (x.strategy.easing = 'spring'),
      (x) => (x.strategy.responsePerSec = 8),
      (x) => (x.projection.near = 200),
      (x) => (x.framing = false),
      (x) => (x.schema_version = '1.0'),
    ]) {
      const invalid = structuredClone(input);
      mutate(invalid);
      expect(() => validateCameraStrategy(invalid)).toThrow();
    }
    const { scene } = fixture(),
      rig = compileCameraStrategy(T, input, context(scene));
    for (const time of [-1, 9, NaN, Infinity])
      expect(() => rig.applyAt(time)).toThrow();
    expect(() =>
      compileCameraStrategy(T, input, { ...context(scene), fps: 0 }),
    ).toThrow();
  });

  it('does not mutate scene transforms and repeats poses after out-of-order evaluation', () => {
    const { scene, parent, subject } = fixture();
    const rig = compileCameraStrategy(
      T,
      view({ type: 'follow', focus, offset: [3, 4, 5] }),
      context(scene),
    );
    const before = [
      parent.matrixWorld.toArray(),
      subject.matrixWorld.toArray(),
    ];
    rig.applyAt(2);
    const pose = rig.camera.matrixWorld.toArray();
    for (const time of [8, 0, 6, 2]) rig.applyAt(time);
    expect(rig.camera.matrixWorld.toArray()).toEqual(pose);
    expect([
      parent.matrixWorld.toArray(),
      subject.matrixWorld.toArray(),
    ]).toEqual(before);
  });

  it('validates the single documented JSON example', () => {
    const guide = fs.readFileSync(
      new URL(
        '../../../../../agent_skills/game-feel/references/camera-view.md',
        import.meta.url,
      ),
      'utf8',
    );
    const example = JSON.parse(guide.match(/```json\s*([\s\S]*?)```/)[1]);
    expect(validateCameraStrategy(example).strategy.type).toBe('follow');
  });
});

describe('visible geometry framing', () => {
  const camera = () => {
    const c = new T.PerspectiveCamera(60, 16 / 9, 0.1, 100);
    c.position.set(0, 0, 5);
    c.lookAt(0, 0, 0);
    c.updateMatrixWorld(true);
    return c;
  };
  it('does not report empty assembly AABB corners as visible clipping', () => {
    const scene = new T.Scene(),
      group = new T.Group();
    group.name = 'assembly';
    scene.add(group);
    for (const position of [
      [-4, 0, -4],
      [2, 0, 2],
    ]) {
      const mesh = new T.Mesh(new T.BoxGeometry(0.2, 0.2, 0.2));
      mesh.position.fromArray(position);
      group.add(mesh);
    }
    const c = camera();
    scene.updateMatrixWorld(true);
    const box = new T.Box3().setFromObject(group);
    const emptyCorner = new T.Vector3(box.min.x, box.min.y, box.max.z).project(
      c,
    );
    expect(Math.abs(emptyCorner.x)).toBeGreaterThan(1);
    expect(
      inspectCameraTargets(T, scene, c, ['assembly'])[0].insideSafeFrame,
    ).toBe(true);
    group.children[1].position.x = 6;
    scene.updateMatrixWorld(true);
    expect(
      inspectCameraTargets(T, scene, c, ['assembly'])[0].insideFrustum,
    ).toBe(false);
  });

  it('ignores hidden descendants and reports hidden, empty and missing subjects', () => {
    const scene = new T.Scene(),
      group = new T.Group();
    group.name = 'assembly';
    scene.add(group);
    group.add(new T.Mesh(new T.BoxGeometry(0.2, 0.2, 0.2)));
    const hidden = new T.Mesh(new T.BoxGeometry());
    hidden.position.x = 100;
    hidden.visible = false;
    group.add(hidden);
    scene.updateMatrixWorld(true);
    const c = camera();
    expect(
      inspectCameraTargets(T, scene, c, ['assembly'])[0].insideSafeFrame,
    ).toBe(true);
    group.visible = false;
    expect(inspectCameraTargets(T, scene, c, ['assembly'])[0]).toMatchObject({
      hasVisibleGeometry: false,
      finite: false,
      insideFrustum: false,
    });
    expect(inspectCameraTargets(T, scene, c, ['absent'])[0]).toEqual({
      name: 'absent',
      missing: true,
    });
  });

  it('streams large meshes without a spread-argument limit and flags unsupported instancing', () => {
    const scene = new T.Scene(),
      geometry = new T.BufferGeometry();
    geometry.setAttribute(
      'position',
      new T.BufferAttribute(new Float32Array(150000 * 3), 3),
    );
    const mesh = new T.Mesh(geometry);
    mesh.name = 'dense';
    scene.add(mesh);
    scene.updateMatrixWorld(true);
    expect(
      inspectCameraTargets(T, scene, camera(), ['dense'])[0],
    ).toMatchObject({
      finite: true,
      supported: true,
      insideSafeFrame: true,
    });
    const instances = new T.InstancedMesh(
      new T.BoxGeometry(),
      new T.MeshBasicMaterial(),
      2,
    );
    instances.name = 'instances';
    scene.add(instances);
    scene.updateMatrixWorld(true);
    expect(
      inspectCameraTargets(T, scene, camera(), ['instances'])[0],
    ).toMatchObject({
      supported: false,
      insideFrustum: false,
    });
  });
});
