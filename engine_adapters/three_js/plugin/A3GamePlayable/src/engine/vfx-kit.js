/**
 * Particles and beams: the visual effects three.js does not ship.
 *
 * three.js has no particle system. `THREE.Points` is a draw call and a
 * point size, and `Sprite` is one quad per object — neither simulates
 * anything, neither pools anything, and a game that reaches for them
 * directly ends up with one `Sprite` per spark, a `requestAnimationFrame`
 * of its own, and nothing disposed.
 *
 * The design follows the two libraries that solved this for three.js
 * already, without taking either as a dependency:
 *
 *   - **three.quarks** contributes the shape: a *batched* renderer that
 *     owns many systems, a system described declaratively (life, speed,
 *     shape, behaviours over lifetime), and `RenderMode` — billboard,
 *     stretched billboard, mesh;
 *   - **Three-VFX** contributes the parameter vocabulary a designer
 *     actually types: `size`, `colorStart`/`colorEnd`, `fadeSize`,
 *     `fadeOpacity`, `emitterShape`, `emitterRadius`,
 *     `startPositionAsDirection`, `turbulence`, `friction`,
 *     `stretchBySpeed`, `blending`, `appearance`, `intensity`.
 *
 * Why it is re-implemented rather than installed: a generated game is a
 * self-contained Vite project that has to build offline, three.quarks
 * ships its own `three` peer range, and Three-VFX targets the WebGPU
 * renderer with a React binding. The part that matters — pooled CPU
 * simulation feeding one instanced draw call — is a few hundred lines,
 * and keeping it inside `@a3game/playable` means a generated game gets
 * effects from the same import surface as everything else.
 *
 * Three properties are non-negotiable here and are what most hand-rolled
 * particle code gets wrong:
 *
 *   1. **One draw call per system.** A hundred sparks are a hundred
 *      instances of one quad, not a hundred objects.
 *   2. **A fixed pool.** Emission never allocates: it reuses the oldest
 *      slot. A game cannot leak GPU memory through its own hit sparks.
 *   3. **No canvas, no document, no GPU at construction time.** The
 *      shape mask is computed in the fragment shader instead of being
 *      drawn into a `<canvas>`, so every class here can be imported and
 *      stepped by a headless `vitest` test — which is the only kind of
 *      test a generated game gets.
 */

import * as THREE from 'three';

/** Where a particle is born inside the emitter. */
export const A3GameEmitterShape = Object.freeze({
  POINT: 'point',
  BOX: 'box',
  SPHERE: 'sphere',
  CONE: 'cone',
  DISK: 'disk',
  EDGE: 'edge',
});

/** How a particle quad combines with what is already in the frame. */
export const A3GameParticleBlending = Object.freeze({
  /** Ordinary alpha blending. Smoke, dust, debris. */
  NORMAL: 'normal',
  /** Light adds to light. Fire, muzzle flash, magic, tracers. */
  ADDITIVE: 'additive',
  /** Darkens. Rarely wanted, included for completeness. */
  MULTIPLY: 'multiply',
});

/** The alpha mask applied inside the quad. */
export const A3GameParticleAppearance = Object.freeze({
  /** The whole quad. Debris and flipbook textures. */
  DEFAULT: 'default',
  /** Soft radial falloff. The default, and what reads as "glow". */
  GRADIENT: 'gradient',
  /** Hard-edged disc. Bullet holes, dots. */
  CIRCULAR: 'circular',
  /** Hollow ring. Shockwaves, impact rings. */
  RING: 'ring',
});

/** What geometry each particle is drawn with. */
export const A3GameParticleRenderMode = Object.freeze({
  /** Camera-facing quad. */
  BILLBOARD: 'billboard',
  /** Camera-facing quad stretched along its screen-space velocity. */
  STRETCHED: 'stretched',
  /** An arbitrary `BufferGeometry`, optionally aligned to velocity. */
  MESH: 'mesh',
});

const APPEARANCE_CODES = Object.freeze({
  default: 0,
  gradient: 1,
  circular: 2,
  ring: 3,
});

const BLENDING_MODES = Object.freeze({
  normal: THREE.NormalBlending,
  additive: THREE.AdditiveBlending,
  multiply: THREE.MultiplyBlending,
});

const PARTICLE_VERTEX_SHADER = /* glsl */ `
attribute vec3 aOffset;
attribute vec3 aColor;
attribute vec4 aParams;

varying vec3 vColor;
varying float vAlpha;
varying vec2 vParticleUv;

void main() {
  vColor = aColor;
  vAlpha = aParams.z;
  vParticleUv = uv;

  vec4 viewPosition = modelViewMatrix * vec4(aOffset, 1.0);
  float size = aParams.x;
  float angle = aParams.y;
  float stretch = aParams.w;

  // Billboarding happens in view space, so the quad faces the camera
  // whatever the emitter's own transform is doing.
  vec2 corner = position.xy * size;
  corner.y *= stretch;
  float c = cos(angle);
  float s = sin(angle);
  corner = vec2(corner.x * c - corner.y * s, corner.x * s + corner.y * c);
  viewPosition.xy += corner;

  gl_Position = projectionMatrix * viewPosition;
}
`;

const PARTICLE_FRAGMENT_SHADER = /* glsl */ `
uniform float uIntensity;
uniform int uAppearance;
#ifdef A3_USE_MAP
uniform sampler2D uMap;
#endif

varying vec3 vColor;
varying float vAlpha;
varying vec2 vParticleUv;

void main() {
  float radius = length(vParticleUv - 0.5) * 2.0;
  float mask = 1.0;
  if (uAppearance == 1) {
    mask = pow(clamp(1.0 - radius, 0.0, 1.0), 1.6);
  } else if (uAppearance == 2) {
    mask = 1.0 - smoothstep(0.9, 1.0, radius);
  } else if (uAppearance == 3) {
    mask = (1.0 - smoothstep(0.9, 1.0, radius)) *
           smoothstep(0.42, 0.64, radius);
  }

  vec4 sampled = vec4(1.0);
  #ifdef A3_USE_MAP
  sampled = texture2D(uMap, vParticleUv);
  #endif

  float alpha = vAlpha * mask * sampled.a;
  if (alpha < 0.003) discard;
  gl_FragColor = vec4(vColor * sampled.rgb * uIntensity, alpha);

  // A raw ShaderMaterial is outside the renderer's automatic output
  // pipeline, so it has to run tone mapping and the output colour space
  // itself. Without these two chunks an additive spark is the only thing
  // in the frame that ignores the game's exposure setting.
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}
`;

/** Read a `number | [min, max]` parameter as a range. */
function readRange(value, fallbackMin, fallbackMax) {
  if (Array.isArray(value)) {
    return [Number(value[0] ?? fallbackMin), Number(value[1] ?? fallbackMax)];
  }
  if (value === undefined || value === null) {
    return [Number(fallbackMin), Number(fallbackMax)];
  }
  const single = Number(value);
  return [single, single];
}

/** Read a per-axis range, as Three-VFX writes `direction`. */
function readAxisRanges(value, fallback) {
  const source = Array.isArray(value) && value.length === 3 ? value : fallback;
  return [
    readRange(source[0], -1, 1),
    readRange(source[1], -1, 1),
    readRange(source[2], -1, 1),
  ];
}

function readVector(value, fallback = [0, 0, 0]) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return new THREE.Vector3(
      Number(value.x ?? 0),
      Number(value.y ?? 0),
      Number(value.z ?? 0),
    );
  }
  const source = Array.isArray(value) ? value : fallback;
  return new THREE.Vector3(
    Number(source[0] ?? 0),
    Number(source[1] ?? 0),
    Number(source[2] ?? 0),
  );
}

function readColorList(value, fallback) {
  const list = Array.isArray(value) ? value : value ? [value] : fallback;
  return list.map((entry) => new THREE.Color(entry));
}

/**
 * A deterministic sequence, because an effect that looks different in a
 * test run than it does in the game cannot be asserted at all.
 */
