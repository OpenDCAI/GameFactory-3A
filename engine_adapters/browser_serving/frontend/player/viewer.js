const state = {
  config: null,
  engine: {
    id: "ue5",
    capabilities: {},
  },
  lastControlAt: 0,
  activeMoveKeys: new Set(),
  currentLocomotion: "idle",
  idleTimer: null,
  avatarPresented: false,
  runtime: {
    ws: null,
    joined: false,
    captureActive: false,
    worldId: "",
    participantId: "",
    controllerId: "",
    entityId: "",
    seq: 0,
    yaw: 0,
    pitch: 0,
    mouseSensitivity: 0.22,
    inputTimer: null,
    heartbeatTimer: null,
    activeKeys: new Set(),
    lastBridgeError: "",
    lastBridgeErrorAt: 0,
  },
  editorCamera: {
    captureActive: false,
    pointerId: null,
    lastX: 0,
    lastY: 0,
    activeKeys: new Set(),
    pendingYaw: 0,
    pendingPitch: 0,
    ws: null,
    timer: null,
    bridgeReady: false,
  },
  assets: {
    avatar: [],
    motion: [],
  },
  multiplayer: {
    slots: [{ slot: 0 }, { slot: 1 }],
  },
  playerSession: {
    userId: "",
    sessionId: "",
    participantId: "",
    state: "",
    pixelStreamingUrl: "",
    pixelHttpPort: 0,
    pixelStreamerPort: 0,
    pixelSfuPort: 0,
    inputPort: 0,
    inputHost: "",
    serverUrl: "",
    previewMap: "",
    projectId: "",
    worldId: "",
    runtimePackageId: "",
    worlds: [],
    signallingPid: 0,
    ueClientPid: 0,
    character: null,
    inputWs: null,
    inputTimer: null,
    inputInFlight: false,
    inputSeq: 0,
    yaw: 0,
    pitch: 0,
    activeKeys: new Set(),
    previewCameraDragging: false,
    previewCameraLastX: 0,
    previewCameraLastY: 0,
    previewCameraLastError: "",
    previewCameraLastErrorAt: 0,
    previewCameraKeyTimer: null,
    viewportDragging: false,
    viewportLastX: 0,
    viewportLastY: 0,
    previewCameraInFlight: false,
    pendingPreviewCamera: null,
    pageInputEnabled: false,
  },
};

const $ = (id) => document.getElementById(id);
const RUNTIME_STORAGE_KEY = "openwl_runtime_session_v1";
const RUNTIME_TAB_KEY = "openwl_runtime_tab_id_v1";
const PLAYER_SESSION_STORAGE_KEY = "openwl_player_session_v1";
const MULTIPLAYER_DEBUG_STORAGE_KEY = "openwl_multiplayer_debug_slots_v1";
const PLAYER_CONTROL_KEYS = ["KeyW", "KeyA", "KeyS", "KeyD", "ShiftLeft", "ShiftRight", "Space"];
const PLAYER_ACTIONS = {
  KeyJ: "punch",
  KeyK: "kick",
  KeyF: "context_primary",
  KeyR: "context_reload_or_restart",
};
const PREVIEW_CAMERA_KEYS = ["KeyW", "KeyA", "KeyS", "KeyD"];
const EDITOR_CAMERA_KEYS = ["KeyW", "KeyA", "KeyS", "KeyD", "KeyQ", "KeyE", "ShiftLeft", "ShiftRight"];
let pixelReadyTimer = null;
let activePixelFrameUrl = "";
const VIEWER_PARAMS = new URLSearchParams(window.location.search);
const VIEWER_EMBEDDED = VIEWER_PARAMS.get("embed") === "1";
const VIEWER_EDITOR_MODE = VIEWER_PARAMS.get("mode") === "editor";
const VIEWER_PIXEL_URL = VIEWER_PARAMS.get("pixelUrl") || "";
const TECHNICAL_AVATAR_SUFFIXES = [
  "_skeleton",
  "_physicsasset",
  "_anim",
  "_anim_mixamo_com",
  "_diffuse",
  "_normal",
  "_specular",
  "_glossiness",
  "_roughness",
  "_metallic",
  "_basecolor",
  "_albedo",
  "_opacity",
  "_emissive",
  "_ao",
  "_mat",
  "_body",
  "mat",
];

function runtimeTabId() {
  let value = sessionStorage.getItem(RUNTIME_TAB_KEY);
  if (!value) {
    value = Math.random().toString(16).slice(2, 10);
    sessionStorage.setItem(RUNTIME_TAB_KEY, value);
  }
  return value;
}

function runtimePreviewLabel() {
  return `A3Game_RuntimePreview_${runtimeTabId()}`;
}

function setPill(element, ok, text) {
  element.textContent = text;
  element.classList.remove("ok", "bad", "muted");
  element.classList.add(ok ? "ok" : "bad");
}

function log(message, payload) {
  const now = new Date().toLocaleTimeString();
  const suffix = payload === undefined ? "" : `\n${JSON.stringify(payload, null, 2)}`;
  $("logOutput").textContent = `[${now}] ${message}${suffix}\n\n${$("logOutput").textContent}`;
}

