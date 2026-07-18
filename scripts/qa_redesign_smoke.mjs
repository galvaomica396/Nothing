// Redesign boot smoke for the dual Notion-style theme.
//
// Boots the app in a browser (vite dev, port 1420 strictPort — reused when
// already running), collects console/page errors, walks the screen panels
// via [data-screen-target] clicks, asserts each [data-screen-panel] gets
// .is-active, and saves dark/light responsive screenshots to build/redesign-evidence/.
//
// The left rail and coordinate-template screen are gone. The smoke walks the
// document workspace plus the two settings panels.
//
// Tauri IPC is mocked with the shared installTauriQaMocks helper
// (scripts/qa_tauri_mock.mjs) — the same convention shared by the other QA
// gates — so no blanket console-error filter is needed. Only the specific,
// known browser-environment gaps listed in KNOWN_BROWSER_ENV_ERRORS are
// tolerated.
//
// Usage: node scripts/qa_redesign_smoke.mjs [--url http://127.0.0.1:1420/]

import { spawn } from "node:child_process";
import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { installTauriQaMocks } from "./qa_tauri_mock.mjs";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(process.argv[index], process.argv[index + 1]);
}

// vite dev binds localhost (host: false in vite.config.ts); on this stack the
// listener may be IPv6-only, so probe via the localhost hostname.
const url = args.get("--url") ?? "http://localhost:1420/";
const desktopViewport = { width: 1280, height: 860 };
const evidenceViewports = [
  { name: "desktop", width: 1280, height: 860 },
  { name: "tablet", width: 768, height: 900 },
  { name: "mobile", width: 375, height: 812 },
];
const batchViewports = [
  evidenceViewports[0],
  { name: "compact-desktop", width: 1024, height: 860 },
  evidenceViewports[1],
  evidenceViewports[2],
];
const evidenceDir = path.join(repoRoot, "build", "redesign-evidence");
const fixturePath = path.join(repoRoot, "tests", "fixtures", "phase6_non_sensitive.pdf");

// Known, expected error signatures from running the Tauri frontend in a plain
// browser. Keep this list narrow — anything else fails the smoke.
const KNOWN_BROWSER_ENV_ERRORS = [
  // @tauri-apps/api raises this when an IPC surface is missing despite the
  // QA mock (e.g. a plugin channel the mock does not implement).
  /window\.__TAURI_INTERNALS__/,
];

function isKnownEnvError(text) {
  return KNOWN_BROWSER_ENV_ERRORS.some((pattern) => pattern.test(text));
}

async function isDevServerUp(target) {
  try {
    const response = await fetch(target, { signal: AbortSignal.timeout(1500) });
    return response.ok;
  } catch {
    return false;
  }
}

async function ensureDevServer(target) {
  if (await isDevServerUp(target)) {
    console.log(`[dev] reusing dev server at ${target}`);
    return null;
  }
  console.log("[dev] starting vite dev server (port 1420, strictPort)...");
  const child = spawn("npx", ["vite", "--port", "1420", "--strictPort"], {
    cwd: repoRoot,
    stdio: ["ignore", "pipe", "pipe"],
    detached: false,
  });
  let output = "";
  child.stdout.on("data", (chunk) => { output += chunk; });
  child.stderr.on("data", (chunk) => { output += chunk; });
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`vite dev exited early (code ${child.exitCode}):\n${output}`);
    }
    if (await isDevServerUp(target)) return child;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  child.kill("SIGTERM");
  throw new Error(`vite dev did not become ready within 60s:\n${output}`);
}

async function launchBrowser() {
  // Shared QA-gate convention: system Chrome first, bundled chromium as fallback.
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return await chromium.launch({ headless: true });
  }
}

async function activateScreen(page, screen) {
  const selector = `[data-screen-target="${screen}"]`;
  const visible = page.locator(`${selector}:visible`).first();
  if ((await visible.count()) > 0) {
    await visible.click();
  } else {
    const any = page.locator(selector).first();
    if ((await any.count()) === 0) throw new Error(`no trigger found for screen: ${screen}`);
    await any.evaluate((element) => element.click());
  }
  await page.locator(`[data-screen-panel="${screen}"].is-active`).waitFor({ state: "visible", timeout: 8_000 });
}

