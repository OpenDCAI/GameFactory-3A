/**
 * DOM overlay HUD layer.
 *
 * A three.js game's HUD is plain DOM on top of the canvas, so the
 * framework provides a minimal declarative layer: named widgets, value
 * binding, and deterministic `data-a3game-*` attributes that end-to-end
 * tests can assert against.
 *
 * Widgets are grouped into one flex container per anchor, so several
 * widgets may share an anchor and stack instead of overlapping.
 *
 * The layer owns no gameplay meaning; a generated game declares what its
 * widgets mean.
 */

const STYLE_ID = 'a3game-hud-style';
const BASE_STYLE = `
.a3game-hud-root { position:absolute; inset:0; pointer-events:none;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  color:#f3f5f8; text-shadow:0 1px 2px rgba(0,0,0,.6); }
.a3game-hud-slot { position:absolute; display:flex; flex-direction:column;
  gap:6px; pointer-events:none; max-width:calc(100% - 32px); }
.a3game-hud-slot[data-a3game-anchor="top-left"] { top:16px; left:16px;
  align-items:flex-start; }
.a3game-hud-slot[data-a3game-anchor="top-center"] { top:16px; left:50%;
  transform:translateX(-50%); align-items:center; }
.a3game-hud-slot[data-a3game-anchor="top-right"] { top:16px; right:16px;
  align-items:flex-end; }
.a3game-hud-slot[data-a3game-anchor="center"] { top:50%; left:50%;
  transform:translate(-50%,-50%); align-items:center; justify-content:center; }
.a3game-hud-slot[data-a3game-anchor="bottom-left"] { bottom:16px; left:16px;
  align-items:flex-start; }
.a3game-hud-slot[data-a3game-anchor="bottom-center"] { bottom:16px; left:50%;
  transform:translateX(-50%); align-items:center; }
.a3game-hud-slot[data-a3game-anchor="bottom-right"] { bottom:16px; right:16px;
  align-items:flex-end; }
.a3game-hud-widget { position:relative; pointer-events:none;
  font-size:14px; line-height:1.4; white-space:pre-line; }
.a3game-hud-bar { width:180px; height:10px; border-radius:5px;
  background:rgba(255,255,255,.18); overflow:hidden; }
.a3game-hud-bar > i { display:block; height:100%; width:0%;
  background:linear-gradient(90deg,#4ade80,#22d3ee); transition:width .12s; }
.a3game-hud-crosshair { width:18px; height:18px; }
.a3game-hud-crosshair::before, .a3game-hud-crosshair::after {
  content:''; position:absolute; background:rgba(255,255,255,.85); }
.a3game-hud-crosshair::before { left:8px; top:0; width:2px; height:18px; }
.a3game-hud-crosshair::after { top:8px; left:0; height:2px; width:18px; }
.a3game-hud-widget[data-a3game-kind="panel"] { padding:10px 14px;
  border-radius:8px; background:rgba(12,14,20,.62);
  border:1px solid rgba(255,255,255,.14); max-width:340px; }
.a3game-hud-widget[data-a3game-kind="banner"] { padding:18px 28px;
  border-radius:12px; background:rgba(12,14,20,.78); font-size:22px;
  font-weight:600; text-align:center;
  border:1px solid rgba(255,255,255,.18); }
`;

const ANCHORS = new Set([
  'top-left',
  'top-center',
  'top-right',
  'center',
  'bottom-left',
  'bottom-center',
  'bottom-right',
]);

const resolveElement = (target) => {
  if (!target) return null;
  if (typeof target === 'string') return document.querySelector(target);
  return target;
};

export class A3GameHudLayer {
  /**
   * @param {{container: string | HTMLElement}} options
   */
  constructor(options = {}) {
    const container = resolveElement(options.container);
    if (!container) {
      throw new Error('A3GameHudLayer requires an existing container');
    }
    this.container = container;
    this.root = document.createElement('div');
    this.root.className = 'a3game-hud-root';
    this.root.dataset.a3gameHud = 'root';
    this.container.appendChild(this.root);
    /** @type {Map<string, {element: HTMLElement, kind: string}>} */
    this.widgets = new Map();
    /** @type {Map<string, HTMLElement>} */
    this.slots = new Map();
    /** @type {Map<string, () => void>} */
    this.fadeTasks = new Map();
    this.#injectStyle();
  }

