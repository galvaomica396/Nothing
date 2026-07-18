import argparse
import json
import os
import platform
import plistlib
import subprocess
import sys
import time
from pathlib import Path


def load_tauri_config(repo_root: Path) -> dict[str, object]:
    config_path = repo_root / "src-tauri" / "tauri.conf.json"
    if not config_path.exists():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


def default_app_path(repo_root: Path) -> Path:
    if platform.system() == "Darwin":
        bundle_dir = repo_root / "src-tauri" / "target" / "release" / "bundle" / "macos"
        candidates = sorted(bundle_dir.glob("*.app"))
        if candidates:
            return candidates[0]
    if platform.system() == "Windows":
        return repo_root / "src-tauri" / "target" / "release" / "Document-Masker-Tauri-windows-x64.exe"
    return repo_root / "src-tauri" / "target" / "release" / "tauri_frontend"


def macos_bundle_identifier(bundle_path: Path) -> str:
    info_plist = bundle_path / "Contents" / "Info.plist"
    if not info_plist.exists():
        return ""
    try:
        info = plistlib.loads(info_plist.read_bytes())
    except (plistlib.InvalidFileException, OSError, ValueError):
        return ""
    identifier = info.get("CFBundleIdentifier")
    return identifier.strip() if isinstance(identifier, str) else ""


def macos_bundle_id_report(search_roots: list[Path], bundle_id: str) -> dict[str, object]:
    active_apps: list[str] = []
    disabled_backups: list[str] = []
    for root in search_roots:
        if not root.exists():
            continue
        for candidate in root.rglob("*"):
            if not candidate.is_dir():
                continue
            if candidate.suffix != ".app" and not candidate.name.endswith(".disabled-bundle"):
                continue
            if macos_bundle_identifier(candidate) != bundle_id:
                continue
            if candidate.suffix == ".app":
                active_apps.append(str(candidate))
            else:
                disabled_backups.append(str(candidate))

    active_apps = sorted(active_apps)
    disabled_backups = sorted(disabled_backups)
    ambiguous = len(active_apps) > 1
    if ambiguous:
        status = "ambiguous-active-apps"
    elif len(active_apps) == 1:
        status = "single-active-app"
    else:
        status = "no-active-app"
    return {
        "status": status,
        "bundle_id": bundle_id,
        "active_apps": active_apps,
        "disabled_backups": disabled_backups,
        "active_app_count": len(active_apps),
        "disabled_backup_count": len(disabled_backups),
        "ambiguous": ambiguous,
    }


def computer_use_attach_diagnosis(
    *,
    active_app_count: int,
    disabled_backup_count: int,
    cg_window_count: int,
    ax_window_count: int,
    computer_use_results: list[dict[str, str]],
) -> dict[str, object]:
    computer_use_summary = "; ".join(
        f"{item.get('app', '')}: {item.get('result', '')}".strip(": ")
        for item in computer_use_results
    )
    if active_app_count > 1:
        status = "ambiguous-bundle-id"
    elif cg_window_count > 0 and ax_window_count <= 0:
        status = "visible-cgwindow-without-accessibility-window"
    elif any(item.get("result") == "attached" for item in computer_use_results):
        status = "computer-use-attached"
    else:
        status = "computer-use-attach-failed"
    return {
        "status": status,
        "active_app_count": active_app_count,
        "disabled_backup_count": disabled_backup_count,
        "cg_window_count": cg_window_count,
        "ax_window_count": ax_window_count,
        "computer_use_summary": computer_use_summary,
        "computer_use_results": computer_use_results,
    }


NATIVE_ACCEPTANCE_STEPS: tuple[str, ...] = (
    "computer_use_attached",
    "canvas_workspace_opened",
    "input_pdf_selected_via_os_picker",
    "fixture_pdf_loaded",
    "output_dir_selected_via_os_picker",
    "manual_mask_box_created",
    "manual_preview_applied",
    "final_save_completed",
)