async function assertTheme(page, preference, resolved, background) {
  const state = await page.evaluate(() => ({
    preference: document.documentElement.getAttribute("data-theme-preference"),
    resolved: document.documentElement.getAttribute("data-theme"),
    background: getComputedStyle(document.documentElement).getPropertyValue("--dm-bg").trim(),
  }));
  if (state.preference !== preference || state.resolved !== resolved || state.background !== background) {
    throw new Error(`unexpected theme state: ${JSON.stringify(state)}`);
  }
}

async function waitForTheme(page, preference, resolved) {
  await page.waitForFunction(({ expectedPreference, expectedResolved }) => (
    document.documentElement.getAttribute("data-theme-preference") === expectedPreference
    && document.documentElement.getAttribute("data-theme") === expectedResolved
  ), { expectedPreference: preference, expectedResolved: resolved }, { timeout: 8_000 });
}

function capturePageErrors(page, errors) {
  page.on("console", (message) => {
    if (message.type() === "error") errors.push({ source: "console", text: message.text() });
  });
  page.on("pageerror", (error) => errors.push({ source: "pageerror", text: error.message }));
}

async function assertNoHorizontalOverflow(page, label) {
  const overflow = await page.evaluate(() => ({
    rootClient: document.documentElement.clientWidth,
    rootScroll: document.documentElement.scrollWidth,
    bodyClient: document.body.clientWidth,
    bodyScroll: document.body.scrollWidth,
  }));
  if (overflow.rootScroll > overflow.rootClient || overflow.bodyScroll > overflow.bodyClient) {
    throw new Error(`${label}: horizontal overflow ${JSON.stringify(overflow)}`);
  }
}

async function assertHorizontallyInsideViewport(page, locator, label) {
  const viewport = page.viewportSize();
  const box = await locator.boundingBox();
  if (!viewport || !box) throw new Error(`${label}: no viewport or bounding box`);
  if (box.x < -0.5 || box.x + box.width > viewport.width + 0.5) {
    throw new Error(`${label}: outside viewport ${JSON.stringify({ viewport, box })}`);
  }
}

async function assertVerticallyInsideViewport(page, locator, label) {
  const viewport = page.viewportSize();
  const box = await locator.boundingBox();
  if (!viewport || !box) throw new Error(`${label}: no viewport or bounding box`);
  if (box.y < -0.5 || box.y + box.height > viewport.height + 0.5) {
    throw new Error(`${label}: outside viewport ${JSON.stringify({ viewport, box })}`);
  }
}

async function assertClickable(page, locator, label) {
  if (!(await locator.isVisible()) || !(await locator.isEnabled())) {
    throw new Error(`${label}: action is not visible and enabled`);
  }
  await assertHorizontallyInsideViewport(page, locator, label);
}

function assertApprox(actual, expected, label, tolerance = 0.75) {
  if (Math.abs(actual - expected) > tolerance) {
    throw new Error(`${label}: expected ${expected}px, got ${actual}px`);
  }
}

async function computedGeometry(locator) {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element);
    const rect = element.getBoundingClientRect();
    return {
      width: rect.width,
      height: rect.height,
      borderRadius: Number.parseFloat(style.borderTopLeftRadius),
      borderWidth: Number.parseFloat(style.borderTopWidth),
      filter: style.filter,
      mixBlendMode: style.mixBlendMode,
      opacity: style.opacity,
    };
  });
}

