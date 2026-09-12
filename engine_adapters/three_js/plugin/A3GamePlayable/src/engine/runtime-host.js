/**
 * Owns the WebGL renderer, scene, camera, controls, and frame loop.
 *
 * Unreal supplies this natively; on the web the framework must own it.
 * `A3GameRuntimeHost` is deliberately game-neutral: it renders, ticks,
 * resizes, picks, and disposes. It defines no player, weapon, vehicle,
 * or combat rule.
 */

import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { PointerLockControls } from 'three/addons/controls/PointerLockControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { Sky } from 'three/addons/objects/Sky.js';
import { createSkyGradient } from './visual-kit.js';
import { A3GameWindField } from './wind-field.js';

/**
 * Procedural environments, so image-based lighting needs no `.hdr`.
 *
 * Without an environment map a PBR material has nothing to reflect: it
 * loses every highlight and reads as flat plastic no matter how the
 * lights are placed. This is the largest single difference between a
 * generated three.js scene that looks cheap and one that does not, and
 * every preset here is generated on the GPU at boot from geometry
 * three.js already ships.
 */
export const A3GameEnvironmentPreset = Object.freeze({
  /** Neutral studio box. Interiors, arenas, menus, product-like views. */
  ROOM: 'room',
  /**
   * Preetham physical sky with a sun.
   *
   * Correct, and therefore *cloudless* — which is why an outdoor scene
   * using it still reads as an empty studio dome. Prefer `GRADIENT`
   * unless the game genuinely needs atmospheric scattering.
   */
  SKY: 'sky',
  /**
   * Stylised sky: horizon gradient, sun disc and glow, drifting clouds.
   *
   * The default for outdoor games. It supplies the two things `SKY`
   * cannot — clouds and an art-directable palette — and it feeds the
   * same PMREM convolution, so reflections match the visible sky.
   */
  GRADIENT: 'gradient',
  /** No environment map. */
  NONE: 'none',
});

const TONE_MAPPINGS = Object.freeze({
  NoToneMapping: THREE.NoToneMapping,
  LinearToneMapping: THREE.LinearToneMapping,
  ReinhardToneMapping: THREE.ReinhardToneMapping,
  CineonToneMapping: THREE.CineonToneMapping,
  ACESFilmicToneMapping: THREE.ACESFilmicToneMapping,
  AgXToneMapping: THREE.AgXToneMapping,
  NeutralToneMapping: THREE.NeutralToneMapping,
});

const resolveElement = (target) => {
  if (!target) return null;
  if (typeof target === 'string') return document.querySelector(target);
  return target;
};

export class A3GameRuntimeHost {
  /**
   * @param {{container: string | HTMLElement,
   *          hudContainer?: string | HTMLElement,
   *          antialias?: boolean,
   *          shadows?: boolean,
   *          pixelRatioCap?: number,
   *          clearColor?: number | string,
   *          toneMapping?: keyof typeof TONE_MAPPINGS,
   *          toneMappingExposure?: number,
   *          cameraType?: 'perspective' | 'orthographic',
   *          frustumHeight?: number,
   *          fov?: number, near?: number, far?: number,
   *          fixedTimeStep?: number, maxSubSteps?: number,
   *          maxFrameDelta?: number, environmentUpdateInterval?: number}} options
   */
  constructor(options = {}) {
    this.options = {
      antialias: true,
      shadows: true,
      pixelRatioCap: 2,
      clearColor: 0x101014,
      toneMapping: 'NeutralToneMapping',
      toneMappingExposure: 1,
      cameraType: 'perspective',
      frustumHeight: 12,
      fov: 50,
      near: 0.1,
      far: 2000,
      fixedTimeStep: 1 / 60,
      maxSubSteps: 6,
      maxFrameDelta: 0.1,
      environmentUpdateInterval: 0,
      ...options,
    };
    for (const key of ['fixedTimeStep', 'maxFrameDelta', 'environmentUpdateInterval']) {
      const value = Number(this.options[key]);
      if (!Number.isFinite(value) || value < 0 || (key === 'maxFrameDelta' && value === 0)) {
        throw new RangeError(`${key} must be finite and ${key === 'maxFrameDelta' ? 'positive' : 'non-negative'}`);
      }
      this.options[key] = value;
    }
    if (!Number.isInteger(this.options.maxSubSteps) || this.options.maxSubSteps < 1) {
      throw new RangeError('maxSubSteps must be a positive integer');
    }
    this.container = resolveElement(options.container);
    this.hudContainer = resolveElement(options.hudContainer);

    /** @type {THREE.WebGLRenderer | null} */
    this.renderer = null;
    /** @type {THREE.Scene | null} */
    this.scene = null;
    /** @type {THREE.PerspectiveCamera | THREE.OrthographicCamera | null} */
    this.camera = null;
    /** @type {OrbitControls | PointerLockControls | null} */
    this.controls = null;
    /**
     * Environment map built by a preset, owned by this host.
     *
     * @type {THREE.Texture | null}
     */
    this.generatedEnvironment = null;
    /**
     * The visible sky object installed by a preset, if any.
     *
     * Held because a stylised sky animates: its clouds drift, so the
     * host has to tick it, and a game that had to remember to do that
     * itself would simply forget.
     *
     * @type {THREE.Object3D | null}
     */
    this.skyDome = null;
    /**
     * Direction *towards* the sun, in world space, unit length.
     *
     * The single most common lighting bug in a generated outdoor scene is
     * a `DirectionalLight` pointing one way while the sun is painted
     * somewhere else, so shadows fall towards the light source. Whatever
     * installs a sky records the answer here and
     * `getSunDirection()` hands it to the game.
     *
     * @type {THREE.Vector3}
     */
    this.sunDirection = new THREE.Vector3(0.35, 0.22, -1).normalize();
    this.wind = new A3GameWindField(options.wind ?? {});
    this.clock = new THREE.Clock();
    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();

    this.frameHandle = null;
    this.running = false;
    this.frameCount = 0;
    this.elapsedSeconds = 0;
    this.accumulator = 0;
    this.interpolationAlpha = 0;
    this.droppedSeconds = 0;
    this.lastSubSteps = 0;
    this.renderListeners = new Set();
    this.interpolatedObjects = new Map();
    this.sunLights = new Map();
    this.sunConfigured = false;
    this.environmentPresetOptions = null;
    this.environmentAge = 0;
    this.generatedEnvironmentTarget = null;

    /** @type {Set<(delta: number, elapsed: number) => void>} */
    this.tickListeners = new Set();
    /** @type {Set<(size: {width: number, height: number}) => void>} */
    this.resizeListeners = new Set();
    /** @type {Map<string, THREE.Object3D>} */
    this.namedRoots = new Map();
    this.resizeObserver = null;
    this.onWindowResize = null;
  }

