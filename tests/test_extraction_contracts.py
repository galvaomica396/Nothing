from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import masking_extraction
from masking_extraction import ExtractResult, ExtractedPage, ExtractedWord, extract_document
from document_masker_ocr_gui import _page_word_offsets


class ExtractionContractTests(unittest.TestCase):
    def test_table_cell_word_offsets_allow_canonical_whitespace_between_raw_characters(self) -> None:
        page = ExtractedPage(
            page_index=0,
            width=612.0,
            height=792.0,
            text="주 무 관 팀 장 주무관",
            words=(
                ExtractedWord("주무관", (10.0, 10.0, 40.0, 20.0), source="pymupdf_table_cell"),
                ExtractedWord("팀장", (50.0, 10.0, 70.0, 20.0), source="pymupdf_table_cell"),
            ),
            source="pymupdf_text_layer",
        )

        (aligned,) = masking_extraction._align_page_evidence(page.text, (page,))

        self.assertEqual([(0, 5), (6, 9)], [
            (word.page_start, word.page_end) for word in aligned.words
        ])
        self.assertEqual(2, len(_page_word_offsets(aligned)))

    def test_plain_text_extraction_keeps_legacy_fields_and_serializes_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input.txt"
            path.write_text("plain text", encoding="utf-8")

            result = extract_document(str(path))

        self.assertEqual("plain text", result.text)
        self.assertEqual("plain-text", result.engine_used)
        self.assertEqual(("plain-text",), result.engine_chain)
        payload = result.to_dict()
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual([], payload["pages"])
        json.dumps(payload, ensure_ascii=False)

    def test_page_word_evidence_uses_json_rects_and_null_confidence(self) -> None:
        result = ExtractResult(
            text="word",
            engine_used="fixture",
            duration_sec=0.0,
            notes=[],
            pages=(
                ExtractedPage(
                    page_index=0,
                    width=612.0,
                    height=792.0,
                    text="word",
                    words=(ExtractedWord("word", (10.0, 20.0, 30.0, 40.0), None, 0, 4),),
                ),
            ),
        )

        payload = result.to_dict()
        word = payload["pages"][0]["words"][0]
        self.assertEqual([10.0, 20.0, 30.0, 40.0], word["bbox"])
        self.assertIsNone(word["confidence"])
        self.assertEqual((0, 4), (word["start"], word["end"]))
        self.assertEqual("pdf_points_top_left", word["coordinate_space"])
        self.assertIn("evidence_adapter", payload)
    def test_manifest_geometry_rejection_matrix_is_fail_closed(self) -> None:
        manifest = {
            "schema_version": "IndependentGoldManifestV1",
            "geometry_policy_version": "GeometryPolicyV1",
            "coordinate_space": "pdf_points_top_left",
            "policy_version": "policy-v1",
            "profile": "mixed",
            "source_class": "synthetic",
            "form": "synthetic",
            "document": {"document_id": "doc-1", "input_sha256": "a" * 64, "output_sha256": "b" * 64},
            "provenance": {"author": {"id": "author"}, "reviewer": {"id": "reviewer", "decision": "approved", "adjudication": "independent_review"}, "detector_output_imported": False},
            "pages": [{"page_index": 0, "width": 100.0, "height": 100.0}],
            "segments": [{"id": "segment-1", "page_index": 0, "type": "body", "offsets": {"start": 0, "end": 4}}],
            "regions": [{"id": "region-1", "page_index": 0, "type": "body", "rects": [{"x0": 1.0, "y0": 1.0, "x1": 2.0, "y1": 2.0}]}],
            "occurrences": [{"id": "occurrence-1", "segment_id": "segment-1", "region_id": "region-1", "page_index": 0, "category": "person_name", "offsets": {"start": 0, "end": 4}, "text_hash": "c" * 64, "ocr_confidence": None, "rects": [{"x0": 1.0, "y0": 1.0, "x1": 2.0, "y1": 2.0}]}],
            "annotation_status": "reviewed_approved",
            "negatives": [],
            "protected_neighbors": [],
            "annotation_completion": {"pages": "completed", "segments": "completed", "regions": "completed", "occurrences": "completed", "negatives": "none_confirmed", "protected_neighbors": "none_confirmed"},
        }
        from masking_evaluation import ManifestValidationError, lock_manifest, validate_manifest
        validate_manifest(manifest)
        locked = lock_manifest(manifest)
        validate_manifest(locked, require_locked=True)


        invalid_cases = {
            "nan rectangle": (
                lambda item: item["regions"][0]["rects"][0].update(x0=float("nan")),
                "non-empty finite rectangle",
            ),
            "infinite rectangle": (
                lambda item: item["occurrences"][0]["rects"][0].update(x1=float("inf")),
                "non-empty finite rectangle",
            ),
            "zero area": (
                lambda item: item["regions"][0]["rects"][0].update(x1=1.0),
                "non-empty finite rectangle",
            ),
            "inverted rectangle": (
                lambda item: item["occurrences"][0]["rects"][0].update(y1=0.0),
                "non-empty finite rectangle",
            ),
            "right overflow": (
                lambda item: item["regions"][0]["rects"][0].update(x1=101.0),
                "outside its page",
            ),
            "negative x": (
                lambda item: item["occurrences"][0]["rects"][0].update(x0=-1.0),
                "outside its page",
            ),
            "negative y": (
                lambda item: item["regions"][0]["rects"][0].update(y0=-1.0),
                "outside its page",
            ),
            "bottom overflow": (
                lambda item: item["occurrences"][0]["rects"][0].update(y1=101.0),
                "outside its page",
            ),
            "nonpositive page width": (
                lambda item: item["pages"][0].update(width=0.0),
                "invalid dimensions",
            ),
            "negative page height": (
                lambda item: item["pages"][0].update(height=-1.0),
                "invalid dimensions",
            ),
            "infinite page width": (
                lambda item: item["pages"][0].update(width=float("inf")),
                "invalid dimensions",
            ),
            "unaligned occurrence offsets": (
                lambda item: item["occurrences"][0]["offsets"].update(end=5),
                "contained in its segment",
            ),
        }
        for name, (corrupt, error) in invalid_cases.items():
            with self.subTest(name=name):
                malformed = copy.deepcopy(manifest)
                corrupt(malformed)
                with self.assertRaisesRegex(ManifestValidationError, error):
                    validate_manifest(malformed)
                with self.assertRaisesRegex(ManifestValidationError, error):
                    lock_manifest(malformed)

    def test_unaligned_evidence_uses_a_reason_without_embedding_canonical_text(self) -> None:
        adapter_page = ExtractedPage(
            page_index=0,
            width=612.0,
            height=792.0,
            text="adapter-only",
            words=(ExtractedWord("adapter-only", (1.0, 2.0, 3.0, 4.0), None),),
            source="fixture_adapter",
        )
        result = masking_extraction._enrich_pdf_result(
            ExtractResult("canonical-only", "fixture", 0.0, []),
            "fixture.pdf",
            lambda _path: (adapter_page,),
        )

        payload = result.to_dict()
        self.assertEqual("canonical_text_unaligned", payload["pages"][0]["evidence_reason"])

    def test_public_analysis_rebases_unaligned_markdown_to_trusted_text_layer(self) -> None:
        page = ExtractedPage(
            page_index=0,
            width=612.0,
            height=792.0,
            text="trusted page text",
            words=(ExtractedWord(
                "trusted", (1.0, 2.0, 30.0, 14.0), source="pymupdf_text_layer",
            ),),
            source="pymupdf_text_layer",
            evidence_status="unaligned",
            evidence_reason="canonical_text_unaligned",
        )
        primary = ExtractResult(
            "# markdown-only text",
            "pymupdf4llm",
            0.0,
            [],
            pages=(page,),
            engine_chain=("pymupdf4llm",),
            evidence_adapter="pymupdf_text_layer",
        )

        with mock.patch.object(masking_extraction, "extract_document", return_value=primary):
            result = masking_extraction.extract_document_for_public_analysis("fixture.pdf", "auto")

        self.assertEqual("trusted page text", result.text)
        self.assertEqual("pymupdf4llm", result.engine_used)
        self.assertEqual("available", result.pages[0].evidence_status)
        self.assertIsNone(result.pages[0].evidence_reason)
        self.assertEqual((0, 7), (result.pages[0].words[0].page_start, result.pages[0].words[0].page_end))

    def test_public_analysis_replaces_non_geometry_extractor_with_pymupdf_adapter(self) -> None:
        primary = ExtractResult(
            "adapter text",
            "pypdf",
            0.0,
            [],
            pages=(ExtractedPage(
                0,
                612.0,
                792.0,
                "adapter text",
                (),
                (),
                source="pypdf",
            ),),
            engine_chain=("pypdf",),
        )
        adapter_page = ExtractedPage(
            page_index=0,
            width=612.0,
            height=792.0,
            text="adapter text",
            words=(ExtractedWord(
                "adapter", (1.0, 2.0, 30.0, 14.0),
                page_start=0,
                page_end=7,
                source="pymupdf_text_layer",
            ),),
            source="pymupdf_text_layer",
        )

        with (
            mock.patch.object(masking_extraction, "extract_document", return_value=primary),
            mock.patch.object(masking_extraction, "_extract_pdf_page_evidence", return_value=(adapter_page,)),
        ):
            result = masking_extraction.extract_document_for_public_analysis("fixture.pdf", "pypdf")

        self.assertEqual("pymupdf_text_layer", result.engine_used)
        self.assertEqual("pymupdf_text_layer", result.evidence_adapter)
        self.assertEqual("adapter text", result.text)
        self.assertEqual("available", result.pages[0].evidence_status)
        self.assertEqual((0, 7), (result.pages[0].words[0].page_start, result.pages[0].words[0].page_end))

    def test_final_page_alignment_allows_primary_extractor_to_trim_only_trailing_newline(self) -> None:
        adapter_page = ExtractedPage(
            page_index=0,
            width=612.0,
            height=792.0,
            text="final page\n",
            words=(ExtractedWord("final", (1.0, 2.0, 3.0, 4.0), None),),
            source="fixture_adapter",
        )

        (aligned,) = masking_extraction._align_page_evidence("final page", (adapter_page,))

        self.assertEqual("available", aligned.evidence_status)
        self.assertIsNone(aligned.evidence_reason)
        self.assertEqual((0, 10), (aligned.start, aligned.end))
        self.assertEqual((0, 5), (aligned.words[0].start, aligned.words[0].end))

    def test_alignment_retains_ocr_page_provenance(self) -> None:
        source_page = ExtractedPage(
            page_index=0,
            width=612.0,
            height=792.0,
            text="OCR sourced page",
            words=(ExtractedWord("OCR", (1.0, 2.0, 10.0, 12.0), 0.4, source="ocr"),),
            source="ocr",
            evidence_reason="ocr_assisted_realignment",
        )

        (aligned,) = masking_extraction._align_page_evidence("OCR sourced page", (source_page,))

        self.assertEqual(("ocr", "ocr_assisted_realignment"), (aligned.source, aligned.evidence_reason))
        self.assertEqual("ocr", aligned.words[0].source)

    def test_alignment_tolerates_equivalent_whitespace_but_uses_canonical_offsets(self) -> None:
        adapter_page = ExtractedPage(
            page_index=0,
            width=612.0,
            height=792.0,
            text="heading\nphone",
            words=(
                ExtractedWord("heading", (1.0, 2.0, 3.0, 4.0), None),
                ExtractedWord("phone", (5.0, 6.0, 7.0, 8.0), None),
            ),
            source="fixture_adapter",
        )

        (aligned,) = masking_extraction._align_page_evidence(
            "prefix\n\nheading \n\nphone\n",
            (adapter_page,),
        )

        self.assertEqual("available", aligned.evidence_status)
        self.assertEqual((8, 23), (aligned.start, aligned.end))
        self.assertEqual((8, 15), (aligned.words[0].start, aligned.words[0].end))
        self.assertEqual((18, 23), (aligned.words[1].start, aligned.words[1].end))

    def test_hostile_adapter_exception_is_reduced_to_allowlisted_diagnostic(self) -> None:
        canary = "RAW NAME Kim /private/patient.pdf"
        result = masking_extraction._enrich_pdf_result(
            ExtractResult(
                "canonical",
                "fixture",
                0.0,
                [],
                engine_chain=("fixture",),
                pages=(
                    ExtractedPage(
                        page_index=0,
                        width=612.0,
                        height=792.0,
                        text="canonical",
                        source="primary-engine",
                    ),
                ),
            ),
            "/private/patient.pdf",
            lambda _path: (_ for _ in ()).throw(RuntimeError(canary)),
        )

        payload = result.to_dict()
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertEqual("canonical", result.text)
        self.assertEqual("fixture", result.engine_used)
        self.assertEqual(("fixture",), result.engine_chain)
        self.assertEqual("canonical", result.pages[0].text)
        self.assertEqual("primary-engine", result.pages[0].source)
        self.assertEqual("gap", result.pages[0].evidence_status)
        self.assertEqual("PAGE_EVIDENCE_ADAPTER_FAILED", result.pages[0].evidence_reason)
        self.assertEqual("PAGE_EVIDENCE_ADAPTER_FAILED", result.evidence_reason)
        self.assertEqual("PAGE_EVIDENCE_ADAPTER_FAILED", payload["evidence_reason"])
        for fragment in (canary, "RAW NAME", "Kim", "/private/patient.pdf", "/private", "patient.pdf"):
            self.assertNotIn(fragment, serialized)

    def test_allowlisted_adapter_exception_preserves_only_its_code(self) -> None:
        result = masking_extraction._enrich_pdf_result(
            ExtractResult(
                "canonical",
                "fixture",
                0.0,
                [],
                engine_chain=("fixture",),
                pages=(
                    ExtractedPage(
                        page_index=0,
                        width=612.0,
                        height=792.0,
                        text="canonical",
                        source="primary-engine",
                    ),
                ),
            ),
            "/sensitive/Lee.pdf",
            lambda _path: (_ for _ in ()).throw(RuntimeError(
                "PAGE_EVIDENCE_ADAPTER_UNAVAILABLE raw name Lee /sensitive/Lee.pdf"
            )),
        )

        payload = result.to_dict()
        self.assertEqual("canonical", result.text)
        self.assertEqual("fixture", result.engine_used)
        self.assertEqual(("fixture",), result.engine_chain)
        self.assertEqual("canonical", result.pages[0].text)
        self.assertEqual("primary-engine", result.pages[0].source)
        self.assertEqual("gap", result.pages[0].evidence_status)
        self.assertEqual("PAGE_EVIDENCE_ADAPTER_UNAVAILABLE", result.pages[0].evidence_reason)
        self.assertEqual("PAGE_EVIDENCE_ADAPTER_UNAVAILABLE", payload["evidence_reason"])
        serialized = json.dumps(payload, ensure_ascii=False)
        for fragment in ("PAGE_EVIDENCE_ADAPTER_UNAVAILABLE raw name Lee /sensitive/Lee.pdf", "raw name", "Lee", "/sensitive/Lee.pdf", "/sensitive", "Lee.pdf"):
            self.assertNotIn(fragment, serialized)

    def test_public_pdf_extraction_preserves_primary_result_when_evidence_adapter_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "public.pdf"
            import fitz

            document = fitz.open()
            document.new_page().insert_text((40, 50), "PUBLIC CANONICAL")
            document.save(path)
            document.close()

            result = extract_document(
                str(path),
                engine="pypdf",
                page_evidence_adapter=lambda _path: (_ for _ in ()).throw(
                    RuntimeError("PAGE_EVIDENCE_ADAPTER_UNAVAILABLE /private/PUBLIC CANONICAL.pdf")
                ),
            )

        self.assertIn("PUBLIC CANONICAL", result.text)
        self.assertEqual("pypdf", result.engine_used)
        self.assertEqual(("pypdf",), result.engine_chain)
        self.assertEqual(1, len(result.pages))
        self.assertEqual("gap", result.pages[0].evidence_status)
        self.assertEqual("PAGE_EVIDENCE_ADAPTER_UNAVAILABLE", result.pages[0].evidence_reason)
        self.assertEqual("PAGE_EVIDENCE_ADAPTER_UNAVAILABLE", result.evidence_reason)
        serialized = json.dumps(result.to_dict(), ensure_ascii=False)
        for fragment in ("/private/PUBLIC CANONICAL.pdf", "/private", "PUBLIC CANONICAL.pdf"):
            self.assertNotIn(fragment, serialized)

    def test_marker_invalid_utf8_is_rejected_with_a_stable_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "marker_out"
            output.mkdir()
            (output / "document.md").write_bytes(b"safe\xfftext")
            with mock.patch.object(masking_extraction.shutil, "which", return_value="marker_single"), mock.patch.object(
                masking_extraction, "_run_cmd", return_value=(0, "", "")
            ):
                with self.assertRaisesRegex(RuntimeError, "^EXTRACTION_MARKER_INVALID_UTF8$"):
                    masking_extraction._extract_pdf_with_marker("/input.pdf", directory)

    def test_malformed_paddle_entry_is_rejected_not_skipped(self) -> None:
        malformed_entries = (
            (),
            ((), ("text", 0.9)),
            (((0, 0), (1, 0), (1, 1), (0, 1)), ("text",)),
            (((0, 0), (1, 0), (1, 1), (0, 1)), ("", 0.9)),
            (((0, 0), (1, 0), (1, 1), (0, 1)), ("text", float("nan"))),
        )
        for entry in malformed_entries:
            with self.subTest(entry=entry):
                with self.assertRaisesRegex(RuntimeError, "^EXTRACTION_PADDLE_MALFORMED_ENTRY$"):
                    masking_extraction._paddle_word(entry, 0)

    def test_paddle_constructor_options_are_selected_by_major_version(self) -> None:
        self.assertEqual(
            {"lang": "korean", "use_angle_cls": True},
            masking_extraction._paddle_constructor_options(type("PaddleV2", (), {"__version__": "2.6.0"})()),
        )
        self.assertEqual(
            {"lang": "korean", "use_textline_orientation": True},
            masking_extraction._paddle_constructor_options(type("PaddleV3", (), {"__version__": "3.0.0"})()),
        )
        with self.assertRaisesRegex(RuntimeError, "^EXTRACTION_PADDLE_VERSION_UNSUPPORTED$"):
            masking_extraction._paddle_constructor_options(type("PaddleV1", (), {"__version__": "1.8.0"})())
if __name__ == "__main__":
    unittest.main()
