// Redesign boot smoke for the dual Notion-style theme.
//
// Boots an isolated checkout-owned Vite server, collects console/page errors, walks the screen panels
// via [data-screen-target] clicks, asserts each [data-screen-panel] gets
// .is-active, and saves dark/light responsive screenshots to build/redesign-evidence/.
//
// The left rail and coordinate-template screen are gone. The smoke walks the
// document workspace plus the two settings panels.
//
// Tauri IPC is mocked with the shared installTauriQaMocks helper
// (scripts/qa_tauri_mock.mjs) for browser UI-wiring and visual behavior only.
// Native authorization, bypass, and replay authority is covered by the packaged
// app receipt harness, not this mock.
//
// Usage: node scripts/qa_redesign_smoke.mjs [--url http://127.0.0.1:1420/]

import { spawn } from "node:child_process";
import { mkdir, readFile, readdir, unlink } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { installTauriQaMocks } from "./qa_tauri_mock.mjs";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

function parseCliArgs(argv) {
  const allowed = new Set(["--url", "--evidence-dir"]);
  const args = new Map();
  for (let index = 2; index < argv.length; index += 2) {
    const name = argv[index];
    const value = argv[index + 1];
    if (!allowed.has(name)) throw new Error(`QA_CLI_UNKNOWN_ARGUMENT:${name}`);
    if (args.has(name)) throw new Error(`QA_CLI_DUPLICATE_ARGUMENT:${name}`);
    if (!value || value.startsWith("--")) throw new Error(`QA_CLI_MISSING_VALUE:${name}`);
    args.set(name, value);
  }
  return args;
}

const args = parseCliArgs(process.argv);
const requestedUrl = args.get("--url");
if (requestedUrl) {
  const target = new URL(requestedUrl);
  if (!["localhost", "127.0.0.1", "[::1]"].includes(target.hostname)) {
    throw new Error("QA_URL_MUST_TARGET_LOCAL_CHECKOUT");
  }
}
let url;
let pdfBytes;
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
const evidenceDir = path.resolve(args.get("--evidence-dir") ?? path.join(repoRoot, "build", "redesign-evidence"));
const fixturePath = path.join(repoRoot, "tests", "fixtures", "phase6_non_sensitive.pdf");


async function isDevServerUp(target) {
  try {
    return (await fetch(target, { signal: AbortSignal.timeout(1500) })).ok;
  } catch {
    return false;
  }
}

function childViteUrl(output) {
  const match = output.match(/Local:\s+(https?:\/\/[^\s]+)/);
  return match?.[1] ?? null;
}

