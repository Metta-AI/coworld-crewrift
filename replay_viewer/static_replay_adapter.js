(function () {
  "use strict";

  function replayArtifactUrl(href) {
    const url = new URL(String(href), "https://replay.invalid/");
    if (url.hash.startsWith("#replay=")) {
      const raw = url.hash.slice("#replay=".length);
      if (raw) {
        try {
          return decodeURIComponent(raw);
        } catch (ignored) {
          return raw;
        }
      }
    }
    return url.searchParams.get("replay") ||
      url.searchParams.get("replay_url") ||
      url.searchParams.get("uri");
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { replayArtifactUrl };
    return;
  }

  // Readiness protocol for the embedding Observatory page (STATIC_REPLAY_VIEWERS.md).
  function tellHost(message) {
    if (window.parent === window) return;
    window.parent.postMessage({ src: "coworld-replay", ...message }, "*");
  }
  tellHost({ type: "loading" });

  const canvas = document.getElementById("c");
  const statusNode = document.getElementById("status");
  const debugPanel = document.getElementById("debugPanel");
  const debugDown = document.getElementById("debugDown");
  const debugUp = document.getElementById("debugUp");
  const debugSprites = document.getElementById("debugSprites");
  const scriptUrl = document.currentScript && document.currentScript.src;
  const workerUrl = new URL("./static_replay_worker.js", scriptUrl || location.href);
  let worker = null;
  let failed = false;
  let disposed = false;
  let loaded = false;
  let clockInFlight = false;
  let clockFrame = 0;
  let inputFrame = 0;
  let resizeFrame = 0;
  let resizeObserver = null;
  let pendingReplayBytes = null;
  let pendingPointerMove = null;
  let pendingWheel = null;
  let fileInput = null;
  let debugOpen = false;

  function status(text) {
    if (!statusNode) return;
    statusNode.textContent = text;
    statusNode.classList.toggle("hidden", !text);
  }

  function showFailure(error) {
    if (failed || disposed) return;
    failed = true;
    console.error(error);
    status("Replay failed: " + (error.message || String(error)));
    tellHost({ type: "error", message: error.message || String(error) });
    stopWorker();
  }

  function formatBytes(value) {
    const units = ["B", "KB", "MB", "GB"];
    let amount = Number(value) || 0;
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
      amount /= 1024;
      unit++;
    }
    return (unit === 0 ? String(amount) : amount.toFixed(1)) + " " + units[unit];
  }

  function renderDebug(snapshot) {
    if (!debugDown || !debugUp || !debugSprites) return;
    debugDown.textContent = formatBytes(snapshot.bytesDown);
    debugUp.textContent = formatBytes(snapshot.bytesUp);
    debugSprites.textContent = "";
    for (const sprite of snapshot.sprites || []) {
      const row = document.createElement("div");
      const label = document.createElement("div");
      const preview = document.createElement("canvas");
      const meta = document.createElement("div");
      const text = document.createElement("div");
      const size = document.createElement("div");
      const traffic = document.createElement("div");
      row.className = "debugSprite";
      row.classList.toggle("updated", !!sprite.updated);
      label.className = "debugSpriteId";
      meta.className = "debugSpriteMeta";
      text.className = "debugSpriteText";
      size.className = "debugSpriteSize";
      traffic.className = "debugSpriteTraffic";
      label.textContent = "#" + sprite.id;
      text.textContent = sprite.label || "(no label)";
      size.textContent = sprite.width + "x" + sprite.height;
      traffic.textContent = formatBytes(sprite.bytesDown) + " / " + sprite.updates + "x";
      preview.width = 64;
      preview.height = 64;
      const previewContext = preview.getContext("2d");
      previewContext.drawImage(sprite.bitmap, 0, 0);
      sprite.bitmap.close();
      label.append(size, traffic);
      meta.append(text);
      row.append(label, preview, meta);
      debugSprites.append(row);
    }
  }

  function setReplayState(message) {
    if (message.tick >= 0) document.documentElement.dataset.replayTick = String(message.tick);
    if (message.maxTick >= 0) {
      document.documentElement.dataset.replayMaxTick = String(message.maxTick);
    }
    document.documentElement.dataset.replayPlaying = String(!!message.playing);
  }

  function onWorkerMessage(event) {
    if (failed || disposed) return;
    const message = event.data || {};
    try {
      if (message.type === "ready") {
        document.documentElement.dataset.replayWorker = "true";
        document.documentElement.dataset.replayWorkerId = message.workerId;
        tellHost({ type: "phase", phase: "bundle_ready" });
      } else if (message.type === "phase") {
        const phase = { type: "phase", phase: message.phase };
        if ("bytes" in message) {
          phase.bytes = message.bytes;
          phase.compressed = message.compressed;
        }
        tellHost(phase);
      } else if (message.type === "status") {
        status(message.text || "");
      } else if (message.type === "loaded") {
        loaded = true;
        document.documentElement.dataset.replayLoaded = "true";
        setReplayState(message);
        status("");
        if (fileInput) {
          fileInput.remove();
          fileInput = null;
        }
        if (!clockFrame) clockFrame = requestAnimationFrame(clock);
        requestAnimationFrame(() => tellHost({ type: "ready" }));
      } else if (message.type === "clock") {
        clockInFlight = false;
        setReplayState(message);
      } else if (message.type === "state") {
        setReplayState(message);
      } else if (message.type === "resized") {
        document.documentElement.dataset.replayViewport =
          Math.round(message.width) + "x" + Math.round(message.height);
      } else if (message.type === "debug") {
        if (debugOpen) renderDebug(message.snapshot || {});
        else {
          for (const sprite of (message.snapshot && message.snapshot.sprites) || []) {
            sprite.bitmap.close();
          }
        }
      } else if (message.type === "error") {
        showFailure(new Error(message.message || "Replay Worker failed"));
      }
    } catch (error) {
      showFailure(error);
    }
  }

  function clock(now) {
    clockFrame = 0;
    if (disposed || failed || !loaded || !worker) return;
    if (!clockInFlight) {
      clockInFlight = true;
      worker.postMessage({ type: "clock", now });
    }
    clockFrame = requestAnimationFrame(clock);
  }

  function viewport() {
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(1, innerWidth || rect.width || 1);
    const height = Math.max(1, innerHeight || rect.height || 1);
    canvas.style.width = width + "px";
    canvas.style.height = height + "px";
    return { width, height, dpr: window.devicePixelRatio || 1 };
  }

  function sendResize() {
    resizeFrame = 0;
    if (!worker || disposed || failed) return;
    worker.postMessage({ type: "resize", ...viewport() });
  }

  function scheduleResize() {
    if (!resizeFrame) resizeFrame = requestAnimationFrame(sendResize);
  }

  function replayBuffer(value) {
    if (value instanceof ArrayBuffer) return value;
    if (ArrayBuffer.isView(value)) {
      return value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength);
    }
    return null;
  }

  function sendReplay(value) {
    const bytes = replayBuffer(value);
    if (!bytes) return false;
    if (worker) worker.postMessage({ type: "replay", bytes }, [bytes]);
    else pendingReplayBytes = bytes;
    return true;
  }

  function showFilePicker() {
    if (fileInput) return;
    status("Choose a Crewrift .bitreplay file");
    fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.accept = ".bitreplay,application/octet-stream";
    fileInput.style.cssText =
      "position:fixed;left:50%;top:58%;transform:translate(-50%,-50%);" +
      "z-index:20;color:white";
    fileInput.addEventListener("change", async () => {
      if (fileInput.files && fileInput.files[0]) {
        sendReplay(await fileInput.files[0].arrayBuffer());
      }
    });
    document.body.appendChild(fileInput);
  }

  function flushInput() {
    inputFrame = 0;
    if (!worker || failed || disposed) return;
    if (pendingWheel) {
      worker.postMessage({ type: "input", action: "wheel", ...pendingWheel });
      pendingWheel = null;
    }
    if (pendingPointerMove) {
      worker.postMessage({ type: "input", action: "pointermove", ...pendingPointerMove });
      pendingPointerMove = null;
    }
  }

  function scheduleInput() {
    if (!inputFrame) inputFrame = requestAnimationFrame(flushInput);
  }

  function pointerPosition(event) {
    return {
      x: Math.max(-32768, Math.min(32767, Number(event.clientX) || 0)),
      y: Math.max(-32768, Math.min(32767, Number(event.clientY) || 0))
    };
  }

  function sendInput(action, event) {
    if (!worker || failed || disposed) return;
    worker.postMessage({ type: "input", action, ...pointerPosition(event) });
  }

  function installInputHandlers() {
    addEventListener("wheel", event => {
      event.preventDefault();
      const point = pointerPosition(event);
      if (pendingWheel) {
        pendingWheel.x = point.x;
        pendingWheel.y = point.y;
        pendingWheel.deltaY += Number(event.deltaY) || 0;
      } else {
        pendingWheel = { ...point, deltaY: Number(event.deltaY) || 0 };
      }
      pendingWheel.deltaY = Math.max(-1200, Math.min(1200, pendingWheel.deltaY));
      scheduleInput();
    }, { passive: false });

    addEventListener("pointerdown", event => {
      flushInput();
      sendInput("pointerdown", event);
      try { canvas.setPointerCapture(event.pointerId); } catch (ignored) {}
    });
    addEventListener("pointermove", event => {
      pendingPointerMove = pointerPosition(event);
      scheduleInput();
    });
    addEventListener("pointerup", event => {
      flushInput();
      sendInput("pointerup", event);
      try { canvas.releasePointerCapture(event.pointerId); } catch (ignored) {}
    });
    addEventListener("pointercancel", event => {
      flushInput();
      sendInput("pointercancel", event);
    });
    addEventListener("dblclick", event => sendInput("dblclick", event));

    for (const eventName of ["pointerdown", "pointermove", "pointerup", "dblclick"]) {
      debugPanel.addEventListener(eventName, event => event.stopPropagation());
    }
    debugPanel.addEventListener("wheel", event => event.stopPropagation(), { passive: true });
    addEventListener("keydown", event => {
      if (event.key !== "F2") return;
      event.preventDefault();
      debugOpen = !debugOpen;
      debugPanel.classList.toggle("hidden", !debugOpen);
      if (worker) worker.postMessage({ type: "debug", enabled: debugOpen });
    });
  }

  function startWorker() {
    if (!canvas || typeof canvas.transferControlToOffscreen !== "function") {
      showFailure(new Error("This browser does not support OffscreenCanvas Workers"));
      return;
    }
    if (typeof Worker !== "function") {
      showFailure(new Error("This browser does not support Dedicated Workers"));
      return;
    }
    const replayUrl = replayArtifactUrl(location.href);
    const size = viewport();
    try {
      worker = new Worker(workerUrl, { name: "crewrift-static-replay" });
      worker.onmessage = onWorkerMessage;
      worker.onerror = event => {
        showFailure(new Error(event.message || "Replay Worker crashed"));
      };
      worker.onmessageerror = () => {
        showFailure(new Error("Replay Worker sent an unreadable message"));
      };
      const offscreen = canvas.transferControlToOffscreen();
      const init = {
        type: "init",
        replayUrl,
        replayBytes: pendingReplayBytes,
        canvas: offscreen,
        ...size
      };
      const transfers = [offscreen];
      if (pendingReplayBytes) transfers.push(pendingReplayBytes);
      pendingReplayBytes = null;
      worker.postMessage(init, transfers);
      if (!replayUrl && !init.replayBytes) showFilePicker();
    } catch (error) {
      showFailure(error);
    }
  }

  function stopWorker() {
    if (!worker) return;
    const stoppedWorker = worker;
    worker = null;
    try { stoppedWorker.postMessage({ type: "dispose" }); } catch (ignored) {}
    setTimeout(() => stoppedWorker.terminate(), 100);
  }

  function dispose() {
    if (disposed) return;
    disposed = true;
    if (clockFrame) cancelAnimationFrame(clockFrame);
    if (inputFrame) cancelAnimationFrame(inputFrame);
    if (resizeFrame) cancelAnimationFrame(resizeFrame);
    if (resizeObserver) resizeObserver.disconnect();
    stopWorker();
  }

  addEventListener("message", event => {
    const message = event.data;
    if (message && message.type === "coworld-replay" && message.bytes) {
      if (!sendReplay(message.bytes)) status("Unable to read replay bytes");
    }
  });
  addEventListener("dragover", event => event.preventDefault());
  addEventListener("drop", event => {
    event.preventDefault();
    const file = event.dataTransfer && event.dataTransfer.files[0];
    if (file) file.arrayBuffer().then(sendReplay).catch(showFailure);
  });
  addEventListener("resize", scheduleResize);
  addEventListener("pagehide", dispose, { once: true });

  installInputHandlers();
  if (typeof ResizeObserver === "function") {
    resizeObserver = new ResizeObserver(scheduleResize);
    resizeObserver.observe(document.documentElement);
  }
  queueMicrotask(startWorker);
})();
