import {
  BufferGeometry, Color, DynamicDrawUsage, Float32BufferAttribute, Mesh,
  MeshDepthMaterial, MeshDistanceMaterial, MeshStandardMaterial, RGBADepthPacking,
} from 'three';

const PRESETS = {
  lava: { viscosity: 5, mobility: 3, coolingRate: 0.055, roughness: 0.78, color: 0x34211c,
    yieldSlope: 0.025, solidificationTemperature: 0.28, thermalViscosity: 5, referenceDepth: 0.25 },
  blood: { viscosity: 1, mobility: 6, coolingRate: 0, roughness: 0.19, color: 0x8e0815 },
};

function finite(value, name, minimum = -Infinity) {
  if (!Number.isFinite(value) || value < minimum) throw new RangeError(`${name} must be finite and >= ${minimum}`);
  return value;
}

function patchMaterial(material, preset, minDepth, shadow = false) {
  material.onBeforeCompile = (shader) => {
    shader.uniforms.a3FlowMinDepth = { value: minDepth };
    shader.vertexShader = shader.vertexShader.replace('#include <common>', `
      #include <common>
      attribute float a3FlowDepth;
      attribute float a3FlowTemperature;
      attribute vec2 a3FlowCoord;
      varying float vA3FlowDepth;
      varying float vA3FlowTemperature;
      varying vec2 vA3FlowXZ;
      varying vec2 vA3SurfaceXZ;
    `).replace('#include <begin_vertex>', `
      #include <begin_vertex>
      vA3FlowDepth = a3FlowDepth;
      vA3FlowTemperature = a3FlowTemperature;
      vA3FlowXZ = a3FlowCoord;
      vA3SurfaceXZ = (modelMatrix * vec4(position, 1.0)).xz;
    `);
    shader.fragmentShader = shader.fragmentShader.replace('#include <common>', `
      #include <common>
      uniform float a3FlowMinDepth;
      varying float vA3FlowDepth;
      varying float vA3FlowTemperature;
      varying vec2 vA3FlowXZ;
      varying vec2 vA3SurfaceXZ;
      ${preset === 'lava' && !shadow ? `
      float a3FlowHash(vec2 p) {
        vec3 q = fract(vec3(p.xyx) * 0.1031);
        q += dot(q, q.yzx + 33.33);
        return fract((q.x + q.y) * q.z);
      }
      float a3FlowNoise(vec2 p) {
        vec2 cell = floor(p), f = fract(p);
        vec2 u = f * f * (3.0 - 2.0 * f);
        return mix(mix(a3FlowHash(cell), a3FlowHash(cell + vec2(1.0, 0.0)), u.x),
                   mix(a3FlowHash(cell + vec2(0.0, 1.0)), a3FlowHash(cell + vec2(1.0)), u.x), u.y);
      }
      float a3FlowFbm(vec2 p) {
        float value = 0.0, weight = 0.533;
        mat2 turn = mat2(0.8, 0.6, -0.6, 0.8);
        for (int octave = 0; octave < 4; octave++) {
          value += weight * a3FlowNoise(p);
          p = turn * p * 2.03 + vec2(7.1, 13.7);
          weight *= 0.5;
        }
        return value;
      }
      ` : ''}
    `).replace('#include <clipping_planes_fragment>', `
      #include <clipping_planes_fragment>
      if (vA3FlowDepth <= a3FlowMinDepth) discard;
    `);
    if (shadow) return;
    shader.fragmentShader = shader.fragmentShader.replace('#include <color_fragment>', `
      #include <color_fragment>
      diffuseColor.a *= smoothstep(a3FlowMinDepth, a3FlowMinDepth * 8.0, vA3FlowDepth);
      ${preset === 'lava' ? `
      float a3Heat = smoothstep(0.22, 1.0, vA3FlowTemperature)
                   * mix(0.28, 1.0, smoothstep(0.015, 0.14, vA3FlowDepth));
      vec2 a3CrustP = mix(vA3SurfaceXZ, vA3FlowXZ, 0.55) * vec2(3.5, 3.0);
      vec2 a3Warp = vec2(a3FlowFbm(a3CrustP * 0.63 + vec2(3.1, 8.7)),
                         a3FlowFbm(a3CrustP * 0.63 + vec2(17.2, 2.4)));
      vec2 a3FoldP = a3CrustP + (a3Warp - 0.5) * 3.4;
      float a3CrustField = a3FlowFbm(a3FoldP);
      float a3Vein = abs(a3CrustField - 0.49);
      float a3CrackWidth = mix(0.004, 0.022, a3Heat * a3Heat);
      float a3Antialias = max(fwidth(a3Vein), 0.002);
      float a3Crack = 1.0 - smoothstep(a3CrackWidth, a3CrackWidth + a3Antialias, a3Vein);
      float a3Molten = smoothstep(0.58, 0.76, a3CrustField) * smoothstep(0.62, 0.97, a3Heat);
      float a3Exposure = max(a3Crack, a3Molten);
      float a3Rope = a3FlowNoise(vec2(a3FoldP.x * 2.5, a3FoldP.y * 11.0));
      float a3Pores = a3FlowNoise(a3FoldP * 26.0);
      float a3Crust = 1.0 - a3Exposure;
      float a3Relief = (a3Crust * (0.055 + 0.04 * a3Rope) + 0.012 * a3Pores)
                      * smoothstep(0.008, 0.10, vA3FlowDepth);
      float a3Glow = a3Heat * (0.008 + 0.95 * a3Exposure);
      diffuseColor.rgb = mix(diffuseColor.rgb * (0.5 + 0.8 * a3Rope + 0.25 * a3Pores),
                            vec3(0.20, 0.012, 0.001), a3Exposure);
      ` : ''}
    `);
    if (preset === 'lava') {
      shader.fragmentShader = shader.fragmentShader.replace('#include <emissivemap_fragment>', `
        #include <emissivemap_fragment>
        totalEmissiveRadiance *= a3Glow * mix(vec3(0.55, 0.012, 0.0003), vec3(1.0, 0.09, 0.002), a3Heat * a3Exposure);
      `).replace('#include <roughnessmap_fragment>', `
        #include <roughnessmap_fragment>
        roughnessFactor = mix(0.86 + 0.10 * a3Pores, 0.4, a3Exposure);
      `).replace('#include <normal_fragment_maps>', `
        #include <normal_fragment_maps>
        vec3 a3SigmaX = dFdx(-vViewPosition), a3SigmaY = dFdy(-vViewPosition);
        vec3 a3R1 = cross(a3SigmaY, normal), a3R2 = cross(normal, a3SigmaX);
        float a3Det = dot(a3SigmaX, a3R1);
        vec3 a3Gradient = dFdx(a3Relief) * a3R1 + dFdy(a3Relief) * a3R2;
        normal = normalize(max(abs(a3Det), 1e-8) * normal - sign(a3Det) * a3Gradient);
      `);
    }
  };
  material.customProgramCacheKey = () => `a3-surface-flow-v3-${preset}-${shadow}`;
}

