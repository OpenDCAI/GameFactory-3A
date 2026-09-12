/**
 * Look-and-feel building blocks shared by every generated game.
 *
 * A generated three.js game usually has no imported art, so its content
 * comes from primitives. Primitives are not the reason such a game looks
 * like programmer art — these four things are:
 *
 *   1. no image-based lighting, so PBR materials have nothing to reflect
 *      and read as flat plastic;
 *   2. hard 90-degree edges, which never occur on a manufactured object;
 *   3. default material parameters, i.e. `roughness: 1, metalness: 0`
 *      for every surface in the scene;
 *   4. an unfitted shadow camera, which yields either no shadow or a
 *      blocky one.
 *
 * This module fixes 2-4 and is game-neutral;
 * `A3GameRuntimeHost.setEnvironment({ preset })` fixes 1.
 *
 * It also owns the utilities that make an *imported* model usable, since
 * every glTF arrives in whatever unit its author chose.
 */

import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { Reflector } from 'three/addons/objects/Reflector.js';
import { Refractor } from 'three/addons/objects/Refractor.js';

/**
 * Physically plausible starting points for the materials a game needs.
 *
 * The numbers matter more than they look: `roughness` below ~0.05 is
 * mirror-like and reads as a bug, and a `metalness` between 0.1 and 0.9
 * describes no real material — a surface is either a conductor or it is
 * not. Dielectrics therefore stay at `metalness: 0` and get their shine
 * from `clearcoat` instead.
 */
export const A3GameMaterialPreset = Object.freeze({
  METAL: 'metal',
  PAINTED_METAL: 'painted_metal',
  GUNMETAL: 'gunmetal',
  PLASTIC: 'plastic',
  RUBBER: 'rubber',
  CLOTH: 'cloth',
  LEATHER: 'leather',
  WOOD: 'wood',
  STONE: 'stone',
  CONCRETE: 'concrete',
  TARMAC: 'tarmac',
  GRASS: 'grass',
  SAND: 'sand',
  GLASS: 'glass',
  EMISSIVE: 'emissive',
});

const PRESET_PARAMETERS = Object.freeze({
  metal: { metalness: 1, roughness: 0.28 },
  painted_metal: {
    metalness: 0,
    roughness: 0.42,
    clearcoat: 1,
    clearcoatRoughness: 0.12,
  },
  gunmetal: { metalness: 1, roughness: 0.42 },
  plastic: {
    metalness: 0,
    roughness: 0.55,
    clearcoat: 0.6,
    clearcoatRoughness: 0.3,
  },
  rubber: { metalness: 0, roughness: 0.92 },
  cloth: { metalness: 0, roughness: 0.86, sheen: 0.5 },
  leather: { metalness: 0, roughness: 0.7, sheen: 0.2 },
  wood: { metalness: 0, roughness: 0.62 },
  stone: { metalness: 0, roughness: 0.85 },
  concrete: { metalness: 0, roughness: 0.9 },
  tarmac: { metalness: 0, roughness: 0.78 },
  grass: { metalness: 0, roughness: 0.95 },
  sand: { metalness: 0, roughness: 0.98 },
  glass: {
    metalness: 0,
    roughness: 0.05,
    transmission: 0.95,
    thickness: 0.4,
    transparent: true,
  },
  emissive: { metalness: 0, roughness: 0.4, emissiveIntensity: 2 },
});

/** Presets that need `MeshPhysicalMaterial` rather than the standard one. */
const PHYSICAL_PRESETS = new Set([
  'painted_metal',
  'plastic',
  'cloth',
  'leather',
  'glass',
]);

/**
 * Build a material from a preset.
 *
 * @param {string} preset a value of `A3GameMaterialPreset`
 * @param {object} [overrides] any material parameter, e.g. `{ color }`
 * @returns {THREE.MeshStandardMaterial | THREE.MeshPhysicalMaterial}
 */
export function createMaterial(preset, overrides = {}) {
  const key = String(preset ?? '').toLowerCase();
  const base = PRESET_PARAMETERS[key];
  if (!base) {
    throw new Error(
      `Unknown material preset ${String(preset)}; expected one of ` +
        Object.values(A3GameMaterialPreset).join(', '),
    );
  }
  const parameters = { color: 0xffffff, ...base, ...overrides };
  if (parameters.emissive === undefined && key === 'emissive') {
    parameters.emissive = parameters.color;
  }
  const Material = PHYSICAL_PRESETS.has(key)
    ? THREE.MeshPhysicalMaterial
    : THREE.MeshStandardMaterial;
  const material = new Material(parameters);
  material.name = `A3Game_${key}`;
  return material;
}

/**
 * A box with bevelled edges.
 *
 * A bevel is the cheapest possible upgrade to a blocky prop: a real
 * edge catches a highlight, and a mathematically sharp one cannot.
 *
 * @param {{width?: number, height?: number, depth?: number,
 *          radius?: number, segments?: number,
 *          material?: THREE.Material, preset?: string,
 *          color?: number | string,
 *          castShadow?: boolean, receiveShadow?: boolean,
 *          name?: string}} [options]
 * @returns {THREE.Mesh}
 */
export function createRoundedBox(options = {}) {
  const width = Number(options.width ??1);
  const height = Number(options.height ?? 1);
  const depth = Number(options.depth ?? 1);
  // A radius at or above half of the smallest side collapses the box, so
  // clamp instead of letting the geometry degenerate.
  const smallest = Math.min(width, height, depth);
  const radius = Math.min(
    Number(options.radius ?? Math.min(0.06, smallest * 0.18)),
    smallest * 0.499,
  );
  const geometry = new RoundedBoxGeometry(
    width,
    height,
    depth,
    Number(options.segments ?? 3),
    radius,
  );
  const material =
    options.material ??
    createMaterial(options.preset ?? A3GameMaterialPreset.PLASTIC, {
      color: options.color ?? 0xb9c0cc,
    });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.castShadow = options.castShadow !== false;
  mesh.receiveShadow = options.receiveShadow !== false;
  if (options.name) mesh.name = options.name;
  return mesh;
}

/**
 * A directional "sun" whose shadow camera actually fits the play area.
 *
 * The default `DirectionalLight` shadow camera is a 10-metre box at the
 * origin. A200-metre race track therefore gets no shadow at all, and
 * shrinking the map to fit is not an option — so the extent is an
 * explicit parameter here.
 *
 * `normalBias` is set rather than `bias`: it scales with the geometry and
 * removes the shadow acne that a flat `bias` fixes only for one distance.
 *
 * @param {{color?: number | string, intensity?: number,
 *          position?: {x: number, y: number, z: number},
 *          target?: {x: number, y: number, z: number},
 *          radius?: number, mapSize?: number,
 *          near?: number, far?: number,
 *          castShadow?: boolean}} [options]
 * @returns {THREE.DirectionalLight}
 */
export function createSunLight(options = {}) {
  const light = new THREE.DirectionalLight(
    options.color ?? 0xfff3e0,
    Number(options.intensity ?? 2.4),
  );
  const position = options.position ?? { x: 40, y: 70, z: 30 };
  light.position.set(position.x, position.y, position.z);
  const target = options.target ?? { x: 0, y: 0, z: 0 };
  light.target.position.set(target.x, target.y, target.z);
  light.name = 'A3GameSun';
  light.userData.a3gameSun = options.syncEnvironment !== false;

  light.castShadow = options.castShadow !== false;
  if (light.castShadow) {
    const radius = Math.max(1, Number(options.radius ?? 40));
    const mapSize = Number(options.mapSize ?? 2048);
    light.shadow.mapSize.set(mapSize, mapSize);
    light.shadow.camera.left = -radius;
    light.shadow.camera.right = radius;
    light.shadow.camera.top = radius;
    light.shadow.camera.bottom = -radius;
    light.shadow.camera.near = Number(options.near ?? 0.5);
    light.shadow.camera.far = Number(
      options.far ?? light.position.length() + radius * 2,
    );
    light.shadow.normalBias = 0.035;
    light.shadow.bias = -0.0002;
    light.shadow.camera.updateProjectionMatrix();
  }
  return light;
}

/**
 * Sky and bounce fill, so surfaces facing away from the sun are not
 * black.
 *
 * This is not a substitute for an environment map — it has no
 * reflections — but it is what keeps the *unlit* side of an object
 * readable, and it costs one draw of nothing.
 *
 * @param {{skyColor?: number | string, groundColor?: number | string,
 *          intensity?: number}} [options]
 * @returns {THREE.HemisphereLight}
 */
export function createFillLight(options = {}) {
  const light = new THREE.HemisphereLight(
    options.skyColor ?? 0x9fc4ff,
    options.groundColor ?? 0x4a4034,
    Number(options.intensity ?? 0.6),
  );
  light.name = 'A3GameFill';
  return light;
}

/**
 * Measure an object's world-space bounding box.
 *
 * @param {THREE.Object3D} object
 * @returns {THREE.Box3}
 */
export function measureObject(object) {
  object.updateMatrixWorld(true);
  return new THREE.Box3().setFromObject(object);
}

/**
 * Scale an object uniformly so that it stands a given number of metres
 * tall.
 *
 * Every glTF arrives in whatever unit its author used, and the spread is
 * not subtle: of three CC0 models staged for these games, one is6.6
 * units tall, one is 1.5, and one is 0.07. Dropping any of them into a
 * scene unscaled produces either a skyscraper or an invisible speck, so
 * normalising by a gameplay dimension — not by a magic constant — is the
 * only reliable way to place imported content.
 *
 * @param {THREE.Object3D} object
 * @param {number} metres desired world-space height
 * @returns {THREE.Object3D} the same object, scaled
 */
export function fitToHeight(object, metres) {
  const target = Number(metres);
  if (!Number.isFinite(target) || target <= 0) {
    throw new RangeError('fitToHeight requires a positive height');
  }
  const size = measureObject(object).getSize(new THREE.Vector3());
  if (!(size.y > 1e-9)) return object;
  const factor = target / size.y;
  object.scale.multiplyScalar(factor);
  object.updateMatrixWorld(true);
  return object;
}

/**
 * Shift an object so its lowest point sits on y = 0 and it is centred
 * horizontally.
 *
 * A model's origin is wherever its author left it — often the hips, or
 * the corner of the bounding box. Gameplay code, by contrast, always
 * wants to place a character by its feet.
 *
 * @param {THREE.Object3D} object
 * @param {{horizontal?: boolean}} [options]
 * @returns {THREE.Object3D}
 */
export function groundObject(object, options = {}) {
  const box = measureObject(object);
  if (box.isEmpty()) return object;
  object.position.y -= box.min.y;
  if (options.horizontal !== false) {
    const centre = box.getCenter(new THREE.Vector3());
    object.position.x -= centre.x;
    object.position.z -= centre.z;
  }
  object.updateMatrixWorld(true);
  return object;
}

/**
 * The local direction a prepared model must point along.
 *
 * The generated games derive facing with `Math.atan2(-x, -z)`, so a yaw
 * of zero means "travelling toward -Z". That is the convention every
 * imported model is rotated into, and it is also what the camera looks
 * down, which is why it was chosen.
 */
export const A3GAME_RUNTIME_FORWARD_AXIS = '-z';

/**
 * The axis an imported model faces *as authored*.
 *
 * glTF fixes the up axis and the unit and says nothing binding about
 * facing, so this is the one fact about a model that cannot be
 * discovered by loading it. The adapter records it in the manifest after
 * a reviewer looks at rendered views; `A3GameAssetLibrary` then applies
 * it, and gameplay code never sees the difference.
 */
export const A3GameForwardAxis = Object.freeze({
  POSITIVE_X: '+x',
  NEGATIVE_X: '-x',
  POSITIVE_Z: '+z',
  NEGATIVE_Z: '-z',
});

const FORWARD_AXIS_VECTORS = Object.freeze({
  '+x': [1, 0],
  '-x': [-1, 0],
  '+z': [0, 1],
  '-z': [0, -1],
});

function normalizeForwardAxis(value) {
  const text = String(value ?? '').trim().toLowerCase();
  if (!text) return '';
  const resolved = text.length === 1 ? `+${text}` : text;
  return resolved in FORWARD_AXIS_VECTORS ? resolved : '';
}

/**
 * Yaw, in radians, that turns `forwardAxis` into `runtimeForwardAxis`.
 *
 * Both arguments name a cardinal axis, so the result is always one of
 * four quarter turns — no arbitrary angle, and no accumulated error.
 *
 * @param {string} forwardAxis for example `'+z'`
 * @param {string} [runtimeForwardAxis]
 * @returns {number} radians about +Y
 */
