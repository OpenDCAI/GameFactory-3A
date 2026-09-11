/**
 * Boot sequence for the exploration example.
 *
 * Shows the wiring order every generated game uses, with the two things an
 * exploration game adds on top of it:
 *
 *   1. host + assets + world (framework)
 *   2. the world, which is both the visible mesh and the ground query
 *   3. HUD widgets
 *   4. entity factory registration
 *   5. local input router piped into the session
 *   6. a follow camera that owns the yaw movement is relative to
 *
 * The bow is bound to a **key**, not to the right mouse button. A held
 * mouse button is invisible in a control panel, collides with the browser
 * context menu, and is awkward on a trackpad; `KeyQ` sits next to WASD and
 * can be written down.
 *
 * The camera rig and the spawner live here rather than in their own
 * modules, because both are part of *this game's* wiring: the camera is
 * only meaningful next to the input that drives it, and the spawner exists
 * only to hand the runtime an explorer. Modules are split by what a thing
 * *is* in the game — the explorer, the arrows, the world — not by which
 * framework interface it happens to implement.
 *
 * Nothing here reaches into the adapter's private modules, and no asset
 * URL is hard coded.
 */

import * as THREE from 'three';
import {
  A3GameCollisionProbe,
  A3GameEntityFactory,
  A3GameInputRouter,
  A3GameLookMode,
  bootA3GameRuntime,
  createAnimatedActor,
  createSunLight,
} from '@a3game/playable';
import { ArrowPool } from './arrow.js';
import { ExplorerEntity } from './explorer.js';
import { buildWorld, terrainHeight } from './world.js';

export { ARROW_PROFILE, ArrowPool } from './arrow.js';
export {
  ExplorerEntity,
  EXPLORER_PROFILE,
  HELD_ANCHORS,
} from './explorer.js';
export {
  buildTerrain,
  buildWorld,
  isBuildable,
  scatterProps,
  terrainHeight,
  WORLD_SIZE,
} from './world.js';

const CAMERA_DISTANCE = 6.2;
const CAMERA_HEIGHT = 2.4;

const CONTROLS_TEXT = [
  'WASD — move (relative to the camera)',
  'Mouse — hold and drag to look',
  'Shift — sprint (costs stamina)',
  'Space — jump',
  'Q — hold to draw the bow, release to shoot',
].join('\n');

/**
 * Candidate character art, best first.
 *
 * A list, and handed to `createAnimatedActor` rather than to
 * `assets.tryInstantiate`, because "is it staged" and "can it be animated"
 * are different questions. `tryInstantiate` stops at the first staged
 * candidate, which is right for a prop; a character has to be *rigged*,
 * and a generated humanoid can only be auto-rigged, which fails on a mesh
 * that was reconstructed holding something. So each candidate is tried and
 * the first that ends up with clips wins.
 */
const CHARACTER_ART = ['explorer_hero', 'explorer_ranger', 'robot_expressive'];

/**
 * A third-person camera rig.
 *
 * Owns yaw and pitch, and writes yaw back onto the explorer so movement
 * and the camera cannot disagree about which way "forward" is. Getting
 * this wrong is the single most common reason a third-person game feels
 * bad, in four specific ways:
 *
 * - it must **lag**, or every small correction of the character shakes the
 *   whole screen;
 * - it must lag *positionally* but track *rotationally* without lag, or
 *   aiming feels like steering a boat;
 * - it must **rise over terrain**, or walking uphill buries the view in
 *   the hillside — which is why it samples the same `terrainHeight` the
 *   character walks on;
 * - it must publish the yaw, because two objects deriving it separately
 *   drift apart within seconds.
 */
export class FollowCamera {
  /**
   * @param {{camera: THREE.Camera, target?: object, distance?: number,
   *          height?: number, lag?: number, minPitch?: number,
   *          maxPitch?: number, sensitivity?: number}} options
   */
  constructor(options) {
    this.camera = options.camera;
    this.target = options.target ?? null;
    this.distance = Number(options.distance ?? CAMERA_DISTANCE);
    this.height = Number(options.height ?? CAMERA_HEIGHT);
    this.lag = Number(options.lag ?? 9);
    this.minPitch = Number(options.minPitch ?? -0.55);
    this.maxPitch = Number(options.maxPitch ?? 0.85);
    this.sensitivity = Number(options.sensitivity ?? 1);
    this.yaw = 0;
    this.pitch = 0.22;
    this.focus = new THREE.Vector3();
    this.#scratch = new THREE.Vector3();
  }

