import json
import hashlib
import hmac
import math
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from document_masker_ocr_gui import (
    _canonical_json_hash,
    _continuity_evidence,
    _effective_policy_material,
    _header_label_candidates,
    _routing_title_evidence,
    _routing_title_signals,
    normalize_opts,
    trusted_analysis_manifest,
)
from document_routing import PdfRect
from document_routing import (
    CONTINUATION_NO_START_SIGNAL,
    CONTINUATION_PAGE_NUMBER_SEQUENCE,
    CONTINUATION_REPEATED_HEADER_FOOTER,
    PageEvidence,
    route_logical_documents,
)
from masking_evaluation import ManifestValidationError, lock_manifest, manifest_sha256, validate_manifest
from masking_extraction import ExtractResult, ExtractedPage, ExtractedWord
from privacy_spans import DetectionSpan
from scripts.generate_t35_mixed_text_scan_fixture import assert_fixture as assert_t35_fixture
from scripts.generate_t35_mixed_text_scan_fixture import TEXT_PAGE_MARKER
from scripts.generate_t35_mixed_text_scan_fixture import write_fixture as write_t35_fixture

SESSION_HASH_KEY = bytes(range(32))
CUSTOM_KEYWORD_TEXT_HASH = hashlib.sha256("공사기간\n연장".encode("utf-8")).hexdigest()


def _analysis_options(**overrides):
    return {
        "profile": "mixed",
        "auto_threshold": 0.85,
        "review_threshold": 0.5,
        **overrides,
    }


def _custom_keyword_extracted(*page_texts: str) -> ExtractResult:
    pages = []
    for page_index, text in enumerate(page_texts):
        words = tuple(
            ExtractedWord(
                match.group(),
                (40.0 + index * 70.0, 80.0, 100.0 + index * 70.0, 98.0),
                page_start=match.start(),
                page_end=match.end(),
                source="pymupdf_text_layer",
            )
            for index, match in enumerate(re.finditer(r"\S+", text))
        )
        pages.append(ExtractedPage(
            page_index,
            612.0,
            792.0,
            text,
            words,
            source="pymupdf_text_layer",
        ))
    return ExtractResult(
        text="\n".join(page_texts),
        engine_used="fixture",
        duration_sec=0.0,
        notes=[],
        pages=tuple(pages),
    )


