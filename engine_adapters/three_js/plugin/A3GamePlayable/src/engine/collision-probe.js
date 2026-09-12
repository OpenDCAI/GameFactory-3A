/**
 * Static triangle collision queries and a kinematic character controller.
 * Sphere sweeps are continuous and two-sided, independent of materials.
 * Characters use an overlapping sphere-chain proxy, not a rigid body or
 * an exact capsule. Skinning/morph deformation needs separate collider meshes.
 */

import * as THREE from 'three';

const DOWN = new THREE.Vector3(0, -1, 0);
const CONTACT_EPSILON = 1e-7;
const SKIN = 1e-5;
const geometryTrees = new WeakMap();

function ignoredObject(object, ignore) {
  for (let current = object; current; current = current.parent) {
    if (ignore.has(current)) return true;
  }
  return false;
}

function worldHitNormal(hit) {
  if (!hit.face) return null;
  const matrix = hit.object.matrixWorld.clone();
  if (hit.instanceId !== undefined && hit.object.isInstancedMesh) {
    const instance = new THREE.Matrix4();
    hit.object.getMatrixAt(hit.instanceId, instance);
    matrix.multiply(instance);
  }
  return hit.face.normal.clone().applyNormalMatrix(
    new THREE.Matrix3().getNormalMatrix(matrix),
  );
}

function geometryTree(geometry) {
  const position = geometry.getAttribute('position');
  if (!position) return null;
  const index = geometry.index;
  const version = position.version ?? position.data?.version ?? 0;
  const cached = geometryTrees.get(geometry);
  const start = Math.max(0, geometry.drawRange.start);
  const end = Math.min(index?.count ?? position.count, start + geometry.drawRange.count);
  if (cached && cached.position === position && cached.index === index &&
      cached.version === version && cached.indexVersion === index?.version &&
      cached.start === start && cached.end === end) return cached.root;
  const triangles = [];
  for (let offset = start; offset + 2 < end; offset += 3) {
    const vertices = [0, 1, 2].map((corner) => new THREE.Vector3().fromBufferAttribute(
      position, index ? index.getX(offset + corner) : offset + corner,
    ));
    const box = new THREE.Box3().setFromPoints(vertices);
    triangles.push({ vertices, box, center: box.getCenter(new THREE.Vector3()), faceIndex: Math.floor(offset / 3) });
  }
  const build = (items) => {
    const box = new THREE.Box3();
    for (const item of items) box.union(item.box);
    if (items.length <= 12) return { box, items };
    const size = box.getSize(new THREE.Vector3());
    const axis = size.x >= size.y && size.x >= size.z ? 'x' : size.y >= size.z ? 'y' : 'z';
    items.sort((a, b) => a.center[axis] - b.center[axis]);
    const middle = Math.floor(items.length / 2);
    return { box, left: build(items.slice(0, middle)), right: build(items.slice(middle)) };
  };
  const root = build(triangles);
  geometryTrees.set(geometry, { position, index, version, indexVersion: index?.version, start, end, root });
  return root;
}

function firstQuadraticRoot(a, b, c) {
  if (a <= Number.EPSILON) return null;
  let discriminant = b * b - 4 * a * c;
  const tolerance = Number.EPSILON * 16 * (b * b + Math.abs(4 * a * c));
  if (discriminant < -tolerance) return null;
  discriminant = Math.max(0, discriminant);
  const q = -0.5 * (b + (b >= 0 ? 1 : -1) * Math.sqrt(discriminant));
  const roots = q === 0 ? [-b / (2 * a)] : [q / a, c / q];
  return Math.min(...roots.filter((time) => time >= -Number.EPSILON * 16));
}

