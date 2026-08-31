from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import fitz

from document_masker_ocr_gui import _threshold_artifact, trusted_finalize_manifest
from masking_redaction import OccurrenceRedactionInput, _rect_text_hash, redact_pdf_native


class OccurrenceScopedRedactionTests(unittest.TestCase):
    def test_exact_word_rectangle_ignores_adjacent_line_box_overlap(self) -> None:
        class OverlappingLinePage:
            def get_text(self, kind: str):
                self_outer.assertEqual("words", kind)
                return (
                    (10.0, 10.0, 60.0, 24.0, "target"),
                    (10.0, 23.0, 65.0, 37.0, "neighbor"),
                )

        self_outer = self
        expected = hashlib.sha256(b"target").hexdigest()

        self.assertEqual(
            expected,
            _rect_text_hash(OverlappingLinePage(), [fitz.Rect(10.0, 10.0, 60.0, 24.0)]),
        )

    def test_float_rounding_at_word_edge_keeps_subword_hash_stable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((40, 50), "ROUNDING-TARGET")
            document.save(source)
            document.close()

            document = fitz.open(source)
            try:
                word = document[0].get_text("words")[0]
                rect = fitz.Rect(
                    word[0] + 5.0,
                    word[1] + 0.01,
                    word[2] + 1e-12,
                    word[3] - 0.01,
                )
                expected = hashlib.sha256(
                    document[0].get_textbox(rect).strip().encode("utf-8")
                ).hexdigest()
                self.assertEqual(expected, _rect_text_hash(document[0], [rect]))
            finally:
                document.close()

    def test_rectangle_order_matches_native_manifest_canonicalization(self) -> None:
        class MultiRectanglePage:
            def get_text(self, kind: str):
                if kind != "words":
                    raise AssertionError(kind)
                return (
                    (10.0, 10.0, 30.0, 20.0, "first"),
                    (40.0, 10.0, 60.0, 20.0, "second"),
                )

            def get_textbox(self, _rect):
                return ""

        first = fitz.Rect(10.0, 10.0, 30.0, 20.0)
        second = fitz.Rect(40.0, 10.0, 60.0, 20.0)
        expected = hashlib.sha256(b"first\nsecond").hexdigest()
        page = MultiRectanglePage()

        self.assertEqual(expected, _rect_text_hash(page, [second, first]))
        self.assertEqual(expected, _rect_text_hash(page, [first, second]))

    def _request(self, source: Path, page_index: int, rects: list[fitz.Rect], action: str = "mask") -> OccurrenceRedactionInput:
        document = fitz.open(source)
        try:
            expected_hash = _rect_text_hash(document[page_index], rects)
        finally:
            document.close()
        self.assertIsNotNone(expected_hash)
        document_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        occurrence_material = f"synthetic-run:{document_sha256}:7:{page_index}:{action}"
        occurrence_id = f"occ_{hashlib.sha256(occurrence_material.encode('utf-8')).hexdigest()[:24]}"
        return OccurrenceRedactionInput(
            occurrence_id=occurrence_id,
            run_id="synthetic-run",
            document_sha256=document_sha256,
            analysis_revision=7,
            page_index=page_index,
            rect_list=tuple((rect.x0, rect.y0, rect.x1, rect.y1) for rect in rects),
            action=action,
            provenance="synthetic-test",
            expected_text_hash=expected_hash or "",
        )

    def test_only_selected_repeated_occurrence_is_masked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory) / "source.pdf", Path(directory) / "output.pdf"
            document = fitz.open()
            first = document.new_page()
            first.insert_text((40, 50), "TOKEN-X")
            first.insert_text((40, 100), "TOKEN-X")
            second = document.new_page()
            second.insert_text((40, 50), "TOKEN-X")
            document.save(source)
            document.close()

            source_document = fitz.open(source)
            try:
                selected = source_document[0].search_for("TOKEN-X")[0]
            finally:
                source_document.close()
            result = redact_pdf_native(
                str(source),
                str(output),
                occurrence_inputs=[self._request(source, 0, [selected])],
                expected_run_id="synthetic-run",
                expected_document_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                expected_analysis_revision=7,
            )

            self.assertEqual("applied", result["status"])
            self.assertTrue(result["verification"]["verified"])
            result_document = fitz.open(output)
            try:
                self.assertEqual("", result_document[0].get_textbox(selected).strip())
                self.assertEqual(1, len(result_document[0].search_for("TOKEN-X")))
                self.assertEqual(1, len(result_document[1].search_for("TOKEN-X")))
            finally:
                result_document.close()

    def test_exclude_and_review_do_not_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((40, 50), "TOKEN-Y")
            document.save(source)
            document.close()
            source_document = fitz.open(source)
            try:
                rect = source_document[0].search_for("TOKEN-Y")[0]
            finally:
                source_document.close()

            excluded_path, reviewed_path = Path(directory) / "excluded.pdf", Path(directory) / "reviewed.pdf"
            excluded = redact_pdf_native(
                str(source), str(excluded_path),
                occurrence_inputs=[self._request(source, 0, [rect], "exclude")],
                expected_run_id="synthetic-run",
                expected_document_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                expected_analysis_revision=7,
            )
            reviewed = redact_pdf_native(
                str(source), str(reviewed_path),
                occurrence_inputs=[self._request(source, 0, [rect], "review")],
                expected_run_id="synthetic-run",
                expected_document_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                expected_analysis_revision=7,
            )
            self.assertEqual(0, excluded["annotations_added"])
            self.assertEqual("applied", excluded["status"])
            self.assertTrue(excluded_path.exists())
            self.assertEqual(0, excluded["occurrences_applied"])
            excluded_document = fitz.open(excluded_path)
            source_document = fitz.open(source)
            try:
                self.assertEqual(source_document.page_count, excluded_document.page_count)
                self.assertEqual(source_document[0].rect, excluded_document[0].rect)
                self.assertIn("TOKEN-Y", excluded_document[0].get_textbox(rect))
            finally:
                excluded_document.close()
                source_document.close()
            self.assertEqual("applied", reviewed["status"])
            self.assertEqual(0, reviewed["annotations_added"])
            self.assertTrue(reviewed_path.exists())
            self.assertEqual(0, reviewed["occurrences_applied"])
            self.assertEqual("review_action_unresolved", reviewed["review_items"][0]["reason_code"])
            reviewed_document = fitz.open(reviewed_path)
            try:
                self.assertIn("TOKEN-Y", reviewed_document[0].get_textbox(rect))
            finally:
                reviewed_document.close()

    def test_missing_geometry_and_neighbor_overlap_block(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((40, 50), "TOKEN-Z")
            document.save(source)
            document.close()
            missing_path = Path(directory) / "missing.pdf"
            base = OccurrenceRedactionInput(
                occurrence_id="occ_000000000000000000000001",
                run_id="synthetic-run",
                document_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                analysis_revision=7,
                page_index=0,
                rect_list=(),
                action="mask",
                provenance="synthetic-test",
                expected_text_hash="0" * 64,
            )
            self.assertFalse(missing_path.exists())
            missing = redact_pdf_native(
                str(source), str(missing_path), occurrence_inputs=[base],
                expected_run_id="synthetic-run", expected_document_sha256=base.document_sha256,
                expected_analysis_revision=7,
            )
            self.assertEqual("blocked", missing["status"])
            self.assertIsNone(missing["output_file"])
            self.assertFalse(missing_path.exists())
            self.assertEqual("missing_grounded_rectangles", missing["review_items"][0]["reason_code"])

            source_document = fitz.open(source)
            try:
                rect = source_document[0].search_for("TOKEN-Z")[0]
            finally:
                source_document.close()
            overlap = self._request(source, 0, [rect])
            overlap = OccurrenceRedactionInput(
                **{**overlap.__dict__, "provenance": {"protected_neighbor_rects": [(rect.x0, rect.y0, rect.x1, rect.y1)]}}
            )
            result = redact_pdf_native(
                str(source), str(Path(directory) / "overlap.pdf"), occurrence_inputs=[overlap],
                expected_run_id="synthetic-run", expected_document_sha256=overlap.document_sha256,
                expected_analysis_revision=7,
            )
            self.assertEqual("blocked", result["status"])
            self.assertIsNone(result["output_file"])
            self.assertFalse((Path(directory) / "overlap.pdf").exists())
            self.assertEqual("protected_neighbor_overlap", result["review_items"][0]["reason_code"])

    def test_stale_identity_revision_and_source_hash_fail_closed_without_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            document = fitz.open()
            page = document.new_page()
            page.insert_text((40, 50), "TOKEN-STALE")
            document.save(source)
            document.close()
            document = fitz.open(source)
            try:
                rect = document[0].search_for("TOKEN-STALE")[0]
            finally:
                document.close()
            request = self._request(source, 0, [rect])
            failures = (
                (OccurrenceRedactionInput(**{**request.__dict__, "analysis_revision": 0}), "stale_analysis_revision"),
                (OccurrenceRedactionInput(**{**request.__dict__, "occurrence_id": ""}), "invalid_occurrence_id"),
                (OccurrenceRedactionInput(**{**request.__dict__, "expected_text_hash": "0" * 64}), "expected_text_hash_mismatch"),
                (OccurrenceRedactionInput(**{**request.__dict__, "run_id": ""}), "stale_occurrence_identity"),
                (OccurrenceRedactionInput(**{**request.__dict__, "run_id": "other-run"}), "stale_occurrence_identity"),
                (OccurrenceRedactionInput(**{**request.__dict__, "document_sha256": ""}), "stale_occurrence_identity"),
                (OccurrenceRedactionInput(**{**request.__dict__, "document_sha256": "0" * 64}), "stale_occurrence_identity"),
            )
            for index, (stale, reason_code) in enumerate(failures):
                output = root / f"stale-{index}.pdf"
                self.assertFalse(output.exists())
                result = redact_pdf_native(
                    str(source), str(output), occurrence_inputs=[stale], expected_run_id="synthetic-run",
                    expected_document_sha256=request.document_sha256, expected_analysis_revision=7,
                )
                self.assertEqual("blocked", result["status"])
                self.assertEqual([reason_code], [item["reason_code"] for item in result["review_items"]])
                self.assertIsNone(result["output_file"])
                self.assertFalse(output.exists())
            changed = fitz.open()
            page = changed.new_page()
            page.insert_text((40, 50), "SOURCE-CHANGED")
            source.unlink()
            changed.save(source)
            changed.close()
            output = root / "stale-source.pdf"
            result = redact_pdf_native(
                str(source), str(output), occurrence_inputs=[request], expected_run_id="synthetic-run",
                expected_document_sha256=request.document_sha256, expected_analysis_revision=7,
            )
            self.assertEqual("blocked", result["status"])
            self.assertEqual(
                ["trusted_document_run_identity_required", "expected_text_hash_mismatch"],
                [item["reason_code"] for item in result["review_items"]],
            )
            self.assertIsNone(result["output_file"])
            self.assertFalse(output.exists())
    def test_trusted_manifest_handoff_rejects_invalid_page_geometry_and_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.pdf"
            document = fitz.open()
            first = document.new_page()
            first.insert_text((40, 50), "TOKEN-IDENTITY")
            first.insert_text((40, 100), "TOKEN-IDENTITY")
            second = document.new_page()
            second.insert_text((40, 50), "TOKEN-IDENTITY")
            document.save(source)
            document.close()
            document = fitz.open(source)
            try:
                first_rect, second_rect = document[0].search_for("TOKEN-IDENTITY")
                other_page_rect = document[1].search_for("TOKEN-IDENTITY")[0]
            finally:
                document.close()
            request = self._request(source, 0, [first_rect])
            manifest = {
                "runId": request.run_id,
                "originalDocumentHash": request.document_sha256,
                "analysisRevision": request.analysis_revision,
                "profile": "mixed",
                "policyVersion": "masking-policy-v1",
                "optionsHash": "c" * 64,
                "thresholdVersion": _threshold_artifact(0.85, 0.5)["version"],
                "thresholdHash": _threshold_artifact(0.85, 0.5)["content_hash"],
                "thresholdArtifact": {
                    "version": _threshold_artifact(0.85, 0.5)["version"],
                    "contentHash": _threshold_artifact(0.85, 0.5)["content_hash"],
                    "autoMaskThreshold": _threshold_artifact(0.85, 0.5)["auto_mask_threshold"],
                    "reviewThreshold": _threshold_artifact(0.85, 0.5)["review_threshold"],
                },
                "coordinateSpace": "pdf_points_top_left",
                "segments": [{"analysisRevision": 7, "pageStart": 0, "pageEnd": 1}],
                "manualActions": [],
                "reviewItems": [],
            }
            options = {
                "run_id": request.run_id,
                "analysis_revision": request.analysis_revision,
                "profile": "mixed",
                "options_hash": "c" * 64,
                "threshold_version": _threshold_artifact(0.85, 0.5)["version"],
                "threshold_hash": _threshold_artifact(0.85, 0.5)["content_hash"],
                "threshold_artifact": _threshold_artifact(0.85, 0.5),
                "auto_mask_threshold": 0.85,
                "review_threshold": 0.5,
                "warnings_confirmed": False,
            }
            for name, page_index, rect, action in (
                ("page", -1, other_page_rect, "mask"),
                ("geometry", 0, fitz.Rect(second_rect.x0, second_rect.y0, second_rect.x0, second_rect.y1), "mask"),
                ("action", 0, first_rect, "restore"),
            ):
                with self.subTest(mutation=name):
                    output = root / f"mutated-{name}.pdf"
                    manifest["occurrences"] = [{
                        "occurrenceId": request.occurrence_id,
                        "analysisRevision": request.analysis_revision,
                        "page": page_index,
                        "rects": [{"x0": rect.x0, "y0": rect.y0, "x1": rect.x1, "y1": rect.y1}],
                        "proposedAction": action,
                        "provenance": request.provenance,
                        "expectedTextHash": request.expected_text_hash,
                    }]
                    with self.assertRaisesRegex(ValueError, "^TRUSTED_FINALIZE_BLOCKED$"):
                        trusted_finalize_manifest(str(source), manifest, options, str(output))
                    self.assertFalse(output.exists())

    def test_geometry_validation_table_rejects_hostile_inputs_and_preserves_valid_neighbors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "geometry-source.pdf"
            document = fitz.open()
            page = document.new_page(width=300, height=180)
            page.insert_text((40, 50), "TOKEN-GEOMETRY SAFE-NEIGHBOR")
            page.insert_text((40, 90), "MULTI-LEFT MULTI-RIGHT")
            document.save(source)
            document.close()

            document = fitz.open(source)
            try:
                target = document[0].search_for("TOKEN-GEOMETRY")[0]
                neighbor = document[0].search_for("SAFE-NEIGHBOR")[0]
                multi_rects = document[0].search_for("MULTI-LEFT") + document[0].search_for("MULTI-RIGHT")
                page_count = document.page_count
            finally:
                document.close()
            base = self._request(source, 0, [target])
            hostile_cases = (
                ("nan", ((float("nan"), target.y0, target.x1, target.y1),), 0, base.provenance, "invalid_pdf_points_top_left_rect"),
                ("infinity", ((target.x0, target.y0, float("inf"), target.y1),), 0, base.provenance, "invalid_pdf_points_top_left_rect"),
                ("inverted", ((target.x1, target.y0, target.x0, target.y1),), 0, base.provenance, "invalid_pdf_points_top_left_rect"),
                ("out-of-page", ((target.x0, target.y0, 301.0, target.y1),), 0, base.provenance, "invalid_pdf_points_top_left_rect"),
                ("page-count", base.rect_list, page_count, base.provenance, "invalid_page_index"),
                (
                    "protected-overlap",
                    base.rect_list,
                    0,
                    {"protected_neighbor_rects": [tuple(base.rect_list[0])]},
                    "protected_neighbor_overlap",
                ),
            )
            for name, rect_list, page_index, provenance, reason_code in hostile_cases:
                with self.subTest(geometry=name):
                    output = root / f"hostile-{name}.pdf"
                    request = OccurrenceRedactionInput(
                        **{**base.__dict__, "page_index": page_index, "rect_list": rect_list, "provenance": provenance}
                    )
                    result = redact_pdf_native(
                        str(source),
                        str(output),
                        occurrence_inputs=[request],
                        expected_run_id=base.run_id,
                        expected_document_sha256=base.document_sha256,
                        expected_analysis_revision=base.analysis_revision,
                    )
                    self.assertEqual("blocked", result["status"])
                    self.assertEqual(reason_code, result["review_items"][0]["reason_code"])
                    self.assertIsNone(result["output_file"])
                    self.assertFalse(output.exists())

            valid_cases = (
                ("multi-rect", self._request(source, 0, multi_rects), None),
                (
                    "adjacent-protected-neighbor",
                    OccurrenceRedactionInput(
                        **{
                            **base.__dict__,
                            "provenance": {"protected_neighbor_rects": [tuple(neighbor)]},
                        }
                    ),
                    neighbor,
                ),
            )
            for name, request, protected_rect in valid_cases:
                with self.subTest(geometry=name):
                    output = root / f"valid-{name}.pdf"
                    result = redact_pdf_native(
                        str(source),
                        str(output),
                        occurrence_inputs=[request],
                        expected_run_id=request.run_id,
                        expected_document_sha256=request.document_sha256,
                        expected_analysis_revision=request.analysis_revision,
                    )
                    self.assertEqual("applied", result["status"])
                    self.assertTrue(result["verification"]["verified"])
                    self.assertTrue(output.exists())
                    output_document = fitz.open(output)
                    try:
                        if name == "multi-rect":
                            self.assertEqual(0, len(output_document[0].search_for("MULTI-LEFT")))
                            self.assertEqual(0, len(output_document[0].search_for("MULTI-RIGHT")))
                        else:
                            self.assertIn("SAFE-NEIGHBOR", output_document[0].get_textbox(protected_rect))
                    finally:
                        output_document.close()
if __name__ == "__main__":
    unittest.main()