/**
 * Conservative, viscous heightfield transport, not a 3D Navier–Stokes solver.
 * The resolution² cells have uniform area and render at their centers. Bed and
 * source coordinates are world-space; construct with position rather than moving,
 * rotating or scaling the resulting mesh. Volumes are cubic world units.
 */
export function createSurfaceFlow(options = {}) {
  const preset = options.preset ?? 'lava';
  if (!PRESETS[preset]) throw new RangeError('preset must be lava or blood');
  const defaults = PRESETS[preset];
  const size = options.size ?? [12, 12];
  const width = finite(Array.isArray(size) ? size[0] : size, 'size.x', 0.001);
  const length = finite(Array.isArray(size) ? size[1] : size, 'size.z', 0.001);
  const n = options.resolution ?? 56;
  if (!Number.isInteger(n) || n < 4 || n > 256) throw new RangeError('resolution must be an integer from 4 to 256');
  const origin = options.position ?? [0, 0, 0];
  const ox = finite(origin[0], 'position.x');
  const oy = finite(origin[1], 'position.y');
  const oz = finite(origin[2], 'position.z');
  const viscosity = finite(options.viscosity ?? defaults.viscosity, 'viscosity', 0.0001);
  const mobility = finite(options.mobility ?? defaults.mobility, 'mobility', 0);
  const coolingRate = finite(options.coolingRate ?? defaults.coolingRate, 'coolingRate', 0);
  const yieldSlope = finite(options.yieldSlope ?? defaults.yieldSlope ?? 0, 'yieldSlope', 0);
  const solidificationTemperature = finite(options.solidificationTemperature ?? defaults.solidificationTemperature ?? 0, 'solidificationTemperature', 0);
  if (solidificationTemperature >= 1) throw new RangeError('solidificationTemperature must be less than 1');
  const thermalViscosity = finite(options.thermalViscosity ?? defaults.thermalViscosity ?? 0, 'thermalViscosity', 0);
  const referenceDepth = finite(options.referenceDepth ?? defaults.referenceDepth ?? 0.25, 'referenceDepth', 0.000001);
  const fixedStep = finite(options.fixedStep ?? 1 / 120, 'fixedStep', 0.00001);
  const minVisibleDepth = finite(options.minVisibleDepth ?? 0.0005, 'minVisibleDepth', 0.000001);
  const boundary = options.boundary ?? 'closed';
  if (boundary !== 'closed' && boundary !== 'open') throw new RangeError('boundary must be closed or open');
  const heightMap = options.heightMap ?? (() => 0);
  const dx = width / n, dz = length / n, cellArea = dx * dz, count = n * n;
  const bed = new Float64Array(count), depth = new Float64Array(count), heat = new Float64Array(count);
  const delta = new Float64Array(count), heatDelta = new Float64Array(count), outgoing = new Float64Array(count);
  const materialMoments = new Float64Array(count * 2), materialDelta = new Float64Array(count * 2);
  const coordEpsilon = 1e-12;
  const positions = new Float32Array(count * 3);
  const depthAttribute = new Float32BufferAttribute(new Float32Array(count), 1).setUsage(DynamicDrawUsage);
  const temperatureAttribute = new Float32BufferAttribute(new Float32Array(count), 1).setUsage(DynamicDrawUsage);
  const coordAttribute = new Float32BufferAttribute(new Float32Array(count * 2), 2).setUsage(DynamicDrawUsage);
  const xs = new Float64Array(n), zs = new Float64Array(n);
  for (let i = 0; i < n; i++) { xs[i] = ox - width / 2 + (i + 0.5) * dx; zs[i] = oz - length / 2 + (i + 0.5) * dz; }
  let initialVolume = 0;
  for (let z = 0; z < n; z++) for (let x = 0; x < n; x++) {
    const i = z * n + x;
    bed[i] = finite(heightMap(xs[x], zs[z]), 'heightMap result');
    const initial = typeof options.initialDepth === 'function' ? options.initialDepth(xs[x], zs[z]) : (options.initialDepth ?? 0);
    depth[i] = finite(initial, 'initialDepth', 0);
    heat[i] = depth[i];
    materialMoments[i * 2] = depth[i] * xs[x];
    materialMoments[i * 2 + 1] = depth[i] * zs[z];
    initialVolume += depth[i] * cellArea;
    positions[i * 3] = xs[x] - ox;
    positions[i * 3 + 1] = bed[i] + depth[i] - oy;
    positions[i * 3 + 2] = zs[z] - oz;
  }
  const indices = [];
  const edges = [];
  for (let z = 0; z < n; z++) for (let x = 0; x < n; x++) {
    const i = z * n + x;
    if (x < n - 1) edges.push([i, i + 1, 1 / (dx * dx)]);
    if (z < n - 1) edges.push([i, i + n, 1 / (dz * dz)]);
    if (x < n - 1 && z < n - 1) indices.push(i, i + n, i + 1, i + 1, i + n, i + n + 1);
    if (boundary === 'open') {
      if (x === 0 || x === n - 1) edges.push([i, -1, 1 / (dx * dx)]);
      if (z === 0 || z === n - 1) edges.push([i, -1, 1 / (dz * dz)]);
    }
  }
  const flux = new Float64Array(edges.length);
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new Float32BufferAttribute(positions, 3).setUsage(DynamicDrawUsage));
  geometry.setAttribute('a3FlowDepth', depthAttribute);
  geometry.setAttribute('a3FlowTemperature', temperatureAttribute);
  geometry.setAttribute('a3FlowCoord', coordAttribute);
  geometry.setIndex(indices);
  const material = new MeshStandardMaterial({
    color: new Color(options.color ?? defaults.color), roughness: options.roughness ?? defaults.roughness,
    metalness: 0, transparent: true, depthWrite: true,
    emissive: preset === 'lava' ? 0xffffff : 0x000000,
    emissiveIntensity: preset === 'lava' ? (options.emissiveIntensity ?? 3.2) : 0,
  });
  patchMaterial(material, preset, minVisibleDepth);
  const mesh = new Mesh(geometry, material);
  mesh.name = options.name ?? `surface-flow-${preset}`;
  mesh.position.set(ox, oy, oz);
  mesh.receiveShadow = true;
  mesh.castShadow = true;
  mesh.customDepthMaterial = new MeshDepthMaterial({ depthPacking: RGBADepthPacking });
  mesh.customDistanceMaterial = new MeshDistanceMaterial();
  patchMaterial(mesh.customDepthMaterial, preset, minVisibleDepth, true);
  patchMaterial(mesh.customDistanceMaterial, preset, minVisibleDepth, true);
  const sources = new Set(), hostBindings = new Set();
  let elapsed = 0, accumulator = 0, totalInjected = 0, outflow = 0, disposed = false;

  function materialCoord(i, component) {
    return depth[i] > coordEpsilon ? materialMoments[i * 2 + component] / depth[i]
      : component === 0 ? xs[i % n] : zs[Math.floor(i / n)];
  }

  function syncGeometry() {
    const position = geometry.attributes.position;
    for (let i = 0; i < count; i++) {
      position.array[i * 3 + 1] = bed[i] + depth[i] - oy;
      depthAttribute.array[i] = depth[i];
      temperatureAttribute.array[i] = depth[i] > 0 ? heat[i] / depth[i] : 0;
      coordAttribute.array[i * 2] = materialCoord(i, 0);
      coordAttribute.array[i * 2 + 1] = materialCoord(i, 1);
    }
    position.needsUpdate = depthAttribute.needsUpdate = temperatureAttribute.needsUpdate = coordAttribute.needsUpdate = true;
    geometry.computeVertexNormals();
    geometry.computeBoundingSphere();
    geometry.computeBoundingBox();
  }

  function inject(source, volume) {
    if (volume <= 0) return;
    for (const [i, weight] of source.weights) {
      const added = volume * weight / cellArea;
      depth[i] += added;
      heat[i] += added * source.temperature;
      materialMoments[i * 2] += added * xs[i % n];
      materialMoments[i * 2 + 1] += added * zs[Math.floor(i / n)];
    }
    totalInjected += volume;
  }

  /** Inject volume immediately; optional rate runs for duration seconds. Returns a stop callback. */
  function addSource(spec = {}) {
    if (disposed) return () => {};
    const x = finite(spec.x ?? ox, 'source.x'), z = finite(spec.z ?? oz, 'source.z');
    if (Math.abs(x - ox) > width / 2 || Math.abs(z - oz) > length / 2) throw new RangeError('source lies outside the heightfield');
    const radius = finite(spec.radius ?? Math.max(dx, dz), 'source.radius', 0);
    const volume = finite(spec.volume ?? 0, 'source.volume', 0);
    const rate = finite(spec.rate ?? 0, 'source.rate', 0);
    const duration = spec.duration === undefined ? Infinity : finite(spec.duration, 'source.duration', 0);
    const temperature = Math.min(1, finite(spec.temperature ?? 1, 'source.temperature', 0));
    const weights = [];
    let sum = 0;
    for (let iz = 0; iz < n; iz++) for (let ix = 0; ix < n; ix++) {
      const distance = Math.hypot(xs[ix] - x, zs[iz] - z);
      if (radius > 0 && distance < radius) {
        const weight = (1 - distance / radius) ** 2;
        weights.push([iz * n + ix, weight]); sum += weight;
      }
    }
    if (!weights.length) {
      const ix = Math.max(0, Math.min(n - 1, Math.floor((x - ox + width / 2) / dx)));
      const iz = Math.max(0, Math.min(n - 1, Math.floor((z - oz + length / 2) / dz)));
      weights.push([iz * n + ix, 1]); sum = 1;
    }
    for (const entry of weights) entry[1] /= sum;
    const source = { weights, rate, remaining: duration, temperature };
    inject(source, volume);
    if (rate > 0 && duration > 0) sources.add(source);
    syncGeometry();
    return () => sources.delete(source);
  }

  function step(h) {
    for (const source of sources) {
      const activeTime = Math.min(h, source.remaining);
      inject(source, source.rate * activeTime);
      source.remaining -= activeTime;
      if (source.remaining <= 1e-12) sources.delete(source);
    }
    outgoing.fill(0); delta.fill(0); heatDelta.fill(0); materialDelta.fill(0);
    // An edge has exactly one signed transfer. Limit donor outflow before applying
    // any transfers, so closed-domain volume cannot be created or overdrawn.
    for (let e = 0; e < edges.length; e++) {
      const [a, b, inverseDistanceSquared] = edges[e];
      const headDifference = b < 0 ? depth[a] : bed[a] + depth[a] - bed[b] - depth[b];
      const donor = headDifference >= 0 ? a : b;
      const donorDepth = depth[donor];
      let conductance = mobility / viscosity;
      if (preset === 'lava') {
        const temperature = donorDepth > 0 ? heat[donor] / donorDepth : 0;
        const stress = donorDepth * Math.abs(headDifference) * Math.sqrt(inverseDistanceSquared);
        if (temperature <= solidificationTemperature || stress <= yieldSlope) {
          flux[e] = 0;
          continue;
        }
        const yieldRatio = yieldSlope / stress;
        const yielded = (1 - yieldRatio) ** 2 * (1 + yieldRatio * 0.5);
        const thickness = Math.min(4, donorDepth / referenceDepth);
        conductance *= Math.exp(-thermalViscosity * (1 - temperature)) * thickness * thickness * yielded;
      }
      const amount = Math.min(Math.abs(headDifference) * 0.24,
        conductance * donorDepth * Math.abs(headDifference) * inverseDistanceSquared * h);
      flux[e] = headDifference >= 0 ? amount : -amount;
      outgoing[donor] += amount;
    }
    for (let e = 0; e < edges.length; e++) {
      const [a, b] = edges[e];
      const donor = flux[e] >= 0 ? a : b;
      const receiver = flux[e] >= 0 ? b : a;
      if (outgoing[donor] === 0) continue;
      const amount = Math.abs(flux[e]) * Math.min(1, depth[donor] / outgoing[donor]);
      const transportedHeat = depth[donor] > 0 ? amount * heat[donor] / depth[donor] : 0;
      delta[donor] -= amount; heatDelta[donor] -= transportedHeat;
      if (receiver >= 0) { delta[receiver] += amount; heatDelta[receiver] += transportedHeat; }
      else outflow += amount * cellArea;
      const fraction = depth[donor] > 0 ? amount / depth[donor] : 0;
      for (let component = 0; component < 2; component++) {
        const transported = fraction * materialMoments[donor * 2 + component];
        materialDelta[donor * 2 + component] -= transported;
        if (receiver >= 0) materialDelta[receiver * 2 + component] += transported;
      }
    }
    const cooling = Math.exp(-coolingRate * h);
    for (let i = 0; i < count; i++) {
      depth[i] = Math.max(0, depth[i] + delta[i]);
      heat[i] = Math.max(0, Math.min(depth[i], heat[i] + heatDelta[i])) * cooling;
      materialMoments[i * 2] = depth[i] > 0 ? materialMoments[i * 2] + materialDelta[i * 2] : 0;
      materialMoments[i * 2 + 1] = depth[i] > 0 ? materialMoments[i * 2 + 1] + materialDelta[i * 2 + 1] : 0;
    }
    elapsed += h;
  }

  function update(dt) {
    if (disposed || !Number.isFinite(dt) || dt <= 0) return;
    accumulator += dt;
    // Preserve the fractional remainder: changing the render rate does not change
    // source injection, transport or cooling. No elapsed time is silently dropped.
    while (accumulator + 1e-12 >= fixedStep) {
      step(fixedStep);
      accumulator = Math.max(0, accumulator - fixedStep);
    }
    syncGeometry();
  }

  function getState() {
    let volume = 0, minDepth = Infinity, maxDepth = 0, wetCells = 0, cx = 0, cz = 0, thermal = 0;
    const temperature = new Float64Array(count), materialCoords = new Float64Array(count * 2);
    for (let i = 0; i < count; i++) {
      const v = depth[i] * cellArea;
      volume += v; minDepth = Math.min(minDepth, depth[i]); maxDepth = Math.max(maxDepth, depth[i]);
      if (depth[i] > minVisibleDepth) wetCells++;
      cx += xs[i % n] * v; cz += zs[Math.floor(i / n)] * v;
      thermal += heat[i] * cellArea;
      temperature[i] = depth[i] > 0 ? heat[i] / depth[i] : 0;
      materialCoords[i * 2] = materialCoord(i, 0);
      materialCoords[i * 2 + 1] = materialCoord(i, 1);
    }
    return {
      model: 'viscous heightfield transport', preset, resolution: n, size: [width, length],
      position: [ox, oy, oz], cellArea, dx, dz, boundary, viscosity, mobility,
      yieldSlope, solidificationTemperature, thermalViscosity, referenceDepth, coolingRate, fixedStep,
      elapsed, pendingTime: accumulator, initialVolume, totalInjected, outflow, volume,
      massError: volume + outflow - initialVolume - totalInjected,
      minDepth, maxDepth, wetCells, activeSources: sources.size, disposed,
      centerOfMass: { x: volume > 0 ? cx / volume : ox, z: volume > 0 ? cz / volume : oz },
      meanTemperature: volume > 0 ? thermal / volume : 0,
      depth: depth.slice(), bed: bed.slice(), temperature, materialCoords,
    };
  }

  function getBedGeometry() {
    const result = geometry.clone();
    const position = result.attributes.position;
    for (let i = 0; i < count; i++) position.array[i * 3 + 1] = bed[i] - oy;
    result.deleteAttribute('a3FlowDepth'); result.deleteAttribute('a3FlowTemperature'); result.deleteAttribute('a3FlowCoord');
    result.computeVertexNormals(); result.computeBoundingSphere(); result.computeBoundingBox();
    return result;
  }

  function attachToHost(host) {
    if (disposed) return () => {};
    if (typeof host?.onTick !== 'function') throw new TypeError('host.onTick is required');
    const removeTick = host.onTick(update);
    const detach = () => { if (hostBindings.delete(detach)) removeTick?.(); };
    hostBindings.add(detach);
    return detach;
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    mesh.userData.disposed = true;
    for (const detach of hostBindings) detach();
    sources.clear();
    geometry.dispose(); material.dispose();
    mesh.customDepthMaterial.dispose(); mesh.customDistanceMaterial.dispose();
  }

  Object.assign(mesh.userData, { update, addSource, getState, getBedGeometry, attachToHost, dispose });
  for (const source of options.sources ?? []) addSource(source);
  syncGeometry();
  return mesh;
}

/** Independent matching bed mesh; dispose it separately from its fluid. */
export function createSurfaceFlowTerrain(flow, materialOptions = {}) {
  if (typeof flow?.userData?.getBedGeometry !== 'function') throw new TypeError('Expected a surface-flow mesh');
  const terrain = new Mesh(flow.userData.getBedGeometry(), new MeshStandardMaterial({
    color: 0x343637, roughness: 0.94, metalness: 0, ...materialOptions,
  }));
  terrain.name = `${flow.name}-terrain`;
  terrain.position.copy(flow.position);
  terrain.quaternion.copy(flow.quaternion);
  terrain.scale.copy(flow.scale);
  terrain.receiveShadow = true;
  terrain.castShadow = true;
  let disposed = false;
  terrain.userData.dispose = () => {
    if (disposed) return;
    disposed = true;
    terrain.userData.disposed = true;
    terrain.geometry.dispose(); terrain.material.dispose();
  };
  return terrain;
}
