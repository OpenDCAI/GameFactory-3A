import { defineConfig } from '../../plugin/A3GamePlayable/node_modules/vite/dist/node/index.js';
import { fileURLToPath } from 'node:url';
const here = path => fileURLToPath(new URL(path, import.meta.url));
export default defineConfig({
  root: here('./'),
  resolve: { alias: [
    { find: /^@a3game\/playable$/, replacement: here('../../plugin/A3GamePlayable/src/index.js') },
    { find: /^three$/, replacement: here('../../plugin/A3GamePlayable/node_modules/three/build/three.module.js') },
    { find: /^three\/addons\//, replacement: here('../../plugin/A3GamePlayable/node_modules/three/examples/jsm/') },
  ] },
  server: { host: '127.0.0.1', fs: { allow: [here('../../')] } },
});