  /** Create renderer, scene, and camera. Call before `start()`. */
  async init() {
    if (!this.container) {
      throw new Error(
        'A3GameRuntimeHost requires an existing container element',
      );
    }
    const { clientWidth, clientHeight } = this.#size();

    this.renderer = new THREE.WebGLRenderer({
      antialias: this.options.antialias,
      alpha: false,
      powerPreference: 'high-performance',
      preserveDrawingBuffer: true,
    });
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.setClearColor(new THREE.Color(this.options.clearColor));
    this.renderer.setPixelRatio(
      Math.min(window.devicePixelRatio || 1, this.options.pixelRatioCap),
    );
    this.renderer.setSize(clientWidth, clientHeight);
    this.renderer.toneMapping =
      TONE_MAPPINGS[this.options.toneMapping] ?? THREE.NeutralToneMapping;
    this.renderer.toneMappingExposure = this.options.toneMappingExposure;
    this.renderer.shadowMap.enabled = Boolean(this.options.shadows);
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.container.appendChild(this.renderer.domElement);

    this.scene = new THREE.Scene();
    this.scene.name = 'A3GameWorld';

    this.camera = this.#createCamera(
      this.options.cameraType,
      clientWidth / Math.max(1, clientHeight),
    );
    this.camera.name = 'A3GameCamera';
    this.camera.position.set(0, 2, 8);
    this.camera.lookAt(0, 1, 0);

