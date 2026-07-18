import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const ts = require("typescript");
const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const moduleCache = new Map();

export async function installTauriQaMocks(page, options) {
  await page.addInitScript(
    ({ fixturePathValue, outputDirValue, fixtureBytes, failBatchItemOnce }) => {
      window.confirm = () => true;
      window.__QA_INVOKES__ = [];
      let selectedFinalTarget = null;
      let saveTokenCounter = 0;
      const failedBatchInputs = new Set();
      window.__TAURI_INTERNALS__ = {
        plugins: { path: { sep: "/", delimiter: ":" } },
        transformCallback: () => 1,
        unregisterCallback: () => {},
        invoke: async (cmd, payload = {}) => {
          window.__QA_INVOKES__.push({ cmd, payload });
          if (["pick_input_document", "pick_input_pdf"].includes(cmd)) return fixturePathValue;
          if (cmd === "pick_input_documents") return [fixturePathValue, fixturePathValue.replace(/\.pdf$/i, "-batch.pdf")];
          if (cmd === "default_output_dir_for_document") return outputDirValue;
          if (cmd === "pick_output_dir" || cmd === "get_preview_workdir") return outputDirValue;
          if (cmd === "choose_final_pdf_path") {
            selectedFinalTarget = {
              outputPath: `${outputDirValue}/${String(payload.defaultFileName || "masked")}.pdf`,
              saveToken: `qa-save-${++saveTokenCounter}`,
            };
            return { ...selectedFinalTarget };
          }
          if (cmd === "read_pdf_bytes") return fixtureBytes;
          if (cmd === "read_text_file" && String(payload.path || "").includes("manual_revalidation.safe_report.json")) {
            return JSON.stringify({
              raw_values_saved: false,
              raw_text_returned: false,
              manual_revalidation: { status: "passed", verified: true, output_file_saved_in_report: false, mask_boxes_applied: 1, restore_boxes_applied: 0, skipped_boxes: 0 },
              product_checks: { quality_gate_passed: true, needs_manual_review: false, final_submission_allowed: true },
              document_redaction: { status: "manual_revalidated", missing_targets_count: 0, verification: { verified: true, residual_hits: 0, reason: "수동 보정 PDF 재검증 완료" } },
              review_items: [],
            });
          }
          if (cmd === "read_text_file") return "원문 ALPHA REVIEW TOKEN\n마스킹 [KEYWORD]";
          if (cmd === "run_masking_pipeline") {
            const inputFile = String(payload.inputFile || "");
            if (failBatchItemOnce && inputFile.endsWith("-batch.pdf") && !failedBatchInputs.has(inputFile)) {
              failedBatchInputs.add(inputFile);
              throw new Error("QA_BATCH_FAIL_ONCE");
            }
            const exportMaskedText = payload.opts?.output_artifacts === "pdf_masked_txt_safe_report";
            const policy = payload.opts?.deidentification_policy ?? "token";
            const maskedTextByPolicy = {
              token: "연락처 [PHONE]",
              partial: "연락처 010-****-5678",
              pseudonym: "연락처 010-0000-0001",
            };
            const maskedPath = exportMaskedText ? `${outputDirValue}/masked.txt` : "";
            return {
              extracted_path: "",
              masked_path: maskedPath,
              report_path: `${outputDirValue}/safe_report.json`,
              extracted_text: "",
              masked_text: exportMaskedText ? maskedTextByPolicy[policy] ?? maskedTextByPolicy.token : "",
              runtime_manifest: {
                outputs: { preview_pdf_source_file: fixturePathValue, masked_pdf_file: `${outputDirValue}/masked.pdf`, safe_report_path: `${outputDirValue}/safe_report.json`, extracted_file: null, masked_file: maskedPath || null },
                review_items: [
                  { tag: "KEYWORD", display_token: "[KEYWORD]", status: "needs_review", count: 1, page: 0, bbox: { x: 72, y: 60, width: 128, height: 18 } },
                  { tag: "ADDRESS", display_token: "[ADDRESS]", status: "needs_review", count: 1, page: 0, bbox: { x: 72, y: 96, width: 144, height: 18 } },
                ],
              },
              report: {
                extract: { engine_used: payload.opts?.extract_engine ?? "auto" },
                outputs: { preview_pdf_source_file: null, masked_pdf_file: null, safe_report_path: null, extracted_file: null, masked_file: null },
                text_deidentification: { policy },
                product_checks: { quality_gate_passed: true, needs_manual_review: true, final_submission_allowed: true },
                pdf_redaction: { status: "ok", verification: { residual_hits: 0 }, missing_targets_count: 0 },
                review_items: [
                  { tag: "KEYWORD", display_token: "[KEYWORD]", status: "needs_review", count: 1, raw_value_saved: false },
                  { tag: "ADDRESS", display_token: "[ADDRESS]", status: "needs_review", count: 1, raw_value_saved: false },
                ],
                warnings: [],
              },
            };
          }
          if (cmd === "apply_manual_boxes") {
            const boxes = payload.boxes ?? [];
            const restoreApplied = boxes.filter((box) => box.mode === "restore").length;
            // Fixed contract: revalidation only when a restore was applied (risk
            // increases). Mask-only additions leave the base report untouched, so
            // no revalidation report is attached and the save gate stays as-is.
            const requiresRevalidation = restoreApplied > 0;
            const result = {
              status: "applied",
              output_file: `${outputDirValue}/manual_preview.pdf`,
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
              result.revalidation_report = `${outputDirValue}/manual_preview.manual_revalidation.safe_report.json`;
              result.revalidation_status = "passed";
            }
            return result;
          }
          if (cmd === "finalize_manual_output" || cmd === "finalize_manual_output_to_selected_path") {
            // v4.2.0: Rust finalize_manual_output 은 리포트 내용(잔존·누락·품질·재검증
            // 실패·리포트 부재/파싱 실패)으로는 절대 실패하지 않는다 — report_allows_
            // final_save 거부 술어는 폐기됐다. 최종 저장은 사용자 재량이며, 이 mock 도
            // finalize 거부 로직 없이 항상 성공을 반환한다.
            // v4.1: Rust finalize copy_report 기본값 false, 프론트는 항상
            // copyReport:false 로 호출한다. 안전 리포트는 시스템 임시폴더 하위 내부
            // 세션 디렉터리에만 존재하고 사용자 산출 폴더에는 마스킹 PDF 만 남으므로
            // copied_files 에 safe_report 가 포함되면 안 된다.
            const normalizePath = (value) => {
              const parts = [];
              for (const part of String(value || "").split("/")) {
                if (!part || part === ".") continue;
                if (part === "..") parts.pop();
                else parts.push(part);
              }
              return `/${parts.join("/")}`;
            };
            const previewPdf = normalizePath(payload.previewPdf);
            const registeredOutputDir = normalizePath(outputDirValue);
            const previewIsRegistered = previewPdf.startsWith(`${registeredOutputDir}/`);
            let finalOutput = `${outputDirValue}/final_masked.pdf`;
            if (!previewIsRegistered) {
              throw new Error("SAVE_SOURCE_REJECTED: 저장 원본을 확인할 수 없습니다.");
            }
            if (cmd === "finalize_manual_output_to_selected_path") {
              const confirmedTarget = selectedFinalTarget;
              selectedFinalTarget = null;
              if (
                !confirmedTarget
                || normalizePath(payload.outputPath) !== normalizePath(confirmedTarget.outputPath)
                || String(payload.saveToken || "") !== confirmedTarget.saveToken
              ) {
                throw new Error("SAVE_OUTPUT_PATH_REJECTED: 저장 경로를 확인할 수 없습니다.");
              }
              finalOutput = confirmedTarget.outputPath;
            } else if (normalizePath(payload.outputDir) !== registeredOutputDir) {
              throw new Error("SAVE_OUTPUT_DIR_REJECTED: 저장 폴더를 확인할 수 없습니다.");
            }
            const copiedFiles = payload.copyReport === true ? [`${outputDirValue}/safe_report.json`] : [];
            return {
              final_output_file: finalOutput,
              copied_files: copiedFiles,
            };
          }
          if (cmd === "plugin:opener|open_path" || cmd === "open_mask_canvas_window" || cmd === "create_canvas_launch_token") return "ok";
          return null;
        },
      };
    },
    {
      fixturePathValue: options.fixturePath,
      outputDirValue: options.outputDir,
      fixtureBytes: options.pdfBytes,
      failBatchItemOnce: options.failBatchItemOnce === true,
    },
  );
}

