import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from approval_layout import ApprovalLayoutResult, LayoutValue
from document_routing import PdfRect
from masking_extraction import ExtractedPage, ExtractedWord, ExtractResult
from document_masker_ocr_gui import _occurrence_id, _rects_within_region, trusted_analysis_manifest
from official_layout import (
    APPROVAL_ROW_LABEL_VALUE_DISTANCE_MAX,
    EVIDENCE_LABEL_VALUE_DISTANCE_MAX,
    INTERNAL_REVIEW_REGION_KINDS,
    OFFICIAL_DISPATCH_REGION_KINDS,
    LayoutRegion,
    REGION_CONFIRMED_SCORE,
    REGION_REVIEW_REQUIRED_SCORE,
    REGION_SCORE_WEIGHTS,
    RegionEvidence,
    confirmation_content_hash,
    detect_internal_review_regions,
    detect_official_dispatch_regions,
)
from privacy_spans import DetectionSpan


class OfficialLayoutTests(unittest.TestCase):
    def test_profile_candidate_containment_accepts_canonicalized_boundary_rounding(self):
        # Given: a region rounded to canonical micro-points and its source rectangle.
        source_rect = {"x0": 416.6400146484375, "y0": 262.6466064453125,
                       "x1": 448.08001708984375, "y1": 273.4439392089844}
        canonical_region = (PdfRect(62.880001, 123.551346, 515.954895, 273.443939),)

        # When: profile geometry checks whether the source value is in the region.
        contained = _rects_within_region([source_rect], canonical_region)

        # Then: canonical rounding alone cannot discard the source value.
        self.assertTrue(contained)

    def test_score_band_and_detector_weights_are_pinned_for_complete_evidence(self):
        # Given: a complete, fixed evidence bundle for each public-layout detector.
        evidence = {
            "rect_list": (PdfRect(10, 10, 40, 25),),
            "box_structure_match": True,
            "label_match": True,
            "structural_match": True,
            "label_value_distance": 24.0,
            "page_position_match": True,
            "ocr_confidence": 0.9,
        }

        # When: the constants and both detector paths are evaluated.
        internal = detect_internal_review_regions(
            [RegionEvidence("approval", 0, **evidence)], document_hash="a" * 64,
        )[0]
        dispatch = detect_official_dispatch_regions(
            [RegionEvidence("recipient_reference", 0, **evidence)], document_hash="a" * 64,
        )[0]

        # Then: the scoring contract and automatic-confirmation outcome stay fixed.
        self.assertEqual(
            {"box_structure": 3, "label_match": 3, "label_value_distance": 2, "page_position": 2},
            REGION_SCORE_WEIGHTS,
        )
        self.assertEqual((10, 5), (REGION_CONFIRMED_SCORE, REGION_REVIEW_REQUIRED_SCORE))
        self.assertEqual(("confirmed", "automatic"), (internal.state, internal.confirmation_source))
        self.assertEqual(("confirmed", "automatic"), (dispatch.state, dispatch.confirmation_source))

    def test_fixed_region_inventory_requires_complete_kind_specific_evidence(self):
        profiles = (
            (
                INTERNAL_REVIEW_REGION_KINDS,
                detect_internal_review_regions,
                detect_official_dispatch_regions,
            ),
            (
                OFFICIAL_DISPATCH_REGION_KINDS,
                detect_official_dispatch_regions,
                detect_internal_review_regions,
            ),
        )
        failure_cases = (
            ("box_structure_match", False, "box_structure_missing"),
            ("label_match", False, "label_evidence_missing"),
            ("structural_match", False, "layout_structure_missing"),
            ("page_position_match", False, "page_position_evidence_missing"),
            ("ocr_confidence", 0.84, "ocr_confidence_uncertain"),
            ("ocr_confidence", None, "ocr_confidence_missing"),
            ("rect_list", (), "page_local_geometry_missing"),
        )

        for kinds, detector, other_profile_detector in profiles:
            for index, kind in enumerate(kinds):
                with self.subTest(kind=kind):
                    rect = (PdfRect(index * 20, index * 20, index * 20 + 10, index * 20 + 10),)
                    complete_evidence = {
                        "box_structure_match": True,
                        "label_match": True,
                        "structural_match": True,
                        "label_value_distance": 24.0,
                        "page_position_match": True,
                        "ocr_confidence": 0.9,
                        "rect_list": rect,
                    }
                    region = detector(
                        [RegionEvidence(kind, index, **complete_evidence)],
                        document_hash="d" * 64,
                    )[0]
                    self.assertEqual(
                        (kind, "confirmed", "automatic", ("compound_layout_text_evidence",)),
                        (region.kind, region.state, region.confirmation_source, region.reason_codes),
                    )
                    self.assertEqual(
                        (),
                        other_profile_detector(
                            [RegionEvidence(kind, index, **complete_evidence)],
                            document_hash="d" * 64,
                        ),
                    )

                    for field, value, reason in failure_cases:
                        with self.subTest(kind=kind, field=field):
                            invalid_evidence = dict(complete_evidence, **{field: value})
                            failed_region = detector(
                                [RegionEvidence(kind, index, **invalid_evidence)],
                                document_hash="d" * 64,
                            )[0]
                            self.assertEqual(
                                ("review_required", None, (reason,)),
                                (
                                    failed_region.state,
                                    failed_region.confirmation_source,
                                    failed_region.reason_codes,
                                ),
                            )

        with self.assertRaisesRegex(ValueError, "unsupported region kind"):
            RegionEvidence("unsupported_kind", 0)

    def test_null_confidence_or_incomplete_evidence_fails_closed(self):
        rect = (PdfRect(1, 1, 2, 2),)
        evidence = RegionEvidence("approval", 0, rect, label_match=True, structural_match=True,
                                  box_structure_match=True, page_position_match=True, ocr_confidence=None)
        region = detect_internal_review_regions([evidence], document_hash="e" * 64)[0]
        self.assertEqual("review_required", region.state)
        self.assertIsNone(region.confirmation_source)
        self.assertIn("ocr_confidence_missing", region.reason_codes)

    def test_text_layer_confidence_is_satisfied_but_uncertain_ocr_stays_review_required(self):
        rect = (PdfRect(1, 1, 12, 12),)
        complete = {
            "rect_list": rect,
            "box_structure_match": True,
            "label_match": True,
            "structural_match": True,
            "label_value_distance": 24.0,
            "page_position_match": True,
        }

        text_layer = RegionEvidence(
            "approval", 0, ocr_confidence=1.0, confidence_source="text_layer", **complete,
        )
        uncertain_ocr = RegionEvidence(
            "approval", 0, ocr_confidence=0.84, confidence_source="ocr", **complete,
        )

        text_layer_region = detect_internal_review_regions([text_layer], document_hash="f" * 64)[0]
        uncertain_ocr_region = detect_internal_review_regions([uncertain_ocr], document_hash="f" * 64)[0]
        self.assertEqual("text_layer", text_layer.safe_dict()["confidence_source"])
        self.assertEqual(("confirmed", "automatic"), (text_layer_region.state, text_layer_region.confirmation_source))
        self.assertEqual("review_required", uncertain_ocr_region.state)
        self.assertIn("ocr_confidence_uncertain", uncertain_ocr_region.reason_codes)

    def test_weighted_region_evidence_maps_full_partial_and_weak_cases_to_three_states(self):
        rect = (PdfRect(1, 1, 12, 12),)
        complete = {
            "rect_list": rect,
            "box_structure_match": True,
            "label_match": True,
            "structural_match": True,
            "label_value_distance": 24.0,
            "page_position_match": True,
            "ocr_confidence": 0.9,
        }
        full = detect_internal_review_regions(
            [RegionEvidence("approval", 0, **complete)], document_hash="b" * 64,
        )[0]
        partial = detect_internal_review_regions(
            [RegionEvidence("approval", 0, **dict(complete, page_position_match=False))],
            document_hash="b" * 64,
        )[0]
        weak = detect_internal_review_regions(
            [RegionEvidence("approval", 0, **dict(
                complete, box_structure_match=False, label_match=False,
            ))],
            document_hash="b" * 64,
        )[0]

        self.assertEqual(
            {"box_structure": 3, "label_match": 3, "label_value_distance": 2, "page_position": 2},
            REGION_SCORE_WEIGHTS,
        )
        self.assertEqual((10, 5), (REGION_CONFIRMED_SCORE, REGION_REVIEW_REQUIRED_SCORE))
        self.assertEqual("confirmed", full.state)
        self.assertEqual("review_required", partial.state)
        self.assertEqual("unconfirmed", weak.state)
        self.assertIn("box_structure_missing", weak.reason_codes)
        self.assertIn("label_evidence_missing", weak.reason_codes)

        value_geometry_only = detect_internal_review_regions(
            [RegionEvidence("approval", 0, **dict(
                complete, box_structure_match=False, structural_match=True,
            ))],
            document_hash="b" * 64,
        )[0]
        self.assertEqual("review_required", value_geometry_only.state)
        self.assertIn("box_structure_missing", value_geometry_only.reason_codes)

    def test_review_band_boundaries_use_independent_signal_weights(self):
        rect = (PdfRect(1, 1, 12, 12),)
        base = {
            "rect_list": rect,
            "structural_match": True,
            "ocr_confidence": 0.9,
        }
        at_lower_band = detect_internal_review_regions(
            [RegionEvidence(
                "approval", 0, box_structure_match=True, label_value_distance=24.0, **base,
            )],
            document_hash="c" * 64,
        )[0]
        below_lower_band = detect_internal_review_regions(
            [RegionEvidence(
                "approval", 0, page_position_match=True,
                label_value_distance=EVIDENCE_LABEL_VALUE_DISTANCE_MAX, **base,
            )],
            document_hash="c" * 64,
        )[0]

        self.assertEqual(5, REGION_REVIEW_REQUIRED_SCORE)
        self.assertEqual("review_required", at_lower_band.state)
        self.assertEqual("unconfirmed", below_lower_band.state)

    def test_approval_row_distance_cap_accepts_measured_gap_without_widening_generic_cap(self):
        base = {
            "rect_list": (PdfRect(1, 1, 12, 12),),
            "box_structure_match": True,
            "label_match": True,
            "structural_match": True,
            "label_value_distance": 100.0,
            "page_position_match": True,
            "ocr_confidence": 0.9,
        }

        approval_row = detect_internal_review_regions(
            [RegionEvidence("approval", 0, approval_row_pattern=True, **base)],
            document_hash="c" * 64,
        )[0]
        generic = detect_official_dispatch_regions(
            [RegionEvidence("recipient_reference", 0, **base)],
            document_hash="c" * 64,
        )[0]

        self.assertEqual(110.0, APPROVAL_ROW_LABEL_VALUE_DISTANCE_MAX)
        self.assertEqual(("confirmed", "automatic"), (approval_row.state, approval_row.confirmation_source))
        self.assertIn("label_value_distance_out_of_range", generic.reason_codes)
    def test_missing_geometry_emits_review_and_invalid_geometry_is_rejected_at_construction(self):
        missing = RegionEvidence(
            "approval",
            0,
            label_match=True,
            structural_match=True,
            page_position_match=True,
            ocr_confidence=.9,
        )
        with self.assertRaisesRegex(ValueError, "rect_list must contain PdfRect values"):
            RegionEvidence(
                "approval",
                1,
                rect_list=("not-a-pdf-rect",),  # type: ignore[arg-type]
                label_match=True,
                structural_match=True,
                page_position_match=True,
                ocr_confidence=.9,
            )
        regions = detect_internal_review_regions([missing], document_hash="a" * 64)
        self.assertEqual(1, len(regions))
        self.assertEqual((), regions[0].rect_list)
        self.assertEqual("review_required", regions[0].state)
        self.assertEqual(("page_local_geometry_missing",), regions[0].reason_codes)
        for coordinate in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaisesRegex(ValueError, "finite"):
                PdfRect(coordinate, 1, 2, 2)

    def test_geometry_and_ocr_confidence_contract_reject_invalid_values_at_the_boundary(self):
        complete = {
            "box_structure_match": True,
            "label_match": True,
            "structural_match": True,
            "label_value_distance": 24.0,
            "page_position_match": True,
        }
        for coordinates in (
            (0, 0, 0, 1),
            (0, 0, 1, 0),
            (1, 0, 0, 1),
            (0, 1, 1, 0),
            (-1, 0, 1, 1),
        ):
            with self.subTest(coordinates=coordinates):
                with self.assertRaisesRegex(ValueError, "non-empty|out-of-page"):
                    PdfRect(*coordinates)
        for confidence in (
            float("nan"),
            float("inf"),
            float("-inf"),
            "0.85",
            True,
            -0.01,
            1.01,
        ):
            with self.subTest(confidence=confidence):
                with self.assertRaisesRegex(ValueError, "ocr_confidence must be in \\[0, 1\\]"):
                    RegionEvidence(
                        "approval",
                        0,
                        (PdfRect(1, 1, 2, 2),),
                        ocr_confidence=confidence,  # type: ignore[arg-type]
                        **complete,
                    )
        threshold_region = detect_internal_review_regions(
            [RegionEvidence(
                "approval",
                0,
                (PdfRect(1, 1, 2, 2),),
                ocr_confidence=.85,
                **complete,
            )],
            document_hash="a" * 64,
        )[0]
        self.assertEqual(
            ("confirmed", "automatic", ("compound_layout_text_evidence",)),
            (
                threshold_region.state,
                threshold_region.confirmation_source,
                threshold_region.reason_codes,
            ),
        )

    def test_bare_user_confirmation_is_not_versioned_content_bound_review_evidence(self):
        evidence = RegionEvidence("approval", 0, (PdfRect(1, 1, 2, 2),), user_confirmed=True)
        region = detect_internal_review_regions([evidence], document_hash="f" * 64)[0]
        self.assertEqual(("review_required", None), (region.state, region.confirmation_source))
        self.assertEqual(("review_evidence_required",), region.reason_codes)
    def test_content_bound_confirmation_requires_every_bound_field(self):
        rect = (PdfRect(1, 1, 2, 2),)
        confirmation = {
            "version": "layout-confirmation-v1",
            "document_hash": "f" * 64,
            "analysis_revision": 3,
            "content_hash": confirmation_content_hash("approval", 0, rect),
            "confirmed": True,
        }
        evidence = RegionEvidence("approval", 0, rect, user_confirmation=confirmation)
        region = detect_internal_review_regions(
            [evidence], document_hash="f" * 64, analysis_revision=3,
        )[0]
        self.assertEqual(("confirmed", "user"), (region.state, region.confirmation_source))
        for user_confirmed, message in ((False, "contradictory"), (True, "ambiguous")):
            with self.subTest(user_confirmed=user_confirmed):
                with self.assertRaisesRegex(ValueError, message):
                    RegionEvidence(
                        "approval",
                        0,
                        rect,
                        user_confirmed=user_confirmed,
                        user_confirmation=confirmation,
                    )

        for field, value in (
            ("version", "layout-confirmation-v0"),
            ("document_hash", "e" * 64),
            ("analysis_revision", 4),
            ("content_hash", "0" * 64),
            ("confirmed", False),
        ):
            with self.subTest(field=field):
                stale_confirmation = dict(confirmation, **{field: value})
                stale = detect_internal_review_regions(
                    [RegionEvidence("approval", 0, rect, user_confirmation=stale_confirmation)],
                    document_hash="f" * 64,
                    analysis_revision=3,
                )[0]
                self.assertEqual(
                    ("review_required", None, ("review_evidence_required",)),
                    (stale.state, stale.confirmation_source, stale.reason_codes),
                )

    def test_conflicting_duplicate_evidence_merges_to_the_lower_state(self):
        rect = (PdfRect(1, 1, 12, 12),)
        automatic = RegionEvidence(
            "approval", 0, rect,
            box_structure_match=True,
            label_match=True,
            structural_match=True,
            label_value_distance=24.0,
            page_position_match=True,
            ocr_confidence=0.9,
        )
        review_required = RegionEvidence(
            "approval", 0, rect,
            box_structure_match=True,
            label_match=True,
            structural_match=True,
            label_value_distance=24.0,
            page_position_match=False,
            ocr_confidence=0.9,
        )

        regions = detect_internal_review_regions(
            [automatic, review_required], document_hash="f" * 64,
        )

        self.assertEqual(1, len(regions))
        self.assertEqual("review_required", regions[0].state)
        self.assertIsNone(regions[0].confirmation_source)
        self.assertEqual(
            (
                "compound_layout_text_evidence",
                "conflicting_region_evidence",
                "page_position_evidence_missing",
            ),
            regions[0].reason_codes,
        )

    def test_overlapping_approval_candidates_keep_only_the_highest_evidence_region(self):
        # Given: two overlapping approval candidates from one page, one fully evidenced.
        full = RegionEvidence(
            "approval", 0, (PdfRect(10, 10, 80, 40),),
            box_structure_match=True, label_match=True, structural_match=True,
            label_value_distance=24.0, page_position_match=True, ocr_confidence=0.9,
        )
        partial = RegionEvidence(
            "approval", 0, (PdfRect(20, 20, 90, 50),),
            box_structure_match=False, label_match=True, structural_match=True,
            label_value_distance=24.0, page_position_match=True, ocr_confidence=0.9,
        )

        # When: region generation deduplicates overlapping approval geometry.
        regions = detect_internal_review_regions([partial, full], document_hash="d" * 64)

        # Then: one highest-scoring automatic region remains for one review surface.
        self.assertEqual(1, len(regions))
        self.assertEqual(("confirmed", "automatic"), (regions[0].state, regions[0].confirmation_source))
        self.assertEqual((PdfRect(10, 10, 90, 50),), regions[0].rect_list)

    def test_user_confirmation_is_retained_only_for_confirmed_duplicate_merges(self):
        rect = (PdfRect(1, 1, 12, 12),)
        document_hash = "f" * 64
        user_confirmed = RegionEvidence(
            "approval", 0, rect,
            user_confirmation={
                "version": "layout-confirmation-v1",
                "document_hash": document_hash,
                "analysis_revision": 1,
                "content_hash": confirmation_content_hash("approval", 0, rect),
                "confirmed": True,
            },
        )
        automatic = RegionEvidence(
            "approval", 0, rect,
            box_structure_match=True,
            label_match=True,
            structural_match=True,
            label_value_distance=24.0,
            page_position_match=True,
            ocr_confidence=0.9,
        )
        review_required = RegionEvidence(
            "approval", 0, rect,
            box_structure_match=True,
            label_match=True,
            structural_match=True,
            label_value_distance=24.0,
            page_position_match=False,
            ocr_confidence=0.9,
        )

        confirmed = detect_internal_review_regions(
            [user_confirmed, automatic], document_hash=document_hash,
        )[0]
        downgraded = detect_internal_review_regions(
            [user_confirmed, review_required], document_hash=document_hash,
        )[0]

        self.assertEqual(("confirmed", "user"), (confirmed.state, confirmed.confirmation_source))
        self.assertEqual(("review_required", None), (downgraded.state, downgraded.confirmation_source))


