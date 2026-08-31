import { spawn, execFile as execFileCallback } from "node:child_process";
import { copyFile, mkdir, mkdtemp, readFile, rm, stat, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import {
  ACTIVE_DISPLAY_INFO_SWIFT,
  checkHostDisplayPreconditions,
  CLAMSHELL_PROBE_COMMAND,
  inspectVisibleAppWindow,
  readActiveDisplayInfo,
  RealAppPreconditionFailure,
  verifyVisibleAppRender,
} from "./real_app_preconditions.mjs";
import {
  loadRealCorpusManifest,
  resolveRealCorpus,
} from "./real_corpus.mjs";
import {
  QA_DRIVE_TIMEOUTS_MS,
  qaDriveCommandTimeoutMs as configuredQaDriveCommandTimeoutMs,
} from "./qa_drive_timeout_config.mjs";

const execFile = promisify(execFileCallback);
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const defaultApp = join(repoRoot, "src-tauri", "target", "release", "bundle", "macos", "Nothing.app");
const defaultEvidenceDir = join(repoRoot, ".omo", "evidence", "acceptance-real-app");
const defaultReportPath = join(repoRoot, ".omo", "evidence", "T53R-baseline.md");
const COMMAND_TIMEOUT = 240_000;
const OPEN_COMMAND_TIMEOUT = configuredQaDriveCommandTimeoutMs("open");
const NAVIGATION_COMMAND_TIMEOUT = configuredQaDriveCommandTimeoutMs("go-page");
const CONTROL_COMMAND_TIMEOUT = configuredQaDriveCommandTimeoutMs("dump-state");
const LONG_COMMAND_TIMEOUT = configuredQaDriveCommandTimeoutMs("run-masking");
const MAX_PAGE_COUNT = 2_000;
const CHECK_NAMES = ["auto", "pendingReview", "reviewExclude", "otherPage", "manual", "restore", "keyword", "finalPdf"];
const INFRASTRUCTURE_CODES = new Set([
  "ACCEPT_APP_MISSING",
  "ACCEPT_PLATFORM_UNSUPPORTED",
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
  "SCREEN_COORDINATES_UNAVAILABLE",
  "SCREEN_PIXEL_ANALYSIS_UNAVAILABLE",
  "SCREEN_TEXT_RECOGNITION_UNAVAILABLE",
  "SCREEN_DISPLAY_INFO_UNAVAILABLE",
  "SCREENSHOT_SIZE_CHANGED",
  "SCREEN_DISPLAY_MISMATCH",
  "SCREEN_LAYOUT_CHANGED",
  "OFFSCREEN",
  "SCREEN_TARGET_OFFSCREEN",
  "QA_DRIVE_FINAL_OUTPUT_STAT_FAILED",
  "OS_INPUT_UNAVAILABLE",
  "PDF_RENDER_VERIFICATION_UNAVAILABLE",
  "KEYWORD_CANDIDATE_UNAVAILABLE",
  "TEXT_LAYER_UNAVAILABLE",
  "MANUAL_TEXT_TARGET_UNAVAILABLE",
  "RESTORE_TARGET_UNAVAILABLE",
  "RESTORE_CAPABILITY_UNAVAILABLE",
  "PAGE_COUNT_UNAVAILABLE",
]);

const PIXEL_DIFF = String.raw`
from PIL import Image
import json, sys

before = Image.open(sys.argv[1]).convert("RGB")
after = Image.open(sys.argv[2]).convert("RGB")
if before.size != after.size:
    raise SystemExit("SCREENSHOT_SIZE_CHANGED")
spec = json.loads(sys.argv[3])
screen = spec.get("screenSize") or {}
screen_width = float(screen.get("width") or 0)
screen_height = float(screen.get("height") or 0)
if screen_width <= 0 or screen_height <= 0:
    raise SystemExit("SCREEN_COORDINATES_UNAVAILABLE")
capture_origin = spec.get("captureOrigin") or {}
capture_origin_x = float(capture_origin.get("x") or 0)
capture_origin_y = float(capture_origin.get("y") or 0)
bitmap_width, bitmap_height = before.size
scale_x = bitmap_width / screen_width
scale_y = bitmap_height / screen_height


def bitmap_rect(css_rect):
    raw_left = round((float(css_rect["x"]) - capture_origin_x) * scale_x)
    raw_top = round((float(css_rect["y"]) - capture_origin_y) * scale_y)
    raw_right = round((float(css_rect["x"]) + float(css_rect["width"]) - capture_origin_x) * scale_x)
    raw_bottom = round((float(css_rect["y"]) + float(css_rect["height"]) - capture_origin_y) * scale_y)
    left = raw_left
    top = raw_top
    right = raw_right
    bottom = raw_bottom
    return (
        max(0, min(bitmap_width, left)),
        max(0, min(bitmap_height, top)),
        max(0, min(bitmap_width, right)),
        max(0, min(bitmap_height, bottom)),
        max(0, min(bitmap_width, raw_right) - max(0, min(bitmap_width, raw_left))),
        max(0, min(bitmap_height, raw_bottom) - max(0, min(bitmap_height, raw_top))),
    )


def analyse(css_rect):
    left, top, right, bottom, visible_width, visible_height = bitmap_rect(css_rect)
    if right <= left or bottom <= top:
        return {
            "newDarkPixels": 0,
            "changedPixels": 0,
            "area": 0,
            "visibleArea": 0,
            "overlap": False,
            "bitmapRect": [left, top, right, bottom],
        }
    before_pixels = before.load()
    after_pixels = after.load()
    new_dark = 0
    changed = 0
    dark_left = bitmap_width
    dark_top = bitmap_height
    dark_right = -1
    dark_bottom = -1
    for y in range(top, bottom):
        for x in range(left, right):
            prior = before_pixels[x, y]
            current = after_pixels[x, y]
            if max(abs(prior[channel] - current[channel]) for channel in range(3)) >= 24:
                changed += 1
            if min(prior) > 120 and max(current) < 64:
                new_dark += 1
                dark_left = min(dark_left, x)
                dark_top = min(dark_top, y)
                dark_right = max(dark_right, x)
                dark_bottom = max(dark_bottom, y)
    return {
        "newDarkPixels": new_dark,
        "changedPixels": changed,
        "area": (right - left) * (bottom - top),
        "visibleArea": visible_width * visible_height,
        "overlap": new_dark > 0,
        "bitmapRect": [left, top, right, bottom],
        "darkBounds": None if dark_right < dark_left else {
            "x": dark_left,
            "y": dark_top,
            "width": dark_right - dark_left + 1,
            "height": dark_bottom - dark_top + 1,
        },
    }

results = [analyse(target) for target in spec.get("targets", [])]
print(json.dumps({"size": {"width": bitmap_width, "height": bitmap_height}, "targets": results}))
`;

const SCREENSHOT_INFO = String.raw`
from PIL import Image
import json, sys

image = Image.open(sys.argv[1])
print(json.dumps({"width": image.width, "height": image.height}))
`;

const SCREEN_TARGET_COLOR = String.raw`
from PIL import Image
import json
import sys

image = Image.open(sys.argv[1]).convert("RGB")
spec = json.loads(sys.argv[2])
screen = spec.get("screenSize") or {}
screen_width = float(screen.get("width") or 0)
screen_height = float(screen.get("height") or 0)
capture_origin = spec.get("captureOrigin") or {}
capture_origin_x = float(capture_origin.get("x") or 0)
capture_origin_y = float(capture_origin.get("y") or 0)
bitmap_width, bitmap_height = image.size
scale_x = bitmap_width / screen_width if screen_width > 0 else 0
scale_y = bitmap_height / screen_height if screen_height > 0 else 0


def analyse(css_rect):
    raw_left = round((float(css_rect["x"]) - capture_origin_x) * scale_x)
    raw_top = round((float(css_rect["y"]) - capture_origin_y) * scale_y)
    raw_right = round((float(css_rect["x"]) + float(css_rect["width"]) - capture_origin_x) * scale_x)
    raw_bottom = round((float(css_rect["y"]) + float(css_rect["height"]) - capture_origin_y) * scale_y)
    left = max(0, min(bitmap_width, raw_left))
    top = max(0, min(bitmap_height, raw_top))
    right = max(0, min(bitmap_width, raw_right))
    bottom = max(0, min(bitmap_height, raw_bottom))
    pending = 0
    for y in range(top, bottom):
        for x in range(left, right):
            red, green, blue = image.getpixel((x, y))
            if red > blue + 20 and green > blue + 10 and red >= green:
                pending += 1
    return {
        "pendingPixels": pending,
        "visibleArea": max(0, right - left) * max(0, bottom - top),
    }


if screen_width <= 0 or screen_height <= 0:
    raise SystemExit("SCREEN_COORDINATES_UNAVAILABLE")
print(json.dumps({"targets": [analyse(target) for target in spec.get("targets", [])]}))
`;

const PDF_VERIFY = String.raw`
import fitz, hashlib, json, sys

path = sys.argv[1]
targets = json.loads(sys.argv[2])
document = fitz.open(path)
rendered = {}


def page_pixmap(page_index):
    if page_index not in rendered:
        rendered[page_index] = document[page_index].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
    return rendered[page_index]


def verify(target):
    page_index = int(target["page"])
    rect = target["rect"]
    if page_index < 0 or page_index >= document.page_count:
        return {"page": page_index, "darkRatio": 0, "meanLuma": 255, "black": False, "valid": False, "textLength": 0, "textHash": None, "expectedTextHash": target.get("expectedTextHash")}
    page = document[page_index]
    page_rect = page.rect
    x0 = max(page_rect.x0, min(page_rect.x1, float(rect["x0"])))
    y0 = max(page_rect.y0, min(page_rect.y1, float(rect["y0"])))
    x1 = max(page_rect.x0, min(page_rect.x1, float(rect["x1"])))
    y1 = max(page_rect.y0, min(page_rect.y1, float(rect["y1"])))
    if x1 <= x0 or y1 <= y0:
        return {"page": page_index, "darkRatio": 0, "meanLuma": 255, "black": False, "valid": False, "textLength": 0, "textHash": None, "expectedTextHash": target.get("expectedTextHash")}
    pixmap = page_pixmap(page_index)
    scale = 2.0
    left = max(0, min(pixmap.width, int(x0 * scale) + 1))
    top = max(0, min(pixmap.height, int(y0 * scale) + 1))
    right = max(0, min(pixmap.width, int(x1 * scale) - 1))
    bottom = max(0, min(pixmap.height, int(y1 * scale) - 1))
    if right <= left or bottom <= top:
        return {"page": page_index, "darkRatio": 0, "meanLuma": 255, "black": False, "valid": False, "textLength": 0, "textHash": None, "expectedTextHash": target.get("expectedTextHash")}
    samples = pixmap.samples
    channels = pixmap.n
    total = 0
    dark = 0
    luma_sum = 0
    for y in range(top, bottom):
        row = y * pixmap.stride
        for x in range(left, right):
            offset = row + x * channels
            rgb = samples[offset:offset + 3]
            if len(rgb) < 3:
                continue
            luma = (int(rgb[0]) + int(rgb[1]) + int(rgb[2])) / 3
            total += 1
            luma_sum += luma
            if max(rgb) < 64:
                dark += 1
    ratio = dark / total if total else 0
    mean = luma_sum / total if total else 255
    text = page.get_textbox(fitz.Rect(x0, y0, x1, y1)).strip()
    return {
        "page": page_index,
        "darkRatio": ratio,
        "meanLuma": mean,
        "black": ratio >= 0.85 and mean < 80,
        "valid": total > 0,
        "textLength": len(text),
        "textHash": hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None,
        "expectedTextHash": target.get("expectedTextHash"),
    }

results = [verify(target) for target in targets]
print(json.dumps({"targets": results, "allBlack": bool(results) and all(result["black"] for result in results)}))
document.close()
`;

const KEYWORD_CANDIDATES = String.raw`
import json, re, sys

sys.path.insert(0, sys.argv[1])
path = sys.argv[2]
from masking_extraction import _extract_pdf_page_evidence


def compact(value):
    return "".join(character.casefold() for character in str(value) if character.isalnum() or character == "_")


pages = _extract_pdf_page_evidence(path)
seen = set()
result = []
for page_index, page in enumerate(pages):
    for word in page.words:
        raw = str(word.text).strip()
        token = compact(raw)
        if len(token) < 2 or len(token) > 32 or token in seen:
            continue
        if not re.search(r"[가-힣A-Za-z]", token):
            continue
        seen.add(token)
        result.append({
            "token": token,
            "page": page_index,
            "rect": {"x0": float(word.bbox[0]), "y0": float(word.bbox[1]), "x1": float(word.bbox[2]), "y1": float(word.bbox[3])},
        })
        if len(result) >= 80:
            break
    if len(result) >= 80:
        break
print(json.dumps(result, ensure_ascii=False))
`;

const TEXT_TARGET_CANDIDATES = String.raw`
import fitz, hashlib, json, sys

path = sys.argv[1]
document = fitz.open(path)
result = []
for page_index, page in enumerate(document):
    for word in page.get_text("words", sort=True):
        text = str(word[4]).strip()
        if not text:
            continue
        rect = {"x0": float(word[0]), "y0": float(word[1]), "x1": float(word[2]), "y1": float(word[3])}
        if rect["x1"] <= rect["x0"] or rect["y1"] <= rect["y0"]:
            continue
        result.append({
            "page": page_index,
            "rect": rect,
            "textLength": len(text),
            "textHash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        })
        if len(result) >= 400:
            break
    if len(result) >= 400:
        break
document.close()
print(json.dumps(result))
`;

const SCREEN_STAGED_COLOR = String.raw`
from PIL import Image
import json, sys

image = Image.open(sys.argv[1]).convert("RGB")
spec = json.loads(sys.argv[2])
screen = spec.get("screenSize") or {}
origin = spec.get("captureOrigin") or {}
screen_width = float(screen.get("width") or 0)
screen_height = float(screen.get("height") or 0)
if screen_width <= 0 or screen_height <= 0:
    raise SystemExit("SCREEN_COORDINATES_UNAVAILABLE")
scale_x = image.width / screen_width
scale_y = image.height / screen_height
counts = []
for target in spec.get("targets", []):
    left = max(0, min(image.width, round((float(target["x"]) - float(origin.get("x") or 0)) * scale_x)))
    top = max(0, min(image.height, round((float(target["y"]) - float(origin.get("y") or 0)) * scale_y)))
    right = max(0, min(image.width, round((float(target["x"]) + float(target["width"]) - float(origin.get("x") or 0)) * scale_x)))
    bottom = max(0, min(image.height, round((float(target["y"]) + float(target["height"]) - float(origin.get("y") or 0)) * scale_y)))
    blue = 0
    orange = 0
    for y in range(top, bottom):
        for x in range(left, right):
            red, green, blue_value = image.getpixel((x, y))
            if blue_value > red + 20 and blue_value > green + 8:
                blue += 1
            if red > blue_value + 20 and green > blue_value + 8:
                orange += 1
    counts.append({"bluePixels": blue, "orangePixels": orange, "visibleArea": max(0, right - left) * max(0, bottom - top)})
print(json.dumps({"targets": counts}))
`;

const OCR_SWIFT = String.raw`
import Foundation
import Vision

let imagePath = CommandLine.arguments[1]
let request = VNRecognizeTextRequest { request, _ in
    let observations = request.results as? [VNRecognizedTextObservation] ?? []
    let text = observations.compactMap { $0.topCandidates(1).first?.string }.joined(separator: " ")
    print(text)
}
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
if #available(macOS 10.15, *) {
    request.recognitionLanguages = ["ko-KR", "en-US"]
}
let handler = VNImageRequestHandler(url: URL(fileURLWithPath: imagePath), options: [:])
do {
    try handler.perform([request])
} catch {
    fputs("OCR_FAILED\n", stderr)
}
`;

const OS_INPUT_SWIFT = String.raw`
import CoreGraphics
import Foundation

func fail(_ message: String) -> Never {
    fputs(message + "\n", stderr)
    exit(2)
}

guard CGPreflightPostEventAccess() else {
    fail("OS_INPUT_UNAVAILABLE")
}
guard CommandLine.arguments.count >= 3 else {
    fail("OS_INPUT_ARGUMENTS_INVALID")
}

let operation = CommandLine.arguments[1]
let values = CommandLine.arguments.dropFirst(2).compactMap { Double($0) }
guard values.allSatisfy({ $0.isFinite }) else {
    fail("OS_INPUT_ARGUMENTS_INVALID")
}
let source = CGEventSource(stateID: .combinedSessionState)

func post(_ type: CGEventType, _ point: CGPoint, _ button: CGMouseButton) {
    guard let event = CGEvent(
        mouseEventSource: source,
        mouseType: type,
        mouseCursorPosition: point,
        mouseButton: button,
    ) else {
        fail("OS_INPUT_UNAVAILABLE")
    }
    event.post(tap: .cghidEventTap)
}

if operation == "click" {
    guard values.count == 2 else { fail("OS_INPUT_ARGUMENTS_INVALID") }
    let point = CGPoint(x: values[0], y: values[1])
    post(.mouseMoved, point, .left)
    post(.leftMouseDown, point, .left)
    usleep(80_000)
    post(.leftMouseUp, point, .left)
} else if operation == "drag" {
    guard values.count == 4 else { fail("OS_INPUT_ARGUMENTS_INVALID") }
    let start = CGPoint(x: values[0], y: values[1])
    let end = CGPoint(x: values[2], y: values[3])
    post(.mouseMoved, start, .left)
    post(.leftMouseDown, start, .left)
    for step in 1...8 {
        let progress = CGFloat(step) / 8.0
        let point = CGPoint(
            x: start.x + (end.x - start.x) * progress,
            y: start.y + (end.y - start.y) * progress,
        )
        post(.leftMouseDragged, point, .left)
        usleep(35_000)
    }
    post(.leftMouseUp, end, .left)
} else {
    fail("OS_INPUT_OPERATION_INVALID")
}
`;

class AcceptanceFailure extends Error {
  constructor(code, pending = false, context = "") {
    super(code);
    this.name = "AcceptanceFailure";
    this.code = code;
    this.pending = pending || INFRASTRUCTURE_CODES.has(code);
    this.context = context;
  }
}

class ExternalFailure extends Error {
  constructor(command, cause) {
    super(command);
    this.name = "ExternalFailure";
    this.command = command;
    this.cause = cause;
    this.output = `${cause?.stdout ?? ""}\n${cause?.stderr ?? ""}`;
  }
}

function parseArguments(argv) {
  const options = {
    appPath: resolve(process.env.REAL_APP_PATH ?? defaultApp),
    evidenceDir: resolve(process.env.T53R_EVIDENCE_DIR ?? defaultEvidenceDir),
    reportPath: resolve(process.env.T53R_REPORT_PATH ?? defaultReportPath),
    alias: null,
    help: false,
  };
  for (let index = 2; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--help" || argument === "-h") {
      options.help = true;
    } else if (argument === "--app") {
      options.appPath = resolve(argv[++index] ?? "");
    } else if (argument === "--evidence-dir") {
      options.evidenceDir = resolve(argv[++index] ?? "");
    } else if (argument === "--report") {
      options.reportPath = resolve(argv[++index] ?? "");
    } else if (argument === "--alias") {
      options.alias = argv[++index] ?? "";
    } else {
      throw new AcceptanceFailure("ACCEPT_ARGUMENTS_INVALID");
    }
  }
  return options;
}

