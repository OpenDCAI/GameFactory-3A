import { afterEach, describe, expect, it, vi } from 'vitest';
import { A3GameAssetLibrary } from '../src/index.js';

afterEach(() => vi.unstubAllGlobals());

describe('subdirectory asset loading', () => {
  it('scopes manifests, textures and decoders to the document directory', async () => {
    vi.stubGlobal('document', { baseURI: 'https://example.test/games/racer/index.html' });
    const assets = new A3GameAssetLibrary();
    expect(assets.manifestUrl).toBe('/games/racer/assets/manifest.json');
    expect(assets.dracoDecoderPath).toBe('/games/racer/draco/');
    expect(assets.ktx2TranscoderPath).toBe('/games/racer/basis/');
    expect(assets.loadingManager.resolveURL('/assets/model.glb')).toBe('/games/racer/assets/model.glb');
    expect(assets.loadingManager.resolveURL('/games/racer/assets/model.glb')).toBe('/games/racer/assets/model.glb');
    await assets.dispose();
  });
  it('allows root overrides and preserves absolute or embedded URLs', async () => {
    const assets = new A3GameAssetLibrary({ baseUrl: '/games/arena' });
    for (const url of ['https://cdn.test/a.glb', '//cdn.test/a.png', 'data:image/png;base64,AA==', 'blob:https://example.test/id']) {
      expect(assets.resolveUrl(url)).toBe(url);
    }
    expect(assets.resolveUrl('assets/a.png')).toBe('/games/arena/assets/a.png');
    const root = new A3GameAssetLibrary({ baseUrl: '/' });
    expect(root.manifestUrl).toBe('/assets/manifest.json');
    await assets.dispose(); await root.dispose();
  });
  it('resolves JSON assets without rewriting manifest identity URLs', async () => {
    const entry = { artifact_id: 'settings', asset_id: 'settings', representation: 'json', url: '/assets/settings.json' };
    vi.stubGlobal('fetch', vi.fn(async (url) => ({ ok: true, json: async () => url.endsWith('manifest.json') ? { assets: { settings: entry } } : { ready: true } })));
    const assets = new A3GameAssetLibrary({ baseUrl: '/games/demo/' });
    await assets.load();
    const result = await assets.loadArtifact('settings');
    expect(result.data).toEqual({ ready: true });
    expect(fetch).toHaveBeenCalledWith('/games/demo/assets/settings.json', { cache: 'no-cache' });
    expect(assets.findEntry('settings').url).toBe('/assets/settings.json');
    await assets.dispose();
  });
});