class TrustedProfileManifestTests(unittest.TestCase):
    def _manifest_for_page(self, page: ExtractedPage, *, profile: str = "official_dispatch") -> dict[str, object]:
        source_bytes = b"%PDF-1.7\nprofile-layout-page-fixture"
        extracted = ExtractResult(page.text, "test", 0.0, [], pages=(page,))
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.pdf"
            source_path.write_bytes(source_bytes)
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=Mock(detect=Mock(return_value=()))),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value="f" * 64),
            ):
                return trusted_analysis_manifest(
                    str(source_path),
                    {
                        "profile": profile,
                        "auto_threshold": 0.85,
                        "review_threshold": 0.5,
                        "profile_authority": {
                            "document_sha256": hashlib.sha256(source_bytes).hexdigest(),
                            "analysis_revision": 1,
                            "profile": profile,
                            "decision_code": "profile_confirmed",
                        },
                    },
                    session_hash_key=b"k" * 32,
                    extracted=extracted,
                )

    def test_body_role_fragment_does_not_create_manifest_approval_candidate(self):
        text = "감사팀장 목적 사진"
        page = ExtractedPage(
            0, 612.0, 792.0, text,
            (
                ExtractedWord("감사팀장", (220.0, 555.0, 269.0, 565.0), 0.9, 0, 4, 0, 4, "pymupdf_text_layer"),
                ExtractedWord("목적", (220.0, 590.0, 254.0, 600.0), 0.9, 5, 7, 5, 7, "pymupdf_text_layer"),
                ExtractedWord("사진", (220.0, 630.0, 254.0, 640.0), 0.9, 8, 10, 8, 10, "pymupdf_text_layer"),
            ),
            source="pymupdf_text_layer",
        )

        manifest = self._manifest_for_page(page, profile="internal_review")

        self.assertFalse(any(
            item["category"] in {"approval", "approval_staff"}
            for item in manifest["occurrences"]
        ))
        self.assertFalse(any(item["kind"] == "approval" for item in manifest["regions"]))

    def test_compact_dispatch_role_and_name_are_automatically_confirmed(self):
        text = "지역건강과장이병삼"
        page = ExtractedPage(
            0, 612.0, 792.0, text,
            (
                ExtractedWord("지역건강과장이병삼", (40.0, 700.0, 130.0, 710.0), 0.9, 0, len(text), 0, len(text), "pymupdf_text_layer"),
            ),
            drawings=((30.0, 695.0, 140.0, 715.0),),
            source="pymupdf_text_layer",
        )

        manifest = self._manifest_for_page(page)

        occurrence = next(item for item in manifest["occurrences"] if item["category"] == "approval_staff")
        region = next(item for item in manifest["regions"] if item["region_id"] == occurrence["region_id"])
        self.assertEqual(("mask", "confirmed"), (occurrence["proposed_action"], occurrence["state"]))
        self.assertEqual(("confirmed", "automatic"), (region["state"], region["confirmation_source"]))

    def test_title_only_header_does_not_emit_a_dispatch_boundary_signal(self):
        # Given: a line-start title in the top header zone without a dispatch label/value row.
        text = "제목 업무 협조\n본문"
        page = ExtractedPage(
            0, 612.0, 792.0, text,
            (
                ExtractedWord("제목", (10.0, 10.0, 30.0, 20.0), 0.9, 0, 2, 0, 2, "pymupdf_text_layer"),
                ExtractedWord("업무", (40.0, 10.0, 60.0, 20.0), 0.9, 3, 5, 3, 5, "pymupdf_text_layer"),
                ExtractedWord("협조", (70.0, 10.0, 90.0, 20.0), 0.9, 6, 8, 6, 8, "pymupdf_text_layer"),
                ExtractedWord("본문", (10.0, 300.0, 30.0, 310.0), 0.9, 9, 11, 9, 11, "pymupdf_text_layer"),
            ),
            source="pymupdf_text_layer",
        )
        import document_masker_ocr_gui as masker
        observed_pages = []
        route = masker.route_logical_documents

        # When: the page is routed through the normal manifest path.
        def capture(profile, pages, **kwargs):
            observed_pages.extend(pages)
            return route(profile, pages, **kwargs)

        with patch.object(masker, "route_logical_documents", side_effect=capture):
            self._manifest_for_page(page)

        # Then: title text alone cannot manufacture a dispatch boundary.
        self.assertEqual(frozenset(), observed_pages[0].start_signals)

    def test_body_paragraph_with_dispatch_words_does_not_emit_a_dispatch_verdict(self):
        # Given: a left-aligned body sentence with a standalone title token and dispatch words.
        text = "제목 심사 결과를 알림 통보합니다"
        page = ExtractedPage(
            0, 612.0, 792.0, text,
            (
                ExtractedWord("제목", (10.0, 360.0, 30.0, 374.0), 0.9, 0, 2, 0, 2, "pymupdf_text_layer"),
                ExtractedWord("심사", (40.0, 360.0, 60.0, 374.0), 0.9, 3, 5, 3, 5, "pymupdf_text_layer"),
                ExtractedWord("결과를", (70.0, 360.0, 110.0, 374.0), 0.9, 6, 9, 6, 9, "pymupdf_text_layer"),
                ExtractedWord("알림", (120.0, 360.0, 150.0, 374.0), 0.9, 10, 12, 10, 12, "pymupdf_text_layer"),
                ExtractedWord("통보합니다", (160.0, 360.0, 220.0, 374.0), 0.9, 13, 18, 13, 18, "pymupdf_text_layer"),
            ),
            source="pymupdf_text_layer",
        )
        import document_masker_ocr_gui as masker
        observed_pages = []
        route = masker.route_logical_documents

        # When: mixed-profile routing processes the actual page geometry.
        def capture(profile, pages, **kwargs):
            observed_pages.extend(pages)
            return route(profile, pages, **kwargs)

        with patch.object(masker, "route_logical_documents", side_effect=capture):
            self._manifest_for_page(page)

        # Then: body prose cannot create a dispatch verdict from title terms alone.
        self.assertNotIn("dispatch", observed_pages[0].start_signals)

    def test_mid_page_reference_does_not_mint_recipient_reference_evidence(self):
        # Given: an isolated, line-start routing instruction outside the reference header zone.
        text = "본문\n참조 서울특별시"
        reference_start = text.index("참조")
        value_start = text.index("서울특별시")
        page = ExtractedPage(
            0, 612.0, 792.0, text,
            (
                ExtractedWord("본문", (10.0, 80.0, 30.0, 90.0), 0.9, 0, 2, 0, 2, "pymupdf_text_layer"),
                ExtractedWord("참조", (10.0, 410.0, 30.0, 420.0), 0.9, reference_start, reference_start + 2, 3, 5, "pymupdf_text_layer"),
                ExtractedWord("서울특별시", (40.0, 410.0, 90.0, 420.0), 0.9, value_start, len(text), 6, len(text), "pymupdf_text_layer"),
            ),
            drawings=((5.0, 400.0, 100.0, 430.0),),
            source="pymupdf_text_layer",
        )
        import document_masker_ocr_gui as masker
        observed_evidence = []
        detector = masker.detect_official_dispatch_regions

        # When: region evidence is generated for the official-dispatch profile.
        def capture(evidence, **kwargs):
            observed_evidence.extend(evidence)
            return detector(evidence, **kwargs)

        with patch.object(masker, "detect_official_dispatch_regions", side_effect=capture):
            self._manifest_for_page(page)

        # Then: a body reference does not become a recipient-reference candidate.
        self.assertFalse(any(item.kind == "recipient_reference" for item in observed_evidence))

    def test_realigned_page_uses_ocr_confidence_and_requires_review(self):
        # Given: text-layer coordinates whose provenance records OCR-assisted realignment.
        text = "수신 서울특별시"
        value_start = text.index("서울특별시")
        page = ExtractedPage(
            0, 612.0, 792.0, text,
            (
                ExtractedWord("수신", (10.0, 10.0, 30.0, 20.0), 0.4, 0, 2, 0, 2, "pymupdf_text_layer"),
                ExtractedWord("서울특별시", (40.0, 10.0, 90.0, 20.0), 0.4, value_start, len(text), 3, len(text), "pymupdf_text_layer"),
            ),
            drawings=((5.0, 5.0, 100.0, 30.0),),
            source="pymupdf_text_layer",
            evidence_reason="ocr_assisted_realignment",
        )
        import document_masker_ocr_gui as masker
        observed_evidence = []
        detector = masker.detect_official_dispatch_regions

        # When: the provenance-bearing page flows through layout extraction.
        def capture(evidence, **kwargs):
            observed_evidence.extend(evidence)
            return detector(evidence, **kwargs)

        with patch.object(masker, "detect_official_dispatch_regions", side_effect=capture):
            manifest = self._manifest_for_page(page)

        # Then: it cannot spoof text-layer certainty or automatic confirmation.
        recipient = next(item for item in observed_evidence if item.kind == "recipient_reference")
        region = next(item for item in manifest["regions"] if item["kind"] == "recipient_reference")
        self.assertEqual(("ocr", 0.4), (recipient.confidence_source, recipient.ocr_confidence))
        self.assertEqual(("review_required", None), (region["state"], region["confirmation_source"]))

    def _manifest(
        self,
        *,
        profile: str,
        label: str,
        value: str,
        confidence: float | None = 0.9,
        source: str = "pymupdf_text_layer",
        expected_hash: str | None = "f" * 64,
    ) -> dict[str, object]:
        text = f"{label} {value}\n본문"
        value_start = len(label) + 1
        value_end = value_start + len(value)
        body_start = value_end + 1
        words = (
            ExtractedWord(label, (10.0, 10.0, 25.0, 20.0), confidence, 0, len(label), 0, len(label), source),
            ExtractedWord(value, (30.0, 10.0, 60.0, 20.0), confidence, value_start, value_end, value_start, value_end, source),
            ExtractedWord("본문", (10.0, 200.0, 35.0, 212.0), confidence, body_start, len(text), body_start, len(text), source),
        )
        extracted = ExtractResult(
            text=text,
            engine_used="test",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(0, 612.0, 792.0, text, words, source=source),),
        )
        source_bytes = b"%PDF-1.7\nprofile-layout-test"
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.pdf"
            source_path.write_bytes(source_bytes)
            profile_authority = {
                "document_sha256": hashlib.sha256(source_bytes).hexdigest(),
                "analysis_revision": 1,
                "profile": profile,
                "decision_code": "profile_confirmed",
            }
            with (
                patch("document_masker_ocr_gui.extract_document", return_value=extracted),
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=Mock(detect=Mock(return_value=()))),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value=expected_hash),
            ):
                return trusted_analysis_manifest(
                    str(source_path),
                    {"profile": profile, "profile_authority": profile_authority, "auto_threshold": 0.85, "review_threshold": 0.5},
                    session_hash_key=b"k" * 32,
                )

    def test_approval_line_without_independent_box_evidence_requires_review(self):
        manifest = self._manifest(profile="official_dispatch", label="결재", value="홍길동")
        occurrence = manifest["occurrences"][0]

        self.assertEqual(("approval_staff", "review", "review_required"), (
            occurrence["category"], occurrence["proposed_action"], occurrence["state"],
        ))
        self.assertEqual(30.0, occurrence["rects"][0]["x0"])
        linked_region = next(
            region for region in manifest["regions"]
            if region["region_id"] == occurrence["region_id"]
        )
        self.assertEqual("approval_staff", linked_region["kind"])
        self.assertEqual(("profile_value", "profile_layout"), (
            occurrence["tag"], occurrence["source"],
        ))

    def test_internal_review_authority_confirms_profile_segment_and_emits_profile_detection(self):
        # Given: the trusted analysis request has the same revision-bound authority Rust injects.
        manifest = self._manifest(profile="internal_review", label="결재", value="홍길동")

        # When: the profile-layout pipeline analyzes the fixture page.
        segments = [
            (item["kind"], item["state"], item["common_only"])
            for item in manifest["segments"]
        ]

        # Then: routing stays confirmed and the profile-only value is available to mask/review.
        self.assertEqual([("internal_review", "confirmed", False)], segments)
        self.assertTrue(any(item["tag"] == "profile_value" for item in manifest["occurrences"]))
        self.assertFalse(any(
            "profile_authority_missing" in item["reason_codes"]
            for item in manifest["review_items"]
        ))

    def test_nested_role_label_is_preserved_and_deduplicated(self):
        text = "결재 담당 홍길동"
        words = (
            ExtractedWord("결재", (10.0, 10.0, 25.0, 20.0), page_start=0, page_end=2, source="pymupdf_text_layer"),
            ExtractedWord("담당", (30.0, 10.0, 45.0, 20.0), page_start=3, page_end=5, source="pymupdf_text_layer"),
            ExtractedWord("홍길동", (50.0, 10.0, 75.0, 20.0), page_start=6, page_end=9, source="pymupdf_text_layer"),
        )
        extracted = ExtractResult(
            text=text,
            engine_used="test",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(0, 612.0, 792.0, text, words, source="pymupdf_text_layer"),),
        )
        source_bytes = b"%PDF-1.7\nnested-label-test"
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.pdf"
            source_path.write_bytes(source_bytes)
            with (
                patch("document_masker_ocr_gui.extract_document", return_value=extracted),
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=Mock(detect=Mock(return_value=()))),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value="f" * 64),
            ):
                manifest = trusted_analysis_manifest(str(source_path), {
                    "profile": "internal_review",
                    "auto_threshold": 0.85,
                    "review_threshold": 0.5,
                    "profile_authority": {
                        "document_sha256": hashlib.sha256(source_bytes).hexdigest(),
                        "analysis_revision": 1,
                        "profile": "internal_review",
                        "decision_code": "profile_confirmed",
                    },
                }, session_hash_key=b"k" * 32)

        self.assertEqual(1, len(manifest["occurrences"]))
        self.assertEqual(
            [{"x0": 50.0, "y0": 10.0, "x1": 75.0, "y1": 20.0}],
            manifest["occurrences"][0]["rects"],
        )
    def test_native_text_layer_boundary_keeps_label_outside_profile_value_geometry(self):
        text = "결재 홍길동"
        words = (
            ExtractedWord("결재", (10.0, 10.0, 25.0, 20.0), page_start=0, page_end=2, source="pymupdf_text_layer"),
            ExtractedWord("홍길동", (30.0, 10.0, 60.0, 20.0), page_start=3, page_end=6, source="pymupdf_text_layer"),
        )
        extracted = ExtractResult(
            text=text, engine_used="pymupdf", duration_sec=0.0, notes=[],
            pages=(ExtractedPage(0, 612.0, 792.0, text, words, source="pymupdf_text_layer"),),
        )
        source_bytes = b"%PDF-1.7\nnative text-layer boundary fixture"
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "native-text.pdf"
            source_path.write_bytes(source_bytes)
            with (
                patch("document_masker_ocr_gui.extract_document", return_value=extracted),
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=Mock(detect=Mock(return_value=()))),
                patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value="c" * 64),
            ):
                manifest = trusted_analysis_manifest(str(source_path), {
                    "profile": "internal_review",
                    "auto_threshold": 0.85,
                    "review_threshold": 0.5,
                    "profile_authority": {
                        "document_sha256": hashlib.sha256(source_bytes).hexdigest(),
                        "analysis_revision": 1,
                        "profile": "internal_review",
                        "decision_code": "profile_confirmed",
                    },
                }, session_hash_key=b"k" * 32)
        occurrence = manifest["occurrences"][0]
        self.assertEqual([{"x0": 30.0, "y0": 10.0, "x1": 60.0, "y1": 20.0}], occurrence["rects"])
        self.assertNotEqual((10.0, 10.0), (occurrence["rects"][0]["x0"], occurrence["rects"][0]["y0"]))

    def test_dispatch_recipient_value_uses_institution_review_kind_when_uncertain(self):
        manifest = self._manifest(
            profile="official_dispatch",
            label="수신",
            value="서울특별시",
            confidence=0.4,
        )
        occurrence = manifest["occurrences"][0]

        self.assertEqual(("recipient_reference", "review", "review_required"), (
            occurrence["category"], occurrence["proposed_action"], occurrence["state"],
        ))
        target_reviews = [
            item for item in manifest["review_items"]
            if item["target_id"] == occurrence["occurrence_id"]
        ]
        self.assertEqual(["institution"], [item["kind"] for item in target_reviews])
        self.assertTrue(any(item["kind"] == "region_geometry" for item in manifest["review_items"]))

    def test_unconfirmed_profile_region_keeps_common_privacy_candidate_and_blocks_save_review(self):
        text = "수신 010-1234-5678"
        phone_start = text.index("010-1234-5678")
        words = (
            ExtractedWord("수신", (10.0, 10.0, 25.0, 20.0), 0.9, 0, 2, 0, 2, "pymupdf_text_layer"),
            ExtractedWord("010-1234-5678", (30.0, 10.0, 100.0, 20.0), 0.9, phone_start, len(text), phone_start, len(text), "pymupdf_text_layer"),
        )
        extracted = ExtractResult(
            text=text,
            engine_used="test",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(0, 612.0, 792.0, text, words, source="pymupdf_text_layer"),),
        )
        source_bytes = b"%PDF-1.7\nunconfirmed-profile-region"
        detector = Mock(detect=Mock(return_value=(DetectionSpan(
            id="fixture-phone",
            label="phone",
            start=phone_start,
            end=len(text),
            length=len(text) - phone_start,
            source="fixture_detector",
            confidence=1.0,
            action="mask",
            evidence=("pattern",),
        ),)))
        region = LayoutRegion(
            "region_unconfirmed", 1, "recipient_reference", 0,
            (PdfRect(10.0, 10.0, 100.0, 20.0),), "unconfirmed", None,
            ("layout_structure_missing", "label_evidence_missing"),
        )
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.pdf"
            source_path.write_bytes(source_bytes)
            with (
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.detect_official_dispatch_regions", return_value=(region,)),
                patch(
                    "document_masker_ocr_gui.occurrence_rect_text_hash",
                    return_value=hashlib.sha256("010-1234-5678".encode("utf-8")).hexdigest(),
                ),
            ):
                manifest = trusted_analysis_manifest(
                    str(source_path),
                    {
                        "profile": "official_dispatch",
                        "auto_threshold": 0.85,
                        "review_threshold": 0.5,
                        "profile_authority": {
                            "document_sha256": hashlib.sha256(source_bytes).hexdigest(),
                            "analysis_revision": 1,
                            "profile": "official_dispatch",
                            "decision_code": "profile_confirmed",
                        },
                    },
                    session_hash_key=b"k" * 32,
                    extracted=extracted,
                )

        self.assertEqual(["common_detector"], [item["source"] for item in manifest["occurrences"]])
        self.assertEqual(("mask", "confirmed"), (
            manifest["occurrences"][0]["proposed_action"], manifest["occurrences"][0]["state"],
        ))
        self.assertEqual([("confirmed", "automatic")], [
            (item["state"], item["confirmation_source"])
            for item in manifest["regions"]
        ])
        geometry_reviews = [
            item for item in manifest["review_items"]
            if item["kind"] == "region_geometry" and item["target_id"] == "region_unconfirmed"
        ]
        self.assertEqual([], geometry_reviews)
        self.assertFalse(manifest["required_region_coverage"]["blocking"])
        self.assertFalse(any(
            item["provenance"] == "profile_layout" and "unconfirmed_region_candidate" in item["reason_codes"]
            for item in manifest["review_items"]
        ))

    def _manifest_for_profile_merge_regression(
        self,
        *,
        common_value: str,
        profile_value: str,
        common_rect: tuple[float, float, float, float],
        profile_rect: tuple[float, float, float, float],
        region_state: str,
    ) -> dict[str, object]:
        text = f"결재 {common_value}"
        value_start = text.index(common_value)
        words = (
            ExtractedWord("결재", (10.0, 10.0, 25.0, 20.0), 0.9, 0, 2, 0, 2, "pymupdf_text_layer"),
            ExtractedWord(
                common_value, common_rect, 0.9, value_start, len(text), value_start, len(text),
                "pymupdf_text_layer",
            ),
        )
        extracted = ExtractResult(
            text=text,
            engine_used="test",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(0, 612.0, 792.0, text, words, source="pymupdf_text_layer"),),
        )
        source_bytes = b"%PDF-1.7\nprofile-merge-regression"
        detector = Mock(detect=Mock(return_value=(DetectionSpan(
            id="fixture-name",
            label="person",
            start=value_start,
            end=len(text),
            length=len(common_value),
            source="fixture_detector",
            confidence=1.0,
            action="mask",
            evidence=("pattern",),
        ),)))
        layout = ApprovalLayoutResult(
            values=(LayoutValue(
                "approval_staff", 0, (profile_rect,), (), profile_value,
                box_structure_match=True, approval_row_pattern=True,
            ),),
            coverage={"approval": "present", "header_meta": "absent", "labeled_staff": "absent"},
        )
        region = LayoutRegion(
            "approval_region", 1, "approval", 0, (PdfRect(10.0, 10.0, 90.0, 25.0),),
            region_state, "automatic" if region_state == "confirmed" else None,
            () if region_state == "confirmed" else ("layout_structure_missing",),
        )
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.pdf"
            source_path.write_bytes(source_bytes)
            with (
                patch("document_masker_ocr_gui.analyze_approval_layout", return_value=layout),
                patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=detector),
                patch("document_masker_ocr_gui.detect_internal_review_regions", return_value=(region,)),
                patch(
                    "document_masker_ocr_gui.occurrence_rect_text_hash",
                    return_value=hashlib.sha256(common_value.encode("utf-8")).hexdigest(),
                ),
            ):
                return trusted_analysis_manifest(
                    str(source_path),
                    {
                        "profile": "internal_review",
                        "auto_threshold": 0.85,
                        "review_threshold": 0.5,
                        "profile_authority": {
                            "document_sha256": hashlib.sha256(source_bytes).hexdigest(),
                            "analysis_revision": 1,
                            "profile": "internal_review",
                            "decision_code": "profile_confirmed",
                        },
                    },
                    session_hash_key=b"k" * 32,
                    extracted=extracted,
                )

    def test_t27_merges_same_hash_with_one_point_coordinate_jitter(self):
        manifest = self._manifest_for_profile_merge_regression(
            common_value="홍길동",
            profile_value="홍길동",
            common_rect=(30.0, 10.0, 60.0, 20.0),
            profile_rect=(31.0, 10.0, 61.0, 20.0),
            region_state="confirmed",
        )

        self.assertEqual(1, len(manifest["occurrences"]))
        self.assertEqual("common_detector", manifest["occurrences"][0]["source"])

    def test_t27_suppresses_unconfirmed_profile_review_when_common_mask_exists(self):
        manifest = self._manifest_for_profile_merge_regression(
            common_value="홍길동",
            profile_value="홍길동",
            common_rect=(30.0, 10.0, 60.0, 20.0),
            profile_rect=(30.0, 10.0, 60.0, 20.0),
            region_state="unconfirmed",
        )

        self.assertEqual(1, len(manifest["occurrences"]))
        self.assertEqual(("common_detector", "mask"), (
            manifest["occurrences"][0]["source"], manifest["occurrences"][0]["proposed_action"],
        ))
        self.assertFalse(any(
            item["provenance"] == "profile_layout" and "unconfirmed_region_candidate" in item["reason_codes"]
            for item in manifest["review_items"]
        ))

    def test_t27_keeps_intersecting_candidates_when_value_hashes_differ(self):
        manifest = self._manifest_for_profile_merge_regression(
            common_value="김철수",
            profile_value="홍길동",
            common_rect=(30.0, 10.0, 60.0, 20.0),
            profile_rect=(31.0, 10.0, 61.0, 20.0),
            region_state="confirmed",
        )

        self.assertEqual(2, len(manifest["occurrences"]))
        self.assertEqual(["common_detector", "profile_layout"], [
            item["source"] for item in manifest["occurrences"]
        ])

    def test_three_region_states_control_profile_masking_and_review_behavior(self):
        text = "수신 서울특별시"
        value_start = text.index("서울특별시")
        words = (
            ExtractedWord("수신", (10.0, 10.0, 25.0, 20.0), 0.9, 0, 2, 0, 2, "pymupdf_text_layer"),
            ExtractedWord(
                "서울특별시", (30.0, 10.0, 80.0, 20.0), 0.9,
                value_start, len(text), value_start, len(text), "pymupdf_text_layer",
            ),
        )
        extracted = ExtractResult(
            text=text,
            engine_used="test",
            duration_sec=0.0,
            notes=[],
            pages=(ExtractedPage(0, 612.0, 792.0, text, words, source="pymupdf_text_layer"),),
        )
        source_bytes = b"%PDF-1.7\nthree-region-state-fixture"
        cases = (
            ("confirmed", "automatic", "mask", "confirmed", None),
            ("review_required", None, "review", "review_required", "profile_region_review_required"),
            ("unconfirmed", None, "review", "review_required", "unconfirmed_region_candidate"),
        )
        with tempfile.TemporaryDirectory() as directory:
            source_path = Path(directory) / "source.pdf"
            source_path.write_bytes(source_bytes)
            for state, confirmation_source, action, occurrence_state, occurrence_reason in cases:
                with self.subTest(state=state):
                    region = LayoutRegion(
                        f"region_{state}", 1, "recipient_reference", 0,
                        (PdfRect(10.0, 10.0, 80.0, 20.0),), state, confirmation_source,
                        () if state == "confirmed" else ("box_structure_missing",),
                    )
                    with (
                        patch(
                            "document_masker_ocr_gui.build_ko_pii_detector",
                            return_value=Mock(detect=Mock(return_value=())),
                        ),
                        patch(
                            "document_masker_ocr_gui.detect_official_dispatch_regions",
                            return_value=(region,),
                        ),
                        patch("document_masker_ocr_gui.occurrence_rect_text_hash", return_value="f" * 64),
                    ):
                        manifest = trusted_analysis_manifest(
                            str(source_path),
                            {
                                "profile": "official_dispatch",
                                "auto_threshold": 0.85,
                                "review_threshold": 0.5,
                                "profile_authority": {
                                    "document_sha256": hashlib.sha256(source_bytes).hexdigest(),
                                    "analysis_revision": 1,
                                    "profile": "official_dispatch",
                                    "decision_code": "profile_confirmed",
                                },
                            },
                            session_hash_key=b"k" * 32,
                            extracted=extracted,
                        )

                    occurrence = manifest["occurrences"][0]
                    self.assertEqual((action, occurrence_state), (
                        occurrence["proposed_action"], occurrence["state"],
                    ))
                    geometry_reviews = [
                        item for item in manifest["review_items"]
                        if item["kind"] == "region_geometry"
                    ]
                    occurrence_reviews = [
                        item for item in manifest["review_items"]
                        if item["target_id"] == occurrence["occurrence_id"]
                    ]
                    self.assertEqual(0 if state == "confirmed" else 1, len(geometry_reviews))
                    self.assertEqual(0 if occurrence_reason is None else 1, len(occurrence_reviews))
                    if occurrence_reason is not None:
                        self.assertEqual([occurrence_reason], occurrence_reviews[0]["reason_codes"])

    def test_missing_rectangle_text_hash_creates_hard_blocking_ocr_review(self):
        manifest = self._manifest(
            profile="official_dispatch",
            label="수신",
            value="서울특별시",
            expected_hash=None,
        )

        self.assertEqual([], manifest["occurrences"])
        ocr_reviews = [item for item in manifest["review_items"] if item["kind"] == "ocr"]
        self.assertEqual(1, len(ocr_reviews))
        self.assertIn("profile_rectangle_text_unavailable", ocr_reviews[0]["reason_codes"])

    def test_occurrence_id_matches_the_versioned_shared_vector(self):
        vector_path = Path(__file__).parent / "fixtures" / "occurrence-id-v2.json"
        vector = json.loads(vector_path.read_text(encoding="utf-8"))
        self.assertEqual("occurrence-id-v2", vector["version"])

        occurrence_id = _occurrence_id(
            vector["document_hash"],
            vector["analysis_revision"],
            vector["page_index"],
            vector["rects"],
            vector["tag"],
            vector["category"],
            vector["value_hash"],
            vector["source"],
            vector["policy"],
            vector["proposed_action"],
        )

        self.assertEqual(vector["expected_occurrence_id"], occurrence_id)
        self.assertRegex(occurrence_id, r"^occ_[0-9a-f]{24}$")

if __name__ == "__main__":
    unittest.main()
