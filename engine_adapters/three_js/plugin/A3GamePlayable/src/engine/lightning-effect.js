import * as THREE from 'three';

/** Seeded electrical arcs: endpoints stay anchored; wind must not advect a bolt. */
export function createLightningArc(options = {}) {
  const group = new THREE.Group();
  const start = new THREE.Vector3().fromArray(options.from ?? [-2, 2, 0]);
  const end = new THREE.Vector3().fromArray(options.to ?? [2, 2, 0]);
  const count = Math.max(4, Math.min(64, Math.trunc(options.segments ?? 24)));
  const geometry = new THREE.CylinderGeometry(1, 1, 1, 5);
  const core = new THREE.MeshBasicMaterial({ color: options.color ?? 0xcdf5ff, toneMapped: false });
  const glow = new THREE.MeshBasicMaterial({ color: 0x278dff, transparent: true, opacity: 0.14, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false });
  const segments = Array.from({ length: count }, () => {
    const outer = new THREE.Mesh(geometry, glow), inner = new THREE.Mesh(geometry, core);
    group.add(outer, inner);
    return { outer, inner };
  });
  const up = new THREE.Vector3(0, 1, 0), delta = new THREE.Vector3(), a = new THREE.Vector3(), b = new THREE.Vector3();
  let elapsed = 0, unsubscribe = null, disposed = false, flashes = 0, wasActive = false;
  const seed = Number(options.seed ?? 7), width = Math.max(0.005, Number(options.width ?? 0.025));
  const noise = (i, frame) => {
    const n = Math.sin(i * 127.1 + frame * 311.7 + seed * 23.7) * 43758.5453123;
    return (n - Math.floor(n)) * 2 - 1;
  };
  const point = (i, frame, target) => {
    const t = i / count, envelope = Math.sin(t * Math.PI);
    target.lerpVectors(start, end, t);
    target.y += noise(i, frame) * envelope * 0.42;
    target.z += noise(i + 41, frame) * envelope * 0.35;
    return target;
  };
  group.userData.update = dt => {
    if (disposed) return;
    elapsed += Math.max(0, Number(dt) || 0);
    const phase = elapsed % (options.period ?? 1.3);
    const active = phase < 0.23 || (phase > 0.32 && phase < 0.4);
    group.visible = active;
    if (active && !wasActive) flashes++;
    wasActive = active;
    const frame = Math.floor(elapsed * 24);
    for (let i = 0; i < count; i++) {
      point(i, frame, a); point(i + 1, frame, b);
      delta.subVectors(b, a);
      for (const [mesh, radius] of [[segments[i].inner, width], [segments[i].outer, width * 5]]) {
        mesh.position.copy(a).add(b).multiplyScalar(0.5);
        mesh.quaternion.setFromUnitVectors(up, delta.clone().normalize());
        mesh.scale.set(radius, delta.length(), radius);
      }
    }
  };
  group.userData.attachToHost = host => { unsubscribe?.(); unsubscribe = host.onTick(group.userData.update); return unsubscribe; };
  group.userData.getState = () => ({ elapsedSeconds: elapsed, flashes, active: group.visible, segments: count });
  group.userData.dispose = () => { if (disposed) return; disposed = true; unsubscribe?.(); geometry.dispose(); core.dispose(); glow.dispose(); };
  group.userData.update(0);
  return group;
}
