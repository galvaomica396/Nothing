from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import masking_extraction as extraction

def assert_no_raw_diagnostic(
    test_case: unittest.TestCase, value: object, forbidden_fragments: tuple[str, ...],
) -> None:
    if isinstance(value, str):
        for fragment in forbidden_fragments:
            test_case.assertNotIn(fragment, value)
    elif isinstance(value, dict):
        for key, nested in value.items():
            assert_no_raw_diagnostic(test_case, key, forbidden_fragments)
            assert_no_raw_diagnostic(test_case, nested, forbidden_fragments)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            assert_no_raw_diagnostic(test_case, nested, forbidden_fragments)



class ExtractionEvidenceTests(unittest.TestCase):
    def test_page_drawings_keep_rules_and_deduplicate_vector_bounds(self) -> None:
        page = type("Page", (), {
            "get_drawings": lambda _self: (
                {"rect": (10.0, 20.0, 110.0, 20.0)},
                {"rect": (10.0, 20.0, 110.0, 20.0)},
                {"rect": (1.0, 1.0, 5.0, 5.0)},
            ),
        })()

        drawings = extraction._page_drawings(page)

        self.assertEqual(((10.0, 20.0, 110.0, 20.0),), drawings)

    def test_page_drawings_merge_parallel_rules_into_a_ruled_row(self) -> None:
        page = type("Page", (), {
            "get_drawings": lambda _self: (
                {"rect": (10.0, 20.0, 60.0, 20.0)},
                {"rect": (60.0, 20.0, 110.0, 20.0)},
                {"rect": (10.0, 60.0, 60.0, 60.0)},
                {"rect": (60.0, 60.0, 110.0, 60.0)},
                {"rect": (10.0, 20.0, 10.0, 60.0)},
                {"rect": (110.0, 20.0, 110.0, 60.0)},
            ),
        })()

        drawings = extraction._page_drawings(page)

        self.assertIn((10.0, 20.0, 110.0, 60.0), drawings)

    def test_full_width_letterhead_rules_do_not_become_a_ruled_row(self) -> None:
        # Given: decorative horizontal rules with no vertical table boundaries.
        page = type("Page", (), {
            "get_drawings": lambda _self: (
                {"rect": (0.0, 20.0, 612.0, 20.0)},
                {"rect": (0.0, 60.0, 612.0, 60.0)},
            ),
        })()

        # When: vector drawings are converted into table-supporting geometry.
        drawings = extraction._page_drawings(page)

        # Then: no synthetic box spans the text between the letterhead rules.
        self.assertNotIn((0.0, 20.0, 612.0, 60.0), drawings)

    @staticmethod
    def adapter(_path: str) -> tuple[extraction.ExtractedPage, ...]:
        return (
            extraction.ExtractedPage(
                page_index=0,
                width=612.0,
                height=792.0,
                text="page-one",
                words=(extraction.ExtractedWord("page-one", (10, 20, 60, 40), None),),
                source="fixture_adapter",
            ),
            extraction.ExtractedPage(
                page_index=1,
                width=612.0,
                height=792.0,
                text="page-two",
                words=(extraction.ExtractedWord("page-two", (15, 25, 65, 45), None),),
                source="fixture_adapter",
            ),
        )

    def test_marker_and_pymupdf_results_accept_injected_page_evidence(self) -> None:
        marker = extraction.ExtractResult("page-one\npage-two", "marker-pdf", 0.0, [])
        pymupdf = extraction.ExtractResult("page-one\npage-two", "pymupdf4llm", 0.0, [])
        with patch.object(extraction, "_extract_pdf_with_marker_cleanup", return_value=marker):
            marker_result = extraction.extract_document("fixture.pdf", "marker", page_evidence_adapter=self.adapter)
        with patch.object(extraction, "_extract_pdf_with_pymupdf4llm", return_value=pymupdf):
            pymupdf_result = extraction.extract_document("fixture.pdf", "pymupdf", page_evidence_adapter=self.adapter)

        for result, engine in ((marker_result, "marker-pdf"), (pymupdf_result, "pymupdf4llm")):
            self.assertEqual(engine, result.engine_used)
            self.assertEqual("pymupdf_text_layer", result.evidence_adapter)
            self.assertEqual([0, 1], [page.page_index for page in result.pages])
            self.assertTrue(all(page.coordinate_space == "pdf_points_top_left" for page in result.pages))
            self.assertTrue(all(page.width == 612.0 and page.height == 792.0 for page in result.pages))
            self.assertTrue(all(word.confidence is None for page in result.pages for word in page.words))

    def test_multi_page_offsets_are_document_and_page_local(self) -> None:
        result = extraction.ExtractResult("page-one\npage-two", "fixture", 0.0, [])
        enriched = extraction._enrich_pdf_result(result, "fixture.pdf", self.adapter)

        first, second = enriched.pages
        self.assertEqual((0, 8), (first.start, first.end))
        self.assertEqual((9, 17), (second.start, second.end))
        self.assertEqual((0, 8), (first.words[0].start, first.words[0].end))
        self.assertEqual((0, 8), (first.words[0].page_start, first.words[0].page_end))
        self.assertEqual((9, 17), (second.words[0].start, second.words[0].end))
        self.assertEqual((0, 8), (second.words[0].page_start, second.words[0].page_end))
        self.assertEqual(
            (second.start + second.words[0].page_start, second.start + second.words[0].page_end),
            (second.words[0].start, second.words[0].end),
        )

    def test_no_text_layer_is_an_explicit_evidence_gap(self) -> None:
        adapter = lambda _path: (extraction.ExtractedPage(0, 612.0, 792.0, "", source="fixture_adapter"),)
        result = extraction._enrich_pdf_result(extraction.ExtractResult("", "fixture", 0.0, []), "fixture.pdf", adapter)

        self.assertEqual("gap", result.pages[0].evidence_status)
        self.assertEqual("no_text_layer", result.pages[0].evidence_reason)
        self.assertEqual([], result.pages[0].to_dict()["words"])

    def test_adapter_failure_preserves_result_and_uses_safe_reason(self) -> None:
        def failing_adapter(_path: str) -> tuple[extraction.ExtractedPage, ...]:
            raise RuntimeError("PAGE_EVIDENCE_ADAPTER_FAILED")

        result = extraction._enrich_pdf_result(extraction.ExtractResult("canonical", "fixture", 0.0, []), "fixture.pdf", failing_adapter)
        self.assertEqual("PAGE_EVIDENCE_ADAPTER_FAILED", result.evidence_reason)
        self.assertEqual((), result.pages)

    def test_auto_fallback_is_enriched_and_serialization_has_no_diagnostic_text(self) -> None:
        fallback = extraction.ExtractResult("page-one\npage-two", "paddleocr", 0.0, [])
        with patch.object(extraction, "_extract_pdf_with_marker_cleanup", side_effect=RuntimeError("EXTRACTION_MARKER_UNAVAILABLE")), patch.object(
            extraction, "_extract_pdf_with_paddle", return_value=fallback
        ):
            result = extraction.extract_document("fixture.pdf", "auto", page_evidence_adapter=self.adapter)

        payload = result.to_dict()
        self.assertEqual(("marker-pdf", "paddleocr"), result.engine_chain)
        self.assertEqual(("EXTRACTION_MARKER_UNAVAILABLE",), result.fallback_chain)
        self.assertEqual("pymupdf_text_layer", payload["evidence_adapter"])
        encoded = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("fixture.pdf", encoded)
        self.assertNotIn("PAGE_EVIDENCE_ADAPTER_FAILED", encoded)
    def test_later_engine_fallback_replaces_hostile_exception_with_stable_code(self) -> None:
        secret = "/private/홍길동/010-1234-5678.pdf"
        fallback = extraction.ExtractResult("safe", "paddleocr", 0.0, [])

        with (
            patch.object(extraction, "_extract_pdf_with_marker_cleanup", side_effect=RuntimeError(f"marker failed: {secret}")),
            patch.object(extraction, "_extract_pdf_with_paddle", return_value=fallback),
        ):
            result = extraction.extract_document("fixture.pdf", "auto")

        self.assertEqual(("marker-pdf", "paddleocr"), result.engine_chain)
        self.assertEqual(("EXTRACTION_MARKER_FAILED",), result.fallback_chain)
        assert_no_raw_diagnostic(self, result.to_dict(), (secret, "/private", "홍길동", "010-1234-5678", ".pdf", "marker failed"))

    def test_all_engine_failure_hides_hostile_path_and_pii_diagnostics(self) -> None:
        secret = "/private/홍길동/010-1234-5678.pdf"
        failures = (
            RuntimeError(f"marker failed: {secret}"),
            RuntimeError(f"paddle failed: {secret}"),
            RuntimeError(f"pymupdf failed: {secret}"),
            RuntimeError(f"pypdf failed: {secret}"),
        )
        with (
            patch.object(extraction, "_extract_pdf_with_marker_cleanup", side_effect=failures[0]),
            patch.object(extraction, "_extract_pdf_with_paddle", side_effect=failures[1]),
            patch.object(extraction, "_extract_pdf_with_pymupdf4llm", side_effect=failures[2]),
            patch.object(extraction, "_extract_pdf_with_pypdf", side_effect=failures[3]),
            self.assertRaises(RuntimeError) as raised,
        ):
            extraction.extract_document("fixture.pdf", "auto")
        self.assertEqual(
            "EXTRACTION_ALL_ENGINES_FAILED:"
            "EXTRACTION_MARKER_FAILED,EXTRACTION_PADDLE_VERSION_UNSUPPORTED,"
            "EXTRACTION_PYMUPDF_FAILED,EXTRACTION_PYPDF_FAILED",
            str(raised.exception),
        )
        assert_no_raw_diagnostic(
            self,
            str(raised.exception),
            (secret, "/private", "홍길동", "010-1234-5678", ".pdf", "marker failed", "paddle failed", "pymupdf failed", "pypdf failed"),
        )

    def test_marker_cleanup_failure_is_terminal_in_auto_mode(self) -> None:
        fallback = extraction.ExtractResult("safe", "paddleocr", 0.0, [])
        with patch.object(
            extraction,
            "_extract_pdf_with_marker_cleanup",
            side_effect=RuntimeError("EXTRACTION_MARKER_CLEANUP_FAILED"),
        ), patch.object(extraction, "_extract_pdf_with_paddle", return_value=fallback) as paddle:
            with self.assertRaises(RuntimeError) as raised:
                extraction.extract_document("fixture.pdf", "auto")

        self.assertEqual("EXTRACTION_MARKER_CLEANUP_FAILED", str(raised.exception))

        paddle.assert_not_called()
    def test_adapter_failure_never_serializes_hostile_exception_text(self) -> None:
        secret = "/private/홍길동/010-1234-5678.pdf"

        def failing_adapter(_path: str) -> tuple[extraction.ExtractedPage, ...]:
            raise RuntimeError(f"adapter failed: {secret}")

        result = extraction._enrich_pdf_result(
            extraction.ExtractResult("canonical", "fixture", 0.0, []),
            "fixture.pdf",
            failing_adapter,
        )
        self.assertEqual("PAGE_EVIDENCE_ADAPTER_FAILED", result.evidence_reason)
        assert_no_raw_diagnostic(self, result.to_dict(), (secret, "/private", "홍길동", "010-1234-5678", ".pdf", "adapter failed"))


if __name__ == "__main__":
    unittest.main()