  /** @param {object} explorer an `ExplorerEntity` */
  follow(explorer) {
    this.target = explorer;
    if (explorer) this.focus.copy(explorer.object.position);
    return this;
  }

  /**
   * Consume a look delta, in radians.
   *
   * Pitch is clamped rather than wrapped: a third-person camera that can
   * pass over the top of the character inverts the controls at the moment
   * the player is least able to recover from it.
   */
  look(deltaYaw, deltaPitch) {
    this.yaw -= Number(deltaYaw) * this.sensitivity;
    this.pitch = THREE.MathUtils.clamp(
      this.pitch + Number(deltaPitch) * this.sensitivity,
      this.minPitch,
      this.maxPitch,
    );
    return this;
  }

  update(delta) {
    if (!this.target) return;

    // Yaw is published before it is used, so the explorer's movement basis
    // this frame is the camera's yaw this frame.
    this.target.cameraYaw = this.yaw;

    // Positional lag only. An exponential ease is frame-rate independent
    // enough for a camera and needs no velocity state.
    const chase = Math.min(1, delta * this.lag);
    this.focus.lerp(
      this.#scratch
        .copy(this.target.object.position)
        .setY(this.target.object.position.y + 1.2),
      chase,
    );

    const horizontal = Math.cos(this.pitch) * this.distance;
    const desired = new THREE.Vector3(
      this.focus.x + Math.sin(this.yaw) * horizontal,
      this.focus.y + this.height + Math.sin(this.pitch) * this.distance,
      this.focus.z + Math.cos(this.yaw) * horizontal,
    );

    // Never below the ground it is flying over. Cheaper and steadier than
    // a raycast, and it uses the authoritative height field.
    const floor = terrainHeight(desired.x, desired.z) + 0.9;
    desired.y = Math.max(desired.y, floor);

    this.camera.position.copy(desired);
    this.camera.lookAt(this.focus);
  }

  #scratch;
}

/** Spawns the single locally controlled explorer. */
export class ExplorerFactory extends A3GameEntityFactory {
  /**
   * @param {{collision?: object, profile?: object,
   *          onEntityEvent?: (event: object) => void}} [options]
   */
  constructor(options = {}) {
    super();
    this.collision = options.collision ?? null;
    this.profile = options.profile ?? {};
    this.onEntityEvent = options.onEntityEvent ?? null;
    /** @type {Map<string, ExplorerEntity>} */
    this.entities = new Map();
  }

  async spawnRuntimeEntity(request, { host }) {
    const explorer = new ExplorerEntity({
      host,
      collision: this.collision,
      entityId: request.entityId,
      profile: this.profile,
    });
    explorer.placeAt(request.transform?.position ?? { x: 0, y: 0, z: 0 });
    if (this.onEntityEvent) explorer.onEvent(this.onEntityEvent);
    this.entities.set(request.entityId || explorer.object.name, explorer);
    return explorer;
  }
}

/**
 * Start the example.
 *
 * @param {{container?: string, hudContainer?: string, worldUrl?: string,
 *          manifestUrl?: string, characterArt?: string[], seed?: number}}
 *        [options]
 */
