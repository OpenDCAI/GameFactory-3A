import * as THREE from 'three';
import { A3GameCameraRig, A3GameFeedback } from '@a3game/playable';
import { createArenaCameraProfile } from '../arena-fighter-example/src/camera-profile.js';
import { createCameraController as control } from './control.js';
import { createCameraController as treatment } from './treatment.js';

export const DURATION = 8;
export const factories = { control, treatment };
export const groups = {
  reference: {
    static: () => ({ update() {}, setPreferences() {}, pause() {}, resume() {}, dispose() {} }),
    chase: (_, camera) => {
      const rig = new A3GameCameraRig(camera, createArenaCameraProfile());
      const feedback = new A3GameFeedback({ navigator: {}, window: null, document: null, media: null });
      let paused = false, disposed = false;
      return {
        update(dt, { player, impact, cut }) {
          if (paused || disposed) return;
          if (impact) feedback.trigger({ strength: 0.05 });
          rig.update(dt, player, { ...feedback.update(dt), reducedMotion: feedback.reducedMotion, cut });
        },
        setPreferences(preferences) { feedback.setPreferences(preferences); rig.clear(); },
        pause() { paused = true; feedback.setSuspended(true); rig.clear(); },
        resume() { paused = false; feedback.setSuspended(false); },
        dispose() { if (!disposed) { feedback.dispose(); rig.clear(); disposed = true; } },
      };
    },
  },
  generated: factories,
};

export function playerAt(seconds) {
  return new THREE.Vector3(10 * Math.sin(seconds * Math.PI / 4), 0, 0);
}

