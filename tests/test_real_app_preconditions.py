import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_session_state_distinguishes_locked_missing_and_failed_dictionary_lookups() -> None:
    script = """
import { parseSessionState } from "./scripts/real_app_preconditions.mjs";

const states = {
  locked: parseSessionState({
    CGSSessionScreenIsLocked: "true",
    CGSSessionOnConsoleKey: "true",
  }, "fixture"),
  unlocked: parseSessionState({
    CGSSessionOnConsoleKey: "true",
  }, "fixture"),
  lookupFailed: parseSessionState(null, "fixture"),
};
process.stdout.write(JSON.stringify(states));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    states = json.loads(completed.stdout)
    assert states["locked"]["locked"] is True
    assert states["unlocked"]["locked"] is False
    assert states["lookupFailed"]["locked"] is None
    assert states["lookupFailed"]["onConsole"] is None


def test_active_display_swift_probe_compiles_and_returns_display_json() -> None:
    if sys.platform != "darwin":
        pytest.skip("the active display probe requires macOS CoreGraphics")

    script = """
import { execFile as execFileCallback } from "node:child_process";
import { promisify } from "node:util";
import { ACTIVE_DISPLAY_INFO_SWIFT } from "./scripts/real_app_preconditions.mjs";

const execFile = promisify(execFileCallback);
const { stdout } = await execFile("swift", ["-e", ACTIVE_DISPLAY_INFO_SWIFT], {
  timeout: 30_000,
  maxBuffer: 4 * 1024 * 1024,
});
const displays = JSON.parse(stdout.trim());
if (!Array.isArray(displays) || displays.length < 1) {
  throw new Error("the display probe must return at least one display");
}
if (displays.some((display) => (
  typeof display.online !== "boolean"
  || typeof display.awake !== "boolean"
))) {
  throw new Error("display status values must be JSON booleans");
}
process.stdout.write(JSON.stringify(displays));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    displays = json.loads(completed.stdout)
    assert len(displays) >= 1


def test_session_swift_probe_compiles_and_returns_session_values() -> None:
    if sys.platform != "darwin":
        pytest.skip("the session probe requires macOS CoreGraphics")

    script = """
import { execFile as execFileCallback } from "node:child_process";
import { promisify } from "node:util";
import { SWIFT_SESSION_CHECK } from "./scripts/real_app_preconditions.mjs";

const execFile = promisify(execFileCallback);
const { stdout } = await execFile("swift", ["-e", SWIFT_SESSION_CHECK], {
  timeout: 30_000,
  maxBuffer: 4 * 1024 * 1024,
});
const [locked, onConsole] = stdout.trim().split(/\\s+/, 2);
if (!["false", "true", "missing"].includes(locked) || !["false", "true", "missing"].includes(onConsole)) {
  throw new Error(`invalid session probe output: ${stdout}`);
}
process.stdout.write(JSON.stringify({ locked, onConsole }));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    session = json.loads(completed.stdout)
    assert session["onConsole"] == "true"


def test_session_fallback_uses_swift_when_python_quartz_is_unavailable() -> None:
    if sys.platform != "darwin":
        pytest.skip("the session probe requires macOS CoreGraphics")

    script = """
import { checkHostDisplayPreconditions } from "./scripts/real_app_preconditions.mjs";

const result = await checkHostDisplayPreconditions();
if (
  result.session.source !== "swift-coregraphics"
  || typeof result.session.locked !== "boolean"
  || typeof result.session.onConsole !== "boolean"
) {
  throw new Error(`invalid Swift session fallback: ${JSON.stringify(result.session)}`);
}
process.stdout.write(JSON.stringify(result.session));
"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        fake_python = Path(temporary_directory) / "python3"
        fake_python.write_text(
            '#!/bin/sh\ncase "$2" in\n  *"import Quartz"*) exit 1 ;;\nesac\nprintf \'%s\\n\' \'{"valid": true}\'\n',
            encoding="utf-8",
        )
        fake_python.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{temporary_directory}{os.pathsep}{environment['PATH']}"
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr
    session = json.loads(completed.stdout)
    assert session["source"] == "swift-coregraphics"
    assert session["onConsole"] is True


