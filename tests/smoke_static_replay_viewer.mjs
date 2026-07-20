const playwrightModule = process.env.PLAYWRIGHT_MODULE || "playwright";
const { chromium } = await import(playwrightModule);

const viewerUrl = process.argv[2];
if (!viewerUrl) {
  throw new Error("Usage: node tests/smoke_static_replay_viewer.mjs <viewer-url>");
}

const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const browserErrors = [];
  page.on("console", message => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", error => browserErrors.push(error.message));

  await page.goto(viewerUrl, { waitUntil: "domcontentloaded" });
  try {
    await page.locator("html[data-replay-loaded=true]").waitFor({
      state: "attached",
      timeout: 30_000
    });
  } catch (error) {
    const state = await page.evaluate(() => ({
      status: document.getElementById("status")?.textContent,
      loaded: document.documentElement.dataset.replayLoaded,
      tick: document.documentElement.dataset.replayTick
    }));
    throw new Error(
      `Replay load timed out: ${JSON.stringify(state)}; ` +
      `browser errors: ${browserErrors.join(" | ")}`,
      { cause: error }
    );
  }
  const firstTick = Number(await page.locator("html").getAttribute("data-replay-tick"));
  await page.waitForFunction(
    tick => Number(document.documentElement.dataset.replayTick) > tick,
    firstTick,
    { timeout: 10_000 }
  );
  const result = await page.evaluate(() => {
    const canvas = document.getElementById("c");
    const status = document.getElementById("status");
    const pixels = canvas.getContext("2d").getImageData(
      0,
      0,
      canvas.width,
      canvas.height
    ).data;
    return {
      statusHidden: status.classList.contains("hidden"),
      tick: Number(document.documentElement.dataset.replayTick),
      maxTick: Number(document.documentElement.dataset.replayMaxTick),
      canvasWidth: canvas.width,
      canvasHeight: canvas.height,
      hasVisiblePixels: pixels.some(value => value !== 0)
    };
  });

  if (!result.statusHidden) throw new Error("Viewer status did not clear");
  if (result.tick <= firstTick) throw new Error("Replay did not advance");
  if (result.maxTick <= result.tick) throw new Error("Replay max tick is invalid");
  if (result.canvasWidth <= 1 || result.canvasHeight <= 1) {
    throw new Error("Shared renderer canvas was not sized");
  }
  if (!result.hasVisiblePixels) throw new Error("Shared renderer canvas is blank");
  if (browserErrors.length) {
    throw new Error("Browser errors: " + browserErrors.join(" | "));
  }
  console.log(JSON.stringify(result));
} finally {
  await browser.close();
}
