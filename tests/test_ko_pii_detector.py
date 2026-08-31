from __future__ import annotations

import json
import os
import unittest
import unicodedata
from dataclasses import dataclass
from importlib import import_module
from unittest.mock import patch

import document_masker_ocr_gui as masker
from ko_pii_detector import ACTIVE_LABELS, SUPPORTED_LABELS, KoPiiPrivacyDetector, build_ko_pii_detector


@dataclass(frozen=True, slots=True)
class FakeKoPiiResult:
    label: str
    text: str
    start: int
    end: int
    confidence: float
    evidence: list[str]


class FakeDetectAll:
    def __init__(self, results: list[FakeKoPiiResult]) -> None:
        self.results = results
        self.includes: tuple[str, ...] = ()

    def __call__(
        self,
        text: str,
        *,
        include: tuple[str, ...],
    ) -> list[FakeKoPiiResult]:
        self.includes = include
        return self.results


def fake_result(
    text: str,
    value: str,
    label: str,
    *,
    confidence: float = 1.0,
    evidence: list[str] | None = None,
) -> FakeKoPiiResult:
    start = text.index(value)
    return FakeKoPiiResult(
        label=label,
        text=value,
        start=start,
        end=start + len(value),
        confidence=confidence,
        evidence=evidence if evidence is not None else [f"pattern:{label.lower()}"],
    )


def require_installed_detector(test_case: unittest.TestCase) -> KoPiiPrivacyDetector:
    detector = build_ko_pii_detector(lambda _message: None)
    if detector is not None:
        return detector
    if os.environ.get("NOTHING_ALLOW_KOPII_SKIP") == "1":
        test_case.skipTest("ko-pii dependency check explicitly disabled")
    test_case.fail(
        "Pinned ko-pii runtime dependency is missing; set NOTHING_ALLOW_KOPII_SKIP=1 only for an explicit local opt-out"
    )


class KoPiiPrivacyDetectorTests(unittest.TestCase):
    def test_supported_results_keep_exact_original_offsets_and_safe_metadata(self) -> None:
        text = "이메일: sample [at] example [dot] com\n신한은행 110-123-456789"
        email = "sample [at] example [dot] com"
        account = "110-123-456789"
        detect_all = FakeDetectAll(
            [
                fake_result(text, email, "EMAIL", evidence=["pattern:email", "obfuscated:deobfuscated"]),
                fake_result(
                    text,
                    account,
                    "ACCOUNT",
                    confidence=0.9,
                    evidence=["pattern:account", "keyword:bank(신한은행)"],
                ),
            ]
        )

        spans = KoPiiPrivacyDetector(detect_all).detect(text)

        self.assertEqual(["email", "bank_account"], [span.label for span in spans])
        self.assertEqual([email, account], [text[span.start : span.end] for span in spans])
        self.assertEqual(
            [("pattern", "obfuscated"), ("pattern", "bank_context")],
            [span.evidence for span in spans],
        )
        public = [span.to_report_dict() for span in spans]
        serialized = json.dumps(public, ensure_ascii=False)
        expected_keys = {
            "schema_version", "id", "label", "start", "end", "length", "source", "sources",
            "confidence", "action", "page", "bbox", "rects", "evidence", "provenance",
            "raw_text_stored", "occurrence_id", "analysis_revision", "coordinate_space", "metadata",
        }
        self.assertEqual([expected_keys, expected_keys], [set(item) for item in public])
        self.assertEqual([{}, {}], [item["metadata"] for item in public])
        self.assertIn('"evidence"', serialized)
        for raw_value in (email, account):
            with self.subTest(raw_value=raw_value):
                self.assertNotIn(raw_value, serialized)

    def test_unsupported_labels_and_mismatched_source_slices_are_not_emitted(self) -> None:
        text = "운전면허번호: 11-90-123456-78\n여권번호: PP12345678"
        driver = fake_result(text, "11-90-123456-78", "DRIVER_LICENSE")
        passport = fake_result(text, "PP12345678", "PASSPORT")
        mismatched = FakeKoPiiResult(
            label="EMAIL",
            text="different@example.invalid",
            start=0,
            end=5,
            confidence=1.0,
            evidence=["pattern:email"],
        )
        detect_all = FakeDetectAll([driver, passport, mismatched])

        spans = KoPiiPrivacyDetector(detect_all).detect(text)

        self.assertEqual(["passport_number"], [span.label for span in spans])
        self.assertNotIn("DRIVER_LICENSE", detect_all.includes)
        self.assertIn("PASSPORT", detect_all.includes)

    def test_low_confidence_checksum_failure_is_reviewed_but_not_dropped(self) -> None:
        text = "주민등록번호: 880101-1999999"
        result = fake_result(
            text,
            "880101-1999999",
            "RRN",
            confidence=0.7,
            evidence=["pattern:rrn", "date_valid:1988-01-01", "checksum:invalid_or_post_2020"],
        )

        spans = KoPiiPrivacyDetector(FakeDetectAll([result])).detect(text)

        self.assertEqual(1, len(spans))
        self.assertEqual("review", spans[0].action)
        self.assertEqual(("pattern", "date_valid", "checksum_invalid"), spans[0].evidence)

    def test_person_results_are_never_requested_or_emitted(self) -> None:
        text = "한국시설공단\n한국시 설공단은 점검"
        detect_all = FakeDetectAll(
            [
                fake_result(
                    text,
                    "설공단",
                    "PERSON",
                    confidence=0.8,
                    evidence=["pos:surname(설)", "pos:particle(은)", "origin:korean"],
                ),
            ]
        )

        spans = KoPiiPrivacyDetector(detect_all).detect(text)

        self.assertEqual([], spans)
        self.assertNotIn("PERSON", detect_all.includes)
        self.assertEqual(
            tuple(label for label in SUPPORTED_LABELS if label != "PERSON"),
            tuple(ACTIVE_LABELS),
        )

    def test_missing_dependency_returns_none_and_logs_no_pii(self) -> None:
        logs: list[str] = []

        with patch(
            "ko_pii_detector.import_module",
            side_effect=ModuleNotFoundError(
                "No module named ko_pii: /private/010-1234-5678.log RAW_DEPENDENCY_CANARY"
            ),
        ):
            detector = build_ko_pii_detector(logs.append)

        self.assertIsNone(detector)
        self.assertEqual(1, len(logs))
        self.assertIn("기존 규칙 엔진", logs[0])
        self.assertNotIn("010-1234-5678", logs[0])
        self.assertNotIn("/private/", logs[0])
        self.assertNotIn("RAW_DEPENDENCY_CANARY", logs[0])


