import * as THREE from 'three';

const finite = (value, fallback) => Number.isFinite(Number(value)) ? Number(value) : fallback;
const vector = (value, fallback = 0) => new THREE.Vector3(
  finite(value?.[0] ?? value?.x, fallback),
  finite(value?.[1] ?? value?.y, fallback),
  finite(value?.[2] ?? value?.z, fallback),
);

/**
 * Lightweight box-volume flotation in metres, kilograms and seconds.
 * The object origin is its centre of mass; size is its local box size.
 * Integrates gravity, sampled buoyancy and box inertia, not a full rigid-body
 * solver: no body/body collisions, hull geometry, breaking waves or fluid solve.
 */
export class A3GameWaterBody {
  constructor({ water, object, mass = 500, volume, size = [1, 1, 1], velocity, onEnterWater,
    density = 1000, gravity = 9.81, damping, angularDamping = 1.5, groundFriction = 4, fixedStep = 1 / 120 } = {}) {
    this.water = water?.userData?.computeBuoyancy ? water.userData : water;
    if (!this.water?.computeBuoyancy || !this.water?.sampleHeight || !object?.isObject3D) {
      throw new TypeError('A3GameWaterBody requires a water surface and THREE.Object3D');
    }
    this.object = object;
    this.mass = Math.max(0.001, finite(mass, 500));
    this.size = vector(typeof size === 'number' ? [size, size, size] : size, 1).max(new THREE.Vector3(0.001, 0.001, 0.001));
    this.volume = Math.max(0, finite(volume, this.size.x * this.size.y * this.size.z));
    this.velocity = vector(velocity);
    this.angularVelocity = new THREE.Vector3();
    this.density = Math.max(0, finite(density, 1000));
    this.gravity = Math.max(0, finite(gravity, 9.81));
    this.damping = Math.max(0, finite(damping, this.mass * 4));
    this.angularDamping = Math.max(0, finite(angularDamping, 1.5));
    this.groundFriction = Math.max(0, finite(groundFriction, 4));
    this.fixedStep = THREE.MathUtils.clamp(finite(fixedStep, 1 / 120), 1 / 240, 1 / 30);
    this.onEnterWater = onEnterWater;
    this.elapsedSeconds = 0;
    this.submergedFraction = 0;
    this.grounded = false;
    this.disposed = false;
    this.force = new THREE.Vector3();
    this.torque = new THREE.Vector3();
    this._accumulator = 0;
    this._unsubscribe = null;
    this._center = new THREE.Vector3();
    this._rotation = new THREE.Quaternion();
    this._scale = new THREE.Vector3();
    this._points = Array.from({ length: 4 }, () => new THREE.Vector3());
    this._samplePoints();
    this._inWater = this._wetPoint() !== null;
  }

  _samplePoints() {
    this.object.updateWorldMatrix(true, false);
    this.object.matrixWorld.decompose(this._center, this._rotation, this._scale);
    for (let i = 0; i < 4; i += 1) {
      this._points[i].set((i % 2 ? 1 : -1) * this.size.x * 0.45, -this.size.y / 2,
        (i < 2 ? -1 : 1) * this.size.z * 0.45).applyMatrix4(this.object.matrixWorld);
    }
    this._draft = Math.max(0.001, this.size.y * Math.abs(this._scale.y));
  }

  _wetPoint() {
    return this._points.find(point => {
      const height = this.water.sampleHeight(point.x, point.z);
      const bottom = this.water.sampleBottom?.(point.x, point.z)
        ?? (height === null ? null : height - (this.water.sampleDepth?.(point.x, point.z) ?? 0));
      return Number.isFinite(height) && Number.isFinite(bottom)
        && Math.min(point.y + this._draft, height) > Math.max(point.y, bottom) + 1e-6;
    }) ?? null;
  }

  _writeTransform() {
    if (this.object.parent) {
      this.object.parent.updateWorldMatrix(true, false);
      this.object.position.copy(this.object.parent.worldToLocal(this._center.clone()));
      const parentRotation = this.object.parent.getWorldQuaternion(new THREE.Quaternion());
      this.object.quaternion.copy(parentRotation.invert().multiply(this._rotation));
    } else {
      this.object.position.copy(this._center);
      this.object.quaternion.copy(this._rotation);
    }
    this.object.updateWorldMatrix(false, false);
  }