function framing(camera, position) {
  const points = [];
  for (const x of [-0.3, 0.3]) for (const y of [0, 1.7]) for (const z of [-0.3, 0.3]) {
    points.push(position.clone().add(new THREE.Vector3(x, y, z)).project(camera));
  }
  const focus = position.clone().add(new THREE.Vector3(0, 1.2, 0)).project(camera);
  return { fullyVisible: points.every(p => Math.abs(p.x) <= 1 && Math.abs(p.y) <= 1 && Math.abs(p.z) <= 1),
    focusError: Math.hypot(focus.x, focus.y),
    x0: Math.min(...points.map(p => p.x)), x1: Math.max(...points.map(p => p.x)),
    y0: Math.min(...points.map(p => p.y)), y1: Math.max(...points.map(p => p.y)) };
}
function overlap(a, b) {
  const intersection = Math.max(0, Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0))
    * Math.max(0, Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0));
  const union = (a.x1 - a.x0) * (a.y1 - a.y0) + (b.x1 - b.x0) * (b.y1 - b.y0) - intersection;
  return union > 0 ? intersection / union : 0;
}
export function createTake(factory, reducedMotion = false) {
  const camera = new THREE.PerspectiveCamera(55, 16 / 9, 0.1, 2000);
  camera.position.set(0, 2, 8); camera.lookAt(0, 1.2, 0);
  const controller = factory(THREE, camera);
  controller.setPreferences({ enabled: true, intensity: 1, reducedMotion });
  return { camera, controller, samples: [], lastTime: 0 };
}
export function step(take, time, impacts = true) {
  if (!Number.isFinite(time) || time < take.lastTime) throw new RangeError('time must advance monotonically');
  const player = playerAt(time), target = player.clone().add(new THREE.Vector3(0, 0, 3));
  const impact = impacts && [3, 6].some(t => take.lastTime < t && time >= t);
  take.controller.update(time - take.lastTime, { player, target, impact, cut: time === 0 });
  take.camera.updateMatrixWorld(true);
  const playerBounds = framing(take.camera, player), targetBounds = framing(take.camera, target);
  take.samples.push({ time, player: playerBounds, target: targetBounds,
    projectedBoundsIou: overlap(playerBounds, targetBounds),
    position: take.camera.position.toArray(), fov: take.camera.fov,
    finite: [...take.camera.position.toArray(), ...take.camera.quaternion.toArray(), take.camera.fov].every(Number.isFinite) });
  take.lastTime = time;
}
export function measure(factory, { fps = 60, reducedMotion = false, impacts = true } = {}) {
  if (!Number.isInteger(fps) || fps < 1) throw new RangeError('fps must be a positive integer');
  const take = createTake(factory, reducedMotion);
  for (let frame = 0; frame <= DURATION * fps; frame++) step(take, frame / fps, impacts);
  const fraction = predicate => take.samples.filter(predicate).length / take.samples.length;
  const report = { fps, reducedMotion, samples: take.samples.length,
    bothFullyInFrameFraction: fraction(s => s.player.fullyVisible && s.target.fullyVisible),
    playerFullyInFrameFraction: fraction(s => s.player.fullyVisible),
    targetFullyInFrameFraction: fraction(s => s.target.fullyVisible),
    meanPlayerFocusErrorNdc: take.samples.reduce((n, s) => n + s.player.focusError, 0) / take.samples.length,
    meanProjectedActorBoundsIou: take.samples.reduce((n, s) => n + s.projectedBoundsIou, 0) / take.samples.length,
    allPosesFinite: take.samples.every(s => s.finite),
    finalPosition: take.camera.position.toArray(), finalFov: take.camera.fov };
  take.controller.dispose();
  return { report, samples: take.samples };
}
export function lifecycle(factory) {
  const take = createTake(factory); step(take, 0);
  const pose = () => [...take.camera.position.toArray(), ...take.camera.quaternion.toArray(), take.camera.fov];
  take.controller.pause(); const pausedPose = pose(); step(take, 1);
  const pausePreventsWrites = JSON.stringify(pausedPose) === JSON.stringify(pose());
  take.controller.resume(); step(take, 2);
  const resumeMoves = JSON.stringify(pausedPose) !== JSON.stringify(pose());
  take.controller.dispose(); const disposedPose = pose(); step(take, 3);
  const disposePreventsWrites = JSON.stringify(disposedPose) === JSON.stringify(pose());
  take.controller.dispose();
  return { pausePreventsWrites, resumeMoves, disposePreventsWrites };
}
function immediateClear(factory, action) {
  const pulse = createTake(factory), base = createTake(factory);
  for (let frame = 0; frame <= 183; frame++) {
    step(pulse, frame / 60); step(base, frame / 60, false);
  }
  action(pulse.controller); action(base.controller);
  const positionError = pulse.camera.position.distanceTo(base.camera.position);
  const fovError = Math.abs(pulse.camera.fov - base.camera.fov);
  const quaternionError = pulse.camera.quaternion.angleTo(base.camera.quaternion);
  pulse.controller.dispose(); base.controller.dispose();
  return { clearsImmediately: positionError < 1e-9 && fovError < 1e-9 && quaternionError < 1e-7,
    positionError, fovError, quaternionError };
}
export function study() {
  return { schema: 'a3game.generated_camera_pair.v1', durationSeconds: DURATION, opponentOffset: [0, 0, 3],
    scope: 'one independent native-agent generation per condition; no statistical or comfort claim',
    conditions: Object.fromEntries(Object.entries(factories).map(([name, factory]) => [name, {
      lifecycle: lifecycle(factory),
      pauseClearsImpact: immediateClear(factory, controller => controller.pause()),
      preferencesClearImpact: immediateClear(factory, controller => controller.setPreferences({ enabled: false })),
      takes: [30, 60, 120].flatMap(fps => [false, true].map(reducedMotion => measure(factory, { fps, reducedMotion }).report)),
      reducedMotionSuppressesImpact: JSON.stringify(measure(factory, { reducedMotion: true }).samples)
        === JSON.stringify(measure(factory, { reducedMotion: true, impacts: false }).samples),
    }])) };
}
