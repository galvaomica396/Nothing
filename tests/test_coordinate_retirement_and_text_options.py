from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import document_masker_ocr_gui as masker
import masking_extraction


REPO_ROOT = Path(__file__).resolve().parents[1]




class MaskedTextOptionTests(unittest.TestCase):
    def test_legacy_raw_text_modes_fail_closed_to_safe_pdf_report(self) -> None:
        raw_aliases = ["txt만", "txt+pdf", "txt+report", "txt+pdf+report", "TXT만 저장", "TXT + PDF"]
        for alias in raw_aliases:
            with self.subTest(alias=alias):
                self.assertEqual({"pdf", "report"}, masker.resolve_output_artifacts({"output_artifacts": alias}))
        self.assertFalse(any(label in masker.OUTPUT_ARTIFACT_LABELS for label in [
            "TXT만 저장", "TXT + PDF", "TXT + 리포트", "고급: TXT + PDF + 리포트",
        ]))

    def test_legacy_raw_text_mode_never_creates_extracted_or_masked_txt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "input.txt"
            source.write_text("연락처 010-1234-5678", encoding="utf-8")
            extracted_path, masked_path, report_path, _report = masker.process_file(
                str(source),
                outdir=str(root),
                opts={
                    "output_artifacts": "txt만",
                    "deidentification_policy": "token",
                    "pdf_redaction": False,
                    "profile": "legal",
                },
            )

            self.assertIsNone(extracted_path)
            self.assertIsNone(masked_path)
            self.assertIsNotNone(report_path)
            self.assertEqual([], list(root.glob("*.extracted.*.txt")))
            self.assertEqual([], list(root.glob("*.masked.*.txt")))

    def test_runner_boundaries_reject_raw_text_preview_without_echoing_input(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPO_ROOT) as temporary_directory:
            root = Path(temporary_directory)
            source = root / "private_document_name.txt"
            source.write_text("연락처 010-1234-5678", encoding="utf-8")
            opts = json.dumps(
                {
                    "output_artifacts": "pdf_safe_report",
                    "deidentification_policy": "token",
                    "pdf_redaction": False,
                    "return_text_preview": True,
                },
                ensure_ascii=False,
            )
            commands = [
                (
                    "run_masking_pipeline.py",
                    [
                        sys.executable,
                        str(REPO_ROOT / "scripts" / "run_masking_pipeline.py"),
                        "--repo-root",
                        str(REPO_ROOT),
                        "--input",
                        str(source),
                        "--outdir",
                        str(root),
                        "--opts",
                        opts,
                    ],
                    {
                        "event": "pipeline_failure",
                        "schemaVersion": 1,
                        "rawTextReturned": False,
                        "error": {"code": "MASKING_PIPELINE_RAW_TEXT_PREVIEW_REJECTED"},
                    },
                ),
                (
                    "masking_engine_entry.py",
                    [
                        sys.executable,
                        str(REPO_ROOT / "scripts" / "masking_engine_entry.py"),
                        "--repo-root",
                        str(REPO_ROOT),
                        "--input",
                        str(source),
                        "--outdir",
                        str(root),
                        "--opts",
                        opts,
                    ],
                    {
                        "event": "engine_failure",
                        "schemaVersion": 1,
                        "rawTextReturned": False,
                        "error": {"code": "RAW_TEXT_PREVIEW_REJECTED"},
                    },
                ),
            ]
            for script, command, expected_event in commands:
                with self.subTest(script=script):
                    try:
                        completed = subprocess.run(
                            command, capture_output=True, text=True, check=False, timeout=15
                        )
                    except subprocess.TimeoutExpired as error:
                        self.fail(f"{script} timed out: {error}")
                    self.assertNotEqual(0, completed.returncode)
                    self.assertEqual("", completed.stdout)
                    self.assertNotIn(source.name, completed.stderr)
                    self.assertNotIn("010-1234-5678", completed.stderr)
                    self.assertEqual(expected_event, json.loads(completed.stderr))

    def test_masked_text_artifact_never_writes_raw_extracted_text(self) -> None:
        source_text = "연락처 010-1234-5678"
        expected_markers = {
            "token": "[PHONE]",
            "partial": "010-****-5678",
            "pseudonym": "010-0000-",
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "input.txt"
            source.write_text(source_text, encoding="utf-8")
            detection_counts: dict[str, int] = {}
            masked_outputs: dict[str, str] = {}
            for policy, marker in expected_markers.items():
                output_dir = root / policy
                output_dir.mkdir()
                extracted_path, masked_path, _report_path, report = masker.process_file(
                    str(source),
                    outdir=str(output_dir),
                    opts={
                        "profile": "legal",
                        "extract_engine": "auto",
                        "output_artifacts": "pdf_masked_txt_safe_report",
                        "deidentification_policy": policy,
                        "pdf_redaction": False,
                    },
                )

                self.assertIsNone(extracted_path, policy)
                self.assertIsNone(report["outputs"]["extracted_file"], policy)
                self.assertIsNotNone(masked_path, policy)
                masked_text = Path(str(masked_path)).read_text(encoding="utf-8")
                self.assertIn(marker, masked_text, policy)
                self.assertNotIn("010-1234-5678", masked_text, policy)
                self.assertEqual(policy, report["text_deidentification"]["policy"])
                self.assertEqual([Path(str(masked_path)).name], sorted(path.name for path in output_dir.glob("*.txt")), policy)
                detection_counts[policy] = sum(int(value) for value in report["counts"].values())
                masked_outputs[policy] = masked_text

            self.assertGreater(detection_counts["token"], 0)
            self.assertEqual(detection_counts["token"], detection_counts["partial"])
            self.assertEqual(detection_counts["token"], detection_counts["pseudonym"])
            self.assertEqual(3, len(set(masked_outputs.values())))



class PrivacySafeErrorBoundaryTests(unittest.TestCase):
    def test_marker_subprocess_errors_never_echo_stderr(self) -> None:
        canary = "private_document_010-1234-5678.pdf"
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(masking_extraction.shutil, "which", return_value="/usr/bin/marker_single"),
            patch.object(masking_extraction, "_run_cmd", return_value=(1, "", canary)),
        ):
            with self.assertRaisesRegex(RuntimeError, "^EXTRACTION_MARKER_FAILED$") as raised:
                masking_extraction._extract_pdf_with_marker("/allowed/input.pdf", temporary_directory)
            self.assertNotIn(canary, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