class KoPiiShadowUnionTests(unittest.TestCase):
    def test_union_preserves_every_baseline_occurrence_and_adds_supported_spans(self) -> None:
        text = "\n".join(
            [
                "주민등록번호: 880101-1234568",
                "연락처: 010-1234-5678",
                "이메일: sample [at] example [dot] com",
                "신한은행 110-123-456789",
                "여권번호: PP12345678",
                "성명: 홍길동",
                "한국시설공단 점검",
                "한국시 설공단은 점검",
            ]
        )
        results = [
            fake_result(text, "880101-1234568", "RRN"),
            fake_result(text, "010-1234-5678", "PHONE"),
            fake_result(text, "sample [at] example [dot] com", "EMAIL"),
            fake_result(text, "110-123-456789", "ACCOUNT", confidence=0.9),
            fake_result(text, "PP12345678", "PASSPORT", confidence=0.9),
            fake_result(text, "홍길동", "PERSON", confidence=0.8, evidence=["pos:particle(은)"]),
            fake_result(text, "설공단", "PERSON", confidence=0.8, evidence=["pos:particle(은)"]),
        ]
        disabled_detector = KoPiiPrivacyDetector(FakeDetectAll([]))
        union_detector = KoPiiPrivacyDetector(FakeDetectAll(results))

        baseline_masked, baseline_counts, baseline_matches, _baseline_meta = masker.process_masking_queue(
            text,
            {"profile": "mixed", "_privacy_detector": disabled_detector},
        )
        union_masked, union_counts, union_matches, _union_meta = masker.process_masking_queue(
            text,
            {"profile": "mixed", "_privacy_detector": union_detector},
        )

        baseline_occurrences = sorted((match.tag, match.start, match.end) for match in baseline_matches)
        union_occurrences = sorted((match.tag, match.start, match.end) for match in union_matches)
        baseline_name_occurrences = [item for item in baseline_occurrences if item[0] == "NAME"]
        union_name_occurrences = [item for item in union_occurrences if item[0] == "NAME"]
        self.assertEqual([("NAME", text.index("홍길동"), text.index("홍길동") + len("홍길동"))], baseline_name_occurrences)
        self.assertEqual(baseline_occurrences, [item for item in union_occurrences if item in baseline_occurrences])
        self.assertEqual(
            {tag: baseline_counts.get(tag) for tag in baseline_counts},
            {tag: union_counts.get(tag) for tag in baseline_counts},
        )
        self.assertEqual(baseline_name_occurrences, union_name_occurrences)
        self.assertEqual(1, union_counts.get("EMAIL"))
        self.assertNotIn("sample [at] example [dot] com", union_masked)
        self.assertNotIn("110-123-456789", union_masked)
        self.assertNotIn("PP12345678", union_masked)
        self.assertIn("홍길동", union_masked)
        self.assertIn("한국시설공단", union_masked)
        self.assertIn("설공단", union_masked)
        self.assertIn("sample [at] example [dot] com", baseline_masked)
        for match in union_matches:
            if match.start >= 0:
                self.assertEqual(match.text, text[match.start : match.end].strip(" ,;:/"))

    def test_default_detector_import_failure_preserves_regex_result(self) -> None:
        text = "연락처: 010-1234-5678"
        logs: list[str] = []
        with patch("document_masker_ocr_gui.build_ko_pii_detector", return_value=None):
            masked, counts, matches, _meta = masker.process_masking_queue(
                text,
                {"profile": "mixed", "log_callback": logs.append},
            )

        self.assertNotIn("010-1234-5678", masked)
        self.assertEqual(1, counts["PHONE"])
        self.assertEqual(1, len([match for match in matches if match.tag == "PHONE"]))


    def test_ai_ordinals_are_calculated_against_unmasked_source_occurrences(self) -> None:
        source = "same same"
        existing = [masker.RedactionMatch("PHONE", "same", start=0, end=4, action="mask")]
        candidate = masker.RedactionMatch("NAME", "same", start=5, end=9, action="mask")

        masked, failures = masker._apply_ai_redactions(
            "[PHONE] same", source, [candidate], existing, korean_tokens=False
        )

        self.assertEqual("[PHONE] [NAME]", masked)
        self.assertEqual([], failures)

    def test_ai_redaction_rejects_an_invalid_source_offset_matrix(self) -> None:
        candidate = masker.RedactionMatch("NAME", "same", start=4, end=8, action="mask")

        _masked, failures = masker._apply_ai_redactions(
            "same", "same", [candidate], [], korean_tokens=False
        )

        self.assertEqual(
            [{"code": "REQUIRED_MASK_MAPPING_FAILED", "reason": "source_ordinal_missing"}],
            [{key: failure[key] for key in ("code", "reason")} for failure in failures],
        )
