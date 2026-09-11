import { describe, expect, it } from 'vitest';
import { A3GameCinematicPlayer } from '@a3game/playable';

function containerDouble() {
  return {
    children: [],
    appendChild(node) {
      node.parentNode = this;
      this.children.push(node);
    },
    removeChild(node) {
      this.children = this.children.filter((item) => item !== node);
      node.parentNode = null;
    },
  };
}

function videoDouble() {
  const listeners = new Map();
  return {
    dataset: {},
    paused: true,
    addEventListener(name, listener) {
      listeners.set(name, listener);
    },
    removeEventListener(name) {
      listeners.delete(name);
    },
    setAttribute() {},
    removeAttribute() {},
    load() {},
    pause() {
      this.paused = true;
    },
    async play() {
      this.paused = false;
    },
    emit(name) {
      listeners.get(name)?.();
    },
  };
}

function response(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

describe('A3GameCinematicPlayer', () => {
  it('submits a task and attaches the returned media URL', async () => {
    const container = containerDouble();
    const video = videoDouble();
    const calls = [];
    const fetchImpl = async (url, options) => {
      calls.push({ url, options });
      return response({
        payload: {
          request_id: 'cgreq_test',
          status: 'ready',
          task_id: 'opening_001',
          video: {
            artifact_id: 'cg_video_test',
            url: 'http://127.0.0.1:7870/api/media/cg-video/cg_video_test',
          },
        },
      });
    };
    const player = new A3GameCinematicPlayer({
      gatewayUrl: 'http://127.0.0.1:7870/',
      container,
      fetchImpl,
      createVideo: () => video,
    });

    const result = await player.play({
      gameId: 'game_demo',
      taskId: 'opening_001',
      triggerId: 'race_intro',
    });

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe('http://127.0.0.1:7870/api/cg-video');
    expect(JSON.parse(calls[0].options.body)).toMatchObject({
      game_id: 'game_demo',
      task_id: 'opening_001',
      trigger_id: 'race_intro',
    });
    expect(container.children).toEqual([video]);
    expect(video.src).toContain('/api/media/cg-video/cg_video_test');
    expect(result.playback.attached).toBe(true);
    expect(result.playback.started).toBe(true);

    player.dispose();
    expect(container.children).toHaveLength(0);
  });

  it('polls queued jobs before playback', async () => {
    const container = containerDouble();
    const video = videoDouble();
    const calls = [];
    let pollCount = 0;
    const fetchImpl = async (url) => {
      calls.push(url);
      if (url.endsWith('/api/cg-video')) {
        return response({
          payload: { request_id: 'cgreq_poll', status: 'queued' },
        }, 202);
      }
      pollCount += 1;
      return response({
        payload: {
          request_id: 'cgreq_poll',
          status: 'ready',
          task_id: 'cutscene_001',
          video: { url: '/api/media/cg-video/cg_video_poll' },
        },
      });
    };
    const player = new A3GameCinematicPlayer({
      gatewayUrl: 'http://127.0.0.1:7870',
      container,
      pollIntervalMs: 20,
      fetchImpl,
      createVideo: () => video,
    });

    await player.play({ gameId: 'game_demo', taskId: 'cutscene_001' });
    expect(pollCount).toBe(1);
    expect(calls[1]).toContain('/api/cg-video/cgreq_poll');
  });
});
