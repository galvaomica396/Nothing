import ast
import json
import hashlib
import shlex
import re
import os
import subprocess
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_VERSION = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))["version"]
WINDOWS_RELEASE_PREFIX = f"Nothing-{APP_VERSION}-windows-x64"
MACOS_RELEASE_PREFIX = f"Nothing-{APP_VERSION}-macos-arm64"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "ARCHITECTURE.md"
def application_composition_typescript_sources() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "src" / "app").rglob("*.ts"))
    )



def _workflow_scalar(value: str) -> str | bool:
    value = value.strip()
    if value.lower() == "false":
        return False
    if value.lower() == "true":
        return True
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_active_workflow(text: str) -> dict[str, dict[str, object]]:
    """Parse the bounded GitHub Actions job/step shape used by release workflows."""
    jobs: dict[str, dict[str, object]] = {}
    current_job: dict[str, object] | None = None
    current_step: dict[str, object] | None = None
    current_mapping: dict[str, object] | None = None
    block_field: str | None = None
    block_indent = 0

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        stripped = raw_line.strip()

        if block_field is not None:
            if indent > block_indent:
                current_step[block_field] += f"{raw_line[block_indent + 2:]}\n"  # type: ignore[index]
                continue
            block_field = None

        job_match = re.fullmatch(r"  ([A-Za-z0-9_-]+):", raw_line)
        if job_match:
            current_job = {"runs-on": None, "steps": []}
            jobs[job_match.group(1)] = current_job
            current_step = None
            current_mapping = None
            continue
        if current_job is None:
            continue
        if indent == 4 and stripped.startswith("runs-on:"):
            current_job["runs-on"] = _workflow_scalar(stripped.split(":", 1)[1])
            continue
        if indent == 6 and stripped.startswith("- "):
            current_step = {}
            current_job["steps"].append(current_step)  # type: ignore[union-attr]
            current_mapping = None
            key, value = stripped[2:].split(":", 1)
            current_step[key] = _workflow_scalar(value)
            continue
        if current_step is None:
            continue
        if current_mapping is not None and indent == 10 and ":" in stripped:
            key, value = stripped.split(":", 1)
            current_mapping[key] = _workflow_scalar(value)
            continue
        if indent != 8 or ":" not in stripped:
            continue
        current_mapping = None
        key, value = stripped.split(":", 1)
        value = value.strip()
        if value in {"|", ">"}:
            current_step[key] = ""
            block_field = key
            block_indent = indent
        elif not value:
            mapping: dict[str, object] = {}
            current_step[key] = mapping
            current_mapping = mapping
        else:
            current_step[key] = _workflow_scalar(value)

    return jobs


def active_steps(workflow: dict[str, object], job_name: str) -> list[dict[str, object]]:
    job = workflow.get(job_name)
    if not isinstance(job, dict):
        return []
    steps = job.get("steps")
    if not isinstance(steps, list):
        return []
    return [step for step in steps if isinstance(step, dict) and _step_is_active(step)]


def _step_is_active(step: dict[str, object]) -> bool:
    condition = step.get("if")
    if condition is False:
        return False
    if not isinstance(condition, str):
        return True
    normalized = re.sub(r"\s+", "", condition).lower()
    if normalized in {"false", "${{false}}", "never()", "${{never()}}"}:
        return False
    expression = normalized.removeprefix("${{").removesuffix("}}")
    return not (expression == "!true" or re.search(r"(^|&&)false(&&|$)", expression))


def workflow_for(path: Path) -> dict[str, dict[str, object]]:
    return parse_active_workflow(path.read_text(encoding="utf-8"))


def step_named(steps: list[dict[str, object]], name: str) -> dict[str, object] | None:
    return next((step for step in steps if step.get("name") == name), None)


def uses_action(steps: list[dict[str, object]], action: str) -> bool:
    return any(step.get("uses") == action for step in steps)


