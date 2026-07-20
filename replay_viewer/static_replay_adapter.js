(function () {
  "use strict";

  const ReplayFps = 24;
  let socket = null;
  let core = null;
  let lastFrameAt = 0;
  let loading = false;
  let pendingReplayBytes = null;

  function status(text) {
    const node = document.getElementById("status");
    if (!node) return;
    node.textContent = text;
    node.classList.toggle("hidden", !text);
  }

  function coreError() {
    const pointer = core._cr_error_ptr();
    return pointer ? core.UTF8ToString(pointer) : "Unknown Crewrift replay error";
  }

  function emitFrame() {
    if (!core || !socket || socket.readyState !== StaticReplaySocket.OPEN) return;
    const length = core._cr_frame_len();
    if (length <= 0) return;
    const pointer = core._cr_frame_ptr();
    const copy = core.HEAPU8.slice(pointer, pointer + length);
    document.documentElement.dataset.replayTick = String(core._cr_tick());
    document.documentElement.dataset.replayMaxTick = String(core._cr_max_tick());
    if (socket.onmessage) socket.onmessage({ data: copy.buffer });
  }

  function passToCore(bytes) {
    const packet = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
    const pointer = core._malloc(packet.length || 1);
    try {
      core.HEAPU8.set(packet, pointer);
      core._cr_input(pointer, packet.length);
    } finally {
      core._free(pointer);
    }
    emitFrame();
  }

  function animationFrame(now) {
    if (core && core._cr_playing()) {
      const frameMs = 1000 / ReplayFps;
      if (!lastFrameAt) lastFrameAt = now;
      let frames = Math.min(4, Math.floor((now - lastFrameAt) / frameMs));
      const shouldEmit = frames > 0;
      while (frames-- > 0) {
        core._cr_advance();
        lastFrameAt += frameMs;
      }
      if (shouldEmit) emitFrame();
      const error = coreError();
      if (error) status(error);
    } else {
      lastFrameAt = now;
    }
    requestAnimationFrame(animationFrame);
  }

  async function loadReplayBytes(bytes) {
    if (loading) return;
    loading = true;
    status("loading replay...");
    try {
      const replay = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
      const pointer = core._malloc(replay.length || 1);
      let loaded;
      try {
        core.HEAPU8.set(replay, pointer);
        loaded = core._cr_load_replay(pointer, replay.length);
      } finally {
        core._free(pointer);
      }
      if (!loaded) throw new Error(coreError());
      socket.readyState = StaticReplaySocket.OPEN;
      document.documentElement.dataset.replayLoaded = "true";
      if (socket.onopen) socket.onopen();
      status("");
      emitFrame();
    } catch (error) {
      loading = false;
      status("Unable to load replay: " + error.message);
      throw error;
    }
  }

  function showFilePicker() {
    status("Choose a Crewrift .bitreplay file");
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".bitreplay,application/octet-stream";
    input.style.cssText = "position:fixed;left:50%;top:58%;transform:translate(-50%,-50%);z-index:20;color:white";
    input.addEventListener("change", async () => {
      if (input.files && input.files[0]) {
        await loadReplayBytes(await input.files[0].arrayBuffer());
        input.remove();
      }
    });
    document.body.appendChild(input);
  }

  async function boot() {
    if (core || loading) return;
    status("starting Crewrift replay core...");
    core = await createCrewriftCore({
      locateFile: name => new URL(name, document.baseURI).toString()
    });
    const params = new URL(location.href).searchParams;
    const replayUrl = params.get("replay") || params.get("replay_url") || params.get("uri");
    if (pendingReplayBytes) {
      const bytes = pendingReplayBytes;
      pendingReplayBytes = null;
      await loadReplayBytes(bytes);
    } else if (replayUrl) {
      const response = await fetch(replayUrl);
      if (!response.ok) throw new Error("Replay fetch failed (HTTP " + response.status + ")");
      await loadReplayBytes(await response.arrayBuffer());
    } else {
      showFilePicker();
    }
    requestAnimationFrame(animationFrame);
  }

  class StaticReplaySocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;

    constructor() {
      this.binaryType = "arraybuffer";
      this.readyState = StaticReplaySocket.CONNECTING;
      socket = this;
      queueMicrotask(() => boot().catch(error => status(error.message)));
    }

    send(bytes) {
      if (core && this.readyState === StaticReplaySocket.OPEN) passToCore(bytes);
    }

    close() {
      this.readyState = StaticReplaySocket.CLOSED;
      if (this.onclose) this.onclose();
    }
  }

  for (const name of ["CONNECTING", "OPEN", "CLOSING", "CLOSED"]) {
    StaticReplaySocket.prototype[name] = StaticReplaySocket[name];
  }
  window.WebSocket = StaticReplaySocket;

  addEventListener("message", event => {
    const message = event.data;
    if (message && message.type === "coworld-replay" && message.bytes) {
      if (core) {
        loadReplayBytes(message.bytes).catch(error => status(error.message));
      } else {
        pendingReplayBytes = message.bytes;
      }
    }
  });
  addEventListener("dragover", event => event.preventDefault());
  addEventListener("drop", event => {
    event.preventDefault();
    const file = event.dataTransfer && event.dataTransfer.files[0];
    if (file) file.arrayBuffer().then(loadReplayBytes).catch(error => status(error.message));
  });
})();
