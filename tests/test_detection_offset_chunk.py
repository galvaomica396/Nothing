# noqa: SIZE_OK - one cohesive privacy regression gate mirrors the ordered refactor slice
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

import document_masker_ocr_gui as masker
from masking_context import build_document_context, find_masking_context
from privacy_detection import detection_candidates_from_matches
from privacy_false_positive import PERSON_NAME_BACKUP_BLOCKLIST
from privacy_spans import DetectionSpan, detection_spans_from_matches, locate_value


PHONE_VALUE = "010-1234-5678"


class InjectedProcessorFailure(Exception):
    pass


class AuthoritativeOccurrenceTests(unittest.TestCase):
    def test_two_argument_match_construction_remains_compatible(self) -> None:
        match = masker.RedactionMatch("PHONE", PHONE_VALUE)

        self.assertEqual("PHONE", match.tag)
        self.assertEqual(PHONE_VALUE, match.text)
        self.assertEqual(-1, match.start)
        self.assertEqual(-1, match.end)
        self.assertEqual("", match.occurrence_id)
        self.assertEqual("", match.source)

    def test_repeated_values_keep_distinct_authoritative_offsets(self) -> None:
        text = f"연락처 {PHONE_VALUE} / 재연락 {PHONE_VALUE}"
        _masked, _counts, matches = masker.mask_text(text, profile="official")
        phone_matches = [match for match in matches if match.tag == "PHONE"]

        self.assertEqual(2, len(phone_matches))
        self.assertEqual([text.index(PHONE_VALUE), text.rindex(PHONE_VALUE)], [match.start for match in phone_matches])
        self.assertEqual([match.start + len(PHONE_VALUE) for match in phone_matches], [match.end for match in phone_matches])
        self.assertEqual(2, len({match.occurrence_id for match in phone_matches}))
        self.assertTrue(all(match.occurrence_id for match in phone_matches))
        self.assertEqual({"regex_phone"}, {match.source for match in phone_matches})

        with patch("privacy_spans.locate_value", side_effect=AssertionError("legacy fallback used")):
            spans = detection_spans_from_matches(text, phone_matches)
        with patch("privacy_spans.locate_value", side_effect=AssertionError("legacy fallback used")):
            candidates = detection_candidates_from_matches(text, phone_matches)

        self.assertEqual([match.start for match in phone_matches], [span.start for span in spans])
        self.assertEqual([match.occurrence_id for match in phone_matches], [span.id for span in spans])
        self.assertEqual([match.occurrence_id for match in phone_matches], [candidate.id for candidate in candidates])
        self.assertEqual([match.source for match in phone_matches], [candidate.recognizer_name for candidate in candidates])
        encoded = json.dumps(
            [span.to_report_dict() for span in spans]
            + [candidate.to_safe_report_dict() for candidate in candidates],
            ensure_ascii=False,
        )
        self.assertNotIn(PHONE_VALUE, encoded)
        self.assertNotIn("bbox\": {", encoded)

    def test_later_pass_offset_survives_an_earlier_length_changing_mask(self) -> None:
        text = f"주민번호 900101-1234567 연락처 {PHONE_VALUE}"
        _masked, _counts, matches = masker.mask_text(text, profile="official")
        phone_match = next(match for match in matches if match.tag == "PHONE")

        self.assertEqual(text.index(PHONE_VALUE), phone_match.start)
        self.assertEqual(phone_match.start + len(PHONE_VALUE), phone_match.end)
        with patch("privacy_spans.locate_value", side_effect=AssertionError("legacy fallback used")):
            span = detection_spans_from_matches(text, [phone_match])[0]
        self.assertEqual((phone_match.start, phone_match.end), (span.start, span.end))

    def test_mismatched_claimed_offset_uses_legacy_fallback(self) -> None:
        text = f"앞부분 {PHONE_VALUE}"
        mismatched = masker.RedactionMatch(
            "PHONE",
            PHONE_VALUE,
            start=0,
            end=len(PHONE_VALUE),
            occurrence_id="occ_invalid",
            source="regex_phone",
        )

        with patch("privacy_spans.locate_value", wraps=locate_value) as lookup:
            span = detection_spans_from_matches(text, [mismatched])[0]

        lookup.assert_called_once()
        self.assertEqual(text.index(PHONE_VALUE), span.start)
        self.assertEqual(span.start + len(PHONE_VALUE), span.end)

    def test_legal_multipass_never_emits_a_false_authoritative_offset(self) -> None:
        text = "원고: 홍길동 사건번호: 2023가단12345 사건명: 손해배상 서울중앙지방법원"
        _masked, _counts, matches = masker.mask_text(text, profile="legal")
        case_number = next(match for match in matches if match.tag == "CASE_NUMBER")

        self.assertEqual(text.index(case_number.text), case_number.start)
        for match in matches:
            if match.start < 0:
                self.assertEqual(-1, match.end)
                continue
            self.assertEqual(
                "".join(text[match.start:match.end].split()),
                "".join(match.text.split()),
            )

    def test_legal_case_title_preserves_original_value_and_offset_after_court_mask(self) -> None:
        text = "사건명: 손해배상 서울중앙지방법원"
        _masked, _counts, matches = masker.mask_text(text, profile="legal")
        case_title = next(match for match in matches if match.tag == "CASE_TITLE")

        self.assertEqual(text.index(case_title.text), case_title.start)
        self.assertEqual(case_title.start + len(case_title.text), case_title.end)
        with patch("privacy_spans.locate_value", side_effect=AssertionError("legacy fallback used")):
            span = detection_spans_from_matches(text, [case_title])[0]
        self.assertEqual((case_title.start, case_title.end), (span.start, span.end))
        self.assertIn(case_title, masker._redaction_search_terms(matches))

    def test_custom_keyword_after_an_earlier_mask_gets_source_offset(self) -> None:
        keyword = "비밀키워드"
        text = f"연락처 {PHONE_VALUE} {keyword}"

        _masked, _counts, matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "official", "custom_keywords": keyword},
        )
        custom = next(match for match in matches if match.tag == "KEYWORD")

        self.assertEqual(text.index(keyword), custom.start)
        self.assertEqual(custom.start + len(keyword), custom.end)

    def test_legacy_matches_still_use_flexible_lookup(self) -> None:
        text = "성명: 홍길동\n영업부"
        legacy = masker.RedactionMatch("NAME", "홍길동 영업부")

        with patch("privacy_spans.locate_value", wraps=locate_value) as span_lookup:
            span = detection_spans_from_matches(text, [legacy])[0]
        with patch("privacy_spans.locate_value", wraps=locate_value) as candidate_lookup:
            candidate = detection_candidates_from_matches(text, [legacy])[0]

        span_lookup.assert_called_once()
        candidate_lookup.assert_called_once()
        self.assertEqual("홍길동\n영업부", text[span.start:span.end])
        self.assertEqual((span.start, span.end), (candidate.start, candidate.end))

    def test_authoritative_context_selects_each_repeated_value(self) -> None:
        text = (
            f"===== PAGE 1 =====\n연락처 {PHONE_VALUE}\n"
            f"===== PAGE 2 =====\n연락처 {PHONE_VALUE}"
        )
        context = build_document_context(text, chunk_size=60, overlap=12)
        starts = [text.index(PHONE_VALUE), text.rindex(PHONE_VALUE)]
        matches = [
            masker.RedactionMatch(
                "PHONE",
                PHONE_VALUE,
                start=start,
                end=start + len(PHONE_VALUE),
                occurrence_id=f"occ_{index:06d}",
                source="regex_phone",
            )
            for index, start in enumerate(starts, 1)
        ]

        with patch("masking_context._search_score", side_effect=AssertionError("substring fallback used")):
            found = find_masking_context(matches, context)

        self.assertEqual([1, 2], [item["page"] for item in found if item is not None])
        self.assertEqual(["authoritative_offset", "authoritative_offset"], [item["confidence"] for item in found if item is not None])