class AnalysisManifestContractTests(unittest.TestCase):
    def test_running_title_evidence_normalizes_split_internal_title_in_top_zone(self):
        words = [
            (0, 20, ExtractedWord("「서울특별시 동작구 입양가정 지원에 관한 조례」", (110.0, 22.0, 500.0, 46.0)), {"x0": 110.0, "y0": 22.0, "x1": 500.0, "y1": 46.0}),
            (21, 35, ExtractedWord("일부개정조례안 검토보고(수정)", (180.0, 54.0, 430.0, 78.0)), {"x0": 180.0, "y0": 54.0, "x1": 430.0, "y1": 78.0}),
        ]

        signals, titles, kind = _routing_title_evidence(
            words,
            (PdfRect(0.0, 0.0, 612.0, 792.0),),
            top_zone_only=True,
        )

        self.assertEqual({"internal"}, signals)
        self.assertEqual("internal_review", kind)
        self.assertEqual(
            ("서울특별시동작구입양가정지원에관한조례일부개정조례안검토보고수정",),
            titles,
        )

    def test_follow_on_top_zone_exposes_title_fragments_without_start_signal(self):
        words = [
            (0, 20, ExtractedWord("「서울특별시 동작구 입양가정 지원에 관한 조례」", (111.0, 91.0, 487.0, 107.0)), {"x0": 111.0, "y0": 91.0, "x1": 487.0, "y1": 107.0}),
            (21, 28, ExtractedWord("일부개정조례안", (123.0, 112.0, 298.0, 137.0)), {"x0": 123.0, "y0": 112.0, "x1": 298.0, "y1": 137.0}),
        ]

        signals, titles, kind = _routing_title_evidence(
            words,
            (PdfRect(0.0, 0.0, 595.2, 841.92),),
            top_zone_only=True,
        )

        self.assertEqual(set(), signals)
        self.assertIsNone(kind)
        self.assertIn(
            "서울특별시동작구입양가정지원에관한조례일부개정조례안",
            titles,
        )

    def test_continuity_evidence_recognizes_page_sequence_and_repeated_header(self):
        first_words = [
            (0, 2, ExtractedWord("공통", (20.0, 20.0, 44.0, 32.0)), {"x0": 20.0, "y0": 20.0, "x1": 44.0, "y1": 32.0}),
            (3, 6, ExtractedWord("머리말", (48.0, 20.0, 78.0, 32.0)), {"x0": 48.0, "y0": 20.0, "x1": 78.0, "y1": 32.0}),
            (7, 8, ExtractedWord("-", (260.0, 748.0, 264.0, 760.0)), {"x0": 260.0, "y0": 748.0, "x1": 264.0, "y1": 760.0}),
            (9, 10, ExtractedWord("1", (268.0, 748.0, 274.0, 760.0)), {"x0": 268.0, "y0": 748.0, "x1": 274.0, "y1": 760.0}),
            (11, 12, ExtractedWord("-", (278.0, 748.0, 282.0, 760.0)), {"x0": 278.0, "y0": 748.0, "x1": 282.0, "y1": 760.0}),
        ]
        second_words = [
            (0, 2, ExtractedWord("공통", (20.0, 20.0, 44.0, 32.0)), {"x0": 20.0, "y0": 20.0, "x1": 44.0, "y1": 32.0}),
            (3, 6, ExtractedWord("머리말", (48.0, 20.0, 78.0, 32.0)), {"x0": 48.0, "y0": 20.0, "x1": 78.0, "y1": 32.0}),
            (7, 8, ExtractedWord("2", (260.0, 748.0, 264.0, 760.0)), {"x0": 260.0, "y0": 748.0, "x1": 264.0, "y1": 760.0}),
            (8, 9, ExtractedWord("/", (266.0, 748.0, 270.0, 760.0)), {"x0": 266.0, "y0": 748.0, "x1": 270.0, "y1": 760.0}),
            (10, 11, ExtractedWord("5", (272.0, 748.0, 276.0, 760.0)), {"x0": 272.0, "y0": 748.0, "x1": 276.0, "y1": 760.0}),
        ]

        _, page_number, edge_signatures = _continuity_evidence(
            first_words, 792.0, {"dispatch"}, None, frozenset(),
        )
        signals, _, _ = _continuity_evidence(
            second_words, 792.0, set(), page_number, edge_signatures,
        )

        self.assertEqual(
            {
                CONTINUATION_NO_START_SIGNAL,
                CONTINUATION_PAGE_NUMBER_SEQUENCE,
                CONTINUATION_REPEATED_HEADER_FOOTER,
            },
            signals,
        )

    def test_continuity_evidence_does_not_treat_nonsequential_footer_numbers_as_page_sequence(self):
        first_words = [
            (0, 1, ExtractedWord("1", (260.0, 748.0, 264.0, 760.0)), {"x0": 260.0, "y0": 748.0, "x1": 264.0, "y1": 760.0}),
            (2, 3, ExtractedWord("/", (266.0, 748.0, 270.0, 760.0)), {"x0": 266.0, "y0": 748.0, "x1": 270.0, "y1": 760.0}),
            (4, 5, ExtractedWord("5", (272.0, 748.0, 276.0, 760.0)), {"x0": 272.0, "y0": 748.0, "x1": 276.0, "y1": 760.0}),
        ]
        third_words = [
            (0, 1, ExtractedWord("3", (260.0, 748.0, 264.0, 760.0)), {"x0": 260.0, "y0": 748.0, "x1": 264.0, "y1": 760.0}),
            (2, 3, ExtractedWord("/", (266.0, 748.0, 270.0, 760.0)), {"x0": 266.0, "y0": 748.0, "x1": 270.0, "y1": 760.0}),
            (4, 5, ExtractedWord("5", (272.0, 748.0, 276.0, 760.0)), {"x0": 272.0, "y0": 748.0, "x1": 276.0, "y1": 760.0}),
        ]

        _, page_number, edge_signatures = _continuity_evidence(
            first_words, 792.0, {"dispatch"}, None, frozenset(),
        )
        signals, _, _ = _continuity_evidence(
            third_words, 792.0, set(), page_number, edge_signatures,
        )

        self.assertEqual({CONTINUATION_NO_START_SIGNAL}, signals)

    def test_standalone_footer_sequence_continues_confirmed_mixed_segment(self):
        first_words = [
            (0, 1, ExtractedWord("1", (260.0, 748.0, 264.0, 760.0)), {"x0": 260.0, "y0": 748.0, "x1": 264.0, "y1": 760.0}),
        ]
        second_words = [
            (0, 1, ExtractedWord("2", (260.0, 748.0, 264.0, 760.0)), {"x0": 260.0, "y0": 748.0, "x1": 264.0, "y1": 760.0}),
        ]

        _, first_page_number, first_edge_signatures = _continuity_evidence(
            first_words, 792.0, {"internal"}, None, frozenset(),
        )
        signals, _, _ = _continuity_evidence(
            second_words, 792.0, set(), first_page_number, first_edge_signatures,
        )
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(0, frozenset({"internal"}), boundary_confidence=0.9),
                PageEvidence(1, continuity_signals=signals),
            ],
            document_hash="a" * 64,
        )

        self.assertEqual(
            [("internal_review", "confirmed", (0, 1), False)],
            [(segment.kind, segment.state, segment.page_range, segment.common_only) for segment in result.segments],
        )

    def test_standalone_footer_restart_at_one_does_not_continue_mixed_segment(self):
        prior_words = [
            (0, 1, ExtractedWord("2", (260.0, 748.0, 264.0, 760.0)), {"x0": 260.0, "y0": 748.0, "x1": 264.0, "y1": 760.0}),
        ]
        restart_words = [
            (0, 1, ExtractedWord("1", (260.0, 748.0, 264.0, 760.0)), {"x0": 260.0, "y0": 748.0, "x1": 264.0, "y1": 760.0}),
        ]

        _, prior_page_number, prior_edge_signatures = _continuity_evidence(
            prior_words, 792.0, {"internal"}, None, frozenset(),
        )
        signals, _, _ = _continuity_evidence(
            restart_words, 792.0, set(), prior_page_number, prior_edge_signatures,
        )
        result = route_logical_documents(
            "mixed",
            [
                PageEvidence(0, frozenset({"internal"}), boundary_confidence=0.9),
                PageEvidence(1, continuity_signals=signals),
            ],
            document_hash="a" * 64,
        )

        self.assertEqual(
            [("internal_review", (0, 0)), ("unknown", (1, 1))],
            [(segment.kind, segment.page_range) for segment in result.segments],
        )

    def test_header_labels_normalize_spacing_suffixes_and_punctuation_at_header_start(self):
        words = (
            ExtractedWord("수", (10.0, 10.0, 18.0, 20.0), page_start=0, page_end=1),
            ExtractedWord("신", (24.0, 10.0, 32.0, 20.0), page_start=2, page_end=3),
            ExtractedWord("수신자", (10.0, 35.0, 45.0, 45.0), page_start=4, page_end=7),
            ExtractedWord("문서번호:", (10.0, 60.0, 70.0, 70.0), page_start=8, page_end=13),
        )

        labels = _header_label_candidates(words, (PdfRect(0.0, 0.0, 200.0, 200.0),))

        self.assertEqual(["수신", "수신", "문서번호"], [candidate[2] for candidate in labels])

    def test_spaced_recipient_suffix_is_consumed_as_part_of_the_label(self):
        words = (
            ExtractedWord("수", (10.0, 10.0, 18.0, 20.0), page_start=0, page_end=1),
            ExtractedWord("신", (24.0, 10.0, 32.0, 20.0), page_start=2, page_end=3),
            ExtractedWord("자", (38.0, 10.0, 46.0, 20.0), page_start=4, page_end=5),
            ExtractedWord("서울시장", (60.0, 10.0, 110.0, 20.0), page_start=6, page_end=10),
        )

        labels = _header_label_candidates(
            words,
            (PdfRect(0.0, 0.0, 200.0, 200.0),),
            header_only=False,
        )

        self.assertEqual((0, 2, "수신"), labels[0][:3])

    def test_non_header_sentence_does_not_emit_label_start_signal(self):
        words = (
            ExtractedWord("수신료", (80.0, 160.0, 120.0, 170.0), page_start=0, page_end=3),
            ExtractedWord("인상", (125.0, 160.0, 155.0, 170.0), page_start=4, page_end=6),
            ExtractedWord("검토", (160.0, 160.0, 190.0, 170.0), page_start=7, page_end=9),
        )

        labels = _header_label_candidates(words, (PdfRect(0.0, 0.0, 200.0, 200.0),))

        self.assertEqual([], labels)

    def test_centered_review_title_below_approval_header_routes_internal_review(self):
        # Given: shared approval-box metadata followed by a centered review title.
        text = "문서번호 정책과-12\n결재일자 2026. 8. 22.\n공개여부 대시민공개\n검토보고"
        review_start = text.index("검토보고")
        extracted = ExtractResult(
            text=text,
            engine_used="fixture",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(
                0, 612.0, 792.0, text,
                (
                    ExtractedWord("문서번호", (40.0, 100.0, 100.0, 112.0), page_start=0, page_end=4),
                    ExtractedWord("정책과-12", (120.0, 100.0, 180.0, 112.0), page_start=5, page_end=11),
                    ExtractedWord("결재일자", (40.0, 125.0, 100.0, 137.0), page_start=12, page_end=16),
                    ExtractedWord("2026.", (120.0, 125.0, 155.0, 137.0), page_start=17, page_end=22),
                    ExtractedWord("8.", (160.0, 125.0, 175.0, 137.0), page_start=23, page_end=25),
                    ExtractedWord("22.", (180.0, 125.0, 200.0, 137.0), page_start=26, page_end=29),
                    ExtractedWord("공개여부", (40.0, 150.0, 100.0, 162.0), page_start=30, page_end=34),
                    ExtractedWord("대시민공개", (120.0, 150.0, 185.0, 162.0), page_start=35, page_end=40),
                    ExtractedWord("검토보고", (260.0, 360.0, 350.0, 388.0), page_start=review_start, page_end=len(text)),
                ),
                source="pymupdf_text_layer",
            ),),
        )
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "review.pdf"
            source.write_bytes(b"%PDF-1.7\nreview-fixture")
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value="a" * 64),
            ):
                # When: mixed-profile routing analyzes the first page.
                manifest = trusted_analysis_manifest(
                    str(source), _analysis_options(), session_hash_key=SESSION_HASH_KEY,
                    extracted=extracted,
                )

        # Then: the shared header cannot override the review title.
        self.assertEqual(
            [("internal_review", "confirmed", False)],
            [(item["kind"], item["state"], item["common_only"]) for item in manifest["segments"]],
        )

    def test_review_token_in_predicate_ending_body_line_does_not_emit_internal_signal(self):
        # Given: a left-aligned body sentence that includes the exact review token.
        words = [
            (0, 4, ExtractedWord("검토보고", (40.0, 360.0, 130.0, 388.0)), {"x0": 40.0, "y0": 360.0, "x1": 130.0, "y1": 388.0}),
            (5, 10, ExtractedWord("결과를", (140.0, 360.0, 205.0, 388.0)), {"x0": 140.0, "y0": 360.0, "x1": 205.0, "y1": 388.0}),
            (11, 15, ExtractedWord("알립니다.", (215.0, 360.0, 290.0, 388.0)), {"x0": 215.0, "y0": 360.0, "x1": 290.0, "y1": 388.0}),
        ]

        # When: title qualification inspects the body line.
        signals = _routing_title_signals(words, (PdfRect(0.0, 0.0, 612.0, 792.0),))

        # Then: a sentence predicate disqualifies it as a standalone title.
        self.assertEqual(set(), signals)

    def test_centered_dispatch_title_below_header_zone_emits_dispatch_signal(self):
        # Given: a centered, standalone dispatch title below the approval header.
        words = [
            (0, 2, ExtractedWord("제목", (250.0, 360.0, 280.0, 374.0)), {"x0": 250.0, "y0": 360.0, "x1": 280.0, "y1": 374.0}),
            (3, 5, ExtractedWord("결과", (285.0, 360.0, 315.0, 374.0)), {"x0": 285.0, "y0": 360.0, "x1": 315.0, "y1": 374.0}),
            (6, 8, ExtractedWord("알림", (320.0, 360.0, 350.0, 374.0)), {"x0": 320.0, "y0": 360.0, "x1": 350.0, "y1": 374.0}),
        ]

        # When: title qualification inspects the centered line.
        signals = _routing_title_signals(words, (PdfRect(0.0, 0.0, 612.0, 792.0),))

        # Then: the exact dispatch token produces dispatch evidence.
        self.assertEqual({"dispatch"}, signals)

    def test_dispatch_title_label_rejects_predicate_bearing_line_with_midline_dispatch_token(self):
        # Given: a body sentence with a first-position title label and a midline dispatch token.
        words = [
            (0, 2, ExtractedWord("제목", (250.0, 360.0, 280.0, 374.0)), {"x0": 250.0, "y0": 360.0, "x1": 280.0, "y1": 374.0}),
            (3, 5, ExtractedWord("결과", (285.0, 360.0, 315.0, 374.0)), {"x0": 285.0, "y0": 360.0, "x1": 315.0, "y1": 374.0}),
            (6, 8, ExtractedWord("알림", (320.0, 360.0, 350.0, 374.0)), {"x0": 320.0, "y0": 360.0, "x1": 350.0, "y1": 374.0}),
            (9, 13, ExtractedWord("드립니다", (355.0, 360.0, 410.0, 374.0)), {"x0": 355.0, "y0": 360.0, "x1": 410.0, "y1": 374.0}),
            (14, 16, ExtractedWord("추가", (415.0, 360.0, 445.0, 374.0)), {"x0": 415.0, "y0": 360.0, "x1": 445.0, "y1": 374.0}),
        ]

        # When: title qualification inspects the predicate-bearing line.
        signals = _routing_title_signals(words, (PdfRect(0.0, 0.0, 612.0, 792.0),))

        # Then: the label and midline token cannot create dispatch start evidence.
        self.assertEqual(set(), signals)

    def test_mid_page_recipient_header_routes_dispatch_despite_body_first_text(self):
        # Given: a dispatch whose text stream begins with body prose before its page-positioned header.
        text = "1. 귀 기관의 발전을 기원합니다.\n수신 서울특별시\n제목 결과 알림"
        recipient_start = text.index("수신")
        title_start = text.index("제목")
        extracted = ExtractResult(
            text=text,
            engine_used="fixture",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(
                0, 612.0, 792.0, text,
                (
                    ExtractedWord("1.", (40.0, 30.0, 55.0, 42.0), page_start=0, page_end=2),
                    ExtractedWord("귀", (60.0, 30.0, 75.0, 42.0), page_start=3, page_end=4),
                    ExtractedWord("기관의", (80.0, 30.0, 120.0, 42.0), page_start=5, page_end=8),
                    ExtractedWord("발전을", (125.0, 30.0, 165.0, 42.0), page_start=9, page_end=12),
                    ExtractedWord("기원합니다.", (170.0, 30.0, 240.0, 42.0), page_start=13, page_end=19),
                    ExtractedWord("수신", (60.0, 430.0, 90.0, 444.0), page_start=recipient_start, page_end=recipient_start + 2),
                    ExtractedWord("서울특별시", (115.0, 430.0, 190.0, 444.0), page_start=recipient_start + 3, page_end=recipient_start + 8),
                    ExtractedWord("제목", (60.0, 460.0, 90.0, 474.0), page_start=title_start, page_end=title_start + 2),
                    ExtractedWord("결과", (115.0, 460.0, 145.0, 474.0), page_start=title_start + 3, page_end=title_start + 5),
                    ExtractedWord("알림", (150.0, 460.0, 180.0, 474.0), page_start=title_start + 6, page_end=len(text)),
                ),
                source="pymupdf_text_layer",
            ),),
        )
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "dispatch.pdf"
            source.write_bytes(b"%PDF-1.7\ndispatch-fixture")
            with patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector):
                # When: mixed-profile routing analyzes the page geometry rather than text order.
                manifest = trusted_analysis_manifest(
                    str(source), _analysis_options(), session_hash_key=SESSION_HASH_KEY,
                    extracted=extracted,
                )

        # Then: the later-in-stream recipient row decides dispatch kind.
        self.assertEqual(
            [("official_dispatch", "confirmed", False)],
            [(item["kind"], item["state"], item["common_only"]) for item in manifest["segments"]],
        )

    def test_attachment_header_routes_clean_mixed_document_without_review(self):
        # Given: a top-of-page attachment header with trustworthy text-layer geometry.
        text = "붙임"
        extracted = ExtractResult(
            text=text,
            engine_used="fixture",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(
                0,
                612.0,
                792.0,
                text,
                (ExtractedWord(
                    text,
                    (10.0, 10.0, 30.0, 20.0),
                    page_start=0,
                    page_end=len(text),
                    source="pymupdf_text_layer",
                ),),
                source="pymupdf_text_layer",
            ),),
        )
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "attachment.pdf"
            source.write_bytes(b"%PDF-1.7\nattachment-fixture")
            with patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector):
                # When: the public manifest is built with the mixed profile.
                manifest = trusted_analysis_manifest(
                    str(source), _analysis_options(), session_hash_key=SESSION_HASH_KEY,
                    extracted=extracted,
                )

        # Then: attachment routing has no official-layout obligations or review work.
        self.assertEqual(
            [("attachment", "confirmed", False)],
            [(item["kind"], item["state"], item["common_only"]) for item in manifest["segments"]],
        )
        self.assertEqual([], manifest["regions"])
        self.assertEqual([], manifest["occurrences"])
        self.assertEqual([], manifest["review_items"])
        self.assertEqual("absent", manifest["approval_coverage"]["state"])
        self.assertFalse(manifest["required_region_coverage"]["blocking"])

    def test_public_profile_reanalysis_accepts_a_legal_boundary_correction(self):
        text = "붙임"
        extracted = ExtractResult(
            text=text,
            engine_used="fixture",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(
                0,
                612.0,
                792.0,
                text,
                (ExtractedWord(
                    text,
                    (10.0, 10.0, 30.0, 20.0),
                    page_start=0,
                    page_end=len(text),
                    source="pymupdf_text_layer",
                ),),
                source="pymupdf_text_layer",
            ),),
        )
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "public-legal-segment.pdf"
            source.write_bytes(b"%PDF-1.7\npublic-legal-segment")
            with patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector):
                manifest = trusted_analysis_manifest(
                    str(source),
                    _analysis_options(),
                    session_hash_key=SESSION_HASH_KEY,
                    extracted=extracted,
                    reanalysis={
                        "kind": "boundary",
                        "page_start": 0,
                        "page_end": 0,
                        "segment_kind": "legal",
                        "analysis_revision": 2,
                    },
                )

        self.assertEqual("mixed", manifest["profile"])
        self.assertEqual(2, manifest["analysis_revision"])
        self.assertEqual(
            [("legal", "user_confirmed", False)],
            [(item["kind"], item["state"], item["common_only"]) for item in manifest["segments"]],
        )

    def test_public_profile_partial_boundary_reanalysis_keeps_confirmed_fragments(self):
        extracted = _custom_keyword_extracted("본문 1", "본문 2", "본문 3")
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "public-partial-boundary.pdf"
            source_bytes = b"%PDF-1.7\npublic-partial-boundary"
            source.write_bytes(source_bytes)
            document_sha256 = hashlib.sha256(source_bytes).hexdigest()
            with patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector):
                for profile in ("internal_review", "official_dispatch"):
                    with self.subTest(profile=profile):
                        manifest = trusted_analysis_manifest(
                            str(source),
                            _analysis_options(
                                profile=profile,
                                profile_authority={
                                    "document_sha256": document_sha256,
                                    "analysis_revision": 1,
                                    "profile": profile,
                                    "decision_code": "profile_confirmed",
                                },
                            ),
                            session_hash_key=SESSION_HASH_KEY,
                            extracted=extracted,
                            reanalysis={
                                "kind": "boundary",
                                "page_start": 1,
                                "page_end": 1,
                                "segment_kind": profile,
                                "analysis_revision": 2,
                            },
                        )

                        self.assertEqual(
                            [(profile, "confirmed", 0, 0), (profile, "user_confirmed", 1, 1), (profile, "confirmed", 2, 2)],
                            [(item["kind"], item["state"], item["page_start"], item["page_end"]) for item in manifest["segments"]],
                        )
                        self.assertFalse(any(
                            "profile_authority_missing" in item["reason_codes"]
                            for item in manifest["review_items"]
                        ))

    def test_follow_on_footer_page_number_inherits_confirmed_dispatch_segment(self):
        first_text = "시행 서울시 - 1 -"
        second_text = "- 2 -"
        extracted = ExtractResult(
            text=f"{first_text}\n{second_text}",
            engine_used="fixture",
            duration_sec=0.0,
            notes=[],
            pages=(
                ExtractedPage(
                    0,
                    612.0,
                    792.0,
                    first_text,
                    (
                        ExtractedWord("시행", (20.0, 20.0, 40.0, 32.0), page_start=0, page_end=2),
                        ExtractedWord("서울시", (48.0, 20.0, 78.0, 32.0), page_start=3, page_end=6),
                        ExtractedWord("-", (260.0, 748.0, 264.0, 760.0), page_start=7, page_end=8),
                        ExtractedWord("1", (268.0, 748.0, 274.0, 760.0), page_start=9, page_end=10),
                        ExtractedWord("-", (278.0, 748.0, 282.0, 760.0), page_start=11, page_end=12),
                    ),
                    source="pymupdf_text_layer",
                ),
                ExtractedPage(
                    1,
                    612.0,
                    792.0,
                    second_text,
                    (
                        ExtractedWord("-", (260.0, 748.0, 264.0, 760.0), page_start=0, page_end=1),
                        ExtractedWord("2", (268.0, 748.0, 274.0, 760.0), page_start=2, page_end=3),
                        ExtractedWord("-", (278.0, 748.0, 282.0, 760.0), page_start=4, page_end=5),
                    ),
                    source="pymupdf_text_layer",
                ),
            ),
        )
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "dispatch.pdf"
            source.write_bytes(b"%PDF-1.7\ndispatch-fixture")
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value="a" * 64),
            ):
                manifest = trusted_analysis_manifest(
                    str(source), _analysis_options(), session_hash_key=SESSION_HASH_KEY,
                    extracted=extracted,
                )

        self.assertEqual(
            [("official_dispatch", "confirmed", 0, 1, False)],
            [
                (item["kind"], item["state"], item["page_start"], item["page_end"], item["common_only"])
                for item in manifest["segments"]
            ],
        )
        self.assertFalse(manifest["required_region_coverage"]["blocking"])

    def test_options_hash_matches_native_canonical_public_options(self):
        public_options = {
            "rrn": True, "phone": True, "business_reg": True, "name": True,
            "address": True, "place": True, "legal_party": True, "company": True,
            "court": True, "case_title": True, "case_number": True, "law_firm": True,
            "attorney": True, "approval_line": True, "region_context": True,
            "doc_meta": True, "email": True, "pdf_redaction": True, "custom_keywords": "",
            "extract_engine": "auto", "profile": "mixed",
            "output_artifacts": "pdf_safe_report", "display_mode": "black",
            "deidentification_policy": "token", "region_scope": "document",
            "custom_regions": "", "return_text_preview": False,
            "auto_mask_threshold": 0.85, "review_threshold": 0.5,
        }
        material = _effective_policy_material(normalize_opts(public_options))

        self.assertEqual(public_options, material)
        self.assertEqual(64, len(_canonical_json_hash(material)))
        self.assertEqual(
            hashlib.sha256(json.dumps(
                public_options, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            ).encode("utf-8")).hexdigest(),
            _canonical_json_hash(material),
        )

    def test_options_hash_changes_when_email_detection_changes(self):
        enabled = _effective_policy_material(normalize_opts(_analysis_options(email=True)))
        disabled = _effective_policy_material(normalize_opts(_analysis_options(email=False)))

        self.assertNotEqual(_canonical_json_hash(enabled), _canonical_json_hash(disabled))

    def test_options_hash_changes_when_custom_keywords_change(self):
        empty_keyword = _effective_policy_material(normalize_opts(_analysis_options(custom_keywords="")))
        with_keyword = _effective_policy_material(normalize_opts(_analysis_options(custom_keywords="공사기간 연장")))

        self.assertNotEqual(_canonical_json_hash(empty_keyword), _canonical_json_hash(with_keyword))

    def test_boxed_internal_review_metadata_rows_emit_confirmed_value_only_candidates(self):
        # Given: Dongjak and Gwanak header-table variants with trusted value-cell geometry.
        rows = (
            ("문서번호", "핵심정책추진단-3770", 60.0),
            ("방침번호", "2026-동작-17", 90.0),
            ("생산등록번호", "건축과-1526", 120.0),
            ("등록일", "2026. 8. 25.", 150.0),
            ("결재일", "2026. 8. 26.", 180.0),
        )
        text = "\n".join([*(f"{label} {value}" for label, value, _y in rows), "검토보고"])
        cursor = 0
        words = []
        expected_hashes = {}
        drawings = []
        for label, value, y0 in rows:
            label_start = text.index(label, cursor)
            value_start = text.index(value, label_start + len(label))
            label_rect = (20.0, y0, 90.0, y0 + 14.0)
            value_rect = (110.0, y0, 280.0, y0 + 14.0)
            words.extend((
                ExtractedWord(label, label_rect, page_start=label_start, page_end=label_start + len(label)),
                ExtractedWord(value, value_rect, page_start=value_start, page_end=value_start + len(value)),
            ))
            expected_hashes[tuple(value_rect)] = hashlib.sha256(value.encode("utf-8")).hexdigest()
            drawings.append((10.0, y0 - 4.0, 300.0, y0 + 18.0))
            cursor = value_start + len(value)
        title_start = text.index("검토보고", cursor)
        words.append(ExtractedWord("검토보고", (260.0, 360.0, 350.0, 388.0), page_start=title_start, page_end=len(text)))
        extracted = ExtractResult(
            text=text,
            engine_used="fixture",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(0, 612.0, 792.0, text, tuple(words), tuple(drawings), source="pymupdf_text_layer"),),
        )
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()

        def text_hash(_path, _page, rects):
            return expected_hashes.get(tuple(rects[0]))

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "internal-header.pdf"
            source.write_bytes(b"%PDF-1.7\ninternal-header-fixture")
            options = _analysis_options(
                profile="internal_review",
                profile_authority={
                    "document_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "analysis_revision": 1,
                    "profile": "internal_review",
                    "decision_code": "profile_confirmed",
                },
            )
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", side_effect=text_hash),
            ):
                # When: the internal-review profile analyzes the boxed header rows.
                manifest = trusted_analysis_manifest(
                    str(source), options,
                    session_hash_key=SESSION_HASH_KEY, extracted=extracted,
                )

        # Then: every header value is auto-confirmed while the label cells remain outside occurrence geometry.
        self.assertEqual("present", next(
            item["state"] for item in manifest["required_region_coverage"]["kinds"] if item["kind"] == "header_meta"
        ))
        header_regions = [item for item in manifest["regions"] if item["kind"] == "header_meta"]
        self.assertEqual(len(rows), len(header_regions))
        self.assertTrue(all(item["state"] == "confirmed" for item in header_regions))
        header_values = [item for item in manifest["occurrences"] if item["category"] == "header_meta"]
        self.assertEqual(len(rows), len(header_values))
        self.assertTrue(all(item["proposed_action"] == "mask" for item in header_values))
        self.assertTrue(all(item["rects"][0]["x0"] == 110.0 for item in header_values))

    def test_boxed_dispatch_footer_contact_rows_emit_all_sensitive_values(self):
        # Given: a dispatch footer with a postal code, phone, fax, and email beside preserved public address and URL rows.
        header = ("시행", "총무과-12", 30.0)
        footer_rows = (
            ("우편번호", "03718", 680.0),
            ("전화", "02-1234-5678", 705.0),
            ("전송", "02-7654-3210", 730.0),
            ("이메일", "privacy@example.go.kr", 755.0),
        )
        public_rows = (("주소", "서울특별시 동작구 장승배기로 161", 650.0), ("홈페이지", "https://www.dongjak.go.kr", 665.0))
        text = "\n".join((
            f"{header[0]} {header[1]}",
            *(f"{label} {value}" for label, value, _y in public_rows),
            *(f"{label} {value}" for label, value, _y in footer_rows),
        ))
        cursor = 0
        words = []
        expected_hashes = {}
        drawings = []
        for label, value, y0 in (header, *public_rows, *footer_rows):
            label_start = text.index(label, cursor)
            value_start = text.index(value, label_start + len(label))
            label_rect = (20.0, y0, 90.0, y0 + 14.0)
            value_rect = (110.0, y0, 290.0, y0 + 14.0)
            words.extend((
                ExtractedWord(label, label_rect, page_start=label_start, page_end=label_start + len(label)),
                ExtractedWord(value, value_rect, page_start=value_start, page_end=value_start + len(value)),
            ))
            expected_hashes[tuple(value_rect)] = hashlib.sha256(value.encode("utf-8")).hexdigest()
            drawings.append((10.0, y0 - 4.0, 310.0, y0 + 18.0))
            cursor = value_start + len(value)
        extracted = ExtractResult(
            text=text,
            engine_used="fixture",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(0, 612.0, 792.0, text, tuple(words), tuple(drawings), source="pymupdf_text_layer"),),
        )
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()

        def text_hash(_path, _page, rects):
            return expected_hashes.get(tuple(rects[0]))

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "dispatch-footer.pdf"
            source.write_bytes(b"%PDF-1.7\ndispatch-footer-fixture")
            options = _analysis_options(
                profile="official_dispatch",
                profile_authority={
                    "document_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "analysis_revision": 1,
                    "profile": "official_dispatch",
                    "decision_code": "profile_confirmed",
                },
            )
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", side_effect=text_hash),
            ):
                # When: the official-dispatch profile analyzes every boxed footer contact row.
                manifest = trusted_analysis_manifest(
                    str(source), options,
                    session_hash_key=SESSION_HASH_KEY, extracted=extracted,
                )

        # Then: each sensitive footer value is auto-confirmed through footer_contact, not the public institution metadata path.
        footer_regions = [item for item in manifest["regions"] if item["kind"] == "footer_contact"]
        self.assertEqual(len(footer_rows), len(footer_regions))
        self.assertTrue(all(item["state"] == "confirmed" for item in footer_regions))
        footer_values = [item for item in manifest["occurrences"] if item["category"] == "footer_contact"]
        self.assertEqual(len(footer_rows), len(footer_values))
        self.assertTrue(all(item["proposed_action"] == "mask" for item in footer_values))

    def test_inline_dispatch_footer_band_masks_sensitive_values_and_preserves_institution_address(self):
        header = ("시행", "총무과-12", 30.0)
        footer_words = (
            ("우03718", 20.0), ("서울특별시 동작구 장승배기로 161", 95.0),
            ("https://www.dongjak.go.kr", 280.0), ("전화", 410.0),
            ("02-1234-5678", 450.0), ("/전송", 20.0),
            ("02-7654-3210", 65.0), ("privacy@example.go.kr", 175.0),
        )
        text = " ".join((header[0], header[1], *(value for value, _x in footer_words)))
        cursor = 0
        words = []
        expected_hashes = {}
        for value, x0 in ((header[0], 20.0), (header[1], 110.0), *footer_words):
            start = text.index(value, cursor)
            y0 = header[2] if value in header[:2] else 755.0
            rect = (x0, y0, x0 + max(45.0, len(value) * 6.0), y0 + 14.0)
            words.append(ExtractedWord(value, rect, page_start=start, page_end=start + len(value)))
            expected_hashes[tuple(rect)] = hashlib.sha256(value.encode("utf-8")).hexdigest()
            cursor = start + len(value)
        extracted = ExtractResult(
            text=text,
            engine_used="fixture",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(0, 612.0, 792.0, text, tuple(words), (), source="pymupdf_text_layer"),),
        )
        address = footer_words[1][0]
        address_start = text.index(address)
        detector = type("Detector", (), {"detect": lambda _self, _text: [DetectionSpan(
            id="fixture-address", label="address", start=address_start, end=address_start + len(address),
            length=len(address), source="fixture_detector", confidence=1.0, action="review", evidence=("pattern",),
        )]})()

        def text_hash(_path, _page, rects):
            return expected_hashes.get(tuple(rects[0]))

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "inline-dispatch-footer.pdf"
            source.write_bytes(b"%PDF-1.7\ninline-dispatch-footer-fixture")
            options = _analysis_options(
                profile="official_dispatch",
                profile_authority={
                    "document_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "analysis_revision": 1,
                    "profile": "official_dispatch",
                    "decision_code": "profile_confirmed",
                },
            )
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", side_effect=text_hash),
            ):
                manifest = trusted_analysis_manifest(
                    str(source), options,
                    session_hash_key=SESSION_HASH_KEY, extracted=extracted,
                )

        footer_values = [item for item in manifest["occurrences"] if item["category"] == "footer_contact"]
        self.assertEqual(4, len(footer_values))
        self.assertTrue(all(item["state"] == "confirmed" and item["proposed_action"] == "mask" for item in footer_values))
        self.assertFalse(any(item["category"] == "address" for item in manifest["occurrences"]))

    def test_trusted_analysis_emits_confirmed_custom_keyword_occurrence_with_exact_page_rects(self):
        # Given: user-declared multiword keyword evidence in a public document page.
        extracted = _custom_keyword_extracted("공사기간 연장")
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "custom-keyword.pdf"
            source.write_bytes(b"%PDF-1.7\ncustom-keyword-fixture")
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value=CUSTOM_KEYWORD_TEXT_HASH),
            ):
                # When: public analysis receives the custom keyword option.
                for profile in ("internal_review", "official_dispatch"):
                    with self.subTest(profile=profile):
                        manifest = trusted_analysis_manifest(
                            str(source),
                            _analysis_options(profile=profile, custom_keywords="공사기간 연장"),
                            session_hash_key=SESSION_HASH_KEY,
                            extracted=extracted,
                        )

                        # Then: the explicit keyword is immediately maskable without a name review gate.
                        occurrences = [item for item in manifest["occurrences"] if item["category"] == "custom_keyword"]
                        self.assertEqual(1, len(occurrences))
                        self.assertEqual(0, occurrences[0]["page"])
                        self.assertEqual(
                            [
                                {"x0": 40.0, "y0": 80.0, "x1": 100.0, "y1": 98.0},
                                {"x0": 110.0, "y0": 80.0, "x1": 170.0, "y1": 98.0},
                            ],
                            occurrences[0]["rects"],
                        )
                        self.assertEqual("mask", occurrences[0]["proposed_action"])
                        self.assertEqual("confirmed", occurrences[0]["state"])
                        self.assertEqual("custom_keyword", occurrences[0]["provenance"])

    def test_trusted_analysis_matches_spaced_keyword_only_within_adjacent_word_geometry(self):
        text = "행 정 지\n원 과\n행 정 지 원 과"
        nearby_rects = [
            (40.0, 80.0, 52.0, 98.0),
            (62.0, 80.0, 74.0, 98.0),
            (84.0, 80.0, 96.0, 98.0),
            (40.0, 105.0, 52.0, 123.0),
            (62.0, 105.0, 74.0, 123.0),
        ]
        distant_rects = [
            (40.0, 200.0, 52.0, 218.0),
            (62.0, 270.0, 74.0, 288.0),
            (84.0, 340.0, 96.0, 358.0),
            (106.0, 410.0, 118.0, 428.0),
            (128.0, 480.0, 140.0, 498.0),
        ]
        words = []
        cursor = 0
        for index, character in enumerate("행정지원과행정지원과"):
            start = text.index(character, cursor)
            cursor = start + 1
            rect = (nearby_rects + distant_rects)[index]
            words.append(ExtractedWord(
                character,
                rect,
                page_start=start,
                page_end=start + 1,
                source="pymupdf_text_layer",
            ))
        extracted = ExtractResult(
            text=text,
            engine_used="fixture",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(0, 612.0, 792.0, text, tuple(words), (), source="pymupdf_text_layer"),),
        )
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()
        expected_hash = hashlib.sha256("행\n원\n정\n과\n지".encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "spaced-keyword.pdf"
            source.write_bytes(b"%PDF-1.7\nspaced-keyword-fixture")
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value=expected_hash),
            ):
                manifest = trusted_analysis_manifest(
                    str(source),
                    _analysis_options(custom_keywords="행정지원과"),
                    session_hash_key=SESSION_HASH_KEY,
                    extracted=extracted,
                )

        occurrences = [item for item in manifest["occurrences"] if item["category"] == "custom_keyword"]
        self.assertEqual(1, len(occurrences))
        self.assertEqual(
            [
                {"x0": 40.0, "y0": 80.0, "x1": 52.0, "y1": 98.0},
                {"x0": 62.0, "y0": 80.0, "x1": 74.0, "y1": 98.0},
                {"x0": 84.0, "y0": 80.0, "x1": 96.0, "y1": 98.0},
                {"x0": 40.0, "y0": 105.0, "x1": 52.0, "y1": 123.0},
                {"x0": 62.0, "y0": 105.0, "x1": 74.0, "y1": 123.0},
            ],
            occurrences[0]["rects"],
        )

    def test_trusted_analysis_keyword_matching_uses_the_same_punctuation_free_token(self):
        extracted = _custom_keyword_extracted("공사-기간")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "punctuated-keyword.pdf"
            document = fitz.open()
            document.new_page().insert_text((40, 90), "공사-기간")
            document.save(source)
            document.close()

            with patch(
                "document_masker_ocr_gui.occurrence_rect_text_hash",
                return_value=hashlib.sha256("공사-기간".encode("utf-8")).hexdigest(),
            ):
                manifest = trusted_analysis_manifest(
                    str(source),
                    _analysis_options(custom_keywords="공사기간"),
                    session_hash_key=SESSION_HASH_KEY,
                    extracted=extracted,
                )

        occurrences = [
            item for item in manifest["occurrences"] if item["category"] == "custom_keyword"
        ]
        self.assertEqual(1, len(occurrences))
        self.assertEqual("confirmed", occurrences[0]["state"])
        self.assertEqual(
            [{"x0": 40.0, "y0": 80.0, "x1": 100.0, "y1": 98.0}],
            occurrences[0]["rects"],
        )

    def test_trusted_analysis_keeps_keyword_substrings_inside_extractor_words(self):
        text = "담당행정지원과장"
        rect = (40.0, 80.0, 160.0, 98.0)
        extracted = ExtractResult(
            text=text,
            engine_used="fixture",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(
                0,
                612.0,
                792.0,
                text,
                (ExtractedWord(text, rect, page_start=0, page_end=len(text), source="pymupdf_text_layer"),),
                (),
                source="pymupdf_text_layer",
            ),),
        )
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()
        expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "keyword-substring.pdf"
            source.write_bytes(b"%PDF-1.7\nkeyword-substring-fixture")
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value=expected_hash),
            ):
                manifest = trusted_analysis_manifest(
                    str(source),
                    _analysis_options(custom_keywords="행정지원과"),
                    session_hash_key=SESSION_HASH_KEY,
                    extracted=extracted,
                )

        occurrences = [item for item in manifest["occurrences"] if item["category"] == "custom_keyword"]
        self.assertEqual(1, len(occurrences))
        self.assertEqual([{"x0": 40.0, "y0": 80.0, "x1": 160.0, "y1": 98.0}], occurrences[0]["rects"])

    def test_trusted_analysis_omits_custom_keyword_occurrences_when_keyword_is_absent(self):
        # Given: trusted text-layer evidence that does not contain the configured keyword.
        extracted = _custom_keyword_extracted("공사기간 조정")
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "missing-custom-keyword.pdf"
            source.write_bytes(b"%PDF-1.7\nmissing-custom-keyword-fixture")
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value=CUSTOM_KEYWORD_TEXT_HASH),
            ):
                # When: the absent keyword is analyzed.
                manifest = trusted_analysis_manifest(
                    str(source),
                    _analysis_options(custom_keywords="공사기간 연장"),
                    session_hash_key=SESSION_HASH_KEY,
                    extracted=extracted,
                )

        # Then: no custom keyword occurrence is invented.
        self.assertEqual([], [item for item in manifest["occurrences"] if item["category"] == "custom_keyword"])

    def test_trusted_analysis_emits_each_custom_keyword_match_across_source_pages(self):
        # Given: multiple matches for the same explicit keyword across two public pages.
        extracted = _custom_keyword_extracted("공사기간 연장", "공사기간 연장 일반 공사기간 연장")
        detector = type("Detector", (), {"detect": lambda _self, _text: []})()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "multi-page-custom-keyword.pdf"
            source.write_bytes(b"%PDF-1.7\nmulti-page-custom-keyword-fixture")
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value=CUSTOM_KEYWORD_TEXT_HASH),
            ):
                # When: the public manifest is rebuilt from both pages.
                manifest = trusted_analysis_manifest(
                    str(source),
                    _analysis_options(custom_keywords="공사기간 연장"),
                    session_hash_key=SESSION_HASH_KEY,
                    extracted=extracted,
                )

        # Then: each page retains only the rectangles that prove its match.
        occurrences = [item for item in manifest["occurrences"] if item["category"] == "custom_keyword"]
        self.assertEqual([0, 1, 1], [item["page"] for item in occurrences])
        self.assertEqual(
            [
                [
                    {"x0": 40.0, "y0": 80.0, "x1": 100.0, "y1": 98.0},
                    {"x0": 110.0, "y0": 80.0, "x1": 170.0, "y1": 98.0},
                ],
                [
                    {"x0": 40.0, "y0": 80.0, "x1": 100.0, "y1": 98.0},
                    {"x0": 110.0, "y0": 80.0, "x1": 170.0, "y1": 98.0},
                ],
                [
                    {"x0": 250.0, "y0": 80.0, "x1": 310.0, "y1": 98.0},
                    {"x0": 320.0, "y0": 80.0, "x1": 380.0, "y1": 98.0},
                ],
            ],
            [item["rects"] for item in occurrences],
        )

    def test_mixed_text_and_scanned_pages_keep_text_detection_and_create_scan_review(self):
        text = "시행 공사기간 연장"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mixed-text-scan.pdf"
            write_t35_fixture(source)
            self.assertEqual(3, assert_t35_fixture(source)["pages"])
            keyword_start = text.index("공사기간")
            extracted = ExtractResult(
                text=text,
                engine_used="fixture",
                duration_sec=0.0,
                notes=[],
                pages=(
                    ExtractedPage(
                        0,
                        612.0,
                        792.0,
                        text,
                        (
                            ExtractedWord("시행", (20.0, 20.0, 40.0, 32.0), page_start=0, page_end=2, source="pymupdf_text_layer"),
                            ExtractedWord("공사기간", (50.0, 80.0, 110.0, 98.0), page_start=keyword_start, page_end=keyword_start + 4, source="pymupdf_text_layer"),
                            ExtractedWord("연장", (120.0, 80.0, 170.0, 98.0), page_start=keyword_start + 5, page_end=keyword_start + 7, source="pymupdf_text_layer"),
                        ),
                        source="pymupdf_text_layer",
                    ),
                    ExtractedPage(1, 612.0, 792.0, "", (), source="scan", evidence_status="gap", evidence_reason="PAGE_EVIDENCE_ADAPTER_UNAVAILABLE"),
                    ExtractedPage(2, 612.0, 792.0, "", (), source="scan", evidence_status="gap", evidence_reason="PAGE_EVIDENCE_ADAPTER_UNAVAILABLE"),
                ),
            )
            detector = type("Detector", (), {"detect": lambda _self, _text: []})()
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value=CUSTOM_KEYWORD_TEXT_HASH),
            ):
                manifest = trusted_analysis_manifest(
                    str(source),
                    _analysis_options(custom_keywords="공사기간 연장"),
                    session_hash_key=SESSION_HASH_KEY,
                    extracted=extracted,
                )

        self.assertEqual(
            [("official_dispatch", "confirmed", 0, 0, False), ("unknown", "review_required", 1, 2, False)],
            [(item["kind"], item["state"], item["page_start"], item["page_end"], item["common_only"])
             for item in manifest["segments"]],
        )
        self.assertEqual([(0, "KEYWORD")], [(item["page"], item["tag"]) for item in manifest["occurrences"]])
        self.assertEqual(
            [("acknowledge", 1, 2, ("scanned_geometry_unavailable",), True, False)],
            [(item["kind"], item["page_start"], item["page_end"], tuple(item["reason_codes"]), item["requires_acknowledgment"], item["common_only"])
             for item in manifest["review_items"]],
        )

    def test_generated_mixed_fixture_uses_real_extraction_and_keeps_text_detection(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "mixed-text-scan.pdf"
            write_t35_fixture(source)
            fixture_result = assert_t35_fixture(source)
            detector = type("Detector", (), {"detect": lambda _self, _text: []})()
            with patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector):
                manifest = trusted_analysis_manifest(
                    str(source),
                    _analysis_options(custom_keywords="construction-period extension"),
                    session_hash_key=SESSION_HASH_KEY,
                )

        self.assertEqual(3, fixture_result["pages"])
        self.assertIn("construction-period extension", TEXT_PAGE_MARKER)
        self.assertEqual([(0, "KEYWORD")], [(item["page"], item["tag"]) for item in manifest["occurrences"]])
        self.assertEqual(
            [(1, 2, "scanned_geometry_unavailable")],
            [(item["page_start"], item["page_end"], item["source"])
             for item in manifest["segments"] if item["source"] == "scanned_geometry_unavailable"],
        )
        self.assertEqual(
            [(1, 2, ("scanned_geometry_unavailable",))],
            [(item["page_start"], item["page_end"], tuple(item["reason_codes"]))
             for item in manifest["review_items"] if "scanned_geometry_unavailable" in item["reason_codes"]],
        )

    def test_routing_review_spanning_scan_pages_is_preserved_for_the_text_range(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ambiguous-text-scan.pdf"
            document = fitz.open()
            document.new_page().insert_text((72, 72), "body")
            document.new_page().draw_rect((20, 20, 200, 200), color=None, fill=(0.8, 0.8, 0.8))
            document.save(source)
            document.close()
            extracted = ExtractResult(
                text="본문",
                engine_used="fixture",
                duration_sec=0.0,
                notes=[],
                pages=(
                    ExtractedPage(0, 612.0, 792.0, "본문", (
                        ExtractedWord("본문", (72.0, 60.0, 100.0, 74.0), page_start=0, page_end=2, source="pymupdf_text_layer"),
                    ), source="pymupdf_text_layer"),
                    ExtractedPage(1, 612.0, 792.0, "", (), source="scan", evidence_status="gap", evidence_reason="PAGE_EVIDENCE_ADAPTER_UNAVAILABLE"),
                ),
            )
            detector = type("Detector", (), {"detect": lambda _self, _text: []})()
            with patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector):
                manifest = trusted_analysis_manifest(
                    str(source), _analysis_options(), session_hash_key=SESSION_HASH_KEY, extracted=extracted,
                )

        self.assertEqual(
            [(0, 0, "routing"), (1, 1, "scanned_geometry_unavailable")],
            [(item["page_start"], item["page_end"], item["source"]) for item in manifest["segments"]],
        )
        self.assertEqual(
            [
                (1, 1, "extraction_evidence", ("scanned_geometry_unavailable",)),
                (0, 0, "routing", ("ambiguous_boundary", "unrecognized_start_signals")),
            ],
            [(item["page_start"], item["page_end"], item["provenance"], tuple(item["reason_codes"]))
             for item in manifest["review_items"]],
        )

    def test_scanned_only_pages_degrade_to_an_acknowledgment_instead_of_rejecting_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "scanned-only.pdf"
            document = fitz.open()
            for _page in range(2):
                scanned_page = document.new_page()
                scanned_page.draw_rect(scanned_page.rect, color=None, fill=(0.85, 0.85, 0.85))
            document.save(source)
            document.close()
            extracted = ExtractResult(
                text="",
                engine_used="fixture",
                duration_sec=0.0,
                notes=[],
                pages=tuple(
                    ExtractedPage(index, 612.0, 792.0, "", (), source="scan", evidence_status="gap", evidence_reason="PAGE_EVIDENCE_ADAPTER_UNAVAILABLE")
                    for index in range(2)
                ),
            )
            detector = type("Detector", (), {"detect": lambda _self, _text: []})()
            with patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector):
                manifest = trusted_analysis_manifest(
                    str(source), _analysis_options(), session_hash_key=SESSION_HASH_KEY, extracted=extracted,
                )

        self.assertEqual(
            [("unknown", "review_required", 0, 1, False, "scanned_geometry_unavailable")],
            [(item["kind"], item["state"], item["page_start"], item["page_end"], item["common_only"], item["source"])
             for item in manifest["segments"]],
        )
        self.assertEqual(
            [("acknowledge", 0, 1, ("scanned_geometry_unavailable",), True)],
            [(item["kind"], item["page_start"], item["page_end"], tuple(item["reason_codes"]), item["requires_acknowledgment"])
             for item in manifest["review_items"]],
        )

    def test_trusted_analysis_generates_pii_safe_geometry_manifest(self):
        pii = "010-1234-5678"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), pii, fontsize=12)
            document.save(source)
            document.close()

            detector = type("Detector", (), {
                "detect": lambda _self, text: [DetectionSpan(
                    id="fixture-phone", label="phone", start=0, end=len(text),
                    length=len(text), source="fixture_detector", confidence=1.0,
                    action="mask", evidence=("pattern",),
                )],
            })()
            source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            with patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector):
                manifest = trusted_analysis_manifest(
                    str(source), _analysis_options(), session_hash_key=SESSION_HASH_KEY,
                )

        encoded = json.dumps(manifest, ensure_ascii=False)
        self.assertEqual(1, manifest["schema_version"])
        self.assertEqual("mixed", manifest["profile"])
        self.assertEqual("pdf_points_top_left", manifest["coordinate_space"])
        self.assertEqual(source_sha256, manifest["original_document_hash"])
        self.assertEqual(1, manifest["analysis_revision"])
        self.assertEqual([(0, 0)], [(item["page_start"], item["page_end"]) for item in manifest["segments"]])
        self.assertEqual(1, len(manifest["occurrences"]))
        occurrence = manifest["occurrences"][0]
        self.assertEqual("phone", occurrence["category"])
        self.assertEqual("common_detector", occurrence["source"])
        self.assertEqual(
            hmac.new(SESSION_HASH_KEY, pii.encode("utf-8"), hashlib.sha256).hexdigest(),
            occurrence["value_hash"],
        )
        self.assertEqual(hashlib.sha256(pii.encode("utf-8")).hexdigest(), occurrence["expected_text_hash"])
        self.assertEqual(1, len(occurrence["rects"]))
        rect = occurrence["rects"][0]
        self.assertTrue(all(
            isinstance(rect[key], (int, float)) and math.isfinite(rect[key])
            for key in ("x0", "y0", "x1", "y1")
        ))
        self.assertGreater(rect["x1"], rect["x0"])
        self.assertGreater(rect["y1"], rect["y0"])
        self.assertGreaterEqual(rect["x0"], 0.0)
        self.assertGreaterEqual(rect["y0"], 0.0)
        self.assertLessEqual(rect["x1"], 612.0)
        self.assertLessEqual(rect["y1"], 792.0)
        self.assertNotIn(pii, encoded)

    def test_trusted_analysis_rejects_extraction_from_a_different_source_at_every_page_depth(self):
        pii = "010-1234-5678"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "blank.pdf"
            other = Path(directory) / "pii.pdf"
            for path, text in ((source, ""), (other, pii)):
                document = fitz.open()
                page = document.new_page()
                if text:
                    page.insert_text((72, 72), text, fontsize=12)
                document.save(path)
                document.close()

            rendered = fitz.open(other)
            x0, y0, x1, y1, _text, *_rest = rendered[0].get_text("words")[0]
            rendered.close()
            mismatched = ExtractResult(
                text=pii,
                engine_used="independent-fixture",
                duration_sec=0.0,
                notes=[],
                pages=(ExtractedPage(
                    0, 612.0, 792.0, pii,
                    (ExtractedWord(
                        pii, (x0, y0, x1, y1), page_start=0, page_end=len(pii),
                        source="pymupdf_text_layer",
                    ),),
                    source="pymupdf_text_layer",
                ),),
            )
            detector = type("Detector", (), {
                "detect": lambda _self, _text: [DetectionSpan(
                    id="fixture-phone", label="phone", start=0, end=len(pii), length=len(pii),
                    source="fixture_detector", confidence=1.0, action="mask", evidence=("pattern",),
                )],
            })()
            with patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector):
                with self.assertRaisesRegex(ValueError, "^EXTRACTED_SOURCE_MISMATCH$"):
                    trusted_analysis_manifest(
                        str(source), _analysis_options(),
                        session_hash_key=SESSION_HASH_KEY, extracted=mismatched,
                    )
    def test_trusted_analysis_preserves_detector_unavailable_review_evidence(self):
        pii = "010-1234-5678"
        extracted = ExtractResult(
            text=pii,
            engine_used="fixture",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(
                0, 612.0, 792.0, pii,
                (ExtractedWord(pii, (10.0, 10.0, 100.0, 30.0), page_start=0, page_end=len(pii), source="pymupdf_text_layer"),),
                source="pymupdf_text_layer",
            ),),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.pdf"
            document = fitz.open()
            document.new_page()
            document.save(source)
            document.close()
            with patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=None):
                with self.assertRaisesRegex(ValueError, "COMMON_DETECTOR_UNAVAILABLE"):
                    trusted_analysis_manifest(
                        str(source), _analysis_options(),
                        session_hash_key=SESSION_HASH_KEY, extracted=extracted,
                    )

    def test_nondefault_revision_propagates_to_nested_artifacts_and_scopes_identities(self):
        pii = "010-1234-5678"
        layout_text = f"결재 주무관 홍길동 {pii}"
        phone_start = layout_text.index(pii)
        detector = type("Detector", (), {
            "detect": lambda _self, text: [DetectionSpan(
                id="fixture-phone", label="phone", start=phone_start, end=phone_start + len(pii),
                length=len(pii), source="fixture_detector", confidence=1.0,
                action="review", evidence=("pattern",),
            )],
        })()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), pii, fontsize=12)
            document.save(source)
            document.close()
            rendered = fitz.open(source)
            rendered_page = rendered[0]
            x0, y0, x1, y1, _text, *_rest = rendered_page.get_text("words")[0]
            width, height = rendered_page.rect.width, rendered_page.rect.height
            rendered.close()
            extracted = ExtractResult(
                text=layout_text, engine_used="fixture", duration_sec=0.0, notes=[],
                pages=(ExtractedPage(
                    0, width, height, layout_text,
                    (
                        ExtractedWord("결재", (10.0, 10.0, 30.0, 30.0), page_start=0, page_end=2, source="pymupdf_text_layer"),
                        ExtractedWord("주무관", (40.0, 10.0, 70.0, 30.0), page_start=3, page_end=6, source="pymupdf_text_layer"),
                        ExtractedWord("홍길동", (40.0, 35.0, 70.0, 55.0), page_start=7, page_end=10, source="pymupdf_text_layer"),
                        ExtractedWord(pii, (x0, y0, x1, y1), page_start=phone_start, page_end=phone_start + len(pii), source="pymupdf_text_layer"),
                    ),
                    source="pymupdf_text_layer",
                ),),
            )
            with patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector):
                revision_three = trusted_analysis_manifest(
                    str(source), _analysis_options(analysis_revision=3),
                    session_hash_key=SESSION_HASH_KEY, extracted=extracted,
                )
                revision_four = trusted_analysis_manifest(
                    str(source), _analysis_options(analysis_revision=4),
                    session_hash_key=SESSION_HASH_KEY, extracted=extracted,
                )
                for invalid in (0, True):
                    with self.subTest(invalid=invalid):
                        with self.assertRaisesRegex(ValueError, "ANALYSIS_REVISION_INVALID"):
                            trusted_analysis_manifest(
                                str(source), _analysis_options(analysis_revision=invalid),
                                session_hash_key=SESSION_HASH_KEY, extracted=extracted,
                            )

        collections = {
            "segments": ("segment_id",),
            "regions": ("region_id",),
            "occurrences": ("occurrence_id",),
            "review_items": ("review_id",),
        }
        for manifest, revision in ((revision_three, 3), (revision_four, 4)):
            self.assertEqual(revision, manifest["analysis_revision"])
            artifact_ids: set[str] = set()
            for collection, (identity_key,) in collections.items():
                items = manifest[collection]
                self.assertTrue(items, collection)
                self.assertTrue(all(item["analysis_revision"] == revision for item in items))
                identities = {item[identity_key] for item in items}
                self.assertTrue(all(isinstance(identity, str) and identity for identity in identities))
                self.assertEqual(len(items), len(identities))
                self.assertTrue(all(
                    isinstance(item.get("provenance", item.get("source")), str)
                    and item.get("provenance", item.get("source"))
                    for item in items
                ))
                artifact_ids.update(identities)
            segment_ids = {item["segment_id"] for item in manifest["segments"]}
            region_ids = {item["region_id"] for item in manifest["regions"]}
            self.assertTrue(all(item["segment_id"] in segment_ids for item in manifest["occurrences"]))
            self.assertTrue(all(
                item["region_id"] is None or item["region_id"] in region_ids
                for item in manifest["occurrences"]
            ))
            self.assertTrue(all(item["target_id"] in artifact_ids for item in manifest["review_items"]))
        for collection, (identity_key,) in collections.items():
            stale_ids = {item[identity_key] for item in revision_three[collection]}
            current_ids = {item[identity_key] for item in revision_four[collection]}
            self.assertFalse(stale_ids & current_ids, collection)
            self.assertTrue(all(
                item["target_id"] not in stale_ids
                for item in revision_four["review_items"]
            ))

    def test_independent_manifest_lock_is_machine_validated_and_tampering_is_rejected(self):
        manifest = {
            "schema_version": "IndependentGoldManifestV1",
            "geometry_policy_version": "GeometryPolicyV1",
            "coordinate_space": "pdf_points_top_left",
            "policy_version": "policy-v1",
            "profile": "mixed",
            "source_class": "synthetic",
            "form": "synthetic",
            "document": {"document_id": "doc-1", "input_sha256": "a" * 64, "output_sha256": "b" * 64},
            "provenance": {
                "author": {"id": "annotator"},
                "reviewer": {
                    "id": "reviewer",
                    "decision": "approved",
                    "adjudication": "independent_review",
                },
                "detector_output_imported": False,
            },
            "pages": [{"page_index": 0, "width": 612.0, "height": 792.0}],
            "segments": [{"id": "segment-1", "page_index": 0, "type": "body", "offsets": {"start": 0, "end": 1}}],
            "regions": [{"id": "region-1", "page_index": 0, "type": "body",
                         "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}]}],
            "occurrences": [{"id": "occurrence-1", "segment_id": "segment-1", "region_id": "region-1",
                             "page_index": 0, "category": "person_name", "offsets": {"start": 0, "end": 1},
                             "text_hash": "c" * 64, "ocr_confidence": None,
                             "rects": [{"x0": 1, "y0": 1, "x1": 2, "y1": 2}]}],
            "annotation_status": "reviewed_approved",
            "negatives": [],
            "protected_neighbors": [],
            "annotation_completion": {
                "pages": "completed",
                "segments": "completed",
                "regions": "completed",
                "occurrences": "completed",
                "negatives": "none_confirmed",
                "protected_neighbors": "none_confirmed",
            },
        }
        locked = lock_manifest(manifest)
        validate_manifest(locked, require_locked=True)
        locked["profile"] = "official"
        with self.assertRaisesRegex(ManifestValidationError, "manifest"):
            validate_manifest(locked, require_locked=True)
        locked["manifest_sha256"] = manifest_sha256(locked)
        with self.assertRaisesRegex(ManifestValidationError, "canonical document profile"):
            validate_manifest(locked, require_locked=True)


if __name__ == "__main__":
    unittest.main()
