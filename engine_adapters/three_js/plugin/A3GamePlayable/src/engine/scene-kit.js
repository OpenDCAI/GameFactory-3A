import * as THREE from 'three';
import { createSeededRandom } from './visual-kit.js';

const finite = (value, name) => {
  const number = Number(value);
  if (!Number.isFinite(number)) throw new RangeError(`${name} must be finite`);
  return number;
};
const positive = (value, name) => {
  const number = finite(value, name);
  if (number <= 0) throw new RangeError(`${name} must be positive`);
  return number;
};
const point3 = (point) => {
  const x = Array.isArray(point) ? point[0] : point.x;
  const y = Array.isArray(point) ? (point.length === 3 ? point[1] : 0) : (point.y ?? 0);
  const z = Array.isArray(point) ? point[point.length === 3 ? 2 : 1] : point.z;
  return new THREE.Vector3(finite(x, 'x'), finite(y, 'y'), finite(z, 'z'));
};

/** Heading in radians for runtime forward -Z; a vertical/zero vector has no yaw. */
export function directionToYaw(direction) {
  const x = finite(direction.x, 'direction.x');
  const z = finite(direction.z, 'direction.z');
  if (Math.hypot(x, z) < 1e-10) throw new RangeError('direction must have an XZ component');
  return Math.atan2(-x, -z);
}

/** Runtime yaw/pitch to a unit forward vector. Positive pitch looks upward. */
export function yawToDirection(yaw, pitch = 0, target = new THREE.Vector3()) {
  finite(yaw, 'yaw');
  finite(pitch, 'pitch');
  const cosPitch = Math.cos(pitch);
  return target.set(-Math.sin(yaw) * cosPitch, Math.sin(pitch), -Math.cos(yaw) * cosPitch);
}

/** Ground-plane corners of a box; rotation uses THREE's right-handed Y rotation. */
export function footprintCorners({ x = 0, z = 0, width, depth, rotation = 0 }, margin = 0) {
  margin = finite(margin, 'margin');
  const w = positive(width, 'width') / 2 + margin;
  const d = positive(depth, 'depth') / 2 + margin;
  if (w < 0 || d < 0) throw new RangeError('margin inverts the footprint');
  x = finite(x, 'x'); z = finite(z, 'z'); rotation = finite(rotation, 'rotation');
  const c = Math.cos(rotation), s = Math.sin(rotation);
  return [[-w, -d], [w, -d], [w, d], [-w, d]].map(([px, pz]) => ({
    x: x + c * px + s * pz, z: z - s * px + c * pz,
  }));
}

/** XZ distance to a finite polyline, including its endpoints. */
export function distanceToPolyline(point, points, closed = false) {
  if (!points.length) return Infinity;
  const p = point3(point);
  const vertices = points.map(point3);
  if (vertices.length === 1) return Math.hypot(p.x - vertices[0].x, p.z - vertices[0].z);
  let best = Infinity;
  const count = vertices.length - (closed ? 0 : 1);
  for (let i = 0; i < count; i++) {
    const a = vertices[i], b = vertices[(i + 1) % vertices.length];
    const dx = b.x - a.x, dz = b.z - a.z;
    const lengthSq = dx * dx + dz * dz;
    const t = lengthSq ? THREE.MathUtils.clamp(((p.x - a.x) * dx + (p.z - a.z) * dz) / lengthSq, 0, 1) : 0;
    best = Math.min(best, Math.hypot(p.x - a.x - dx * t, p.z - a.z - dz * t));
  }
  return best;
}

/**
 * Terrain-conforming path mesh. points: Vector3, {x,y?,z}, [x,z] or [x,y,z].
 * width/lift/tileLength are metres; segments is subdivisions per source segment.
 * UVs use world distance, not vertex count. The caller owns the supplied material.
 */