async function api(path, options = {}) {
  const legacyPrefix = "/api/debug/engines/unreal";
  const resolvedPath = path.startsWith(legacyPrefix)
    ? `/api/engines/${encodeURIComponent(selectedEngineId())}/debug${path.slice(legacyPrefix.length)}`
    : path;
  const response = await fetch(resolvedPath, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail || response.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

function selectedEngineId() {
  return state.engine.id || "ue5";
}

function sessionUrl(suffix = "", params = {}) {
  const query = new URLSearchParams({
    engine: selectedEngineId(),
    ...params,
  });
  return `/api/sessions${suffix}?${query.toString()}`;
}

function applyEngineCapabilities() {
  const capabilities = state.engine.capabilities || {};
  $("playerSessionCreateBtn").disabled = !capabilities.runtime_sessions;
  $("refreshAssetsBtn").disabled = !capabilities.asset_inspection;
  if (!capabilities.pixel_streaming) {
    $("pixelMessage").textContent = "当前引擎不提供流式画面能力。";
  }
}

async function loadEngines() {
  const payload = await api("/api/engines");
  const engines = payload.engines || [];
  const select = $("engineSelect");
  select.innerHTML = "";
  engines.forEach((engine) => {
    const option = document.createElement("option");
    option.value = engine.engine_id;
    option.textContent = engine.display_name || engine.engine_id;
    select.appendChild(option);
  });
  const storedEngineId = loadStoredPlayerSession().engineId || "";
  const selected = engines.find(
    (engine) => engine.engine_id === (storedEngineId || state.engine.id)
  ) || engines[0];
  if (!selected) {
    throw new Error("没有已注册的运行引擎");
  }
  state.engine.id = selected.engine_id;
  state.engine.capabilities = selected.capabilities || {};
  select.value = selected.engine_id;
  applyEngineCapabilities();
}

function assetClass(asset) {
  return String(asset?.native?.class || "").split(".").pop();
}

function assetPath(asset) {
  return String(asset?.native?.path || "");
}

function artifactReference(asset) {
  return String(asset?.artifact_id || "");
}

function assetSkeletonPath(asset) {
  const metadata = asset?.metadata || {};
  if (metadata.skeleton_path) {
    return String(metadata.skeleton_path);
  }
  const dependency = (metadata.dependencies || []).find(
    (item) => item?.type === "skeleton"
  );
  return String(dependency?.assets?.[0] || "");
}

function assetRawName(asset) {
  return asset?.metadata?.display_name
    || asset?.asset_id
    || assetPath(asset).split("/").pop()
    || "";
}

function prettifyAssetName(name) {
  let value = String(name || "").trim();
  value = value.replace(/^(SK|SM|BP)_/i, "");
  value = value.replace(/_nonPBR$/i, "");
  value = value.replace(/_PBR$/i, "");
  value = value.replace(/_Anim$/i, "");
  value = value.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  return value || name || "Asset";
}

function avatarLabel(asset) {
  return prettifyAssetName(assetRawName(asset));
}

function motionLabel(asset) {
  let name = assetRawName(asset);
  const skeletonName = assetSkeletonPath(asset).split("/").pop() || "";
  const skeletonSuffix = skeletonName ? `_${skeletonName}_Anim` : "";
  if (skeletonSuffix && name.endsWith(skeletonSuffix)) {
    name = name.slice(0, -skeletonSuffix.length);
  }
  return prettifyAssetName(name);
}

function assetLabel(asset, kind = "") {
  if (kind === "avatar") {
    return avatarLabel(asset);
  }
  if (kind === "motion") {
    return motionLabel(asset);
  }
  return prettifyAssetName(assetRawName(asset));
}

function fillSelect(select, assets, kind = "") {
  const previousValue = select.value;
  select.innerHTML = "";
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = kind === "avatar" ? "选择 Avatar" : kind === "motion" ? "选择 Motion" : "请选择";
  select.appendChild(empty);
  for (const asset of assets || []) {
    const option = document.createElement("option");
    option.value = artifactReference(asset);
    option.textContent = assetLabel(asset, kind);
    option.title = [
      assetClass(asset),
      assetPath(asset),
      assetSkeletonPath(asset)
        ? `Skeleton: ${assetSkeletonPath(asset)}`
        : "",
    ].filter(Boolean).join(" | ");
    select.appendChild(option);
  }
  if (previousValue && [...select.options].some((option) => option.value === previousValue)) {
    select.value = previousValue;
  }
}

function normalizeAssetPath(path) {
  return (path || "").split(".", 1)[0].trim();
}

function hasTechnicalAvatarName(asset) {
  const name = assetRawName(asset).toLowerCase();
  return TECHNICAL_AVATAR_SUFFIXES.some((suffix) => name.endsWith(suffix));
}

function isPlayerAvatarAsset(asset) {
  return Boolean(artifactReference(asset))
    && assetClass(asset) === "SkeletalMesh"
    && Boolean(assetSkeletonPath(asset))
    && !hasTechnicalAvatarName(asset);
}

function isPlayerMotionAsset(asset) {
  return Boolean(artifactReference(asset))
    && assetClass(asset) === "AnimSequence";
}

function selectedAvatar() {
  const artifactId = $("avatarSelect").value;
  return (state.assets.avatar || []).find(
    (asset) => artifactReference(asset) === artifactId
  ) || null;
}

function assetNameForPath(path) {
  if (!path) {
    return "-";
  }
  const allAssets = [...(state.assets.avatar || []), ...(state.assets.motion || [])];
  const matched = allAssets.find(
    (asset) => artifactReference(asset) === path
      || normalizeAssetPath(assetPath(asset)) === normalizeAssetPath(path)
  );
  if (matched) {
    return assetRawName(matched) || path;
  }
  return path.split("/").pop() || path;
}

function filteredMotionsForSelectedAvatar() {
  const motions = state.assets.motion || [];
  const avatar = selectedAvatar();
  const showAll = Boolean($("showAllMotionsInput")?.checked);
  if (showAll) {
    return motions;
  }
  if (!avatar) {
    return [];
  }
  const skeletonPath = normalizeAssetPath(assetSkeletonPath(avatar));
  if (!skeletonPath) {
    return [];
  }
  const matched = motions.filter(
    (motion) => normalizeAssetPath(assetSkeletonPath(motion)) === skeletonPath
  );
  return matched;
}

function refreshMotionSelects() {
  const motions = filteredMotionsForSelectedAvatar();
  fillSelect($("motionSelect"), motions, "motion");
  fillSelect($("idleMotionSelect"), motions, "motion");
  fillSelect($("moveMotionSelect"), motions, "motion");
  restoreStoredMotionSelections();

  const avatar = selectedAvatar();
  if (avatar && !assetSkeletonPath(avatar)) {
    log("当前 Avatar 没有 Skeleton 信息，已隐藏 Motion；可勾选“显示全部 Motion（调试）”查看全部动作", { avatar: artifactReference(avatar) });
  } else if (avatar && motions.length === 0 && state.assets.motion.length > 0 && !$("showAllMotionsInput")?.checked) {
    log("当前 Avatar 没有匹配 Skeleton 的 Motion，可勾选“显示全部 Motion（调试）”查看未匹配动作", {
      avatar: artifactReference(avatar),
      avatar_skeleton: assetSkeletonPath(avatar),
      total_motion: state.assets.motion.length,
    });
  }
}

async function loadConfig() {
  state.config = await api("/api/debug/engines/unreal/viewer/config");
  if (VIEWER_PIXEL_URL) {
    state.config.pixel_streaming_url = VIEWER_PIXEL_URL;
    state.config.pixel_streaming_enabled = true;
  }
  const runtimeHostInput = $("runtimeUeHostInput");
  const runtimePortInput = $("runtimeUePortInput");
  if (runtimeHostInput && state.config.runtime_ue_host) {
    runtimeHostInput.value = state.config.runtime_ue_host;
  }
  if (runtimePortInput && state.config.runtime_ue_port) {
    runtimePortInput.value = String(state.config.runtime_ue_port);
  }
  const pixel = state.engine.capabilities.pixel_streaming
    ? (
      VIEWER_PIXEL_URL
        ? await api(
          `/api/debug/engines/unreal/viewer/url-status?url=${encodeURIComponent(VIEWER_PIXEL_URL)}`
        ).then((status) => ({
          ...status,
          configured: true,
          stream_ready: Boolean(status.reachable),
        }))
        : await api("/api/debug/engines/unreal/viewer/pixel-status")
    )
    : {
        configured: false,
        stream_ready: false,
        message: "当前引擎不提供流式画面能力。",
      };
  const frame = $("pixelFrame");
  const fallback = $("pixelFallback");
  const directLink = $("pixelDirectLink");
  $("pixelMessage").textContent = pixel.message || "";
  if (directLink && state.config.pixel_streaming_url) {
    directLink.href = state.config.pixel_streaming_url;
    directLink.hidden = false;
  }

  if (VIEWER_EDITOR_MODE) {
    if (state.config.pixel_streaming_url && pixel.stream_ready) {
      frame.src = pixelPlaybackUrl(state.config.pixel_streaming_url);
      frame.hidden = false;
      fallback.hidden = true;
      setPill($("pixelStatus"), true, "Editor stream: ready");
      updateViewportInputOverlay();
      focusPixelFrameSoon(500);
    } else {
      frame.removeAttribute("src");
      frame.hidden = true;
      fallback.hidden = false;
      $("pixelMessage").textContent = pixel.message || "UE Editor Pixel Streaming 尚未就绪。";
      setPill($("pixelStatus"), false, "Editor stream: offline");
      updateViewportInputOverlay();
    }
    return;
  }

  if (pixel.configured && pixel.stream_ready) {
    frame.src = state.config.pixel_streaming_url;
    frame.hidden = false;
    fallback.hidden = true;
    setPill($("pixelStatus"), true, "Client stream: reachable");
  } else {
    frame.removeAttribute("src");
    frame.hidden = true;
    fallback.hidden = false;
    setPill($("pixelStatus"), true, pixel.configured ? "Client stream: waiting" : "Client stream: start client");
  }
}

async function refreshStatus() {
  if (!state.engine.capabilities.asset_import) {
    setPill($("ueStatus"), true, "Editor: not supported");
    return { ok: true, supported: false };
  }
  const status = await api("/api/debug/engines/unreal/status");
  setPill(
    $("ueStatus"),
    status.ok || !VIEWER_EDITOR_MODE,
    status.ok ? "Editor RC: connected" : (VIEWER_EDITOR_MODE ? "Editor RC: offline" : "Editor RC: not used"),
  );
  return status;
}

async function refreshAssets() {
  const groups = await api(
    `/api/assets/groups?engine=${encodeURIComponent(selectedEngineId())}`
  );
  const rawAvatars = groups.avatar || [];
  const rawMotions = groups.motion || [];
  state.assets.avatar = rawAvatars.filter(isPlayerAvatarAsset);
  state.assets.motion = rawMotions.filter(isPlayerMotionAsset);
  fillSelect($("avatarSelect"), state.assets.avatar, "avatar");
  restoreStoredAvatarSelection();
  refreshMotionSelects();
  const filteredMotionCount = filteredMotionsForSelectedAvatar().length;
  const avatar = selectedAvatar();
  const summary = {
    selectable_avatar: state.assets.avatar.length,
    runtime_motion: state.assets.motion.length,
    matched_motion: filteredMotionCount,
    hidden_non_runtime_avatar: Math.max(0, rawAvatars.length - state.assets.avatar.length),
    hidden_non_runtime_motion: Math.max(0, rawMotions.length - state.assets.motion.length),
    avatar_skeleton: assetSkeletonPath(avatar),
    effect: (groups.effect || []).length,
    prop: (groups.prop || []).length,
  };
  if (groups._errors) {
    summary.partial_errors = groups._errors;
    log("资产已部分刷新，部分类型查询失败", summary);
    return;
  }
  log("资产已刷新", summary);
}

function actorLabel() {
  return $("actorLabelInput")?.value.trim() || "A3Game_Presentation_Actor";
}

function forwardOffsetYaw() {
  return Number($("forwardOffsetInput")?.value || 0) || 0;
}

function faceDirection() {
  return Boolean($("faceDirectionInput")?.checked);
}

function debugKeyboardEnabled() {
  return Boolean($("debugKeyboardInput")?.checked);
}

function setRuntimeStatus(ok, text) {
  const element = $("runtimeStatus");
  if (element) {
    setPill(element, ok, text);
  }
}

function setPixelFrameUrl(url) {
  const frame = $("pixelFrame");
  const fallback = $("pixelFallback");
  const directLink = $("pixelDirectLink");
  if (!url || !frame || !fallback) {
    return;
  }
  const frameUrl = pixelPlaybackUrl(url, true);
  if (directLink) {
    directLink.href = pixelPlaybackUrl(url, false);
    directLink.hidden = false;
  }
  if (activePixelFrameUrl === frameUrl && !frame.hidden) {
    if (VIEWER_EDITOR_MODE) {
      focusPixelFrame();
    } else {
      focusControlSurface();
    }
    return;
  }
  frame.hidden = true;
  fallback.hidden = false;
  $("pixelMessage").textContent = `正在启动画面服务: ${url}`;
  waitForPixelPage(url, frameUrl);
}

function pixelPlaybackUrl(url, embedded = true) {
  try {
    const playbackUrl = new URL(url, window.location.href);
    const useNativeInput = VIEWER_EDITOR_MODE || !embedded;
    playbackUrl.searchParams.set("AutoConnect", "true");
    playbackUrl.searchParams.set("AutoPlayVideo", "true");
    playbackUrl.searchParams.set("StartVideoMuted", "true");
    playbackUrl.searchParams.set("KeyboardInput", String(useNativeInput));
    playbackUrl.searchParams.set("MouseInput", String(useNativeInput));
    playbackUrl.searchParams.set("SuppressBrowserKeys", String(useNativeInput));
    playbackUrl.searchParams.set("HoveringMouse", "false");
    return playbackUrl.toString();
  } catch (err) {
    return url;
  }
}

function clearPixelFrame() {
  if (pixelReadyTimer) {
    window.clearInterval(pixelReadyTimer);
    pixelReadyTimer = null;
  }
  activePixelFrameUrl = "";
  const frame = $("pixelFrame");
  const fallback = $("pixelFallback");
  const directLink = $("pixelDirectLink");
  if (frame) {
    frame.removeAttribute("src");
    frame.hidden = true;
  }
  if (fallback) {
    fallback.hidden = false;
  }
  if (directLink) {
    directLink.hidden = true;
  }
  updateViewportInputOverlay();
}

function focusPixelFrameSoon(delay = 250) {
  window.setTimeout(
    VIEWER_EDITOR_MODE ? focusPixelFrame : focusControlSurface,
    delay
  );
}

function focusPixelFrame() {
  const frame = $("pixelFrame");
  if (!frame || frame.hidden) {
    return;
  }
  try {
    frame.focus();
    frame.contentWindow?.focus();
  } catch (err) {
    frame.focus();
  }
}

function focusControlSurface() {
  if (document.activeElement && typeof document.activeElement.blur === "function") {
    document.activeElement.blur();
  }
  document.body.focus({ preventScroll: true });
}

async function pixelPageReachable(url) {
  try {
    const status = await api(`/api/debug/engines/unreal/viewer/url-status?url=${encodeURIComponent(url)}`);
    return Boolean(status.reachable);
  } catch (err) {
    return false;
  }
}

function waitForPixelPage(url, frameUrl = url) {
  if (pixelReadyTimer) {
    window.clearInterval(pixelReadyTimer);
    pixelReadyTimer = null;
  }
  let attempts = 0;
  setPill($("pixelStatus"), true, "Client stream: starting");
  $("pixelMessage").textContent = `正在启动 Pixel Streaming 画面服务。首次启动会检查 Node/前端文件，通常需要几十秒: ${url}`;
  pixelReadyTimer = window.setInterval(async () => {
    attempts += 1;
    if (await pixelPageReachable(url)) {
      window.clearInterval(pixelReadyTimer);
      pixelReadyTimer = null;
      const frame = $("pixelFrame");
      const fallback = $("pixelFallback");
      activePixelFrameUrl = frameUrl;
      frame.src = frameUrl;
      frame.hidden = false;
      fallback.hidden = true;
      updateViewportInputOverlay();
      frame.addEventListener("load", () => {
        if (VIEWER_EDITOR_MODE) {
          focusPixelFrame();
        } else {
          focusControlSurface();
        }
      }, { once: true });
      if (VIEWER_EDITOR_MODE) {
        focusPixelFrame();
      } else {
        focusControlSurface();
      }
      setPill($("pixelStatus"), true, "Client stream: reachable");
      return;
    }
    $("pixelMessage").textContent = `正在等待画面服务启动... (${attempts}s) ${url}`;
    if (attempts >= 40) {
      window.clearInterval(pixelReadyTimer);
      pixelReadyTimer = null;
      setPill($("pixelStatus"), false, "Client stream: unreachable");
      $("pixelMessage").textContent = `画面服务仍不可达，请确认 Pixel Streaming 窗口是否启动: ${url}`;
    }
  }, 1000);
}

function runtimeWsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/engines/${encodeURIComponent(selectedEngineId())}/debug/runtime/ws`;
}

function playerInputWsUrl(sessionId) {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/sessions/${encodeURIComponent(sessionId)}/input-ws`;
}

function editorCameraWsUrl() {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/engines/${encodeURIComponent(selectedEngineId())}/debug/editor-camera/input-ws`;
}

function selectValueIfAvailable(id) {
  const select = $(id);
  if (!select || !select.value) {
    return "";
  }
  return [...select.options].some((option) => option.value === select.value) ? select.value : "";
}

function selectedAvatarPath() {
  return $("avatarSelect").value || $("avatarPathInput")?.value.trim() || "";
}

function selectedIdleMotionPath() {
  return selectValueIfAvailable("idleMotionSelect") || $("idleMotionPathInput")?.value.trim() || "";
}

function selectedMoveMotionPath() {
  return selectValueIfAvailable("moveMotionSelect") || $("moveMotionPathInput")?.value.trim() || "";
}

function selectValueIfOptionExists(id, value) {
  const select = $(id);
  if (!select || !value) {
    return false;
  }
  const normalized = normalizeAssetPath(value);
  const option = [...select.options].find((item) => normalizeAssetPath(item.value) === normalized);
  if (!option) {
    return false;
  }
  select.value = option.value;
  return true;
}

function loadStoredPlayerSession() {
  try {
    return JSON.parse(sessionStorage.getItem(PLAYER_SESSION_STORAGE_KEY) || "{}");
  } catch (err) {
    return {};
  }
}

function savePlayerSession() {
  const selectedCharacter = selectedAvatarPath() ? selectedCharacterConfig() : null;
  sessionStorage.setItem(PLAYER_SESSION_STORAGE_KEY, JSON.stringify({
    engineId: selectedEngineId(),
    userId: state.playerSession.userId,
    sessionId: state.playerSession.sessionId,
    participantId: state.playerSession.participantId,
    state: state.playerSession.state,
    pixelStreamingUrl: state.playerSession.pixelStreamingUrl,
    pixelHttpPort: state.playerSession.pixelHttpPort,
    pixelStreamerPort: state.playerSession.pixelStreamerPort,
    pixelSfuPort: state.playerSession.pixelSfuPort,
    inputHost: state.playerSession.inputHost,
    inputPort: state.playerSession.inputPort,
    serverUrl: state.playerSession.serverUrl,
    previewMap: state.playerSession.previewMap,
    projectId: state.playerSession.projectId,
    worldId: state.playerSession.worldId,
    runtimePackageId: state.playerSession.runtimePackageId,
    signallingPid: state.playerSession.signallingPid,
    ueClientPid: state.playerSession.ueClientPid,
    yaw: state.playerSession.yaw,
    pitch: state.playerSession.pitch,
    avatarPath: selectedAvatarPath(),
    idleMotionPath: selectedIdleMotionPath(),
    moveMotionPath: selectedMoveMotionPath(),
    character: selectedCharacter || state.playerSession.character || null,
  }));
}

function clearStoredPlayerSession() {
  sessionStorage.removeItem(PLAYER_SESSION_STORAGE_KEY);
}

function restoreStoredAvatarSelection() {
  const stored = loadStoredPlayerSession();
  const avatarPath = stored.avatarPath || stored.character?.avatar_id || "";
  if (selectValueIfOptionExists("avatarSelect", avatarPath)) {
    if ($("avatarPathInput")) {
      $("avatarPathInput").value = "";
    }
  } else if (avatarPath && $("avatarPathInput")) {
    $("avatarPathInput").value = avatarPath;
  }
}

function fillWorldSelect(worlds) {
  const select = $("worldSelect");
  if (!select) {
    return;
  }
  const previous = select.value || loadStoredPlayerSession().runtimePackageId || "";
  select.innerHTML = "";
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "选择已发布 World";
  select.appendChild(empty);
  for (const world of worlds || []) {
    const option = document.createElement("option");
    option.value = world.package_id || "";
    option.textContent = worldOptionLabel(world);
    option.title = [world.project_id || "", world.package_id || ""].filter(Boolean).join(" | ");
    select.appendChild(option);
  }
  if (previous && [...select.options].some((option) => option.value === previous)) {
    select.value = previous;
  }
  refreshWorldSummary();
}

function worldMetadata(world) {
  const manifest = world?.manifest || {};
  const worldSpec = manifest.world || {};
  return {
    manifest,
    worldSpec,
    metadata: worldSpec.metadata || {},
    ue: manifest.ue || {},
    capabilities: manifest.capabilities || {},
  };
}

function basename(path) {
  return String(path || "").replaceAll("\\", "/").split("/").filter(Boolean).pop() || "";
}

function worldOptionLabel(world) {
  const { metadata, ue } = worldMetadata(world);
  const representation = String(metadata.representation || "");
  let kind = "Generated Scene";
  let sourceName = basename(metadata.source_path);
  if (representation === "native_ue_level" || ue.level_path) {
    kind = "UE Map";
    sourceName = basename(ue.level_path || metadata.level_path);
  } else if (sourceName) {
    const suffix = sourceName.includes(".") ? sourceName.split(".").pop().toUpperCase() : "";
    kind = suffix ? `${suffix} Scene` : kind;
  }
  const worldName = prettifyAssetName(world.world_id || sourceName || "world");
  return `${worldName} · ${kind}`;
}

function selectedWorldPackage() {
  const packageId = $("worldSelect")?.value || "";
  return (state.playerSession.worlds || []).find((world) => world.package_id === packageId) || null;
}

function refreshWorldSummary() {
  const summary = $("worldSummaryText");
  if (!summary) {
    return;
  }
  const world = selectedWorldPackage();
  if (!world) {
    summary.textContent = "尚未选择 Scene";
    return;
  }
  const { worldSpec, metadata, ue, capabilities } = worldMetadata(world);
  const sceneName = basename(ue.level_path || metadata.source_path) || world.world_id || "Scene";
  const entityCount = (worldSpec.entities || []).length;
  const collisionText = capabilities.collidable ? "碰撞已启用" : "未确认碰撞";
  summary.textContent = `${sceneName} · ${entityCount} 个场景实体 · ${collisionText}`;
}

async function refreshPlayerCatalog() {
  const catalog = await api(sessionUrl("/catalog"));
  state.playerSession.worlds = catalog.worlds || [];
  fillWorldSelect(state.playerSession.worlds);
  log("Player Catalog 已刷新", {
    avatars: (catalog.avatars || []).length,
    motions: (catalog.motions || []).length,
    worlds: state.playerSession.worlds.length,
  });
  return catalog;
}

function restoreStoredMotionSelections() {
  const stored = loadStoredPlayerSession();
  const idleMotionPath = stored.idleMotionPath || stored.character?.idle_animation || "";
  const moveMotionPath = stored.moveMotionPath || stored.character?.move_animation || "";
  const idleRestored = selectValueIfOptionExists("idleMotionSelect", idleMotionPath);
  const moveRestored = selectValueIfOptionExists("moveMotionSelect", moveMotionPath);
  selectValueIfOptionExists("motionSelect", moveMotionPath || idleMotionPath);
  if (idleMotionPath && !idleRestored && $("idleMotionPathInput")) {
    $("idleMotionPathInput").value = idleMotionPath;
  }
  if (moveMotionPath && !moveRestored && $("moveMotionPathInput")) {
    $("moveMotionPathInput").value = moveMotionPath;
  }
}

async function restorePlayerSessionFromStorage() {
  const stored = loadStoredPlayerSession();
  if (!stored.sessionId) {
    return false;
  }
  state.playerSession.userId = stored.userId || "";
  state.playerSession.sessionId = stored.sessionId || "";
  state.playerSession.participantId = stored.participantId || "";
  state.playerSession.state = stored.state || "";
  state.playerSession.pixelStreamingUrl = stored.pixelStreamingUrl || "";
  state.playerSession.pixelHttpPort = Number(stored.pixelHttpPort || 0);
  state.playerSession.pixelStreamerPort = Number(stored.pixelStreamerPort || 0);
  state.playerSession.pixelSfuPort = Number(stored.pixelSfuPort || 0);
  state.playerSession.inputHost = stored.inputHost || "";
  state.playerSession.inputPort = Number(stored.inputPort || 0);
  state.playerSession.serverUrl = stored.serverUrl || "";
  state.playerSession.previewMap = stored.previewMap || "";
  state.playerSession.projectId = stored.projectId || "";
  state.playerSession.worldId = stored.worldId || "";
  state.playerSession.runtimePackageId = stored.runtimePackageId || "";
  state.playerSession.signallingPid = Number(stored.signallingPid || 0);
  state.playerSession.ueClientPid = Number(stored.ueClientPid || 0);
  state.playerSession.character = stored.character || null;
  state.playerSession.yaw = Number(stored.yaw || 0);
  state.playerSession.pitch = Number(stored.pitch || 0);
  refreshPlayerSessionText();
  updateViewportInputOverlay();
  if (stored.pixelStreamingUrl) {
    setPixelFrameUrl(stored.pixelStreamingUrl);
  }

  try {
    const session = await api(sessionUrl(`/${encodeURIComponent(stored.sessionId)}`));
    if (isDeadPlayerSession(session)) {
      resetPlayerSessionState();
      log("上次玩家客户端已退出，已清除本标签页恢复状态", session);
      return false;
    }
    applyPlayerSessionResult(session);
    if (isPlayerInWorld()) {
      activatePlayerInput();
    }
    log("已恢复本浏览器标签页的玩家客户端", session);
    return true;
  } catch (err) {
    try {
      const recovered = await api("/api/sessions/recover", {
        method: "POST",
        body: JSON.stringify({ engine: selectedEngineId(), session: stored }),
      });
      if (isDeadPlayerSession(recovered)) {
        resetPlayerSessionState();
        log("上次玩家客户端已退出，已清除本标签页恢复状态", recovered);
        return false;
      }
      applyPlayerSessionResult(recovered);
      if (isPlayerInWorld()) {
        activatePlayerInput();
      }
      log("已接管刷新前的玩家客户端", recovered);
      return true;
    } catch (recoverErr) {
      resetPlayerSessionState();
      log("上次玩家客户端不可用，已清除本标签页恢复状态", recoverErr.message || err.message);
      return false;
    }
  }
}

function loadStoredMultiplayerSlots() {
  try {
    const stored = JSON.parse(localStorage.getItem(MULTIPLAYER_DEBUG_STORAGE_KEY) || "[]");
    if (Array.isArray(stored)) {
      for (const slot of stored) {
        const slotIndex = Number(slot.slot);
        if (slotIndex === 0 || slotIndex === 1) {
          state.multiplayer.slots[slotIndex] = {
            slot: slotIndex,
            participant_id: slot.participant_id || `p_debug_${slotIndex}`,
            avatar_asset_path: slot.avatar_asset_path || "",
            idle_animation_path: slot.idle_animation_path || "",
            move_animation_path: slot.move_animation_path || "",
          };
        }
      }
    }
  } catch (err) {
    // Ignore corrupt local debug state.
  }
}

function saveMultiplayerSlots() {
  localStorage.setItem(MULTIPLAYER_DEBUG_STORAGE_KEY, JSON.stringify(state.multiplayer.slots));
}

function refreshMultiplayerSlotText() {
  for (const slotIndex of [0, 1]) {
    const element = $(`mpSlot${slotIndex}Text`);
    if (!element) {
      continue;
    }
    const slot = state.multiplayer.slots[slotIndex] || {};
    if (!slot.avatar_asset_path) {
      element.textContent = "-";
      continue;
    }
    element.textContent = `${assetNameForPath(slot.avatar_asset_path)} / idle=${assetNameForPath(slot.idle_animation_path)} / move=${assetNameForPath(slot.move_animation_path)}`;
  }
}

function refreshRuntimeMeta() {
  $("runtimeParticipantText").textContent = state.runtime.participantId || "-";
  $("runtimeControllerText").textContent = state.runtime.controllerId || "-";
  $("runtimeEntityText").textContent = state.runtime.entityId || "-";
  refreshRuntimeInputText();
}

function refreshPlayerSessionText() {
  const session = state.playerSession;
  const sessionText = $("playerSessionText");
  const stateText = $("playerSessionStateText");
  const streamText = $("playerStreamText");
  const portsText = $("playerPortsText");
  if (sessionText) {
    sessionText.textContent = session.sessionId || "-";
  }
  if (stateText) {
    stateText.textContent = session.state || "-";
  }
  if (streamText) {
    streamText.textContent = session.pixelStreamingUrl || "-";
  }
  if (portsText) {
    portsText.textContent = session.inputPort ? `input=${session.inputHost || "127.0.0.1"}:${session.inputPort}` : "-";
  }
}

function applyPlayerSessionResult(result) {
  state.playerSession.userId = result.user_id || state.playerSession.userId || "";
  state.playerSession.sessionId = result.session_id || state.playerSession.sessionId || "";
  state.playerSession.participantId = result.participant_id || state.playerSession.participantId || "";
  state.playerSession.state = result.state || result.status || state.playerSession.state || "";
  state.playerSession.pixelStreamingUrl = result.pixel_streaming_url || state.playerSession.pixelStreamingUrl || "";
  state.playerSession.pixelHttpPort = Number(result.pixel_http_port || state.playerSession.pixelHttpPort || 0);
  state.playerSession.pixelStreamerPort = Number(result.pixel_streamer_port || state.playerSession.pixelStreamerPort || 0);
  state.playerSession.pixelSfuPort = Number(result.pixel_sfu_port || state.playerSession.pixelSfuPort || 0);
  state.playerSession.inputHost = result.ue_input_host || state.playerSession.inputHost || "";
  state.playerSession.inputPort = Number(result.ue_input_port || result.input_port || state.playerSession.inputPort || 0);
  state.playerSession.serverUrl = result.server_url || state.playerSession.serverUrl || "";
  state.playerSession.previewMap = result.preview_map || state.playerSession.previewMap || "";
  state.playerSession.projectId = result.project_id || state.playerSession.projectId || "";
  state.playerSession.worldId = result.world_id || state.playerSession.worldId || "";
  state.playerSession.runtimePackageId = result.runtime_package_id || state.playerSession.runtimePackageId || "";
  state.playerSession.signallingPid = Number(result.signalling_pid || state.playerSession.signallingPid || 0);
  state.playerSession.ueClientPid = Number(result.ue_client_pid || state.playerSession.ueClientPid || 0);
  state.playerSession.character = result.character || state.playerSession.character || null;
  refreshPlayerSessionText();
  updateViewportInputOverlay();
  if (result.pixel_streaming_url) {
    setPixelFrameUrl(result.pixel_streaming_url);
  }
  if (result.ue_input_host && $("runtimeUeHostInput")) {
    $("runtimeUeHostInput").value = result.ue_input_host;
  }
  if (result.ue_input_port && $("runtimeUePortInput")) {
    $("runtimeUePortInput").value = String(result.ue_input_port);
  }
  savePlayerSession();
}

function resetPlayerSessionState() {
  stopPlayerInputTimer();
  if (state.playerSession.previewCameraKeyTimer) {
    window.clearInterval(state.playerSession.previewCameraKeyTimer);
  }
  clearPixelFrame();
  state.playerSession = {
    userId: "",
    sessionId: "",
    participantId: "",
    state: "",
    pixelStreamingUrl: "",
    pixelHttpPort: 0,
    pixelStreamerPort: 0,
    pixelSfuPort: 0,
    inputPort: 0,
    inputHost: "",
    serverUrl: "",
    previewMap: "",
    projectId: "",
    worldId: "",
    runtimePackageId: "",
    worlds: state.playerSession.worlds || [],
    signallingPid: 0,
    ueClientPid: 0,
    character: null,
    inputWs: null,
    inputTimer: null,
    inputInFlight: false,
    inputSeq: 0,
    yaw: 0,
    pitch: 0,
    activeKeys: new Set(),
    previewCameraDragging: false,
    previewCameraLastX: 0,
    previewCameraLastY: 0,
    previewCameraLastError: "",
    previewCameraLastErrorAt: 0,
    previewCameraKeyTimer: null,
    viewportDragging: false,
    viewportLastX: 0,
    viewportLastY: 0,
    previewCameraInFlight: false,
    pendingPreviewCamera: null,
    pageInputEnabled: false,
  };
  refreshPlayerSessionText();
  updateViewportInputOverlay();
  clearStoredPlayerSession();
}

function playerMoveVector() {
  const keys = state.playerSession.activeKeys;
  const moveX = (keys.has("KeyD") ? 1 : 0) + (keys.has("KeyA") ? -1 : 0);
  const moveY = (keys.has("KeyW") ? 1 : 0) + (keys.has("KeyS") ? -1 : 0);
  return {
    moveX: Math.max(-1, Math.min(1, moveX)),
    moveY: Math.max(-1, Math.min(1, moveY)),
  };
}

function sendPlayerInput() {
  const session = state.playerSession;
  if (!session.sessionId || !isPlayerInWorld() || !session.pageInputEnabled) {
    return;
  }
  const { moveX, moveY } = playerMoveVector();
  const boost = session.activeKeys.has("ShiftLeft")
    || session.activeKeys.has("ShiftRight");
  const handbrake = session.activeKeys.has("Space");
  const payload = {
    type: "input",
    move_x: moveX,
    move_y: moveY,
    yaw: session.yaw,
    pitch: session.pitch,
    run: boost,
    jump: handbrake,
    seq: ++session.inputSeq,
    ts: Date.now() / 1000,
  };
  if (isRacingPlayerSession()) {
    Object.assign(payload, {
      steering: moveX,
      throttle: moveY,
      boost,
      handbrake,
    });
  }
  if (session.inputWs && session.inputWs.readyState === WebSocket.OPEN) {
    session.inputWs.send(JSON.stringify(payload));
    return;
  }
  if (session.inputInFlight) {
    return;
  }
  session.inputInFlight = true;
  fetch(`/api/sessions/${encodeURIComponent(session.sessionId)}/input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ engine: selectedEngineId(), input: payload }),
  }).catch(() => {}).finally(() => {
    session.inputInFlight = false;
  });
}

