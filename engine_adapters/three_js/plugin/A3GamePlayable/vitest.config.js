import { defineConfig } from 'vitest/config';
import { fileURLToPath } from 'node:url';

const here = (path) => fileURLToPath(new URL(path, import.meta.url));

export default defineConfig({
  resolve: {
    alias: [
      { find: /^@a3game\/playable$/, replacement: here('./src/index.js') },
      { find: /^three$/, replacement: here('./node_modules/three/build/three.module.js') },
      { find: /^three\/addons\//, replacement: here('./node_modules/three/examples/jsm/') },
    ],
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.spec.js', '../../examples/**/tests/**/*.spec.js'],
  },
});
