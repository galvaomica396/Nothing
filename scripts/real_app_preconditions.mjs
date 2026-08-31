import { execFile as execFileCallback } from "node:child_process";
import { mkdtemp, rm, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";
import { QA_DRIVE_TIMEOUTS_MS } from "./qa_drive_timeout_config.mjs";

const execFile = promisify(execFileCallback);

export const DISPLAY_REMEDIATION = "노트북 덮개를 열거나 외부 디스플레이를 활성화한 뒤 다시 실행하세요.";
export const CLAMSHELL_PROBE_COMMAND = "ioreg -r -k AppleClamshellState";

export const ACTIVE_DISPLAY_INFO_SWIFT = String.raw`
import CoreGraphics
import Foundation

var displayCount: UInt32 = 0
var displayIds = [CGDirectDisplayID](repeating: 0, count: 32)
guard CGGetActiveDisplayList(UInt32(displayIds.count), &displayIds, &displayCount) == .success else {
    exit(1)
}
let displays = displayIds.prefix(Int(displayCount)).map { displayId -> [String: Any] in
    let bounds = CGDisplayBounds(displayId)
    return [
        "id": Int(displayId),
        "x": bounds.origin.x,
        "y": bounds.origin.y,
        "width": bounds.size.width,
        "height": bounds.size.height,
        "online": CGDisplayIsOnline(displayId) != 0,
        "awake": CGDisplayIsAsleep(displayId) == 0,
    ]
}
let data = try JSONSerialization.data(withJSONObject: displays)
print(String(data: data, encoding: .utf8) ?? "[]")
`;

const QUARTZ_SESSION_CHECK = String.raw`
import Quartz, json
current = Quartz.CGSessionCopyCurrentDictionary()
if current is None:
    raise RuntimeError("CGSessionCopyCurrentDictionary returned no dictionary")
current = dict(current)
print(json.dumps({key: str(value) for key, value in current.items()}))
`;

export const SWIFT_SESSION_CHECK = String.raw`
import CoreGraphics
import Foundation

guard let current = CGSessionCopyCurrentDictionary() as? [String: Any] else {
    exit(1)
}
func value(_ keys: [String]) -> String {
    for key in keys {
        guard let raw = current[key] else { continue }
        if let boolValue = raw as? Bool { return boolValue ? "true" : "false" }
        return String(describing: raw)
    }
    return "missing"
}
print("\(value(["CGSSessionScreenIsLocked"])) \(value(["CGSSessionOnConsoleKey", "kCGSSessionOnConsoleKey"]))")
`;

const CG_WINDOW_INFO = String.raw`
import Quartz, json, sys

pid = int(sys.argv[1])
windows = Quartz.CGWindowListCopyWindowInfo(
    Quartz.kCGWindowListOptionOnScreenOnly,
    Quartz.kCGNullWindowID,
) or []
result = []
for window in windows:
    try:
        owner_pid = int(window.get("kCGWindowOwnerPID"))
    except (TypeError, ValueError):
        continue
    if owner_pid != pid:
        continue
    try:
        layer = int(window.get("kCGWindowLayer", 1))
    except (TypeError, ValueError):
        layer = 1
    if layer != 0:
        continue
    raw_bounds = window.get("kCGWindowBounds") or {}
    try:
        bounds = {
            "x": float(raw_bounds["X"]),
            "y": float(raw_bounds["Y"]),
            "width": float(raw_bounds["Width"]),
            "height": float(raw_bounds["Height"]),
        }
    except (KeyError, TypeError, ValueError):
        bounds = None
    try:
        window_id = int(window.get("kCGWindowNumber"))
    except (TypeError, ValueError):
        window_id = None
    try:
        alpha = float(window.get("kCGWindowAlpha", 1))
    except (TypeError, ValueError):
        alpha = 0
    result.append({
        "windowId": window_id,
        "layer": layer,
        "onScreen": True,
        "alpha": alpha,
        "bounds": bounds,
    })
print(json.dumps(result))
`;

export const CG_WINDOW_INFO_SWIFT = String.raw`
import CoreGraphics
import Foundation

guard CommandLine.arguments.count > 1, let pid = Int32(CommandLine.arguments[1]) else {
    exit(2)
}

func number(_ value: Any?) -> NSNumber? {
    if let value = value as? NSNumber {
        return value
    }
    if let value = value as? Int {
        return NSNumber(value: value)
    }
    if let value = value as? Double {
        return NSNumber(value: value)
    }
    return nil
}

let windows = CGWindowListCopyWindowInfo(
    [.optionOnScreenOnly],
    kCGNullWindowID
) as? [[String: Any]] ?? []
var result = [[String: Any]]()

for window in windows {
    guard number(window[kCGWindowOwnerPID as String])?.intValue == Int(pid) else {
        continue
    }

    let layer: Int
    if window[kCGWindowLayer as String] == nil {
        layer = 1
    } else {
        layer = number(window[kCGWindowLayer as String])?.intValue ?? 1
    }
    guard layer == 0 else {
        continue
    }

    let onScreen: Bool
    if let rawOnScreen = window[kCGWindowIsOnscreen as String] {
        onScreen = number(rawOnScreen)?.boolValue ?? false
    } else {
        onScreen = true
    }
    guard onScreen else {
        continue
    }

    let bounds: Any
    if let rawBounds = window[kCGWindowBounds as String] as? [String: Any],
       let x = number(rawBounds["X"])?.doubleValue,
       let y = number(rawBounds["Y"])?.doubleValue,
       let width = number(rawBounds["Width"])?.doubleValue,
       let height = number(rawBounds["Height"])?.doubleValue {
        bounds = [
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        ]
    } else {
        bounds = NSNull()
    }

    let windowId: Any = number(window[kCGWindowNumber as String])?.intValue ?? NSNull()
    let alpha: Double
    if window[kCGWindowAlpha as String] == nil {
        alpha = 1
    } else {
        alpha = number(window[kCGWindowAlpha as String])?.doubleValue ?? 0
    }
    result.append([
        "windowId": windowId,
        "layer": layer,
        "onScreen": onScreen,
        "alpha": alpha,
        "bounds": bounds,
    ])
}

let data = try JSONSerialization.data(withJSONObject: result)
print(String(data: data, encoding: .utf8) ?? "[]")
`;

const AX_WINDOW_COUNT = String.raw`
tell application "System Events" to count windows of process "tauri_frontend"
`;

const FRAME_INFO = String.raw`
from PIL import Image
import json
import sys

before = Image.open(sys.argv[1]).convert("RGB")
after = Image.open(sys.argv[2]).convert("RGB")
if before.size != after.size:
    raise SystemExit("SCREENSHOT_SIZE_CHANGED")
changed = 0
for prior, current in zip(before.getdata(), after.getdata()):
    if max(abs(prior[index] - current[index]) for index in range(3)) >= 16:
        changed += 1
print(json.dumps({
    "size": {"width": before.width, "height": before.height},
    "changedPixels": changed,
}))
`;

function parseBoolean(value) {
  if (value === undefined || value === null) return null;
  if (["1", "true", "True", "yes", "Yes", "on", "On"].includes(String(value))) return true;
  if (["0", "false", "False", "no", "No", "off", "Off"].includes(String(value))) return false;
  return null;
}

const SESSION_LOCKED_KEY = "CGSSessionScreenIsLocked";
const SESSION_ON_CONSOLE_KEYS = ["CGSSessionOnConsoleKey", "kCGSSessionOnConsoleKey"];

export function parseSessionState(session, source) {
  if (!session || typeof session !== "object" || Array.isArray(session)) {
    return { source, locked: null, onConsole: null };
  }
  const hasLockedKey = Object.prototype.hasOwnProperty.call(session, SESSION_LOCKED_KEY);
  const onConsoleKey = SESSION_ON_CONSOLE_KEYS.find((key) => (
    session[key] !== undefined && session[key] !== null
  ));
  return {
    source,
    // macOS omits CGSSessionScreenIsLocked while the screen is unlocked; after a
    // successful session-dictionary lookup, that missing key means false.
    locked: hasLockedKey ? parseBoolean(session[SESSION_LOCKED_KEY]) : false,
    onConsole: onConsoleKey ? parseBoolean(session[onConsoleKey]) : null,
  };
}

function externalMessage(error) {
  if (!error || typeof error !== "object") return String(error ?? "");
  return [error.stdout, error.stderr, error.message].filter(Boolean).join("\n");
}

function errorCode(error, fallback = "E2E_DISPLAY_UNAVAILABLE") {
  const output = [
    error?.stdout,
    error?.stderr,
    error?.cause?.stdout,
    error?.cause?.stderr,
  ].filter(Boolean).join("\n");
  if (error?.cause && !output) return fallback;
  const message = output || externalMessage(error);
  return message.match(/[A-Z][A-Z0-9_]{2,}/)?.[0] ?? fallback;
}

async function runExternal(file, args, timeout = QA_DRIVE_TIMEOUTS_MS.precondition) {
  try {
    return await execFile(file, args, { timeout, maxBuffer: 4 * 1024 * 1024 });
  } catch (error) {
    const wrapped = new Error(`${file}: ${externalMessage(error) || "command failed"}`);
    wrapped.cause = error;
    throw wrapped;
  }
}

export class RealAppPreconditionFailure extends Error {
  constructor(code, reason, signals = {}) {
    super(`${code}: ${DISPLAY_REMEDIATION} (reason=${reason})`);
    this.name = "RealAppPreconditionFailure";
    this.code = code;
    this.reason = reason;
    this.signals = signals;
    this.pending = true;
  }
}

function displayFailure(reason, signals = {}) {
  return new RealAppPreconditionFailure("E2E_DISPLAY_UNAVAILABLE", reason, signals);
}

async function readSessionState() {
  let output = "";
  try {
    output = (await runExternal("python3", ["-c", QUARTZ_SESSION_CHECK], 15_000)).stdout.trim();
    return parseSessionState(JSON.parse(output), "python-quartz");
  } catch {
    try {
      output = (await runExternal("swift", ["-e", SWIFT_SESSION_CHECK], 30_000)).stdout.trim();
      const [locked, onConsole] = output.split(/\s+/, 2);
      if (!locked || !onConsole) throw new Error("invalid session probe output");
      const session = {};
      if (locked !== "missing") session[SESSION_LOCKED_KEY] = locked;
      if (onConsole !== "missing") session.CGSSessionOnConsoleKey = onConsole;
      return parseSessionState(session, "swift-coregraphics");
    } catch {
      return { source: "unavailable", locked: null, onConsole: null };
    }
  }
}

async function readClamshellState() {
  try {
    const [command, ...args] = CLAMSHELL_PROBE_COMMAND.split(/\s+/);
    const { stdout } = await runExternal(
      command,
      args,
      10_000,
    );
    const match = stdout.match(/AppleClamshellState["']?\s*=\s*(Yes|No|true|false|1|0)\b/i);
    return match ? parseBoolean(match[1]) : null;
  } catch {
    return null;
  }
}

function parseDisplayList(stdout) {
  const serialized = stdout.trim();
  if (!serialized) throw new Error("display probe returned empty output");
  const parsed = JSON.parse(serialized);
  if (!Array.isArray(parsed)) throw new Error("display probe returned non-array JSON");
  return parsed
    .filter((display) => (
      display
      && Number.isFinite(Number(display.id))
      && Number.isFinite(Number(display.x))
      && Number.isFinite(Number(display.y))
      && Number.isFinite(Number(display.width))
      && Number.isFinite(Number(display.height))
      && Number(display.width) > 0
      && Number(display.height) > 0
      && display.online === true
      && display.awake === true
    ))
    .map((display) => ({
      id: Number(display.id),
      x: Number(display.x),
      y: Number(display.y),
      width: Number(display.width),
      height: Number(display.height),
      online: true,
      awake: true,
    }));
}

export async function readActiveDisplayInfo() {
  let stdout;
  try {
    ({ stdout } = await runExternal("swift", ["-e", ACTIVE_DISPLAY_INFO_SWIFT], 30_000));
  } catch (error) {
    throw displayFailure("probe-error", { probe: errorCode(error, "SCREEN_DISPLAY_INFO_UNAVAILABLE") });
  }
  let displays;
  try {
    displays = parseDisplayList(stdout);
  } catch (error) {
    throw displayFailure("probe-error", { probe: errorCode(error, "SCREEN_DISPLAY_INFO_INVALID") });
  }
  if (displays.length === 0) {
    throw displayFailure("no-display", { probe: "SCREEN_DISPLAY_LIST_EMPTY" });
  }
  return displays;
}

async function captureScreen(path) {
  try {
    await runExternal("screencapture", ["-x", path], 30_000);
    const details = await stat(path);
    if (!details.isFile() || details.size <= 0) throw new Error("empty screenshot");
  } catch (error) {
    throw displayFailure("capture-unavailable", { probe: errorCode(error, "SCREEN_CAPTURE_UNAVAILABLE") });
  }
}

async function verifyImage(path) {
  try {
    const { stdout } = await runExternal("python3", ["-c", `${FRAME_INFO}\nprint(json.dumps({"valid": True}))`, path, path], 30_000);
    const lines = stdout.trim().split("\n").filter(Boolean);
    const result = JSON.parse(lines.at(-1) ?? "{}");
    if (result.valid !== true) throw new Error("invalid screenshot");
  } catch (error) {
    throw displayFailure("capture-unavailable", { probe: errorCode(error, "SCREEN_CAPTURE_UNAVAILABLE") });
  }
}

export async function checkHostDisplayPreconditions() {
  if (process.platform !== "darwin") {
    throw displayFailure("platform=unsupported");
  }
  const clamshell = await readClamshellState();
  if (clamshell === null) throw displayFailure("clamshell=unknown");
  const session = await readSessionState();
  if (session.locked === true || session.onConsole === false) {
    if (clamshell === true) {
      throw displayFailure("clamshell=closed;session=locked-or-off-console", { session, clamshell });
    }
    throw new RealAppPreconditionFailure("E2E_SESSION_LOCKED", "session=locked-or-off-console", { session });
  }
  if (session.locked === null || session.onConsole === null) {
    if (session.source === "unavailable") {
      throw displayFailure("probe-error", {
        probe: "SESSION_INFO_UNAVAILABLE",
        session,
        clamshell,
      });
    }
    throw displayFailure(`clamshell=${clamshell ? "closed" : "open"};session=unknown`, { session, clamshell });
  }
  const displays = await readActiveDisplayInfo();
  const probeRoot = await mkdtemp(join(tmpdir(), "nothing-display-preflight-"));
  const probePath = join(probeRoot, "screen.png");
  try {
    await captureScreen(probePath);
    await verifyImage(probePath);
  } finally {
    await rm(probeRoot, { recursive: true, force: true });
  }
  return {
    status: "pass",
    session,
    clamshell,
    clamshellState: clamshell ? "closed" : "open",
    displays,
    capture: { available: true },
  };
}

function intersects(left, right) {
  const x = Math.max(left.x, right.x);
  const y = Math.max(left.y, right.y);
  const rightEdge = Math.min(left.x + left.width, right.x + right.width);
  const bottomEdge = Math.min(left.y + left.height, right.y + right.height);
  return rightEdge > x && bottomEdge > y;
}

function parseWindowList(stdout) {
  const serialized = stdout.trim();
  if (!serialized) throw new Error("window probe returned empty output");
  const parsed = JSON.parse(serialized);
  if (!Array.isArray(parsed)) throw new Error("window probe returned non-array JSON");
  return parsed.filter((window) => (
    window
    && window.onScreen === true
    && Number(window.layer) === 0
    && Number.isSafeInteger(Number(window.windowId))
    && Number(window.windowId) > 0
    && Number(window.alpha) > 0
    && window.bounds
    && Number.isFinite(Number(window.bounds.x))
    && Number.isFinite(Number(window.bounds.y))
    && Number.isFinite(Number(window.bounds.width))
    && Number.isFinite(Number(window.bounds.height))
    && Number(window.bounds.width) > 0
    && Number(window.bounds.height) > 0
  )).map((window) => ({
    windowId: Number(window.windowId),
    layer: 0,
    onScreen: true,
    alpha: Number(window.alpha),
    bounds: {
      x: Number(window.bounds.x),
      y: Number(window.bounds.y),
      width: Number(window.bounds.width),
      height: Number(window.bounds.height),
    },
  }));
}

async function queryAppWindows(pid) {
  try {
    const { stdout } = await runExternal("python3", ["-c", CG_WINDOW_INFO, String(pid)], 15_000);
    return { known: true, windows: parseWindowList(stdout), source: "python-quartz" };
  } catch {
    try {
      const { stdout } = await runExternal("swift", ["-e", CG_WINDOW_INFO_SWIFT, String(pid)], 30_000);
      return { known: true, windows: parseWindowList(stdout), source: "swift-coregraphics" };
    } catch (swiftError) {
      const probe = errorCode(swiftError, "SCREEN_WINDOW_INFO_UNAVAILABLE");
      try {
        const { stdout } = await runExternal("osascript", ["-e", AX_WINDOW_COUNT], 15_000);
        const count = Number.parseInt(stdout.trim(), 10);
        return {
          known: false,
          windows: [],
          windowCount: Number.isFinite(count) ? count : null,
          source: "system-events",
          probeError: probe,
        };
      } catch {
        return {
          known: false,
          windows: [],
          windowCount: null,
          source: "unavailable",
          probeError: probe,
        };
      }
    }
  }
}

export async function inspectVisibleAppWindow(pid, displays, options = {}) {
  const attempts = Number.isSafeInteger(options.attempts) ? Math.max(1, options.attempts) : 6;
  const delayMs = Number.isFinite(options.delayMs) ? Math.max(0, options.delayMs) : 500;
  let lastProbe = null;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    lastProbe = await queryAppWindows(pid);
    if (lastProbe.known) {
      const window = lastProbe.windows
        .filter((candidate) => displays.some((display) => intersects(candidate.bounds, display)))
        .sort((left, right) => (
          right.bounds.width * right.bounds.height
          - left.bounds.width * left.bounds.height
        ))[0];
      if (window) {
        const display = displays.find((candidate) => intersects(window.bounds, candidate));
        return {
          ...window,
          display,
          source: lastProbe.source,
          verified: true,
        };
      }
    } else if (lastProbe.windowCount === 0) {
      throw new RealAppPreconditionFailure("E2E_WINDOW_NOT_VISIBLE", "window=not-visible", { window: lastProbe });
    }
    if (attempt + 1 < attempts && delayMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
  if (lastProbe?.known && lastProbe.windows.length > 0) {
    throw displayFailure("window-invisible", { window: lastProbe, displays });
  }
  if (lastProbe?.known) {
    throw new RealAppPreconditionFailure("E2E_WINDOW_NOT_VISIBLE", "window=not-visible", { window: lastProbe });
  }
  throw displayFailure("probe-error", {
    window: lastProbe,
    probe: lastProbe?.probeError ?? "SCREEN_WINDOW_INFO_UNAVAILABLE",
    displays,
  });
}

async function captureWindowFrame(windowId, path) {
  try {
    await runExternal("screencapture", ["-x", "-l", String(windowId), path], 30_000);
    const details = await stat(path);
    if (!details.isFile() || details.size <= 0) throw new Error("empty window screenshot");
  } catch (error) {
    throw displayFailure("capture-unavailable", { probe: errorCode(error, "SCREEN_CAPTURE_UNAVAILABLE") });
  }
}

async function compareFrames(before, after) {
  try {
    const { stdout } = await runExternal("python3", ["-c", FRAME_INFO, before, after], 30_000);
    const lines = stdout.trim().split("\n").filter(Boolean);
    const result = JSON.parse(lines.at(-1) ?? "{}");
    if (!Number.isSafeInteger(result.changedPixels) || result.changedPixels <= 0) {
      throw displayFailure("frame=no-pixel-change", { frame: result });
    }
    return result;
  } catch (error) {
    if (error instanceof RealAppPreconditionFailure) throw error;
    throw displayFailure("capture-unavailable", { probe: errorCode(error, "SCREEN_PIXEL_ANALYSIS_UNAVAILABLE") });
  }
}

export async function verifyVisibleAppRender({ drive, window, directory }) {
  const beforePath = join(directory, "qa-render-before.png");
  const afterPath = join(directory, "qa-render-after.png");
  let probeFailure = null;
  try {
    await drive.send("render-probe on");
    await captureWindowFrame(window.windowId, beforePath);
    await drive.send("render-probe off");
    await captureWindowFrame(window.windowId, afterPath);
    const frame = await compareFrames(beforePath, afterPath);
    return {
      verified: true,
      windowId: window.windowId,
      frame,
    };
  } catch (error) {
    probeFailure = error;
    if (error instanceof RealAppPreconditionFailure) throw error;
    throw displayFailure(`render=${errorCode(error, "QA_DRIVE_RENDER_UNAVAILABLE")}`, {
      window,
      probe: errorCode(error, "QA_DRIVE_RENDER_UNAVAILABLE"),
    });
  } finally {
    try {
      await drive.send("render-probe clear");
    } catch (error) {
      if (!probeFailure) {
        throw displayFailure(`render-cleanup=${errorCode(error, "QA_DRIVE_RENDER_UNAVAILABLE")}`, { window });
      }
    }
  }
}