  /**
   * Declare a text widget.
   *
   * @param {string} name
   * @param {{anchor?: string, value?: string, className?: string}} [options]
   */
  addText(name, options = {}) {
    this.#createWidget(name, 'text', options);
    // Route the initial value through `setValue` so `data-a3game-value`
    // is populated from the start; a test must not have to wait for the
    // first update before HUD state becomes observable.
    this.setValue(name, options.value ?? '');
    return this.widgets.get(String(name)).element;
  }

  /**
   * Declare a normalized 0..1 bar widget, for example health or boost.
   *
   * @param {string} name
   * @param {{anchor?: string, value?: number, label?: string}} [options]
   */
  addBar(name, options = {}) {
    const element = this.#createWidget(name, 'bar', options);
    element.innerHTML = '';
    if (options.label) {
      const label = document.createElement('span');
      label.textContent = String(options.label);
      element.appendChild(label);
    }
    const bar = document.createElement('div');
    bar.className = 'a3game-hud-bar';
    bar.appendChild(document.createElement('i'));
    element.appendChild(bar);
    this.setValue(name, Number(options.value ?? 1));
    return element;
  }

  /** Declare a centred crosshair, for FPS-style aiming. */
  addCrosshair(name = 'crosshair') {
    const element = this.#createWidget(name, 'crosshair', {
      anchor: 'center',
    });
    element.classList.add('a3game-hud-crosshair');
    return element;
  }

  /**
   * Declare a multi-line panel, for objectives, inventory, or controls.
   *
   * Values may contain newlines; `setValue` replaces the whole block, so
   * a test still reads one `data-a3game-value` attribute.
   *
   * @param {string} name
   * @param {{anchor?: string, value?: string, className?: string}} [options]
   */
  addPanel(name, options = {}) {
    this.#createWidget(name, 'panel', options);
    this.setValue(name, options.value ?? '');
    return this.widgets.get(String(name)).element;
  }

  /**
   * Declare a prominent banner, for round results and prompts.
   *
   * @param {string} name
   * @param {{value?: string, visible?: boolean, anchor?: string}} [options]
   */
  addBanner(name, options = {}) {
    const element = this.#createWidget(name, 'banner', {
      anchor: options.anchor ?? 'center',
    });
    this.setValue(name, options.value ?? '');
    this.setVisible(name, options.visible !== false);
    return element;
  }

  /**
   * Update one widget's value.
   *
   * Text, panel, and banner widgets receive the stringified value; bar
   * widgets clamp to 0..1. Every update also writes `data-a3game-value`
   * so a Playwright assertion can read HUD state without screenshots.
   */
  setValue(name, value) {
    const widget = this.widgets.get(String(name));
    if (!widget) return false;
    const { element, kind } = widget;
    if (kind === 'bar') {
      const ratio = Math.max(0, Math.min(1, Number(value) || 0));
      const fill = element.querySelector('.a3game-hud-bar > i');
      if (fill) fill.style.width = `${(ratio * 100).toFixed(1)}%`;
      element.dataset.a3gameValue = ratio.toFixed(4);
      return true;
    }
    if (kind === 'text' || kind === 'panel' || kind === 'banner') {
      element.textContent = String(value ?? '');
    }
    element.dataset.a3gameValue = String(value ?? '');
    return true;
  }

  /** Batch-update several widgets. */
  setValues(values = {}) {
    for (const [name, value] of Object.entries(values)) {
      this.setValue(name, value);
    }
    return this;
  }

  setVisible(name, visible) {
    const key = String(name);
    const widget = this.widgets.get(key);
    if (!widget) return false;
    this.fadeTasks.get(key)?.();
    widget.element.style.display = visible ? '' : 'none';
    widget.element.style.opacity = visible ? '1' : '0';
    widget.element.style.pointerEvents = '';
    widget.element.dataset.a3gameVisible = visible ? 'true' : 'false';
    widget.element.dataset.a3gameFaded = 'false';
    return true;
  }

  remove(name) {
    const key = String(name);
    const widget = this.widgets.get(key);
    if (!widget) return false;
    this.fadeTasks.get(key)?.();
    widget.element.remove();
    this.widgets.delete(key);
    return true;
  }

  /** Fade using host frame time when supplied; otherwise use wall-clock time. */
  autoHide(name, options = {}) {
    const key = String(name);
    const widget = this.widgets.get(key);
    if (!widget) return () => {};
    const after = Number(options.after ?? 6);
    const fade = Number(options.fade ?? 0.8);
    if (![after, fade].every(value => Number.isFinite(value) && value >= 0)) {
      throw new RangeError('autoHide after/fade must be finite non-negative seconds');
    }
    if (options.host && typeof options.host.onRender !== 'function') {
      throw new TypeError('autoHide host requires onRender');
    }
    this.setVisible(key, true);
    widget.element.style.transition = 'none';
    let elapsed = 0;
    let detach = () => {};
    let cancelled = false;
    const cancel = () => {
      if (cancelled) return;
      cancelled = true;
      detach();
      if (this.fadeTasks.get(key) === cancel) this.fadeTasks.delete(key);
    };
    const update = (delta) => {
      if (cancelled) return;
      elapsed += Math.max(0, Number(delta) || 0);
      const opacity = elapsed < after ? 1 : (fade > 0 ? Math.max(0, 1 - (elapsed - after) / fade) : 0);
      widget.element.style.opacity = String(opacity);
      if (opacity <= 1e-8) {
        widget.element.style.display = 'none';
        widget.element.dataset.a3gameVisible = 'false';
        widget.element.dataset.a3gameFaded = 'true';
        cancel();
      }
    };
    this.fadeTasks.set(key, cancel);
    if (options.host) {
      detach = options.host.onRender(update);
    } else {
      let previous = performance.now();
      const timer = setInterval(() => {
        const now = performance.now();
        update((now - previous) / 1000);
        previous = now;
      }, 16);
      detach = () => clearInterval(timer);
    }
    update(0);
    return cancel;
  }

  /** @returns {object} the readable HUD state, for tests and evidence. */
  getState() {
    const state = {};
    for (const [name, widget] of this.widgets) {
      state[name] = {
        kind: widget.kind,
        value: widget.element.dataset.a3gameValue ?? '',
        visible: widget.element.style.display !== 'none' && widget.element.style.opacity !== '0',
        anchor: widget.element.dataset.a3gameAnchor ?? '',
      };
    }
    return state;
  }

  dispose() {
    for (const cancel of this.fadeTasks.values()) cancel();
    this.fadeTasks.clear();
    this.widgets.clear();
    this.slots.clear();
    this.root.remove();
  }

  #createWidget(name, kind, options) {
    const key = String(name);
    this.remove(key);
    const anchor = ANCHORS.has(options.anchor) ? options.anchor : 'top-left';
    const element = document.createElement('div');
    element.className = `a3game-hud-widget ${options.className ?? ''}`.trim();
    element.dataset.a3gameWidget = key;
    element.dataset.a3gameKind = kind;
    element.dataset.a3gameAnchor = anchor;
    this.#slot(anchor).appendChild(element);
    this.widgets.set(key, { element, kind });
    return element;
  }

  /** Anchor containers are created on demand and stack their children. */
  #slot(anchor) {
    const existing = this.slots.get(anchor);
    if (existing) return existing;
    const slot = document.createElement('div');
    slot.className = 'a3game-hud-slot';
    slot.dataset.a3gameAnchor = anchor;
    this.root.appendChild(slot);
    this.slots.set(anchor, slot);
    return slot;
  }

  #injectStyle() {
    if (document.getElementById(STYLE_ID)) return;
    const style = document.createElement('style');
    style.id = STYLE_ID;
    style.textContent = BASE_STYLE;
    document.head.appendChild(style);
  }
}
