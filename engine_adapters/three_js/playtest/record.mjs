#!/usr/bin/env node
/**
 * Fixed-tick A3Game capture: --url URL --output-dir BASE (1280x720, 20fps, 14s).
 * --preview / --poster writes one PNG; --mode overview uses an optional game camera hook.
 * Plans use keyboard/pointer events; simulation advances independently of capture speed.
 * Every invocation preserves a unique take. Only a verified MP4 receives `completed`;
 * PNG-only success is `preview_completed`. --source-hash labels the captured build.
 */

import { mkdir, mkdtemp, readFile, rename, stat, writeFile } from 'node:fs/promises';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import assert from 'node:assert/strict';
import path from 'node:path';

function parseArgs(argv) {
  const out = {};
  const supported = new Set(['url', 'output-dir', 'duration', 'fps', 'width', 'height',
    'playwright-root', 'browser-executable', 'action-plan', 'look', 'warmup', 'hold',
    'mode', 'allow-partial-plan', 'self-test', 'preview', 'poster', 'source-hash']);
  for (let i = 2; i < argv.length; i += 1) {
    const key = argv[i].slice(2);
    if (!argv[i].startsWith('--') || !supported.has(key)) throw new Error(`Unknown option: ${argv[i]}`);
    if (['self-test', 'allow-partial-plan', 'preview', 'poster'].includes(key) && (!argv[i + 1] || argv[i + 1].startsWith('--'))) {
      out[key] = 'true';
    } else {
      if (!argv[i + 1] || argv[i + 1].startsWith('--')) throw new Error(`Missing value for --${key}`);
      out[key] = argv[++i];
    }
  }
  return out;
}

/**
 * Discover {actions, sustained, warmup, look}, returning only serializable plan fields.
 * Priority: declared plan, action/key bindings, DOM actions, generic keyboard fallback.
 * `sustained` holds keyboard inputs through warmup and capture; `warmup` is unrecorded
 * simulation time; `look` selects auto/off/pan. Keyboard inputs use event.code.
 */
const DISCOVERY = () => {
  const game = globalThis.__A3GAME_GAME__;
  const input = game?.input;

  // Binding inference uses substring matching; discrete verbs override holds.
  const HELD = new RegExp([
    'forward|back|left|right|up|down|strafe|ascend|descend|thrust|yaw|pitch|roll',
    'run|walk|sprint|dash|crouch|sneak|prone|slide|swim|climb|hover|fly|glide',
    'accel|throttle|brake|boost|nitro|nos|drift|gas|handbrake|ebrake|clutch',
    'draw|charge|aim|zoom|scope|focus|hold|pull|push|drag|carry|grab|grapple',
    'block|guard|defend|shield|parry|brace|cover',
    'auto|spray|beam|burn|heal|repair|mine|dig|build|paint|spray',
    'interact|use|open|channel|cast|revive|capture|hack|lockpick',
    'look|turn|pan|orbit|lean|peek|freelook',
  ].join('|'), 'i');
  const TAPPED = /toggle|switch|cycle|next|prev|swap|select|equip|holster|sheathe|reload|jump|hop|dodge|roll_dodge|evade|blink|teleport|respawn|reset|restart|pause|menu|map|inventory|emote|taunt|horn|light|flash/i;

  const isHeld = (action) => !TAPPED.test(action) && HELD.test(action);

  const asPlan = (source, actions, extra = {}) => ({ source, actions, ...extra });

  const declared = (() => {
    const exposed = globalThis.__A3GAME_PLAYTEST__ ?? game?.playtestActions;
    let value = exposed?.actions ?? exposed;
    if (typeof value === 'function') value = value();
    if (exposed == null) return null;
    if (!Array.isArray(value)) throw new Error('__A3GAME_PLAYTEST__.actions must be an array');
    const scalar = (item) => ['string', 'number', 'boolean'].includes(typeof item) ? item : undefined;
    const list = (items) => Array.isArray(items) ? items.map(scalar) : undefined;
    const safeActions = value.map((item) => {
      if (!item || typeof item !== 'object') throw new Error('Invalid declared action');
      return Object.fromEntries([
        ...['id', 'name', 'label', 'mouse', 'hold', 'click', 'duration', 'seconds'].map((key) => [key, scalar(item[key])]),
        ...['keys', 'taps'].map((key) => [key, list(item[key])]),
      ]);
    });
    return asPlan('declared', safeActions, {
      sustained: list(exposed?.sustained ?? exposed?.hold),
      warmup: scalar(exposed?.warmup),
      look: scalar(exposed?.look),
    });
  })();
  if (declared) return declared;

  const actions = [];
  const seen = new Set();
  const push = (id, extra) => {
    if (!id || seen.has(id)) return;
    seen.add(id);
    actions.push({ id, label: id, ...extra });
  };
  const bind = (code, action) => {
    if (/^Mouse0$/i.test(code)) push(action, { mouse: true, hold: isHeld(action) });
    else if (/^Mouse/i.test(code)) return;            // only button 0 is emulable
    else if (isHeld(action)) push(action, { keys: [code] });
    else push(action, { taps: [code] });
  };
  for (const [code, action] of Object.entries(input?.actionBindings ?? {})) bind(code, action);
  for (const [code, action] of Object.entries(input?.keyBindings ?? {})) bind(code, action);

  if (actions.length) {
    // Infer sustained throttle only when vehicle-specific bindings exist.
    const drive = actions.find((item) => /^(accel\w*|throttle|gas|forward)$/i.test(item.id));
    const vehicular = /brake|handbrake|ebrake|drift|steer|clutch|nitro|nos|respawn|lap|gear/i;
    const sustained = drive && actions.some((item) => vehicular.test(item.id))
      ? [drive.id]
      : [];
    return asPlan('input_router', actions, { sustained });
  }

  const dom = [...document.querySelectorAll('[data-game-action]')]
    .map((node) => ({
      id: node.getAttribute('data-game-action'),
      label: node.getAttribute('aria-label') || node.textContent?.trim() || '',
      click: '[data-game-action="' + CSS.escape(node.getAttribute('data-game-action') || '') + '"]',
    }))
    .filter((item) => item.id);
  if (dom.length) return asPlan('dom', dom);

  return asPlan('fallback', [
    { id: 'forward', keys: ['KeyW'] },
    { id: 'left', keys: ['KeyA'] },
    { id: 'right', keys: ['KeyD'] },
    { id: 'jump', taps: ['Space'] },
    { id: 'primary', mouse: true },
  ]);
};

