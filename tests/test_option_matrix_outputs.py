from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import fitz

import document_masker_ocr_gui as masker
from pdf_redaction_rendering import insert_pdf_pseudonym_label, pseudonym_text_layout
from privacy_transformers import TransformState, partial_value, pseudonym_value
from masking_redaction import OccurrenceRedactionInput, _rect_text_hash, redact_pdf_native
from masking_extraction import ExtractResult, ExtractedPage

SENSITIVE_VALUES = ("010-0000-0000", "4000-0000-0000-0000", "sample@example.com")
BENIGN_ANCHORS = ("Phone", "Card", "Email")
SENSITIVE_TAGS = (("PHONE", "010-0000-0000"), ("CARD", "4000-0000-0000-0000"), ("EMAIL", "sample@example.com"))
ENGLISH_LABELS = ("[PHONE]", "[CARD]", "[EMAIL]")
KOREAN_LABELS = ("[전화번호]", "[카드번호]", "[이메일]")


def assert_masked_matrix_pdf(path: Path) -> str:
    rendered = pdf_text(path)
    for value in SENSITIVE_VALUES:
        if value in rendered:
            raise AssertionError(f"sensitive value leaked: {value}")
    for anchor in BENIGN_ANCHORS:
        if anchor not in rendered:
            raise AssertionError(f"benign anchor missing: {anchor}")
    return rendered
def assert_contains_all(rendered: str, expected: tuple[str, ...]) -> None:
    for value in expected:
        if value not in rendered:
            raise AssertionError(f"expected transformed value missing: {value}")


def expected_pseudonyms() -> tuple[str, ...]:
    state = TransformState()
    return tuple(pseudonym_value(tag, value, state) for tag, value in SENSITIVE_TAGS)



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

def local_pdf_output(outdir: Path, kind: str) -> Path:
    matches = list(outdir.glob(f"*.final_masked_{kind}.*.pdf"))
    if len(matches) != 1:
        raise AssertionError(f"expected one local {kind} PDF, found {matches}")
    return matches[0]



