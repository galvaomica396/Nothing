#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""패키지 A(탐지 규칙 정확성) 회귀 테스트.

CODE_REVIEW_2026-07-04.md 의 C-1 / C-2 / C-3 / H-5 / M-4 / H-4 항목에 대해
리뷰가 실증한 입력을 그대로 회귀 테스트로 고정한다. 각 항목은 탐지되어야 할 것과
오탐되면 안 되는 것을 양방향으로 검증한다.
"""
from __future__ import annotations

import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

import document_masker_ocr_gui as masker
import privacy_false_positive as fp
from masking_extraction import ExtractedPage, ExtractedWord
from privacy_spans import DetectionSpan




class C1OcrDelimiterVariants(unittest.TestCase):
    """C-1: OCR 변형 구분자(en-dash, 점, 괄호, 자간) 탐지 확장."""

    def _mask(self, text: str):
        return masker.mask_text(text, profile="mixed")

    def test_review_proven_variants_are_detected(self):
        cases = [
            ("900101–1234567", "RRN"),          # en-dash U+2013
            ("010.1234.5678", "PHONE"),          # 점 구분자
            ("(02)123-4567", "PHONE"),           # 지역번호 괄호
            ("9 0 0 1 0 1 - 1 2 3 4 5 6 7", "RRN"),  # 자간 삽입
        ]
        for text, tag in cases:
            with self.subTest(text=text):
                _masked, counts, _matches = self._mask(text)
                self.assertEqual(1, counts.get(tag), f"{text!r} -> {counts}")

    def test_additional_unicode_and_dot_variants(self):
        cases = [
            ("900101—1234567", "RRN"),   # em-dash U+2014
            ("900101.1234567", "RRN"),   # 점 구분자
            ("900101–5234567", "FOREIGN_REG"),  # 외국인 등록번호 en-dash
            ("02-123-4567", "PHONE"),
            ("전화: 010 1234 5678", "PHONE"),
        ]
        for text, tag in cases:
            with self.subTest(text=text):
                _masked, counts, _matches = self._mask(text)
                self.assertEqual(1, counts.get(tag), f"{text!r} -> {counts}")

    def test_existing_normal_cases_do_not_regress(self):
        cases = [
            ("900101-1234567", "RRN"),
            ("900101 1234567", "RRN"),
            ("0101234 5678", "PHONE"),
            ("010-1234-5678", "PHONE"),
            ("123-45-67890", "BUSINESS_REG_NO"),
            ("4000-0000-0000-0000", "CARD"),
        ]
        for text, tag in cases:
            with self.subTest(text=text):
                _masked, counts, _matches = self._mask(text)
                self.assertEqual(1, counts.get(tag), f"{text!r} -> {counts}")

    def test_non_pii_number_shapes_are_not_overmasked(self):
        # 날짜/금액/사건연도 등이 새로 오탐되지 않아야 한다.
        clean = [
            "2023-12-25",
            "금액 1,234,567원",
            "사건 2023-1234",
            "2024. 1. 2.",
            "예산 12345678",
            "도로 123-45",
            "제2023-45호",
        ]
        for text in clean:
            with self.subTest(text=text):
                masked, counts, matches = self._mask(text)
                self.assertEqual(text, masked, f"overmask: {counts}")
                self.assertEqual({}, counts)
                self.assertEqual([], matches)


class C2SurnameWhitelistDoesNotBlockLabeledNames(unittest.TestCase):
    """C-2: 강한 라벨 컨텍스트에서 성씨 화이트리스트가 실명을 차단하지 않음."""

    def test_review_names_pass_labeled_gate(self):
        for value in ["은지원", "태영호"]:
            with self.subTest(value=value):
                self.assertTrue(fp.is_labeled_person_name_value(value))

    def test_boosted_whitelist_covers_review_surnames(self):
        # 리뷰가 지목한 누락 성씨가 화이트리스트에 반영되어 인라인 게이트도 통과.
        for value in ["은지원", "태영호", "국중현", "편성수", "봉준호", "피천득"]:
            with self.subTest(value=value):
                self.assertTrue(fp.is_likely_person_name_value(value), value)

    def test_labeled_context_masks_names_with_legal_party_isolation(self):
        cases = [
            ("성명: 은지원", "NAME", "mixed"),
            ("신청인 태영호", "NAME", "mixed"),
            ("원고: 은지원", "LEGAL_PARTY", "legal"),
            ("성명: 홍길동", "NAME", "mixed"),
        ]
        for text, tag, profile in cases:
            with self.subTest(text=text, profile=profile):
                _masked, counts, _matches = masker.mask_text(text, profile=profile)
                self.assertEqual(1, counts.get(tag), f"{text!r} -> {counts}")
        for profile in ("internal_review", "official_dispatch", "mixed"):
            with self.subTest(profile=profile):
                text = "원고: 은지원"
                masked, counts, matches = masker.mask_text(text, profile=profile)
                self.assertEqual(text, masked)
                self.assertEqual({}, counts)
                self.assertEqual([], matches)

    def test_non_person_and_workflow_values_have_empty_public_candidate_surfaces(self):
        clean = [
            "담당자 관리", "대표자 시스템", "신청인 제도", "원고 품질 기준",
            "건축과장 만족도", "과장 승인", "결재 상신", "팀장 계약",
            "담당 반려", "과장 공람", "건축과장 시행",
        ]
        for text in clean:
            with self.subTest(text=text):
                masked, counts, matches = masker.mask_text(text, profile="mixed")
                self.assertEqual(text, masked)
                self.assertEqual({}, counts)
                self.assertEqual([], matches)

    def test_spoofed_approval_geometry_options_fail_closed(self):
        text = "건축과장 김철수"
        spoofed_options = {
            "profile": "mixed",
            "approval_region_state": "confirmed",
            "approval_region_geometry": [{"page_index": 0, "rects": [{"x0": 0, "y0": 0, "x1": 100, "y1": 20}]}],
            "candidate_page_index": 0,
            "candidate_segment_id": "spoofed-segment",
            "candidate_rects": [{"x0": 10, "y0": 5, "x1": 40, "y1": 15}],
        }
        masked, counts, matches, _meta = masker.process_masking_queue(text, spoofed_options)
        self.assertEqual(text, masked)
        self.assertEqual({}, counts)
        self.assertEqual([], matches)

    def test_trusted_page_geometry_and_occurrence_identity_are_stable_across_detector_order(self):
        values = ("Alice Example", "Bob Example")
        text = "approval block Alice Example\napproval block Bob Example\n"
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "trusted.pdf"
            document = fitz.open()
            page = document.new_page(width=360, height=180)
            page.insert_text((32, 52), "approval block Alice Example")
            page.insert_text((32, 92), "approval block Bob Example")
            document.save(source)
            document.close()

            document = fitz.open(source)
            try:
                page_text = document[0].get_text()
                rects = {value: document[0].search_for(value)[0] for value in values}
            finally:
                document.close()
            self.assertEqual(text, page_text)
            extracted = masker.ExtractResult(
                text=page_text,
                engine_used="pymupdf",
                duration_sec=0.0,
                pages=(
                    ExtractedPage(
                        page_index=0,
                        text=page_text,
                        source="pymupdf_text_layer",
                        evidence_status="available",
                        coordinate_space="pdf_points_top_left",
                        words=tuple(
                            ExtractedWord(
                                text=value,
                                bbox=(rects[value].x0, rects[value].y0, rects[value].x1, rects[value].y1),
                                page_start=page_text.index(value),
                                page_end=page_text.index(value) + len(value),
                                source="pymupdf_text_layer",
                            )
                            for value in values
                        ),
                    ),
                ),
            )

            test_case = self

            class Detector:
                def __init__(self, ordered_values: tuple[str, ...]) -> None:
                    self.ordered_values = ordered_values

                def detect(self, received_text: str):
                    test_case.assertEqual(page_text, received_text)
                    return [
                        DetectionSpan(
                            f"trusted-{value}", "person_name", page_text.index(value),
                            page_text.index(value) + len(value), len(value), "test_detector", 1.0, "mask",
                        )
                        for value in self.ordered_values
                    ]

            options = {"profile": "mixed", "auto_threshold": 0.85, "review_threshold": 0.5}
            with patch.object(masker, "build_ko_pii_detector", return_value=Detector(values)):
                forward = masker.trusted_analysis_manifest(
                    str(source), options, extracted=extracted, session_hash_key=b"k" * 32,
                )
            with patch.object(masker, "build_ko_pii_detector", return_value=Detector(tuple(reversed(values)))):
                reverse = masker.trusted_analysis_manifest(
                    str(source), options, extracted=extracted, session_hash_key=b"k" * 32,
                )

        def identity_by_value(manifest):
            return {
                occurrence["value_hash"]: (
                    occurrence["occurrence_id"], occurrence["expected_text_hash"], occurrence["rects"],
                )
                for occurrence in manifest["occurrences"]
            }

        self.assertEqual(identity_by_value(forward), identity_by_value(reverse))
        self.assertEqual(2, len(forward["occurrences"]))
        for occurrence in forward["occurrences"]:
            self.assertEqual("NAME", occurrence["tag"])
            self.assertEqual("mask", occurrence["proposed_action"])
            self.assertEqual(0, occurrence["page"])
            self.assertRegex(occurrence["occurrence_id"], r"^occ_[0-9a-f]{24}$")
            self.assertEqual("common_detector", occurrence["source"])
            self.assertEqual("common_detector", occurrence["provenance"])
            value = next(
                value for value in values
                if hmac.new(b"k" * 32, value.encode("utf-8"), hashlib.sha256).hexdigest()
                == occurrence["value_hash"]
            )
            self.assertEqual(
                [{"x0": rects[value].x0, "y0": rects[value].y0, "x1": rects[value].x1, "y1": rects[value].y1}],
                occurrence["rects"],
            )
            self.assertEqual(hashlib.sha256(value.encode("utf-8")).hexdigest(), occurrence["expected_text_hash"])


    def test_context_filter_rejects_public_document_domain_words(self):
        cases = [
            ("담당 안전", "안전"),
            ("건축과장 장애인", "장애인"),
            ("안전관리과장 안전", "안전"),
            ("공사 안전", "안전"),
            ("○○과 점검", "점검"),
            ("안전관리과장 안전점검", "안전점검"),
            ("장애인자립지원과장 장애인자", "장애인자"),
        ]
        for text, value in cases:
            with self.subTest(text=text):
                start = text.index(value)
                self.assertFalse(fp.is_likely_person_name(value, text, start, start + len(value)))

    def test_context_filter_keeps_labeled_and_approval_names(self):
        cases = [
            ("성명: 홍길동", "홍길동"),
            ("민원인: 김철수", "김철수"),
            ("신청인 이영희", "이영희"),
            ("주무관 홍길동", "홍길동"),
            ("장애인자립지원과장 김철수", "김철수"),
            ("장애인복지과장 김철수", "김철수"),
            ("건축과장 이영희", "이영희"),
            ("안전관리과장 박민수", "박민수"),
            ("과장\n김철수", "김철수"),
            ("주무관 장미", "장미"),
            ("주무관 안정민", "안정민"),
            ("주무관 장영실", "장영실"),
            ("주무관 안현", "안현"),
        ]
        for text, value in cases:
            with self.subTest(text=text):
                start = text.index(value)
                self.assertTrue(fp.is_likely_person_name(value, text, start, start + len(value)))

        dense = "과장 가온 / 팀장 다온 / 주무관 라온"
        for value in ("가온", "다온", "라온"):
            with self.subTest(dense_value=value):
                start = dense.index(value)
                self.assertTrue(fp.is_likely_person_name(value, dense, start, start + len(value)))

    def test_role_adjacent_domain_words_remain_unmasked(self):
        samples = [
            "담당 안전",
            "건축과장 장애인",
            "안전관리과장 안전",
            "장애인자립지원과",
            "공사 안전",
            "○○과 점검",
            "복지 지원",
            "과장\n안전",
            "과장 안전 팀장 관리",
            "안전관리과장 안전점검",
            "장애인자립지원과장 장애인자",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile="mixed")
                self.assertEqual(sample, masked)
                self.assertEqual({}, counts)
                self.assertEqual([], matches)

    def test_labeled_names_remain_masked_without_approval_geometry(self):
        cases = [
            ("성명: 홍길동", "홍길동"),
            ("민원인: 김철수", "김철수"),
            ("신청인 이영희", "이영희"),
            ("성명: 지원", "지원"),
        ]
        for sample, value in cases:
            with self.subTest(sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile="mixed")
                self.assertEqual(1, masked.count("[NAME]"))
                self.assertEqual(1, counts.get("NAME"))
                occurrence = next(match for match in matches if match.tag == "NAME")
                self.assertEqual(value, occurrence.text)
                self.assertEqual(value, sample[occurrence.start:occurrence.end])


class C3LabeledJibunAddress(unittest.TestCase):
    """C-3: 라벨된 지번 주소(단일 동 + 지번) 탐지."""

    def setUp(self):
        self.sido = masker._region_terms("sido")[:40]

    def test_single_dong_plus_jibun_is_address(self):
        for value in ["역삼동 123-45", "서초동 1498-3", "반포동 20"]:
            with self.subTest(value=value):
                self.assertTrue(fp.is_likely_address_value(value, self.sido))

    def test_labeled_address_is_masked(self):
        cases = ["주소: 역삼동 123-45", "주소: 서초동 1498-3", "주소: 반포동 20"]
        for text in cases:
            with self.subTest(text=text):
                _masked, counts, _matches = masker.mask_text(text, profile="mixed")
                self.assertEqual(1, counts.get("ADDRESS"), f"{text!r} -> {counts}")

    def test_non_address_values_stay_clean(self):
        for value in ["품질관리팀 101호", "시스템 개선 요청", "추가 3건"]:
            with self.subTest(value=value):
                self.assertFalse(fp.is_likely_address_value(value, self.sido))


class H5LegalPartyGlobalReplacementBoundary(unittest.TestCase):
    """H-5: 2자 당사자명 전역 치환의 한글 경계 처리."""

    def test_two_char_name_does_not_destroy_glued_word(self):
        text = "원고 이가 피고 김철수\n이가방을 들었다"
        masked, _counts, _matches = masker.mask_text(text, profile="legal")
        self.assertIn("이가방을 들었다", masked)  # '이가방'은 파괴되지 않음
        self.assertNotIn("[LEGAL_PARTY]방", masked)

    def test_two_char_name_with_josa_is_masked(self):
        text = "원고 이가 피고 김철수\n이가는 출석했다\n이가 대표가 말했다"
        masked, _counts, _matches = masker.mask_text(text, profile="legal")
        self.assertIn("[LEGAL_PARTY]는 출석했다", masked)
        self.assertIn("[LEGAL_PARTY] 대표가 말했다", masked)

    def test_three_char_name_with_josa_still_masked(self):
        text = "원고 홍길동 피고 김철수\n홍길동이 말했다"
        masked, _counts, _matches = masker.mask_text(text, profile="legal")
        self.assertIn("[LEGAL_PARTY]이 말했다", masked)
        self.assertNotIn("홍길동", masked)


class M4LegalSpacingVariants(unittest.TestCase):
    """M-4: legal 프로파일 법원명/사건번호 자간 변형 탐지."""

    def test_legal_court_and_case_number_spacing(self):
        court_cases = ["서 울 행 정 법 원", "대 법 원", "서 울 중 앙 지 방 법 원", "서울행정법원"]
        case_cases = ["사건번호: 2023 가단 12345", "사건번호: 2023 가 단 12345", "사건번호: 2023가단12345"]
        for text, tag in [*( (text, "COURT") for text in court_cases), *( (text, "CASE_NUMBER") for text in case_cases)]:
            with self.subTest(text=text, profile="legal"):
                _masked, counts, _matches = masker.mask_text(text, profile="legal")
                self.assertEqual(1, counts.get(tag), f"{text!r} -> {counts}")
        for profile in ("internal_review", "official_dispatch", "mixed"):
            for text in (*court_cases, *case_cases):
                with self.subTest(text=text, profile=profile):
                    masked, counts, matches = masker.mask_text(text, profile=profile)
                    self.assertEqual(text, masked)
                    self.assertEqual({}, counts)
                    self.assertEqual([], matches)

    def test_public_bare_case_number_without_label_has_empty_public_surfaces(self):
        for text in ["프로젝트 2026가 123", "예산 2026나 456"]:
            with self.subTest(text=text):
                masked, counts, matches = masker.mask_text(text, profile="mixed")
                self.assertEqual(text, masked)
                self.assertEqual({}, counts)
                self.assertEqual([], matches)


class CourtFalsePositiveFiltering(unittest.TestCase):
    def test_welfare_support_phrase_is_not_a_court_value(self):
        for value in ["장애인자립지원", "장애인 자립 지원"]:
            with self.subTest(value=value):
                self.assertFalse(fp.is_likely_court_value(value, value, 0, len(value)))

    def test_strong_court_structure_and_whitelisted_branch_are_court_values(self):
        strong = "서울중앙지방법원 안양지원"
        self.assertTrue(fp.is_likely_court_value(strong, strong, 0, len(strong)))

        branch = "안양지원"
        self.assertTrue(fp.is_likely_court_value(branch, branch, 0, len(branch)))

    def test_adjacent_court_context_is_legal_only(self):
        text = "관할 법원: 동부지원"
        value = "동부지원"
        start = text.index(value)

        self.assertTrue(fp.is_likely_court_value(value, text, start, start + len(value)))
        masked, counts, _matches = masker.mask_text(text, profile="legal")
        self.assertEqual("관할 법원: [COURT]", masked)
        self.assertEqual(1, counts.get("COURT"))
        for profile in ("internal_review", "official_dispatch", "mixed"):
            with self.subTest(profile=profile):
                public_masked, public_counts, public_matches = masker.mask_text(text, profile=profile)
                self.assertEqual(text, public_masked)
                self.assertEqual(0, public_counts.get("COURT", 0))
                self.assertFalse(any(match.tag == "COURT" for match in public_matches))

    def test_reported_false_positive_is_preserved_across_profiles_and_spacing(self):
        for profile in ["internal_review", "official_dispatch", "mixed", "legal"]:
            for text in ["장애인자립지원", "장애인 자립 지원"]:
                with self.subTest(profile=profile, text=text):
                    masked, counts, matches = masker.mask_text(text, profile=profile)

                    self.assertEqual(text, masked)
                    self.assertEqual(0, counts.get("COURT", 0))
                    self.assertFalse(any(match.tag == "COURT" for match in matches))

    def test_real_court_structure_and_whitelisted_branch_are_legal_only(self):
        for text in ["서울중앙지방법원 안양지원", "안양지원"]:
            with self.subTest(profile="legal", text=text):
                masked, counts, matches = masker.mask_text(text, profile="legal")
                self.assertNotIn(text, masked)
                self.assertEqual(1, counts.get("COURT"))
                self.assertEqual(["COURT"], [match.tag for match in matches])
            for profile in ("internal_review", "official_dispatch", "mixed"):
                with self.subTest(profile=profile, text=text):
                    masked, counts, matches = masker.mask_text(text, profile=profile)
                    self.assertEqual(text, masked)
                    self.assertEqual(0, counts.get("COURT", 0))
                    self.assertFalse(any(match.tag == "COURT" for match in matches))

    def test_court_filter_does_not_change_other_tag_counts(self):
        text = "장애인자립지원 연락처 010-1234-5678"

        masked, counts, matches = masker.mask_text(text, profile="mixed")

        self.assertIn("장애인자립지원", masked)
        self.assertEqual(1, counts.get("PHONE"))
        self.assertEqual(0, counts.get("COURT", 0))
        self.assertEqual(["PHONE"], [match.tag for match in matches])

    def test_court_match_keeps_authoritative_source_offset_across_chunk_boundary(self):
        court = "안양지원"
        text = "가" * 3997 + "\n" + court + " 사건"

        _masked, counts, matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "legal", "chunk_size": 4000},
        )
        court_matches = [match for match in matches if match.tag == "COURT"]

        self.assertEqual(1, counts.get("COURT"))
        self.assertEqual(1, len(court_matches))
        self.assertEqual(court, text[court_matches[0].start:court_matches[0].end])


class H4MarkerTempCleanup(unittest.TestCase):
    """H-4: marker 추출 임시 원문(PII) 잔존 방지."""

    def test_marker_cleanup_removes_temp_input_after_controlled_post_marker_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            input_pdf = Path(directory) / "input.pdf"
            input_pdf.write_bytes(b"%PDF-1.4\nfixture")
            work_dirs: list[Path] = []

            def fail_after_marker(_pdf_path: str, work_dir: str):
                work = Path(work_dir)
                work_dirs.append(work)
                (work / "marker_out").mkdir()
                (work / "marker_out" / "raw.md").write_text("RAW_MARKER_CANARY", encoding="utf-8")
                raise RuntimeError("CONTROLLED_POST_MARKER_FAILURE")

            with patch("masking_extraction._extract_pdf_with_marker", side_effect=fail_after_marker):
                with self.assertRaisesRegex(RuntimeError, "^CONTROLLED_POST_MARKER_FAILURE$"):
                    masker._extract_pdf_with_marker_cleanup(str(input_pdf))

            self.assertEqual(1, len(work_dirs))
            self.assertFalse(work_dirs[0].exists())
            self.assertFalse((Path(f"{input_pdf}_tmp")).exists())



    def test_approval_role_without_trusted_geometry_has_empty_public_surfaces(self):
        text = "건축과장 김철수"
        masked, counts, matches = masker.mask_text(text, profile="mixed")
        self.assertEqual(text, masked)
        self.assertEqual({}, counts)
        self.assertEqual([], matches)

    def test_spaced_email_local_part_is_fully_masked(self):
        text = "연락처: hong gildong @ korea . kr"
        masked, counts, _matches = masker.mask_text(text, profile="mixed")
        self.assertEqual("연락처: [EMAIL]", masked)
        self.assertEqual(1, counts.get("EMAIL"))

    def test_document_number_does_not_consume_trailing_sentence(self):
        text = "시행번호: 총무과-1234호(2026.1.1.) 관련 붙임을 참고하시기 바랍니다."
        masked, counts, _matches = masker.mask_text(text, profile="mixed")
        self.assertIn("관련 붙임을 참고하시기 바랍니다.", masked)
        self.assertEqual(1, counts.get("DOC_META"))


class T37PostalCodeAddressRule(unittest.TestCase):
    def test_prefixed_and_address_labeled_postal_codes_mask_only_under_address_rule(self):
        # Given: footer-style postal values and an unrelated five-digit body number.
        cases = (
            ("우03718 기관 하단", "우[ADDRESS] 기관 하단"),
            ("우 04515 기관 하단", "우 [ADDRESS] 기관 하단"),
            ("우편번호: 03718", "우편번호: [ADDRESS]"),
            ("주소: 04515", "주소: [ADDRESS]"),
        )

        # When: the address rule is enabled, then disabled.
        for text, expected in cases:
            with self.subTest(text=text):
                masked, counts, _matches = masker.mask_text(text, profile="mixed", use_address=True)
                self.assertEqual(expected, masked)
                self.assertEqual(1, counts.get("ADDRESS"))
                self.assertEqual(text, masker.mask_text(text, profile="mixed", use_address=False)[0])

        # Then: a bare body number remains outside the address rule's context guard.
        body = "본문 관리번호 03718은 우편번호가 아니다."
        self.assertEqual(body, masker.mask_text(body, profile="mixed")[0])

    def test_public_homepage_url_remains_outside_document_metadata_masking(self):
        text = "홈페이지: https://www.dongjak.go.kr"
        self.assertEqual(text, masker.mask_text(text, profile="mixed")[0])

if __name__ == "__main__":
    unittest.main()