/** Map an action name back to the key code that triggers it, for `sustained`. */
const RESOLVE = (names) => {
  const input = globalThis.__A3GAME_GAME__?.input;
  const table = { ...(input?.keyBindings ?? {}), ...(input?.actionBindings ?? {}) };
  return names.map((name) => {
    if (/^(Key|Digit|Arrow|Numpad|F\d)/.test(name) || /^(Space|Shift|Control|Alt|Tab|Enter|Escape)/.test(name)) {
      return { name, code: name };                     // already a code
    }
    const code = Object.entries(table).find(([, action]) => action === name)?.[0];
    return { name, code: code && !/^Mouse/i.test(code) ? code : null };
  });
};

function normalize(raw, source, budgetSeconds, fps, allowPartial = false) {
  if (!Array.isArray(raw)) throw new Error('Plan actions must be an array');
  const codes = (value, name) => {
    if (value === undefined) return [];
    if (!Array.isArray(value) || value.some((code) => typeof code !== 'string' || !code.trim())) {
      throw new Error(`${name} must contain keyboard codes`);
    }
    return [...new Set(value)];
  };
  const actions = raw.map((item, index) => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) throw new Error(`Invalid action ${index}`);
    const seconds = item.duration ?? item.seconds;
    if (seconds !== undefined && (!Number.isFinite(Number(seconds)) || Number(seconds) <= 0)) {
      throw new Error(`Invalid duration for action ${index}`);
    }
    const keys = codes(item.keys, 'keys');
    const taps = codes(item.taps, 'taps');
    if (keys.some((code) => taps.includes(code))) throw new Error(`Action ${index} both holds and taps a key`);
    return {
      id: String(item.id ?? item.name ?? `${source}_${index + 1}`),
      label: String(item.label ?? item.id ?? ''), keys, taps,
      mouse: Boolean(item.mouse), hold: Boolean(item.hold),
      click: typeof item.click === 'string' ? item.click : '',
      seconds: seconds === undefined ? null : Number(seconds), source,
    };
  });
  const fixedSeconds = actions.reduce((sum, action) => sum + (action.seconds ?? 0), 0);
  const unspecified = actions.filter((action) => action.seconds === null).length;
  const share = unspecified ? Math.max(1 / fps, (budgetSeconds - fixedSeconds) / unspecified) : 0;
  let elapsed = 0;
  let endFrame = 0;
  for (const action of actions) {
    action.seconds ??= share;
    action.start_frame = endFrame;
    elapsed += action.seconds;
    endFrame = Math.round(elapsed * fps);
    action.planned_frames = endFrame - action.start_frame;
    if (action.planned_frames < 1) throw new Error(`Action ${action.id} is shorter than one frame at ${fps} fps`);
  }
  const budgetFrames = Math.max(1, Math.round(budgetSeconds * fps));
  if (endFrame > budgetFrames && !allowPartial) {
    throw new Error(`Plan needs ${endFrame} frames (${endFrame / fps}s); increase --duration or use --allow-partial-plan for an explicit probe`);
  }
  for (const action of actions) {
    action.frames = Math.max(0, Math.min(action.planned_frames, budgetFrames - action.start_frame));
  }
  return { actions, targetFrames: budgetFrames, planFrames: endFrame, partial: endFrame > budgetFrames };
}

function lookMode(requested, runtimeMode) {
  if (requested === false || requested === 'false') requested = 'off';
  if (!['auto', 'off', 'pan'].includes(requested)) throw new Error('--look must be auto, off, or pan');
  return requested === 'auto' ? (runtimeMode && runtimeMode !== 'drag' ? 'pan' : 'off') : requested;
}

