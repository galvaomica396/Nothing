import { spawn, execFile as execFileCallback } from "node:child_process";
import { copyFile, mkdir, mkdtemp, rm } from "node:fs/promises";
import { existsSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import {
  ACTIVE_DISPLAY_INFO_SWIFT,
  CLAMSHELL_PROBE_COMMAND,
  DISPLAY_REMEDIATION,
  checkHostDisplayPreconditions,
  inspectVisibleAppWindow,
  verifyVisibleAppRender,
} from "./real_app_preconditions.mjs";
import { resolveRealCorpus, sha256File } from "./real_corpus.mjs";
import {
  QA_DRIVE_TIMEOUTS_MS,
  qaDriveCommandTimeoutMs as configuredQaDriveCommandTimeoutMs,
} from "./qa_drive_timeout_config.mjs";

const execFile = promisify(execFileCallback);
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const args = new Map();
for (let index = 2; index < process.argv.length; index += 1) {
  const value = process.argv[index];
  if (value.startsWith("--")) args.set(value, process.argv[index + 1]);
}
const smoke = args.has("--smoke");
const defaultApp = join(repoRoot, "src-tauri", "target", "release", "bundle", "macos", "Nothing.app");
const appPath = resolve(args.get("--app") ?? process.env.REAL_APP_PATH ?? defaultApp);
const geometryFixture = resolve(args.get("--fixture") ?? join(repoRoot, "src-tauri", "resources", "public_native_qa_geometry_fixture.pdf"));
const realDocumentPath = process.env.E2E_REAL_DOC?.trim() ?? "";
const evidenceDir = resolve(args.get("--evidence-dir") ?? join(repoRoot, ".omo", "evidence", "real-app-e2e"));
const OPEN_COMMAND_TIMEOUT = configuredQaDriveCommandTimeoutMs("open");
const NAVIGATION_COMMAND_TIMEOUT = configuredQaDriveCommandTimeoutMs("go-page");
const CONTROL_COMMAND_TIMEOUT = configuredQaDriveCommandTimeoutMs("dump-state");
const LONG_COMMAND_TIMEOUT = configuredQaDriveCommandTimeoutMs("run-masking");
const INFRASTRUCTURE_CODES = new Set([
  "E2E_DISPLAY_UNAVAILABLE",
  "E2E_SESSION_LOCKED",
  "E2E_WINDOW_NOT_VISIBLE",
  "FRONTEND_READY_TIMEOUT",
  "QA_DRIVE_FRONTEND_NOT_READY",
  "QA_DRIVE_FRONTEND_UNAVAILABLE",
  "QA_DRIVE_COMMAND_TIMEOUT",
  "QA_DRIVE_COMMAND_CANCELLED",
  "QA_DRIVE_RENDER_UNAVAILABLE",
  "QA_DRIVE_RENDER_CANCEL_TIMEOUT",
  "QA_DRIVE_PROTOCOL_FAILED",
  "QA_DRIVE_PROCESS_EXITED",
  "SCREEN_CAPTURE_UNAVAILABLE",
  "SCREEN_PIXEL_ANALYSIS_UNAVAILABLE",
  "REAL_CORPUS_INCOMPLETE",
]);

function fail(step, detail) {
  throw new Error(`${step}: ${detail}`);
}

function preconditionRecord(error, fallbackCode = "E2E_DISPLAY_UNAVAILABLE") {
  return {
    status: "blocked",
    code: error && typeof error === "object" && typeof error.code === "string"
      ? error.code
      : fallbackCode,
    reason: error && typeof error === "object" && typeof error.reason === "string"
      ? error.reason
      : "probe-failed",
    signals: error && typeof error === "object" && error.signals && typeof error.signals === "object"
      ? error.signals
      : {},
  };
}

function canvasRect(state, x0Fraction, y0Fraction, x1Fraction, y1Fraction) {
  const width = state?.overlayPixels?.width;
  const height = state?.overlayPixels?.height;
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    fail("canvas-geometry", "overlay dimensions unavailable");
  }
  return {
    x0: Math.round(width * x0Fraction),
    y0: Math.round(height * y0Fraction),
    x1: Math.round(width * x1Fraction),
    y1: Math.round(height * y1Fraction),
  };
}

function rectsOverlap(left, right) {
  return Math.max(left.x0, right.x0) < Math.min(left.x1, right.x1)
    && Math.max(left.y0, right.y0) < Math.min(left.y1, right.y1);
}