function sendPlayerAction(action) {
  const session = state.playerSession;
  if (!session.sessionId || !isPlayerInWorld() || !action) {
    return;
  }
  fetch(`/api/sessions/${encodeURIComponent(session.sessionId)}/input`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      engine: selectedEngineId(),
      input: {
        action,
        seq: ++session.inputSeq,
        ts: Date.now() / 1000,
      },
    }),
  }).catch(() => {});
}

function isPlayerInWorld() {
  return state.playerSession.state === "IN_WORLD" || String(state.playerSession.state || "").toLowerCase() === "in_world";
}

function isRacingPlayerSession() {
  const session = state.playerSession;
  const character = session.character || {};
  return Boolean(character.vehicle_id || character.vehicle_asset_path)
    || String(session.worldId || "").toLowerCase().includes("racing");
}

function canAdjustPreviewCamera() {
  const stateName = String(state.playerSession.state || "").toUpperCase();
  return Boolean(state.playerSession.sessionId)
    && stateName !== "CONNECTING"
    && !isPlayerInWorld();
}

function canControlViewport() {
  if (VIEWER_EDITOR_MODE) {
    const frame = $("pixelFrame");
    return Boolean(frame && !frame.hidden);
  }
  return canAdjustPreviewCamera() || isPlayerInWorld();
}

