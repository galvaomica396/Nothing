import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LICENSE_FILES = (
    "LICENSE",
    "THIRD_PARTY_NOTICES.md",
    "LICENSE-APACHE-2.0.txt",
)


class DistributionLicenseTests(unittest.TestCase):
    def test_root_notices_cover_direct_runtime_dependencies(self):
        # Given: the public repository's distributable legal notices.
        notices = (REPO_ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")

        # When: the direct frontend, desktop, and Python runtime notices are audited.
        required_notices = (
            "React and React DOM",
            "Copyright (c) Meta Platforms, Inc. and affiliates.",
            "pdfjs-dist",
            "Tauri and @tauri-apps packages",
            "Copyright (c) 2017 - Present Tauri Apps Contributors",
            "serde and serde_json",
            "rfd",
            "Copyright (c) 2022 Bartłomiej Maryńczak",
            "ko-pii 1.15.2",
            "PyMuPDF and PyMuPDF4LLM",
            "data/kr_regions.json",
            "The above copyright notice and this permission notice shall be included",
        )

        # Then: every required attribution and redistribution condition is present.
        for notice in required_notices:
            self.assertIn(notice, notices)
        apache = (REPO_ROOT / "LICENSE-APACHE-2.0.txt").read_text(encoding="utf-8")
        self.assertIn("Apache License", apache)
        self.assertIn("Version 2.0, January 2004", apache)
        self.assertIn("END OF TERMS AND CONDITIONS", apache)

    def test_tauri_bundle_resources_include_all_license_files(self):
        # Given: the Tauri resource mapping used by every desktop bundle.
        config = json.loads((REPO_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))

        # When: the public legal files are resolved into the app resource tree.
        resources = config["bundle"]["resources"]

        # Then: every legal file has a stable licenses/ destination.
        for filename in LICENSE_FILES:
            self.assertEqual(f"licenses/{filename}", resources[f"../{filename}"])

    def test_release_workflows_verify_licenses_in_every_artifact(self):
        # Given: the macOS and Windows release packaging workflows.
        macos = (REPO_ROOT / ".github" / "workflows" / "build-macos.yml").read_text(encoding="utf-8")
        windows = (REPO_ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")

        # When: required-file, manifest, portable, installer, and roundtrip checks are inspected.
        # Then: both workflows name and verify every legal file in their delivered artifacts.
        for filename in LICENSE_FILES:
            self.assertGreaterEqual(macos.count(f"Contents/Resources/licenses/{filename}"), 3)
            self.assertGreaterEqual(windows.count(f'"licenses\\{filename}"'), 2)
        self.assertIn("required_app_files", macos)
        self.assertIn("app_bundle_files", macos)
        self.assertIn("required_portable_files", windows)
        self.assertIn("installer_license_files", windows)
        self.assertIn("7z l -ba $installerExe", windows)
        self.assertIn("7z l -ba $installerPath", windows)


if __name__ == "__main__":
    unittest.main()