async function assertWorkspaceGeometry(page, label) {
  const header = await computedGeometry(page.locator(".dm-header"));
  const inspector = await computedGeometry(page.locator("#canvas-workspace-screen .dm-inspector"));
  const status = await computedGeometry(page.locator(".dm-statusbar"));
  const card = await computedGeometry(page.locator("#canvas-workspace-screen .dm-inspector__card").first());
  const primary = await computedGeometry(page.locator("#btn-run-masking"));
  assertApprox(header.height, 48, `${label}/header`);
  assertApprox(inspector.width, 320, `${label}/inspector`);
  assertApprox(status.height, 28, `${label}/statusbar`);
  assertApprox(card.borderRadius, 12, `${label}/inspector-card-radius`);
  assertApprox(card.borderWidth, 1, `${label}/hairline`);
  if (primary.borderRadius < 999) throw new Error(`${label}/primary-pill: expected pill radius, got ${primary.borderRadius}px`);
}

async function assertSettingsGeometry(page, selector, label) {
  const grid = await computedGeometry(page.locator(selector));
  const card = await computedGeometry(page.locator(`${selector} .dm-settings-card`).first());
  assertApprox(grid.width, 800, `${label}/content-width`);
  assertApprox(card.borderRadius, 12, `${label}/card-radius`);
  assertApprox(card.borderWidth, 1, `${label}/hairline`);
}

async function assertSaveDialogGeometry(page, label) {
  const dialog = await computedGeometry(page.locator("#final-save-dialog .ux-modal"));
  const primary = await computedGeometry(page.locator("#btn-dialog-save-all"));
  assertApprox(dialog.width, 480, `${label}/save-width`);
  assertApprox(dialog.borderRadius, 16, `${label}/save-radius`);
  if (primary.borderRadius < 999) throw new Error(`${label}/save-pill: expected pill radius, got ${primary.borderRadius}px`);
}

async function assertPdfCanvasNeutral(page, label) {
  for (const selector of ["#pdf-canvas-orig", "#pdf-canvas-result"]) {
    const geometry = await computedGeometry(page.locator(selector));
    if (geometry.filter !== "none" || geometry.mixBlendMode !== "normal" || geometry.opacity !== "1") {
      throw new Error(`${label}/${selector}: theme altered PDF canvas CSS ${JSON.stringify(geometry)}`);
    }
  }
}

async function captureState(page, theme, state, viewports, screenshots, beforeCapture) {
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    if (beforeCapture) await beforeCapture(viewport);
    await page.waitForTimeout(120);
    await assertNoHorizontalOverflow(page, `${theme}/${viewport.name}/${state}`);
    const shotPath = path.join(evidenceDir, `${theme}-${viewport.name}-${state}.png`);
    await page.screenshot({ path: shotPath, fullPage: false });
    screenshots.push(shotPath);
    console.log(`[capture] ${theme}/${viewport.name}/${state} -> ${path.relative(repoRoot, shotPath)}`);
  }
  await page.setViewportSize(desktopViewport);
}

async function openThemePage(browser, theme, errors, options = {}) {
  const page = await browser.newPage({ viewport: desktopViewport });
  capturePageErrors(page, errors);
  await installTauriQaMocks(page, {
    fixturePath,
    outputDir: path.join(evidenceDir, "output"),
    pdfBytes,
    failBatchItemOnce: options.failBatchItemOnce === true,
  });
  await page.addInitScript((selectedTheme) => {
    localStorage.setItem("makiiing-v2-settings", JSON.stringify({ theme: selectedTheme, profile: "official" }));
  }, theme);
  await page.goto(url, { waitUntil: "networkidle" });
  await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
  await assertTheme(page, theme, theme, theme === "light" ? "#f6f5f4" : "#0e1116");
  return page;
}

