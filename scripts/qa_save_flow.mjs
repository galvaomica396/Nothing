// Final-save advisory flow QA (v4.2.0 정책 전환 회귀 가드).
//
// 확정된 정책(사용자 지시): **최종 저장은 항상 사용자 재량이다.** 검증(잔존 감지·
// 품질 게이트·복원 재검증)은 내부에서 계속 수행·기록되지만 결과는 "권고"로만
// 노출된다. 이전의 "하드 차단 3종 우회 불가" 정책은 폐기됐다. 마스킹본이 존재하면
// 저장 버튼은 항상 활성이고, 권고형 경고가 있으면 저장 직전 확인 다이얼로그가 1회
// 뜬다("무시하고 그대로 저장"/"취소하고 검토하기"). 경고가 없으면 다이얼로그 없이 곧바로 저장된다.
//
// 이 스크립트는 구성 가능한 Tauri IPC mock 위에서 전체 흐름(PDF → 기본 마스킹 →
// 저장)을 구동하며, 리포트 조합별로 다음을 단언한다:
//   - 경고가 없으면 다이얼로그 없이 바로 저장되고 finalize 가 호출된다.
//   - 경고가 있으면 저장 클릭 시 다이얼로그가 열리고(그 시점 finalize 미호출),
//     경고 목록에 정확한 권고 문구가 표출되며, "무시하고 그대로 저장" 시 finalize 가
//     호출되어 저장이 완료된다.
//   - "취소하고 검토하기" 시 finalize 는 호출되지 않고 저장되지 않는다.
//   - 불변식: finalize(=사용자 폴더로의 확정 저장)는 사용자가 명시적으로 확정한
//     경우에만 호출된다(경고 상태에서 미확인/취소 시 미호출).
//   - 리포트 내부화: 저장이 성공해도 safe_report 는 사용자 폴더에 절대 복사되지 않는다.
//
// Usage: node scripts/qa_save_flow.mjs [--url http://localhost:1420/]

import { spawn } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));

const args = new Map();
for (let index = 2; index < process.argv.length; index += 2) {
  args.set(process.argv[index], process.argv[index + 1]);
}

const url = args.get("--url") ?? "http://localhost:1420/";
const viewport = { width: 1440, height: 1080 };
const evidenceDir = path.join(repoRoot, "build", "save-flow-evidence");
const fixturePath = path.join(repoRoot, "tests", "fixtures", "phase6_non_sensitive.pdf");
const outputDir = path.join(evidenceDir, "output");
const previewDir = path.join(evidenceDir, "preview");

mkdirSync(evidenceDir, { recursive: true });
mkdirSync(outputDir, { recursive: true });
mkdirSync(previewDir, { recursive: true });
mkdirSync(path.join(repoRoot, "build", "redesign-evidence"), { recursive: true });

// ---------------------------------------------------------------------------
// 권고형 경고 문구(정확 문자열). src/features/save-gate/saveGate.ts finalSaveWarnings
// 와 1:1 대응해야 한다. {N}=건수.
// ---------------------------------------------------------------------------
const WARN = {
  residual: (count) => `잔존 개인정보 후보 ${count}건이 남아 있습니다. 보정 화면에서 확인하는 것을 권장합니다.`,
  missing: (count) => `마스킹되지 않은 대상 ${count}건이 있습니다. 보정 화면에서 확인하는 것을 권장합니다.`,
  quality: "자동 검증을 통과하지 못했습니다. 보정 화면에서 확인하는 것을 권장합니다.",
  advisory: "수동 검토가 권장되는 항목이 있습니다. 보정 화면에서 확인하는 것을 권장합니다.",
  restore: "복원 영역이 마스킹을 다시 노출할 수 있습니다. 보정 화면에서 확인하는 것을 권장합니다.",
};

// ---------------------------------------------------------------------------
// Safe-report scenarios. Each varies the product_checks / redaction / review
// combination the engine would return so we can prove the advisory flow behaves.
// ---------------------------------------------------------------------------
function baseOutputs() {
  return {
    preview_pdf_source_file: null,
    masked_pdf_file: null,
    safe_report_path: null,
    extracted_file: null,
    masked_file: null,
  };
}

function reviewItems(count) {
  const items = [];
  for (let index = 0; index < count; index += 1) {
    items.push({
      tag: "KEYWORD",
      display_token: "[KEYWORD]",
      status: "needs_review",
      count: 1,
    });
  }
  return items;
}

const SCENARIOS = [
  {
    // 경고 0건 → 다이얼로그 없이 곧바로 저장된다.
    id: "clean-pass",
    label: "자동 검증 통과 (경고 0건) — 다이얼로그 없이 바로 저장",
    productChecks: { quality_gate_passed: true, needs_manual_review: false },
    redaction: { status: "ok", verification: { residual_hits: 0 }, missing_targets_count: 0 },
    reviewItemCount: 0,
    expect: { warns: false, warnings: [], saves: true },
  },
  {
    // (구 residual-hard-block 재정의) 잔존>0 → 저장 클릭 → 다이얼로그에 경고 1
    // 표출 → "그대로 저장" → finalize 호출·저장 성공. 하드 차단 폐기 회귀 가드.
    id: "residual-warn-confirm-save",
    label: "잔존 개인정보 후보(경고 1) — 확인 다이얼로그 경유 그대로 저장",
    productChecks: { quality_gate_passed: true, needs_manual_review: false },
    redaction: { status: "unverified", verification: { residual_hits: 2 }, missing_targets_count: 0 },
    reviewItemCount: 2,
    expect: { warns: true, warnings: [WARN.residual(2)], saves: true },
  },
  {
    // (구 quality-gate-fail 재정의) 품질 게이트 실패 → 경고 3 → "그대로 저장" →
    // finalize 호출·저장 성공.
    id: "quality-warn-confirm-save",
    label: "품질 게이트 실패(경고 3) — 확인 다이얼로그 경유 그대로 저장",
    productChecks: { quality_gate_passed: false, needs_manual_review: false },
    redaction: { status: "unverified", verification: { residual_hits: 0 }, missing_targets_count: 0 },
    reviewItemCount: 1,
    expect: { warns: true, warnings: [WARN.quality], saves: true },
  },
  {
    // 자문(수동 검토 권장) → 경고 4 → "그대로 저장" → 저장 성공. 엔진이
    // needs_manual_review=true 를 켜되 final_submission_allowed 를 확정하지 않은
    // 자문 형태(omitFinalSubmissionAllowed)를 모사한다.
    id: "advisory-warn-confirm-save",
    label: "수동 검토 권장(경고 4) — 확인 다이얼로그 경유 그대로 저장",
    productChecks: { quality_gate_passed: true, needs_manual_review: true },
    omitFinalSubmissionAllowed: true,
    redaction: { status: "ok", verification: { residual_hits: 0 }, missing_targets_count: 0 },
    reviewItemCount: 2,
    expect: { warns: true, warnings: [WARN.advisory], saves: true },
  },
  {
    // 경고 상태에서 "취소" → finalize 미호출, 저장되지 않음. 저장은 사용자의
    // 명시적 확정으로만 일어난다는 불변식 가드.
    id: "warn-cancel-keeps-unsaved",
    label: "경고 상태에서 취소 — finalize 미호출·미저장",
    productChecks: { quality_gate_passed: true, needs_manual_review: false },
    redaction: { status: "unverified", verification: { residual_hits: 3 }, missing_targets_count: 0 },
    reviewItemCount: 1,
    expect: { warns: true, warnings: [WARN.residual(3)], cancel: true, saves: false },
  },
  {
    // 리포트 내부화 고정: 저장이 성공(경고 0건, 바로 저장)해도 finalize 는
    // copyReport:false 로 호출되어 safe_report 가 사용자 폴더에 미복사됨을 단언한다.
    id: "report-never-copied",
    label: "리포트 내부화 — 저장 성공 시에도 safe_report 는 사용자 폴더에 미복사",
    productChecks: { quality_gate_passed: true, needs_manual_review: false },
    redaction: { status: "ok", verification: { residual_hits: 0 }, missing_targets_count: 0 },
    reviewItemCount: 0,
    assertReportNotCopied: true,
    expect: { warns: false, warnings: [], saves: true },
  },
];

