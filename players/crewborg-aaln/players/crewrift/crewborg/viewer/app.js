"use strict";

const MAP_FPS = 24;

const overlayDefs = [
  ["rooms", "Rooms", true],
  ["tasks", "Tasks", true],
  ["vents", "Vents", true],
  ["occupancy", "Crew occupancy", true],
  ["teammates", "Teammate occupancy", false],
  ["route", "Route and target", true],
  ["players", "Player beliefs", true],
  ["bodies", "Bodies", true],
  ["camera", "Camera window", true],
  ["self", "Self", true],
];

const state = {
  map: null,
  grid: null,
  events: [],
  eventsByTick: new Map(),
  frames: [],
  frameIndex: 0,
  playing: false,
  speed: 1,
  overlays: Object.fromEntries(overlayDefs.map(([key, , enabled]) => [key, enabled])),
  view: { scale: 1, offsetX: 0, offsetY: 0, fitted: false },
  drag: null,
  lastPlaybackTime: 0,
};

const els = {
  file: document.getElementById("traceFile"),
  fileStatus: document.getElementById("fileStatus"),
  dropZone: document.getElementById("dropZone"),
  canvas: document.getElementById("mapCanvas"),
  emptyState: document.getElementById("emptyState"),
  overlayToggles: document.getElementById("overlayToggles"),
  timeline: document.getElementById("timeline"),
  playPause: document.getElementById("playPause"),
  speedSelect: document.getElementById("speedSelect"),
  frameReadout: document.getElementById("frameReadout"),
  tickValue: document.getElementById("tickValue"),
  phaseValue: document.getElementById("phaseValue"),
  roleValue: document.getElementById("roleValue"),
  intentValue: document.getElementById("intentValue"),
  modeName: document.getElementById("modeName"),
  modeReason: document.getElementById("modeReason"),
  modeDetailName: document.getElementById("modeDetailName"),
  modeSource: document.getElementById("modeSource"),
  modeAge: document.getElementById("modeAge"),
  modeParams: document.getElementById("modeParams"),
  navTarget: document.getElementById("navTarget"),
  navWaypoint: document.getElementById("navWaypoint"),
  navRoute: document.getElementById("navRoute"),
  occTopCell: document.getElementById("occTopCell"),
  occExpected: document.getElementById("occExpected"),
  occTracked: document.getElementById("occTracked"),
  eventList: document.getElementById("eventList"),
  rawFrame: document.getElementById("rawFrame"),
};

const ctx = els.canvas.getContext("2d");

function init() {
  buildOverlayToggles();
  bindControls();
  resizeCanvas();
  draw();
  requestAnimationFrame(playbackLoop);
}

function buildOverlayToggles() {
  els.overlayToggles.innerHTML = "";
  for (const [key, label, enabled] of overlayDefs) {
    const row = document.createElement("label");
    const text = document.createElement("span");
    const input = document.createElement("input");
    text.textContent = label;
    input.type = "checkbox";
    input.checked = enabled;
    input.addEventListener("change", () => {
      state.overlays[key] = input.checked;
      draw();
    });
    row.append(text, input);
    els.overlayToggles.append(row);
  }
}

function bindControls() {
  els.file.addEventListener("change", (event) => {
    const file = event.target.files && event.target.files[0];
    if (file) readFile(file);
  });

  for (const eventName of ["dragenter", "dragover"]) {
    els.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      els.dropZone.classList.add("dragging");
    });
  }
  for (const eventName of ["dragleave", "drop"]) {
    els.dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      els.dropZone.classList.remove("dragging");
    });
  }
  els.dropZone.addEventListener("drop", (event) => {
    const file = event.dataTransfer.files && event.dataTransfer.files[0];
    if (file) readFile(file);
  });

  document.getElementById("jumpStart").addEventListener("click", () => setFrame(0));
  document.getElementById("stepBack").addEventListener("click", () => setFrame(state.frameIndex - 1));
  document.getElementById("stepForward").addEventListener("click", () => setFrame(state.frameIndex + 1));
  document.getElementById("jumpEnd").addEventListener("click", () => setFrame(state.frames.length - 1));
  document.getElementById("fitMap").addEventListener("click", () => {
    fitView(true);
    draw();
  });
  document.getElementById("clearTrace").addEventListener("click", clearTrace);
  els.playPause.addEventListener("click", togglePlayback);
  els.speedSelect.addEventListener("change", () => {
    state.speed = Number(els.speedSelect.value);
  });
  els.timeline.addEventListener("input", () => setFrame(Number(els.timeline.value)));

  els.canvas.addEventListener("wheel", onWheel, { passive: false });
  els.canvas.addEventListener("pointerdown", onPointerDown);
  window.addEventListener("pointermove", onPointerMove);
  window.addEventListener("pointerup", onPointerUp);
  window.addEventListener("resize", () => {
    resizeCanvas();
    if (state.view.fitted) fitView(false);
    draw();
  });
}

