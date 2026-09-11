import * as THREE from 'three';
import {
  A3GameRuntimeHost, A3GameSceneLoader, A3GameWaterBody, A3GameParticleSystem, A3GameVfxPreset,
  createRoundedBox, createSunLight, createCloudLayer, createLightningArc, createWaterSurface,
  createSurfaceFlow, createSurfaceFlowTerrain, bindVegetationWind, createSeededRandom,
} from '../src/index.js';

const mode = new URLSearchParams(location.search).get('mode') ?? 'wind';
const titles = {
  wind: ['统一风场 / 烟 · 火 · 电', '烟羽与火焰顺风偏转，草木根部固定；电弧保持端点并持续重构。'],
  water: ['水体交互 / 浮力 · 水流', '轻木浮起、重金属沉底；入水产生涟漪，桥下水域保持连续。'],
  lava: ['熔岩 / 熔融热芯 · 黏塑冷壳', '厚流缓慢推挤，表皮随流拉伸；冷却增稠，热光与轻烟跟随熔融区域。'],
  blood: ['斜面血液 / 材质实验', '无人物受伤的红色液体测试：重力下流、分支、洼地积液。'],
};
const [title, subtitle] = titles[mode] ?? titles.wind;
document.querySelector('#title').textContent = title;
document.querySelector('#subtitle').textContent = subtitle;
for (const a of document.querySelectorAll('nav a')) if (a.href.endsWith(`mode=${mode}`)) a.classList.add('active');
document.querySelector('#note').innerHTML = 'THREE.JS r185 · 固定步进 60 Hz<br>实时游戏近似 / 非完整流体求解';
const metrics = document.querySelector('#metrics');
const random = createSeededRandom(32);

function smokeTexture() {
  const canvas = document.createElement('canvas'); canvas.width = canvas.height = 96;
  const context = canvas.getContext('2d'), image = context.createImageData(96, 96);
  for (let y = 0; y < 96; y++) for (let x = 0; x < 96; x++) {
    const dx = (x - 48) / 48, dy = (y - 48) / 48;
    const noise = 0.64 + Math.sin(x * 0.19 + Math.cos(y * 0.13) * 3) * 0.12 + Math.cos(y * 0.27 + x * 0.09) * 0.1;
    const alpha = Math.max(0, 1 - dx * dx - dy * dy) ** 1.8 * noise;
    const i = (y * 96 + x) * 4;
    image.data[i] = image.data[i + 1] = image.data[i + 2] = 255;
    image.data[i + 3] = Math.round(alpha * 255);
  }
  context.putImageData(image, 0, 0);
  return new THREE.CanvasTexture(canvas);
}