// The real engine (document_masker_ocr_gui.py build_safe_report) ALWAYS sets
// final_submission_allowed = quality_gate_passed. Mirror that here so every mock
// report matches the exact product_checks shape the frontend save flow receives.
// Exception: scenario.omitFinalSubmissionAllowed leaves the field absent to
// exercise the advisory manual-review warning (final_submission_allowed !== true).
function buildProductChecks(scenario) {
  const checks = { ...scenario.productChecks };
  if (!scenario.omitFinalSubmissionAllowed && checks.final_submission_allowed === undefined) {
    checks.final_submission_allowed = checks.quality_gate_passed === true;
  }
  return checks;
}

function buildReport(scenario) {
  const report = {
    extract: { engine_used: "auto" },
    outputs: baseOutputs(),
    product_checks: buildProductChecks(scenario),
    document_redaction: scenario.redaction,
    pdf_redaction: scenario.redaction,
    review_items: reviewItems(scenario.reviewItemCount),
    warnings: [],
  };
  // v4.1: 산출물 선택이 삭제되어 엔진 rules.output_artifacts 는 내부 고정
  // ("pdf_safe_report")이다. 저장 흐름은 이 필드를 읽지 않고 product_checks 만 본다.
  report.rules = { output_artifacts: ["pdf", "pdf_safe_report"] };
  return report;
}

// ---------------------------------------------------------------------------
// Dev server
// ---------------------------------------------------------------------------
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

// ---------------------------------------------------------------------------
// Page helpers
// ---------------------------------------------------------------------------
const FINALIZE_PAYLOAD_KEYS = [
  "copyReport",
  "extractedPath",
  "maskedPath",
  "originalPdf",
  "outputPath",
  "previewPdf",
  "reportPath",
  "saveToken",
];

function isExactFinalizePayload(payload) {
  return Object.keys(payload ?? {}).sort().join(",") === FINALIZE_PAYLOAD_KEYS.join(",")
    && Object.prototype.hasOwnProperty.call(payload, "copyReport")
    && payload.copyReport === false
    && payload.extractedPath === ""
    && typeof payload.maskedPath === "string"
    && typeof payload.saveToken === "string"
    && payload.saveToken.length > 0;
}
function createMockState(options = {}) {
  return {
    deletedPaths: new Set(),
    existingPaths: new Set([fixturePath]),
    finalizedPaths: [],
    finalizationCount: 0,
    tokens: new Map(),
    tokenCounter: 0,
    saveTokenCounter: 0,
    canvasLaunchAttempts: [],
    applyInputs: [],
    applyOperations: [],
    failFinalReadPending: options.failFinalRead === true,
    failCanvasTokenPending: options.failCanvasToken === true,
  };
}

function isPathWithin(candidate, directory) {
  const resolvedCandidate = path.resolve(candidate);
  const resolvedDirectory = path.resolve(directory);
  return resolvedCandidate === resolvedDirectory || resolvedCandidate.startsWith(`${resolvedDirectory}${path.sep}`);
}

function isDeletedMockPath(mockState, candidate) {
  return mockState.deletedPaths.has(path.resolve(candidate));
}