export function forwardAxisYaw(
  forwardAxis,
  runtimeForwardAxis = A3GAME_RUNTIME_FORWARD_AXIS,
) {
  const source = normalizeForwardAxis(forwardAxis);
  const target = normalizeForwardAxis(runtimeForwardAxis);
  if (!source || !target) return 0;
  const [sx, sz] = FORWARD_AXIS_VECTORS[source];
  const [tx, tz] = FORWARD_AXIS_VECTORS[target];
  // three.js rotates about +Y as x' = x·cos + z·sin, z' = -x·sin + z·cos.
  for (const quarter of [0, 1, 2, 3]) {
    const angle = (quarter * Math.PI) / 2;
    const cos = Math.round(Math.cos(angle));
    const sin = Math.round(Math.sin(angle));
    if (sx * cos + sz * sin === tx && -sx * sin + sz * cos === tz) {
      return angle;
    }
  }
  return 0;
}

/**
 * Rotate an imported model until it faces the way the runtime assumes.
 *
 * A model facing +Z and the same model facing -Z are indistinguishable
 * from their file: same bounding box, same node tree, same clips. So the
 * facing axis is supplied, not detected, and applied here. With nothing
 * supplied this is a no-op, which is what keeps an already-correct game
 * unchanged.
 *
 * The rotation is written to `object.rotation`, so pass a wrapper when
 * gameplay code also writes `rotation.y` on the same object — otherwise
 * the game's facing assignment overwrites the correction on the first
 * frame. `A3GameAssetLibrary.tryInstantiate` wraps for exactly this
 * reason.
 *
 * @param {THREE.Object3D} object
 * @param {{forwardAxis?: string, runtimeForwardAxis?: string,
 *          yawOffsetDegrees?: number, pitchOffsetDegrees?: number,
 *          rollOffsetDegrees?: number, orientation?: object}} [options]
 * @returns {THREE.Object3D} the same object
 */
export function orientModel(object, options = {}) {
  if (!object) throw new TypeError('orientModel requires an Object3D');
  const orientation = options.orientation ?? {};
  const forwardAxis = normalizeForwardAxis(
    options.forwardAxis ?? orientation.forward_axis,
  );
  const runtimeForwardAxis =
    normalizeForwardAxis(
      options.runtimeForwardAxis ?? orientation.runtime_forward_axis,
    ) || A3GAME_RUNTIME_FORWARD_AXIS;
  const yawDegrees = Number(
    options.yawOffsetDegrees ?? orientation.yaw_offset_degrees ?? 0,
  );
  const pitchDegrees = Number(
    options.pitchOffsetDegrees ?? orientation.pitch_offset_degrees ?? 0,
  );
  const rollDegrees = Number(
    options.rollOffsetDegrees ?? orientation.roll_offset_degrees ?? 0,
  );

  const yaw =
    (forwardAxis ? forwardAxisYaw(forwardAxis, runtimeForwardAxis) : 0) +
    THREE.MathUtils.degToRad(Number.isFinite(yawDegrees) ? yawDegrees : 0);
  const pitch = THREE.MathUtils.degToRad(
    Number.isFinite(pitchDegrees) ? pitchDegrees : 0,
  );
  const roll = THREE.MathUtils.degToRad(
    Number.isFinite(rollDegrees) ? rollDegrees : 0,
  );
  if (yaw === 0 && pitch === 0 && roll === 0) return object;

  object.rotation.set(
    object.rotation.x + pitch,
    object.rotation.y + yaw,
    object.rotation.z + roll,
  );
  object.updateMatrixWorld(true);
  return object;
}

/**
 * Collect every vertex of a subtree in the subtree's own local space.
 *
 * Local, not world: the caller is about to derive a rotation to *write on*
 * this object, so measuring through its own transform would fold that
 * transform into the answer and the correction would be applied twice.
 *
 * @param {THREE.Object3D} object
 * @param {number} [stride] take every n-th vertex; a 20k-vertex prop does
 *        not need every point to yield a stable covariance
 * @returns {THREE.Vector3[]}
 */
function collectLocalPoints(object, stride = 1) {
  object.updateMatrixWorld(true);
  const inverseRoot = object.matrixWorld.clone().invert();
  const step = Math.max(1, Math.floor(stride));
  /** @type {THREE.Vector3[]} */
  const points = [];
  const matrix = new THREE.Matrix4();
  object.traverse((child) => {
    if (!child.isMesh && !child.isSkinnedMesh) return;
    const position = child.geometry?.getAttribute('position');
    if (!position) return;
    matrix.copy(inverseRoot).multiply(child.matrixWorld);
    for (let index = 0; index < position.count; index += step) {
      points.push(
        new THREE.Vector3().fromBufferAttribute(position, index).applyMatrix4(matrix),
      );
    }
  });
  return points;
}

/**
 * Principal axes of a point cloud, largest spread first.
 *
 * Jacobi rotations on the 3x3 covariance matrix, which is small enough to
 * write out and converges in a handful of sweeps. This exists because the
 * manifest's `forward_axis` can only ever name a cardinal direction, and a
 * generated prop is frequently authored at an angle to all of them — for a
 * weapon that means no quarter turn aligns the barrel, so the axis has to
 * come from the geometry.
 *
 * @param {THREE.Vector3[]} points
 * @returns {{centroid: THREE.Vector3,
 *            axes: {vector: THREE.Vector3, deviation: number}[]}}
 */
export function principalAxes(points) {
  const centroid = new THREE.Vector3();
  if (points.length === 0) {
    return {
      centroid,
      axes: [
        { vector: new THREE.Vector3(1, 0, 0), deviation: 0 },
        { vector: new THREE.Vector3(0, 1, 0), deviation: 0 },
        { vector: new THREE.Vector3(0, 0, 1), deviation: 0 },
      ],
    };
  }
  for (const point of points) centroid.add(point);
  centroid.multiplyScalar(1 / points.length);

  let matrix = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0],
  ];
  for (const point of points) {
    const d = [point.x - centroid.x, point.y - centroid.y, point.z - centroid.z];
    for (let i = 0; i < 3; i += 1) {
      for (let j = 0; j < 3; j += 1) matrix[i][j] += d[i] * d[j];
    }
  }
  for (let i = 0; i < 3; i += 1) {
    for (let j = 0; j < 3; j += 1) matrix[i][j] /= points.length;
  }

  let vectors = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ];
  for (let sweep = 0; sweep < 32; sweep += 1) {
    let p = 0;
    let q = 1;
    let largest = Math.abs(matrix[0][1]);
    for (const [i, j] of [
      [0, 2],
      [1, 2],
    ]) {
      if (Math.abs(matrix[i][j]) > largest) {
        largest = Math.abs(matrix[i][j]);
        p = i;
        q = j;
      }
    }
    if (largest < 1e-12) break;
    const theta = 0.5 * Math.atan2(2 * matrix[p][q], matrix[p][p] - matrix[q][q]);
    const cos = Math.cos(theta);
    const sin = Math.sin(theta);
    const rows = matrix.map((row) => [...row]);
    for (let k = 0; k < 3; k += 1) {
      rows[p][k] = cos * matrix[p][k] + sin * matrix[q][k];
      rows[q][k] = -sin * matrix[p][k] + cos * matrix[q][k];
    }
    const columns = rows.map((row) => [...row]);
    for (let k = 0; k < 3; k += 1) {
      columns[k][p] = cos * rows[k][p] + sin * rows[k][q];
      columns[k][q] = -sin * rows[k][p] + cos * rows[k][q];
    }
    matrix = columns;
    const turned = vectors.map((row) => [...row]);
    for (let k = 0; k < 3; k += 1) {
      turned[k][p] = cos * vectors[k][p] + sin * vectors[k][q];
      turned[k][q] = -sin * vectors[k][p] + cos * vectors[k][q];
    }
    vectors = turned;
  }

  const axes = [0, 1, 2]
    .map((i) => ({
      deviation: Math.sqrt(Math.max(0, matrix[i][i])),
      vector: new THREE.Vector3(
        vectors[0][i],
        vectors[1][i],
        vectors[2][i],
      ).normalize(),
    }))
    .sort((a, b) => b.deviation - a.deviation);
  return { centroid, axes };
}

/**
 * Measure a weapon mesh well enough to point it the right way.
 *
 * The manifest cannot answer this. Its `forward_axis` is written by the
 * heuristic "generated from a front view, so it faces the camera", which is
 * about a *character*; it is recorded for every artifact regardless, with
 * `needs_vision_check: true` and a yaw of 0 or 180 as the only options. For
 * a weapon that guess is a coin flip whose losing side points the barrel at
 * the player's own face, and a mesh authored at an angle cannot be fixed by
 * either value.
 *
 * Geometry settles it, because a gun has a shape no other prop has:
 *
 *  - it is **much longer than it is thick**, so the first principal axis is
 *    the barrel line and the smallest is across the flat of the receiver;
 *  - a stretch of **bare barrel** sticks out past everything that hangs
 *    below it. Grip, magazine and stock are all in the lower silhouette, so
 *    the end where the lower silhouette stops short is the muzzle.
 *
 * @param {THREE.Object3D} object
 * @param {{lowerBandFraction?: number, minElongation?: number,
 *          maxThicknessRatio?: number, minMuzzleMargin?: number,
 *          stride?: number}} [options]
 * @returns {{weaponlike: boolean, barrel: THREE.Vector3, up: THREE.Vector3,
 *            side: THREE.Vector3, centroid: THREE.Vector3, length: number,
 *            height: number, thickness: number, elongation: number,
 *            muzzleMargin: number, confident: boolean, warnings: string[]}}
 */
