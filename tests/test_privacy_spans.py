from __future__ import annotations

import json
import unittest

import document_masker_ocr_gui as masker
from privacy_spans import OptionalAIPrivacyDetector, detection_spans_from_matches, merge_detection_spans


class PrivacySpanTests(unittest.TestCase):
    def test_detection_spans_store_offsets_not_raw_text(self) -> None:
        text = "홍길동의 전화번호는 010-1234-5678입니다."
        matches = [masker.RedactionMatch("PHONE", "010-1234-5678")]

        spans = detection_spans_from_matches(text, matches)

        self.assertEqual(1, len(spans))
        span = spans[0]
        self.assertEqual("phone", span.label)
        self.assertEqual(text.index("010-1234-5678"), span.start)
        self.assertEqual(span.start + len("010-1234-5678"), span.end)
        self.assertEqual("regex_phone", span.source)
        self.assertEqual("mask", span.action)
        encoded = json.dumps([span.to_report_dict() for span in spans], ensure_ascii=False)
        self.assertNotIn("010-1234-5678", encoded)
        self.assertFalse(span.raw_text_stored)

    def test_merge_detection_spans_combines_duplicate_sources(self) -> None:
        text = "연락처 010-1234-5678"
        regex_span = detection_spans_from_matches(text, [masker.RedactionMatch("PHONE", "010-1234-5678")])[0]
        ai_span = regex_span.with_source("optional_ai_detector")

        merged = merge_detection_spans([regex_span, ai_span])

        self.assertEqual(1, len(merged))
        self.assertEqual(["regex_phone", "optional_ai_detector"], merged[0]["sources"])
        self.assertNotIn("010-1234-5678", json.dumps(merged, ensure_ascii=False))

    def test_optional_ai_detector_is_disabled_without_model_or_download(self) -> None:
        detector = OptionalAIPrivacyDetector()

        self.assertFalse(detector.enabled)
        self.assertEqual([], detector.detect("연락처 010-1234-5678"))

    def test_detection_candidates_explain_policy_without_raw_text(self) -> None:
        from privacy_detection import detection_candidates_from_matches

        text = "주소: 부산광역시 해운대구 우동 테스트로 10\n연락처 010-1234-5678"
        matches = [
            masker.RedactionMatch("ADDRESS", "부산광역시 해운대구 우동 테스트로 10"),
            masker.RedactionMatch("PHONE", "010-1234-5678"),
        ]

        candidates = detection_candidates_from_matches(text, matches)

        self.assertEqual(["review", "auto_mask"], [candidate.decision for candidate in candidates])
        self.assertEqual("regex_address_context", candidates[0].recognizer_name)
        self.assertEqual("regex_phone", candidates[1].recognizer_name)
        self.assertLess(candidates[0].score, candidates[1].score)
        self.assertIn("manual review", candidates[0].reason)
        self.assertEqual(matches, [candidate.to_redaction_match() for candidate in candidates])
        encoded = json.dumps([candidate.to_safe_report_dict() for candidate in candidates], ensure_ascii=False)
        self.assertNotIn("부산광역시 해운대구 우동 테스트로 10", encoded)
        self.assertNotIn("010-1234-5678", encoded)

    def test_safe_report_exposes_candidate_decision_metadata(self) -> None:
        report = masker.build_safe_report(
            input_file="/tmp/synthetic.pdf",
            opts={"profile": "official"},
            counts={"ADDRESS": 1, "PHONE": 1},
            redaction_matches=[
                masker.RedactionMatch("ADDRESS", "부산광역시 해운대구 우동 테스트로 10"),
                masker.RedactionMatch("PHONE", "010-1234-5678"),
            ],
            extract_meta={"engine_used": "plain-text", "chars": 50, "notes": []},
            pdf_redaction_result={
                "verification": {"verified": True, "residual_hits": 0},
                "targets_requested": 2,
                "targets_hit": 2,
                "missing_targets_count": 0,
            },
            output_paths={"report_path": "/tmp/out/synthetic.safe_report.json"},
            source_text="주소: 부산광역시 해운대구 우동 테스트로 10\n연락처 010-1234-5678",
        )

        runtime_manifest = masker.runtime_manifest_for_report(report)
        decisions = [candidate["decision"] for candidate in runtime_manifest["detection_candidates"]]
        self.assertEqual(["review", "auto_mask"], decisions)
        self.assertEqual("regex_address_context", runtime_manifest["detection_candidates"][0]["recognizer_name"])
        self.assertIn("reason", runtime_manifest["detection_candidates"][0])
        self.assertNotIn("부산광역시 해운대구 우동 테스트로 10", json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
