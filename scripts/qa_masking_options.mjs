// Browser UI-wiring regression for the explicit masked-TXT export and its three policies; mocked IPC does not establish native/backend authority.

import { spawn } from "node:child_process";
import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { installTauriQaMocks } from "./qa_tauri_mock.mjs";

const repoRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
let url;
const fixturePath = path.join(repoRoot, "tests", "fixtures", "phase6_non_sensitive.pdf");
const evidenceDir = path.join(repoRoot, "build", "masking-options-evidence");

async function serverIsReady(target) {
  try {
    return (await fetch(target, { signal: AbortSignal.timeout(1_500) })).ok;
  } catch {
    return false;
  }
}

function childViteUrl(output) {
  const match = output.match(/Local:\s+(https?:\/\/[^\s]+)/);
  return match?.[1] ?? null;
}

async function ensureDevServer() {
  const child = spawn("npx", ["vite", "--host", "127.0.0.1", "--port", "0", "--strictPort"], {
    cwd: repoRoot,
    stdio: ["ignore", "pipe", "pipe"],
  });
  let output = "";
  child.stdout.on("data", (chunk) => { output += chunk; });
  child.stderr.on("data", (chunk) => { output += chunk; });
  const deadline = Date.now() + 60_000;
  try {
    while (Date.now() < deadline) {
      if (child.exitCode !== null) throw new Error(`vite exited early:\n${output}`);
      const childUrl = childViteUrl(output);
      if (childUrl && await serverIsReady(childUrl)) return { child, url: childUrl };
      await new Promise((resolve) => setTimeout(resolve, 300));
    }
    throw new Error(`vite did not publish a ready child-bound URL:\n${output}`);
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
  // Masked-TXT policy/export options are exercised through the legacy legal
  // pipeline; public-document profiles use the server-owned review lifecycle.
  await page.locator("#profile").selectOption("legal");

  const exportCheckbox = page.locator("#settings-export-masked-text");
  const policySelect = page.locator("#deidentification-policy");
  const displaySelect = page.locator("#display-mode");
  const phoneRule = page.locator("#rule-phone");
  const phoneRuleDisabled = page.locator('button[data-rule-control="rule-phone"][value="disabled"]');
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
  await phoneRuleDisabled.click();
  if (await phoneRule.isChecked()) throw new Error("rule toggle: phone rule did not remain disabled in React state");
  if ((await phoneRuleDisabled.getAttribute("aria-pressed")) !== "true") throw new Error("rule toggle: disabled segment did not expose selected state");
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
  if (payload?.opts?.phone !== false) {
    throw new Error(`rule toggle: expected phone=false, got ${payload?.opts?.phone}`);
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
    const staleSaveState = await page.evaluate(() => {
      const isVisible = (element) => {
        if (!(element instanceof HTMLButtonElement)) return false;
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) > 0
          && rect.width > 0 && rect.height > 0;
      };
      const controls = ["#btn-save", "#btn-canvas-final-save"]
        .map((selector) => document.querySelector(selector))
        .filter(isVisible);
      if (controls.length === 0) throw new Error("no visible final-save surface");
      for (const control of controls) control.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true }));
      return {
        controls: controls.map((control) => ({ id: control.id, disabled: control.disabled })),
        dialogVisible: (() => {
          const dialog = document.querySelector("#final-save-dialog");
          if (!(dialog instanceof HTMLElement)) return false;
          const style = getComputedStyle(dialog);
          const rect = dialog.getBoundingClientRect();
          return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) > 0
            && rect.width > 0 && rect.height > 0;
        })(),
        saveIpcCalls: window.__QA_INVOKES__.filter((call) => [
          "choose_final_pdf_path",
          "finalize_masking_run",
          "finalize_manual_output",
          "finalize_manual_output_to_selected_path",
        ].includes(call.cmd)).map((call) => call.cmd),
      };
    });
    if (staleSaveState.controls.some((control) => !control.disabled)) {
      throw new Error(`stale policy: every visible final-save surface must be disabled (${JSON.stringify(staleSaveState.controls)})`);
    }
    if (staleSaveState.dialogVisible) {
      throw new Error("stale policy: disabled save controls must not open the confirmation dialog");
    }
    if (staleSaveState.saveIpcCalls.length !== 0) {
      throw new Error(`stale policy: guarded save path reached chooser/finalize IPC (${staleSaveState.saveIpcCalls.join(",")})`);
    }
  }
  if (errors.length > 0) throw new Error(`${policy}: browser errors: ${errors.join(" | ")}`);
  if (exportMaskedText && policy === "pseudonym") {
    await page.screenshot({ path: path.join(evidenceDir, "masked-text-options.png") });
  }
  await context.close();
  console.log(`[option] ${exportMaskedText ? policy : "pdf-only"}: IPC options OK`);
}

async function runCodedFailureScenario(browser, pdfBytes) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 860 } });
  const page = await context.newPage();
  await installTauriQaMocks(page, {
    fixturePath,
    outputDir: path.join(evidenceDir, "output"),
    pdfBytes,
    codedAnalyzeFailure: true,
  });
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForFunction(() => (document.querySelector("#status")?.textContent ?? "").includes("대기 중: PDF 열기"));
  await page.locator('[data-screen-target="masking-settings"]').first().evaluate((element) => element.click());
  await page.locator("#profile").selectOption("official_dispatch");
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
    () => /마스킹 실패/.test(document.querySelector("#status")?.textContent ?? ""),
    undefined,
    { timeout: 20_000 },
  );
  const status = await page.locator("#status").innerText();
  if (!status.includes("MASKING_SESSION_ANALYZER_UNAVAILABLE") || !status.includes("실행 파일 없음") || status.includes("unknown")) {
    throw new Error(`coded failure presentation mismatch: ${status}`);
  }
  await context.close();
  console.log("[option] coded failure: code and stage presentation OK");
}

await mkdir(evidenceDir, { recursive: true });
const pdfBytes = [...(await readFile(fixturePath))];
let devServer;
let browser;
try {
  const server = await ensureDevServer();
  devServer = server.child;
  url = server.url;
  const launched = await launchBrowser();
  browser = launched.browser;
  console.log(`[browser] ${launched.selection}${launched.systemChromeDiagnostic ? `; system Chrome unavailable: ${launched.systemChromeDiagnostic}` : ""}`);
  await runScenario(browser, pdfBytes, "token", false);
  for (const policy of ["token", "partial", "pseudonym"]) {
    await runScenario(browser, pdfBytes, policy, true, policy === "token");
  }
  await runScenario(browser, pdfBytes, "pseudonym", true, false, "pseudonym");
  await runCodedFailureScenario(browser, pdfBytes);
} finally {
  await browser?.close();
  if (devServer) devServer.kill("SIGTERM");
}

console.log("MASKING OPTIONS QA PASS");