function readFile(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      loadTrace(String(reader.result || ""), file.name);
    } catch (error) {
      console.error(error);
      els.fileStatus.textContent = `Could not parse ${file.name}: ${error.message}`;
    }
  };
  reader.readAsText(file);
}

function loadTrace(text, fileName) {
  const parsed = parseTrace(text);
  state.map = parsed.map;
  state.grid = parsed.grid;
  state.events = parsed.events;
  state.eventsByTick = parsed.eventsByTick;
  state.frames = parsed.frames;
  state.frameIndex = 0;
  state.playing = false;
  state.view.fitted = false;
  els.playPause.textContent = ">";
  els.emptyState.style.display = state.frames.length ? "none" : "grid";
  els.timeline.max = String(Math.max(0, state.frames.length - 1));
  els.timeline.value = "0";
  els.fileStatus.textContent = `${fileName}: ${state.frames.length.toLocaleString()} frames, ${state.events.length.toLocaleString()} trace events`;
  if (!parsed.hasViewerFrames && state.frames.length) {
    els.fileStatus.textContent += " (sparse fallback, no viewer frames)";
  }
  fitView(true);
  updateInspector();
  draw();
}

function parseTrace(text) {
  const events = [];
  for (const [lineIndex, line] of text.split(/\r?\n/).entries()) {
    const trimmed = line.trim();
    if (!trimmed || trimmed[0] !== "{") continue;
    let row;
    try {
      row = JSON.parse(trimmed);
    } catch {
      continue;
    }
    const name = row.event || row.name;
    if (!name || (row.kind && row.kind !== "trace")) continue;
    const tick = Number.isFinite(row.tick) ? row.tick : null;
    events.push({
      tick,
      name,
      data: row.data || {},
      line: lineIndex + 1,
    });
  }

  const eventsByTick = new Map();
  for (const event of events) {
    if (event.tick === null) continue;
    if (!eventsByTick.has(event.tick)) eventsByTick.set(event.tick, []);
    eventsByTick.get(event.tick).push(event);
  }

  const mapEvent = events.find((event) => event.name === "domain.viewer_map");
  const gridEvent = events.find((event) => event.name === "domain.viewer_occupancy_grid");
  const viewerFrames = events
    .filter((event) => event.name === "domain.viewer_frame")
    .map((event) => ({ tick: event.tick, data: event.data, sourceLine: event.line }));

  let frames = viewerFrames;
  if (!frames.length) frames = sparseFrames(eventsByTick);

  return {
    events,
    eventsByTick,
    frames,
    map: mapEvent ? mapEvent.data : inferMap(frames),
    grid: gridEvent ? gridEvent.data : null,
    hasViewerFrames: viewerFrames.length > 0,
  };
}

function sparseFrames(eventsByTick) {
  return [...eventsByTick.keys()]
    .sort((a, b) => a - b)
    .map((tick) => {
      const tickEvents = eventsByTick.get(tick) || [];
      const action = tickEvents.find((event) => event.name === "action_intent");
      return {
        tick,
        sourceLine: tickEvents[0] ? tickEvents[0].line : null,
        data: {
          schema_version: 0,
          tick,
          phase: "-",
          role: "-",
          mode: { name: action && action.data ? action.data.mode : "-", params: {}, reason: "" },
          intent: { kind: action && action.data ? action.data.intent || "-" : "-" },
          nav: {},
          occupancy: null,
          players: [],
          bodies: [],
          tasks: {},
        },
      };
    });
}

function inferMap(frames) {
  const points = [];
  for (const frame of frames) {
    const data = frame.data || {};
    if (data.self) points.push([data.self.x, data.self.y]);
    for (const point of [data.nav && data.nav.target, data.nav && data.nav.route_goal]) {
      if (Array.isArray(point)) points.push(point);
    }
    for (const player of data.players || []) points.push([player.x, player.y]);
    for (const body of data.bodies || []) points.push([body.x, body.y]);
  }
  if (!points.length) return null;
  const maxX = Math.max(...points.map((point) => point[0]), 128);
  const maxY = Math.max(...points.map((point) => point[1]), 128);
  return { schema_version: 0, width: maxX + 80, height: maxY + 80, rooms: [], tasks: [], vents: [] };
}