class MaskingChunkBoundaryTests(unittest.TestCase):
    def test_chunk_offsets_and_overlap_reference_source_text(self) -> None:
        text = "abcdefghij"

        chunks = masker._chunk_text(text, 6, overlap=2)

        self.assertEqual([("abcdef", 0), ("efghij", 4)], chunks)
        self.assertTrue(all(text[base:base + len(chunk)] == chunk for chunk, base in chunks))

    def test_line_flush_keeps_document_base_offsets(self) -> None:
        text = "가" * 30 + "\n" + f"연락처 {PHONE_VALUE}\n"

        masked, counts, matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "official", "chunk_size": 32},
        )
        phone = next(match for match in matches if match.tag == "PHONE")

        self.assertFalse(PHONE_VALUE in masked)
        self.assertEqual(1, counts.get("PHONE"))
        self.assertEqual(text.index(PHONE_VALUE), phone.start)

    def test_injected_processor_retry_preserves_contract_and_redacts_log_details(self) -> None:
        logs: list[str] = []
        calls = 0

        def flaky(chunk: str, _opts: dict) -> masker.ChunkProcessResult:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise InjectedProcessorFailure("SENSITIVE_SENTINEL")
            return masker.ChunkProcessResult(masked_text=chunk, counts={}, matches=[])

        result = masker.process_masking_queue(
            "안전한 합성 텍스트",
            {"chunk_size": 5, "chunk_retries": 1, "_chunk_processor": flaky, "log_callback": logs.append},
        )

        self.assertEqual(4, len(result))
        self.assertGreaterEqual(calls, 2)
        self.assertEqual(1, result[3]["retried_chunks"])
        self.assertFalse(any("SENSITIVE_SENTINEL" in item for item in logs))

    def test_phone_split_at_4000_is_masked_once(self) -> None:
        text = "가" * 3994 + PHONE_VALUE + "이후텍스트"

        masked, counts, matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "official", "chunk_size": 4000},
        )
        phone_matches = [match for match in matches if match.tag == "PHONE"]

        self.assertFalse(PHONE_VALUE in masked)
        self.assertEqual(1, masked.count("[PHONE]"))
        self.assertEqual(1, counts.get("PHONE"))
        self.assertEqual(1, len(phone_matches))
        self.assertEqual((3994, 4007), (phone_matches[0].start, phone_matches[0].end))

    def test_name_and_legal_party_split_at_boundary_are_masked_once(self) -> None:
        cases = [
            ("가" * 3995 + " 성명: 홍길동 이후텍스트", "NAME"),
            ("가" * 3995 + " 원고: 홍길동", "LEGAL_PARTY"),
        ]
        for text, tag in cases:
            with self.subTest(tag=tag):
                masked, counts, matches, _meta = masker.process_masking_queue(
                    text,
                    {"profile": "official", "chunk_size": 4000},
                )
                tagged = [match for match in matches if match.tag == tag]

                self.assertFalse("홍길동" in masked)
                self.assertEqual(1, masked.count(f"[{tag}]"))
                self.assertEqual(1, counts.get(tag))
                self.assertEqual(1, len(tagged))
                self.assertEqual(text.index("홍길동"), tagged[0].start)

    def test_approval_name_filter_preserves_boundary_offsets_and_drops_only_false_positive(self) -> None:
        text = "가" * 3985 + "\n안전관리과장 안전\n장애인복지과장 김철수\n" + "나" * 100

        for chunk_size in (4000, 128, 32):
            with self.subTest(chunk_size=chunk_size):
                masked, counts, matches, _meta = masker.process_masking_queue(
                    text,
                    {"profile": "official", "chunk_size": chunk_size},
                )
                approval_matches = [match for match in matches if match.tag == "APPROVAL_LINE"]

                self.assertIn("안전관리과장 안전", masked)
                self.assertNotIn("김철수", masked)
                self.assertEqual(1, counts.get("APPROVAL_LINE"))
                self.assertEqual(1, len(approval_matches))
                self.assertEqual("김철수", approval_matches[0].text)
                self.assertEqual(text.index("김철수"), approval_matches[0].start)
                self.assertEqual(text.index("김철수") + len("김철수"), approval_matches[0].end)

    def test_partial_name_at_boundary_is_replaced_by_one_full_occurrence(self) -> None:
        text = "가" * 3993 + " 성명: 홍길동 이후텍스트"

        masked, counts, matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "official", "chunk_size": 4000},
        )
        names = [match for match in matches if match.tag == "NAME"]

        self.assertFalse("홍길동" in masked)
        self.assertEqual(1, counts.get("NAME"))
        self.assertEqual(1, masked.count("[NAME]"))
        self.assertEqual(1, len(names))
        self.assertEqual((text.index("홍길동"), text.index("홍길동") + 3), (names[0].start, names[0].end))

    def test_role_value_chunk_arbitration_rejects_every_partial_backup_term(self) -> None:
        contexts = (
            ("single", "건축과장 {value}", 0),
            ("dense", "건축과장 {value} / 팀장 장영실", 1),
        )
        for chunk_size in (4000, 128, 32):
            for value in sorted(PERSON_NAME_BACKUP_BLOCKLIST):
                for split_at in range(1, len(value)):
                    for context_name, template, expected_count in contexts:
                        suffix = template.format(value=value)
                        boundary_in_suffix = suffix.index(value) + split_at
                        text = "가" * (chunk_size - boundary_in_suffix) + suffix
                        with self.subTest(
                            chunk_size=chunk_size,
                            value=value,
                            split_at=split_at,
                            context=context_name,
                        ):
                            masked, counts, matches, _meta = masker.process_masking_queue(
                                text,
                                {"profile": "official", "chunk_size": chunk_size},
                            )
                            approval_matches = [match for match in matches if match.tag == "APPROVAL_LINE"]

                            self.assertIn(value, masked)
                            self.assertEqual(expected_count, counts.get("APPROVAL_LINE", 0))
                            self.assertEqual(expected_count, len(approval_matches))
                            self.assertTrue(all(match.text == "장영실" for match in approval_matches))

    def test_role_value_chunk_arbitration_reconstructs_split_names_once(self) -> None:
        contexts = (
            ("single", "건축과장 {value}", 1),
            ("dense", "과장 {value} / 팀장 박민수 / 주무관 이영희", 3),
        )
        for chunk_size in (4000, 128, 32):
            for value in ("김철수", "장영실"):
                for split_at in range(1, len(value)):
                    for context_name, template, expected_count in contexts:
                        suffix = template.format(value=value)
                        boundary_in_suffix = suffix.index(value) + split_at
                        text = "가" * (chunk_size - boundary_in_suffix) + suffix
                        with self.subTest(
                            chunk_size=chunk_size,
                            value=value,
                            split_at=split_at,
                            context=context_name,
                        ):
                            masked, counts, matches, _meta = masker.process_masking_queue(
                                text,
                                {"profile": "official", "chunk_size": chunk_size},
                            )
                            approval_matches = [match for match in matches if match.tag == "APPROVAL_LINE"]
                            value_matches = [match for match in approval_matches if match.text == value]

                            self.assertNotIn(value, masked)
                            self.assertEqual(expected_count, counts.get("APPROVAL_LINE"))
                            self.assertEqual(expected_count, len(approval_matches))
                            self.assertEqual(1, len(value_matches))
                            self.assertEqual(text.index(value), value_matches[0].start)
                            self.assertEqual(text.index(value) + len(value), value_matches[0].end)

    def test_overlap_dedup_keeps_distinct_occurrences(self) -> None:
        one_boundary = "가" * 3994 + PHONE_VALUE + "나" * 100
        two_occurrences = PHONE_VALUE + "가" * 3980 + PHONE_VALUE + "나" * 100

        masked_one, counts_one, matches_one, _meta = masker.process_masking_queue(
            one_boundary,
            {"profile": "official", "chunk_size": 4000},
        )
        masked_two, counts_two, matches_two, _meta = masker.process_masking_queue(
            two_occurrences,
            {"profile": "official", "chunk_size": 4000},
        )

        self.assertEqual(1, counts_one.get("PHONE"))
        self.assertEqual(1, masked_one.count("[PHONE]"))
        self.assertEqual(1, len([match for match in matches_one if match.tag == "PHONE"]))
        self.assertEqual(2, counts_two.get("PHONE"))
        self.assertEqual(2, masked_two.count("[PHONE]"))
        phone_matches = [match for match in matches_two if match.tag == "PHONE"]
        self.assertEqual(2, len(phone_matches))
        self.assertEqual(2, len({match.occurrence_id for match in phone_matches}))

    def test_custom_keyword_split_after_an_earlier_mask_is_repaired_once(self) -> None:
        keyword = "비밀키워드"
        leading = f"연락처 {PHONE_VALUE} "
        text = leading + "가" * (3997 - len(leading)) + keyword + "이후"

        masked, counts, matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "official", "chunk_size": 4000, "custom_keywords": keyword},
        )
        custom_matches = [match for match in matches if match.tag == "KEYWORD"]

        self.assertFalse(keyword in masked)
        self.assertEqual(1, counts.get("KEYWORD"))
        self.assertEqual(1, len(custom_matches))
        self.assertEqual(text.index(keyword), custom_matches[0].start)

    def test_long_custom_keyword_split_at_boundary_is_not_limited_by_default_window(self) -> None:
        keyword = "비" * 300
        text = "가" * 3850 + keyword + "이후"

        masked, counts, matches, meta = masker.process_masking_queue(
            text,
            {"profile": "official", "chunk_size": 4000, "custom_keywords": keyword},
        )

        self.assertFalse(keyword in masked)
        self.assertEqual(1, counts.get("KEYWORD"))
        self.assertEqual(1, len([match for match in matches if match.tag == "KEYWORD"]))
        self.assertGreaterEqual(meta["chunk_overlap"], len(keyword))

    def test_repeated_legal_party_split_at_boundary_is_repaired(self) -> None:
        text = "가" * 3939 + "\n원고: 홍길동\n"
        text += "나" * (3998 - len(text)) + "홍길동\n" + "다" * 100

        masked, counts, matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "legal", "chunk_size": 4000},
        )
        party_matches = [match for match in matches if match.tag == "LEGAL_PARTY"]

        self.assertFalse("홍길동" in masked)
        self.assertEqual(2, counts.get("LEGAL_PARTY"))
        self.assertEqual(2, masked.count("[LEGAL_PARTY]"))
        self.assertEqual(2, len(party_matches))
        self.assertEqual(2, len({(match.start, match.end) for match in party_matches}))

    def test_tag_counts_never_drop_below_f8bfa06_golden(self) -> None:
        cases = [
            (
                "official_core",
                "주민번호 900101-1234567 연락처: 010-1234-5678 이메일 test@example.com 사업자등록번호 123-45-67890 성명: 홍길동 주소: 부산광역시 해운대구 우동 테스트로 10",
                {"profile": "official"},
                {"ADDRESS": 1, "BUSINESS_REG_NO": 1, "EMAIL": 1, "NAME": 1, "PHONE": 1, "RRN": 1},
            ),
            (
                "legal_core",
                "원고: 홍길동 피고: 김철수 사건번호: 2023가단12345 사건명: 손해배상",
                {"profile": "legal"},
                {"CASE_NUMBER": 1, "CASE_TITLE": 1},
            ),
            (
                "custom",
                "비밀키워드와 커스텀구역가",
                {"profile": "official", "custom_keywords": "비밀키워드", "custom_regions": "커스텀구역가"},
                {"KEYWORD": 1, "REGION": 1},
            ),
            (
                "identifier_variants",
                "카드번호: 4000-0000-0000-0000 여권번호: M00000000 주민번호: 900101-1234567 외국인등록번호: 900101-5234567 사업자등록번호: 123-45-67890 연락처: 010-1234-5678 이메일: test@example.com 계좌번호: 110-222-333444",
                {"profile": "official"},
                {"ACCOUNT": 1, "BUSINESS_REG_NO": 1, "CARD": 1, "EMAIL": 1, "FOREIGN_REG": 1, "PASSPORT": 1, "PHONE": 1, "RRN": 1},
            ),
            ("approval_line", "대리 홍길동", {"profile": "official"}, {"APPROVAL_LINE": 1}),
            ("approval_flow", "결재구분 전결", {"profile": "official"}, {"APPROVAL_FLOW": 1}),
            ("document_meta", "시행 건축과-1234", {"profile": "official"}, {"DOC_META": 1}),
            ("address_detail", "주소 안내\n테스트로 10", {"profile": "official"}, {"ADDR_DETAIL": 1}),
            ("lot_number", "주소 안내\n123-45번지", {"profile": "official"}, {"LOT_NO": 1}),
            ("company", "주식회사 한빛", {"profile": "official"}, {"COMPANY": 1}),
            ("court", "서울중앙지방법원", {"profile": "official"}, {"COURT": 1}),
            ("law_firm", "법무법인 한빛", {"profile": "official"}, {"LAW_FIRM": 1}),
            ("attorney", "변호사: 김영희", {"profile": "official"}, {"ATTORNEY": 1}),
            ("legal_party", "원고: 홍길동", {"profile": "official"}, {"LEGAL_PARTY": 1}),
            ("place", "강남구", {"profile": "official"}, {"PLACE": 1}),
            ("weak_place", "가곡동", {"profile": "official"}, {"WEAK_PLACE": 1}),
        ]
        covered_tags = {tag for _case_id, _text, _options, golden in cases for tag in golden}
        self.assertEqual((set(masker.MASK_TOKEN_LABELS) - {"MANUAL"}) | {"WEAK_PLACE"}, covered_tags)
        for case_id, text, options, golden in cases:
            for chunk_size in (10_000, 128, 32):
                with self.subTest(case_id=case_id, chunk_size=chunk_size):
                    _masked, counts, _matches, _meta = masker.process_masking_queue(
                        text,
                        {**options, "chunk_size": chunk_size},
                    )
                    self.assertTrue(all(counts.get(tag, 0) >= expected for tag, expected in golden.items()))