function sweepTriangle(from, delta, radius, triangle, accept) {
  const faceNormal = triangle.getNormal(new THREE.Vector3());
  if (faceNormal.lengthSq() === 0) return;
  const closest = triangle.closestPointToPoint(from, new THREE.Vector3());
  const separation = from.distanceTo(closest);
  const report = (time, point, penetrationDepth = 0) => {
    if (!Number.isFinite(time) || time < -Number.EPSILON * 16 || time > 1 + Number.EPSILON * 16) return;
    const timeOfImpact = THREE.MathUtils.clamp(time, 0, 1);
    const centre = from.clone().addScaledVector(delta, timeOfImpact);
    const normal = centre.clone().sub(point);
    if (normal.lengthSq() > CONTACT_EPSILON * CONTACT_EPSILON) normal.normalize();
    else {
      normal.copy(faceNormal);
      if (normal.dot(delta) > 0) normal.negate();
    }
    accept({ point: point.clone(), normal, centre, timeOfImpact, penetrationDepth });
  };
  if (separation <= radius + CONTACT_EPSILON) {
    report(0, closest, Math.max(0, radius - separation));
  }
  if (delta.lengthSq() === 0) return;
  const signedDistance = faceNormal.dot(from.clone().sub(triangle.a));
  const normalTravel = faceNormal.dot(delta);
  if (Math.abs(normalTravel) > Number.EPSILON) {
    for (const side of [-1, 1]) {
      const time = (side * radius - signedDistance) / normalTravel;
      if (time < 0 || time > 1) continue;
      const point = from.clone().addScaledVector(delta, time).addScaledVector(faceNormal, -side * radius);
      if (triangle.containsPoint(point)) report(time, point);
    }
  }
  for (const [a, b] of [[triangle.a, triangle.b], [triangle.b, triangle.c], [triangle.c, triangle.a]]) {
    const edge = b.clone().sub(a);
    const edgeLengthSq = edge.lengthSq();
    if (edgeLengthSq > 0) {
      const offset = from.clone().sub(a);
      const projection = offset.dot(edge) / edgeLengthSq;
      const along = delta.dot(edge) / edgeLengthSq;
      const perpendicular = offset.addScaledVector(edge, -projection);
      const velocity = delta.clone().addScaledVector(edge, -along);
      const time = firstQuadraticRoot(velocity.lengthSq(), 2 * perpendicular.dot(velocity), perpendicular.lengthSq() - radius * radius);
      const parameter = projection + along * time;
      if (time !== null && parameter >= 0 && parameter <= 1) report(time, a.clone().addScaledVector(edge, parameter));
    }
    const offset = from.clone().sub(a);
    const time = firstQuadraticRoot(delta.lengthSq(), 2 * offset.dot(delta), offset.lengthSq() - radius * radius);
    if (time !== null) report(time, a);
  }
}

/**
 * Walk up the hierarchy for the nearest runtime entity id.
 *
 * A raycast returns the leaf `Mesh`, but identity is attached to the
 * entity root by `A3GameIdentityComponent` or by the scene loader, so
 * every probe result has to resolve it the same way.
 *
 * @param {THREE.Object3D | null} object
 * @returns {string}
 */
export function resolveEntityId(object) {
  let current = object;
  while (current) {
    const entityId = String(
      current.userData?.a3game?.entityId ??
        current.userData?.a3gameWorldEntity?.entityId ??
        '',
    );
    if (entityId) return entityId;
    current = current.parent;
  }
  return '';
}