async function runThemeVisualAudit(browser, theme, screenshots, errors) {
  const page = await openThemePage(browser, theme, errors);
  try {
    await activateScreen(page, "documents");
    if (await page.locator("#btn-new-document").isVisible()) {
      throw new Error(`${theme}/workspace-empty: new-work action must stay hidden before final save`);
    }
    await captureState(page, theme, "workspace-empty", evidenceViewports, screenshots, async (viewport) => {
      if (viewport.name === "mobile") {
        await assertVerticallyInsideViewport(page, page.locator(".dm-canvas__hero-title"), `${theme}/mobile/hero-title`);
        await assertVerticallyInsideViewport(page, page.locator(".dm-canvas__hero-cta"), `${theme}/mobile/hero-cta`);
      }
    });
    await assertWorkspaceGeometry(page, `${theme}/workspace-empty`);

    await activateScreen(page, "masking-settings");
    const maskingSettingsScroll = page.locator("#masking-settings-screen .dm-settings-scroll");
    await maskingSettingsScroll.evaluate((element) => { element.scrollTop = 0; });
    await captureState(page, theme, "masking-settings-top", evidenceViewports, screenshots, async () => {
      await maskingSettingsScroll.evaluate((element) => { element.scrollTop = 0; });
    });
    await assertSettingsGeometry(page, ".dm-settings-grid--masking", `${theme}/masking-settings`);
    await captureState(page, theme, "masking-settings-scrolled", evidenceViewports, screenshots, async () => {
      await maskingSettingsScroll.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    });

    await activateScreen(page, "settings");
    await captureState(page, theme, "general-settings", evidenceViewports, screenshots);
    await assertSettingsGeometry(page, ".dm-settings-grid--app", `${theme}/general-settings`);

    await activateScreen(page, "documents");
    await page.locator("#btn-pick-pdf").click();
    await page.locator("#canvas-wrap-result.has-rendered-pdf").waitFor({ state: "attached", timeout: 8_000 });
    await captureState(page, theme, "workspace-loaded", evidenceViewports, screenshots, async (viewport) => {
      if (viewport.name === "mobile") {
        await assertHorizontallyInsideViewport(page, page.locator(".dm-canvas__sync"), `${theme}/mobile/page-sync`);
      }
    });
    await assertWorkspaceGeometry(page, `${theme}/workspace-loaded`);
    await assertPdfCanvasNeutral(page, `${theme}/workspace-loaded`);
    const pdfFingerprint = await page.locator("#pdf-canvas-orig").evaluate((canvas) => canvas.toDataURL());

    await page.locator("#btn-run-masking").click();
    await page.waitForFunction(() => (
      window.__QA_INVOKES__.filter((entry) => entry.cmd === "run_masking_pipeline").length === 1
      && document.querySelector("#btn-run-masking")?.disabled === false
    ), { timeout: 15_000 });

    // The responsive capture loop can leave an inner workspace scroller below
    // the sticky toolbar. Dispatch through the bound DOM action here; batch
    // run/retry remain real pointer clicks and have their own reachability gate.
    await page.locator("#btn-open-keyword-dialog").evaluate((element) => element.click());
    await page.locator("#keyword-dialog").waitFor({ state: "visible", timeout: 8_000 });
    await captureState(page, theme, "keyword-modal", evidenceViewports, screenshots);
    await page.locator("#btn-close-keyword-dialog").click();

    await activateScreen(page, "masking-settings");
    // Requesting TXT after the completed PDF-only run creates a deterministic,
    // non-blocking save advisory without weakening the production mock report.
    await page.locator("#settings-export-masked-text").check();
    await page.locator("#btn-masking-settings-apply").click();
    await activateScreen(page, "documents");
    await page.locator("#btn-save").evaluate((element) => element.click());
    await page.locator("#final-save-dialog").waitFor({ state: "visible", timeout: 8_000 });
    if (await page.locator("#final-save-warning-list .dm-savewarn__item").count() < 1) {
      throw new Error(`${theme}/save-warning: expected at least one advisory item`);
    }
    await captureState(page, theme, "save-warning", evidenceViewports, screenshots);
    await assertSaveDialogGeometry(page, `${theme}/save-warning`);
    await page.locator("#btn-dialog-cancel-save").click();
    return pdfFingerprint;
  } finally {
    await page.close();
  }
}