function printUsage() {
  return [
    "Usage: node scripts/acceptance_real_app.mjs [--app PATH] [--evidence-dir PATH] [--report PATH] [--alias ALIAS]",
    "The default run resolves all 15 documents from the hash-only real corpus manifest.",
  ].join("\n");
}

function safeErrorCode(error, fallback = "ACCEPTANCE_FAILED") {
  if (error instanceof AcceptanceFailure) return error.code;
  const source = error instanceof ExternalFailure ? error.output : String(error?.message ?? "");
  const codes = source.match(/[A-Z][A-Z0-9_]{2,}/g) ?? [];
  return codes.find((code) => code.startsWith("QA_DRIVE_") || code.startsWith("E2E_") || code.startsWith("REAL_CORPUS_"))
    ?? fallback;
}

function errorDetail(error, fallback = "ACCEPTANCE_FAILED") {
  const code = safeErrorCode(error, fallback);
  return error instanceof AcceptanceFailure && error.context
    ? `${code} (command=${error.context})`
    : code;
}

function failureFromExternal(error, unavailableCode, failedCode = unavailableCode) {
  const output = error instanceof ExternalFailure ? error.output : "";
  const reportedCode = output.match(/(?:SCREENSHOT_SIZE_CHANGED|SCREEN_COORDINATES_UNAVAILABLE|OCR_FAILED)/)?.[0];
  if (reportedCode === "SCREEN_COORDINATES_UNAVAILABLE") return new AcceptanceFailure(reportedCode, true);
  if (reportedCode === "SCREENSHOT_SIZE_CHANGED") return new AcceptanceFailure(reportedCode, true);
  if (/No module named|ModuleNotFoundError|not found|ENOENT|permission denied/i.test(output)) {
    return new AcceptanceFailure(unavailableCode, true);
  }
  return new AcceptanceFailure(failedCode, false);
}

async function runExternal(file, args, timeout = COMMAND_TIMEOUT) {
  try {
    return await execFile(file, args, { timeout, maxBuffer: 4 * 1024 * 1024 });
  } catch (error) {
    throw new ExternalFailure(file, error);
  }
}

async function packagedExecutable(appPath) {
  if (!existsSync(appPath)) throw new AcceptanceFailure("ACCEPT_APP_MISSING", true);
  const infoPath = join(appPath, "Contents", "Info.plist");
  try {
    const { stdout } = await runExternal("plutil", ["-extract", "CFBundleExecutable", "raw", "-o", "-", infoPath], 15_000);
    const executable = join(appPath, "Contents", "MacOS", stdout.trim());
    if (!existsSync(executable)) throw new AcceptanceFailure("ACCEPT_APP_MISSING", true);
    return executable;
  } catch (error) {
    if (error instanceof AcceptanceFailure) throw error;
    throw failureFromExternal(error, "ACCEPT_APP_MISSING");
  }
}

function createDrive(child) {
  const pending = new Map();
  let commandSequence = 0;
  let stdoutBuffer = "";
  let stderrBuffer = "";
  let readyResolve;
  let readyReject;
  let readyReceived = false;
  let closed = false;
  const ready = new Promise((resolveReady, rejectReady) => {
    readyResolve = resolveReady;
    readyReject = rejectReady;
  });
  const readyTimeout = setTimeout(() => readyReject(new AcceptanceFailure("FRONTEND_READY_TIMEOUT", true)), QA_DRIVE_TIMEOUTS_MS.startup);

  const rejectPending = (error) => {
    if (!closed) {
      closed = true;
      clearTimeout(readyTimeout);
      readyReject(error);
    }
    for (const request of pending.values()) request.reject(error);
    pending.clear();
  };

  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    stdoutBuffer += chunk;
    const lines = stdoutBuffer.split("\n");
    stdoutBuffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.trim()) continue;
      let transcript;
      try {
        transcript = JSON.parse(line);
      } catch {
        rejectPending(new AcceptanceFailure("QA_DRIVE_PROTOCOL_FAILED", true));
        return;
      }
      if (transcript.command === "ready") {
        clearTimeout(readyTimeout);
        readyReceived = true;
        readyResolve(transcript);
      } else {
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
    }
  });
  child.stderr.on("data", (chunk) => {
    stderrBuffer += chunk;
    if (!readyReceived && /QA_DRIVE_(?:FRONTEND|STDIN|STATE|COMMAND)/.test(stderrBuffer)) {
      rejectPending(new AcceptanceFailure("QA_DRIVE_FRONTEND_UNAVAILABLE", true));
    }
  });
  child.on("error", () => rejectPending(new AcceptanceFailure("QA_DRIVE_FRONTEND_UNAVAILABLE", true)));
  child.on("close", () => rejectPending(new AcceptanceFailure("QA_DRIVE_PROCESS_EXITED", true)));

  return {
    async waitUntilReady() {
      const transcript = await ready;
      if (!transcript?.ok) throw new AcceptanceFailure("QA_DRIVE_FRONTEND_NOT_READY", true);
    },
    async send(command) {
      if (closed) throw new AcceptanceFailure("QA_DRIVE_PROCESS_EXITED", true);
      const commandName = command.trim().split(/\s+/)[0] || "unknown";
      const requestId = `qa-${commandSequence++}`;
      const transcript = await new Promise((resolveResponse, rejectResponse) => {
        const timeoutDuration = commandName === "open"
          ? OPEN_COMMAND_TIMEOUT
          : ["go-page", "scroll-to", "inspect-target", "resolve-geometry"].includes(commandName)
            ? NAVIGATION_COMMAND_TIMEOUT
            : ["apply-keyword", "run-masking", "wait-idle", "apply-manual", "confirm-save", "wait-save", "save-final"].includes(commandName)
              ? LONG_COMMAND_TIMEOUT
              : CONTROL_COMMAND_TIMEOUT;
        const timeout = setTimeout(() => {
          pending.delete(requestId);
          rejectResponse(new AcceptanceFailure(
            commandName === "open" ? "QA_DRIVE_RENDER_UNAVAILABLE" : "QA_DRIVE_COMMAND_TIMEOUT",
            true,
            commandName,
          ));
        }, timeoutDuration);
        pending.set(requestId, {
          resolve: (response) => {
            clearTimeout(timeout);
            resolveResponse(response);
          },
          reject: (error) => {
            clearTimeout(timeout);
            rejectResponse(error);
          },
        });
        try {
          child.stdin.write(`${command}\n`);
        } catch {
          clearTimeout(timeout);
          pending.delete(requestId);
          rejectResponse(new AcceptanceFailure("QA_DRIVE_FRONTEND_UNAVAILABLE", true));
        }
      });
      if (!transcript?.ok) {
        const code = safeErrorCode(transcript?.error, "QA_DRIVE_COMMAND_FAILED");
        throw new AcceptanceFailure(code, INFRASTRUCTURE_CODES.has(code), commandName);
      }
      return transcript;
    },
    close() {
      if (closed) return;
      rejectPending(new AcceptanceFailure("QA_DRIVE_PROCESS_EXITED", true));
      try {
        child.stdin.end();
      } catch {
        // Process cleanup below is authoritative.
      }
    },
  };
}

function stateOf(transcript) {
  if (!transcript?.state || typeof transcript.state !== "object" || Array.isArray(transcript.state)) {
    throw new AcceptanceFailure("QA_STATE_UNAVAILABLE", true);
  }
  return transcript.state;
}

