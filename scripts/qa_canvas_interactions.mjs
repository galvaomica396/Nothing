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

// Dispatch a real DOM mouse sequence directly to the production overlay handler.
// Coordinates are overlay-local canvas pixels, matching the QA driver's
// `drag-canvas <x0> <y0> <x1> <y1>` command. This is the true pointer-path
// regression check; `draw-box` only injects state and intentionally remains a
// separate drive fixture for handler-independent apply tests.
async function dragCanvas(page, fromFrac, toFrac) {
  await page.evaluate(({ from, to }) => {
    const overlay = document.getElementById("overlay-canvas-result");
    if (!(overlay instanceof HTMLCanvasElement)) throw new Error("#overlay-canvas-result has no canvas");
    const bounds = overlay.getBoundingClientRect();
    const scaleX = overlay.width > 0 && bounds.width > 0 ? bounds.width / overlay.width : 1;
    const scaleY = overlay.height > 0 && bounds.height > 0 ? bounds.height / overlay.height : 1;
    const clientPoint = (point) => ({
      x: bounds.left + overlay.width * point.x * scaleX,
      y: bounds.top + overlay.height * point.y * scaleY,
    });
    const dispatch = (target, type, point) => {
      const client = clientPoint(point);
      target.dispatchEvent(new MouseEvent(type, {
        bubbles: true,
        cancelable: true,
        view: window,
        button: 0,
        buttons: type === "mouseup" ? 0 : 1,
        clientX: client.x,
        clientY: client.y,
      }));
    };
    dispatch(overlay, "mousedown", from);
    for (const progress of [0.2, 0.4, 0.6, 0.8, 1]) {
      dispatch(overlay, "mousemove", {
        x: from.x + (to.x - from.x) * progress,
        y: from.y + (to.y - from.y) * progress,
      });
    }
    dispatch(window, "mouseup", to);
  }, { from: fromFrac, to: toFrac });
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

async function panDragVertical(page) {
  const rect = await page.evaluate(() => {
    const scroll = document.getElementById("canvas-wrap-result").closest(".dm-canvas__scroll");
    const bounds = scroll.getBoundingClientRect();
    return { x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height };
  });
  const x = rect.x + rect.width / 2;
  const startY = rect.y + rect.height * 0.8;
  const endY = rect.y + rect.height * 0.2;
  await page.mouse.move(x, startY);
  await page.mouse.down();
  await page.mouse.move(x, (startY + endY) / 2, { steps: 6 });
  await page.mouse.move(x, endY, { steps: 6 });
  await page.mouse.up();
  await page.waitForTimeout(120);
}

async function totalBoxCount(page) {
  const counts = await Promise.all(
    ["#review-summary-mask-count", "#review-summary-restore-count"].map(async (selector) => {
      const text = (await page.locator(selector).textContent()) ?? "";
      return Number(text.match(/(\d+)\s*개/)?.[1] ?? Number.NaN);
    }),
  );
  return counts.every(Number.isFinite) ? counts.reduce((sum, count) => sum + count, 0) : Number.NaN;
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
  await dragCanvas(page, { x: 0.12, y: 0.10 }, { x: 0.55, y: 0.28 });
  check((await totalBoxCount(page)) === 1, "mask drag created 1 visible box");
  check(/1개/.test((await page.locator("#review-summary-mask-count").textContent()) ?? ""), "summary shows 마스킹 박스 1개");
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
  await dragCanvas(page, { x: 0.15, y: 0.45 }, { x: 0.60, y: 0.66 });
  check((await totalBoxCount(page)) === 2, "restore drag created a 2nd visible box");
  check(/1개/.test((await page.locator("#review-summary-restore-count").textContent()) ?? ""), "summary shows 복원 박스 1개");
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
  check(/0개/.test((await page.locator("#review-summary-mask-count").textContent()) ?? ""), "summary shows the mask box was removed (마스킹 박스 0개)");

  // Delete tool clicking empty space is a no-op (does not throw / delete anything).
  const beforeNoop = await totalBoxCount(page);
  await clickOnPdf(page, { x: 0.90, y: 0.06 });
  check((await totalBoxCount(page)) === beforeNoop, "delete tool: clicking empty canvas removes nothing");

  // --- "선택 삭제" button path still works (select a box, then delete-selected) ---
  await selectCanvasTool(page, "btn-canvas-tool-select");
  await clickOnPdf(page, { x: 0.37, y: 0.55 }); // select remaining restore box
  const beforeBtnDelete = await totalBoxCount(page);
  check(await page.locator("#btn-canvas-box-delete").isEnabled(), "선택 삭제 button is enabled once a box is selected");
  // Fire the handler at the DOM level: at this viewport the button can sit under
  // the sticky toolbar, so a coordinate click would hit the toolbar instead. The
  // canvas delete TOOL above is already exercised with a real mouse click.
  await page.locator("#btn-canvas-box-delete").evaluate((el) => el.click());
  check((await totalBoxCount(page)) === beforeBtnDelete - 1, "선택 삭제 button removes the selected box");

  // --- Pan tool: dragging scrolls the view ---
  // The QA fixture is a small page; zoom to max so it overflows the scroll
  // container and the pan tool has room to move (each click is +0.1, clamped 2.5).
  await selectCanvasTool(page, "btn-canvas-tool-mask");
  await dragCanvas(page, { x: 0.20, y: 0.20 }, { x: 0.60, y: 0.40 }); // ensure a box exists to render
  for (let zoom = 0; zoom < 20; zoom += 1) {
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
    return {
      left: el.scrollLeft,
      top: el.scrollTop,
      scrollWidth: el.scrollWidth,
      scrollHeight: el.scrollHeight,
      clientWidth: el.clientWidth,
      clientHeight: el.clientHeight,
    };
  });
  const horizontalOverflow = scrollBefore.scrollWidth > scrollBefore.clientWidth;
  const verticalOverflow = scrollBefore.scrollHeight > scrollBefore.clientHeight;
  check(horizontalOverflow || verticalOverflow, "zoomed PDF overflows the scroll container (pan has room to move)");
  if (horizontalOverflow) await panDragHorizontal(page);
  else await panDragVertical(page);
  const scrollAfter = await page.evaluate(() => {
    const el = document.querySelector("#canvas-wrap-result").closest(".dm-canvas__scroll");
    return { left: el.scrollLeft, top: el.scrollTop };
  });
  const panned = horizontalOverflow
    ? scrollAfter.left > scrollBefore.left
    : scrollAfter.top > scrollBefore.top;
  check(
    panned,
    `pan tool: dragging scrolled the view (left ${scrollBefore.left} -> ${scrollAfter.left}, top ${scrollBefore.top} -> ${scrollAfter.top})`,
  );
}

