/**
 * Loads the adapter-written asset manifest and resolves artifacts into
 * three.js objects.
 *
 * Generated gameplay must reference assets by `artifact_id` or
 * `asset_id` from `/assets/manifest.json`. Hard-coded URLs break when
 * the adapter re-stages content.
 */

import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { FBXLoader } from 'three/addons/loaders/FBXLoader.js';
import { OBJLoader } from 'three/addons/loaders/OBJLoader.js';
import { STLLoader } from 'three/addons/loaders/STLLoader.js';
import { HDRLoader } from 'three/addons/loaders/HDRLoader.js';
import { clone as cloneSkinned } from 'three/addons/utils/SkeletonUtils.js';
import { createTilingTexture, orientModel, prepareModel } from './visual-kit.js';

export class A3GameAssetLibrary {
  /**
   * @param {{manifestUrl?: string, dracoDecoderPath?: string,
   *          ktx2TranscoderPath?: string,
   *          requireManifest?: boolean,
   *          renderer?: THREE.WebGLRenderer}} [options]
   */
  constructor(options = {}) {
    this.manifestUrl = options.manifestUrl ?? '/assets/manifest.json';
    this.dracoDecoderPath = options.dracoDecoderPath ?? '/draco/';
    this.ktx2TranscoderPath = options.ktx2TranscoderPath ?? '/basis/';
    this.renderer = options.renderer ?? null;
    // A procedurally built game imports no artifact, so a project may
    // legitimately have no manifest yet. Set `requireManifest: true`
    // when the game genuinely cannot run without imported content.
    this.requireManifest = Boolean(options.requireManifest);

    /** @type {object | null} */
    this.manifest = null;
    /** Whether a manifest was found and indexed. */
    this.available = false;
    /** @type {string[]} */
    this.warnings = [];
    /** @type {Map<string, object>} */
    this.byArtifactId = new Map();
    /** @type {Map<string, object[]>} */
    this.byAssetId = new Map();
    /** @type {Map<string, object[]>} */
    this.byType = new Map();
    /** @type {Map<string, Promise<object>>} */
    this.cache = new Map();
    this.resources = new Set();
    this.releasedResources = new WeakSet();
    this.instanceResources = new WeakMap();

    this.textureLoader = new THREE.TextureLoader();
    this.hdrLoader = new HDRLoader();
    this.audioLoader = new THREE.AudioLoader();
    this.fileLoaders = {
      glb: this.#createGltfLoader(),
      gltf: this.#createGltfLoader(),
      fbx: new FBXLoader(),
      obj: new OBJLoader(),
      stl: new STLLoader(),
    };
  }

  /**
   * Fetch and index the manifest.
   *
   * A missing manifest is not fatal unless `requireManifest` was set:
   * generated gameplay that builds its content from three.js primitives
   * still needs a library instance for the few artifacts it may later
   * receive. Inspect `available` and `warnings` to branch on it.
   */
  async load() {
    this.warnings = [];
    let manifest = null;
    try {
      const response = await fetch(this.manifestUrl, { cache: 'no-cache' });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      manifest = await response.json();
    } catch (error) {
      const reason =
        `Asset manifest is unavailable at ${this.manifestUrl}: ` +
        String(error?.message ?? error);
      if (this.requireManifest) throw new Error(reason);
      this.warnings.push(
        `${reason}; continuing with an empty asset library`,
      );
      this.manifest = null;
      this.available = false;
      this.byArtifactId.clear();
      this.byAssetId.clear();
      this.byType.clear();
      return this;
    }
    this.manifest = manifest;
    this.available = true;
    this.byArtifactId.clear();
    this.byAssetId.clear();
    this.byType.clear();
    for (const entry of Object.values(manifest?.assets ?? {})) {
      this.byArtifactId.set(entry.artifact_id, entry);
      const assetGroup = this.byAssetId.get(entry.asset_id) ?? [];
      assetGroup.push(entry);
      this.byAssetId.set(entry.asset_id, assetGroup);
      const typeGroup = this.byType.get(entry.type) ?? [];
      typeGroup.push(entry);
      this.byType.set(entry.type, typeGroup);
    }
    return this;
  }