function resolveTsModule(basePath, specifier) {
  const resolved = path.resolve(path.dirname(basePath), specifier);
  const candidates = [resolved, `${resolved}.ts`, path.join(resolved, "index.ts")];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return require.resolve(specifier, { paths: [path.dirname(basePath)] });
}

function loadTsModule(relativePath) {
  const absolutePath = path.resolve(repoRoot, relativePath);
  if (moduleCache.has(absolutePath)) return moduleCache.get(absolutePath).exports;
  const source = fs.readFileSync(absolutePath, "utf8");
  const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  const module = { exports: {} };
  moduleCache.set(absolutePath, module);
  function localRequire(specifier) {
    if (specifier.startsWith(".")) return loadTsModule(path.relative(repoRoot, resolveTsModule(absolutePath, specifier)));
    return require(specifier);
  }
  const sandbox = { module, exports: module.exports, require: localRequire };
  vm.runInNewContext(js, sandbox, { filename: absolutePath });
  return module.exports;
}

function expectPass(result, label) {
  if (!result.ok) {
    const details = result.errors.map((issue) => `${issue.code}:${issue.field}`).join(", ");
    throw new Error(`${label} failed contract parse: ${details}`);
  }
  return result.value;
}

function runProbe(label, result, expectedCode) {
  return {
    label,
    passed: result.ok === false && result.errors[0]?.code === expectedCode,
    error_code: result.ok ? null : result.errors[0]?.code ?? null,
  };
}