async function runPublicTextManualMaskSurface(page, { malformedSuccessor = false } = {}) {
  console.log("\n=== public text manual mask ===");
  await page.locator('[data-screen-target="documents"]').first().evaluate((element) => element.click());
  await page.locator('[data-screen-panel="documents"].is-active').waitFor({ state: "visible", timeout: 8_000 });
  // #profile lives only on the (hidden) masking-settings screen, so selectOption
  // fails its visibility check here. Set the value + dispatch change directly,
  // mirroring qa_save_flow. This still drives the real onChange handler.
  await page.locator("#profile").evaluate((element, selectedProfile) => {
    element.value = selectedProfile;
    element.dispatchEvent(new Event("change", { bubbles: true }));
  }, "internal_review");
  await page.locator("#btn-pick-pdf").evaluate((element) => element.click());
  await page.locator("#canvas-wrap-result.has-rendered-pdf").waitFor({ state: "attached", timeout: 8_000 });
  await selectCanvasTool(page, "btn-canvas-tool-mask");
  await page.locator("#btn-run-masking").click();
  await page.waitForFunction(() => window.__QA_INVOKES__.filter((entry) => entry.cmd === "analyze_masking_run").length === 1, null, { timeout: 15_000 });
  await page.waitForFunction(() => document.querySelector("#btn-run-masking")?.getAttribute("data-running") === "true", null, { timeout: 3_000 });
  check(await page.locator("#btn-canvas-tool-mask").isDisabled(), "public text page: mask tool is disabled during masking");
  check(await page.locator("#btn-canvas-tool-restore").isDisabled(), "public text page: restore tool is disabled during masking");
  check(await page.locator("#btn-canvas-apply").isDisabled(), "public text page: manual apply is disabled during masking");
  await dragCanvas(page, { x: 0.16, y: 0.14 }, { x: 0.44, y: 0.28 });
  check((await page.locator("#canvas-box-list button").count()) === 0, "public text page: drag during masking creates no box");
  if (!malformedSuccessor) {
    // T44R4: 이 단언은 analyzeDelayMs가 걸린 첫 표면에서만 결정론적이다.
    // malformed-successor 대조 표면은 분석 지연 없이 실행되므로 드래그 거부 문구가
    // #status에 찍힌 직후 완료 문구로 덮일 수 있다 — 거부 동작 자체는 위의
    // "creates no box" 단언이 두 표면 모두에서 검증한다.
    check((await page.locator("#status").textContent()) === "마스킹 실행 중에는 박스를 그릴 수 없습니다. 완료 후 그려 주세요.", "public text page: masking drag rejection explains when to draw");
  }
  await page.waitForFunction(() => document.querySelector("#btn-run-masking")?.getAttribute("data-running") !== "true", null, { timeout: 8_000 });
  if (!malformedSuccessor) {
    await selectCanvasTool(page, "btn-canvas-tool-mask");
    await dragCanvas(page, { x: 0.16, y: 0.14 }, { x: 0.44, y: 0.28 });
    check((await page.locator("#canvas-box-list button").count()) === 1, "public text page: mask drag after masking creates a box");
    await page.locator("#btn-canvas-apply").click();
    await page.waitForFunction(() => document.querySelectorAll("#canvas-box-list button").length === 0, null, { timeout: 8_000 });
    check((await page.locator("#canvas-box-list button").count()) === 0, "public text page: mask apply clears the draft box");
    const stagedOverlay = await page.evaluate(() => {
      const overlay = document.querySelector("#overlay-canvas-result");
      if (!(overlay instanceof HTMLCanvasElement)) {
        return {
          style: null,
          stagedMaskCount: null,
          blockedRestoreCount: null,
          stagedPixel: null,
          confirmedPixel: null,
          hasOverlay: overlay instanceof HTMLCanvasElement,
        };
      }
      const context = overlay.getContext("2d");
      if (!context) return { style: null, stagedMaskCount: null, blockedRestoreCount: null, stagedPixel: null, confirmedPixel: null };
      const sample = (x, y) => [...context.getImageData(
        Math.max(0, Math.min(overlay.width - 1, Math.round(x))),
        Math.max(0, Math.min(overlay.height - 1, Math.round(y))),
        1,
        1,
      ).data];
      return {
        style: overlay.dataset.stagedOverlayStyle ?? null,
        stagedMaskCount: overlay.dataset.stagedMaskCount ?? null,
        blockedRestoreCount: overlay.dataset.blockedRestoreCount ?? null,
        restoreState: overlay.dataset.stagedRestoreState ?? null,
        // The deterministic fixture is 420x260 and this drag is
        // 0.16..0.44 x 0.14..0.28, so the midpoint is (126,55).
        stagedPixel: sample(126, 55),
        confirmedPixel: sample(344, 71),
      };
    });
    check(stagedOverlay.style === "translucent-dashed-labeled", "public text page: applied mask is rendered as a labeled translucent dashed staged overlay");
    check(stagedOverlay.stagedMaskCount === "1" && stagedOverlay.blockedRestoreCount === "0", "public text page: staged mask overlay counters identify one non-blocked mask");
    check(
      Array.isArray(stagedOverlay.stagedPixel)
        && stagedOverlay.stagedPixel[3] > 0
        && !(
          stagedOverlay.stagedPixel[0] === 0
          && stagedOverlay.stagedPixel[1] === 0
          && stagedOverlay.stagedPixel[2] === 0
          && stagedOverlay.stagedPixel[3] === 255
        )
        && Array.isArray(stagedOverlay.confirmedPixel)
        && stagedOverlay.confirmedPixel[0] === 0
        && stagedOverlay.confirmedPixel[1] === 0
        && stagedOverlay.confirmedPixel[2] === 0
        && stagedOverlay.confirmedPixel[3] === 255,
      "public text page: staged pixels differ from the solid black confirmed detection pixels",
    );
    check(
      (await page.locator("#review-summary-banner").textContent())?.includes("자동 1건 · 수동 1건(저장 시 적용)") === true,
      "public text page: review summary separates automatic and staged manual counts",
    );
    check((await page.locator("#review-total-count").textContent())?.trim() === "1건", "public text page: manual action does not enter the review queue counter");
  } else {
    await selectCanvasTool(page, "btn-canvas-tool-mask");
    await dragCanvas(page, { x: 0.16, y: 0.14 }, { x: 0.44, y: 0.28 });
    check((await page.locator("#canvas-box-list button").count()) === 1, "public text page: mask drag creates an observable box");
  }
  // T62: 복원 도구는 표시되지만 synthetic browser input cannot authorize it.
  check(!(await page.locator("#btn-canvas-tool-restore").isDisabled()), "public text page: restore tool is usable");
  if (malformedSuccessor) {
    // Synthetic browser events are never a restore authorization signal,
    // regardless of what the test double would return.
    await page.locator("#btn-canvas-undo").click();
    await selectCanvasTool(page, "btn-canvas-tool-restore");
    await dragCanvas(page, { x: 0.67, y: 0.22 }, { x: 0.93, y: 0.37 });
    await page.locator("#btn-canvas-apply").click();
    await page.waitForFunction(
      () => document.querySelector("#status")?.textContent?.includes("실제 캔버스 드래그"),
      null,
      { timeout: 8_000 },
    );
    check((await page.locator("#canvas-box-list button").count()) === 1, "public text page: synthetic restore keeps the draft box visible");
    check((await page.locator("#status").textContent()).includes("실제 캔버스 드래그"), "public text page: synthetic restore reports the trust-boundary rejection");
    return;
  }
  // T62: real OS input is required before a confirmed automatic mask can be restored.
  // 두 번째 fixture occurrence(페이지 420x260 포인트 기준 x 280~408, y 60~82)를
  // 덮도록 드래그해 다른 확정 마스크가 계속 남는 경로를 검증한다.
  await selectCanvasTool(page, "btn-canvas-tool-restore");
  const boxesBeforeRestore = await page.locator("#canvas-box-list button").count();
  await dragCanvas(page, { x: 0.67, y: 0.22 }, { x: 0.93, y: 0.37 });
  // T44R4: 직전 mask apply가 드래프트를 비웠으므로 이 드래그는 2번째가 아니라
  // (기존 수 + 1)번째 박스를 만든다. 하드코딩 대신 드래그 전 카운트에서 파생한다.
  check((await page.locator("#canvas-box-list button").count()) === boxesBeforeRestore + 1, "public text page: occurrence-bound restore drag adds a new draft box");
  check(/1개/.test((await page.locator("#review-summary-restore-count").textContent()) ?? ""), "public text page: summary shows 복원 박스 1개");
  const appliedRestoreCount = await page.locator("#canvas-box-list button").count();
  await page.locator("#btn-canvas-apply").click();
  await page.waitForTimeout(300);
  const syntheticRestore = await page.evaluate(() => ({
    applyCount: window.__QA_INVOKES__.filter((entry) => entry.cmd === "apply_manual_action_v1").length,
    status: document.querySelector("#status")?.textContent ?? "",
    boxCount: document.querySelectorAll("#canvas-box-list button").length,
  }));
  check(syntheticRestore.applyCount === 1, "public text page: synthetic restore does not reach the native action endpoint");
  check(syntheticRestore.boxCount === appliedRestoreCount, "public text page: synthetic restore keeps the draft box visible");
  check(syntheticRestore.status.includes("실제 캔버스 드래그"), "public text page: synthetic restore reports the trust-boundary rejection");
  await page.locator("#btn-canvas-undo").click();
  await page.waitForFunction(() => document.querySelectorAll("#canvas-box-list button").length === 0, null, { timeout: 8_000 });
  const disjointRestoreOverlay = await page.locator("#overlay-canvas-result").evaluate((element) => ({
    stagedRestoreCount: element.dataset.stagedRestoreCount ?? null,
    blockedRestoreCount: element.dataset.blockedRestoreCount ?? null,
    style: element.dataset.stagedOverlayStyle ?? null,
    restoreState: element.dataset.stagedRestoreState ?? null,
  }));
  check(
    disjointRestoreOverlay.stagedRestoreCount === "0"
      && disjointRestoreOverlay.blockedRestoreCount === "0",
    "public text page: rejected synthetic restore draft boxes cleared without changing detections",
  );
  const cleanState = await page.evaluate(() => ({
    state: document.querySelector("#final-state-card")?.getAttribute("data-state"),
    detail: document.querySelector("#final-state-detail")?.textContent ?? "",
  }));
  check(cleanState.state !== "fail" && !cleanState.detail.includes("복원 영역 때문에"), "public text page: rejected synthetic restore keeps the final save gate open");
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

  const publicPage = await browser.newPage({ viewport });
  publicPage.on("console", (m) => { if (m.type() === "error") errors.push({ surface: "public", text: m.text() }); });
  publicPage.on("pageerror", (e) => errors.push({ surface: "public", text: e.message }));
  await installTauriQaMocks(publicPage, { fixturePath, outputDir: path.join(evidenceDir, "output"), pdfBytes, analyzeDelayMs: 1_500 });
  await publicPage.goto(url, { waitUntil: "networkidle" });
  await publicPage.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
  await runPublicTextManualMaskSurface(publicPage);

  const malformedPublicPage = await browser.newPage({ viewport });
  malformedPublicPage.on("console", (m) => { if (m.type() === "error") errors.push({ surface: "public-malformed-successor", text: m.text() }); });
  malformedPublicPage.on("pageerror", (e) => errors.push({ surface: "public-malformed-successor", text: e.message }));
  await installTauriQaMocks(malformedPublicPage, {
    fixturePath,
    outputDir: path.join(evidenceDir, "output"),
    pdfBytes,
    malformedPublicManualMaskSuccessor: true,
  });
  await malformedPublicPage.goto(url, { waitUntil: "networkidle" });
  await malformedPublicPage.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
  await runPublicTextManualMaskSurface(malformedPublicPage, { malformedSuccessor: true });
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
