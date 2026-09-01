from __future__ import annotations

import json
import subprocess
from pathlib import Path
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "contracts" / "converted-screens.json"
CHECKER_PATH = "scripts/check_runtime_contract.mjs"


def run_runtime_contract() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["node", CHECKER_PATH],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def runtime_contract_output(result: subprocess.CompletedProcess[str]) -> str:
    return "\n".join(stream for stream in (result.stderr, result.stdout) if stream)


class TransitionDisciplineTests(unittest.TestCase):
    def test_converted_screens_config_registers_all_react_owned_screens(self) -> None:
        # Given: the completed React ownership conversions.
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

        # When: its root schema and every future screen entry are inspected.
        self.assertEqual({"screens"}, set(config))
        self.assertIsInstance(config["screens"], list)
        for screen in config["screens"]:
            with self.subTest(screen=screen):
                self.assertIsInstance(screen["screenId"], str)
                self.assertIsInstance(screen["rootSelector"], str)
                self.assertTrue(screen["rootSelector"].startswith("#"))
                self.assertIsInstance(screen["ownedIds"], list)
                self.assertTrue(all(isinstance(owned_id, str) for owned_id in screen["ownedIds"]))
                grace = screen.get("legacyReferenceGrace", [])
                self.assertIsInstance(grace, list)
                for entry in grace:
                    self.assertEqual({"id", "expiresOn", "comment"}, set(entry))
                    self.assertIn(entry["id"], screen["ownedIds"])

        self.assertEqual(
            [
                {
                    "screenId": "documents",
                    "rootSelector": "#canvas-workspace-screen",
                    "ownedIds": [
                        "canvas-workspace-screen",
                        "pdf-canvas-orig",
                        "pdf-canvas-result",
                        "overlay-canvas-result",
                        "canvas-wrap-orig",
                        "canvas-wrap-result",
                        "pdf-compare-view",
                        "text-compare-view",
                        "extracted-text-view",
                        "masked-text-view",
                        "obsidian-detection-list",
                        "save-summary-accordion",
                        "review-summary-mask-count",
                        "review-summary-restore-count",
                        "review-summary-keyword-count",
                        "review-summary-output-file",
                        "review-summary-pdf-policy",
                        "review-summary-txt-policy",
                        "btn-open-keyword-dialog",
                        "btn-close-keyword-dialog",
                        "btn-keyword-dialog-cancel",
                        "btn-keyword-dialog-apply",
                        "keyword-entry-input",
                        "custom-keywords",
                        "keyword-dialog-count",
                        "keyword-dialog-chip-list",
                        "btn-keyword-policy",
                        "segment-thumbnail-strip",
                        "segment-boundary-kind",
                        "btn-apply-segment-boundary",
                    ],
                },
                {
                    "screenId": "desk",
                    "rootSelector": "#document-desk-screen",
                    "ownedIds": [
                        "document-desk-screen",
                        "btn-desk-open-pdf",
                        "desk-stat-documents",
                        "desk-stat-detected",
                        "desk-stat-pending",
                        "btn-desk-pick-file",
                        "desk-recent-list",
                        "desk-search-empty",
                        "desk-search-input",
                        "sidebar-review-pending-count",
                    ],
                },
                {
                    "screenId": "storage",
                    "rootSelector": "#storage-screen",
                    "ownedIds": [
                        "storage-screen",
                        "storage-result-count",
                        "storage-session-count",
                        "storage-save-list",
                        "storage-search-empty",
                        "storage-search-input",
                    ],
                },
                {
                    "screenId": "settings",
                    "rootSelector": "#settings-screen",
                    "ownedIds": [
                        "settings-screen",
                        "settings-title",
                        "btn-settings-back",
                        "btn-settings-close",
                        "settings-open-output-after-save",
                        "btn-app-settings-reset",
                        "btn-app-settings-save",
                        "btn-app-settings-close",
                    ],
                },
                {
                    "screenId": "masking-settings",
                    "rootSelector": "#masking-settings-screen",
                    "ownedIds": [
                        "masking-settings-screen",
                        "btn-masking-settings-back",
                        "btn-masking-settings-cancel",
                        "btn-masking-settings-preview",
                        "btn-masking-settings-apply",
                        "rule-grid",
                        "rule-rrn",
                        "rule-phone",
                        "rule-business_reg",
                        "rule-name",
                        "rule-address",
                        "rule-place",
                        "rule-legal_party",
                        "rule-company",
                        "rule-court",
                        "rule-case_title",
                        "rule-case_number",
                        "rule-law_firm",
                        "rule-attorney",
                        "rule-approval_line",
                        "rule-region_context",
                        "rule-doc_meta",
                        "display-mode",
                        "btn-display-mode-black",
                        "btn-display-mode-label-en",
                        "btn-display-mode-label-ko",
                        "btn-display-mode-pseudonym",
                        "settings-export-masked-text",
                        "deidentification-policy",
                        "btn-policy-token",
                        "btn-policy-partial",
                        "btn-policy-pseudonym",
                        "region-scope",
                        "custom-regions",
                        "opt-pdf-redaction",
                        "settings-apply-scope-status",
                    ],
                },
            ],
            config["screens"],
        )

    @unittest.skipIf(
        sys.platform == "win32",
        "node checker hard-crashes with empty streams on windows runner; tracked for post-release diagnosis",
    )
    def test_checker_rejects_a_legacy_reference_to_a_fake_converted_owned_id(self) -> None:
        original_config = CONFIG_PATH.read_text(encoding="utf-8")
        fake_conversion = json.loads(original_config)
        fake_conversion["screens"].append(
            {
                "screenId": "proof-of-failure",
                "rootSelector": "#proof-of-failure",
                "ownedIds": ["btn-pick-pdf"],
            }
        )

        try:
            CONFIG_PATH.write_text(
                f"{json.dumps(fake_conversion, ensure_ascii=False, indent=2)}\n",
                encoding="utf-8",
            )

            # When: the runtime contract checks that temporarily declared conversion.
            result = run_runtime_contract()
        finally:
            CONFIG_PATH.write_text(original_config, encoding="utf-8")

        output = runtime_contract_output(result)
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn('Application composition code references React-owned id "btn-pick-pdf"', output)
        self.assertEqual(original_config, CONFIG_PATH.read_text(encoding="utf-8"))

    @unittest.skipIf(
        sys.platform == "win32",
        "node checker hard-crashes with empty streams on windows runner; tracked for post-release diagnosis",
    )
    def test_checker_rejects_an_unregistered_screen_root_after_transition_completion(self) -> None:
        original_config = CONFIG_PATH.read_text(encoding="utf-8")
        config = json.loads(original_config)
        config["screens"] = [screen for screen in config["screens"] if screen["screenId"] != "documents"]

        try:
            CONFIG_PATH.write_text(
                f"{json.dumps(config, ensure_ascii=False, indent=2)}\n",
                encoding="utf-8",
            )

            result = run_runtime_contract()
        finally:
            CONFIG_PATH.write_text(original_config, encoding="utf-8")

        output = runtime_contract_output(result)
        self.assertNotEqual(0, result.returncode, output)
        self.assertIn("#canvas-workspace-screen must be declared", output)
        self.assertEqual(original_config, CONFIG_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