function clearTrace() {
  state.map = null;
  state.grid = null;
  state.events = [];
  state.eventsByTick = new Map();
  state.frames = [];
  state.frameIndex = 0;
  state.playing = false;
  els.file.value = "";
  els.fileStatus.textContent = "Drop a trace log or choose one.";
  els.emptyState.style.display = "grid";
  els.timeline.max = "0";
  els.timeline.value = "0";
  els.playPause.textContent = ">";
  updateInspector();
  draw();
}

function togglePlayback() {
  if (!state.frames.length) return;
  state.playing = !state.playing;
  els.playPause.textContent = state.playing ? "||" : ">";
  state.lastPlaybackTime = performance.now();
}

function playbackLoop(now) {
  if (state.playing && state.frames.length > 1) {
    const delay = 1000 / (MAP_FPS * state.speed);
    if (now - state.lastPlaybackTime >= delay) {
      const next = state.frameIndex + Math.max(1, Math.floor((now - state.lastPlaybackTime) / delay));
      setFrame(next >= state.frames.length ? 0 : next);
      state.lastPlaybackTime = now;
    }
  }
  requestAnimationFrame(playbackLoop);
}

function setFrame(index) {
  if (!state.frames.length) {
    state.frameIndex = 0;
    updateInspector();
    draw();
    return;
  }
  state.frameIndex = clamp(index, 0, state.frames.length - 1);
  els.timeline.value = String(state.frameIndex);
  updateInspector();
  draw();
}

function currentFrame() {
  return state.frames[state.frameIndex] || null;
}

function updateInspector() {
  const frame = currentFrame();
  const data = frame ? frame.data || {} : {};
  const mode = data.mode || {};
  const nav = data.nav || {};
  const occ = data.occupancy || {};
  const tickEvents = frame && frame.tick !== null ? state.eventsByTick.get(frame.tick) || [] : [];
  const visibleEvents = tickEvents.filter((event) => !event.name.startsWith("domain.viewer_"));

  els.frameReadout.textContent = state.frames.length
    ? `${state.frameIndex + 1} / ${state.frames.length}`
    : "0 / 0";
  els.tickValue.textContent = valueText(data.tick);
  els.phaseValue.textContent = valueText(data.phase);
  els.roleValue.textContent = valueText(data.role);
  els.intentValue.textContent = valueText(data.intent && (data.intent.kind || data.intent));
  els.modeName.textContent = mode.name ? `${mode.name}` : "No trace loaded";
  els.modeReason.textContent = mode.reason || "No mode reason recorded.";
  els.modeDetailName.textContent = valueText(mode.name);
  els.modeSource.textContent = valueText(mode.source);
  els.modeAge.textContent = mode.age_ticks === undefined ? "-" : `${mode.age_ticks} ticks`;
  els.modeParams.textContent = pretty(mode.params || {});
  els.navTarget.textContent = pointText(nav.target || nav.route_goal);
  els.navWaypoint.textContent = pointText(nav.next_waypoint);
  els.navRoute.textContent = nav.route ? `${nav.route.length} points, cursor ${nav.route_cursor || 0}` : "-";
  els.occTopCell.textContent = valueText(occ && occ.top_cell);
  els.occExpected.textContent = occ && occ.top_expected !== undefined ? String(occ.top_expected) : "-";
  els.occTracked.textContent = occ ? `${occ.tracked || 0} agents, ${occ.support_cells || 0} cells` : "-";
  els.rawFrame.textContent = pretty(data);

  els.eventList.innerHTML = "";
  if (!visibleEvents.length) {
    const empty = document.createElement("div");
    empty.className = "subtle";
    empty.textContent = "No non-viewer trace events on this tick.";
    els.eventList.append(empty);
  } else {
    for (const event of visibleEvents.slice(0, 12)) {
      const item = document.createElement("div");
      item.className = "event-item";
      const name = document.createElement("div");
      name.className = "event-name";
      name.textContent = event.name.replace(/^domain\./, "");
      const body = document.createElement("div");
      body.className = "event-data";
      body.textContent = compact(event.data);
      item.append(name, body);
      els.eventList.append(item);
    }
  }
}

function resizeCanvas() {
  const rect = els.canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  els.canvas.width = Math.max(1, Math.round(rect.width * dpr));
  els.canvas.height = Math.max(1, Math.round(rect.height * dpr));
}