function createRandom(seed) {
  let state = Number(seed) >>> 0 || 0x9e3779b9;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let value = Math.imul(state ^ (state >>> 15), 1 | state);
    value = (value + Math.imul(value ^ (value >>> 7), 61 | value)) ^ value;
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

/** Cheap divergence-free-ish flow field. Curl noise without the cost. */
function turbulence(target, x, y, z, time, frequency) {
  const fx = x * frequency;
  const fy = y * frequency;
  const fz = z * frequency;
  target.set(
    Math.sin(fy + time * 1.3) + Math.cos(fz * 1.7 - time),
    Math.sin(fz + time * 1.1) + Math.cos(fx * 1.9 + time * 0.7),
    Math.sin(fx + time * 0.9) + Math.cos(fy * 1.5 - time * 1.2),
  );
  return target;
}

/**
 * One pooled, self-simulating particle effect drawn in a single call.
 *
 * The simulation runs on the CPU. That is a deliberate trade: a GPU
 * compute pass needs WebGPU, cannot be stepped by a headless test, and
 * cannot answer "did this effect actually emit" without a read-back —
 * while a few thousand particles of `p += v·dt` cost microseconds and
 * stay fully observable. `getState()` is what a generated test asserts
 * instead of taking a screenshot.
 */
export class A3GameParticleSystem {
  /**
   * @param {{maxParticles?: number, lifetime?: number | number[],
   *          size?: number | number[], sizeOverLife?: number[],
   *          opacityOverLife?: number[],
   *          colorStart?: string | string[], colorEnd?: string | string[],
   *          intensity?: number, appearance?: string, blending?: string,
   *          map?: THREE.Texture | null,
   *          speed?: number | number[], direction?: Array<number | number[]>,
   *          gravity?: number[], drag?: number,
   *          turbulence?: {intensity?: number, frequency?: number,
   *                        speed?: number} | null,
   *          emitterShape?: string, emitterRadius?: number[],
   *          emitterAngle?: number, emitterHeight?: number[],
   *          emitterDirection?: number[], emitterSize?: number[],
   *          surfaceOnly?: boolean, startPositionAsDirection?: boolean,
   *          rotation?: number | number[],
   *          rotationSpeed?: number | number[],
   *          stretchBySpeed?: {factor?: number, maxStretch?: number} | null,
   *          renderMode?: string, geometry?: THREE.BufferGeometry | null,
   *          material?: THREE.Material | null,
   *          orientToDirection?: boolean,
   *          emissionOverTime?: number, looping?: boolean,
   *          autoStart?: boolean, depthTest?: boolean,
   *          renderOrder?: number, name?: string, seed?: number}} [config]
   */
  constructor(config = {}) {
    const maxParticles = Math.max(
      1,
      Math.trunc(Number(config.maxParticles ?? 200)),
    );
    this.config = { ...config };
    this.name = String(config.name ?? 'a3game_particles');
    this.capacity = maxParticles;

    this.lifetime = readRange(config.lifetime, 0.6, 1.2);
    this.sizeRange = readRange(config.size, 0.1, 0.3);
    this.sizeOverLife = readRange(config.sizeOverLife, 1, 0);
    this.opacityOverLife = readRange(config.opacityOverLife, 1, 0);
    this.colorStart = readColorList(config.colorStart, ['#ffffff']);
    this.colorEnd = config.colorEnd ? readColorList(config.colorEnd, []) : null;
    this.speedRange = readRange(config.speed, 1, 2);
    this.directionRanges = readAxisRanges(config.direction, [
      [-1, 1],
      [-1, 1],
      [-1, 1],
    ]);
    this.gravity = readVector(config.gravity, [0, 0, 0]);
    this.drag = Math.max(0, Number(config.drag ?? 0));
    this.windResponse = Math.max(0, Number(config.windResponse ?? 0));
    this.windField = config.windField ?? null;
    this.unsubscribeWind = null;
    this.disposed = false;
    this.turbulence = config.turbulence
      ? {
          intensity: Number(config.turbulence.intensity ?? 0),
          frequency: Number(config.turbulence.frequency ?? 1),
          speed: Number(config.turbulence.speed ?? 1),
        }
      : null;

    this.emitterShape = String(
      config.emitterShape ?? A3GameEmitterShape.POINT,
    ).toLowerCase();
    this.emitterRadius = readRange(config.emitterRadius, 0, 0.2);
    this.emitterAngle = Number(config.emitterAngle ?? Math.PI / 4);
    this.emitterHeight = readRange(config.emitterHeight, 0, 0);
    this.emitterDirection = readVector(
      config.emitterDirection,
      [0, 1, 0],
    ).normalize();
    this.emitterSize = readVector(config.emitterSize, [0.2, 0.2, 0.2]);
    this.surfaceOnly = Boolean(config.surfaceOnly);
    this.startPositionAsDirection = Boolean(config.startPositionAsDirection);

    this.rotationRange = readRange(config.rotation, 0, Math.PI * 2);
    this.rotationSpeedRange = readRange(config.rotationSpeed, -1, 1);
    this.stretchBySpeed = config.stretchBySpeed
      ? {
          factor: Number(config.stretchBySpeed.factor ?? 0.1),
          maxStretch: Number(config.stretchBySpeed.maxStretch ?? 4),
        }
      : null;

    this.emissionOverTime = Math.max(0, Number(config.emissionOverTime ?? 0));
    this.looping = config.looping !== false;
    this.emitting = Boolean(config.autoStart) && this.emissionOverTime > 0;
    this.emissionCarry = 0;
    /**
     * Where continuous emission happens, in world space.
     *
     * A trail follows its object by moving *this* every frame, not by
     * parenting the particles: a parented particle would be dragged along
     * after it was emitted, and the streak would never form behind the
     * projectile.
     */
    this.emitterPosition = readVector(config.position, [0, 0, 0]);
    this.elapsedSeconds = 0;
    this.totalEmitted = 0;

    this.renderMode = String(
      config.renderMode ??
        (config.geometry
          ? A3GameParticleRenderMode.MESH
          : config.stretchBySpeed
            ? A3GameParticleRenderMode.STRETCHED
            : A3GameParticleRenderMode.BILLBOARD),
    ).toLowerCase();
    this.orientToDirection = config.orientToDirection !== false;

    this.random = createRandom(config.seed ?? 0x51ff);

    // Structure-of-arrays, not an array of objects: the update loop
    // touches every field of every live particle each frame, and this is
    // the layout that does not allocate while doing it.
    this.positions = new Float32Array(maxParticles * 3);
    this.velocities = new Float32Array(maxParticles * 3);
    this.ages = new Float32Array(maxParticles);
    this.lifespans = new Float32Array(maxParticles);
    this.sizes = new Float32Array(maxParticles);
    this.rotations = new Float32Array(maxParticles);
    this.rotationSpeeds = new Float32Array(maxParticles);
    this.startColors = new Float32Array(maxParticles * 3);
    this.endColors = new Float32Array(maxParticles * 3);
    this.alive = new Uint8Array(maxParticles);
    this.cursor = 0;
    this.activeCount = 0;

    this.#scratch = {
      vector: new THREE.Vector3(),
      flow: new THREE.Vector3(),
      view: new THREE.Vector3(),
      color: new THREE.Color(),
      matrix: new THREE.Matrix4(),
      quaternion: new THREE.Quaternion(),
      scale: new THREE.Vector3(),
      up: new THREE.Vector3(0, 1, 0),
    };

    if (this.renderMode === A3GameParticleRenderMode.MESH) {
      this.#buildMeshBatch(config);
    } else {
      this.#buildSpriteBatch(config);
    }
    this.object3D.name = this.name;
    this.object3D.renderOrder = Number(config.renderOrder ?? 0);
    // The bounding volume of a particle system is meaningless: particles
    // leave the emitter, and a frustum test against the emitter's own
    // point culls the whole effect the instant the emitter goes offscreen.
    this.object3D.frustumCulled = false;
  }

  #scratch;

  /** Begin continuous emission (`emissionOverTime` particles a second). */
  start() {
    this.emitting = this.emissionOverTime > 0;
    return this;
  }

  /** Stop continuous emission. Live particles finish their lives. */
  stop() {
    this.emitting = false;
    return this;
  }

  /**
   * Spawn particles now.
   *
   * Every spawn parameter can be overridden per call, which is what makes
   * one registered system serve every impact in a game: the same
   * `bullet_impact` emits at a different point, along a different normal,
   * with a different colour, without a second system being built.
   *
   * @param {number} [count]
   * @param {{position?: THREE.Vector3 | number[],
   *          direction?: THREE.Vector3 | number[],
   *          spread?: number, speed?: number | number[],
   *          size?: number | number[], lifetime?: number | number[],
   *          colorStart?: string | string[], colorEnd?: string | string[],
   *          scale?: number}} [options]
   * @returns {number} how many particles were actually spawned
   */
  emit(count = 1, options = {}) {
    const requested = Math.max(0, Math.trunc(Number(count) || 0));
    if (requested === 0) return 0;

    const origin = options.position
      ? readVector(
          Array.isArray(options.position)
            ? options.position
            : [options.position.x, options.position.y, options.position.z],
        )
      : null;
    const axis = options.direction
      ? readVector(
          Array.isArray(options.direction)
            ? options.direction
            : [
                options.direction.x,
                options.direction.y,
                options.direction.z,
              ],
        ).normalize()
      : null;
    const spread = Number(options.spread ?? 0);
    const scale = Number(options.scale ?? 1);
    const speedRange = options.speed
      ? readRange(options.speed, this.speedRange[0], this.speedRange[1])
      : this.speedRange;
    const sizeRange = options.size
      ? readRange(options.size, this.sizeRange[0], this.sizeRange[1])
      : this.sizeRange;
    const lifeRange = options.lifetime
      ? readRange(options.lifetime, this.lifetime[0], this.lifetime[1])
      : this.lifetime;
    const startColors = options.colorStart
      ? readColorList(options.colorStart, ['#ffffff'])
      : this.colorStart;
    const endColors = options.colorEnd
      ? readColorList(options.colorEnd, [])
      : this.colorEnd;

    let spawned = 0;
    for (let index = 0; index < requested; index += 1) {
      const slot = this.#claimSlot();
      if (slot < 0) break;
      this.#spawnInto(slot, {
        origin,
        axis,
        spread,
        scale,
        speedRange,
        sizeRange,
        lifeRange,
        startColors,
        endColors,
      });
      spawned += 1;
    }
    this.totalEmitted += spawned;
    return spawned;
  }

  /** `emit` with a default count, kept for readability at call sites. */
  burst(count, options = {}) {
    return this.emit(count ?? this.capacity, options);
  }

  /** Kill every live particle without releasing GPU resources. */
  clear() {
    this.alive.fill(0);
    this.activeCount = 0;
    this.#syncBuffers(null);
    return this;
  }

  /**
   * Advance the simulation and upload the frame.
   *
   * @param {number} deltaSeconds
   * @param {THREE.Camera} [camera] required only for velocity stretching
   */
  update(deltaSeconds, camera = null) {
    const delta = Math.max(0, Number(deltaSeconds) || 0);
    if (delta === 0) {
      this.#syncBuffers(camera);
      return this;
    }
    this.elapsedSeconds += delta;

    if (this.emitting) {
      this.emissionCarry += this.emissionOverTime * delta;
      const whole = Math.floor(this.emissionCarry);
      if (whole > 0) {
        this.emissionCarry -= whole;
        this.emit(whole, { position: this.emitterPosition });
      }
      if (!this.looping) this.emitting = false;
    }

    const damping = this.drag > 0 ? Math.exp(-this.drag * delta) : 1;
    const flow = this.#scratch.flow;
    let active = 0;
    for (let index = 0; index < this.capacity; index += 1) {
      if (!this.alive[index]) continue;
      const age = this.ages[index] + delta;
      if (age >= this.lifespans[index]) {
        this.alive[index] = 0;
        continue;
      }
      this.ages[index] = age;
      const base = index * 3;

      if (this.turbulence && this.turbulence.intensity !== 0) {
        turbulence(
          flow,
          this.positions[base],
          this.positions[base + 1],
          this.positions[base + 2],
          this.elapsedSeconds * this.turbulence.speed,
          this.turbulence.frequency,
        );
        const strength = this.turbulence.intensity * delta;
        this.velocities[base] += flow.x * strength;
        this.velocities[base + 1] += flow.y * strength;
        this.velocities[base + 2] += flow.z * strength;
      }

      this.velocities[base] =
        (this.velocities[base] + this.gravity.x * delta) * damping;
      this.velocities[base + 1] =
        (this.velocities[base + 1] + this.gravity.y * delta) * damping;
      this.velocities[base + 2] =
        (this.velocities[base + 2] + this.gravity.z * delta) * damping;

      if (this.windField && this.windResponse > 0) {
        this.#scratch.vector.set(this.positions[base], this.positions[base + 1], this.positions[base + 2]);
        this.windField.sample(this.#scratch.vector, this.windField.elapsedSeconds, flow);
        const follow = 1 - Math.exp(-this.windResponse * delta);
        this.velocities[base] += (flow.x - this.velocities[base]) * follow;
        this.velocities[base + 1] += (flow.y - this.velocities[base + 1]) * follow;
        this.velocities[base + 2] += (flow.z - this.velocities[base + 2]) * follow;
      }
      this.positions[base] += this.velocities[base] * delta;
      this.positions[base + 1] += this.velocities[base + 1] * delta;
      this.positions[base + 2] += this.velocities[base + 2] * delta;
      this.rotations[index] += this.rotationSpeeds[index] * delta;
      active += 1;
    }
    this.activeCount = active;
    this.#syncBuffers(camera);
    return this;
  }

  /** Bind the system to a host tick, including the camera it needs. */
  attachToHost(host) {
    this.unsubscribeWind?.();
    this.windField = host.wind ?? this.windField;
    this.unsubscribeWind = host.onTick((delta) => this.update(delta, host.camera));
    return this.unsubscribeWind;
  }

  /** @returns {object} observable state, which is what tests assert. */
  getState() {
    return {
      name: this.name,
      renderMode: this.renderMode,
      capacity: this.capacity,
      activeCount: this.activeCount,
      totalEmitted: this.totalEmitted,
      emitting: this.emitting,
      elapsedSeconds: Number(this.elapsedSeconds.toFixed(3)),
    };
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.unsubscribeWind?.();
    this.unsubscribeWind = null;
    this.object3D.parent?.remove(this.object3D);
    this.geometry?.dispose?.();
    this.material?.dispose?.();
    this.alive.fill(0);
    this.activeCount = 0;
  }

  // --- internals ----------------------------------------------------

  #claimSlot() {
    for (let attempt = 0; attempt < this.capacity; attempt += 1) {
      const index = this.cursor;
      this.cursor = (this.cursor + 1) % this.capacity;
      if (!this.alive[index]) return index;
    }
    // Every slot is live. Overwriting the oldest is the only behaviour
    // that keeps a busy effect responsive; growing the pool would let a
    // firefight allocate without bound.
    const index = this.cursor;
    this.cursor = (this.cursor + 1) % this.capacity;
    return index;
  }

  #spawnInto(slot, context) {
    const random = this.random;
    const base = slot * 3;
    const scale = context.scale;
    const wasAlive = this.alive[slot] === 1;

    const local = this.#sampleEmitterShape(this.#scratch.vector, random);
    const originX = context.origin ? context.origin.x : 0;
    const originY = context.origin ? context.origin.y : 0;
    const originZ = context.origin ? context.origin.z : 0;
    this.positions[base] = originX + local.x * scale;
    this.positions[base + 1] = originY + local.y * scale;
    this.positions[base + 2] = originZ + local.z * scale;

    let dx;
    let dy;
    let dz;
    if (this.startPositionAsDirection && local.lengthSq() > 1e-8) {
      const inverse = 1 / local.length();
      dx = local.x * inverse;
      dy = local.y * inverse;
      dz = local.z * inverse;
    } else {
      dx = THREE.MathUtils.lerp(
        this.directionRanges[0][0],
        this.directionRanges[0][1],
        random(),
      );
      dy = THREE.MathUtils.lerp(
        this.directionRanges[1][0],
        this.directionRanges[1][1],
        random(),
      );
      dz = THREE.MathUtils.lerp(
        this.directionRanges[2][0],
        this.directionRanges[2][1],
        random(),
      );
    }
    if (context.axis) {
      // A caller-supplied axis replaces the configured cone and keeps its
      // spread: an impact effect has to point away from the surface it
      // hit, and that normal is only known at the moment of the hit.
      const spread = context.spread || 0.45;
      dx = context.axis.x + (random() - 0.5) * spread * 2;
      dy = context.axis.y + (random() - 0.5) * spread * 2;
      dz = context.axis.z + (random() - 0.5) * spread * 2;
    }
    const length = Math.hypot(dx, dy, dz) || 1;
    const speed =
      THREE.MathUtils.lerp(
        context.speedRange[0],
        context.speedRange[1],
        random(),
      ) * scale;
    this.velocities[base] = (dx / length) * speed;
    this.velocities[base + 1] = (dy / length) * speed;
    this.velocities[base + 2] = (dz / length) * speed;

    this.ages[slot] = 0;
    this.lifespans[slot] = Math.max(
      1e-3,
      THREE.MathUtils.lerp(
        context.lifeRange[0],
        context.lifeRange[1],
        random(),
      ),
    );
    this.sizes[slot] =
      THREE.MathUtils.lerp(
        context.sizeRange[0],
        context.sizeRange[1],
        random(),
      ) * scale;
    this.rotations[slot] = THREE.MathUtils.lerp(
      this.rotationRange[0],
      this.rotationRange[1],
      random(),
    );
    this.rotationSpeeds[slot] = THREE.MathUtils.lerp(
      this.rotationSpeedRange[0],
      this.rotationSpeedRange[1],
      random(),
    );

    const start =
      context.startColors[
        Math.min(
          context.startColors.length - 1,
          Math.floor(random() * context.startColors.length),
        )
      ];
    this.startColors[base] = start.r;
    this.startColors[base + 1] = start.g;
    this.startColors[base + 2] = start.b;
    const end =
      context.endColors && context.endColors.length > 0
        ? context.endColors[
            Math.min(
              context.endColors.length - 1,
              Math.floor(random() * context.endColors.length),
            )
          ]
        : start;
    this.endColors[base] = end.r;
    this.endColors[base + 1] = end.g;
    this.endColors[base + 2] = end.b;

    this.alive[slot] = 1;
    if (!wasAlive) this.activeCount += 1;
  }

  #sampleEmitterShape(target, random) {
    const [innerRadius, outerRadius] = this.emitterRadius;
    switch (this.emitterShape) {
      case A3GameEmitterShape.BOX:
        return target.set(
          (random() - 0.5) * this.emitterSize.x * 2,
          (random() - 0.5) * this.emitterSize.y * 2,
          (random() - 0.5) * this.emitterSize.z * 2,
        );
      case A3GameEmitterShape.SPHERE: {
        const theta = random() * Math.PI * 2;
        const phi = Math.acos(2 * random() - 1);
        const radius = this.surfaceOnly
          ? outerRadius
          : THREE.MathUtils.lerp(innerRadius, outerRadius, random());
        return target.set(
          Math.sin(phi) * Math.cos(theta) * radius,
          Math.cos(phi) * radius,
          Math.sin(phi) * Math.sin(theta) * radius,
        );
      }
      case A3GameEmitterShape.DISK: {
        const angle = random() * Math.PI * 2;
        const radius = this.surfaceOnly
          ? outerRadius
          : THREE.MathUtils.lerp(innerRadius, outerRadius, Math.sqrt(random()));
        target.set(Math.cos(angle) * radius, 0, Math.sin(angle) * radius);
        return this.#alignToEmitterDirection(target);
      }
      case A3GameEmitterShape.CONE: {
        const angle = random() * Math.PI * 2;
        const height = THREE.MathUtils.lerp(
          this.emitterHeight[0],
          this.emitterHeight[1],
          random(),
        );
        const radius =
          THREE.MathUtils.lerp(innerRadius, outerRadius, random()) +
          Math.tan(this.emitterAngle) * height;
        target.set(Math.cos(angle) * radius, height, Math.sin(angle) * radius);
        return this.#alignToEmitterDirection(target);
      }
      case A3GameEmitterShape.EDGE: {
        const t = random() - 0.5;
        return target.set(
          this.emitterSize.x * 2 * t,
          this.emitterSize.y * 2 * t,
          this.emitterSize.z * 2 * t,
        );
      }
      default:
        return target.set(0, 0, 0);
    }
  }

  /** Rotate a +Y-authored shape onto the configured emitter normal. */
  #alignToEmitterDirection(target) {
    const direction = this.emitterDirection;
    if (Math.abs(direction.y - 1) < 1e-6) return target;
    const quaternion = this.#scratch.quaternion.setFromUnitVectors(
      this.#scratch.up,
      direction,
    );
    return target.applyQuaternion(quaternion);
  }

  #buildSpriteBatch(config) {
    const geometry = new THREE.InstancedBufferGeometry();
    const quad = new THREE.PlaneGeometry(1, 1);
    geometry.index = quad.index;
    geometry.setAttribute('position', quad.getAttribute('position'));
    geometry.setAttribute('uv', quad.getAttribute('uv'));
    quad.dispose();

    this.attributeOffset = new THREE.InstancedBufferAttribute(
      new Float32Array(this.capacity * 3),
      3,
    );
    this.attributeColor = new THREE.InstancedBufferAttribute(
      new Float32Array(this.capacity * 3),
      3,
    );
    this.attributeParams = new THREE.InstancedBufferAttribute(
      new Float32Array(this.capacity * 4),
      4,
    );
    for (const attribute of [
      this.attributeOffset,
      this.attributeColor,
      this.attributeParams,
    ]) {
      attribute.setUsage(THREE.DynamicDrawUsage);
    }
    geometry.setAttribute('aOffset', this.attributeOffset);
    geometry.setAttribute('aColor', this.attributeColor);
    geometry.setAttribute('aParams', this.attributeParams);
    geometry.instanceCount = 0;

    const map = config.map ?? null;
    const material = new THREE.ShaderMaterial({
      vertexShader: PARTICLE_VERTEX_SHADER,
      fragmentShader: PARTICLE_FRAGMENT_SHADER,
      uniforms: {
        uIntensity: { value: Number(config.intensity ?? 1) },
        uAppearance: {
          value:
            APPEARANCE_CODES[
              String(
                config.appearance ?? A3GameParticleAppearance.GRADIENT,
              ).toLowerCase()
            ] ?? 1,
        },
        ...(map ? { uMap: { value: map } } : {}),
      },
      defines: map ? { A3_USE_MAP: '' } : {},
      transparent: true,
      // Depth *testing* stays on so a spark behind a wall is hidden;
      // depth *writing* stays off so overlapping particles do not carve
      // holes in each other.
      depthTest: config.depthTest !== false,
      depthWrite: false,
      blending:
        BLENDING_MODES[
          String(config.blending ?? A3GameParticleBlending.NORMAL).toLowerCase()
        ] ?? THREE.NormalBlending,
      side: THREE.DoubleSide,
      toneMapped: true,
    });

    this.geometry = geometry;
    this.material = material;
    this.object3D = new THREE.Mesh(geometry, material);
  }

  #buildMeshBatch(config) {
    const geometry = config.geometry ?? new THREE.TetrahedronGeometry(0.5);
    const material =
      config.material ??
      new THREE.MeshBasicMaterial({
        color: 0xffffff,
        transparent: true,
        depthWrite: false,
        blending:
          BLENDING_MODES[
            String(
              config.blending ?? A3GameParticleBlending.NORMAL,
            ).toLowerCase()
          ] ?? THREE.NormalBlending,
      });
    const mesh = new THREE.InstancedMesh(geometry, material, this.capacity);
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    mesh.count = 0;
    // `instanceColor` only exists once something writes it, and the
    // per-particle colour ramp needs it from the first frame.
    mesh.instanceColor = new THREE.InstancedBufferAttribute(
      new Float32Array(this.capacity * 3).fill(1),
      3,
    );
    mesh.instanceColor.setUsage(THREE.DynamicDrawUsage);
    this.geometry = geometry;
    this.material = material;
    this.object3D = mesh;
  }

  /** Copy the live particles into the GPU buffers, compacted. */
  #syncBuffers(camera) {
    return this.renderMode === A3GameParticleRenderMode.MESH
      ? this.#syncMeshBuffers()
      : this.#syncSpriteBuffers(camera);
  }

  #syncSpriteBuffers(camera) {
    const offsets = this.attributeOffset.array;
    const colors = this.attributeColor.array;
    const params = this.attributeParams.array;
    const stretching =
      this.stretchBySpeed !== null &&
      this.renderMode === A3GameParticleRenderMode.STRETCHED;
    const view = this.#scratch.view;
    let write = 0;

    for (let index = 0; index < this.capacity; index += 1) {
      if (!this.alive[index]) continue;
      const source = index * 3;
      const target = write * 3;
      const progress = Math.min(1, this.ages[index] / this.lifespans[index]);

      offsets[target] = this.positions[source];
      offsets[target + 1] = this.positions[source + 1];
      offsets[target + 2] = this.positions[source + 2];

      const mix = progress;
      colors[target] =
        this.startColors[source] +
        (this.endColors[source] - this.startColors[source]) * mix;
      colors[target + 1] =
        this.startColors[source + 1] +
        (this.endColors[source + 1] - this.startColors[source + 1]) * mix;
      colors[target + 2] =
        this.startColors[source + 2] +
        (this.endColors[source + 2] - this.startColors[source + 2]) * mix;

      const sizeFactor = THREE.MathUtils.lerp(
        this.sizeOverLife[0],
        this.sizeOverLife[1],
        progress,
      );
      const alpha = THREE.MathUtils.lerp(
        this.opacityOverLife[0],
        this.opacityOverLife[1],
        progress,
      );
      const slot = write * 4;
      params[slot] = Math.max(0, this.sizes[index] * sizeFactor);
      params[slot + 1] = this.rotations[index];
      params[slot + 2] = Math.max(0, alpha);
      params[slot + 3] = 1;

      if (stretching && camera) {
        // Align the quad's local +Y with the *screen-space* velocity,
        // which is the only direction a stretched billboard can honestly
        // follow: a world-space direction pointing at the camera would
        // stretch a spark that visually is not moving at all.
        view
          .set(
            this.velocities[source],
            this.velocities[source + 1],
            this.velocities[source + 2],
          )
          .transformDirection(camera.matrixWorldInverse);
        const planar = Math.hypot(view.x, view.y);
        if (planar > 1e-5) {
          params[slot + 1] = Math.atan2(-view.x, view.y);
          params[slot + 3] = Math.min(
            this.stretchBySpeed.maxStretch,
            1 + planar * this.stretchBySpeed.factor,
          );
        }
      }
      write += 1;
    }

    this.geometry.instanceCount = write;
    this.attributeOffset.addUpdateRange(0, write * 3);
    this.attributeColor.addUpdateRange(0, write * 3);
    this.attributeParams.addUpdateRange(0, write * 4);
    this.attributeOffset.needsUpdate = true;
    this.attributeColor.needsUpdate = true;
    this.attributeParams.needsUpdate = true;
    return this;
  }

  #syncMeshBuffers() {
    const mesh = this.object3D;
    const matrix = this.#scratch.matrix;
    const quaternion = this.#scratch.quaternion;
    const scale = this.#scratch.scale;
    const position = this.#scratch.vector;
    const velocity = this.#scratch.flow;
    const color = this.#scratch.color;
    let write = 0;

    for (let index = 0; index < this.capacity; index += 1) {
      if (!this.alive[index]) continue;
      const source = index * 3;
      const progress = Math.min(1, this.ages[index] / this.lifespans[index]);
      const sizeFactor = THREE.MathUtils.lerp(
        this.sizeOverLife[0],
        this.sizeOverLife[1],
        progress,
      );
      const alpha = THREE.MathUtils.lerp(
        this.opacityOverLife[0],
        this.opacityOverLife[1],
        progress,
      );
      position.set(
        this.positions[source],
        this.positions[source + 1],
        this.positions[source + 2],
      );
      if (this.orientToDirection) {
        velocity.set(
          this.velocities[source],
          this.velocities[source + 1],
          this.velocities[source + 2],
        );
        if (velocity.lengthSq() > 1e-8) {
          quaternion.setFromUnitVectors(
            this.#scratch.up,
            velocity.normalize(),
          );
        } else {
          quaternion.identity();
        }
      } else {
        quaternion.setFromAxisAngle(this.#scratch.up, this.rotations[index]);
      }
      const size = Math.max(0, this.sizes[index] * sizeFactor);
      scale.setScalar(size);
      matrix.compose(position, quaternion, scale);
      mesh.setMatrixAt(write, matrix);
      // A mesh particle has no per-instance alpha, so the colour ramp
      // carries the fade instead. Multiplying by alpha keeps an additive
      // mesh fading out and a normal-blended one darkening away.
      color.setRGB(
        (this.startColors[source] +
          (this.endColors[source] - this.startColors[source]) * progress) *
          alpha,
        (this.startColors[source + 1] +
          (this.endColors[source + 1] - this.startColors[source + 1]) *
            progress) *
          alpha,
        (this.startColors[source + 2] +
          (this.endColors[source + 2] - this.startColors[source + 2]) *
            progress) *
          alpha,
      );
      mesh.setColorAt(write, color);
      write += 1;
    }

    mesh.count = write;
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    return this;
  }
}