export async function startExplorer(options = {}) {
  const runtimeContext = await bootA3GameRuntime({
    container: options.container ?? '#a3game-viewport',
    hudContainer: options.hudContainer ?? '#a3game-hud',
    manifestUrl: options.manifestUrl,
    worldUrl: options.worldUrl,
    hostOptions: { fov: 62, clearColor: 0x8fb4d4 },
    autoBeginPlay: false,
    autoStart: false,
  });
  const { host, assets, session, runtime, hud } = runtimeContext;

  // A stylised sky dome, not `preset: 'sky'`. three.js's Sky is the
  // Preetham model — physically correct and therefore cloudless — and an
  // empty gradient overhead is the clearest single tell of a generated
  // outdoor scene. The dome is also convolved into the environment map, so
  // what surfaces reflect is what the player sees above them.
  host.setEnvironment({
    preset: 'gradient',
    sunPosition: { x: -0.45, y: 0.42, z: -0.78 },
    sky: {
      zenith: 0x2f6fbd,
      horizon: 0xd6e4ee,
      ground: 0x5d6a52,
      cloudCoverage: 0.45,
      cloudOpacity: 0.9,
      haze: 0.38,
    },
    environmentIntensity: 1,
    toneMapping: 'ACESFilmicToneMapping',
    toneMappingExposure: 0.8,
  });

  const world = buildWorld(host, { segments: 180, seed: options.seed ?? 3 });

  // One sun direction, read back from the sky that was actually painted.
  // Deriving it separately is how shadows end up falling towards the light.
  const sun = createSunLight({
    position: host.getSunPosition(new THREE.Vector3()).multiplyScalar(60),
    radius: 126,
    intensity: 2.6,
    color: 0xfff2d8,
  });
  host.add(sun, 'lights');
  host.setFog({ type: 'Fog', color: 0xd6e4ee, near: 60, far: 380 });

  // The probe resolves movement against the terrain mesh; the height field
  // resolves everything else. Both describe the same surface because the
  // mesh was built from the function.
  const collision = new A3GameCollisionProbe({
    targets: world.collisionTargets,
    stepHeight: 0.6,
    radius: 0.34,
    gravity: -19,
  });

  hud.addBar('stamina', { anchor: 'bottom-left', label: 'STAMINA', value: 1 });
  hud.addBar('draw', { anchor: 'bottom-center', label: 'DRAW', value: 0 });
  hud.addText('arrows', { anchor: 'bottom-right', value: 'Arrows 0' });
  hud.addPanel('controls', { anchor: 'top-left', value: CONTROLS_TEXT });

  const arrows = new ArrowPool({ host, size: 24 });
  const factory = new ExplorerFactory({
    collision,
    onEntityEvent: (event) => {
      if (event.type === 'shot_released') hud.setValue('draw', 0);
    },
  });
  runtime.setEntityFactory(factory);

  const joined = await session.syncSession(
    {
      participant: { participantId: 'local_player' },
      controller: { controllerId: 'local_controller', kind: 'human' },
      binding: { mode: 'exclusive', priority: 10 },
      spawnRequest: {
        entityId: 'explorer_local',
        transform: { position: world.playerSpawn },
      },
    },
    (request) => runtime.spawnEntity(request),
  );
  const explorer = session.getEntity(joined.entityId);

  // Imported character art, when any was staged. The stand-in body is
  // hidden rather than removed, so a later failure can put it back.
  const actor = await createAnimatedActor(
    assets,
    options.characterArt ?? CHARACTER_ART,
    { height: 1.8 },
  );
  if (actor?.object) {
    for (const warning of actor.warnings ?? []) {
      console.warn(`[explorer-example] ${warning}`);
    }
    // The bow stays: it is held equipment placed from HELD_ANCHORS, and
    // the imported mesh has no bow of its own.
    for (const child of [...explorer.body.children]) {
      if (child !== explorer.bow) child.visible = false;
    }
    explorer.body.add(actor.object);
    explorer.actor = actor;
  }

  const followCamera = new FollowCamera({ camera: host.camera }).follow(
    explorer,
  );

  // `DRAG`, not pointer lock: a third-person explorer should not steal the
  // cursor, so look input only applies while a button is held.
  const input = new A3GameInputRouter({
    target: host.container,
    controllerId: joined.controllerId,
    lookMode: A3GameLookMode.POINTER_DRAG,
    actionBindings: { KeyQ: 'draw', KeyE: 'interact' },
  }).enable();
  input.onAction((action, phase) => {
    if (action !== 'draw') return;
    // Pressed and released, not clicked: the interval between them is the
    // charge, so both phases have to be observed.
    if (phase === 'pressed') explorer.startDraw();
    if (phase === 'released') {
      const shot = explorer.releaseDraw();
      if (shot) arrows.fire(shot);
    }
  });
  input.pipeToSession(session, host, { controllerId: joined.controllerId });

  runtime.onWorldBeginPlay();
  const unsubscribe = host.onTick((delta) => {
    arrows.update(delta);
    actor?.update?.(delta);
  });
  const unsubscribeRender = host.onRender((delta) => {
    followCamera.look(input.lookDelta?.x ?? 0, input.lookDelta?.y ?? 0);
    followCamera.update(delta);
    const state = explorer.getState();
    hud.setValue('stamina', state.staminaRatio);
    hud.setValue('draw', state.drawRatio);
    hud.setValue('arrows', `Arrows ${arrows.countActive()}`);
  });

  host.start();

  const context = {
    ...runtimeContext,
    world,
    collision,
    factory,
    arrows,
    followCamera,
    input,
    localEntityId: joined.entityId,
    dispose() {
      unsubscribe();
      unsubscribeRender();
      input.disable();
      arrows.dispose();
      world.dispose();
      hud.dispose();
      runtime.deinitialize();
      assets.dispose();
      host.dispose();
    },
  };
  globalThis.__A3GAME_EXPLORER__ = context;
  return context;
}
