/**
 * Browser client and playback lifecycle for generated CG-video clips.
 *
 * The player owns only the request and media element lifecycle.  A generated
 * game decides when to call it and what the clip means to its gameplay.
 */

const resolveElement = (target) => {
  if (!target) return null;
  if (typeof target === 'string') return document.querySelector(target);
  return target;
};

const asPayload = (value) => {
  if (value && typeof value.payload === 'object') return value.payload;
  return value ?? {};
};

const errorMessage = (value, fallback) => {
  if (typeof value === 'string' && value) return value;
  if (value && typeof value.error === 'string' && value.error) {
    return value.error;
  }
  if (value && Array.isArray(value.errors) && value.errors.length > 0) {
    return String(value.errors[0]);
  }
  return fallback;
};

const wait = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

/**
 * Request and play one server-generated CG-video artifact.
 *
 * @example
 * const cinematic = new A3GameCinematicPlayer({
 *   gatewayUrl: 'http://127.0.0.1:7870',
 *   container: '#a3game-viewport',
 * });
 * await cinematic.play({ gameId: 'gameA', taskId: 'opening_001' });
 */
export class A3GameCinematicPlayer {
  /**
   * @param {{gatewayUrl: string, container?: string | HTMLElement,
   *          pollIntervalMs?: number, timeoutMs?: number,
   *          fetchImpl?: Function, createVideo?: Function}} options
   */
  constructor(options = {}) {
    const gatewayUrl = String(options.gatewayUrl ?? '').trim();
    if (!gatewayUrl) {
      throw new TypeError('A3GameCinematicPlayer requires gatewayUrl');
    }
    const fetchImpl = options.fetchImpl ?? globalThis.fetch?.bind(globalThis);
    if (typeof fetchImpl !== 'function') {
      throw new Error('A3GameCinematicPlayer requires a fetch implementation');
    }
    const container = resolveElement(options.container);
    const fallbackContainer =
      container ?? (typeof document !== 'undefined' ? document.body : null);
    if (!fallbackContainer) {
      throw new Error('A3GameCinematicPlayer requires a browser container');
    }

    this.gatewayUrl = gatewayUrl.replace(/\/+$/, '');
    this.container = fallbackContainer;
    this.fetchImpl = fetchImpl;
    this.pollIntervalMs = Math.max(20, Number(options.pollIntervalMs ?? 250));
    this.timeoutMs = Math.max(1000, Number(options.timeoutMs ?? 300000));
    this.createVideo =
      options.createVideo ??
      (() => {
        if (typeof document === 'undefined') {
          throw new Error('Video creation requires a browser document');
        }
        return document.createElement('video');
      });
    this.video = null;
    this.job = null;
    this.listeners = new Map();
    this.endedListener = null;
  }

  /**
   * Register a lifecycle listener.
   *
   * @param {'queued'|'running'|'ready'|'started'|'ended'|'stopped'|
   *         'error'|'autoplay_blocked'} event
   * @param {(payload: object) => void} listener
   * @returns {() => boolean} unsubscribe function
   */
  on(event, listener) {
    if (typeof listener !== 'function') {
      throw new TypeError('A3GameCinematicPlayer listener must be a function');
    }
    const key = String(event);
    const listeners = this.listeners.get(key) ?? new Set();
    listeners.add(listener);
    this.listeners.set(key, listeners);
    return () => listeners.delete(listener);
  }