function updateViewportInputOverlay() {
  const overlay = $("viewportInputOverlay");
  if (!overlay) {
    return;
  }
  if (VIEWER_EDITOR_MODE) {
    const frame = $("pixelFrame");
    overlay.hidden = !frame || frame.hidden;
    overlay.title = "点击后使用鼠标和 WASD 控制 UE Editor 视角";
    const editorHint = overlay.querySelector("span");
    if (editorHint) {
      editorHint.textContent = "点击控制视角 · 鼠标观察 · WASD 移动 · Q/E 升降";
    }
    return;
  }
  overlay.hidden = !canControlViewport();
  const hint = overlay.querySelector("span");
  if (isPlayerInWorld()) {
    if (isRacingPlayerSession()) {
      overlay.title = "赛车控制：按住 W，再同时按 A/D 转向；S 刹车/倒车，Shift 氮气，Space 手刹，R 重置";
      if (hint) {
        hint.textContent = "W+A/D 驾驶转向 · S 刹车/倒车 · Shift 氮气 · Space 手刹 · R 重置";
      }
    } else {
      overlay.title = "角色控制：WASD 移动，Shift 跑步，Space 跳跃，左键/F 射击，R 换弹，J/K 格斗";
      if (hint) {
        hint.textContent = "WASD 移动 · Shift 跑步 · Space 跳跃 · 左键/F 射击 · R 换弹";
      }
    }
  } else if (canAdjustPreviewCamera()) {
    overlay.title = "预览相机：左键拖拽旋转，滚轮或 W/S 缩放，A/D 环绕，Shift+拖拽平移";
    if (hint) {
      hint.textContent = "拖拽旋转 · W/S 缩放 · A/D 环绕 · Shift 拖拽平移";
    }
  } else {
    overlay.title = "";
  }
}

async function startEditorCameraBridge(force = false) {
  if (!VIEWER_EDITOR_MODE || (state.editorCamera.bridgeReady && !force)) {
    return;
  }
  const result = await api("/api/debug/engines/unreal/editor-camera/start", {
    method: "POST",
    body: JSON.stringify({}),
  });
  state.editorCamera.bridgeReady = Boolean(result.ok);
  ensureEditorCameraSocket();
}

function ensureEditorCameraSocket() {
  const editor = state.editorCamera;
  if (!VIEWER_EDITOR_MODE || (editor.ws && editor.ws.readyState <= WebSocket.OPEN)) {
    return editor.ws;
  }
  const ws = new WebSocket(editorCameraWsUrl());
  editor.ws = ws;
  ws.addEventListener("open", sendEditorCameraInput);
  ws.addEventListener("close", () => {
    if (editor.ws === ws) {
      editor.ws = null;
    }
  });
  ws.addEventListener("error", () => {
    if (editor.ws === ws) {
      editor.ws = null;
    }
  });
  return ws;
}

function editorCameraMoveVector() {
  const keys = state.editorCamera.activeKeys;
  return {
    moveX: (keys.has("KeyD") ? 1 : 0) + (keys.has("KeyA") ? -1 : 0),
    moveY: (keys.has("KeyW") ? 1 : 0) + (keys.has("KeyS") ? -1 : 0),
    moveZ: (keys.has("KeyE") ? 1 : 0) + (keys.has("KeyQ") ? -1 : 0),
  };
}

function sendEditorCameraInput(force = false) {
  const editor = state.editorCamera;
  if (!VIEWER_EDITOR_MODE || (!editor.captureActive && !force)) {
    return;
  }
  const ws = ensureEditorCameraSocket();
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    return;
  }
  const { moveX, moveY, moveZ } = editorCameraMoveVector();
  ws.send(JSON.stringify({
    move_x: Math.max(-1, Math.min(1, moveX)),
    move_y: Math.max(-1, Math.min(1, moveY)),
    move_z: Math.max(-1, Math.min(1, moveZ)),
    yaw_delta: editor.pendingYaw,
    pitch_delta: editor.pendingPitch,
    speed: editor.activeKeys.has("ShiftLeft") || editor.activeKeys.has("ShiftRight") ? 3200 : 1400,
  }));
  editor.pendingYaw = 0;
  editor.pendingPitch = 0;
}

function startEditorCameraInput() {
  if (state.editorCamera.timer) {
    sendEditorCameraInput();
    return;
  }
  state.editorCamera.timer = window.setInterval(sendEditorCameraInput, 16);
  sendEditorCameraInput();
}

function stopEditorCameraInput() {
  const editor = state.editorCamera;
  const wasActive = editor.captureActive;
  editor.captureActive = false;
  editor.pointerId = null;
  editor.activeKeys.clear();
  editor.pendingYaw = 0;
  editor.pendingPitch = 0;
  if (editor.timer) {
    window.clearInterval(editor.timer);
    editor.timer = null;
  }
  if (wasActive) {
    sendEditorCameraInput(true);
  }
  if (document.pointerLockElement === document.body) {
    document.exitPointerLock();
  }
}

function sendPreviewCameraInput({ yawDelta = 0, pitchDelta = 0, zoomDelta = 0, panYDelta = 0, panZDelta = 0 } = {}) {
  const session = state.playerSession;
  if (!session.sessionId || !canAdjustPreviewCamera()) {
    return;
  }
  const pending = session.pendingPreviewCamera || { yawDelta: 0, pitchDelta: 0, zoomDelta: 0, panYDelta: 0, panZDelta: 0 };
  pending.yawDelta += yawDelta;
  pending.pitchDelta += pitchDelta;
  pending.zoomDelta += zoomDelta;
  pending.panYDelta += panYDelta;
  pending.panZDelta += panZDelta;
  session.pendingPreviewCamera = pending;
  if (session.previewCameraInFlight) {
    return;
  }
  flushPreviewCameraInput();
}