function fitView(markFitted) {
  const bounds = mapBounds();
  if (!bounds) return;
  const rect = els.canvas.getBoundingClientRect();
  const pad = 34;
  const scale = Math.min((rect.width - pad * 2) / bounds.width, (rect.height - pad * 2) / bounds.height);
  state.view.scale = Math.max(0.1, scale);
  state.view.offsetX = (rect.width - bounds.width * state.view.scale) / 2 - bounds.x * state.view.scale;
  state.view.offsetY = (rect.height - bounds.height * state.view.scale) / 2 - bounds.y * state.view.scale;
  state.view.fitted = markFitted;
}

function mapBounds() {
  if (state.map) {
    return { x: 0, y: 0, width: state.map.width || 128, height: state.map.height || 128 };
  }
  const frame = currentFrame();
  if (!frame) return null;
  const inferred = inferMap([frame]);
  return inferred ? { x: 0, y: 0, width: inferred.width, height: inferred.height } : null;
}

function draw() {
  resizeCanvas();
  const dpr = window.devicePixelRatio || 1;
  const width = els.canvas.width / dpr;
  const height = els.canvas.height / dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#131210";
  ctx.fillRect(0, 0, width, height);

  const frame = currentFrame();
  if (!frame) return;
  drawMapBase();
  if (state.overlays.rooms) drawRooms();
  if (state.overlays.occupancy) drawOccupancy(false);
  if (state.overlays.teammates) drawOccupancy(true);
  if (state.overlays.tasks) drawTasks();
  if (state.overlays.vents) drawVents();
  if (state.overlays.bodies) drawBodies(frame.data);
  if (state.overlays.players) drawPlayers(frame.data);
  if (state.overlays.route) drawRoute(frame.data);
  if (state.overlays.camera) drawCamera(frame.data);
  if (state.overlays.self) drawSelf(frame.data);
}

function worldPath() {
  ctx.setTransform(
    (window.devicePixelRatio || 1) * state.view.scale,
    0,
    0,
    (window.devicePixelRatio || 1) * state.view.scale,
    (window.devicePixelRatio || 1) * state.view.offsetX,
    (window.devicePixelRatio || 1) * state.view.offsetY,
  );
}