async function installMock(page, report, options = {}) {
  const pdfBytes = [...readFileSync(fixturePath)];
  const mockState = options.mockState ?? createMockState(options);
  // Restore-revalidation outcome for this scenario: "passed" (safe restore) or
  // "failed" (restore re-exposed a masked region). Mask-only applies never reach
  // this — the real backend returns no revalidation report for them.
  const manualOutcome = options.manualOutcome ?? "passed";
  let failManualPreviewLoad = options.failManualPreviewLoad === true;
  const finalizeDelayMs = options.finalizeDelayMs ?? 0;
  const applyDelayMs = options.applyDelayMs ?? 0;
  // The restore-revalidation safe report this scenario's backend would have
  // written. Hoisted so read_text_file (frontend adoption) reads the SAME report
  // bytes the real backend produced. A failed restore report keeps blocking
  // fields (residual/missing/quality) — under v4.2.0 these become advisory
  // warnings, never a hard block.
  const manualRevalReport =
    manualOutcome === "failed"
      ? {
          manual_revalidation: { status: "failed", verified: false },
          product_checks: { quality_gate_passed: false, needs_manual_review: true, final_submission_allowed: false },
          document_redaction: {
            status: "manual_revalidation_failed",
            missing_targets_count: 1,
            verification: { verified: false, residual_hits: 1, reason_code: "manual_restore_reexposure" },
          },
          review_items: [{ page: null, tag: "MANUAL", display_token: "[MASK]", status: "needs_review", count: 1, raw_value_saved: false }],
        }
      : {
          manual_revalidation: { status: "passed", verified: true },
          product_checks: { quality_gate_passed: true, needs_manual_review: false, final_submission_allowed: true },
          document_redaction: { status: "manual_revalidated", missing_targets_count: 0, verification: { verified: true, residual_hits: 0 } },
          review_items: [],
        };
  const invokeLog = [];
  const finalizeCalls = [];
  let selectedFinalTarget = null;
  await page.exposeFunction("__qaSaveInvoke", async (cmd, payload = {}) => {
    invokeLog.push(cmd);
    switch (cmd) {
      case "pick_input_pdf":
      case "pick_input_document":
        return fixturePath;
      case "pick_input_documents":
        return [fixturePath];
      case "choose_final_pdf_path":
        selectedFinalTarget = {
          outputPath: `${outputDir}/${String(payload.defaultFileName || "masked")}.pdf`,
          saveToken: `qa-save-${++mockState.saveTokenCounter}`,
        };
        return { ...selectedFinalTarget };
      case "get_preview_workdir":
        return previewDir;
      case "read_pdf_bytes":
        return pdfBytes;
      case "read_text_file":
        if (String(payload.path || "").includes("manual_revalidation.safe_report.json")) {
          // Mirrors the real backend contract: a restore-bearing apply performs
          // an actual revalidation and writes a passing OR blocking safe report.
          return JSON.stringify(manualRevalReport);
        }
        return "원문 REVIEW TOKEN\n마스킹 [KEYWORD]";
      case "run_masking_pipeline": {
        const maskedPreview = `${outputDir}/masked.pdf`;
        mockState.existingPaths.add(path.resolve(maskedPreview));
        return {
          extracted_path: "",
          masked_path: "",
          report_path: `${outputDir}/safe_report.json`,
          extracted_text: "",
          masked_text: "",
          runtime_manifest: {
            outputs: {
              preview_pdf_source_file: fixturePath,
              masked_pdf_file: maskedPreview,
              safe_report_path: `${outputDir}/safe_report.json`,
              extracted_file: null,
              masked_file: null,
            },
          },
          report,
        };
      }
      case "apply_manual_boxes": {
        if (applyDelayMs > 0) {
          await new Promise((resolve) => setTimeout(resolve, applyDelayMs));
        }
        const inputPdf = String(payload.inputPdf || "");
        const originalPdf = String(payload.originalPdf || "");
        mockState.applyInputs.push(inputPdf);
        if (isDeletedMockPath(mockState, inputPdf)) {
          throw new Error("APPLY_SOURCE_REJECTED: preview was deleted after final save");
        }
        const boxes = payload.boxes ?? [];
        mockState.applyOperations.push({
          inputPdf,
          originalPdf,
          maskCount: boxes.filter((box) => box.mode === "mask").length,
          restoreCount: boxes.filter((box) => box.mode === "restore").length,
        });
        const restoreApplied = boxes.filter((box) => box.mode === "restore").length;
        const requiresRevalidation = restoreApplied > 0;
        const manualPreview = `${previewDir}/manual_preview_${mockState.applyInputs.length}.pdf`;
        mockState.existingPaths.add(path.resolve(manualPreview));
        const result = {
          status: "applied",
          output_file: manualPreview,
          mask_count: boxes.filter((box) => box.mode === "mask").length,
          restore_count: restoreApplied,
          applied_count: boxes.length,
          mask_boxes_applied: boxes.filter((box) => box.mode === "mask").length,
          unmask_boxes_applied: restoreApplied,
          skipped_boxes: 0,
          warnings: [],
          requires_revalidation: requiresRevalidation,
        };
        if (requiresRevalidation) {
          const passed = manualOutcome !== "failed";
          result.revalidation_report = `${previewDir}/manual_preview.manual_revalidation.safe_report.json`;
          result.revalidation_status = passed ? "passed" : "failed";
        }
        return result;
      }
      case "finalize_manual_output_to_selected_path": {
        // Rust의 exact-path 최종 저장과 동일하게, 검증 리포트는 저장을 막지 않고
        // 네이티브 저장 창에서 선택한 정확한 PDF 경로만 허용한다.
        const previewPdf = String(payload.previewPdf || "");
        const selectedOutputPath = String(payload.outputPath || "");
        const selectedSaveToken = String(payload.saveToken || "");
        const canonicalPreview = previewPdf ? path.resolve(previewPdf) : "";
        const canonicalOutput = selectedOutputPath ? path.resolve(selectedOutputPath) : "";
        const confirmedTarget = selectedFinalTarget;
        selectedFinalTarget = null;
        if (
          !confirmedTarget
          || canonicalOutput !== path.resolve(confirmedTarget.outputPath)
          || selectedSaveToken !== confirmedTarget.saveToken
        ) {
          throw new Error("SAVE_OUTPUT_PATH_REJECTED: 저장 경로를 확인할 수 없습니다.");
        }
        const previewIsRegistered =
          canonicalPreview.startsWith(`${path.resolve(outputDir)}${path.sep}`)
          || canonicalPreview.startsWith(`${path.resolve(previewDir)}${path.sep}`);
        if (!previewIsRegistered) {
          throw new Error("SAVE_SOURCE_REJECTED: 저장 원본을 확인할 수 없습니다.");
        }
        // v4.1: Rust finalize copy_report 기본값 false 이고 프론트는 항상
        // copyReport:false 로 호출한다. 리포트는 사용자 산출 폴더로 복사되지 않으므로
        // copied_files 에는 safe_report 가 포함되면 안 된다(내부 리포트는 임시폴더에만).
        if (
          isDeletedMockPath(mockState, canonicalPreview)
          || !mockState.existingPaths.has(canonicalPreview)
          || path.extname(canonicalPreview).toLowerCase() !== ".pdf"
        ) {
          throw new Error("SAVE_SOURCE_REJECTED: preview is missing, deleted, or not a PDF.");
        }
        const copyReport = payload.copyReport === true;
        const copiedFiles = copyReport ? [`${outputDir}/safe_report.json`] : [];
        mockState.finalizationCount += 1;
        const finalOutput = confirmedTarget.outputPath;
        mockState.existingPaths.add(path.resolve(finalOutput));
        mockState.finalizedPaths.push(path.resolve(finalOutput));
        if (isPathWithin(canonicalPreview, previewDir)) {
          mockState.deletedPaths.add(canonicalPreview);
          mockState.existingPaths.delete(canonicalPreview);
        }
        finalizeCalls.push({
          cmd,
          previewPdf,
          finalOutput,
          copyReport,
          copied_files: copiedFiles,
          rawPayload: { ...payload },
        });
        if (finalizeDelayMs > 0) {
          await new Promise((resolve) => setTimeout(resolve, finalizeDelayMs));
        }
        return { final_output_file: finalOutput, copied_files: copiedFiles };
      }
      case "create_canvas_launch_token": {
        const tokenPayload = payload.payload ?? {};
        const targetPath = String(tokenPayload.targetPath || "");
        mockState.canvasLaunchAttempts.push(targetPath);
        if (
          targetPath
          && (
            isDeletedMockPath(mockState, targetPath)
            || !mockState.existingPaths.has(path.resolve(targetPath))
          )
        ) {
          throw new Error("CANVAS_SOURCE_REJECTED: target PDF is missing or deleted");
        }
        if (mockState.failCanvasTokenPending) {
          mockState.failCanvasTokenPending = false;
          throw new Error("CANVAS_SOURCE_REJECTED: injected final candidate failure");
        }
        const token = `qa-token-${++mockState.tokenCounter}`;
        mockState.tokens.set(token, tokenPayload);
        return token;
      }
      case "take_canvas_launch_payload": {
        const token = String(payload.token || "");
        const tokenPayload = mockState.tokens.get(token) ?? null;
        mockState.tokens.delete(token);
        return tokenPayload;
      }
      case "plugin:opener|open_path":
      case "open_mask_canvas_window":
        return "ok";
      default:
        return null;
    }
  });
  await page.exposeFunction("__qaSaveCheckPdfPath", async (requestedPath = "") => {
    invokeLog.push("read_pdf_bytes");
    const canonicalPath = path.resolve(String(requestedPath || ""));
    if (
      isDeletedMockPath(mockState, canonicalPath)
      || !mockState.existingPaths.has(canonicalPath)
      || path.extname(canonicalPath).toLowerCase() !== ".pdf"
    ) {
      throw new Error("READ_FAILED: PDF path is missing, deleted, or unauthorized");
    }
    if (mockState.failFinalReadPending && mockState.finalizedPaths.includes(canonicalPath)) {
      mockState.failFinalReadPending = false;
      throw new Error("READ_FAILED: injected final successor load failure");
    }
    if (failManualPreviewLoad && canonicalPath.includes("manual_preview_")) {
      failManualPreviewLoad = false;
      throw new Error("READ_FAILED: injected manual preview load failure");
    }
    return true;
  });
  await page.addInitScript(({ localPdfBytes }) => {
    window.confirm = () => true;
    let callbackId = 1;
    window.__TAURI_INTERNALS__ = {
      plugins: { path: { sep: "/", delimiter: ":" } },
      // Keep large PDF bytes in-page. The Node-side probe validates existence,
      // deletion, extension, and injected failures without serializing the PDF
      // through the Playwright bridge on every render.
      invoke: (cmd, payload) => {
        if (cmd === "read_pdf_bytes") {
          const pathValue = String(payload?.path ?? "");
          return window.__qaSaveCheckPdfPath(pathValue).then(() => localPdfBytes);
        }
        return window.__qaSaveInvoke(cmd, payload);
      },
      transformCallback: (callback) => {
        const id = callbackId++;
        window[`__qa_callback_${id}`] = callback;
        return id;
      },
      unregisterCallback: () => {},
    };
    window.__TAURI__ = { core: { invoke: window.__TAURI_INTERNALS__.invoke } };
  }, { localPdfBytes: pdfBytes });
  return { invokeLog, finalizeCalls, mockState };
}

async function waitForStatus(page, pattern, timeout = 60_000) {
  await page.waitForFunction(
    (source) => new RegExp(source).test(document.querySelector("#status")?.textContent ?? ""),
    pattern.source,
    { timeout },
  );
  return page.locator("#status").innerText();
}