function previewCameraKeyVector() {
  const keys = state.playerSession.activeKeys;
  return {
    yawDelta: (keys.has("KeyD") ? 1 : 0) + (keys.has("KeyA") ? -1 : 0),
    zoomDelta: (keys.has("KeyS") ? 1 : 0) + (keys.has("KeyW") ? -1 : 0),
  };
}

function hasPreviewCameraKeys() {
  return PREVIEW_CAMERA_KEYS.some((key) => state.playerSession.activeKeys.has(key));
}

function tickPreviewCameraKeys() {
  if (!canAdjustPreviewCamera() || !hasPreviewCameraKeys()) {
    return;
  }
  const { yawDelta, zoomDelta } = previewCameraKeyVector();
  sendPreviewCameraInput({
    yawDelta: yawDelta * 4.0,
    zoomDelta: zoomDelta * 24,
  });
}

function startPreviewCameraKeyTimer() {
  if (state.playerSession.previewCameraKeyTimer) {
    return;
  }
  state.playerSession.previewCameraKeyTimer = window.setInterval(tickPreviewCameraKeys, 33);
  tickPreviewCameraKeys();
}

function stopPreviewCameraKeyTimerIfIdle() {
  if (!state.playerSession.previewCameraKeyTimer || hasPreviewCameraKeys()) {
    return;
  }
  window.clearInterval(state.playerSession.previewCameraKeyTimer);
  state.playerSession.previewCameraKeyTimer = null;
}

function flushPreviewCameraInput() {
  const session = state.playerSession;
  const pending = session.pendingPreviewCamera;
  if (!session.sessionId || !pending || !canAdjustPreviewCamera()) {
    session.previewCameraInFlight = false;
    return;
  }
  session.pendingPreviewCamera = null;
  session.previewCameraInFlight = true;
  fetch(`/api/sessions/${encodeURIComponent(session.sessionId)}/preview-camera`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      engine: selectedEngineId(),
      input: {
        yaw_delta: pending.yawDelta,
        pitch_delta: pending.pitchDelta,
        zoom_delta: pending.zoomDelta,
        pan_y_delta: pending.panYDelta,
        pan_z_delta: pending.panZDelta,
      },
    }),
  }).then(async (response) => {
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(body.detail || response.statusText || "preview camera request failed");
    }
    if (body.skipped) {
      logPreviewCameraIssue(body.reason || "preview camera command skipped");
    } else {
      session.previewCameraLastError = "";
    }
  }).catch((err) => {
    logPreviewCameraIssue(err.message || String(err));
  }).finally(() => {
    session.previewCameraInFlight = false;
    if (session.pendingPreviewCamera) {
      flushPreviewCameraInput();
    }
  });
}

function logPreviewCameraIssue(message) {
  const session = state.playerSession;
  const now = Date.now();
  if (message === session.previewCameraLastError && now - session.previewCameraLastErrorAt < 2500) {
    return;
  }
  session.previewCameraLastError = message;
  session.previewCameraLastErrorAt = now;
  log("预览镜头命令未生效", message);
}

function startPlayerInputTimer() {
  const session = state.playerSession;
  if (session.inputTimer) {
    sendPlayerInput();
    return;
  }
  if (session.sessionId) {
    const ws = new WebSocket(playerInputWsUrl(session.sessionId));
    session.inputWs = ws;
    ws.addEventListener("open", () => sendPlayerInput());
    ws.addEventListener("close", () => {
      if (session.inputWs === ws) {
        session.inputWs = null;
      }
    });
    ws.addEventListener("error", () => {
      if (session.inputWs === ws) {
        session.inputWs = null;
      }
    });
  }
  state.playerSession.inputTimer = window.setInterval(sendPlayerInput, 33);
  sendPlayerInput();
}

function activatePlayerInput() {
  if (!isPlayerInWorld()) {
    return;
  }
  state.playerSession.pageInputEnabled = true;
  startPlayerInputTimer();
  updateViewportInputOverlay();
  focusControlSurface();
}

function stopPlayerInputTimer() {
  if (state.playerSession.inputTimer) {
    window.clearInterval(state.playerSession.inputTimer);
    state.playerSession.inputTimer = null;
  }
  if (state.playerSession.previewCameraKeyTimer) {
    window.clearInterval(state.playerSession.previewCameraKeyTimer);
    state.playerSession.previewCameraKeyTimer = null;
  }
  if (state.playerSession.inputWs) {
    try {
      state.playerSession.inputWs.close();
    } catch (err) {
      // Ignore already-closed sockets.
    }
    state.playerSession.inputWs = null;
  }
  state.playerSession.inputInFlight = false;
  state.playerSession.activeKeys.clear();
  state.playerSession.viewportDragging = false;
  state.playerSession.pageInputEnabled = false;
  updateViewportInputOverlay();
}

function isDeadPlayerSession(session) {
  const value = String(session?.state || session?.status || "").toLowerCase();
  return value === "client_crashed" || value === "error" || value === "destroyed";
}

function refreshRuntimeInputText() {
  const element = $("runtimeInputText");
  if (!element) {
    return;
  }
  if (!state.runtime.joined) {
    element.textContent = "-";
    return;
  }
  const { moveX, moveY } = runtimeMoveVector();
  const keys = [...state.runtime.activeKeys].map((key) => key.replace("Key", "")).join(" ");
  const host = $("runtimeUeHostInput")?.value.trim() || "-";
  const port = $("runtimeUePortInput")?.value || "-";
  element.textContent = `${state.runtime.captureActive ? "capturing" : "idle-focus"} move=(${moveX}, ${moveY}) yaw=${state.runtime.yaw.toFixed(1)} pitch=${state.runtime.pitch.toFixed(1)} ue=${host}:${port} keys=${keys || "-"}`;
}

function captureRuntimeInput() {
  state.runtime.captureActive = true;
  focusControlSurface();
  refreshRuntimeInputText();
  updateViewportInputOverlay();
}

function loadStoredRuntimeSession() {
  try {
    return JSON.parse(sessionStorage.getItem(RUNTIME_STORAGE_KEY) || "{}");
  } catch (err) {
    return {};
  }
}

function saveRuntimeSession() {
  sessionStorage.setItem(RUNTIME_STORAGE_KEY, JSON.stringify({
    worldId: state.runtime.worldId,
    participantId: state.runtime.participantId,
    entityId: state.runtime.entityId,
  }));
}

function clearStoredRuntimeSession() {
  sessionStorage.removeItem(RUNTIME_STORAGE_KEY);
}

function runtimeMoveVector() {
  const keys = state.runtime.activeKeys;
  const moveX = (keys.has("KeyD") ? 1 : 0) + (keys.has("KeyA") ? -1 : 0);
  const moveY = (keys.has("KeyW") ? 1 : 0) + (keys.has("KeyS") ? -1 : 0);
  return {
    moveX: Math.max(-1, Math.min(1, moveX)),
    moveY: Math.max(-1, Math.min(1, moveY)),
  };
}

function sendRuntimeInput() {
  const runtime = state.runtime;
  if (!runtime.joined || !runtime.ws || runtime.ws.readyState !== WebSocket.OPEN) {
    return;
  }
  const { moveX, moveY } = runtimeMoveVector();
  runtime.seq += 1;
  runtime.ws.send(JSON.stringify({
    type: "input_state",
    world_id: runtime.worldId,
    participant_id: runtime.participantId,
    controller_id: runtime.controllerId,
    entity_id: runtime.entityId,
    move_x: moveX,
    move_y: moveY,
    run: runtime.activeKeys.has("ShiftLeft") || runtime.activeKeys.has("ShiftRight"),
    jump: runtime.activeKeys.has("Space"),
    yaw: runtime.yaw,
    pitch: runtime.pitch,
    ue_input_host: $("runtimeUeHostInput")?.value.trim() || "",
    ue_input_port: Number($("runtimeUePortInput")?.value || 0),
    seq: runtime.seq,
    ts: Date.now() / 1000,
  }));
}

function startRuntimeTimers() {
  stopRuntimeTimers();
  state.runtime.inputTimer = setInterval(sendRuntimeInput, 50);
  state.runtime.heartbeatTimer = setInterval(() => {
    const runtime = state.runtime;
    if (runtime.joined && runtime.ws && runtime.ws.readyState === WebSocket.OPEN) {
      runtime.ws.send(JSON.stringify({ type: "heartbeat", controller_id: runtime.controllerId }));
    }
  }, 5000);
}

function stopRuntimeTimers() {
  if (state.runtime.inputTimer) {
    clearInterval(state.runtime.inputTimer);
    state.runtime.inputTimer = null;
  }
  if (state.runtime.heartbeatTimer) {
    clearInterval(state.runtime.heartbeatTimer);
    state.runtime.heartbeatTimer = null;
  }
}

function resetRuntimeSession() {
  stopRuntimeTimers();
  state.runtime.joined = false;
  state.runtime.captureActive = false;
  state.runtime.worldId = "";
  state.runtime.participantId = "";
  state.runtime.controllerId = "";
  state.runtime.entityId = "";
  state.runtime.activeKeys.clear();
  refreshRuntimeMeta();
  setRuntimeStatus(false, "Runtime: offline");
}

function handleRuntimeMessage(message) {
  if (message.type === "joined") {
    state.runtime.joined = true;
    state.runtime.worldId = message.world_id || "";
    state.runtime.participantId = message.participant_id || "";
    state.runtime.controllerId = message.controller_id || "";
    state.runtime.entityId = message.entity_id || "";
    refreshRuntimeMeta();
    saveRuntimeSession();
    setRuntimeStatus(true, "Runtime: joined");
    captureRuntimeInput();
    startRuntimeTimers();
    log("Runtime 已加入", message);
    if (message.ue_bridge && message.ue_bridge.queued) {
      log("UE Runtime Bridge 创建实体已入队", message.ue_bridge.status || {});
    }
    if (message.ue_bridge && message.ue_bridge.error) {
      log("UE Runtime Bridge 创建实体失败", message.ue_bridge.error);
    }
    return;
  }
  if (message.type === "input_ack" && message.ue_bridge && message.ue_bridge.error) {
    const now = Date.now();
    if (message.ue_bridge.error !== state.runtime.lastBridgeError || now - state.runtime.lastBridgeErrorAt > 3000) {
      state.runtime.lastBridgeError = message.ue_bridge.error;
      state.runtime.lastBridgeErrorAt = now;
      log("UE Runtime Bridge 输入失败", message.ue_bridge.error);
    }
    return;
  }
  if (message.type === "world_state") {
    log("Runtime WorldState", message);
    return;
  }
  if (message.type === "error" || message.ok === false) {
    log("Runtime 错误", message);
  }
}

function joinRuntime() {
  if (state.runtime.ws && state.runtime.ws.readyState === WebSocket.OPEN) {
    state.runtime.ws.close();
  }
  resetRuntimeSession();
  setRuntimeStatus(false, "Runtime: connecting");
  const ws = new WebSocket(runtimeWsUrl());
  state.runtime.ws = ws;
  ws.addEventListener("open", () => {
    const worldId = $("runtimeWorldInput").value.trim() || "world_001";
    const userId = $("runtimeUserInput").value.trim();
    const storedRuntime = loadStoredRuntimeSession();
    ws.send(JSON.stringify({
      type: "join",
      world_id: worldId,
      participant_id: state.playerSession.participantId || storedRuntime.participantId || "",
      user_id: userId,
      avatar_asset_path: selectedAvatarPath(),
      idle_animation_path: selectedIdleMotionPath(),
      move_animation_path: selectedMoveMotionPath(),
      controller_kind: "human",
      ue_input_host: $("runtimeUeHostInput")?.value.trim() || "",
      ue_input_port: Number($("runtimeUePortInput")?.value || 0),
    }));
  });
  ws.addEventListener("message", (event) => {
    try {
      handleRuntimeMessage(JSON.parse(event.data));
    } catch (err) {
      log("Runtime 消息解析失败", event.data);
    }
  });
  ws.addEventListener("close", () => {
    if (state.runtime.ws === ws) {
      resetRuntimeSession();
      log("Runtime WebSocket 已断开");
    }
  });
  ws.addEventListener("error", () => {
    if (state.runtime.ws === ws) {
      setRuntimeStatus(false, "Runtime: error");
    }
  });
}