async function assertBatchPanel(page, action, label) {
  const panel = page.locator(".dm-canvas__batch-panel");
  await assertHorizontallyInsideViewport(page, panel, `${label}/panel`);
  await assertClickable(page, action, `${label}/action`);
  const widths = await panel.evaluate((element) => ({ client: element.clientWidth, scroll: element.scrollWidth }));
  if (widths.scroll > widths.client) throw new Error(`${label}/panel: horizontal overflow ${JSON.stringify(widths)}`);
  const actionBox = await action.boundingBox();
  const expectedHeight = page.viewportSize()?.width === 375 ? 44 : 32;
  if (!actionBox || actionBox.height < expectedHeight) {
    throw new Error(`${label}/action: expected at least ${expectedHeight}px, got ${actionBox?.height ?? 0}px`);
  }
  const outputStyle = await panel.locator(".batch-output").last().evaluate((element) => getComputedStyle(element).whiteSpace);
  if (outputStyle !== "normal") throw new Error(`${label}/output: expected wrapped failure/output text, got ${outputStyle}`);
}

async function runBatchVisualAudit(browser, theme, screenshots, errors) {
  const page = await openThemePage(browser, theme, errors, { failBatchItemOnce: true });
  try {
    await page.locator("#btn-pick-batch").click();
    await page.locator("#batch-queue .batch-item").nth(1).waitFor({ state: "attached", timeout: 8_000 });
    await page.locator(".dm-canvas__batch-summary").click();
    const batchButton = page.locator("#btn-run-batch");
    await batchButton.waitFor({ state: "visible", timeout: 8_000 });
    await captureState(page, theme, "batch-pending", batchViewports, screenshots, async (viewport) => {
      await assertBatchPanel(page, batchButton, `${theme}/${viewport.name}/batch-pending`);
    });
    await batchButton.click();
    await page.waitForFunction(() => Array.from(document.querySelectorAll("#batch-queue .batch-state"))
      .every((element) => element.textContent === "완료" || element.textContent === "실패"), { timeout: 15_000 });
    const retryButton = page.locator("#batch-queue .status-실패 .batch-actions button", { hasText: "재실행" });
    if (await retryButton.count() !== 1) throw new Error(`${theme}/batch-failed: expected one retry action`);
    await captureState(page, theme, "batch-failed", batchViewports, screenshots, async (viewport) => {
      await assertBatchPanel(page, retryButton, `${theme}/${viewport.name}/batch-failed`);
    });
    await retryButton.click();
    await page.waitForFunction(() => Array.from(document.querySelectorAll("#batch-queue .batch-state"))
      .filter((element) => element.textContent === "완료").length === 2, { timeout: 15_000 });
    console.log(`[batch] ${theme}: pending action and failed-item retry stayed clickable at 1280/1024/768/375`);
  } finally {
    await page.close();
  }
}

await mkdir(evidenceDir, { recursive: true });

const devServer = await ensureDevServer(url);
const pdfBytes = [...(await readFile(fixturePath))];
const browser = await launchBrowser();

const errors = [];
const screenshots = [];
const failures = [];