function substeps(dt, maximum = 1 / 60) {
  const count = Math.max(1, Math.ceil(dt / maximum - 1e-9));
  return { count, dt: dt / count };
}

function safeSnapshot() {
  const seen = new WeakSet();
  let budget = 2048;
  const copy = (value, depth = 0) => {
    if (--budget < 0 || depth > 8) return '[truncated]';
    if (value == null || typeof value === 'boolean') return value ?? null;
    if (typeof value === 'number') return Number.isFinite(value) ? value : null;
    if (typeof value === 'string') return value.slice(0, 512);
    if (typeof value !== 'object') return null;
    if (seen.has(value)) return '[circular]';
    if (value.isObject3D || value.isMaterial || value.isBufferGeometry || value.isTexture ||
        value.isWebGLRenderer || (value.tick && value.renderer)) return '[runtime object]';
    if (!Array.isArray(value) && ![Object.prototype, null].includes(Object.getPrototypeOf(value))) return '[non-plain object]';
    seen.add(value);
    try {
      if (Array.isArray(value)) return value.slice(0, 128).map((item) => copy(item, depth + 1));
      const result = {};
      for (const key of Object.keys(value).slice(0, 128)) {
        const descriptor = Object.getOwnPropertyDescriptor(value, key);
        if (descriptor && 'value' in descriptor) result[key] = copy(descriptor.value, depth + 1);
      }
      return result;
    } finally {
      seen.delete(value);
    }
  };
  return copy(globalThis.__A3GAME_GAME__?.getState?.() ?? null);
}

function validateVideo(metadata, frames, fps, width, height) {
  const stream = metadata.streams?.[0];
  const seconds = Number(stream?.duration ?? metadata.format?.duration);
  const [numerator, denominator = 1] = String(stream?.avg_frame_rate ?? '0').split('/').map(Number);
  if (Number(stream?.nb_read_frames) !== frames || stream?.width !== width || stream?.height !== height ||
      Math.abs(numerator / denominator - fps) > 1e-6 || !Number.isFinite(seconds) ||
      Math.abs(seconds - frames / fps) > 0.002) throw new Error('Encoded video frame count, duration, fps, or dimensions do not match the capture');
  return { frames: Number(stream.nb_read_frames), seconds, fps: numerator / denominator, width, height };
}

async function browserAdvance({ dt, maximum, mode, reviewTime, frame, duration, look, yaw, pitch }) {
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Fixed tick/compositor timed out')), 30_000);
    requestAnimationFrame(() => {
      try {
        const game = globalThis.__A3GAME_GAME__;
        const host = game.host;
        if (look === 'pan' && reviewTime !== null) {
          game.input.setLook(yaw + Math.sin(reviewTime * 0.35) * 0.28, pitch + Math.sin(reviewTime * 0.6) * 0.06);
        }
        if (dt > 0) {
          const internal = Number(host.options?.fixedTimeStep ?? 0);
          const canAccumulate = internal > 0 && Number(host.options.maxFrameDelta) >= dt &&
            Number(host.options.maxSubSteps) >= Math.ceil(dt / internal) + 1;
          const count = canAccumulate ? 1 : Math.max(1, Math.ceil(dt / maximum - 1e-9));
          for (let i = 0; i < count; i += 1) host.tick(dt / count);
        }
        if (mode === 'overview' && reviewTime !== null) {
          if (typeof globalThis.__A3GAME_REVIEW__ === 'function') globalThis.__A3GAME_REVIEW__({ mode, time: reviewTime, frame, dt, duration });
          host.renderer.render(host.scene, host.camera);
        } else if (dt === 0) host.renderer.render(host.scene, host.camera);
        requestAnimationFrame(() => { clearTimeout(timer); resolve(); });
      } catch (error) { clearTimeout(timer); reject(error); }
    });
  });
}

