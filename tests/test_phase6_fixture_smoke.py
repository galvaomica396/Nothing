from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fitz


REPO_ROOT = Path(__file__).resolve().parents[1]
ENSURE_FIXTURE_SCRIPT = REPO_ROOT / "scripts" / "ensure_phase6_fixture.py"
FIXTURE_SMOKE_SCRIPT = REPO_ROOT / "scripts" / "e2e_fixture_smoke.py"


def run_json_command(args: list[str]) -> dict[str, object]:
    completed = subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


class Phase6FixtureSmokeTests(unittest.TestCase):
    def test_fixture_generator_creates_non_sensitive_pdf_with_expected_dummy_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "phase6_fixture.pdf"

            result = run_json_command(
                [
                    sys.executable,
                    str(ENSURE_FIXTURE_SCRIPT),
                    "--output",
                    str(output_path),
                    "--force",
                ]
            )

            self.assertEqual(str(output_path.resolve()), result["fixture_path"])
            self.assertTrue(output_path.exists())
            self.assertGreater(int(result["bytes"]), 0)
            self.assertEqual(64, len(str(result["sha256"])))

            doc = fitz.open(output_path)
            try:
                text = "\n".join(page.get_text() for page in doc)
            finally:
                doc.close()
            normalized = text.replace("\xa0", " ")
            self.assertIn("010-0000-0000", normalized)
            self.assertIn("4000-0000-0000-0000", normalized)
            self.assertIn("M00000000", normalized)
            self.assertIn("부산광역시 해운대구 우동 테스트로 0", normalized)

    def test_fixture_smoke_masks_fixture_without_returning_raw_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            fixture_path = work / "phase6_fixture.pdf"

            result = run_json_command(
                [
                    sys.executable,
                    str(FIXTURE_SMOKE_SCRIPT),
                    "--repo-root",
                    str(REPO_ROOT),
                    "--workdir",
                    str(work),
                    "--fixture",
                    str(fixture_path),
                ]
            )

            self.assertEqual("pass", result["status"])
            self.assertFalse(result["raw_text_returned"])
            self.assertTrue(Path(str(result["safe_report_path"])).exists())
            self.assertTrue(Path(str(result["masked_pdf_path"])).exists())
            self.assertFalse(result["extracted_txt_default_saved"])
            self.assertEqual([], result["raw_values_found_in_safe_report"])


if __name__ == "__main__":
    unittest.main()