def invokes_command(step: dict[str, object] | None, command: str) -> bool:
    if not step or not isinstance(step.get("run"), str) or str(step.get("continue-on-error", "")).lower() not in {"", "false"}:
        return False
    expected = shlex.split(command)
    for line in step["run"].splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#") or any(token in candidate for token in ("||", ";", "set +e")):
            continue
        try:
            tokens = shlex.split(candidate, comments=True)
        except ValueError:
            continue
        if tokens[:len(expected)] == expected:
            return True
    return False


def has_artifact_upload(
    steps: list[dict[str, object]], name: str, path: str, gated: bool = False
) -> bool:
    for step in steps:
        if step.get("uses") != "actions/upload-artifact@v7":
            continue
        with_values = step.get("with")
        if not isinstance(with_values, dict):
            continue
        if with_values.get("name") != name or with_values.get("path") != path:
            continue
        if gated and re.sub(r"\s+", "", str(step.get("continue-on-error", ""))) != "${{inputs.publish_release}}":
            continue
        return True
    return False
def is_release_gated(step: dict[str, object] | None) -> bool:
    return bool(
        step
        and re.sub(r"\s+", "", str(step.get("if", ""))) == "${{inputs.publish_release}}"
    )


def safe_process_diagnostic(reason: str, result: subprocess.CompletedProcess[str] | None = None) -> str:
    if result is None:
        return f"reason={reason}"
    output = (result.stdout or "") + (result.stderr or "")
    digest = hashlib.sha256(output.encode("utf-8", errors="replace")).hexdigest()[:16]
    return (
        f"reason={reason} returncode={result.returncode} "
        f"output_chars={len(output)} output_sha256_16={digest}"
    )
