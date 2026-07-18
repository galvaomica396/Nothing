from __future__ import annotations

import importlib.util
import json
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "e2e_tauri_local_smoke.py"


def load_smoke_module():
    spec = importlib.util.spec_from_file_location("e2e_tauri_local_smoke", SMOKE_SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class TauriLocalSmokeTests(unittest.TestCase):
    def write_app_info(self, bundle_root: Path, bundle_id: str, name: str = "Nothing") -> None:
        contents = bundle_root / "Contents"
        contents.mkdir(parents=True, exist_ok=True)
        (contents / "Info.plist").write_bytes(
            plistlib.dumps(
                {
                    "CFBundleIdentifier": bundle_id,
                    "CFBundleName": name,
                    "CFBundleExecutable": "tauri_frontend",
                }
            )
        )

    def test_macos_process_labels_include_bundle_executable_product_and_window_title(self) -> None:
        smoke = load_smoke_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_path = root / "src-tauri/target/release/bundle/macos/Nothing.app"
            executable = app_path / "Contents/MacOS/tauri_frontend"
            config_path = root / "src-tauri/tauri.conf.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "productName": "Nothing",
                        "app": {"windows": [{"label": "main", "title": "Nothing"}]},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            labels = smoke.macos_process_labels(root, app_path, executable)

        self.assertEqual(
            ["Nothing", "tauri_frontend"],
            labels,
            "Labels should be config-aware and de-duplicated for System Events lookup.",
        )

    def test_macos_zero_window_failure_reports_labels_and_window_details(self) -> None:
        smoke = load_smoke_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_path = root / "Nothing.app"
            macos_dir = app_path / "Contents/MacOS"
            macos_dir.mkdir(parents=True)
            executable = macos_dir / "tauri_frontend"
            executable.write_text("#!/bin/sh\nsleep 10\n", encoding="utf-8")
            executable.chmod(0o755)

            with (
                mock.patch.object(smoke, "pids_for_process_name", side_effect=[set(), {1234}]),
                mock.patch.object(smoke, "macos_process_labels", return_value=["Nothing", "tauri_frontend"]),
                mock.patch.object(smoke, "macos_window_snapshot", return_value=(0, "tauri_frontend windows=0")),
                mock.patch.object(smoke.subprocess, "run") as run_mock,
            ):
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = ""
                run_mock.return_value.stderr = ""

                result = smoke.run_macos_app_smoke(root, app_path, executable, 0.01)

        self.assertEqual("fail", result["status"])
        self.assertEqual(["Nothing", "tauri_frontend"], result["labels"])
        self.assertEqual("tauri_frontend windows=0", result["window_details"])
        self.assertIn("표시 가능한 창", result["error"])

    def test_coregraphics_window_snapshot_parses_renderable_window_count(self) -> None:
        smoke = load_smoke_module()

        with mock.patch.object(smoke.subprocess, "run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = (
                "1|Nothing cg_windows=1 id=42 name= bounds={X=80,Y=30,Width=1280,Height=806}\n"
            )
            run_mock.return_value.stderr = ""

            count, details = smoke.macos_cg_window_snapshot(["Nothing"])

        self.assertEqual(1, count)
        self.assertIn("cg_windows=1", details)
        self.assertIn("id=42", details)

    def test_coregraphics_window_capture_uses_largest_renderable_window_id(self) -> None:
        smoke = load_smoke_module()
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "native.png"

            with (
                mock.patch.object(smoke.platform, "system", return_value="Darwin"),
                mock.patch.object(smoke.subprocess, "run") as run_mock,
            ):
                run_mock.side_effect = [
                    mock.Mock(
                        returncode=0,
                        stdout="42|Nothing id=42 area=1031680 bounds={X=80,Y=30,Width=1280,Height=806}\n",
                        stderr="",
                    ),
                    mock.Mock(returncode=0, stdout="", stderr=""),
                ]

                result = smoke.macos_cg_window_capture(["Nothing"], out_path)

        self.assertEqual("pass", result["status"])
        self.assertEqual("42", result["window_id"])
        self.assertEqual(str(out_path), result["screenshot"])
        self.assertEqual(["screencapture", "-x", "-l", "42", str(out_path)], run_mock.call_args_list[1].args[0])

    def test_coregraphics_snapshot_uses_renderable_window_bounds(self) -> None:
        script = SMOKE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(".optionAll", script)
        self.assertIn("width >= 200 && height >= 120 && alpha > 0", script)
        self.assertNotIn(".optionOnScreenOnly", script)

    def test_macos_duplicate_bundle_id_report_distinguishes_disabled_backups(self) -> None:
        smoke = load_smoke_module()
        bundle_id = "io.github.galvaomica.nothing"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            active_app = root / "bundle/macos/Nothing.app"
            old_app = root / ".omx/disabled-duplicate-apps/run/문서 마스킹 도구.disabled-bundle"
            download_backup = root / ".omx/disabled-duplicate-apps/run/Nothing 2.disabled-bundle"
            self.write_app_info(active_app, bundle_id)
            self.write_app_info(old_app, bundle_id, "문서 마스킹 도구")
            self.write_app_info(download_backup, bundle_id)

            report = smoke.macos_bundle_id_report([root], bundle_id)

        self.assertEqual([str(active_app)], report["active_apps"])
        self.assertEqual(
            sorted([str(old_app), str(download_backup)]),
            sorted(report["disabled_backups"]),
        )
        self.assertFalse(report["ambiguous"])
        self.assertEqual("single-active-app", report["status"])

    def test_computer_use_diagnosis_reports_attach_blockers(self) -> None:
        smoke = load_smoke_module()

        ambiguous = smoke.computer_use_attach_diagnosis(
            active_app_count=2,
            disabled_backup_count=0,
            cg_window_count=0,
            ax_window_count=0,
            computer_use_results=[{"app": "bundle-id", "result": "Ambiguous app identifier"}],
        )
        visible_without_ax = smoke.computer_use_attach_diagnosis(
            active_app_count=1,
            disabled_backup_count=3,
            cg_window_count=1,
            ax_window_count=0,
            computer_use_results=[{"app": "Nothing.app", "result": "cgWindowNotFound"}],
        )
        attach_failure = smoke.computer_use_attach_diagnosis(
            active_app_count=1,
            disabled_backup_count=0,
            cg_window_count=0,
            ax_window_count=0,
            computer_use_results=[{"app": "Nothing.app", "result": "remoteConnection"}],
        )

        self.assertEqual("ambiguous-bundle-id", ambiguous["status"])
        self.assertEqual("visible-cgwindow-without-accessibility-window", visible_without_ax["status"])
        self.assertIn("cgWindowNotFound", visible_without_ax["computer_use_summary"])
        self.assertEqual("computer-use-attach-failed", attach_failure["status"])

    def test_main_writes_bundle_scan_report_when_bundle_id_is_supplied(self) -> None:
        smoke = load_smoke_module()
        bundle_id = "io.github.galvaomica.nothing"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_path = root / "src-tauri/target/release/bundle/macos/Nothing.app"
            executable = app_path / "Contents/MacOS/tauri_frontend"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            self.write_app_info(app_path, bundle_id)
            self.write_app_info(root / ".omx/disabled-duplicate-apps/old/Nothing.disabled-bundle", bundle_id)
            out_path = root / "diagnosis.json"

            with (
                mock.patch.object(smoke, "run_smoke", return_value={"status": "pass", "scope": "mock"}),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "e2e_tauri_local_smoke.py",
                        "--repo-root",
                        str(root),
                        "--app-path",
                        str(app_path),
                        "--bundle-id",
                        bundle_id,
                        "--bundle-search-root",
                        str(root),
                        "--out",
                        str(out_path),
                    ],
                ),
            ):
                code = smoke.main()

            payload = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(0, code)
        self.assertEqual("single-active-app", payload["bundle_report"]["status"])
        self.assertEqual([str(app_path.resolve())], payload["bundle_report"]["active_apps"])
        self.assertEqual(1, payload["bundle_report"]["disabled_backup_count"])

    def test_main_writes_attach_diagnosis_for_visible_cgwindow_without_ax_window(self) -> None:
        smoke = load_smoke_module()
        bundle_id = "io.github.galvaomica.nothing"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_path = root / "Nothing.app"
            executable = app_path / "Contents/MacOS/tauri_frontend"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            self.write_app_info(app_path, bundle_id)
            out_path = root / "diagnosis.json"

            with (
                mock.patch.object(smoke, "run_smoke", return_value={"status": "pass", "scope": "mock"}),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "e2e_tauri_local_smoke.py",
                        "--repo-root",
                        str(root),
                        "--app-path",
                        str(app_path),
                        "--bundle-id",
                        bundle_id,
                        "--bundle-search-root",
                        str(root),
                        "--computer-use-result",
                        "Nothing.app=cgWindowNotFound",
                        "--cg-window-count",
                        "2",
                        "--ax-window-count",
                        "0",
                        "--out",
                        str(out_path),
                    ],
                ),
            ):
                code = smoke.main()

            payload = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(0, code)
        self.assertEqual(
            "visible-cgwindow-without-accessibility-window",
            payload["attach_diagnosis"]["status"],
        )
        self.assertIn("cgWindowNotFound", payload["attach_diagnosis"]["computer_use_summary"])

    def test_main_writes_attach_diagnosis_for_full_path_attach_failure_even_with_single_active_app(self) -> None:
        smoke = load_smoke_module()
        bundle_id = "io.github.galvaomica.nothing"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_path = root / "Nothing.app"
            executable = app_path / "Contents/MacOS/tauri_frontend"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            self.write_app_info(app_path, bundle_id)
            out_path = root / "diagnosis.json"

            with (
                mock.patch.object(smoke, "run_smoke", return_value={"status": "pass", "scope": "mock"}),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "e2e_tauri_local_smoke.py",
                        "--repo-root",
                        str(root),
                        "--app-path",
                        str(app_path),
                        "--bundle-id",
                        bundle_id,
                        "--bundle-search-root",
                        str(root),
                        "--computer-use-result",
                        f"{app_path}=remoteConnection",
                        "--cg-window-count",
                        "0",
                        "--ax-window-count",
                        "0",
                        "--out",
                        str(out_path),
                    ],
                ),
            ):
                code = smoke.main()

            payload = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(0, code)
        self.assertEqual("single-active-app", payload["bundle_report"]["status"])
        self.assertEqual("computer-use-attach-failed", payload["attach_diagnosis"]["status"])
        self.assertIn("remoteConnection", payload["attach_diagnosis"]["computer_use_summary"])

    def test_native_acceptance_report_is_partial_until_real_workflow_is_proven(self) -> None:
        smoke = load_smoke_module()

        report = smoke.native_gui_acceptance_report(
            [
                {"name": "computer_use_attached", "status": "pass", "evidence": "get_app_state(Nothing)"},
                {"name": "canvas_workspace_opened", "status": "pass", "evidence": "PDF 작업창 열기"},
                {"name": "fixture_pdf_loaded", "status": "pass", "evidence": "QA 샘플 PDF 로드 완료"},
                {"name": "manual_mask_box_created", "status": "blocked", "evidence": "computer-use drag primitive unavailable"},
            ]
        )

        self.assertEqual("partial", report["status"])
        self.assertIn("computer_use_attached", report["proven"])
        self.assertIn("manual_mask_box_created", report["blocked"])
        self.assertIn("input_pdf_selected_via_os_picker", report["not_proven"])
        self.assertIn("final_save_completed", report["not_proven"])

    def test_native_acceptance_report_rejects_final_save_without_preview_prerequisite(self) -> None:
        smoke = load_smoke_module()

        report = smoke.native_gui_acceptance_report(
            [
                {"name": "computer_use_attached", "status": "pass"},
                {"name": "canvas_workspace_opened", "status": "pass"},
                {"name": "final_save_completed", "status": "pass", "evidence": "최종 저장 완료"},
            ]
        )

        self.assertEqual("partial", report["status"])
        self.assertNotIn("final_save_completed", report["proven"])
        self.assertIn("final_save_completed", report["blocked"])
        self.assertIn("manual_preview_applied", report["not_proven"])

    def test_main_writes_native_acceptance_actions(self) -> None:
        smoke = load_smoke_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_path = root / "Nothing.app"
            executable = app_path / "Contents/MacOS/tauri_frontend"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            self.write_app_info(app_path, "io.github.galvaomica.nothing")
            out_path = root / "native.json"

            with (
                mock.patch.object(smoke, "run_smoke", return_value={"status": "pass", "scope": "mock"}),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "e2e_tauri_local_smoke.py",
                        "--repo-root",
                        str(root),
                        "--app-path",
                        str(app_path),
                        "--native-action",
                        "computer_use_attached=pass::get_app_state(Nothing)",
                        "--native-action",
                        "manual_mask_box_created=blocked::drag primitive unavailable",
                        "--out",
                        str(out_path),
                    ],
                ),
            ):
                code = smoke.main()

            payload = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(0, code)
        self.assertEqual("partial", payload["native_gui_acceptance"]["status"])
        self.assertIn("computer_use_attached", payload["native_gui_acceptance"]["proven"])
        self.assertIn("manual_mask_box_created", payload["native_gui_acceptance"]["blocked"])


if __name__ == "__main__":
    unittest.main()