class OptionMatrixOutputTests(unittest.TestCase):
    def test_historical_official_profile_is_rejected_outside_settings_migration(self) -> None:
        with self.assertRaisesRegex(ValueError, "^MASKING_PROFILE_UNSUPPORTED$"):
            masker.normalize_opts({"profile": "official"})

    def test_public_pdf_without_trusted_geometry_degrades_to_scan_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "untrusted.pdf"
            write_matrix_pdf(source)
            untrusted = ExtractResult(
                text="Phone 010-0000-0000",
                engine_used="fixture",
                duration_sec=0.0,
                notes=[],
                pages=(
                    ExtractedPage(
                        page_index=0,
                        width=420.0,
                        height=180.0,
                        text="Phone 010-0000-0000",
                        words=(),
                        source="fixture",
                        evidence_status="gap",
                        evidence_reason="no_text_layer",
                    ),
                ),
            )
            with patch.object(masker, "extract_document", return_value=untrusted):
                extracted, masked, report_path, report = masker.process_file(
                    str(source),
                    opts={
                        "profile": "official_dispatch",
                        "extract_engine": "pypdf",
                        "auto_threshold": 0.85,
                        "review_threshold": 0.5,
                    },
                    session_hash_key=b"k" * 32,
                )
            self.assertIsNone(extracted)
            self.assertIsNone(masked)
            self.assertIsNone(report_path)
            self.assertFalse(report["raw_text_returned"])
            manifest = report["analysis_manifest"]
            self.assertEqual(
                [("unknown", "review_required", False, "scanned_geometry_unavailable")],
                [(item["kind"], item["state"], item["common_only"], item["source"])
                 for item in manifest["segments"]],
            )
            self.assertEqual(
                [("acknowledge", ("scanned_geometry_unavailable",), True)],
                [(item["kind"], tuple(item["reason_codes"]), item["requires_acknowledgment"])
                 for item in manifest["review_items"]],
            )
    def test_trusted_occurrences_produce_non_vacuous_public_pdf_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "matrix.pdf"
            write_matrix_pdf(source)
            source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            document = fitz.open(source)
            try:
                page = document[0]
                occurrences = []
                for value in SENSITIVE_VALUES:
                    rects = tuple((rect.x0, rect.y0, rect.x1, rect.y1) for rect in page.search_for(value))
                    text_hash = _rect_text_hash(page, page.search_for(value))
                    self.assertTrue(rects)
                    self.assertIsNotNone(text_hash)
                    occurrences.append(
                        OccurrenceRedactionInput(
                            occurrence_id=f"occ_{hashlib.sha256(f'trusted:{value}'.encode()).hexdigest()[:24]}",
                            run_id="trusted-matrix-run",
                            document_sha256=source_sha256,
                            analysis_revision=1,
                            page_index=0,
                            rect_list=rects,
                            action="mask",
                            provenance="trusted-test-geometry",
                            expected_text_hash=str(text_hash),
                        )
                    )
            finally:
                document.close()

            for display_mode in ("black", "label_en", "label_ko", "pseudonym"):
                with self.subTest(display_mode=display_mode):
                    output = root / f"{display_mode}.pdf"
                    result = redact_pdf_native(
                        str(source),
                        str(output),
                        occurrence_inputs=occurrences,
                        expected_run_id="trusted-matrix-run",
                        expected_document_sha256=source_sha256,
                        expected_analysis_revision=1,
                        display_mode=display_mode,
                        profile="official_dispatch",
                    )
                    self.assertEqual("applied", result["status"])
                    rendered = assert_masked_matrix_pdf(output)
                    self.assertNotIn("[MASK]", rendered)
                    self.assertNotIn("[마스킹]", rendered)
    def test_trusted_occurrence_tampering_blocks_without_writing_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "matrix.pdf"
            write_matrix_pdf(source)
            source_bytes = source.read_bytes()
            source_sha256 = hashlib.sha256(source_bytes).hexdigest()
            document = fitz.open(source)
            try:
                rects = tuple((rect.x0, rect.y0, rect.x1, rect.y1) for rect in document[0].search_for(SENSITIVE_VALUES[0]))
                text_hash = _rect_text_hash(document[0], document[0].search_for(SENSITIVE_VALUES[0]))
            finally:
                document.close()
            self.assertIsNotNone(text_hash)
            trusted = OccurrenceRedactionInput(
                occurrence_id="trusted-occurrence", run_id="trusted-run",
                document_sha256=source_sha256, analysis_revision=1, page_index=0,
                rect_list=rects, action="mask", provenance="trusted-test-geometry",
                expected_text_hash=str(text_hash),
            )
            tampering = {
                "run_id": ("other-run", "stale_occurrence_identity"),
                "document_sha256": ("0" * 64, "stale_occurrence_identity"),
                "analysis_revision": (2, "stale_analysis_revision"),
                "expected_text_hash": ("0" * 64, "expected_text_hash_mismatch"),
            }
            for field, (value, reason_code) in tampering.items():
                with self.subTest(field=field):
                    output = root / f"tampered-{field}.pdf"
                    result = redact_pdf_native(
                        str(source), str(output), occurrence_inputs=[replace(trusted, **{field: value})],
                        expected_run_id="trusted-run", expected_document_sha256=source_sha256,
                        expected_analysis_revision=1, display_mode="black", profile="official_dispatch",
                    )
                    self.assertEqual("blocked", result["status"])
                    self.assertIsNone(result["output_file"])
                    self.assertEqual([reason_code], [item["reason_code"] for item in result["review_items"]])
                    self.assertFalse(output.exists())
                    self.assertEqual(source_bytes, source.read_bytes())

    def test_duplicate_text_occurrences_with_distinct_trusted_ids_are_both_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "duplicate-values.pdf"
            document = fitz.open()
            try:
                page = document.new_page()
                page.insert_text((32, 52), "Phone 010-0000-0000")
                page.insert_text((32, 84), "Phone 010-0000-0000")
                document.save(source)
            finally:
                document.close()
            source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            document = fitz.open(source)
            try:
                rects = document[0].search_for("010-0000-0000")
                occurrences = []
                for index, rect in enumerate(rects):
                    text_hash = _rect_text_hash(document[0], [rect])
                    self.assertIsNotNone(text_hash)
                    occurrences.append(OccurrenceRedactionInput(
                        occurrence_id=f"duplicate-{index}", run_id="duplicate-run",
                        document_sha256=source_sha256, analysis_revision=1, page_index=0,
                        rect_list=((rect.x0, rect.y0, rect.x1, rect.y1),), action="mask",
                        provenance="trusted-test-geometry", expected_text_hash=str(text_hash),
                    ))
            finally:
                document.close()
            output = root / "duplicate-values-masked.pdf"
            result = redact_pdf_native(
                str(source), str(output), occurrence_inputs=occurrences,
                expected_run_id="duplicate-run", expected_document_sha256=source_sha256,
                expected_analysis_revision=1, display_mode="black", profile="official_dispatch",
            )
            self.assertEqual("applied", result["status"])
            self.assertTrue(output.exists())
            self.assertNotIn("010-0000-0000", pdf_text(output))
            self.assertEqual(2, result["occurrences_applied"])
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
                "black": (),
                "label_en": ENGLISH_LABELS,
                "label_ko": KOREAN_LABELS,
            }
            for display_mode, label in expected.items():
                outdir = root / display_mode
                outdir.mkdir()
                _extracted, _masked, _report_path, report = masker.process_file(
                    str(source),
                    outdir=str(outdir),
                    opts={
                        "profile": "legal",
                        "extract_engine": "pypdf",
                        "output_artifacts": "pdf+report",
                        "display_mode": display_mode,
                        "pdf_redaction": True,
                    },
                )

                runtime_outputs = masker.runtime_manifest_for_report(report)["outputs"]
                self.assertTrue(runtime_outputs["masked_pdf_file"])
                output_file = local_pdf_output(outdir, "black")
                self.assertTrue(output_file.exists(), display_mode)
                rendered_text = assert_masked_matrix_pdf(output_file)
                if label:
                    assert_contains_all(rendered_text, label)

    def test_black_and_labeled_artifact_creates_both_pdf_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "matrix.pdf"
            write_matrix_pdf(source)

            _extracted, _masked, _report_path, report = masker.process_file(
                str(source),
                outdir=str(root),
                opts={
                    "profile": "legal",
                    "extract_engine": "pypdf",
                    "output_artifacts": "pdf_black_and_labeled",
                    "display_mode": "black",
                    "pdf_redaction": True,
                },
            )

            runtime_outputs = masker.runtime_manifest_for_report(report)["outputs"]
            self.assertTrue(runtime_outputs["masked_pdf_file"])
            self.assertTrue(runtime_outputs["labeled_pdf_file"])
            black_pdf = local_pdf_output(root, "black")
            labeled_pdf = local_pdf_output(root, "labeled")
            self.assertTrue(black_pdf.exists())
            self.assertTrue(labeled_pdf.exists())
            black_text = assert_masked_matrix_pdf(black_pdf)
            labeled_text = assert_masked_matrix_pdf(labeled_pdf)
            self.assertNotIn("[PHONE]", black_text)
            assert_contains_all(labeled_text, ENGLISH_LABELS)

    def test_text_deidentification_options_only_affect_text_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "matrix.pdf"
            write_matrix_pdf(source)

            policies = {
                "token": ENGLISH_LABELS,
                "partial": tuple(partial_value(tag, value) for tag, value in SENSITIVE_TAGS),
                "pseudonym": expected_pseudonyms(),
            }
            for policy, expected_values in policies.items():
                outdir = root / policy
                outdir.mkdir()
                _extracted, masked_path, _report_path, report = masker.process_file(
                    str(source),
                    outdir=str(outdir),
                    opts={
                        "profile": "legal",
                        "extract_engine": "pypdf",
                        "output_artifacts": "pdf_masked_txt_safe_report",
                        "display_mode": "black",
                        "deidentification_policy": policy,
                        "pdf_redaction": True,
                    },
                )

                self.assertIsNotNone(masked_path, policy)
                masked_text = Path(str(masked_path)).read_text(encoding="utf-8")
                assert_contains_all(masked_text, expected_values)
                for original in SENSITIVE_VALUES:
                    self.assertNotIn(original, masked_text)
                for anchor in BENIGN_ANCHORS:
                    self.assertIn(anchor, masked_text)
                self.assertEqual(policy, report["text_deidentification"]["policy"])
                self.assertEqual("text_preview_and_txt_output_only", report["text_deidentification"]["scope"])
                self.assertTrue(masker.runtime_manifest_for_report(report)["outputs"]["masked_pdf_file"])
                rendered_pdf_text = assert_masked_matrix_pdf(local_pdf_output(outdir, "black"))
                for expected in expected_values:
                    self.assertNotIn(expected, rendered_pdf_text, policy)

    def test_pseudonym_pdf_matches_masked_txt_and_hides_original_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "matrix.pdf"
            write_matrix_pdf(source)
            expected_pseudo_values = expected_pseudonyms()
            rendered_runs: list[str] = []

            for run_number in (1, 2):
                outdir = root / f"pseudonym-{run_number}"
                outdir.mkdir()
                _extracted, masked_path, _report_path, report = masker.process_file(
                    str(source),
                    outdir=str(outdir),
                    opts={
                        "profile": "legal",
                        "extract_engine": "pypdf",
                        "output_artifacts": "pdf_masked_txt_safe_report",
                        "display_mode": "pseudonym",
                        "deidentification_policy": "pseudonym",
                        "pdf_redaction": True,
                    },
                )

                self.assertIsNotNone(masked_path)
                masked_text = Path(str(masked_path)).read_text(encoding="utf-8")
                runtime_outputs = masker.runtime_manifest_for_report(report)["outputs"]
                self.assertTrue(runtime_outputs["masked_pdf_file"])
                output_file = local_pdf_output(outdir, "black")
                rendered_text = pdf_text(output_file)
                rendered_runs.append(rendered_text)
                assert_contains_all(masked_text, expected_pseudo_values)
                assert_contains_all(rendered_text, expected_pseudo_values)
                for original in SENSITIVE_VALUES:
                    self.assertNotIn(original, masked_text)
                    self.assertNotIn(original, rendered_text)
                for anchor in BENIGN_ANCHORS:
                    self.assertIn(anchor, rendered_text)
                serialized_report = json.dumps(report, ensure_ascii=False)
                for expected in expected_pseudo_values:
                    self.assertNotIn(expected, serialized_report)
                self.assertEqual("pseudonym", report["rules"]["display_mode"])
                self.assertEqual("applied", report["pdf_redaction"]["status"])
                self.assertTrue(report["pdf_redaction"]["verification"]["verified"])
                self.assertEqual(0, report["pdf_redaction"]["verification"]["residual_hits"])

            self.assertEqual(rendered_runs[0], rendered_runs[1])

    def test_pseudonym_pdf_requires_trusted_occurrence_inputs(self) -> None:
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

            with self.assertRaisesRegex(ValueError, "^PUBLIC_OCCURRENCE_INPUTS_REQUIRED$"):
                masker.redact_pdf_native(
                    str(source),
                    str(output),
                    [masker.RedactionMatch("RRN", original)],
                    display_mode="pseudonym",
                )
            self.assertFalse(output.exists())


    def test_pdf_redaction_off_does_not_create_masked_output_or_mutate_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "matrix.pdf"
            write_matrix_pdf(source)
            source_bytes = source.read_bytes()

            _extracted, masked_path, _report_path, report = masker.process_file(
                str(source),
                outdir=str(root),
                opts={
                    "profile": "legal",
                    "extract_engine": "pypdf",
                    "output_artifacts": "pdf+report",
                    "display_mode": "black",
                    "pdf_redaction": False,
                },
            )

            self.assertIsNone(masked_path)
            self.assertFalse(masker.runtime_manifest_for_report(report)["outputs"]["masked_pdf_file"])
            self.assertEqual("skipped", report["pdf_redaction"]["status"])
            self.assertFalse(report["product_checks"]["final_submission_allowed"])
            self.assertEqual(source_bytes, source.read_bytes())
            self.assertEqual([source], list(root.glob("*.pdf")))

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
                    "profile": "legal",
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
            self.assertTrue(runtime_outputs["report_path"])
            self.assertTrue(runtime_outputs["masked_pdf_file"])
            # The runtime manifest exposes availability only; local paths remain test-only.
            self.assertNotIn(str(resolved), json.dumps(runtime_outputs))
            # 마스킹 PDF는 사용자 outdir에 남는다.
            masked_pdf = local_pdf_output(outdir, "black")
            self.assertTrue(masked_pdf.exists())
            self.assertTrue(masked_pdf.resolve().is_relative_to(outdir.resolve()))


if __name__ == "__main__":
    unittest.main()