    this.#observeResize();
    return this;
  }

  /**
   * Replace the active camera with a perspective camera.
   *
   * The new camera inherits the current position and any parent, so a
   * game may switch projection at runtime without re-parenting.
   *
   * @param {{fov?: number, near?: number, far?: number}} [options]
   */
  usePerspectiveCamera(options = {}) {
    Object.assign(this.options, {
      cameraType: 'perspective',
      fov: Number(options.fov ?? this.options.fov),
      near: Number(options.near ?? this.options.near),
      far: Number(options.far ?? this.options.far),
    });
    return this.#swapCamera('perspective');
  }

  /**
   * Replace the active camera with an orthographic camera.
   *
   * `frustumHeight` is the number of world-space metres visible
   * vertically; width follows the container aspect ratio. This is what a
   * side-scrolling or isometric game needs, and it is what the world
   * spec's `camera.type = 'orthographic'` selects.
   *
   * @param {{frustumHeight?: number, near?: number, far?: number}} [options]
   */
  useOrthographicCamera(options = {}) {
    Object.assign(this.options, {
      cameraType: 'orthographic',
      frustumHeight: Math.max(
        0.01,
        Number(options.frustumHeight ?? this.options.frustumHeight),
      ),
      near: Number(options.near ?? -this.options.far),
      far: Number(options.far ?? this.options.far),
    });
    return this.#swapCamera('orthographic');
  }

  /** Begin the render loop. Safe to call once. */
  start() {
    if (this.running) return this;
    if (!this.renderer || !this.scene || !this.camera) {
      throw new Error('A3GameRuntimeHost.init() must run before start()');
    }
    this.running = true;
    this.clock.start();
    const loop = () => {
      if (!this.running) return;
      this.frameHandle = requestAnimationFrame(loop);
      this.tick();
    };
    this.frameHandle = requestAnimationFrame(loop);
    return this;
  }

  /** Pause the render loop without releasing GPU resources. */
  stop() {
    this.running = false;
    if (this.frameHandle !== null) {
      cancelAnimationFrame(this.frameHandle);
      this.frameHandle = null;
    }
    return this;
  }

  /**
   * Advance and render exactly one frame.
   *
   * Exposed so headless tests can step the simulation deterministically
   * without a real animation frame.
   *
   * @param {number} [forcedDelta] seconds
   */
  tick(forcedDelta) {
    const requested = forcedDelta === undefined ? this.clock.getDelta() : Number(forcedDelta);
    if (!Number.isFinite(requested) || requested < 0) {
      throw new RangeError('tick delta must be finite and non-negative');
    }
    const delta = Math.min(requested, this.options.maxFrameDelta);
    this.droppedSeconds += requested - delta;
    this.frameCount += 1;
    this.lastSubSteps = 0;
    const step = this.options.fixedTimeStep;
    if (step > 0) {
      this.accumulator += delta;
      while (this.accumulator + step * 1e-9 >= step && this.lastSubSteps < this.options.maxSubSteps) {
        this.#simulate(step);
        this.accumulator = Math.max(0, this.accumulator - step);
        this.lastSubSteps += 1;
      }
      if (this.accumulator >= step) {
        const discarded = Math.floor(this.accumulator / step) * step;
        this.droppedSeconds += discarded;
        this.accumulator -= discarded;
      }
      this.interpolationAlpha = this.accumulator / step;
    } else {
      this.#simulate(delta);
      this.lastSubSteps = 1;
      this.interpolationAlpha = 1;
    }
    this.#renderFrame(delta);
    return this;
  }

  #simulate(delta) {
    for (const [object, previous] of this.interpolatedObjects) {
      previous.position.copy(object.position);
      previous.quaternion.copy(object.quaternion);
      previous.scale.copy(object.scale);
    }
    this.elapsedSeconds += delta;
    this.#emitTick(delta);
    this.environmentAge += delta;
    const interval = this.options.environmentUpdateInterval;
    if (interval > 0 && this.environmentAge >= interval && this.generatedEnvironment) {
      this.refreshEnvironment();
    }
  }

  #renderFrame(delta) {
    const restored = [];
    try {
      for (const [object, previous] of this.interpolatedObjects) {
        const state = previous.current;
        state.position.copy(object.position);
        state.quaternion.copy(object.quaternion);
        state.scale.copy(object.scale);
        restored.push([object, state]);
        object.position.lerpVectors(previous.position, state.position, this.interpolationAlpha);
        object.quaternion.slerpQuaternions(previous.quaternion, state.quaternion, this.interpolationAlpha);
        object.scale.lerpVectors(previous.scale, state.scale, this.interpolationAlpha);
        object.updateMatrixWorld(true);
      }
      for (const listener of this.renderListeners) listener(delta, this.interpolationAlpha);
      this.controls?.update?.(delta);
      this.renderer?.render(this.scene, this.camera);
    } finally {
      for (const [object, state] of restored) {
        object.position.copy(state.position);
        object.quaternion.copy(state.quaternion);
        object.scale.copy(state.scale);
        object.updateMatrixWorld(true);
      }
    }
  }

  /** Display-only updates after simulation; alpha interpolates the last two states. */
  onRender(listener) {
    if (typeof listener !== 'function') throw new TypeError('onRender requires a function');
    this.renderListeners.add(listener);
    return () => this.renderListeners.delete(listener);
  }

  /** Render interpolation never writes back to the simulation transform. */
  interpolateObject(object) {
    if (!object?.isObject3D) throw new TypeError('interpolateObject requires an Object3D');
    const snapshot = () => ({
      position: object.position.clone(), quaternion: object.quaternion.clone(), scale: object.scale.clone(),
    });
    this.interpolatedObjects.set(object, { ...snapshot(), current: snapshot() });
    return () => this.interpolatedObjects.delete(object);
  }

  /** Reset the previous transform after teleporting or respawning. */
  resetInterpolation(object) {
    if (this.interpolatedObjects.has(object)) this.interpolateObject(object);
    return this;
  }

  /**
   * Subscribe to the frame tick.
   *
   * @param {(delta: number, elapsed: number) => void} listener
   * @returns {() => void} unsubscribe
   */
  onTick(listener) {
    if (typeof listener !== 'function') {
      throw new TypeError('onTick requires a function');
    }
    this.tickListeners.add(listener);
    return () => this.tickListeners.delete(listener);
  }

  /** @param {(size: {width: number, height: number}) => void} listener */
  onResize(listener) {
    if (typeof listener !== 'function') {
      throw new TypeError('onResize requires a function');
    }
    this.resizeListeners.add(listener);
    return () => this.resizeListeners.delete(listener);
  }

  /** Add an object under a stable named root, creating the root once. */
  add(object, rootName = 'entities') {
    if (!this.scene) throw new Error('init() must run before add()');
    let root = this.namedRoots.get(rootName);
    if (!root) {
      root = new THREE.Group();
      root.name = `A3Game_${rootName}`;
      this.scene.add(root);
      this.namedRoots.set(rootName, root);
    }
    root.add(object);
    object.traverse((child) => {
      if (child.isDirectionalLight && child.userData.a3gameSun) this.registerSunLight(child);
    });
    return object;
  }

  remove(object) {
    object?.traverse?.((child) => {
      this.sunLights.delete(child);
      this.interpolatedObjects.delete(child);
    });
    object?.parent?.remove(object);
    return this;
  }

  registerSunLight(light, distance) {
    if (!light?.isDirectionalLight) throw new TypeError('Sun must be a DirectionalLight');
    light.updateWorldMatrix(true, false);
    light.target.updateWorldMatrix(true, false);
    const span = distance ?? light.getWorldPosition(new THREE.Vector3())
      .distanceTo(light.target.getWorldPosition(new THREE.Vector3()));
    if (!Number.isFinite(span) || span <= 0) throw new RangeError('Sun distance must be positive');
    this.sunLights.set(light, span);
    if (!light.target.parent && this.scene) this.scene.add(light.target);
    if (this.sunConfigured) this.#syncSun();
    return () => this.sunLights.delete(light);
  }

  #syncSun() {
    this.skyDome?.userData?.setSunDirection?.(this.sunDirection);
    this.skyDome?.material?.uniforms?.sunPosition?.value.copy(this.sunDirection);
    for (const [light, distance] of this.sunLights) {
      const position = light.target.getWorldPosition(new THREE.Vector3())
        .addScaledVector(this.sunDirection, distance);
      if (light.parent) light.parent.worldToLocal(position);
      light.position.copy(position);
      light.updateMatrixWorld(true);
    }
  }

  /** Rebuild procedural IBL on demand, never replacing an imported HDRI. */
  refreshEnvironment() {
    if (this.generatedEnvironment && this.environmentPresetOptions) {
      this.#applyEnvironmentPreset(this.environmentPresetOptions, true);
    }
    this.environmentAge = 0;
    return this;
  }

  /** @returns {THREE.Group} the named root group, created on demand. */
  getRoot(rootName = 'entities') {
    if (!this.namedRoots.has(rootName)) {
      this.add(new THREE.Group(), rootName);
    }
    return this.namedRoots.get(rootName);
  }

  /**
   * Attach orbit controls for a spectator or third-person camera.
   *
   * @param {{target?: {x: number, y: number, z: number},
   *          enableDamping?: boolean, maxPolarAngle?: number,
   *          minDistance?: number, maxDistance?: number}} [options]
   */
  attachOrbitControls(options = {}) {
    this.detachControls();
    const controls = new OrbitControls(
      this.camera,
      this.renderer.domElement,
    );
    controls.enableDamping = options.enableDamping !== false;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = false;
    if (options.maxPolarAngle !== undefined) {
      controls.maxPolarAngle = options.maxPolarAngle;
    }
    if (options.minDistance !== undefined) {
      controls.minDistance = options.minDistance;
    }
    if (options.maxDistance !== undefined) {
      controls.maxDistance = options.maxDistance;
    }
    const target = options.target ?? { x: 0, y: 1, z: 0 };
    controls.target.set(target.x, target.y, target.z);
    controls.update();
    this.controls = controls;
    return controls;
  }

  /**
   * Attach pointer-lock controls for a first-person camera.
   *
   * Use this only when `PointerLockControls` should own the camera
   * orientation. A game that derives yaw/pitch from the normalized input
   * frame must call `requestPointerLock()` instead, otherwise both the
   * controls and the game write to the same camera every frame.
   */
  attachPointerLockControls() {
    this.detachControls();
    const controls = new PointerLockControls(this.camera, this.container);
    this.controls = controls;
    return controls;
  }

  /**
   * Capture the mouse without installing any camera controller.
   *
   * This is the primitive an FPS built on `A3GameInputRouter` needs: the
   * browser delivers relative `movementX/movementY`, the router turns
   * them into yaw/pitch, and gameplay stays the only camera author.
   *
   * The Pointer Lock API requires a user gesture, so call this from a
   * click or key handler, never at boot.
   *
   * @returns {Promise<boolean>} whether the lock was granted
   */
  async requestPointerLock() {
    const element = this.container;
    if (!element?.requestPointerLock) return false;
    try {
      const result = element.requestPointerLock({
        unadjustedMovement: true,
      });
      if (result && typeof result.then === 'function') await result;
      return true;
    } catch {
      // Older browsers reject the options object; retry bare.
      try {
        element.requestPointerLock();
        return true;
      } catch {
        return false;
      }
    }
  }

  /** Release the mouse captured by `requestPointerLock()`. */
  exitPointerLock() {
    if (document.pointerLockElement) document.exitPointerLock?.();
    return this;
  }

  /** @returns {boolean} whether this host's container owns the mouse. */
  isPointerLocked() {
    return document.pointerLockElement === this.container;
  }

  /**
   * Resize the orthographic view without rebuilding the camera.
   *
   * A 2D game zooms out as players separate, so this has to be cheap
   * enough to call every frame.
   *
   * @param {number} frustumHeight world-space metres visible vertically
   */
  setFrustumHeight(frustumHeight) {
    const height = Math.max(0.01, Number(frustumHeight) || 0);
    if (height === this.options.frustumHeight) return this;
    this.options.frustumHeight = height;
    if (this.camera?.isOrthographicCamera) this.#applyViewport();
    return this;
  }

  detachControls() {
    if (this.controls && typeof this.controls.dispose === 'function') {
      this.controls.dispose();
    }
    this.controls = null;
    return this;
  }

  /**
   * Configure background, environment map, and tone mapping.
   *
   * Pass `preset` to generate image-based lighting procedurally; pass
   * `environmentTexture` to supply an imported `.hdr` instead. A game
   * that does neither gets no reflections, and its PBR materials will
   * look flat however many lights it adds.
   *
   * An imported equirectangular image can serve either role, and the two
   * are independent: `environmentTexture` is what surfaces reflect,
   * `backgroundTexture` is what the player sees behind the scene. Using
   * one HDRI for both is the usual case and is what
   * `A3GameAssetLibrary.applyEnvironment()` does.
   *
   * @param {{background?: number | string,
   *          preset?: 'room' | 'sky' | 'gradient' | 'none',
   *          environmentTexture?: THREE.Texture,
   *          backgroundTexture?: THREE.Texture,
   *          environmentIntensity?: number,
   *          environmentRotationDegrees?: number,
   *          backgroundIntensity?: number,
   *          backgroundBlurriness?: number,
   *          backgroundRotationDegrees?: number,
   *          showSky?: boolean,
   *          sunPosition?: {x: number, y: number, z: number},
   *          turbidity?: number, rayleigh?: number,
   *          sky?: object,
   *          toneMapping?: keyof typeof TONE_MAPPINGS,
   *          toneMappingExposure?: number}} options
   */
  setWind(config, immediate = false) {
    this.wind.set(config, immediate);
    return this;
  }

  setEnvironment(options = {}) {
    if (!this.scene) throw new Error('init() must run before setEnvironment()');
    if (options.wind) this.setWind(options.wind);
    let sunChanged = false;
    if (options.sunPosition) {
      const sun = new THREE.Vector3(
        Number(options.sunPosition.x ?? 0), Number(options.sunPosition.y ?? 1), Number(options.sunPosition.z ?? 0),
      );
      if (!sun.toArray().every(Number.isFinite) || sun.lengthSq() < 1e-12) {
        throw new RangeError('sunPosition must be a finite non-zero direction');
      }
      sun.normalize();
      sunChanged = this.sunDirection.distanceToSquared(sun) > 1e-12;
      this.sunDirection.copy(sun);
      this.sunConfigured = true;
    }
    if (options.preset !== undefined) {
      this.environmentPresetOptions = { ...options, sunPosition: this.sunDirection.clone() };
      this.#applyEnvironmentPreset(this.environmentPresetOptions);
      this.sunConfigured = true;
    } else if (sunChanged && this.environmentPresetOptions) {
      this.environmentPresetOptions.sunPosition = this.sunDirection.clone();
      if (!options.environmentTexture) this.refreshEnvironment();
    }
    if (this.sunConfigured) this.#syncSun();
    if (options.background !== undefined) {
      this.scene.background =
        options.background === null
          ? null
          : new THREE.Color(options.background);
    }
    if (options.environmentTexture) {
      options.environmentTexture.mapping =
        THREE.EquirectangularReflectionMapping;
      this.#releaseGeneratedEnvironment();
      this.scene.environment = options.environmentTexture;
    }
    if (options.backgroundTexture) {
      // A photograph is a *better* sky than any shader for a still
      // horizon, and it is the one thing the Sky addon cannot produce.
      // The mapping has to be set here: a texture loaded as a plain
      // image defaults to UV mapping and would render as a stretched
      // smear across the backdrop.
      options.backgroundTexture.mapping =
        THREE.EquirectangularReflectionMapping;
      this.#removeSkyRoot();
      this.scene.background = options.backgroundTexture;
    }
    if (options.environmentIntensity !== undefined) {
      this.scene.environmentIntensity = Number(options.environmentIntensity);
    }
    if (options.backgroundIntensity !== undefined) {
      this.scene.backgroundIntensity = Number(options.backgroundIntensity);
    }
    if (options.backgroundBlurriness !== undefined) {
      // Blurring the backdrop while leaving the environment map sharp is
      // how a game keeps crisp reflections without the player reading the
      // JPEG artefacts of a 1k sky.
      this.scene.backgroundBlurriness = Number(options.backgroundBlurriness);
    }
    if (options.backgroundRotationDegrees !== undefined) {
      this.scene.backgroundRotation.set(
        0,
        THREE.MathUtils.degToRad(Number(options.backgroundRotationDegrees)),
        0,
      );
    }
    if (options.environmentRotationDegrees !== undefined) {
      this.scene.environmentRotation.set(
        0,
        THREE.MathUtils.degToRad(Number(options.environmentRotationDegrees)),
        0,
      );
    }
    if (options.toneMapping && Object.hasOwn(TONE_MAPPINGS, options.toneMapping)) {
      this.options.toneMapping = options.toneMapping;
      if (this.renderer) this.renderer.toneMapping = TONE_MAPPINGS[options.toneMapping];
    }
    if (options.toneMappingExposure !== undefined) {
      this.options.toneMappingExposure = Number(options.toneMappingExposure);
      if (this.renderer) this.renderer.toneMappingExposure = this.options.toneMappingExposure;
    }
    return this;
  }

  /**
   * Unit vector pointing at the sun, as the installed sky painted it.
   *
   * @returns {THREE.Vector3} a copy, so callers cannot corrupt it
   */
  getSunDirection() {
    return this.sunDirection.clone();
  }

  /**
   * World position for a `DirectionalLight` that matches the visible sun.
   *
   * @param {number} [distance] metres from the origin
   * @returns {THREE.Vector3}
   */
  getSunPosition(distance = 100) {
    return this.sunDirection.clone().multiplyScalar(Number(distance) || 1);
  }

  /**
   * Build the environment map for a preset and install it.
   *
   * `PMREMGenerator` is used once at boot and then disposed: it holds
   * render targets and shader programs that are worthless afterwards.
   * The resulting texture is owned by this host so `dispose()` can free
   * it — nothing else in the scene graph references it.
   */
  #applyEnvironmentPreset(options, refreshOnly = false) {
    const preset = String(options.preset ?? 'none').toLowerCase();
    const cloudTime = this.skyDome?.userData?.uniforms?.uTime?.value ?? 0;
    if (preset === A3GameEnvironmentPreset.NONE) {
      this.#releaseGeneratedEnvironment();
      this.#removeSkyRoot();
      this.scene.environment = null;
      this.environmentPresetOptions = null;
      return;
    }
    if (!this.renderer) {
      throw new Error('init() must run before an environment preset is set');
    }

    const generator = new THREE.PMREMGenerator(this.renderer);
    let source = null;
    let sky = null;
    const paintsSky =
      preset === A3GameEnvironmentPreset.SKY ||
      preset === A3GameEnvironmentPreset.GRADIENT;
    try {
      if (preset === A3GameEnvironmentPreset.SKY) {
        sky = new Sky();
        sky.scale.setScalar(this.options.far * 0.9);
        const sun = options.sunPosition ?? { x: 0.4, y: 0.22, z: -1 };
        const direction = new THREE.Vector3(sun.x, sun.y, sun.z).normalize();
        this.sunDirection.copy(direction);
        sky.material.uniforms.sunPosition.value.copy(direction);
        sky.material.uniforms.turbidity.value = Number(options.turbidity ?? 6);
        sky.material.uniforms.rayleigh.value = Number(options.rayleigh ?? 2);
        sky.material.uniforms.mieCoefficient.value = Number(
          options.mieCoefficient ?? 0.005,
        );
        sky.material.uniforms.mieDirectionalG.value = Number(
          options.mieDirectionalG ?? 0.8,
        );
        source = new THREE.Scene();
        source.add(sky);
      } else if (preset === A3GameEnvironmentPreset.GRADIENT) {
        // The convolved copy has to be geometrically the same dome as the
        // visible one, or reflections disagree with the backdrop — the
        // subtle wrongness that makes a scene feel composited. It is built
        // from the same factory with the same options, at a radius the
        // generator's unit camera can see.
        const dome = createSkyGradient({
          ...(options.sky ?? {}),
          radius: 50,
          sunDirection: options.sunPosition ?? this.sunDirection,
        });
        // `depthTest: false` is right for a backdrop and wrong for a
        // PMREM source, where it lets the far side of the sphere win.
        dome.material.depthTest = true;
        dome.material.depthWrite = true;
        dome.material.toneMapped = false;
        dome.userData.uniforms.uTime.value = cloudTime;
        dome.userData.update(0, this.wind);
        dome.onBeforeRender = () => {};
        source = new THREE.Scene();
        source.add(dome);
      } else if (preset === A3GameEnvironmentPreset.ROOM) {
        source = new RoomEnvironment();
      } else {
        throw new Error(
          `Unknown environment preset ${String(options.preset)}; expected ` +
            'room, sky, gradient, or none',
        );
      }
      const target = generator.fromScene(source, 0.04);
      const backgroundWasGenerated = this.scene.background === this.generatedEnvironment;
      this.#releaseGeneratedEnvironment();
      this.generatedEnvironmentTarget = target;
      this.generatedEnvironment = target.texture;
      this.scene.environment = target.texture;
      this.environmentAge = 0;
      this.scene.environmentRotation.set(0, 0, 0);
      if (refreshOnly) {
        if (backgroundWasGenerated) this.scene.background = target.texture;
      } else if (paintsSky && options.showSky !== false) {
        if (preset === A3GameEnvironmentPreset.GRADIENT) {
          this.#installSkyGradient(options);
          this.skyDome.userData.uniforms.uTime.value = cloudTime;
        } else {
          this.#installSkyDome(options);
        }
      } else {
        this.#removeSkyRoot();
        if (options.background === undefined) this.scene.background = target.texture;
      }
    } finally {
      // `RoomEnvironment` is a Scene of meshes; the Sky holds a shader
      // material. Both are throwaway sources for the convolution.
      source?.traverse?.((child) => {
        child.geometry?.dispose?.();
        const materials = Array.isArray(child.material)
          ? child.material
          : child.material
            ? [child.material]
            : [];
        for (const material of materials) material.dispose?.();
      });
      generator.dispose();
    }
  }

  /** Add the visible stylised sky dome, and remember it so it can tick. */
  #installSkyGradient(options) {
    this.#removeSkyRoot();
    const dome = createSkyGradient({
      ...(options.sky ?? {}),
      // Sized from the far plane so it is always beyond the scene, and
      // scaled down slightly so a game that raises `far` later still has
      // headroom before the dome starts clipping.
      radius: Number(options.sky?.radius ?? this.options.far * 0.92),
      sunDirection: options.sunPosition ?? this.sunDirection,
    });
    if (options.sunPosition) {
      this.sunDirection
        .set(
          Number(options.sunPosition.x ?? 0),
          Number(options.sunPosition.y ?? 1),
          Number(options.sunPosition.z ?? 0),
        )
        .normalize();
    }
    dome.userData.setSunDirection(this.sunDirection);
    this.skyDome = dome;
    this.add(dome, 'sky');
    // A shader dome and a `scene.background` would both draw; the dome
    // wins because it has clouds.
    this.scene.background = null;
    return dome;
  }

  /** Drop whatever sky object is installed, if any. */
  #removeSkyRoot() {
    const previous = this.namedRoots.get('sky');
    if (previous) {
      disposeObject3D(previous);
      this.namedRoots.delete('sky');
    }
    this.skyDome = null;
  }

  /** Add the visible sky dome under a dedicated root. */
  #installSkyDome(options) {
    this.#removeSkyRoot();
    const sky = new Sky();
    sky.scale.setScalar(this.options.far * 0.9);
    const sun = options.sunPosition ?? { x: 0.4, y: 0.22, z: -1 };
    this.sunDirection.set(sun.x, sun.y, sun.z).normalize();
    sky.material.uniforms.sunPosition.value.copy(this.sunDirection);
    sky.material.uniforms.turbidity.value = Number(options.turbidity ?? 6);
    sky.material.uniforms.rayleigh.value = Number(options.rayleigh ?? 2);
    sky.material.uniforms.mieCoefficient.value = Number(options.mieCoefficient ?? 0.005);
    sky.material.uniforms.mieDirectionalG.value = Number(options.mieDirectionalG ?? 0.8);
    sky.name = 'A3GameSky';
    this.skyDome = sky;
    this.add(sky, 'sky');
    this.scene.background = null;
    return sky;
  }

  #releaseGeneratedEnvironment() {
    if (this.generatedEnvironment) {
      if (this.scene?.background === this.generatedEnvironment) this.scene.background = null;
      if (this.generatedEnvironmentTarget) this.generatedEnvironmentTarget.dispose();
      else this.generatedEnvironment.dispose();
      this.generatedEnvironmentTarget = null;
      this.generatedEnvironment = null;
    }
  }

  /**
   * Configure scene fog.
   *
   * @param {{type?: 'none' | 'Fog' | 'FogExp2', color?: number | string,
   *          near?: number, far?: number, density?: number}} options
   */
  setFog(options = {}) {
    if (!this.scene) throw new Error('init() must run before setFog()');
    const type = options.type ?? 'none';
    if (type === 'none') {
      this.scene.fog = null;
      return this;
    }
    const color = new THREE.Color(options.color ?? 0x101014);
    this.scene.fog =
      type === 'FogExp2'
        ? new THREE.FogExp2(color, options.density ?? 0.001)
        : new THREE.Fog(color, options.near ?? 1, options.far ?? 200);
    return this;
  }

  /**
   * Pick the first object hit under a pointer event.
   *
   * @param {PointerEvent | MouseEvent} event
   * @param {THREE.Object3D[]} [targets] defaults to the whole scene
   * @returns {THREE.Intersection | null}
   */
  raycastFromPointer(event, targets) {
    if (!this.renderer || !this.camera) return null;
    const rect = this.renderer.domElement.getBoundingClientRect();
    this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const list = this.raycaster.intersectObjects(
      targets ?? this.scene.children,
      true,
    );
    return list.length > 0 ? list[0] : null;
  }

  /**
   * Cast a ray from an origin along a direction.
   *
   * @param {THREE.Vector3} origin
   * @param {THREE.Vector3} direction normalized
   * @param {{targets?: THREE.Object3D[], far?: number, near?: number}} [options]
   * @returns {THREE.Intersection[]}
   */
  raycast(origin, direction, options = {}) {
    this.raycaster.set(origin, direction.clone().normalize());
    this.raycaster.near = options.near ?? 0;
    this.raycaster.far = options.far ?? Infinity;
    return this.raycaster.intersectObjects(
      options.targets ?? this.scene.children,
      true,
    );
  }

  /** @returns {string} a PNG data URL of the current frame. */
  captureFrame() {
    this.#renderFrame(0);
    return this.renderer?.domElement.toDataURL('image/png') ?? '';
  }

  /** @returns {object} render statistics for observation and evidence. */
  getStats() {
    const info = this.renderer?.info;
    return {
      frameCount: this.frameCount,
      elapsedSeconds: Number(this.elapsedSeconds.toFixed(3)),
      simulationSteps: this.lastSubSteps,
      interpolationAlpha: this.interpolationAlpha,
      droppedSeconds: this.droppedSeconds,
      drawCalls: info?.render?.calls ?? 0,
      triangles: info?.render?.triangles ?? 0,
      geometries: info?.memory?.geometries ?? 0,
      textures: info?.memory?.textures ?? 0,
      programs: info?.programs?.length ?? 0,
      pixelRatio: this.renderer?.getPixelRatio() ?? 0,
      size: this.#size(),
    };
  }

  /** Release renderer, scene, and listener resources. */
  dispose() {
    this.stop();
    this.detachControls();
    this.tickListeners.clear();
    this.renderListeners.clear();
    this.interpolatedObjects.clear();
    this.sunLights.clear();
    this.environmentPresetOptions = null;
    this.resizeListeners.clear();
    if (this.onWindowResize) {
      window.removeEventListener('resize', this.onWindowResize);
      this.onWindowResize = null;
    }
    this.resizeObserver?.disconnect();
    this.resizeObserver = null;
    this.#releaseGeneratedEnvironment();
    this.skyDome = null;
    if (this.scene) disposeObject3D(this.scene);
    this.scene = null;
    this.namedRoots.clear();
    if (this.renderer) {
      this.renderer.dispose();
      this.renderer.domElement.remove();
      this.renderer = null;
    }
    this.camera = null;
  }

  #emitTick(delta) {
    // The sky is host-owned scenery, so the host animates it. A game that
    // had to remember to advance the cloud clock itself would forget, and
    // a frozen cloud deck is worse than none.
    this.wind.update(delta);
    this.skyDome?.userData?.update?.(delta, this.wind);
    for (const listener of this.tickListeners) {
      listener(delta, this.elapsedSeconds);
    }
  }

  #size() {
    const width = this.container?.clientWidth || globalThis.window?.innerWidth || 1;
    const height = this.container?.clientHeight || globalThis.window?.innerHeight || 1;
    return { width, height, clientWidth: width, clientHeight: height };
  }

  #createCamera(type, aspect) {
    if (type === 'orthographic') {
      const halfHeight = this.options.frustumHeight / 2;
      const halfWidth = halfHeight * aspect;
      return new THREE.OrthographicCamera(
        -halfWidth,
        halfWidth,
        halfHeight,
        -halfHeight,
        this.options.near,
        this.options.far,
      );
    }
    return new THREE.PerspectiveCamera(
      this.options.fov,
      aspect,
      this.options.near,
      this.options.far,
    );
  }

  #swapCamera(type) {
    if (!this.scene) {
      throw new Error('init() must run before switching cameras');
    }
    const { width, height } = this.#size();
    const previous = this.camera;
    const camera = this.#createCamera(type, width / Math.max(1, height));
    camera.name = previous?.name ?? 'A3GameCamera';
    if (previous) {
      camera.position.copy(previous.position);
      camera.quaternion.copy(previous.quaternion);
      const parent = previous.parent;
      previous.removeFromParent?.();
      if (parent) parent.add(camera);
    }
    this.camera = camera;
    if (this.controls?.object) this.controls.object = camera;
    this.#applyViewport();
    return camera;
  }

  #applyViewport() {
    const { width, height } = this.#size();
    if (!this.camera || !this.renderer) return { width, height };
    const aspect = width / Math.max(1, height);
    if (this.camera.isOrthographicCamera) {
      const halfHeight = this.options.frustumHeight / 2;
      const halfWidth = halfHeight * aspect;
      this.camera.left = -halfWidth;
      this.camera.right = halfWidth;
      this.camera.top = halfHeight;
      this.camera.bottom = -halfHeight;
    } else {
      this.camera.aspect = aspect;
    }
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height);
    return { width, height };
  }

  #observeResize() {
    const apply = () => {
      const size = this.#applyViewport();
      for (const listener of this.resizeListeners) {
        listener(size);
      }
    };
    this.onWindowResize = apply;
    window.addEventListener('resize', apply);
    if (typeof ResizeObserver === 'function') {
      this.resizeObserver = new ResizeObserver(apply);
      this.resizeObserver.observe(this.container);
    }
    apply();
  }
}

/**
 * Recursively dispose geometries, materials, and textures.
 *
 * three.js never frees GPU memory automatically; generated gameplay must
 * call this when removing content.
 *
 * @param {THREE.Object3D | null} root
 */
export function disposeObject3D(root) {
  if (!root) return;
  const textureKeys = [
    'map',
    'normalMap',
    'roughnessMap',
    'metalnessMap',
    'aoMap',
    'emissiveMap',
    'alphaMap',
    'displacementMap',
    'envMap',
    'lightMap',
    'clearcoatMap',
    'sheenColorMap',
  ];
  const disposed = new Set();
  const release = (resource) => {
    if (!resource || disposed.has(resource) || resource.userData?.a3AssetLibraryOwned) return;
    disposed.add(resource);
    resource.dispose?.();
  };
  root.traverse((child) => {
    child.userData?.dispose?.();
    if (child.userData?.disposed) return;
    if (child.isLight) child.dispose?.();
    release(child.geometry);
    const materials = Array.isArray(child.material)
      ? child.material
      : child.material
        ? [child.material]
        : [];
    for (const material of materials) {
      for (const key of textureKeys) release(material[key]);
      release(material);
    }
  });
  root.parent?.remove(root);
  root.clear?.();
}