class InstalledKoPiiIntegrationTests(unittest.TestCase):
    def test_installed_detector_revision_covers_immediate_shadow_union_categories(self) -> None:
        text = "\n".join(
            [
                "이메일: sample [at] example [dot] com",
                "신한은행 110-123-456789",
                "여권번호: PP12345678",
                "홍길동은 참석했다",
                "국제전화: +82-10-1234-5678",
                "한국시설공단",
                "한국시 설공단은 점검",
            ]
        )
        detector = require_installed_detector(self)
        spans = detector.detect(text)

        expected = {
            "bank_account": ("110-123-456789", "review"),
            "email": ("sample [at] example [dot] com", "mask"),
            "passport_number": ("PP12345678", "mask"),
            "phone": ("+82-10-1234-5678", "mask"),
        }
        self.assertEqual(set(expected), {span.label for span in spans})
        for span in spans:
            value, action = expected[span.label]
            with self.subTest(label=span.label):
                self.assertEqual(value, text[span.start:span.end])
                self.assertEqual(text.index(value), span.start)
                self.assertEqual(span.start + len(value), span.end)
                self.assertEqual(action, span.action)

    def test_live_detector_preserves_offsets_across_unicode_and_chunk_boundaries(self) -> None:
        detector = require_installed_detector(self)
        composed_name = "홍길동"
        decomposed_name = unicodedata.normalize("NFD", "김민수")
        repeated_phone = "010-1234-5678"
        boundary_phone = "010-9876-5432"
        head = "\n".join(
            [
                f"🙂{composed_name}은 참석했다🙂",
                f"🙂{decomposed_name}는 참석했다🙂",
                f"연락처 {repeated_phone} / 재확인 {repeated_phone}",
            ]
        ) + "\n"
        prefix = "가" * (3995 - len(head))
        text = f"{head}{prefix}☎{boundary_phone}🙂"
        baseline = KoPiiPrivacyDetector(FakeDetectAll([]))

        raw_results = import_module("ko_pii").detect_all(text, include=tuple(SUPPORTED_LABELS))
        self.assertTrue(raw_results)
        for result in raw_results:
            self.assertEqual(result.text, text[result.start : result.end])

        _baseline_masked, baseline_counts, baseline_matches, _ = masker.process_masking_queue(
            text,
            {"profile": "mixed", "_privacy_detector": baseline},
        )
        _union_masked, union_counts, union_matches, _ = masker.process_masking_queue(
            text,
            {"profile": "mixed", "_privacy_detector": detector},
        )

        baseline_occurrences = sorted((match.tag, match.start, match.end) for match in baseline_matches)
        union_occurrences = sorted((match.tag, match.start, match.end) for match in union_matches)
        self.assertEqual(baseline_occurrences, [item for item in union_occurrences if item in baseline_occurrences])
        self.assertEqual(
            {tag: baseline_counts.get(tag) for tag in baseline_counts},
            {tag: union_counts.get(tag) for tag in baseline_counts},
        )
        authoritative = [match for match in union_matches if match.start >= 0]
        self.assertNotEqual([], authoritative)
        for match in authoritative:
            self.assertEqual(match.text, text[match.start : match.end])
        repeated_offsets = sorted((match.start, match.end) for match in authoritative if match.text == repeated_phone)
        self.assertEqual(
            [
                (text.index(repeated_phone), text.index(repeated_phone) + len(repeated_phone)),
                (text.rindex(repeated_phone), text.rindex(repeated_phone) + len(repeated_phone)),
            ],
            repeated_offsets,
        )
        boundary_matches = [match for match in authoritative if match.text == boundary_phone]
        self.assertEqual(1, len(boundary_matches))
        self.assertLess(boundary_matches[0].start, 4000)
        self.assertGreater(boundary_matches[0].end, 4000)


if __name__ == "__main__":
    unittest.main()
