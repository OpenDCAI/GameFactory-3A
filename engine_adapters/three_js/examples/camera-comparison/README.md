# Camera comparison

One shared trace, preview and test suite for static/chase cameras and the raw
control/treatment SKILL pair. Select the pair in the page. Generated modules remain
unchanged; the control's pause/feedback-off failure is preserved. Projection metrics
are not rendered occlusion, comfort scores or proof of general SKILL effectiveness.

From `engine_adapters/three_js/plugin/A3GamePlayable/`:

```bash
npm test
node_modules/.bin/vite-node --config vitest.config.js ../../examples/camera-comparison/measure.js
node node_modules/vite/bin/vite.js --config ../../examples/camera-comparison/vite.config.mjs
```

Open Vite's printed URL (`--port` overrides it). Replay, seek, reduced motion and
18-second canvas recording share one page; recordings download through the browser.
Historical prompts, snapshots and screenshots are local artifacts under
`test_data/outputs/camera-comparison-261015/`, not required source files.
