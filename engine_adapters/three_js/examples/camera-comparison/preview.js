import * as THREE from 'three';
import { groups, createTake, step, playerAt, DURATION } from './evaluate.js';

const scene = new THREE.Scene();
scene.background = new THREE.Color('#182332');
scene.add(new THREE.HemisphereLight(0xffffff, 0x334455, 3));
const grid = new THREE.GridHelper(32, 32); scene.add(grid);
const actor = color => {
  const mesh = new THREE.Mesh(new THREE.BoxGeometry(0.6, 1.7, 0.6), new THREE.MeshStandardMaterial({ color }));
  scene.add(mesh); return mesh;
};
const player = actor('#3cb8ff'), target = actor('#ff8840');
const renderers = ['before', 'after'].map(id => {
  const renderer = new THREE.WebGLRenderer({ canvas: document.getElementById(id), antialias: true });
  renderer.setSize(640, 360, false); return renderer;
});
const mode = document.getElementById('mode');
const reduced = document.getElementById('reduced');
const recordButton = document.getElementById('record');
const status = document.getElementById('capture-status');
const captureCanvas = document.createElement('canvas');
captureCanvas.width = 1280; captureCanvas.height = 480;
const context = captureCanvas.getContext('2d');
const mimeType = ['video/mp4;codecs=avc1.42E01E', 'video/mp4', 'video/webm;codecs=vp9', 'video/webm']
  .find(type => globalThis.MediaRecorder?.isTypeSupported(type));
recordButton.disabled = !mimeType || !captureCanvas.captureStream;
let takes = [], handle, start, capture, stream;
const pair = () => groups[mode.value];
function resetTakes(reducedMotion) {
  takes.forEach(t => t.controller.dispose());
  takes = Object.values(pair()).map(factory => createTake(factory, reducedMotion));
}
function reset() {
  cancelAnimationFrame(handle); resetTakes(reduced.checked); start = null;
  Object.keys(pair()).forEach((label, i) => { document.getElementById(i ? 'after-label' : 'before-label').textContent = label; });
}
function render(time) {
  player.position.copy(playerAt(time)); player.position.y = 0.85;
  target.position.copy(player.position).add(new THREE.Vector3(0, 0, 3));
  takes.forEach((take, i) => { step(take, time); renderers[i].render(scene, take.camera); });
  document.getElementById('seek').value = time;
  document.getElementById('report').textContent = JSON.stringify({ time,
    ...Object.fromEntries(Object.keys(pair()).map((key, i) => [key, takes[i].samples.at(-1)])) }, null, 2);
}
function frame(now) {
  start ??= now; const time = Math.min(DURATION, (now - start) / 1000);
  render(time); if (time < DURATION) handle = requestAnimationFrame(frame);
}
function replay() { reset(); handle = requestAnimationFrame(frame); }
recordButton.onclick = () => {
  reset(); let recordingStart, phase = -1;
  const chunks = [], controls = ['mode', 'replay', 'reduced', 'seek', 'record'];
  controls.forEach(id => { document.getElementById(id).disabled = true; });
  stream = captureCanvas.captureStream(30);
  capture = new MediaRecorder(stream, { mimeType, videoBitsPerSecond: 2500000 });
  capture.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
  capture.onstop = () => {
    stream.getTracks().forEach(track => track.stop());
    const blob = new Blob(chunks, { type: capture.mimeType }), link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.href = url; link.download = `camera-${mode.value}.${capture.mimeType.includes('mp4') ? 'mp4' : 'webm'}`;
    link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    status.textContent = ' Recording downloaded';
    controls.forEach(id => { document.getElementById(id).disabled = false; });
  };
  capture.start();
  function recordFrame(now) {
    recordingStart ??= now; const elapsed = Math.min(18, (now - recordingStart) / 1000);
    const nextPhase = elapsed < DURATION ? 0 : elapsed < 10 ? 1 : 2;
    if (phase !== nextPhase) {
      resetTakes(nextPhase === 2);
      if (nextPhase === 1) {
        for (let i = 0; i <= 183; i++) takes.forEach(t => step(t, i / 60));
        takes.forEach(t => t.controller.pause());
      }
      phase = nextPhase;
    }
    render(phase === 0 ? elapsed : phase === 1 ? 3.05 : elapsed - 10);
    context.fillStyle = '#111b27'; context.fillRect(0, 0, 1280, 480);
    context.fillStyle = '#e9f3ff'; context.font = 'bold 20px sans-serif';
    Object.keys(pair()).forEach((label, i) => {
      context.fillText(`${mode.value}: ${label}`, i * 640 + 18, 32);
      context.drawImage(renderers[i].domElement, i * 640, 60);
    });
    context.fillText(['Movement and impacts', 'Paused at t=3.05', 'Reduced motion'][phase], 18, 450);
    status.textContent = ` Recording ${elapsed.toFixed(1)} / 18s`;
    if (elapsed < 18) handle = requestAnimationFrame(recordFrame); else capture.stop();
  }
  handle = requestAnimationFrame(recordFrame);
};
document.getElementById('replay').onclick = replay;
mode.onchange = replay; reduced.onchange = replay;
document.getElementById('seek').oninput = event => {
  const time = Number(event.target.value); reset();
  for (let frame = 0; frame < Math.ceil(time * 60); frame++) takes.forEach(t => step(t, frame / 60));
  render(time);
};
window.addEventListener('pagehide', () => {
  cancelAnimationFrame(handle); takes.forEach(t => t.controller.dispose());
  if (capture?.state === 'recording') capture.stop();
  stream?.getTracks().forEach(track => track.stop());
  renderers.forEach(r => r.dispose()); grid.geometry.dispose(); grid.material.dispose();
  [player, target].forEach(a => { a.geometry.dispose(); a.material.dispose(); });
}, { once: true });
replay();
