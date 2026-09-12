/** Engine scaffolding exports for the three.js runtime framework. */

export {
  A3GameEnvironmentPreset,
  A3GameRuntimeHost,
  disposeObject3D,
} from './runtime-host.js';
export { A3GameAssetLibrary } from './asset-library.js';
export {
  directionToYaw,
  yawToDirection,
  footprintCorners,
  distanceToPolyline,
  createGroundRibbon,
  createFacadeTexture,
} from './scene-kit.js';
export { A3GameWindField, bindVegetationWind } from './wind-field.js';
export { createLightningArc } from './lightning-effect.js';
export { A3GameWaterBody } from './water-body.js';
export { createSurfaceFlow, createSurfaceFlowTerrain } from './surface-flow.js';
export {
  A3GAME_RUNTIME_FORWARD_AXIS,
  A3GameForwardAxis,
  A3GameMaterialPreset,
  A3GameSurfacePattern,
  alignWeaponModel,
  createCloudLayer,
  createContactShadow,
  createDistantRange,
  createFillLight,
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
  createWaterSurface,
  fitToHeight,
  forwardAxisYaw,
  groundObject,
  measureObject,
  measureWeapon,
  orientModel,
  prepareModel,
  principalAxes,
} from './visual-kit.js';
export { A3GameSceneLoader } from './scene-loader.js';
export {
  A3GameInputRouter,
  A3GameLookMode,
  DEFAULT_KEY_BINDINGS,
} from './input-router.js';
export { A3GameAnimationDirector } from './animation-director.js';
export {
  A3GAME_HUMANOID_CLIP_NAMES,
  A3GameHumanoidBone,
  A3GameMotionLibrary,
  A3GameMotionState,
  A3GameSourceBoneAliases,
  autoRigHumanoid,
  createAnimatedActor,
  createHumanoidClip,
  createHumanoidClipSet,
  createHumanoidSkeleton,
  findRiggedHumanoid,
  measureHumanoid,
  retargetClipToSkeleton,
} from './motion-kit.js';
export {
  A3GameBeamEffect,
  A3GameEmitterShape,
  A3GameParticleAppearance,
  A3GameParticleBlending,
  A3GameParticleRenderMode,
  A3GameParticleSystem,
  A3GameTrailRibbon,
  A3GameVfxDirector,
  A3GameVfxPreset,
  createVfxDirector,
} from './vfx-kit.js';
export {
  A3GameCollisionProbe,
  resolveEntityId,
} from './collision-probe.js';
export { A3GameHudLayer } from './hud-layer.js';
export { A3GameRuntimeChannel } from './runtime-channel.js';
