// Browser regression for the explicit masked-TXT export and its three policies.

import { spawn } from "node:child_process";
import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { installTauriQaMocks } from "./qa_tauri_mock.mjs";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
const url = "http://localhost:1420/";
const fixturePath = path.join(repoRoot, "tests", "fixtures", "phase6_non_sensitive.pdf");
const evidenceDir = path.join(repoRoot, "build", "masking-options-evidence");

async function serverIsReady() {
  try {
    return (await fetch(url, { signal: AbortSignal.timeout(1_500) })).ok;
  } catch {
    return false;
  }
}

async function ensureDevServer() {
  if (await serverIsReady()) return null;
  const child = spawn("npx", ["vite", "--port", "1420", "--strictPort"], {
    cwd: repoRoot,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.on("data", (chunk) => { output += chunk; });
  child.stderr.on("data", (chunk) => { output += chunk; });
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) throw new Error(`vite exited early:\n${output}`);
    if (await serverIsReady()) return child;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  child.kill("SIGTERM");
  throw new Error(`vite did not become ready:\n${output}`);
}

async function launchBrowser() {
  try {
    return await chromium.launch({ channel: "chrome", headless: true });
  } catch {
    return chromium.launch({ headless: true });
  }
}

async function runScenario(browser, pdfBytes, policy, exportMaskedText, verifyStalePolicy = false, requestedDisplayMode = "") {
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await context.newPage();
  const errors = [];
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  page.on("pageerror", (error) => errors.push(error.message));
  await installTauriQaMocks(page, {
    fixturePath,
    outputDir: path.join(evidenceDir, "output"),
    pdfBytes,
  });
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => (document.querySelector("#status")?.textContent ?? "").includes("대기 중: PDF 열기"),
  );
  await page.locator('[data-screen-target="masking-settings"]').first().evaluate((element) => element.click());

  const exportCheckbox = page.locator("#settings-export-masked-text");
  const policySelect = page.locator("#deidentification-policy");
  const displaySelect = page.locator("#display-mode");
  const displayMode = requestedDisplayMode || (!exportMaskedText ? "black" : policy === "token" ? "label_en" : policy === "partial" ? "label_ko" : "black");
  const displayCard = page.locator(`#btn-display-mode-${displayMode.replace("_", "-")}`);
  await displayCard.click();
  if ((await displaySelect.inputValue()) !== displayMode) throw new Error(`${displayMode}: display card did not update the select contract`);
  if ((await displayCard.getAttribute("aria-pressed")) !== "true") throw new Error(`${displayMode}: display card did not expose selected state`);
  if (exportMaskedText) {
    await exportCheckbox.check();
    const policyCard = page.locator(`#btn-policy-${policy}`);
    await policyCard.click();
    if ((await policySelect.inputValue()) !== policy) throw new Error(`${policy}: policy card did not update the select contract`);
    if (await policySelect.isDisabled()) throw new Error(`${policy}: policy selector stayed disabled`);
    if (await policyCard.isDisabled()) throw new Error(`${policy}: policy card stayed disabled`);
    if ((await policyCard.getAttribute("aria-pressed")) !== "true") throw new Error(`${policy}: policy card did not expose selected state`);
  } else {
    if (!(await policySelect.isDisabled())) throw new Error("disabled export: policy selector must be disabled");
    if (!(await page.locator("#btn-policy-partial").isDisabled())) throw new Error("disabled export: policy card must be disabled");
  }
  if (requestedDisplayMode === "pseudonym") {
    await page.screenshot({ path: path.join(evidenceDir, "pseudonym-settings.png"), fullPage: true });
  }
  await page.locator("#btn-masking-settings-apply").click();
  await page.locator('[data-screen-target="documents"]').first().evaluate((element) => element.click());
  await page.locator("#btn-pick-pdf").click();
  await page.waitForFunction((expected) => document.querySelector("#input-path")?.value === expected, fixturePath);
  await page.waitForFunction(
    () => /원문 PDF 로드 완료|문서 로드 실패/.test(document.querySelector("#status")?.textContent ?? ""),
    undefined,
    { timeout: 20_000 },
  );
  await page.locator("#btn-run-masking").click();
  await page.waitForFunction(
    () => /마스킹 실행 완료/.test(document.querySelector("#status")?.textContent ?? ""),
    undefined,
    { timeout: 30_000 },
  );

  const payload = await page.evaluate(() => {
    const calls = window.__QA_INVOKES__.filter((call) => call.cmd === "run_masking_pipeline");
    return calls.at(-1)?.payload;
  });
  const expectedArtifacts = exportMaskedText ? "pdf_masked_txt_safe_report" : "pdf_safe_report";
  if (payload?.opts?.output_artifacts !== expectedArtifacts) {
    throw new Error(`${policy}: expected ${expectedArtifacts}, got ${payload?.opts?.output_artifacts}`);
  }
  if (payload?.opts?.deidentification_policy !== policy) {
    throw new Error(`${policy}: IPC policy mismatch (${payload?.opts?.deidentification_policy})`);
  }
  if (payload?.opts?.display_mode !== displayMode) {
    throw new Error(`${displayMode}: IPC display mode mismatch (${payload?.opts?.display_mode})`);
  }
  const pdfPolicySummary = await page.locator("#review-summary-pdf-policy").textContent();
  const txtPolicySummary = await page.locator("#review-summary-txt-policy").textContent();
  const expectedPdfSummary = displayMode === "pseudonym"
    ? "가명 표시"
    : displayMode === "label_en"
      ? "영문 유형 라벨"
      : displayMode === "label_ko"
        ? "한글 유형 라벨"
        : "검정 박스";
  const expectedTxtSummary = exportMaskedText
    ? policy === "pseudonym"
      ? "일관 가명"
      : policy === "partial"
        ? "부분 마스킹"
        : "완전 치환"
    : "저장 안 함";
  if (!pdfPolicySummary?.includes(expectedPdfSummary)) throw new Error(`${displayMode}: PDF policy summary mismatch (${pdfPolicySummary})`);
  if (!txtPolicySummary?.includes(expectedTxtSummary)) throw new Error(`${policy}: TXT policy summary mismatch (${txtPolicySummary})`);
  if (payload?.opts?.return_text_preview !== false) {
    throw new Error(`${policy}: raw text preview request must stay disabled`);
  }
  if (verifyStalePolicy) {
    await page.locator('[data-screen-target="masking-settings"]').first().evaluate((element) => element.click());
    await policySelect.selectOption("partial");
    await page.locator("#btn-masking-settings-apply").click();
    await page.locator('[data-screen-target="documents"]').first().evaluate((element) => element.click());
    await page.locator("#btn-save").click();
    await page.waitForFunction(() => {
      const dialog = document.querySelector("#final-save-dialog");
      return Boolean(dialog) && !dialog.classList.contains("is-hidden");
    });
    const warningText = await page.locator("#final-save-warning-list").textContent();
    if (!warningText?.includes("마스킹을 다시 실행")) {
      throw new Error("stale policy: rerun warning was not shown");
    }
    await page.locator("#btn-dialog-save-all").click();
    await page.waitForFunction(
      () => /최종 저장 완료/.test(document.querySelector("#status")?.textContent ?? ""),
      undefined,
      { timeout: 30_000 },
    );
    const finalizePayload = await page.evaluate(() => {
      const calls = window.__QA_INVOKES__.filter((call) => call.cmd === "finalize_manual_output_to_selected_path");
      return calls.at(-1)?.payload;
    });
    if (finalizePayload?.maskedPath !== "") {
      throw new Error("stale policy: outdated masked TXT must not be published");
    }
  }
  if (errors.length > 0) throw new Error(`${policy}: browser errors: ${errors.join(" | ")}`);
  if (exportMaskedText && policy === "pseudonym") {
    await page.screenshot({ path: path.join(evidenceDir, "masked-text-options.png") });
  }
  await context.close();
  console.log(`[option] ${exportMaskedText ? policy : "pdf-only"}: IPC options OK`);
}

await mkdir(evidenceDir, { recursive: true });
const pdfBytes = [...(await readFile(fixturePath))];
const devServer = await ensureDevServer();
const browser = await launchBrowser();
try {
  await runScenario(browser, pdfBytes, "token", false);
  for (const policy of ["token", "partial", "pseudonym"]) {
    await runScenario(browser, pdfBytes, policy, true, policy === "token");
  }
  await runScenario(browser, pdfBytes, "pseudonym", true, false, "pseudonym");
} finally {
  await browser.close();
  if (devServer) devServer.kill("SIGTERM");
}

console.log("MASKING OPTIONS QA PASS");