function screenPath() {
  const dpr = window.devicePixelRatio || 1;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function worldToScreen(point) {
  return {
    x: point[0] * state.view.scale + state.view.offsetX,
    y: point[1] * state.view.scale + state.view.offsetY,
  };
}

function drawMapBase() {
  const bounds = mapBounds();
  if (!bounds) return;
  worldPath();
  ctx.fillStyle = "#191814";
  ctx.fillRect(0, 0, bounds.width, bounds.height);
  ctx.strokeStyle = "#4b453c";
  ctx.lineWidth = 3 / state.view.scale;
  ctx.strokeRect(0, 0, bounds.width, bounds.height);
  screenPath();
}

function drawRooms() {
  const rooms = (state.map && state.map.rooms) || [];
  worldPath();
  for (const room of rooms) {
    ctx.fillStyle = "rgba(70, 67, 58, 0.52)";
    ctx.strokeStyle = "#5d574d";
    ctx.lineWidth = 1.5 / state.view.scale;
    ctx.fillRect(room.x, room.y, room.w, room.h);
    ctx.strokeRect(room.x, room.y, room.w, room.h);
    drawWorldLabel(room.name, room.x + 8, room.y + 16, "#c9c0ae", "left");
  }
  screenPath();
}

function drawTasks() {
  const tasks = (state.map && state.map.tasks) || [];
  const frame = currentFrame();
  const taskState = (frame && frame.data && frame.data.tasks) || {};
  const completed = new Set(taskState.completed || []);
  const visible = new Set(taskState.visible || []);
  worldPath();
  for (const task of tasks) {
    const done = completed.has(task.index);
    const seen = visible.has(task.index);
    ctx.fillStyle = done ? "rgba(132, 204, 22, 0.55)" : seen ? "rgba(250, 204, 21, 0.90)" : "rgba(250, 204, 21, 0.42)";
    ctx.strokeStyle = done ? "#84cc16" : "#facc15";
    ctx.lineWidth = 1.25 / state.view.scale;
    roundRect(task.x, task.y, task.w, task.h, 3 / state.view.scale);
    ctx.fill();
    ctx.stroke();
  }
  screenPath();
}

function drawVents() {
  const vents = (state.map && state.map.vents) || [];
  worldPath();
  for (const vent of vents) {
    const cx = vent.x + vent.w / 2;
    const cy = vent.y + vent.h / 2;
    ctx.fillStyle = "rgba(56, 189, 248, 0.24)";
    ctx.strokeStyle = "#38bdf8";
    ctx.lineWidth = 1.4 / state.view.scale;
    ctx.beginPath();
    ctx.moveTo(cx, vent.y);
    ctx.lineTo(vent.x + vent.w, cy);
    ctx.lineTo(cx, vent.y + vent.h);
    ctx.lineTo(vent.x, cy);
    ctx.closePath();
    ctx.fill();
    ctx.stroke();
  }
  screenPath();
}

function drawOccupancy(teammates) {
  const frame = currentFrame();
  const occ = frame && frame.data && frame.data.occupancy;
  if (!state.grid || !occ) return;
  const values = teammates ? occ.teammate_cells || [] : occ.cells || [];
  if (!values.length) return;
  const byId = new Map(state.grid.cells.map((cell) => [cell.index, cell]));
  const maxValue = Math.max(...values.map(([, value]) => value), 0.0001);
  worldPath();
  for (const [cellId, value] of values) {
    const cell = byId.get(cellId);
    if (!cell) continue;
    const alpha = clamp(value / maxValue, 0.08, 0.78);
    const x = cell.center[0] - state.grid.cell_size / 2;
    const y = cell.center[1] - state.grid.cell_size / 2;
    ctx.fillStyle = teammates
      ? `rgba(45, 212, 191, ${alpha * 0.55})`
      : heatColor(alpha);
    ctx.fillRect(x, y, state.grid.cell_size, state.grid.cell_size);
  }
  screenPath();
}

function drawRoute(data) {
  const nav = data.nav || {};
  const route = nav.route || [];
  worldPath();
  if (route.length > 1) {
    ctx.strokeStyle = "#2dd4bf";
    ctx.lineWidth = 3 / state.view.scale;
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(route[0][0], route[0][1]);
    for (const point of route.slice(1)) ctx.lineTo(point[0], point[1]);
    ctx.stroke();
  }
  const target = nav.target || nav.route_goal;
  if (target) {
    ctx.strokeStyle = "#f59e0b";
    ctx.fillStyle = "rgba(245, 158, 11, 0.18)";
    ctx.lineWidth = 2 / state.view.scale;
    ctx.beginPath();
    ctx.arc(target[0], target[1], 12 / state.view.scale, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    crosshair(target[0], target[1], 14 / state.view.scale);
  }
  if (nav.next_waypoint) {
    ctx.fillStyle = "#2dd4bf";
    ctx.beginPath();
    ctx.arc(nav.next_waypoint[0], nav.next_waypoint[1], 4 / state.view.scale, 0, Math.PI * 2);
    ctx.fill();
  }
  screenPath();
}

function drawPlayers(data) {
  const players = data.players || [];
  worldPath();
  for (const player of players) {
    if (player.life_status === "dead") continue;
    const radius = player.believed_imposter || player.confirmed_imposter ? 7 : 5;
    ctx.fillStyle = player.teammate ? "#2dd4bf" : player.confirmed_imposter ? "#ef4444" : player.believed_imposter ? "#f97316" : "#f4f1e8";
    ctx.strokeStyle = "#111111";
    ctx.lineWidth = 2 / state.view.scale;
    ctx.beginPath();
    ctx.arc(player.x, player.y, radius / state.view.scale, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    if (player.suspicion !== null && player.suspicion !== undefined) {
      drawWorldLabel(`${player.color} ${Math.round(player.suspicion * 100)}%`, player.x + 8, player.y - 7, "#f4f1e8", "left");
    } else {
      drawWorldLabel(player.color, player.x + 8, player.y - 7, "#f4f1e8", "left");
    }
  }
  screenPath();
}

function drawBodies(data) {
  const bodies = data.bodies || [];
  worldPath();
  for (const body of bodies) {
    ctx.fillStyle = body.visible ? "#fb7185" : "rgba(251, 113, 133, 0.48)";
    ctx.strokeStyle = "#111111";
    ctx.lineWidth = 2 / state.view.scale;
    ctx.beginPath();
    ctx.rect(body.x - 5 / state.view.scale, body.y - 3 / state.view.scale, 10 / state.view.scale, 6 / state.view.scale);
    ctx.fill();
    ctx.stroke();
    drawWorldLabel(`body ${body.color}`, body.x + 8, body.y + 9, "#ffd0d8", "left");
  }
  screenPath();
}

function drawCamera(data) {
  const camera = data.camera || {};
  if (!camera.ready) return;
  worldPath();
  ctx.strokeStyle = "rgba(244, 241, 232, 0.55)";
  ctx.setLineDash([8 / state.view.scale, 6 / state.view.scale]);
  ctx.lineWidth = 1.5 / state.view.scale;
  ctx.strokeRect(camera.x, camera.y, camera.width || 128, camera.height || 128);
  ctx.setLineDash([]);
  screenPath();
}

function drawSelf(data) {
  if (!data.self) return;
  worldPath();
  const x = data.self.x;
  const y = data.self.y;
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#2dd4bf";
  ctx.lineWidth = 2 / state.view.scale;
  ctx.beginPath();
  ctx.arc(x, y, 7 / state.view.scale, 0, Math.PI * 2);
  ctx.fill();
  ctx.stroke();
  crosshair(x, y, 11 / state.view.scale);
  drawWorldLabel("self", x + 10, y - 10, "#ffffff", "left");
  screenPath();
}

function drawWorldLabel(text, x, y, color, align) {
  const point = worldToScreen([x, y]);
  screenPath();
  ctx.font = "12px Inter, ui-sans-serif, system-ui, sans-serif";
  ctx.textAlign = align || "center";
  ctx.textBaseline = "middle";
  const metrics = ctx.measureText(text);
  const width = metrics.width + 8;
  const height = 18;
  const left = align === "left" ? point.x - 4 : point.x - width / 2;
  ctx.fillStyle = "rgba(18, 18, 18, 0.72)";
  roundedScreenRect(left, point.y - height / 2, width, height, 4);
  ctx.fill();
  ctx.fillStyle = color;
  ctx.fillText(text, align === "left" ? point.x : point.x, point.y);
  worldPath();
}

function crosshair(x, y, size) {
  ctx.beginPath();
  ctx.moveTo(x - size, y);
  ctx.lineTo(x + size, y);
  ctx.moveTo(x, y - size);
  ctx.lineTo(x, y + size);
  ctx.stroke();
}

function roundRect(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function roundedScreenRect(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function onWheel(event) {
  event.preventDefault();
  const rect = els.canvas.getBoundingClientRect();
  const mouse = { x: event.clientX - rect.left, y: event.clientY - rect.top };
  const before = screenToWorld(mouse);
  const factor = event.deltaY < 0 ? 1.12 : 0.88;
  state.view.scale = clamp(state.view.scale * factor, 0.08, 8);
  state.view.offsetX = mouse.x - before.x * state.view.scale;
  state.view.offsetY = mouse.y - before.y * state.view.scale;
  state.view.fitted = false;
  draw();
}

function onPointerDown(event) {
  els.canvas.setPointerCapture(event.pointerId);
  els.canvas.classList.add("panning");
  state.drag = {
    id: event.pointerId,
    x: event.clientX,
    y: event.clientY,
    offsetX: state.view.offsetX,
    offsetY: state.view.offsetY,
  };
}

function onPointerMove(event) {
  if (!state.drag || state.drag.id !== event.pointerId) return;
  state.view.offsetX = state.drag.offsetX + event.clientX - state.drag.x;
  state.view.offsetY = state.drag.offsetY + event.clientY - state.drag.y;
  state.view.fitted = false;
  draw();
}

function onPointerUp(event) {
  if (!state.drag || state.drag.id !== event.pointerId) return;
  state.drag = null;
  els.canvas.classList.remove("panning");
}

function screenToWorld(point) {
  return {
    x: (point.x - state.view.offsetX) / state.view.scale,
    y: (point.y - state.view.offsetY) / state.view.scale,
  };
}

function heatColor(alpha) {
  if (alpha > 0.55) return `rgba(239, 68, 68, ${alpha})`;
  if (alpha > 0.28) return `rgba(249, 115, 22, ${alpha})`;
  return `rgba(250, 204, 21, ${alpha})`;
}

function pretty(value) {
  return JSON.stringify(value || {}, null, 2);
}

function compact(value) {
  const text = JSON.stringify(value || {});
  return text.length > 180 ? `${text.slice(0, 177)}...` : text;
}

function valueText(value) {
  return value === undefined || value === null || value === "" ? "-" : String(value);
}

function pointText(point) {
  if (!Array.isArray(point)) return "-";
  return `${point[0]}, ${point[1]}`;
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

init();