async function pickPdfAndMask(page) {
  // React markup can be attached a few ticks before startLegacyApp finishes
  // binding its handlers. Waiting for the bootstrap terminal status makes each
  // isolated scenario deterministic instead of racing the first click.
  await page.waitForFunction(
    () => (document.querySelector("#status")?.textContent ?? "").includes("대기 중: PDF 열기"),
    undefined,
    { timeout: 20_000 },
  );
  await page.locator("#btn-pick-pdf").click();
  await page.waitForFunction((expected) => document.querySelector("#input-path")?.value === expected, fixturePath, { timeout: 20_000 });
  const loadStatus = await waitForStatus(page, /원문 PDF 로드 완료|문서 로드 실패/, 20_000);
  if (loadStatus.includes("실패")) throw new Error(`document load failed: ${loadStatus}`);
  await page.locator("#btn-run-masking").click();
  const status = await waitForStatus(page, /마스킹 완료|마스킹 실패/);
  if (status.includes("실패")) throw new Error(`base masking failed: ${status}`);
  await page.waitForFunction(
    () => document.querySelector("#btn-canvas-tool-mask")?.disabled === false,
    undefined,
    { timeout: 20_000 },
  );
}


async function saveButtonState(page) {
  return page.evaluate(() => ({
    disabled: document.querySelector("#btn-save")?.disabled === true,
    title: document.querySelector("#btn-save")?.getAttribute("title") ?? "",
    readiness: document.querySelector("#final-save-readiness")?.textContent ?? "",
  }));
}

// 저장 전 확인 다이얼로그의 현재 상태를 읽는다. 신설 ID(final-save-warning-list,
// final-save-dialog-state)로 단언하며, 권고 항목(dm-savewarn__item)만 warnings 로
// 추린다(빈 상태 dm-savewarn__empty 는 제외).
async function finalSaveDialogState(page) {
  return page.evaluate(() => {
    const dialog = document.querySelector("#final-save-dialog");
    const open = Boolean(dialog) && !dialog.classList.contains("is-hidden");
    const listEl = document.querySelector("#final-save-warning-list");
    const rows = listEl
      ? [...listEl.querySelectorAll("li")].map((li) => ({
          text: (li.textContent ?? "").trim(),
          empty: li.classList.contains("dm-savewarn__empty"),
        }))
      : [];
    const cancelBtn = document.querySelector("#btn-dialog-cancel-save");
    const confirmBtn = document.querySelector("#btn-dialog-save-all");
    return {
      open,
      badge: (document.querySelector("#final-save-dialog-state")?.textContent ?? "").trim(),
      warnings: rows.filter((row) => !row.empty).map((row) => row.text),
      emptyRows: rows.filter((row) => row.empty).map((row) => row.text),
      cancelPresent: Boolean(cancelBtn),
      cancelText: (cancelBtn?.textContent ?? "").trim(),
      confirmText: (confirmBtn?.textContent ?? "").trim(),
      confirmDisabled: confirmBtn?.disabled === true,
      cancelSecondary: cancelBtn?.classList.contains("dm-btn--ghost") === true && cancelBtn?.classList.contains("dm-btn--danger") !== true,
      confirmPrimary: confirmBtn?.classList.contains("dm-btn--primary") === true && confirmBtn?.classList.contains("dm-btn--danger") !== true,
    };
  });
}

async function dialogIsOpen(page) {
  return page.evaluate(() => {
    const dialog = document.querySelector("#final-save-dialog");
    return Boolean(dialog) && !dialog.classList.contains("is-hidden");
  });
}

// 저장 버튼을 클릭하고, "다이얼로그가 열리거나(경고)" 또는 "종결 상태 문구가
// 렌더될 때까지(경고 없음/전제 미충족)" 결정적으로 기다린다.
async function clickSaveAndSettle(page) {
  const disabled = await page.locator("#btn-save").isDisabled();
  if (disabled) return { clicked: false };
  await page.locator("#btn-save").evaluate((element) => element.click());
  await page.waitForFunction(
    () => {
      const dialog = document.querySelector("#final-save-dialog");
      const open = Boolean(dialog) && !dialog.classList.contains("is-hidden");
      const status = document.querySelector("#status")?.textContent ?? "";
      return open || /최종 저장 완료|최종 저장 실패|파일은 저장되었으나|저장할 마스킹본이 없습니다/.test(status);
    },
    undefined,
    { timeout: 40_000 },
  );
  return { clicked: true };
}

async function confirmDialogSave(page) {
  await page.locator("#btn-dialog-save-all").click();
  const status = await waitForStatus(page, /최종 저장 완료|최종 저장 실패|파일은 저장되었으나/, 40_000);
  return { saved: status.includes("완료"), status };
}

async function cancelDialogSave(page) {
  await page.locator("#btn-dialog-cancel-save").click();
  await page.waitForFunction(
    () => {
      const dialog = document.querySelector("#final-save-dialog");
      return !dialog || dialog.classList.contains("is-hidden");
    },
    undefined,
    { timeout: 10_000 },
  );
}

// Drag on the visible PDF to draw a manual box (same primitive the canvas QA uses).
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

async function totalBoxCount(page) {
  const text = (await page.locator("#box-info").textContent()) ?? "";
  const match = text.match(/전체\s+(\d+)\s*개/);
  return match ? Number(match[1]) : NaN;
}

async function waitForInvoke(invokeLog, cmd, timeout = 30_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (invokeLog.includes(cmd)) return true;
    await new Promise((resolve) => setTimeout(resolve, 40));
  }
  throw new Error(`invoke '${cmd}' not observed within ${timeout}ms (log=${invokeLog.join(",")})`);
}

async function waitForApplyComplete(page, invokeLog) {
  await waitForInvoke(invokeLog, "apply_manual_boxes");
  // 성공 미리보기 "(미리보기):" 는 복원 재검증 실패 시 곧바로 "복원 반영됨 — …"
  // 종결 문구로 동기 덮어써지므로(레이스), 그 종결 문구도 대기 대상에 포함한다.
  await waitForStatus(page, /\(미리보기\):|재검증 필요|복원 반영됨|실패/, 30_000);
}

function finalizeCount(invokeLog) {
  return invokeLog.filter((cmd) => cmd === "finalize_manual_output_to_selected_path").length;
}

function saveDialogCount(invokeLog) {
  return invokeLog.filter((cmd) => cmd === "choose_final_pdf_path").length;
}

// ---------------------------------------------------------------------------
// Manual-correction save scenarios (v4.2.0 advisory model). After applying a
// manual correction:
//   · mask-only add   → no revalidation needed, no warnings → direct save.
//   · restore add (passed) → revalidation auto-runs, clean report adopted → save.
//   · restore add (failed) → 재검증 실패해도 하드 차단하지 않는다: 경고 5(복원
//     재노출 가능)와 함께 확인 다이얼로그가 뜨고, "그대로 저장" 으로 저장할 수 있다.
// saveVia distinguishes the two ways an apply reaches the save flow:
//   · "button-apply" — the user clicks 반영(apply) first, then 저장(save).
//   · "auto-apply"   — the user skips apply and clicks 저장 directly; saveFinalOutput
//                      auto-applies the pending boxes, then evaluates warnings.
// ---------------------------------------------------------------------------
const MANUAL_SCENARIOS = [
  {
    id: "manual-mask-only-keeps-gate",
    label: "수동 마스킹 박스만 추가 → 반영 후 저장: 경고 없음, 다이얼로그 없이 저장 성공",
    tool: "mask",
    manualOutcome: "passed",
    saveVia: "button-apply",
    expect: { warns: false, saves: true },
  },
  {
    id: "manual-mask-only-autoapply-saves",
    label: "수동 마스킹 박스만 추가 → 반영 없이 저장: 자동 반영 경로로 바로 저장 성공",
    tool: "mask",
    manualOutcome: "passed",
    saveVia: "auto-apply",
    expect: { warns: false, saves: true },
  },
  {
    id: "manual-restore-auto-revalidate",
    label: "복원 포함(재검증 통과) → 반영 후 저장: 통과 리포트 채택, 경고 없이 저장 성공",
    tool: "restore",
    manualOutcome: "passed",
    saveVia: "button-apply",
    expect: { warns: false, saves: true },
  },
  {
    id: "manual-restore-reexposure-confirm-save",
    label: "복원 재노출(반영 후 저장) → 재검증 실패해도 경고 5와 함께 확인 다이얼로그 경유 저장 가능",
    tool: "restore",
    manualOutcome: "failed",
    saveVia: "button-apply",
    expect: { warns: true, expectRestoreWarning: true, saves: true },
  },
  {
    id: "manual-restore-reexposure-autoapply-confirm-save",
    label: "복원 재노출(반영 없이 바로 저장) → 자동 반영 경로가 경고 5 다이얼로그를 띄우고 확인 시 저장",
    tool: "restore",
    manualOutcome: "failed",
    saveVia: "auto-apply",
    expect: { warns: true, expectRestoreWarning: true, saves: true },
  },
];