async function ensureDevServer() {
  console.log("[dev] starting isolated checkout-owned vite dev server...");
  const child = spawn("npx", ["vite", "--host", "127.0.0.1", "--port", "0", "--strictPort"], {
    cwd: repoRoot,
    stdio: ["ignore", "pipe", "pipe"],
    detached: false,
  });
  let output = "";
  child.stdout.on("data", (chunk) => { output += chunk; });
  child.stderr.on("data", (chunk) => { output += chunk; });
  const deadline = Date.now() + 60_000;
  try {
    while (Date.now() < deadline) {
      if (child.exitCode !== null) throw new Error(`vite dev exited early (code ${child.exitCode}):\n${output}`);
      const childUrl = childViteUrl(output);
      if (childUrl && await isDevServerUp(childUrl)) return { child, url: childUrl };
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    throw new Error(`vite dev did not publish a ready child-bound URL within 60s:\n${output}`);
  } catch (error) {
    if (child.exitCode === null) child.kill("SIGTERM");
    throw error;
  }
}

function isMissingSystemChrome(error) {
  return error instanceof Error && /(executable (does not exist|was not found)|browserType\.launch: Executable)/i.test(error.message);
}

function launchDiagnostic(error) {
  return error instanceof Error ? error.message : String(error);
}

async function launchBrowser() {
  try {
    const browser = await chromium.launch({ channel: "chrome", headless: true });
    return { browser, selection: "system-chrome" };
  } catch (systemChromeError) {
    if (!isMissingSystemChrome(systemChromeError)) {
      throw new Error(`system Chrome launch failed: ${launchDiagnostic(systemChromeError)}`);
    }
    try {
      const browser = await chromium.launch({ headless: true });
      return { browser, selection: "bundled-chromium", systemChromeDiagnostic: launchDiagnostic(systemChromeError) };
    } catch (bundledChromiumError) {
      throw new Error(`browser launch failed; system Chrome: ${launchDiagnostic(systemChromeError)}; bundled Chromium: ${launchDiagnostic(bundledChromiumError)}`);
    }
  }
}

async function activateScreen(page, screen) {
  const selector = `[data-screen-target="${screen}"]`;
  const triggers = page.locator(`${selector}:visible`);
  if ((await triggers.count()) < 1) {
    throw new Error(`screen '${screen}' has no visible enabled navigation trigger`);
  }
  const trigger = triggers.first();
  if (!(await trigger.isEnabled())) throw new Error(`screen '${screen}' has no visible enabled navigation trigger`);
  await trigger.click();
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
async function applyPublicManualMask(page, theme) {
  try {
    await page.waitForFunction(() => (
      document.querySelector("#btn-run-masking")?.getAttribute("data-running") !== "true"
      && document.querySelector("#final-state-card")?.getAttribute("data-state") !== "running"
    ), null, { timeout: 8_000 });
  } catch {
    const diagnostic = await page.evaluate(() => ({
      btnRunMaskingRunning: document.querySelector("#btn-run-masking")?.getAttribute("data-running") ?? null,
      finalStateCardState: document.querySelector("#final-state-card")?.getAttribute("data-state") ?? null,
      finalStateCardPresent: Boolean(document.querySelector("#final-state-card")),
      profile: document.querySelector("#profile")?.value ?? null,
      status: document.querySelector("#status")?.textContent ?? null,
      boxCount: document.querySelectorAll("#canvas-box-list button").length,
      restoreLocked: document.querySelector("#btn-canvas-tool-restore") instanceof HTMLButtonElement
        && document.querySelector("#btn-canvas-tool-restore").disabled,
      analyzeInvokes: window.__QA_INVOKES__?.filter((entry) => entry.cmd === "analyze_masking_run").length ?? null,
      activeScreen: document.querySelector('[data-screen-panel].is-active')?.getAttribute("data-screen-panel") ?? null,
    }));
    console.error(`${theme}/public-manual-mask: masking-settle wait timed out; state=${JSON.stringify(diagnostic)}`);
    throw new Error(`${theme}/public-manual-mask: masking-settle wait timed out ${JSON.stringify(diagnostic)}`);
  }
  const pending = await page.evaluate(() => {
    return {
      boxCount: document.querySelectorAll("#canvas-box-list button").length,
      restoreLocked: document.querySelector("#btn-canvas-tool-restore") instanceof HTMLButtonElement
        && document.querySelector("#btn-canvas-tool-restore").disabled,
    };
  });
  if (pending.boxCount !== 1 || pending.restoreLocked) {
    throw new Error(`${theme}/public-manual-mask: mask box or restore availability is incorrect ${JSON.stringify(pending)}`);
  }
  await page.locator("#btn-canvas-apply").click();
  await page.waitForFunction(() => window.__QA_INVOKES__.filter((entry) => entry.cmd === "apply_manual_action_v1").length === 1, null, { timeout: 8_000 });
  const request = await page.evaluate(() => window.__QA_INVOKES__.find((entry) => entry.cmd === "apply_manual_action_v1")?.payload.request);
  if (
    request?.page !== 0
    || request?.rectCount !== 1
    || request?.mode !== "mask"
    || request?.sourceKind !== "scan"
    || request?.linkedOccurrenceId !== null
    || request?.expectedTextHash !== null
    || request?.protectedNeighborRefCount !== 0
  ) throw new Error(`${theme}/public-manual-mask: invalid authority request ${JSON.stringify(request)}`);
  await page.waitForFunction(() => document.querySelectorAll("#canvas-box-list button").length === 0, null, { timeout: 8_000 });

  await page.locator("#btn-canvas-tool-restore").click();
  const overlayBox = await page.locator("#overlay-canvas-result").boundingBox();
  if (!overlayBox) throw new Error(`${theme}/public-manual-restore: result overlay has no bounding box`);
  await page.locator("#overlay-canvas-result").evaluate((canvas, bounds) => {
    const start = { x: bounds.x + bounds.width * 0.67, y: bounds.y + bounds.height * 0.22 };
    const end = { x: bounds.x + bounds.width * 0.93, y: bounds.y + bounds.height * 0.37 };
    const emit = (type, point) => canvas.dispatchEvent(new MouseEvent(type, {
      bubbles: true,
      clientX: point.x,
      clientY: point.y,
      button: 0,
    }));
    emit("mousedown", start);
    for (let step = 1; step <= 4; step += 1) {
      emit("mousemove", {
        x: start.x + (end.x - start.x) * step / 4,
        y: start.y + (end.y - start.y) * step / 4,
      });
    }
    emit("mouseup", end);
  }, overlayBox);
  if (await page.locator("#canvas-box-list button").count() !== 1) {
    throw new Error(`${theme}/public-manual-restore: restore box was not created`);
  }
  await page.locator("#btn-canvas-apply").click();
  await page.waitForTimeout(300);
  const restoreResult = await page.evaluate(() => ({
    applyCount: window.__QA_INVOKES__.filter((entry) => entry.cmd === "apply_manual_action_v1").length,
    status: document.querySelector("#status")?.textContent ?? "",
    boxCount: document.querySelectorAll("#canvas-box-list button").length,
  }));
  if (
    restoreResult.applyCount !== 1
    || restoreResult.boxCount !== 1
    || !restoreResult.status.includes("실제 캔버스 드래그")
  ) throw new Error(`${theme}/public-manual-restore: synthetic restore was not blocked ${JSON.stringify(restoreResult)}`);
  await page.locator("#btn-canvas-undo").click();
  await page.waitForFunction(() => document.querySelectorAll("#canvas-box-list button").length === 0, null, { timeout: 8_000 });
  await page.locator("#btn-canvas-tool-mask").click();
}

async function readPublicDetectionOverlay(page) {
  return page.evaluate(() => {
    const overlay = document.querySelector("#overlay-canvas-result");
    if (!(overlay instanceof HTMLCanvasElement)) throw new Error("detection overlay canvas is unavailable");
    const context = overlay.getContext("2d");
    if (!context) throw new Error("detection overlay context is unavailable");
    const sample = (x, y) => [...context.getImageData(x, y, 1, 1).data];
    const sampleRect = (x0, y0, x1, y1) => {
      const x = Math.round(x0 + (x1 - x0) / 2);
      const y = Math.round(y0 + (y1 - y0) / 2);
      return { x, y, rgba: sample(x, y) };
    };
    const scale = overlay.width / 420;
    const mapRect = ({ x0, y0, x1, y1 }) => ({ x0: x0 * scale, y0: y0 * scale, x1: x1 * scale, y1: y1 * scale });
    const pending = mapRect({ x0: 72, y0: 60, x1: 200, y1: 78 });
    const confirmed = mapRect({ x0: 280, y0: 60, x1: 408, y1: 82 });
    const keyword = mapRect({ x0: 72, y0: 108, x1: 200, y1: 130 });
    return {
      width: overlay.width,
      height: overlay.height,
      scale,
      pending: {
        bounds: pending,
        outline: sample(Math.round(pending.x0), Math.round((pending.y0 + pending.y1) / 2)),
        center: sampleRect(pending.x0, pending.y0, pending.x1, pending.y1),
      },
      confirmed: {
        bounds: confirmed,
        center: sampleRect(confirmed.x0, confirmed.y0, confirmed.x1, confirmed.y1),
      },
      keyword: {
        bounds: keyword,
        center: sampleRect(keyword.x0, keyword.y0, keyword.x1, keyword.y1),
      },
    };
  });
}

function assertOverlayBounds(overlay, label) {
  for (const rect of [overlay.pending.bounds, overlay.confirmed.bounds, overlay.keyword.bounds]) {
    if (rect.x0 < 0 || rect.y0 < 0 || rect.x1 > overlay.width || rect.y1 > overlay.height || rect.x1 <= rect.x0 || rect.y1 <= rect.y0) {
      throw new Error(`${label}: overlay rect is outside rendered canvas ${JSON.stringify({ rect, width: overlay.width, height: overlay.height })}`);
    }
  }
}

function isOpaqueBlack(rgba) {
  return rgba[0] === 0 && rgba[1] === 0 && rgba[2] === 0 && rgba[3] === 255;
}

async function assertFinalSaveControls(page, expectedSelector, enabled) {
  const controls = await page.evaluate(() => ["#btn-save", "#btn-canvas-final-save"].map((selector) => {
    const element = document.querySelector(selector);
    const visible = element instanceof HTMLElement && getComputedStyle(element).display !== "none"
      && getComputedStyle(element).visibility !== "hidden" && element.getClientRects().length > 0;
    return { selector, visible, disabled: element instanceof HTMLButtonElement ? element.disabled : null };
  }));
  const expected = controls.find((control) => control.selector === expectedSelector);
  const expectedVisible = expected?.visible === true;
  const expectedDisabled = expected?.disabled === !enabled;
  if (!expectedVisible || !expectedDisabled) {
    throw new Error(`unexpected final-save control: ${JSON.stringify({ expectedSelector, enabled, expectedVisible, expectedDisabled, controls })}`);
  }
}

async function assertConfirmSaveWarning(page, label, expectedWarning) {
  const finalizationsBefore = await page.evaluate(() => window.__QA_INVOKES__.filter((entry) => entry.cmd === "finalize_masking_run").length);
  await page.locator("#btn-save").click();
  await page.locator("#final-save-dialog").waitFor({ state: "visible", timeout: 8_000 });
  const dialog = await page.evaluate(() => ({
    warnings: [...document.querySelectorAll("#final-save-warning-list .dm-savewarn__item")].map((item) => item.textContent?.trim() ?? ""),
    confirmDisabled: document.querySelector("#btn-dialog-save-all") instanceof HTMLButtonElement
      ? document.querySelector("#btn-dialog-save-all").disabled
      : null,
    finalizations: window.__QA_INVOKES__.filter((entry) => entry.cmd === "finalize_masking_run").length,
  }));
  if (dialog.warnings.length === 0 || (expectedWarning && !dialog.warnings.includes(expectedWarning)) || dialog.confirmDisabled !== false || dialog.finalizations !== finalizationsBefore) {
    throw new Error(`${label}: confirm-save warning contract failed ${JSON.stringify({ expectedWarning, finalizationsBefore, dialog })}`);
  }
  await page.locator("#btn-dialog-cancel-save").click();
  await page.locator("#final-save-dialog").waitFor({ state: "hidden", timeout: 8_000 });
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
      borderBottomWidth: Number.parseFloat(style.borderBottomWidth),
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
  const primary = await computedGeometry(page.locator("#btn-run-masking"));
  assertApprox(header.height, 64, `${label}/header`);
  assertApprox(inspector.width, 340, `${label}/inspector`);
  assertApprox(status.height, 28, `${label}/statusbar`);
  assertApprox(primary.borderRadius, 8, `${label}/primary-radius`);
}

async function assertWorkspaceCardGeometry(page, label) {
  const detectCard = await computedGeometry(page.locator("#final-state-card"));
  const saveSummary = await computedGeometry(page.locator("#save-summary-accordion"));
  if (!(detectCard.width > 0)) {
    throw new Error(`${label}: card assertions require a post-run state (final-state-card is still hidden, bounding box width is ${detectCard.width})`);
  }
  if (!(saveSummary.width > 0)) {
    throw new Error(`${label}: card assertions require a post-run state (save-summary-accordion is still hidden, bounding box width is ${saveSummary.width})`);
  }
  assertApprox(detectCard.borderRadius, 0, `${label}/inspector-detect-flat`);
  assertApprox(saveSummary.borderRadius, 12, `${label}/inspector-card-radius`);
  assertApprox(saveSummary.borderBottomWidth, 1, `${label}/hairline`);
}

async function assertSettingsGeometry(page, selector, label) {
  const grid = await computedGeometry(page.locator(selector));
  const card = await computedGeometry(page.locator(`${selector} .dm-settings-card`).first());
  assertApprox(grid.width, 800, `${label}/content-width`);
  assertApprox(card.borderRadius, 12, `${label}/card-radius`);
  assertApprox(card.borderBottomWidth, 1, `${label}/hairline`);
}

async function assertSaveDialogGeometry(page, label) {
  const dialog = await computedGeometry(page.locator("#final-save-dialog .ux-modal"));
  const primary = await computedGeometry(page.locator("#btn-dialog-save-all"));
  const expectedWidth = Math.min(440, (page.viewportSize()?.width ?? 440) * 0.9);
  assertApprox(dialog.width, expectedWidth, `${label}/save-width`, 1.5);
  assertApprox(dialog.borderRadius, 12, `${label}/save-radius`);
  assertApprox(primary.borderRadius, 8, `${label}/save-primary-radius`);
  const layout = await page.locator("#final-save-dialog .ux-modal").evaluate((modal) => {
    const visible = (element) => element instanceof HTMLElement && getComputedStyle(element).display !== "none";
    const rect = (element) => {
      const box = element.getBoundingClientRect();
      return { top: box.top, right: box.right, bottom: box.bottom, left: box.left };
    };
    const header = modal.querySelector(".ux-modal-head");
    const advisory = modal.querySelector(".dm-savewarn__summary");
    const ready = modal.querySelector("#final-save-dialog-state");
    const footer = modal.querySelector(".ux-modal-actions");
    const content = visible(advisory) ? advisory : ready;
    const descriptionStyle = getComputedStyle(modal.querySelector(".ux-modal-head p"));
    const advisoryStyle = getComputedStyle(advisory);
    const footerStyle = getComputedStyle(footer);
    return {
      header: rect(header),
      content: rect(content),
      footer: rect(footer),
      advisoryVisible: visible(advisory),
      readyVisible: visible(ready),
      clientWidth: modal.clientWidth,
      scrollWidth: modal.scrollWidth,
      descriptionWordBreak: descriptionStyle.wordBreak,
      descriptionOverflowWrap: descriptionStyle.overflowWrap,
      descriptionLineHeight: Number.parseFloat(descriptionStyle.lineHeight),
      descriptionFontSize: Number.parseFloat(descriptionStyle.fontSize),
      advisoryWordBreak: advisoryStyle.wordBreak,
      advisoryOverflowWrap: advisoryStyle.overflowWrap,
      footerFlexWrap: footerStyle.flexWrap,
    };
  });
  if (layout.header.bottom > layout.content.top + 0.5 || layout.content.bottom > layout.footer.top + 0.5) {
    throw new Error(`${label}/save-overlap: vertical regions overlap ${JSON.stringify(layout)}`);
  }
  if (layout.scrollWidth > layout.clientWidth + 1) throw new Error(`${label}/save-overflow: ${JSON.stringify(layout)}`);
  if (layout.descriptionWordBreak !== "keep-all" || layout.descriptionOverflowWrap !== "break-word") {
    throw new Error(`${label}/save-wrap: description wrapping is unsafe ${JSON.stringify(layout)}`);
  }
  if (layout.advisoryWordBreak !== "keep-all" || layout.advisoryOverflowWrap !== "break-word") {
    throw new Error(`${label}/save-wrap: advisory wrapping is unsafe ${JSON.stringify(layout)}`);
  }
  if (layout.descriptionLineHeight / layout.descriptionFontSize < 1.45) {
    throw new Error(`${label}/save-line-height: expected at least 1.5 ${JSON.stringify(layout)}`);
  }
  if (layout.footerFlexWrap !== "wrap") throw new Error(`${label}/save-footer: buttons cannot wrap`);
}

async function assertSuccessDialogGeometry(page, label) {
  const dialog = await computedGeometry(page.locator("#finalization-success-dialog .ux-modal"));
  const expectedWidth = Math.min(440, (page.viewportSize()?.width ?? 440) * 0.9);
  assertApprox(dialog.width, expectedWidth, `${label}/success-width`, 1.5);
  assertApprox(dialog.borderRadius, 12, `${label}/success-radius`);
}

async function assertStatusToastReadable(page, label) {
  const status = await page.locator("#status").evaluate((element) => {
    const style = getComputedStyle(element);
    const parseRgb = (value) => {
      const match = value.match(/\d+(\.\d+)?/g) ?? [];
      return match.slice(0, 3).map((channel) => Number(channel) / 255);
    };
    const luminance = ([r, g, b]) => {
      const linear = [r, g, b].map((channel) => (channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4));
      return (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2]);
    };
    const fg = parseRgb(style.color);
    const bg = parseRgb(style.backgroundColor);
    const lighter = Math.max(luminance(fg), luminance(bg));
    const darker = Math.min(luminance(fg), luminance(bg));
    return {
      text: element.textContent?.trim() ?? "",
      color: style.color,
      backgroundColor: style.backgroundColor,
      contrast: (lighter + 0.05) / (darker + 0.05),
    };
  });
  if (!status.text) throw new Error(`${label}: status text is empty`);
  if (status.contrast < 4.5) {
    throw new Error(`${label}: status contrast below AA ${JSON.stringify(status)}`);
  }
}

async function assertSearchMatch(page, inputSelector, rowSelector, emptySelector, query, label) {
  await page.locator(inputSelector).fill(query);
  const result = await page.evaluate(({ rowSelector, emptySelector }) => {
    const rows = [...document.querySelectorAll(rowSelector)];
    const visibleRows = rows.filter((row) => row instanceof HTMLElement && !row.hidden);
    const empty = document.querySelector(emptySelector);
    return {
      totalRows: rows.length,
      visibleRows: visibleRows.length,
      visibleTexts: visibleRows.map((row) => row.textContent?.trim() ?? ""),
      emptyHidden: empty instanceof HTMLElement ? empty.hidden : null,
    };
  }, { rowSelector, emptySelector });
  if (result.totalRows < 1 || result.visibleRows < 1) {
    throw new Error(`${label}: populated match did not stay visible ${JSON.stringify(result)}`);
  }
  if (result.emptyHidden !== true) {
    throw new Error(`${label}: empty state appeared during a positive match ${JSON.stringify(result)}`);
  }
}

async function assertSearchNoMatch(page, inputSelector, rowSelector, emptySelector, query, label) {
  await page.locator(inputSelector).fill(query);
  const result = await page.evaluate(({ rowSelector, emptySelector }) => {
    const rows = [...document.querySelectorAll(rowSelector)];
    const visibleRows = rows.filter((row) => row instanceof HTMLElement && !row.hidden);
    const empty = document.querySelector(emptySelector);
    return {
      totalRows: rows.length,
      visibleRows: visibleRows.length,
      emptyHidden: empty instanceof HTMLElement ? empty.hidden : null,
      emptyText: empty?.textContent?.trim() ?? "",
    };
  }, { rowSelector, emptySelector });
  const rowsWereUnmounted = result.totalRows === 0;
  const rowsWereHidden = result.totalRows > 0 && result.visibleRows === 0;
  if (!rowsWereUnmounted && !rowsWereHidden) {
    throw new Error(`${label}: no-match search did not remove or hide populated rows ${JSON.stringify(result)}`);
  }
  if (result.emptyHidden !== false) {
    throw new Error(`${label}: empty state did not appear after filtering ${JSON.stringify(result)}`);
  }
}

async function clearSearch(page, inputSelector) {
  await page.locator(inputSelector).fill("");
}

async function assertMobileStorageCardLayout(page, label) {
  const result = await page.evaluate(() => {
    const row = document.querySelector("#storage-save-list .dm-desk__row");
    if (!(row instanceof HTMLElement)) return { ok: false, reason: "missing-row" };
    const rowRect = row.getBoundingClientRect();
    const children = [...row.children].map((child) => {
      const rect = child.getBoundingClientRect();
      return {
        text: child.textContent?.trim() ?? "",
        left: rect.left,
        top: rect.top,
        right: rect.right,
        bottom: rect.bottom,
      };
    });
    const overlaps = [];
    for (let index = 0; index < children.length; index += 1) {
      for (let other = index + 1; other < children.length; other += 1) {
        const a = children[index];
        const b = children[other];
        if (a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top) overlaps.push([a.text, b.text]);
      }
    }
    const escapes = children.filter((child) => (
      child.left < rowRect.left - 0.5
      || child.top < rowRect.top - 0.5
      || child.right > rowRect.right + 0.5
      || child.bottom > rowRect.bottom + 0.5
    ));
    return { ok: overlaps.length === 0 && escapes.length === 0, overlaps, escapes, count: children.length };
  });
  if (!result.ok) throw new Error(`${label}: storage card children overlap or escape ${JSON.stringify(result)}`);
}

async function assertSingleActiveSidebarItem(page, target, label) {
  const result = await page.evaluate((screenTarget) => {
    const buttons = [...document.querySelectorAll("[data-screen-target]")].map((button) => ({
      target: button.getAttribute("data-screen-target"),
      pressed: button.getAttribute("aria-pressed"),
      active: button.classList.contains("is-active"),
    }));
    const activeButtons = buttons.filter((button) => button.active || button.pressed === "true");
    return { buttons, activeButtons, screenTarget };
  }, target);
  if (result.activeButtons.length !== 1 || result.activeButtons[0]?.target !== target) {
    throw new Error(`${label}: expected exactly one active sidebar item ${JSON.stringify(result)}`);
  }
}

async function assertModalFocusTrap(page, { open, close, dialogSelector, firstFocusable, lastFocusable, returnTo, inertSelector, label }) {
  let lastError;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    try {
      await open();
      await page.waitForFunction((selector) => document.querySelector(selector) === document.activeElement, firstFocusable, { timeout: 3_000 });
      await page.waitForTimeout(80);
      await page.waitForFunction((selector) => document.querySelector(selector) === document.activeElement, firstFocusable, { timeout: 3_000 });
      const inertState = await page.locator(inertSelector).evaluate((element) => element instanceof HTMLElement && element.inert);
      if (!inertState) throw new Error(`${label}: background did not become inert`);
      await page.keyboard.press("Shift+Tab");
      if (!(await page.locator(lastFocusable).evaluate((element) => element === document.activeElement))) {
        throw new Error(`${label}: Shift+Tab did not loop to the last control`);
      }
      await page.keyboard.press("Tab");
      if (!(await page.locator(firstFocusable).evaluate((element) => element === document.activeElement))) {
        throw new Error(`${label}: Tab did not loop back to the first control`);
      }
      await close();
      await page.waitForFunction((selector) => !document.querySelector(selector) || document.querySelector(selector)?.getAttribute("aria-hidden") === "true", dialogSelector, { timeout: 3_000 }).catch(() => {});
      if (!(await page.locator(returnTo).evaluate((element) => element === document.activeElement))) {
        throw new Error(`${label}: focus did not return to the invoker`);
      }
      return;
    } catch (error) {
      lastError = error;
      await page.keyboard.press("Escape").catch(() => {});
      await close().catch(() => {});
    }
  }
  throw lastError ?? new Error(`${label}: focus-trap assertion failed`);
}

