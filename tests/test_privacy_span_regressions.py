"""Regression tests for privacy-span merge and offset lookup behavior."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, replace

from privacy_detection import detection_candidates_from_matches
from privacy_spans import detection_spans_from_matches, locate_value, merge_detection_spans


@dataclass(frozen=True)
class _Match:
    tag: str
    text: str


class MergeActionPriorityTests(unittest.TestCase):
    def test_overlapping_mask_is_not_demoted_by_review(self) -> None:
        text = "연락처 010-1234-5678"
        mask_span = detection_spans_from_matches(text, [_Match("PHONE", "010-1234-5678")])[0]
        review_span = replace(mask_span, action="review", source="dictionary_weak_place")

        merged_mask_first = merge_detection_spans([mask_span, review_span])
        merged_review_first = merge_detection_spans([review_span, mask_span])

        self.assertEqual(1, len(merged_mask_first))
        self.assertEqual("mask", merged_mask_first[0]["action"])
        self.assertEqual("mask", merged_review_first[0]["action"])

    def test_review_only_overlap_stays_review(self) -> None:
        text = "주소 서울특별시 강남구"
        span = detection_spans_from_matches(text, [_Match("ADDRESS", "서울특별시 강남구")])[0]
        other = span.with_source("dictionary_weak_place")
        merged = merge_detection_spans([span, other])
        self.assertEqual("review", merged[0]["action"])


class OffsetLookupTests(unittest.TestCase):
    def test_newline_value_resolves_via_flexible_match(self) -> None:
        text = "성명: 홍길동\n영업부"
        collapsed = "홍길동 영업부"
        self.assertEqual(-1, text.find(collapsed))
        start, end = locate_value(text, collapsed, 0)
        self.assertGreaterEqual(start, 0)
        self.assertEqual(text[start:end], "홍길동\n영업부")

    def test_spaced_out_characters_resolve(self) -> None:
        text = "이 름 : 김 철 수 님"
        start, end = locate_value(text, "김철수", 0)
        self.assertEqual("김 철 수", text[start:end])

    def test_missing_value_keeps_zero_length_contract(self) -> None:
        text = "본문에 없는 값"
        match = _Match("NAME", "존재하지않는이름")
        span = detection_spans_from_matches(text, [match])[0]
        self.assertEqual(-1, span.start)
        self.assertEqual(-1, span.end)
        self.assertEqual(0, span.length)

    def test_candidate_newline_value_offset_is_consistent(self) -> None:
        text = "주소: 서울특별시\n강남구 테헤란로 1"
        match = _Match("ADDRESS", "서울특별시 강남구 테헤란로 1")
        candidate = detection_candidates_from_matches(text, [match])[0]
        self.assertGreaterEqual(candidate.start, 0)
        self.assertEqual(candidate.end - candidate.start, candidate.length)
        self.assertEqual(text[candidate.start:candidate.end], "서울특별시\n강남구 테헤란로 1")


if __name__ == "__main__":
    unittest.main()
