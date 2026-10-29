export function createCameraController(THREE, camera) {
  const baseFov = camera.fov;
  const focus = new THREE.Vector3();
  const previousGoal = new THREE.Vector3();
  const goal = new THREE.Vector3();
  const basePosition = new THREE.Vector3();
  const direction = new THREE.Vector3(7, 5, 8).normalize();
  const right = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 1, 0), direction).normalize();
  const up = new THREE.Vector3().crossVectors(direction, right).normalize();
  const relative = new THREE.Vector3();
  const corner = new THREE.Vector3();
  const offset = new THREE.Vector3();
  let enabled = true, intensity = 1, reducedMotion = false;
  let paused = false, disposed = false, ready = false;
  let impactHeld = false, pulseAge = Infinity;
  const pulseDuration = 0.20;

  function writePose() {
    if (!ready || disposed) return;
    const strength = enabled && !reducedMotion ? intensity : 0;
    const phase = pulseAge / pulseDuration;
    const envelope = phase >= 0 && phase < 1 ? Math.sin(Math.PI * phase) * (1 - phase) : 0;
    offset.copy(right).multiplyScalar(0.025 * envelope * strength);
    offset.addScaledVector(up, 0.012 * envelope * strength);
    camera.position.copy(basePosition);
    camera.up.set(0, 1, 0);
    camera.lookAt(focus);
    camera.position.add(offset);
    const fov = baseFov + 0.35 * envelope * strength;
    if (camera.fov !== fov) {
      camera.fov = fov;
      camera.updateProjectionMatrix();
    }
    camera.updateMatrixWorld(true);
  }

  function distanceForActor(actor, required) {
    const tangentY = Math.tan(THREE.MathUtils.degToRad(baseFov * 0.5)) * 0.80;
    const tangentX = tangentY * Math.max(0.1, camera.aspect);
    for (const x of [-0.3, 0.3]) {
      for (const y of [0, 1.7]) {
        for (const z of [-0.3, 0.3]) {
          corner.set(actor.x + x, actor.y + y, actor.z + z);
          relative.copy(corner).sub(focus);
          const depthOffset = relative.dot(direction);
          required = Math.max(required,
            depthOffset + Math.abs(relative.dot(right)) / tangentX,
            depthOffset + Math.abs(relative.dot(up)) / tangentY,
            depthOffset + camera.near + 0.5);
        }
      }
    }
    return required;
  }

  return {
    update(deltaSeconds, { player, target, impact, cut = false }) {
      if (disposed || paused) return;
      const dt = Number.isFinite(deltaSeconds) ? Math.max(0, deltaSeconds) : 0;
      const hasTarget = target && player.distanceToSquared(target) <= 144;
      goal.copy(player);
      if (hasTarget) goal.lerp(target, 0.45);
      goal.y += 0.85;
      if (cut || !ready) {
        focus.copy(goal);
        previousGoal.copy(goal);
        pulseAge = Infinity;
        impactHeld = Boolean(impact);
        ready = true;
      } else {
        // Exact exponential response for a goal moving linearly within this step.
        // This avoids tuning follow speed to a particular replay frame rate.
        const k = 12 * dt;
        const decay = Math.exp(-k);
        const blend = -Math.expm1(-k);
        const ramp = k > 1e-5 ? 1 - blend / k : k / 2 - k * k / 6;
        focus.multiplyScalar(decay).addScaledVector(previousGoal, blend);
        relative.copy(goal).sub(previousGoal);
        focus.addScaledVector(relative, ramp);
        previousGoal.copy(goal);
        pulseAge += dt;
        if (impact && !impactHeld && enabled && !reducedMotion && intensity > 0) {
          // Replace overlapping pulses; never accumulate feedback into the base.
          pulseAge = 0;
        }
        impactHeld = Boolean(impact);
      }
      let distance = distanceForActor(player, 9.5);
      if (hasTarget) distance = distanceForActor(target, distance);
      basePosition.copy(focus).addScaledVector(direction, distance);
      writePose();
    },
    setPreferences(preferences = {}) {
      if (disposed) return;
      if (preferences.enabled !== undefined) enabled = Boolean(preferences.enabled);
      if (preferences.intensity !== undefined) {
        intensity = Number.isFinite(preferences.intensity)
          ? THREE.MathUtils.clamp(preferences.intensity, 0, 1) : 1;
      }
      if (preferences.reducedMotion !== undefined) reducedMotion = Boolean(preferences.reducedMotion);
      if (!enabled || reducedMotion || intensity === 0) pulseAge = Infinity;
      writePose();
    },
    pause() {
      if (disposed) return;
      paused = true;
      pulseAge = Infinity;
      writePose();
    },
    resume() {
      if (disposed) return;
      paused = false;
      // The first resumed sample is an explicit re-acquisition after lost time.
      ready = false;
      impactHeld = false;
      pulseAge = Infinity;
    },
    dispose() {
      if (disposed) return;
      pulseAge = Infinity;
      writePose();
      disposed = true;
      paused = true;
    }
  };
}