async function assertRepresentativeContrast(page, label) {
  const audited = await page.evaluate(() => {
    const selectors = [
      { selector: ".dm-sidebar__count", min: 4.5 },
      { selector: ".dm-desk__recent-note, #desk-search-empty", min: 4.5 },
      { selector: ".dm-storage__stat", min: 4.5 },
      { selector: "#final-save-dialog-state", min: 4.5 },
      { selector: "#final-save-dialog .dm-savewarn__summary span", min: 4.5 },
      { selector: ".dm-progress-dialog__stats span", min: 4.5 },
      { selector: ".dm-progress-dialog__stats strong", min: 4.5 },
      { selector: ".dm-save-success__file dt", min: 4.5 },
      { selector: ".dm-save-success__file dd", min: 4.5 },
    ];
    const parseRgb = (value) => {
      const match = value.match(/\d+(\.\d+)?/g) ?? [];
      return match.slice(0, 3).map((channel) => Number(channel) / 255);
    };
    const luminance = ([r, g, b]) => {
      const linear = [r, g, b].map((channel) => (channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4));
      return (0.2126 * linear[0]) + (0.7152 * linear[1]) + (0.0722 * linear[2]);
    };
    const effectiveBackground = (element) => {
      let current = element;
      while (current && current !== document.documentElement) {
        const color = getComputedStyle(current).backgroundColor;
        if (!/rgba?\(0,\s*0,\s*0,\s*0\)/.test(color) && color !== "transparent") return color;
        current = current.parentElement;
      }
      return getComputedStyle(document.documentElement).backgroundColor;
    };
    return selectors.flatMap(({ selector, min }) => {
      const element = document.querySelector(selector);
      if (!(element instanceof HTMLElement)) return [];
      const style = getComputedStyle(element);
      const bg = effectiveBackground(element);
      const lighter = Math.max(luminance(parseRgb(style.color)), luminance(parseRgb(bg)));
      const darker = Math.min(luminance(parseRgb(style.color)), luminance(parseRgb(bg)));
      return [{
        selector,
        text: element.textContent?.trim() ?? "",
        color: style.color,
        backgroundColor: bg,
        contrast: (lighter + 0.05) / (darker + 0.05),
        min,
      }];
    });
  });
  const failures = audited.filter((entry) => entry.contrast < entry.min);
  if (failures.length > 0) throw new Error(`${label}: representative contrast below AA ${JSON.stringify(failures)}`);
}

async function assertPdfCanvasNeutral(page, label) {
  for (const selector of ["#pdf-canvas-orig", "#pdf-canvas-result"]) {
    const geometry = await computedGeometry(page.locator(selector));
    if (geometry.filter !== "none" || geometry.mixBlendMode !== "normal" || geometry.opacity !== "1") {
      throw new Error(`${label}/${selector}: theme altered PDF canvas CSS ${JSON.stringify(geometry)}`);
    }
  }
}

async function dragMaskBox(page) {
  const box = await page.locator("#pdf-canvas-result").boundingBox();
  if (!box) throw new Error("manual-dirty: PDF canvas has no bounding box");
  const start = { x: box.x + box.width * 0.14, y: box.y + box.height * 0.14 };
  const end = { x: box.x + box.width * 0.48, y: box.y + box.height * 0.28 };
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 8 });
  await page.mouse.up();
}

async function dragGeometryBox(page, geometryOverlay) {
  const box = await page.locator("#overlay-canvas-result").boundingBox();
  if (!box) throw new Error("geometry-review: PDF overlay has no bounding box");
  const xScale = box.width / geometryOverlay.width;
  const yScale = box.height / geometryOverlay.height;
  const padding = 6;
  const start = {
    x: box.x + Math.max(0, geometryOverlay.bounds.x0 - padding) * xScale,
    y: box.y + Math.max(0, geometryOverlay.bounds.y0 - padding) * yScale,
  };
  const end = {
    x: box.x + Math.min(geometryOverlay.width, geometryOverlay.bounds.x1 + padding) * xScale,
    y: box.y + Math.min(geometryOverlay.height, geometryOverlay.bounds.y1 + padding) * yScale,
  };
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 8 });
  await page.mouse.up();
}

async function dragIncompleteGeometryBox(page, geometryOverlay) {
  const box = await page.locator("#overlay-canvas-result").boundingBox();
  if (!box) throw new Error("geometry-review: PDF overlay has no bounding box");
  const xScale = box.width / geometryOverlay.width;
  const yScale = box.height / geometryOverlay.height;
  const width = Math.max(8, Math.min(20, (geometryOverlay.candidateBounds.x1 - geometryOverlay.candidateBounds.x0) * 0.12));
  const height = Math.max(8, Math.min(20, (geometryOverlay.candidateBounds.y1 - geometryOverlay.candidateBounds.y0) * 0.12));
  const start = {
    x: box.x + (geometryOverlay.candidateBounds.x0 + 2) * xScale,
    y: box.y + (geometryOverlay.candidateBounds.y0 + 2) * yScale,
  };
  const end = { x: start.x + width * xScale, y: start.y + height * yScale };
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 4 });
  await page.mouse.up();
}

async function readGeometryOverlay(page) {
  return page.locator("#overlay-canvas-result").evaluate((canvas) => {
    if (!(canvas instanceof HTMLCanvasElement)) throw new Error("geometry-review: PDF overlay is unavailable");
    const pixels = canvas.getContext("2d")?.getImageData(0, 0, canvas.width, canvas.height).data;
    if (!pixels) throw new Error("geometry-review: PDF overlay pixels are unavailable");
    const candidateBounds = { x0: canvas.width, y0: canvas.height, x1: -1, y1: -1 };
    const occurrenceBounds = { x0: canvas.width, y0: canvas.height, x1: -1, y1: -1 };
    let candidates = 0;
    let occurrences = 0;
    const include = (bounds, pixel) => {
      const x = pixel % canvas.width;
      const y = Math.floor(pixel / canvas.width);
      bounds.x0 = Math.min(bounds.x0, x);
      bounds.y0 = Math.min(bounds.y0, y);
      bounds.x1 = Math.max(bounds.x1, x);
      bounds.y1 = Math.max(bounds.y1, y);
    };
    for (let index = 0; index < pixels.length; index += 4) {
      const red = pixels[index] ?? 0;
      const green = pixels[index + 1] ?? 0;
      const blue = pixels[index + 2] ?? 0;
      const pixel = index / 4;
      if (red < 20 && green >= 90 && green <= 145 && blue >= 70 && blue <= 125) {
        candidates += 1;
        include(candidateBounds, pixel);
      }
      if (red >= 150 && red <= 205 && green >= 55 && green <= 110 && blue < 45) {
        occurrences += 1;
        include(occurrenceBounds, pixel);
      }
    }
    if (candidates === 0 || occurrences === 0) return { width: canvas.width, height: canvas.height, candidates, occurrences };
    return {
      width: canvas.width,
      height: canvas.height,
      candidates,
      occurrences,
      candidateBounds,
      occurrenceBounds,
      bounds: {
        x0: Math.min(candidateBounds.x0, occurrenceBounds.x0),
        y0: Math.min(candidateBounds.y0, occurrenceBounds.y0),
        x1: Math.max(candidateBounds.x1, occurrenceBounds.x1),
        y1: Math.max(candidateBounds.y1, occurrenceBounds.y1),
      },
    };
  });
}