async function createPlayerSession() {
  focusControlSurface();
  if (state.playerSession.sessionId) {
    const current = await api(sessionUrl(`/${encodeURIComponent(state.playerSession.sessionId)}`));
    applyPlayerSessionResult(current);
    if (!isDeadPlayerSession(current)) {
      log("玩家客户端已经启动", current);
      return;
    }
    resetPlayerSessionState();
  }
  const userId = $("runtimeUserInput").value.trim() || `user_${runtimeTabId()}`;
  const result = await api("/api/sessions", {
    method: "POST",
    body: JSON.stringify({
      engine: selectedEngineId(),
      user_id: userId,
      character: {},
    }),
  });
  applyPlayerSessionResult(result);
  focusPixelFrameSoon(500);
  log("玩家客户端已启动，UE Client 正在打开预览场景", result);
}

async function ensurePlayerSession() {
  if (state.playerSession.sessionId) {
    try {
      const session = await api(sessionUrl(`/${encodeURIComponent(state.playerSession.sessionId)}`));
      applyPlayerSessionResult(session);
      if (!isDeadPlayerSession(session)) {
        return state.playerSession.sessionId;
      }
      log("当前客户端已退出，正在创建新的玩家客户端", session);
      resetPlayerSessionState();
    } catch (err) {
      log("当前客户端状态不可用，正在创建新的玩家客户端", err.message);
      resetPlayerSessionState();
    }
  }
  if (await restorePlayerSessionFromStorage()) {
    return state.playerSession.sessionId;
  }
  await createPlayerSession();
  return state.playerSession.sessionId;
}

async function requirePlayerSession(actionLabel) {
  const sessionId = state.playerSession.sessionId;
  if (!sessionId) {
    log(`请先点击“启动客户端”，再${actionLabel}`);
    return "";
  }
  const session = await api(sessionUrl(`/${encodeURIComponent(sessionId)}`));
  applyPlayerSessionResult(session);
  if (isDeadPlayerSession(session)) {
    resetPlayerSessionState();
    throw new Error("当前客户端已经退出，请重新启动客户端");
  }
  return sessionId;
}

function selectedCharacterConfig() {
  return {
    avatar_id: selectedAvatarPath(),
    idle_animation: selectedIdleMotionPath(),
    move_animation: selectedMoveMotionPath(),
    scale: 1.0,
    rotation_yaw: optionalNumber("meshYawInput") || 0,
  };
}

async function setPlayerPreviewCharacter() {
  focusControlSurface();
  const avatar = selectedAvatarPath();
  if (!avatar) {
    log("请先选择 Avatar，再预览角色");
    return;
  }
  const sessionId = await requirePlayerSession("预览角色");
  if (!sessionId) {
    return;
  }
  const result = await api(`/api/sessions/${encodeURIComponent(sessionId)}/character`, {
    method: "POST",
    body: JSON.stringify({
      engine: selectedEngineId(),
      character: selectedCharacterConfig(),
    }),
  });
  applyPlayerSessionResult(result);
  if (isDeadPlayerSession(result)) {
    resetPlayerSessionState();
    throw new Error("当前客户端已退出，请重新启动客户端");
  }
  log("预览角色命令已发送", result);
  focusPixelFrameSoon(300);
}

async function playPlayerPreviewAnimation() {
  const animation = $("motionSelect").value || selectedMoveMotionPath() || selectedIdleMotionPath();
  if (!animation) {
    log("请先选择要预览的 Motion");
    return;
  }
  const sessionId = await requirePlayerSession("播放预览动作");
  if (!sessionId) {
    return;
  }
  const result = await api(`/api/sessions/${encodeURIComponent(sessionId)}/preview-animation`, {
    method: "POST",
    body: JSON.stringify({
      engine: selectedEngineId(),
      animation,
      loop: true,
      play_rate: 1.0,
    }),
  });
  applyPlayerSessionResult(result);
  log("预览动作命令已发送", result);
}

async function waitForPlayerWorldReady(sessionId, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const session = await api(sessionUrl(`/${encodeURIComponent(sessionId)}`));
    applyPlayerSessionResult(session);
    if (isPlayerInWorld()) {
      activatePlayerInput();
      log("UE 场景已加载，角色已生成并由当前客户端控制", {
        world: session.world_name || session.world_id || "",
        pawn: session.pawn_name || "",
      });
      return session;
    }
    if (isDeadPlayerSession(session)) {
      throw new Error(session.error || "UE 客户端在进入场景时退出");
    }
    await new Promise((resolve) => window.setTimeout(resolve, 500));
  }
  throw new Error("等待引擎场景和角色就绪超时；请查看 backend Runtime 日志");
}

async function joinPlayerWorld() {
  focusControlSurface();
  const sessionId = await requirePlayerSession("进入场景");
  if (!sessionId) {
    return;
  }
  if (!selectedAvatarPath()) {
    log("请先选择并预览 Avatar，再进入场景");
    return;
  }
  const session = await api(sessionUrl(`/${encodeURIComponent(sessionId)}`));
  applyPlayerSessionResult(session);
  if (session.state === "IN_WORLD" || session.status === "in_world") {
    log("当前客户端已经在多人世界中；如需换角色，请先离开世界或关闭客户端后重新启动", session);
    return;
  }
  if (session.state === "CONNECTING" || session.status === "connecting") {
    log("当前客户端正在进入多人世界，请稍等", session);
    return;
  }
  const previewedAvatar = normalizeAssetPath(state.playerSession.character?.avatar_id || "");
  const selectedAvatarRecord = selectedAvatar();
  const selectedAvatarReferences = new Set([
    normalizeAssetPath(selectedAvatarPath()),
    normalizeAssetPath(assetPath(selectedAvatarRecord)),
  ]);
  if (!selectedAvatarReferences.has(previewedAvatar)) {
    log("当前客户端还没有预览所选 Avatar，请先点击“预览当前角色”");
    return;
  }
  const packageId = $("worldSelect")?.value || state.playerSession.runtimePackageId || "";
  if (!packageId) {
    log("请先选择 Scene / World");
    return;
  }
  const selectedWorld = await api(`/api/sessions/${encodeURIComponent(sessionId)}/load-world`, {
    method: "POST",
    body: JSON.stringify({ engine: selectedEngineId(), package_id: packageId }),
  });
  applyPlayerSessionResult(selectedWorld);
  log("已选择发布 World", selectedWorld.package || selectedWorld);
  const result = await api(`/api/sessions/${encodeURIComponent(sessionId)}/join`, {
    method: "POST",
    body: JSON.stringify({ engine: selectedEngineId() }),
  });
  applyPlayerSessionResult(result);
  stopPlayerInputTimer();
  updateViewportInputOverlay();
  log("进入场景命令已发送，正在等待 UE 完成换图并生成角色", result);
  await waitForPlayerWorldReady(sessionId);
}

async function leavePlayerWorld() {
  const sessionId = state.playerSession.sessionId;
  if (!sessionId) {
    log("当前没有玩家客户端");
    return;
  }
  const result = await api(sessionUrl(`/${encodeURIComponent(sessionId)}/leave`), {
    method: "POST",
  });
  stopPlayerInputTimer();
  applyPlayerSessionResult(result);
  window.setTimeout(focusPixelFrame, 750);
  log("离开世界命令已发送，UE Client 将回到预览场景并恢复当前角色", result);
}

async function destroyPlayerSession() {
  const sessionId = state.playerSession.sessionId;
  if (!sessionId) {
    log("当前没有玩家客户端");
    return;
  }
  const result = await api(sessionUrl(`/${encodeURIComponent(sessionId)}`), {
    method: "DELETE",
  });
  stopPlayerInputTimer();
  state.playerSession = {
    userId: "",
    sessionId: "",
    participantId: "",
    state: "",
    pixelStreamingUrl: "",
    pixelHttpPort: 0,
    pixelStreamerPort: 0,
    pixelSfuPort: 0,
    inputPort: 0,
    inputHost: "",
    serverUrl: "",
    previewMap: "",
    projectId: "",
    worldId: "",
    runtimePackageId: "",
    worlds: [],
    signallingPid: 0,
    ueClientPid: 0,
    character: null,
    inputWs: null,
    inputTimer: null,
    inputInFlight: false,
    inputSeq: 0,
    yaw: 0,
    pitch: 0,
    activeKeys: new Set(),
    previewCameraDragging: false,
    previewCameraLastX: 0,
    previewCameraLastY: 0,
    previewCameraLastError: "",
    previewCameraLastErrorAt: 0,
    previewCameraKeyTimer: null,
    viewportDragging: false,
    viewportLastX: 0,
    viewportLastY: 0,
    previewCameraInFlight: false,
    pendingPreviewCamera: null,
    pageInputEnabled: false,
  };
  refreshPlayerSessionText();
  updateViewportInputOverlay();
  clearStoredPlayerSession();
  log("玩家客户端已关闭", result);
}

async function refreshPlayerSessions() {
  const result = await api(sessionUrl());
  log("玩家客户端列表", result);
}

async function loadPlayerWorld() {
  const packageId = $("worldSelect")?.value || "";
  if (!packageId) {
    log("请先选择已发布 World");
    return;
  }
  const sessionId = await requirePlayerSession("加载 World");
  if (!sessionId) {
    return;
  }
  const result = await api(`/api/sessions/${encodeURIComponent(sessionId)}/load-world`, {
    method: "POST",
    body: JSON.stringify({ engine: selectedEngineId(), package_id: packageId }),
  });
  applyPlayerSessionResult(result);
  log("Runtime Package 已加载到当前玩家会话", result);
}

function leaveRuntime() {
  const runtime = state.runtime;
  if (runtime.ws && runtime.ws.readyState === WebSocket.OPEN && runtime.controllerId) {
    runtime.ws.send(JSON.stringify({ type: "leave", controller_id: runtime.controllerId, participant_id: runtime.participantId }));
    runtime.ws.close();
  } else {
    resetRuntimeSession();
  }
}

async function showRuntimeSnapshot() {
  const worldId = $("runtimeWorldInput").value.trim() || state.runtime.worldId || "world_001";
  const snapshot = await api(`/api/debug/engines/unreal/runtime/world-state?world_id=${encodeURIComponent(worldId)}`);
  log("Runtime Snapshot", snapshot);
}

async function setActorAnimation(motionAssetPath, mode, looping = true) {
  if (!motionAssetPath) {
    return null;
  }
  if (!state.avatarPresented) {
    log("已记录动作选择，展示 Avatar 后再应用", { mode, motion_asset_path: motionAssetPath });
    return null;
  }
  state.currentLocomotion = mode;
  const result = await api("/api/debug/engines/unreal/scene/set-animation", {
    method: "POST",
    body: JSON.stringify({
      actor_label: actorLabel(),
      motion_asset_path: motionAssetPath,
      avatar_asset_path: selectedAvatarPath(),
      looping,
    }),
  });
  log(`${mode === "move" ? "移动" : "待机"}动作已设置`, result);
  return result;
}

async function ensureLocomotion(mode) {
  if (mode !== "move" && state.currentLocomotion === mode) {
    return;
  }
  const selectId = mode === "move" ? "moveMotionSelect" : "idleMotionSelect";
  const motion = mode === "move" ? selectedMoveMotionPath() : selectedIdleMotionPath();
  if (!motion) {
    log(mode === "move" ? "未选择 Move Motion，只执行位置移动" : "未选择 Idle Motion，停下后不切待机动作");
    return;
  }
  if (mode === "idle") {
    state.currentLocomotion = mode;
    const result = await api("/api/debug/engines/unreal/scene/play-motion", {
      method: "POST",
      body: JSON.stringify({
        motion_asset_path: motion,
        avatar_asset_path: selectedAvatarPath(),
      }),
    });
    log("待机动作已设置（Sequencer）", result);
    return;
  }
  await setActorAnimation(motion, mode, true);
}