async function runManualScenario(browser, scenario) {
  const page = await browser.newPage({ viewport });
  const consoleErrors = [];
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  const result = { id: scenario.id, label: scenario.label, checks: [], pass: true };
  const check = (name, ok, detail) => {
    result.checks.push({ name, ok, detail });
    if (!ok) result.pass = false;
  };
  try {
    const cleanReport = buildReport(SCENARIOS[0]);
    const manualLoadFailureProbe = scenario.id === "manual-mask-only-keeps-gate";
    const { invokeLog } = await installMock(page, cleanReport, {
      manualOutcome: scenario.manualOutcome,
      failManualPreviewLoad: manualLoadFailureProbe,
    });
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });

    await pickPdfAndMask(page);

    // 저장 버튼은 마스킹본이 있으면 항상 활성이다(사용자 재량).
    const saveOpenBefore = await saveButtonState(page);
    check("save-enabled-when-masked", saveOpenBefore.disabled === false, `title=${saveOpenBefore.title}`);

    await page.locator(`#btn-canvas-tool-${scenario.tool}`).click();
    await dragOnPdf(page, { x: 0.15, y: 0.2 }, { x: 0.6, y: 0.4 });
    check("manual-box-drawn", (await totalBoxCount(page)) === 1, "one manual box created");

    const baseRunsBeforeApply = invokeLog.filter((cmd) => cmd === "run_masking_pipeline").length;

    if (scenario.saveVia === "button-apply") {
      await page.locator("#btn-canvas-apply").click();
      await waitForApplyComplete(page, invokeLog);
      if (manualLoadFailureProbe) {
        check("failed-manual-load-keeps-box", (await totalBoxCount(page)) === 1, "one box retained after failed preview load");
        check("failed-manual-load-does-not-finalize", finalizeCount(invokeLog) === 0, `count=${finalizeCount(invokeLog)}`);
        await page.locator("#btn-canvas-apply").click();
        await waitForStatus(page, /\(미리보기\):/, 30_000);
      }
      // Core regression: recovering the flow must NOT require re-running base masking.
      const baseRunsAfterApply = invokeLog.filter((cmd) => cmd === "run_masking_pipeline").length;
      check("no-base-masking-rerun", baseRunsAfterApply === baseRunsBeforeApply, `before=${baseRunsBeforeApply} after=${baseRunsAfterApply}`);
    }

    const finalizeBeforeSave = finalizeCount(invokeLog);
    const clickRes = await clickSaveAndSettle(page);
    check("save-click-registered", clickRes.clicked === true, `clicked=${clickRes.clicked}`);

    if (scenario.saveVia === "auto-apply") {
      check("autoapply-invoked", invokeLog.includes("apply_manual_boxes"), "auto-apply ran during save");
      const baseRunsAfterSave = invokeLog.filter((cmd) => cmd === "run_masking_pipeline").length;
      check("no-base-masking-rerun", baseRunsAfterSave === baseRunsBeforeApply, `before=${baseRunsBeforeApply} after=${baseRunsAfterSave}`);
    }

    if (scenario.expect.warns) {
      const dialog = await finalSaveDialogState(page);
      check("warning-dialog-opened", dialog.open === true, `open=${dialog.open}`);
      if (scenario.expect.expectRestoreWarning) {
        check("restore-warning-present", dialog.warnings.includes(WARN.restore), `warnings=${JSON.stringify(dialog.warnings)}`);
      }
      // Invariant: finalize must NOT be called while the dialog is unconfirmed.
      check("finalize-not-called-before-confirm", finalizeCount(invokeLog) === finalizeBeforeSave, `count=${finalizeCount(invokeLog)}`);
      const confirm = await confirmDialogSave(page);
      check("saved-after-confirm", confirm.saved === scenario.expect.saves, `saved=${confirm.saved} expected=${scenario.expect.saves} status=${confirm.status}`);
    } else {
      check("no-warning-dialog", (await dialogIsOpen(page)) === false, "dialog stayed closed");
      const status = await page.locator("#status").innerText();
      check("saved-directly", /최종 저장 완료/.test(status) === scenario.expect.saves, `status=${status}`);
    }

    // finalize(=사용자 확정 저장)는 저장이 실제로 완료된 경우에만 호출되어야 한다.
    const expectedFinalizeCount = scenario.expect.saves ? 1 : 0;
    check("finalize-called-exactly-once", finalizeCount(invokeLog) === expectedFinalizeCount, `count=${finalizeCount(invokeLog)} expected=${expectedFinalizeCount}`);

    check("no-page-errors", consoleErrors.length === 0, consoleErrors.join(" | "));
    await page.screenshot({ path: path.join(evidenceDir, `${scenario.id}.png`), fullPage: true });
  } catch (error) {
    check("scenario-threw", false, error instanceof Error ? error.message : String(error));
    await page.screenshot({ path: path.join(evidenceDir, `${scenario.id}-error.png`), fullPage: true }).catch(() => {});
  } finally {
    await page.close();
  }
  return result;
}

