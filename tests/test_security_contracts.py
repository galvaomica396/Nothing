import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import masking_rules

import fitz

import document_masker_ocr_gui as masker
from masking_extraction import ExtractResult, ExtractedPage, ExtractedWord
from privacy_spans import DetectionSpan

SESSION_HASH_KEY = bytes(range(32))

CONFIRMED_APPROVAL_CONTEXT = {
    "profile": "mixed",
    "approval_region_state": "confirmed",
    "approval_region_geometry": [{
        "page_index": 0,
        "segment_id": "segment-0",
        "rects": [{"x0": 0, "y0": 0, "x1": 1000, "y1": 1000}],
    }],
    "candidate_page_index": 0,
    "candidate_segment_id": "segment-0",
    "candidate_rects": [{"x0": 10, "y0": 10, "x1": 100, "y1": 30}],
}


def mask_with_confirmed_approval_context(text: str):
    """Text-queue context is review-only; trusted finalization owns public masks."""
    masked, counts, matches, _meta = masker.process_masking_queue(
        text,
        CONFIRMED_APPROVAL_CONTEXT,
    )
    return masked, counts, matches


class SecurityDefaultsTests(unittest.TestCase):
    def test_approval_authorization_is_not_available_to_text_queue(self):
        text = "대리 홍길동 대리 김철수"
        masked, counts, matches = mask_with_confirmed_approval_context(text)

        self.assertEqual(text, masked)
        self.assertNotIn("APPROVAL_LINE", counts)
        self.assertEqual([], matches)
    def test_default_output_artifacts_do_not_include_raw_txt(self):
        artifacts = masker.resolve_output_artifacts({})
        self.assertEqual({"pdf", "report"}, artifacts)

    def test_default_output_boundary_emits_only_safe_artifacts(self):
        pii = "DEFAULT_PII_CANARY_010-1234-5678"
        canaries = (pii, "DEFAULT_PATH_CANARY.pdf", "DEFAULT_ERROR_CANARY")
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            source = work / "DEFAULT_PATH_CANARY.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), pii)
            doc.save(source)
            doc.close()

            rendered = fitz.open(source)
            x0, y0, x1, y1, _text, *_rest = rendered[0].get_text("words")[0]
            rendered.close()
            extracted = ExtractResult(
                text=pii,
                engine_used="fixture",
                duration_sec=0.0,
                notes=["DEFAULT_ERROR_CANARY"],
                pages=(ExtractedPage(
                    0, 612.0, 792.0, pii,
                    (ExtractedWord(
                        pii, (x0, y0, x1, y1), page_start=0, page_end=len(pii),
                        source="pymupdf_text_layer",
                    ),),
                    source="pymupdf_text_layer",
                    coordinate_space="pdf_points_top_left",
                    evidence_status="available",
                ),),
            )
            detector = type("Detector", (), {
                "detect": lambda _self, text: [DetectionSpan(
                    id="fixture-phone", label="phone", start=0, end=len(text),
                    length=len(text), source="fixture_detector", confidence=1.0,
                    action="mask", evidence=("pattern",),
                )],
            })()
            with (
                patch.object(masker, "extract_document", return_value=extracted),
                patch.object(masker, "build_ko_pii_detector", return_value=detector),
            ):
                masked_pdf, masked_txt, report_path, report = masker._process_file(
                    str(source),
                    outdir=str(work / "out"),
                    opts={"auto_threshold": 0.85, "review_threshold": 0.5},
                    session_hash_key=SESSION_HASH_KEY,
                )

            self.assertIsNone(masked_pdf)
            self.assertIsNone(masked_txt)
            self.assertIsNone(report_path)
            self.assertEqual("public-analysis-only-v1", report["schema_version"])
            self.assertTrue(report["analysis_manifest"]["occurrences"])
            self.assertFalse((work / "out").exists())
            serialized_report = json.dumps(report, ensure_ascii=False)
            for canary in canaries:
                self.assertNotIn(canary, serialized_report)
    def test_review_restoration_requires_valid_source_boundaries(self):
        match = masker.RedactionMatch("NAME", "홍길동", 0, 3, action="review")
        with patch.object(
            masker,
            "mask_text",
            return_value=("[NAME]", {"NAME": 1}, [match]),
        ), patch.object(masker, "current_masking_source_boundaries", return_value=()):
            with self.assertRaisesRegex(masker.RequiredMaskMappingError, "^REQUIRED_MASK_MAPPING_FAILED$"):
                masker._mask_text_chunk("홍길동", {"profile": "mixed"})

    def test_safe_output_paths_use_safe_report_name(self):
        paths = masker.safe_output_paths("/tmp/synthetic.pdf", outdir="/tmp/out")
        self.assertIn("safe_report", paths["report_json"])
        self.assertTrue(paths["report_json"].endswith(".json"))

    def test_safe_report_omits_raw_match_values(self):
        source_text = "연락처: 010-0000-0000\n주소: 테스트시 테스트구 테스트동"
        report = masker.build_safe_report(
            input_file="/tmp/synthetic.pdf",
            opts={"profile": "mixed"},
            counts={"PHONE": 1, "ADDRESS": 1},
            redaction_matches=[
                masker.RedactionMatch("PHONE", "010-0000-0000"),
                masker.RedactionMatch("ADDRESS", "테스트시 테스트구 테스트동"),
            ],
            extract_meta={"engine_used": "plain-text", "chars": 10, "notes": []},
            pdf_redaction_result={
                "verification": {"verified": True, "residual_hits": 0},
                "targets_requested": 2,
                "targets_hit": 2,
                "missing_targets_count": 0,
            },
            output_paths={"report_path": "/tmp/out/synthetic.safe_report.json"},
            source_text=source_text,
        )
        encoded = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("010-0000-0000", encoded)
        self.assertNotIn("테스트시 테스트구 테스트동", encoded)
        self.assertFalse(report["raw_values_saved"])
        self.assertEqual("[PHONE]", report["review_items"][0]["display_token"])
        runtime_manifest = masker.runtime_manifest_for_report(report)
        self.assertEqual("phone", runtime_manifest["detected_spans"][0]["label"])
        self.assertEqual("regex_phone", runtime_manifest["detected_spans"][0]["source"])
        self.assertFalse(runtime_manifest["detected_spans"][0]["raw_text_stored"])
        self.assertNotIn("text", runtime_manifest["detected_spans"][0])
        address_items = [item for item in report["review_items"] if item["tag"] == "ADDRESS"]
        self.assertEqual("needs_review", address_items[0]["status"])

    def test_safe_report_recursively_omits_raw_text_paths_and_errors(self):
        report_canaries = {
            "raw_text": "RAW_TEXT_CANARY_홍길동_900101-1234567",
            "path": "/tmp/RAW_PATH_CANARY.pdf",
            "error": "RAW_ERROR_CANARY",
        }
        report = masker.build_safe_report(
            input_file=report_canaries["path"],
            opts={"profile": "mixed", "untrusted": {"raw_text": report_canaries["raw_text"]}},
            counts={"PHONE": 1},
            redaction_matches=[masker.RedactionMatch("PHONE", "010-1234-5678", start=7, end=20)],
            extract_meta={
                "engine_used": "plain-text",
                "chars": 20,
                "notes": [{"nested": {"text": report_canaries["raw_text"], "path": report_canaries["path"], "error": report_canaries["error"]}}],
            },
            pdf_redaction_result={
                "status": "failed",
                "reason": f"{report_canaries['error']}: {report_canaries['raw_text']}",
                "verification": {"verified": False, "reason": {"nested": report_canaries}},
                "targets_requested": 1,
                "targets_hit": 0,
                "missing_targets_count": 1,
            },
            output_paths={"report_path": report_canaries["path"], "masked_pdf_file": report_canaries["path"]},
            source_text=report_canaries["raw_text"],
        )

        runtime_canaries = {
            "raw_value": "RUNTIME_RAW_VALUE_CANARY_010-1234-5678",
            "path": "/private/RUNTIME_PATH_CANARY.pdf",
            "error": "RUNTIME_ERROR_CANARY",
        }
        runtime_report = masker.build_safe_report(
            input_file="/safe/input.pdf",
            opts={"profile": "mixed"},
            counts={"PHONE": 1},
            redaction_matches=[masker.RedactionMatch("PHONE", "010-1234-5678")],
            extract_meta={"engine_used": "plain-text", "chars": 20, "notes": []},
            pdf_redaction_result={
                "verification": {"verified": True},
                "targets_requested": 1,
                "targets_hit": 1,
                "missing_targets_count": 0,
                "review_items": [{
                    "tag": "PHONE",
                    "bbox": {
                        "x": 10,
                        "y": 20,
                        "width": 30,
                        "height": 40,
                        "nested": {"raw_value": runtime_canaries["raw_value"]},
                    },
                    "rects": [{
                        "x0": 10,
                        "y0": 20,
                        "x1": 40,
                        "y1": 60,
                        "nested": {
                            "path": runtime_canaries["path"],
                            "error": runtime_canaries["error"],
                        },
                    }],
                    "nested": runtime_canaries,
                }],
            },
            output_paths={"report_path": "/safe/report.json"},
            source_text="safe fixture",
        )
        runtime_manifest = masker.runtime_manifest_for_report(runtime_report)

        def assert_safe(value: object, canaries: dict[str, str]) -> None:
            encoded = json.dumps(value, ensure_ascii=False)
            for canary in canaries.values():
                with self.subTest(value_type=type(value).__name__, canary=canary):
                    self.assertNotIn(canary, encoded)

            def scan(node: object) -> None:
                if isinstance(node, dict):
                    for key, child in node.items():
                        self.assertNotIn(key.lower(), {"raw_text", "text", "raw_value", "rawvalue", "value", "path", "error", "exception"})
                        scan(child)
                elif isinstance(node, list):
                    for child in node:
                        scan(child)

            scan(value)

        assert_safe(report, report_canaries)
        assert_safe(runtime_manifest, runtime_canaries)
        runtime_item = runtime_manifest["review_items"][0]
        self.assertEqual(
            {"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0},
            runtime_item["bbox"],
        )
        self.assertEqual(
            [{"x0": 10.0, "y0": 20.0, "x1": 40.0, "y1": 60.0}],
            runtime_item["rects"],
        )
        self.assertEqual({"PHONE": 1}, report["counts"])

    def test_native_pdf_review_items_keep_safe_bbox_without_raw_match(self):
        target = "ALPHA REVIEW TOKEN"
        with tempfile.TemporaryDirectory() as tmpdir:
            source_pdf = Path(tmpdir) / "source.pdf"
            output_pdf = Path(tmpdir) / "masked.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), target, fontsize=12)
            doc.save(source_pdf)
            doc.close()

            result = masker.redact_pdf_native(
                str(source_pdf),
                str(output_pdf),
                [masker.RedactionMatch("KEYWORD", target)],
                display_mode="label_ko",
                profile="legal",
                legal_compatibility=True,
            )
            for profile, legal_compatibility in (("mixed", False), ("mixed", True), ("legal", False)):
                with self.subTest(profile=profile, legal_compatibility=legal_compatibility):
                    with self.assertRaisesRegex(ValueError, "^PUBLIC_OCCURRENCE_INPUTS_REQUIRED$"):
                        masker.redact_pdf_native(
                            str(source_pdf), str(Path(tmpdir) / f"{profile}-rejected.pdf"),
                            [masker.RedactionMatch("KEYWORD", target)],
                            profile=profile, legal_compatibility=legal_compatibility,
                        )

        encoded = json.dumps(result["review_items"], ensure_ascii=False)
        self.assertNotIn(target, encoded)
        self.assertEqual(1, len(result["review_items"]))
        self.assertEqual(0, result["review_items"][0]["page"])
        self.assertEqual("KEYWORD", result["review_items"][0]["tag"])
        self.assertEqual("applied", result["review_items"][0]["status"])
        self.assertFalse(result["review_items"][0]["raw_value_saved"])
        bbox = result["review_items"][0]["bbox"]
        self.assertGreater(bbox["width"], 0)
        self.assertGreater(bbox["height"], 0)

    def test_quality_gate_fails_closed_for_malformed_or_contradictory_results(self):
        passing = {
            "verification": {"verified": True, "residual_hits": 0},
            "targets_requested": 2,
            "targets_hit": 2,
            "missing_targets_count": 0,
        }
        failures = {
            "unverified": {**passing, "verification": {"verified": False, "residual_hits": 0}},
            "residual": {**passing, "verification": {"verified": True, "residual_hits": 1}},
            "missing": {**passing, "targets_hit": 1, "missing_targets_count": 1},
            "contradictory_missing": {**passing, "targets_hit": 1, "missing_targets_count": 0},
            "contradictory_hit": {**passing, "targets_hit": 2, "missing_targets_count": 1},
            "missing_verification": {key: value for key, value in passing.items() if key != "verification"},
            "malformed_verification": {**passing, "verification": "verified"},
            "zero_targets": {**passing, "targets_requested": 0, "targets_hit": 0},
        }
        scalar_failures = (
            {"verification": {"verified": 1, "residual_hits": 0}},
            {"verification": {"verified": "true", "residual_hits": 0}},
        )
        for mutation in scalar_failures:
            with self.subTest(mutation=mutation):
                self.assertFalse(masker.evaluate_quality_gate({**passing, **mutation}))
        for counter_path in (
            ("verification", "residual_hits"),
            ("result", "targets_requested"),
            ("result", "targets_hit"),
            ("result", "missing_targets_count"),
        ):
            for malformed in (True, "2", -1, float("inf"), 1.5):
                with self.subTest(counter=counter_path, malformed=malformed):
                    result = {**passing, "verification": {**passing["verification"]}}
                    if counter_path[0] == "verification":
                        result["verification"][counter_path[1]] = malformed
                    else:
                        result[counter_path[1]] = malformed
                    self.assertFalse(masker.evaluate_quality_gate(result))
        self.assertTrue(masker.evaluate_quality_gate(passing))
        for name, result in failures.items():
            with self.subTest(case=name):
                self.assertFalse(masker.evaluate_quality_gate(result))

    def test_pattern_order_masks_card_passport_and_phone_without_phone_overmatch(self):
        text = "카드번호: 4000-0000-0000-0000\n여권번호: M00000000\n연락처: 010-0000-0000"
        masked, counts, matches = masker.mask_text(text, profile="mixed")
        self.assertIn("[CARD]", masked)
        self.assertIn("[PASSPORT]", masked)
        self.assertIn("[PHONE]", masked)
        self.assertEqual(1, counts.get("CARD"))
        self.assertEqual(1, counts.get("PASSPORT"))
        self.assertEqual(1, counts.get("PHONE"))
        self.assertNotIn("4000-0000-0000-0000", [m.text for m in matches if m.tag == "PHONE"])

    def test_compact_public_document_header_masks_adjacent_landline_numbers(self):
        text = "작성과개인정보보호협력과담당자과장이윤숙사무관소진숙연락처02-2100-413002-2100-4136"
        masked, counts, matches = masker.mask_text(text, profile="mixed")

        self.assertEqual(2, counts.get("PHONE"))
        self.assertNotIn("02-2100-4130", masked)
        self.assertNotIn("02-2100-4136", masked)
        self.assertEqual(
            ["02-2100-4130", "02-2100-4136"],
            [match.text for match in matches if match.tag == "PHONE"],
        )

    def test_compact_public_document_header_role_name_is_preserved(self):
        text = "작성과개인정보보호협력과담당자과장이윤숙사무관소진숙연락처02-2100-4130"
        masked, counts, matches = mask_with_confirmed_approval_context(text)

        self.assertNotIn("APPROVAL_LINE", counts)
        self.assertIn("소진숙", masked)
        self.assertIn("연락처[PHONE]", masked)
        self.assertFalse(any(match.tag == "APPROVAL_LINE" for match in matches))

    def test_acting_approval_marker_is_preserved_in_text_queue(self):
        text = "업팀장 代유미정친 환 경 건 물 과\n장\n05/02\n代안승현\n협조자"
        masked, counts, matches = mask_with_confirmed_approval_context(text)

        self.assertNotIn("APPROVAL_LINE", counts)
        self.assertIn("유미정", masked)
        self.assertIn("안승현", masked)
        self.assertFalse(any(match.tag == "APPROVAL_LINE" for match in matches))

    def test_body_reference_and_marketing_consent_do_not_mask_as_doc_meta(self):
        text = (
            "개인정보의 안전성 확보 조치 기준 해설서 p57 ~ p65 참조\n"
            "마케팅 수신 동의(선택) ○1. 개인정보의 수집‧이용 목적"
        )
        masked, counts, matches = masker.mask_text(text, profile="mixed")

        self.assertIsNone(counts.get("DOC_META"))
        self.assertNotIn("[DOC_META]", masked)
        self.assertEqual([], [match.tag for match in matches if match.tag == "DOC_META"])

    def test_region_data_loaded_and_weak_place_goes_to_review(self):
        regions = masker.load_region_data()
        self.assertIn("부산광역시", regions["sido"])
        raw_place = "가곡동"
        masked, counts, matches = masker.mask_text(raw_place, profile="mixed")
        self.assertEqual(raw_place, masked)
        self.assertEqual(1, counts["WEAK_PLACE"])
        self.assertEqual(1, len(matches))
        self.assertEqual("review", matches[0].action)
        review = masker.review_items_for_matches(matches, counts)
        encoded = json.dumps(review, ensure_ascii=False)
        place_items = [item for item in review if item["tag"] == "PLACE"]
        self.assertEqual(1, len(place_items))
        self.assertEqual(1, place_items[0]["count"])
        self.assertEqual("needs_review", place_items[0]["status"])
        self.assertEqual("[PLACE]", place_items[0]["display_token"])
        self.assertFalse(place_items[0]["raw_value_saved"])
        self.assertNotIn(raw_place, encoded)

        report = masker.build_safe_report(
            input_file="/tmp/weak-place.pdf",
            opts={"profile": "mixed"},
            counts=counts,
            redaction_matches=matches,
            extract_meta={"engine_used": "fixture", "chars": len(raw_place), "notes": []},
            pdf_redaction_result={
                "verification": {"verified": True},
                "targets_requested": 1,
                "targets_hit": 1,
                "missing_targets_count": 0,
            },
            output_paths={"report_path": "/tmp/safe-report.json"},
            source_text=raw_place,
        )
        self.assertTrue(report["product_checks"]["needs_manual_review"])
        self.assertEqual(1, report["review_items"][0]["count"])
    def test_custom_region_terms_are_masked_as_region(self):
        baseline, baseline_counts, _baseline_matches, _baseline_meta = masker.process_masking_queue(
            "문맥 없는 커스텀구역가",
            {},
        )
        self.assertNotIn("[REGION]", baseline)
        self.assertIsNone(baseline_counts.get("REGION"))

        masked, counts, matches, _meta = masker.process_masking_queue(
            "문맥 없는 커스텀구역가",
            {"custom_regions": "커스텀구역가"},
        )
        self.assertIn("[REGION]", masked)
        self.assertEqual(1, counts.get("REGION"))
        self.assertEqual(["REGION"], [match.tag for match in matches])

    def test_common_business_terms_do_not_mask_as_weak_places(self):
        samples = [
            "하자 관리 계획",
            "시설 관리 대장",
            "품질 관리 기준",
            "안전 관리 책임자",
            "이동 평균 계산",
            "수동 입력 방식",
            "상동 내용 참조",
            "중동 정렬",
            "대리 처리",
        ]

        for sample in samples:
            with self.subTest(sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile="mixed")
                self.assertEqual(sample, masked)
                self.assertEqual({}, counts)
                self.assertEqual([], matches)

    def test_broad_labels_do_not_mask_plain_business_phrases(self):
        samples = [
            "주소: 시스템 개선 요청",
            "주소: 품질관리팀 101호",
            "소재지: 관리 부서",
            "대표자 시스템",
            "신청인 제도",
            "원고 주장 피고 반박",
            "회사명 관리 시스템",
            "상호 하자관리",
            "기관명 품질관리팀",
            "담당자 관리",
            "담당부서 하자관리팀",
            "부서 품질관리팀",
            "팀장 관리",
            "검토자 관리",
        ]

        for sample in samples:
            with self.subTest(sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile="mixed")
                self.assertEqual(sample, masked)
                self.assertEqual({}, counts)
                self.assertEqual([], matches)

    def test_false_positive_guard_keeps_high_confidence_masks(self):
        cases = [
            ("mixed", "연락처: 010-1234-5678", "[PHONE]", "PHONE", 1),
            ("mixed", "주소: 부산광역시 해운대구 우동 테스트로 0", "[ADDRESS]", "ADDRESS", 1),
            ("mixed", "담당자 홍길동", "[NAME]", "NAME", 1),
            ("legal", "원고 홍길동 피고 김철수", "[LEGAL_PARTY]", "LEGAL_PARTY", 2),
            ("mixed", "사업자등록번호: 123-45-67890", "[BUSINESS_REG_NO]", "BUSINESS_REG_NO", 1),
        ]
        for profile, sample, token, tag, expected_count in cases:
            with self.subTest(profile=profile, sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile=profile)
                self.assertEqual(expected_count, masked.count(token))
                self.assertEqual(expected_count, counts.get(tag))
                self.assertEqual(expected_count, len([match for match in matches if match.tag == tag]))

    def test_deidentification_policy_defaults_to_existing_tokens(self):
        masked, counts, matches, meta = masker.process_masking_queue(
            "담당자 홍길동 연락처: 010-1234-5678",
            {"profile": "mixed"},
        )

        self.assertNotIn("[NAME]", masked)
        self.assertIn("홍길동", masked)
        self.assertIn("[PHONE]", masked)
        self.assertEqual("token", meta["deidentification_policy"])
        self.assertEqual(1, counts.get("NAME"))
        self.assertEqual(1, counts.get("PHONE"))
        actions = {match.tag: match.action for match in matches}
        self.assertEqual({"NAME": "review", "PHONE": "mask"}, actions)

    def test_partial_deidentification_policy_keeps_readable_nonidentifying_shape(self):
        masked, counts, _matches, meta = masker.process_masking_queue(
            "연락처: 010-1234-5678 이메일 test@example.com",
            {"profile": "mixed", "deidentification_policy": "partial"},
        )

        self.assertIn("연락처: 010-****-5678", masked)
        self.assertIn("t***@example.com", masked)
        self.assertEqual("partial", meta["deidentification_policy"])
        self.assertEqual(1, counts.get("PHONE"))
        self.assertEqual(1, counts.get("EMAIL"))

    def test_pseudonym_policy_applies_to_required_phone_mask(self):
        masked, _counts, _matches, meta = masker.process_masking_queue(
            "연락처: 010-1234-5678",
            {"profile": "mixed", "deidentification_policy": "pseudonym"},
        )

        self.assertIn("010-0000-", masked)
        self.assertEqual("pseudonym", meta["deidentification_policy"])

    def test_safe_report_marks_partial_and_pseudonym_text_as_review_only(self):
        source_text = "연락처: 010-1234-5678"
        for policy in ("partial", "pseudonym"):
            with self.subTest(policy=policy):
                report = masker.build_safe_report(
                    input_file="/tmp/synthetic.pdf",
                    opts={"profile": "mixed", "deidentification_policy": policy},
                    counts={"PHONE": 1},
                    redaction_matches=[masker.RedactionMatch("PHONE", "010-1234-5678")],
                    extract_meta={"engine_used": "plain-text", "chars": 10, "notes": []},
                    pdf_redaction_result={
                        "verification": {"verified": True, "residual_hits": 0},
                        "targets_requested": 1,
                        "targets_hit": 1,
                        "missing_targets_count": 0,
                    },
                    output_paths={"report_path": "/tmp/out/synthetic.safe_report.json"},
                    source_text=source_text,
                )

                encoded = json.dumps(report, ensure_ascii=False)
                self.assertTrue(report["product_checks"]["final_submission_allowed"])
                self.assertFalse(report["product_checks"]["text_deidentification_final_submission_evidence"])
                self.assertEqual(policy, report["text_deidentification"]["policy"])
                self.assertEqual("text_preview_and_txt_output_only", report["text_deidentification"]["scope"])
                self.assertFalse(report["text_deidentification"]["final_submission_evidence"])
                self.assertIn("review-only", " ".join(report["warnings"]))
                self.assertNotIn(source_text, encoded)

    def test_business_workflow_terms_do_not_mask_as_approval_region_or_case_number(self):
        samples = [
            "대리 만족도 조사",
            "건축과장 만족도",
            "급수관리팀장 만족도",
            "시설팀장 처리율",
            "하자관리과장 검토결과",
            "원고 품질 기준",
            "전결 처리 기준",
            "대결 결과 보고",
            "지역: 중동 정렬",
            "위치: 관리 사무실",
            "프로젝트 2026가 123",
            "예산 2026나 456",
        ]

        for sample in samples:
            with self.subTest(sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile="mixed")
                self.assertEqual(sample, masked)
                self.assertEqual({}, counts)
                self.assertEqual([], matches)

    def test_public_approval_text_is_preserved_and_legal_masks(self):
        approval_cases = [
            "대리 홍길동",
            "건축과장 김철수",
            "급수관리팀장 이한수",
            "팀장(안승현)",
            "공공주택과장 04/30 하대근",
            "결재구분 전결",
        ]
        for sample in approval_cases:
            with self.subTest(sample=sample):
                masked, counts, matches = mask_with_confirmed_approval_context(sample)
                self.assertEqual(sample, masked)
                self.assertFalse(counts)
                self.assertEqual([], matches)

        raw_address = "지역: 부산광역시 해운대구"
        masked, counts, matches = mask_with_confirmed_approval_context(raw_address)
        self.assertEqual(raw_address, masked)
        self.assertEqual(1, counts.get("ADDRESS"))
        self.assertEqual("review", matches[0].action)

        masked, counts, matches = masker.mask_text("사건번호 2026가 123", profile="legal")
        self.assertIn("[CASE_NUMBER]", masked)
        self.assertEqual(1, counts.get("CASE_NUMBER"))
        self.assertEqual(["CASE_NUMBER"], [match.tag for match in matches])

    def test_legal_profile_masks_current_case_number_but_preserves_citation_numbers(self):
        text = "\n".join(
            [
                "사건번호 2026가 123",
                "대법원 2024. 1. 2. 선고 2023다12345 판결 참조",
                "서울고등법원 2024. 1. 2. 선고 2023나12345 판결 참조",
                "대법원 2024. 1. 2.자 2023마12345 결정 참조",
                "대법원 2023다12345 판결 취지",
            ],
        )

        masked, counts, matches = masker.mask_text(text, profile="legal")

        self.assertIn("사건번호 [CASE_NUMBER]", masked)
        self.assertEqual(1, counts.get("CASE_NUMBER"))
        self.assertIn("2023다12345", masked)
        self.assertIn("2023나12345", masked)
        self.assertIn("2023마12345", masked)
        self.assertIn("대법원 2024. 1. 2.자 2023마12345 결정", masked)
        self.assertIn("대법원 2023다12345 판결", masked)
        self.assertNotIn("[APPROVAL_LINE]", masked)
        self.assertEqual(["CASE_NUMBER"], [match.tag for match in matches if match.tag == "CASE_NUMBER"])

    def test_law_firm_business_phrases_are_not_masked_as_firm_names(self):
        samples = [
            "법무법인 검토 결과",
            "법률사무소 검토의견",
            "변호사사무실 사건자료",
        ]

        for sample in samples:
            with self.subTest(sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile="mixed")
                self.assertEqual(sample, masked)
                self.assertEqual({}, counts)
                self.assertEqual([], matches)

    def test_law_firm_names_mask_only_name_portion_in_legal_profile(self):
        cases = [
            "법무법인 한빛",
            "법무법인 태평양",
            "소속법무법인: 한빛",
            "법률사무소 ABC",
        ]

        for sample in cases:
            with self.subTest(sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile="legal")
                self.assertIn("[LAW_FIRM]", masked)
                self.assertNotEqual("[LAW_FIRM][LAW_FIRM]", masked)
                self.assertEqual(1, counts.get("LAW_FIRM"))
                self.assertEqual(["LAW_FIRM"], [match.tag for match in matches])

    def test_general_titles_do_not_mask_as_case_titles_without_legal_context(self):
        samples = [
            "제목: 2026가 123 예산",
            "제목: 손해배상 청구",
            "2026가 123 손해배상",
        ]

        for sample in samples:
            with self.subTest(sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile="mixed")
                self.assertEqual(sample, masked)
                self.assertEqual({}, counts)
                self.assertEqual([], matches)

    def test_legal_profile_keeps_case_title_masking(self):
        cases = [
            "제목: 손해배상 청구",
            "사건명: 손해배상 청구",
            "2026가 123 손해배상",
        ]

        for sample in cases:
            with self.subTest(sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile="legal")
                self.assertIn("[CASE_TITLE]", masked)
                self.assertEqual(1, counts.get("CASE_TITLE"))
                self.assertEqual(["CASE_TITLE"], [match.tag for match in matches])

    def test_region_metadata_reports_primary_and_forced_seed_fallback(self):
        primary = masker.region_data_metadata()
        self.assertFalse(primary["region_data_is_seed"])
        self.assertEqual("2", primary["region_data_version"])
        self.assertIn("official", primary["region_data_source"])
        self.assertIn("부산광역시", masker.load_region_data()["sido"])

        masking_rules._region_terms.cache_clear()
        try:
            with patch.object(masking_rules, "REGION_DATA_PATH", Path("/unavailable/kr_regions.json")):
                seed = masker.region_data_metadata()
                self.assertTrue(seed["region_data_is_seed"])
                self.assertEqual("1", seed["region_data_version"])
                self.assertTrue(seed["region_data_path"].endswith("kr_regions.seed.json"))
                self.assertIn("seed", seed["region_data_source"])
                masked, counts, _matches = masker.mask_text("주소: 부산광역시 해운대구 우동", profile="mixed")
                self.assertIn("[ADDRESS]", masked)
                self.assertGreater(counts.get("ADDRESS", 0), 0)
        finally:
            masking_rules._region_terms.cache_clear()

    def test_pdf_label_redaction_keeps_english_and_korean_label_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            input_pdf = work / "label.pdf"
            output_en_pdf = work / "label_en_out.pdf"
            output_ko_pdf = work / "label_ko_out.pdf"
            doc = fitz.open()
            page = doc.new_page(width=320, height=160)
            page.insert_text((32, 52), "010-0000-0000")
            page.insert_text((32, 92), "Seoul Address 10")
            doc.save(input_pdf)
            doc.close()
            matches = [
                masker.RedactionMatch("PHONE", "010-0000-0000"),
                masker.RedactionMatch("ADDRESS", "Seoul Address 10"),
            ]

            masker.redact_pdf_native(
                str(input_pdf),
                str(output_en_pdf),
                matches,
                display_mode="label_en",
                profile="legal",
                legal_compatibility=True,
            )
            masker.redact_pdf_native(
                str(input_pdf),
                str(output_ko_pdf),
                matches,
                display_mode="label_ko",
                profile="legal",
                legal_compatibility=True,
            )

            for path, expected in [
                (output_en_pdf, ["[PHONE]", "[ADDRESS]"]),
                (output_ko_pdf, ["[전화번호]", "[주소]"]),
            ]:
                result_doc = fitz.open(path)
                try:
                    text = "\n".join(page.get_text() for page in result_doc)
                    phone_hits = sum(len(page.search_for("010-0000-0000")) for page in result_doc)
                    address_hits = sum(len(page.search_for("Seoul Address 10")) for page in result_doc)
                finally:
                    result_doc.close()
                for label in expected:
                    self.assertIn(label, text)
                self.assertNotIn("010-0000-0000", text)
                self.assertNotIn("Seoul Address 10", text)
                self.assertEqual(0, phone_hits)
                self.assertEqual(0, address_hits)
                self.assertNotIn("[????]", text)
                self.assertNotIn("[???]", text)



if __name__ == "__main__":
    unittest.main()