def json_payloads(result: subprocess.CompletedProcess[str]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for channel, output in (("stdout", result.stdout or ""), ("stderr", result.stderr or "")):
        for line in output.splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as error:
                raise AssertionError(f"{channel} contains an unstructured line") from error
            if not isinstance(payload, dict):
                raise AssertionError(f"{channel} event must be an object")
            payloads.append(payload)
    return payloads





class ReleaseWorkflowTests(unittest.TestCase):
    def test_architecture_doc_describes_react_tauri_python_boundary(self):
        self.assertTrue(
            ARCHITECTURE_DOC.exists(),
            "Architecture documentation should exist at docs/ARCHITECTURE.md.",
        )
        architecture = ARCHITECTURE_DOC.read_text(encoding="utf-8")

        self.assertIn("React", architecture)
        self.assertIn("Tauri", architecture)
        self.assertIn("Python", architecture)
        self.assertIn("IPC", architecture)
        self.assertNotIn("coordinate batch", architecture.lower())

    def test_windows_specific_tauri_config_builds_nsis_installer(self):
        config = json.loads((REPO_ROOT / "src-tauri" / "tauri.windows.conf.json").read_text(encoding="utf-8"))

        self.assertEqual(["nsis"], config["bundle"]["targets"])

    def test_release_workflows_use_active_tauri_v2_build_and_artifact_steps(self):
        windows = workflow_for(REPO_ROOT / ".github" / "workflows" / "build-windows.yml")
        macos = workflow_for(REPO_ROOT / ".github" / "workflows" / "build-macos.yml")
        windows_job = windows.get("build-windows", {})
        macos_job = macos.get("build-macos", {})
        windows_steps = active_steps(windows, "build-windows")
        macos_steps = active_steps(macos, "build-macos")

        self.assertEqual("windows-2025-vs2026", windows_job.get("runs-on"))
        self.assertEqual("macos-15", macos_job.get("runs-on"))
        for steps in (windows_steps, macos_steps):
            self.assertTrue(uses_action(steps, "actions/checkout@v6"))
            self.assertTrue(uses_action(steps, "actions/setup-node@v6"))
            self.assertTrue(uses_action(steps, "actions/setup-python@v6"))
            setup_node = step_named(steps, "Setup Node")
            self.assertEqual(
                {"node-version": "24", "cache": "npm"},
                setup_node.get("with") if setup_node else None,
            )

        self.assertTrue(invokes_command(step_named(windows_steps, "Build Windows app"), "npm run tauri build"))
        self.assertTrue(
            invokes_command(
                step_named(macos_steps, "Build macOS app"),
                "npm run tauri build -- --bundles app",
            )
        )
        self.assertTrue(
            invokes_command(
                step_named(macos_steps, "Ad-hoc sign macOS bundle"),
                'codesign --force --deep --sign - "$app_bundle"',
            )
        )
        self.assertTrue(
            invokes_command(
                step_named(macos_steps, "Ad-hoc sign macOS bundle"),
                'codesign --verify --deep --strict "$app_bundle"',
            )
        )
        self.assertTrue(
            has_artifact_upload(
                windows_steps,
                f"{WINDOWS_RELEASE_PREFIX}-release-assets",
                "release-windows",
                gated=True,
            )
        )
        self.assertTrue(
            has_artifact_upload(
                macos_steps,
                f"{MACOS_RELEASE_PREFIX}-release-assets",
                "release-macos",
                gated=True,
            )
        )
        active_runs = [
            run
            for step in (*windows_steps, *macos_steps)
            if isinstance((run := step.get("run")), str)
        ]
        self.assertFalse(any("hwpx_masking.py" in run for run in active_runs))
        self.assertFalse(any("FORCE_JAVASCRIPT_ACTIONS_TO_NODE24" in run for run in active_runs))

    def test_release_workflows_run_packaged_native_qa_before_gated_release(self):
        windows = workflow_for(REPO_ROOT / ".github" / "workflows" / "build-windows.yml")
        macos = workflow_for(REPO_ROOT / ".github" / "workflows" / "build-macos.yml")
        windows_steps = active_steps(windows, "build-windows")
        macos_steps = active_steps(macos, "build-macos")

        self.assertTrue(
            invokes_command(
                step_named(windows_steps, "Windows packaged desktop launch smoke"),
                f"./scripts/e2e_windows_desktop_smoke.ps1 -ReleaseDir release-windows/portable "
                f"-InstallerPath release-windows/{WINDOWS_RELEASE_PREFIX}-setup.exe",
            )
        )
        self.assertTrue(
            invokes_command(
                step_named(macos_steps, "macOS packaged desktop launch smoke"),
                'python scripts/e2e_tauri_local_smoke.py --app-path '
                '"src-tauri/target/release/bundle/macos/Nothing.app" --seconds 5 '
                "--out release-macos/macos-launch-smoke.json",
            )
        )
        for steps, qa_name, upload_name, roundtrip_name in (
            (
                windows_steps,
                "Windows packaged desktop launch smoke",
                "Upload Windows release assets to GitHub Release",
                "Windows release asset roundtrip smoke",
            ),
            (
                macos_steps,
                "macOS packaged desktop launch smoke",
                "Upload macOS release assets to GitHub Release",
                "macOS release asset roundtrip smoke",
            ),
        ):
            qa = step_named(steps, qa_name)
            upload = step_named(steps, upload_name)
            roundtrip = step_named(steps, roundtrip_name)
            self.assertIsNotNone(qa)
            self.assertTrue(is_release_gated(upload))
            self.assertTrue(invokes_command(upload, "gh release upload"))
            self.assertTrue(is_release_gated(roundtrip))
            self.assertTrue(invokes_command(roundtrip, "gh release download"))
            self.assertTrue(all(
                step is not None and str(step.get("continue-on-error", "")).lower() in {"", "false"}
                for step in (qa, upload, roundtrip)
            ))
            names = [step.get("name") for step in steps]
            self.assertLess(names.index(qa_name), names.index(upload_name))
            self.assertLess(names.index(upload_name), names.index(roundtrip_name))
    def test_workflow_parser_rejects_dead_release_contract_decoys_without_leaking_canaries(self):
        canary = "RAW_OUTPUT_CANARY_7d26"
        workflow = parse_active_workflow(
            """
jobs:
  release:
    runs-on: stable
    steps:
      # - name: Build macOS app
      #   run: npm run tauri build -- --bundles app
      - name: Quoted dead command
        run: echo "npm run tauri build -- --bundles app"
      - name: Disabled native QA
        if: ${{ false }}
        run: python scripts/e2e_tauri_local_smoke.py --app-path app --seconds 5 --out smoke.json
      - name: Wrong native QA
        run: python scripts/e2e_tauri_local_smoke.py --app-path app --seconds 1 --out smoke.json
      - name: Wrong artifact
        uses: actions/upload-artifact@v7
        with:
          name: expected-release-assets
          path: wrong-release-dir
"""
        )
        steps = active_steps(workflow, "release")

        self.assertEqual(
            ["Quoted dead command", "Wrong native QA", "Wrong artifact"],
            [step.get("name") for step in steps],
        )
        self.assertFalse(
            invokes_command(
                step_named(steps, "Quoted dead command"),
                "npm run tauri build -- --bundles app",
            )
        )
        self.assertFalse(
            invokes_command(
                step_named(steps, "Wrong native QA"),
                "python scripts/e2e_tauri_local_smoke.py --app-path app --seconds 5 --out smoke.json",
            )
        )
        self.assertFalse(has_artifact_upload(steps, "expected-release-assets", "release-assets"))
        diagnostic = safe_process_diagnostic(
            "CANARY_FAILURE",
            subprocess.CompletedProcess(
                args=["/private/command-canary"],
                returncode=7,
                stdout=canary,
                stderr=canary,
            ),
        )
        self.assertFalse(canary in diagnostic)
        self.assertFalse("/private/command-canary" in diagnostic)

    def test_windows_smoke_uses_phase6_fixture_smoke(self):
        smoke = (REPO_ROOT / "scripts" / "e2e_windows_smoke.ps1").read_text(encoding="utf-8")

        self.assertIn("e2e_fixture_smoke.py", smoke)
        self.assertIn("FixturePath", smoke)
        self.assertIn("fixture-backed masking smoke", smoke)
        self.assertNotIn("HWPX", smoke)

    def test_windows_desktop_smoke_runs_packaged_pdf_manual_masking(self):
        smoke = (REPO_ROOT / "scripts" / "e2e_windows_desktop_smoke.ps1").read_text(encoding="utf-8")

        self.assertNotIn("hwpx_masking.py", smoke)
        self.assertNotIn("e2e_hwpx_fixture_smoke.py", smoke)
        self.assertNotIn("ensure_phase7_hwpx_fixture.py", smoke)
        self.assertIn("masking_runtime\\bin\\masking_engine.exe", smoke)
        self.assertIn("e2e_manual_boxes_smoke.py", smoke)
        self.assertIn(f"{WINDOWS_RELEASE_PREFIX}.exe", smoke)
        self.assertIn(f"{WINDOWS_RELEASE_PREFIX}-setup.exe", smoke)
        self.assertIn("InstallerPath", smoke)
        self.assertNotIn(f"makiiing-v2-{APP_VERSION}-windows-x64", smoke)
        self.assertNotIn("makiiing-v2-2.1.3-windows-x64.exe", smoke)
        self.assertIn("Packaged manual boxes PASS", smoke)

    def test_packaged_manual_boxes_entry_has_a_parseable_manual_mode_and_runtime_command(self):
        engine_entry = (REPO_ROOT / "scripts" / "masking_engine_entry.py").read_text(encoding="utf-8")
        tree = ast.parse(engine_entry)

        function_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        option_literals = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        self.assertIn("run_manual_boxes", function_names)
        self.assertIn("--manual-boxes", option_literals)

    def test_packaged_engine_and_runner_reject_raw_text_preview_without_disclosure(self):
        commands = [
            [sys.executable, "scripts/masking_engine_entry.py", "--repo-root", str(REPO_ROOT), "--input", "/private/raw-secret.pdf", "--opts", '{"return_text_preview": true}'],
            [sys.executable, "scripts/run_masking_pipeline.py", "--repo-root", str(REPO_ROOT), "--mode", "analyze", "--input", "/private/raw-secret.pdf", "--opts", '{"return_text_preview": true}'],
        ]
        for command in commands:
            try:
                result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=False, timeout=10)
            except subprocess.TimeoutExpired:
                raise AssertionError("reason=RAW_PREVIEW_TIMEOUT timeout_seconds=10") from None
            diagnostic = safe_process_diagnostic("RAW_PREVIEW_REJECTION_FAILED", result)
            payloads = json_payloads(result)
            self.assertNotEqual(result.returncode, 0, diagnostic)
            self.assertTrue(payloads, diagnostic)
            serialized = json.dumps(payloads)
            self.assertTrue("RAW_TEXT_PREVIEW_REJECTED" in serialized, diagnostic)
            self.assertTrue('"rawTextReturned": false' in serialized, diagnostic)
            self.assertFalse("raw-secret" in serialized, diagnostic)
            self.assertFalse("raw-secret" in (result.stderr or ""), diagnostic)

    def test_masking_engine_import_does_not_require_tkinter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shadow = Path(tmpdir) / "tkinter.py"
            shadow.write_text("raise ModuleNotFoundError(\"No module named '_tkinter'\")\n", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{tmpdir}{os.pathsep}{REPO_ROOT}"
            try:
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import document_masker_ocr_gui; print(document_masker_ocr_gui.APP_VERSION)",
                    ],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                raise AssertionError("reason=ENGINE_IMPORT_TIMEOUT timeout_seconds=10") from None

        diagnostic = safe_process_diagnostic("ENGINE_IMPORT_FAILED", result)
        self.assertTrue(not result.stderr, diagnostic)
        self.assertEqual(0, result.returncode, diagnostic)
        self.assertTrue(APP_VERSION in (result.stdout or ""), diagnostic)

    def test_masking_engine_cli_entry_does_not_start_legacy_gui(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shadow = Path(tmpdir) / "tkinter.py"
            shadow.write_text("raise ModuleNotFoundError(\"No module named '_tkinter'\")\n", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{tmpdir}{os.pathsep}{REPO_ROOT}"
            try:
                result = subprocess.run(
                    [sys.executable, "document_masker_ocr_gui.py"],
                    cwd=REPO_ROOT,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                raise AssertionError("reason=LEGACY_GUI_GUARD_TIMEOUT timeout_seconds=10") from None

        diagnostic = safe_process_diagnostic("LEGACY_GUI_GUARD_FAILED", result)
        self.assertTrue(not result.stderr, diagnostic)
        self.assertEqual(0, result.returncode, diagnostic)

    def test_tauri_resources_include_context_module(self):
        config = json.loads((REPO_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))

        self.assertEqual(
            "masking_runtime/masking_context.py",
            config["bundle"]["resources"]["../masking_context.py"],
        )
        self.assertEqual(
            "masking_runtime/pdf_redaction_rendering.py",
            config["bundle"]["resources"]["../pdf_redaction_rendering.py"],
        )
        self.assertNotIn("../hwpx_masking.py", config["bundle"]["resources"])
        self.assertNotIn("../scripts/e2e_hwpx_fixture_smoke.py", config["bundle"]["resources"])

    def test_pyinstaller_engine_spec_excludes_removed_hwpx_modules(self):
        spec = (REPO_ROOT / "packaging" / "pyinstaller" / "masking_engine.spec").read_text(encoding="utf-8")

        self.assertIn('"document_masker_ocr_gui"', spec)
        self.assertNotIn('"hwpx_masking"', spec)
        self.assertNotIn('"hwpx_models"', spec)
        self.assertNotIn('"hwpx_xml_redaction"', spec)

    def test_pyinstaller_builds_fail_when_packaged_ko_pii_detector_is_unavailable(self):
        entry = (REPO_ROOT / "scripts" / "masking_engine_entry.py").read_text(encoding="utf-8")
        posix_build = (REPO_ROOT / "scripts" / "build_masking_engine.sh").read_text(encoding="utf-8")
        windows_build = (REPO_ROOT / "scripts" / "build_masking_engine.ps1").read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--detector-smoke"', entry)
        self.assertIn("build_ko_pii_detector", entry)
        self.assertIn('"detector_available": True', entry)
        self.assertIn('"$dist_bin" --detector-smoke', posix_build)
        self.assertIn('& $distExe --detector-smoke', windows_build)

    def test_local_tauri_smoke_is_startup_render_only(self):
        script = (REPO_ROOT / "scripts" / "e2e_tauri_local_smoke.py").read_text(encoding="utf-8")

        self.assertIn("startup/render smoke only", script)
        self.assertIn("OS file picker", script)
        self.assertIn("drag masking", script)
        self.assertIn("final save", script)
        self.assertIn("default_app_path", script)

    def test_tauri_config_avoids_global_tauri_api_exposure(self):
        config = json.loads((REPO_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        composition_sources = application_composition_typescript_sources()

        self.assertIs(
            False,
            config["app"]["withGlobalTauri"],
            "Desktop builds should use imported Tauri APIs instead of exposing window.__TAURI__.",
        )
        self.assertIn('from "@tauri-apps/api/core"', composition_sources)
        self.assertIn("__TAURI_INTERNALS__", composition_sources)

    def test_tauri_main_window_is_created_during_setup(self):
        config = json.loads((REPO_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        lib_rs = (REPO_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        # R3 module split: the macOS activation-policy / foreground FFI moved to
        # platform_macos.rs. lib.rs still owns the setup wiring + ensure_main_window
        # and calls activate_macos_app(); the FFI-internal strings are asserted
        # against their new home without weakening any check.
        platform_macos_rs = (
            REPO_ROOT / "src-tauri" / "src" / "platform_macos.rs"
        ).read_text(encoding="utf-8")

        self.assertIs(
            False,
            config["app"]["windows"][0].get("create"),
            "The main window is created explicitly in Rust setup.",
        )
        self.assertIn("fn ensure_main_window", lib_rs)
        self.assertIn(".setup(move |app|", lib_rs)
        setup_block = lib_rs[lib_rs.index(".setup(move |app|") : lib_rs.index(".invoke_handler")]
        self.assertIn("ensure_main_window(app)?", setup_block)
        self.assertIn("let context = tauri::generate_context!();", lib_rs)
        self.assertIn(".build(context)", lib_rs)
        self.assertNotIn("tauri::RunEvent::Ready", lib_rs)
        self.assertIn("set_activation_policy(tauri::ActivationPolicy::Regular)", platform_macos_rs)
        self.assertIn("app.show()", platform_macos_rs)
        self.assertIn("activate_macos_app()", lib_rs)
        self.assertIn("activateIgnoringOtherApps:", platform_macos_rs)
        self.assertIn("WebviewWindowBuilder::from_config", lib_rs)
        self.assertIn("window.set_focusable(true)", lib_rs)
        self.assertIn("window.center()", lib_rs)
        self.assertIn("window.unminimize()", lib_rs)
        self.assertIn("window.show()", lib_rs)
        self.assertIn("window.set_focus()", lib_rs)
        self.assertIn("window.is_visible()", lib_rs)

    def test_macos_info_plist_does_not_request_carbon_launch_mode(self):
        with (REPO_ROOT / "src-tauri" / "Info.plist").open("rb") as source:
            plist = plistlib.load(source)

        self.assertIn("LSRequiresCarbon", plist)
        self.assertIs(plist["LSRequiresCarbon"], False)


if __name__ == "__main__":
    unittest.main()