function buildSafeReportArtifact() {
  const maskingSessionModule = loadTsModule("src/state/maskingSession.ts");
  const report = { product_checks: { quality_gate_passed: true }, document_redaction: { status: "passed" }, review_items: [] };
  const parsed = expectPass(maskingSessionModule.parseSafeReport(report), "safe-report");
  const probe = runProbe("malformed_input.missing_product_checks", maskingSessionModule.parseSafeReport({}), "missing_product_checks");
  return { status: probe.passed ? "pass" : "fail", contract: "safe-report", raw_values_saved: false, report: parsed, adversarial_probes: [probe] };
}

function parseCliArgs(argv) {
  const args = { contract: "", output: "" };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--contract") args.contract = argv[index + 1] ?? "";
    else if (value === "--output") args.output = argv[index + 1] ?? "";
  }
  return args;
}

function writeArtifact(outputPath, payload) {
  const absoluteOutputPath = path.resolve(repoRoot, outputPath);
  fs.mkdirSync(path.dirname(absoluteOutputPath), { recursive: true });
  fs.writeFileSync(absoluteOutputPath, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
}

async function main() {
  const args = parseCliArgs(process.argv.slice(2));
  if (!args.contract) return;
  if (args.contract !== "safe-report") throw new Error(`Unsupported contract: ${args.contract}`);
  const artifact = buildSafeReportArtifact();
  if (!args.output) {
    process.stdout.write(`${JSON.stringify(artifact, null, 2)}\n`);
    process.exitCode = artifact.status === "pass" ? 0 : 1;
    return;
  }
  writeArtifact(args.output, artifact);
  process.stdout.write(`${artifact.status.toUpperCase()} ${args.contract} -> ${args.output}\n`);
  process.exitCode = artifact.status === "pass" ? 0 : 1;
}

const entryPath = process.argv[1] ? path.resolve(process.argv[1]) : "";
if (entryPath === fileURLToPath(import.meta.url)) await main();