NATIVE_ACCEPTANCE_PREREQUISITES: dict[str, tuple[str, ...]] = {
    "canvas_workspace_opened": ("computer_use_attached",),
    "fixture_pdf_loaded": ("computer_use_attached", "canvas_workspace_opened"),
    "input_pdf_selected_via_os_picker": ("computer_use_attached", "canvas_workspace_opened"),
    "output_dir_selected_via_os_picker": ("computer_use_attached", "canvas_workspace_opened"),
    "manual_mask_box_created": ("computer_use_attached", "canvas_workspace_opened"),
    "manual_preview_applied": ("manual_mask_box_created",),
    "final_save_completed": ("manual_preview_applied",),
}


def parse_native_actions(raw_actions: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for raw_action in raw_actions:
        action_text, _, evidence = raw_action.partition("::")
        name, separator, status = action_text.partition("=")
        if not separator:
            parsed.append({"name": action_text.strip(), "status": "", "evidence": evidence.strip()})
            continue
        parsed.append({"name": name.strip(), "status": status.strip(), "evidence": evidence.strip()})
    return parsed


def native_gui_acceptance_report(actions: list[dict[str, str]]) -> dict[str, object]:
    action_by_name = {
        str(action.get("name", "")).strip(): {
            "status": str(action.get("status", "")).strip(),
            "evidence": str(action.get("evidence", "")).strip(),
        }
        for action in actions
        if str(action.get("name", "")).strip()
    }

    proven: list[str] = []
    blocked: list[str] = []
    blockers: list[dict[str, str]] = []

    for step in NATIVE_ACCEPTANCE_STEPS:
        action = action_by_name.get(step)
        if not action:
            continue

        raw_status = action["status"]
        evidence = action["evidence"]
        if raw_status == "pass":
            missing_prerequisites = [
                prerequisite
                for prerequisite in NATIVE_ACCEPTANCE_PREREQUISITES.get(step, ())
                if prerequisite not in proven
            ]
            if missing_prerequisites:
                blocked.append(step)
                blockers.append(
                    {
                        "step": step,
                        "reason": f"missing prerequisite evidence: {', '.join(missing_prerequisites)}",
                        "evidence": evidence,
                    }
                )
            else:
                proven.append(step)
        elif raw_status in {"blocked", "fail"}:
            blocked.append(step)
            blockers.append({"step": step, "reason": raw_status, "evidence": evidence})
        else:
            blocked.append(step)
            blockers.append({"step": step, "reason": f"unknown status: {raw_status}", "evidence": evidence})

    not_proven = [step for step in NATIVE_ACCEPTANCE_STEPS if step not in proven]
    if not_proven:
        status = "partial" if proven else "fail"
    else:
        status = "pass"
    return {
        "status": status,
        "scope": "native packaged app GUI acceptance",
        "proven": proven,
        "blocked": blocked,
        "not_proven": not_proven,
        "blockers": blockers,
        "actions": actions,
    }


def parse_computer_use_results(raw_results: list[str]) -> list[dict[str, str]]:
    parsed: list[dict[str, str]] = []
    for raw_result in raw_results:
        app, separator, result = raw_result.partition("=")
        if not separator:
            parsed.append({"app": raw_result.strip(), "result": ""})
            continue
        parsed.append({"app": app.strip(), "result": result.strip()})
    return parsed


def executable_for_app(app_path: Path) -> Path:
    if app_path.suffix == ".app":
        macos_dir = app_path / "Contents" / "MacOS"
        executables = [path for path in macos_dir.iterdir() if path.is_file() and os.access(path, os.X_OK)]
        if not executables:
            raise RuntimeError(f"실행 파일을 찾을 수 없습니다: {macos_dir}")
        return executables[0]
    return app_path


def pids_for_process_name(name: str) -> set[int]:
    if platform.system() != "Darwin":
        return set()
    result = subprocess.run(
        ["pgrep", "-x", name],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {int(line) for line in result.stdout.splitlines() if line.strip().isdigit()}


def macos_cg_window_snapshot(labels: list[str]) -> tuple[int, str]:
    script = "\n".join(
        [
            "import CoreGraphics",
            "import Foundation",
            "let rawLabels = ProcessInfo.processInfo.environment[\"MAKIIING_WINDOW_LABELS\"] ?? \"[]\"",
            "let labelData = rawLabels.data(using: .utf8) ?? Data()",
            "let labels = (try? JSONSerialization.jsonObject(with: labelData)) as? [String] ?? []",
            "let windows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []",
            "var total = 0",
            "var details: [String] = []",
            "for window in windows {",
            "  let owner = window[kCGWindowOwnerName as String] as? String ?? \"\"",
            "  let name = window[kCGWindowName as String] as? String ?? \"\"",
            "  let windowId = window[kCGWindowNumber as String] as? Int ?? 0",
            "  let layer = window[kCGWindowLayer as String] as? Int ?? -1",
            "  let bounds = window[kCGWindowBounds as String] as? [String: Any] ?? [:]",
            "  let width = (bounds[\"Width\"] as? NSNumber)?.doubleValue ?? 0",
            "  let height = (bounds[\"Height\"] as? NSNumber)?.doubleValue ?? 0",
            "  let alpha = (window[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1",
            "  let matched = labels.contains { !$0.isEmpty && (owner == $0 || owner.contains($0) || name == $0 || name.contains($0)) }",
            "  if matched && layer == 0 && width >= 200 && height >= 120 && alpha > 0 {",
            "    total += 1",
            "    details.append(\"\\(owner) cg_windows=1 id=\\(windowId) name=\\(name) bounds=\\(bounds)\")",
            "  }",
            "}",
            "print(\"\\(total)|\\(details.joined(separator: \"\\n\"))\")",
        ]
    )
    env = os.environ.copy()
    env["MAKIIING_WINDOW_LABELS"] = json.dumps(labels, ensure_ascii=False)
    result = subprocess.run(
        ["swift", "-e", script],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        return 0, result.stderr.strip()
    raw = result.stdout.strip()
    count_text, _, details = raw.partition("|")
    try:
        return int(count_text), details.strip()
    except ValueError:
        return 0, raw


def macos_largest_cg_window_id(labels: list[str]) -> tuple[str, str]:
    script = "\n".join(
        [
            "import CoreGraphics",
            "import Foundation",
            "let rawLabels = ProcessInfo.processInfo.environment[\"MAKIIING_WINDOW_LABELS\"] ?? \"[]\"",
            "let labelData = rawLabels.data(using: .utf8) ?? Data()",
            "let labels = (try? JSONSerialization.jsonObject(with: labelData)) as? [String] ?? []",
            "let windows = CGWindowListCopyWindowInfo([.optionAll, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []",
            "var bestId = 0",
            "var bestArea = 0.0",
            "var bestDetail = \"\"",
            "for window in windows {",
            "  let owner = window[kCGWindowOwnerName as String] as? String ?? \"\"",
            "  let name = window[kCGWindowName as String] as? String ?? \"\"",
            "  let windowId = window[kCGWindowNumber as String] as? Int ?? 0",
            "  let layer = window[kCGWindowLayer as String] as? Int ?? -1",
            "  let bounds = window[kCGWindowBounds as String] as? [String: Any] ?? [:]",
            "  let width = (bounds[\"Width\"] as? NSNumber)?.doubleValue ?? 0",
            "  let height = (bounds[\"Height\"] as? NSNumber)?.doubleValue ?? 0",
            "  let alpha = (window[kCGWindowAlpha as String] as? NSNumber)?.doubleValue ?? 1",
            "  let matched = labels.contains { !$0.isEmpty && (owner == $0 || owner.contains($0) || name == $0 || name.contains($0)) }",
            "  let area = width * height",
            "  if matched && layer == 0 && width >= 200 && height >= 120 && alpha > 0 && area > bestArea {",
            "    bestId = windowId",
            "    bestArea = area",
            "    bestDetail = \"\\(owner) id=\\(windowId) area=\\(Int(area)) bounds=\\(bounds)\"",
            "  }",
            "}",
            "print(\"\\(bestId)|\\(bestDetail)\")",
        ]
    )
    env = os.environ.copy()
    env["MAKIIING_WINDOW_LABELS"] = json.dumps(labels, ensure_ascii=False)
    result = subprocess.run(
        ["swift", "-e", script],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if result.returncode != 0:
        return "", result.stderr.strip()
    window_id, _, details = result.stdout.strip().partition("|")
    return window_id.strip(), details.strip()


def macos_cg_window_capture(labels: list[str], out_path: Path) -> dict[str, str]:
    if platform.system() != "Darwin":
        return {"status": "skipped", "reason": "CoreGraphics window capture is macOS-only"}
    window_id, details = macos_largest_cg_window_id(labels)
    if not window_id or window_id == "0":
        return {"status": "fail", "reason": "renderable CoreGraphics window not found", "details": details}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["screencapture", "-x", "-l", window_id, str(out_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return {
            "status": "fail",
            "window_id": window_id,
            "details": details,
            "stderr_tail": result.stderr[-1000:],
        }
    return {"status": "pass", "window_id": window_id, "details": details, "screenshot": str(out_path)}


def macos_window_snapshot(labels: list[str]) -> tuple[int, str]:
    cg_count, cg_details = macos_cg_window_snapshot(labels)
    if cg_count > 0:
        return cg_count, cg_details

    script = "\n".join(
        [
            "on run argv",
            "  tell application \"System Events\"",
            "    set totalWindows to 0",
            "    set outputText to \"\"",
            "    repeat with p in every process",
            "      set pname to name of p",
            "      set matched to false",
            "      repeat with labelText in argv",
            "        if labelText is not \"\" and (pname is labelText or pname contains labelText) then",
            "          set matched to true",
            "        end if",
            "      end repeat",
            "      if matched then",
            "        set wcount to count of windows of p",
            "        set totalWindows to totalWindows + wcount",
            "        set outputText to outputText & pname & \" windows=\" & wcount & linefeed",
            "      end if",
            "    end repeat",
            "    return (totalWindows as text) & \"|\" & outputText",
            "  end tell",
            "end run",
        ]
    )
    result = subprocess.run(
        ["osascript", "-"] + labels,
        input=script,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        return 0, result.stderr.strip()
    raw = result.stdout.strip()
    count_text, _, details = raw.partition("|")
    try:
        return int(count_text), details.strip()
    except ValueError:
        return 0, raw


def macos_process_labels(repo_root: Path, app_path: Path, executable: Path) -> list[str]:
    config = load_tauri_config(repo_root)
    labels: list[str] = [app_path.stem, executable.name]
    product_name = str(config.get("productName", "")).strip()
    if product_name:
        labels.append(product_name)

    app_config = config.get("app")
    if isinstance(app_config, dict):
        windows = app_config.get("windows")
        if isinstance(windows, list):
            for window in windows:
                if not isinstance(window, dict):
                    continue
                title = str(window.get("title", "")).strip()
                if title:
                    labels.append(title)

    deduped: list[str] = []
    for label in labels:
        if label and label not in deduped:
            deduped.append(label)
    return deduped


def run_macos_app_smoke(
    repo_root: Path,
    app_path: Path,
    executable: Path,
    seconds: float,
) -> dict[str, object]:
    labels = macos_process_labels(repo_root, app_path, executable)
    before_pids = pids_for_process_name(executable.name)
    launch = subprocess.run(
        ["open", "-n", "-F", str(app_path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if launch.returncode != 0:
        return {
            "status": "fail",
            "app_path": str(app_path),
            "executable": str(executable),
            "labels": labels,
            "exit_code": launch.returncode,
            "stdout_tail": launch.stdout[-1000:],
            "stderr_tail": launch.stderr[-1000:],
            "scope": "startup/render smoke with visible-window check",
        }

    deadline = time.time() + seconds
    window_count = 0
    window_details = ""
    while time.time() < deadline:
        window_count, window_details = macos_window_snapshot(labels)
        if window_count > 0:
            break
        time.sleep(0.25)

    after_pids = pids_for_process_name(executable.name)
    new_pids = sorted(after_pids - before_pids)
    for pid in new_pids:
        subprocess.run(["kill", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if window_count <= 0:
        return {
            "status": "fail",
            "app_path": str(app_path),
            "executable": str(executable),
            "labels": labels,
            "alive_seconds": seconds,
            "window_count": window_count,
            "window_details": window_details,
            "new_pids": new_pids,
            "scope": "startup/render smoke with visible-window check",
            "error": "앱 프로세스는 시작됐지만 표시 가능한 창을 찾지 못했습니다.",
        }

    return {
        "status": "pass",
        "app_path": str(app_path),
        "executable": str(executable),
        "labels": labels,
        "alive_seconds": seconds,
        "window_count": window_count,
        "window_details": window_details,
        "new_pids": new_pids,
        "scope": "startup/render smoke with visible-window check",
        "not_proven": ["OS file picker", "drag masking", "final save"],
    }


def run_smoke(app_path: Path, seconds: float) -> dict[str, object]:
    executable = executable_for_app(app_path)
    if not executable.exists():
        raise RuntimeError(f"앱 실행 파일이 없습니다: {executable}")

    if platform.system() == "Darwin" and app_path.suffix == ".app":
        repo_root = Path(__file__).resolve().parents[1]
        return run_macos_app_smoke(
            repo_root,
            app_path,
            executable,
            seconds,
        )

    process = subprocess.Popen([str(executable)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(seconds)
    alive = process.poll() is None
    if alive:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    else:
        stdout, stderr = process.communicate(timeout=5)
        return {
            "status": "fail",
            "app_path": str(app_path),
            "executable": str(executable),
            "alive_seconds": seconds,
            "exit_code": process.returncode,
            "stdout_tail": stdout.decode("utf-8", "replace")[-1000:],
            "stderr_tail": stderr.decode("utf-8", "replace")[-1000:],
            "scope": "startup/render smoke only",
        }
    return {
        "status": "pass",
        "app_path": str(app_path),
        "executable": str(executable),
        "alive_seconds": seconds,
        "scope": "startup/render smoke only",
        "not_proven": ["OS file picker", "drag masking", "final save"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--app-path", default="")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--out", default="")
    parser.add_argument("--evidence", default="")
    parser.add_argument("--bundle-id", default="")
    parser.add_argument("--bundle-search-root", action="append", default=[])
    parser.add_argument("--computer-use-result", action="append", default=[])
    parser.add_argument("--cg-window-count", type=int, default=0)
    parser.add_argument("--ax-window-count", type=int, default=0)
    parser.add_argument("--native-action", action="append", default=[])
    parser.add_argument("--native-screenshot-out", default="")
    args = parser.parse_args()
    repo_root = Path(args.repo_root).resolve()
    app_path = Path(args.app_path).resolve() if args.app_path else default_app_path(repo_root)
    output_path = args.out or args.evidence
    try:
        result = run_smoke(app_path, args.seconds)
    except Exception as exc:
        result = {
            "status": "fail",
            "app_path": str(app_path),
            "error": str(exc),
            "scope": "startup/render smoke only",
        }
    bundle_report: dict[str, object] | None = None
    if args.bundle_id:
        search_roots = [Path(path).resolve() for path in args.bundle_search_root]
        if not search_roots:
            search_roots = [
                repo_root / "src-tauri" / "target" / "release" / "bundle" / "macos",
                Path.home() / "Downloads",
                repo_root / ".omx" / "disabled-duplicate-apps",
            ]
        bundle_report = macos_bundle_id_report(search_roots, args.bundle_id)
        result["bundle_report"] = bundle_report

    computer_use_results = parse_computer_use_results(args.computer_use_result)
    if computer_use_results:
        result["attach_diagnosis"] = computer_use_attach_diagnosis(
            active_app_count=int(bundle_report["active_app_count"]) if bundle_report else 0,
            disabled_backup_count=int(bundle_report["disabled_backup_count"]) if bundle_report else 0,
            cg_window_count=args.cg_window_count,
            ax_window_count=args.ax_window_count,
            computer_use_results=computer_use_results,
        )
    native_actions = parse_native_actions(args.native_action)
    if native_actions:
        result["native_gui_acceptance"] = native_gui_acceptance_report(native_actions)
    if args.native_screenshot_out:
        try:
            executable = executable_for_app(app_path)
            result["native_screenshot"] = macos_cg_window_capture(
                macos_process_labels(repo_root, app_path, executable),
                Path(args.native_screenshot_out),
            )
        except Exception as exc:
            result["native_screenshot"] = {"status": "fail", "reason": str(exc)}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if output_path:
        Path(output_path).write_text(f"{text}\n", encoding="utf-8")
    print(text)
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
