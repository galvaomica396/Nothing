// Canvas drawing-interaction QA (regression guard for the v3.1.0 bug where
// "마스킹 박스 / 복원 박스가 기능을 안 한다" — dragging on the PDF produced no box).
//
// Root cause the redesign (d4cfca7) deleted src/styles.css, which had provided
//   #pdf-canvas-result / #overlay-canvas-result { position: absolute; left/top:0 }
// screen-canvas.css never carried that rule over, so the overlay canvas fell out
// of the PDF stack (rendered *below* the page in normal flow). Pointer events over
// the visible PDF then hit #pdf-canvas-result (no listeners) instead of the
// overlay, so no box was ever created. The earlier smoke only walked screens and
// never dragged, so it missed this. This script drags on the *visible PDF* and
// asserts real boxes appear — for both the main canvas screen and the standalone
// (?mode=canvas) window.
//
// v3.3.0 follow-up (this file's second half): the select / delete / pan tools did
// not work in the packaged app even though this QA was green, because the QA only
// toggled the tool buttons and checked aria-pressed — it never exercised the
// canvas pointer behaviour a real user relies on. Root cause: the overlay
// mousedown handler only implemented mask/restore drawing; select/delete/pan were
// dead pointer paths. The "USER-PERSPECTIVE SCENARIO" block below now drives the
// real workflow: click a box to select it (highlight + properties panel), switch
// tool and click a box to delete it, delete-selected via button, and drag with the
// pan tool to scroll the view.
//
// Gate contract: exit 0 only if every interaction assertion passes with no
// unexpected console/page errors.
//
// Usage: node scripts/qa_canvas_interactions.mjs [--url http://localhost:1420/]

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

const url = args.get("--url") ?? "http://localhost:1420/";
const viewport = { width: 1400, height: 940 };
const evidenceDir = path.join(repoRoot, "build", "redesign-evidence");
const fixturePath = path.join(repoRoot, "tests", "fixtures", "phase6_non_sensitive.pdf");

// Same narrow allowance as qa_redesign_smoke.mjs — anything else fails the gate.
const KNOWN_BROWSER_ENV_ERRORS = [/window\.__TAURI_INTERNALS__/];
const isKnownEnvError = (text) => KNOWN_BROWSER_ENV_ERRORS.some((pattern) => pattern.test(text));

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
    if (child.exitCode !== null) throw new Error(`vite dev exited early (code ${child.exitCode}):\n${output}`);
    if (await isDevServerUp(target)) return child;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  child.kill("SIGTERM");
  throw new Error(`vite dev did not become ready within 60s:\n${output}`);
}

async function launchBrowser() {
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return await chromium.launch({ headless: true });
  }
}

const failures = [];
function check(condition, label) {
  if (condition) {
    console.log(`  ✓ ${label}`);
  } else {
    failures.push(label);
    console.error(`  ✗ ${label}`);
  }
}