  /**
   * Generate or retrieve one task and attach its video to the container.
   *
   * @param {{gameId: string, taskId: string, runId?: string,
   *          backend?: string, engine?: string, sessionId?: string,
   *          triggerId?: string, idempotencyKey?: string,
   *          options?: object, playback?: object}} request
   * @returns {Promise<object>} job and playback status
   */
  async play(request = {}) {
    const gameId = String(request.gameId ?? '').trim();
    const taskId = String(request.taskId ?? '').trim();
    if (!gameId || !taskId) {
      throw new TypeError('play requires gameId and taskId');
    }
    const playback = {
      autoplay: true,
      muted: true,
      loop: false,
      controls: false,
      plays_inline: true,
      ...(request.playback ?? {}),
    };
    const body = await this.#request('/api/cg-video', {
      method: 'POST',
      body: {
        game_id: gameId,
        task_id: taskId,
        run_id: request.runId ?? 'auto',
        backend: request.backend ?? '',
        engine: request.engine ?? '',
        session_id: request.sessionId ?? '',
        trigger_id: request.triggerId ?? '',
        idempotency_key: request.idempotencyKey ?? '',
        options: { ...(request.options ?? {}) },
        playback,
      },
    });
    let job = asPayload(body);
    this.#emit(job.status, job);
    const deadline = Date.now() + this.timeoutMs;
    while (job.status === 'queued' || job.status === 'running') {
      if (Date.now() >= deadline) {
        const error = new Error('CG-video request timed out while waiting');
        this.#emit('error', { ...job, error: error.message });
        throw error;
      }
      await wait(this.pollIntervalMs);
      const status = await this.#request(
        `/api/cg-video/${encodeURIComponent(String(job.request_id ?? ''))}`,
        { method: 'GET' },
      );
      job = asPayload(status);
      this.#emit(job.status, job);
    }
    if (job.status !== 'ready' || !job.video?.url) {
      const message = errorMessage(job, 'CG-video generation failed');
      this.#emit('error', { ...job, error: message });
      throw new Error(message);
    }

    this.stop();
    const video = this.createVideo();
    if (!video || typeof video !== 'object') {
      throw new Error('createVideo must return a video element');
    }
    video.src = String(job.video.url);
    video.autoplay = Boolean(playback.autoplay);
    video.muted = Boolean(playback.muted);
    video.loop = Boolean(playback.loop);
    video.controls = Boolean(playback.controls);
    video.playsInline = playback.plays_inline !== false;
    video.setAttribute?.('playsinline', '');
    video.dataset && (video.dataset.a3gameCgVideo = String(job.task_id));
    this.endedListener = () => this.#emit('ended', job);
    video.addEventListener?.('ended', this.endedListener);
    this.container.appendChild(video);
    this.video = video;
    this.job = job;

    let started = false;
    if (video.autoplay && typeof video.play === 'function') {
      try {
        await video.play();
        started = true;
        this.#emit('started', job);
      } catch (error) {
        this.#emit('autoplay_blocked', {
          ...job,
          error: String(error?.message ?? error),
        });
      }
    }
    return {
      ...job,
      playback: {
        ...playback,
        attached: true,
        started,
      },
    };
  }

  /** Stop and remove the active video element. */
  stop() {
    if (!this.video) return false;
    const video = this.video;
    if (this.endedListener) {
      video.removeEventListener?.('ended', this.endedListener);
    }
    video.pause?.();
    video.removeAttribute?.('src');
    video.load?.();
    video.parentNode?.removeChild(video);
    this.video = null;
    this.job = null;
    this.endedListener = null;
    this.#emit('stopped', {});
    return true;
  }

  /** Release the active media element and all listeners. */
  dispose() {
    this.stop();
    this.listeners.clear();
  }

  async #request(path, options = {}) {
    const response = await this.fetchImpl(`${this.gatewayUrl}${path}`, {
      method: options.method ?? 'GET',
      headers: { 'Content-Type': 'application/json' },
      ...(options.body === undefined
        ? {}
        : { body: JSON.stringify(options.body) }),
    });
    const value = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(errorMessage(value, `CG-video HTTP ${response.status}`));
    }
    return value;
  }

  #emit(event, payload) {
    if (!event) return;
    const listeners = this.listeners.get(String(event));
    if (!listeners) return;
    for (const listener of listeners) listener(payload);
  }
}