async function selfTest() {
  const test = (name, run) => { run(); console.log(`ok - ${name}`); };
  test('CLI defaults and flag validation', () => {
    assert.equal(parseArgs(['node', 'record', '--self-test'])['self-test'], 'true');
    assert.equal(parseArgs(['node', 'record', '--preview']).preview, 'true');
    assert.equal(parseArgs(['node', 'record', '--poster', '--width', '1280']).poster, 'true');
    assert.equal(parseArgs(['node', 'record', '--source-hash', 'AbC0123'])['source-hash'], 'AbC0123');
    assert.throws(() => parseArgs(['node', 'record', '--look']), /Missing value/);
    assert.throws(() => parseArgs(['node', 'record', '--typo', 'x']), /Unknown option/);
  });
  test('cumulative boundaries preserve exact total frames', () => {
    const plan = normalize(Array.from({ length: 3 }, (_, id) => ({ id, seconds: 0.075 })), 'plan', 0.225, 20);
    assert.deepEqual(plan.actions.map((action) => action.frames), [2, 1, 2]);
    assert.equal(plan.targetFrames, 5);
  });
  test('explicit plans are never silently truncated or reordered', () => {
    const raw = [{ id: 'restart', seconds: 1 }, { id: 'brake', seconds: 1 }];
    assert.throws(() => normalize(raw, 'declared', 1, 20), /Plan needs/);
    const plan = normalize(raw, 'declared', 1, 20, true);
    assert.deepEqual(plan.actions.map((action) => action.id), ['restart', 'brake']);
    assert.deepEqual(plan.actions.map((action) => action.frames), [20, 0]);
    assert.equal(plan.partial, true);
  });
  test('idle tails and single frame takes retain requested duration', () => {
    assert.equal(normalize([], 'overview', 0.01, 20).targetFrames, 1);
    assert.equal(normalize([{ seconds: 1 }], 'plan', 3, 20).targetFrames, 60);
    assert.throws(() => normalize([{ seconds: 0.001 }], 'plan', 1, 20), /shorter than one frame/);
    assert.throws(() => normalize([{ keys: ['KeyW'], taps: ['KeyW'] }], 'plan', 1, 20), /both holds and taps/);
  });
  test('look off preserves camera; pan is explicit and validated', () => {
    assert.equal(lookMode('off', 'pointerlock'), 'off');
    assert.equal(lookMode(false, 'pointerlock'), 'off');
    assert.equal(lookMode('false', 'pointerlock'), 'off');
    assert.equal(lookMode('auto', 'drag'), 'off');
    assert.equal(lookMode('pan', ''), 'pan');
    assert.throws(() => lookMode('orbit', 'drag'), /--look/);
  });
  test('20 and 30fps advance full simulation time without delta clamps', () => {
    for (const fps of [1, 20, 30, 60]) {
      const step = substeps(1 / fps);
      assert.ok(step.dt <= 1 / 60 + 1e-12);
      assert.ok(Math.abs(step.count * step.dt * fps - 1) < 1e-12);
    }
    assert.equal(substeps(1 / 20).count, 3);
  });
  test('state serialization is bounded and rejects THREE graphs', () => {
    const previous = globalThis.__A3GAME_GAME__;
    try {
      const state = { score: 2, object: { isObject3D: true }, huge: Array(10000).fill('a'.repeat(1000)) };
      state.self = state;
      globalThis.__A3GAME_GAME__ = { getState: () => state };
      const result = safeSnapshot();
      assert.equal(result.score, 2);
      assert.equal(result.object, '[runtime object]');
      assert.equal(result.self, '[circular]');
      assert.ok(JSON.stringify(result).length < 70000);
    } finally { globalThis.__A3GAME_GAME__ = previous; }
  });
  test('shared player aliases are copied while actual recursion cycles stay bounded', () => {
    const previous = globalThis.__A3GAME_GAME__;
    try {
      const player = { isPlayer: true, checkpointsPassed: 8, speedKph: 40 };
      const standings = [player];
      const state = { racers: standings, standings, player, shared: [player, player] };
      player.self = player;
      globalThis.__A3GAME_GAME__ = { getState: () => state };
      const result = safeSnapshot();
      assert.equal(result.player.checkpointsPassed, 8);
      assert.deepEqual(result.player, result.racers[0]);
      assert.deepEqual(result.standings, result.racers);
      assert.deepEqual(result.shared[0], result.shared[1]);
      assert.equal(result.player.self, '[circular]');
      assert.notEqual(result.player, result.racers[0]);
      assert.ok(JSON.stringify(result).length < 2048);
    } finally { globalThis.__A3GAME_GAME__ = previous; }
  });
  test('completion requires verified MP4, including single frame takes', () => {
    const metadata = { streams: [{ nb_read_frames: '1', duration: '0.05', avg_frame_rate: '20/1', width: 1280, height: 720 }] };
    assert.equal(validateVideo(metadata, 1, 20, 1280, 720).seconds, 0.05);
    assert.throws(() => validateVideo(metadata, 2, 20, 1280, 720), /do not match/);
    assert.throws(() => validateVideo({}, 1, 20, 1280, 720), /do not match/);
  });
  const previous = { game: globalThis.__A3GAME_GAME__, review: globalThis.__A3GAME_REVIEW__, raf: globalThis.requestAnimationFrame };
  try {
    const ticks = [];
    let lookCalls = 0;
    let renders = 0;
    const host = { options: {}, renderer: { render() { renders += 1; return host; } }, tick(dt) { ticks.push(dt); return this; } };
    host.self = host;
    globalThis.__A3GAME_GAME__ = { host, input: { setLook(yaw, pitch) {
      lookCalls += 1;
      assert.ok(Math.abs(yaw - 1) <= 0.28 + 1e-12);
      assert.ok(Math.abs(pitch - 0.1) <= 0.06 + 1e-12);
      return this;
    } } };
    globalThis.requestAnimationFrame = (callback) => queueMicrotask(() => callback(0));
    const frame = { dt: 0.05, maximum: 1 / 60, mode: 'gameplay', reviewTime: 0.05, frame: 0, duration: 1, look: 'off', yaw: 1, pitch: 0.1 };
    assert.equal(await browserAdvance(frame), undefined);
    assert.equal(ticks.length, 3);
    assert.ok(Math.abs(ticks.reduce((a, b) => a + b, 0) - 0.05) < 1e-12);
    assert.equal(lookCalls, 0);
    host.options = { fixedTimeStep: 1 / 60, maxFrameDelta: 0.1, maxSubSteps: 6 };
    ticks.length = 0;
    await browserAdvance({ ...frame, look: 'pan' });
    assert.equal(ticks.length, 1);
    assert.equal(ticks[0], 0.05);
    assert.equal(lookCalls, 1);
    for (const time of [0, 4.5, 9, 14, 60, 3600]) await browserAdvance({ ...frame, look: 'pan', reviewTime: time });
    console.log('ok - browser ticks return no host graph, preserve off, bound long-take pan, and use internal substeps');
    let reviewTime;
    globalThis.__A3GAME_REVIEW__ = ({ time }) => { reviewTime = time; return host; };
    assert.equal(await browserAdvance({ ...frame, dt: 0, mode: 'overview', reviewTime: 0 }), undefined);
    assert.equal(reviewTime, 0);
    assert.equal(renders, 1);
    host.tick = () => { throw new Error('deliberate tick failure'); };
    await assert.rejects(browserAdvance(frame), /deliberate tick failure/);
    console.log('ok - overview hook return is not serialized and tick errors reject instead of hanging');
  } finally {
    globalThis.__A3GAME_GAME__ = previous.game;
    globalThis.__A3GAME_REVIEW__ = previous.review;
    globalThis.requestAnimationFrame = previous.raf;
  }
}

