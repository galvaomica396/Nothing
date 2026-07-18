import json
import tempfile
import unittest
from pathlib import Path

import fitz

import document_masker_ocr_gui as masker


class SecurityDefaultsTests(unittest.TestCase):
    def test_default_output_artifacts_do_not_include_raw_txt(self):
        artifacts = masker.resolve_output_artifacts({})
        self.assertEqual({"pdf", "report"}, artifacts)

    def test_safe_output_paths_use_safe_report_name(self):
        paths = masker.safe_output_paths("/tmp/synthetic.pdf", outdir="/tmp/out")
        self.assertIn("safe_report", paths["report_json"])
        self.assertTrue(paths["report_json"].endswith(".json"))

    def test_safe_report_omits_raw_match_values(self):
        source_text = "연락처: 010-0000-0000\n주소: 테스트시 테스트구 테스트동"
        report = masker.build_safe_report(
            input_file="/tmp/synthetic.pdf",
            opts={"profile": "official"},
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

    def test_safe_report_contains_counts_not_paths_coordinates_offsets_or_raw_errors(self):
        canary = "홍길동_900101-1234567"
        report = masker.build_safe_report(
            input_file=f"/tmp/{canary}.pdf",
            opts={"profile": "official"},
            counts={"PHONE": 1},
            redaction_matches=[masker.RedactionMatch("PHONE", "010-1234-5678", start=7, end=20)],
            extract_meta={"engine_used": "plain-text", "chars": 20, "notes": []},
            pdf_redaction_result={
                "status": "failed",
                "reason": f"raw failure {canary}",
                "verification": {"verified": False, "reason": f"trace {canary}"},
                "targets_requested": 1,
                "targets_hit": 0,
                "missing_targets_count": 1,
                "review_items": [{
                    "tag": "PHONE",
                    "status": "missing_pdf_rect",
                    "page": 0,
                    "bbox": {"x": 10, "y": 20, "width": 30, "height": 40},
                    "display_token": "[PHONE]",
                    "count": 1,
                }],
            },
            output_paths={
                "report_path": f"/tmp/{canary}.safe_report.json",
                "masked_pdf_file": f"/tmp/{canary}.masked.pdf",
            },
            source_text="연락처 010-1234-5678",
        )
        encoded = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(canary, encoded)
        self.assertNotIn("/tmp/", encoded)
        self.assertNotIn('"bbox"', encoded)
        self.assertNotIn('"start"', encoded)
        self.assertNotIn('"end"', encoded)
        self.assertNotIn("raw failure", encoded)
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

    def test_quality_gate_requires_all_targets_hit(self):
        result = {
            "verification": {"verified": True, "residual_hits": 0},
            "targets_requested": 3,
            "targets_hit": 2,
            "missing_targets_count": 1,
        }
        self.assertFalse(masker.evaluate_quality_gate(result))

    def test_pattern_order_masks_card_passport_and_phone_without_phone_overmatch(self):
        text = "카드번호: 4000-0000-0000-0000\n여권번호: M00000000\n연락처: 010-0000-0000"
        masked, counts, matches = masker.mask_text(text, profile="official")
        self.assertIn("[CARD]", masked)
        self.assertIn("[PASSPORT]", masked)
        self.assertIn("[PHONE]", masked)
        self.assertEqual(1, counts.get("CARD"))
        self.assertEqual(1, counts.get("PASSPORT"))
        self.assertEqual(1, counts.get("PHONE"))
        self.assertNotIn("4000-0000-0000-0000", [m.text for m in matches if m.tag == "PHONE"])

    def test_compact_public_document_header_masks_adjacent_landline_numbers(self):
        text = "작성과개인정보보호협력과담당자과장이윤숙사무관소진숙연락처02-2100-413002-2100-4136"
        masked, counts, matches = masker.mask_text(text, profile="official")

        self.assertEqual(2, counts.get("PHONE"))
        self.assertNotIn("02-2100-4130", masked)
        self.assertNotIn("02-2100-4136", masked)
        self.assertEqual(
            ["02-2100-4130", "02-2100-4136"],
            [match.text for match in matches if match.tag == "PHONE"],
        )

    def test_compact_public_document_header_role_name_stops_before_contact_label(self):
        text = "작성과개인정보보호협력과담당자과장이윤숙사무관소진숙연락처02-2100-4130"
        masked, counts, matches = masker.mask_text(text, profile="official")

        self.assertEqual(1, counts.get("APPROVAL_LINE"))
        self.assertIn("연락처[PHONE]", masked)
        self.assertIn(masker.RedactionMatch("APPROVAL_LINE", "소진숙"), matches)
        self.assertNotIn(masker.RedactionMatch("APPROVAL_LINE", "소진숙연"), matches)

    def test_acting_approval_marker_masks_substitute_approver_names(self):
        text = "업팀장 代유미정친 환 경 건 물 과\n장\n05/02\n代안승현\n협조자"
        masked, counts, matches = masker.mask_text(text, profile="official")

        self.assertEqual(2, counts.get("APPROVAL_LINE"))
        self.assertNotIn("유미정", masked)
        self.assertNotIn("안승현", masked)
        self.assertIn(masker.RedactionMatch("APPROVAL_LINE", "유미정"), matches)
        self.assertIn(masker.RedactionMatch("APPROVAL_LINE", "안승현"), matches)

    def test_body_reference_and_marketing_consent_do_not_mask_as_doc_meta(self):
        text = (
            "개인정보의 안전성 확보 조치 기준 해설서 p57 ~ p65 참조\n"
            "마케팅 수신 동의(선택) ○1. 개인정보의 수집‧이용 목적"
        )
        masked, counts, matches = masker.mask_text(text, profile="official")

        self.assertIsNone(counts.get("DOC_META"))
        self.assertNotIn("[DOC_META]", masked)
        self.assertNotIn("DOC_META", {match.tag for match in matches})

    def test_region_data_loaded_and_weak_place_goes_to_review(self):
        regions = masker.load_region_data()
        self.assertIn("부산광역시", regions["sido"])
        masked, counts, matches = masker.mask_text("주소: 부산광역시 해운대구 우동 테스트로 0", profile="official")
        self.assertIn("[ADDRESS]", masked)
        review = masker.review_items_for_matches(matches, counts)
        encoded = json.dumps(review, ensure_ascii=False)
        self.assertNotIn("부산광역시 해운대구 우동", encoded)

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
                masked, counts, matches = masker.mask_text(sample, profile="official")
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
                masked, counts, matches = masker.mask_text(sample, profile="official")
                self.assertEqual(sample, masked)
                self.assertEqual({}, counts)
                self.assertEqual([], matches)

    def test_false_positive_guard_keeps_high_confidence_masks(self):
        cases = [
            ("연락처: 010-1234-5678", "[PHONE]", "PHONE"),
            ("주소: 부산광역시 해운대구 우동 테스트로 0", "[ADDRESS]", "ADDRESS"),
            ("담당자 홍길동", "[NAME]", "NAME"),
            ("원고 홍길동 피고 김철수", "[LEGAL_PARTY]", "LEGAL_PARTY"),
            ("사업자등록번호: 123-45-67890", "[BUSINESS_REG_NO]", "BUSINESS_REG_NO"),
        ]

        for sample, token, tag in cases:
            with self.subTest(sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile="official")
                self.assertIn(token, masked)
                self.assertGreaterEqual(counts.get(tag, 0), 1)
                self.assertIn(tag, [match.tag for match in matches])

    def test_deidentification_policy_defaults_to_existing_tokens(self):
        masked, counts, matches, meta = masker.process_masking_queue(
            "담당자 홍길동 연락처: 010-1234-5678",
            {"profile": "official"},
        )

        self.assertIn("[NAME]", masked)
        self.assertIn("[PHONE]", masked)
        self.assertEqual("token", meta["deidentification_policy"])
        self.assertEqual(1, counts.get("NAME"))
        self.assertEqual(1, counts.get("PHONE"))
        self.assertEqual({"NAME", "PHONE"}, {match.tag for match in matches})

    def test_partial_deidentification_policy_keeps_readable_nonidentifying_shape(self):
        masked, counts, _matches, meta = masker.process_masking_queue(
            "담당자 홍길동 연락처: 010-1234-5678 이메일 test@example.com",
            {"profile": "official", "deidentification_policy": "partial"},
        )

        self.assertIn("담당자 홍OO", masked)
        self.assertIn("연락처: 010-****-5678", masked)
        self.assertIn("t***@example.com", masked)
        self.assertNotIn("[NAME]", masked)
        self.assertEqual("partial", meta["deidentification_policy"])
        self.assertEqual(1, counts.get("NAME"))
        self.assertEqual(1, counts.get("PHONE"))
        self.assertEqual(1, counts.get("EMAIL"))

    def test_pseudonym_policy_is_consistent_within_document(self):
        masked, _counts, _matches, meta = masker.process_masking_queue(
            "담당자 홍길동 검토자 홍길동 연락처: 010-1234-5678",
            {"profile": "official", "deidentification_policy": "pseudonym"},
        )

        name_values = [part for part in masked.split() if part in {"김민준", "이서연", "박지훈", "최하은", "정도윤", "강서윤", "조현우", "윤지아"}]
        self.assertGreaterEqual(len(name_values), 2)
        self.assertEqual(1, len(set(name_values)))
        self.assertIn("010-0000-", masked)
        self.assertEqual("pseudonym", meta["deidentification_policy"])

    def test_safe_report_marks_partial_and_pseudonym_text_as_review_only(self):
        report = masker.build_safe_report(
            input_file="/tmp/synthetic.pdf",
            opts={"profile": "official", "deidentification_policy": "partial"},
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
            source_text="연락처: 010-1234-5678",
        )

        self.assertTrue(report["product_checks"]["final_submission_allowed"])
        self.assertFalse(report["product_checks"]["text_deidentification_final_submission_evidence"])
        self.assertEqual("partial", report["text_deidentification"]["policy"])
        self.assertEqual("text_preview_and_txt_output_only", report["text_deidentification"]["scope"])
        self.assertFalse(report["text_deidentification"]["final_submission_evidence"])
        self.assertIn("review-only", " ".join(report["warnings"]))

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
                masked, counts, matches = masker.mask_text(sample, profile="official")
                self.assertEqual(sample, masked)
                self.assertEqual({}, counts)
                self.assertEqual([], matches)

    def test_false_positive_tightening_keeps_contextual_approval_region_and_case_masks(self):
        cases = [
            ("대리 홍길동", "[APPROVAL_LINE]", "APPROVAL_LINE"),
            ("건축과장 김철수", "[APPROVAL_LINE]", "APPROVAL_LINE"),
            ("급수관리팀장 이한수", "[APPROVAL_LINE]", "APPROVAL_LINE"),
            ("팀장(안승현)", "[APPROVAL_LINE]", "APPROVAL_LINE"),
            ("공공주택과장 04/30 하대근", "[APPROVAL_LINE]", "APPROVAL_LINE"),
            ("결재구분 전결", "[APPROVAL_FLOW]", "APPROVAL_FLOW"),
            ("지역: 부산광역시 해운대구", "[ADDRESS]", "ADDRESS"),
            ("사건번호 2026가 123", "[CASE_NUMBER]", "CASE_NUMBER"),
        ]

        for sample, token, tag in cases:
            with self.subTest(sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile="official")
                self.assertIn(token, masked)
                self.assertGreaterEqual(counts.get(tag, 0), 1)
                self.assertIn(tag, [match.tag for match in matches])

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
                masked, counts, matches = masker.mask_text(sample, profile="official")
                self.assertEqual(sample, masked)
                self.assertEqual({}, counts)
                self.assertEqual([], matches)

    def test_law_firm_names_still_mask_only_name_portion(self):
        cases = [
            "법무법인 한빛",
            "법무법인 태평양",
            "소속법무법인: 한빛",
            "법률사무소 ABC",
        ]

        for sample in cases:
            with self.subTest(sample=sample):
                masked, counts, matches = masker.mask_text(sample, profile="official")
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
                masked, counts, matches = masker.mask_text(sample, profile="official")
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

    def test_region_metadata_reports_seed_fallback(self):
        meta = masker.region_data_metadata()
        self.assertIn("region_data_source", meta)
        self.assertIn("region_data_version", meta)
        self.assertIn("region_data_is_seed", meta)
        self.assertIsInstance(meta["region_data_is_seed"], bool)

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
            )
            masker.redact_pdf_native(
                str(input_pdf),
                str(output_ko_pdf),
                matches,
                display_mode="label_ko",
            )

            for path, expected in [
                (output_en_pdf, ["[PHONE]", "[ADDRESS]"]),
                (output_ko_pdf, ["[전화번호]", "[주소]"]),
            ]:
                result_doc = fitz.open(path)
                try:
                    text = "\n".join(page.get_text() for page in result_doc)
                finally:
                    result_doc.close()
                for label in expected:
                    self.assertIn(label, text)
                self.assertNotIn("[????]", text)
                self.assertNotIn("[???]", text)


if __name__ == "__main__":
    unittest.main()