async function runScenario(browser, scenario) {
  const page = await browser.newPage({ viewport });
  const consoleErrors = [];
  page.on("pageerror", (error) => consoleErrors.push(error.message));
  const result = { id: scenario.id, label: scenario.label, checks: [], pass: true };
  const check = (name, ok, detail) => {
    result.checks.push({ name, ok, detail });
    if (!ok) result.pass = false;
  };
  try {
    const doubleSaveProbe = scenario.id === "clean-pass";
    const { invokeLog, finalizeCalls } = await installMock(page, buildReport(scenario), {
      finalizeDelayMs: doubleSaveProbe ? 800 : 0,
    });
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });

    await pickPdfAndMask(page);

    // v4.2.0: 마스킹본이 존재하면 저장 버튼은 검증 결과와 무관하게 항상 활성이다.
    const saveBefore = await saveButtonState(page);
    check("save-enabled-when-masked", saveBefore.disabled === false, `disabled=${saveBefore.disabled} title=${saveBefore.title}`);

    // 저장 클릭: 경고가 있으면 다이얼로그가 열리고, 없으면 바로 저장이 종결된다.
    let clickRes;
    if (doubleSaveProbe) {
      await page.locator("#btn-save").click();
      await waitForInvoke(invokeLog, "finalize_manual_output_to_selected_path");
      const disabledDuringSave = await page.evaluate(() => ({
        primary: document.querySelector("#btn-save")?.disabled === true,
        canvas: document.querySelector("#btn-canvas-final-save")?.disabled === true,
        maskTool: document.querySelector("#btn-canvas-tool-mask")?.disabled === true,
        restoreTool: document.querySelector("#btn-canvas-tool-restore")?.disabled === true,
        deleteTool: document.querySelector("#btn-canvas-tool-delete")?.disabled === true,
      }));
      await page.evaluate(() => {
        document.querySelector("#btn-canvas-final-save")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      });
      await page.waitForTimeout(50);
      const boxesBeforeConcurrentDrag = await totalBoxCount(page);
      await dragOnPdf(page, { x: 0.2, y: 0.2 }, { x: 0.45, y: 0.35 });
      const boxesAfterConcurrentDrag = await totalBoxCount(page);
      check("save-buttons-disabled-during-finalize", disabledDuringSave.primary && disabledDuringSave.canvas, JSON.stringify(disabledDuringSave));
      check("canvas-edits-locked-during-finalize", boxesAfterConcurrentDrag === boxesBeforeConcurrentDrag, `before=${boxesBeforeConcurrentDrag} after=${boxesAfterConcurrentDrag}`);
      check("canvas-tools-disabled-during-finalize", disabledDuringSave.maskTool && disabledDuringSave.restoreTool && disabledDuringSave.deleteTool, JSON.stringify(disabledDuringSave));
      check("concurrent-save-finalizes-once", finalizeCount(invokeLog) === 1, `count=${finalizeCount(invokeLog)}`);
      await waitForStatus(page, /최종 저장 완료|최종 저장 실패/, 40_000);
      clickRes = { clicked: true };
    } else {
      clickRes = await clickSaveAndSettle(page);
    }
    check("save-click-registered", clickRes.clicked === true, `clicked=${clickRes.clicked}`);

    if (scenario.expect.warns) {
      const dialog = await finalSaveDialogState(page);
      check("warning-dialog-opened", dialog.open === true, `open=${dialog.open}`);
      check(
        "warning-list-matches-exact",
        JSON.stringify(dialog.warnings) === JSON.stringify(scenario.expect.warnings),
        `got=${JSON.stringify(dialog.warnings)} expected=${JSON.stringify(scenario.expect.warnings)}`,
      );
      check("dialog-badge-shows-count", dialog.badge === `확인 권장 ${scenario.expect.warnings.length}건`, `badge=${dialog.badge}`);
      // 권고형 선택 카피("취소하고 검토하기"/"무시하고 그대로 저장") 가드.
      check("cancel-control-present", dialog.cancelPresent && dialog.cancelText === "취소하고 검토하기", `cancelText=${dialog.cancelText}`);
      check("confirm-control-label", dialog.confirmText === "무시하고 그대로 저장" && dialog.confirmDisabled === false, `confirmText=${dialog.confirmText} disabled=${dialog.confirmDisabled}`);
      check("cancel-control-hierarchy", dialog.cancelSecondary === true, `secondary=${dialog.cancelSecondary}`);
      check("confirm-control-hierarchy", dialog.confirmPrimary === true, `primary=${dialog.confirmPrimary}`);
      // 불변식: 다이얼로그가 미확인 상태일 때 finalize 는 호출되지 않아야 한다.
      check("finalize-not-called-before-confirm", finalizeCount(invokeLog) === 0, `count=${finalizeCount(invokeLog)}`);

      if (scenario.expect.cancel) {
        await cancelDialogSave(page);
        check("finalize-not-called-after-cancel", finalizeCount(invokeLog) === 0, `count=${finalizeCount(invokeLog)}`);
        const status = await page.locator("#status").innerText();
        check("not-saved-after-cancel", /최종 저장 완료/.test(status) === false, `status=${status}`);
      } else {
        const confirm = await confirmDialogSave(page);
        check("saved-after-confirm", confirm.saved === scenario.expect.saves, `saved=${confirm.saved} expected=${scenario.expect.saves} status=${confirm.status}`);
      }
    } else {
      // 경고 0건: 다이얼로그 없이 곧바로 저장된다.
      check("no-warning-dialog", (await dialogIsOpen(page)) === false, "dialog stayed closed");
      const status = await page.locator("#status").innerText();
      check("saved-directly", /최종 저장 완료/.test(status) === scenario.expect.saves, `status=${status}`);
    }

    // 불변식: finalize 는 저장이 실제로 완료된 경우에만 호출된다(경고+취소 시 미호출).
    const expectedFinalizeCount = scenario.expect.saves ? 1 : 0;
    check("finalize-called-exactly-once", finalizeCount(invokeLog) === expectedFinalizeCount, `count=${finalizeCount(invokeLog)} expected=${expectedFinalizeCount}`);
    check("native-save-dialog-called-once", saveDialogCount(invokeLog) === expectedFinalizeCount, `count=${saveDialogCount(invokeLog)} expected=${expectedFinalizeCount}`);
    if (scenario.expect.saves) {
      const outputPath = String(finalizeCalls[0]?.rawPayload?.outputPath || "");
      check(
        "native-default-filename-used",
        path.basename(outputPath) === "phase6_non_sensitive_masked.pdf",
        `outputPath=${outputPath}`,
      );
      check(
        "legacy-output-dir-omitted",
        !Object.hasOwn(finalizeCalls[0]?.rawPayload ?? {}, "outputDir"),
        `payload=${JSON.stringify(finalizeCalls[0]?.rawPayload ?? {})}`,
      );
      const completionActions = await page.evaluate(() => ({
        newWorkVisible: !document.querySelector("#btn-new-document")?.classList.contains("is-hidden"),
        applyHidden: document.querySelector("#btn-canvas-apply")?.classList.contains("is-hidden") === true,
        saveHidden: document.querySelector("#btn-canvas-final-save")?.classList.contains("is-hidden") === true,
      }));
      check("saved-session-replaces-commit-actions", completionActions.newWorkVisible && completionActions.applyHidden && completionActions.saveHidden, JSON.stringify(completionActions));
      if (scenario.id === "manual-mask-only-keeps-gate") {
        await page.locator("#btn-new-document").click();
        await page.waitForFunction(() => !document.querySelector("#canvas-wrap-result")?.classList.contains("has-rendered-pdf"));
        const resetState = await page.evaluate(() => ({
          title: document.querySelector("#current-document-title")?.textContent,
          path: document.querySelector("#input-path")?.value,
          queue: document.querySelectorAll("#batch-queue .batch-item").length,
          heroVisible: getComputedStyle(document.querySelector(".dm-canvas__hero")).display !== "none",
        }));
        check("new-work-reset-restores-empty-hero", resetState.title === "문서를 선택하세요" && resetState.path === "" && resetState.queue === 0 && resetState.heroVisible, JSON.stringify(resetState));
      }
    }

    // report-never-copied: 저장이 성공한 경우에도 finalize 는 copyReport:false 로
    // 호출되어야 하고 copied_files 에 safe_report 가 들어가면 안 된다.
    if (scenario.assertReportNotCopied) {
      const copiedAll = finalizeCalls.flatMap((call) => call.copied_files);
      check(
        "finalize-copyReport-always-false",
        finalizeCalls.length > 0 && finalizeCalls.every((call) => call.copyReport === false),
        `calls=${JSON.stringify(finalizeCalls.map((call) => call.copyReport))}`,
      );
      check(
        "safe-report-never-copied-to-output",
        !copiedAll.some((file) => /safe_report/.test(file)),
        `copied=${JSON.stringify(copiedAll)}`,
      );
    }

    check("no-page-errors", consoleErrors.length === 0, consoleErrors.join(" | "));
    await page.screenshot({ path: path.join(evidenceDir, `${scenario.id}.png`), fullPage: true });
  } catch (error) {
    check("scenario-threw", false, error instanceof Error ? error.message : String(error));
    await page.screenshot({ path: path.join(evidenceDir, `${scenario.id}-error.png`), fullPage: true }).catch(() => {});
  } finally {
    await page.close();
  }
  return result;
}