function disjointRestoreRect(state) {
  const candidates = [
    canvasRect(state, 0.08, 0.76, 0.30, 0.88),
    canvasRect(state, 0.62, 0.76, 0.92, 0.88),
    canvasRect(state, 0.68, 0.10, 0.92, 0.20),
  ];
  return candidates.find((candidate) => !state.paintedBounds || !rectsOverlap(candidate, {
    x0: state.paintedBounds.x,
    y0: state.paintedBounds.y,
    x1: state.paintedBounds.x + state.paintedBounds.width,
    y1: state.paintedBounds.y + state.paintedBounds.height,
  })) ?? fail("real-restore-geometry", "no disjoint restore area was available");
}

async function command(file, values, step) {
  try {
    return await execFile(file, values, { timeout: 30_000, maxBuffer: 1024 * 1024 });
  } catch (error) {
    fail(step, error instanceof Error ? error.message : "command failed");
  }
}

async function packagedExecutable() {
  const info = join(appPath, "Contents", "Info.plist");
  const result = await command("plutil", ["-extract", "CFBundleExecutable", "raw", "-o", "-", info], "app-executable");
  const executable = join(appPath, "Contents", "MacOS", result.stdout.trim());
  if (!existsSync(executable)) fail("app-executable", `bundle executable missing at ${executable}`);
  return executable;
}

const pixelDiff = String.raw`
from PIL import Image
import json, sys
before = Image.open(sys.argv[1]).convert("RGB")
after = Image.open(sys.argv[2]).convert("RGB")
if before.size != after.size: raise SystemExit("SCREENSHOT_SIZE_CHANGED")
bounds = json.loads(sys.argv[3])
left = max(0, bounds["x"])
top = max(0, bounds["y"])
right = min(before.width, left + bounds["width"])
bottom = min(before.height, top + bounds["height"])
if right <= left or bottom <= top: raise SystemExit("WINDOW_BOUNDS_OUTSIDE_SCREEN")
before = before.crop((left, top, right, bottom))
after = after.crop((left, top, right, bottom))
new_dark = sum(1 for prior, current in zip(before.getdata(), after.getdata()) if min(prior) > 120 and max(current) < 48)
print(json.dumps({"newDarkPixels": new_dark, "crop": {"x": left, "y": top, "width": right - left, "height": bottom - top}}))
`;

function createDrive(child) {
  const transcripts = [];
  const pending = new Map();
  let commandSequence = 0;
  let resolveReady;
  let rejectReady;
  const ready = new Promise((resolveReadyPromise, rejectReadyPromise) => {
    resolveReady = resolveReadyPromise;
    rejectReady = rejectReadyPromise;
  });
  const readyTimeout = setTimeout(() => rejectReady(new Error("frontend-ready: command transcript timeout")), QA_DRIVE_TIMEOUTS_MS.startup);
  let stderr = "";
  let stdout = "";
  let readyReceived = false;
  let closed = false;
  const rejectPending = (error) => {
    for (const request of pending.values()) request.reject(error);
    pending.clear();
    rejectReady(error);
  };
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    stdout += chunk;
    const lines = stdout.split("\n");
    stdout = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.trim()) continue;
      let transcript;
      try {
        transcript = JSON.parse(line);
      } catch {
        rejectPending(new Error("QA_DRIVE_PROTOCOL_FAILED"));
        return;
      }
      transcripts.push(transcript);
      if (transcript.command === "ready") {
        clearTimeout(readyTimeout);
        readyReceived = true;
        resolveReady(transcript);
        continue;
      }
      const request = typeof transcript.id === "string"
        ? pending.get(transcript.id)
        : pending.values().next().value;
      if (!request) continue;
      if (typeof transcript.id === "string") pending.delete(transcript.id);
      else {
        const firstId = pending.keys().next().value;
        if (firstId !== undefined) pending.delete(firstId);
      }
      request.resolve(transcript);
    }
  });
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
    if (!readyReceived && stderr.includes("QA_DRIVE_")) {
      clearTimeout(readyTimeout);
      const error = new Error(stderr.trim());
      rejectPending(error);
    }
  });
  child.on("error", (error) => {
    clearTimeout(readyTimeout);
    closed = true;
    rejectPending(error);
  });
  child.on("close", () => {
    clearTimeout(readyTimeout);
    if (closed) return;
    closed = true;
    rejectPending(new Error("QA_DRIVE_PROCESS_EXITED"));
  });
  return {
    async waitUntilReady() {
      const transcript = await ready;
      if (!transcript.ok) fail("frontend-ready", transcript.error ?? "failed");
    },
    async send(command) {
      if (closed) fail("qa-drive-command", "QA_DRIVE_PROCESS_EXITED");
      const commandName = command.trim().split(/\s+/)[0] || "unknown";
      const requestId = `qa-${commandSequence++}`;
      const transcript = await new Promise((resolveResponse, rejectResponse) => {
        const timeoutDuration = commandName === "open"
          ? OPEN_COMMAND_TIMEOUT
          : ["go-page", "scroll-to", "inspect-target", "resolve-geometry"].includes(commandName)
            ? NAVIGATION_COMMAND_TIMEOUT
            : ["apply-keyword", "run-masking", "wait-idle", "resolve-review", "apply-manual", "confirm-save", "save-final"].includes(commandName)
              ? LONG_COMMAND_TIMEOUT
              : CONTROL_COMMAND_TIMEOUT;
        const timeout = setTimeout(() => {
          pending.delete(requestId);
          rejectResponse(new Error(
            `${commandName === "open" ? "QA_DRIVE_RENDER_UNAVAILABLE" : "QA_DRIVE_COMMAND_TIMEOUT"}:stage=client_wait:command=${commandName}`,
          ));
        }, timeoutDuration);
        pending.set(requestId, {
          resolve: (response) => { clearTimeout(timeout); resolveResponse(response); },
          reject: (error) => { clearTimeout(timeout); rejectResponse(error); },
        });
        try {
          child.stdin.write(`${command}\n`);
        } catch (error) {
          clearTimeout(timeout);
          pending.delete(requestId);
          rejectResponse(error);
        }
      });
      if (!transcript.ok) fail("qa-drive-command", `${transcript.command}: ${transcript.error ?? "failed"}`);
      return transcript;
    },
    close() {
      if (closed) return;
      try {
        child.stdin.end();
      } catch {
        // Process cleanup below is authoritative.
      }
    },
  };
}