export function createGroundRibbon(points, options = {}) {
  const width = positive(options.width ?? 2, 'width');
  const tileLength = positive(options.tileLength ?? 2, 'tileLength');
  const subdivisions = Math.ceil(positive(options.segments ?? 8, 'segments'));
  if (subdivisions > 1024) throw new RangeError('segments must not exceed 1024');
  const lift = finite(options.lift ?? 0.03, 'lift');
  const closed = Boolean(options.closed);
  const source = [];
  for (const value of points) {
    const point = point3(value);
    const previous = source[source.length - 1];
    if (!previous || Math.hypot(point.x - previous.x, point.z - previous.z) > 1e-8) source.push(point);
  }
  if (closed && source.length > 1 && source[0].distanceTo(source[source.length - 1]) < 1e-8) source.pop();
  if (source.length < (closed ? 3 : 2)) throw new RangeError('ribbon needs distinct XZ points');
  const centres = [];
  const count = source.length - (closed ? 0 : 1);
  for (let i = 0; i < count; i++) {
    const a = source[i], b = source[(i + 1) % source.length];
    for (let j = 0; j < subdivisions; j++) centres.push(a.clone().lerp(b, j / subdivisions));
  }
  centres.push((closed ? source[0] : source[source.length - 1]).clone());
  const positions = [], uvs = [], indices = [];
  const heightAt = options.heightAt;
  const last = centres.length - 1;
  let length = 0;
  for (let i = 0; i <= last; i++) {
    const centre = centres[i];
    const previous = centres[i === 0 ? (closed ? last - 1 : 0) : i - 1];
    const next = centres[i === last ? (closed ? 1 : last) : i + 1];
    let dx = next.x - previous.x, dz = next.z - previous.z;
    if (Math.hypot(dx, dz) < 1e-8) { dx = next.x - centre.x; dz = next.z - centre.z; }
    const distance = Math.hypot(dx, dz);
    if (distance < 1e-8) throw new RangeError('ribbon contains a reversing cusp');
    if (i > 0) length += Math.hypot(centre.x - centres[i - 1].x, centre.z - centres[i - 1].z);
    for (const side of [1, -1]) {
      const x = centre.x - dz / distance * width * 0.5 * side;
      const z = centre.z + dx / distance * width * 0.5 * side;
      const y = heightAt ? finite(heightAt(x, z), 'heightAt result') : centre.y;
      positions.push(x, y + lift, z);
      uvs.push(side === 1 ? 0 : width / tileLength, length / tileLength);
    }
    if (i < last) {
      const n = i * 2;
      indices.push(n, n + 2, n + 1, n + 1, n + 2, n + 3);
    }
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute('uv', new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  geometry.computeBoundingBox();
  geometry.computeBoundingSphere();
  const material = options.material ?? new THREE.MeshStandardMaterial({ color: 0x9c8864, roughness: 0.95 });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = options.name ?? 'ground_ribbon';
  mesh.receiveShadow = true;
  mesh.userData.pathLength = length;
  return mesh;
}

/**
 * Seeded facade albedo, generated without DOM/Canvas. Lit windows are baked colour,
 * not a light source; use the same texture as emissiveMap with a restrained intensity.
 */
export function createFacadeTexture(options = {}) {
  const width = Math.floor(positive(options.width ?? 256, 'width'));
  const height = Math.floor(positive(options.height ?? 512, 'height'));
  if (width < 16 || height < 16 || width > 2048 || height > 2048) throw new RangeError('facade dimensions must be 16..2048');
  const columns = Math.ceil(positive(options.columns ?? 6, 'columns'));
  const rows = Math.ceil(positive(options.rows ?? 12, 'rows'));
  if (columns > width / 4 || rows > height / 4) throw new RangeError('window cells must be at least 4 pixels');
  const ratio = THREE.MathUtils.clamp(finite(options.litRatio ?? 0.45, 'litRatio'), 0, 1);
  const random = createSeededRandom(finite(options.seed ?? 1, 'seed'));
  const wall = new THREE.Color(options.wallColor ?? 0x263448);
  const glass = new THREE.Color(options.windowColor ?? 0x101d2b);
  const lit = new THREE.Color(options.litColor ?? 0xffd99b);
  const cells = Array.from({ length: rows * columns }, () => ({ lit: random() < ratio, shade: 0.68 + random() * 0.32 }));
  const data = new Uint8Array(width * height * 4);
  const color = new THREE.Color();
  for (let y = 0; y < height; y++) {
    const row = Math.floor(y / height * rows), v = (y / height * rows) % 1;
    for (let x = 0; x < width; x++) {
      const column = Math.floor(x / width * columns), u = (x / width * columns) % 1;
      const cell = cells[row * columns + column];
      const window = u > 0.18 && u < 0.82 && v > 0.2 && v < 0.82;
      const mullion = Math.abs(u - 0.5) < 0.018 || Math.abs(v - 0.5) < 0.018;
      color.copy(window && !mullion ? (cell.lit ? lit : glass) : wall);
      color.multiplyScalar(window ? cell.shade : (v < 0.075 ? 0.58 : 0.92));
      color.convertLinearToSRGB();
      const offset = (y * width + x) * 4;
      data[offset] = Math.round(THREE.MathUtils.clamp(color.r, 0, 1) * 255);
      data[offset + 1] = Math.round(THREE.MathUtils.clamp(color.g, 0, 1) * 255);
      data[offset + 2] = Math.round(THREE.MathUtils.clamp(color.b, 0, 1) * 255);
      data[offset + 3] = 255;
    }
  }
  const texture = new THREE.DataTexture(data, width, height);
  texture.name = options.name ?? 'facade';
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.magFilter = THREE.LinearFilter;
  texture.minFilter = THREE.LinearMipmapLinearFilter;
  texture.generateMipmaps = true;
  texture.anisotropy = 4;
  texture.needsUpdate = true;
  return texture;
}