try {
  const firstPaintPage = await browser.newPage({ viewport: desktopViewport });
  let releaseMainModule;
  const mainModuleGate = new Promise((resolve) => { releaseMainModule = resolve; });
  await firstPaintPage.route("**/src/main.tsx", async (route) => {
    await mainModuleGate;
    await route.continue();
  });
  await installTauriQaMocks(firstPaintPage, {
    fixturePath,
    outputDir: path.join(evidenceDir, "output"),
    pdfBytes,
  });
  await firstPaintPage.addInitScript(() => {
    localStorage.setItem("makiiing-v2-settings", JSON.stringify({ theme: "light", profile: "official" }));
  });
  const firstPaintNavigation = firstPaintPage.goto(url, { waitUntil: "domcontentloaded" });
  await firstPaintPage.waitForFunction(() => (
    document.documentElement.getAttribute("data-theme-preference") === "light"
    && document.documentElement.getAttribute("data-theme") === "light"
  ), { timeout: 8_000 });
  releaseMainModule();
  await firstPaintNavigation;
  await firstPaintPage.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
  await assertTheme(firstPaintPage, "light", "light", "#f6f5f4");
  if (!(await firstPaintPage.locator("#btn-run-masking").isDisabled())) {
    throw new Error("current PDF masking action was enabled before document readiness initialized");
  }
  const firstPaintShot = path.join(evidenceDir, "light-first-paint.png");
  await firstPaintPage.screenshot({ path: firstPaintShot, fullPage: false });
  screenshots.push(firstPaintShot);
  await firstPaintPage.close();
  console.log("[theme] stored light resolved before the application module; first frame stayed light with masking disabled");

  const themeControlPage = await openThemePage(browser, "dark", errors);
  await activateScreen(themeControlPage, "settings");
  await themeControlPage.locator('input[name="settings-theme"][value="light"]').check({ force: true });
  await assertTheme(themeControlPage, "light", "light", "#f6f5f4");
  const persistedLightTheme = await themeControlPage.evaluate(() => JSON.parse(localStorage.getItem("makiiing-v2-settings") ?? "{}").theme);
  if (persistedLightTheme !== "light") throw new Error(`light theme was not persisted: ${persistedLightTheme}`);
  await themeControlPage.emulateMedia({ colorScheme: "light" });
  await themeControlPage.locator('input[name="settings-theme"][value="system"]').check({ force: true });
  await assertTheme(themeControlPage, "system", "light", "#f6f5f4");
  await themeControlPage.emulateMedia({ colorScheme: "dark" });
  await waitForTheme(themeControlPage, "system", "dark");
  await assertTheme(themeControlPage, "system", "dark", "#0e1116");
  await themeControlPage.emulateMedia({ colorScheme: "light" });
  await waitForTheme(themeControlPage, "system", "light");
  await assertTheme(themeControlPage, "system", "light", "#f6f5f4");
  await themeControlPage.close();
  console.log("[theme] legacy dark fallback, immediate light persistence, and live system switching OK");

  const pdfFingerprints = new Map();
  for (const theme of ["dark", "light"]) {
    try {
      pdfFingerprints.set(theme, await runThemeVisualAudit(browser, theme, screenshots, errors));
    } catch (error) {
      failures.push({ screen: `${theme}/visual-audit`, error: error instanceof Error ? error.message : String(error) });
      console.error(`[visual] ${theme}: FAILED (${failures.at(-1).error})`);
    }
    try {
      await runBatchVisualAudit(browser, theme, screenshots, errors);
    } catch (error) {
      failures.push({ screen: `${theme}/batch`, error: error instanceof Error ? error.message : String(error) });
      console.error(`[batch] ${theme}: FAILED (${failures.at(-1).error})`);
    }
  }
  if (pdfFingerprints.size === 2 && pdfFingerprints.get("dark") !== pdfFingerprints.get("light")) {
    failures.push({ screen: "pdf-color", error: "original PDF canvas pixels changed between dark and light themes" });
  } else if (pdfFingerprints.size === 2) {
    console.log("[pdf] original canvas pixel fingerprint is identical in dark and light themes");
  }
} finally {
  await browser.close();
  if (devServer) {
    devServer.kill("SIGTERM");
    console.log("[dev] stopped vite dev server");
  }
}

const unexpectedErrors = errors.filter((entry) => !isKnownEnvError(entry.text));
const ignoredErrors = errors.filter((entry) => isKnownEnvError(entry.text));

if (ignoredErrors.length > 0) {
  console.log(`[info] ${ignoredErrors.length} known browser-env error(s) ignored:`);
  for (const entry of ignoredErrors) console.log(`  - (${entry.source}) ${entry.text}`);
}

if (unexpectedErrors.length > 0) {
  console.error(`SMOKE FAILED — ${unexpectedErrors.length} console/page error(s):`);
  for (const entry of unexpectedErrors) console.error(`  - (${entry.source}) ${entry.text}`);
}
if (failures.length > 0) {
  console.error(`SMOKE FAILED — ${failures.length} screen transition failure(s):`);
  for (const failure of failures) console.error(`  - ${failure.screen}: ${failure.error}`);
}

if (unexpectedErrors.length > 0 || failures.length > 0) {
  process.exit(1);
}

console.log(`SMOKE OK — dark/light responsive screens activated, 0 console/page errors, ${screenshots.length} screenshots in ${path.relative(repoRoot, evidenceDir)}/`);
