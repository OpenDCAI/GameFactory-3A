import * as THREE from 'three';

const readVelocity = (value = [0, 0, 0]) => {
  const values = Array.isArray(value) ? value : [value.x, value.y, value.z];
  if (values.length !== 3 || !values.every(v => Number.isFinite(Number(v)))) {
    throw new TypeError('Wind velocity must contain three finite numbers in metres/second');
  }
  return new THREE.Vector3(...values.map(Number));
};
const number = (value, fallback, min = 0) => {
  const result = Number(value ?? fallback);
  if (!Number.isFinite(result) || result < min) throw new RangeError('Invalid wind parameter');
  return result;
};

/** Deterministic world-space air velocity; no obstacle or pressure solver. */
export class A3GameWindField {
  constructor(config = {}) {
    this.velocity = new THREE.Vector3();
    this.targetVelocity = new THREE.Vector3();
    this.displacement = new THREE.Vector3();
    this.elapsedSeconds = 0;
    this.set(config, true);
  }

  set(config = {}, immediate = false) {
    const velocity = config.velocity === undefined ? this.targetVelocity.clone() : readVelocity(config.velocity);
    const gustStrength = number(config.gustStrength, this.gustStrength ?? 0);
    const gustPeriod = number(config.gustPeriod, this.gustPeriod ?? 6, 0.1);
    const spatialScale = number(config.spatialScale, this.spatialScale ?? 30, 0.1);
    const response = number(config.response, this.response ?? 1.5, 0.01);
    const heightShear = number(config.heightShear, this.heightShear ?? 0.05);
    const seed = number(config.seed, this.seed ?? 1, -Number.MAX_SAFE_INTEGER);
    Object.assign(this, { gustStrength, gustPeriod, spatialScale, response, heightShear, seed });
    this.targetVelocity.copy(velocity);
    if (immediate) this.velocity.copy(velocity);
    return this;
  }

  sample(position, time = this.elapsedSeconds, target = new THREE.Vector3()) {
    const t = Number(time);
    if (!Number.isFinite(t)) throw new RangeError('Wind time must be finite');
    const x = position?.x ?? 0, y = position?.y ?? 0, z = position?.z ?? 0;
    const phase = (this.seed % 10000) * 0.61803398875;
    const w = 2 * Math.PI / this.gustPeriod;
    const a = Math.sin(t * w + x / this.spatialScale + phase);
    const b = Math.sin(t * w * 0.63 - z / this.spatialScale + phase * 2.1);
    const shear = 1 + this.heightShear * Math.log1p(Math.max(0, y) / 10);
    target.copy(this.velocity).multiplyScalar(shear);
    const speed = Math.hypot(this.velocity.x, this.velocity.z);
    const dx = speed > 1e-9 ? this.velocity.x / speed : 1;
    const dz = speed > 1e-9 ? this.velocity.z / speed : 0;
    target.x += this.gustStrength * (dx * a - dz * b * 0.25);
    target.z += this.gustStrength * (dz * a + dx * b * 0.25);
    return target;
  }

  update(dt) {
    if (!Number.isFinite(dt) || dt < 0) throw new RangeError('Wind dt must be finite and non-negative');
    if (dt === 0) return this;
    const previous = this.sample(null, this.elapsedSeconds);
    this.velocity.lerp(this.targetVelocity, 1 - Math.exp(-dt / this.response));
    this.elapsedSeconds += dt;
    const current = this.sample(null, this.elapsedSeconds);
    this.displacement.addScaledVector(previous.add(current), dt * 0.5);
    return this;
  }

  getState() {
    return { velocity: this.velocity.toArray(), targetVelocity: this.targetVelocity.toArray(),
      gustStrength: this.gustStrength, gustPeriod: this.gustPeriod, spatialScale: this.spatialScale,
      elapsedSeconds: this.elapsedSeconds, displacement: this.displacement.toArray(), seed: this.seed };
  }
}

/** The object origin must be at its root; rotation bends the plant and its shadow together. */
export function bindVegetationWind(object, host, options = {}) {
  if (!object?.isObject3D || !host?.wind || !host?.onTick) throw new TypeError('Vegetation needs an Object3D and wind-enabled host');
  const rest = object.quaternion.clone();
  const position = new THREE.Vector3(), wind = new THREE.Vector3();
  const axis = new THREE.Vector3(), bend = new THREE.Quaternion(), target = new THREE.Quaternion();
  const parentRotation = new THREE.Quaternion();
  const flexibility = number(options.flexibility, 0.035);
  const maxBend = number(options.maxBend, 0.25);
  const response = number(options.response, 4, 0.01);
  const phase = number(options.phase, 0, -Number.MAX_SAFE_INTEGER);
  let disposed = false;
  const unsubscribe = host.onTick((dt) => {
    object.getWorldPosition(position);
    host.wind.sample(position, host.wind.elapsedSeconds, wind);
    if (object.parent) {
      object.parent.getWorldQuaternion(parentRotation).invert();
      wind.applyQuaternion(parentRotation);
    }
    const speed = Math.hypot(wind.x, wind.z);
    axis.set(wind.z, 0, -wind.x);
    if (speed > 1e-8) axis.divideScalar(speed); else axis.set(1, 0, 0);
    const flutter = 1 + 0.12 * Math.sin(host.wind.elapsedSeconds * 3 + phase);
    bend.setFromAxisAngle(axis, Math.min(maxBend, speed * flexibility) * flutter);
    target.copy(bend).multiply(rest);
    object.quaternion.slerp(target, 1 - Math.exp(-response * dt));
  });
  const previousDispose = object.userData.dispose;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    unsubscribe();
    object.quaternion.copy(rest);
    if (object.userData.dispose === combined) object.userData.dispose = previousDispose;
  };
  const combined = () => { dispose(); previousDispose?.(); };
  object.userData.dispose = combined;
  return dispose;
}
