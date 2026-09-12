/**
 * `@a3game/playable` - runtime extension contracts for generated
 * three.js gameplay packages.
 *
 * This is the only supported import surface. Generated Gameplay Packages
 * must depend on this barrel, never on deep paths inside the adapter.
 *
 * Layers:
 *   data-types/  generic runtime data contract (wire format)
 *   interfaces/  contracts implemented by generated gameplay
 *   components/  identity and runtime-entity state on Object3D
 *   subsystems/  sessionownership and runtime coordination
 *   engine/      renderer, loop, assets, world, input, animation, HUD
 *
 * The framework provides no Character, Controller, GameMode, weapon,
 * vehicle, combat rule, or game-specific input mapping. Generated
 * projects own concrete gameplay.
 */

import { A3GameRuntimeHost } from './engine/runtime-host.js';
import { A3GameAssetLibrary } from './engine/asset-library.js';
import { A3GameSceneLoader } from './engine/scene-loader.js';
import { A3GameHudLayer } from './engine/hud-layer.js';
import { A3GameWorldSessionSubsystem } from './subsystems/world-session-subsystem.js';
import { A3GameRuntimeSubsystem } from './subsystems/runtime-subsystem.js';

export const A3GAME_PLAYABLE_API_VERSION = 'v1';
export const A3GAME_PLAYABLE_ENGINE = 'three_js';

export {
  A3GameControlMode,
  A3GameLocomotionState,
  A3GameRuntimeCommand,
  createControlBinding,
  createControllerState,
  createEntitySnapshot,
  createEntitySpawnRequest,
  createParticipantInfo,
  createRuntimeInputState,
  createTransform,
  createVector3,
  locomotionStateFromInput,
} from './data-types/runtime-types.js';

export {
  A3GameControllableEntity,
  A3GameEntityFactory,
  A3GameRuntimeMessageHandler,
  CONTROLLABLE_ENTITY_METHODS,
  ENTITY_FACTORY_METHODS,
  RUNTIME_MESSAGE_HANDLER_METHODS,
  assertControllableEntity,
  assertEntityFactory,
  assertRuntimeMessageHandler,
  isControllableEntity,
  isEntityFactory,
  isRuntimeMessageHandler,
} from './interfaces/contracts.js';

export {
  A3GAME_USER_DATA_KEY,
  A3GameIdentityComponent,
  A3GameRuntimeEntityComponent,
} from './components/index.js';

export {
  A3GameRuntimeSubsystem,
  A3GameWorldSessionSubsystem,
} from './subsystems/index.js';

export {
  A3GAME_HUMANOID_CLIP_NAMES,
  A3GAME_RUNTIME_FORWARD_AXIS,
  A3GameAnimationDirector,
  A3GameAssetLibrary,
  A3GameBeamEffect,
  A3GameCollisionProbe,
  A3GameEmitterShape,
  A3GameEnvironmentPreset,
  A3GameForwardAxis,
  A3GameHudLayer,
  A3GameHumanoidBone,
  A3GameInputRouter,
  A3GameLookMode,
  A3GameMaterialPreset,
  A3GameSurfacePattern,
  A3GameMotionLibrary,
  A3GameMotionState,
  A3GameParticleAppearance,
  A3GameParticleBlending,
  A3GameParticleRenderMode,
  A3GameParticleSystem,
  A3GameRuntimeChannel,
  A3GameRuntimeHost,
  A3GameSceneLoader,
  A3GameSourceBoneAliases,
  A3GameTrailRibbon,
  A3GameVfxDirector,
  A3GameVfxPreset,
  A3GameWindField,
  bindVegetationWind,
  createLightningArc,
  A3GameWaterBody,
  createSurfaceFlow,
  createSurfaceFlowTerrain,
  DEFAULT_KEY_BINDINGS,
  alignWeaponModel,
  autoRigHumanoid,
  createAnimatedActor,
  createCloudLayer,
  createContactShadow,
  createDistantRange,
  createFillLight,
  createFacadeTexture,
  createGroundRibbon,
  directionToYaw,
  yawToDirection,
  footprintCorners,
  distanceToPolyline,
  createHumanoidClip,
  createHumanoidClipSet,
  createHumanoidSkeleton,
  createInstancedFromModel,
  createMaterial,
  createRadialGradientTexture,
  createRoundedBox,
  createSeededRandom,
  createSkyGradient,
  createSunLight,
  createSurfaceMaterial,
  createSurfaceTextures,
  createTilingTexture,
  createVfxDirector,
  createWaterSurface,
  disposeObject3D,
  fitToHeight,
  forwardAxisYaw,
  groundObject,
  findRiggedHumanoid,
  measureHumanoid,
  measureObject,
  measureWeapon,
  orientModel,
  prepareModel,
  principalAxes,
  resolveEntityId,
  retargetClipToSkeleton,
} from './engine/index.js';

/**
 * Boot the whole framework in the conventional order.
 *
 * Convenience only: a generated game may wire the pieces itself.
 *
 * Tick ordering matters. The runtime subsystem's tick delivers queued
 * input to entities, so a game that samples local input must register
 * `input.pipeToSession()` *before* `runtime.onWorldBeginPlay()`, or every
 * frame of input arrives one frame late. Pass `autoBeginPlay: false` to
 * take that ordering over, then call `runtime.onWorldBeginPlay()` and
 * `host.start()` yourself.
 *
 * @param {{container: string | HTMLElement,
 *          hudContainer?: string | HTMLElement,
 *          manifestUrl?: string, worldUrl?: string, baseUrl?: string,
 *          worldId?: string, hostOptions?: object,
 *          requireManifest?: boolean, createHud?: boolean,
 *          autoBeginPlay?: boolean, autoStart?: boolean,
 *          entityFactory?: object}} options
 */
export async function bootA3GameRuntime(options = {}) {
  const host = new A3GameRuntimeHost({
    container: options.container,
    hudContainer: options.hudContainer,
    ...(options.hostOptions ?? {}),
  });
  await host.init();

  const assets = new A3GameAssetLibrary({
    baseUrl: options.baseUrl,
    manifestUrl: options.manifestUrl,
    requireManifest: options.requireManifest,
    renderer: host.renderer,
  });
  await assets.load();

  const sceneLoader = new A3GameSceneLoader({ host, assets });
  let world = null;
  if (options.worldUrl) {
    world = await sceneLoader.loadWorld(options.worldUrl);
  }

  // One HUD layer per boot. Reuse `context.hud` instead of constructing
  // a second layer, which would stack two overlay roots on the same
  // container and make `getState()` ambiguous.
  const hud =
    options.hudContainer && options.createHud !== false
      ? new A3GameHudLayer({ container: options.hudContainer })
      : null;

  const session = new A3GameWorldSessionSubsystem({
    worldId: options.worldId ?? world?.worldId ?? 'world_001',
  });
  const runtime = new A3GameRuntimeSubsystem({ host, session, assets });
  if (options.entityFactory) runtime.setEntityFactory(options.entityFactory);
  if (options.autoBeginPlay !== false) runtime.onWorldBeginPlay();
  if (options.autoStart !== false) host.start();

  return { host, assets, sceneLoader, hud, session, runtime, world };
}