def test_window_swift_probe_fallback_finds_real_packaged_app_window() -> None:
    if sys.platform != "darwin":
        pytest.skip("the window probe requires macOS CoreGraphics")

    app_path = REPOSITORY_ROOT / "src-tauri/target/release/bundle/macos/Nothing.app"
    executable = app_path / "Contents/MacOS/tauri_frontend"
    if not executable.is_file():
        pytest.skip("the packaged application is unavailable")

    script = """
import { spawn } from "node:child_process";
import {
  inspectVisibleAppWindow,
  readActiveDisplayInfo,
} from "./scripts/real_app_preconditions.mjs";

const appPath = process.env.NOTHING_APP_PATH;
const executable = `${appPath}/Contents/MacOS/tauri_frontend`;
const child = spawn(executable, ["--qa-drive-stdin"], {
  cwd: appPath,
  env: { ...process.env, MASK_TOOL_QA_DRIVE: "1" },
  stdio: "ignore",
});
try {
  const displays = await readActiveDisplayInfo();
  const window = await inspectVisibleAppWindow(child.pid, displays, {
    attempts: 12,
    delayMs: 500,
  });
  if (window.source !== "swift-coregraphics") {
    throw new Error(`expected Swift fallback, got ${window.source}`);
  }
  if (
    window.onScreen !== true
    || window.bounds.width <= 0
    || window.bounds.height <= 0
    || !Number.isSafeInteger(window.windowId)
  ) {
    throw new Error(`invalid window probe result: ${JSON.stringify(window)}`);
  }
  process.stdout.write(JSON.stringify({
    source: window.source,
    windowId: window.windowId,
    bounds: window.bounds,
  }));
} finally {
  if (child.exitCode === null && child.signalCode === null) {
    child.kill("SIGTERM");
    await new Promise((resolve) => {
      const timer = setTimeout(resolve, 5_000);
      child.once("close", () => {
        clearTimeout(timer);
        resolve();
      });
    });
  }
}
"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        fake_python = Path(temporary_directory) / "python3"
        fake_python.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        fake_python.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{temporary_directory}{os.pathsep}{environment['PATH']}"
        environment["NOTHING_APP_PATH"] = str(app_path)
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["source"] == "swift-coregraphics"
    assert result["windowId"] > 0
    assert result["bounds"]["width"] > 0
    assert result["bounds"]["height"] > 0


def test_window_probe_failure_is_reported_as_probe_error() -> None:
    script = """
import { inspectVisibleAppWindow } from "./scripts/real_app_preconditions.mjs";

try {
  await inspectVisibleAppWindow(12345, [], { attempts: 1, delayMs: 0 });
  process.stdout.write(JSON.stringify({ reason: "unexpected-pass" }));
} catch (error) {
  process.stdout.write(JSON.stringify({
    reason: error.reason,
    probe: error.signals?.probe,
  }));
}
"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        for command in ("python3", "swift"):
            fake_probe = Path(temporary_directory) / command
            fake_probe.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            fake_probe.chmod(0o755)
        fake_osascript = Path(temporary_directory) / "osascript"
        fake_osascript.write_text("#!/bin/sh\nprintf '%s' '1\\n'\n", encoding="utf-8")
        fake_osascript.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{temporary_directory}{os.pathsep}{environment['PATH']}"
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["reason"] == "probe-error"
    assert result["probe"] == "SCREEN_WINDOW_INFO_UNAVAILABLE"


def test_display_probe_failure_and_empty_display_list_have_distinct_reasons() -> None:
    script = """
import { readActiveDisplayInfo } from "./scripts/real_app_preconditions.mjs";

try {
  await readActiveDisplayInfo();
  process.stdout.write(JSON.stringify({ reason: "unexpected-pass" }));
} catch (error) {
  process.stdout.write(JSON.stringify({
    reason: error.reason,
    probe: error.signals?.probe,
  }));
}
"""
    with tempfile.TemporaryDirectory() as temporary_directory:
        fake_swift = Path(temporary_directory) / "swift"
        fake_swift.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        fake_swift.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{temporary_directory}{os.pathsep}{environment['PATH']}"
        failed_probe = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    assert failed_probe.returncode == 0, failed_probe.stderr
    assert json.loads(failed_probe.stdout)["reason"] == "probe-error"

    with tempfile.TemporaryDirectory() as temporary_directory:
        fake_swift = Path(temporary_directory) / "swift"
        fake_swift.write_text("#!/bin/sh\nprintf '%s' '[]'\n", encoding="utf-8")
        fake_swift.chmod(0o755)
        environment = os.environ.copy()
        environment["PATH"] = f"{temporary_directory}{os.pathsep}{environment['PATH']}"
        empty_display_list = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=REPOSITORY_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    assert empty_display_list.returncode == 0, empty_display_list.stderr
    assert json.loads(empty_display_list.stdout)["reason"] == "no-display"