export function measureWeapon(object, options = {}) {
  const stride = Number(options.stride ?? 1);
  const points = collectLocalPoints(object, stride);
  const warnings = [];
  const { centroid, axes } = principalAxes(points);

  const barrel = axes[0].vector.clone();
  // Up is whichever remaining axis is closest to world up. glTF fixes +Y as
  // up and every generator here respects it, so this is a fact about the
  // file rather than another guess. Ties go to the wider axis, because a gun
  // is taller through the grip than it is thick across the receiver.
  const candidates = [axes[1], axes[2]];
  candidates.sort(
    (a, b) => Math.abs(b.vector.y) - Math.abs(a.vector.y) || b.deviation - a.deviation,
  );
  const up = candidates[0].vector.clone();
  if (up.y < 0) up.negate();
  const side = new THREE.Vector3().crossVectors(barrel, up).normalize();
  // Re-orthogonalise: the covariance axes are orthogonal but `up` was
  // flipped and cross products accumulate error.
  up.crossVectors(side, barrel).normalize();

  /** Project the cloud into the current frame. */
  const project = () =>
    points.map((point) => {
      const d = point.clone().sub(centroid);
      return new THREE.Vector3(d.dot(barrel), d.dot(up), d.dot(side));
    });

  let local = project();
  let box = new THREE.Box3().setFromPoints(
    local.length ? local : [new THREE.Vector3()],
  );

  // Re-derive the barrel from the **upper silhouette** only.
  //
  // The first principal axis of the whole cloud is not the barrel line: the
  // grip and the stock are heavy, they are all at one end, and they are all
  // below, so they tilt the axis by several degrees. That tilt is not
  // cosmetic. It pitches the aligned weapon nose-up in the player's hands,
  // and it breaks the muzzle test below — in a tilted frame the far bottom
  // corner of the receiver dips under the lower-band threshold, so the bare
  // barrel stops looking bare. Whether it dips is decided by a few
  // thousandths, which showed up as the same gun measuring differently at
  // different mesh densities.
  //
  // The top half is just the barrel and the receiver, which is straight, so
  // its own principal axis is the line wanted here.
  const upperCut = box.min.y + box.getSize(new THREE.Vector3()).y * 0.5;
  const upper = points.filter((_, index) => local[index].y > upperCut);
  if (upper.length >= 12) {
    const refined = principalAxes(upper).axes[0].vector.clone();
    // Keep pointing the same way, so the flip test below stays meaningful.
    if (refined.dot(barrel) < 0) refined.negate();
    barrel.copy(refined);
    side.crossVectors(barrel, up).normalize();
    up.crossVectors(side, barrel).normalize();
    local = project();
    box = new THREE.Box3().setFromPoints(
      local.length ? local : [new THREE.Vector3()],
    );
  }

  const size = box.getSize(new THREE.Vector3());
  const length = size.x;
  const height = size.y;
  const thickness = size.z;
  const elongation =
    axes[1].deviation > 1e-9 ? axes[0].deviation / axes[1].deviation : 0;

  // Which end is the muzzle, from the one feature every gun has: a stretch
  // of **bare barrel** that nothing hangs below. The grip, the magazine and
  // the stock all sit in the lower part of the silhouette, so the span they
  // occupy along the barrel is measured and compared with the full span. The
  // end with the longer bare stretch is the muzzle.
  //
  // Two simpler statistics were tried and both are unsound. The *thinnest*
  // section near each end is equal at both ends, because the receiver runs
  // the whole length and every window contains some bin without the grip in
  // it. The *thickest* section near each end is contaminated by the
  // magazine, which hangs just as low as the grip and starts a tenth of the
  // way along, so on the staged SMG it separated the ends by 4% — inside the
  // noise. The bare-barrel span is the feature that is actually asymmetric.
  const lowerBand = THREE.MathUtils.clamp(
    Number(options.lowerBandFraction ?? 0.4),
    0.15,
    0.6,
  );
  const lowerLimit = box.min.y + height * lowerBand;
  let lowerMin = Infinity;
  let lowerMax = -Infinity;
  for (const point of local) {
    if (point.y > lowerLimit) continue;
    if (point.x < lowerMin) lowerMin = point.x;
    if (point.x > lowerMax) lowerMax = point.x;
  }
  const hasLower = Number.isFinite(lowerMin) && Number.isFinite(lowerMax);
  // Normalised by length so the numbers mean the same thing at any scale.
  const frontGap = hasLower && length > 1e-9 ? (lowerMin - box.min.x) / length : 0;
  const rearGap = hasLower && length > 1e-9 ? (box.max.x - lowerMax) / length : 0;
  // `barrel` points at +pc0 so far; flip it when the bare stretch is at -pc0.
  // Flipping turns the frame 180 degrees about `up`, which flips `side` too:
  // negating the barrel alone would leave a left-handed basis, and
  // `setFromRotationMatrix` on a reflection returns a quaternion that is not
  // the rotation asked for — it keeps the pitch and drops the turn, which
  // looks like the alignment "almost" working.
  if (frontGap > rearGap) {
    barrel.negate();
    side.negate();
  }
  const muzzleMargin = Math.abs(frontGap - rearGap);

  const minElongation = Number(options.minElongation ?? 1.5);
  const maxThicknessRatio = Number(options.maxThicknessRatio ?? 0.4);
  const minMuzzleMargin = Number(options.minMuzzleMargin ?? 0.05);
  const thicknessRatio = length > 1e-9 ? thickness / length : 1;

  if (points.length === 0) warnings.push('mesh has no vertices');
  if (elongation < minElongation) {
    warnings.push(
      `elongation ${elongation.toFixed(2)} is below ${minElongation} — ` +
        'this reads as a blob rather than a weapon',
    );
  }
  if (thicknessRatio > maxThicknessRatio) {
    warnings.push(
      `thickness is ${(thicknessRatio * 100).toFixed(0)}% of length — too ` +
        'chunky to be a weapon',
    );
  }
  const confident = muzzleMargin >= minMuzzleMargin;
  if (!confident) {
    warnings.push(
      `both ends look alike (margin ${muzzleMargin.toFixed(3)}) — the muzzle ` +
        'end could not be told apart from the grip end',
    );
  }

  return {
    weaponlike:
      points.length > 0 &&
      elongation >= minElongation &&
      thicknessRatio <= maxThicknessRatio,
    barrel,
    up: up.clone(),
    side: side.clone(),
    centroid,
    length,
    height,
    thickness,
    elongation,
    muzzleMargin,
    confident,
    warnings,
  };
}

/**
 * Point a weapon mesh down the runtime forward axis, from its geometry.
 *
 * The rotation is written to `object.quaternion` — a full orientation, not a
 * yaw, because a generated mesh is usually tilted and rolled as well as
 * turned. Pass a wrapper if gameplay code writes rotation on the same
 * object.
 *
 * Returns the measurement so the caller can *refuse* the model: an
 * unrecognisable mesh in the player's hands for an entire match is worse
 * than a plain procedural one, and only the caller knows what its fallback
 * looks like.
 *
 * @param {THREE.Object3D} object
 * @param {{requireConfident?: boolean, tipFraction?: number,
 *          minElongation?: number, maxThicknessRatio?: number,
 *          minMuzzleMargin?: number, stride?: number}} [options]
 * @returns {ReturnType<typeof measureWeapon> & {applied: boolean}}
 */
export function alignWeaponModel(object, options = {}) {
  if (!object?.isObject3D) {
    throw new TypeError('alignWeaponModel requires an Object3D');
  }
  const measurement = measureWeapon(object, options);
  const usable =
    measurement.weaponlike &&
    (measurement.confident || options.requireConfident === false);
  if (!usable) return { ...measurement, applied: false };

  // Map the model's own frame onto the runtime's: barrel -> -Z (what the
  // camera looks down), up -> +Y, and the flat of the receiver -> X.
  const from = new THREE.Matrix4().makeBasis(
    measurement.side,
    measurement.up,
    measurement.barrel.clone().negate(),
  );
  const rotation = new THREE.Quaternion().setFromRotationMatrix(from).invert();
  // Assigned, not multiplied in. `measureWeapon` reports the barrel in the
  // frame the object's *children* live in, which excludes this node's own
  // transform, so the final orientation has to be exactly `rotation`.
  // Composing with whatever was here before would reintroduce it.
  object.quaternion.copy(rotation);
  object.updateMatrixWorld(true);
  return { ...measurement, applied: true };
}

/**
 * Make a freshly loaded model behave like scene content.
 *
 * A glTF mesh does not cast or receive shadows until told to: the format
 * has no such concept, so `GLTFLoader` leaves both flags false. A model
 * that lights correctly but floats shadowless above the floor is the
 * single most common "why does my imported asset look wrong" symptom.
 *
 * Facing is corrected first, because every measurement that follows —
 * the fit to height, the grounding, the bounding box a game reads back —
 * is taken from the rotated model.
 *
 * @param {THREE.Object3D} object
 * @param {{height?: number, ground?: boolean,
 *          castShadow?: boolean, receiveShadow?: boolean,
 *          envMapIntensity?: number, frustumCulled?: boolean,
 *          forwardAxis?: string, runtimeForwardAxis?: string,
 *          yawOffsetDegrees?: number, pitchOffsetDegrees?: number,
 *          rollOffsetDegrees?: number, orientation?: object}} [options]
 * @returns {THREE.Object3D} the same object
 */
export function prepareModel(object, options = {}) {
  if (!object) throw new TypeError('prepareModel requires an Object3D');
  orientModel(object, options);
  if (options.height !== undefined) fitToHeight(object, options.height);
  if (options.ground) groundObject(object, { horizontal: false });

  const cast = options.castShadow !== false;
  const receive = options.receiveShadow !== false;
  object.traverse((child) => {
    if (!child.isMesh && !child.isSkinnedMesh) return;
    child.castShadow = cast;
    child.receiveShadow = receive;
    if (options.frustumCulled !== undefined) {
      // A skinned mesh animated far from its bind pose is culled using
      // stale bounds, which makes a character blink out at screen edges.
      child.frustumCulled = options.frustumCulled;
    }
    const materials = Array.isArray(child.material)
      ? child.material
      : child.material
        ? [child.material]
        : [];
    for (const material of materials) {
      if (options.envMapIntensity !== undefined && 'envMapIntensity' in material) {
        material.envMapIntensity = Number(options.envMapIntensity);
      }
    }
  });
  return object;
}

/**
 * Draw one imported model many times, in one draw call.
 *
 * This is what makes generated art usable for *scenery*. A generated mesh
 * costs tens of thousands of triangles, which is affordable once for a
 * character the player inspects and ruinous seventy-four times for trees:
 * seventy-four clones are seventy-four draw calls and seventy-four times
 * the geometry. An `InstancedMesh` is one of each, with the per-copy
 * transforms living on the GPU.
 *
 * Only single-mesh bodies qualify, which is exactly what image-to-3D
 * produces. A skinned or multi-part model returns `null` rather than being
 * silently flattened — losing a skeleton is not a detail to discover at
 * runtime — so a caller can fall back to whatever it was drawing before.
 *
 * The source model's own transform is baked into each instance, so a model
 * already scaled by `fitToHeight` keeps its metre size.
 *
 * @param {THREE.Object3D} source a prepared, non-skinned model
 * @param {number} count
 * @param {{castShadow?: boolean, receiveShadow?: boolean,
 *          frustumCulled?: boolean}} [options]
 * @returns {THREE.InstancedMesh | null}
 */
export function createInstancedFromModel(source, count, options = {}) {
  if (!source || !Number.isFinite(count) || count <= 0) return null;
  const meshes = [];
  let skinned = false;
  source.traverse((child) => {
    if (child.isSkinnedMesh) skinned = true;
    else if (child.isMesh) meshes.push(child);
  });
  if (skinned || meshes.length !== 1) return null;

  const [mesh] = meshes;
  source.updateMatrixWorld(true);
  const geometry = mesh.geometry.clone();
  // Bake the model's transform into the geometry, so an instance matrix
  // only has to carry where the copy goes, not how the model was fitted.
  geometry.applyMatrix4(mesh.matrixWorld);
  const instanced = new THREE.InstancedMesh(geometry, mesh.material, count);
  instanced.castShadow = options.castShadow !== false;
  instanced.receiveShadow = options.receiveShadow !== false;
  if (options.frustumCulled !== undefined) {
    instanced.frustumCulled = options.frustumCulled;
  }
  instanced.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  return instanced;
}

/**
 * A soft blob of shadow under an object.
 *
 * Real-time shadow maps are expensive and, at a distance, blocky. A
 * radial-gradient decal on the floor grounds an object convincingly for
 * one draw call, which is why almost every mobile game ships one. It also
 * works when shadow maps are disabled entirely.
 *
 * @param {{radius?: number, opacity?: number, color?: number | string,
 *          resolution?: number}} [options]
 * @returns {THREE.Mesh} lying in the XZ plane, to be positioned by caller
 */
export function createContactShadow(options = {}) {
  const radius = Number(options.radius ?? 0.6);
  const resolution = Number(options.resolution ?? 128);
  const texture = createRadialGradientTexture({
    resolution,
    color: options.color ?? '#000000',
  });
  const material = new THREE.MeshBasicMaterial({
    map: texture,
    transparent: true,
    opacity: Number(options.opacity ?? 0.45),
    depthWrite: false,
  });
  const mesh = new THREE.Mesh(
    new THREE.PlaneGeometry(radius * 2, radius * 2),
    material,
  );
  mesh.rotation.x = -Math.PI / 2;
  mesh.renderOrder = -1;
  mesh.name = 'A3GameContactShadow';
  return mesh;
}

/**
 * Draw a radial gradient into a texture.
 *
 * Canvas work must stay inside a factory like this one: a module that
 * touches `document` at import time cannot be loaded by a headless test.
 *
 * @param {{resolution?: number, color?: string}} [options]
 * @returns {THREE.Texture}
 */