/**
 * A fading straight line, for tracers, laser sights, and hit beams.
 *
 * A tracer is not a particle effect: it exists for two frames, has a
 * definite start and end, and must not be simulated. A pool of lines is
 * both cheaper and more controllable than particles trying to describe a
 * straight segment.
 */
export class A3GameBeamEffect {
  /**
   * @param {{count?: number, color?: number | string, width?: number,
   *          lifetime?: number, blending?: string,
   *          name?: string}} [options]
   */
  constructor(options = {}) {
    this.capacity = Math.max(1, Math.trunc(Number(options.count ?? 8)));
    this.lifetime = Number(options.lifetime ?? 0.08);
    this.object3D = new THREE.Group();
    this.object3D.name = String(options.name ?? 'a3game_beams');
    this.object3D.frustumCulled = false;

    const material = new THREE.LineBasicMaterial({
      color: options.color ?? 0xffd9a0,
      transparent: true,
      depthWrite: false,
      blending:
        BLENDING_MODES[
          String(options.blending ?? A3GameParticleBlending.ADDITIVE)
            .toLowerCase()
        ] ?? THREE.AdditiveBlending,
    });
    this.material = material;
    /** @type {{line: THREE.Line, life: number}[]} */
    this.beams = [];
    for (let index = 0; index < this.capacity; index += 1) {
      const geometry = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(),
        new THREE.Vector3(),
      ]);
      const line = new THREE.Line(geometry, material.clone());
      line.name = `beam_${index}`;
      line.visible = false;
      line.frustumCulled = false;
      this.object3D.add(line);
      this.beams.push({ line, life: 0 });
    }
    this.totalFired = 0;
  }

  /**
   * Draw one beam.
   *
   * @param {THREE.Vector3} from
   * @param {THREE.Vector3} to
   * @param {{lifetime?: number, color?: number | string}} [options]
   */
  fire(from, to, options = {}) {
    const beam =
      this.beams.find((entry) => entry.life <= 0) ?? this.beams[0];
    if (!beam) return null;
    const positions = beam.line.geometry.attributes.position;
    positions.setXYZ(0, from.x, from.y, from.z);
    positions.setXYZ(1, to.x, to.y, to.z);
    positions.needsUpdate = true;
    beam.line.geometry.computeBoundingSphere();
    if (options.color !== undefined) beam.line.material.color.set(options.color);
    beam.line.visible = true;
    beam.life = Number(options.lifetime ?? this.lifetime);
    beam.maxLife = beam.life;
    this.totalFired += 1;
    return beam;
  }

  update(deltaSeconds) {
    const delta = Math.max(0, Number(deltaSeconds) || 0);
    for (const beam of this.beams) {
      if (beam.life <= 0) continue;
      beam.life = Math.max(0, beam.life - delta);
      beam.line.material.opacity = beam.maxLife
        ? beam.life / beam.maxLife
        : 0;
      if (beam.life === 0) beam.line.visible = false;
    }
    return this;
  }

  getState() {
    return {
      capacity: this.capacity,
      activeCount: this.beams.filter((beam) => beam.life > 0).length,
      totalFired: this.totalFired,
    };
  }

  dispose() {
    for (const beam of this.beams) {
      beam.line.geometry.dispose();
      beam.line.material.dispose();
    }
    this.beams = [];
    this.material.dispose();
    this.object3D.parent?.remove(this.object3D);
  }
}