  _step(dt) {
    this._samplePoints();
    const result = this.water.computeBuoyancy({
      points: this._points, centerOfMass: this._center, velocity: this.velocity,
      angularVelocity: this.angularVelocity, volume: this.volume, draft: this._draft,
      density: this.density, gravity: this.gravity, damping: Math.min(this.damping, this.mass / dt),
    });
    this.submergedFraction = result.submergedFraction;
    this.force.copy(result.force);
    this.force.y -= this.mass * this.gravity;
    this.torque.copy(result.torque);
    this.velocity.addScaledVector(this.force, dt / this.mass);
    this._center.addScaledVector(this.velocity, dt);
    const dimensions = this.size.clone().multiply(this._scale);
    const inertia = new THREE.Vector3(
      dimensions.y ** 2 + dimensions.z ** 2,
      dimensions.x ** 2 + dimensions.z ** 2,
      dimensions.x ** 2 + dimensions.y ** 2,
    ).multiplyScalar(this.mass / 12).max(new THREE.Vector3(1e-6, 1e-6, 1e-6));
    const acceleration = this.torque.clone().applyQuaternion(this._rotation.clone().invert())
      .divide(inertia).applyQuaternion(this._rotation);
    this.angularVelocity.addScaledVector(acceleration, dt)
      .multiplyScalar(Math.exp(-this.angularDamping * this.submergedFraction * dt));
    const speed = this.angularVelocity.length();
    if (speed > 1e-9) {
      this._rotation.premultiply(new THREE.Quaternion().setFromAxisAngle(
        this.angularVelocity.clone().divideScalar(speed), speed * dt,
      )).normalize();
    }
    this._writeTransform();
    let penetration = 0;
    const corner = new THREE.Vector3();
    for (let i = 0; i < 8; i += 1) {
      corner.set((i & 1 ? 1 : -1) * this.size.x / 2, (i & 2 ? 1 : -1) * this.size.y / 2,
        (i & 4 ? 1 : -1) * this.size.z / 2).applyMatrix4(this.object.matrixWorld);
      const bottom = this.water.sampleBottom?.(corner.x, corner.z);
      if (Number.isFinite(bottom)) penetration = Math.max(penetration, bottom - corner.y);
    }
    this.grounded = penetration > 0;
    if (this.grounded) {
      this._center.y += penetration;
      this.velocity.y = Math.max(0, this.velocity.y);
      const friction = Math.exp(-this.groundFriction * dt);
      this.velocity.x *= friction;
      this.velocity.z *= friction;
      this.angularVelocity.multiplyScalar(Math.exp(-12 * dt));
      this._writeTransform();
    }
    this._samplePoints();
    const entering = this._wetPoint();
    this.elapsedSeconds += dt;
    if (entering && !this._inWater) {
      const position = entering.clone();
      position.y = this.water.sampleHeight(position.x, position.z);
      const waterVelocity = this.water.sampleVelocity?.(position) ?? new THREE.Vector3();
      const impactSpeed = this.velocity.clone().sub(waterVelocity).length();
      this.water.addRipple?.(position.x, position.z, Math.min(0.5, 0.03 + impactSpeed * 0.035),
        Math.max(0.1, Math.min(this.size.x, this.size.z) * 0.5));
      this.onEnterWater?.({ body: this, object: this.object, position, velocity: this.velocity.clone(), impactSpeed });
    }
    this._inWater = Boolean(entering);
  }

  /** Fixed substeps, capped at 0.25 seconds per call to bound catch-up work. */
  update(dt) {
    if (this.disposed) return this;
    if (this.water.getState?.().disposed) { this.dispose(); return this; }
    this._accumulator += THREE.MathUtils.clamp(finite(dt, 0), 0, 0.25);
    while (this._accumulator + 1e-10 >= this.fixedStep) {
      this._accumulator = Math.max(0, this._accumulator - this.fixedStep);
      this._step(this.fixedStep);
    }
    return this;
  }

  attachToHost(host) {
    if (this.disposed) throw new Error('Cannot attach disposed water body');
    if (typeof host?.onTick !== 'function') throw new TypeError('Water body attachToHost needs host.onTick');
    this._unsubscribe?.();
    const detach = host.onTick(dt => this.update(dt));
    let attached = true;
    this._unsubscribe = () => { if (attached) { attached = false; detach?.(); } };
    return this._unsubscribe;
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this._unsubscribe?.();
    this._unsubscribe = null;
  }
}
