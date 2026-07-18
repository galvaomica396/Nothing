import json
import unittest
from unittest import mock

import document_masker_ocr_gui as masker
import masking_context
from masking_context import build_document_context, find_masking_context


class DocumentContextTests(unittest.TestCase):
    def test_chunk_search_normalization_is_computed_once_per_chunk(self):
        text = (
            "===== PAGE 1 =====\n첫 문장에는 Alpha Person이 있습니다.\n"
            "===== PAGE 2 =====\n둘째 문장에는 010 1111 2222가 있습니다."
        )
        context = build_document_context(text, chunk_size=64, overlap=8)
        matches = [
            masker.RedactionMatch("NAME", "Alpha-Person"),
            masker.RedactionMatch("PHONE", "010-1111-2222"),
        ]
        chunk_texts = {chunk.text for chunk in context.chunks}
        original_compact = masking_context._compact_search_text

        with mock.patch.object(
            masking_context,
            "_compact_search_text",
            wraps=original_compact,
        ) as compact:
            found = find_masking_context(matches, context)

        chunk_calls = [call.args[0] for call in compact.call_args_list if call.args[0] in chunk_texts]
        self.assertEqual(len(context.chunks), len(chunk_calls))
        self.assertEqual(chunk_texts, set(chunk_calls))
        self.assertEqual([1, 2], [item["page"] for item in found if item is not None])

    def test_context_chunks_follow_page_markers_and_sentence_boundaries(self):
        text = (
            "===== PAGE 1 =====\n"
            "첫 문장입니다. 둘째 문장에는 010-0000-0000이 있습니다.\n"
            "===== PAGE 2 =====\n"
            "셋째 문장은 서울특별시 테스트구 테스트동을 포함합니다."
        )

        context = build_document_context(text, chunk_size=44, overlap=8)

        self.assertGreaterEqual(len(context.chunks), 3)
        self.assertEqual(2, context.summary["page_count"])
        self.assertTrue(all(chunk.context_id.startswith("ctx-") for chunk in context.chunks))
        self.assertIn(1, {chunk.page for chunk in context.chunks})
        self.assertIn(2, {chunk.page for chunk in context.chunks})
        self.assertTrue(all(chunk.end_offset > chunk.start_offset for chunk in context.chunks))

    def test_find_masking_context_returns_safe_context_without_raw_value(self):
        text = "===== PAGE 3 =====\n연락처는 010-0000-0000입니다. 다음 줄은 일반 문장입니다."
        context = build_document_context(text, chunk_size=80, overlap=12)
        matches = [masker.RedactionMatch("PHONE", "010-0000-0000")]

        found = find_masking_context(matches, context)
        encoded = json.dumps(found, ensure_ascii=False)

        self.assertEqual(1, len(found))
        self.assertIsNotNone(found[0])
        self.assertEqual(3, found[0]["page"])
        self.assertFalse(found[0]["raw_text_saved"])
        self.assertNotIn("010-0000-0000", encoded)

    def test_context_search_normalizes_spacing_and_punctuation_without_raw_value(self):
        text = (
            "===== PAGE 1 =====\n"
            "검토용 일반 문장입니다.\n"
            "===== PAGE 2 =====\n"
            "담당자 연락처는 010 0000 0000 입니다."
        )
        context = build_document_context(text, chunk_size=80, overlap=12)
        matches = [masker.RedactionMatch("PHONE", "010-0000-0000")]

        found = find_masking_context(matches, context)
        encoded = json.dumps(found, ensure_ascii=False)

        self.assertIsNotNone(found[0])
        self.assertEqual(2, found[0]["page"])
        self.assertEqual("normalized", found[0]["confidence"])
        self.assertFalse(found[0]["raw_text_saved"])
        self.assertNotIn("010-0000-0000", encoded)
        self.assertNotIn("010 0000 0000", encoded)

    def test_review_items_keep_distinct_context_pages_for_same_tag(self):
        text = (
            "===== PAGE 1 =====\n"
            "첫 연락처는 010-1111-1111입니다.\n"
            "===== PAGE 2 =====\n"
            "둘째 연락처는 010-2222-2222입니다."
        )
        context = build_document_context(text, chunk_size=80, overlap=12)
        matches = [
            masker.RedactionMatch("PHONE", "010-1111-1111"),
            masker.RedactionMatch("PHONE", "010-2222-2222"),
        ]

        items = masker.review_items_for_matches(matches, {"PHONE": 2}, document_context=context)

        self.assertEqual(2, len(items))
        self.assertEqual([1, 2], [item["context"]["page"] for item in items])
        encoded = json.dumps(items, ensure_ascii=False)
        self.assertNotIn("010-1111-1111", encoded)
        self.assertNotIn("010-2222-2222", encoded)

    def test_safe_report_includes_context_summary_without_raw_values(self):
        report = masker.build_safe_report(
            input_file="/tmp/synthetic.pdf",
            opts={"profile": "official"},
            counts={"PHONE": 1},
            redaction_matches=[masker.RedactionMatch("PHONE", "010-0000-0000")],
            extract_meta={"engine_used": "pypdf", "chars": 64, "notes": []},
            pdf_redaction_result={
                "verification": {"verified": True, "residual_hits": 0},
                "targets_requested": 1,
                "targets_hit": 1,
                "missing_targets_count": 0,
            },
            output_paths={"report_path": "/tmp/out/synthetic.safe_report.json"},
            document_context=build_document_context(
                "===== PAGE 1 =====\n연락처는 010-0000-0000입니다.",
                chunk_size=80,
                overlap=12,
            ),
        )
        encoded = json.dumps(report, ensure_ascii=False)

        self.assertIn("document_context", report)
        self.assertEqual(1, report["document_context"]["summary"]["page_count"])
        self.assertFalse(report["document_context"]["summary"]["raw_text_saved"])
        self.assertNotIn("context", report["review_items"][0])
        self.assertNotIn("bbox", report["review_items"][0])
        runtime_manifest = masker.runtime_manifest_for_report(report)
        self.assertIn("context", runtime_manifest["review_items"][0])
        self.assertNotIn("010-0000-0000", encoded)


if __name__ == "__main__":
    unittest.main()
