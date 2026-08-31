from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from test_frontend_state_helpers import run_node_helper


REPO_ROOT = Path(__file__).resolve().parents[1]
QA_SAVE_FLOW = REPO_ROOT / "scripts" / "qa_save_flow.mjs"


class QaSaveFlowPublicReceiptContractTests(unittest.TestCase):
    def test_node_parser_accepts_the_public_harness(self) -> None:
        completed = subprocess.run(
            ["node", "--check", str(QA_SAVE_FLOW)],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_public_mode_fails_closed_when_packaged_child_cannot_launch(self) -> None:
        completed = subprocess.run(
            [
                "node", str(QA_SAVE_FLOW),
                "--scenario", "public-document-plumbing",
                "--native-app-path", "/definitely/not/a/packaged-app",
                "--receipt-nonce", "n" * 32,
                "--threshold-artifact", "/definitely/not/thresholds.json",
                "--threshold-digest", "a" * 64,
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("PUBLIC-DOCUMENT FAIL", completed.stdout)

    def test_public_mode_rejects_caller_authored_receipt_flags(self) -> None:
        completed = subprocess.run(
            [
                "node", str(QA_SAVE_FLOW),
                "--scenario", "public-document-plumbing",
                "--native-lifecycle-receipt", "forged.json",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(0, completed.returncode)
        self.assertIn("QA_CLI_UNKNOWN_ARGUMENT", completed.stderr)

    def test_receipt_regressions_cover_fully_resigned_identity_and_evidence_attacks(self) -> None:
        source = QA_SAVE_FLOW.read_text(encoding="utf-8")
        self.assertIn("function sealPublicReceipt(receipt)", source)
        self.assertIn("fullyResignedForgery", source)
        self.assertIn("evidenceSubstitution", source)
        self.assertIn("const replay =", source)
        self.assertIn("requestEvidence: action.requestEvidence", source)
        self.assertIn("resultEvidence: action.resultEvidence", source)
        self.assertIn('if (isHash(value)) return true;', source)
        self.assertIn('publicReceiptPiiSafe("0".repeat(64))', source)
        self.assertIn('!publicReceiptPiiSafe("01012345678")', source)

    def test_browser_matrix_adds_public_unresolved_confirm_save_as_scenario_15(self) -> None:
        source = QA_SAVE_FLOW.read_text(encoding="utf-8")
        self.assertIn('id: "unresolved-review-confirm-save"', source)
        self.assertIn("publicConfirmSave: true", source)
        self.assertIn('request?.warningsConfirmed !== true', source)
        self.assertIn('cmd === "finalize_masking_run"', source)
        self.assertIn("const finalizedMaskCount = new Set(publicManifest.occurrences", source)
        self.assertIn("occurrenceCount: finalizedMaskCount", source)
        self.assertIn("appliedMaskCount: finalizedMaskCount", source)

    def test_unresolved_geometry_fixture_keeps_one_confirm_save_warning(self) -> None:
        completed = subprocess.run(
            [
                "node",
                "--input-type=module",
                "--eval",
                "import { unresolvedGeometryManifestForQa } from './scripts/qa_tauri_mock.mjs';"
                "console.log(JSON.stringify(unresolvedGeometryManifestForQa()));",
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        manifest = json.loads(completed.stdout)
        result = run_node_helper(
            "src/features/save-gate/saveGate.ts",
            "(() => {"
            f"const manifest = {json.dumps(manifest)};"
            "const session = loadModule(path.resolve('src/state/maskingSession.ts'));"
            "const identity = { runId: manifest.runId, originalDocumentHash: manifest.originalDocumentHash, analysisRevision: manifest.analysisRevision, manifestHash: manifest.manifestHash, profile: manifest.profile };"
            "const parsed = session.parseBoundSafeReport({ product_checks: {}, analysisManifest: manifest, reviewQueue: manifest.reviewItems }, identity);"
            "return { parsed, warnings: parsed.ok ? m.publicFinalSaveWarnings({ report: parsed.value }) : [] };"
            "})()",
        )
        self.assertTrue(result["parsed"]["ok"])
        self.assertEqual(
            [
                "미가림 가능성: 결재선 · 2쪽 — 결재란 영역 자동확인 미완료 — 확인하고 저장",
                "미가림 가능성: staff · 2쪽 — 결재란 영역 자동확인 미완료 — 확인하고 저장",
            ],
            result["warnings"],
        )

    def test_real_acceptance_separates_save_failure_from_visual_disclosure(self) -> None:
        source = (REPO_ROOT / "scripts" / "acceptance_real_app.mjs").read_text(encoding="utf-8")
        drive_source = (REPO_ROOT / "src" / "app" / "qaDrive.ts").read_text(encoding="utf-8")

        self.assertIn("function saveDisclosureRequired(state)", source)
        self.assertIn("async function verifyPartialSaveDisclosure", source)
        self.assertIn('if (pendingSave.status !== "PASS")', source)
        self.assertIn("save pipeline ${pendingSave.detail}", source)
        self.assertIn("const committed = finalizationSuccess?.visible === true", source)
        self.assertIn("if (pendingSave.committed)", source)
        self.assertIn('await drive.send("close-success-dialog")', source)
        self.assertIn("re-open an independent session before later checks", source)
        self.assertIn("SCREEN_TARGET_COLOR", source)
        self.assertIn("inspect-target", source)
        self.assertIn("QA_DRIVE_SAVE_FINAL_REQUIRES_CONFIRM_SAVE", drive_source)
        self.assertIn('controller.saveFinalOutput({ warningsConfirmed: true })', drive_source)

if __name__ == "__main__":
    unittest.main()
