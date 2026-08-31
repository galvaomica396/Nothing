import json
import unittest
import tempfile
from pathlib import Path
from unittest import mock

import document_masker_ocr_gui as masker
import masking_context
from masking_context import build_document_context, find_masking_context

SESSION_HASH_KEY = bytes(range(32))


class DocumentContextTests(unittest.TestCase):
    def test_chunks_are_declared_source_slices_with_stable_page_boundaries(self):
        text = (
            "===== PAGE 1 =====\n첫 문장입니다. 둘째 문장입니다.\n"
            "===== PAGE 2 =====\n셋째 문장입니다. 넷째 문장입니다."
        )
        context = build_document_context(text, chunk_size=44, overlap=8)

        self.assertEqual(2, context.summary["page_count"])
        self.assertTrue(context.chunks)
        for chunk in context.chunks:
            with self.subTest(context_id=chunk.context_id):
                self.assertEqual(
                    text[chunk.start_offset:chunk.end_offset],
                    chunk.text,
                )
                page_marker = f"===== PAGE {chunk.page} ====="
                self.assertIn(page_marker, text[:chunk.end_offset])
                self.assertNotIn(
                    f"===== PAGE {chunk.page + 1} =====",
                    chunk.text,
                )
        self.assertEqual(
            [chunk.start_offset for chunk in context.chunks],
            sorted(chunk.start_offset for chunk in context.chunks),
        )

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
    def test_inconsistent_authoritative_context_offsets_are_not_repaired_by_search(self):
        text = "===== PAGE 1 =====\n앞부분 010-0000-0000"
        context = build_document_context(text, chunk_size=80, overlap=12)
        mismatched = masker.RedactionMatch(
            "PHONE",
            "010-0000-0000",
            start=0,
            end=len("010-0000-0000"),
            occurrence_id="occ_mismatched",
            source="regex_phone",
        )

        with mock.patch("masking_context._search_score", side_effect=AssertionError("legacy search used")):
            found = find_masking_context([mismatched], context)

        self.assertEqual((None,), found)

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
        raw_text = "===== PAGE 1 =====\n연락처는 010-0000-0000입니다. /private/context-source.pdf"
        document_context = build_document_context(raw_text, chunk_size=80, overlap=12)
        matches = [masker.RedactionMatch("PHONE", "010-0000-0000")]
        located_reviews = masker.review_items_for_matches(
            matches, {"PHONE": 1}, document_context=document_context
        )
        safe_context = located_reviews[0]["context"]
        self.assertEqual("ctx-0001", safe_context["context_id"])
        self.assertEqual(1, safe_context["page"])
        self.assertFalse(safe_context["raw_text_saved"])

        report = masker.build_safe_report(
            input_file="/tmp/synthetic.pdf",
            opts={"profile": "mixed"},
            counts={"PHONE": 1},
            redaction_matches=matches,
            extract_meta={"engine_used": "pypdf", "chars": 64, "notes": []},
            pdf_redaction_result={
                "verification": {"verified": True, "residual_hits": 0},
                "targets_requested": 1,
                "targets_hit": 1,
                "missing_targets_count": 0,
                "review_items": [{
                    "tag": "PHONE",
                    "status": "applied",
                    "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                    "rects": [{"x0": 1, "y0": 2, "x1": 4, "y1": 6}],
                    "context": safe_context,
                }],
            },
            output_paths={"report_path": "/tmp/out/synthetic.safe_report.json"},
            document_context=document_context,
            source_text=raw_text,
        )
        encoded = json.dumps(report, ensure_ascii=False)
        runtime_manifest = masker.runtime_manifest_for_report(report)
        runtime_encoded = json.dumps(runtime_manifest, ensure_ascii=False)

        self.assertIn("document_context", report)
        self.assertEqual(1, report["document_context"]["summary"]["page_count"])
        self.assertFalse(report["document_context"]["summary"]["raw_text_saved"])
        self.assertNotIn("context", report["review_items"][0])
        self.assertNotIn("bbox", report["review_items"][0])
        runtime_review_item = runtime_manifest["review_items"][0]
        self.assertNotIn("context", runtime_review_item)
        self.assertEqual(
            {
                "tag": "PHONE",
                "bbox": {"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0},
                "rects": [{"x0": 1.0, "y0": 2.0, "x1": 4.0, "y1": 6.0}],
            },
            {
                key: runtime_review_item[key]
                for key in ("tag", "bbox", "rects")
            },
        )
        self.assertNotIn("010-0000-0000", encoded)
        self.assertNotIn("010-0000-0000", runtime_encoded)
        self.assertNotIn("/private/context-source.pdf", encoded)
        self.assertNotIn("/private/context-source.pdf", runtime_encoded)


    def test_real_snapshot_acquisition_uses_copy_and_detects_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "source.txt"
            original.write_text("연락처 010-1234-5678", encoding="utf-8")
            observed_snapshot_paths: list[Path] = []

            def extract(snapshot_path: str, *, engine: str):
                snapshot = Path(snapshot_path)
                observed_snapshot_paths.append(snapshot)
                self.assertNotEqual(original, snapshot)
                self.assertEqual("연락처 010-1234-5678", snapshot.read_text(encoding="utf-8"))
                original.write_text("mutated", encoding="utf-8")
                return masker.ExtractResult(
                    text="연락처 010-1234-5678", engine_used=engine, duration_sec=0.0
                )

            with mock.patch.object(masker, "extract_document", side_effect=extract):
                with self.assertRaisesRegex(ValueError, "^ORIGINAL_CHANGED$"):
                    masker._extract_and_analyze_snapshot(
                        str(original), {"profile": "legal"},
                        session_hash_key=SESSION_HASH_KEY,
                    )

            self.assertEqual(1, len(observed_snapshot_paths))
            self.assertFalse(observed_snapshot_paths[0].exists())

    def test_real_snapshot_acquisition_returns_immutable_render_input_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "source.txt"
            original.write_text("stable source", encoding="utf-8")
            observed_snapshot_paths: list[Path] = []

            def extract(snapshot_path: str, *, engine: str):
                snapshot = Path(snapshot_path)
                observed_snapshot_paths.append(snapshot)
                return masker.ExtractResult(text="stable source", engine_used=engine, duration_sec=0.0)

            with mock.patch.object(masker, "extract_document", side_effect=extract):
                source_bytes, snapshot_path, _extract, manifest = masker._extract_and_analyze_snapshot(
                    str(original), {"profile": "legal"},
                    session_hash_key=SESSION_HASH_KEY,
                )
            try:
                self.assertEqual(b"stable source", source_bytes)
                self.assertIsNone(manifest)
                self.assertEqual([Path(snapshot_path)], observed_snapshot_paths)
                self.assertEqual(source_bytes, Path(snapshot_path).read_bytes())
                original.write_text("changed after analysis", encoding="utf-8")
                self.assertEqual(b"stable source", Path(snapshot_path).read_bytes())
            finally:
                Path(snapshot_path).unlink()
                masker._ACTIVE_SOURCE_SNAPSHOT.set(None)
            self.assertFalse(Path(snapshot_path).exists())

    def test_public_analysis_receives_the_immutable_source_snapshot_provenance(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            original = Path(temporary_directory) / "source.pdf"
            original_bytes = b"%PDF-immutable-source"
            original.write_bytes(original_bytes)
            observed: dict[str, object] = {}

            def extract(snapshot_path: str, *, engine: str):
                return masker.ExtractResult(text="evidence", engine_used=engine, duration_sec=0.0)

            def analyze(
                snapshot_path: str,
                opts: dict[str, object],
                *,
                session_hash_key: bytes,
                source_bytes: bytes,
                extracted: masker.ExtractResult,
            ):
                observed["snapshot_path"] = snapshot_path
                observed["session_hash_key"] = session_hash_key
                observed["source_bytes"] = source_bytes
                observed["extracted"] = extracted
                return {"original_document_hash": "trusted"}

            with mock.patch.object(masker, "extract_document", side_effect=extract), mock.patch.object(
                masker, "trusted_analysis_manifest", side_effect=analyze
            ):
                source_bytes, snapshot_path, extracted, manifest = masker._extract_and_analyze_snapshot(
                    str(original), {"profile": "mixed"},
                    session_hash_key=SESSION_HASH_KEY,
                )
            try:
                self.assertEqual(original_bytes, source_bytes)
                self.assertEqual(original_bytes, observed["source_bytes"])
                self.assertEqual(SESSION_HASH_KEY, observed["session_hash_key"])
                self.assertEqual(snapshot_path, observed["snapshot_path"])
                self.assertIs(extracted, observed["extracted"])
                self.assertEqual({"original_document_hash": "trusted"}, manifest)
            finally:
                Path(snapshot_path).unlink()
                masker._ACTIVE_SOURCE_SNAPSHOT.set(None)
    def test_real_snapshot_acquisition_failure_cleans_up(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            original = Path(temporary_directory) / "source.txt"
            original.write_text("source", encoding="utf-8")
            created_snapshots: list[Path] = []

            def fail(snapshot_path: str, *, engine: str):
                created_snapshots.append(Path(snapshot_path))
                raise RuntimeError("EXTRACT_FAILED")

            with mock.patch.object(masker, "extract_document", side_effect=fail):
                with self.assertRaisesRegex(RuntimeError, "^EXTRACT_FAILED$"):
                    masker._extract_and_analyze_snapshot(
                        str(original), {"profile": "legal"},
                        session_hash_key=SESSION_HASH_KEY,
                    )
            self.assertEqual(1, len(created_snapshots))
            self.assertFalse(created_snapshots[0].exists())
    def test_snapshot_cleanup_failure_is_sanitized(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            original = root / "source.txt"
            snapshot = root / "snapshot.txt"
            original.write_text("secret 010-1234-5678", encoding="utf-8")
            snapshot.write_bytes(original.read_bytes())
            extract = masker.ExtractResult(text="연락처 010-1234-5678", engine_used="plain-text", duration_sec=0.0)

            def acquire(*_args, **_kwargs):
                masker._ACTIVE_SOURCE_SNAPSHOT.set(str(snapshot))
                return original.read_bytes(), str(snapshot), extract, None

            hostile_error = OSError(
                "cleanup failure /private/source_010-1234-5678.txt RAW_CONTEXT_CANARY"
            )
            with mock.patch.object(masker, "_extract_and_analyze_snapshot", side_effect=acquire), mock.patch.object(
                masker.os, "unlink", side_effect=hostile_error
            ):
                with self.assertRaisesRegex(RuntimeError, "^SOURCE_SNAPSHOT_CLEANUP_FAILED$") as error:
                    masker.process_file(
                        str(original),
                        outdir=str(root),
                        opts={"pdf_redaction": False},
                        session_hash_key=SESSION_HASH_KEY,
                    )

            exposed = str(error.exception)
            self.assertNotIn("010-1234-5678", exposed)
            self.assertNotIn("/private/source", exposed)
            self.assertNotIn("RAW_CONTEXT_CANARY", exposed)
if __name__ == "__main__":
    unittest.main()
