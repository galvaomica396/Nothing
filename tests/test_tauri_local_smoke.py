from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import plistlib
import sys
import subprocess
import tempfile
import unittest
import fitz
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

    @unittest.skipUnless(
        sys.platform == "darwin",
        "macOS zero-window semantics require Darwin",
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
                mock.patch.object(smoke, "macos_process_labels", return_value=["Nothing", "tauri_frontend"]),
                mock.patch.object(smoke, "macos_window_snapshot", return_value=(0, "COREGRAPHICS_NO_MATCH")),
            ):
                result = smoke.run_macos_app_smoke(root, app_path, executable, 0.01)

        self.assertEqual("fail", result["status"])
        self.assertIn("VISIBLE_WINDOW_NOT_OBSERVED", result["diagnostics"])

    def test_coregraphics_window_snapshot_parses_renderable_window_count(self) -> None:
        smoke = load_smoke_module()

        with mock.patch.object(smoke.subprocess, "run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = "1\n"
            run_mock.return_value.stderr = ""

            count, details = smoke.macos_cg_window_snapshot(["Nothing"])

        self.assertEqual(1, count)
        self.assertEqual("COREGRAPHICS_RENDERABLE_WINDOW_OBSERVED", details)

    def test_coregraphics_window_capture_uses_largest_renderable_window_id(self) -> None:
        smoke = load_smoke_module()
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "native.png"

            observed_commands: list[list[str]] = []
            with (
                mock.patch.object(smoke.platform, "system", return_value="Darwin"),
                mock.patch.object(smoke.subprocess, "run") as run_mock,
            ):
                def run(command, **_kwargs):
                    observed_commands.append(command)
                    if command[0] == "screencapture":
                        document = fitz.open()
                        page = document.new_page(width=1, height=1)
                        page.get_pixmap().save(command[-1])
                        document.close()
                    return mock.Mock(
                        returncode=0,
                        stdout="42\n" if command[0] == "swift" else "",
                        stderr="",
                    )
                run_mock.side_effect = run

                result = smoke.macos_cg_window_capture(["Nothing"], out_path)

                self.assertEqual("pass", result["status"])
                self.assertEqual("NATIVE_SCREENSHOT_CAPTURED", result["artifact"])
                self.assertNotIn("window_id", result)
                self.assertEqual(
                    [["swift", "-e", mock.ANY], ["screencapture", "-x", "-l", "42", mock.ANY]],
                    observed_commands,
                )
                self.assertGreater(out_path.stat().st_size, 0)
                self.assertEqual(b"\x89PNG\r\n\x1a\n", out_path.read_bytes()[:8])
                pixmap = fitz.Pixmap(str(out_path))
                self.assertEqual(1, pixmap.width)
                self.assertEqual(1, pixmap.height)

    def test_coregraphics_window_capture_rejects_missing_empty_corrupt_and_failed_screenshots(self) -> None:
        smoke = load_smoke_module()
        cases = {
            "missing": (None, 0),
            "empty": (b"", 0),
            "corrupt": (b"not-a-png", 0),
            "nonzero": (b"\x89PNG\r\n\x1a\n", 1),
        }
        for name, (payload, returncode) in cases.items():
            with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                out_path = Path(tmp) / "native.png"

                def run(command, **_kwargs):
                    if command[0] == "screencapture" and payload is not None:
                        Path(command[-1]).write_bytes(payload)
                    return mock.Mock(
                        returncode=returncode if command[0] == "screencapture" else 0,
                        stdout="42\n" if command[0] == "swift" else "",
                        stderr="CAPTURE_ERROR_CANARY" if command[0] == "screencapture" else "",
                    )

                with (
                    mock.patch.object(smoke.platform, "system", return_value="Darwin"),
                    mock.patch.object(smoke.subprocess, "run", side_effect=run),
                ):
                    result = smoke.macos_cg_window_capture(["Nothing"], out_path)
                self.assertEqual("fail", result["status"])
                self.assertNotIn("CAPTURE_ERROR_CANARY", json.dumps(result, ensure_ascii=False))

    def test_coregraphics_snapshot_reports_no_renderable_window_as_a_failed_observation(self) -> None:
        smoke = load_smoke_module()
        with mock.patch.object(smoke.subprocess, "run") as run_mock:
            run_mock.return_value.returncode = 0
            run_mock.return_value.stdout = "0\n"
            run_mock.return_value.stderr = ""

            count, details = smoke.macos_cg_window_snapshot(["Nothing"])

        self.assertEqual(0, count)
        self.assertEqual("COREGRAPHICS_NO_MATCH", details)
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

    @unittest.skipUnless(
        sys.platform == "darwin",
        "macOS Accessibility attach semantics require Darwin",
    )
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
        self.assertNotIn("computer_use_summary", visible_without_ax)
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
        self.assertEqual(1, payload["bundle_report"]["active_app_count"])
        self.assertEqual(1, payload["bundle_report"]["disabled_backup_count"])

    @unittest.skipUnless(
        sys.platform == "darwin",
        "macOS Accessibility attach semantics require Darwin",
    )
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

        self.assertNotEqual(0, code)
        self.assertEqual(
            "visible-cgwindow-without-accessibility-window",
            payload["attach_diagnosis"]["status"],
        )
        self.assertNotIn("computer_use_summary", payload["attach_diagnosis"])

    @unittest.skipUnless(
        sys.platform == "darwin",
        "macOS Accessibility attach semantics require Darwin",
    )
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

        self.assertNotEqual(0, code, "Acceptance mode must fail when requested computer-use attachment cannot be established.")
        self.assertEqual("single-active-app", payload["bundle_report"]["status"])
        self.assertEqual("computer-use-attach-failed", payload["attach_diagnosis"]["status"])
        self.assertNotIn("computer_use_summary", payload["attach_diagnosis"])

    def test_native_acceptance_rejects_raw_caller_pass_dictionaries(self) -> None:
        smoke = load_smoke_module()
        raw_actions = smoke.parse_native_actions(
            [
                "computer_use_attached=pass::caller assertion",
                "canvas_workspace_opened=pass::caller assertion",
            ]
        )
        report = smoke.native_gui_acceptance_report(raw_actions)
        self.assertEqual("fail", report["status"])
        self.assertEqual([], report["proven"])
        self.assertIn("computer_use_attached", report["blocked"])
        self.assertIn("CALLER_AUTHORED_NATIVE_EVIDENCE_REJECTED", json.dumps(report, ensure_ascii=False))

    def test_non_gating_attach_diagnosis_does_not_count_as_native_acceptance(self) -> None:
        smoke = load_smoke_module()
        diagnosis = smoke.computer_use_attach_diagnosis(
            active_app_count=1,
            disabled_backup_count=0,
            cg_window_count=0,
            ax_window_count=0,
            computer_use_results=[{"app": "Nothing.app", "result": "remoteConnection"}],
        )
        report = smoke.native_gui_acceptance_report(
            [{"name": "computer_use_attached", "status": "diagnostic", "evidence": diagnosis["status"]}]
        )
        self.assertEqual("computer-use-attach-failed", diagnosis["status"])
        self.assertEqual("fail", report["status"])
        self.assertNotIn("computer_use_attached", report["proven"])


    def test_main_rejects_caller_authored_native_pass_claims_without_runtime_receipt(self) -> None:
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

        acceptance = payload["native_gui_acceptance"]
        self.assertEqual(1, code)
        self.assertEqual("fail", acceptance["status"])
        self.assertNotIn("computer_use_attached", acceptance["proven"])
        self.assertIn("computer_use_attached", acceptance["blocked"])
        self.assertIn("CALLER_AUTHORED_NATIVE_EVIDENCE_REJECTED", json.dumps(acceptance, ensure_ascii=False))
    def test_main_public_lifecycle_fails_closed_without_threshold_and_never_leaks_cli_pii(self) -> None:
        smoke = load_smoke_module()
        secret = "홍길동-010-1234-5678"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_path = root / "Nothing.app"
            executable = app_path / "Contents/MacOS/tauri_frontend"
            executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            out_path = root / "public.json"
            with (
                mock.patch.object(smoke, "run_smoke", return_value={"status": "pass", "scope": "mock"}),
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "e2e_tauri_local_smoke.py",
                        "--repo-root", str(root),
                        "--app-path", str(app_path),
                        "--scenario", "public-document-all",
                        "--native-action", f"public_analyze_completed=pass::{secret}",
                        "--out", str(out_path),
                    ],
                ),
            ):
                code = smoke.main()
            payload = json.loads(out_path.read_text(encoding="utf-8"))

        self.assertEqual(1, code)
        self.assertEqual("fail", payload["status"])
        self.assertEqual("fail", payload["public_document_lifecycle"]["status"])
        self.assertNotIn(secret, json.dumps(payload, ensure_ascii=False))
    def test_preexisting_receipt_channel_cannot_authorize_public_lifecycle(self) -> None:
        smoke = load_smoke_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / "tauri_frontend"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            stale_channel = root / "stale.ndjson"
            stale_channel.write_text(
                '{"event":"public_action_receipt","source":"native","receipt":{"forged":true}}\n',
                encoding="utf-8",
            )
            out_path = root / "public.json"
            with (
                mock.patch.object(smoke, "run_smoke", return_value={"status": "pass", "scope": "visible"}),
                mock.patch.object(smoke, "launch_public_native_receipt") as launch,
                mock.patch.object(
                    sys,
                    "argv",
                    [
                        "e2e_tauri_local_smoke.py",
                        "--app-path", str(executable),
                        "--scenario", "public-document-plumbing",
                        "--receipt-nonce", "n" * 32,
                        "--runtime-receipt-channel", str(stale_channel),
                        "--out", str(out_path),
                    ],
                ),
            ):
                code = smoke.main()
            payload = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(1, code)
        launch.assert_not_called()
        self.assertEqual(
            "RUNTIME_RECEIPT_CHANNEL_PREEXISTS",
            payload["public_document_lifecycle"]["receipt_diagnostic"],
        )
        self.assertEqual("pass", payload["runtime"]["status"])

    def test_pii_filter_accepts_sha256_with_phone_like_digit_sequence(self) -> None:
        smoke = load_smoke_module()
        hash_value = "a" * 16 + "01012345678" + "b" * 37

        self.assertEqual(64, len(hash_value))
        self.assertTrue(smoke.pii_safe({"manifestHash": hash_value}))
        self.assertFalse(smoke.pii_safe({"manifestHash": "010-1234-5678"}))

    def test_public_native_receipt_requires_native_source_order_and_hash_bound_identity(self) -> None:
        smoke = load_smoke_module()
        scenario = "public-document-plumbing"
        receipt = {
            "schema": "PublicActionReceiptV1",
            "schemaVersion": 1,
            "scenario": scenario,
            "nonce": "n" * 32,
            "binaryHash": "a" * 64,
            "runId": "run-1",
            "analysisRevision": 1,
            "manifestHash": "b" * 64,
            "thresholdVersion": "threshold-v1",
            "thresholdHash": "c" * 64,
            "thresholdValueHash": "d" * 64,
            "scenarioSteps": [
                "public_analyze_completed",
                "public_unresolved_review_blocked",
                "public_unresolved_review_confirmed",
                "public_stale_revision_blocked",
                "public_stale_manifest_hash_blocked",
                "public_tampered_manifest_blocked",
                "public_forged_resolution_blocked",
                "public_destination_bypass_blocked",
                "public_destination_authorized",
                "public_destination_token_issued",
                "public_threshold_hash_bound",
                "public_atomic_promotion_failure_blocked",
                "public_finalize_promoted",
            ],
            "actions": [],
        }
        result_codes = {
            "public_analyze_completed": "ANALYZE_COMPLETED",
            "public_unresolved_review_blocked": "UNRESOLVED_REVIEW",
            "public_unresolved_review_confirmed": "UNRESOLVED_REVIEW_CONFIRMED",
            "public_stale_revision_blocked": "STALE_OR_FORGED_PUBLIC_REQUEST_REJECTED",
            "public_stale_manifest_hash_blocked": "STALE_OR_FORGED_PUBLIC_REQUEST_REJECTED",
            "public_tampered_manifest_blocked": "PUBLIC_FINALIZE_REJECTED",
            "public_forged_resolution_blocked": "REVIEW_RESOLUTION_REJECTED",
            "public_destination_bypass_blocked": "PUBLIC_FINALIZE_REJECTED",
            "public_destination_authorized": "DESTINATION_AUTHORIZED",
            "public_destination_token_issued": "SAVE_TOKEN_ISSUED",
            "public_threshold_hash_bound": "THRESHOLD_HASH_BOUND",
            "public_atomic_promotion_failure_blocked": "ATOMIC_PROMOTION_FAILED",
            "public_finalize_promoted": "PROMOTED",
        }
        semantic_vector = {
            "public_analyze_completed": ("pass", None),
            "public_unresolved_review_blocked": ("blocked", "UNRESOLVED_REVIEW"),
            "public_unresolved_review_confirmed": ("pass", None),
            "public_stale_revision_blocked": ("blocked", "STALE_OR_FORGED_PUBLIC_REQUEST_REJECTED"),
            "public_stale_manifest_hash_blocked": ("blocked", "STALE_OR_FORGED_PUBLIC_REQUEST_REJECTED"),
            "public_tampered_manifest_blocked": ("blocked", "PUBLIC_FINALIZE_REJECTED"),
            "public_forged_resolution_blocked": ("blocked", "REVIEW_RESOLUTION_REJECTED"),
            "public_destination_bypass_blocked": ("blocked", "PUBLIC_FINALIZE_REJECTED"),
            "public_destination_authorized": ("pass", None),
            "public_destination_token_issued": ("pass", None),
            "public_threshold_hash_bound": ("pass", None),
            "public_atomic_promotion_failure_blocked": ("blocked", "ATOMIC_PROMOTION_FAILED"),
            "public_finalize_promoted": ("pass", None),
        }
        fixture_hash = hashlib.sha256(b"fixture").hexdigest()
        run_id_hash = hashlib.sha256(receipt["runId"].encode()).hexdigest()
        profile_hash = hashlib.sha256(b"profile").hexdigest()
        options_hash = hashlib.sha256(b"options").hexdigest()
        token_hash = hashlib.sha256(b"save-token").hexdigest()
        destination_hash = hashlib.sha256(b"destination").hexdigest()
        review_hash = hashlib.sha256(b"review").hexdigest()
        final_hash = hashlib.sha256(b"final").hexdigest()

        def actual_evidence(name: str) -> tuple[dict[str, object], dict[str, object], int]:
            if name == "public_analyze_completed":
                return (
                    {"inputHash": fixture_hash, "profileHash": profile_hash, "optionsHash": options_hash},
                    {
                        "runIdHash": run_id_hash,
                        "analysisRevision": 1,
                        "manifestHash": "b" * 64,
                        "reviewCount": 10,
                        "nameReviewPending": True,
                        "institutionReviewPending": True,
                        "nonPersonNameCandidateExcluded": True,
                    },
                    19,
                )
            if name in {"public_unresolved_review_blocked", "public_stale_revision_blocked", "public_stale_manifest_hash_blocked", "public_tampered_manifest_blocked", "public_destination_bypass_blocked", "public_atomic_promotion_failure_blocked"}:
                status = {"public_unresolved_review_blocked": "unresolved_review", "public_stale_revision_blocked": "stale_revision", "public_stale_manifest_hash_blocked": "stale_manifest", "public_tampered_manifest_blocked": "tampered_manifest", "public_destination_bypass_blocked": "destination_rejected", "public_atomic_promotion_failure_blocked": "promotion_failed"}[name]
                error = {"public_unresolved_review_blocked": "UNRESOLVED_REVIEW", "public_stale_revision_blocked": "MASKING_SESSION_STALE_ANALYSIS", "public_stale_manifest_hash_blocked": "MASKING_SESSION_STALE_ANALYSIS", "public_tampered_manifest_blocked": "MASKING_SESSION_STALE_ANALYSIS", "public_destination_bypass_blocked": "MASKING_SESSION_DESTINATION_REJECTED", "public_atomic_promotion_failure_blocked": "MASKING_SESSION_PRECOMMIT_RETRYABLE;cause=MASKING_SESSION_PROMOTION_FAILED"}[name]
                return ({"operationKind": "finalize", "runIdHash": run_id_hash, "requestedRevision": 1, "requestedManifestHash": "b" * 64, "saveTokenHash": token_hash, "destinationHash": destination_hash, "bindingCode": "registered"}, {"errorCode": error, "currentRevision": 1, "currentManifestHash": "b" * 64, "statusCode": status}, 1)
            if name == "public_unresolved_review_confirmed":
                return (
                    {
                        "operationKind": "finalize",
                        "runIdHash": run_id_hash,
                        "requestedRevision": 1,
                        "requestedManifestHash": "b" * 64,
                        "saveTokenHash": token_hash,
                        "destinationHash": destination_hash,
                        "bindingCode": "registered",
                        "warningsConfirmed": True,
                    },
                    {
                        "statusCode": "unresolved_review_confirmed",
                        "confirmationStatus": "user_confirmed",
                        "unresolvedReviewCount": 2,
                        "categoryPageEvidence": True,
                        "finalHash": final_hash,
                        "confirmedRunIdHash": run_id_hash,
                    },
                    1,
                )
            if name == "public_forged_resolution_blocked":
                return ({"operationKind": "resolve", "runIdHash": run_id_hash, "requestedRevision": 1, "requestedManifestHash": "b" * 64, "reviewIdHash": review_hash, "resolutionKind": "acknowledge"}, {"errorCode": "MASKING_SESSION_UNKNOWN_REVIEW", "currentRevision": 1, "currentManifestHash": "b" * 64, "statusCode": "unknown_review"}, 1)
            if name == "public_destination_authorized":
                return ({"destinationHash": destination_hash, "manifestHash": "b" * 64, "bindingCode": "public"}, {"saveTokenHash": token_hash, "bindingCode": "registered"}, 1)
            if name == "public_destination_token_issued":
                return ({"manifestHash": "b" * 64, "bindingCode": "registered"}, {"saveTokenHash": token_hash, "nonempty": True}, 1)
            if name == "public_threshold_hash_bound":
                return (
                    {
                        "autoMaskThreshold": 0.85,
                        "reviewThreshold": 0.5,
                        "thresholdHash": "c" * 64,
                        "thresholdValueHash": "d" * 64,
                        "thresholdVersion": "threshold-v1",
                    },
                    {
                        "artifactHash": "c" * 64,
                        "autoMaskThreshold": 0.85,
                        "reviewThreshold": 0.5,
                        "thresholdValueHash": "d" * 64,
                    },
                    1,
                )
            return ({"destinationHash": destination_hash, "manifestHash": "b" * 64}, {"statusCode": "promoted", "finalHash": final_hash}, 1)
        for name in receipt["scenarioSteps"]:
            outcome, error_code = semantic_vector[name]
            actual_request, actual_result, count = actual_evidence(name)
            action = {
                "name": name, "outcome": outcome, "errorCode": error_code,
                "requestEvidence": {
                    "operationCode": name,
                    "fixtureHash": fixture_hash,
                    "actualRequest": actual_request,
                    "requestEvidenceHash": "",
                },
                "resultEvidence": {
                    "resultCode": result_codes[name],
                    "observed": True,
                    "count": count,
                    "actualResult": actual_result,
                    "resultEvidenceHash": "",
                },
                "requestHash": "", "resultHash": "",
            }
            action["requestEvidence"]["requestEvidenceHash"] = smoke.canonical_json_hash(
                action["requestEvidence"]["actualRequest"]
            )
            action["resultEvidence"]["resultEvidenceHash"] = smoke.canonical_json_hash(
                action["resultEvidence"]["actualResult"]
            )
            action["requestHash"] = smoke.receipt_action_hash(receipt, action, "request")
            action["resultHash"] = smoke.receipt_action_hash(receipt, action, "result")
            receipt["actions"].append(action)
        canonical = {key: value for key, value in receipt.items()}
        receipt["canonicalReceiptHash"] = smoke.canonical_json_hash(canonical)
        receipt["receiptAuth"] = smoke.receipt_auth(receipt)

        native_stdout = json.dumps({
            "event": "public_action_receipt", "source": "native", "receipt": receipt,
        }) + "\n"
        self.assertIsNotNone(smoke.public_receipt_from_native_stdout(
            native_stdout, nonce=receipt["nonce"], binary_hash=receipt["binaryHash"], scenario=scenario,
        ))
        self.assertIsNone(smoke.public_receipt_from_native_stdout(
            native_stdout + native_stdout,
            nonce=receipt["nonce"], binary_hash=receipt["binaryHash"], scenario=scenario,
        ))
        self.assertIsNone(smoke.public_receipt_from_native_stdout(
            native_stdout,
            nonce="x" * 32, binary_hash=receipt["binaryHash"], scenario=scenario,
        ))
        self.assertIsNone(smoke.public_receipt_from_native_stdout(
            native_stdout,
            nonce=receipt["nonce"], binary_hash="f" * 64, scenario=scenario,
        ))

        for mutation in (
            lambda item: item.pop("receiptAuth"),
            lambda item: item["actions"].reverse(),
            lambda item: item["actions"][0].pop("resultEvidence"),
            lambda item: item["actions"][0]["requestEvidence"].__setitem__("patientName", "홍길동"),
            lambda item: item["actions"][0]["requestEvidence"].__setitem__("requestEvidenceHash", "0" * 64),
            lambda item: item["actions"][0]["resultEvidence"].__setitem__("observed", False),
            lambda item: item.__setitem__("thresholdValueHash", "e" * 64),
        ):
            malformed = json.loads(json.dumps(receipt))
            mutation(malformed)
            self.assertIsNone(smoke.public_receipt_from_native_stdout(
                json.dumps({"event": "public_action_receipt", "source": "native", "receipt": malformed}) + "\n",
                nonce=receipt["nonce"], binary_hash=receipt["binaryHash"], scenario=scenario,
            ))
        def resign(item: dict[str, object]) -> None:
            for action in item["actions"]:
                action["requestEvidence"]["requestEvidenceHash"] = smoke.canonical_json_hash(
                    action["requestEvidence"]["actualRequest"]
                )
                action["resultEvidence"]["resultEvidenceHash"] = smoke.canonical_json_hash(
                    action["resultEvidence"]["actualResult"]
                )
                action["requestHash"] = smoke.receipt_action_hash(item, action, "request")
                action["resultHash"] = smoke.receipt_action_hash(item, action, "result")
            unsigned = {
                key: value for key, value in item.items()
                if key not in {"canonicalReceiptHash", "receiptAuth"}
            }
            item["canonicalReceiptHash"] = smoke.canonical_json_hash(unsigned)
            item["receiptAuth"] = smoke.receipt_auth(item)

        for mutation in (
            lambda item: item["actions"].reverse(),
            lambda item: item["actions"][0]["requestEvidence"].__setitem__("patientName", "x"),
            lambda item: item["actions"][0]["resultEvidence"].__setitem__("observed", False),
            lambda item: item["actions"][0]["resultEvidence"].__setitem__("count", 0),
            lambda item: item["actions"][0]["resultEvidence"].__setitem__("count", 5),
            lambda item: item["actions"][0]["resultEvidence"]["actualResult"].__setitem__("reviewCount", 9),
            lambda item: item["actions"][0]["resultEvidence"]["actualResult"].__setitem__("nameReviewPending", False),
            lambda item: item["actions"][0]["resultEvidence"]["actualResult"].__setitem__("institutionReviewPending", False),
            lambda item: item["actions"][0]["resultEvidence"]["actualResult"].__setitem__("nonPersonNameCandidateExcluded", False),
            lambda item: item["actions"][0]["requestEvidence"].__setitem__(
                "actualRequest",
                {"operationCode": item["actions"][0]["name"], "observed": True, "count": 1},
            ),
        ):
            forged = json.loads(json.dumps(receipt))
            mutation(forged)
            resign(forged)
            self.assertIsNone(smoke.public_receipt_from_native_stdout(
                json.dumps({"event": "public_action_receipt", "source": "native", "receipt": forged}) + "\n",
                nonce=receipt["nonce"], binary_hash=receipt["binaryHash"], scenario=scenario,
            ))
        self.assertIsNone(smoke.public_receipt_from_native_stdout(
            json.dumps({"event": "public_action_receipt", "source": "browser_mock", "receipt": receipt}) + "\n",
            nonce=receipt["nonce"], binary_hash=receipt["binaryHash"], scenario=scenario,
        ))
    def test_public_observation_evidence_contracts_reject_resigned_semantic_mutations(self) -> None:
        smoke = load_smoke_module()
        fixture_hash = "a" * 64
        receipt = {
            "scenario": "public-document-all",
            "nonce": "n" * 32,
            "binaryHash": "a" * 64,
            "runId": "run-1",
            "analysisRevision": 1,
            "manifestHash": "b" * 64,
            "thresholdVersion": "threshold-v1",
            "thresholdHash": "c" * 64,
            "thresholdValueHash": "d" * 64,
        }
        cases = {
            "public_mixed_boundary_blocked": ({"fixtureHash": fixture_hash, "manifestHash": "b" * 64, "pendingBoundaryCount": 1, "pendingReviewIdHash": "d" * 64}, {"boundaryBlocked": True, "pendingBoundaryCount": 1, "pendingReviewCount": 10}, 1, ("result", "pendingReviewCount", 11)),
            "public_ambiguous_common_only_blocked": ({"fixtureHash": fixture_hash, "manifestHash": "b" * 64, "pendingCommonOnlyCount": 1, "pendingReviewIdHash": "d" * 64}, {"commonOnlyBlocked": True, "pendingCommonOnlyCount": 1, "pendingReviewCount": 1}, 1, ("result", "pendingReviewCount", 0)),
            "public_scan_manual_review_required": ({"inputHash": fixture_hash, "profileHash": "d" * 64, "optionsHash": "e" * 64}, {"scanSegmentCount": 1, "pendingScanReviewCount": 1, "manifestHash": "b" * 64}, 1, ("result", "pendingScanReviewCount", 0)),
            "public_repeated_occurrence_scoped": ({"inputHash": fixture_hash, "duplicateValueHash": "d" * 64, "distinctPageOrRectHash": "e" * 64}, {"duplicateOccurrenceCount": 2, "occurrenceCount": 2, "scoped": True, "manifestHash": "b" * 64}, 2, ("result", "scoped", False)),
            "public_review_cards_resolved": ({"fixtureHash": fixture_hash, "pendingBefore": 10, "manifestHash": "b" * 64}, {"pendingAfter": 0, "resolvedRevision": 2, "resolvedManifestHash": "d" * 64}, 10, ("request", "pendingBefore", 11)),
            "public_manual_combined_resolved": ({"fixtureHash": fixture_hash, "linkedOccurrenceHash": "d" * 64, "neighborRefCount": 1, "pendingReviewCount": 10}, {"manualActionCount": 1, "pendingReviewCount": 0, "linkedOccurrenceHash": "d" * 64, "neighborRefCount": 1, "promotedFinalHash": "e" * 64}, 1, ("request", "pendingReviewCount", 9)),
            "public_legal_advisory_isolated": ({"fixtureHash": fixture_hash, "checkedTagSetHash": "d" * 64, "manifestHash": "b" * 64}, {"matchedCount": 0, "occurrenceCount": 19}, 0, ("result", "occurrenceCount", 18)),
            "public_intrinsic_failure_blocked": ({"fixtureHash": fixture_hash, "sourceBeforeHash": "d" * 64, "sourceAfterHash": "e" * 64}, {"errorCode": "MASKING_SESSION_ORIGINAL_CHANGED", "destinationAbsent": True}, 1, ("result", "destinationAbsent", False)),
            "public_clean_document_verified": ({"fixtureHash": fixture_hash, "inputManifestHash": "b" * 64}, {"sourceHash": fixture_hash, "finalHash": fixture_hash, "occurrenceCount": 0, "pendingReviewCount": 0}, 0, ("result", "finalHash", "d" * 64)),
        }
        result_codes = {
            "public_mixed_boundary_blocked": "MIXED_BOUNDARY_OBSERVED", "public_ambiguous_common_only_blocked": "AMBIGUOUS_COMMON_ONLY_OBSERVED",
            "public_scan_manual_review_required": "SCANNED_GEOMETRY_REVIEW_OBSERVED", "public_repeated_occurrence_scoped": "REPEATED_OCCURRENCE_SCOPE_OBSERVED",
            "public_review_cards_resolved": "REVIEW_RESOLUTION_OBSERVED", "public_manual_combined_resolved": "MANUAL_AND_INTRINSIC_OBSERVED",
            "public_legal_advisory_isolated": "LEGAL_TAGS_ABSENT", "public_intrinsic_failure_blocked": "MASKING_SESSION_ORIGINAL_CHANGED",
            "public_clean_document_verified": "CLEAN_DOCUMENT_HASH_MATCHED",
        }
        for name, (actual_request, actual_result, count, mutation) in cases.items():
            outcome, error_code = smoke.PUBLIC_ACTION_SEMANTICS[name]
            action = {"name": name, "outcome": outcome, "errorCode": error_code,
                "requestEvidence": {"operationCode": name, "fixtureHash": fixture_hash, "actualRequest": actual_request, "requestEvidenceHash": smoke.canonical_json_hash(actual_request)},
                "resultEvidence": {"resultCode": result_codes[name], "observed": True, "count": count, "actualResult": actual_result, "resultEvidenceHash": smoke.canonical_json_hash(actual_result)},
                "requestHash": "", "resultHash": ""}
            action["requestHash"] = smoke.receipt_action_hash(receipt, action, "request")
            action["resultHash"] = smoke.receipt_action_hash(receipt, action, "result")
            self.assertTrue(smoke.valid_action_evidence(receipt, action), name)
            target = action["requestEvidence"]["actualRequest"] if mutation[0] == "request" else action["resultEvidence"]["actualResult"]
            target[mutation[1]] = mutation[2]
            action["requestEvidence"]["requestEvidenceHash"] = smoke.canonical_json_hash(action["requestEvidence"]["actualRequest"])
            action["resultEvidence"]["resultEvidenceHash"] = smoke.canonical_json_hash(action["resultEvidence"]["actualResult"])
            action["requestHash"] = smoke.receipt_action_hash(receipt, action, "request")
            action["resultHash"] = smoke.receipt_action_hash(receipt, action, "result")
            self.assertFalse(smoke.valid_action_evidence(receipt, action), name)
    def test_direct_native_launch_sends_only_request_and_fails_on_child_error_or_mutation(self) -> None:
        smoke = load_smoke_module()
        nonce = "n" * 32
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "tauri_frontend"
            executable.write_bytes(b"packaged-binary")
            executable.chmod(0o755)
            threshold_binding = {
                "thresholdVersion": "threshold-v1",
                "thresholdHash": "a" * 64,
                "thresholdValueHash": "b" * 64,
                "autoMaskThreshold": 0.85,
                "reviewThreshold": 0.5,
            }
            observed: dict[str, object] = {}

            def child_failure(*command: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
                observed["command"] = command
                observed["input"] = kwargs["input"]
                return subprocess.CompletedProcess(command[0], 9, "", "")

            with mock.patch.object(smoke.subprocess, "run", side_effect=child_failure):
                receipt, diagnostic = smoke.launch_public_native_receipt(
                    executable,
                    scenario="public-document-plumbing",
                    nonce=nonce,
                    timeout=1,
                    threshold_binding=threshold_binding,
                )
            self.assertIsNone(receipt)
            self.assertEqual("NATIVE_RECEIPT_NONZERO_EXIT", diagnostic)
            self.assertEqual(([str(executable), "--public-native-qa-stdin"],), observed["command"])
            self.assertEqual(
                {"schemaVersion": 1, "scenario": "public-document-plumbing", "nonce": nonce, **threshold_binding},
                json.loads(str(observed["input"])),
            )

            def mutate_binary(*_command: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
                executable.write_bytes(b"mutated-binary")
                return subprocess.CompletedProcess([], 0, "", "")

            with mock.patch.object(smoke.subprocess, "run", side_effect=mutate_binary):
                receipt, diagnostic = smoke.launch_public_native_receipt(
                    executable,
                    scenario="public-document-plumbing",
                    nonce=nonce,
                    timeout=1,
                    threshold_binding=threshold_binding,
                )
            self.assertIsNone(receipt)
            self.assertEqual("NATIVE_EXECUTABLE_MUTATED", diagnostic)
    def test_threshold_artifact_binds_version_hash_and_value_identity(self) -> None:
        smoke = load_smoke_module()
        receipt = {
            "thresholdVersion": "threshold-v1",
            "thresholdHash": "a" * 64,
            "thresholdValueHash": "b" * 64,
        }
        artifact = {
            "schemaVersion": 1,
            "thresholdVersion": "threshold-v1",
            "thresholdHash": "a" * 64,
            "thresholdValueHash": "b" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            artifact_path = Path(tmp) / "thresholds.json"
            artifact_path.write_text(json.dumps(artifact), encoding="utf-8")
            digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            self.assertTrue(smoke.validate_threshold_artifact(str(artifact_path), digest, receipt))
            for field in ("thresholdHash", "thresholdVersion", "thresholdValueHash"):
                with self.subTest(field=field):
                    mismatched = dict(artifact)
                    mismatched[field] = "e" * 64 if field != "thresholdVersion" else "threshold-v2"
                    artifact_path.write_text(json.dumps(mismatched), encoding="utf-8")
                    self.assertFalse(smoke.validate_threshold_artifact(
                        str(artifact_path),
                        hashlib.sha256(artifact_path.read_bytes()).hexdigest(),
                        receipt,
                    ))


if __name__ == "__main__":
    unittest.main()