class FakePrivacyDetector:
    name = "optional_ai_detector"

    def __init__(self, span: DetectionSpan) -> None:
        self._span = span

    def detect(self, text: str, context: dict[str, str] | None = None) -> list[DetectionSpan]:
        return [self._span]


class OptionalAIOccurrenceAdapterTests(unittest.TestCase):
    def test_fake_ai_occurrence_reaches_text_report_and_pdf_search_terms(self) -> None:
        value = "AI_ONLY_SECRET"
        text = f"검토 대상 {value}"
        start = text.index(value)
        span = DetectionSpan(
            id="external-sensitive-id",
            label="person_name",
            start=start,
            end=start + len(value),
            length=len(value),
            source="external-sensitive-source",
            confidence=0.91,
            action="mask",
        )

        masked, counts, matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "official", "name": False, "_privacy_detector": FakePrivacyDetector(span)},
        )

        self.assertFalse(value in masked)
        self.assertEqual(1, counts.get("NAME"))
        ai_match = next(match for match in matches if match.source == "optional_ai_detector")
        self.assertEqual((span.start, span.end), (ai_match.start, ai_match.end))
        self.assertIn(ai_match, masker._redaction_search_terms(matches))

        report = masker.build_safe_report(
            input_file="/tmp/synthetic.pdf",
            opts={"profile": "official"},
            counts=counts,
            redaction_matches=matches,
            extract_meta={"engine_used": "plain-text", "chars": len(text), "notes": []},
            pdf_redaction_result={
                "verification": {"verified": True, "residual_hits": 0},
                "targets_requested": 1,
                "targets_hit": 1,
                "missing_targets_count": 0,
            },
            output_paths={"report_path": "/tmp/out/synthetic.safe_report.json"},
            source_text=text,
        )
        encoded = json.dumps(report, ensure_ascii=False)

        self.assertNotIn(value, encoded)
        self.assertNotIn(span.id, encoded)
        self.assertNotIn(span.source, encoded)
        runtime_manifest = masker.runtime_manifest_for_report(report)
        self.assertIn(ai_match.occurrence_id, {item["id"] for item in runtime_manifest["detected_spans"]})
        self.assertIn(ai_match.occurrence_id, {item["id"] for item in runtime_manifest["detection_candidates"]})

    def test_ai_source_is_retained_when_regex_found_the_same_occurrence(self) -> None:
        text = f"연락처 {PHONE_VALUE}"
        start = text.index(PHONE_VALUE)
        span = DetectionSpan(
            id="ai_occ_duplicate",
            label="phone",
            start=start,
            end=start + len(PHONE_VALUE),
            length=len(PHONE_VALUE),
            source="optional_ai_detector",
            confidence=0.91,
            action="mask",
        )

        masked, counts, matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "official", "_privacy_detector": FakePrivacyDetector(span)},
        )
        report = masker.build_safe_report(
            input_file="/tmp/synthetic.pdf",
            opts={"profile": "official"},
            counts=counts,
            redaction_matches=matches,
            extract_meta={"engine_used": "plain-text", "chars": len(text), "notes": []},
            pdf_redaction_result={
                "verification": {"verified": True, "residual_hits": 0},
                "targets_requested": 1,
                "targets_hit": 1,
                "missing_targets_count": 0,
            },
            output_paths={"report_path": "/tmp/out/synthetic.safe_report.json"},
            source_text=text,
        )

        self.assertFalse(PHONE_VALUE in masked)
        self.assertEqual(1, counts.get("PHONE"))
        ai_match = next(match for match in matches if match.source == "optional_ai_detector")
        runtime_manifest = masker.runtime_manifest_for_report(report)
        self.assertIn(ai_match.occurrence_id, {item["id"] for item in runtime_manifest["detection_candidates"]})
        merged = next(item for item in runtime_manifest["detected_spans"] if item["label"] == "phone")
        self.assertEqual({"regex_phone", "optional_ai_detector"}, set(merged["sources"]))

    def test_duplicate_ai_provenance_does_not_shift_deidentification_values(self) -> None:
        first = "010-1111-1111"
        second = "010-2222-2222"
        text = f"연락처 {first} 재연락 {second}"
        start = text.index(first)
        span = DetectionSpan(
            id="external-id",
            label="phone",
            start=start,
            end=start + len(first),
            length=len(first),
            source="external-source",
            confidence=0.91,
            action="mask",
        )
        detector = FakePrivacyDetector(span)

        partial, partial_counts, _matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "official", "_privacy_detector": detector, "deidentification_policy": "partial"},
        )
        pseudonym, pseudonym_counts, _matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "official", "_privacy_detector": detector, "deidentification_policy": "pseudonym"},
        )

        self.assertEqual(2, partial_counts.get("PHONE"))
        self.assertEqual(2, pseudonym_counts.get("PHONE"))
        self.assertIn("010-****-1111", partial)
        self.assertIn("010-****-2222", partial)
        pseudonyms = re.findall(r"010-0000-\d{4}", pseudonym)
        self.assertEqual(2, len(pseudonyms))
        self.assertEqual(2, len(set(pseudonyms)))

    def test_ai_text_redaction_applies_only_to_reported_occurrence(self) -> None:
        value = "AI_ONLY_SECRET"
        text = f"{value} 구분 {value}"
        span = DetectionSpan(
            id="external-id",
            label="person_name",
            start=0,
            end=len(value),
            length=len(value),
            source="external-source",
            confidence=0.91,
            action="mask",
        )

        masked, counts, matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "official", "name": False, "_privacy_detector": FakePrivacyDetector(span)},
        )

        self.assertEqual(1, counts.get("NAME"))
        self.assertEqual(1, masked.count("[NAME]"))
        self.assertEqual(1, masked.count(value))
        ai_match = next(match for match in matches if match.source == "optional_ai_detector")
        self.assertEqual("ai_occ_000001", ai_match.occurrence_id)

    def test_ai_occurrence_reaches_native_pdf_redaction_and_safe_report(self) -> None:
        value = "AI_ONLY_SECRET"
        text = f"검토 {value}"
        start = text.index(value)
        span = DetectionSpan(
            id="external-sensitive-id",
            label="person_name",
            start=start,
            end=start + len(value),
            length=len(value),
            source="external-sensitive-source",
            confidence=0.91,
            action="mask",
        )
        _masked, counts, matches, _meta = masker.process_masking_queue(
            text,
            {"profile": "official", "name": False, "_privacy_detector": FakePrivacyDetector(span)},
        )
        ai_match = next(match for match in matches if match.source == "optional_ai_detector")

        with tempfile.TemporaryDirectory() as tmpdir:
            source_pdf = Path(tmpdir) / "source.pdf"
            output_pdf = Path(tmpdir) / "masked.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((72, 72), value, fontsize=12)
            document.save(source_pdf)
            document.close()

            pdf_result = masker.redact_pdf_native(str(source_pdf), str(output_pdf), matches, display_mode="black")
            output_document = fitz.open(output_pdf)
            try:
                output_text = "\n".join(page.get_text() for page in output_document)
            finally:
                output_document.close()

        self.assertEqual("applied", pdf_result["status"])
        self.assertEqual(1, pdf_result["targets_hit"])
        self.assertTrue(pdf_result["verification"]["verified"])
        self.assertFalse(value in output_text)
        report = masker.build_safe_report(
            input_file="/tmp/synthetic.pdf",
            opts={"profile": "official"},
            counts=counts,
            redaction_matches=matches,
            extract_meta={"engine_used": "plain-text", "chars": len(text), "notes": []},
            pdf_redaction_result=pdf_result,
            output_paths={"report_path": "/tmp/out/synthetic.safe_report.json"},
            source_text=text,
        )
        runtime_manifest = masker.runtime_manifest_for_report(report)
        self.assertIn(ai_match.occurrence_id, {item["id"] for item in runtime_manifest["detected_spans"]})
        self.assertNotIn(value, json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
