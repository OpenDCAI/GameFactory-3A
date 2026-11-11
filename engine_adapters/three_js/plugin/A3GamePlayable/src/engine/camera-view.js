/** Camera View 2.0: deterministic strategy evaluation after scene.update(t). */
const object = (v, keys, required, label) => {
  if (!v || typeof v !== 'object' || Array.isArray(v))
    throw new TypeError(`${label} must be an object`);
  for (const k of Object.keys(v))
    if (!keys.includes(k)) throw new TypeError(`Unknown ${label}.${k}`);
  for (const k of required)
    if (!Object.hasOwn(v, k)) throw new TypeError(`Missing ${label}.${k}`);
};
const number = (v, label, min = -Infinity, max = Infinity) => {
  if (typeof v !== 'number' || !Number.isFinite(v) || v < min || v > max)
    throw new RangeError(`Invalid ${label}`);
};
const vector = (v, n, label) => {
  if (!Array.isArray(v) || v.length !== n)
    throw new TypeError(`Invalid ${label}`);
  v.forEach((x) => number(x, label));
};
function validateProjection(p) {
  if (!['perspective', 'orthographic'].includes(p?.type))
    throw new RangeError('Invalid projection type');
  const perspective = p.type === 'perspective';
  const lens = perspective ? 'fov_y_deg' : 'vertical_size';
  object(
    p,
    ['type', lens, 'near', 'far'],
    ['type', lens, 'near', 'far'],
    'projection',
  );
  number(
    p[lens],
    lens,
    perspective ? 10 : Number.MIN_VALUE,
    perspective ? 100 : Infinity,
  );
  number(p.near, 'near', perspective ? Number.MIN_VALUE : 0);
  number(p.far, 'far', Number.MIN_VALUE);
  if (p.near >= p.far) throw new RangeError('near must be smaller than far');
}
function focus(value) {
  object(
    value,
    ['subjects', 'axes', 'anchor'],
    ['subjects', 'axes', 'anchor'],
    'focus',
  );
  vector(value.anchor, 3, 'anchor');
  if (
    !Array.isArray(value.axes) ||
    !value.axes.length ||
    new Set(value.axes).size !== value.axes.length ||
    value.axes.some((x) => !['x', 'y', 'z'].includes(x))
  )
    throw new RangeError('Invalid follow axes');
  if (!Array.isArray(value.subjects) || !value.subjects.length)
    throw new RangeError('Follow subjects required');
  for (const subject of value.subjects) {
    object(subject, ['id', 'weight'], ['id', 'weight'], 'subject');
    if (typeof subject.id !== 'string' || !subject.id)
      throw new TypeError('Subject id required');
    number(subject.weight, 'weight', 0);
  }
  if (
    new Set(value.subjects.map((s) => s.id)).size !== value.subjects.length ||
    value.subjects.reduce((a, s) => a + s.weight, 0) <= 0
  )
    throw new RangeError('Invalid subject weights');
}
function spherical(pose, label) {
  object(
    pose,
    ['target', 'azimuth_deg', 'elevation_deg', 'distance'],
    ['target', 'azimuth_deg', 'elevation_deg', 'distance'],
    label,
  );
  vector(pose.target, 3, 'target');
  number(pose.azimuth_deg, 'azimuth', -180, 180);
  number(pose.elevation_deg, 'elevation', -85, 85);
  number(pose.distance, 'distance', Number.MIN_VALUE);
}
export function validateCameraStrategy(input) {
  object(
    input,
    [
      'schema_version',
      'coordinate_system',
      'projection',
      'strategy',
      'framing',
    ],
    ['schema_version', 'coordinate_system', 'projection', 'strategy'],
    'camera_view',
  );
  if (input.schema_version !== '2.0')
    throw new RangeError('Camera strategy requires schema_version 2.0');
  if (input.coordinate_system !== 'Y_UP_RIGHT_HANDED')
    throw new RangeError('Expected Y_UP_RIGHT_HANDED coordinates');
  validateProjection(input.projection);
  const framing = Object.hasOwn(input, 'framing')
    ? input.framing
    : { required_targets: [], safe_margin: 0.05 };
  object(
    framing,
    ['required_targets', 'safe_margin'],
    ['required_targets', 'safe_margin'],
    'framing',
  );
  if (
    !Array.isArray(framing.required_targets) ||
    framing.required_targets.some((id) => typeof id !== 'string' || !id) ||
    new Set(framing.required_targets).size !== framing.required_targets.length
  )
    throw new RangeError('Framing targets must be unique nonempty IDs');
  number(framing.safe_margin, 'safe_margin', 0, 0.4);
  const s = input.strategy;
  if (s?.type === 'fixed') {
    object(s, ['type', 'pose'], ['type', 'pose'], 'strategy');
    spherical(s.pose, 'pose');
  } else if (s?.type === 'follow') {
    object(
      s,
      ['type', 'focus', 'offset'],
      ['type', 'focus', 'offset'],
      'strategy',
    );
    focus(s.focus);
    vector(s.offset, 3, 'offset');
    if (Math.hypot(s.offset[0], s.offset[2]) < 1e-8)
      throw new RangeError(
        'Follow offset must provide a nonvertical viewing direction',
      );
  } else if (s?.type === 'follow_move') {
    object(
      s,
      ['type', 'focus', 'start_offset', 'end_offset', 'easing'],
      ['type', 'focus', 'start_offset', 'end_offset', 'easing'],
      'strategy',
    );
    focus(s.focus);
    vector(s.start_offset, 3, 'start_offset');
    vector(s.end_offset, 3, 'end_offset');
    const a = s.start_offset,
      d = s.end_offset.map((x, i) => x - a[i]),
      length = d[0] * d[0] + d[2] * d[2];
    const u = length
      ? Math.max(0, Math.min(1, -(a[0] * d[0] + a[2] * d[2]) / length))
      : 0;
    if (Math.hypot(a[0] + u * d[0], a[2] + u * d[2]) < 1e-8)
      throw new RangeError(
        'Follow move must not cross a vertical viewing direction',
      );
  } else if (s?.type === 'orbit') {
    object(
      s,
      ['type', 'start', 'end', 'easing'],
      ['type', 'start', 'end', 'easing'],
      'strategy',
    );
    spherical(s.start, 'start');
    spherical(s.end, 'end');
  } else if (s?.type === 'move') {
    object(
      s,
      ['type', 'start', 'end', 'easing'],
      ['type', 'start', 'end', 'easing'],
      'strategy',
    );
    for (const pose of [s.start, s.end]) {
      object(pose, ['position', 'target'], ['position', 'target'], 'move pose');
      vector(pose.position, 3, 'position');
      vector(pose.target, 3, 'target');
    }
  } else if (s?.type === 'recorded') {
    object(s, ['type', 'frames'], ['type', 'frames'], 'strategy');
    if (!Array.isArray(s.frames) || s.frames.length < 2)
      throw new RangeError('Recorded camera requires at least two frames');
    let previous = -1;
    for (const frame of s.frames) {
      object(
        frame,
        ['time_sec', 'position', 'quaternion'],
        ['time_sec', 'position', 'quaternion'],
        'frame',
      );
      number(frame.time_sec, 'time_sec', 0);
      if (frame.time_sec <= previous)
        throw new RangeError('Track times must be strictly increasing');
      previous = frame.time_sec;
      vector(frame.position, 3, 'position');
      vector(frame.quaternion, 4, 'quaternion');
      if (Math.abs(Math.hypot(...frame.quaternion) - 1) > 1e-5)
        throw new RangeError('Quaternion must have unit length');
    }
    if (s.frames[0].time_sec !== 0)
      throw new RangeError('Track must start at zero');
  } else throw new RangeError('Unknown camera strategy');
  if (
    ['orbit', 'move', 'follow_move'].includes(s.type) &&
    !['linear', 'smootherstep'].includes(s.easing)
  )
    throw new RangeError('Unsupported easing');
  return structuredClone({ ...input, framing });
}
export function compileCameraStrategy(
  T,
  input,
  { scene, aspect, duration_sec, fps },
) {
  const view = validateCameraStrategy(input),
    s = view.strategy;
  number(duration_sec, 'duration_sec', Number.MIN_VALUE);
  number(fps, 'fps', Number.MIN_VALUE);
  number(aspect, 'aspect', Number.MIN_VALUE);
  const p = view.projection;
  const camera =
    p.type === 'perspective'
      ? new T.PerspectiveCamera(p.fov_y_deg, aspect, p.near, p.far)
      : new T.OrthographicCamera(
          (-p.vertical_size * aspect) / 2,
          (p.vertical_size * aspect) / 2,
          p.vertical_size / 2,
          -p.vertical_size / 2,
          p.near,
          p.far,
        );
  camera.up.set(0, 1, 0);
  camera.position.set(0, 0, 1);
  camera.lookAt(0, 0, 0);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
  const lerp = (a, b, u) => a + (b - a) * u,
    mixVector = (a, b, u) => a.map((x, i) => lerp(x, b[i], u));
  const sphericalAt = (pose) => {
    const a = T.MathUtils.degToRad(pose.azimuth_deg),
      e = T.MathUtils.degToRad(pose.elevation_deg),
      r = pose.distance;
    return {
      position: [
        pose.target[0] + r * Math.cos(e) * Math.sin(a),
        pose.target[1] + r * Math.sin(e),
        pose.target[2] + r * Math.cos(e) * Math.cos(a),
      ],
      target: pose.target,
    };
  };
  let subjects;
  if (['follow', 'follow_move'].includes(s.type)) {
    const matches = new Map(
      s.focus.subjects.map((binding) => [binding.id, []]),
    );
    scene?.traverse((object) => {
      matches.get(object.name)?.push(object);
    });
    subjects = s.focus.subjects.map((binding) => {
      const objects = matches.get(binding.id);
      if (!objects.length)
        throw new Error(`Missing follow subject ${binding.id}`);
      if (objects.length !== 1)
        throw new Error(
          `Ambiguous follow subject ${binding.id}: object.name must be unique`,
        );
      return { object: objects[0], weight: binding.weight };
    });
  }
  if (
    s.type === 'recorded' &&
    s.frames.at(-1).time_sec + 1e-8 < duration_sec - 1 / fps
  )
    throw new RangeError('Recorded track does not cover the clip');
  function poseAt(time) {
    number(time, 'time', 0, duration_sec);
    let u = time / duration_sec;
    if (s.easing === 'smootherstep') u = u * u * u * (u * (u * 6 - 15) + 10);
    if (s.type === 'fixed') return sphericalAt(s.pose);
    if (s.type === 'orbit')
      return sphericalAt({
        target: mixVector(s.start.target, s.end.target, u),
        azimuth_deg: lerp(s.start.azimuth_deg, s.end.azimuth_deg, u),
        elevation_deg: lerp(s.start.elevation_deg, s.end.elevation_deg, u),
        distance: lerp(s.start.distance, s.end.distance, u),
      });
    if (s.type === 'move')
      return {
        position: mixVector(s.start.position, s.end.position, u),
        target: mixVector(s.start.target, s.end.target, u),
      };
    if (['follow', 'follow_move'].includes(s.type)) {
      const p = new T.Vector3(),
        sum = subjects.reduce((a, o) => a + o.weight, 0);
      for (const entry of subjects)
        p.addScaledVector(
          entry.object.getWorldPosition(new T.Vector3()),
          entry.weight / sum,
        );
      const target = s.focus.anchor.map((x, i) =>
        s.focus.axes.includes(['x', 'y', 'z'][i]) ? p.getComponent(i) : x,
      );
      const offset =
        s.type === 'follow'
          ? s.offset
          : mixVector(s.start_offset, s.end_offset, u);
      return { position: target.map((x, i) => x + offset[i]), target };
    }
    const frames = s.frames;
    if (time >= frames.at(-1).time_sec)
      return {
        position: frames.at(-1).position,
        quaternion: frames.at(-1).quaternion,
      };
    let lo = 0,
      hi = frames.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (frames[mid].time_sec <= time) lo = mid;
      else hi = mid;
    }
    const a = frames[lo],
      b = frames[hi],
      w = (time - a.time_sec) / (b.time_sec - a.time_sec),
      q = new T.Quaternion(...a.quaternion).slerp(
        new T.Quaternion(...b.quaternion),
        w,
      );
    return {
      position: mixVector(a.position, b.position, w),
      quaternion: q.toArray(),
    };
  }
  function applyAt(time) {
    const pose = poseAt(time);
    camera.position.fromArray(pose.position);
    camera.up.set(0, 1, 0);
    if (pose.quaternion)
      camera.quaternion.fromArray(pose.quaternion).normalize();
    else {
      if (camera.position.distanceTo(new T.Vector3(...pose.target)) < 1e-8)
        throw new RangeError('Camera position and focus coincide');
      camera.lookAt(...pose.target);
    }
    camera.updateProjectionMatrix();
    camera.updateMatrixWorld(true);
    return pose;
  }
  return { camera, view, applyAt, poseAt };
}