/**
 * A ribbon that follows a moving object.
 *
 * This is what makes a projectile read as *fast*. Particles alone cannot:
 * a spark emitted behind an arrow is stationary once emitted, so the eye
 * sees a dotted line rather than a streak. A ribbon is a continuous
 * surface through the positions the object actually occupied.
 */
export class A3GameTrailRibbon {
  /**
   * @param {{segments?: number, width?: number,
   *          color?: number | string, endColor?: number | string,
   *          opacity?: number, taper?: boolean, blending?: string,
   *          name?: string}} [options]
   */
  constructor(options = {}) {
    this.segments = Math.max(2, Math.trunc(Number(options.segments ?? 24)));
    this.width = Number(options.width ?? 0.09);
    this.taper = options.taper !== false;
    this.color = new THREE.Color(options.color ?? 0x8fd8ff);
    this.endColor = new THREE.Color(options.endColor ?? this.color);

    const vertexCount = this.segments * 2;
    this.geometry = new THREE.BufferGeometry();
    this.geometry.setAttribute(
      'position',
      new THREE.BufferAttribute(new Float32Array(vertexCount * 3), 3),
    );
    this.geometry.setAttribute(
      'color',
      new THREE.BufferAttribute(new Float32Array(vertexCount * 3), 3),
    );
    const indices = [];
    for (let index = 0; index < this.segments - 1; index += 1) {
      const a = index * 2;
      indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
    }
    this.geometry.setIndex(indices);

    this.material = new THREE.MeshBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: Number(options.opacity ?? 0.9),
      depthWrite: false,
      side: THREE.DoubleSide,
      blending:
        BLENDING_MODES[
          String(options.blending ?? A3GameParticleBlending.ADDITIVE)
            .toLowerCase()
        ] ?? THREE.AdditiveBlending,
    });
    this.object3D = new THREE.Mesh(this.geometry, this.material);
    this.object3D.name = String(options.name ?? 'a3game_trail');
    this.object3D.frustumCulled = false;

    /** @type {THREE.Vector3[]} */
    this.history = [];
    this.#scratch = {
      side: new THREE.Vector3(),
      forward: new THREE.Vector3(),
      toCamera: new THREE.Vector3(),
      color: new THREE.Color(),
    };
  }

  #scratch;

  /** Reset the ribbon to a single point, for a reused projectile. */
  reset(position) {
    this.history = [];
    if (position) {
      for (let index = 0; index < this.segments; index += 1) {
        this.history.push(position.clone());
      }
    }
    return this;
  }

  /**
   * Record a new head position and rebuild the ribbon.
   *
   * @param {THREE.Vector3} position
   * @param {THREE.Camera} [camera] used to face the ribbon at the viewer
   */
  push(position, camera = null) {
    if (this.history.length === 0) return this.reset(position);
    this.history.push(position.clone());
    while (this.history.length > this.segments) this.history.shift();

    const positions = this.geometry.attributes.position.array;
    const colors = this.geometry.attributes.color.array;
    const { side, forward, toCamera, color } = this.#scratch;
    const count = this.history.length;

    for (let index = 0; index < count; index += 1) {
      const point = this.history[index];
      const next = this.history[Math.min(count - 1, index + 1)];
      const previous = this.history[Math.max(0, index - 1)];
      forward.copy(next).sub(previous);
      if (forward.lengthSq() < 1e-10) forward.set(0, 0, 1);
      if (camera) {
        toCamera.copy(camera.position).sub(point);
      } else {
        toCamera.set(0, 1, 0);
      }
      side.crossVectors(forward, toCamera);
      if (side.lengthSq() < 1e-10) side.set(1, 0, 0);
      side.normalize();

      const head = index / Math.max(1, count - 1);
      const half = (this.taper ? head : 1) * this.width * 0.5;
      const base = index * 6;
      positions[base] = point.x - side.x * half;
      positions[base + 1] = point.y - side.y * half;
      positions[base + 2] = point.z - side.z * half;
      positions[base + 3] = point.x + side.x * half;
      positions[base + 4] = point.y + side.y * half;
      positions[base + 5] = point.z + side.z * half;

      color.copy(this.endColor).lerp(this.color, head);
      for (const vertex of [0, 3]) {
        colors[base + vertex] = color.r * head;
        colors[base + vertex + 1] = color.g * head;
        colors[base + vertex + 2] = color.b * head;
      }
    }
    // Collapse the unused tail onto the oldest point instead of leaving
    // stale vertices at the origin, which would draw a ribbon back to the
    // world centre for the first few frames of every projectile.
    for (let index = count; index < this.segments; index += 1) {
      const base = index * 6;
      const last = (count - 1) * 6;
      for (let component = 0; component < 6; component += 1) {
        positions[base + component] = positions[last + component];
        colors[base + component] = 0;
      }
    }
    this.geometry.attributes.position.needsUpdate = true;
    this.geometry.attributes.color.needsUpdate = true;
    this.geometry.computeBoundingSphere();
    return this;
  }

  dispose() {
    this.geometry.dispose();
    this.material.dispose();
    this.object3D.parent?.remove(this.object3D);
  }
}