async function preflightPackagedApp(executable, appPath, hostPreconditions) {
  const scratch = await mkdtemp(join(tmpdir(), "nothing-accept-preflight-"));
  const outputPath = join(scratch, "preflight-output.pdf");
  let child = null;
  let drive = null;
  try {
    child = spawn(executable, ["--qa-drive-stdin"], {
      cwd: appPath,
      env: {
        ...process.env,
        MASK_TOOL_QA_DRIVE: "1",
        MASK_TOOL_ALLOWED_DIRS: scratch,
        MASK_TOOL_QA_FINAL_OUTPUT_PATH: outputPath,
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    drive = createDrive(child);
    await drive.waitUntilReady();
    const window = await inspectVisibleAppWindow(child.pid, hostPreconditions.displays);
    const render = await verifyVisibleAppRender({ drive, window, directory: scratch });
    return {
      ...hostPreconditions,
      appWindow: {
        windowId: window.windowId,
        bounds: window.bounds,
        source: window.source,
      },
      render,
    };
  } finally {
    await terminateChild(child, drive);
    await rm(scratch, { recursive: true, force: true });
  }
}

async function launchDrive(executable, appPath, scratch, outputPath, { verifyWindow = true, displays = [] } = {}) {
  const child = spawn(executable, ["--qa-drive-stdin"], {
    cwd: appPath,
    env: {
      ...process.env,
      MASK_TOOL_QA_DRIVE: "1",
      MASK_TOOL_ALLOWED_DIRS: scratch,
      MASK_TOOL_QA_FINAL_OUTPUT_PATH: outputPath,
    },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const drive = createDrive(child);
  try {
    await drive.waitUntilReady();
    if (verifyWindow) await inspectVisibleAppWindow(child.pid, displays);
    return { child, drive };
  } catch (error) {
    await terminateChild(child, drive);
    throw error;
  }
}

async function terminateChild(child, drive) {
  drive?.close();
  if (!child || child.exitCode !== null || child.signalCode !== null) return;
  await new Promise((resolveExit) => {
    let settled = false;
    const finish = () => {
      if (settled) return;
      settled = true;
      resolveExit();
    };
    child.once("close", finish);
    child.kill("SIGTERM");
    setTimeout(() => {
      if (!settled) child.kill("SIGKILL");
      setTimeout(finish, 1_000);
    }, 5_000);
  });
}

function openAttemptStage(error) {
  return error instanceof AcceptanceFailure && error.context
    ? error.context
    : "open";
}

function openAttemptOutcome(attempts) {
  if (attempts.some((attempt) => attempt.outcome === "PASS_AFTER_RETRY")) {
    return "PASS_AFTER_RETRY";
  }
  if (attempts.some((attempt) => attempt.outcome === "PASS")) return "PASS";
  if (attempts.length === 0) return "NOT_RUN";
  return attempts.at(-1).outcome;
}

let activeDisplayInfoPromise = null;

async function activeDisplayInfo() {
  if (activeDisplayInfoPromise === null) {
    activeDisplayInfoPromise = readActiveDisplayInfo()
      .catch((error) => {
        if (error instanceof AcceptanceFailure) throw error;
        throw new AcceptanceFailure("SCREEN_DISPLAY_INFO_UNAVAILABLE", true);
      });
  }
  return activeDisplayInfoPromise;
}

function captureDisplayForState(state, displays) {
  const screen = state?.screenSize;
  const dpr = Number(state?.devicePixelRatio);
  const screenWidth = Number(screen?.width);
  const screenHeight = Number(screen?.height);
  const windowBounds = state?.windowBounds;
  const windowWidth = Number(windowBounds?.width);
  const windowHeight = Number(windowBounds?.height);
  const windowX = Number(windowBounds?.x);
  const windowY = Number(windowBounds?.y);
  if (
    !finite(screenWidth)
    || !finite(screenHeight)
    || screenWidth <= 0
    || screenHeight <= 0
    || !finite(dpr)
    || dpr <= 0
    || !finite(windowX)
    || !finite(windowY)
    || !finite(windowWidth)
    || !finite(windowHeight)
    || windowWidth <= 0
    || windowHeight <= 0
  ) {
    throw new AcceptanceFailure("SCREEN_COORDINATES_UNAVAILABLE", true);
  }

  const centerX = (windowX + windowWidth / 2) / dpr;
  const centerY = (windowY + windowHeight / 2) / dpr;
  const display = displays.find((candidate) => (
    centerX >= candidate.x - 1
    && centerX <= candidate.x + candidate.width + 1
    && centerY >= candidate.y - 1
    && centerY <= candidate.y + candidate.height + 1
  ));
  if (!display || !sameNumber(display.width, screenWidth, 1) || !sameNumber(display.height, screenHeight, 1)) {
    throw new AcceptanceFailure("SCREEN_DISPLAY_MISMATCH", true);
  }
  return {
    ...display,
    nativeWidth: Math.round(display.width * dpr),
    nativeHeight: Math.round(display.height * dpr),
  };
}

function validateCaptureDisplay(size, state, display) {
  if (
    size.width !== display.nativeWidth
    || size.height !== display.nativeHeight
    || !sameNumber(display.width, Number(state?.screenSize?.width), 1)
    || !sameNumber(display.height, Number(state?.screenSize?.height), 1)
  ) {
    throw new AcceptanceFailure("SCREEN_DISPLAY_MISMATCH", true);
  }
}

async function captureScreenshot(path, state = null) {
  try {
    await rm(path, { force: true });
    const display = state ? captureDisplayForState(state, await activeDisplayInfo()) : null;
    const captureArgs = display
      ? ["-x", `-R${Math.round(display.x)},${Math.round(display.y)},${Math.round(display.width)},${Math.round(display.height)}`, path]
      : ["-x", path];
    await runExternal("screencapture", captureArgs, 30_000);
    const details = await stat(path);
    if (!details.isFile() || details.size === 0) throw new AcceptanceFailure("SCREEN_CAPTURE_UNAVAILABLE", true);
    const size = await runJsonPython(
      SCREENSHOT_INFO,
      [path],
      "SCREEN_PIXEL_ANALYSIS_UNAVAILABLE",
      "SCREEN_PIXEL_ANALYSIS_UNAVAILABLE",
    );
    if (!finite(size?.width) || !finite(size?.height) || size.width <= 0 || size.height <= 0) {
      throw new AcceptanceFailure("SCREEN_PIXEL_ANALYSIS_UNAVAILABLE", true);
    }
    if (state) validateCaptureDisplay(size, state, display);
    return {
      path,
      width: size.width,
      height: size.height,
      origin: display ? { x: display.x, y: display.y } : { x: 0, y: 0 },
      displayId: display?.id ?? null,
    };
  } catch (error) {
    if (error instanceof AcceptanceFailure) throw error;
    throw failureFromExternal(error, "SCREEN_CAPTURE_UNAVAILABLE");
  }
}

async function runJsonPython(source, args, unavailableCode, failedCode) {
  try {
    const { stdout } = await runExternal("python3", ["-c", source, ...args]);
    const lines = stdout.trim().split("\n").filter(Boolean);
    return JSON.parse(lines.at(-1) ?? "{}");
  } catch (error) {
    if (error instanceof AcceptanceFailure) throw error;
    throw failureFromExternal(error, unavailableCode, failedCode);
  }
}

async function screenshotDiff(before, after, state, rects) {
  const beforePath = typeof before === "string" ? before : before.path;
  const afterPath = typeof after === "string" ? after : after.path;
  if (typeof before !== "string" && typeof after !== "string"
      && (
        before.width !== after.width
        || before.height !== after.height
        || before.displayId !== after.displayId
        || before.origin?.x !== after.origin?.x
        || before.origin?.y !== after.origin?.y
      )) {
    throw new AcceptanceFailure("SCREENSHOT_SIZE_CHANGED", true);
  }
  const targets = rects.map((rect) => screenRectForPdfRect(state, rect));
  const captureOrigin = typeof before === "string" ? { x: 0, y: 0 } : before.origin ?? { x: 0, y: 0 };
  try {
    return await runJsonPython(
      PIXEL_DIFF,
      [beforePath, afterPath, JSON.stringify({ screenSize: state.screenSize, captureOrigin, targets })],
      "SCREEN_PIXEL_ANALYSIS_UNAVAILABLE",
      "SCREEN_PIXEL_COMPARE_FAILED",
    );
  } catch (error) {
    if (error instanceof AcceptanceFailure && error.code === "SCREEN_PIXEL_COMPARE_FAILED") {
      throw new AcceptanceFailure("SCREEN_PIXEL_ANALYSIS_UNAVAILABLE", true);
    }
    throw error;
  }
}

async function screenshotTargetColor(capture, state, rect) {
  const target = screenRectForPdfRect(state, rect);
  return runJsonPython(
    SCREEN_TARGET_COLOR,
    [capture.path, JSON.stringify({
      screenSize: state.screenSize,
      captureOrigin: capture.origin ?? { x: 0, y: 0 },
      targets: [target],
    })],
    "SCREEN_PIXEL_ANALYSIS_UNAVAILABLE",
    "SCREEN_PIXEL_COMPARE_FAILED",
  ).then((result) => result?.targets?.[0] ?? null);
}

async function verifyFinalPdf(path, targets) {
  try {
    return await runJsonPython(
      PDF_VERIFY,
      [path, JSON.stringify(targets)],
      "PDF_RENDER_VERIFICATION_UNAVAILABLE",
      "PDF_RENDER_VERIFICATION_FAILED",
    );
  } catch (error) {
    if (error instanceof AcceptanceFailure && error.code === "PDF_RENDER_VERIFICATION_FAILED") {
      throw new AcceptanceFailure("PDF_RENDER_VERIFICATION_UNAVAILABLE", true);
    }
    throw error;
  }
}

async function extractKeywordCandidates(path) {
  return runJsonPython(
    KEYWORD_CANDIDATES,
    [repoRoot, path],
    "KEYWORD_CANDIDATE_UNAVAILABLE",
    "KEYWORD_CANDIDATE_UNAVAILABLE",
  );
}

async function extractTextTargetCandidates(path) {
  return runJsonPython(
    TEXT_TARGET_CANDIDATES,
    [path],
    "TEXT_LAYER_UNAVAILABLE",
    "TEXT_LAYER_UNAVAILABLE",
  );
}

async function screenshotStagedColor(capture, state, rect) {
  const target = screenRectForPdfRect(state, rect);
  return runJsonPython(
    SCREEN_STAGED_COLOR,
    [capture.path, JSON.stringify({
      screenSize: state.screenSize,
      captureOrigin: capture.origin ?? { x: 0, y: 0 },
      targets: [target],
    })],
    "SCREEN_PIXEL_ANALYSIS_UNAVAILABLE",
    "SCREEN_PIXEL_COMPARE_FAILED",
  ).then((result) => result?.targets?.[0] ?? null);
}

async function ocrScreenshot(path, scriptPath) {
  try {
    const { stdout } = await runExternal("swift", [scriptPath, path], 60_000);
    return { available: true, text: stdout.replace(/\s+/g, " ").trim() };
  } catch (swiftError) {
    try {
      const { stdout } = await runExternal("tesseract", [path, "stdout", "-l", "kor+eng", "--psm", "6"], 60_000);
      return { available: true, text: stdout.replace(/\s+/g, " ").trim() };
    } catch {
      throw failureFromExternal(swiftError, "SCREEN_TEXT_RECOGNITION_UNAVAILABLE");
    }
  }
}

function finite(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function sameNumber(left, right, tolerance = 0.5) {
  return finite(left) && finite(right) && Math.abs(left - right) <= tolerance;
}

function acceptanceColumn(header) {
  if (header === "alias") return "alias";
  if (header === "sha256") return "sha256";
  if (header.includes("저장 PDF")) return "finalPdf";
  if (header.includes("자동")) return "auto";
  if (header.includes("검토 대기")) return "pendingReview";
  if (header.includes("검토 행 제외")) return "reviewExclude";
  if (header.includes("다른 페이지")) return "otherPage";
  if (header.includes("수동 픽셀") || header.includes("수동 실제 텍스트")) return "manual";
  if (header.includes("복원/잔존")) return "restore";
  if (header.includes("키워드 픽셀")) return "keyword";
  if (header.includes("문서 판정")) return "document";
  if (header === "증거") return "evidence";
  return null;
}

function captureFrameMismatchFields(before, after) {
  const mismatches = [];
  const addMismatch = (field) => {
    if (!mismatches.includes(field)) mismatches.push(field);
  };
  const scalarFields = [
    ["devicePixelRatio", 0.01],
    ["scale", 0.01],
    ["currentResultPage", 0],
    ["currentOrigPage", 0],
  ];
  if (before?.overlayVisibility !== "hidden" && after?.overlayVisibility !== "visible") {
    addMismatch("overlayVisibility");
  }
  const scrollSignature = (value) => {
    if (!Array.isArray(value)) return null;
    return value
      .filter((offset) => sameNumber(offset?.left, 0) === false || sameNumber(offset?.top, 0) === false)
      .map((offset) => ({
        tag: offset.tag,
        id: offset.id,
        className: offset.className,
        left: offset.left,
        top: offset.top,
      }))
      .sort((left, right) => JSON.stringify(left).localeCompare(JSON.stringify(right)));
  };
  const beforeScrollSignature = scrollSignature(before?.scrollOffsets);
  const afterScrollSignature = scrollSignature(after?.scrollOffsets);
  if (beforeScrollSignature === null || afterScrollSignature === null) {
    addMismatch("scrollOffsets");
  } else if (JSON.stringify(beforeScrollSignature) !== JSON.stringify(afterScrollSignature)) {
    addMismatch("scrollOffsets");
  }
  for (const [field, tolerance] of scalarFields) {
    if (!sameNumber(before?.[field], after?.[field], tolerance)) addMismatch(field);
  }
  for (const field of [
    "screenSize",
    "screenBounds",
    "windowBounds",
    "viewport",
    "overlayPixels",
    "overlayBounds",
    "scrollViewport",
    "scrollPosition",
    "documentScroll",
  ]) {
    const left = before?.[field];
    const right = after?.[field];
    if (!left || !right || Object.keys(left).length !== Object.keys(right).length) {
      addMismatch(field);
      continue;
    }
    for (const key of Object.keys(left)) {
      if (!sameNumber(left[key], right[key], 0.5)) {
        addMismatch(field);
        break;
      }
    }
  }
  return mismatches;
}

function screenVisibleArea(state, rect, display) {
  const target = screenRectForPdfRect(state, rect);
  const screenLeft = Number(display?.x);
  const screenTop = Number(display?.y);
  const screenWidth = Number(display?.width);
  const screenHeight = Number(display?.height);
  if (
    !finite(screenLeft)
    || !finite(screenTop)
    || !finite(screenWidth)
    || !finite(screenHeight)
    || screenWidth <= 0
    || screenHeight <= 0
  ) {
    throw new AcceptanceFailure("SCREEN_COORDINATES_UNAVAILABLE", true);
  }
  const left = Math.max(screenLeft, target.x);
  const top = Math.max(screenTop, target.y);
  const right = Math.min(screenLeft + screenWidth, target.x + target.width);
  const bottom = Math.min(screenTop + screenHeight, target.y + target.height);
  return Math.max(0, right - left) * Math.max(0, bottom - top);
}

async function prepareVisibleTarget(drive, page, rect) {
  const command = `scroll-to ${page + 1} ${rect.x0} ${rect.y0} ${rect.x1} ${rect.y1}`;
  const state = stateOf(await drive.send(command));
  const display = captureDisplayForState(state, await activeDisplayInfo());
  const inspection = state.targetInspection;
  if (!inspection || inspection.page !== page) throw new AcceptanceFailure("QA_DRIVE_TARGET_INSPECTION_UNAVAILABLE", true);
  if (!inspection.visible || inspection.visibleArea <= 0) {
    throw new AcceptanceFailure("OFFSCREEN", true);
  }
  if (screenVisibleArea(state, rect, display) <= 0) throw new AcceptanceFailure("SCREEN_TARGET_OFFSCREEN", true);
  return state;
}

async function stableMeasurementFrame(drive, rect, captureState = null) {
  void rect;
  const first = stateOf(await drive.send("wait-idle"));
  const second = stateOf(await drive.send("dump-state"));
  const mismatch = captureFrameMismatchFields(first, second);
  if (mismatch.length > 0) {
    throw new AcceptanceFailure("SCREEN_LAYOUT_CHANGED", true, `frame-stability:${mismatch.join(",")}`);
  }
  return captureState ?? first;
}

function windowContentScreenOrigin(state) {
  const dpr = finite(Number(state.devicePixelRatio)) && Number(state.devicePixelRatio) > 0
    ? Number(state.devicePixelRatio)
    : 1;
  const viewport = state.viewport;
  const windowBounds = state.windowBounds;
  const windowX = Number(windowBounds?.x);
  const windowY = Number(windowBounds?.y);
  const windowWidth = Number(windowBounds?.width);
  const windowHeight = Number(windowBounds?.height);
  const viewportWidth = Number(viewport?.width);
  const viewportHeight = Number(viewport?.height);
  return windowBounds && viewport
    && finite(windowX) && finite(windowY) && finite(windowWidth) && finite(windowHeight)
    && finite(viewportWidth) && finite(viewportHeight)
    ? {
      x: windowX / dpr + Math.max(0, (windowWidth / dpr - viewportWidth) / 2),
      y: windowY / dpr + Math.max(0, (windowHeight / dpr - viewportHeight)),
    }
    : null;
}

function screenContentOrigin(state) {
  const windowContentOrigin = windowContentScreenOrigin(state);
  const contentOrigin = state.contentScreenOrigin;
  const screenOrigin = state.screenOrigin;
  if (windowContentOrigin) return windowContentOrigin;
  if (contentOrigin && finite(Number(contentOrigin.x)) && finite(Number(contentOrigin.y))) {
    return { x: Number(contentOrigin.x), y: Number(contentOrigin.y) };
    }
  if (screenOrigin && finite(Number(screenOrigin.x)) && finite(Number(screenOrigin.y))) {
    return { x: Number(screenOrigin.x), y: Number(screenOrigin.y) };
}
  return null;
}

function screenRectForPdfRect(state, rect) {
  const overlay = state.overlayBounds;
  const pixels = state.overlayPixels;
  const scale = state.scale;
  const origin = screenContentOrigin(state);
  if (!overlay || !pixels || !origin || !state.screenSize
      || !finite(overlay.x) || !finite(overlay.y) || !finite(overlay.width) || !finite(overlay.height)
      || !finite(pixels.width) || !finite(pixels.height) || pixels.width <= 0 || pixels.height <= 0
      || !finite(scale) || scale <= 0 || !finite(origin.x) || !finite(origin.y)
      || !finite(state.screenSize.width) || !finite(state.screenSize.height)) {
    throw new AcceptanceFailure("SCREEN_COORDINATES_UNAVAILABLE", true);
  }
  const leftPdf = Math.min(Number(rect.x0), Number(rect.x1));
  const topPdf = Math.min(Number(rect.y0), Number(rect.y1));
  const rightPdf = Math.max(Number(rect.x0), Number(rect.x1));
  const bottomPdf = Math.max(Number(rect.y0), Number(rect.y1));
  const cssScaleX = overlay.width / pixels.width;
  const cssScaleY = overlay.height / pixels.height;
  return {
    x: origin.x + overlay.x + leftPdf * scale * cssScaleX,
    y: origin.y + overlay.y + topPdf * scale * cssScaleY,
    width: Math.max(1, (rightPdf - leftPdf) * scale * cssScaleX),
    height: Math.max(1, (bottomPdf - topPdf) * scale * cssScaleY),
  };
}

async function osDragCanvas(scriptPath, state, rect) {
  const target = screenRectForPdfRect(state, rect);
  const insetX = Math.min(Math.max(1, target.width * 0.05), target.width / 4);
  const insetY = Math.min(Math.max(1, target.height * 0.05), target.height / 4);
  const start = {
    x: target.x + insetX,
    y: target.y + insetY,
  };
  const end = {
    x: target.x + target.width - insetX,
    y: target.y + target.height - insetY,
  };
  try {
    await runExternal(
      "swift",
      [scriptPath, "drag", String(start.x), String(start.y), String(end.x), String(end.y)],
      45_000,
    );
  } catch (error) {
    if (error instanceof AcceptanceFailure) throw error;
    const output = error instanceof ExternalFailure ? error.output : "";
    if (/OS_INPUT_UNAVAILABLE|accessibility|not authorized|permission denied|CGPreflight/i.test(output)) {
      throw new AcceptanceFailure("OS_INPUT_UNAVAILABLE", true);
    }
    throw error;
  }
}

async function osClickFinalSave(scriptPath, state) {
  const button = state?.finalSaveButtonBounds;
  if (
    !button
    || !finite(button.x)
    || !finite(button.y)
    || !finite(button.width)
    || !finite(button.height)
    || button.width <= 0
    || button.height <= 0
  ) {
    throw new AcceptanceFailure("SCREEN_COORDINATES_UNAVAILABLE", true);
  }
  const origin = screenContentOrigin(state);
  if (!origin || !finite(origin.x) || !finite(origin.y)) {
    throw new AcceptanceFailure("SCREEN_COORDINATES_UNAVAILABLE", true);
  }
  const x = origin.x + button.x + button.width / 2;
  const y = origin.y + button.y + button.height / 2;
  try {
    await runExternal("swift", [scriptPath, "click", String(x), String(y)], 45_000);
  } catch (error) {
    if (error instanceof AcceptanceFailure) throw error;
    const output = error instanceof ExternalFailure ? error.output : "";
    if (/OS_INPUT_UNAVAILABLE|accessibility|not authorized|permission denied|CGPreflight/i.test(output)) {
      throw new AcceptanceFailure("OS_INPUT_UNAVAILABLE", true);
    }
    throw error;
  }
}

function pageSize(state) {
  if (state.pageSize && finite(state.pageSize.width) && finite(state.pageSize.height)) return state.pageSize;
  if (state.overlayPixels && finite(state.overlayPixels.width) && finite(state.overlayPixels.height)
      && finite(state.scale) && state.scale > 0) {
    return { width: state.overlayPixels.width / state.scale, height: state.overlayPixels.height / state.scale };
  }
  throw new AcceptanceFailure("SCREEN_COORDINATES_UNAVAILABLE", true);
}

function rectsOverlap(left, right) {
  return Math.max(left.x0, right.x0) < Math.min(left.x1, right.x1)
    && Math.max(left.y0, right.y0) < Math.min(left.y1, right.y1);
}

function sameRectLists(left, right, tolerance = 0.5) {
  return Array.isArray(left)
    && Array.isArray(right)
    && left.length === right.length
    && left.every((leftRect, index) => {
      const rightRect = right[index];
      return rightRect
        && ["x0", "y0", "x1", "y1"].every((key) =>
          sameNumber(leftRect?.[key], rightRect?.[key], tolerance));
    });
}

function occurrenceRects(state, predicate = () => true) {
  return (Array.isArray(state.occurrences) ? state.occurrences : [])
    .filter(predicate)
    .flatMap((occurrence) => (Array.isArray(occurrence.rects) ? occurrence.rects : [])
      .map((rect) => ({ page: occurrence.page, rect, occurrenceId: occurrence.occurrenceId })));
}

function autoOccurrences(state) {
  const linkedOccurrenceIds = new Set(
    (Array.isArray(state.manualActions) ? state.manualActions : [])
      .map((action) => action.linkedOccurrenceId)
      .filter((value) => typeof value === "string"),
  );
  return occurrenceRects(state, (occurrence) => occurrence.proposedAction === "mask"
    && occurrence.category !== "custom_keyword"
    && ["confirmed", "user_confirmed"].includes(occurrence.state)
    && !linkedOccurrenceIds.has(occurrence.occurrenceId));
}

function confirmedAutomaticOccurrenceEntries(state) {
  return (Array.isArray(state.occurrences) ? state.occurrences : [])
    .filter((occurrence) => occurrence.proposedAction === "mask"
      && occurrence.category !== "custom_keyword"
      && ["confirmed", "user_confirmed"].includes(occurrence.state));
}

function occurrenceBoundingRect(occurrence) {
  const rects = Array.isArray(occurrence?.rects) ? occurrence.rects : [];
  if (rects.length === 0) return null;
  return {
    x0: Math.min(...rects.map((rect) => rect.x0)),
    y0: Math.min(...rects.map((rect) => rect.y0)),
    x1: Math.max(...rects.map((rect) => rect.x1)),
    y1: Math.max(...rects.map((rect) => rect.y1)),
  };
}

function restoreScenarioTargets(state, textCandidates) {
  const candidates = confirmedAutomaticOccurrenceEntries(state)
    .filter((occurrence) => {
      const bounds = occurrenceBoundingRect(occurrence);
      return typeof occurrence.expectedTextHash === "string"
        && occurrence.rects?.length > 0
        && bounds !== null
        && textCandidates.some((candidate) =>
          candidate?.page === occurrence.page
            && candidate.textLength > 0
            && candidate.rect
            && rectsOverlap(bounds, candidate.rect));
    });
  if (candidates.length < 2) return null;
  const restored = candidates[0];
  const untouched = candidates.find((candidate) => candidate.occurrenceId !== restored.occurrenceId);
  if (!restored || !untouched) return null;
  return {
    restored,
    untouched,
    dragRect: occurrenceBoundingRect(restored),
    restoredPdfTargets: restored.rects.map((rect) => ({
      page: restored.page,
      rect,
      expectedTextHash: restored.expectedTextHash,
    })),
    untouchedPdfTargets: untouched.rects.map((rect) => ({ page: untouched.page, rect })),
  };
}

function findManualTextTarget(state, textCandidates) {
  const occupied = occurrenceRects(state);
  for (const candidate of textCandidates) {
    if (
      !candidate
      || !finite(candidate.page)
      || !candidate.rect
      || !finite(candidate.textLength)
      || candidate.textLength <= 0
      || typeof candidate.textHash !== "string"
      || candidate.rect.x1 - candidate.rect.x0 < 8
      || candidate.rect.y1 - candidate.rect.y0 < 5
    ) continue;
    const rect = {
      x0: Math.max(0, candidate.rect.x0 - 10),
      y0: Math.max(0, candidate.rect.y0 - 10),
      x1: candidate.rect.x1 + 10,
      y1: candidate.rect.y1 + 10,
    };
    if (occupied.some((entry) => entry.page === candidate.page && rectsOverlap(entry.rect, rect))) continue;
    return { ...candidate, textRect: candidate.rect, rect };
  }
  throw new AcceptanceFailure("MANUAL_TEXT_TARGET_UNAVAILABLE", true);
}

function findKeywordTarget(candidates, state, occupied, manualTarget) {
  for (const candidate of candidates) {
    if (!candidate || typeof candidate.token !== "string" || !candidate.token.trim()) continue;
    const rect = candidate.rect;
    if (!rect || !finite(candidate.page) || occupied.some((entry) => entry.page === candidate.page && rectsOverlap(entry.rect, rect))) continue;
    if (manualTarget && candidate.page === manualTarget.page && rectsOverlap(manualTarget.rect, rect)) continue;
    return candidate;
  }
  return null;
}

function keywordOccurrences(state) {
  return occurrenceRects(state, (occurrence) => occurrence.category === "custom_keyword"
    && occurrence.proposedAction === "mask"
    && ["confirmed", "user_confirmed"].includes(occurrence.state));
}

function pendingTargetEntries(state) {
  return (Array.isArray(state.pendingTargets) ? state.pendingTargets : [])
    .flatMap((target) => (Array.isArray(target.rects) ? target.rects : [])
      .map((rect) => ({ page: target.page, rect, reviewId: target.reviewId, kind: target.kind })));
}

function excludedTargetEntries(state) {
  return occurrenceRects(state, (occurrence) => occurrence.proposedAction === "exclude")
    .map((entry) => ({ page: entry.page, rect: entry.rect }));
}

function finalManifestTargetGroups(state) {
  const auto = autoOccurrences(state).map((entry) => ({ page: entry.page, rect: entry.rect }));
  const manual = (Array.isArray(state.manualActions) ? state.manualActions : [])
    .filter((action) => action.mode === "mask")
    .flatMap((action) => (Array.isArray(action.rects) ? action.rects : [])
      .map((rect) => ({ page: action.page, rect })));
  const keyword = keywordOccurrences(state).map((entry) => ({ page: entry.page, rect: entry.rect }));
  return { auto, manual, keyword };
}

function hasPageGuidanceText(text) {
  return /첫\s*마스킹\s*위치|현재\s*페이지.*(?:유지|마스킹)|페이지별|\d+\s*쪽.*\d+\s*건/.test(text);
}

function reviewAction(kind) {
  switch (kind) {
    case "name":
    case "institution": return "mask";
    case "acknowledge": return "acknowledge";
    case "boundary": return "confirm_boundary";
    case "region_geometry": return "confirm_suggested_geometry";
    case "ocr": return "reanalyze";
    default: return null;
  }
}

async function resolvePendingReviews(drive, state) {
  let current = state;
  for (let round = 0; round < 4; round += 1) {
    const pending = Array.isArray(current.pendingReviews) ? current.pendingReviews : [];
    if (pending.length === 0) return current;
    for (const review of pending) {
      if (!review?.reviewId) throw new AcceptanceFailure("REVIEW_ID_UNAVAILABLE");
      const action = reviewAction(review.kind);
      if (!action) throw new AcceptanceFailure("REVIEW_ACTION_UNAVAILABLE");
      current = stateOf(await drive.send(`resolve-review ${review.reviewId} ${action}`));
    }
  }
  if ((current.pendingReviews ?? []).length > 0) throw new AcceptanceFailure("UNRESOLVED_REVIEWS");
  return current;
}

async function resolveOneReviewAsExclude(drive, state) {
  const pending = Array.isArray(state.pendingReviews) ? state.pendingReviews : [];
  const review = pending.find((item) => item?.kind === "name" || item?.kind === "institution");
  if (!review) {
    return {
      state,
      status: "PASS",
      detail: "not applicable: no name/institution review row was available for exclusion",
    };
  }
  const beforeExcluded = Number(state.excludedOccurrenceCount ?? 0);
  const targetId = review.targetId;
  const priorOccurrence = (state.occurrences ?? []).find((item) => item.occurrenceId === targetId);
  const resolved = stateOf(await drive.send(`resolve-review ${review.reviewId} exclude`));
  const excluded = Number(resolved.excludedOccurrenceCount ?? 0);
  if (excluded <= beforeExcluded) {
    throw new AcceptanceFailure("REVIEW_EXCLUDE_NOT_COMMITTED");
  }
  const remaining = (resolved.pendingReviews ?? []).some((item) => item?.reviewId === review.reviewId);
  if (remaining) throw new AcceptanceFailure("REVIEW_EXCLUDE_REMAINED_PENDING");
  const excludedOccurrence = (resolved.occurrences ?? []).find((item) =>
    item.proposedAction === "exclude"
      && item.state === "confirmed"
      && (!priorOccurrence
        || (item.page === priorOccurrence.page
          && item.category === priorOccurrence.category
          && JSON.stringify(item.rects) === JSON.stringify(priorOccurrence.rects))),
  );
  if (!excludedOccurrence || !Array.isArray(excludedOccurrence.rects) || excludedOccurrence.rects.length === 0) {
    throw new AcceptanceFailure("REVIEW_EXCLUDE_NOT_COMMITTED");
  }
  return {
    state: resolved,
    status: "PASS",
    detail: `review row ${review.kind} resolved as exclude; excludedOccurrenceCount=${excluded}`,
    targets: excludedOccurrence.rects.map((rect) => ({ page: excludedOccurrence.page, rect })),
  };
}

async function verifyPendingReviewOverlay(drive, state, document, scratch) {
  const pendingReviews = Array.isArray(state.pendingReviews) ? state.pendingReviews : [];
  const pendingOccurrences = Number(state.pendingOccurrenceCount ?? 0);
  const targets = pendingTargetEntries(state);
  const excluded = excludedTargetEntries(state);
  if (targets.length === 0 && excluded.length === 0) {
    return {
      status: pendingReviews.length === 0 && pendingOccurrences === 0 ? "PASS" : "PENDING",
      detail: pendingReviews.length === 0 && pendingOccurrences === 0
        ? "manifest pending=0; no review rectangle was exposed"
        : `pending=${pendingReviews.length}; no measurable review rectangle was exposed`,
    };
  }
  if (targets.length === 0 && (pendingReviews.length > 0 || pendingOccurrences > 0)) {
    return {
      status: "PENDING",
      detail: `pending=${pendingReviews.length}; no measurable review rectangle was exposed`,
    };
  }
  let yellowDashed = 0;
  for (const [index, target] of targets.entries()) {
    let targetState;
    try {
      targetState = await prepareVisibleTarget(drive, target.page, target.rect);
    } catch (error) {
      if (error instanceof AcceptanceFailure && error.code === "OFFSCREEN") {
        return { status: "PENDING", detail: "OFFSCREEN: pending review target remained outside the captured display" };
      }
      throw error;
    }
    const capturePath = join(
      scratch,
      `${document.alias}-${document.sha256.slice(0, 12)}-pending-review-${index}.png`,
    );
    const capture = await captureScreenshot(capturePath, targetState);
    const color = await screenshotTargetColor(capture, targetState, target.rect);
    if (!color || Number(color.pendingPixels ?? 0) < 1) {
      return { status: "FAIL", detail: "pending review target was not painted with a yellow dashed overlay" };
    }
    yellowDashed += 1;
  }
  for (const [index, target] of excluded.entries()) {
    const targetState = await prepareVisibleTarget(drive, target.page, target.rect);
    const capturePath = join(
      scratch,
      `${document.alias}-${document.sha256.slice(0, 12)}-excluded-review-${index}.png`,
    );
    const capture = await captureScreenshot(capturePath, targetState);
    const color = await screenshotTargetColor(capture, targetState, target.rect);
    if (Number(color?.pendingPixels ?? 0) > 0) {
      return { status: "FAIL", detail: "excluded occurrence was painted on the detection overlay" };
    }
  }
  return {
    status: "PASS",
    detail: `pending=${pendingReviews.length}; yellow-dashed=${yellowDashed}; excluded=${excluded.length}`,
  };
}

async function captureOverlayPair(drive, document, scratch, label, page, rect, targetIndex) {
  const preparedState = await prepareVisibleTarget(drive, page, rect);
  const beforePath = join(scratch, `${document.alias}-${document.sha256.slice(0, 12)}-${label}-before-${targetIndex}.png`);
  const afterPath = join(scratch, `${document.alias}-${document.sha256.slice(0, 12)}-${label}-after-${targetIndex}.png`);
  let beforeCapture;
  let afterCapture;
  try {
    const hiddenState = stateOf(await drive.send("set-overlay hidden"));
    const stableHiddenState = targetIndex === 0
      ? await stableMeasurementFrame(drive, rect, hiddenState)
      : hiddenState;
    beforeCapture = await captureScreenshot(beforePath, hiddenState);
    await drive.send("set-overlay visible");
    const afterState = stateOf(await drive.send(
      `inspect-target ${rect.x0} ${rect.y0} ${rect.x1} ${rect.y1}`,
    ));
    if (targetIndex === 0) await stableMeasurementFrame(drive, rect, afterState);
    afterCapture = await captureScreenshot(afterPath, afterState);
    const frameMismatch = captureFrameMismatchFields(stableHiddenState, afterState);
    if (frameMismatch.length > 0) {
      throw new AcceptanceFailure("SCREEN_LAYOUT_CHANGED", true, frameMismatch.join(","));
    }
    const diff = await screenshotDiff(beforeCapture, afterCapture, afterState, [rect]);
    const target = diff.targets?.[0];
    if (!target || Number(target.visibleArea ?? 0) <= 0) throw new AcceptanceFailure("OFFSCREEN", true);
    return { beforeCapture, afterCapture, beforeState: hiddenState, afterState, diff, preparedState };
  } finally {
    try {
      await drive.send("set-overlay visible");
    } catch {
      // The enclosing document run will report the drive failure.
    }
  }
}

function createChecks(defaultStatus = "PENDING", defaultDetail = "not run") {
  return Object.fromEntries(CHECK_NAMES.map((name) => [
    name,
    name === "finalPdf"
      ? {
          status: defaultStatus,
          detail: defaultDetail,
          subchecks: {
            auto: { status: defaultStatus, detail: defaultDetail },
            manual: { status: defaultStatus, detail: defaultDetail },
            restore: { status: defaultStatus, detail: defaultDetail },
            keyword: { status: defaultStatus, detail: defaultDetail },
          },
        }
      : { status: defaultStatus, detail: defaultDetail },
  ]));
}

function checkResult(checks, name, status, detail) {
  checks[name] = { status, detail };
}

function finalPdfSubcheck(checks, name, status, detail) {
  const check = checks.finalPdf;
  check.subchecks[name] = { status, detail };
  const statuses = Object.values(check.subchecks).map((subcheck) => subcheck.status);
  check.status = statuses.includes("FAIL")
    ? "FAIL"
    : statuses.includes("PENDING")
      ? "PENDING"
      : "PASS";
  check.detail = Object.entries(check.subchecks)
    .map(([key, subcheck]) => `${key}=${subcheck.status}`)
    .join("; ");
}

function documentResult(document, checks, evidence, measurement = {}) {
  const statuses = Object.values(checks).map((check) => check.status);
  const status = statuses.includes("FAIL")
    ? "FAIL"
    : statuses.includes("PENDING")
      ? "PENDING"
      : "PASS";
  return {
    document,
    checks,
    evidence,
    status,
    renderOutcome: measurement.renderOutcome ?? "NOT_RUN",
    attempts: Array.isArray(measurement.attempts) ? measurement.attempts : [],
  };
}

function emptyEvidence() {
  return { directory: "", paths: [] };
}

function finalPdfSkipDetail(name, check) {
  return `${name} 단계가 ${check.status}여서 저장 PDF 비교를 건너뛰었습니다`;
}

async function verifyPdfTargets(path, targets) {
  if (targets.length === 0) return { allBlack: false, empty: true, targets: [] };
  return verifyFinalPdf(path, targets);
}

function saveOutcomeSummary(saveResult) {
  if (!saveResult) return "status=missing";
  const steps = ["chooser", "prepare", "trustedFinalize", "fileStat"]
    .map((name) => `${name}=${saveResult.steps?.[name]?.status ?? "missing"}`)
    .join(",");
  const diagnostics = Array.isArray(saveResult.failureDiagnostics)
    ? saveResult.failureDiagnostics.map((diagnostic) => [
      diagnostic.reasonCode,
      diagnostic.category ?? "unknown",
      diagnostic.page === undefined ? "p?" : `p${diagnostic.page}`,
      diagnostic.occurrenceId ? `occ=${diagnostic.occurrenceId.slice(0, 12)}` : "occ=?",
      diagnostic.rectFingerprint ? `rect=${diagnostic.rectFingerprint.slice(0, 12)}` : "rect=?",
      diagnostic.expectedTextHash ? `text=${diagnostic.expectedTextHash.slice(0, 12)}` : "text=?",
      diagnostic.observedTextHash ? `observed=${diagnostic.observedTextHash.slice(0, 12)}` : "observed=?",
    ].join(":")).join("|")
    : "";
  return `status=${saveResult.status}; stage=${saveResult.stage}; error=${publicSaveErrorCode(saveResult.errorCode) ?? "none"}; field=${saveResult.errorField ?? "none"}; diagnostics=${diagnostics || "none"}; ${steps}`;
}

function publicSaveErrorCode(code) {
  return typeof code === "string" && code.startsWith("MASKING_PIPELINE_")
    ? code.slice("MASKING_PIPELINE_".length)
    : code;
}

function saveDisclosureRequired(state) {
  return state?.saveGateState === "review"
    || (Array.isArray(state?.pendingReviews) && state.pendingReviews.length > 0)
    || Number(state?.pendingOccurrenceCount ?? 0) > 0;
}

async function verifyPartialSaveDisclosure(drive, document, scratch, savedState, ocrScriptPath) {
  const settledState = stateOf(await drive.send("wait-idle"));
  const success = settledState?.finalizationSuccess;
  if (
    !success
    || success.visible !== true
    || success.statusTone !== "warn"
    || !Array.isArray(success.warnings)
    || success.warnings.length === 0
  ) {
    throw new AcceptanceFailure("PARTIAL_SAVE_CONFIRMATION_NOT_VISIBLE");
  }
  const capturePath = join(
    scratch,
    `${document.alias}-${document.sha256.slice(0, 12)}-partial-save-confirmation.png`,
  );
  const capture = await captureScreenshot(capturePath, settledState ?? savedState);
  const ocr = await ocrScreenshot(capture.path, ocrScriptPath);
  const text = ocr.text.replace(/\s+/g, " ").trim();
  if (
    !/(미\s*가림|가려지지|부분\s*마스킹|확인\s*저장)/.test(text)
    || !/\d+\s*쪽/.test(text)
  ) {
    throw new AcceptanceFailure("PARTIAL_SAVE_CONFIRMATION_NOT_VISIBLE");
  }
  return `external screencapture OCR saw ${success.warnings.length} category/page disclosure warning(s)`;
}

async function saveAndVerify(
  drive,
  outputPath,
  finalState,
  groups,
  { dialog = true, textTargets = [], restoreTargets = [], untouchedTargets = [], excludedTargets = [], osInputScriptPath = null } = {},
) {
  const targetGroups = finalManifestTargetGroups(finalState);
  const allSaveTargets = {
    auto: groups.auto ? targetGroups.auto : [],
    manual: groups.manual ? [...targetGroups.auto, ...targetGroups.manual] : [],
    keyword: groups.keyword ? [...targetGroups.auto, ...targetGroups.keyword] : [],
  };
  const saveTargets = Object.fromEntries(Object.entries(allSaveTargets).filter(([name]) => groups[name]));
  const requestedGroupNames = [
    ...Object.keys(saveTargets),
    ...(restoreTargets.length > 0 ? ["restore"] : []),
    ...(excludedTargets.length > 0 ? ["reviewExclude"] : []),
  ];
  let savedTranscript;
  try {
    if (dialog) {
      const saveDialog = await drive.send("open-save-dialog");
      if (!osInputScriptPath) throw new AcceptanceFailure("OS_INPUT_UNAVAILABLE", true);
      await osClickFinalSave(osInputScriptPath, stateOf(saveDialog));
      savedTranscript = await drive.send("wait-save");
    } else {
      savedTranscript = await drive.send("save-final");
    }
  } catch (error) {
    const code = errorDetail(error, "QA_DRIVE_SAVE_FINAL_FAILED");
    return {
      status: error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL",
      detail: `save-final command failed (${code})`,
      groups: Object.fromEntries(requestedGroupNames.map((name) => [name, {
        status: error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL",
        detail: `save-final command failed (${code})`,
      }])),
    };
  }
  const savedState = stateOf(savedTranscript);
  const saveResult = savedState.saveFinal;
  const finalizationSuccess = savedState.finalizationSuccess;
  const committed = finalizationSuccess?.visible === true
    && saveResult?.steps?.trustedFinalize?.status === "ok";
  const externalStat = await stat(outputPath).catch(() => null);
  const fileStatOk = Boolean(externalStat?.isFile() && externalStat.size > 0)
    && saveResult?.steps?.fileStat?.status === "ok";
  if (!saveResult || saveResult.status !== "ok" || !fileStatOk) {
    const code = publicSaveErrorCode(saveResult?.errorCode)
      ?? publicSaveErrorCode(saveResult?.steps?.fileStat?.code)
      ?? (externalStat?.isFile() ? "QA_DRIVE_FINAL_OUTPUT_STAT_FAILED" : "FINAL_OUTPUT_MISSING");
    const pending = INFRASTRUCTURE_CODES.has(code) || saveResult?.status === "cancelled";
    const detail = `save-final ${pending ? "measurement pending" : "failed"} (${code}; ${saveOutcomeSummary(saveResult)})`;
    return {
      status: pending ? "PENDING" : "FAIL",
      detail,
      groups: Object.fromEntries(requestedGroupNames.map((name) => [name, {
        status: pending ? "PENDING" : "FAIL",
        detail,
      }])),
      saveResult,
      savedState,
      finalizationSuccess,
      committed,
    };
  }
  const groupResults = {};
  for (const [name, targets] of Object.entries(saveTargets)) {
    try {
      const verification = await verifyPdfTargets(outputPath, targets);
      groupResults[name] = verification.allBlack
        ? { status: "PASS", detail: `${name} final manifest targets rendered black (${targets.length})` }
        : verification.empty
          ? { status: "FAIL", detail: `${name} final manifest exposed no target rectangle for PDF verification` }
          : { status: "FAIL", detail: `${name} final manifest contains an unmasked target region` };
    } catch (error) {
      const code = safeErrorCode(error, "PDF_RENDER_VERIFICATION_FAILED");
      groupResults[name] = {
        status: INFRASTRUCTURE_CODES.has(code) ? "PENDING" : "FAIL",
        detail: `final manifest PDF verification ${code}`,
      };
    }
  }
  if (textTargets.length > 0) {
    try {
      const verification = await verifyFinalPdf(outputPath, textTargets);
      const textLengths = verification.targets?.map((target) => Number(target.textLength ?? 0)) ?? [];
      const masked = verification.targets?.length === textTargets.length
        && verification.targets.every((target) => target.black === true && Number(target.textLength ?? 0) === 0);
      groupResults.manual = masked
        ? { status: "PASS", detail: `manual final PDF black and text-span=0 (${textTargets.length})` }
        : { status: "FAIL", detail: `manual final PDF text-span remained (${textLengths.join(",")})` };
    } catch (error) {
      const code = safeErrorCode(error, "PDF_RENDER_VERIFICATION_FAILED");
      groupResults.manual = {
        status: INFRASTRUCTURE_CODES.has(code) ? "PENDING" : "FAIL",
        detail: `manual text-span verification ${code}`,
      };
    }
  }
  if (restoreTargets.length > 0) {
    try {
      const [restored, untouched] = await Promise.all([
        verifyFinalPdf(outputPath, restoreTargets),
        verifyFinalPdf(outputPath, untouchedTargets),
      ]);
      const restoredValid = restored.targets?.length === restoreTargets.length
        && restored.targets.every((target) =>
          target.valid === true
            && target.black === false
            && Number(target.textLength ?? 0) > 0
            // The app and verifier use different text extractors, so their
            // hashes are not comparable. Text re-exposure is proven by
            // textLength above.
        );
      const untouchedValid = untouched.targets?.length === untouchedTargets.length
        && untouched.targets.every((target) => target.valid === true && target.black === true);
      groupResults.restore = restoredValid && untouchedValid
        ? {
            status: "PASS",
            detail: `restore final PDF text-span=${restored.targets.map((target) => target.textLength).join(",")}; untouched confirmed mask black=${untouchedTargets.length}`,
          }
        : {
            status: "FAIL",
            detail: `restore final PDF re-exposure/untouched-mask check failed (restored=${restored.targets?.map((target) => `${target.black ? "black" : "visible"}:${target.textLength}`).join(",") ?? "none"}; untouched=${untouched.targets?.map((target) => target.black).join(",") ?? "none"})`,
          };
    } catch (error) {
      const code = safeErrorCode(error, "PDF_RENDER_VERIFICATION_FAILED");
      groupResults.restore = {
        status: INFRASTRUCTURE_CODES.has(code) ? "PENDING" : "FAIL",
        detail: `restore/untouched text-span verification ${code}`,
      };
    }
  }
  if (excludedTargets.length > 0) {
    try {
      const verification = await verifyFinalPdf(outputPath, excludedTargets);
      const remainedVisible = verification.targets?.length === excludedTargets.length
        && verification.targets.every((target) => target.valid === true && target.black === false);
      groupResults.reviewExclude = remainedVisible
        ? { status: "PASS", detail: `excluded review target remained visible in final PDF (${excludedTargets.length})` }
        : { status: "FAIL", detail: "excluded review target was unexpectedly rendered as a black mask" };
    } catch (error) {
      const code = safeErrorCode(error, "PDF_RENDER_VERIFICATION_FAILED");
      groupResults.reviewExclude = {
        status: INFRASTRUCTURE_CODES.has(code) ? "PENDING" : "FAIL",
        detail: `excluded review target PDF verification ${code}`,
      };
    }
  }
  const statuses = Object.values(groupResults).map((result) => result.status);
  return {
    status: statuses.includes("FAIL") ? "FAIL" : statuses.includes("PENDING") ? "PENDING" : "PASS",
    detail: `file-stat=PASS; ${saveOutcomeSummary(saveResult)}; pdf=${statuses.join("/")}`,
    groups: groupResults,
    saveResult,
    savedState,
    finalizationSuccess,
    committed,
  };
}

async function runDocument(document, options, executable, ocrScriptPath, osInputScriptPath) {
  const checks = createChecks();
  const evidence = { directory: options.evidenceDir, paths: [] };
  const openAttempts = [];
  let openRetryUsed = false;
  let scratch = null;
  let child = null;
  let drive = null;
  let outputPath = null;
  try {
    scratch = await mkdtemp(join(tmpdir(), `nothing-accept-${document.alias}-${document.sha256.slice(0, 12)}-`));
    const inputPath = join(scratch, "input.pdf");
    outputPath = join(scratch, "final-output.pdf");
    await copyFile(document.path, inputPath);

    let textTargetCandidates = [];
    try {
      textTargetCandidates = await extractTextTargetCandidates(inputPath);
      if (!Array.isArray(textTargetCandidates) || textTargetCandidates.length === 0) {
        throw new AcceptanceFailure("TEXT_LAYER_UNAVAILABLE", true);
      }
    } catch (error) {
      textTargetCandidates = [];
    }

    let keywordCandidates = [];
    try {
      keywordCandidates = await extractKeywordCandidates(inputPath);
      if (!Array.isArray(keywordCandidates)) throw new AcceptanceFailure("KEYWORD_CANDIDATE_INVALID");
      if (keywordCandidates.length === 0) {
        checkResult(checks, "keyword", "PENDING", "no unmasked text token with a screen rectangle was available");
      }
    } catch (error) {
      checkResult(
        checks,
        "keyword",
        error instanceof AcceptanceFailure && !error.pending ? "FAIL" : "PENDING",
        errorDetail(error, "KEYWORD_CANDIDATE_UNAVAILABLE"),
      );
    }

    ({ child, drive } = await launchDrive(executable, options.appPath, scratch, outputPath, { verifyWindow: false }));
    const openDocument = async (path) => {
      for (;;) {
        const attempt = openAttempts.length + 1;
        const startedAt = Date.now();
        try {
          const result = await drive.send(`open ${path}`);
          openAttempts.push({
            attempt,
            command: "open",
            code: "OK",
            stage: "open",
            elapsedMs: Math.max(0, Date.now() - startedAt),
            outcome: openRetryUsed ? "PASS_AFTER_RETRY" : "PASS",
          });
          return result;
        } catch (error) {
          const code = safeErrorCode(error, "QA_DRIVE_OPEN_FAILED");
          openAttempts.push({
            attempt,
            command: "open",
            code,
            stage: openAttemptStage(error),
            elapsedMs: Math.max(0, Date.now() - startedAt),
            outcome: "FAIL",
          });
          if (code !== "QA_DRIVE_RENDER_UNAVAILABLE" || openRetryUsed) throw error;
          openRetryUsed = true;
          await terminateChild(child, drive);
          child = null;
          drive = null;
          try {
            ({ child, drive } = await launchDrive(
              executable,
              options.appPath,
              scratch,
              outputPath,
              { verifyWindow: false },
            ));
          } catch (restartError) {
            openAttempts.push({
              attempt: openAttempts.length + 1,
              command: "open",
              code: safeErrorCode(restartError, "QA_DRIVE_FRONTEND_UNAVAILABLE"),
              stage: "restart",
              elapsedMs: 0,
              outcome: "FAIL",
            });
            throw restartError;
          }
        }
      }
    };
    const analyseIndependentSession = async () => {
      const reopened = stateOf(await openDocument(inputPath));
      if (
        reopened.analysisRevision !== null
        || (reopened.boxes?.length ?? 0) !== 0
        || (reopened.manualActions?.length ?? 0) !== 0
      ) {
        throw new AcceptanceFailure("QA_DRIVE_SESSION_NOT_RESET");
      }
      await drive.send(`set-profile ${document.category}`);
      const analysed = stateOf(await drive.send("run-masking"));
      const settled = stateOf(await drive.send("wait-idle"));
      if (!settled.renderedPdf || !finite(settled.analysisRevision)) {
        throw new AcceptanceFailure("QA_DRIVE_RENDER_NOT_READY", true);
      }
      return settled ?? analysed;
    };

    const opened = stateOf(await openDocument(inputPath));
    await drive.send(`set-profile ${document.category}`);
    const openedPageCount = Number(opened.pageCount ?? 0);
    const pageCount = openedPageCount;
    if (!Number.isSafeInteger(pageCount) || pageCount < 1 || pageCount > MAX_PAGE_COUNT) {
      throw new AcceptanceFailure("PAGE_COUNT_UNAVAILABLE", true);
    }

    let analysedState = await analyseIndependentSession();
    let excludedReviewTargets = [];
    const allOccurrences = occurrenceRects(analysedState);
    const automaticOccurrences = autoOccurrences(analysedState);
    const automaticPages = new Set(automaticOccurrences.map((entry) => entry.page));
    const occurrencePages = new Set(allOccurrences.map((entry) => entry.page));

    if (automaticOccurrences.length === 0) {
      const manifestWasObservable = Array.isArray(analysedState.occurrences)
        && finite(analysedState.analysisRevision)
        && Number(analysedState.maskCounts?.automaticMaskCount ?? 0) === 0;
      checkResult(
        checks,
        "auto",
        manifestWasObservable ? "PASS" : "FAIL",
        manifestWasObservable
          ? "final analysis manifest contained zero confirmed automatic targets; no OCR zero-message shortcut was used"
          : "analysis manifest did not expose a trustworthy automatic-target count",
      );
    } else {
      const missed = [];
      const pending = [];
      for (const [index, entry] of automaticOccurrences.entries()) {
        try {
          const pair = await captureOverlayPair(
            drive,
            document,
            scratch,
            "auto",
            entry.page,
            entry.rect,
            index,
          );
          const target = pair.diff.targets?.[0];
          if (!target || !target.overlap || target.newDarkPixels < 1) missed.push(entry);
        } catch (error) {
          if (error instanceof AcceptanceFailure && error.pending) pending.push(error.code);
          else missed.push(entry);
        }
      }
      const detail = missed.length > 0
        ? `confirmed automatic target screenshot mismatch (${missed.length}/${automaticOccurrences.length})`
        : pending.length > 0
          ? `PENDING ${pending.join(",")}`
          : `confirmed automatic targets darkened in matched external frames (${automaticOccurrences.length})`;
      checkResult(checks, "auto", missed.length > 0 ? "FAIL" : pending.length > 0 ? "PENDING" : "PASS", detail);
    }

    try {
      const pendingResult = await verifyPendingReviewOverlay(drive, analysedState, document, scratch);
      checkResult(checks, "pendingReview", pendingResult.status, pendingResult.detail);
    } catch (error) {
      checkResult(
        checks,
        "pendingReview",
        error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL",
          errorDetail(error, "PENDING_REVIEW_ASSERTION_FAILED"),
      );
    }

    try {
      const disclosureRequired = saveDisclosureRequired(analysedState);
      const pendingSave = await saveAndVerify(
        drive,
        outputPath,
        analysedState,
        { auto: false, manual: false, keyword: false },
        { dialog: true, osInputScriptPath },
      );
      if (pendingSave.status !== "PASS") {
        checkResult(
          checks,
          "pendingReview",
          pendingSave.status,
          `${checks.pendingReview.detail}; save pipeline ${pendingSave.detail}`,
        );
        if (pendingSave.committed) {
          let cleanupError = null;
          try {
            await drive.send("close-success-dialog");
          } catch (error) {
            cleanupError = error;
          }
          try {
            // File-stat or PDF verification can fail after trusted finalize
            // has already cleared the session. Do not reuse stale state.
            analysedState = await analyseIndependentSession();
          } catch (error) {
            cleanupError ??= error;
          }
          if (cleanupError !== null) {
            checkResult(
              checks,
              "pendingReview",
              cleanupError instanceof AcceptanceFailure && cleanupError.pending ? "PENDING" : "FAIL",
              `${checks.pendingReview.detail}; post-save session recovery ${errorDetail(cleanupError, "POST_SAVE_SESSION_RECOVERY_FAILED")}`,
            );
          }
        }
      } else {
        const confirmation = pendingSave.saveResult?.saveConfirmation;
        const success = pendingSave.finalizationSuccess;
        const unresolved = confirmation?.unresolvedReviews;
        let saveValidationError = null;
        let saveValidationDetail = "";
        try {
          if (disclosureRequired) {
            if (
              confirmation?.status !== "user_confirmed"
              || !Array.isArray(unresolved)
              || unresolved.length === 0
              || success?.warnings.length !== unresolved.length
              || !success.warnings.every((warning) => /쪽/.test(warning))
            ) {
              throw new AcceptanceFailure("PARTIAL_SAVE_CONFIRMATION_NOT_VISIBLE");
            }
            saveValidationDetail = await verifyPartialSaveDisclosure(
              drive,
              document,
              scratch,
              pendingSave.savedState,
              ocrScriptPath,
            );
          } else if (
            confirmation?.status !== "not_required"
            || !Array.isArray(unresolved)
            || unresolved.length !== 0
            || success?.visible !== true
            || success.statusTone !== "ok"
            || success.warnings.length !== 0
          ) {
            throw new AcceptanceFailure("SAVE_CONFIRMATION_UNEXPECTED_WARNING");
          }
        } catch (error) {
          saveValidationError = error;
        }
        let saveCleanupError = null;
        try {
          await drive.send("close-success-dialog");
        } catch (error) {
          saveCleanupError = error;
        }
        try {
          // A successful trusted save clears the active session. Always
          // re-open an independent session before later checks, even when a
          // disclosure assertion failed.
          analysedState = await analyseIndependentSession();
        } catch (error) {
          saveCleanupError ??= error;
        }
        if (saveValidationError !== null) throw saveValidationError;
        if (saveCleanupError !== null) throw saveCleanupError;
        checkResult(
          checks,
          "pendingReview",
          checks.pendingReview.status === "FAIL" ? "FAIL" : checks.pendingReview.status === "PENDING" ? "PENDING" : "PASS",
          disclosureRequired
            ? `${checks.pendingReview.detail}; unresolved confirmation saved with category/page warnings; ${saveValidationDetail}`
            : `${checks.pendingReview.detail}; no unresolved review required a partial-save disclosure`,
        );
      }
    } catch (error) {
      checkResult(
        checks,
        "pendingReview",
        error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL",
        `${checks.pendingReview.detail}; ${errorDetail(error, "PARTIAL_SAVE_CONFIRMATION_FAILED")}`,
      );
    }

    try {
      const exclusion = await resolveOneReviewAsExclude(drive, analysedState);
      analysedState = exclusion.state;
      excludedReviewTargets = exclusion.targets ?? [];
      checkResult(checks, "reviewExclude", exclusion.status, exclusion.detail);
    } catch (error) {
      checkResult(
        checks,
        "reviewExclude",
        error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL",
        errorDetail(error, "REVIEW_EXCLUDE_ASSERTION_FAILED"),
      );
    }

    const emptyPage = [...Array(pageCount).keys()].find((page) => !automaticPages.has(page)
      && [...occurrencePages].some((otherPage) => otherPage !== page));
    if (emptyPage === undefined) {
      checkResult(checks, "otherPage", "PASS", "not applicable: no page without a detection while another page has one");
    } else {
      const guidanceState = stateOf(await drive.send(`go-page ${emptyPage + 1}`));
      const guidanceCapture = join(scratch, `${document.alias}-${document.sha256.slice(0, 12)}-page-guidance-p${emptyPage + 1}.png`);
      await captureScreenshot(guidanceCapture, guidanceState);
      try {
        const ocr = await ocrScreenshot(guidanceCapture, ocrScriptPath);
        checkResult(checks, "otherPage", hasPageGuidanceText(ocr.text) ? "PASS" : "FAIL", hasPageGuidanceText(ocr.text) ? "external OCR saw page-location guidance" : "screen did not show page-location guidance");
      } catch (error) {
        checkResult(checks, "otherPage", error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL", errorDetail(error, "SCREEN_TEXT_RECOGNITION_UNAVAILABLE"));
      }
    }

    let autoFinalState = analysedState;
    try {
      autoFinalState = await resolvePendingReviews(drive, analysedState);
      if ((autoFinalState.pendingReviews ?? []).length > 0) throw new AcceptanceFailure("UNRESOLVED_REVIEWS");
      const autoSave = await saveAndVerify(
        drive,
        outputPath,
        autoFinalState,
        { auto: true, manual: false, keyword: false },
        { dialog: true, osInputScriptPath, excludedTargets: excludedReviewTargets },
      );
      const autoResult = autoSave.groups?.auto;
      finalPdfSubcheck(checks, "auto", autoResult?.status ?? autoSave.status, autoResult?.detail ?? autoSave.detail);
      if (excludedReviewTargets.length > 0) {
        const excludedResult = autoSave.groups?.reviewExclude;
        checkResult(
          checks,
          "reviewExclude",
          excludedResult?.status ?? autoSave.status,
          excludedResult?.detail ?? autoSave.detail,
        );
      }
      if (autoSave.finalizationSuccess?.visible === true) await drive.send("close-success-dialog");
    } catch (error) {
      const status = error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL";
      finalPdfSubcheck(checks, "auto", status, errorDetail(error, "AUTO_FINAL_SAVE_FAILED"));
      if (excludedReviewTargets.length > 0) {
        checkResult(checks, "reviewExclude", status, errorDetail(error, "REVIEW_EXCLUDE_FINAL_PDF_FAILED"));
      }
    }

    let manualFinalState = null;
    let manualTextTarget = null;
    try {
      const manualState = await analyseIndependentSession();
      if (textTargetCandidates.length === 0) {
        throw new AcceptanceFailure("TEXT_LAYER_UNAVAILABLE", true);
      }
      manualTextTarget = findManualTextTarget(manualState, textTargetCandidates);
      const manualTarget = {
        page: manualTextTarget.page,
        rect: manualTextTarget.rect,
        textLength: manualTextTarget.textLength,
        textHash: manualTextTarget.textHash,
      };
      await drive.send("set-tool mask");
      const beforeState = await prepareVisibleTarget(drive, manualTarget.page, manualTarget.rect);
      await stableMeasurementFrame(drive, manualTarget.rect, beforeState);
      const manualBefore = join(scratch, `${document.alias}-${document.sha256.slice(0, 12)}-manual-before.png`);
      const beforeCapture = await captureScreenshot(manualBefore, beforeState);
      await osDragCanvas(osInputScriptPath, beforeState, manualTarget.rect);
      const draggedState = stateOf(await drive.send("dump-state"));
      const stagedCapture = await captureScreenshot(
        join(scratch, `${document.alias}-${document.sha256.slice(0, 12)}-manual-staged.png`),
        draggedState,
      );
      const stagedColor = await screenshotStagedColor(stagedCapture, draggedState, manualTarget.rect);
      if (
        !Array.isArray(draggedState.boxes)
        || draggedState.boxes.length === 0
        || Number(stagedColor?.bluePixels ?? 0) < 1
      ) {
        throw new AcceptanceFailure(
          "MANUAL_STAGED_OVERLAY_NOT_VISIBLE",
          false,
          `boxes=${draggedState.boxes?.length ?? 0};blue=${stagedColor?.bluePixels ?? 0}`,
        );
      }
      const appliedState = stateOf(await drive.send("apply-manual"));
      const afterState = stateOf(await drive.send(
        `inspect-target ${manualTarget.rect.x0} ${manualTarget.rect.y0} ${manualTarget.rect.x1} ${manualTarget.rect.y1}`,
      ));
      await stableMeasurementFrame(drive, manualTarget.rect, afterState);
      const manualAfter = join(scratch, `${document.alias}-${document.sha256.slice(0, 12)}-manual-after-apply.png`);
      const afterCapture = await captureScreenshot(manualAfter, afterState);
      const frameMismatch = captureFrameMismatchFields(beforeState, afterState);
      if (frameMismatch.length > 0) {
        throw new AcceptanceFailure("SCREEN_LAYOUT_CHANGED", true, frameMismatch.join(","));
      }
      const diff = await screenshotDiff(beforeCapture, afterCapture, afterState, [manualTarget.rect]);
      const target = diff.targets?.[0];
      const changed = Number(target?.changedPixels ?? 0);
      const appliedManualMask = (appliedState.manualActions ?? []).some((action) => action.mode === "mask");
      const manualCount = Number(appliedState.maskCounts?.manualMaskCount ?? 0);
      if (
        manualTarget.textLength <= 0
        || changed <= 20
        || !appliedManualMask
        || manualCount < 1
      ) {
        throw new AcceptanceFailure("MANUAL_SCREEN_ASSERTION_FAILED");
      }
      checkResult(
        checks,
        "manual",
        "PASS",
        `real text span selected (length=${manualTarget.textLength}, hash=${manualTarget.textHash.slice(0, 12)}); staged overlay visible; committed manual mask count=${manualCount}`,
      );
      manualFinalState = appliedState;
    } catch (error) {
      checkResult(
        checks,
        "manual",
        error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL",
          errorDetail(error, "MANUAL_SCREEN_ASSERTION_FAILED"),
      );
    }

    if (checks.manual.status === "PASS" && manualFinalState) {
      try {
        manualFinalState = await resolvePendingReviews(drive, manualFinalState);
        if ((manualFinalState.pendingReviews ?? []).length > 0) throw new AcceptanceFailure("UNRESOLVED_REVIEWS");
        const manualSave = await saveAndVerify(
          drive,
          outputPath,
          manualFinalState,
          { auto: true, manual: true, keyword: false },
          {
            dialog: true,
            osInputScriptPath,
            textTargets: manualTextTarget ? [{ page: manualTextTarget.page, rect: manualTextTarget.textRect }] : [],
          },
        );
        for (const name of ["auto", "manual"]) {
          const result = manualSave.groups?.[name];
          if (name === "manual" || checks.finalPdf.subchecks.auto.status !== "PASS") {
            finalPdfSubcheck(checks, name, result?.status ?? manualSave.status, result?.detail ?? manualSave.detail);
          }
        }
        if (manualSave.status === "PASS" && manualSave.saveResult?.manualMaskCount !== 1) {
          finalPdfSubcheck(
            checks,
            "manual",
            "FAIL",
            `save summary manualMaskCount=${manualSave.saveResult?.manualMaskCount ?? "missing"}; expected=1`,
          );
        }
        if (manualSave.finalizationSuccess?.visible === true) await drive.send("close-success-dialog");
      } catch (error) {
        finalPdfSubcheck(
          checks,
          "manual",
          error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL",
          errorDetail(error, "MANUAL_FINAL_SAVE_FAILED"),
        );
      }
    } else {
      finalPdfSubcheck(checks, "manual", "PENDING", finalPdfSkipDetail("manual", checks.manual));
    }

    try {
      const restoreState = await analyseIndependentSession();
      const scenario = restoreScenarioTargets(restoreState, textTargetCandidates);
      if (!scenario) {
        if (confirmedAutomaticOccurrenceEntries(restoreState).length === 0) {
          checkResult(
            checks,
            "restore",
            "PASS",
            "not applicable: no confirmed automatic text-backed target was available",
          );
          finalPdfSubcheck(
            checks,
            "restore",
            "PASS",
            "not applicable: no confirmed automatic text-backed target was available",
          );
        } else {
          throw new AcceptanceFailure("RESTORE_TARGET_UNAVAILABLE", true);
        }
      }
      if (scenario) {
      await drive.send("set-tool restore");
      const restorePrepared = await prepareVisibleTarget(
        drive,
        scenario.restored.page,
        scenario.dragRect,
      );
      await osDragCanvas(osInputScriptPath, restorePrepared, scenario.dragRect);
      const draggedState = stateOf(await drive.send("dump-state"));
      const stagedCapture = await captureScreenshot(
        join(scratch, `${document.alias}-${document.sha256.slice(0, 12)}-restore-staged.png`),
        draggedState,
      );
      const stagedColor = await screenshotStagedColor(stagedCapture, draggedState, scenario.dragRect);
      if (
        !Array.isArray(draggedState.boxes)
        || draggedState.boxes.length !== 1
        || Number(stagedColor?.orangePixels ?? 0) < 1
      ) {
        throw new AcceptanceFailure(
          "RESTORE_STAGED_OVERLAY_NOT_VISIBLE",
          false,
          `boxes=${draggedState.boxes?.length ?? 0};orange=${stagedColor?.orangePixels ?? 0}`,
        );
      }
      let appliedState = stateOf(await drive.send("apply-manual"));
      const restoreAction = (appliedState.manualActions ?? []).find((action) => action.mode === "restore");
      const linkedOccurrence = restoreAction
        ? (appliedState.occurrences ?? []).find((occurrence) =>
          occurrence.occurrenceId === restoreAction.linkedOccurrenceId)
        : null;
      if (
        !restoreAction
        || restoreAction.sourceKind !== "text_pdf"
        || !linkedOccurrence
        || linkedOccurrence.page !== scenario.restored.page
        || linkedOccurrence.category !== scenario.restored.category
        || linkedOccurrence.proposedAction !== "mask"
        || !["confirmed", "user_confirmed"].includes(linkedOccurrence.state)
        || linkedOccurrence.expectedTextHash !== scenario.restored.expectedTextHash
        || !sameRectLists(linkedOccurrence.rects, scenario.restored.rects)
        || !sameRectLists(restoreAction.rects, linkedOccurrence.rects)
        || typeof restoreAction.restoreAuthorizationHash !== "string"
        || Number(appliedState.maskCounts?.manualRestoreCount ?? 0) !== 1
      ) {
        throw new AcceptanceFailure("RESTORE_AUTHORIZED_ACTION_NOT_COMMITTED");
      }
      checkResult(
        checks,
        "restore",
        "PASS",
        "native-trusted-ui restore action committed with occurrence, geometry, and authorization evidence",
      );
      appliedState = await resolvePendingReviews(drive, appliedState);
      if ((appliedState.pendingReviews ?? []).length > 0) throw new AcceptanceFailure("UNRESOLVED_REVIEWS");
      const restoreSave = await saveAndVerify(
        drive,
        outputPath,
        appliedState,
        { auto: true, manual: false, keyword: false },
        {
          dialog: true,
          osInputScriptPath,
          restoreTargets: scenario.restoredPdfTargets,
          untouchedTargets: scenario.untouchedPdfTargets,
        },
      );
      if (restoreSave.finalizationSuccess?.visible === true) await drive.send("close-success-dialog");
      const restoreResult = restoreSave.groups?.restore;
      if (restoreSave.status !== "PASS") {
        if (restoreResult) {
          finalPdfSubcheck(checks, "restore", restoreResult.status, restoreResult.detail);
        }
        const failedRestoreState = restoreSave.savedState ?? appliedState;
        const failedRestoreAction = (failedRestoreState?.manualActions ?? [])
          .find((action) => action.mode === "restore");
        throw new AcceptanceFailure(
          "RESTORE_FINAL_SAVE_FAILED",
          restoreSave.status === "PENDING",
          `${restoreSave.detail ?? "restore save did not complete"}; revision=${failedRestoreState?.analysisRevision ?? "unknown"}; actionRevision=${failedRestoreAction?.analysisRevision ?? "missing"}; pending=${failedRestoreState?.pendingReviews?.length ?? "unknown"}`,
        );
      }
      if (
        restoreSave.saveResult?.restoreCount !== 1
        || !restoreSave.saveResult?.restoreAuthorization
        || restoreSave.saveResult.restoreAuthorization.authorizationEvent !== "native_trusted_ui"
      ) {
        throw new AcceptanceFailure("RESTORE_SAFE_REPORT_AUDIT_MISSING");
      }
      finalPdfSubcheck(
        checks,
        "restore",
        restoreResult?.status ?? restoreSave.status,
        restoreResult?.detail
          ? `${restoreResult.detail}; safe-report restoreCount=1; event=${restoreSave.saveResult.restoreAuthorization.authorizationEvent}`
          : restoreSave.detail,
      );
      if (restoreResult?.status !== "PASS") {
        throw new AcceptanceFailure("RESTORE_FINAL_PDF_ASSERTION_FAILED");
      }
      if (checks.finalPdf.subchecks.auto.status !== "PASS" && restoreSave.groups?.auto) {
        finalPdfSubcheck(
          checks,
          "auto",
          restoreSave.groups.auto.status,
          restoreSave.groups.auto.detail,
        );
      }
      }
    } catch (error) {
      if (checks.restore.status === "PENDING" && checks.restore.detail === "not run") {
        checkResult(
          checks,
          "restore",
          error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL",
          errorDetail(error, "RESTORE_AUTHORIZED_ACTION_FAILED"),
        );
      }
      if (checks.finalPdf.subchecks.restore?.status !== "FAIL") {
        finalPdfSubcheck(
          checks,
          "restore",
          error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL",
          errorDetail(error, "RESTORE_FINAL_PDF_ASSERTION_FAILED"),
        );
      }
    }

    let keywordFinalState = null;
    if (checks.keyword.status !== "PENDING" || keywordCandidates.length > 0) {
      try {
        const keywordState = await analyseIndependentSession();
        const keywordOccupied = occurrenceRects(keywordState);
        const candidate = findKeywordTarget(keywordCandidates, keywordState, keywordOccupied, null);
        if (!candidate) throw new AcceptanceFailure("KEYWORD_TARGET_UNAVAILABLE", true);
        const keywordBeforeState = await prepareVisibleTarget(drive, candidate.page, candidate.rect);
        await stableMeasurementFrame(drive, candidate.rect, keywordBeforeState);
        const keywordBeforePath = join(scratch, `${document.alias}-${document.sha256.slice(0, 12)}-keyword-before.png`);
        const keywordBefore = await captureScreenshot(keywordBeforePath, keywordBeforeState);
        await drive.send(`apply-keyword ${candidate.token}`);
        const keywordAfterState = stateOf(await drive.send("wait-idle"));
        const finalKeywordEntry = keywordOccurrences(keywordAfterState)
          .find((entry) => entry.page === candidate.page && rectsOverlap(entry.rect, candidate.rect));
        if (!finalKeywordEntry) throw new AcceptanceFailure("KEYWORD_FINAL_MANIFEST_TARGET_UNAVAILABLE");
        const keywordAfterVisibleState = stateOf(await drive.send(
          `inspect-target ${finalKeywordEntry.rect.x0} ${finalKeywordEntry.rect.y0} ${finalKeywordEntry.rect.x1} ${finalKeywordEntry.rect.y1}`,
        ));
        await stableMeasurementFrame(drive, finalKeywordEntry.rect, keywordAfterVisibleState);
        const keywordAfterPath = join(scratch, `${document.alias}-${document.sha256.slice(0, 12)}-keyword-after.png`);
        const keywordAfter = await captureScreenshot(keywordAfterPath, keywordAfterVisibleState);
        const frameMismatch = captureFrameMismatchFields(keywordBeforeState, keywordAfterVisibleState);
        if (frameMismatch.length > 0) {
          throw new AcceptanceFailure("SCREEN_LAYOUT_CHANGED", true, frameMismatch.join(","));
        }
        const diff = await screenshotDiff(keywordBefore, keywordAfter, keywordAfterVisibleState, [finalKeywordEntry.rect]);
        const target = diff.targets?.[0];
        const changed = Number(target?.changedPixels ?? 0);
        if (changed <= 20) {
          throw new AcceptanceFailure("KEYWORD_SCREEN_ASSERTION_FAILED");
        }
        checkResult(checks, "keyword", "PASS", "independent reopened session changed the final-manifest custom-keyword region");
        keywordFinalState = keywordAfterState;
      } catch (error) {
        if (checks.keyword.status === "PENDING" && error instanceof AcceptanceFailure && error.code === "KEYWORD_TARGET_UNAVAILABLE") {
          // Keep the candidate-unavailable prerequisite status.
        } else {
          checkResult(
            checks,
            "keyword",
            error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL",
              errorDetail(error, "KEYWORD_SCREEN_ASSERTION_FAILED"),
          );
        }
      }
    }

    if (checks.keyword.status === "PASS" && keywordFinalState) {
      try {
        keywordFinalState = await resolvePendingReviews(drive, keywordFinalState);
        if ((keywordFinalState.pendingReviews ?? []).length > 0) throw new AcceptanceFailure("UNRESOLVED_REVIEWS");
        const keywordSave = await saveAndVerify(
          drive,
          outputPath,
          keywordFinalState,
          { auto: true, manual: false, keyword: true },
          { dialog: true, osInputScriptPath },
        );
        if (keywordSave.finalizationSuccess?.visible === true) await drive.send("close-success-dialog");
        for (const name of ["auto", "keyword"]) {
          const result = keywordSave.groups?.[name];
          if (name === "keyword" || checks.finalPdf.subchecks.auto.status !== "PASS") {
            finalPdfSubcheck(checks, name, result?.status ?? keywordSave.status, result?.detail ?? keywordSave.detail);
          }
        }
      } catch (error) {
        finalPdfSubcheck(
          checks,
          "keyword",
          error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL",
          errorDetail(error, "KEYWORD_FINAL_SAVE_FAILED"),
        );
      }
    } else {
      finalPdfSubcheck(checks, "keyword", "PENDING", finalPdfSkipDetail("keyword", checks.keyword));
    }
  } catch (error) {
    const status = error instanceof AcceptanceFailure && error.pending ? "PENDING" : "FAIL";
    const detail = errorDetail(error, status === "PENDING" ? "ACCEPTANCE_PREREQUISITE_UNAVAILABLE" : "ACCEPTANCE_ASSERTION_FAILED");
    for (const name of CHECK_NAMES) {
      if (name === "finalPdf") {
        if (checks[name].detail !== "not run") continue;
        for (const subcheck of ["auto", "manual", "restore", "keyword"]) {
          finalPdfSubcheck(checks, subcheck, status, detail);
        }
      } else if (checks[name].status === "PENDING" && checks[name].detail === "not run") {
        checkResult(checks, name, status, detail);
      }
    }
  } finally {
    await terminateChild(child, drive);
    if (scratch) await rm(scratch, { recursive: true, force: true });
    await rm(options.evidenceDir, { recursive: true, force: true });
  }
  return documentResult(document, checks, evidence, {
    renderOutcome: openAttemptOutcome(openAttempts),
    attempts: openAttempts,
  });
}

function globalPendingResult(document, code) {
  return documentResult(document, createChecks("PENDING", code), emptyEvidence(), {
    renderOutcome: "NOT_RUN",
    attempts: [],
  });
}

function globalFailureResult(document, code) {
  return documentResult(document, createChecks("FAIL", code), emptyEvidence(), {
    renderOutcome: "NOT_RUN",
    attempts: [],
  });
}

function formatCheck(check) {
  return `${check.status} — ${check.detail}`.replaceAll("|", "/");
}

function formatRenderAttempts(row) {
  const attempts = Array.isArray(row.attempts) ? row.attempts : [];
  if (attempts.length === 0) return "—";
  return attempts.map((attempt) => [
    `#${attempt.attempt}`,
    attempt.outcome,
    attempt.code,
    attempt.stage,
    `${attempt.elapsedMs}ms`,
  ].join("/")).join("<br>");
}

function formatFinalPdfCheck(check) {
  const subchecks = ["auto", "manual", "restore", "keyword"]
    .map((name) => `${name} ${formatCheck(check.subchecks[name])}`)
    .join("; ");
  return `${check.status} — ${subchecks}`.replaceAll("|", "/");
}

function renderReport(status, rows, notes = [], preconditions = null, structured = null) {
  const lines = [
    "# T63 실앱 저장·측정 안정화 수용 시험",
    "",
    `Status: **${status}**`,
    "",
    "완료 근거는 대상 rect를 화면 안으로 이동한 뒤 같은 레이아웃·스크롤 상태에서 얻은 외부 `screencapture`의 일회성 픽셀 측정과 최종 매니페스트 기반 PDF 렌더·텍스트 추출 검증이다. 원문이 포함될 수 있는 캡처는 문서별 비공개 scratch에서 측정 후 삭제하며 evidence에는 복사하지 않는다. 앱 상태 덤프는 가시 교집합·오버레이 색상·페이지 이동·검토 조작 제어 보조로 사용했고, 목 QA·CLI 매니페스트 수치는 완료 근거로 사용하지 않았다.",
    "",
    "| alias | sha256 | open render attempts | 자동 확정 마스킹 화면 | 검토 대기 표시 | 검토 행 제외 | 다른 페이지 안내 | 수동 실제 텍스트 | 복원/잔존 마스크 PDF | 키워드 픽셀 변화 | 저장 PDF 가림 (자동/수동/복원/키워드) | 문서 판정 | 증거 |",
    "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
  ];
  for (const row of rows) {
    const checks = row.checks;
    const evidence = row.evidence?.paths?.length > 0 ? `${row.document.alias}-${row.document.sha256.slice(0, 12)}` : "—";
    lines.push(`| ${row.document.alias} | ${row.document.sha256} | ${row.renderOutcome ?? "NOT_RUN"}: ${formatRenderAttempts(row)} | ${formatCheck(checks.auto)} | ${formatCheck(checks.pendingReview)} | ${formatCheck(checks.reviewExclude)} | ${formatCheck(checks.otherPage)} | ${formatCheck(checks.manual)} | ${formatCheck(checks.restore)} | ${formatCheck(checks.keyword)} | ${formatFinalPdfCheck(checks.finalPdf)} | **${row.status}** | ${evidence} |`);
  }
  if (preconditions) {
    lines.push("", "## Preconditions", "", "```json", JSON.stringify(preconditions, null, 2), "```");
  }
  if (notes.length > 0) {
    lines.push("", "## Notes", "", ...notes.map((note) => `- ${note}`));
  }
  if (structured) {
    lines.push(
      "",
      "## Structured results",
      "",
      "```json",
      JSON.stringify(structured, null, 2),
      "```",
    );
  }
  lines.push("", "QA 상태·매니페스트 수치만으로는 완료를 선언하지 않는다. 외부 픽셀·최종 PDF 검증이 PASS가 아니면 T63 완료가 아니다.", "");
  return lines.join("\n");
}

function aggregateStatus(rows) {
  if (rows.some((row) => row.status === "FAIL")) return "FAIL";
  if (rows.some((row) => row.status === "PENDING")) return "PENDING";
  return "PASS";
}

function structuredAcceptanceResult(status, rows, preconditions) {
  return {
    schemaVersion: 1,
    status,
    preconditions,
    documents: rows.map((row) => ({
      alias: row.document.alias,
      sha256: row.document.sha256,
      status: row.status,
      renderOutcome: row.renderOutcome ?? "NOT_RUN",
      attempts: Array.isArray(row.attempts) ? row.attempts : [],
      checks: Object.fromEntries(CHECK_NAMES.map((name) => [
        name,
        name === "finalPdf"
          ? {
              status: row.checks[name].status,
              subchecks: Object.fromEntries(
                Object.entries(row.checks[name].subchecks ?? {}).map(([key, value]) => [
                  key,
                  value.status,
                ]),
              ),
            }
          : row.checks[name].status,
      ])),
    })),
  };
}

async function measurementFlipNote(rows) {
  const priorPath = join(repoRoot, ".omo", "evidence", "T52-baseline.md");
  let prior;
  try {
    prior = await readFile(priorPath, "utf8");
  } catch {
    return "교정 전 표가 없어 측정 오류로 뒤집힌 항목은 0건으로 기록했습니다.";
  }
  const priorLines = prior.split("\n");
  const headerIndex = priorLines.findIndex((line) => /^\|\s*alias\s*\|/.test(line));
  if (headerIndex < 0) return "교정 전 표를 읽을 수 없어 측정 오류로 뒤집힌 항목은 0건으로 기록했습니다.";
  const headers = priorLines[headerIndex].split("|").slice(1, -1).map((value) => value.trim());
  const columns = Object.fromEntries(headers.map((header, index) => [
    acceptanceColumn(header),
    index,
  ]).filter(([name]) => name !== null));
  const aliasIndex = columns.alias;
  if (aliasIndex === undefined) return "교정 전 표를 읽을 수 없어 측정 오류로 뒤집힌 항목은 0건으로 기록했습니다.";
  const priorStatus = new Map();
  for (const line of priorLines.slice(headerIndex + 2)) {
    if (!line.startsWith("|")) continue;
    const cells = line.split("|").slice(1, -1).map((value) => value.trim());
    const alias = cells[aliasIndex];
    if (cells.length !== headers.length || !/^doc-\d+$/.test(alias ?? "")) continue;
    const rowStatus = {};
    for (const [kind, index] of Object.entries(columns)) {
      const status = cells[index]?.match(/^(?:\*\*)?(PASS|FAIL|PENDING)\b/)?.[1]
        ?? cells[index]?.match(/\*\*(PASS|FAIL|PENDING)\*\*/)?.[1];
      if (!status) continue;
      if (kind !== "alias" && kind !== "sha256" && kind !== "evidence") rowStatus[kind] = status;
    }
    priorStatus.set(alias, rowStatus);
  }
  const comparableChecks = ["auto", "reviewExclude", "otherPage", "manual", "restore", "keyword", "finalPdf", "document"];
  let flipped = 0;
  let reclassified = 0;
  const flippedDocuments = new Set();
  const reclassifiedDocuments = new Set();
  for (const row of rows) {
    const previous = priorStatus.get(row.document.alias);
    if (!previous) continue;
    const current = {
      auto: row.checks.auto.status,
      reviewExclude: row.checks.reviewExclude.status,
      otherPage: row.checks.otherPage.status,
      manual: row.checks.manual.status,
      restore: row.checks.restore.status,
      keyword: row.checks.keyword.status,
      finalPdf: row.checks.finalPdf.status,
      document: row.status,
    };
    for (const check of comparableChecks) {
      if (previous[check] !== "FAIL") continue;
      if (current[check] === "PASS") {
        flipped += 1;
        flippedDocuments.add(row.document.alias);
      } else if (current[check] === "PENDING") {
        reclassified += 1;
        reclassifiedDocuments.add(row.document.alias);
      }
    }
  }
  return `기존 표와 비교한 측정 오류 반전 ${flipped}건 (${flippedDocuments.size}개 문서, FAIL→PASS), 측정 보류로 재분류 ${reclassified}건 (${reclassifiedDocuments.size}개 문서, FAIL→PENDING)`;
}

async function writeReport(reportPath, content) {
  await mkdir(dirname(reportPath), { recursive: true });
  await writeFile(reportPath, content, "utf8");
}

function preconditionRecord(error, status = "blocked") {
  const code = error instanceof RealAppPreconditionFailure
    ? error.code
    : safeErrorCode(error, "E2E_DISPLAY_UNAVAILABLE");
  return {
    status,
    code,
    reason: error instanceof RealAppPreconditionFailure ? error.reason : "probe-failed",
    signals: error instanceof RealAppPreconditionFailure ? error.signals : {},
  };
}

function preconditionDetail(error) {
  const record = preconditionRecord(error);
  return `${record.code} (reason=${record.reason})`;
}

async function main() {
  const options = parseArguments(process.argv);
  if (options.help) {
    console.log(printUsage());
    return 0;
  }
  await rm(options.evidenceDir, { recursive: true, force: true });
  if (process.platform !== "darwin") {
    const preconditions = {
      status: "blocked",
      code: "ACCEPT_PLATFORM_UNSUPPORTED",
      reason: "platform=unsupported",
      signals: {},
    };
    await writeReport(
      options.reportPath,
      renderReport("PENDING", [], ["ACCEPT_PLATFORM_UNSUPPORTED"], preconditions),
    );
    console.error("ACCEPT_PLATFORM_UNSUPPORTED");
    return 2;
  }

  let preconditions;
  try {
    preconditions = await checkHostDisplayPreconditions();
  } catch (error) {
    const record = preconditionRecord(error);
    await writeReport(
      options.reportPath,
      renderReport("PENDING", [], [preconditionDetail(error)], record),
    );
    console.error(error instanceof Error ? error.message : record.code);
    return 2;
  }

  if (!existsSync(options.appPath)) {
    const error = new AcceptanceFailure("ACCEPT_APP_MISSING", true);
    const record = preconditionRecord(error);
    await writeReport(
      options.reportPath,
      renderReport("PENDING", [], ["ACCEPT_APP_MISSING"], {
        ...record,
        signals: preconditions,
      }),
    );
    console.error("ACCEPT_APP_MISSING");
    return 2;
  }

  let executable;
  try {
    executable = await packagedExecutable(options.appPath);
    preconditions = await preflightPackagedApp(executable, options.appPath, preconditions);
  } catch (error) {
    const record = preconditionRecord(error);
    await writeReport(
      options.reportPath,
      renderReport("PENDING", [], [preconditionDetail(error)], {
        ...record,
        signals: { ...preconditions, ...(record.signals ?? {}) },
      }),
    );
    console.error(error instanceof Error ? error.message : record.code);
    return 2;
  }

  let manifest;
  try {
    manifest = loadRealCorpusManifest();
  } catch (error) {
    const code = safeErrorCode(error, "REAL_CORPUS_INCOMPLETE");
    const report = renderReport("FAIL", [], [code], preconditions);
    await writeReport(options.reportPath, report);
    console.error(code);
    return 1;
  }

  let documents;
  try {
    documents = await resolveRealCorpus({ alias: options.alias ?? undefined });
  } catch (error) {
    const code = safeErrorCode(error, "REAL_CORPUS_INCOMPLETE");
    const selected = options.alias ? manifest.filter((entry) => entry.alias === options.alias) : manifest;
    const rows = selected.map((document) => globalFailureResult(document, code));
    const report = renderReport("FAIL", rows, [code], preconditions);
    await writeReport(options.reportPath, report);
    console.error(code);
    return 1;
  }

  await mkdir(options.evidenceDir, { recursive: true });
  const rows = [];
  let ocrRoot = null;
  try {
    ocrRoot = await mkdtemp(join(tmpdir(), "nothing-accept-ocr-"));
    const ocrScriptPath = join(ocrRoot, "recognize.swift");
    const osInputScriptPath = join(ocrRoot, "os-input.swift");
    await writeFile(ocrScriptPath, OCR_SWIFT, "utf8");
    await writeFile(osInputScriptPath, OS_INPUT_SWIFT, "utf8");
    for (const document of documents) {
      const evidenceDocumentDir = join(options.evidenceDir, `${document.alias}-${document.sha256.slice(0, 12)}`);
      await rm(evidenceDocumentDir, { recursive: true, force: true });
      await mkdir(evidenceDocumentDir, { recursive: true });
      rows.push(await runDocument(
        document,
        { ...options, evidenceDir: evidenceDocumentDir },
        executable,
        ocrScriptPath,
        osInputScriptPath,
      ));
    }
  } catch (error) {
    const code = safeErrorCode(error, "ACCEPT_APP_MISSING");
    rows.push(...documents.map((document) => globalPendingResult(document, code)));
  } finally {
    if (ocrRoot) await rm(ocrRoot, { recursive: true, force: true });
  }

  const status = aggregateStatus(rows);
  const structured = structuredAcceptanceResult(status, rows, preconditions);
  const report = renderReport(status, rows, [await measurementFlipNote(rows)], preconditions, structured);
  await writeReport(options.reportPath, report);
  console.log(report);
  return status === "PASS" ? 0 : status === "PENDING" ? 2 : 1;
}

main().then((code) => {
  process.exitCode = code;
}).catch(async (error) => {
  const code = safeErrorCode(error);
  try {
    const reportPath = resolve(process.env.T53R_REPORT_PATH ?? defaultReportPath);
    await writeReport(reportPath, renderReport("FAIL", [], [code]));
  } catch {
    // The process exit code remains the authoritative failure signal.
  }
  console.error(code);
  process.exitCode = 1;
});
