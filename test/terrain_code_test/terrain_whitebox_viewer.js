import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const params = new URLSearchParams(location.search);
const sceneSelect = document.querySelector('#scene');
const variantSelect = document.querySelector('#variant');
const main = document.querySelector('main');
const capture = params.has('capture');
if (capture) document.body.classList.add('capture');
if ([...sceneSelect.options].some(o => o.value === params.get('scene'))) sceneSelect.value = params.get('scene');
if ([...variantSelect.options].some(o => o.value === params.get('variant'))) variantSelect.value = params.get('variant');
let panes = [], generation = 0, angle = 0, last = performance.now();
const loader = new GLTFLoader();
const framing = await fetch('./framing.json').then(response => {
  if (!response.ok) throw new Error('Missing scene framing manifest');
  return response.json();
});

function dispose(pane) {
  pane.controls.dispose();
  pane.world.traverse(object => {
    object.geometry?.dispose();
    if (object.material) for (const material of [object.material].flat()) material.dispose();
  });
  pane.renderer.dispose();
  pane.renderer.forceContextLoss();
}

function draw() {
  for (const pane of panes) pane.renderer.render(pane.world, pane.camera);
}

function frame(rotation = 0) {
  angle = rotation;
  for (const pane of panes) {
    const radius = pane.size * 1.7;
    pane.camera.position.set(pane.center.x + Math.sin(rotation + 0.72) * radius,
      pane.center.y + pane.size * 1.2, pane.center.z + Math.cos(rotation + 0.72) * radius);
    pane.controls.target.copy(pane.center);
    pane.camera.zoom = 1;
    pane.camera.lookAt(pane.center);
    pane.controls.update();
  }
  draw();
}

function resize() {
  for (const pane of panes) {
    const width = pane.element.clientWidth, height = pane.element.clientHeight;
    pane.renderer.setSize(width, height, false);
    const aspect = width / Math.max(height, 1);
    const extent = pane.size * Math.max(0.70, 0.78 / aspect);
    Object.assign(pane.camera, {left: -extent * aspect, right: extent * aspect,
      top: extent, bottom: -extent, near: 0.1, far: pane.size * 15});
    pane.camera.updateProjectionMatrix();
  }
  draw();
}

function materials() {
  for (const pane of panes) pane.model.traverse(object => {
    if (!object.isMesh) return;
    for (const material of [object.material].flat()) {
      material.wireframe = document.querySelector('#wireframe').checked;
      material.color.copy(document.querySelector('#clay').checked ? new THREE.Color(0xe0e4e2) : material.userData.originalColor);
    }
  });
  draw();
}

async function load() {
  const current = ++generation;
  document.body.dataset.ready = 'false';
  delete document.body.dataset.error;
  panes.forEach(dispose); panes = []; main.replaceChildren();
  const variants = variantSelect.value === 'both' ? ['opus', 'gpt6'] : [variantSelect.value];
  main.classList.toggle('single', variants.length === 1);
  try {
    const loaded = await Promise.all(variants.map(async variant => {
      const url = `${variant}/${sceneSelect.value}.glb`;
      const gltf = await loader.loadAsync(url);
      return {variant, url, model: gltf.scene};
    }));
    if (current !== generation) {
      for (const item of loaded) item.model.traverse(o => { o.geometry?.dispose(); if (o.material) for (const m of [o.material].flat()) m.dispose(); });
      return;
    }
    const common = new THREE.Box3();
    for (const item of loaded) common.union(new THREE.Box3().setFromObject(item.model));
    const reference = framing[sceneSelect.value];
    if (reference) common.set(new THREE.Vector3(...reference.min), new THREE.Vector3(...reference.max));
    const center = common.getCenter(new THREE.Vector3());
    const dimensions = common.getSize(new THREE.Vector3());
    const size = Math.max(dimensions.x, dimensions.y, dimensions.z);
    for (const {variant, url, model} of loaded) {
      const element = document.createElement('section');
      const title = sceneSelect.selectedOptions[0].textContent;
      element.innerHTML = `<div class="caption"><h2>${title}</h2><span class="version">${variant === 'opus' ? 'OPUS / BASELINE' : 'GPT-6 / REVISION'}</span></div><div class="status" role="status"></div><a class="download" download href="${url}">Download GLB</a>`;
      main.append(element);
      const renderer = new THREE.WebGLRenderer({antialias: true, preserveDrawingBuffer: true});
      renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = THREE.PCFSoftShadowMap;
      renderer.outputColorSpace = THREE.SRGBColorSpace;
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 0.95;
      element.prepend(renderer.domElement);
      const world = new THREE.Scene(); world.background = new THREE.Color(0xe9eeeb);
      world.add(new THREE.HemisphereLight(0xf9fffc, 0x6e7d78, 1.0));
      const sun = new THREE.DirectionalLight(0xfff9ed, 2.0);
      sun.position.set(-size * 0.6, size * 1.7, size * 0.8);
      sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048);
      Object.assign(sun.shadow.camera, {left: -size, right: size, top: size, bottom: -size, near: 1, far: size * 6});
      sun.shadow.normalBias = size * 0.001; sun.shadow.bias = -0.00015;
      world.add(sun);
      const floor = new THREE.Mesh(new THREE.PlaneGeometry(size * 200, size * 200), new THREE.MeshStandardMaterial({color: 0xe9eeeb, roughness: 1}));
      floor.rotation.x = -Math.PI / 2; floor.position.y = common.min.y - 0.12;
      floor.receiveShadow = true; world.add(floor);
      model.traverse(object => {
        if (!object.isMesh) return;
        object.castShadow = true; object.receiveShadow = true;
        for (const material of [object.material].flat()) material.userData.originalColor = material.color.clone();
      });
      world.add(model);
      const camera = new THREE.OrthographicCamera();
      const controls = new OrbitControls(camera, renderer.domElement);
      controls.maxPolarAngle = Math.PI * 0.49; controls.minZoom = 0.45; controls.maxZoom = 6;
      const pane = {element, renderer, world, model, camera, controls, size, center};
      panes.push(pane);
      controls.addEventListener('change', () => {
        for (const other of panes) if (other !== pane) {
          other.camera.position.copy(camera.position); other.camera.quaternion.copy(camera.quaternion);
          other.camera.zoom = camera.zoom; other.camera.updateProjectionMatrix();
          other.controls.target.copy(controls.target);
        }
        draw();
      });
      const meshes = []; model.traverse(o => { if (o.isMesh) meshes.push(o); });
      element.querySelector('.status').textContent = `${Math.round(size)} m site / ${meshes.length} meshes`;
    }
    resize(); frame(0); materials();
    document.body.dataset.ready = 'true';
  } catch (error) {
    if (current !== generation) return;
    document.body.dataset.error = String(error);
    const message = document.createElement('p'); message.setAttribute('role', 'alert');
    message.textContent = `Scene could not be loaded: ${error.message}`; main.append(message);
  }
}

for (const select of [sceneSelect, variantSelect]) select.addEventListener('change', load);
for (const id of ['wireframe', 'clay']) document.querySelector(`#${id}`).addEventListener('change', materials);
document.querySelector('#reset').addEventListener('click', () => frame(0));
new ResizeObserver(resize).observe(main);
window.demo = {frame, draw, get cameras() {return panes.map(p => p.camera.position.toArray());}};
function animate(now) {
  const dt = Math.min((now - last) / 1000, 0.1); last = now;
  if (!capture && document.querySelector('#rotate').checked) frame(angle + dt * 0.17);
  requestAnimationFrame(animate);
}
requestAnimationFrame(animate);
await load();