async function readReviewSurfaceCounts(page) {
  const surface = await page.evaluate(() => ({
    summary: document.querySelector("#review-summary-banner")?.textContent?.trim() ?? "",
    total: document.querySelector("#review-total-count")?.textContent?.trim() ?? "",
    all: document.querySelector("#review-filter-all-count")?.textContent?.trim() ?? "",
    pending: document.querySelector("#review-filter-pending-count")?.textContent?.trim() ?? "",
    resolved: document.querySelector("#review-filter-resolved-count")?.textContent?.trim() ?? "",
  }));
  const summary = /^자동 (\d+)건 · 수동 (\d+)건\(저장 시 적용\) · 검토 필요 (\d+)건$/.exec(surface.summary);
  const count = (value, label) => {
    const match = /^(\d+)건?$/.exec(value);
    if (!match) throw new Error(`review surface has invalid ${label} count (${value})`);
    return Number(match[1]);
  };
  if (!summary) throw new Error(`review surface has invalid summary (${surface.summary})`);
  const counts = {
    autoMasked: Number(summary[1]),
    manualMasked: Number(summary[2]),
    all: count(surface.all, "all"),
    pending: count(surface.pending, "pending"),
    resolved: count(surface.resolved, "resolved"),
    total: count(surface.total, "total"),
  };
  if (counts.all !== counts.total || counts.total !== counts.pending + counts.resolved || counts.pending !== Number(summary[3])) {
    throw new Error(`review surface counts drifted (${JSON.stringify({ surface, counts })})`);
  }
  return counts;
}

async function captureState(page, theme, state, viewports, screenshots, screenshotPathsSet, beforeCapture) {
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    await page.evaluate(() => window.scrollTo(0, 0));
    if (beforeCapture) await beforeCapture(viewport);
    await page.waitForTimeout(120);
    await assertNoHorizontalOverflow(page, `${theme}/${viewport.name}/${state}`);
    const shotPath = path.join(evidenceDir, `${theme}-${viewport.name}-${state}.png`);
    if (screenshotPathsSet.has(shotPath)) throw new Error(`duplicate screenshot path attempted: ${shotPath}`);
    await page.screenshot({ path: shotPath, fullPage: false });
    screenshotPathsSet.add(shotPath);
    screenshots.push(shotPath);
    console.log(`[capture] ${theme}/${viewport.name}/${state} -> ${path.relative(repoRoot, shotPath)}`);
  }
  await page.setViewportSize(desktopViewport);
  await page.evaluate(() => window.scrollTo(0, 0));
}

async function openThemePage(browser, theme, errors, options = {}) {
  const page = await browser.newPage({ viewport: desktopViewport });
  capturePageErrors(page, errors);
  await installTauriQaMocks(page, {
    fixturePath,
    outputDir: path.join(evidenceDir, "output"),
    pdfBytes,
    failBatchItemOnce: options.failBatchItemOnce === true,
    analyzeDelayMs: options.analyzeDelayMs ?? 0,
    reviewScenario: options.reviewScenario ?? "default",
  });
  await page.addInitScript(({ selectedTheme, profile }) => {
    const settings = profile === null ? { theme: selectedTheme } : { theme: selectedTheme, profile };
    localStorage.setItem("makiiing-v2-settings", JSON.stringify(settings));
  }, { selectedTheme: theme, profile: options.profile === undefined ? "official_dispatch" : options.profile });
  await page.goto(url, { waitUntil: "networkidle" });
  await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
  await assertTheme(page, theme, theme, theme === "light" ? "#f4f5f6" : "#11151c");
  return page;
}

