#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""패키지 A(탐지 규칙 정확성) 회귀 테스트.

CODE_REVIEW_2026-07-04.md 의 C-1 / C-2 / C-3 / H-5 / M-4 / H-4 항목에 대해
리뷰가 실증한 입력을 그대로 회귀 테스트로 고정한다. 각 항목은 탐지되어야 할 것과
오탐되면 안 되는 것을 양방향으로 검증한다.
"""
from __future__ import annotations

import glob
import os
import tempfile
import unittest

import document_masker_ocr_gui as masker
import privacy_false_positive as fp


class C1OcrDelimiterVariants(unittest.TestCase):
    """C-1: OCR 변형 구분자(en-dash, 점, 괄호, 자간) 탐지 확장."""

    def _mask(self, text: str):
        return masker.mask_text(text, profile="official")

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
                self.assertGreaterEqual(counts.get(tag, 0), 1, f"{text!r} -> {counts}")

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
                self.assertGreaterEqual(counts.get(tag, 0), 1, f"{text!r} -> {counts}")

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
                self.assertGreaterEqual(counts.get(tag, 0), 1, f"{text!r} -> {counts}")

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

    def test_labeled_context_masks_names(self):
        cases = [
            ("성명: 은지원", "NAME"),
            ("신청인 태영호", "NAME"),
            ("원고: 은지원", "LEGAL_PARTY"),
            ("성명: 홍길동", "NAME"),
        ]
        for text, tag in cases:
            with self.subTest(text=text):
                _masked, counts, _matches = masker.mask_text(text, profile="official")
                self.assertGreaterEqual(counts.get(tag, 0), 1, f"{text!r} -> {counts}")

    def test_non_person_values_after_label_stay_clean(self):
        # COMMON_NON_PERSON_VALUES 필터 유지 — 라벨 뒤 비인명 어절 과탐 방지.
        clean = [
            "담당자 관리",
            "대표자 시스템",
            "신청인 제도",
            # 인라인 성씨 하드게이트 유지: 비인명 어절이 마스킹되지 않아야 함
            "원고 품질 기준",
            "건축과장 만족도",
        ]
        for text in clean:
            with self.subTest(text=text):
                masked, counts, matches = masker.mask_text(text, profile="official")
                self.assertEqual(text, masked, f"overmask: {counts}")

    def test_workflow_nouns_not_masked_as_names(self):
        # 성씨 보강으로 인한 결재/업무 흐름 어절 과탐을 COMMON 필터가 차단.
        clean = [
            "과장 승인",
            "결재 상신",
            "팀장 계약",
            "담당 반려",
            "과장 공람",
            "건축과장 시행",
        ]
        for text in clean:
            with self.subTest(text=text):
                masked, counts, _matches = masker.mask_text(text, profile="official")
                self.assertEqual(text, masked, f"overmask: {counts}")

    def test_real_role_names_still_masked(self):
        cases = [
            ("건축과장 김철수", "APPROVAL_LINE"),
            ("급수관리팀장 이한수", "APPROVAL_LINE"),
        ]
        for text, tag in cases:
            with self.subTest(text=text):
                _masked, counts, _matches = masker.mask_text(text, profile="official")
                self.assertGreaterEqual(counts.get(tag, 0), 1, f"{text!r} -> {counts}")


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
                _masked, counts, _matches = masker.mask_text(text, profile="official")
                self.assertGreaterEqual(counts.get("ADDRESS", 0), 1, f"{text!r} -> {counts}")

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


class M4OfficialSpacingVariants(unittest.TestCase):
    """M-4: official 프로파일 법원명/사건번호 자간 변형 탐지."""

    def test_official_court_spacing(self):
        for text in ["서 울 행 정 법 원", "대 법 원", "서 울 중 앙 지 방 법 원"]:
            with self.subTest(text=text):
                _masked, counts, _matches = masker.mask_text(text, profile="official")
                self.assertGreaterEqual(counts.get("COURT", 0), 1, f"{text!r} -> {counts}")

    def test_official_court_non_spaced_still_masked(self):
        _masked, counts, _matches = masker.mask_text("서울행정법원", profile="official")
        self.assertEqual(1, counts.get("COURT"))

    def test_official_case_number_spacing_with_label(self):
        cases = ["사건번호: 2023 가단 12345", "사건번호: 2023 가 단 12345", "사건번호: 2023가단12345"]
        for text in cases:
            with self.subTest(text=text):
                _masked, counts, _matches = masker.mask_text(text, profile="official")
                self.assertGreaterEqual(counts.get("CASE_NUMBER", 0), 1, f"{text!r} -> {counts}")

    def test_official_bare_case_number_without_label_not_masked(self):
        # 라벨/문맥 게이트 유지 — 오탐 억제.
        for text in ["프로젝트 2026가 123", "예산 2026나 456"]:
            with self.subTest(text=text):
                masked, counts, _matches = masker.mask_text(text, profile="official")
                self.assertEqual(text, masked, f"overmask: {counts}")


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

    def test_adjacent_court_context_accepts_a_bare_branch(self):
        text = "관할 법원: 동부지원"
        value = "동부지원"
        start = text.index(value)

        self.assertTrue(fp.is_likely_court_value(value, text, start, start + len(value)))
        masked, counts, _matches = masker.mask_text(text, profile="official")
        self.assertEqual("관할 법원: [COURT]", masked)
        self.assertEqual(1, counts.get("COURT"))

    def test_reported_false_positive_is_preserved_across_profiles_and_spacing(self):
        for profile in ["official", "legal", "common"]:
            for text in ["장애인자립지원", "장애인 자립 지원"]:
                with self.subTest(profile=profile, text=text):
                    masked, counts, matches = masker.mask_text(text, profile=profile)

                    self.assertEqual(text, masked)
                    self.assertEqual(0, counts.get("COURT", 0))
                    self.assertFalse(any(match.tag == "COURT" for match in matches))

    def test_real_court_structure_and_whitelisted_branch_remain_masked(self):
        for profile in ["official", "legal", "common"]:
            for text in ["서울중앙지방법원 안양지원", "안양지원"]:
                with self.subTest(profile=profile, text=text):
                    masked, counts, matches = masker.mask_text(text, profile=profile)

                    self.assertNotIn(text, masked)
                    self.assertGreaterEqual(counts.get("COURT", 0), 1)
                    self.assertTrue(any(match.tag == "COURT" for match in matches))

    def test_court_filter_does_not_change_other_tag_counts(self):
        text = "장애인자립지원 연락처 010-1234-5678"

        masked, counts, matches = masker.mask_text(text, profile="official")

        self.assertIn("장애인자립지원", masked)
        self.assertEqual(1, counts.get("PHONE"))
        self.assertEqual(0, counts.get("COURT", 0))
        self.assertEqual(["PHONE"], [match.tag for match in matches])

    def test_court_match_keeps_authoritative_source_offset_across_chunk_boundary(self):
        court = "안양지원"
        text = "가" * 3997 + "\n" + court + " 사건"

        _masked, counts, matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "official", "chunk_size": 4000},
        )
        court_matches = [match for match in matches if match.tag == "COURT"]

        self.assertEqual(1, counts.get("COURT"))
        self.assertEqual(1, len(court_matches))
        self.assertEqual(court, text[court_matches[0].start:court_matches[0].end])


class H4MarkerTempCleanup(unittest.TestCase):
    """H-4: marker 추출 임시 원문(PII) 잔존 방지."""

    def test_marker_cleanup_leaves_no_temp_dir(self):
        pattern = os.path.join(tempfile.gettempdir(), "marker_*")
        before = set(glob.glob(pattern))
        # marker_single 미설치 환경에서는 RuntimeError 가 나지만 정리는 반드시 수행되어야 함.
        try:
            masker._extract_pdf_with_marker_cleanup("/nonexistent_input.pdf")
        except Exception:
            pass
        after = set(glob.glob(pattern))
        self.assertEqual(set(), after - before, "임시 marker 디렉터리가 잔존함")

    def test_marker_no_sidecar_tmp_beside_input(self):
        # 입력 폴더 옆 `{입력}_tmp` 를 더 이상 만들지 않음.
        try:
            masker._extract_pdf_with_marker_cleanup("/nonexistent_input.pdf")
        except Exception:
            pass
        self.assertFalse(os.path.exists("/nonexistent_input_tmp"))


if __name__ == "__main__":
    unittest.main()