export function createRadialGradientTexture(options = {}) {
  const resolution = Number(options.resolution ?? 128);
  // Headless fallback. The comment above says canvas work belongs in a
  // factory so a module *loads* without a DOM — but a factory that throws
  // when called is barely better, and scene dressing that needs a soft dot
  // (cloud layers, contact shadows, particle sprites) is exactly what a
  // headless world-building test builds. A `DataTexture` computes the same
  // falloff arithmetically and needs nothing from the browser.
  if (typeof document === 'undefined') {
    const colour = new THREE.Color(options.color ?? '#000000');
    const data = new Uint8Array(resolution * resolution * 4);
    const half = resolution / 2;
    for (let y = 0; y < resolution; y += 1) {
      for (let x = 0; x < resolution; x += 1) {
        const distance = Math.hypot(x - half, y - half) / half;
        const alpha = Math.max(0, 1 - distance);
        const index = (y * resolution + x) * 4;
        data[index] = Math.round(colour.r * 255);
        data[index + 1] = Math.round(colour.g * 255);
        data[index + 2] = Math.round(colour.b * 255);
        data[index + 3] = Math.round(alpha * 255);
      }
    }
    const texture = new THREE.DataTexture(data, resolution, resolution);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.needsUpdate = true;
    return texture;
  }
  const canvas = document.createElement('canvas');
  canvas.width = resolution;
  canvas.height = resolution;
  const context = canvas.getContext('2d');
  const half = resolution / 2;
  const gradient = context.createRadialGradient(half, half, 0, half, half, half);
  const color = String(options.color ?? '#000000');
  gradient.addColorStop(0, color);
  gradient.addColorStop(1, 'rgba(0,0,0,0)');
  context.fillStyle = gradient;
  context.fillRect(0, 0, resolution, resolution);
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/**
 * Deterministic pseudo-random sequence.
 *
 * Procedural scenery must look identical on every reload and in every
 * test run, which `Math.random` cannot promise. Mulberry32 is three
 * lines and has a long enough period for scene dressing.
 *
 * @param {number} seed
 * @returns {() => number} values in [0, 1)
 */
export function createSeededRandom(seed = 1) {
  let state = Number(seed) >>> 0 || 1;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = Math.imul(state ^ (state >>> 15), 1 | state);
    value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value;
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

/* ────────────────────────────────────────────────────────────────────────
 * Outdoor scene dressing
 *
 * A generated outdoor scene loses its realism in four specific places,
 * and none of them is the geometry:
 *
 *   1. the sky is a flat colour, or the `Sky` shader — which is
 *      physically correct and therefore completely cloudless, so it
 *      reads as an empty studio dome;
 *   2. the ground is one untextured plane, which removes every scale
 *      and motion cue the player has;
 *   3. the horizon is a hard line where the plane ends;
 *   4. water, when there is any, is a blue plane.
 *
 * These four factories fix those four things. They are game-neutral,
 * allocate no global state, and take their textures as arguments so the
 * caller decides whether a staged photograph or a canvas fallback is
 * used.
 * ──────────────────────────────────────────────────────────────────── */

/** Shared value-noise + fbm, used by the sky and the range silhouette. */
const FBM_GLSL = /* glsl */ `
  float a3Hash(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
  }
  float a3Noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(
      mix(a3Hash(i + vec2(0.0, 0.0)), a3Hash(i + vec2(1.0, 0.0)), u.x),
      mix(a3Hash(i + vec2(0.0, 1.0)), a3Hash(i + vec2(1.0, 1.0)), u.x),
      u.y);
  }
  float a3Fbm(vec2 p) {
    float total = 0.0;
    float amplitude = 0.5;
    for (int i = 0; i < 5; i++) {
      total += amplitude * a3Noise(p);
      p = p * 2.03 + 17.3;
      amplitude *= 0.5;
    }
    return total;
  }
`;

/**
 * A stylised sky dome with a horizon gradient, a sun, and clouds.
 *
 * Why not `three/addons/objects/Sky.js`: it is a Preetham atmospheric
 * model, so it is accurate and it has no clouds, no cloud shadowing and
 * no artistic control beyond turbidity. An empty gradient is exactly
 * what makes a generated outdoor scene read as a tech demo. This dome
 * costs one draw call, produces drifting cumulus, and lets a game pick
 * its palette — which is the difference between "the sky is blue" and
 * "it is late afternoon in this world".
 *
 * The dome tracks the camera in `onBeforeRender`, so it can never be
 * left behind or clipped by the far plane however far the player
 * travels, and it is excluded from fog and from depth writes.
 *
 * @param {{radius?: number, zenith?: number|string, horizon?: number|string,
 *          ground?: number|string, sunColor?: number|string,
 *          sunDirection?: THREE.Vector3 | number[],
 *          sunSize?: number, sunGlow?: number,
 *          cloudCoverage?: number, cloudOpacity?: number,
 *          cloudSpeed?: number, cloudScale?: number,
 *          cloudColor?: number|string, cloudShadow?: number|string,
 *          haze?: number}} [options]
 * @returns {THREE.Mesh} with `userData.update(delta)` and
 *          `userData.setSunDirection(vec3)`
 */
export function createSkyGradient(options = {}) {
  const radius = Number(options.radius ?? 4000);
  const sun = readVector3(options.sunDirection, [0.35, 0.22, -1]).normalize();
  const uniforms = {
    uZenith: { value: new THREE.Color(options.zenith ?? 0x2a6fc4) },
    uHorizon: { value: new THREE.Color(options.horizon ?? 0xbcd7ee) },
    uGround: { value: new THREE.Color(options.ground ?? 0x6b6558) },
    uSunColor: { value: new THREE.Color(options.sunColor ?? 0xfff2d0) },
    uCloudColor: { value: new THREE.Color(options.cloudColor ?? 0xffffff) },
    uCloudShadow: { value: new THREE.Color(options.cloudShadow ?? 0x8a9bb0) },
    uSunDirection: { value: sun.clone() },
    uSunSize: { value: Number(options.sunSize ?? 0.004) },
    uSunGlow: { value: Number(options.sunGlow ?? 220) },
    uCloudCoverage: { value: Number(options.cloudCoverage ?? 0.48) },
    uCloudOpacity: { value: Number(options.cloudOpacity ?? 0.9) },
    uCloudScale: { value: Number(options.cloudScale ?? 1.6) },
    uCloudSpeed: { value: Number(options.cloudSpeed ?? 0.006) },
    uWindOffset: { value: new THREE.Vector2() },
    uWindDriven: { value: 0 },
    uHaze: { value: Number(options.haze ?? 0.35) },
    uTime: { value: 0 },
  };

  const material = new THREE.ShaderMaterial({
    uniforms,
    side: THREE.BackSide,
    depthWrite: false,
    depthTest: false,
    vertexShader: /* glsl */ `
      varying vec3 vDirection;
      void main() {
        vDirection = normalize(position);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        gl_Position.z = gl_Position.w;
      }
    `,
    fragmentShader: /* glsl */ `
      varying vec3 vDirection;
      uniform vec3 uZenith, uHorizon, uGround, uSunColor;
      uniform vec3 uCloudColor, uCloudShadow, uSunDirection;
      uniform float uSunSize, uSunGlow, uCloudCoverage, uCloudOpacity;
      uniform float uCloudScale, uCloudSpeed, uHaze, uTime, uWindDriven;
      uniform vec2 uWindOffset;
      ${FBM_GLSL}
      void main() {
        vec3 dir = normalize(vDirection);
        float height = dir.y;

        // Horizon gradient. The 0.42 exponent keeps the bright band low
        // and thin, which is what a real sky does and a linear mix does not.
        vec3 sky = mix(uHorizon, uZenith, pow(clamp(height, 0.0, 1.0), 0.42));
        sky = mix(sky, uGround, 1.0 - smoothstep(-0.10, 0.0, height));

        float sunDot = clamp(dot(dir, normalize(uSunDirection)), 0.0, 1.0);
        // Forward scattering: the whole sky warms towards the sun, most
        // strongly near the horizon. Skipping this is why a gradient sky
        // looks like a painted backdrop.
        sky += uSunColor * pow(sunDot, 6.0) * uHaze *
               (1.0 - smoothstep(0.0, 0.6, height));
        sky += uSunColor * pow(sunDot, uSunGlow) * 0.9;
        float disc = 0.0;
        if (uSunSize > 0.0) {
          disc = smoothstep(1.0 - uSunSize, 1.0 - uSunSize * 0.25, sunDot);
        }
        sky += uSunColor * disc * 6.0;

        // Clouds live on a virtual plane above the camera, so the
        // projection stretches them towards the horizon exactly as a real
        // cloud deck does.
        float above = max(height, 0.035);
        vec2 plane = dir.xz / above * uCloudScale;
        vec2 drift = mix(vec2(uTime * uCloudSpeed * 60.0, uTime * uCloudSpeed * 22.0), uWindOffset * 0.09, uWindDriven);
        float shape = a3Fbm(plane * 0.09 + drift);
        vec2 detailDrift = mix(-drift * 1.7, uWindOffset * 0.31, uWindDriven);
        float detail = a3Fbm(plane * 0.31 + detailDrift);
        float density = shape * 0.78 + detail * 0.22;
        float cover = smoothstep(uCloudCoverage, uCloudCoverage + 0.22, density);
        cover *= smoothstep(0.015, 0.22, height) * uCloudOpacity;
        // One fbm sample offset towards the sun is enough to read as
        // self-shadowing, and it is what gives the deck volume.
        float lit = smoothstep(0.35, 0.75,
          a3Fbm((plane + normalize(uSunDirection).xz * 6.0) * 0.09 + drift));
        vec3 cloud = mix(uCloudShadow, uCloudColor, lit);
        cloud += uSunColor * pow(sunDot, 24.0) * 0.6;

        vec3 color = mix(sky, cloud, cover);
        gl_FragColor = vec4(color, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
  });

  const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 48, 32), material);
  mesh.name = 'A3GameSkyGradient';
  mesh.frustumCulled = false;
  // Drawn first, with depth test off, so nothing in the scene can be
  // hidden behind it regardless of the far plane.
  mesh.renderOrder = -1000;
  mesh.onBeforeRender = (renderer, scene, camera) => {
    camera.getWorldPosition(mesh.position);
    if (mesh.parent) mesh.parent.worldToLocal(mesh.position);
    mesh.updateMatrixWorld(true);
  };
  mesh.userData.update = (delta, wind = options.windField) => {
    uniforms.uTime.value += Number(delta) || 0;
    if (wind) {
      const scale = uniforms.uCloudScale.value / Math.max(1, Number(options.cloudHeight ?? 120));
      uniforms.uWindOffset.value.set(-wind.displacement.x * scale, -wind.displacement.z * scale);
      uniforms.uWindDriven.value = 1;
    }
  };
  mesh.userData.setSunDirection = (direction) => {
    uniforms.uSunDirection.value.copy(
      readVector3(direction, [0.35, 0.22, -1]),
    ).normalize();
  };
  mesh.userData.uniforms = uniforms;
  return mesh;
}

/**
 * Surface patterns `createSurfaceTextures` can build.
 *
 * @enum {string}
 */
export const A3GameSurfacePattern = Object.freeze({
  /** Poured concrete slabs with expansion joints and worn speckle. */
  CONCRETE: 'concrete',
  /** Steel tread plate: raised diamonds, panel seams, bolt heads. */
  STEEL_PLATE: 'steel_plate',
  /** Stacked concrete blockwork with recessed mortar courses. */
  BLOCKWORK: 'blockwork',
  /** Flat painted sheet metal with panel lines and wear streaks. */
  PAINTED_PANEL: 'painted_panel',
});

/**
 * Build a tiling PBR texture set procedurally: colour, normal, roughness.
 *
 * This exists because the realism of a large surface is carried almost
 * entirely by its **normal map**, and a generated project has no textures
 * to import. A single colour map — even a good one — cannot respond to a
 * moving light, so a floor lit by a lamp the player walks past stays
 * visibly flat. That is the difference between "a grey plane" and "a
 * concrete floor", and no amount of colour detail substitutes for it.
 *
 * All three maps are derived from **one height field**, which is the point:
 * a normal map that disagrees with the colour map reads as a printed
 * pattern rather than as relief. Joints are darker *and* recessed *and*
 * rougher, because that is what a real joint is.
 *
 * Everything is computed into typed arrays rather than drawn into a
 * canvas, so this works identically in a browser and in a headless test,
 * and the noise is built on a wrapping lattice so the result tiles without
 * a seam.
 *
 * @param {{pattern?: string, size?: number, repeat?: number | number[],
 *          color?: THREE.ColorRepresentation,
 *          jointColor?: THREE.ColorRepresentation,
 *          roughness?: number | number[], normalStrength?: number,
 *          cells?: number, seed?: number, contrast?: number,
 *          renderer?: THREE.WebGLRenderer, anisotropy?: number}} [options]
 * @returns {{map: THREE.DataTexture, normalMap: THREE.DataTexture,
 *            roughnessMap: THREE.DataTexture, height: Float32Array,
 *            size: number, dispose: () => void}}
 */
export function createSurfaceTextures(options = {}) {
  const pattern = String(options.pattern ?? A3GameSurfacePattern.CONCRETE);
  const size = Math.max(32, Math.floor(Number(options.size ?? 512)));
  const cells = Math.max(1, Math.floor(Number(options.cells ?? 2)));
  const contrast = Number(options.contrast ?? 1);
  const normalStrength = Number(options.normalStrength ?? 1);
  const roughRange = Array.isArray(options.roughness)
    ? options.roughness
    : [Number(options.roughness ?? 0.82) - 0.1, Number(options.roughness ?? 0.82) + 0.1];
  const base = new THREE.Color(options.color ?? 0x9a9a95);
  const joint = new THREE.Color(options.jointColor ?? 0x4c4c49);

  // Tileable value noise. The lattice index wraps at `period`, so the
  // right edge samples the same corners as the left one and the texture
  // can be repeated without a seam — the single most common flaw in a
  // procedurally generated tiling texture.
  const random = createSeededRandom(Number(options.seed ?? 7));
  const lattices = [];
  for (let octave = 0; octave < 4; octave += 1) {
    const period = 4 << octave;
    const values = new Float32Array(period * period);
    for (let index = 0; index < values.length; index += 1) values[index] = random();
    lattices.push({ period, values });
  }
  const smooth = (t) => t * t * (3 - 2 * t);
  const sampleNoise = (u, v) => {
    let total = 0;
    let amplitude = 1;
    let sum = 0;
    for (const { period, values } of lattices) {
      const x = u * period;
      const y = v * period;
      const x0 = Math.floor(x);
      const y0 = Math.floor(y);
      const fx = smooth(x - x0);
      const fy = smooth(y - y0);
      const ix = ((x0 % period) + period) % period;
      const iy = ((y0 % period) + period) % period;
      const jx = (ix + 1) % period;
      const jy = (iy + 1) % period;
      const a = values[iy * period + ix];
      const b = values[iy * period + jx];
      const c = values[jy * period + ix];
      const d = values[jy * period + jx];
      total +=
        amplitude *
        ((a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy);
      sum += amplitude;
      amplitude *= 0.5;
    }
    return total / sum;
  };

  const height = new Float32Array(size * size);
  const tint = new Float32Array(size * size);
  const wear = new Float32Array(size * size);
  const cell = size / cells;

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const index = y * size + x;
      const u = x / size;
      const v = y / size;
      const grain = sampleNoise(u, v);
      const fine = sampleNoise(u * 4, v * 4);
      let h = 0.5 + (grain - 0.5) * 0.35 * contrast;
      let mix = grain;
      let rough = 0;

      if (pattern === A3GameSurfacePattern.CONCRETE) {
        // Expansion joints: a narrow recess on a slab grid, plus a
        // shallow crown across each slab so it does not read as a decal.
        const jx = Math.min(x % cell, cell - (x % cell));
        const jy = Math.min(y % cell, cell - (y % cell));
        const joints = Math.min(jx, jy);
        const groove = 1 - Math.min(1, joints / (size * 0.012));
        h -= groove * 0.55;
        mix = Math.max(mix, groove);
        rough += groove * 0.12;
        // Aggregate pitting: small dark pocks, which is what stops a
        // concrete floor looking like painted card.
        const pit = fine > 0.72 ? (fine - 0.72) / 0.28 : 0;
        h -= pit * 0.18;
        rough += pit * 0.08;
      } else if (pattern === A3GameSurfacePattern.STEEL_PLATE) {
        // Raised tread: two offset diagonal bars per cell.
        const px = ((x % cell) / cell) * 2 - 1;
        const py = ((y % cell) / cell) * 2 - 1;
        const bar = Math.min(
          Math.abs(px + py) < 0.42 ? 1 : 0,
          1,
        ) * (Math.abs(px - py) < 0.86 ? 1 : 0);
        const barB =
          (Math.abs(px - py) < 0.42 ? 1 : 0) * (Math.abs(px + py) < 0.86 ? 1 : 0);
        const tread = Math.max(bar, barB);
        h += tread * 0.45;
        mix = Math.min(mix, 1 - tread * 0.6);
        rough -= tread * 0.16;
        // Panel seam every four cells, and a bolt at its corners.
        const seam =
          (x % (cell * 4) < size * 0.006 ? 1 : 0) ||
          (y % (cell * 4) < size * 0.006 ? 1 : 0)
            ? 1
            : 0;
        h -= seam * 0.35;
        mix = Math.max(mix, seam * 0.8);
      } else if (pattern === A3GameSurfacePattern.BLOCKWORK) {
        // Staggered courses: every other row is offset by half a block,
        // which is what makes it read as masonry rather than as a grid.
        const course = Math.floor(y / cell);
        const offset = course % 2 === 0 ? 0 : cell / 2;
        const bx = (x + offset) % cell;
        const by = y % cell;
        const mortar =
          Math.min(bx, cell - bx) < size * 0.008 ||
          Math.min(by, cell - by) < size * 0.008
            ? 1
            : 0;
        h -= mortar * 0.7;
        mix = Math.max(mix, mortar);
        rough += mortar * 0.14;
        // Per-block colour variation, so no two blocks match.
        const blockNoise = sampleNoise(
          (Math.floor((x + offset) / cell) + 0.5) / cells,
          (course + 0.5) / cells,
        );
        if (!mortar) h += (blockNoise - 0.5) * 0.12;
      } else {
        // PAINTED_PANEL: broad panels, faint vertical wear streaks.
        const seamX = x % (cell * 2) < size * 0.005 ? 1 : 0;
        h -= seamX * 0.4;
        mix = Math.max(mix, seamX * 0.7);
        const streak = sampleNoise(u * 24, v * 0.6);
        h += (streak - 0.5) * 0.08;
        rough += Math.max(0, streak - 0.6) * 0.2;
      }

      height[index] = Math.min(1, Math.max(0, h));
      tint[index] = Math.min(1, Math.max(0, mix));
      wear[index] = rough;
    }
  }

  // Colour and roughness, read off the same height field.
  const colourData = new Uint8Array(size * size * 4);
  const roughData = new Uint8Array(size * size * 4);
  const shade = new THREE.Color();
  for (let index = 0; index < height.length; index += 1) {
    shade.copy(base).lerp(joint, tint[index] * 0.85);
    // Ambient occlusion baked into the albedo: recesses are darker, which
    // survives even when a renderer's shadows do not reach them.
    const cavity = 0.72 + height[index] * 0.38;
    shade.multiplyScalar(cavity).convertLinearToSRGB();
    const offset = index * 4;
    colourData[offset] = Math.min(255, Math.round(shade.r * 255));
    colourData[offset + 1] = Math.min(255, Math.round(shade.g * 255));
    colourData[offset + 2] = Math.min(255, Math.round(shade.b * 255));
    colourData[offset + 3] = 255;
    const rough = Math.min(
      1,
      Math.max(
        0,
        roughRange[0] +
          (roughRange[1] - roughRange[0]) * (1 - height[index]) +
          wear[index],
      ),
    );
    const encoded = Math.round(rough * 255);
    roughData[offset] = encoded;
    roughData[offset + 1] = encoded;
    roughData[offset + 2] = encoded;
    roughData[offset + 3] = 255;
  }

  // Normals by central difference on the height field, wrapping at the
  // edges so the normal map tiles as seamlessly as the colour map.
  const normalData = new Uint8Array(size * size * 4);
  const at = (x, y) => height[((y + size) % size) * size + ((x + size) % size)];
  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const dx = (at(x + 1, y) - at(x - 1, y)) * normalStrength * size * 0.02;
      const dy = (at(x, y + 1) - at(x, y - 1)) * normalStrength * size * 0.02;
      const length = Math.sqrt(dx * dx + dy * dy + 1);
      const offset = (y * size + x) * 4;
      normalData[offset] = Math.round(((-dx / length) * 0.5 + 0.5) * 255);
      normalData[offset + 1] = Math.round(((-dy / length) * 0.5 + 0.5) * 255);
      normalData[offset + 2] = Math.round((1 / length) * 0.5 * 255 + 127.5);
      normalData[offset + 3] = 255;
    }
  }

  const build = (data, srgb) => {
    const texture = new THREE.DataTexture(data, size, size);
    texture.magFilter = THREE.LinearFilter;
    texture.minFilter = THREE.LinearMipmapLinearFilter;
    texture.generateMipmaps = true;
    texture.needsUpdate = true;
    return createTilingTexture(texture, {
      repeat: options.repeat ?? 1,
      srgb,
      anisotropy: options.anisotropy ?? 8,
      renderer: options.renderer,
    });
  };

  const map = build(colourData, true);
  // Normal and roughness are data, not colour. Tagging either sRGB
  // silently bends every normal and every gloss value in the scene.
  const normalMap = build(normalData, false);
  const roughnessMap = build(roughData, false);

  return {
    map,
    normalMap,
    roughnessMap,
    height,
    size,
    dispose() {
      map.dispose();
      normalMap.dispose();
      roughnessMap.dispose();
    },
  };
}

/**
 * Build a `MeshStandardMaterial` on a procedural PBR surface set.
 *
 * @param {{pattern?: string, repeat?: number | number[],
 *          color?: THREE.ColorRepresentation, metalness?: number,
 *          normalScale?: number, envMapIntensity?: number,
 *          material?: object}} [options] also accepts everything
 *          `createSurfaceTextures` takes
 * @returns {THREE.MeshStandardMaterial} with `userData.surface` holding the
 *          texture set, so a caller can dispose it.
 */
export function createSurfaceMaterial(options = {}) {
  const surface = createSurfaceTextures(options);
  const scale = Number(options.normalScale ?? 1);
  const material = new THREE.MeshStandardMaterial({
    map: surface.map,
    normalMap: surface.normalMap,
    roughnessMap: surface.roughnessMap,
    // `roughness` multiplies `roughnessMap`, so it has to be 1 or the map
    // is scaled down to nothing and the surface goes uniformly glossy.
    roughness: 1,
    metalness: Number(options.metalness ?? 0.04),
    envMapIntensity: Number(options.envMapIntensity ?? 1),
    ...(options.material ?? {}),
  });
  material.normalScale = new THREE.Vector2(scale, scale);
  material.userData.surface = surface;
  return material;
}

/**
 * Configure an imported texture for tiling over a large surface.
 *
 * Four settings decide whether a ground texture helps or hurts, and
 * three of them are wrong by default: `wrapS/wrapT` clamp (so `repeat`
 * does nothing but stretch the last pixel), `anisotropy` is 1 (so the
 * surface turns to mush at grazing angles, which is *every* angle on a
 * ground plane), and `colorSpace` is not sRGB on a colour map.
 *
 * @param {THREE.Texture} texture
 * @param {{repeat?: number | number[], colorSpace?: string,
 *          anisotropy?: number, rotation?: number,
 *          renderer?: THREE.WebGLRenderer, srgb?: boolean}} [options]
 * @returns {THREE.Texture} the same texture, configured
 */
export function createTilingTexture(texture, options = {}) {
  if (!texture) return texture;
  const repeat = Array.isArray(options.repeat)
    ? options.repeat
    : [Number(options.repeat ?? 1), Number(options.repeat ?? 1)];
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;
  texture.repeat.set(repeat[0], repeat[1]);
  if (options.rotation !== undefined) {
    texture.center.set(0.5, 0.5);
    texture.rotation = Number(options.rotation);
  }
  texture.colorSpace =
    options.colorSpace ??
    (options.srgb === false ? THREE.NoColorSpace : THREE.SRGBColorSpace);
  const cap = options.renderer?.capabilities?.getMaxAnisotropy?.() ?? 8;
  texture.anisotropy = Math.min(Number(options.anisotropy ?? 8), cap);
  texture.needsUpdate = true;
  return texture;
}

/**
 * Bounded analytic water, not a fluid solver. Standard quality uses GPU
 * waves; low quality uses a bounded CPU mesh. Both expose world-space
 * height/normal/depth sampling, pooled ripples and approximate buoyancy.
 * Optional planar reflection/refraction targets are created on first render.
 *
 * @param {object} [options] quality, size, waves, terrainHeight, depth,
 *   reflection/refraction, rippleCapacity and appearance settings.
 * @returns {THREE.Mesh} in the XZ plane, with `userData.update(delta)`
 */
export function createWaterSurface(options = {}) {
  const finite = (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const clamp = THREE.MathUtils.clamp;
  const quality = options.quality === 'low' ? 'low' : 'standard';
  const inputSize = Array.isArray(options.size) ? options.size : [options.size, options.size];
  const size = [Math.max(0.01, finite(inputSize[0], 60)), Math.max(0.01, finite(inputSize[1], finite(inputSize[0], 60)))];
  const segments = clamp(Math.trunc(finite(options.segments, quality === 'low' ? 24 : 64)), 1, quality === 'low' ? 32 : 192);
  const amplitude = clamp(finite(options.waveHeight, quality === 'low' ? 0.06 : 0.12), 0, 20);
  const capacity = clamp(Math.trunc(finite(options.rippleCapacity, quality === 'low' ? 4 : 8)), 0, 8);
  const lifetime = clamp(finite(options.rippleLifetime, 6), 0.1, 30);
  const maxRipple = clamp(finite(options.maxRippleStrength, 0.5), 0, 5);
  const depthResolution = clamp(Math.trunc(finite(options.depthResolution, 32)), 2, 128);
  const defaultDepth = Math.max(0, finite(options.depth, 5));
  const flowSpeed = finite(options.flowSpeed, 0.03);
  const waves = [
    new THREE.Vector4(0.35, 0, amplitude, 1.1),
    new THREE.Vector4(0, 0.51, amplitude * 0.7, -0.8),
    new THREE.Vector4(0.23, 0.19, quality === 'low' ? 0 : amplitude * 0.35, 0.6),
    new THREE.Vector4(-0.61, 0.4, quality === 'low' ? 0 : amplitude * 0.18, 1.4),
  ];
  const phases = new THREE.Vector4(0, 0, 1.2, 2.3);
  if (Array.isArray(options.waves)) {
    for (let i = 0; i < 4; i += 1) {
      const wave = options.waves[i];
      if (!wave) { waves[i].set(0, 0, 0, 0); continue; }
      const direction = new THREE.Vector2(finite(wave.direction?.[0], 1), finite(wave.direction?.[1], 0));
      if (direction.lengthSq() === 0) direction.set(1, 0);
      direction.normalize().multiplyScalar(2 * Math.PI / Math.max(0.1, finite(wave.wavelength, 12)));
      waves[i].set(direction.x, direction.y, clamp(finite(wave.amplitude, amplitude), 0, 20), finite(wave.speed, 1));
      phases.setComponent(i, finite(wave.phase, 0));
    }
  }
  const vector = (value, target = new THREE.Vector3()) => target.set(finite(value?.[0] ?? value?.x, 0), finite(value?.[1] ?? value?.y, 0), finite(value?.[2] ?? value?.z, 0));
  const windInfluence = clamp(finite(options.windInfluence, 0.15), 0, 1);
  const baseAmplitudes = waves.map(wave => wave.z);
  const windAmplitudes = waves.map((wave, i) => i < 2 ? 0 : wave.z || (!options.waves ? amplitude * 0.15 : 0));
  const windVelocity = new THREE.Vector3();
  const windTarget = new THREE.Vector3();
  const localWind = new THREE.Vector3();
  const inverseWaterMatrix = new THREE.Matrix4();
  const windPhaseRates = [0, 0, 0, 0];
  let attachedHost = null;
  const ripples = Array.from({ length: Math.max(1, capacity) }, () => new THREE.Vector4(0, 0, -1e6, 0));
  const rippleShapes = ripples.map(() => new THREE.Vector4(1, 2, 5, 0.8));
  const depthValues = new Float32Array(depthResolution * depthResolution);
  const depthTexture = new THREE.DataTexture(depthValues, depthResolution, depthResolution, THREE.RedFormat, THREE.FloatType);
  depthTexture.minFilter = depthTexture.magFilter = THREE.NearestFilter;
  depthTexture.generateMipmaps = false;
  const normalMap = options.normalMap ?? null;
  if (normalMap) createTilingTexture(normalMap, { repeat: finite(options.repeat, 8), srgb: false, renderer: options.renderer });
  const material = new THREE.MeshPhysicalMaterial({
    color: options.color ?? 0x1f4f63,
    roughness: clamp(finite(options.roughness, 0.09), 0.02, 1),
    metalness: 0,
    ior: 1.333,
    normalMap,
    transparent: true,
    opacity: clamp(finite(options.opacity, 0.9), 0, 1),
    depthWrite: false,
    envMapIntensity: Math.max(0, finite(options.envMapIntensity, 1)),
  });
  material.normalScale.setScalar(finite(options.normalScale, 0.25));
  const geometry = new THREE.PlaneGeometry(size[0], size[1], segments, segments);
  const mesh = new THREE.Mesh(geometry, material);
  mesh.rotation.x = -Math.PI / 2;
  mesh.name = 'A3GameWater';
  vector(options.position, mesh.position);
  mesh.receiveShadow = true;
  mesh.userData.isA3GameWater = true;
  const displacementBound = waves.reduce((sum, wave, i) => sum + wave.z + windAmplitudes[i] * windInfluence * 3, 0) + capacity * maxRipple;
  geometry.boundingBox = new THREE.Box3(new THREE.Vector3(-size[0] / 2, -size[1] / 2, -displacementBound), new THREE.Vector3(size[0] / 2, size[1] / 2, displacementBound));
  geometry.boundingSphere = geometry.boundingBox.getBoundingSphere(new THREE.Sphere());
  const uniforms = {
    a3WaterTime: { value: 0 },
    a3WaterWaves: { value: waves },
    a3WaterPhases: { value: phases },
    a3WaterRipples: { value: ripples },
    a3WaterRippleShapes: { value: rippleShapes },
    a3WaterRippleLife: { value: lifetime },
    a3WaterSize: { value: new THREE.Vector2(...size) },
    a3WaterDepthMap: { value: depthTexture },
    a3WaterDepthResolution: { value: depthResolution },
    a3WaterWorldY: { value: new THREE.Vector4() },
    a3WaterShallow: { value: new THREE.Color(options.shallowColor ?? 0x55a99b) },
    a3WaterDeep: { value: new THREE.Color(options.deepColor ?? options.color ?? 0x073545) },
    a3WaterAbsorption: { value: new THREE.Vector3(...[0.45, 0.14, 0.08].map((fallback, index) => Math.max(0, finite(options.absorption?.[index], fallback)))) },
    a3WaterFoamWidth: { value: Math.max(0.001, finite(options.foamWidth, 0.6)) },
    a3WaterFoamStrength: { value: clamp(finite(options.foamStrength, 0.65), 0, 1) },
    a3WaterReflection: { value: null },
    a3WaterRefraction: { value: null },
    a3WaterReflectionMatrix: { value: new THREE.Matrix4() },
    a3WaterRefractionMatrix: { value: new THREE.Matrix4() },
    a3WaterReflectionReady: { value: 0 },
    a3WaterRefractionReady: { value: 0 },
    a3WaterDistortion: { value: clamp(finite(options.distortion, 0.015), 0, 0.1) },
  };
  const waveGLSL = `
    uniform float a3WaterTime;
    uniform vec4 a3WaterWaves[4];
    uniform vec4 a3WaterPhases;
    uniform vec4 a3WaterRipples[${ripples.length}];
    uniform vec4 a3WaterRippleShapes[${ripples.length}];
    uniform float a3WaterRippleLife;
    vec3 a3WaveAt(vec2 p) {
      vec3 result = vec3(0.0);
      for (int i = 0; i < 4; i++) {
        vec4 w = a3WaterWaves[i];
        float phase = dot(w.xy, p) + w.w * a3WaterTime + a3WaterPhases[i];
        result += vec3(w.z * sin(phase), w.z * cos(phase) * w.xy);
      }
      for (int i = 0; i < ${ripples.length}; i++) {
        vec4 r = a3WaterRipples[i];
        vec4 s = a3WaterRippleShapes[i];
        float age = a3WaterTime - r.z;
        if (age >= 0.0 && age < a3WaterRippleLife && r.w != 0.0) {
          vec2 offset = p - r.xy;
          float distanceToCenter = sqrt(dot(offset, offset) + 0.000001);
          float travel = distanceToCenter - s.y * age;
          float q = travel / s.x;
          float fade = 1.0 - age / a3WaterRippleLife;
          float envelope = r.w * exp(-s.w * age - q * q) * fade * fade;
          float phase = travel * s.z;
          float slope = envelope * (s.z * cos(phase) - 2.0 * q / s.x * sin(phase));
          result += vec3(envelope * sin(phase), slope * offset / distanceToCenter);
        }
      }
      return result;
    }
  `;
  const varyings = `
    varying vec2 vA3WaterLocal;
    varying vec4 vA3WaterReflection;
    varying vec4 vA3WaterRefraction;
  `;
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = waveGLSL + varyings + `
      uniform mat4 a3WaterReflectionMatrix;
      uniform mat4 a3WaterRefractionMatrix;
    ` + shader.vertexShader;
    if (quality === 'standard') {
      shader.vertexShader = shader.vertexShader
        .replace('#include <beginnormal_vertex>', '#include <beginnormal_vertex>\nvec3 a3Wave = a3WaveAt(position.xy);\nobjectNormal = normalize(vec3(-a3Wave.yz, 1.0));')
        .replace('#include <begin_vertex>', '#include <begin_vertex>\ntransformed.z = a3Wave.x;');
    }
    shader.vertexShader = shader.vertexShader.replace('#include <project_vertex>', `
      vA3WaterLocal = position.xy;
      vA3WaterReflection = a3WaterReflectionMatrix * vec4(transformed, 1.0);
      vA3WaterRefraction = a3WaterRefractionMatrix * vec4(transformed, 1.0);
      #include <project_vertex>
    `);
    shader.fragmentShader = waveGLSL + varyings + `
      uniform vec2 a3WaterSize;
      uniform sampler2D a3WaterDepthMap;
      uniform float a3WaterDepthResolution;
      uniform vec4 a3WaterWorldY;
      uniform vec3 a3WaterShallow;
      uniform vec3 a3WaterDeep;
      uniform vec3 a3WaterAbsorption;
      uniform float a3WaterFoamWidth;
      uniform float a3WaterFoamStrength;
      uniform sampler2D a3WaterReflection;
      uniform sampler2D a3WaterRefraction;
      uniform float a3WaterReflectionReady;
      uniform float a3WaterRefractionReady;
      uniform float a3WaterDistortion;
      float a3BottomAt(vec2 p) {
        vec2 grid = clamp(p / a3WaterSize + 0.5, 0.0, 1.0) * (a3WaterDepthResolution - 1.0);
        vec2 cell = min(floor(grid), vec2(a3WaterDepthResolution - 2.0));
        vec2 f = grid - cell;
        vec2 uv = (cell + 0.5) / a3WaterDepthResolution;
        vec2 stepUV = vec2(1.0 / a3WaterDepthResolution, 0.0);
        float a = texture2D(a3WaterDepthMap, uv).r;
        float b = texture2D(a3WaterDepthMap, uv + stepUV.xy).r;
        float c = texture2D(a3WaterDepthMap, uv + stepUV.yx).r;
        float d = texture2D(a3WaterDepthMap, uv + stepUV.xx).r;
        return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
      }
    ` + shader.fragmentShader;
    shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>', `
      #include <color_fragment>
      float a3Height = a3WaveAt(vA3WaterLocal).x;
      float a3Depth = dot(vec4(vA3WaterLocal, a3Height, 1.0), a3WaterWorldY) - a3BottomAt(vA3WaterLocal);
      if (a3Depth <= 0.0) discard;
      vec3 a3Attenuation = exp(-a3WaterAbsorption * a3Depth);
      vec3 a3Tint = mix(a3WaterDeep, a3WaterShallow, a3Attenuation);
      float a3Foam = (1.0 - smoothstep(0.0, a3WaterFoamWidth, a3Depth)) * a3WaterFoamStrength;
      a3Foam *= 0.7 + 0.3 * sin(dot(vA3WaterLocal, vec2(3.1, 4.7)) - a3WaterTime * 1.7);
      diffuseColor.rgb = mix(a3Tint, vec3(0.9, 0.97, 0.94), a3Foam);
      diffuseColor.a *= smoothstep(0.0, 0.08, a3Depth);
    `).replace('#include <opaque_fragment>', `
      float a3Fresnel = 0.020373 + (1.0 - 0.020373) * pow(1.0 - clamp(dot(normal, normalize(vViewPosition)), 0.0, 1.0), 5.0);
      if (a3WaterRefractionReady > 0.5 && vA3WaterRefraction.w > 0.0) {
        vec2 projected = vA3WaterRefraction.xy / vA3WaterRefraction.w + normal.xy * a3WaterDistortion;
        vec3 transmitted = texture2D(a3WaterRefraction, clamp(projected, 0.001, 0.999)).rgb;
        transmitted = transmitted * a3Attenuation + a3Tint * (1.0 - a3Attenuation);
        outgoingLight = mix(outgoingLight, transmitted + totalSpecular, (1.0 - a3Fresnel) * (1.0 - a3Foam));
        diffuseColor.a = smoothstep(0.0, 0.08, a3Depth);
      }
      if (a3WaterReflectionReady > 0.5 && vA3WaterReflection.w > 0.0) {
        vec2 projected = vA3WaterReflection.xy / vA3WaterReflection.w + normal.xy * a3WaterDistortion;
        vec3 reflected = texture2D(a3WaterReflection, clamp(projected, 0.001, 0.999)).rgb;
        outgoingLight = mix(outgoingLight, reflected, a3Fresnel * (1.0 - a3Foam));
      }
      #include <opaque_fragment>
    `);
  };
  material.customProgramCacheKey = () => `a3-water-v1-${quality}-${ripples.length}`;
  material.userData.waterUniforms = uniforms;
  let elapsed = 0;
  let cursor = 0;
  let disposed = false;
  let unsubscribe = null;
  let reflector = null;
  let refractor = null;
  let rendering = false;
  let lastPassTime = -Infinity;
  let lastRenderTime = -Infinity;
  let passCount = 0;
  let depthDirty = true;
  const lastCameraMatrix = new THREE.Matrix4();
  const lastProjectionMatrix = new THREE.Matrix4();
  const lastPassMatrix = new THREE.Matrix4();
  const lastMatrix = new THREE.Matrix4();
  const normalMatrix = new THREE.Matrix3();
  const scratch = new THREE.Vector3();
  const waveResult = new THREE.Vector3();

  function evaluate(x, y, time = elapsed, target = new THREE.Vector3()) {
    let h = 0, dx = 0, dy = 0;
    for (let i = 0; i < 4; i += 1) {
      const w = waves[i];
      const phase = w.x * x + w.y * y + w.w * time + phases.getComponent(i) + windPhaseRates[i] * (time - elapsed);
      h += w.z * Math.sin(phase);
      const slope = w.z * Math.cos(phase);
      dx += slope * w.x;
      dy += slope * w.y;
    }
    for (let i = 0; i < capacity; i += 1) {
      const r = ripples[i], s = rippleShapes[i];
      const age = time - r.z;
      if (age < 0 || age >= lifetime || r.w === 0) continue;
      const ox = x - r.x, oy = y - r.y;
      const distance = Math.sqrt(ox * ox + oy * oy + 0.000001);
      const travel = distance - s.y * age;
      const q = travel / s.x;
      const fade = 1 - age / lifetime;
      const envelope = r.w * Math.exp(-s.w * age - q * q) * fade * fade;
      const phase = travel * s.z;
      const slope = envelope * (s.z * Math.cos(phase) - 2 * q / s.x * Math.sin(phase));
      h += envelope * Math.sin(phase);
      dx += slope * ox / distance;
      dy += slope * oy / distance;
    }
    return target.set(h, dx, dy);
  }

  function syncTransform() {
    mesh.updateWorldMatrix(true, false);
    if (!depthDirty && lastMatrix.equals(mesh.matrixWorld)) return;
    options.refreshTerrain?.();
    const e = mesh.matrixWorld.elements;
    uniforms.a3WaterWorldY.value.set(e[1], e[5], e[9], e[13]);
    normalMatrix.getNormalMatrix(mesh.matrixWorld);
    for (let row = 0; row < depthResolution; row += 1) {
      for (let col = 0; col < depthResolution; col += 1) {
        scratch.set((col / (depthResolution - 1) - 0.5) * size[0], (row / (depthResolution - 1) - 0.5) * size[1], 0).applyMatrix4(mesh.matrixWorld);
        const bottom = typeof options.terrainHeight === 'function' ? options.terrainHeight(scratch.x, scratch.z) : scratch.y - defaultDepth;
        depthValues[row * depthResolution + col] = Number.isFinite(bottom) ? bottom : scratch.y - defaultDepth;
      }
    }
    depthTexture.needsUpdate = true;
    lastMatrix.copy(mesh.matrixWorld);
    depthDirty = false;
  }

  function locate(x, z, time = elapsed) {
    if (disposed || !Number.isFinite(x) || !Number.isFinite(z)) return null;
    syncTransform();
    const e = mesh.matrixWorld.elements;
    const det = e[0] * e[6] - e[4] * e[2];
    if (Math.abs(det) < 1e-10) return null;
    let lx = ((x - e[12]) * e[6] - (z - e[14]) * e[4]) / det;
    let ly = ((z - e[14]) * e[0] - (x - e[12]) * e[2]) / det;
    for (let i = 0; i < 8; i += 1) {
      evaluate(lx, ly, time, waveResult);
      const fx = e[0] * lx + e[4] * ly + e[8] * waveResult.x + e[12] - x;
      const fz = e[2] * lx + e[6] * ly + e[10] * waveResult.x + e[14] - z;
      if (Math.abs(fx) + Math.abs(fz) < 1e-7) break;
      const a = e[0] + e[8] * waveResult.y, b = e[4] + e[8] * waveResult.z;
      const c = e[2] + e[10] * waveResult.y, d = e[6] + e[10] * waveResult.z;
      const jacobian = a * d - b * c;
      if (Math.abs(jacobian) < 1e-10) return null;
      lx -= (fx * d - fz * b) / jacobian;
      ly -= (fz * a - fx * c) / jacobian;
    }
    if (Math.abs(lx) > size[0] / 2 + 1e-7 || Math.abs(ly) > size[1] / 2 + 1e-7) return null;
    evaluate(lx, ly, time, waveResult);
    if (Math.abs(e[0] * lx + e[4] * ly + e[8] * waveResult.x + e[12] - x) + Math.abs(e[2] * lx + e[6] * ly + e[10] * waveResult.x + e[14] - z) > 0.001) return null;
    return { x: lx, y: ly, height: e[1] * lx + e[5] * ly + e[9] * waveResult.x + e[13], dx: waveResult.y, dy: waveResult.z };
  }

  function bottomAt(x, y) {
    const gx = clamp(x / size[0] + 0.5, 0, 1) * (depthResolution - 1);
    const gy = clamp(y / size[1] + 0.5, 0, 1) * (depthResolution - 1);
    const ix = Math.min(Math.floor(gx), depthResolution - 2), iy = Math.min(Math.floor(gy), depthResolution - 2);
    const fx = gx - ix, fy = gy - iy;
    const offset = iy * depthResolution + ix;
    const a = THREE.MathUtils.lerp(depthValues[offset], depthValues[offset + 1], fx);
    const b = THREE.MathUtils.lerp(depthValues[offset + depthResolution], depthValues[offset + depthResolution + 1], fx);
    return THREE.MathUtils.lerp(a, b, fy);
  }

  const sampleHeight = (x, z) => locate(x, z)?.height ?? null;
  const sampleNormal = (x, z, target = new THREE.Vector3()) => {
    const point = locate(x, z);
    if (!point) return null;
    target.set(-point.dx, -point.dy, 1).applyMatrix3(normalMatrix).normalize();
    if (target.y < 0) target.negate();
    return target;
  };
  const sampleDepth = (x, z) => {
    const point = locate(x, z);
    return point ? Math.max(0, point.height - bottomAt(point.x, point.y)) : null;
  };
  const sampleBottom = (x, z) => {
    const point = locate(x, z);
    return point ? bottomAt(point.x, point.y) : null;
  };
  /** World-space current (m/s) plus analytic vertical wave velocity. */
  const sampleVelocity = (position, time = elapsed, target = new THREE.Vector3()) => {
    if (time?.isVector3) { target = time; time = elapsed; }
    const point = vector(position);
    const water = locate(point.x, point.z, finite(time, elapsed));
    if (!water || water.height <= bottomAt(water.x, water.y)) return null;
    target.set(0, 0, 0);
    const current = typeof options.current === 'function' ? options.current(point, finite(time, elapsed), target) : options.current;
    vector(current ?? target, target);
    const before = locate(point.x, point.z, finite(time, elapsed) - 0.001);
    const after = locate(point.x, point.z, finite(time, elapsed) + 0.001);
    if (before && after) target.y += (after.height - before.height) / 0.002;
    return target;
  };
  const updateLowGeometry = () => {
    if (quality !== 'low') return;
    const position = geometry.attributes.position, normals = geometry.attributes.normal;
    for (let i = 0; i < position.count; i += 1) {
      evaluate(position.getX(i), position.getY(i), elapsed, waveResult);
      position.setZ(i, waveResult.x);
      scratch.set(-waveResult.y, -waveResult.z, 1).normalize();
      normals.setXYZ(i, scratch.x, scratch.y, scratch.z);
    }
    position.needsUpdate = true;
    normals.needsUpdate = true;
  };
  const update = (delta) => {
    if (disposed) return;
    const dt = Math.max(0, finite(delta, 0));
    elapsed += dt;
    uniforms.a3WaterTime.value = elapsed;
    syncTransform();
    windTarget.set(0, 0, 0);
    if (windInfluence && typeof attachedHost?.wind?.sample === 'function') {
      const position = new THREE.Vector3().setFromMatrixPosition(mesh.matrixWorld);
      const sampled = attachedHost.wind.sample(position, finite(attachedHost.wind.elapsedSeconds, elapsed), windTarget);
      vector(sampled ?? windTarget, windTarget).clampLength(0, 30);
    }
    windVelocity.lerp(windTarget, -Math.expm1(-dt / 2));
    inverseWaterMatrix.copy(mesh.matrixWorld).invert();
    const e = inverseWaterMatrix.elements;
    localWind.set(e[0] * windVelocity.x + e[4] * windVelocity.y + e[8] * windVelocity.z,
      e[1] * windVelocity.x + e[5] * windVelocity.y + e[9] * windVelocity.z, 0);
    const strength = Math.min(3, windVelocity.length() / 10);
    for (let i = 2; i < 4; i += 1) {
      waves[i].z = baseAmplitudes[i] + windAmplitudes[i] * windInfluence * strength;
      windPhaseRates[i] = -(waves[i].x * localWind.x + waves[i].y * localWind.y) * windInfluence * 0.15;
      phases.setComponent(i, phases.getComponent(i) + windPhaseRates[i] * dt);
    }
    if (material.normalMap) {
      material.normalMap.offset.x += dt * (flowSpeed + localWind.x * windInfluence * 0.01);
      material.normalMap.offset.y += dt * (flowSpeed * 0.6 + localWind.y * windInfluence * 0.01);
    }
    updateLowGeometry();
  };
  const addRipple = (x, z, strength = 0.12, radius = 1, rippleOptions = {}) => {
    if (!capacity || disposed) return false;
    const point = locate(x, z);
    if (!point || sampleDepth(x, z) <= 0) return false;
    const index = cursor;
    cursor = (cursor + 1) % capacity;
    ripples[index].set(point.x, point.y, elapsed, clamp(finite(strength, 0.12), -maxRipple, maxRipple));
    rippleShapes[index].set(Math.max(0.05, finite(radius, 1)), Math.max(0, finite(rippleOptions.speed, 2)), Math.max(0.01, finite(rippleOptions.frequency, 5)), Math.max(0, finite(rippleOptions.decay, 0.8)));
    updateLowGeometry();
    return true;
  };

  /** Points are column bottoms; draft extends upwards. Only overlap with [bottom, surface] displaces water. */
  const computeBuoyancy = (settings = {}) => {
    const center = vector(settings.centerOfMass);
    const points = (Array.isArray(settings.points) ? settings.points : [center]).slice(0, 64).map(point => vector(point));
    const velocity = vector(settings.velocity), angular = vector(settings.angularVelocity);
    const volume = clamp(finite(settings.volume, 1), 0, 1e12);
    const draft = clamp(finite(settings.draft, 0.5), 0.001, 1e9);
    const density = clamp(finite(settings.density, 1000), 0, 1e9);
    const gravity = clamp(finite(settings.gravity, 9.81), 0, 1e6);
    const damping = clamp(finite(settings.damping, 80), 0, 1e12);
    const result = { force: new THREE.Vector3(), torque: new THREE.Vector3(), submergedFraction: 0, points: [] };
    for (const point of points) {
      const water = locate(point.x, point.z);
      const bottom = water ? bottomAt(water.x, water.y) : null;
      const depth = water ? Math.max(0, Math.min(point.y + draft, water.height) - Math.max(point.y, bottom)) : 0;
      const fraction = clamp(depth / draft, 0, 1);
      const force = new THREE.Vector3();
      if (fraction > 0) {
        const offset = point.clone().sub(center);
        const relativeVelocity = angular.clone().cross(offset).add(velocity);
        const fluidVelocity = sampleVelocity(new THREE.Vector3(point.x, Math.max(point.y, bottom) + depth / 2, point.z));
        if (fluidVelocity) relativeVelocity.sub(fluidVelocity);
        force.copy(relativeVelocity).multiplyScalar(-damping * fraction / points.length);
        force.y += density * gravity * volume * fraction / points.length;
        result.force.add(force);
        result.torque.add(offset.cross(force));
      }
      result.submergedFraction += fraction / points.length;
      result.points.push({ position: point, force, depth, bottom, surface: water?.height ?? null, submergedFraction: fraction });
    }
    return result;
  };

  const passSize = clamp(Math.trunc(finite(options.reflectionResolution, 256)), 64, 1024);
  const passInterval = 1 / clamp(finite(options.reflectionUpdateRate, 30), 1, 60);
  mesh.onBeforeRender = (renderer, scene, camera) => {
    if (disposed || rendering) return;
    syncTransform();
    if (quality === 'low' || (!options.reflection && !options.refraction)) return;
    camera.updateWorldMatrix(true, false);
    const viewChanged = !lastCameraMatrix.equals(camera.matrixWorld)
      || !lastProjectionMatrix.equals(camera.projectionMatrix) || !lastPassMatrix.equals(mesh.matrixWorld);
    const paused = elapsed === lastRenderTime;
    lastRenderTime = elapsed;
    if (!(paused && viewChanged) && elapsed - lastPassTime < passInterval) return;
    const worldNormal = new THREE.Vector3(0, 0, 1).applyMatrix3(normalMatrix).normalize();
    const worldPosition = new THREE.Vector3().setFromMatrixPosition(mesh.matrixWorld);
    const cameraPosition = new THREE.Vector3().setFromMatrixPosition(camera.matrixWorld);
    if (cameraPosition.sub(worldPosition).dot(worldNormal) <= 0) return;
    const target = renderer.getRenderTarget();
    const cubeFace = renderer.getActiveCubeFace?.() ?? 0;
    const mip = renderer.getActiveMipmapLevel?.() ?? 0;
    const viewport = renderer.getViewport?.(new THREE.Vector4());
    const scissor = renderer.getScissor?.(new THREE.Vector4());
    const scissorTest = renderer.getScissorTest?.();
    const xrEnabled = renderer.xr.enabled;
    const shadowAutoUpdate = renderer.shadowMap.autoUpdate;
    const context = renderer.getContext?.();
    const depthMask = context?.getParameter(context.DEPTH_WRITEMASK);
    const hidden = [];
    rendering = true;
    try {
      scene.traverse((object) => {
        if (object.userData?.isA3GameWater || object.isReflector || object.isRefractor) {
          hidden.push([object, object.visible]);
          object.visible = false;
        }
      });
      const args = { textureWidth: passSize, textureHeight: passSize, multisample: 0, clipBias: 0.003 };
      if (options.reflection) {
        reflector ??= new Reflector(geometry, args);
        reflector.matrixWorld.copy(mesh.matrixWorld);
        reflector.onBeforeRender(renderer, scene, camera);
        uniforms.a3WaterReflection.value = reflector.getRenderTarget().texture;
        uniforms.a3WaterReflectionMatrix.value.copy(reflector.material.uniforms.textureMatrix.value);
        uniforms.a3WaterReflectionReady.value = 1;
        passCount += 1;
      }
      if (options.refraction && camera.isPerspectiveCamera) {
        refractor ??= new Refractor(geometry, args);
        refractor.matrixWorld.copy(mesh.matrixWorld);
        refractor.onBeforeRender(renderer, scene, camera);
        uniforms.a3WaterRefraction.value = refractor.getRenderTarget().texture;
        uniforms.a3WaterRefractionMatrix.value.copy(refractor.material.uniforms.textureMatrix.value);
        uniforms.a3WaterRefractionReady.value = 1;
        passCount += 1;
      }
      lastPassTime = elapsed;
      lastCameraMatrix.copy(camera.matrixWorld);
      lastProjectionMatrix.copy(camera.projectionMatrix);
      lastPassMatrix.copy(mesh.matrixWorld);
    } finally {
      renderer.xr.enabled = xrEnabled;
      renderer.shadowMap.autoUpdate = shadowAutoUpdate;
      renderer.setRenderTarget(target, cubeFace, mip);
      if (viewport) renderer.setViewport(viewport);
      if (scissor) renderer.setScissor(scissor);
      if (scissorTest !== undefined) renderer.setScissorTest(scissorTest);
      if (depthMask !== undefined) renderer.state.buffers.depth.setMask(depthMask);
      for (const [object, visible] of hidden) object.visible = visible;
      rendering = false;
    }
  };
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    unsubscribe?.();
    unsubscribe = null;
    reflector?.dispose();
    refractor?.dispose();
    reflector = refractor = null;
    uniforms.a3WaterReflection.value = uniforms.a3WaterRefraction.value = null;
    uniforms.a3WaterReflectionReady.value = uniforms.a3WaterRefractionReady.value = 0;
    depthTexture.dispose();
    geometry.dispose();
    material.dispose();
    mesh.visible = false;
    mesh.userData.disposed = true;
  };
  Object.assign(mesh.userData, {
    quality,
    update,
    sampleHeight,
    sampleNormal,
    sampleDepth,
    sampleBottom,
    sampleVelocity,
    addRipple,
    computeBuoyancy,
    applyBuoyancy: (body, settings = {}) => {
      if (typeof body?.applyForce !== 'function') throw new TypeError('Water buoyancy needs body.applyForce(force, worldPoint)');
      const result = computeBuoyancy({ centerOfMass: body.position, velocity: body.velocity, angularVelocity: body.angularVelocity, ...settings });
      for (const point of result.points) if (point.submergedFraction > 0) body.applyForce(point.force, point.position);
      return result;
    },
    refreshDepth: () => { if (!disposed) { depthDirty = true; syncTransform(); } },
    attachToHost: (host) => {
      if (disposed) throw new Error('Cannot attach disposed water');
      if (typeof host?.onTick !== 'function') throw new TypeError('Water attachToHost needs host.onTick');
      unsubscribe?.();
      attachedHost = host;
      const detach = host.onTick(update);
      let attached = true;
      unsubscribe = () => { if (attached) { attached = false; attachedHost = null; detach?.(); } };
      return unsubscribe;
    },
    dispose,
    getState: () => ({
      quality,
      elapsedSeconds: elapsed,
      disposed,
      rippleCapacity: capacity,
      activeRipples: ripples.filter((r) => r.w !== 0 && elapsed >= r.z && elapsed - r.z < lifetime).length,
      renderTargetCount: Number(Boolean(reflector)) + Number(Boolean(refractor)),
      reflectionPasses: passCount,
      depthResolution,
      simulation: 'analytic-waves-approximate-buoyancy',
    }),
  });
  syncTransform();
  updateLowGeometry();
  return mesh;
}

/**
 * A silhouetted mountain range closing the horizon.
 *
 * The hard edge where a ground plane stops is the single most obvious
 * "this is a demo" tell in an outdoor scene, and fog alone does not fix
 * it: fog fades the ground into the sky and leaves nothing behind it.
 * A ring of noise-driven peaks, fog-affected and unlit, closes the
 * horizon for one draw call and no shadow cost.
 *
 * @param {{radius?: number, height?: number, segments?: number,
 *          color?: number|string, topColor?: number|string,
 *          seed?: number, roughness?: number, baseY?: number}} [options]
 * @returns {THREE.Mesh}
 */
export function createDistantRange(options = {}) {
  const radius = Number(options.radius ?? 320);
  const height = Number(options.height ?? 70);
  const segments = Math.max(16, Math.trunc(Number(options.segments ?? 96)));
  const random = createSeededRandom(Number(options.seed ?? 7));
  const baseY = Number(options.baseY ?? -2);
  const jitter = Number(options.roughness ?? 0.55);

  // Two overlaid sine series with random phases: cheap, seamless around
  // the ring (integer frequencies), and never produces the regular
  // sawtooth a single frequency gives.
  const waves = Array.from({ length: 5 }, (_, index) => ({
    frequency: 2 + index * 3,
    phase: random() * Math.PI * 2,
    amplitude: 1 / (index + 1.4),
  }));
  const peakAt = (angle) => {
    let value = 0;
    let total = 0;
    for (const wave of waves) {
      value += Math.sin(angle * wave.frequency + wave.phase) * wave.amplitude;
      total += wave.amplitude;
    }
    return 0.45 + (value / total) * 0.5 * (1 + jitter * (random() - 0.5) * 0.2);
  };

  const positions = [];
  const colors = [];
  const base = new THREE.Color(options.color ?? 0x4a5a6e);
  const top = new THREE.Color(options.topColor ?? 0x8fa4bc);
  const push = (angle, y, blend) => {
    positions.push(Math.cos(angle) * radius, y, Math.sin(angle) * radius);
    const color = base.clone().lerp(top, blend);
    colors.push(color.r, color.g, color.b);
  };

  const peaks = Array.from({ length: segments }, (_, i) =>
    baseY + height * peakAt((i / segments) * Math.PI * 2));
  for (let i = 0; i < segments; i += 1) {
    const a0 = (i / segments) * Math.PI * 2;
    const a1 = ((i + 1) / segments) * Math.PI * 2;
    const h0 = peaks[i];
    const h1 = peaks[(i + 1) % segments];
    // Two triangles per segment, wound so the inside of the ring is front.
    push(a0, baseY, 0);
    push(a1, baseY, 0);
    push(a1, h1, 1);
    push(a0, baseY, 0);
    push(a1, h1, 1);
    push(a0, h0, 1);
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    'position',
    new THREE.Float32BufferAttribute(positions, 3),
  );
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  geometry.computeVertexNormals();
  const mesh = new THREE.Mesh(
    geometry,
    new THREE.MeshBasicMaterial({
      vertexColors: true,
      side: THREE.DoubleSide,
      fog: true,
      depthWrite: true,
    }),
  );
  mesh.name = 'A3GameDistantRange';
  mesh.frustumCulled = false;
  mesh.renderOrder = -900;
  return mesh;
}

/**
 * A drifting sprite cloud layer, for parallax the sky shader cannot give.
 *
 * The dome's clouds are painted at infinity, so they never move relative
 * to the player. A handful of soft billboards a few hundred metres up do
 * move, and that parallax is what tells the eye the sky has depth.
 *
 * @param {{count?: number, radius?: number, height?: number,
 *          size?: number | number[], texture?: THREE.Texture,
 *          color?: number|string, opacity?: number, speed?: number,
 *          seed?: number}} [options]
 * @returns {THREE.Group} with `userData.update(delta)`
 */
export function createCloudLayer(options = {}) {
  const count = Math.max(0, Math.trunc(Number(options.count ?? 18)));
  const radius = Number(options.radius ?? 420);
  const height = Number(options.height ?? 150);
  const sizeRange = Array.isArray(options.size)
    ? options.size
    : [Number(options.size ?? 120) * 0.6, Number(options.size ?? 120)];
  const random = createSeededRandom(Number(options.seed ?? 31));
  const texture =
    options.texture ?? createRadialGradientTexture({ resolution: 128, color: '#ffffff' });
  const material = new THREE.SpriteMaterial({
    map: texture,
    color: new THREE.Color(options.color ?? 0xffffff),
    transparent: true,
    opacity: Number(options.opacity ?? 0.34),
    depthWrite: false,
    fog: false,
  });

  const group = new THREE.Group();
  group.name = 'A3GameCloudLayer';
  group.renderOrder = -800;
  const speed = Number(options.speed ?? 1.6);
  for (let i = 0; i < count; i += 1) {
    const sprite = new THREE.Sprite(material.clone());
    const angle = random() * Math.PI * 2;
    const distance = radius * (0.45 + random() * 0.55);
    const scale = sizeRange[0] + random() * (sizeRange[1] - sizeRange[0]);
    sprite.position.set(
      Math.cos(angle) * distance,
      height * (0.75 + random() * 0.5),
      Math.sin(angle) * distance,
    );
    sprite.scale.set(scale, scale * (0.32 + random() * 0.18), 1);
    sprite.material.opacity *= 0.6 + random() * 0.7;
    sprite.userData.speed = speed * (0.6 + random() * 0.8);
    group.add(sprite);
  }
  let windField = options.windField ?? null;
  let unsubscribe = null;
  const position = new THREE.Vector3(), wind = new THREE.Vector3(), localEnd = new THREE.Vector3();
  const wrap = value => ((value + radius) % (radius * 2) + radius * 2) % (radius * 2) - radius;
  group.userData.update = (delta) => {
    const step = Math.max(0, Number(delta) || 0);
    for (const sprite of group.children) {
      if (windField) {
        sprite.getWorldPosition(position);
        windField.sample(position, windField.elapsedSeconds, wind);
        localEnd.copy(position).addScaledVector(wind, step);
        group.worldToLocal(localEnd);
        sprite.position.copy(localEnd);
        sprite.position.x = wrap(sprite.position.x);
        sprite.position.z = wrap(sprite.position.z);
      } else {
        sprite.position.x = wrap(sprite.position.x + sprite.userData.speed * step);
      }
    }
  };
  group.userData.attachToHost = (host) => {
    unsubscribe?.();
    windField = host.wind;
    unsubscribe = host.onTick(group.userData.update);
    return unsubscribe;
  };
  group.userData.dispose = () => { unsubscribe?.(); unsubscribe = null; material.dispose(); };
  return group;
}

/** Read a loose vector-ish value into a `THREE.Vector3`. */
function readVector3(value, fallback = [0, 0, 0]) {
  if (value?.isVector3) return value.clone();
  if (Array.isArray(value) && value.length >= 3) {
    return new THREE.Vector3(Number(value[0]), Number(value[1]), Number(value[2]));
  }
  if (value && typeof value === 'object' && 'x' in value) {
    return new THREE.Vector3(
      Number(value.x ?? 0),
      Number(value.y ?? 0),
      Number(value.z ?? 0),
    );
  }
  return new THREE.Vector3(fallback[0], fallback[1], fallback[2]);
}