// Drag over the *visible PDF* (the #pdf-canvas-result rect), not the overlay's own
// rect — that is what a real user does, and what the regression broke. If the
// overlay is not stacked on the PDF, these client coords miss its listeners.
async function dragOnPdf(page, fromFrac, toFrac) {
  const box = await page.locator("#pdf-canvas-result").boundingBox();
  if (!box) throw new Error("#pdf-canvas-result has no bounding box");
  const start = { x: box.x + box.width * fromFrac.x, y: box.y + box.height * fromFrac.y };
  const end = { x: box.x + box.width * toFrac.x, y: box.y + box.height * toFrac.y };
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move((start.x + end.x) / 2, (start.y + end.y) / 2, { steps: 6 });
  await page.mouse.move(end.x, end.y, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(120);
}

async function selectCanvasTool(page, toolId) {
  const tool = page.locator(`#${toolId}`);
  if (!(await tool.isVisible())) await page.locator("#canvas-tool-menu-trigger").click();
  await tool.click();
}

// Drag horizontally within the *visible* scroll-viewport rect (not the PDF rect,
// which at max zoom extends beyond the viewport and can put a fraction off-screen).
// The overlay fills the whole visible viewport when the PDF overflows, so any
// point here lands on its pointer listeners — this is what the pan tool needs.
async function panDragHorizontal(page) {
  const rect = await page.evaluate(() => {
    const s = document.getElementById("canvas-wrap-result").closest(".dm-canvas__scroll");
    const r = s.getBoundingClientRect();
    return { x: r.x, y: r.y, w: r.width, h: r.height };
  });
  const y = rect.y + rect.h / 2;
  const startX = rect.x + rect.w * 0.8;
  const endX = rect.x + rect.w * 0.2;
  await page.mouse.move(startX, y);
  await page.mouse.down();
  await page.mouse.move((startX + endX) / 2, y, { steps: 6 });
  await page.mouse.move(endX, y, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(120);
}

async function totalBoxCount(page) {
  const text = (await page.locator("#box-info").textContent()) ?? "";
  const match = text.match(/전체\s+(\d+)\s*개/);
  return match ? Number(match[1]) : NaN;
}

// Click a single point inside the *visible PDF* (like a real user picking a box),
// not on the overlay's own rect — the click must land on the overlay's listeners.
async function clickOnPdf(page, frac) {
  const box = await page.locator("#pdf-canvas-result").boundingBox();
  if (!box) throw new Error("#pdf-canvas-result has no bounding box");
  await page.mouse.click(box.x + box.width * frac.x, box.y + box.height * frac.y);
  await page.waitForTimeout(80);
}

// The app-reported selected-box index (-1 when nothing is selected). This reads
// the same state the overlay highlight + properties panel render from, so it is a
// faithful proxy for "the user sees a selection".
async function selectedBoxIndex(page) {
  return page.evaluate(() => {
    const el = document.getElementById("canvas-box-property-page");
    return el ? el.textContent : null;
  });
}

async function runSurface(page, { label, standalone }) {
  console.log(`\n=== ${label} ===`);

  // v4 P2: 문서 관제 + 캔버스가 하나의 "문서" 화면으로 통합됐다. 별도 canvas
  // 패널이 없어졌고, 메인 창은 부팅 시 통합 "문서" 화면(data-screen-panel=
  // "documents")이 곧 활성 상태다. 상단 탭 "문서"로 언제든 되돌릴 수 있다.
  if (!standalone) {
    await page.locator('[data-screen-target="documents"]').first().evaluate((element) => element.click());
  }
  await page.locator('[data-screen-panel="documents"].is-active').waitFor({ state: "visible", timeout: 8_000 });

  await page.locator("#btn-pick-pdf").evaluate((element) => element.click());
  await page.locator("#canvas-wrap-result.has-rendered-pdf").waitFor({ state: "attached", timeout: 8_000 });
  // The overlay is sized to match the PDF canvas asynchronously (redrawOverlay
  // after the pdf.js render). Let layout settle before measuring rects, otherwise
  // the coincidence check can read a mid-render (unsized) overlay.
  await page.waitForFunction(() => {
    const pdf = document.getElementById("pdf-canvas-result");
    const overlay = document.getElementById("overlay-canvas-result");
    return pdf && overlay && overlay.width === pdf.width && overlay.width > 0;
  }, { timeout: 8_000 });
  await page.waitForTimeout(120);

  // --- Structural guard: overlay must be stacked exactly over the PDF canvas ---
  const geometry = await page.evaluate(() => {
    const pdf = document.getElementById("pdf-canvas-result");
    const overlay = document.getElementById("overlay-canvas-result");
    const pr = pdf.getBoundingClientRect();
    const or = overlay.getBoundingClientRect();
    const cx = pr.left + pr.width / 2;
    const cy = pr.top + pr.height / 2;
    const topEl = document.elementFromPoint(cx, cy);
    return {
      coincident: Math.abs(pr.left - or.left) < 1.5 && Math.abs(pr.top - or.top) < 1.5 &&
        Math.abs(pr.width - or.width) < 1.5 && Math.abs(pr.height - or.height) < 1.5,
      overlayPosition: getComputedStyle(overlay).position,
      topElementId: topEl ? topEl.id : "",
    };
  });
  check(geometry.overlayPosition === "absolute", `overlay is absolutely positioned (got ${geometry.overlayPosition})`);
  check(geometry.coincident, "overlay rect coincides with PDF canvas rect (stacked)");
  check(geometry.topElementId === "overlay-canvas-result", `overlay is topmost element over PDF center (got '${geometry.topElementId}')`);

  // --- Masking box drag ---
  await selectCanvasTool(page, "btn-canvas-tool-mask");
  check(await page.locator("#btn-canvas-tool-mask").getAttribute("aria-pressed") === "true", "mask tool is active");
  await dragOnPdf(page, { x: 0.12, y: 0.10 }, { x: 0.55, y: 0.28 });
  check((await totalBoxCount(page)) === 1, "mask drag created 1 box (#box-info 전체 1개)");
  check(/마스킹 박스 1개/.test((await page.locator("#canvas-summary-mask-count").textContent()) ?? ""), "summary shows 마스킹 박스 1개");
  check((await page.locator("#canvas-box-list .canvas-box-empty").count()) === 0, "canvas-box-list no longer shows empty state");
  check((await page.locator("#canvas-box-list button").count()) >= 1, "canvas-box-list has a box entry");

  if (!standalone) {
    await mkdir(evidenceDir, { recursive: true });
    await page.screenshot({ path: path.join(evidenceDir, "07-canvas-box-drawn.png"), fullPage: false });
    console.log(`  [evidence] build/redesign-evidence/07-canvas-box-drawn.png`);
  }

  // --- Restore box drag ---
  await selectCanvasTool(page, "btn-canvas-tool-restore");
  check(await page.locator("#btn-canvas-tool-restore").getAttribute("aria-pressed") === "true", "restore tool is active");
  await dragOnPdf(page, { x: 0.15, y: 0.45 }, { x: 0.60, y: 0.66 });
  check((await totalBoxCount(page)) === 2, "restore drag created a 2nd box (#box-info 전체 2개)");
  check(/복원 박스 1개/.test((await page.locator("#canvas-summary-restore-count").textContent()) ?? ""), "summary shows 복원 박스 1개");
  check((await page.locator("#canvas-box-list button").count()) >= 2, "canvas-box-list has 2 box entries");

  // ------------------------------------------------------------------------
  // USER-PERSPECTIVE SCENARIO (the real v3.3.0 bug: 선택/삭제/이동 도구가 캔버스에서
  // 작동하지 않음). The pre-redesign QA only toggled aria-pressed on the tool
  // buttons and leaned on drawing auto-selecting the last box — it never verified
  // that a user can CLICK a box on the canvas to select it, see it reflected in
  // the properties panel, delete it with the delete tool, or pan the view. Those
  // pointer paths were dead (mousedown only handled mask/restore), so a green QA
  // sat on top of a broken canvas. These asserts drive the actual user workflow.
  // ------------------------------------------------------------------------

  // Nothing selected yet (drawing the restore box left it selected — deselect by
  // clicking empty canvas with the select tool, then assert the empty panel).
  await selectCanvasTool(page, "btn-canvas-tool-select");
  check(await page.locator("#btn-canvas-tool-select").getAttribute("aria-pressed") === "true", "select tool activates");
  await clickOnPdf(page, { x: 0.88, y: 0.95 }); // empty corner, no box there
  check(await selectedBoxIndex(page) === "-", "clicking empty canvas with select tool clears selection (properties panel empty)");
  check((await page.locator("#canvas-box-properties.is-empty").count()) === 1, "properties panel shows empty state when nothing selected");

  // Click the MASK box (drawn at x 0.12–0.55, y 0.10–0.28) — its center ~ (0.33,0.19).
  await clickOnPdf(page, { x: 0.33, y: 0.19 });
  check(await selectedBoxIndex(page) === "1페이지", "select tool: clicking the mask box selects it (properties panel filled)");
  check((await page.locator("#canvas-box-property-type").textContent()) === "마스킹 박스", "properties panel reflects the clicked box type (마스킹 박스)");
  check((await page.locator("#canvas-box-properties.is-empty").count()) === 0, "properties panel leaves empty state once a box is selected");
  check((await page.locator("#canvas-box-list button.is-active").count()) === 1, "box list highlights the click-selected box");

  if (!standalone) {
    await mkdir(evidenceDir, { recursive: true });
    await page.screenshot({ path: path.join(evidenceDir, "16-canvas-select.png"), fullPage: false });
    console.log(`  [evidence] build/redesign-evidence/16-canvas-select.png`);
  }

  // Selecting a DIFFERENT box (the restore box, center ~ (0.37, 0.55)) moves the
  // selection — proves the hit-test picks the box under the cursor, not the last drawn.
  await clickOnPdf(page, { x: 0.37, y: 0.55 });
  check((await page.locator("#canvas-box-property-type").textContent()) === "복원 박스", "select tool: clicking the restore box switches selection to it (복원 박스)");

  // --- Delete tool: click a box on the canvas to remove it ---
  await selectCanvasTool(page, "btn-canvas-tool-delete");
  check(await page.locator("#btn-canvas-tool-delete").getAttribute("aria-pressed") === "true", "delete tool stays active (persistent click-to-delete tool)");
  const beforeDelete = await totalBoxCount(page);
  await clickOnPdf(page, { x: 0.33, y: 0.19 }); // click the mask box to delete it
  const afterDelete = await totalBoxCount(page);
  check(afterDelete === beforeDelete - 1, `delete tool: clicking a box removes it (${beforeDelete} -> ${afterDelete})`);
  check((await page.locator("#canvas-box-list button").count()) === afterDelete, "box list count matches after canvas delete");
  check(/마스킹 박스 0개/.test((await page.locator("#canvas-summary-mask-count").textContent()) ?? ""), "summary shows the mask box was removed (마스킹 박스 0개)");

  // Delete tool clicking empty space is a no-op (does not throw / delete anything).
  const beforeNoop = await totalBoxCount(page);
  await clickOnPdf(page, { x: 0.90, y: 0.06 });
  check((await totalBoxCount(page)) === beforeNoop, "delete tool: clicking empty canvas removes nothing");

  // --- "선택 삭제" button path still works (select a box, then delete-selected) ---
  await selectCanvasTool(page, "btn-canvas-tool-select");
  await clickOnPdf(page, { x: 0.37, y: 0.55 }); // select remaining restore box
  const beforeBtnDelete = await totalBoxCount(page);
  check(await page.locator("#btn-canvas-delete-box").isEnabled(), "선택 삭제 button is enabled once a box is selected");
  // Fire the handler at the DOM level: at this viewport the button can sit under
  // the sticky toolbar, so a coordinate click would hit the toolbar instead. The
  // canvas delete TOOL above is already exercised with a real mouse click.
  await page.locator("#btn-canvas-delete-box").evaluate((el) => el.click());
  check((await totalBoxCount(page)) === beforeBtnDelete - 1, "선택 삭제 button removes the selected box");

  // --- Pan tool: dragging scrolls the view ---
  // The QA fixture is a small page; zoom to max so it overflows the scroll
  // container and the pan tool has room to move (each click is +0.1, clamped 2.5).
  await selectCanvasTool(page, "btn-canvas-tool-mask");
  await dragOnPdf(page, { x: 0.20, y: 0.20 }, { x: 0.60, y: 0.40 }); // ensure a box exists to render
  for (let zoom = 0; zoom < 14; zoom += 1) {
    const zoomIn = page.locator("#btn-canvas-zoom-in");
    if (!(await zoomIn.isEnabled())) break; // disabled at max scale
    try {
      await zoomIn.click({ timeout: 2_000 });
    } catch {
      break; // became disabled mid-flight (reached max scale)
    }
    await page.waitForTimeout(60); // let the async zoom re-render settle
  }
  await page.waitForTimeout(200);
  await selectCanvasTool(page, "btn-canvas-tool-pan");
  check(await page.locator("#btn-canvas-tool-pan").getAttribute("aria-pressed") === "true", "pan tool activates");
  const scrollBefore = await page.evaluate(() => {
    const el = document.querySelector("#canvas-wrap-result").closest(".dm-canvas__scroll");
    return { left: el.scrollLeft, scrollWidth: el.scrollWidth, clientWidth: el.clientWidth };
  });
  // The scroll container grows vertically with content (flex), so overflow is on
  // the width axis — the zoomed PDF is wider than the viewport. Pan along X.
  check(scrollBefore.scrollWidth > scrollBefore.clientWidth, "zoomed PDF overflows the scroll container (pan has room to move)");
  // Drag right→left across the viewport → content scrolls, scrollLeft grows from 0.
  await panDragHorizontal(page);
  const scrollAfter = await page.evaluate(() => {
    const el = document.querySelector("#canvas-wrap-result").closest(".dm-canvas__scroll");
    return { left: el.scrollLeft };
  });
  check(scrollAfter.left > scrollBefore.left, `pan tool: dragging scrolled the view (scrollLeft ${scrollBefore.left} -> ${scrollAfter.left})`);
}

await mkdir(evidenceDir, { recursive: true });
const devServer = await ensureDevServer(url);
const pdfBytes = [...(await readFile(fixturePath))];
const browser = await launchBrowser();
const errors = [];

try {
  // ---- Surface 1: main canvas screen ----
  const mainPage = await browser.newPage({ viewport });
  mainPage.on("console", (m) => { if (m.type() === "error") errors.push({ surface: "main", text: m.text() }); });
  mainPage.on("pageerror", (e) => errors.push({ surface: "main", text: e.message }));
  await installTauriQaMocks(mainPage, { fixturePath, outputDir: path.join(evidenceDir, "output"), pdfBytes });
  await mainPage.goto(url, { waitUntil: "networkidle" });
  await mainPage.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
  await runSurface(mainPage, { label: "main canvas screen", standalone: false });

  // ---- Surface 2: standalone canvas window (?mode=canvas) ----
  const standaloneUrl = url.includes("?") ? `${url}&mode=canvas` : `${url}?mode=canvas`;
  const standalonePage = await browser.newPage({ viewport });
  standalonePage.on("console", (m) => { if (m.type() === "error") errors.push({ surface: "standalone", text: m.text() }); });
  standalonePage.on("pageerror", (e) => errors.push({ surface: "standalone", text: e.message }));
  await installTauriQaMocks(standalonePage, { fixturePath, outputDir: path.join(evidenceDir, "output"), pdfBytes });
  await standalonePage.goto(standaloneUrl, { waitUntil: "networkidle" });
  await standalonePage.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
  await runSurface(standalonePage, { label: "standalone canvas window (?mode=canvas)", standalone: true });
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
  console.log(`\n[info] ${ignoredErrors.length} known browser-env error(s) ignored`);
}
if (unexpectedErrors.length > 0) {
  console.error(`\nCANVAS QA FAILED — ${unexpectedErrors.length} console/page error(s):`);
  for (const entry of unexpectedErrors) console.error(`  - (${entry.surface}) ${entry.text}`);
}
if (failures.length > 0) {
  console.error(`\nCANVAS QA FAILED — ${failures.length} interaction assertion(s):`);
  for (const failure of failures) console.error(`  - ${failure}`);
}

if (unexpectedErrors.length > 0 || failures.length > 0) {
  process.exit(1);
}

console.log("\nCANVAS QA OK — mask/restore drag creates boxes, select clicks select, delete removes, pan scrolls, on both surfaces, 0 unexpected errors.");
