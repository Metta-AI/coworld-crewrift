"use strict";

// SnappyJS publishes through `window`; a classic Dedicated Worker supplies the
// same global alias without loading any replay/runtime code on the iframe Window.
self.window = self;
importScripts(
  "./snappyjs.min.js",
  "./static_replay_renderer.js",
  "./crewrift_core.js"
);

const ReplayFps = 24;
const FrameMs = 1000 / ReplayFps;
const MaxCatchUpMs = 250;
const MaxCatchUpFrames = 4;
const workerId = typeof crypto.randomUUID === "function"
  ? crypto.randomUUID()
  : Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);

let core = null;
let renderer = null;
let initialized = false;
let loading = false;
let loaded = false;
let failed = false;
let disposed = false;
let lastClockAt = 0;
let clockAccumulator = 0;
let pendingReplayBytes = null;

function coreError() {
  if (!core) return "";
  const pointer = core._cr_error_ptr();
  return pointer ? core.UTF8ToString(pointer) : "";
}

function reportFailure(error) {
  if (failed || disposed) return;
  failed = true;
  postMessage({
    type: "error",
    message: error && error.message ? error.message : String(error),
    workerId
  });
}

function postState(type) {
  postMessage({
    type,
    workerId,
    tick: core ? core._cr_tick() : -1,
    maxTick: core ? core._cr_max_tick() : -1,
    playing: !!(core && core._cr_playing())
  });
}

function copyIntoCore(bytes, callback) {
  const packet = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const pointer = core._malloc(packet.length || 1);
  try {
    core.HEAPU8.set(packet, pointer);
    return callback(pointer, packet.length);
  } finally {
    core._free(pointer);
  }
}

function emitFrame() {
  const length = core._cr_frame_len();
  if (length <= 0) return;
  const pointer = core._cr_frame_ptr();
  // The renderer parses synchronously in this Worker, so it can consume the
  // WASM heap view without allocating/copying a packet between threads.
  renderer.ingest(core.HEAPU8.subarray(pointer, pointer + length));
  core._cr_frame_clear();
}

function passToCore(bytes) {
  if (!loaded || failed || disposed) return;
  copyIntoCore(bytes, (pointer, length) => core._cr_input(pointer, length));
  emitFrame();
  const error = coreError();
  if (error) throw new Error(error);
  postState("state");
}

async function loadReplayBytes(bytes) {
  if (loading || loaded || failed || disposed) return;
  loading = true;
  postMessage({ type: "status", text: "loading replay...", workerId });
  try {
    const replay = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    if (!replay.length) throw new Error("Replay response was empty");
    const accepted = copyIntoCore(
      replay,
      (pointer, length) => core._cr_load_replay(pointer, length)
    );
    if (!accepted) throw new Error(coreError() || "Unknown Crewrift replay error");
    loaded = true;
    emitFrame();
    postState("loaded");
  } catch (error) {
    loading = false;
    throw error;
  }
}

async function loadReplayUrl(replayUrl) {
  const response = await fetch(replayUrl, { credentials: "omit", mode: "cors" });
  if (!response.ok) throw new Error("Replay fetch failed (HTTP " + response.status + ")");
  await loadReplayBytes(await response.arrayBuffer());
}

async function initialize(message) {
  if (initialized) throw new Error("Replay Worker was initialized more than once");
  initialized = true;
  if (!(message.canvas instanceof OffscreenCanvas)) {
    throw new Error("Replay Worker did not receive an OffscreenCanvas");
  }
  renderer = self.CrewriftReplayRenderer.create({
    canvas: message.canvas,
    width: message.width,
    height: message.height,
    dpr: message.dpr,
    onPacket: passToCore,
    onDebug: (snapshot, transfers) => {
      postMessage({ type: "debug", snapshot, workerId }, transfers);
    },
    onError: reportFailure
  });

  postMessage({ type: "status", text: "starting Crewrift replay core...", workerId });
  core = await createCrewriftCore({
    locateFile: name => new URL(name, self.location.href).toString(),
    onAbort: what => reportFailure(new Error("Replay runtime aborted: " + what))
  });
  if (failed || disposed) return;
  postMessage({ type: "ready", workerId });

  if (pendingReplayBytes || message.replayBytes) {
    const bytes = pendingReplayBytes || message.replayBytes;
    pendingReplayBytes = null;
    await loadReplayBytes(bytes);
  } else if (message.replayUrl) {
    await loadReplayUrl(message.replayUrl);
  } else {
    postMessage({ type: "status", text: "Choose a Crewrift .bitreplay file", workerId });
  }
}

function runClock(now) {
  if (!loaded || failed || disposed) {
    postState("clock");
    return;
  }
  const clockNow = Number(now);
  const safeNow = Number.isFinite(clockNow) ? clockNow : performance.now();
  if (!lastClockAt) lastClockAt = safeNow;
  const elapsed = Math.max(0, Math.min(MaxCatchUpMs, safeNow - lastClockAt));
  lastClockAt = safeNow;

  if (core._cr_playing()) {
    clockAccumulator = Math.min(MaxCatchUpMs, clockAccumulator + elapsed);
    const frames = Math.min(MaxCatchUpFrames, Math.floor(clockAccumulator / FrameMs));
    for (let index = 0; index < frames; index++) core._cr_advance();
    clockAccumulator -= frames * FrameMs;
    if (frames > 0) emitFrame();
  } else {
    clockAccumulator = 0;
  }
  const error = coreError();
  if (error) throw new Error(error);
  postState("clock");
}

function dispose() {
  if (disposed) return;
  disposed = true;
  if (renderer) renderer.dispose();
  postMessage({ type: "disposed", workerId });
  close();
}

self.onmessage = event => {
  const message = event.data || {};
  if (disposed) return;
  try {
    if (message.type === "init") {
      initialize(message).catch(reportFailure);
    } else if (message.type === "clock") {
      runClock(message.now);
    } else if (message.type === "resize" && renderer) {
      renderer.resize(message.width, message.height, message.dpr);
      postMessage({
        type: "resized",
        width: Math.max(1, Number(message.width) || 1),
        height: Math.max(1, Number(message.height) || 1),
        workerId
      });
    } else if (message.type === "input" && renderer) {
      renderer.handleInput(message);
    } else if (message.type === "debug" && renderer) {
      renderer.setDebug(message.enabled);
    } else if (message.type === "replay" && message.bytes) {
      if (core) loadReplayBytes(message.bytes).catch(reportFailure);
      else pendingReplayBytes = message.bytes;
    } else if (message.type === "dispose") {
      dispose();
    } else {
      throw new Error("Unknown replay Worker message " + message.type);
    }
  } catch (error) {
    reportFailure(error);
  }
};