async function runManualClearRaceScenario(browser) {
  const page = await browser.newPage({ viewport });
  const result = { id: "manual-apply-clear-race", label: "수동 반영 중 Clear 차단 및 세션 소유권 유지", checks: [], pass: true };
  const check = (name, ok, detail) => {
    result.checks.push({ name, ok, detail });
    if (!ok) result.pass = false;
  };
  try {
    const { invokeLog } = await installMock(page, buildReport(SCENARIOS[0]), { applyDelayMs: 300 });
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
    await pickPdfAndMask(page);
    await page.locator("#btn-canvas-tool-mask").click();
    await dragOnPdf(page, { x: 0.15, y: 0.2 }, { x: 0.6, y: 0.4 });
    await page.locator("#btn-canvas-apply").click();
    await waitForInvoke(invokeLog, "apply_manual_boxes");
    const disabled = await page.evaluate(() => ({
      primary: document.querySelector("#btn-clear")?.disabled === true,
      canvas: document.querySelector("#btn-canvas-clear")?.disabled === true,
    }));
    check("clear-controls-disabled-during-apply", disabled.primary && disabled.canvas, JSON.stringify(disabled));
    await page.evaluate(() => {
      document.querySelector("#btn-clear")?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    await waitForApplyComplete(page, invokeLog);
    check("manual-result-commits-after-blocked-clear", (await totalBoxCount(page)) === 0, `boxes=${await totalBoxCount(page)}`);
    check("apply-finalized-once", invokeLog.filter((cmd) => cmd === "apply_manual_boxes").length === 1, `count=${invokeLog.filter((cmd) => cmd === "apply_manual_boxes").length}`);
  } catch (error) {
    check("scenario-threw", false, error instanceof Error ? error.message : String(error));
  } finally {
    await page.close();
  }
  return result;
}
async function runPostSaveContinuationScenario(browser, mode) {
  const page = await browser.newPage({ viewport });
  const result = {
    id: mode === "inline" ? "post-save-inline-final-successor" : "post-save-detached-final-successor",
    label: mode === "inline" ? "최종 저장 뒤 인라인 복원은 확정본에서 계속" : "최종 저장 뒤 독립 캔버스 복원은 확정본에서 계속",
    checks: [],
    pass: true,
  };
  const check = (name, ok, detail) => {
    result.checks.push({ name, ok, detail });
    if (!ok) result.pass = false;
  };
  let detachedPage = null;
  try {
    const mockState = createMockState();
    const firstMock = await installMock(page, buildReport(SCENARIOS[0]), { mockState });
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
    await pickPdfAndMask(page);
    await page.locator("#btn-canvas-tool-mask").click();
    await dragOnPdf(page, { x: 0.15, y: 0.2 }, { x: 0.6, y: 0.4 });
    await page.locator("#btn-canvas-apply").click();
    await waitForApplyComplete(page, firstMock.invokeLog);
    await clickSaveAndSettle(page);
    const firstFinalize = firstMock.finalizeCalls[0];
    const firstSubmittedPreview = path.resolve(firstFinalize?.previewPdf || "");
    const firstFinal = mockState.finalizedPaths[0];
    check("first-submitted-preview-deleted", isDeletedMockPath(mockState, firstSubmittedPreview), `preview=${firstSubmittedPreview}`);
    check("first-final-exists-in-mock-state", Boolean(firstFinal) && mockState.existingPaths.has(firstFinal), `final=${firstFinal}`);
    check("first-save-keeps-wire-and-privacy-contract", isExactFinalizePayload(firstFinalize?.rawPayload) && firstFinalize?.copied_files.length === 0, JSON.stringify(firstFinalize ?? {}));

    let continuationPage = page;
    let continuationLog = firstMock.invokeLog;
    let continuationFinalizes = firstMock.finalizeCalls;
    if (mode === "detached") {
      await page.locator("#btn-mask-canvas").evaluate((element) => element.click());
      await waitForInvoke(firstMock.invokeLog, "create_canvas_launch_token");
      const [token] = [...mockState.tokens.keys()];
      const launchPayload = mockState.tokens.get(token);
      check("detached-token-targets-first-final", launchPayload?.targetPath === firstFinal, `target=${launchPayload?.targetPath} firstFinal=${firstFinal}`);
      check("detached-no-stale-canvas-candidates", mockState.canvasLaunchAttempts.length === 1 && mockState.canvasLaunchAttempts[0] === firstFinal, `attempts=${JSON.stringify(mockState.canvasLaunchAttempts)} deleted=${JSON.stringify([...mockState.deletedPaths])}`);
      detachedPage = await browser.newPage({ viewport });
      const detachedMock = await installMock(detachedPage, buildReport(SCENARIOS[0]), { mockState });
      await detachedPage.goto(`${url}${url.includes("?") ? "&" : "?"}mode=canvas&token=${encodeURIComponent(token)}`, { waitUntil: "networkidle" });
      await detachedPage.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
      await waitForStatus(detachedPage, /독립 작업창 로드 완료|자동 로드 실패/, 20_000);
      const detachedStatus = await detachedPage.locator("#status").innerText();
      check("detached-final-load-succeeds", /독립 작업창 로드 완료/.test(detachedStatus), `status=${detachedStatus}`);
      continuationPage = detachedPage;
      continuationLog = detachedMock.invokeLog;
      continuationFinalizes = detachedMock.finalizeCalls;
    }

    await continuationPage.locator("#btn-canvas-tool-restore").click();
    await dragOnPdf(continuationPage, { x: 0.22, y: 0.24 }, { x: 0.55, y: 0.38 });
    await continuationPage.locator("#btn-canvas-apply").click();
    await waitForApplyComplete(continuationPage, continuationLog);
    const restoreOperation = mockState.applyOperations.at(-1);
    const restoreInput = mockState.applyInputs.at(-1);
    check("restore-applies-from-first-final", restoreInput === firstFinal, `input=${restoreInput} firstFinal=${firstFinal}`);
    check(
      "original-path-remains-restore-reference",
      mockState.applyOperations.every((operation) => operation.originalPdf === fixturePath),
      `operations=${JSON.stringify(mockState.applyOperations)}`,
    );
    check("restore-count-is-explicit-only", restoreOperation?.restoreCount === 1 && restoreOperation.maskCount === 0, JSON.stringify(restoreOperation ?? {}));
    check(
      "masking-counts-change-only-by-explicit-operations",
      mockState.applyOperations.length === 2
        && mockState.applyOperations[0]?.maskCount === 1
        && mockState.applyOperations[0]?.restoreCount === 0
        && restoreOperation?.maskCount === 0
        && restoreOperation.restoreCount === 1,
      JSON.stringify(mockState.applyOperations),
    );

    await clickSaveAndSettle(continuationPage);
    const secondFinalize = continuationFinalizes.at(-1);
    const secondSubmittedPreview = path.resolve(secondFinalize?.previewPdf || "");
    const secondFinal = mockState.finalizedPaths[1];
    check("second-save-uses-fresh-preview", Boolean(secondSubmittedPreview) && secondSubmittedPreview !== firstFinal && isPathWithin(secondSubmittedPreview, previewDir), `preview=${secondSubmittedPreview} firstFinal=${firstFinal}`);
    check("second-save-overwrites-selected-path", Boolean(secondFinal) && secondFinal === firstFinal, `first=${firstFinal} second=${secondFinal}`);
    check("second-final-keeps-wire-and-privacy-contract", isExactFinalizePayload(secondFinalize?.rawPayload) && secondFinalize?.copied_files.length === 0, JSON.stringify(secondFinalize ?? {}));
    check("second-submitted-preview-deleted", isDeletedMockPath(mockState, secondSubmittedPreview), `preview=${secondSubmittedPreview}`);
  } catch (error) {
    check("scenario-threw", false, error instanceof Error ? error.message : String(error));
  } finally {
    await detachedPage?.close();
    await page.close();
  }
  return result;
}

async function runDetachedNoFallbackFailureScenario(browser) {
  const page = await browser.newPage({ viewport });
  const result = {
    id: "post-save-detached-final-candidate-failure",
    label: "확정본 작업창 실패 시 옛 미리보기나 원본으로 폴백하지 않음",
    checks: [],
    pass: true,
  };
  const check = (name, ok, detail) => {
    result.checks.push({ name, ok, detail });
    if (!ok) result.pass = false;
  };
  try {
    const mockState = createMockState();
    const { invokeLog } = await installMock(page, buildReport(SCENARIOS[0]), { mockState });
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
    await pickPdfAndMask(page);
    await page.locator("#btn-canvas-tool-mask").click();
    await dragOnPdf(page, { x: 0.15, y: 0.2 }, { x: 0.6, y: 0.4 });
    await page.locator("#btn-canvas-apply").click();
    await waitForApplyComplete(page, invokeLog);
    await clickSaveAndSettle(page);
    const firstFinal = mockState.finalizedPaths[0];

    mockState.failCanvasTokenPending = true;
    await page.locator("#btn-mask-canvas").evaluate((element) => element.click());
    await waitForInvoke(invokeLog, "create_canvas_launch_token");
    const status = await waitForStatus(page, /마스킹 작업창 열기 실패|마스킹 작업창을 열었습니다/, 10_000);
    check("detached-failed-final-does-not-fallback", mockState.canvasLaunchAttempts.length === 1 && mockState.canvasLaunchAttempts[0] === firstFinal, `attempts=${JSON.stringify(mockState.canvasLaunchAttempts)}`);
    check("detached-failed-final-does-not-open-window", /마스킹 작업창 열기 실패/.test(status) && !invokeLog.includes("open_mask_canvas_window"), `status=${status} log=${invokeLog.join(",")}`);
    check("detached-failed-final-does-not-mint-stale-token", mockState.tokens.size === 0, `tokens=${JSON.stringify([...mockState.tokens.keys()])}`);
  } catch (error) {
    check("scenario-threw", false, error instanceof Error ? error.message : String(error));
  } finally {
    await page.close();
  }
  return result;
}

async function runFinalLoadFailureScenario(browser) {
  const page = await browser.newPage({ viewport });
  const result = { id: "post-save-final-load-failure", label: "확정본 로드 실패는 파일 기록과 무결성 실패를 분리", checks: [], pass: true };
  const check = (name, ok, detail) => {
    result.checks.push({ name, ok, detail });
    if (!ok) result.pass = false;
  };
  try {
    const mockState = createMockState({ failFinalRead: true });
    const { invokeLog, finalizeCalls } = await installMock(page, buildReport(SCENARIOS[0]), { mockState });
    await page.goto(url, { waitUntil: "networkidle" });
    await page.locator("#workspace-shell").waitFor({ state: "attached", timeout: 15_000 });
    await pickPdfAndMask(page);
    await page.locator("#btn-canvas-tool-mask").click();
    await dragOnPdf(page, { x: 0.15, y: 0.2 }, { x: 0.6, y: 0.4 });
    await page.locator("#btn-canvas-apply").click();
    await waitForApplyComplete(page, invokeLog);
    await clickSaveAndSettle(page);
    const status = await page.locator("#status").innerText();
    const statusDetail = await page.locator("#status-detail").innerText();
    const controls = await page.evaluate(() => ({
      apply: document.querySelector("#btn-canvas-apply")?.disabled === true,
      save: document.querySelector("#btn-save")?.disabled === true,
      canvasSave: document.querySelector("#btn-canvas-final-save")?.disabled === true,
      mask: document.querySelector("#btn-canvas-tool-mask")?.disabled === true,
      restore: document.querySelector("#btn-canvas-tool-restore")?.disabled === true,
      baseMasking: document.querySelector("#btn-run-masking")?.disabled === true,
    }));
    const maskingRunsBeforeRetry = invokeLog.filter((cmd) => cmd === "run_masking_pipeline").length;
    await page.locator("#btn-run-masking").evaluate((element) => element.click());
    await page.waitForTimeout(50);
    const maskingRunsAfterRetry = invokeLog.filter((cmd) => cmd === "run_masking_pipeline").length;
    check("finalize-completed-before-load-failure", finalizeCalls.length === 1 && mockState.finalizedPaths.length === 1, `finalize=${finalizeCalls.length} finals=${JSON.stringify(mockState.finalizedPaths)}`);
    check("final-load-failure-separates-written-file-from-integrity-failure", /파일은 저장되었으나/.test(status) && /무결성 확인/.test(status) && /다시 열/.test(status) && !/최종 저장 완료/.test(status) && !/최종 저장 실패/.test(status), `status=${status}`);
    check("final-load-failure-does-not-record-verified-save-time", /저장 -/.test(statusDetail), `statusDetail=${statusDetail}`);
    check("final-load-failure-disables-edit-and-save-controls", controls.apply && controls.save && controls.canvasSave && controls.mask && controls.restore, JSON.stringify(controls));
    check("final-load-failure-disables-base-remasking", controls.baseMasking && maskingRunsAfterRetry === maskingRunsBeforeRetry, `controls=${JSON.stringify(controls)} before=${maskingRunsBeforeRetry} after=${maskingRunsAfterRetry}`);
  } catch (error) {
    check("scenario-threw", false, error instanceof Error ? error.message : String(error));
  } finally {
    await page.close();
  }
  return result;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
const devServer = await ensureDevServer(url);
const browser = await launchBrowser();
const results = [];
try {
  for (const scenario of SCENARIOS) {
    const result = await runScenario(browser, scenario);
    results.push(result);
    const status = result.pass ? "PASS" : "FAIL";
    console.log(`[${status}] ${result.id} — ${result.label}`);
    for (const c of result.checks) {
      if (!c.ok) console.log(`    ✗ ${c.name}: ${c.detail}`);
    }
  }
  for (const scenario of MANUAL_SCENARIOS) {
    const result = await runManualScenario(browser, scenario);
    results.push(result);
    const status = result.pass ? "PASS" : "FAIL";
    console.log(`[${status}] ${result.id} — ${result.label}`);
    for (const c of result.checks) {
      if (!c.ok) console.log(`    ✗ ${c.name}: ${c.detail}`);
    }
  }
  const clearRaceResult = await runManualClearRaceScenario(browser);
  results.push(clearRaceResult);
  console.log(`[${clearRaceResult.pass ? "PASS" : "FAIL"}] ${clearRaceResult.id} — ${clearRaceResult.label}`);
  for (const check of clearRaceResult.checks) {
    if (!check.ok) console.log(`    ✗ ${check.name}: ${check.detail}`);
  }
  for (const mode of ["inline", "detached"]) {
    const continuationResult = await runPostSaveContinuationScenario(browser, mode);
    results.push(continuationResult);
    console.log(`[${continuationResult.pass ? "PASS" : "FAIL"}] ${continuationResult.id} — ${continuationResult.label}`);
    for (const check of continuationResult.checks) {
      if (!check.ok) console.log(`    ✗ ${check.name}: ${check.detail}`);
    }
  }
  const detachedNoFallbackResult = await runDetachedNoFallbackFailureScenario(browser);
  results.push(detachedNoFallbackResult);
  console.log(`[${detachedNoFallbackResult.pass ? "PASS" : "FAIL"}] ${detachedNoFallbackResult.id} — ${detachedNoFallbackResult.label}`);
  for (const check of detachedNoFallbackResult.checks) {
    if (!check.ok) console.log(`    ✗ ${check.name}: ${check.detail}`);
  }
  const finalLoadFailureResult = await runFinalLoadFailureScenario(browser);
  results.push(finalLoadFailureResult);
  console.log(`[${finalLoadFailureResult.pass ? "PASS" : "FAIL"}] ${finalLoadFailureResult.id} — ${finalLoadFailureResult.label}`);
  for (const check of finalLoadFailureResult.checks) {
    if (!check.ok) console.log(`    ✗ ${check.name}: ${check.detail}`);
  }
} finally {
  await browser.close();
  if (devServer) {
    devServer.kill("SIGTERM");
    console.log("[dev] stopped vite dev server");
  }
}

const summary = {
  status: results.every((result) => result.pass) ? "pass" : "fail",
  url,
  scenarios: results,
};
writeFileSync(path.join(evidenceDir, "save_flow_summary.json"), `${JSON.stringify(summary, null, 2)}\n`);
console.log(`\nSAVE-FLOW ${summary.status.toUpperCase()} — ${results.filter((r) => r.pass).length}/${results.length} scenarios`);
process.exit(summary.status === "pass" ? 0 : 1);