/**
 * Effect definitions, tuned once so a game names an effect instead of
 * inventing twenty numbers per hit.
 *
 * These are the effects the four generated games actually need. Each is a
 * plain configuration object accepted by `A3GameParticleSystem`, so a
 * game may spread one and override a field rather than starting over.
 */
export const A3GameVfxPreset = Object.freeze({
  /** Gunshot at the muzzle: bright, additive, gone in three frames. */
  MUZZLE_FLASH: Object.freeze({
    maxParticles: 60,
    lifetime: [0.04, 0.09],
    size: [0.12, 0.3],
    sizeOverLife: [1.3, 0.2],
    opacityOverLife: [1, 0],
    colorStart: ['#fff3c4', '#ffcf6a', '#ff9c3a'],
    colorEnd: ['#ff5a1f'],
    speed: [1.5, 5],
    spread: 0.35,
    drag: 6,
    blending: A3GameParticleBlending.ADDITIVE,
    appearance: A3GameParticleAppearance.GRADIENT,
    intensity: 3.4,
  }),
  /** Sparks and dust where a bullet lands. */
  BULLET_IMPACT: Object.freeze({
    maxParticles: 160,
    lifetime: [0.15, 0.4],
    size: [0.02, 0.06],
    sizeOverLife: [1, 0.3],
    opacityOverLife: [1, 0],
    colorStart: ['#ffe6a8', '#ffb057'],
    colorEnd: ['#7a3a10'],
    speed: [3, 9],
    spread: 0.8,
    gravity: [0, -9, 0],
    drag: 1.6,
    stretchBySpeed: { factor: 0.06, maxStretch: 4 },
    renderMode: A3GameParticleRenderMode.STRETCHED,
    blending: A3GameParticleBlending.ADDITIVE,
    intensity: 2.2,
  }),
  /** Grey puff for a concrete or metal hit, under the sparks. */
  IMPACT_DUST: Object.freeze({
    maxParticles: 120,
    lifetime: [0.35, 0.8],
    size: [0.18, 0.42],
    sizeOverLife: [0.6, 1.8],
    opacityOverLife: [0.5, 0],
    colorStart: ['#b9b3a6', '#8d857a'],
    colorEnd: ['#5b554c'],
    speed: [0.6, 2],
    spread: 0.9,
    drag: 2.6,
    turbulence: { intensity: 0.6, frequency: 1.4, speed: 0.6 },
    blending: A3GameParticleBlending.NORMAL,
    intensity: 1,
  }),
  /** A hit on a body reads red and heavy, not sparky. */
  BLOOD_HIT: Object.freeze({
    maxParticles: 120,
    lifetime: [0.25, 0.55],
    size: [0.05, 0.13],
    sizeOverLife: [1, 0.4],
    opacityOverLife: [0.95, 0],
    colorStart: ['#c02020', '#8c1212'],
    colorEnd: ['#4a0a0a'],
    speed: [2, 6],
    spread: 0.7,
    gravity: [0, -12, 0],
    drag: 1.2,
    blending: A3GameParticleBlending.NORMAL,
    intensity: 1,
  }),
  /** Fist or foot connecting: a hard white flash plus a ring. */
  MELEE_IMPACT: Object.freeze({
    maxParticles: 90,
    lifetime: [0.1, 0.26],
    size: [0.08, 0.22],
    sizeOverLife: [1.4, 0.1],
    opacityOverLife: [1, 0],
    colorStart: ['#ffffff', '#ffe9b0'],
    colorEnd: ['#ff9d4d'],
    speed: [2.5, 7],
    spread: 1,
    drag: 5,
    blending: A3GameParticleBlending.ADDITIVE,
    intensity: 2.6,
  }),
  /** Expanding ring that sells the weight of a heavy hit. */
  SHOCK_RING: Object.freeze({
    maxParticles: 8,
    lifetime: [0.16, 0.24],
    size: [0.5, 0.7],
    sizeOverLife: [0.4, 2.6],
    opacityOverLife: [0.85, 0],
    colorStart: ['#ffffff'],
    colorEnd: ['#9fd0ff'],
    speed: [0, 0],
    appearance: A3GameParticleAppearance.RING,
    blending: A3GameParticleBlending.ADDITIVE,
    intensity: 2,
  }),
  /** Guard held: bright, small, and clearly not damage. */
  BLOCK_SPARK: Object.freeze({
    maxParticles: 80,
    lifetime: [0.12, 0.3],
    size: [0.03, 0.08],
    sizeOverLife: [1, 0.2],
    opacityOverLife: [1, 0],
    colorStart: ['#bfe9ff', '#7fc4ff'],
    colorEnd: ['#2f6bb0'],
    speed: [3, 8],
    spread: 0.9,
    gravity: [0, -6, 0],
    drag: 2,
    stretchBySpeed: { factor: 0.05, maxStretch: 3 },
    renderMode: A3GameParticleRenderMode.STRETCHED,
    blending: A3GameParticleBlending.ADDITIVE,
    intensity: 2.4,
  }),
  /** Dust kicked up by a footfall or a landing. */
  FOOT_DUST: Object.freeze({
    maxParticles: 90,
    lifetime: [0.3, 0.7],
    size: [0.12, 0.3],
    sizeOverLife: [0.7, 2],
    opacityOverLife: [0.35, 0],
    colorStart: ['#cfc6b4'],
    colorEnd: ['#8a8272'],
    speed: [0.4, 1.4],
    direction: [
      [-1, 1],
      [0.2, 0.8],
      [-1, 1],
    ],
    drag: 3,
    blending: A3GameParticleBlending.NORMAL,
    intensity: 1,
  }),
  /** The light arrow's body: a dense additive core that follows it. */
  LIGHT_ARROW_CORE: Object.freeze({
    maxParticles: 220,
    lifetime: [0.16, 0.34],
    size: [0.07, 0.16],
    sizeOverLife: [1.1, 0],
    opacityOverLife: [1, 0],
    colorStart: ['#eaffff', '#9fe4ff', '#6fb6ff'],
    colorEnd: ['#2a63c8'],
    speed: [0.2, 0.9],
    emitterShape: A3GameEmitterShape.SPHERE,
    emitterRadius: [0, 0.07],
    startPositionAsDirection: true,
    drag: 3.5,
    emissionOverTime: 220,
    autoStart: false,
    blending: A3GameParticleBlending.ADDITIVE,
    intensity: 3,
  }),
  /** Motes that fall away from the arrow's path. */
  LIGHT_ARROW_MOTES: Object.freeze({
    maxParticles: 180,
    lifetime: [0.4, 0.9],
    size: [0.03, 0.07],
    sizeOverLife: [1, 0.1],
    opacityOverLife: [0.9, 0],
    colorStart: ['#d8f6ff', '#7ec8ff'],
    colorEnd: ['#1c4f9c'],
    speed: [0.3, 1.2],
    gravity: [0, -1.6, 0],
    turbulence: { intensity: 0.8, frequency: 2.2, speed: 1.4 },
    emissionOverTime: 70,
    autoStart: false,
    blending: A3GameParticleBlending.ADDITIVE,
    intensity: 2,
  }),
  /** The arrow landing: a bright radial burst. */
  LIGHT_ARROW_IMPACT: Object.freeze({
    maxParticles: 200,
    lifetime: [0.2, 0.55],
    size: [0.06, 0.16],
    sizeOverLife: [1.2, 0],
    opacityOverLife: [1, 0],
    colorStart: ['#ffffff', '#a9e8ff'],
    colorEnd: ['#1f57b8'],
    speed: [4, 12],
    emitterShape: A3GameEmitterShape.SPHERE,
    emitterRadius: [0.02, 0.16],
    startPositionAsDirection: true,
    drag: 4.5,
    stretchBySpeed: { factor: 0.05, maxStretch: 5 },
    renderMode: A3GameParticleRenderMode.STRETCHED,
    blending: A3GameParticleBlending.ADDITIVE,
    intensity: 3,
  }),
  /** A sword arc: few, large, short-lived, additive. */
  BLADE_SLASH: Object.freeze({
    maxParticles: 70,
    lifetime: [0.1, 0.24],
    size: [0.18, 0.44],
    sizeOverLife: [1, 0.2],
    opacityOverLife: [0.9, 0],
    colorStart: ['#ffffff', '#d8f0ff'],
    colorEnd: ['#5aa0ff'],
    speed: [1, 4],
    spread: 0.5,
    drag: 4,
    blending: A3GameParticleBlending.ADDITIVE,
    intensity: 2.6,
  }),
  /** Loot and pickups: slow, upward, sparkling. */
  PICKUP_SPARKLE: Object.freeze({
    maxParticles: 120,
    lifetime: [0.6, 1.3],
    size: [0.03, 0.08],
    sizeOverLife: [1, 0.2],
    opacityOverLife: [1, 0],
    colorStart: ['#ffe9a3', '#fff6d0'],
    colorEnd: ['#c78a20'],
    speed: [0.4, 1.3],
    direction: [
      [-0.5, 0.5],
      [0.6, 1],
      [-0.5, 0.5],
    ],
    gravity: [0, -0.6, 0],
    drag: 1.2,
    emitterShape: A3GameEmitterShape.DISK,
    emitterRadius: [0.1, 0.45],
    blending: A3GameParticleBlending.ADDITIVE,
    intensity: 2,
  }),
  /** Smoke that keeps rising, for a corpse or a wreck. */
  SMOKE_PLUME: Object.freeze({
    maxParticles: 200,
    lifetime: [1.2, 2.4],
    size: [0.3, 0.7],
    sizeOverLife: [0.6, 2.4],
    opacityOverLife: [0.45, 0],
    colorStart: ['#6e6a63', '#4c4842'],
    colorEnd: ['#2a2724'],
    speed: [0.3, 0.9],
    direction: [
      [-0.2, 0.2],
      [0.6, 1],
      [-0.2, 0.2],
    ],
    gravity: [0, 0.4, 0],
    windResponse: 1.2,
    turbulence: { intensity: 0.5, frequency: 0.8, speed: 0.4 },
    emissionOverTime: 26,
    autoStart: false,
    blending: A3GameParticleBlending.NORMAL,
    intensity: 1,
  }),
  /** An explosion: fire first, the smoke preset layered on top. */
  EXPLOSION: Object.freeze({
    maxParticles: 240,
    lifetime: [0.25, 0.7],
    size: [0.3, 0.9],
    sizeOverLife: [1, 1.8],
    opacityOverLife: [1, 0],
    colorStart: ['#fff1b8', '#ffab3d', '#ff5f1f'],
    colorEnd: ['#4a1608'],
    speed: [4, 14],
    emitterShape: A3GameEmitterShape.SPHERE,
    emitterRadius: [0, 0.4],
    startPositionAsDirection: true,
    drag: 3,
    blending: A3GameParticleBlending.ADDITIVE,
    intensity: 3,
  }),
  FIRE_PLUME: Object.freeze({
    maxParticles: 320, lifetime: [0.35, 0.85], size: [0.12, 0.4],
    sizeOverLife: [1, 0.1], opacityOverLife: [0.9, 0],
    colorStart: ['#fff2ba', '#ffad29', '#ff521c'], colorEnd: ['#94240a'],
    speed: [1, 2.2], direction: [[-0.12, 0.12], [0.8, 1], [-0.12, 0.12]],
    gravity: [0, 1.5, 0], windResponse: 1.8,
    turbulence: { intensity: 0.6, frequency: 2.5, speed: 1.3 },
    emitterShape: A3GameEmitterShape.DISK, emitterRadius: [0, 0.4],
    emissionOverTime: 170, autoStart: false,
    blending: A3GameParticleBlending.ADDITIVE, intensity: 2.3,
  }),
  /** Tyre smoke while a car slides. */
  TYRE_SMOKE: Object.freeze({
    maxParticles: 260,
    lifetime: [0.5, 1.1],
    size: [0.25, 0.6],
    sizeOverLife: [0.5, 2.2],
    opacityOverLife: [0.4, 0],
    colorStart: ['#cfcac2', '#9e9a92'],
    colorEnd: ['#6c6862'],
    speed: [0.6, 2],
    direction: [
      [-0.6, 0.6],
      [0.3, 0.9],
      [-0.6, 0.6],
    ],
    drag: 2.2,
    turbulence: { intensity: 0.7, frequency: 1.2, speed: 0.8 },
    emissionOverTime: 90,
    autoStart: false,
    blending: A3GameParticleBlending.NORMAL,
    intensity: 1,
  }),
  /** Sparks from a scrape along a barrier. */
  SCRAPE_SPARK: Object.freeze({
    maxParticles: 200,
    lifetime: [0.2, 0.5],
    size: [0.02, 0.05],
    sizeOverLife: [1, 0.2],
    opacityOverLife: [1, 0],
    colorStart: ['#fff0c0', '#ffb44a'],
    colorEnd: ['#8a3c08'],
    speed: [4, 11],
    spread: 1,
    gravity: [0, -14, 0],
    drag: 0.8,
    stretchBySpeed: { factor: 0.07, maxStretch: 6 },
    renderMode: A3GameParticleRenderMode.STRETCHED,
    emissionOverTime: 140,
    autoStart: false,
    blending: A3GameParticleBlending.ADDITIVE,
    intensity: 2.6,
  }),
  /** Exhaust or boost flame behind a vehicle. */
  BOOST_FLAME: Object.freeze({
    maxParticles: 180,
    lifetime: [0.12, 0.3],
    size: [0.12, 0.3],
    sizeOverLife: [1.2, 0.2],
    opacityOverLife: [1, 0],
    colorStart: ['#bfe4ff', '#7fb4ff', '#ffd9a0'],
    colorEnd: ['#ff5a1f'],
    speed: [2, 6],
    drag: 5,
    emissionOverTime: 160,
    autoStart: false,
    blending: A3GameParticleBlending.ADDITIVE,
    intensity: 3,
  }),
});