async function main() {
  const host = new A3GameRuntimeHost({ container: '#view', pixelRatioCap: 1, toneMapping: 'ACESFilmicToneMapping', toneMappingExposure: 0.85 });
  await host.init();
  const errors = [];
  host.renderer.debug.onShaderError = (gl, p, v, f) => errors.push(gl.getProgramInfoLog(p), gl.getShaderInfoLog(v), gl.getShaderInfoLog(f));
  host.setEnvironment({ preset: 'gradient', sunPosition: { x: -0.8, y: 0.5, z: -0.4 }, sky: { zenith: 0x162c47, horizon: 0x739298, ground: 0x1a2930, cloudCoverage: 0.55, cloudOpacity: 0.7 } });
  host.setFog({ type: 'Fog', color: 0x344d5d, near: 35, far: 130 });
  host.add(createSunLight({ intensity: 2.5, radius: 24, mapSize: 1024, position: { x: -20, y: 30, z: -10 } }), 'lights');
  host.add(new THREE.HemisphereLight(0xaecbdd, 0x191c22, 1.1), 'lights');
  const groundMaterial = new THREE.MeshStandardMaterial({ color: 0x26373d, roughness: 0.88 });
  const base = createRoundedBox({ width: 24, height: 0.5, depth: 20, radius: 0.2, material: groundMaterial });
  base.position.y = -0.4; host.add(base);
  const grid = new THREE.GridHelper(80, 40, 0x354754, 0x273640); grid.position.y = -0.66; host.add(grid);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(250, 250), new THREE.MeshStandardMaterial({ color: 0x14202c, roughness: 0.95 }));
  ground.rotation.x = -Math.PI / 2; ground.position.y = -0.68; ground.receiveShadow = true; host.add(ground);
  const renderUpdates = [];
  const evidence = { mode, entries: 0, smoke: null, fire: null, arc: null, flow: null, bodies: [] };
  let target = new THREE.Vector3(0, 1.5, 0), cameraStart = new THREE.Vector3(15, 10, 19);

  function label(text, position, color = '#c3e6e2') {
    const canvas = document.createElement('canvas'); canvas.width = 512; canvas.height = 96;
    const ctx = canvas.getContext('2d'); ctx.font = '28px sans-serif'; ctx.textAlign = 'center'; ctx.fillStyle = color; ctx.fillText(text, 256, 58);
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(canvas), transparent: true, depthTest: false }));
    sprite.position.fromArray(position); sprite.scale.set(5.5, 1.03, 1); host.add(sprite);
  }
  function rocks(count, area, dark = true) {
    for (let i = 0; i < count; i++) {
      const rock = new THREE.Mesh(new THREE.DodecahedronGeometry(0.3 + random() * 0.45, 0), new THREE.MeshStandardMaterial({ color: dark ? 0x343c43 : 0x879082, roughness: 0.95, flatShading: true }));
      rock.position.set((random() - 0.5) * area, 0.1, (random() - 0.5) * area); rock.scale.set(1.2, 0.6, 1); rock.rotation.y = random() * 6; rock.castShadow = true; host.add(rock);
    }
  }
  function plant(x, z, height) {
    const root = new THREE.Group(); root.position.set(x, -0.05, z);
    const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.025, 0.045, height, 5), new THREE.MeshStandardMaterial({ color: 0x698d56, roughness: 0.9 }));
    stem.position.y = height / 2; stem.castShadow = true; root.add(stem);
    for (let j = 0; j < 4; j++) {
      const leaf = new THREE.Mesh(new THREE.SphereGeometry(1, 8, 6), new THREE.MeshStandardMaterial({ color: j % 2 ? 0x83a865 : 0x54774e, roughness: 0.8 }));
      leaf.scale.set(0.17, height * 0.24, 0.045); leaf.position.set((j % 2 ? 1 : -1) * 0.12, height * (0.4 + j * 0.16), 0); leaf.rotation.z = (j % 2 ? 1 : -1) * 0.6; leaf.castShadow = true; root.add(leaf);
    }
    host.add(root); bindVegetationWind(root, host, { flexibility: 0.1, maxBend: 0.52, phase: x + z });
  }
  function particles(config, position) {
    const system = new A3GameParticleSystem(config); system.emitterPosition.fromArray(position); host.add(system.object3D); system.attachToHost(host); system.start(); return system;
  }

  if (mode === 'wind') {
    host.setWind({ velocity: [2.8, 0, 0.6], gustStrength: 0.5, gustPeriod: 4, response: 1.3, seed: 33 }, true);
    for (let i = 0; i < 22; i++) plant(-6 + random() * 12, 1.7 + random() * 2.8, 0.7 + random() * 1.0);
    const clouds = createCloudLayer({ count: 10, radius: 45, height: 17, size: 13, opacity: 0.3 }); host.add(clouds); clouds.userData.attachToHost(host);
    rocks(14, 16);
    const hearth = new THREE.Mesh(new THREE.CylinderGeometry(0.8, 1, 0.45, 18), new THREE.MeshStandardMaterial({ color: 0x42474a, roughness: 0.7 })); hearth.position.set(-3, 0.1, 0); hearth.castShadow = true; host.add(hearth);
    const fire = particles({ ...A3GameVfxPreset.FIRE_PLUME, size: [0.22, 0.75], speed: [2, 3.8], lifetime: [0.6, 1.2], emitterRadius: [0, 0.6] }, [-3, 0.4, 0]);
    const smoke = particles({ ...A3GameVfxPreset.SMOKE_PLUME, map: smokeTexture(), maxParticles: 380, lifetime: [3.2, 5.5], size: [0.6, 1.1], sizeOverLife: [0.7, 3.0], colorStart: ['#b9bcc0', '#929a9e'], colorEnd: ['#788891'], opacityOverLife: [0.4, 0], speed: [1.1, 1.8], gravity: [0, 0.8, 0], emissionOverTime: 55 }, [-3, 1.1, 0]);
    particles({ maxParticles: 100, lifetime: [1, 2], size: [0.025, 0.06], colorStart: ['#ffe5a8'], colorEnd: ['#9f2c10'], speed: [1, 3], direction: [[-0.2, 0.2], [0.6, 1], [-0.2, 0.2]], gravity: [0, -0.8, 0], windResponse: 0.7, emissionOverTime: 28, blending: 'additive' }, [-3, 0.6, 0]);
    const glow = new THREE.PointLight(0xff6b26, 25, 9, 2); glow.position.set(-3, 1.3, 0); host.add(glow);
    host.onTick((dt, time) => { glow.intensity = 22 + 6 * Math.sin(time * 17) + 4 * Math.sin(time * 29); });
    for (const x of [1.6, 6.4]) {
      const pillar = createRoundedBox({ width: 0.45, height: 2.6, depth: 0.45, preset: 'metal' }); pillar.position.set(x, 1.25, -1.3); host.add(pillar);
      const cap = new THREE.Mesh(new THREE.SphereGeometry(0.24, 20, 14), new THREE.MeshPhysicalMaterial({ color: 0xa5c9d8, metalness: 1, roughness: 0.15 })); cap.position.set(x, 2.8, -1.3); host.add(cap);
    }
    const arc = createLightningArc({ from: [1.6, 2.8, -1.3], to: [6.4, 2.8, -1.3], width: 0.02, segments: 28 }); host.add(arc); arc.userData.attachToHost(host);
    const flash = new THREE.PointLight(0x61bbff, 0, 8); flash.position.set(4, 3, -1); host.add(flash);
    host.onTick(() => { flash.intensity = arc.visible ? 20 : 0; });
    const arrow = new THREE.ArrowHelper(new THREE.Vector3(1, 0, 0), new THREE.Vector3(-6, 0.25, 6), 3, 0x72d6bc, 0.4, 0.25); host.add(arrow);
    let reversed = false;
    host.onTick((dt, time) => { if (time > 5 && !reversed) { host.setWind({ velocity: [-2.2, 0, 1.1] }); reversed = true; } });
    renderUpdates.push(() => { const wind = host.wind.sample(new THREE.Vector3()); arrow.setDirection(wind.clone().normalize()); arrow.setLength(Math.max(0.4, wind.length()), 0.4, 0.25); metrics.textContent = `风速  ${wind.length().toFixed(2)} m/s   ${reversed ? '转向后的惯性响应' : '稳定侧风 + 阵风'}\n粒子  ${smoke.activeCount + fire.activeCount}    电弧闪现  ${arc.userData.getState().flashes}`; });
    label('热浮升 / 横向空气阻力', [-3, 0.6, 3.7]); label('固定端点 / 闪烁电弧', [4, 0.6, 1.8], '#85c6ee');
    Object.assign(evidence, { smoke, fire, arc });
    cameraStart.set(10.5, 6.8, 13.5); target.set(0.5, 2.0, 0);
  } else if (mode === 'water') {
    base.visible = false;
    grid.visible = false;
    ground.position.y = -3;
    host.setWind({ velocity: [1.6, 0, 0.4], gustStrength: 0.2 }, true);
    const bed = (x, z) => -1.8 + Math.max(0, Math.hypot(x / 1.2, z) - 4.3) * 0.6;
    const geometry = new THREE.PlaneGeometry(22, 18, 70, 60); geometry.rotateX(-Math.PI / 2);
    const p = geometry.attributes.position;
    for (let i = 0; i < p.count; i++) p.setY(i, bed(p.getX(i), p.getZ(i)));
    geometry.computeVertexNormals();
    const terrain = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: 0x81867a, roughness: 0.92 })); terrain.receiveShadow = true; host.add(terrain);
    const water = createWaterSurface({ size: [20, 16], quality: 'standard', waveHeight: 0.035, terrainHeight: bed, current: [0.3, 0, 0.08], reflection: true, refraction: true, reflectionResolution: 256, reflectionUpdateRate: 12, foamStrength: 0.2, foamWidth: 0.22, shallowColor: 0x7ab2a7 }); host.add(water); water.userData.attachToHost(host);
    const bridge = createRoundedBox({ width: 11, height: 0.2, depth: 1.2, material: new THREE.MeshStandardMaterial({ color: 0x775b43, roughness: 0.83 }) }); bridge.position.set(0, 0.9, -3.2); host.add(bridge);
    for (const x of [-4, 0, 4]) { const support = createRoundedBox({ width: 0.25, height: 2.7, depth: 0.25, preset: 'wood' }); support.position.set(x, -0.4, -3.2); host.add(support); }
    const splash = particles({ maxParticles: 180, lifetime: [0.35, 0.65], size: [0.04, 0.12], colorStart: ['#cfebe8'], colorEnd: ['#6dabad'], speed: [1.2, 3], gravity: [0, -9.81, 0], direction: [[-0.7, 0.7], [0.3, 1], [-0.7, 0.7]], windResponse: 0.1, emissionOverTime: 0 }, [0, 0, 0]);
    const bodies = [];
    for (let i = 0; i < 2; i++) {
      const size = i === 0 ? [1.4, 0.7, 1.1] : [0.65, 0.65, 0.65];
      const object = createRoundedBox({ width: size[0], height: size[1], depth: size[2], radius: 0.06, material: new THREE.MeshStandardMaterial({ color: i ? 0x65778a : 0xb78750, metalness: i ? 0.9 : 0, roughness: i ? 0.2 : 0.6 }) });
      object.position.set(i ? 2.0 : -1.7, i ? 3.1 : 2.3, 0.8); object.rotation.z = i ? 0.2 : 0.12; host.add(object);
      const body = new A3GameWaterBody({ water, object, mass: i ? 1200 : 420, size, onEnterWater: event => { evidence.entries++; splash.emit(45, { position: event.position }); } });
      body.attachToHost(host); bodies.push(body);
    }
    label('木材 / 浮力平衡', [-2.5, 1.4, 4.3]); label('金属 / 沉底', [2.4, 1.4, 4.3], '#c2cbd7');
    renderUpdates.push(() => { metrics.textContent = `木材浸没  ${(bodies[0].submergedFraction * 100).toFixed(0)}%    金属高度  ${bodies[1].object.position.y.toFixed(2)} m\n入水事件  ${evidence.entries}    独立水流  0.30 m/s`; });
    Object.assign(evidence, { water, bodies }); cameraStart.set(13, 10, 17); target.set(0, 0, 0);
  } else {
    const lava = mode === 'lava';
    base.visible = false;
    host.setWind({ velocity: [1.4, 0, 0.4], gustStrength: 0.1 }, true);
    const bed = (x, z) => {
      const channel = Math.sin(z * 0.52) * 0.5;
      const banks = Math.max(0, Math.abs(x - channel) - 1.6) ** 1.5 * 0.38;
      const ridge = 0.7 * Math.exp(-((x + 0.2) ** 2 / 0.25 + (z + 1.6) ** 2 / 0.8));
      const pool = -0.35 * Math.exp(-(x * x / 3 + (z - 4.5) ** 2 / 2));
      return 1.3 - 0.24 * z + banks + ridge + pool;
    };
    const flow = createSurfaceFlow({ preset: lava ? 'lava' : 'blood', size: [9, 14], resolution: lava ? 80 : 56, heightMap: bed,
      viscosity: lava ? 5 : 1, mobility: lava ? 8 : 14, coolingRate: lava ? 0.035 : 0,
      color: lava ? 0x51443e : 0x780814, emissiveIntensity: lava ? 4.2 : 0,
      initialDepth: lava ? (x, z) => {
        const radius = Math.hypot(x / 0.95, (z + 5.3) / 1.5);
        return radius < 1 ? 0.55 * (1 - radius * radius) ** 2 : 0;
      } : 0,
      sources: [{ x: 0, z: -5.6, radius: lava ? 1.05 : 0.75, volume: lava ? 0.25 : 0.55, rate: lava ? 1.05 : 0.35, duration: lava ? 9 : 7 }] });
    const terrain = createSurfaceFlowTerrain(flow, { color: lava ? 0x34333a : 0x999d9b, roughness: lava ? 0.97 : 0.8 });
    host.add(terrain); host.add(flow); flow.userData.attachToHost(host);
    const plinth = createRoundedBox({ width: 9.5, height: 0.35, depth: 14.5, material: new THREE.MeshStandardMaterial({ color: 0x273541, roughness: 0.7 }) }); plinth.position.y = -0.7; host.add(plinth);
    for (let i = 0; i < 22; i++) {
      const x = (random() < 0.5 ? -1 : 1) * (2.5 + random() * 1.5), z = (random() - 0.5) * 12;
      const rock = new THREE.Mesh(new THREE.DodecahedronGeometry(0.25 + random() * 0.4, 0), new THREE.MeshStandardMaterial({ color: lava ? 0x34333a : 0x747d79, roughness: 0.95, flatShading: true }));
      rock.position.set(x, bed(x, z) + 0.1, z); rock.scale.set(1.2, 0.55, 0.8); rock.castShadow = true; host.add(rock);
    }
    if (lava) {
      grid.visible = false;
      host.scene.traverse(object => {
        if (object.isDirectionalLight) object.intensity = 1.6;
        if (object.isHemisphereLight) object.intensity = 1.0;
      });
      const thermal = Array.from({ length: 4 }, () => {
        const light = new THREE.PointLight(0xff581c, 0, 6, 2); host.add(light);
        const smoke = particles({ ...A3GameVfxPreset.SMOKE_PLUME, map: smokeTexture(), maxParticles: 50,
          lifetime: [1.7, 3.2], size: [0.25, 0.65], sizeOverLife: [0.6, 2.0],
          colorStart: ['#8b7770'], colorEnd: ['#434349'], opacityOverLife: [0.10, 0],
          speed: [0.25, 0.5], gravity: [0, 0.3, 0], windResponse: 0.35, emissionOverTime: 0 }, [0, 0, 0]);
        const sparks = particles({ maxParticles: 24, lifetime: [0.5, 1.3], size: [0.018, 0.04],
          colorStart: ['#ffce78'], colorEnd: ['#9e2506'], opacityOverLife: [0.65, 0],
          speed: [0.15, 0.55], gravity: [0, 0.22, 0], windResponse: 0.3,
          direction: [[-0.15, 0.15], [0.8, 1], [-0.15, 0.15]], emissionOverTime: 0, blending: 'additive' }, [0, 0, 0]);
        return { light, smoke, sparks };
      });
      let thermalTime = 0;
      host.onTick((dt, time) => {
        thermalTime += dt;
        if (thermalTime < 0.12) return;
        thermalTime %= 0.12;
        const state = flow.userData.getState();
        const bands = thermal.map(() => ({ weight: 0, x: 0, y: 0, z: 0 }));
        for (let i = 0; i < state.depth.length; i++) {
          if (state.depth[i] < 0.02) continue;
          const x = -4.5 + (i % state.resolution + 0.5) * state.dx;
          const z = -7 + (Math.floor(i / state.resolution) + 0.5) * state.dz;
          const weight = state.depth[i] * state.cellArea * Math.max(0, state.temperature[i] - 0.3) ** 2;
          const band = bands[Math.min(3, Math.floor((z + 7) / 3.5))];
          band.weight += weight; band.x += x * weight; band.z += z * weight;
          band.y += (state.bed[i] + state.depth[i]) * weight;
        }
        bands.forEach((band, i) => {
          const { light, smoke, sparks } = thermal[i];
          light.intensity = Math.min(24, band.weight * 22) * (0.97 + 0.03 * Math.sin(time * 1.8 + i));
          if (band.weight < 0.01) return;
          light.position.set(band.x / band.weight, band.y / band.weight + 0.65, band.z / band.weight);
          const emission = light.position.clone(); emission.y -= 0.55;
          smoke.emit(1, { position: emission });
          if (band.weight > 0.15 && random() < 0.25) sparks.emit(1, { position: emission });
        });
      });
    }
    if (!lava) {
      const axis = new THREE.ArrowHelper(new THREE.Vector3(0, -0.24, 1).normalize(), new THREE.Vector3(-3.2, 3.5, -3), 3, 0xd47e84, 0.35, 0.2); host.add(axis);
      label('重力方向 / 洼地汇集', [0, 0.7, 6.4], '#cdb4b8');
    }
    renderUpdates.push(() => { const s = flow.userData.getState(); metrics.textContent = `流体体积  ${s.volume.toFixed(3)} m³    ${lava ? `最大厚度  ${s.maxDepth.toFixed(2)} m` : `湿润单元  ${s.wetCells}`}\n守恒误差  ${s.massError.toExponential(1)} m³    ${lava ? `热量比例  ${(s.meanTemperature * 100).toFixed(0)}%` : '低黏度 / 湿润反光'}`; });
    evidence.flow = flow;
    cameraStart.set(lava ? 6.8 : 12, lava ? 7.6 : 12, lava ? 8.8 : 17);
    target.set(0, lava ? 2.0 : 1.6, lava ? -2.8 : 0);
  }
  host.camera.position.copy(cameraStart); host.camera.lookAt(target);
  host.onRender(() => {
    const t = host.elapsedSeconds;
    host.camera.position.copy(cameraStart).applyAxisAngle(new THREE.Vector3(0, 1, 0), Math.sin(t * 0.16) * 0.055);
    host.camera.lookAt(target);
    document.querySelector('#time').textContent = `${t.toFixed(2)} s`;
    for (const update of renderUpdates) update();
  });
  host.tick(0);
  window.demo = { host, evidence, errors, snapshot() {
    const s = evidence.flow?.userData.getState();
    return { mode, time: host.elapsedSeconds, wind: host.wind.getState(), entries: evidence.entries,
      smoke: evidence.smoke?.getState(), fire: evidence.fire?.getState(), arc: evidence.arc?.userData.getState(),
      flow: s ? { volume: s.volume, massError: s.massError, wetCells: s.wetCells, maxDepth: s.maxDepth, centerOfMass: s.centerOfMass, meanTemperature: s.meanTemperature } : null,
      bodies: evidence.bodies.map(b => ({ mass: b.mass, position: b.object.position.toArray(), submerged: b.submergedFraction, grounded: b.grounded })), errors };
  } };
  window.ready = true;
  if (!new URLSearchParams(location.search).has('record')) host.start();
}
main().catch(error => { window.bootError = String(error.stack ?? error); document.querySelector('#error').textContent = window.bootError; console.error(error); });
