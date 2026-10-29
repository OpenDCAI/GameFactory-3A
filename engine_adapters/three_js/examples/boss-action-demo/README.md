# Forge Sentinel: action camera prototype

Procedural arena with two articulated characters, hit effects and
`A3GameActionCameraDirector`. BASE uses a static camera; UPDATED consumes confirmed
encounter events through the director and `A3GameFeedback`. This is an authored
prototype, not an independent SKILL-generation experiment.

From `engine_adapters/three_js/plugin/A3GamePlayable/`:

```bash
npm ci
npm test
node node_modules/vite/bin/vite.js --config ../../examples/boss-action-demo/vite.config.mjs
```

Open the URL printed by Vite; use `--port` if needed. The 16-second replay includes
approach, strike, launch, aerial strike, landing and recovery. Launch trajectories
are authored, not ragdoll physics. `Play live`: WASD move, Space jump, J attack.
Pause/blur clears input and impulses; reduced motion preserves health and hit cues.
Optional hit audio is synthesized locally.

`Record action comparison` captures the replay plus 8 seconds with reduced motion.
MP4/WebM saves locally (25 MiB limit); recordings and builds are ignored. Review
rendered frames and decode the video, not just numerical framing results.

Tests cover bounds, frame-rate consistency, lifecycle and live input. They do not
prove physical haptics, camera-volume clearance, final asset quality or comfort.