/**
 * The batched owner of every effect in a game.
 *
 * three.quarks calls this a `BatchedRenderer`, and the reason it exists
 * is worth stating: effects are requested from all over a game — a rule
 * resolving a hit, an entity firing, a projectile expiring — and if each
 * of those constructs its own system, the game accumulates draw calls,
 * duplicate materials, and undisposed geometry in proportion to how much
 * is happening. One director means one place that owns, ticks, and frees
 * them, and one place a test can ask what fired.
 */
export class A3GameVfxDirector {
  /**
   * @param {{host?: object, root?: string, seed?: number,
   *          scale?: number}} [options]
   */
  constructor(options = {}) {
    this.host = options.host ?? null;
    this.rootName = String(options.root ?? 'effects');
    this.seed = Number(options.seed ?? 0x51ff);
    this.scale = Number(options.scale ?? 1);
    /** @type {Map<string, A3GameParticleSystem>} */
    this.systems = new Map();
    /** @type {Map<string, A3GameBeamEffect>} */
    this.beams = new Map();
    /** @type {{system: A3GameParticleSystem, target: object,
     *           offset: THREE.Vector3}[]} */
    this.followers = [];
    /** @type {Map<string, number>} */
    this.playCounts = new Map();
    this.unsubscribe = null;
    this.container = this.host
      ? this.host.getRoot(this.rootName)
      : new THREE.Group();
    if (!this.host) this.container.name = `A3Game_${this.rootName}`;
  }

