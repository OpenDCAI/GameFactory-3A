// A level, two-actor composition with optional follow softness and damage feedback.
export function createCameraController(THREE, camera) {
  const originalPosition = camera.position.clone();
  const originalQuaternion = camera.quaternion.clone();
  const originalFov = camera.fov;
  const originalUp = camera.up.clone();
  const center = new THREE.Vector3();
  const previousGoal = new THREE.Vector3();
  const goal = new THREE.Vector3();
  const offset = new THREE.Vector3(0, 4.8, 9);
  const lookAt = new THREE.Vector3();
  const displacement = new THREE.Vector3();
  let enabled = true;
  let intensity = 1;
  let reducedMotion = false;
  let initialized = false;
  let paused = false;
  let disposed = false;
  let kick = 0;

  function render() {
    const feedback = enabled && !reducedMotion ? kick * intensity : 0;
    camera.up.set(0, 1, 0);
    camera.position.copy(center).add(offset);
    camera.position.y += feedback * 0.025;
    lookAt.copy(center);
    camera.lookAt(lookAt);
    const fov = 55 + feedback * 0.45;
    if (camera.fov !== fov) {
      camera.fov = fov;
      camera.updateProjectionMatrix();
    }
    camera.updateMatrixWorld(true);
  }

  return {
    update(deltaSeconds, { player, target, impact, cut = false }) {
      if (disposed || paused || !player) return;
      const dt = Number.isFinite(deltaSeconds) ? Math.max(0, deltaSeconds) : 0;
      // Nearby rivals share the composition; distant targets cannot drag it away.
      goal.copy(player);
      if (target) {
        displacement.copy(target).sub(player);
        const distance = displacement.length();
        if (distance > 6) displacement.multiplyScalar(6 / distance);
        goal.addScaledVector(displacement, 0.5);
      }
      goal.y += 0.9;

      if (cut || !initialized) {
        center.copy(goal);
        previousGoal.copy(goal);
        initialized = true;
        kick = 0;
      } else if (!enabled || reducedMotion || intensity === 0) {
        center.copy(goal);
        previousGoal.copy(goal);
        kick = 0;
      } else if (dt > 0) {
        // Integrate an exponential follower against a linearly moving goal.
        // This accounts for motion during the interval, rather than treating
        // every frame's endpoint as if it existed for the entire interval.
        const rate = 10 / Math.max(0.25, intensity);
        const x = rate * dt;
        const oneMinusDecay = -Math.expm1(-x);
        const ramp = x < 1e-4
          ? x / 2 - x * x / 6 + x * x * x / 24
          : 1 - oneMinusDecay / x;
        center.multiplyScalar(1 - oneMinusDecay);
        center.addScaledVector(previousGoal, oneMinusDecay);
        displacement.copy(goal).sub(previousGoal);
        center.addScaledVector(displacement, ramp);
        previousGoal.copy(goal);
        kick *= Math.exp(-14 * dt);
      }
      if (impact && !cut && enabled && !reducedMotion && intensity > 0) {
        kick = Math.min(1, kick + 0.8);
      }
      render();
    },

    setPreferences(preferences = {}) {
      if (disposed) return;
      if (preferences.enabled !== undefined) enabled = !!preferences.enabled;
      if (preferences.reducedMotion !== undefined) {
        reducedMotion = !!preferences.reducedMotion;
      }
      if (preferences.intensity !== undefined && Number.isFinite(preferences.intensity)) {
        intensity = Math.max(0, Math.min(1, preferences.intensity));
      }
      if (!enabled || reducedMotion || intensity === 0) kick = 0;
    },

    pause() {
      if (!disposed) paused = true;
    },

    resume() {
      if (disposed || !paused) return;
      paused = false;
      initialized = false;
      kick = 0;
    },

    dispose() {
      if (disposed) return;
      disposed = true;
      camera.position.copy(originalPosition);
      camera.quaternion.copy(originalQuaternion);
      camera.up.copy(originalUp);
      camera.fov = originalFov;
      camera.updateProjectionMatrix();
      camera.updateMatrixWorld(true);
    }
  };
}
