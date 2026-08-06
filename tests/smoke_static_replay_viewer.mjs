const playwrightModule = process.env.PLAYWRIGHT_MODULE || "playwright";
const { chromium } = await import(playwrightModule);

const viewerUrl = process.argv[2];
if (!viewerUrl) {
  throw new Error("Usage: node tests/smoke_static_replay_viewer.mjs <viewer-url>");
}

const requestedUrl = new URL(viewerUrl);
const replayUrl = requestedUrl.searchParams.get("replay") ||
  requestedUrl.searchParams.get("replay_url") ||
  requestedUrl.searchParams.get("uri");
if (!replayUrl) throw new Error("The smoke URL must include a replay parameter");

const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const browserErrors = [];
  context.on("page", page => {
    page.on("console", message => {
      if (message.type() === "error") browserErrors.push(message.text());
    });
    page.on("pageerror", error => browserErrors.push(error.message));
  });

  const page = await context.newPage();
  await page.goto(viewerUrl, { waitUntil: "domcontentloaded" });
  try {
    await page.locator("html[data-replay-loaded=true][data-replay-worker=true]").waitFor({
      state: "attached",
      timeout: 30_000
    });
  } catch (error) {
    const state = await page.evaluate(() => ({
      status: document.getElementById("status")?.textContent,
      loaded: document.documentElement.dataset.replayLoaded,
      worker: document.documentElement.dataset.replayWorker,
      workerId: document.documentElement.dataset.replayWorkerId,
      tick: document.documentElement.dataset.replayTick
    }));
    throw new Error(
      `Replay load timed out: ${JSON.stringify(state)}; ` +
      `browser errors: ${browserErrors.join(" | ")}`,
      { cause: error }
    );
  }

  const canvas = page.locator("#c");
  const workerId = await page.locator("html").getAttribute("data-replay-worker-id");
  if (!workerId) throw new Error("Worker-loaded marker did not include an instance id");
  const firstTick = Number(await page.locator("html").getAttribute("data-replay-tick"));
  const firstFrame = await canvas.screenshot();
  await page.waitForFunction(
    tick => Number(document.documentElement.dataset.replayTick) > tick,
    firstTick,
    { timeout: 10_000 }
  );
  let secondFrame = await canvas.screenshot();
  for (let attempt = 0; attempt < 5 && firstFrame.equals(secondFrame); attempt++) {
    await page.waitForTimeout(250);
    secondFrame = await canvas.screenshot();
  }
  if (firstFrame.equals(secondFrame)) throw new Error("Visible replay canvas did not change");

  const initialBox = await canvas.boundingBox();
  if (!initialBox || initialBox.width <= 1 || initialBox.height <= 1) {
    throw new Error("Transferred replay canvas was not visibly sized");
  }

  // Replay transport is authored in the bottom-left Sprite v1 UI layer. At
  // 1280x800 the shared renderer selects 3x UI zoom, so layer point (11, 2)
  // maps to this visible pause/resume button coordinate.
  const transportX = initialBox.x + 33;
  const transportY = initialBox.y + initialBox.height - 48;
  await page.mouse.click(transportX, transportY);
  await page.waitForFunction(
    () => document.documentElement.dataset.replayPlaying === "false",
    null,
    { timeout: 5_000 }
  );
  const pausedTick = Number(await page.locator("html").getAttribute("data-replay-tick"));
  await page.waitForTimeout(500);
  const stillPausedTick = Number(await page.locator("html").getAttribute("data-replay-tick"));
  if (stillPausedTick !== pausedTick) throw new Error("Replay advanced while paused");

  await page.mouse.click(transportX, transportY);
  await page.waitForFunction(
    tick => document.documentElement.dataset.replayPlaying === "true" &&
      Number(document.documentElement.dataset.replayTick) > tick,
    pausedTick,
    { timeout: 5_000 }
  );

  await page.setViewportSize({ width: 900, height: 600 });
  await page.waitForFunction(
    () => document.documentElement.dataset.replayViewport === "900x600",
    null,
    { timeout: 5_000 }
  );
  const resizedBox = await canvas.boundingBox();
  if (!resizedBox || Math.round(resizedBox.width) !== 900 || Math.round(resizedBox.height) !== 600) {
    throw new Error("Transferred replay canvas did not resize with the iframe");
  }

  await page.keyboard.press("F2");
  await page.locator("#debugPanel:not(.hidden) .debugSprite").first().waitFor({
    state: "attached",
    timeout: 5_000
  });
  await page.keyboard.press("F2");

  const result = await page.evaluate(() => {
    const status = document.getElementById("status");
    const canvas = document.getElementById("c");
    let mainThreadCanvasContext = false;
    try { mainThreadCanvasContext = !!canvas.getContext("2d"); } catch (ignored) {}
    return {
      statusHidden: status.classList.contains("hidden"),
      worker: document.documentElement.dataset.replayWorker,
      workerId: document.documentElement.dataset.replayWorkerId,
      tick: Number(document.documentElement.dataset.replayTick),
      maxTick: Number(document.documentElement.dataset.replayMaxTick),
      playing: document.documentElement.dataset.replayPlaying,
      viewport: document.documentElement.dataset.replayViewport,
      mainThreadCanvasContext,
      windowCore: typeof window.createCrewriftCore,
      windowRenderer: typeof window.CrewriftReplayRenderer
    };
  });

  if (!result.statusHidden) throw new Error("Viewer status did not clear");
  if (result.maxTick <= result.tick) throw new Error("Replay max tick is invalid");
  if (result.mainThreadCanvasContext) throw new Error("Canvas remained owned by the Window");
  if (result.windowCore !== "undefined" || result.windowRenderer !== "undefined") {
    throw new Error("Replay runtime or renderer leaked onto the Window");
  }

  // Preserve the existing black-box byte-postMessage loading contract in the
  // same emitted bundle, without introducing a wrapper API.
  const bytePage = await context.newPage();
  const bundleUrl = new URL(viewerUrl);
  bundleUrl.search = "";
  await bytePage.goto(bundleUrl.toString(), { waitUntil: "domcontentloaded" });
  await bytePage.locator("html[data-replay-worker=true]").waitFor({
    state: "attached",
    timeout: 30_000
  });
  await bytePage.evaluate(async replay => {
    const response = await fetch(replay);
    const bytes = await response.arrayBuffer();
    window.postMessage({ type: "coworld-replay", bytes }, "*");
  }, replayUrl);
  await bytePage.locator("html[data-replay-loaded=true]").waitFor({
    state: "attached",
    timeout: 30_000
  });
  const byteWorkerId = await bytePage.locator("html").getAttribute("data-replay-worker-id");
  if (!byteWorkerId || byteWorkerId === workerId) {
    throw new Error("Separate iframe pages did not create independent Workers");
  }

  if (browserErrors.length) {
    throw new Error("Browser errors: " + browserErrors.join(" | "));
  }
  console.log(JSON.stringify({ ...result, byteWorkerId }));
} finally {
  await browser.close();
}