  /**
   * Register one effect under a name.
   *
   * @param {string} name
   * @param {object} config a preset, or a preset spread with overrides
   * @returns {A3GameParticleSystem}
   */
  register(name, config = {}) {
    const key = String(name);
    const existing = this.systems.get(key);
    if (existing) return existing;
    const system = new A3GameParticleSystem({
      ...config,
      name: key,
      seed: this.seed + key.length * 7919,
    });
    this.systems.set(key, system);
    this.container.add(system.object3D);
    return system;
  }

  /**
   * Register several presets at once.
   *
   * @param {Record<string, object>} presets name -> config
   */
  registerAll(presets = {}) {
    for (const [name, config] of Object.entries(presets)) {
      this.register(name, config);
    }
    return this;
  }

  /** @returns {A3GameParticleSystem | null} */
  get(name) {
    return this.systems.get(String(name)) ?? null;
  }

  /**
   * Fire a one-shot burst.
   *
   * Unknown names are ignored rather than thrown: an effect is decoration,
   * and a missing one must never be the reason a hit stops registering.
   *
   * @param {string} name
   * @param {{position?: THREE.Vector3 | number[],
   *          direction?: THREE.Vector3 | number[],
   *          count?: number, scale?: number,
   *          colorStart?: string | string[],
   *          spread?: number}} [options]
   * @returns {number} particles spawned
   */
  play(name, options = {}) {
    const key = String(name);
    const system = this.systems.get(key);
    if (!system) return 0;
    const count = Math.trunc(
      Number(options.count ?? Math.min(system.capacity, 24)),
    );
    const spawned = system.emit(count, {
      ...options,
      scale: Number(options.scale ?? 1) * this.scale,
    });
    this.playCounts.set(key, (this.playCounts.get(key) ?? 0) + 1);
    return spawned;
  }