  /** @returns {boolean} whether a reference resolves to a staged entry. */
  has(reference) {
    return this.findEntry(reference) !== null;
  }

  /** @returns {object} the manifest entry, or throws when unknown. */
  requireEntry(reference) {
    const entry = this.findEntry(reference);
    if (!entry) {
      throw new Error(
        `Unknown asset reference ${String(reference)}; the adapter must ` +
          'import and register it first',
      );
    }
    return entry;
  }

  /**
   * @returns {object | null}
   *
   * An **array** is a preference list: the first staged candidate wins.
   * That is what lets a game name the art it wants without knowing what
   * was actually produced — `['explorer_ranger', 'robot_expressive']`
   * uses the generated ranger when one exists, the CC0 stand-in when it
   * does not, and returns null so the caller falls back to a primitive
   * when neither has been staged. Nothing about that decision belongs in
   * gameplay code, and none of it needs a manifest to be present.
   */
  findEntry(reference) {
    if (Array.isArray(reference)) {
      for (const candidate of reference) {
        const entry = this.findEntry(candidate);
        if (entry) return entry;
      }
      return null;
    }
    const key = String(reference ?? '');
    if (this.byArtifactId.has(key)) return this.byArtifactId.get(key);
    const group = this.byAssetId.get(key);
    return group && group.length > 0 ? group[0] : null;
  }

  /** @returns {object[]} every entry of one asset type. */
  listByType(type) {
    return [...(this.byType.get(String(type)) ?? [])];
  }

