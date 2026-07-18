from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

import document_masker_ocr_gui as masker
from pdf_redaction_rendering import insert_pdf_pseudonym_label, pseudonym_text_layout
from privacy_transformers import TransformState, pseudonym_value


def write_matrix_pdf(path: Path) -> None:
    doc = fitz.open()
    try:
        page = doc.new_page(width=420, height=180)
        page.insert_text((32, 52), "Phone 010-0000-0000")
        page.insert_text((32, 84), "Card 4000-0000-0000-0000")
        page.insert_text((32, 116), "Email sample@example.com")
        doc.save(path)
    finally:
        doc.close()


def pdf_text(path: Path) -> str:
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


class OptionMatrixOutputTests(unittest.TestCase):
    def test_pseudonym_layout_fits_truncates_and_uses_korean_font(self) -> None:
        wide = pseudonym_text_layout(fitz.Rect(0, 0, 100, 20), "박지훈")
        narrow = pseudonym_text_layout(fitz.Rect(0, 0, 18, 12), "서울특별시 중구 샘플로 120")
        self.assertIsNotNone(wide)
        self.assertEqual("박지훈", wide.text if wide else "")
        self.assertIsNotNone(narrow)
        self.assertTrue(narrow.text.endswith("…") if narrow else False)
        self.assertEqual(4.0, narrow.fontsize if narrow else 0.0)
        self.assertIsNone(pseudonym_text_layout(fitz.Rect(0, 0, 3, 3), "박지훈"))

        class RecordingPage:
            def __init__(self) -> None:
                self.kwargs: dict[str, object] = {}

            def insert_textbox(self, _rect: object, _text: str, **kwargs: object) -> int:
                self.kwargs = kwargs
                return 1

        page = RecordingPage()
        with patch("pdf_redaction_rendering.korean_pdf_font_file", return_value="/fonts/korean.ttf"):
            self.assertTrue(insert_pdf_pseudonym_label(page, fitz.Rect(0, 0, 100, 20), "박지훈"))
        self.assertEqual("docmaskko", page.kwargs["fontname"])
        self.assertEqual("/fonts/korean.ttf", page.kwargs["fontfile"])
        unavailable_page = RecordingPage()
        with patch("pdf_redaction_rendering.korean_pdf_font_file", return_value=None):
            self.assertFalse(insert_pdf_pseudonym_label(unavailable_page, fitz.Rect(0, 0, 100, 20), "박지훈"))
        self.assertEqual({}, unavailable_page.kwargs)

    def test_pdf_display_modes_create_expected_redaction_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "matrix.pdf"
            write_matrix_pdf(source)

            expected = {
                "black": ("", ("010-0000-0000", "4000-0000-0000-0000")),
                "label_en": ("[PHONE]", ("010-0000-0000", "4000-0000-0000-0000")),
                "label_ko": ("[전화번호]", ("010-0000-0000", "4000-0000-0000-0000")),
            }
            for display_mode, (label, forbidden_values) in expected.items():
                outdir = root / display_mode
                outdir.mkdir()
                _extracted, _masked, _report_path, report = masker.process_file(
                    str(source),
                    outdir=str(outdir),
                    opts={
                        "profile": "official",
                        "extract_engine": "pypdf",
                        "output_artifacts": "pdf+report",
                        "display_mode": display_mode,
                        "pdf_redaction": True,
                    },
                )

                output_file = Path(masker.runtime_manifest_for_report(report)["outputs"]["masked_pdf_file"])
                self.assertTrue(output_file.exists(), display_mode)
                rendered_text = pdf_text(output_file)
                for forbidden in forbidden_values:
                    self.assertNotIn(forbidden, rendered_text)
                if label:
                    self.assertIn(label, rendered_text)

    def test_black_and_labeled_artifact_creates_both_pdf_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "matrix.pdf"
            write_matrix_pdf(source)

            _extracted, _masked, _report_path, report = masker.process_file(
                str(source),
                outdir=str(root),
                opts={
                    "profile": "official",
                    "extract_engine": "pypdf",
                    "output_artifacts": "pdf_black_and_labeled",
                    "display_mode": "black",
                    "pdf_redaction": True,
                },
            )

            runtime_outputs = masker.runtime_manifest_for_report(report)["outputs"]
            black_pdf = Path(runtime_outputs["masked_pdf_file"])
            labeled_pdf = Path(runtime_outputs["labeled_pdf_file"])
            self.assertTrue(black_pdf.exists())
            self.assertTrue(labeled_pdf.exists())
            self.assertNotIn("010-0000-0000", pdf_text(black_pdf))
            self.assertIn("[PHONE]", pdf_text(labeled_pdf))

    def test_text_deidentification_options_only_affect_text_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "matrix.pdf"
            write_matrix_pdf(source)

            policies = {
                "token": "[PHONE]",
                "partial": "010-****-0000",
                "pseudonym": "010-0000-",
            }
            for policy, expected_text in policies.items():
                outdir = root / policy
                outdir.mkdir()
                _extracted, masked_path, _report_path, report = masker.process_file(
                    str(source),
                    outdir=str(outdir),
                    opts={
                        "profile": "official",
                        "extract_engine": "pypdf",
                        "output_artifacts": "pdf_masked_txt_safe_report",
                        "display_mode": "black",
                        "deidentification_policy": policy,
                        "pdf_redaction": True,
                    },
                )

                self.assertIsNotNone(masked_path, policy)
                masked_text = Path(str(masked_path)).read_text(encoding="utf-8")
                self.assertIn(expected_text, masked_text)
                self.assertNotIn("sample@example.com", masked_text)
                self.assertEqual(policy, report["text_deidentification"]["policy"])
                self.assertEqual("text_preview_and_txt_output_only", report["text_deidentification"]["scope"])
                self.assertTrue(Path(masker.runtime_manifest_for_report(report)["outputs"]["masked_pdf_file"]).exists())

    def test_pseudonym_pdf_matches_masked_txt_and_hides_original_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "matrix.pdf"
            write_matrix_pdf(source)
            state = TransformState()
            expected_phone = pseudonym_value("PHONE", "010-0000-0000", state)
            expected_email = pseudonym_value("EMAIL", "sample@example.com", state)
            rendered_runs: list[str] = []

            for run_number in (1, 2):
                outdir = root / f"pseudonym-{run_number}"
                outdir.mkdir()
                _extracted, masked_path, _report_path, report = masker.process_file(
                    str(source),
                    outdir=str(outdir),
                    opts={
                        "profile": "official",
                        "extract_engine": "pypdf",
                        "output_artifacts": "pdf_masked_txt_safe_report",
                        "display_mode": "pseudonym",
                        "deidentification_policy": "pseudonym",
                        "pdf_redaction": True,
                    },
                )

                self.assertIsNotNone(masked_path)
                masked_text = Path(str(masked_path)).read_text(encoding="utf-8")
                output_file = Path(masker.runtime_manifest_for_report(report)["outputs"]["masked_pdf_file"])
                rendered_text = pdf_text(output_file)
                rendered_runs.append(rendered_text)
                for expected in (expected_phone, expected_email):
                    self.assertIn(expected, masked_text)
                    self.assertIn(expected, rendered_text)
                for original in ("010-0000-0000", "sample@example.com"):
                    self.assertNotIn(original, masked_text)
                    self.assertNotIn(original, rendered_text)
                serialized_report = json.dumps(report, ensure_ascii=False)
                self.assertNotIn(expected_phone, serialized_report)
                self.assertNotIn(expected_email, serialized_report)
                self.assertEqual("pseudonym", report["rules"]["display_mode"])
                self.assertEqual("applied", report["pdf_redaction"]["status"])
                self.assertTrue(report["pdf_redaction"]["verification"]["verified"])
                self.assertEqual(0, report["pdf_redaction"]["verification"]["residual_hits"])

            self.assertEqual(rendered_runs[0], rendered_runs[1])

    def test_pseudonym_pdf_never_reinserts_a_self_collision_original(self) -> None:
        original = "[RRN_8710]"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "collision.pdf"
            output = root / "collision-masked.pdf"
            doc = fitz.open()
            try:
                page = doc.new_page(width=420, height=120)
                page.insert_text((32, 60), f"Identifier {original}")
                doc.save(source)
            finally:
                doc.close()

            result = masker.redact_pdf_native(
                str(source),
                str(output),
                [masker.RedactionMatch("RRN", original)],
                display_mode="pseudonym",
            )

            self.assertEqual("applied", result["status"])
            self.assertNotIn(original, pdf_text(output))

    def test_pdf_redaction_off_does_not_claim_masked_pdf_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "matrix.pdf"
            write_matrix_pdf(source)

            _extracted, _masked, _report_path, report = masker.process_file(
                str(source),
                outdir=str(root),
                opts={
                    "profile": "official",
                    "extract_engine": "pypdf",
                    "output_artifacts": "pdf+report",
                    "display_mode": "black",
                    "pdf_redaction": False,
                },
            )

            self.assertIsNone(masker.runtime_manifest_for_report(report)["outputs"]["masked_pdf_file"])
            self.assertEqual("skipped", report["pdf_redaction"]["status"])
            self.assertFalse(report["product_checks"]["final_submission_allowed"])

    def test_safe_report_never_written_to_user_outdir(self) -> None:
        # 문서 일괄(batch) 흐름 회귀: outdir=사용자 폴더로 실행해도 안전 리포트 JSON은
        # 사용자 outdir에 절대 남지 않고 내부 경로에만 생성된다. 마스킹 PDF만 남는다.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "matrix.pdf"
            write_matrix_pdf(source)
            outdir = root / "user_out"
            outdir.mkdir()

            _extracted, _masked, report_path, report = masker.process_file(
                str(source),
                outdir=str(outdir),
                opts={
                    "profile": "official",
                    "extract_engine": "pypdf",
                    "output_artifacts": "pdf+report",
                    "display_mode": "black",
                    "pdf_redaction": True,
                },
            )

            # 사용자 outdir 안에는 safe_report JSON이 하나도 없어야 한다.
            self.assertEqual([], list(outdir.rglob("*safe_report*.json")))
            self.assertEqual([], list(outdir.rglob("*.json")))
            # 반환된 리포트 경로는 존재하며 사용자 outdir 바깥(내부 경로)이다.
            self.assertIsNotNone(report_path)
            resolved = Path(str(report_path)).resolve()
            self.assertTrue(resolved.exists())
            self.assertFalse(resolved.is_relative_to(outdir.resolve()))
            runtime_outputs = masker.runtime_manifest_for_report(report)["outputs"]
            self.assertEqual(resolved.name, Path(runtime_outputs["report_path"]).name)
            # 마스킹 PDF는 사용자 outdir에 남는다.
            masked_pdf = Path(runtime_outputs["masked_pdf_file"])
            self.assertTrue(masked_pdf.exists())
            self.assertTrue(masked_pdf.resolve().is_relative_to(outdir.resolve()))


if __name__ == "__main__":
    unittest.main()