  /**
   * Make a registered continuous system follow an object.
   *
   * The system stays in world space and its emitter is moved to the
   * target's world position each frame, which is what a trail needs:
   * parenting the *particles* to a moving object would drag every already
   * emitted particle along with it, and the streak would never form.
   *
   * @param {string} name
   * @param {THREE.Object3D} target
   * @param {{offset?: number[], rate?: number}} [options]
   * @returns {{stop: () => void} | null}
   */
  follow(name, target, options = {}) {
    const system = this.systems.get(String(name));
    if (!system || !target) return null;
    if (options.rate !== undefined) {
      system.emissionOverTime = Math.max(0, Number(options.rate));
    }
    system.start();
    const follower = {
      system,
      target,
      offset: readVector(options.offset, [0, 0, 0]),
    };
    this.followers.push(follower);
    return {
      stop: () => {
        system.stop();
        const index = this.followers.indexOf(follower);
        if (index >= 0) this.followers.splice(index, 1);
      },
    };
  }

  /**
   * Register a pooled beam effect, for tracers and hit lines.
   *
   * @param {string} name
   * @param {object} [options] as `A3GameBeamEffect`
   */
  registerBeam(name, options = {}) {
    const key = String(name);
    const existing = this.beams.get(key);
    if (existing) return existing;
    const beam = new A3GameBeamEffect({ ...options, name: key });
    this.beams.set(key, beam);
    this.container.add(beam.object3D);
    return beam;
  }

  /**
   * Draw a registered beam between two points.
   *
   * @param {string} name
   * @param {THREE.Vector3} from
   * @param {THREE.Vector3} to
   * @param {object} [options]
   */
  fireBeam(name, from, to, options = {}) {
    const beam = this.beams.get(String(name));
    if (!beam) return null;
    this.playCounts.set(
      `beam:${name}`,
      (this.playCounts.get(`beam:${name}`) ?? 0) + 1,
    );
    return beam.fire(from, to, options);
  }

  /** Advance every system, beam, and follower. */
  update(deltaSeconds, camera = null) {
    const view = camera ?? this.host?.camera ?? null;
    for (let index = this.followers.length - 1; index >= 0; index -= 1) {
      const follower = this.followers[index];
      const target = follower.target;
      if (!target.parent && !target.isCamera) {
        // The followed object was removed from the scene. Stop emitting
        // rather than trailing a ghost from wherever it last was.
        follower.system.stop();
        this.followers.splice(index, 1);
        continue;
      }
      target.updateMatrixWorld(true);
      follower.system.emitterPosition
        .setFromMatrixPosition(target.matrixWorld)
        .add(follower.offset);
    }
    for (const system of this.systems.values()) {
      if (this.host?.wind) system.windField = this.host.wind;
      system.update(deltaSeconds, view);
    }
    for (const beam of this.beams.values()) beam.update(deltaSeconds);
    return this;
  }

  /** Bind the director to the host tick and keep the unsubscribe. */
  attachToHost(host = this.host) {
    if (!host) throw new Error('A3GameVfxDirector.attachToHost needs a host');
    this.host = host;
    if (this.container.parent === null) {
      host.add(this.container, this.rootName);
    }
    this.unsubscribe?.();
    this.unsubscribe = host.onTick((delta) => this.update(delta, host.camera));
    return this.unsubscribe;
  }

  /** @returns {object} what is registered and what has fired. */
  getState() {
    return {
      systems: [...this.systems.values()].map((system) => system.getState()),
      beams: Object.fromEntries(
        [...this.beams.entries()].map(([name, beam]) => [
          name,
          beam.getState(),
        ]),
      ),
      followers: this.followers.length,
      plays: Object.fromEntries(this.playCounts),
      activeParticles: [...this.systems.values()].reduce(
        (total, system) => total + system.activeCount,
        0,
      ),
    };
  }

  dispose() {
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.followers = [];
    for (const system of this.systems.values()) system.dispose();
    this.systems.clear();
    for (const beam of this.beams.values()) beam.dispose();
    this.beams.clear();
    this.playCounts.clear();
  }
}

/**
 * Build a director already loaded with the standard effect set.
 *
 * @param {{host?: object, root?: string, seed?: number, scale?: number,
 *          presets?: Record<string, object>,
 *          attach?: boolean}} [options]
 * @returns {A3GameVfxDirector}
 */
export function createVfxDirector(options = {}) {
  const director = new A3GameVfxDirector(options);
  const presets = options.presets ?? {
    muzzle_flash: A3GameVfxPreset.MUZZLE_FLASH,
    bullet_impact: A3GameVfxPreset.BULLET_IMPACT,
    impact_dust: A3GameVfxPreset.IMPACT_DUST,
    blood_hit: A3GameVfxPreset.BLOOD_HIT,
    melee_impact: A3GameVfxPreset.MELEE_IMPACT,
    shock_ring: A3GameVfxPreset.SHOCK_RING,
    block_spark: A3GameVfxPreset.BLOCK_SPARK,
    foot_dust: A3GameVfxPreset.FOOT_DUST,
  };
  director.registerAll(presets);
  if (options.attach !== false && options.host) {
    director.attachToHost(options.host);
  }
  return director;
}