async function runThemeVisualAudit(browser, theme, screenshots, screenshotPathsSet, errors) {
  const page = await openThemePage(browser, theme, errors, { analyzeDelayMs: 3_000 });
  try {
    await activateScreen(page, "desk");
    await assertSingleActiveSidebarItem(page, "desk", `${theme}/desk-active`);
    await captureState(page, theme, "document-desk", evidenceViewports, screenshots, screenshotPathsSet);

    await activateScreen(page, "storage");
    await captureState(page, theme, "storage-empty", evidenceViewports, screenshots, screenshotPathsSet);

    await activateScreen(page, "documents");
    if (await page.locator("#btn-new-document").isVisible()) {
      throw new Error(`${theme}/workspace-empty: new-work action must stay hidden before final save`);
    }
    await captureState(page, theme, "workspace-empty", evidenceViewports, screenshots, screenshotPathsSet, async (viewport) => {
      if (viewport.name === "mobile") {
        await assertVerticallyInsideViewport(page, page.locator(".dm-canvas__hero-title"), `${theme}/mobile/hero-title`);
        await assertVerticallyInsideViewport(page, page.locator(".dm-canvas__hero-cta"), `${theme}/mobile/hero-cta`);
      }
    });
    await assertWorkspaceGeometry(page, `${theme}/workspace-empty`);
    if (await page.locator("#canvas-tool-menu").isVisible()) throw new Error(`${theme}/workspace-empty: tool menu trigger must stay hidden`);
    if (!(await page.locator("#inspector-empty-guide").isVisible())) throw new Error(`${theme}/workspace-empty: inspector guide is not visible`);
    if (await page.locator("#btn-canvas-tool-mask").count() !== 1) throw new Error(`${theme}/workspace-empty: hidden tool DOM was removed`);

    await activateScreen(page, "masking-settings");
    const maskingSettingsScroll = page.locator("#masking-settings-screen .dm-settings-scroll");
    await maskingSettingsScroll.evaluate((element) => { element.scrollTop = 0; });
    await captureState(page, theme, "masking-settings-top", evidenceViewports, screenshots, screenshotPathsSet, async () => {
      await maskingSettingsScroll.evaluate((element) => { element.scrollTop = 0; });
    });
    await assertSettingsGeometry(page, ".dm-settings-grid--masking", `${theme}/masking-settings`);
    await captureState(page, theme, "masking-settings-scrolled", evidenceViewports, screenshots, screenshotPathsSet, async () => {
      await maskingSettingsScroll.evaluate((element) => { element.scrollTop = element.scrollHeight; });
    });

    await activateScreen(page, "settings");
    await captureState(page, theme, "general-settings", evidenceViewports, screenshots, screenshotPathsSet);
    await assertSettingsGeometry(page, ".dm-settings-grid--app", `${theme}/general-settings`);

    await activateScreen(page, "masking-settings");
    await page.locator("#profile").selectOption("official_dispatch");
    await page.locator("#btn-masking-settings-apply").click();
    await activateScreen(page, "documents");
    await page.locator("#btn-pick-pdf").click();
    await page.locator("#canvas-wrap-result.has-rendered-pdf").waitFor({ state: "attached", timeout: 8_000 });
    await captureState(page, theme, "workspace-loaded", evidenceViewports, screenshots, screenshotPathsSet, async (viewport) => {
      if (viewport.name === "mobile") {
        await assertHorizontallyInsideViewport(page, page.locator(".dm-canvas__sync"), `${theme}/mobile/page-sync`);
      }
    });
    await assertStatusToastReadable(page, `${theme}/workspace-loaded/status`);
    await assertWorkspaceGeometry(page, `${theme}/workspace-loaded`);
    await assertPdfCanvasNeutral(page, `${theme}/workspace-loaded`);
    if (await page.locator(".dm-canvas__batch").isVisible()) throw new Error(`${theme}/workspace-loaded: empty batch disclosure is visible`);
    const pdfFingerprint = await page.locator("#pdf-canvas-orig").evaluate((canvas) => canvas.toDataURL());
    await activateScreen(page, "desk");
    await assertSearchMatch(page, "#desk-search-input", "#desk-recent-list .dm-desk__row", "#desk-search-empty", "phase6_non_sensitive", `${theme}/desk-search-positive`);
    await assertSearchNoMatch(page, "#desk-search-input", "#desk-recent-list .dm-desk__row", "#desk-search-empty", "__qa-no-match__", `${theme}/desk-search-negative`);
    await clearSearch(page, "#desk-search-input");
    await activateScreen(page, "documents");

    const toolSegment = page.locator("#canvas-tool-menu");
    if (!(await toolSegment.isVisible())) throw new Error(`${theme}/tool-segment: not visible after loading a PDF`);
    const toolButtons = toolSegment.locator("button[data-canvas-tool]");
    if (await toolButtons.count() !== 5) throw new Error(`${theme}/tool-segment: expected five correction tools`);
    for (const tool of await toolButtons.all()) {
      if (!(await tool.isVisible())) throw new Error(`${theme}/tool-segment: a correction tool is hidden`);
    }
    const maskTool = page.locator("#btn-canvas-tool-mask");
    const restoreTool = page.locator("#btn-canvas-tool-restore");
    await maskTool.focus();
    await page.keyboard.press("ArrowRight");
    if (!(await restoreTool.evaluate((element) => element === document.activeElement))) {
      throw new Error(`${theme}/tool-segment: ArrowRight did not move focus to the next tool`);
    }
    await page.locator("#btn-canvas-tool-select").click();
    if ((await page.locator("#btn-canvas-tool-select").getAttribute("aria-pressed")) !== "true") {
      throw new Error(`${theme}/tool-segment: selected tool did not become active`);
    }
    await captureState(page, theme, "toolbar-segment-active", evidenceViewports, screenshots, screenshotPathsSet);
    await maskTool.click();
    if ((await maskTool.getAttribute("aria-pressed")) !== "true") {
      throw new Error(`${theme}/tool-segment: mask tool did not reactivate`);
    }

    const viewMenu = page.locator("#canvas-view-menu");
    await page.locator("#canvas-view-menu-trigger").click();
    if (!(await viewMenu.evaluate((element) => element.open))) throw new Error(`${theme}/view-menu: did not open`);
    await captureState(page, theme, "view-dropdown-open", evidenceViewports, screenshots, screenshotPathsSet);
    await page.keyboard.press("Escape");
    if (await viewMenu.evaluate((element) => element.open)) throw new Error(`${theme}/view-menu: Escape did not close`);
    if (!(await page.locator("#canvas-view-menu-trigger").evaluate((element) => element === document.activeElement))) {
      throw new Error(`${theme}/view-menu: Escape did not restore trigger focus`);
    }
    await page.keyboard.press("ArrowDown");
    if (!(await page.locator("#toggle-original-compare").evaluate((element) => element === document.activeElement))) {
      throw new Error(`${theme}/view-menu: ArrowDown did not focus compare toggle`);
    }
    await page.keyboard.press("Space");
    if (!(await page.locator("#toggle-original-compare").isChecked())) throw new Error(`${theme}/view-menu: Space did not toggle compare on`);
    await page.keyboard.press("Space");
    if (await page.locator("#toggle-original-compare").isChecked()) throw new Error(`${theme}/view-menu: Space did not toggle compare off`);
    if ((await page.locator("#toggle-original-compare").getAttribute("aria-checked")) !== "false") {
      throw new Error(`${theme}/view-menu: checked and aria-checked diverged`);
    }
    await page.keyboard.press("Escape");
    if (await viewMenu.evaluate((element) => element.open)) throw new Error(`${theme}/view-menu: keyboard path did not close`);

    await page.evaluate(() => {
      const detected = document.querySelector("#masking-progress-detected");
      const elapsed = document.querySelector("#masking-progress-elapsed");
      const stage = document.querySelector("#masking-progress-pages");
      if (detected) detected.textContent = "99건";
      if (elapsed) elapsed.textContent = "99초";
      if (stage) stage.textContent = "99 / 99페이지";
    });
    await page.locator("#btn-run-masking").click();
    await page.locator("#masking-progress-dialog").waitFor({ state: "visible", timeout: 8_000 });
    await page.waitForFunction(
      () => Boolean(document.activeElement?.closest?.("#masking-progress-dialog")),
      null,
      { timeout: 2_000 },
    );
    if (!(await page.evaluate(() => Boolean(document.activeElement?.closest?.("#masking-progress-dialog"))))) {
      throw new Error(`${theme}/masking-progress: focus did not enter the modal`);
    }
    const progressSummary = await page.evaluate(() => ({
      pages: document.querySelector("#masking-progress-pages")?.textContent?.trim() ?? "",
      detected: document.querySelector("#masking-progress-detected")?.textContent?.trim() ?? "",
      elapsed: document.querySelector("#masking-progress-elapsed")?.textContent?.trim() ?? "",
    }));
    if (!/0\s*\/\s*2페이지/.test(progressSummary.pages) || progressSummary.detected !== "0건" || progressSummary.elapsed !== "0초") {
      throw new Error(`${theme}/masking-progress: incomplete live stats ${JSON.stringify(progressSummary)}`);
    }
    await page.waitForTimeout(1100);
    const progressedElapsed = await page.locator("#masking-progress-elapsed").textContent();
    if (progressedElapsed === "0초") throw new Error(`${theme}/masking-progress: elapsed time did not advance`);
    await captureState(page, theme, "masking-progress", evidenceViewports, screenshots, screenshotPathsSet);
    await page.keyboard.press("Escape");
    await page.locator("#masking-progress-dialog").waitFor({ state: "hidden", timeout: 8_000 });
    await page.waitForFunction(
      () => !document.activeElement?.closest?.("#masking-progress-dialog"),
      null,
      { timeout: 2_000 },
    );
    if (await page.evaluate(() => Boolean(document.activeElement?.closest?.("#masking-progress-dialog")))) {
      throw new Error(`${theme}/masking-progress-dismissed: focus remained trapped in the hidden modal`);
    }
    await assertStatusToastReadable(page, `${theme}/masking-progress-dismissed/status`);
    if (!(await page.locator("#status").textContent())?.includes("백그라운드")) {
      throw new Error(`${theme}/masking-progress-dismissed: background-run status not surfaced`);
    }
    await captureState(page, theme, "masking-progress-background", evidenceViewports, screenshots, screenshotPathsSet);
    await page.waitForFunction(
      () => window.__QA_INVOKES__.filter((entry) => entry.cmd === "analyze_masking_run").length === 1,
      null,
      { timeout: 15_000 },
    );
    await page.waitForFunction(
      () => document.querySelector("#btn-run-masking")?.getAttribute("data-running") !== "true",
      null,
      { timeout: 8_000 },
    );
    if (!(await page.locator("#final-state-card").isVisible())) {
      const state = await page.evaluate(() => ({
        profile: document.querySelector("#profile")?.value,
        status: document.querySelector("#status")?.textContent,
        finalState: document.querySelector("#final-state-card")?.getAttribute("data-state"),
        invokes: window.__QA_INVOKES__,
      }));
      throw new Error(`${theme}/inspector: review accordion stayed hidden after masking ${JSON.stringify(state)}`);
    }
    if (!(await page.locator("#final-state-card").evaluate((element) => element.open))) throw new Error(`${theme}/inspector: review accordion is not open by default`);
    if (await page.locator("#canvas-box-accordion").evaluate((element) => element.open)) throw new Error(`${theme}/inspector: box accordion is not closed by default`);
    if ((await page.locator("#sidebar-review-pending-count").textContent())?.trim() !== "1") throw new Error(`${theme}/review-queue: pending count did not rise to 1`);
    await page.locator('[data-screen-target="review-queue"]').click();
    await assertSingleActiveSidebarItem(page, "review-queue", `${theme}/review-queue-active`);
    await page.waitForFunction(() => Boolean(document.activeElement?.closest?.("#final-state-card")), null, { timeout: 2_000 });
    await captureState(page, theme, "inspector-accordion", evidenceViewports, screenshots, screenshotPathsSet);

    await dragMaskBox(page);
    await applyPublicManualMask(page, theme);
    const initialOverlay = await readPublicDetectionOverlay(page);
    assertOverlayBounds(initialOverlay, `${theme}/public-detection-overlay`);
    if (!isOpaqueBlack(initialOverlay.confirmed.center.rgba)) {
      throw new Error(`${theme}/public-detection-overlay: confirmed occurrence is not filled black ${JSON.stringify(initialOverlay.confirmed)}`);
    }
    if (isOpaqueBlack(initialOverlay.pending.outline) || initialOverlay.pending.outline[3] === 0) {
      throw new Error(`${theme}/public-detection-overlay: pending occurrence has no outlined highlight ${JSON.stringify(initialOverlay.pending)}`);
    }
    await page.locator("#btn-next-orig").click();
    await page.waitForFunction(() => document.querySelector("#viewer-meta-result")?.textContent?.trim() === "2/2", null, { timeout: 8_000 });
    const secondPageOverlay = await readPublicDetectionOverlay(page);
    if (secondPageOverlay.pending.outline[3] !== 0 || secondPageOverlay.confirmed.center.rgba[3] !== 0) {
      throw new Error(`${theme}/public-detection-page-change: first-page occurrences leaked onto page two ${JSON.stringify(secondPageOverlay)}`);
    }
    await page.locator("#btn-prev-orig").click();
    await page.waitForFunction(() => document.querySelector("#viewer-meta-result")?.textContent?.trim() === "1/2", null, { timeout: 8_000 });
    const pageReturnOverlay = await readPublicDetectionOverlay(page);
    if (!isOpaqueBlack(pageReturnOverlay.confirmed.center.rgba) || pageReturnOverlay.pending.outline[3] === 0) {
      throw new Error(`${theme}/public-detection-page-change: first-page occurrences did not redraw ${JSON.stringify(pageReturnOverlay)}`);
    }
    const initialScale = pageReturnOverlay.scale;
    await page.locator("#btn-canvas-zoom-in").click();
    await page.waitForFunction((scale) => document.querySelector("#zoom-info")?.textContent?.trim() !== `${Math.round(scale * 100)}%`, initialScale, { timeout: 8_000 });
    const zoomedOverlay = await readPublicDetectionOverlay(page);
    assertOverlayBounds(zoomedOverlay, `${theme}/public-detection-zoom`);
    if (zoomedOverlay.scale <= initialScale || !isOpaqueBlack(zoomedOverlay.confirmed.center.rgba) || zoomedOverlay.pending.outline[3] === 0) {
      throw new Error(`${theme}/public-detection-zoom: occurrence coordinates did not redraw with zoom ${JSON.stringify({ initialScale, zoomedOverlay })}`);
    }
    const readAccentEdge = (bounds) => page.evaluate(({ x0, y0, y1 }) => {
      const overlay = document.querySelector("#overlay-canvas-result");
      if (!(overlay instanceof HTMLCanvasElement)) throw new Error("detection overlay canvas is unavailable");
      const context = overlay.getContext("2d");
      if (!context) throw new Error("detection overlay context is unavailable");
      const accent = getComputedStyle(document.documentElement).getPropertyValue("--dm-accent").trim().match(/^#([0-9a-f]{6})$/i);
      if (!accent) throw new Error("detection overlay accent token is unavailable");
      const expected = [
        Number.parseInt(accent[1].slice(0, 2), 16),
        Number.parseInt(accent[1].slice(2, 4), 16),
        Number.parseInt(accent[1].slice(4, 6), 16),
      ];
      const centerY = Math.round((y0 + y1) / 2);
      let count = 0;
      for (let x = Math.max(0, Math.round(x0) - 2); x <= Math.min(overlay.width - 1, Math.round(x0) + 2); x += 1) {
        for (let y = Math.max(0, centerY - 2); y <= Math.min(overlay.height - 1, centerY + 2); y += 1) {
          const rgba = [...context.getImageData(x, y, 1, 1).data];
          const colorDistance = Math.abs(rgba[0] - expected[0]) + Math.abs(rgba[1] - expected[1]) + Math.abs(rgba[2] - expected[2]);
          if (rgba[3] > 0 && colorDistance < 12) count += 1;
        }
      }
      return { count };
    }, bounds);
    const preHoverAccent = await readAccentEdge(zoomedOverlay.pending.bounds);
    if (preHoverAccent.count !== 0) {
      throw new Error(`${theme}/public-detection-overlay: pending occurrence already has focus accent before hover ${JSON.stringify(preHoverAccent)}`);
    }
    await page.locator('.dm-detect__item[data-review-id="review-1"]').hover();
    await page.waitForFunction(({ x0, y0, y1 }) => {
      const overlay = document.querySelector("#overlay-canvas-result");
      if (!(overlay instanceof HTMLCanvasElement)) return null;
      const context = overlay.getContext("2d");
      if (!context) return null;
      const accent = getComputedStyle(document.documentElement).getPropertyValue("--dm-accent").trim().match(/^#([0-9a-f]{6})$/i);
      if (!accent) return null;
      const expected = [
        Number.parseInt(accent[1].slice(0, 2), 16),
        Number.parseInt(accent[1].slice(2, 4), 16),
        Number.parseInt(accent[1].slice(4, 6), 16),
      ];
      const centerY = Math.round((y0 + y1) / 2);
      let count = 0;
      for (let x = Math.max(0, Math.round(x0) - 2); x <= Math.min(overlay.width - 1, Math.round(x0) + 2); x += 1) {
        for (let y = Math.max(0, centerY - 2); y <= Math.min(overlay.height - 1, centerY + 2); y += 1) {
          const rgba = [...context.getImageData(x, y, 1, 1).data];
          const colorDistance = Math.abs(rgba[0] - expected[0]) + Math.abs(rgba[1] - expected[1]) + Math.abs(rgba[2] - expected[2]);
          if (rgba[3] > 0 && colorDistance < 12) count += 1;
        }
      }
      return count > 0 ? { count } : null;
    }, zoomedOverlay.pending.bounds, { timeout: 2_000 });
    await captureState(page, theme, "public-session-locked", evidenceViewports, screenshots, screenshotPathsSet);

    await assertModalFocusTrap(page, {
      open: async () => {
        await page.locator("#btn-open-keyword-dialog").click();
        await page.locator("#keyword-dialog").waitFor({ state: "visible", timeout: 8_000 });
      },
      close: async () => {
        await page.keyboard.press("Escape");
        await page.locator("#keyword-dialog").waitFor({ state: "hidden", timeout: 8_000 });
      },
      dialogSelector: "#keyword-dialog",
      firstFocusable: "#keyword-entry-input",
      lastFocusable: "#btn-keyword-dialog-apply",
      returnTo: "#btn-open-keyword-dialog",
      inertSelector: ".dm-canvas__toolbar",
      label: `${theme}/keyword-modal-focus`,
    });
    await page.locator("#btn-open-keyword-dialog").click();
    await page.locator("#keyword-dialog").waitFor({ state: "visible", timeout: 8_000 });
    await page.locator("#keyword-entry-input").fill("서울시청");
    await page.locator("#keyword-entry-input").press("Enter");
    await page.locator("#keyword-entry-input").fill("서울시청");
    await page.locator("#keyword-entry-input").press("Enter");
    const keywordChips = await page.locator("#keyword-dialog-chip-list span").allTextContents();
    if (keywordChips.filter((value) => value.includes("서울시청")).length !== 1) {
      throw new Error(`${theme}/keyword-modal: duplicate keyword chip rendered ${JSON.stringify(keywordChips)}`);
    }
    await captureState(page, theme, "keyword-modal", evidenceViewports, screenshots, screenshotPathsSet);
    await page.locator("#btn-keyword-dialog-apply").click();
    await page.waitForFunction(
      () => {
        const analyses = window.__QA_INVOKES__.filter((entry) => entry.cmd === "analyze_masking_run");
        if (analyses.length !== 2) return false;
        const keywordAnalysis = analyses[1]?.payload?.request;
        if (
          keywordAnalysis?.profile !== "official_dispatch"
          || keywordAnalysis?.optionsProfile !== "official_dispatch"
          || keywordAnalysis?.hasCustomKeyword !== true
        ) return false;
        const overlay = document.querySelector("#overlay-canvas-result");
        if (!(overlay instanceof HTMLCanvasElement)) return false;
        const context = overlay.getContext("2d");
        if (!context) return false;
        const scale = overlay.width / 420;
        const x = Math.round((72 + (200 - 72) / 2) * scale);
        const y = Math.round((108 + (130 - 108) / 2) * scale);
        const rgba = context.getImageData(x, y, 1, 1).data;
        return rgba[0] === 0 && rgba[1] === 0 && rgba[2] === 0 && rgba[3] === 255;
      },
      null,
      { timeout: 8_000 },
    );
    const keywordOverlay = await readPublicDetectionOverlay(page);
    assertOverlayBounds(keywordOverlay, `${theme}/keyword-detection-overlay`);
    if (!isOpaqueBlack(keywordOverlay.keyword.center.rgba)) {
      throw new Error(`${theme}/keyword-detection-overlay: keyword re-detection did not add a filled occurrence ${JSON.stringify(keywordOverlay.keyword)}`);
    }

    await assertFinalSaveControls(page, "#btn-save", true);
    await assertConfirmSaveWarning(
      page,
      `${theme}/public-review-confirm-save`,
      "미가림 가능성: 이름 · 1쪽 — 이름 또는 기관 탐지 결과를 확인해야 합니다.",
    );
    if (!(await page.locator('button[data-review-id="review-1"][data-review-action="mask"]').isVisible())) {
      throw new Error(`${theme}/public-review-confirm-save: typed resolution action is hidden`);
    }
    await assertWorkspaceCardGeometry(page, `${theme}/public-review-confirm-save`);
    await captureState(page, theme, "public-review-confirm-save", evidenceViewports, screenshots, screenshotPathsSet);
    await page.setViewportSize(desktopViewport);

    const resolveButton = page.locator('button[data-review-id="review-1"][data-review-action="mask"]');
    await resolveButton.click();
    await page.waitForFunction(
      () => window.__QA_INVOKES__.filter((entry) => entry.cmd === "resolve_masking_review").length === 1,
      null,
      { timeout: 8_000 },
    );
    const resolvedOverlay = await readPublicDetectionOverlay(page);
    if (!isOpaqueBlack(resolvedOverlay.pending.center.rgba)) {
      throw new Error(`${theme}/resolved-detection-overlay: review resolution did not fill the occurrence ${JSON.stringify(resolvedOverlay.pending)}`);
    }
    await assertFinalSaveControls(page, "#btn-save", true);
    if ((await page.locator("#sidebar-review-pending-count").textContent())?.trim() !== "0") throw new Error(`${theme}/review-queue: pending count did not clear after resolution`);
    await page.locator("#btn-save").click();
    await page.locator("#final-save-dialog").waitFor({ state: "visible", timeout: 8_000 });
    if (await page.locator("#final-save-warning-list .dm-savewarn__item").count() !== 0) {
      throw new Error(`${theme}/save-ready: controller retained blockers after server resolution`);
    }
    if (await page.locator("#final-save-dialog .dm-savewarn__summary").isVisible()) {
      throw new Error(`${theme}/save-ready: blocker card should be hidden without pending reviews`);
    }
    if (!(await page.locator("#final-save-dialog-state").isVisible())) {
      throw new Error(`${theme}/save-ready: ready message is hidden`);
    }
    await captureState(page, theme, "save-ready", evidenceViewports, screenshots, screenshotPathsSet, async (viewport) => {
      await assertSaveDialogGeometry(page, `${theme}/${viewport.name}/save-ready`);
    });
    await page.locator("#btn-dialog-save-all").click();
    try {
      await page.locator("#finalization-success-dialog").waitFor({ state: "visible", timeout: 8_000 });
    } catch {
      const diagnostic = await page.evaluate(() => ({
        status: document.querySelector("#status")?.textContent,
        invokes: window.__QA_INVOKES__,
      }));
      throw new Error(`${theme}/public-finalize: success dialog missing ${JSON.stringify(diagnostic)}`);
    }
    if (await page.locator("#final-save-dialog:visible").count() !== 0) {
      throw new Error(`${theme}/public-finalize: save dialog remained open after successful save`);
    }
    await assertSuccessDialogGeometry(page, `${theme}/public-finalize`);
    const successSummary = await page.evaluate(() => ({
      meta: document.querySelector("#final-save-result-meta")?.textContent?.trim() ?? "",
      openStorageLabel: document.querySelector("#btn-final-save-go-storage")?.textContent?.trim() ?? "",
      exportLabel: document.querySelector("#btn-final-save-open-file")?.textContent?.trim() ?? "",
    }));
    if (!/·/.test(successSummary.meta) || successSummary.meta.includes("/Users/") || successSummary.meta.includes(":\\")) {
      throw new Error(`${theme}/save-success: saved-file meta is missing or leaks a path ${JSON.stringify(successSummary)}`);
    }
    if (successSummary.openStorageLabel !== "저장함 열기" || successSummary.exportLabel !== "문서 내보내기") {
      throw new Error(`${theme}/save-success: completion action labels drifted ${JSON.stringify(successSummary)}`);
    }
    await assertRepresentativeContrast(page, `${theme}/save-success/contrast`);
    await captureState(page, theme, "save-success", evidenceViewports, screenshots, screenshotPathsSet, async (viewport) => {
      await assertSuccessDialogGeometry(page, `${theme}/${viewport.name}/save-success`);
    });
    await page.locator("#btn-final-save-open-file").click();
    const openerCalls = await page.evaluate(() => window.__QA_INVOKES__.filter((entry) => entry.cmd === "plugin:opener|open_path"));
    if (openerCalls.length !== 1 || !openerCalls[0]?.payload?.path?.hasValue || typeof openerCalls[0]?.payload?.path?.hash !== "string") {
      throw new Error(`${theme}/save-success: file opener did not record one privacy-safe invocation ${JSON.stringify(openerCalls)}`);
    }
    await page.locator("#btn-final-save-go-storage").click();
    await page.locator("#finalization-success-dialog").waitFor({ state: "hidden", timeout: 8_000 });
    await page.locator('[data-screen-panel="storage"].is-active').waitFor({ state: "visible", timeout: 8_000 });
    await captureState(page, theme, "storage-populated", evidenceViewports, screenshots, screenshotPathsSet, async (viewport) => {
      if (viewport.name === "mobile") await assertMobileStorageCardLayout(page, `${theme}/storage-populated-mobile`);
    });
    await assertSearchMatch(page, "#storage-search-input", "#storage-save-list .dm-desk__row", "#storage-search-empty", "phase6_non_sensitive", `${theme}/storage-search-positive`);
    await assertSearchNoMatch(page, "#storage-search-input", "#storage-save-list .dm-desk__row", "#storage-search-empty", "__qa-no-match__", `${theme}/storage-search-negative`);
    await clearSearch(page, "#storage-search-input");
    const enabledFinalControls = await page.locator("#btn-save:not(:disabled), #btn-canvas-final-save:not(:disabled)").count();
    if (enabledFinalControls !== 0) throw new Error(`${theme}/public-finalize: final save remained enabled after completion`);
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
  if (widths.scroll > widths.client + 1) throw new Error(`${label}/panel: horizontal overflow ${JSON.stringify(widths)}`);
  const actionBox = await action.boundingBox();
  const expectedHeight = page.viewportSize()?.width === 375 ? 44 : 32;
  if (!actionBox || actionBox.height < expectedHeight) {
    throw new Error(`${label}/action: expected at least ${expectedHeight}px, got ${actionBox?.height ?? 0}px`);
  }
  const outputStyle = await panel.locator(".batch-output").last().evaluate((element) => getComputedStyle(element).whiteSpace);
  if (outputStyle !== "normal") throw new Error(`${label}/output: expected wrapped failure/output text, got ${outputStyle}`);
}

async function runBatchVisualAudit(browser, theme, screenshots, screenshotPathsSet, errors) {
  const page = await openThemePage(browser, theme, errors, { failBatchItemOnce: true });
  try {
    await activateScreen(page, "documents");
    await page.locator("#profile").evaluate((element) => {
      element.value = "mixed";
      element.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await page.locator("#btn-pick-batch").click();
    await page.locator("#batch-queue .batch-item").nth(1).waitFor({ state: "attached", timeout: 8_000 });
    await page.locator(".dm-canvas__batch-summary").click();
    const batchButton = page.locator("#btn-run-batch");
    await batchButton.waitFor({ state: "visible", timeout: 8_000 });
    await captureState(page, theme, "batch-pending", batchViewports, screenshots, screenshotPathsSet, async (viewport) => {
      await assertBatchPanel(page, batchButton, `${theme}/${viewport.name}/batch-pending`);
    });
    await batchButton.click();
    await page.waitForFunction(() => Array.from(document.querySelectorAll("#batch-queue .batch-state"))
      .every((element) => element.textContent === "완료" || element.textContent === "실패"), { timeout: 15_000 });
    const retryButton = page.locator("#batch-queue .status-실패 .batch-actions button", { hasText: "재실행" });
    if (await retryButton.count() !== 1) throw new Error(`${theme}/batch-failed: expected one retry action`);
    await captureState(page, theme, "batch-failed", batchViewports, screenshots, screenshotPathsSet, async (viewport) => {
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

async function runDeskDefaultRoutingQa(browser, errors) {
  const page = await openThemePage(browser, "light", errors, { profile: null });
  const scenario = "desk-default-mixed-routing";
  try {
    await activateScreen(page, "desk");
    if (await page.locator("#desk-profile").count() !== 0) {
      throw new Error(`${scenario}: desk exposes a manual document-type selector`);
    }
    if (await page.locator("#profile").inputValue() !== "mixed") {
      throw new Error(`${scenario}: mixed routing is not the default profile`);
    }
    await page.locator("#btn-desk-open-pdf").click();
    await page.locator("#canvas-wrap-result.has-rendered-pdf").waitFor({ state: "attached", timeout: 8_000 });
    await page.locator("#btn-run-masking").click();
    await page.waitForFunction(
      () => window.__QA_INVOKES__.filter((entry) => entry.cmd === "analyze_masking_run").length === 1,
      null,
      { timeout: 15_000 },
    );
    await page.waitForFunction(
      () => document.querySelector("#btn-run-masking")?.getAttribute("data-running") !== "true",
      null,
      { timeout: 8_000 },
    );
    const request = await page.evaluate(() => window.__QA_INVOKES__.find((entry) => entry.cmd === "analyze_masking_run")?.payload.request);
    if (request?.profile !== "mixed" || request?.optionsProfile !== "mixed") {
      throw new Error(`${scenario}: expected mixed automatic routing request, got ${JSON.stringify(request)}`);
    }
    await activateScreen(page, "desk");
    const detected = await page.locator("#desk-stat-detected").textContent();
    if ((Number.parseInt(detected ?? "", 10) || 0) < 1) {
      throw new Error(`${scenario}: masking completed without a surfaced detection (${detected ?? ""})`);
    }
    console.log(`[qa] ${scenario}: desk opened and masked with mixed routing without a type prompt (${detected})`);
  } finally {
    await page.close();
  }
}

async function runGeometryAutoConfirmationQa(browser, errors) {
  const page = await openThemePage(browser, "light", errors, { reviewScenario: "geometry" });
  const scenario = "geometry-auto-confirm-save";
  try {
    await activateScreen(page, "documents");
    await page.locator("#profile").evaluate((element) => {
      element.value = "official_dispatch";
      element.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await page.locator("#btn-pick-pdf").click();
    await page.locator("#canvas-wrap-result.has-rendered-pdf").waitFor({ state: "attached", timeout: 8_000 });
    await page.locator("#btn-run-masking").click();
    await page.waitForFunction(() => (
      document.querySelector("#btn-run-masking")?.getAttribute("data-running") !== "true"
      && document.querySelector("#final-state-card")?.getAttribute("data-state") !== "running"
    ), null, { timeout: 8_000 });
    const pageNavigationBefore = await page.evaluate(() => ({
      pager: document.querySelector(".dm-canvas__pager-label")?.textContent?.trim() ?? "",
      ctaVisible: document.querySelector("#first-masking-location-cta") instanceof HTMLElement
        && !document.querySelector("#first-masking-location-cta")?.hasAttribute("hidden"),
      targetPage: document.querySelector("#first-masking-location-cta")?.getAttribute("data-target-page") ?? null,
    }));
    if (pageNavigationBefore.pager !== "1 / 2" || !pageNavigationBefore.ctaVisible || pageNavigationBefore.targetPage !== "2") {
      throw new Error(`${scenario}: analysis did not preserve the visible page and expose the first-location CTA ${JSON.stringify(pageNavigationBefore)}`);
    }
    const pageMaskCounts = await page.evaluate(() => [...document.querySelectorAll(".dm-segment-strip__page")].map((item) => ({
      page: item.getAttribute("data-segment-page"),
      count: item.querySelector(".dm-segment-strip__mask-count")?.textContent?.trim() ?? null,
    })));
    if (pageMaskCounts.find((item) => item.page === "1")?.count !== "2건 마스킹") {
      throw new Error(`${scenario}: thumbnail strip did not expose page-level masking counts ${JSON.stringify(pageMaskCounts)}`);
    }
    await page.locator("#btn-go-first-masking-location").click();
    await page.waitForFunction(() => document.querySelector(".dm-canvas__pager-label")?.textContent?.trim() === "2 / 2", null, { timeout: 8_000 });
    const automaticCounts = await readReviewSurfaceCounts(page);
    const primaryGeometryCards = await page.locator('#obsidian-detection-list .dm-detect__item').filter({ hasText: "결재선 영역" }).count();
    if (automaticCounts.autoMasked !== 2 || automaticCounts.pending !== 0 || primaryGeometryCards !== 0) {
      throw new Error(`${scenario}: automatically confirmed geometry remained in the primary review flow ${JSON.stringify({ automaticCounts, primaryGeometryCards })}`);
    }
    await assertFinalSaveControls(page, "#btn-save", true);
    await page.locator("#btn-save").click();
    await page.locator("#final-save-dialog").waitFor({ state: "visible", timeout: 8_000 });
    if (await page.locator("#final-save-warning-list .dm-savewarn__item").count() !== 0) {
      throw new Error(`${scenario}: automatic geometry confirmation still produced a save warning`);
    }
    await page.locator("#btn-dialog-cancel-save").click();
    console.log(`[qa] ${scenario}: automatic geometry confirmation removed primary cards and allowed clean save`);
  } finally {
    await page.close();
  }
}

async function runSuggestedGeometryConfirmationQa(browser, errors) {
  const page = await openThemePage(browser, "light", errors, { reviewScenario: "geometry" });
  const scenario = "geometry-review-suggested-one-click";
  try {
    await activateScreen(page, "documents");
    await page.locator("#btn-pick-pdf").click();
    await page.locator("#canvas-wrap-result.has-rendered-pdf").waitFor({ state: "attached", timeout: 8_000 });
    await page.locator("#btn-run-masking").click();
    const mergedCard = page.locator('#obsidian-detection-list .dm-detect__item[data-state="pending"]').filter({ hasText: /결재선 영역 \d+건 통합/ });
    await mergedCard.waitFor({ state: "visible", timeout: 8_000 });
    const mergedLabel = (await mergedCard.locator("strong").textContent())?.trim() ?? "";
    const mergedCount = Number(/^결재선 영역 (\d+)건 통합$/.exec(mergedLabel)?.[1] ?? 0);
    const reviewId = await mergedCard.getAttribute("data-review-id");
    if (!reviewId || mergedCount < 2) throw new Error(`${scenario}: grouped card identity is unavailable`);
    const suggested = page.locator(`button[data-review-id="${reviewId}"][data-review-action="confirm_suggested_geometry"]`);
    if ((await suggested.textContent())?.trim() !== "제안 영역 확정" || !(await suggested.evaluate((button) => button.classList.contains("dm-btn--primary")))) {
      throw new Error(`${scenario}: proposed geometry is not the primary action`);
    }
    const before = await readReviewSurfaceCounts(page);
    await suggested.click();
    await page.waitForFunction((expected) => document.querySelector("#review-filter-pending-count")?.textContent?.trim() === String(expected), before.pending - 1, { timeout: 8_000 });
    const after = await readReviewSurfaceCounts(page);
    const requests = await page.evaluate(() => window.__QA_INVOKES__.filter((entry) => entry.cmd === "resolve_masking_review" && entry.payload.request?.resolutionKind === "region_geometry"));
    if (requests.length !== mergedCount || requests.some((entry) => entry.payload.request.rectCount < mergedCount)) {
      throw new Error(`${scenario}: one click did not submit the grouped candidates and linked detections ${JSON.stringify(requests)}`);
    }
    if (after.pending !== before.pending - 1 || after.autoMasked !== before.autoMasked) {
      throw new Error(`${scenario}: review projection did not refresh after successor analyses ${JSON.stringify({ before, after })}`);
    }
    console.log(`[qa] ${scenario}: primary action resolved grouped candidates and refreshed the review projection in one click`);
  } finally {
    await page.close();
  }
}

async function runBoundarySegmentStripQa(browser, errors) {
  const boundaryPage = await openThemePage(browser, "light", errors, { reviewScenario: "boundary" });
  const readonlyPage = await openThemePage(browser, "light", errors);
  const scenario = "boundary-segment-strip";
  try {
    await activateScreen(boundaryPage, "documents");
    await boundaryPage.locator("#btn-pick-pdf").click();
    await boundaryPage.locator("#canvas-wrap-result.has-rendered-pdf").waitFor({ state: "attached", timeout: 8_000 });
    await boundaryPage.locator("#btn-run-masking").click();
    await boundaryPage.locator("#segment-thumbnail-strip").waitFor({ state: "visible", timeout: 8_000 });
    const initialStrip = await boundaryPage.locator("#segment-thumbnail-strip").evaluate((strip) => ({
      hasThumbnail: strip.querySelector(".dm-segment-strip__thumbnail") !== null,
      neutralPattern: getComputedStyle(strip.querySelector(".dm-segment-strip__bar")).backgroundImage.includes("repeating-linear-gradient"),
      editable: strip.querySelector("#segment-boundary-kind") !== null,
    }));
    if (!initialStrip.hasThumbnail || !initialStrip.neutralPattern || !initialStrip.editable) {
      throw new Error(`${scenario}: strip did not expose neutral page, pattern, and editable boundary state ${JSON.stringify(initialStrip)}`);
    }
    const startHandle = boundaryPage.locator('[data-boundary-handle="start"]');
    const endHandle = boundaryPage.locator('[data-boundary-handle="end"]');
    await startHandle.press("Home");
    await endHandle.press("End");
    const endHandleBox = await endHandle.boundingBox();
    const neighborBox = await boundaryPage.locator('[data-segment-page="1"] .dm-segment-strip__thumbnail').boundingBox();
    if (endHandleBox === null || neighborBox === null) throw new Error(`${scenario}: boundary drag geometry is unavailable`);
    await boundaryPage.mouse.move(endHandleBox.x + endHandleBox.width / 2, endHandleBox.y + endHandleBox.height / 2);
    await boundaryPage.mouse.down();
    await boundaryPage.mouse.move(neighborBox.x + neighborBox.width / 2, neighborBox.y + neighborBox.height / 2, { steps: 4 });
    await boundaryPage.mouse.up();
    if ((await boundaryPage.locator(".dm-segment-strip__range").textContent())?.trim() !== "1–1쪽") {
      throw new Error(`${scenario}: keyboard or pointer input escaped the reviewed segment`);
    }
    await boundaryPage.locator("#segment-boundary-kind").selectOption("legal");
    await boundaryPage.locator("#btn-apply-segment-boundary").click();
    await boundaryPage.waitForFunction(
      () => window.__QA_INVOKES__.some((entry) => entry.cmd === "resolve_masking_review" && entry.payload.request?.resolutionKind === "boundary"),
      null,
      { timeout: 8_000 },
    );
    const resolution = await boundaryPage.evaluate(() => window.__QA_INVOKES__.find((entry) => entry.cmd === "resolve_masking_review")?.payload.request);
    if (resolution?.segmentKind !== "legal" || resolution?.pageStart !== 0 || resolution?.pageEnd !== 0) {
      throw new Error(`${scenario}: selected kind or clamped range was not sent ${JSON.stringify(resolution)}`);
    }
    await boundaryPage.waitForFunction(
      () => document.querySelector('.dm-segment-strip__page[data-kind="legal"]') !== null
        && document.querySelector("#segment-boundary-kind") === null,
      null,
      { timeout: 8_000 },
    );

    await activateScreen(readonlyPage, "documents");
    await readonlyPage.locator("#btn-pick-pdf").click();
    await readonlyPage.locator("#canvas-wrap-result.has-rendered-pdf").waitFor({ state: "attached", timeout: 8_000 });
    await readonlyPage.locator("#btn-run-masking").click();
    await readonlyPage.locator("#segment-thumbnail-strip").waitFor({ state: "visible", timeout: 8_000 });
    if (await readonlyPage.locator("#segment-boundary-kind").count() !== 0 || !(await readonlyPage.locator(".dm-segment-strip__readonly").isVisible())) {
      throw new Error(`${scenario}: a session without a boundary review exposed editing controls`);
    }
    console.log(`[qa] ${scenario}: segment colors, segment-local keyboard/pointer clamp, guarded type correction, reanalysis refresh, and read-only fallback OK`);
  } finally {
    await boundaryPage.close();
    await readonlyPage.close();
  }
}

async function runIndeterminateCoverageReviewQa(browser, errors) {
  const page = await openThemePage(browser, "light", errors, { reviewScenario: "indeterminate_coverage", profile: "mixed" });
  const scenario = "geometry-warning-downgrade-confirm-save";
  try {
    await activateScreen(page, "documents");
    await page.locator("#btn-pick-pdf").click();
    await page.locator("#canvas-wrap-result.has-rendered-pdf").waitFor({ state: "attached", timeout: 8_000 });
    await page.locator("#btn-run-masking").click();
    await page.locator("#advanced-geometry-reviews").waitFor({ state: "visible", timeout: 8_000 });
    const surface = await page.evaluate(() => ({
      primaryGeometryCards: [...document.querySelectorAll("#obsidian-detection-list .dm-detect__item")]
        .filter((item) => item.textContent?.includes("결재선 영역")).length,
      advancedGeometryMarkers: document.querySelectorAll("#advanced-geometry-reviews-content button[data-review-id]").length,
      pendingCount: document.querySelector("#review-filter-pending-count")?.textContent?.trim() ?? null,
    }));
    if (surface.primaryGeometryCards !== 0 || surface.advancedGeometryMarkers !== 2 || surface.pendingCount !== "0") {
      throw new Error(`${scenario}: pending geometry was not downgraded to advanced visual markers ${JSON.stringify(surface)}`);
    }
    await assertFinalSaveControls(page, "#btn-save", true);
    await page.locator("#btn-save").click();
    await page.locator("#final-save-dialog").waitFor({ state: "visible", timeout: 8_000 });
    const warningDialog = await page.evaluate(() => ({
      warnings: [...document.querySelectorAll("#final-save-warning-list .dm-savewarn__item")].map((item) => item.textContent?.trim() ?? ""),
      confirmDisabled: document.querySelector("#btn-dialog-save-all") instanceof HTMLButtonElement
        ? document.querySelector("#btn-dialog-save-all").disabled
        : null,
      finalizations: window.__QA_INVOKES__.filter((entry) => entry.cmd === "finalize_masking_run").length,
    }));
    const expectedWarnings = [
      "미가림 가능성: 머리말 정보 · 1쪽 — 결재란 영역 자동확인 미완료 — 확인하고 저장",
      "미가림 가능성: 시행 정보 · 1쪽 — 결재란 영역 자동확인 미완료 — 확인하고 저장",
    ];
    if (JSON.stringify(warningDialog.warnings) !== JSON.stringify(expectedWarnings) || warningDialog.confirmDisabled !== false || warningDialog.finalizations !== 0) {
      throw new Error(`${scenario}: expected exactly one confirm-save warning ${JSON.stringify(warningDialog)}`);
    }
    await page.locator("#btn-dialog-save-all").click();
    try {
      await page.locator("#finalization-success-dialog").waitFor({ state: "visible", timeout: 8_000 });
    } catch (error) {
      const diagnostic = await page.evaluate(() => ({
        success: {
          hidden: document.querySelector("#finalization-success-dialog")?.classList.contains("is-hidden") ?? null,
          ariaHidden: document.querySelector("#finalization-success-dialog")?.getAttribute("aria-hidden") ?? null,
          text: document.querySelector("#finalization-success-dialog")?.textContent?.trim() ?? "",
        },
        finalSave: document.querySelector("#final-save-dialog")?.className ?? null,
        status: document.querySelector("#status")?.textContent?.trim() ?? "",
        readiness: document.querySelector("#final-save-readiness")?.textContent?.trim() ?? "",
        commands: window.__QA_INVOKES__.map((entry) => entry.cmd),
      }));
      throw new Error(`${scenario}: success dialog did not open ${JSON.stringify(diagnostic)}`, { cause: error });
    }
    const finalization = await page.evaluate(() => window.__QA_INVOKES__.filter((entry) => entry.cmd === "finalize_masking_run").at(-1)?.payload.request);
    if (finalization?.warningsConfirmed !== true) {
      throw new Error(`${scenario}: confirm-save did not submit warning acknowledgment ${JSON.stringify(finalization)}`);
    }
    console.log(`[qa] ${scenario}: geometry stayed out of the primary rail, exposed advanced markers, and saved through one acknowledged warning`);
  } finally {
    await page.close();
  }
}

await mkdir(evidenceDir, { recursive: true });
for (const entry of await readdir(evidenceDir)) {
  if (entry.endsWith(".png")) await unlink(path.join(evidenceDir, entry));
}

let devServer;
let browser;
const errors = [];
const screenshots = [];
const screenshotPathsSet = new Set();
const failures = [];

try {
  const server = await ensureDevServer();
  devServer = server.child;
  url = server.url;
  pdfBytes = [...(await readFile(fixturePath))];
  const launched = await launchBrowser();
  browser = launched.browser;
  console.log(`[browser] ${launched.selection}${launched.systemChromeDiagnostic ? `; system Chrome unavailable: ${launched.systemChromeDiagnostic}` : ""}`);
  const firstPaintPage = await browser.newPage({ viewport: desktopViewport });
  capturePageErrors(firstPaintPage, errors);
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
    localStorage.setItem("makiiing-v2-settings", JSON.stringify({ theme: "light", profile: "official_dispatch" }));
  });
  const firstPaintNavigation = firstPaintPage.goto(url, { waitUntil: "domcontentloaded" });
  await firstPaintPage.waitForFunction(() => (
    document.documentElement.getAttribute("data-theme-preference") === "light"
    && document.documentElement.getAttribute("data-theme") === "light"
  ), { timeout: 8_000 });
  releaseMainModule();
  await firstPaintNavigation;
  await firstPaintPage.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
  await assertTheme(firstPaintPage, "light", "light", "#f4f5f6");
  if (!(await firstPaintPage.locator("#btn-run-masking").isDisabled())) {
    throw new Error("current PDF masking action was enabled before document readiness initialized");
  }
  const firstPaintShot = path.join(evidenceDir, "light-first-paint.png");
  if (screenshotPathsSet.has(firstPaintShot)) throw new Error(`duplicate screenshot path attempted: ${firstPaintShot}`);
  await firstPaintPage.screenshot({ path: firstPaintShot, fullPage: false });
  screenshotPathsSet.add(firstPaintShot);
  screenshots.push(firstPaintShot);
  await firstPaintPage.close();
  console.log("[theme] stored light resolved before the application module; first frame stayed light with masking disabled");

  const themeControlPage = await openThemePage(browser, "dark", errors);
  await activateScreen(themeControlPage, "settings");
  await themeControlPage.locator('input[name="settings-theme"][value="light"]').check({ force: true });
  await assertTheme(themeControlPage, "light", "light", "#f4f5f6");
  const persistedLightTheme = await themeControlPage.evaluate(() => JSON.parse(localStorage.getItem("makiiing-v2-settings") ?? "{}").theme);
  if (persistedLightTheme !== "light") throw new Error(`light theme was not persisted: ${persistedLightTheme}`);
  await themeControlPage.emulateMedia({ colorScheme: "light" });
  await themeControlPage.locator('input[name="settings-theme"][value="system"]').check({ force: true });
  await assertTheme(themeControlPage, "system", "light", "#f4f5f6");
  await themeControlPage.emulateMedia({ colorScheme: "dark" });
  await waitForTheme(themeControlPage, "system", "dark");
  await assertTheme(themeControlPage, "system", "dark", "#11151c");
  await themeControlPage.emulateMedia({ colorScheme: "light" });
  await waitForTheme(themeControlPage, "system", "light");
  await assertTheme(themeControlPage, "system", "light", "#f4f5f6");
  await themeControlPage.close();
  console.log("[theme] light-first default, persisted preference, and live system switching OK");

  const pdfFingerprints = new Map();
  for (const theme of ["dark", "light"]) {
    try {
      pdfFingerprints.set(theme, await runThemeVisualAudit(browser, theme, screenshots, screenshotPathsSet, errors));
    } catch (error) {
      failures.push({ screen: `${theme}/visual-audit`, error: error instanceof Error ? error.message : String(error) });
      console.error(`[visual] ${theme}: FAILED (${failures.at(-1).error})`);
    }
    try {
      await runBatchVisualAudit(browser, theme, screenshots, screenshotPathsSet, errors);
    } catch (error) {
      failures.push({ screen: `${theme}/batch`, error: error instanceof Error ? error.message : String(error) });
      console.error(`[batch] ${theme}: FAILED (${failures.at(-1).error})`);
    }
  }
  try {
    await runDeskDefaultRoutingQa(browser, errors);
  } catch (error) {
    failures.push({ screen: "desk-default-mixed-routing", error: error instanceof Error ? error.message : String(error) });
    console.error(`[qa] desk-default-mixed-routing: FAILED (${failures.at(-1).error})`);
  }
  try {
    await runGeometryAutoConfirmationQa(browser, errors);
  } catch (error) {
    failures.push({ screen: "geometry-auto-confirm-save", error: error instanceof Error ? error.message : String(error) });
    console.error(`[qa] geometry-auto-confirm-save: FAILED (${failures.at(-1).error})`);
  }
  try {
    await runBoundarySegmentStripQa(browser, errors);
  } catch (error) {
    failures.push({ screen: "boundary-segment-strip", error: error instanceof Error ? error.message : String(error) });
    console.error(`[qa] boundary-segment-strip: FAILED (${failures.at(-1).error})`);
  }
  try {
    await runIndeterminateCoverageReviewQa(browser, errors);
  } catch (error) {
    failures.push({ screen: "indeterminate-coverage-review-and-save-gate", error: error instanceof Error ? error.message : String(error) });
    console.error(`[qa] indeterminate-coverage-review-and-save-gate: FAILED (${failures.at(-1).error})`);
  }
  if (pdfFingerprints.size === 2 && pdfFingerprints.get("dark") !== pdfFingerprints.get("light")) {
    failures.push({ screen: "pdf-color", error: "original PDF canvas pixels changed between dark and light themes" });
  } else if (pdfFingerprints.size === 2) {
    console.log("[pdf] original canvas pixel fingerprint is identical in dark and light themes");
  }
} finally {
  await browser?.close();
  if (devServer) {
    devServer.kill("SIGTERM");
    console.log("[dev] stopped vite dev server");
  }
}

if (errors.length > 0) {
  console.error(`SMOKE FAILED — ${errors.length} console/page error(s):`);
  for (const entry of errors) console.error(`  - (${entry.source}) ${entry.text}`);
}
if (failures.length > 0) {
  console.error(`SMOKE FAILED — ${failures.length} screen transition failure(s):`);
  for (const failure of failures) console.error(`  - ${failure.screen}: ${failure.error}`);
}

if (errors.length > 0 || failures.length > 0) {
  process.exit(1);
}

const onDiskPngCount = (await readdir(evidenceDir)).filter((entry) => entry.endsWith(".png")).length;
if (onDiskPngCount !== screenshotPathsSet.size || screenshotPathsSet.size !== screenshots.length) {
  console.error(`SMOKE FAILED — screenshot accounting mismatch (attempted ${screenshots.length}, unique ${screenshotPathsSet.size}, on disk ${onDiskPngCount})`);
  process.exit(1);
}

console.log(`SMOKE OK — dark/light responsive screens activated, 0 console/page errors, ${onDiskPngCount} unique screenshots in ${path.relative(repoRoot, evidenceDir)}/`);