  /**
   * Load an artifact once and cache the result.
   *
   * Meshes resolve to `{ object, animations, entry }`; textures resolve
   * to `{ texture, entry }`; audio resolves to `{ buffer, entry }`.
   *
   * @param {string} reference artifact_id or asset_id
   */
  async loadArtifact(reference) {
    const entry = this.requireEntry(reference);
    if (this.cache.has(entry.artifact_id)) {
      return this.cache.get(entry.artifact_id);
    }
    const promise = this.#loadEntry(entry).then((loaded) => {
      this.#own(loaded.texture);
      this.#ownObjectResources(loaded.object);
      if (loaded.object) {
        loaded.object.userData.a3Asset = {
          url: entry.url, representation: entry.representation,
          artifact_id: entry.artifact_id,
        };
      }
      return loaded;
    }).catch((error) => {
      this.cache.delete(entry.artifact_id);
      throw error;
    });
    this.cache.set(entry.artifact_id, promise);
    return promise;
  }

  /**
   * Return an independent instance of a mesh artifact.
   *
   * Skinned hierarchies are cloned with `SkeletonUtils.clone` so each
   * instance keeps its own bones and can play its own animations.
   * Materials are instance-local; geometry and original textures stay
   * shared. `object.userData.dispose()` releases local materials and binding
   * textures; shared caches remain valid until `library.dispose()`.
   *
   * @param {string} reference
   * @returns {Promise<{object: THREE.Object3D, animations: THREE.AnimationClip[], entry: object}>}
   */
  async instantiate(reference) {
    const loaded = await this.loadArtifact(reference);
    if (!loaded.object) {
      throw new Error(
        `Asset ${reference} is not an instantiable mesh artifact`,
      );
    }
    this.#ownObjectResources(loaded.object);
    let skinned = Boolean(loaded.entry?.capabilities?.skinned);
    loaded.object.traverse((child) => { skinned ||= Boolean(child.isSkinnedMesh); });
    const object = skinned
      ? cloneSkinned(loaded.object)
      : loaded.object.clone(true);
    object.name = object.name || loaded.entry.asset_id;
    object.userData.a3Asset = {
      url: loaded.entry.url,
      representation: loaded.entry.representation,
      artifact_id: loaded.entry.artifact_id,
    };
    const owner = { root: object, resources: new Set(), disposed: false };
    this.instanceResources.set(object, owner);
    object.userData.dispose = () => {
      if (owner.disposed) return;
      owner.disposed = true;
      for (const resource of owner.resources) {
        if (this.resources.has(resource)) resource.dispose();
      }
      owner.resources.clear();
    };
    const materials = new Map();
    object.traverse((child) => {
      if (!child.material) return;
      const cloneMaterial = (material) => {
        if (!material) return material;
        if (!materials.has(material)) {
          const cloned = this.#own(material.clone());
          materials.set(material, cloned);
          owner.resources.add(cloned);
        }
        return materials.get(material);
      };
      child.material = Array.isArray(child.material)
        ? child.material.map(cloneMaterial)
        : cloneMaterial(child.material);
    });
    try {
      for (const url of loaded.entry.material_bindings ?? []) {
        await this.applyMaterialBinding(object, url);
      }
    } catch (error) {
      object.userData.dispose();
      throw error;
    }
    return {
      object,
      animations: (loaded.animations ?? []).map((clip) => clip.clone()),
      entry: loaded.entry,
    };
  }

  /**
   * Instantiate a model if it was staged, otherwise return `null`.
   *
   * A generated game is written to run with an empty manifest — a
   * mechanic task usually precedes its art — so "use the good model when
   * there is one" is the normal case rather than an edge case. The
   * returned object is scene-ready: rotated to face the runtime forward
   * axis, normalised to `height` if given, and shadow-enabled, which
   * `GLTFLoader` never does on its own.
   *
   * A staged-but-unloadable artifact records a warning and yields `null`
   * rather than throwing, because a broken asset must not cost the player
   * the whole game.
   *
   * @param {string | string[]} reference artifact_id, asset_id, or a
   *        preference list of either
   * @param {{height?: number, ground?: boolean, envMapIntensity?: number,
   *          castShadow?: boolean, receiveShadow?: boolean,
   *          frustumCulled?: boolean, forwardAxis?: string,
   *          yawOffsetDegrees?: number, orient?: boolean}} [options]
   * @returns {Promise<{object: THREE.Object3D,
   *                    animations: THREE.AnimationClip[],
   *                    entry: object,
   *                    orientation: object} | null>}
   */
  async tryInstantiate(reference, options = {}) {
    if (!this.has(reference)) return null;
    try {
      const loaded = await this.instantiate(reference);
      const prepared = this.#prepare(loaded, options);
      return prepared;
    } catch (error) {
      this.warnings.push(
        `Staged asset ${String(reference)} failed to load: ` +
          String(error?.message ?? error),
      );
      return null;
    }
  }

  /**
   * Instantiate a staged model, or build a procedural stand-in.
   *
   * The counterpart to `tryInstantiate` for content whose visual is
   * created from scratch rather than swapped in afterwards. Branching on
   * `has()` at every call site duplicates this logic and usually forgets
   * the shadow flags.
   *
   * @param {string} reference artifact_id or asset_id
   * @param {() => THREE.Object3D} fallback builds the primitive version
   * @param {object} [options] as `tryInstantiate`
   * @returns {Promise<{object: THREE.Object3D,
   *                    animations: THREE.AnimationClip[],
   *                    source: 'asset' | 'fallback',
   *                    entry: object | null}>}
   */
  async instantiateOrBuild(reference, fallback, options = {}) {
    if (typeof fallback !== 'function') {
      throw new TypeError(
        'instantiateOrBuild requires a fallback factory, so a game ' +
          'stays runnable before its art is imported',
      );
    }
    const loaded = await this.tryInstantiate(reference, options);
    if (loaded) return { ...loaded, source: 'asset' };

    // A procedural body is authored by the game, so it already faces the
    // runtime forward axis: no orientation correction applies to it.
    const object = fallback();
    if (!object?.isObject3D) {
      throw new TypeError('The fallback factory must return an Object3D');
    }
    return {
      object,
      animations: [],
      source: 'fallback',
      entry: null,
      orientation: {},
    };
  }

  /**
   * Apply an adapter-written PBR material binding to an object subtree.
   * Targets identify staged asset URLs, not mesh/material names. An
   * explicit target list must match at least one library-loaded mesh.
   * Replacement materials and textures are owned by this library; the
   * caller retains ownership of any external materials being replaced.
   *
   * @param {THREE.Object3D} object
   * @param {string} bindingUrl for example `/assets/bindings/<id>.json`
   */
  async applyMaterialBinding(object, bindingUrl) {
    const response = await fetch(bindingUrl, { cache: 'no-cache' });
    if (!response.ok) {
      throw new Error(
        `Material binding is unavailable at ${bindingUrl}: ` +
          `HTTP ${response.status}`,
      );
    }
    const binding = await response.json();
    const types = {
      MeshStandardMaterial: THREE.MeshStandardMaterial,
      MeshPhysicalMaterial: THREE.MeshPhysicalMaterial,
      MeshBasicMaterial: THREE.MeshBasicMaterial,
      MeshLambertMaterial: THREE.MeshLambertMaterial,
      MeshPhongMaterial: THREE.MeshPhongMaterial,
      MeshMatcapMaterial: THREE.MeshMatcapMaterial,
      MeshToonMaterial: THREE.MeshToonMaterial,
    };
    const MaterialType = types[binding.material_type];
    if (binding.material_type && !Object.hasOwn(types, binding.material_type)) {
      throw new Error(`Unsupported binding material_type: ${binding.material_type}`);
    }
    if (binding.targets !== undefined && !Array.isArray(binding.targets)) {
      throw new TypeError('Material binding targets must be asset URL arrays');
    }
    const meshes = [];
    object.traverse((child) => {
      if (!child.isMesh || !child.material) return;
      let source = child;
      while (source && !source.userData?.a3Asset) source = source.parent;
      const asset = source?.userData.a3Asset;
      if (binding.targets && !binding.targets.includes(asset?.url)) return;
      const gltf = /gltf|glb/i.test(asset?.representation ?? '') ||
        /\.(glb|gltf)(?:[?#]|$)/i.test(asset?.url ?? '');
      const owner = this.instanceResources.get(source);
      if (owner?.disposed) throw new Error('Cannot bind a disposed asset instance');
      meshes.push({ child, flipY: !gltf, owner });
    });
    if (!meshes.length) {
      throw new Error(`Material binding ${bindingUrl} matched no target asset meshes`);
    }
    const allocated = new Set();
    const textureSets = new Map();
    const replacements = [];
    const number = (value, key) => {
      if (value === null || value === '' || typeof value === 'boolean' ||
          !['number', 'string'].includes(typeof value) || !Number.isFinite(Number(value))) {
        throw new TypeError(`Invalid material number for ${key}`);
      }
      return Number(value);
    };
    try {
      for (const { child, flipY, owner } of meshes) {
        if (!textureSets.has(owner)) textureSets.set(owner, new Map());
        const instanceTextures = textureSets.get(owner);
        if (!instanceTextures.has(flipY)) {
          const textures = {};
          for (const [slot, url] of Object.entries(binding.textures ?? {})) {
            const texture = await this.textureLoader.loadAsync(url);
            allocated.add(texture);
            texture.colorSpace = ['map', 'emissiveMap', 'sheenColorMap', 'specularColorMap'].includes(slot)
              ? THREE.SRGBColorSpace : THREE.NoColorSpace;
            texture.flipY = flipY;
            texture.needsUpdate = true;
            textures[slot] = texture;
          }
          instanceTextures.set(flipY, textures);
        }
        const materials = [child.material].flat().map((original) => {
          if (!original) return original;
          const material = MaterialType && original.type !== binding.material_type
            ? new MaterialType() : original.clone();
          allocated.add(material);
          if (material.type !== original.type) {
            THREE.Material.prototype.copy.call(material, original);
            for (const key of Object.keys(material)) {
              if (!(key in original) || key.startsWith('is') ||
                  ['id', 'uuid', 'type', 'defines', 'version', 'userData'].includes(key)) continue;
              const value = original[key];
              if (material[key]?.isColor || material[key]?.isVector2) material[key].copy(value);
              else if (value === null || value?.isTexture || typeof value !== 'object') material[key] = value;
            }
          }
          for (const [key, texture] of Object.entries(instanceTextures.get(flipY))) {
            if (!(key in material)) throw new TypeError(`${material.type} does not support ${key}`);
            material[key] = texture;
          }
          for (const [key, value] of Object.entries({ ...binding.scalars, ...binding.vectors })) {
            if (material[key]?.isVector2) {
              const pair = Array.isArray(value) ? value
                : value && typeof value === 'object' ? [value.x, value.y] : [value, value];
              if (pair.length !== 2) throw new TypeError(`Invalid material vector for ${key}`);
              material[key].set(number(pair[0], key), number(pair[1], key));
            } else {
              if (typeof material[key] !== 'number') throw new TypeError(`${material.type} does not support scalar ${key}`);
              material[key] = number(value, key);
            }
          }
          for (const [key, value] of Object.entries(binding.colors ?? {})) {
            if (!material[key]?.isColor) throw new TypeError(`${material.type} does not support color ${key}`);
            if (Array.isArray(value)) {
              if (value.length !== 3) throw new TypeError(`Invalid material color for ${key}`);
              material[key].setRGB(...value.map((v) => number(v, key)), THREE.SRGBColorSpace);
            } else material[key].set(value);
          }
          for (const [key, value] of Object.entries(binding.flags ?? {})) {
            if (!['transparent', 'side', 'flatShading', 'wireframe', 'depthWrite', 'vertexColors'].includes(key) || !(key in material)) {
              throw new TypeError(`${material.type} does not support flag ${key}`);
            }
            material[key] = key === 'side' && typeof value === 'string'
              ? ({ FrontSide: THREE.FrontSide, BackSide: THREE.BackSide, DoubleSide: THREE.DoubleSide })[value]
              : value;
            if (key === 'side' ? ![0, 1, 2].includes(material[key]) : typeof value !== 'boolean') {
              throw new TypeError(`Invalid material flag ${key}`);
            }
          }
          material.needsUpdate = true;
          return material;
        });
        replacements.push({ child, owner, material: Array.isArray(child.material) ? materials : materials[0] });
      }
      if (meshes.some(({ owner }) => owner?.disposed)) {
        throw new Error('Asset instance was disposed while binding loaded');
      }
    } catch (error) {
      for (const resource of allocated) resource.dispose();
      throw error;
    }
    for (const resource of allocated) this.#own(resource);
    const owners = new Set();
    for (const { child, material, owner } of replacements) {
      child.material = material;
      if (!owner) continue;
      owners.add(owner);
      for (const item of [material].flat()) {
        if (!item) continue;
        owner.resources.add(item);
        for (const value of Object.values(item)) {
          if (value?.isTexture && allocated.has(value)) owner.resources.add(value);
        }
      }
    }
    for (const owner of owners) this.#releaseUnusedInstanceResources(owner);
    return binding;
  }

  /**
   * Load a staged texture, or return `null` when it was never imported.
   *
   * The texture counterpart of `tryInstantiate`, and it exists for the
   * same reason: a game must run before its art arrives. It also applies
   * the three settings that are wrong by default on any tiling surface
   * map — clamped wrapping, anisotropy 1, and the colour space.
   *
   * @param {string | string[]} reference
   * @param {{repeat?: number | number[], srgb?: boolean,
   *          anisotropy?: number, colorSpace?: string,
   *          rotation?: number}} [options]
   * @returns {Promise<THREE.Texture | null>}
   */
  async tryLoadTexture(reference, options = {}) {
    if (!this.has(reference)) return null;
    try {
      const loaded = await this.loadArtifact(reference);
      if (!loaded?.texture) return null;
      // Cloned so two surfaces can tile the same file at different
      // densities; the image data itself stays shared.
      const texture = options.clone === false
        ? loaded.texture
        : loaded.texture.clone();
      texture.needsUpdate = true;
      this.#own(texture);
      return createTilingTexture(texture, {
        renderer: this.renderer,
        ...options,
        ...(['hdr', 'exr'].includes(loaded.entry?.representation)
          ? { colorSpace: loaded.texture.colorSpace } : {}),
      });
    } catch (error) {
      this.warnings.push(
        `Staged texture ${String(reference)} failed to load: ` +
          String(error?.message ?? error),
      );
      return null;
    }
  }

  /**
   * Install a staged equirectangular image as the scene's lighting.
   *
   * This is the highest-value single call in the framework. An imported
   * HDRI supplies, in one texture, every highlight and every bounce that
   * a procedural preset can only approximate — and unlike the presets it
   * also carries the *colour* of a real time of day, which is most of
   * what makes a scene read as a place rather than a render.
   *
   * The manifest entry may carry a `sun` hint (`{elevation, azimuth}` in
   * degrees), written by `operators/gen_3d_scene/funcs/scene_assets.py`
   * from the HDRI's own sun position. When it does, the host's sun
   * direction is aligned to it, so a `DirectionalLight` placed with
   * `host.getSunPosition()` casts shadows in the direction the reflected
   * sky says the light comes from. Getting that wrong is the most
   * common lighting bug in an imported-HDRI scene.
   *
   * @param {object} host an `A3GameRuntimeHost`
   * @param {string | string[]} reference environment artifact or asset id
   * @param {{background?: boolean, backgroundReference?: string | string[],
   *          environmentIntensity?: number, backgroundIntensity?: number,
   *          backgroundBlurriness?: number,
   *          rotationDegrees?: number}} [options]
   * @returns {Promise<{entry: object, texture: THREE.Texture,
   *                    sunDirection: THREE.Vector3 | null} | null>}
   */
  async applyEnvironment(host, reference, options = {}) {
    if (!host?.setEnvironment) {
      throw new TypeError('applyEnvironment needs an A3GameRuntimeHost');
    }
    if (!this.has(reference)) return null;
    let loaded;
    try {
      loaded = await this.loadArtifact(reference);
    } catch (error) {
      this.warnings.push(
        `Staged environment ${String(reference)} failed to load: ` +
          String(error?.message ?? error),
      );
      return null;
    }
    if (!loaded?.texture) return null;
    loaded.texture.mapping = THREE.EquirectangularReflectionMapping;

    const settings = {
      environmentTexture: loaded.texture,
      environmentIntensity: options.environmentIntensity ?? 1,
    };
    if (options.background !== false) {
      // A separate, usually cheaper, image may back the sky — a 1k HDRI
      // is the right size for lighting and visibly soft as a backdrop.
      const backdrop = options.backgroundReference
        ? await this.tryLoadTexture(options.backgroundReference, {
            clone: false,
          })
        : loaded.texture;
      if (backdrop) {
        backdrop.mapping = THREE.EquirectangularReflectionMapping;
        settings.backgroundTexture = backdrop;
      }
      if (options.backgroundIntensity !== undefined) {
        settings.backgroundIntensity = options.backgroundIntensity;
      }
      if (options.backgroundBlurriness !== undefined) {
        settings.backgroundBlurriness = options.backgroundBlurriness;
      }
    }
    const rotation = Number(options.rotationDegrees ?? 0);
    if (!Number.isFinite(rotation)) throw new TypeError('Environment rotation must be finite');
    settings.environmentRotationDegrees = rotation;
    settings.backgroundRotationDegrees = rotation;

    let sunDirection = null;
    const sun = loaded.entry?.sun;
    if (sun && sun.elevation != null &&
        Number.isFinite(Number(sun.elevation)) && Number.isFinite(Number(sun.azimuth ?? 180))) {
      const elevation = THREE.MathUtils.degToRad(Number(sun.elevation));
      const azimuth = THREE.MathUtils.degToRad(Number(sun.azimuth ?? 180) + rotation);
      sunDirection = new THREE.Vector3(
        Math.cos(elevation) * Math.sin(azimuth),
        Math.sin(elevation),
        Math.cos(elevation) * Math.cos(azimuth),
      ).normalize();
      settings.sunPosition = {
        x: sunDirection.x,
        y: sunDirection.y,
        z: sunDirection.z,
      };
    }
    host.setEnvironment(settings);
    return { entry: loaded.entry, texture: loaded.texture, sunDirection };
  }

  #own(resource) {
    if (!resource?.dispose || this.resources.has(resource) || this.releasedResources.has(resource)) return resource;
    resource.userData ??= {};
    resource.userData.a3AssetLibraryOwned = true;
    this.resources.add(resource);
    const released = () => {
      this.resources.delete(resource);
      this.releasedResources.add(resource);
      resource.removeEventListener?.('dispose', released);
    };
    resource.addEventListener?.('dispose', released);
    return resource;
  }

  #releaseUnusedInstanceResources(owner) {
    const used = new Set();
    owner.root.traverse((child) => {
      for (const material of [child.material].flat()) {
        if (!material) continue;
        used.add(material);
        for (const value of Object.values(material)) {
          if (value?.isTexture) used.add(value);
        }
      }
    });
    for (const resource of owner.resources) {
      if (used.has(resource)) continue;
      if (this.resources.has(resource)) resource.dispose();
      owner.resources.delete(resource);
    }
  }

  #ownObjectResources(object) {
    object?.traverse?.((child) => {
      this.#own(child.geometry);
      for (const material of [child.material].flat()) {
        if (!material) continue;
        this.#own(material);
        for (const value of Object.values(material)) {
          if (value?.isTexture) this.#own(value);
        }
      }
    });
  }

  /** Library-created materials and shared geometry/textures live until disposal. */
  async dispose() {
    for (const promise of this.cache.values()) {
      const loaded = await promise.catch(() => null);
      this.#own(loaded?.texture);
      this.#ownObjectResources(loaded?.object);
    }
    for (const resource of this.resources) resource.dispose();
    this.resources.clear();
    this.cache.clear();
    for (const loader of [this.fileLoaders.glb, this.fileLoaders.gltf]) {
      loader?.dracoLoader?.dispose?.();
      loader?.ktx2Loader?.dispose?.();
    }
  }

  /**
   * Turn a loaded artifact into scene-ready content.
   *
   * Two facts the adapter recorded are applied here, because neither can
   * be recovered from the glTF at runtime:
   *
   * - **facing**, which is rotated into the runtime convention;
   * - **scale**, when the reviewer recorded a real-world height and the
   *   caller did not override it.
   *
   * An inner correction Group owns orientation, scale and grounding;
   * the outer Group is free for gameplay/world transforms. The authored
   * model remains untouched below both, so root animation tracks and
   * skeletons retain their original coordinate system.
   *
   * With no orientation, height or grounding requested, no wrappers are
   * introduced and the authored transform is preserved.
   */
  #prepare(loaded, options = {}) {
    const orientation = { ...(loaded.entry?.orientation ?? {}) };
    const forwardAxis = options.forwardAxis ?? orientation.forward_axis ?? '';
    const yaw = Number(
      options.yawOffsetDegrees ?? orientation.yaw_offset_degrees ?? 0,
    );
    const pitch = Number(
      options.pitchOffsetDegrees ?? orientation.pitch_offset_degrees ?? 0,
    );
    const roll = Number(
      options.rollOffsetDegrees ?? orientation.roll_offset_degrees ?? 0,
    );
    const rotates =
      options.orient !== false &&
      (Boolean(forwardAxis) || yaw !== 0 || pitch !== 0 || roll !== 0);

    const hint = Number(orientation.scale_hint_metres);
    const height =
      options.height ?? (Number.isFinite(hint) && hint > 0 ? hint : undefined);

    let object = loaded.object;
    let correction = object;
    if (rotates || height !== undefined || options.ground) {
      correction = new THREE.Group();
      correction.name = `${object.name || loaded.entry?.asset_id || 'asset'}_correction`;
      correction.add(object);
      if (rotates) {
        orientModel(correction, {
          forwardAxis,
          runtimeForwardAxis: orientation.runtime_forward_axis,
          yawOffsetDegrees: yaw,
          pitchOffsetDegrees: pitch,
          rollOffsetDegrees: roll,
        });
      }
      object = new THREE.Group();
      object.name = `${loaded.object.name || loaded.entry?.asset_id || 'asset'}_prepared`;
      object.add(correction);
    }

    prepareModel(correction, {
      ...options, height, orientation: {}, forwardAxis: '',
      yawOffsetDegrees: 0, pitchOffsetDegrees: 0, rollOffsetDegrees: 0,
    });

    if (!forwardAxis && orientation.needs_vision_check) {
      this.warnings.push(
        `Asset ${String(loaded.entry?.asset_id ?? '')} has no verified ` +
          'facing axis, so it is placed exactly as authored. Render it ' +
          'with three.preview.orientation_report and record the answer ' +
          'with three.assets.set_orientation if it faces the wrong way.',
      );
    }
    return { ...loaded, object, model: loaded.object, orientation };
  }

  #createGltfLoader() {    const loader = new GLTFLoader();
    const draco = new DRACOLoader();
    draco.setDecoderPath(this.dracoDecoderPath);
    loader.setDRACOLoader(draco);
    if (this.renderer) {
      const ktx2 = new KTX2Loader()
        .setTranscoderPath(this.ktx2TranscoderPath)
        .detectSupport(this.renderer);
      loader.setKTX2Loader(ktx2);
    }
    return loader;
  }

  async #loadEntry(entry) {
    const url = entry.url;
    const representation = String(entry.representation ?? '');
    if (['png', 'jpeg', 'webp', 'tga'].includes(representation)) {
      const texture = await this.textureLoader.loadAsync(url);
      texture.colorSpace = THREE.SRGBColorSpace;
      return { texture, entry };
    }
    if (['hdr', 'exr'].includes(representation)) {
      const texture = await this.hdrLoader.loadAsync(url);
      texture.mapping = THREE.EquirectangularReflectionMapping;
      return { texture, entry };
    }
    if (['mp3', 'ogg', 'wav', 'm4a'].includes(representation)) {
      const buffer = await this.audioLoader.loadAsync(url);
      return { buffer, entry };
    }
    if (representation === 'json') {
      const response = await fetch(url, { cache: 'no-cache' });
      return { data: await response.json(), entry };
    }

    const suffix = url.split('.').pop()?.toLowerCase() ?? '';
    const loader = this.fileLoaders[suffix];
    if (!loader) {
      throw new Error(
        `No three.js loader is configured for ${url}; convert the ` +
          'artifact to glTF/GLB before runtime use',
      );
    }
    const result = await loader.loadAsync(url);
    if (result?.scene) {
      return {
        object: result.scene,
        animations: result.animations ?? [],
        entry,
        gltf: result,
      };
    }
    if (result?.isBufferGeometry) {
      const mesh = new THREE.Mesh(
        result,
        new THREE.MeshStandardMaterial({ color: 0xcccccc }),
      );
      mesh.name = entry.asset_id;
      return { object: mesh, animations: [], entry };
    }
    return { object: result, animations: result?.animations ?? [], entry };
  }
}
