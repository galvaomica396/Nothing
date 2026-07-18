import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
APP_VERSION = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))["version"]
WINDOWS_RELEASE_PREFIX = f"Nothing-{APP_VERSION}-windows-x64"
MACOS_RELEASE_PREFIX = f"Nothing-{APP_VERSION}-macos-arm64"
ARCHITECTURE_DOC = REPO_ROOT / "docs" / "ARCHITECTURE.md"


def legacy_typescript_sources() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "src" / "legacy").rglob("*.ts"))
    )


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

    def test_windows_workflow_uses_current_app_release_names(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")

        self.assertIn(f"{WINDOWS_RELEASE_PREFIX}.exe", workflow)
        self.assertIn(f"{WINDOWS_RELEASE_PREFIX}-setup.exe", workflow)
        self.assertIn(f"{WINDOWS_RELEASE_PREFIX}-portable.zip", workflow)
        self.assertIn(f"{WINDOWS_RELEASE_PREFIX}-manifest.json", workflow)
        self.assertNotIn(f"{WINDOWS_RELEASE_PREFIX}-bundle.zip", workflow)
        self.assertNotIn("full Windows bundle zip", workflow)
        self.assertNotIn(f"makiiing-v2-{APP_VERSION}-windows-x64", workflow)
        self.assertIn('$nsisRoot = Join-Path $bundleRoot "nsis"', workflow)
        self.assertIn('"masking_context.py"', workflow)
        self.assertIn('"masking_runtime\\masking_context.py"', workflow)
        self.assertNotIn("hwpx_masking.py", workflow)
        self.assertNotIn("Document-Masker-Tauri-windows-x64", workflow)

    def test_macos_workflow_uploads_current_app_release_names(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "build-macos.yml").read_text(encoding="utf-8")

        self.assertIn("runs-on: macos-15", workflow)
        self.assertIn("actions/checkout@v6", workflow)
        self.assertIn("actions/setup-node@v6", workflow)
        self.assertIn("actions/setup-python@v6", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertIn("node-version: 24", workflow)
        self.assertIn("npm run tauri build -- --bundles app", workflow)
        # v3.0.0 회귀 방지: 번들 ad-hoc 서명·검증 단계가 빠지면 다운로드된 앱이
        # Gatekeeper "손상됨"으로 차단된다 (Sealed Resources 부재).
        self.assertIn("Ad-hoc sign macOS bundle", workflow)
        self.assertIn('codesign --force --deep --sign - "$app_bundle"', workflow)
        self.assertIn('codesign --verify --deep --strict "$app_bundle"', workflow)
        self.assertIn(f"{MACOS_RELEASE_PREFIX}-app.zip", workflow)
        self.assertIn(f"{MACOS_RELEASE_PREFIX}-manifest.json", workflow)
        self.assertIn("macOS packaged desktop launch smoke", workflow)
        self.assertIn("macOS release asset roundtrip smoke", workflow)
        self.assertIn("scripts/e2e_tauri_local_smoke.py", workflow)
        self.assertIn("Contents/Resources/masking_runtime/bin/masking_engine", workflow)
        self.assertNotIn("hwpx_masking.py", workflow)

    def test_windows_workflow_pins_vs2026_runner_and_node24_actions(self):
        workflow = (REPO_ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")

        self.assertIn("runs-on: windows-2025-vs2026", workflow)
        self.assertNotIn("runs-on: windows-2025\n", workflow)
        self.assertNotIn("runs-on: windows-latest", workflow)
        self.assertIn("actions/checkout@v6", workflow)
        self.assertIn("actions/setup-node@v6", workflow)
        self.assertIn("actions/setup-python@v6", workflow)
        self.assertIn("actions/upload-artifact@v7", workflow)
        self.assertNotIn("FORCE_JAVASCRIPT_ACTIONS_TO_NODE24", workflow)
        self.assertIn("node-version: 24", workflow)
        self.assertNotIn("node-version: 20", workflow)

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

    def test_packaged_manual_boxes_use_masking_engine_not_system_python(self):
        lib_rs = (REPO_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        engine_entry = (REPO_ROOT / "scripts" / "masking_engine_entry.py").read_text(encoding="utf-8")

        self.assertIn('packaged.arg("--manual-boxes")', lib_rs)
        self.assertIn("runtime.masking_engine.clone()", lib_rs)
        self.assertIn("resolve_python(&root)", lib_rs)
        self.assertIn('parser.add_argument("--manual-boxes"', engine_entry)
        self.assertIn("def run_manual_boxes", engine_entry)

    def test_packaged_engine_rejects_raw_text_preview(self):
        engine_entry = (REPO_ROOT / "scripts" / "masking_engine_entry.py").read_text(encoding="utf-8")
        runner = (REPO_ROOT / "scripts" / "run_masking_pipeline.py").read_text(encoding="utf-8")
        frontend_sources = legacy_typescript_sources()

        self.assertNotIn("def build_preview_texts", engine_entry)
        self.assertNotIn("extract_result_text_for_preview", engine_entry)
        self.assertIn('raise ValueError("RAW_TEXT_PREVIEW_REJECTED")', engine_entry)
        self.assertIn('"raw_text_returned": False', engine_entry)
        self.assertNotIn("def build_preview_texts", runner)
        self.assertNotIn("extract_result_text_for_preview", runner)
        self.assertIn('raise ValueError("RAW_TEXT_PREVIEW_REJECTED")', runner)
        self.assertIn('"raw_text_returned": False', runner)
        self.assertIn("sys.path.insert(0, str(repo_root))", runner)
        # 텍스트 미리보기는 계속 끈다. 사용자가 별도 선택한 경우에만 원문이 아닌
        # 비식별 TXT 산출물 모드가 전달된다.
        self.assertNotIn("outputArtifactsEl", frontend_sources)
        self.assertIn("output_artifacts: maskingOutputArtifacts(settingsExportMaskedTextEl.checked)", frontend_sources)
        self.assertIn("return_text_preview: false", frontend_sources)

    def test_masking_engine_import_does_not_require_tkinter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shadow = Path(tmpdir) / "tkinter.py"
            shadow.write_text("raise ModuleNotFoundError(\"No module named '_tkinter'\")\n", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{tmpdir}{os.pathsep}{REPO_ROOT}"
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
            )

        self.assertEqual("", result.stderr)
        self.assertEqual(0, result.returncode)
        self.assertIn(APP_VERSION, result.stdout)

    def test_masking_engine_cli_entry_does_not_start_legacy_gui(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            shadow = Path(tmpdir) / "tkinter.py"
            shadow.write_text("raise ModuleNotFoundError(\"No module named '_tkinter'\")\n", encoding="utf-8")
            env = os.environ.copy()
            env["PYTHONPATH"] = f"{tmpdir}{os.pathsep}{REPO_ROOT}"
            result = subprocess.run(
                [sys.executable, "document_masker_ocr_gui.py"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual("", result.stderr)
        self.assertEqual(0, result.returncode)

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
        legacy_sources = legacy_typescript_sources()

        self.assertIs(
            False,
            config["app"]["withGlobalTauri"],
            "Desktop builds should use imported Tauri APIs instead of exposing window.__TAURI__.",
        )
        self.assertIn('from "@tauri-apps/api/core"', legacy_sources)
        self.assertIn("__TAURI_INTERNALS__", legacy_sources)

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
        self.assertIn(".setup(|app|", lib_rs)
        setup_block = lib_rs[lib_rs.index(".setup(|app|") : lib_rs.index(".invoke_handler")]
        self.assertIn("ensure_main_window(app)?", setup_block)
        self.assertIn(".build(tauri::generate_context!())", lib_rs)
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
        plist = (REPO_ROOT / "src-tauri" / "Info.plist").read_text(encoding="utf-8")

        self.assertIn("<key>LSRequiresCarbon</key>", plist)
        self.assertIn("<false/>", plist)


if __name__ == "__main__":
    unittest.main()