async function main() {
  if (!smoke) fail("arguments", "pass --smoke");
  let hostPreconditions;
  try {
    hostPreconditions = await checkHostDisplayPreconditions();
  } catch (error) {
    const record = preconditionRecord(error);
    const code = record.code;
    const detail = error instanceof Error
      ? error.message.replace(new RegExp(`^${code}:\\s*`), "")
      : `${DISPLAY_REMEDIATION} (reason=${code})`;
    console.error(`[precondition] ${JSON.stringify(record)}`);
    fail(code, detail);
  }
  let realDocumentIsFile = false;
  if (realDocumentPath) {
    try {
      realDocumentIsFile = statSync(realDocumentPath).isFile();
    } catch {
      realDocumentIsFile = false;
    }
  }
  if (realDocumentPath && (!realDocumentIsFile || !realDocumentPath.toLowerCase().endsWith(".pdf"))) {
    fail("E2E_REAL_DOC_MISSING", "E2E_REAL_DOC must point to a manifest PDF");
  }
  if (!existsSync(appPath)) fail("app-path", `packaged app missing at ${appPath}`);
  if (!existsSync(geometryFixture)) fail("geometry-fixture", `fixture missing at ${geometryFixture}`);
  const executable = await packagedExecutable();
  await mkdir(evidenceDir, { recursive: true });
  const scratch = await mkdtemp(join(tmpdir(), "nothing-real-app-e2e-"));
  const geometryInput = join(scratch, basename(geometryFixture));
  const realInput = join(scratch, "real-document-input.pdf");
  const baseline = join(evidenceDir, "real-app-geometry-before-masking.png");
  const screenshot = join(evidenceDir, "real-app-geometry-resolved.png");
  const realBaseline = join(evidenceDir, "real-app-real-document-before-masking.png");
  const realScreenshot = join(evidenceDir, "real-app-real-document-after-masking.png");
  const child = spawn(executable, ["--qa-drive-stdin"], {
    cwd: appPath,
    env: { ...process.env, MASK_TOOL_QA_DRIVE: "1", MASK_TOOL_ALLOWED_DIRS: scratch },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const drive = createDrive(child);
  try {
    await drive.waitUntilReady();
    const appWindow = await inspectVisibleAppWindow(child.pid, hostPreconditions.displays);
    const renderProbe = await verifyVisibleAppRender({ drive, window: appWindow, directory: scratch });

    let corpus;
    try {
      corpus = await resolveRealCorpus();
    } catch (error) {
      fail("REAL_CORPUS_INCOMPLETE", error instanceof Error ? error.code : "manifest resolution failed");
    }
    const requestedDocumentHash = realDocumentPath
      ? await sha256File(realDocumentPath).catch(() => "")
      : "";
    const selectedRealDocument = realDocumentPath
      ? corpus.find((document) => document.path === resolve(realDocumentPath)
        && document.sha256 === requestedDocumentHash)
      : corpus[0];
    if (!selectedRealDocument) fail("E2E_REAL_DOC_MISSING", "E2E_REAL_DOC must identify one manifest PDF");
    const selectedRealDocumentPath = selectedRealDocument.path;
    try {
      await copyFile(geometryFixture, geometryInput);
      await copyFile(selectedRealDocumentPath, realInput);
    } catch {
      fail("E2E_REAL_DOC_MISSING", "E2E_REAL_DOC could not be copied into the isolated workspace");
    }
    // The app session is kept alive after the one-time window and frame preflight;
    // document scenarios below reuse the same verified visible session.
    await drive.send(`open ${geometryInput}`);
    await drive.send("set-profile mixed");
    await command("screencapture", ["-x", baseline], "screencapture-before");
    await drive.send("run-masking");
    await drive.send("wait-idle");
    const geometryState = (await drive.send("dump-state")).state;
    if (!geometryState?.documentLoaded) fail("document-load", "frontend did not report a loaded document");
    if (geometryState.reviewCardCount !== 1) fail("review-panel", `expected one institution card in the primary review rail (${geometryState.reviewCardCount})`);
    if (geometryState.advancedGeometryCount !== 1) fail("geometry-review", `expected one unresolved geometry review (${geometryState.advancedGeometryCount})`);
    if (!geometryState.pendingReviews?.some((review) => review.kind === "institution")) fail("review-panel", "institution review was not retained in the primary rail");
    if (geometryState.overlayPaintedPixelCount <= 0 || !geometryState.paintedBounds) fail("overlay-state", "no detection overlay was rendered");
    const resolvedGeometryState = (await drive.send("resolve-geometry first 0 0 612 792")).state;
    if (!resolvedGeometryState?.documentLoaded) fail("geometry-review", "geometry resolution did not return a document state");
    if (resolvedGeometryState.advancedGeometryCount !== 0) fail("geometry-review", `geometry review remained unresolved (${resolvedGeometryState.advancedGeometryCount})`);
    if (resolvedGeometryState.reviewCardCount !== 1) fail("review-panel", `institution review did not remain in the primary rail (${resolvedGeometryState.reviewCardCount})`);
    if (!resolvedGeometryState.pendingReviews?.some((review) => review.kind === "institution")) fail("review-panel", "institution review disappeared after geometry resolution");
    if (!resolvedGeometryState.windowBounds) fail("window-bounds", "main-window bounds unavailable");
    await command("screencapture", ["-x", screenshot], "screencapture-after");
    const pixels = await command("python3", ["-c", pixelDiff, baseline, screenshot, JSON.stringify(resolvedGeometryState.windowBounds)], "overlay-pixels");
    const pixelResult = JSON.parse(pixels.stdout);
    if (pixelResult.newDarkPixels < 100) fail("overlay-pixels", "screen did not gain enough opaque dark detection fill");
    // T40 auto-confirmation no longer produces unresolved geometry in a real document.
    // Keep geometry coverage above on the public fixture as a separate scenario.
    await drive.send("draw-box 16 16 64 32 mask");
    const realOpenedState = (await drive.send("open " + realInput)).state;
    await drive.send("set-profile mixed");
    await command("screencapture", ["-x", realBaseline], "screencapture-real-before");
    await drive.send("start-masking");
    const runningRealState = (await drive.send("drag-canvas " + Object.values(canvasRect(realOpenedState, 0.08, 0.10, 0.30, 0.18)).join(" "))).state;
    if (!runningRealState?.maskingRunning) fail("real-running-drag", "masking run was not active during the drag attempt");
    if (runningRealState.boxes?.length !== 0) fail("real-running-drag", "drag during masking created a manual box");
    if (runningRealState.status !== "마스킹 실행 중에는 박스를 그릴 수 없습니다. 완료 후 그려 주세요.") fail("real-running-drag", "running drag did not expose the exact rejection guidance");
    const realAnalysisState = (await drive.send("wait-idle")).state;
    if (!realAnalysisState?.documentLoaded) fail("real-document-load", "real document did not report a loaded document");
    if (realAnalysisState.overlayPaintedPixelCount <= 0 || !realAnalysisState.paintedBounds) fail("real-detection", "real document did not paint a detection overlay");
    if (!realAnalysisState.windowBounds) fail("real-window-bounds", "real document window bounds unavailable");
    await command("screencapture", ["-x", realScreenshot], "screencapture-real-after");
    const realPixels = await command("python3", ["-c", pixelDiff, realBaseline, realScreenshot, JSON.stringify(realAnalysisState.windowBounds)], "real-overlay-pixels");
    const realPixelResult = JSON.parse(realPixels.stdout);
    if (realPixelResult.newDarkPixels < 100) fail("real-overlay-pixels", "real document screen did not gain enough opaque dark detection fill");
    const realMaskRect = canvasRect(realAnalysisState, 0.08, 0.10, 0.30, 0.18);
    const maskedRealState = (await drive.send("drag-canvas " + Object.values(realMaskRect).join(" "))).state;
    if (maskedRealState?.boxes?.length !== 1 || maskedRealState.boxes[0]?.mode !== "mask") fail("real-manual-mask", "post-run mask drag did not create a box");
    const appliedMaskState = (await drive.send("apply-manual")).state;
    if (appliedMaskState?.boxes?.length !== 0 || !appliedMaskState?.manualActionModes?.includes("mask")) fail("real-manual-mask", "mask apply did not commit its public manual action");
    const restoreRect = disjointRestoreRect(realAnalysisState);
    const restoredDraftState = (await drive.send("drag-canvas " + Object.values(restoreRect).join(" "))).state;
    if (restoredDraftState?.boxes?.length !== 1 || restoredDraftState.boxes[0]?.mode !== "restore") fail("real-manual-restore", "disjoint restore drag did not create a box");
    const restoredManualState = (await drive.send("apply-manual")).state;
    if (restoredManualState?.boxes?.length !== 0 || !restoredManualState?.manualActionModes?.includes("mask") || !restoredManualState?.manualActionModes?.includes("restore")) fail("real-manual-restore", "restore apply did not commit both public manual actions");
    if (restoredManualState.saveGateState !== "ready") fail("real-save-gate", `save gate was not ready (${restoredManualState.saveGateState ?? "missing"})`);
    console.log(`[real-doc][1] isolated document opened, mixed profile applied, masking completed`);
    console.log(`[real-doc][2] detection overlay painted ${realAnalysisState.overlayPaintedPixelCount} pixels`);
    console.log(`[real-doc][3] running drag rejected with no box`);
    console.log(`[real-doc][4] post-run mask drag applied and draft boxes cleared`);
    console.log(`[real-doc][5] disjoint restore applied and save gate is ready`);
    console.log(`[real-doc][6] before/after screenshots captured in the isolated-evidence directory`);
    drive.close();
    console.log(JSON.stringify({ status: "pass", preconditions: { ...hostPreconditions, appWindow, renderProbe }, geometry: { reviewCardCount: geometryState.reviewCardCount, advancedGeometryCount: geometryState.advancedGeometryCount, resolvedAdvancedGeometryCount: resolvedGeometryState.advancedGeometryCount, overlayPaintedPixelCount: geometryState.overlayPaintedPixelCount, newDarkPixels: pixelResult.newDarkPixels, screenshot }, realDocument: { overlayPaintedPixelCount: realAnalysisState.overlayPaintedPixelCount, newDarkPixels: realPixelResult.newDarkPixels, manualActionModes: restoredManualState.manualActionModes, saveGateState: restoredManualState.saveGateState, screenshots: [realBaseline, realScreenshot] } }));
  } finally {
    drive.close();
    child.kill("SIGTERM");
    await rm(scratch, { recursive: true, force: true });
  }
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : "e2e-real-app-failed";
  const code = message.match(/(?:E2E|QA_DRIVE|SCREEN|REAL_CORPUS)_[A-Z0-9_]+/)?.[0] ?? "E2E_REAL_APP_FAILED";
  console.error(`[result] ${JSON.stringify({
    status: INFRASTRUCTURE_CODES.has(code) ? "PENDING" : "FAIL",
    code,
    detail: message,
  })}`);
  console.error(message);
  process.exitCode = 1;
});
