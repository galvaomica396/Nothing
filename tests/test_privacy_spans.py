from __future__ import annotations

import json
import urllib.request
import unittest
from unittest.mock import patch

import document_masker_ocr_gui as masker
from privacy_spans import (
    OptionalAIPrivacyDetector,
    canonical_json_sha256,
    detection_spans_from_matches,
    merge_detection_spans,
    occurrence_id_for,
)


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
    def test_detection_span_round_trips_evidence_with_null_confidence(self) -> None:
        class EvidenceMatch:
            tag = "PHONE"
            text = "010-1234-5678"
            start = 3
            end = 16
            page_index = 0
            bbox = (10, 20, 30, 40)
            rects = ((10, 20, 30, 40), (31, 20, 45, 40))
            confidence = None
            evidence = ("ocr_word",)
            provenance = ("paddleocr",)
            action = "mask"

        span = detection_spans_from_matches("전화 010-1234-5678", [EvidenceMatch()])[0]

        payload = span.to_report_dict()
        self.assertEqual(0, payload["page"])
        self.assertEqual([10.0, 20.0, 30.0, 40.0], payload["bbox"])
        self.assertEqual([[10.0, 20.0, 30.0, 40.0], [31.0, 20.0, 45.0, 40.0]], payload["rects"])
        self.assertIsNone(payload["confidence"])
        self.assertEqual(["ocr_word"], payload["evidence"])
        self.assertEqual(["paddleocr"], payload["provenance"])
        self.assertEqual("mask", payload["action"])
        self.assertEqual(("ocr_word",), span.evidence)
        json.dumps(payload, ensure_ascii=False)

    def test_occurrence_id_is_stable_without_match_order(self) -> None:
        identity = {
            "document_sha256": "a" * 64,
            "run_id": "run_test",
            "tag": "PHONE",
            "source": "regex_phone",
            "start": 3,
            "end": 16,
            "page": 0,
        }
        first = occurrence_id_for(**identity, analysis_revision=1)
        second = occurrence_id_for(**identity, analysis_revision=1)
        revised = occurrence_id_for(**identity, analysis_revision=2)

        self.assertEqual(first, second)
        self.assertNotEqual(first, revised)
        self.assertEqual(canonical_json_sha256({"b": 2, "a": 1}), canonical_json_sha256({"a": 1, "b": 2}))
        for field, replacement in {
            "document_sha256": "b" * 64,
            "run_id": "run_other",
            "tag": "EMAIL",
            "source": "regex_email",
            "start": 4,
            "end": 17,
            "page": 1,
            "action": "review",
        }.items():
            with self.subTest(field=field):
                changed = {**identity, field: replacement}
                self.assertNotEqual(first, occurrence_id_for(**changed, analysis_revision=1))

    def test_profiles_normalize_only_supported_values(self) -> None:
        self.assertEqual("mixed", masker.normalize_opts(None)["profile"])
        with self.assertRaisesRegex(ValueError, "MASKING_PROFILE_UNSUPPORTED"):
            masker.normalize_opts({"profile": "official"})
        self.assertEqual("legal", masker.normalize_opts({"profile": "legal"})["profile"])
        self.assertEqual("official_dispatch", masker.normalize_opts({"profile": "official_dispatch"})["profile"])
        self.assertEqual("internal_review", masker.normalize_opts({"profile": "internal_review"})["profile"])
        for profile in ("unknown", "default", "", " mixed "):
            with self.subTest(profile=profile):
                with self.assertRaises(ValueError):
                    masker.normalize_opts({"profile": profile})

    def test_optional_ai_detector_is_disabled_without_model_or_download(self) -> None:
        detector = OptionalAIPrivacyDetector()
        secret = "NO_MODEL_PII_CANARY_010-1234-5678"
        with (
            patch("builtins.__import__", side_effect=AssertionError("model loader must not import")),
            patch.object(urllib.request, "urlopen", side_effect=AssertionError("network must not be called")) as network,
        ):
            result = detector.detect(secret)
        self.assertFalse(detector.enabled)
        self.assertEqual([], result)
        self.assertEqual(0, network.call_count)
        self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))

    def test_detection_candidates_explain_policy_without_raw_text(self) -> None:
        from privacy_detection import detection_candidates_from_matches

        text = "주소: 부산광역시 해운대구 우동 테스트로 10\n연락처 010-1234-5678"
        matches = [
            masker.RedactionMatch("ADDRESS", "부산광역시 해운대구 우동 테스트로 10"),
            masker.RedactionMatch("PHONE", "010-1234-5678"),
        ]

        candidates = detection_candidates_from_matches(text, matches)

        self.assertEqual(["auto_mask", "auto_mask"], [candidate.decision for candidate in candidates])
        self.assertEqual("regex_address_context", candidates[0].recognizer_name)
        self.assertEqual("regex_phone", candidates[1].recognizer_name)
        self.assertLess(candidates[0].score, candidates[1].score)
        self.assertEqual("recognizer_auto_mask", candidates[0].reason)
        self.assertEqual(matches, [candidate.to_redaction_match() for candidate in candidates])
        encoded = json.dumps([candidate.to_safe_report_dict() for candidate in candidates], ensure_ascii=False)
        self.assertNotIn("부산광역시 해운대구 우동 테스트로 10", encoded)
        self.assertNotIn("010-1234-5678", encoded)

    def test_safe_report_exposes_candidate_decision_metadata_without_recursive_canaries(self) -> None:
        canaries = {
            "raw_text": "RAW_TEXT_CANARY_010-1234-5678",
            "path": "/private/RAW_PATH_CANARY.pdf",
            "error": "RAW_ERROR_CANARY",
        }
        report = masker.build_safe_report(
            input_file=canaries["path"],
            opts={"profile": "mixed", "nested": {"raw": canaries["raw_text"]}},
            counts={"ADDRESS": 1, "PHONE": 1},
            redaction_matches=[
                masker.RedactionMatch("ADDRESS", "부산광역시 해운대구 우동 테스트로 10"),
                masker.RedactionMatch("PHONE", "010-1234-5678"),
            ],
            extract_meta={
                "engine_used": "plain-text",
                "chars": 50,
                "notes": [{"nested": {"raw": canaries["raw_text"], "path": canaries["path"], "error": canaries["error"]}}],
            },
            pdf_redaction_result={
                "verification": {"verified": True, "residual_hits": 0, "error": {"detail": canaries["error"]}},
                "targets_requested": 2,
                "targets_hit": 2,
                "missing_targets_count": 0,
            },
            output_paths={"report_path": canaries["path"]},
            source_text="주소: 부산광역시 해운대구 우동 테스트로 10\n연락처 010-1234-5678",
        )

        runtime_manifest = masker.runtime_manifest_for_report(report)
        decisions = [candidate["decision"] for candidate in runtime_manifest["detection_candidates"]]
        self.assertEqual(["auto_mask", "auto_mask"], decisions)
        self.assertEqual("regex_address_context", runtime_manifest["detection_candidates"][0]["recognizer_name"])
        self.assertIn("reason", runtime_manifest["detection_candidates"][0])
        encoded = json.dumps(report, ensure_ascii=False)
        for canary in canaries.values():
            with self.subTest(canary=canary):
                self.assertNotIn(canary, encoded)
        for raw_match in (
            "부산광역시 해운대구 우동 테스트로 10",
            "010-1234-5678",
        ):
            with self.subTest(raw_match=raw_match):
                self.assertNotIn(raw_match, encoded)


if __name__ == "__main__":
    unittest.main()