export class A3GameCollisionProbe {
  /**
   * @param {{targets?: THREE.Object3D[], groundOffset?: number,
   *          stepHeight?: number, radius?: number,
   *          gravity?: number, maxFallSpeed?: number}} [options]
   */
  constructor(options = {}) {
    /** @type {THREE.Object3D[]} */
    this.targets = [...(options.targets ?? [])];
    this.groundOffset = Number(options.groundOffset ?? 0);
    this.stepHeight = Number(options.stepHeight ?? 0.6);
    this.radius = Number(options.radius ?? 0.4);
    this.gravity = Number(options.gravity ?? -18);
    this.maxFallSpeed = Number(options.maxFallSpeed ?? -40);
    this.raycaster = new THREE.Raycaster();
    this.#scratch = {
      origin: new THREE.Vector3(),
      direction: new THREE.Vector3(),
      move: new THREE.Vector3(),
      point: new THREE.Vector3(),
      box: new THREE.Box3(),
      normalMatrix: new THREE.Matrix3(),
    };
  }

  #scratch;

  /** Replace the collision target list, typically from the scene loader. */
  setTargets(targets = []) {
    this.targets = [...targets];
    return this;
  }

  addTarget(target) {
    if (target) this.targets.push(target);
    return this;
  }

  /** Stop treating an object as a collision target. */
  removeTarget(target) {
    const index = this.targets.indexOf(target);
    if (index >= 0) this.targets.splice(index, 1);
    return this;
  }

  /**
   * Find the ground height under a position.
   *
   * @param {THREE.Vector3} position
   * @param {{maxDrop?: number, probeHeight?: number,
   *          ignore?: THREE.Object3D[], targets?: THREE.Object3D[]}} [options]
   * @returns {{hit: boolean, height: number, normal: THREE.Vector3,
   *            object: THREE.Object3D | null, distance: number}}
   */
  sampleGround(position, options = {}) {
    const probeHeight = Number(options.probeHeight ?? this.stepHeight + 0.1);
    const maxDrop = Number(options.maxDrop ?? 50);
    const origin = this.#scratch.origin
      .copy(position)
      .setY(position.y + probeHeight);
    this.raycaster.set(origin, DOWN);
    this.raycaster.near = 0;
    this.raycaster.far = probeHeight + maxDrop;
    const targets = options.targets ?? this.targets;
    const ignore = new Set(options.ignore ?? []);
    for (const target of targets) target?.updateWorldMatrix(true, true);
    const hits = this.raycaster.intersectObjects(targets, true)
      .filter((hit) => !ignoredObject(hit.object, ignore));
    if (hits.length === 0) {
      return {
        hit: false,
        height: position.y,
        normal: new THREE.Vector3(0, 1, 0),
        object: null,
        distance: Infinity,
      };
    }
    const hit = hits[0];
    return {
      hit: true,
      height: hit.point.y + this.groundOffset,
      normal: worldHitNormal(hit) ?? new THREE.Vector3(0, 1, 0),
      object: hit.object,
      distance: hit.distance,
    };
  }

  /**
   * Resolve a horizontal move against walls.
   *
   * Position is the actor's feet; height spans the overlapping sphere
   * chain, with radius clamped to height / 2. At most eight contacts are
   * resolved per move. This does not implement automatic stair climbing.
   *
   * @param {THREE.Vector3} position
   * @param {THREE.Vector3} displacement
   * @param {{height?: number, radius?: number, ignore?: THREE.Object3D[],
   *          targets?: THREE.Object3D[]}} [options]
   * @returns {{move: THREE.Vector3, blocked: boolean,
   *            normal: THREE.Vector3 | null}}
   */
  resolveMove(position, displacement, options = {}) {
    return this.#moveCharacter(position, displacement.clone().setY(0), options, true);
  }

  #sweepCharacter(position, displacement, options, horizontalOnly) {
    const height = Math.max(0.001, Number(options.height ?? 1));
    const radius = Math.min(height / 2, Math.max(0.001, Number(options.radius ?? this.radius)));
    const span = height - 2 * radius;
    const segments = Math.max(1, Math.ceil(span / radius));
    let best = null;
    for (let index = 0; index <= segments; index += 1) {
      const from = position.clone().add(new THREE.Vector3(0, radius + span * index / segments - this.groundOffset, 0));
      const hit = this.sweepSphere(from, from.clone().add(displacement), {
        radius, ignore: options.ignore, targets: options.targets, blockingOnly: true,
        contactFilter: horizontalOnly ? (contact) => contact.normal.x ** 2 + contact.normal.z ** 2 > 1e-10 : undefined,
      });
      if (hit.hit && (!best || hit.timeOfImpact < best.timeOfImpact ||
          (hit.timeOfImpact === best.timeOfImpact && hit.penetrationDepth > best.penetrationDepth))) best = hit;
    }
    return best;
  }

  #moveCharacter(position, displacement, options, horizontalOnly = false) {
    const current = position.clone();
    const remaining = displacement.clone();
    const contacts = [];
    const planes = [];
    for (let iteration = 0; iteration < 8; iteration += 1) {
      const hit = this.#sweepCharacter(current, remaining, options, horizontalOnly);
      if (!hit) {
        current.add(remaining);
        break;
      }
      contacts.push(hit);
      const normal = hit.normal.clone();
      if (horizontalOnly) normal.y = 0;
      const length = normal.length();
      if (length < CONTACT_EPSILON) break;
      normal.divideScalar(length);
      const travel = remaining.length();
      const fraction = Math.max(0, hit.timeOfImpact - SKIN / Math.max(travel, SKIN));
      current.addScaledVector(remaining, fraction);
      remaining.multiplyScalar(1 - fraction);
      if (hit.penetrationDepth > CONTACT_EPSILON) {
        current.addScaledVector(normal, (hit.penetrationDepth + SKIN) / length);
      }
      if (!horizontalOnly && options.stopOnFloor && normal.y >= 0.65 && remaining.y < 0) {
        remaining.set(0, 0, 0);
      }
      planes.push(normal);
      for (let pass = 0; pass < planes.length + 1; pass += 1) {
        for (const plane of planes) {
          const into = remaining.dot(plane);
          if (into < 0) remaining.addScaledVector(plane, -into);
        }
      }
      if (remaining.dot(displacement) < 0) remaining.set(0, 0, 0);
      if (remaining.lengthSq() < CONTACT_EPSILON ** 2 && hit.penetrationDepth <= CONTACT_EPSILON) break;
    }
    return {
      move: current.sub(position), blocked: contacts.length > 0,
      normal: contacts.at(-1)?.normal.clone() ?? null, contacts,
    };
  }

  /**
   * Integrate a simple grounded character step.
   *
   * @param {{position: THREE.Vector3, velocityY: number, grounded: boolean}} state
   * @param {THREE.Vector3} displacement horizontal move for this frame
   * @param {number} deltaSeconds
   * @param {{height?: number, jumpImpulse?: number, jump?: boolean}} [options]
   */
  stepCharacter(state, displacement, deltaSeconds, options = {}) {
    const delta = Math.max(0, Number(deltaSeconds) || 0);
    const support = state.grounded ? this.sampleGround(state.position, {
      probeHeight: this.stepHeight + Math.abs(this.groundOffset),
      ignore: options.ignore, targets: options.targets,
    }) : null;
    const radius = Math.min(Number(options.height ?? 1) / 2, Number(options.radius ?? this.radius));
    const supportGap = support ? state.position.y - support.height : Infinity;
    const onSlope = support?.hit && support.normal.y >= 0.65 && support.normal.y < 0.999999 &&
      supportGap >= -SKIN && supportGap <= radius * (1 / support.normal.y - 1) + 0.02;
    const tangent = displacement.clone().setY(0);
    if (onSlope) tangent.y = -(support.normal.x * tangent.x + support.normal.z * tangent.z) / support.normal.y;
    const resolved = onSlope
      ? this.#moveCharacter(state.position, tangent, options)
      : this.resolveMove(state.position, tangent, options);
    state.position.add(resolved.move);
    let velocityY = Number(state.velocityY ?? 0);
    if (options.jump && state.grounded) velocityY = Number(options.jumpImpulse ?? 6.5);
    const initialVelocity = Math.max(this.maxFallSpeed, velocityY);
    velocityY = Math.max(this.maxFallSpeed, initialVelocity + this.gravity * delta);
    const accelerationTime = this.gravity < 0
      ? Math.min(delta, Math.max(0, (this.maxFallSpeed - initialVelocity) / this.gravity)) : delta;
    const verticalDistance = initialVelocity * accelerationTime +
      0.5 * this.gravity * accelerationTime ** 2 + velocityY * (delta - accelerationTime);
    const vertical = this.#moveCharacter(state.position, new THREE.Vector3(0, verticalDistance, 0), {
      ...options, stopOnFloor: true,
    });
    state.position.add(vertical.move);
    const floorContact = vertical.contacts.find((hit) => hit.normal.y >= 0.65);
    const ceilingContact = vertical.contacts.find((hit) => hit.normal.y <= -0.01);
    if ((floorContact && verticalDistance <= 0) || (ceilingContact && verticalDistance > 0)) velocityY = 0;
    state.grounded = Boolean(floorContact && verticalDistance <= 0);
    const ground = this.sampleGround(state.position, {
      probeHeight: 0.02 + Math.abs(this.groundOffset), ignore: options.ignore, targets: options.targets,
    });
    const gap = state.position.y - ground.height;
    if (velocityY <= 0 && ground.hit && ground.normal.y >= 0.65 && gap >= -SKIN && gap <= 0.02) {
      const snap = this.#moveCharacter(state.position, new THREE.Vector3(0, -Math.max(0, gap), 0), {
        ...options, stopOnFloor: true,
      });
      state.position.add(snap.move);
      state.grounded = true;
      velocityY = 0;
    }
    state.velocityY = velocityY;
    return {
      grounded: state.grounded,
      blocked: resolved.blocked || Boolean(ceilingContact),
      groundHeight: ground.height,
      normal: resolved.normal ?? vertical.normal,
      contacts: [...resolved.contacts, ...vertical.contacts],
    };
  }

  /**
   * Resolve a hitscan shot from an origin along a direction.
   *
   * @param {THREE.Vector3} origin
   * @param {THREE.Vector3} direction
   * @param {{range?: number, ignore?: THREE.Object3D[],
   *          targets?: THREE.Object3D[]}} [options]
   * @returns {{hit: boolean, point: THREE.Vector3 | null,
   *            object: THREE.Object3D | null, distance: number,
   *            entityId: string}}
   */
  hitscan(origin, direction, options = {}) {
    this.raycaster.set(origin, direction.clone().normalize());
    this.raycaster.near = 0;
    this.raycaster.far = Number(options.range ?? 200);
    const ignore = new Set(options.ignore ?? []);
    const hits = this.raycaster
      .intersectObjects(options.targets ?? this.targets, true)
      .filter((item) => {
        let current = item.object;
        while (current) {
          if (ignore.has(current)) return false;
          current = current.parent;
        }
        return true;
      });
    if (hits.length === 0) {
      return {
        hit: false,
        point: null,
        object: null,
        distance: Infinity,
        entityId: '',
        normal: null,
      };
    }
    const hit = hits[0];
    // The surface normal, in world space. `Raycaster` reports the face
    // normal in the hit object's local space, which is not what an impact
    // effect needs: sparks have to fly away from the wall as the wall is
    // oriented in the world, and a rotated prop would otherwise scatter
    // them sideways. Null for geometry without faces, for example a line
    // or a bare marker.
    const normal = worldHitNormal(hit);
    return {
      hit: true,
      point: hit.point.clone(),
      object: hit.object,
      distance: hit.distance,
      entityId: resolveEntityId(hit.object),
      normal,
    };
  }

  /**
   * Find every target whose world bounds overlap a sphere.
   *
   * Ray casts answer "what is in front of me"; pickups, melee arcs,
   * chest proximity, checkpoints, and explosion radius all need "what is
   * near me" instead. Bounding boxes keep this allocation-light and are
   * exact enough for gameplay volumes.
   *
   * A target that carries no geometry — a bare `Object3D` used as a
   * marker, which is what world spawn points and checkpoints are — is
   * treated as a point at its world position rather than skipped.
   *
   * @param {THREE.Vector3} center
   * @param {number} radius
   * @param {{targets?: THREE.Object3D[], ignore?: THREE.Object3D[],
   *          requireEntityId?: boolean}} [options]
   * @returns {{object: THREE.Object3D, entityId: string,
   *            distance: number}[]} sorted nearest first
   */
  overlapSphere(center, radius, options = {}) {
    const ignore = new Set(options.ignore ?? []);
    const limit = Math.max(0, Number(radius) || 0);
    const found = [];
    for (const target of options.targets ?? this.targets) {
      if (!target || ignore.has(target)) continue;
      target.updateWorldMatrix?.(true, false);
      const box = this.#scratch.box.setFromObject(target);
      let distance;
      if (box.isEmpty()) {
        distance = this.#scratch.point
          .setFromMatrixPosition(target.matrixWorld)
          .distanceTo(center);
      } else {
        distance = box.clampPoint(center, this.#scratch.point).distanceTo(center);
      }
      if (distance > limit) continue;
      const entityId = resolveEntityId(target);
      if (options.requireEntityId && !entityId) continue;
      found.push({ object: target, entityId, distance });
    }
    found.sort((left, right) => left.distance - right.distance);
    return found;
  }

  /**
   * Move a small sphere along a segment and report the first blocker.
   *
   * Exact face/edge/vertex TOI against static triangle surfaces. A cached
   * geometry-local BVH culls triangles before world-space narrow phase;
   * set position/index.needsUpdate after editing geometry to rebuild it.
   * Object and instance transforms are read afresh on each query. Mesh
   * interiors are not filled solids: initial overlap means touching a
   * surface, not containment entirely inside a closed mesh.
   *
   * point is the surface contact; centre is the sphere centre at contact;
   * distance is centre travel, NOT ray-to-surface distance. TOI is [0, 1].
   * Static/initial contacts return TOI=0. A miss returns Infinity for both
   * distance and TOI. blockingOnly excludes tangent/separating contacts
   * without penetration and is used by the kinematic movement solver.
   *
   * @param {THREE.Vector3} from
   * @param {THREE.Vector3} to
   * @param {{radius?: number, ignore?: THREE.Object3D[],
   *          targets?: THREE.Object3D[], blockingOnly?: boolean,
   *          contactFilter?: (contact: object) => boolean}} [options]
   * @returns {{hit: boolean, point: THREE.Vector3 | null,
   *            centre: THREE.Vector3 | null, normal: THREE.Vector3 | null,
   *            object: THREE.Object3D | null, distance: number,
   *            timeOfImpact: number, penetrationDepth: number,
   *            entityId: string, faceIndex?: number, instanceId?: number}}
   */
  sweepSphere(from, to, options = {}) {
    const direction = this.#scratch.direction.copy(to).sub(from);
    const travelled = direction.length();
    const miss = {
      hit: false,
      point: null,
      normal: null,
      object: null,
      distance: Infinity,
      entityId: '',
    };
    miss.timeOfImpact = Infinity;
    miss.centre = null;
    miss.penetrationDepth = 0;
    const radius = Math.max(0, Number(options.radius ?? 0.1) || 0);
    const delta = direction.clone();
    const queryBox = new THREE.Box3().setFromPoints([from, to]).expandByScalar(radius + CONTACT_EPSILON);
    const ignore = new Set(options.ignore ?? []);
    const visited = new Set();
    let best = miss;
    const inspectMesh = (object) => {
      if (!object.isMesh || !object.geometry || !this.raycaster.layers.test(object.layers)) return;
      const tree = geometryTree(object.geometry);
      if (!tree || tree.box.isEmpty()) return;
      const count = object.isInstancedMesh ? object.count : 1;
      for (let instanceId = 0; instanceId < count; instanceId += 1) {
        const matrix = object.matrixWorld.clone();
        if (object.isInstancedMesh) {
          const instance = new THREE.Matrix4();
          object.getMatrixAt(instanceId, instance);
          matrix.multiply(instance);
        }
        if (matrix.determinant() === 0) continue;
        if (!tree.box.clone().applyMatrix4(matrix).intersectsBox(queryBox)) continue;
        const localQuery = queryBox.clone().applyMatrix4(matrix.clone().invert());
        const visitNode = (node) => {
          if (!node.box.intersectsBox(localQuery)) return;
          if (!node.items) {
            visitNode(node.left);
            visitNode(node.right);
            return;
          }
          for (const item of node.items) {
            if (!item.box.intersectsBox(localQuery)) continue;
            const triangle = new THREE.Triangle(...item.vertices.map((point) => point.clone().applyMatrix4(matrix)));
            sweepTriangle(from, delta, radius, triangle, (contact) => {
              if (options.blockingOnly && contact.penetrationDepth <= CONTACT_EPSILON &&
                  contact.normal.dot(delta) >= -CONTACT_EPSILON) return;
              if (options.contactFilter && !options.contactFilter(contact)) return;
              if (contact.timeOfImpact > best.timeOfImpact) return;
              if (contact.timeOfImpact === best.timeOfImpact && best.hit &&
                  contact.penetrationDepth <= best.penetrationDepth) return;
              best = {
                hit: true, ...contact, object, distance: travelled * contact.timeOfImpact,
                entityId: resolveEntityId(object), faceIndex: item.faceIndex,
                ...(object.isInstancedMesh ? { instanceId } : {}),
              };
            });
          }
        };
        visitNode(tree);
      }
    };
    const visit = (object) => {
      if (!object || visited.has(object) || ignoredObject(object, ignore)) return;
      visited.add(object);
      inspectMesh(object);
      for (const child of object.children) visit(child);
    };
    for (const target of options.targets ?? this.targets) {
      if (!target || ignoredObject(target, ignore)) continue;
      target.updateWorldMatrix(true, true);
      visit(target);
    }
    return best;
  }
}