function optionalNumber(id) {
  const value = $(id).value.trim();
  if (!value) {
    return null;
  }
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

async function previewRuntimeAvatar() {
  const avatar = selectedAvatarPath();
  if (!avatar) {
    log("请先选择 Avatar");
    return;
  }
  const result = await api("/api/debug/engines/unreal/scene/preview-runtime-avatar", {
    method: "POST",
    body: JSON.stringify({
      avatar_asset_path: avatar,
      idle_animation_path: selectedIdleMotionPath(),
      actor_label: runtimePreviewLabel(),
      mesh_forward_axis: $("meshForwardSelect").value || "auto",
      mesh_relative_yaw: optionalNumber("meshYawInput"),
    }),
  });
  log("Runtime Avatar 已预览。确认无误后点 Join 进入共享 Play World", result);
}

function setMultiplayerSlot(slotIndex) {
  const avatar = selectedAvatarPath();
  if (!avatar) {
    log("请先选择 Avatar，再设置多人 Player Slot");
    return;
  }
  const slot = {
    slot: slotIndex,
    participant_id: `p_debug_${slotIndex}`,
    avatar_asset_path: avatar,
    idle_animation_path: selectedIdleMotionPath(),
    move_animation_path: selectedMoveMotionPath(),
  };
  state.multiplayer.slots[slotIndex] = slot;
  saveMultiplayerSlots();
  refreshMultiplayerSlotText();
  log(`已设置 Player ${slotIndex} 多人资产`, slot);
}

async function writeMultiplayerDebugConfig() {
  const slots = state.multiplayer.slots
    .filter((slot) => slot && slot.avatar_asset_path)
    .map((slot, index) => ({
      slot: Number.isFinite(Number(slot.slot)) ? Number(slot.slot) : index,
      participant_id: slot.participant_id || `p_debug_${index}`,
      avatar_asset_path: slot.avatar_asset_path || "",
      idle_animation_path: slot.idle_animation_path || "",
      move_animation_path: slot.move_animation_path || "",
    }));
  if (slots.length === 0) {
    log("请至少设置一个 Player Slot");
    return;
  }
  const result = await api("/api/debug/engines/unreal/scene/multiplayer-debug-config", {
    method: "POST",
    body: JSON.stringify({ slots, replace: false }),
  });
  updateMultiplayerSlotsFromResult(result);
  log("UE 多人 Play 配置已写入。现在回 UE 点击 Play 测试 2 clients", result);
}

async function clearMultiplayerDebugConfig() {
  state.multiplayer.slots = [{ slot: 0 }, { slot: 1 }];
  saveMultiplayerSlots();
  refreshMultiplayerSlotText();
  const result = await api("/api/debug/engines/unreal/scene/multiplayer-debug-config", {
    method: "POST",
    body: JSON.stringify({ slots: [], replace: true }),
  });
  log("UE 多人 Play 配置已清空", result);
}

function updateMultiplayerSlotsFromResult(result) {
  const slots = Array.isArray(result?.slots) ? result.slots : [];
  for (const slot of slots) {
    const slotIndex = Number(slot.slot);
    if (slotIndex === 0 || slotIndex === 1) {
      state.multiplayer.slots[slotIndex] = {
        slot: slotIndex,
        participant_id: slot.participant_id || `p_debug_${slotIndex}`,
        avatar_asset_path: slot.avatar_asset_path || "",
        idle_animation_path: slot.idle_animation_path || "",
        move_animation_path: slot.move_animation_path || "",
      };
    }
  }
  saveMultiplayerSlots();
  refreshMultiplayerSlotText();
}

async function refreshMultiplayerDebugConfig() {
  const result = await api("/api/debug/engines/unreal/scene/multiplayer-debug-config");
  updateMultiplayerSlotsFromResult(result);
  log("已读取 UE 多人 Play 配置", result);
}

async function presentAvatar() {
  const avatar = selectedAvatarPath();
  if (!avatar) {
    log("请先选择 Avatar");
    return;
  }
  const result = await api("/api/debug/engines/unreal/scene/present-avatar", {
    method: "POST",
    body: JSON.stringify({ avatar_asset_path: avatar }),
  });
  log("Avatar 已调试展示（旧 Presentation Actor，不吃 WASD）", result);
  state.avatarPresented = true;
}

async function playMotion() {
  const motion = $("motionSelect").value || selectedMoveMotionPath() || selectedIdleMotionPath();
  if (!motion) {
    log("请先选择 Motion");
    return;
  }
  const result = await api("/api/debug/engines/unreal/scene/play-motion", {
    method: "POST",
    body: JSON.stringify({ motion_asset_path: motion, avatar_asset_path: selectedAvatarPath() }),
  });
  log("Motion 已播放", result);
}

async function clearScene() {
  let result = null;
  if (state.runtime.participantId || state.runtime.controllerId || state.runtime.entityId) {
    result = await api("/api/debug/engines/unreal/runtime/clear-entity", {
      method: "POST",
      body: JSON.stringify({
        participant_id: state.runtime.participantId,
        controller_id: state.runtime.controllerId,
        entity_id: state.runtime.entityId,
        destroy_actor: true,
      }),
    });
    clearStoredRuntimeSession();
    if (state.runtime.ws && state.runtime.ws.readyState === WebSocket.OPEN) {
      state.runtime.ws.close();
    }
    resetRuntimeSession();
  } else {
    const stored = loadStoredRuntimeSession();
    if (stored.participantId || stored.entityId) {
      result = await api("/api/debug/engines/unreal/runtime/clear-entity", {
        method: "POST",
        body: JSON.stringify({
          participant_id: stored.participantId || "",
          entity_id: stored.entityId || "",
          destroy_actor: true,
        }),
      });
      clearStoredRuntimeSession();
    } else {
      result = await api("/api/debug/engines/unreal/scene/clear", {
        method: "POST",
        body: JSON.stringify({ preview_actor_label: runtimePreviewLabel() }),
      });
    }
  }
  state.avatarPresented = false;
  state.currentLocomotion = "";
  state.activeMoveKeys.clear();
  if (state.idleTimer) {
    clearTimeout(state.idleTimer);
    state.idleTimer = null;
  }
  log("当前展示已清除", result);
}

async function move(direction) {
  const now = Date.now();
  if (now - state.lastControlAt < 120) {
    return;
  }
  state.lastControlAt = now;
  if (!["up", "down"].includes(direction)) {
    await ensureLocomotion("move").catch((err) => log("设置移动动作失败", err.message));
  }
  const offset = forwardOffsetYaw();
  const result = await api("/api/debug/engines/unreal/scene/move", {
    method: "POST",
    body: JSON.stringify({
      actor_label: actorLabel(),
      direction,
      step: state.config?.movement_step || 30,
      forward_offset_yaw: offset,
      face_direction: faceDirection(),
    }),
  });
  log(`移动: ${direction} (offset=${offset})`, result);
  if (!["up", "down"].includes(direction)) {
    if (state.idleTimer) {
      clearTimeout(state.idleTimer);
      state.idleTimer = null;
    }
    state.idleTimer = setTimeout(() => {
      state.activeMoveKeys.clear();
      ensureLocomotion("idle").catch((err) => log("设置待机动作失败", err.message));
      state.idleTimer = null;
    }, 700);
  }
}

async function rotate(yawDelta) {
  const now = Date.now();
  if (now - state.lastControlAt < 120) {
    return;
  }
  state.lastControlAt = now;
  const result = await api("/api/debug/engines/unreal/scene/rotate", {
    method: "POST",
    body: JSON.stringify({ actor_label: actorLabel(), yaw_delta: Number(yawDelta) }),
  });
  log(`旋转: ${yawDelta}`, result);
}

async function transform() {
  const result = await api(`/api/debug/engines/unreal/scene/transform?actor_label=${encodeURIComponent(actorLabel())}`);
  log("当前 Transform", result);
}

function bindEvents() {
  $("engineSelect").addEventListener("change", async () => {
    state.engine.id = $("engineSelect").value;
    const payload = await api(
      `/api/engines/${encodeURIComponent(state.engine.id)}/capabilities`
    );
    state.engine.capabilities = payload.capabilities || {};
    applyEngineCapabilities();
    await Promise.all([
      loadConfig(),
      refreshStatus(),
      refreshAssets(),
      refreshPlayerCatalog(),
    ]);
  });
  $("refreshAssetsBtn").addEventListener("click", () => Promise.all([
    refreshAssets(),
    refreshPlayerCatalog(),
  ]).catch((err) => log("刷新资产或 Scene 失败", err.message)));
  $("avatarSelect").addEventListener("change", () => {
    refreshMotionSelects();
    savePlayerSession();
  });
  $("worldSelect").addEventListener("change", () => {
    refreshWorldSummary();
    savePlayerSession();
  });
  $("idleMotionSelect").addEventListener("change", savePlayerSession);
  $("moveMotionSelect").addEventListener("change", savePlayerSession);
  $("motionSelect").addEventListener("change", savePlayerSession);
  $("showAllMotionsInput").addEventListener("change", refreshMotionSelects);
  $("runtimePreviewBtn").addEventListener("click", () => previewRuntimeAvatar().catch((err) => log("Runtime 预览失败", err.message)));
  $("runtimeJoinBtn").addEventListener("click", joinRuntime);
  $("runtimeLeaveBtn").addEventListener("click", leaveRuntime);
  $("runtimeCaptureBtn").addEventListener("click", captureRuntimeInput);
  $("runtimeSnapshotBtn").addEventListener("click", () => showRuntimeSnapshot().catch((err) => log("Runtime Snapshot 失败", err.message)));
  $("playerSessionCreateBtn").addEventListener("click", () => createPlayerSession().catch((err) => log("启动客户端失败", err.message)));
  $("playerLoadWorldBtn").addEventListener("click", () => loadPlayerWorld().catch((err) => log("加载 World 失败", err.message)));
  $("playerPreviewBtn").addEventListener("click", () => setPlayerPreviewCharacter().catch((err) => log("预览角色失败", err.message)));
  $("playerPreviewAnimationBtn").addEventListener("click", () => playPlayerPreviewAnimation().catch((err) => log("播放预览动作失败", err.message)));
  $("playerJoinWorldBtn").addEventListener("click", () => joinPlayerWorld().catch((err) => log("进入多人世界失败", err.message)));
  $("playerLeaveWorldBtn").addEventListener("click", () => leavePlayerWorld().catch((err) => log("离开世界失败", err.message)));
  $("playerDestroySessionBtn").addEventListener("click", () => destroyPlayerSession().catch((err) => log("关闭客户端失败", err.message)));
  $("playerSessionRefreshBtn").addEventListener("click", () => refreshPlayerSessions().catch((err) => log("读取会话列表失败", err.message)));
  $("mpSetSlot0Btn").addEventListener("click", () => setMultiplayerSlot(0));
  $("mpSetSlot1Btn").addEventListener("click", () => setMultiplayerSlot(1));
  $("mpWriteConfigBtn").addEventListener("click", () => writeMultiplayerDebugConfig().catch((err) => log("写入 UE 多人配置失败", err.message)));
  $("mpRefreshConfigBtn").addEventListener("click", () => refreshMultiplayerDebugConfig().catch((err) => log("读取 UE 多人配置失败", err.message)));
  $("mpClearConfigBtn").addEventListener("click", () => clearMultiplayerDebugConfig().catch((err) => log("清空 UE 多人配置失败", err.message)));
  $("presentAvatarBtn").addEventListener("click", () => presentAvatar().catch((err) => log("展示 Avatar 失败", err.message)));
  $("playMotionBtn").addEventListener("click", () => playMotion().catch((err) => log("播放 Motion 失败", err.message)));
  $("clearSceneBtn").addEventListener("click", () => clearScene().catch((err) => log("清除场景失败", err.message)));

  const viewportInputOverlay = $("viewportInputOverlay");
  if (viewportInputOverlay) {
    viewportInputOverlay.addEventListener("pointerdown", (event) => {
      if ((event.button !== 0 && event.button !== 2) || !canControlViewport()) {
        return;
      }
      focusControlSurface();
      if (VIEWER_EDITOR_MODE) {
        startEditorCameraBridge(true).catch((err) => {
          state.editorCamera.bridgeReady = false;
          log("Editor 相机输入桥重建失败", err.message);
        });
        state.editorCamera.captureActive = true;
        state.editorCamera.pointerId = event.pointerId;
        state.editorCamera.lastX = event.clientX;
        state.editorCamera.lastY = event.clientY;
        viewportInputOverlay.setPointerCapture(event.pointerId);
        document.body.requestPointerLock?.();
        startEditorCameraInput();
      } else if (isPlayerInWorld()) {
        state.playerSession.viewportDragging = true;
        state.playerSession.viewportLastX = event.clientX;
        state.playerSession.viewportLastY = event.clientY;
        state.playerSession.pageInputEnabled = true;
        startPlayerInputTimer();
        state.runtime.captureActive = true;
        viewportInputOverlay.setPointerCapture(event.pointerId);
        document.body.requestPointerLock?.();
        if (event.button === 0) {
          sendPlayerAction("context_primary");
        }
      } else {
        state.playerSession.previewCameraDragging = true;
        state.playerSession.previewCameraLastX = event.clientX;
        state.playerSession.previewCameraLastY = event.clientY;
        log("预览相机拖拽已捕获");
        viewportInputOverlay.setPointerCapture(event.pointerId);
      }
      updateViewportInputOverlay();
      refreshRuntimeInputText();
      event.preventDefault();
    });

    const moveViewportDrag = (event) => {
      if (!canControlViewport()) {
        return;
      }
      if (VIEWER_EDITOR_MODE) {
        if (!state.editorCamera.captureActive) {
          return;
        }
        const dx = document.pointerLockElement === document.body
          ? event.movementX
          : event.clientX - state.editorCamera.lastX;
        const dy = document.pointerLockElement === document.body
          ? event.movementY
          : event.clientY - state.editorCamera.lastY;
        state.editorCamera.lastX = event.clientX;
        state.editorCamera.lastY = event.clientY;
        state.editorCamera.pendingYaw += dx * 0.18;
        state.editorCamera.pendingPitch -= dy * 0.14;
        sendEditorCameraInput();
      } else if (isPlayerInWorld()) {
        if (!state.playerSession.viewportDragging) {
          return;
        }
        if (document.pointerLockElement === document.body) {
          return;
        }
        const dx = event.clientX - state.playerSession.viewportLastX;
        const dy = event.clientY - state.playerSession.viewportLastY;
        state.playerSession.viewportLastX = event.clientX;
        state.playerSession.viewportLastY = event.clientY;
        state.playerSession.yaw += dx * state.runtime.mouseSensitivity;
        state.playerSession.pitch = Math.max(-75, Math.min(75, state.playerSession.pitch - dy * state.runtime.mouseSensitivity));
        state.runtime.yaw = state.playerSession.yaw;
        state.runtime.pitch = state.playerSession.pitch;
        sendPlayerInput();
        refreshRuntimeInputText();
      } else {
        if (!state.playerSession.previewCameraDragging || !canAdjustPreviewCamera()) {
          return;
        }
        const dx = event.clientX - state.playerSession.previewCameraLastX;
        const dy = event.clientY - state.playerSession.previewCameraLastY;
        state.playerSession.previewCameraLastX = event.clientX;
        state.playerSession.previewCameraLastY = event.clientY;
        if (event.shiftKey) {
          sendPreviewCameraInput({ panYDelta: dx * 0.25, panZDelta: -dy * 0.25 });
        } else {
          sendPreviewCameraInput({ yawDelta: dx * 0.45, pitchDelta: -dy * 0.32 });
        }
      }
      event.preventDefault();
    };

    const stopViewportDrag = (event) => {
      if (VIEWER_EDITOR_MODE && state.editorCamera.captureActive) {
        state.editorCamera.pointerId = null;
        try {
          viewportInputOverlay.releasePointerCapture(event.pointerId);
        } catch (err) {
          // Pointer lock or the browser may already have released capture.
        }
        event.preventDefault();
        return;
      }
      if (!state.playerSession.previewCameraDragging && !state.playerSession.viewportDragging) {
        return;
      }
      state.playerSession.previewCameraDragging = false;
      if (
        !isPlayerInWorld()
        || document.pointerLockElement !== document.body
      ) {
        state.playerSession.viewportDragging = false;
      }
      try {
        viewportInputOverlay.releasePointerCapture(event.pointerId);
      } catch (err) {
        // The browser may already release pointer capture on iframe transitions.
      }
      updateViewportInputOverlay();
    };
    window.addEventListener("pointermove", moveViewportDrag, { capture: true });
    window.addEventListener("pointerup", stopViewportDrag, { capture: true });
    window.addEventListener("pointercancel", stopViewportDrag, { capture: true });
    viewportInputOverlay.addEventListener("contextmenu", (event) => {
      if (VIEWER_EDITOR_MODE) {
        event.preventDefault();
      }
    });
    viewportInputOverlay.addEventListener("wheel", (event) => {
      if (!canAdjustPreviewCamera()) {
        return;
      }
      sendPreviewCameraInput({ zoomDelta: Math.max(-160, Math.min(160, event.deltaY * 0.6)) });
      event.preventDefault();
    }, { passive: false });
  }

  window.addEventListener("keydown", (event) => {
    const targetTag = event.target?.tagName?.toLowerCase();
    if (targetTag === "input" || targetTag === "select" || targetTag === "textarea") {
      return;
    }
    if (VIEWER_EDITOR_MODE && state.editorCamera.captureActive && EDITOR_CAMERA_KEYS.includes(event.code)) {
      state.editorCamera.activeKeys.add(event.code);
      startEditorCameraInput();
      event.preventDefault();
    } else if (PLAYER_ACTIONS[event.code] && isPlayerInWorld()) {
      state.playerSession.pageInputEnabled = true;
      if (!event.repeat) {
        sendPlayerAction(PLAYER_ACTIONS[event.code]);
      }
      event.preventDefault();
    } else if (PLAYER_CONTROL_KEYS.includes(event.code) && isPlayerInWorld()) {
      state.playerSession.pageInputEnabled = true;
      state.playerSession.activeKeys.add(event.code);
      startPlayerInputTimer();
      sendPlayerInput();
      refreshRuntimeInputText();
      event.preventDefault();
    } else if (PREVIEW_CAMERA_KEYS.includes(event.code) && canAdjustPreviewCamera()) {
      state.runtime.captureActive = true;
      state.playerSession.activeKeys.add(event.code);
      startPreviewCameraKeyTimer();
      refreshRuntimeInputText();
      event.preventDefault();
    }
  });

  window.addEventListener("keyup", (event) => {
    if (VIEWER_EDITOR_MODE && EDITOR_CAMERA_KEYS.includes(event.code)) {
      state.editorCamera.activeKeys.delete(event.code);
      sendEditorCameraInput();
      event.preventDefault();
    } else if (PLAYER_ACTIONS[event.code] && isPlayerInWorld()) {
      event.preventDefault();
    } else if (PLAYER_CONTROL_KEYS.includes(event.code) && isPlayerInWorld()) {
      state.playerSession.activeKeys.delete(event.code);
      sendPlayerInput();
      refreshRuntimeInputText();
      event.preventDefault();
    } else if (PREVIEW_CAMERA_KEYS.includes(event.code) && !isPlayerInWorld()) {
      state.playerSession.activeKeys.delete(event.code);
      stopPreviewCameraKeyTimerIfIdle();
      refreshRuntimeInputText();
      event.preventDefault();
    }
  });

  window.addEventListener("mousemove", (event) => {
    if (
      isPlayerInWorld()
      && document.pointerLockElement === document.body
      && state.playerSession.pageInputEnabled
    ) {
      state.playerSession.yaw += event.movementX * state.runtime.mouseSensitivity;
      state.playerSession.pitch = Math.max(
        -75,
        Math.min(
          75,
          state.playerSession.pitch
            - event.movementY * state.runtime.mouseSensitivity
        )
      );
      state.runtime.yaw = state.playerSession.yaw;
      state.runtime.pitch = state.playerSession.pitch;
      sendPlayerInput();
      refreshRuntimeInputText();
      return;
    }
    if (
      isPlayerInWorld()
      || !state.runtime.captureActive
      || document.pointerLockElement !== document.body
      || state.playerSession.viewportDragging
    ) {
      return;
    }
    state.runtime.yaw += event.movementX * state.runtime.mouseSensitivity;
    state.runtime.pitch = Math.max(-75, Math.min(75, state.runtime.pitch - event.movementY * state.runtime.mouseSensitivity));
    state.playerSession.yaw += event.movementX * state.runtime.mouseSensitivity;
    state.playerSession.pitch = Math.max(-75, Math.min(75, state.playerSession.pitch - event.movementY * state.runtime.mouseSensitivity));
    sendPlayerInput();
    refreshRuntimeInputText();
  });

  window.addEventListener("mousedown", (event) => {
    if (
      event.button === 0
      && isPlayerInWorld()
      && document.pointerLockElement === document.body
    ) {
      sendPlayerAction("context_primary");
      event.preventDefault();
    }
  });

  document.addEventListener("pointerlockchange", () => {
    if (VIEWER_EDITOR_MODE && !document.pointerLockElement && state.editorCamera.captureActive) {
      stopEditorCameraInput();
    }
    const bodyHasPointerLock =
      document.pointerLockElement === document.body;
    state.runtime.captureActive = bodyHasPointerLock;
    if (isPlayerInWorld()) {
      state.playerSession.viewportDragging = bodyHasPointerLock;
      state.playerSession.pageInputEnabled =
        state.playerSession.pageInputEnabled || bodyHasPointerLock;
      if (bodyHasPointerLock) {
        startPlayerInputTimer();
      } else {
        sendPlayerInput();
      }
    }
    updateViewportInputOverlay();
    refreshRuntimeInputText();
  });

  window.addEventListener("blur", () => {
    stopEditorCameraInput();
    state.runtime.activeKeys.clear();
    state.playerSession.activeKeys.clear();
    state.runtime.captureActive = false;
    state.playerSession.viewportDragging = false;
    state.playerSession.previewCameraDragging = false;
    stopPreviewCameraKeyTimerIfIdle();
    sendPlayerInput();
    updateViewportInputOverlay();
    refreshRuntimeInputText();
  });

}

async function init() {
  if (VIEWER_EMBEDDED) {
    document.body.classList.add("embedded-viewer");
  }
  if (VIEWER_EDITOR_MODE) {
    document.body.classList.add("editor-viewer");
  }
  bindEvents();
  loadStoredMultiplayerSlots();
  refreshMultiplayerSlotText();
  refreshPlayerSessionText();
  const storedRuntime = loadStoredRuntimeSession();
  if (storedRuntime.participantId || storedRuntime.entityId) {
    state.runtime.worldId = storedRuntime.worldId || "";
    state.runtime.participantId = storedRuntime.participantId || "";
    state.runtime.entityId = storedRuntime.entityId || "";
    refreshRuntimeMeta();
    setRuntimeStatus(false, "Runtime: stored");
  }
  try {
    await loadEngines();
    await loadConfig();
    await refreshStatus();
    await refreshAssets();
    await refreshPlayerCatalog();
    if (VIEWER_EDITOR_MODE) {
      await startEditorCameraBridge().catch((err) => {
        state.editorCamera.bridgeReady = false;
        log("Editor 相机输入桥等待首次点击重试", err.message);
      });
    } else {
      await restorePlayerSessionFromStorage();
    }
    log("Viewer 已启动");
  } catch (err) {
    log("Viewer 初始化失败", err.message);
  }
}

init();