async function main(args) {
  for (const key of ['url', 'output-dir']) if (!args[key]) throw new Error(`Missing --${key}`);
  const FPS = Number(args.fps ?? 20);
  const duration = Number(args.duration ?? 14);
  const width = Number(args.width ?? 1280);
  const height = Number(args.height ?? 720);
  const mode = args.mode ?? 'gameplay';
  const preview = args.preview === 'true' || args.poster === 'true';
  const allowPartial = args['allow-partial-plan'] === 'true';
  if (![FPS, duration, width, height].every((n) => Number.isFinite(n) && n > 0)) throw new Error('duration, fps, width, and height must be positive');
  if (![width, height].every((n) => Number.isInteger(n) && n % 2 === 0)) throw new Error('width and height must be even integers');
  if (!['gameplay', 'overview'].includes(mode)) throw new Error('--mode must be gameplay or overview');
  if (args['allow-partial-plan'] && !['true', 'false'].includes(args['allow-partial-plan'])) throw new Error('--allow-partial-plan must be true or false');
  const DT = 1 / FPS;
  const baseDir = path.resolve(args['output-dir']);
  await mkdir(baseDir, { recursive: true });
  const outputDir = await mkdtemp(path.join(baseDir, `${mode}-${new Date().toISOString().replace(/[:.]/g, '-')}-`));
  const framesDir = path.join(outputDir, 'frames');
  await mkdir(framesDir);
  const report = {
    schema_version: 'a3game.playtest_report.v1', engine: 'three_js', status: 'failed',
    url: args.url, output_dir: outputDir, source_hash: args['source-hash'] ?? null,
    mode, fps: FPS, requested_seconds: duration,
    viewport: { width, height }, action_source: '', look_mode: '', warmup_seconds: 0,
    sustained: [], excluded_actions: [], actions: [], executed_actions: [], unexecuted_actions: [],
    frames: 0, recorded_seconds: 0, simulated_seconds: 0, fixed_tick: false,
    crash: '', page_errors: [], console_errors: [], warnings: [], video: null,
    init: null, poster: null, game_state: null, created_at: new Date().toISOString(),
  };
  let browser;
  let page;
  let mouseDown = false;
  const held = new Set();
  const started = Date.now();
  const failOnPageErrors = () => {
    if (report.crash || report.page_errors.length || report.console_errors.length) {
      throw new Error(report.crash || report.page_errors[0] || report.console_errors[0]);
    }
  };
  try {
    const require_ = createRequire(path.join(path.resolve(args['playwright-root'] || process.cwd()), 'package.json'));
    const { chromium } = require_('playwright');
    browser = await chromium.launch({
      executablePath: args['browser-executable'] || undefined,
      args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu-watchdog',
        '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--in-process-gpu',
        '--override-use-software-gl-for-tests', '--disable-features=CDPScreenshotNewSurface'],
    });
    page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
    page.setDefaultTimeout(30_000);
    const appendError = (list, value) => { if (list.length < 100) list.push(String(value).slice(0, 500)); };
    page.on('pageerror', (error) => appendError(report.page_errors, error.message || error));
    page.on('console', (message) => { if (message.type() === 'error') appendError(report.console_errors, message.text()); });
    page.on('crash', () => { report.crash = 'Page renderer crashed'; });
    await page.addInitScript(() => {
      globalThis.__A3GAME_RECORDING__ = true;
      let game;
      Object.defineProperty(globalThis, '__A3GAME_GAME__', {
        configurable: true, enumerable: true,
        get() { return game; },
        set(value) { game = value; value?.host?.stop?.(); },
      });
    });
    await page.goto(args.url, { waitUntil: 'load', timeout: 120_000 });
    await page.waitForFunction(() => typeof globalThis.__A3GAME_GAME__?.host?.tick === 'function', null, { timeout: 120_000, polling: 100 });
    const runtime = await page.evaluate(() => {
      const game = globalThis.__A3GAME_GAME__;
      const host = game.host;
      host.stop();
      return {
        lookMode: String(game.input?.lookMode ?? ''), yaw: Number(game.input?.yaw ?? 0),
        pitch: Number(game.input?.pitch ?? 0), canLook: typeof game.input?.setLook === 'function',
        review: typeof globalThis.__A3GAME_REVIEW__ === 'function',
        maxDelta: Number(host.options?.maxFrameDelta ?? 1 / 60),
        fixedStep: Number(host.options?.fixedTimeStep ?? 0),
        simulationTime: Number(host.elapsedSeconds ?? 0), droppedSeconds: Number(host.droppedSeconds ?? 0),
      };
    });
    report.fixed_tick = true;
    report.initial_simulated_seconds = runtime.simulationTime;
    failOnPageErrors();
    if (mode === 'overview' && !runtime.review) report.warnings.push('No review hook; using the game\'s own camera');
    if (preview) {
      const warmup = Number(args.warmup ?? 0);
      if (!Number.isFinite(warmup) || warmup < 0) throw new Error('warmup must be finite and non-negative');
      const maximum = Math.min(1 / 60, runtime.maxDelta > 0 ? runtime.maxDelta : 1 / 60,
        runtime.fixedStep > 0 ? runtime.fixedStep : 1 / 60);
      const warmupFrames = Math.round(warmup * FPS);
      const frame = { maximum, mode, duration: warmupFrames / FPS, look: 'off', yaw: runtime.yaw, pitch: runtime.pitch };
      for (let i = 0; i < warmupFrames; i += 1) {
        await page.evaluate(browserAdvance, { ...frame, dt: DT, reviewTime: (i + 1) / FPS, frame: i });
        failOnPageErrors();
      }
      await page.evaluate(browserAdvance, { ...frame, dt: 0, reviewTime: warmupFrames / FPS, frame: warmupFrames });
      const cdp = await page.context().newCDPSession(page);
      const shot = await cdp.send('Page.captureScreenshot', { format: 'png' });
      report.poster = path.join(outputDir, 'poster.png');
      await writeFile(report.poster, Buffer.from(shot.data, 'base64'), { flag: 'wx' });
      report.init = report.poster;
      report.game_state = await page.evaluate(safeSnapshot);
      failOnPageErrors();
      report.preview = true;
      report.png_frames = 1;
      report.action_source = 'preview';
      report.look_mode = 'off';
      report.warmup_seconds = warmupFrames / FPS;
      report.simulated_seconds = report.warmup_seconds;
      report.status = 'preview_completed';
      report.completed_at = new Date().toISOString();
    } else {
    await page.waitForTimeout(3_000);
    failOnPageErrors();
    let discovered = { source: 'overview', actions: [], look: 'off', warmup: 0, sustained: [] };
    if (mode === 'gameplay') {
      if (args['action-plan']) {
        report.action_plan = path.resolve(args['action-plan']);
        const parsed = JSON.parse(await readFile(report.action_plan, 'utf8'));
        discovered = Array.isArray(parsed) ? { source: 'plan', actions: parsed } : { ...parsed, source: 'plan' };
      } else discovered = await page.evaluate(DISCOVERY);
    }
    report.action_source = discovered.source;
    report.look_mode = lookMode(String(args.look ?? discovered.look ?? 'auto').toLowerCase(), runtime.lookMode);
    if (mode === 'overview' && report.look_mode !== 'off') throw new Error('overview owns its camera; use --look off');
    if (report.look_mode === 'pan' && !runtime.canLook) throw new Error('pan requires input.setLook');
    const warmup = Number(args.warmup ?? discovered.warmup ?? 0);
    if (!Number.isFinite(warmup) || warmup < 0) throw new Error('warmup must be finite and non-negative');
    const sustainedNames = args.hold ? args.hold.split(',') : discovered.sustained ?? [];
    if (!Array.isArray(sustainedNames)) throw new Error('sustained must be an array');
    const sustained = sustainedNames.length ? await page.evaluate(RESOLVE, sustainedNames) : [];
    for (const item of sustained) if (!item.code) throw new Error(`Cannot hold "${item.name}": no keyboard binding`);
    const sustainedCodes = [...new Set(sustained.map((item) => item.code))];
    report.sustained = sustainedCodes;
    const plan = normalize(discovered.actions, discovered.source, duration, FPS, allowPartial);
    report.actions = plan.actions;
    report.target_frames = plan.targetFrames;
    report.plan_frames = plan.planFrames;
    report.partial_plan = plan.partial;
    if (plan.partial) report.warnings.push('Explicit partial-plan probe: see unexecuted_actions for omitted frames');
    for (const action of report.actions) {
      if (action.taps.some((code) => sustainedCodes.includes(code))) throw new Error(`Action ${action.id} taps a sustained key`);
    }
    const maximum = Math.min(1 / 60, runtime.maxDelta > 0 ? runtime.maxDelta : 1 / 60,
      runtime.fixedStep > 0 ? runtime.fixedStep : 1 / 60);
    const fixed = substeps(DT, maximum);
    report.substeps_per_frame = fixed.count;
    report.substep_seconds = fixed.dt;
    const cdp = await page.context().newCDPSession(page);
    cdp.setDefaultTimeout?.(30_000);
    const screenshot = async (file, format = 'png') => {
      failOnPageErrors();
      const shot = await cdp.send('Page.captureScreenshot', { format, ...(format === 'jpeg' ? { quality: 94 } : {}) });
      await writeFile(file, Buffer.from(shot.data, 'base64'), { flag: 'wx' });
      failOnPageErrors();
    };

    async function setKeys(wanted) {
      const target = new Set([...wanted, ...sustainedCodes]);
      for (const key of [...held]) {
        if (!target.has(key)) { await page.keyboard.up(key); held.delete(key); }
      }
      for (const key of target) {
        if (!held.has(key)) { await page.keyboard.down(key); held.add(key); }
      }
    }
    const setMouse = async (down) => {
      if (mouseDown === down) return;
      await page.evaluate((isDown) => {
        const element = globalThis.__A3GAME_GAME__.host.container;
        const rect = element.getBoundingClientRect();
        element.dispatchEvent(new PointerEvent(isDown ? 'pointerdown' : 'pointerup', {
          bubbles: true, button: 0, buttons: isDown ? 1 : 0, isPrimary: true,
          pointerId: 1, pointerType: 'mouse', clientX: rect.x + rect.width / 2, clientY: rect.y + rect.height / 2,
        }));
      }, down);
      mouseDown = down;
    };
    async function advance(dt, reviewTime = null) {
      failOnPageErrors();
      await page.evaluate(browserAdvance, { dt, maximum, mode, reviewTime, frame: report.frames, duration: report.target_frames / FPS,
        look: report.look_mode, yaw: runtime.yaw, pitch: runtime.pitch });
      report.simulated_seconds += dt;
      failOnPageErrors();
    }
    async function capture() {
      await advance(DT, (report.frames + 1) / FPS);
      await screenshot(path.join(framesDir, `f${String(report.frames).padStart(6, '0')}.jpg`), 'jpeg');
      report.frames += 1;
    }
    await advance(0, 0);
    report.init = path.join(outputDir, 'init.png');
    await screenshot(report.init);
    await setKeys([]);
    const warmupFrames = Math.round(warmup * FPS);
    for (let i = 0; i < warmupFrames; i += 1) await advance(DT);
    report.warmup_seconds = warmupFrames / FPS;
    for (const [index, action] of report.actions.entries()) {
      if (!action.frames) continue;
      const record = { index, id: action.id, source: action.source, start_frame: report.frames, frames: 0, ok: false };
      report.executed_actions.push(record);
      try {
        if (action.click) await page.locator(action.click).first().click({ timeout: 2_000 });
        await setKeys(action.keys);
        for (const key of action.taps) { await page.keyboard.down(key); held.add(key); }
        if (action.mouse) await setMouse(true);
        for (let i = 0; i < action.frames; i += 1) {
          await capture();
          record.frames += 1;
          if (i === 0 && !action.hold) {
            for (const key of action.taps) { await page.keyboard.up(key); held.delete(key); }
            if (action.mouse) await setMouse(false);
          }
        }
        if (action.hold) {
          for (const key of action.taps) { await page.keyboard.up(key); held.delete(key); }
        }
        if (action.mouse) await setMouse(false);
        record.ok = true;
      } catch (error) {
        record.error = String(error.message || error).slice(0, 500);
        throw error;
      }
    }
    await setKeys([]);
    while (report.frames < report.target_frames) await capture();
    await advance(0, report.frames / FPS);
    report.poster = path.join(outputDir, 'poster.png');
    await screenshot(report.poster);
    report.game_state = await page.evaluate(safeSnapshot);
    const timing = await page.evaluate(() => {
      const host = globalThis.__A3GAME_GAME__.host;
      return { simulationTime: Number(host.elapsedSeconds ?? 0), droppedSeconds: Number(host.droppedSeconds ?? 0) };
    });
    report.host_simulated_seconds = timing.simulationTime - runtime.simulationTime;
    report.dropped_seconds = timing.droppedSeconds - runtime.droppedSeconds;
    if (report.dropped_seconds > 1e-8) throw new Error('Host dropped simulation time during fixed capture');
    if (runtime.fixedStep > 0 && Math.abs(report.host_simulated_seconds - report.simulated_seconds) > runtime.fixedStep + 1e-7) {
      throw new Error('Host simulation time differs from requested fixed ticks');
    }
    failOnPageErrors();
    }
  } catch (error) {
    report.status = 'failed';
    report.crash ||= String(error.message || error).slice(0, 1000);
  } finally {
    if (page) {
      for (const key of held) await page.keyboard.up(key).catch(() => {});
      if (mouseDown) await page.evaluate(() => {
        globalThis.__A3GAME_GAME__?.host?.container?.dispatchEvent(new PointerEvent('pointerup', {
          bubbles: true, button: 0, buttons: 0, isPrimary: true, pointerId: 1, pointerType: 'mouse',
        }));
      }).catch(() => {});
      if (!report.game_state && !page.isClosed() && !report.crash) report.game_state = await page.evaluate(safeSnapshot).catch(() => null);
    }
    await browser?.close().catch(() => {});
  }
  report.recorded_seconds = report.frames / FPS;
  report.ms_per_frame = report.frames ? Math.round((Date.now() - started) / report.frames) : 0;
  report.unexecuted_actions = report.actions.flatMap((action, index) => {
    const recorded = report.executed_actions.find((item) => item.index === index)?.frames ?? 0;
    return recorded < action.planned_frames ? [{ id: action.id, index, missing_frames: action.planned_frames - recorded }] : [];
  });
  try {
    if (!preview) {
    if (!report.frames) throw new Error('No captured frames to encode');
    const video = path.join(outputDir, 'video.mp4');
    const ffmpeg = process.env.A3GAME_PLAYTEST_FFMPEG || process.env.FFMPEG || 'ffmpeg';
    const encoded = spawnSync(ffmpeg,
      ['-n', '-loglevel', 'error', '-framerate', String(FPS), '-start_number', '0',
        '-i', path.join(framesDir, 'f%06d.jpg'), '-frames:v', String(report.frames),
        '-c:v', process.env.A3GAME_PLAYTEST_CODEC || 'libopenh264',
        '-b:v', process.env.A3GAME_PLAYTEST_BITRATE || '6000k', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', video],
      { encoding: 'utf8', timeout: 120_000, maxBuffer: 2 * 1024 * 1024 });
    if (encoded.status !== 0) throw new Error(`Video encoding failed: ${encoded.error?.message || encoded.stderr?.trim() || encoded.status}`);
    if ((await stat(video)).size <= 0) throw new Error('Encoded video is empty');
    const ffprobe = process.env.A3GAME_PLAYTEST_FFPROBE || (ffmpeg.includes('/') ? path.join(path.dirname(ffmpeg), 'ffprobe') : 'ffprobe');
    const probed = spawnSync(ffprobe, ['-v', 'error', '-select_streams', 'v:0', '-count_frames',
      '-show_entries', 'stream=nb_read_frames,width,height,avg_frame_rate,duration:format=duration', '-of', 'json', video],
    { encoding: 'utf8', timeout: 120_000, maxBuffer: 2 * 1024 * 1024 });
    if (probed.status !== 0) throw new Error(`Video validation failed: ${probed.error?.message || probed.stderr?.trim() || probed.status}`);
    report.video_metadata = validateVideo(JSON.parse(probed.stdout), report.frames, FPS, width, height);
    report.video = video;
    if (!report.crash && !report.page_errors.length && !report.console_errors.length &&
        report.frames === report.target_frames && report.init && report.poster && report.executed_actions.every((action) => action.ok)) {
      report.status = 'completed';
      report.completed_at = new Date().toISOString();
    }
    }
  } catch (error) {
    report.warnings.push(String(error.message || error).slice(0, 1000));
    report.crash ||= report.warnings.at(-1);
  }
  const reportPath = path.join(outputDir, 'report.json');
  const manifest = {
    schema_version: 'a3game.playtest_manifest.v1', status: report.status, mode, url: args.url,
    source_hash: report.source_hash,
    output_dir: outputDir, created_at: report.created_at, completed_at: report.completed_at ?? null,
    fps: FPS, frames: report.frames, seconds: report.recorded_seconds, viewport: report.viewport,
    partial_plan: report.partial_plan ?? false, report: path.relative(baseDir, reportPath),
    video: report.video ? path.relative(baseDir, report.video) : null,
    poster: report.poster ? path.relative(baseDir, report.poster) : null,
    init: report.init ? path.relative(baseDir, report.init) : null,
  };
  await writeFile(reportPath, `${JSON.stringify(report, null, 2)}\n`, { flag: 'wx' });
  await writeFile(path.join(outputDir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`, { flag: 'wx' });
  if (report.status === 'completed') {
    const pending = path.join(baseDir, `.manifest-${path.basename(outputDir)}.tmp`);
    await writeFile(pending, `${JSON.stringify(manifest, null, 2)}\n`, { flag: 'wx' });
    await rename(pending, path.join(baseDir, 'manifest.json'));
  }
  console.log(JSON.stringify({ status: report.status, source_hash: report.source_hash, report: reportPath, output_dir: outputDir, poster: report.poster }));
  if (!['completed', 'preview_completed'].includes(report.status)) process.exitCode = 1;
}

const args = parseArgs(process.argv);
if (args['self-test'] === 'true') await selfTest();
else await main(args);
