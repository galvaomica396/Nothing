from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import fitz

import hashlib
from masking_redaction import ManualActionV1, _has_residual_text, _rect_text_hash, apply_manual_actions_v1
from scan_raster_verification import ScanManualRasterVerifier


class _RasterAdapter:
    """Independent scan evidence provider that derives each verdict from a defect."""

    def __init__(self, defect: str | object | None = None, failure_stage: str | None = None) -> None:
        self.defect = defect
        self.failure_stage = failure_stage
        self.render_calls: list[tuple[str, int, int, str, object]] = []
        self.verify_calls: list[tuple[object, object, object, object, int]] = []

    def render(self, path: str, page: int, *, dpi: int, color_profile: str) -> object:
        stage = "after" if path.endswith("scan-success.pdf") or "failed" in path else "before"
        if self.failure_stage == f"{stage}_render":
            raise RuntimeError(f"HOSTILE_EXCEPTION_CANARY {stage} raster failure /private/subject.pdf")
        image = {"path": path, "page": page, "dpi": dpi, "profile": color_profile, "stage": stage}
        self.render_calls.append((path, page, dpi, color_profile, image))
        return image

    def verify(self, before: object, after: object, rects: object, protected: object, *, boundary_px: int) -> dict[str, bool]:
        if self.failure_stage == "verify":
            raise RuntimeError("HOSTILE_EXCEPTION_CANARY verifier failure /private/subject.pdf")
        self.verify_calls.append((before, after, rects, protected, boundary_px))
        if self.defect == "true":
            return self.defect  # type: ignore[return-value]
        if not isinstance(self.defect, str) and self.defect is not None:
            return self.defect  # type: ignore[return-value]
        return {
            "coverage_100": self.defect != "coverage_100",
            "protected_ratio_ok": self.defect != "protected_ratio_ok",
            "no_connected_diff": self.defect != "no_connected_diff",
        }


class _OcrAdapter:
    def __init__(self, clear: bool) -> None:
        self.clear = clear
        self.calls: list[tuple[object, object]] = []

    def no_residual(self, image: object, rects: object) -> bool:
        self.calls.append((image, rects))
        return self.clear


class _FailingOcrAdapter:
    def no_residual(self, _image: object, _rects: object) -> bool:
        raise RuntimeError("HOSTILE_EXCEPTION_CANARY adapter unavailable /private/subject.pdf")


class ManualActionIntrinsicTests(unittest.TestCase):
    def _assert_safe_result(self, result: object) -> None:
        serialized = json.dumps(result, sort_keys=True, default=str)
        for canary in ("HOSTILE_EXCEPTION_CANARY", "/private/subject.pdf"):
            self.assertNotIn(canary, serialized)
    def _source(self, root: Path) -> tuple[Path, fitz.Rect, fitz.Rect]:
        source = root / "source.pdf"
        document = fitz.open()
        page = document.new_page()
        page.insert_text((40, 50), "TARGET-A")
        page.insert_text((40, 100), "NEIGHBOR-A")
        document.save(source)
        document.close()
        document = fitz.open(source)
        try:
            return source, document[0].search_for("TARGET-A")[0], document[0].search_for("NEIGHBOR-A")[0]
        finally:
            document.close()

    def test_residual_text_uses_glyph_center_not_adjacent_word_box_touch(self) -> None:
        class _Page:
            def get_text(self, _kind: str) -> dict[str, object]:
                return {
                    "blocks": [{
                        "lines": [{
                            "spans": [{
                                "chars": [
                                    {"c": "adjacent", "bbox": (0.0, 0.0, 4.9, 10.0)},
                                    {"c": "target", "bbox": (5.0, 0.0, 10.0, 10.0)},
                                ],
                            }],
                        }],
                    }],
                }

        target = fitz.Rect(5.0, 0.0, 10.0, 10.0)
        self.assertTrue(_has_residual_text(_Page(), (target,)))
        self.assertFalse(_has_residual_text(_Page(), (fitz.Rect(4.8, 0.0, 5.1, 10.0),)))

    def test_linked_and_free_form_actions_report_intrinsic_mode(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            source, target, neighbor = self._source(Path(directory))
            document = fitz.open(source)
            try:
                expected = _rect_text_hash(document[0], [target])
            finally:
                document.close()

            document_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            linked = ManualActionV1(
                manual_action_id="manual-linked",
                run_id="run",
                document_sha256=document_sha256,
                analysis_revision=1,
                page_index=0,
                rect_list=((target.x0, target.y0, target.x1, target.y1),),
                mode="mask",
                source_kind="text_pdf",
                linked_occurrence_id="occ",
                expected_text_hash=expected,
                protected_neighbor_refs=((neighbor.x0, neighbor.y0, neighbor.x1, neighbor.y1),),
            )
            output = Path(directory) / "output.pdf"
            result = apply_manual_actions_v1(
                str(source), str(output), [linked], expected_run_id="run",
                expected_document_sha256=document_sha256, expected_analysis_revision=1,
            )
            self.assertEqual("applied", result["status"])
            self.assertIs(result["verification"]["verified"], True)
            self.assertEqual("linked_occurrence", result["text_check"])
            document = fitz.open(output)
            try:
                self.assertEqual("", document[0].get_textbox(target).strip())
                self.assertIn("NEIGHBOR-A", document[0].get_textbox(neighbor))
            finally:
                document.close()

            free_output = Path(directory) / "free.pdf"
            free = ManualActionV1(
                manual_action_id="manual-free",
                run_id="run",
                document_sha256=document_sha256,
                analysis_revision=1,
                page_index=0,
                rect_list=((target.x0, target.y0, target.x1, target.y1),),
                mode="mask",
                source_kind="text_pdf",
            )
            result = apply_manual_actions_v1(
                str(source), str(free_output), [free], expected_run_id="run",
                expected_document_sha256=document_sha256, expected_analysis_revision=1,
            )
            self.assertEqual(("applied", "not_applicable"), (result["status"], result["text_check"]))
            self.assertIs(result["verification"]["verified"], True)
            self.assertTrue(free_output.exists())
            document = fitz.open(free_output)
            try:
                self.assertEqual("", document[0].get_textbox(target).strip())
                self.assertIn("NEIGHBOR-A", document[0].get_textbox(neighbor))
            finally:
                document.close()
            stale = ManualActionV1(
                manual_action_id="manual-stale",
                run_id="run",
                document_sha256=document_sha256,
                analysis_revision=1,
                page_index=0,
                rect_list=((target.x0, target.y0, target.x1, target.y1),),
                mode="mask",
                source_kind="text_pdf",
                linked_occurrence_id="occ",
                expected_text_hash="0" * 64,
                protected_neighbor_refs=((neighbor.x0, neighbor.y0, neighbor.x1, neighbor.y1),),
            )
            stale_output = Path(directory) / "stale.pdf"
            result = apply_manual_actions_v1(
                str(source), str(stale_output), [stale], expected_run_id="run",
                expected_document_sha256=document_sha256, expected_analysis_revision=1,
            )
            self.assertEqual("blocked", result["status"])
            self.assertEqual(["linked_occurrence_evidence_missing"], [item["reason_code"] for item in result["review_items"] if item["manual_action_id"] == stale.manual_action_id])
            self.assertIsNone(result["output_file"])
            self.assertFalse(stale_output.exists())
            document = fitz.open(source)
            try:
                self.assertIn("NEIGHBOR-A", document[0].get_textbox(neighbor))
            finally:
                document.close()

    def test_scan_restore_recovers_original_region_and_passes_raster_verification(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            original, target, _neighbor = self._source(root)
            original_sha256 = hashlib.sha256(original.read_bytes()).hexdigest()
            mask = ManualActionV1(
                manual_action_id="manual-mask",
                run_id="run",
                document_sha256=original_sha256,
                analysis_revision=1,
                page_index=0,
                rect_list=((target.x0, target.y0, target.x1, target.y1),),
                mode="mask",
                source_kind="text_pdf",
            )
            masked = root / "masked.pdf"
            masked_result = apply_manual_actions_v1(
                str(original), str(masked), [mask], expected_run_id="run",
                expected_document_sha256=original_sha256, expected_analysis_revision=1,
            )
            self.assertEqual("applied", masked_result["status"])

            masked_sha256 = hashlib.sha256(masked.read_bytes()).hexdigest()
            restore = ManualActionV1(
                manual_action_id="manual-restore",
                run_id="run",
                document_sha256=masked_sha256,
                analysis_revision=1,
                page_index=0,
                rect_list=((target.x0, target.y0, target.x1, target.y1),),
                mode="restore",
                source_kind="scan",
            )
            restored = root / "restored.pdf"
            verifier = ScanManualRasterVerifier({
                0: ((target.x0, target.y0, target.x1, target.y1),),
            })
            result = apply_manual_actions_v1(
                str(masked), str(restored), [restore], expected_run_id="run",
                expected_document_sha256=masked_sha256, expected_analysis_revision=1,
                raster_adapter=verifier, ocr_adapter=verifier,
                restore_source_pdf_path=str(original),
            )

            self.assertEqual("applied", result["status"])
            self.assertIs(result["verification"]["verified"], True)
            self.assertEqual(
                {
                    "coverage_100": True,
                    "protected_ratio_ok": True,
                    "no_connected_diff": True,
                    "no_residual": True,
                },
                verifier.summary(),
            )
            self.assertTrue(restored.is_file())

    def test_text_backed_scan_mask_uses_text_boundary_verification(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source, target, _neighbor = self._source(root)
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            action = ManualActionV1(
                manual_action_id="text-backed-scan-mask",
                run_id="run",
                document_sha256=source_hash,
                analysis_revision=1,
                page_index=0,
                rect_list=((target.x0, target.y0, target.x1, target.y1),),
                mode="mask",
                source_kind="scan",
            )
            verifier = ScanManualRasterVerifier({
                0: ((target.x0, target.y0, target.x1, target.y1),),
            })
            output = root / "text-backed-scan.pdf"
            result = apply_manual_actions_v1(
                str(source),
                str(output),
                [action],
                expected_run_id="run",
                expected_document_sha256=source_hash,
                expected_analysis_revision=1,
                raster_adapter=verifier,
                ocr_adapter=verifier,
            )
            self.assertEqual("applied", result["status"])
            self.assertIs(result["verification"]["verified"], True)
            self.assertEqual(
                {
                    "coverage_100": True,
                    "protected_ratio_ok": True,
                    "no_connected_diff": True,
                    "no_residual": True,
                },
                verifier.summary(),
            )

    def test_text_restore_reinserts_text_layer_with_authorized_occurrence_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            original, target, _neighbor = self._source(root)
            original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            document = fitz.open(original)
            try:
                expected_text_hash = _rect_text_hash(document[0], [target])
            finally:
                document.close()
            self.assertIsNotNone(expected_text_hash)

            masked = root / "text-masked.pdf"
            mask = ManualActionV1(
                manual_action_id="text-mask",
                run_id="run",
                document_sha256=original_hash,
                analysis_revision=1,
                page_index=0,
                rect_list=((target.x0, target.y0, target.x1, target.y1),),
                mode="mask",
                source_kind="text_pdf",
            )
            masked_result = apply_manual_actions_v1(
                str(original),
                str(masked),
                [mask],
                expected_run_id="run",
                expected_document_sha256=original_hash,
                expected_analysis_revision=1,
            )
            self.assertEqual("applied", masked_result["status"])

            masked_hash = hashlib.sha256(masked.read_bytes()).hexdigest()
            restored = root / "text-restored.pdf"
            restore = ManualActionV1(
                manual_action_id="text-restore",
                run_id="run",
                document_sha256=masked_hash,
                analysis_revision=1,
                page_index=0,
                rect_list=((target.x0, target.y0, target.x1, target.y1),),
                mode="restore",
                source_kind="text_pdf",
                linked_occurrence_id="occ_aaaaaaaaaaaaaaaaaaaaaaaa",
                expected_text_hash=expected_text_hash,
                restore_authorization_hash="a" * 64,
            )
            restored_result = apply_manual_actions_v1(
                str(masked),
                str(restored),
                [restore],
                expected_run_id="run",
                expected_document_sha256=masked_hash,
                expected_analysis_revision=1,
                restore_source_pdf_path=str(original),
            )
            self.assertEqual("applied", restored_result["status"])
            self.assertEqual(1, restored_result["restore_actions_applied"])
            self.assertEqual("linked_occurrence", restored_result["text_check"])
            document = fitz.open(restored)
            try:
                self.assertEqual("TARGET-A", document[0].get_textbox(target).strip())
            finally:
                document.close()

    def test_text_restore_skips_overlay_when_all_multi_rect_text_is_already_visible(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            original, target, neighbor = self._source(root)
            original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            document = fitz.open(original)
            try:
                expected_text_hash = _rect_text_hash(document[0], [target, neighbor])
            finally:
                document.close()
            self.assertIsNotNone(expected_text_hash)

            restored = root / "multi-rect-restored.pdf"
            restore = ManualActionV1(
                manual_action_id="multi-rect-restore",
                run_id="run",
                document_sha256=original_hash,
                analysis_revision=1,
                page_index=0,
                rect_list=(
                    (target.x0, target.y0, target.x1, target.y1),
                    (neighbor.x0, neighbor.y0, neighbor.x1, neighbor.y1),
                ),
                mode="restore",
                source_kind="text_pdf",
                linked_occurrence_id="occ_aaaaaaaaaaaaaaaaaaaaaaaa",
                expected_text_hash=expected_text_hash,
                restore_authorization_hash="a" * 64,
            )
            result = apply_manual_actions_v1(
                str(original),
                str(restored),
                [restore],
                expected_run_id="run",
                expected_document_sha256=original_hash,
                expected_analysis_revision=1,
                restore_source_pdf_path=str(original),
            )

            self.assertEqual("applied", result["status"])
            document = fitz.open(restored)
            try:
                self.assertEqual("TARGET-A", document[0].get_textbox(target).strip())
                self.assertEqual("NEIGHBOR-A", document[0].get_textbox(neighbor).strip())
            finally:
                document.close()

    def test_scan_intrinsic_contracts_cover_success_residual_and_adapter_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = Path(directory)
            source, target, neighbor = self._source(root)
            document_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            action = ManualActionV1(
                manual_action_id="manual-scan",
                run_id="run",
                document_sha256=document_sha256,
                analysis_revision=1,
                page_index=0,
                rect_list=((target.x0, target.y0, target.x1, target.y1),),
                mode="mask",
                source_kind="scan",
                protected_neighbor_refs=((neighbor.x0, neighbor.y0, neighbor.x1, neighbor.y1),),
            )
            raster = _RasterAdapter()
            ocr = _OcrAdapter(True)
            output = root / "scan-success.pdf"
            result = apply_manual_actions_v1(
                str(source), str(output), [action], expected_run_id="run",
                expected_document_sha256=document_sha256, expected_analysis_revision=1,
                raster_adapter=raster, ocr_adapter=ocr,
            )
            self.assertEqual("applied", result["status"])
            self.assertIs(result["verification"]["verified"], True)
            self.assertEqual(
                [(str(source), 0, 300, "sRGB"), (str(output), 0, 300, "sRGB")],
                [call[:4] for call in raster.render_calls],
            )
            before, after, target_rects, protected_rects, boundary_px = raster.verify_calls[0]
            self.assertIs(before, raster.render_calls[0][4])
            self.assertIs(after, raster.render_calls[1][4])
            self.assertEqual((target.x0, target.y0, target.x1, target.y1), tuple(target_rects[0]))
            self.assertEqual((neighbor.x0, neighbor.y0, neighbor.x1, neighbor.y1), tuple(protected_rects[0]))
            self.assertEqual(2, boundary_px)
            self.assertEqual([(after, target_rects)], ocr.calls)
            document = fitz.open(output)
            try:
                self.assertEqual("", document[0].get_textbox(target).strip())
                self.assertIn("NEIGHBOR-A", document[0].get_textbox(neighbor))
            finally:
                document.close()

            failures = (
                ("coverage_100", "scan_target_coverage_incomplete"),
                ("protected_ratio_ok", "scan_protected_roi_changed"),
                ("no_connected_diff", "scan_connected_diff_into_protected_glyph"),
            )
            for index, (defect, reason_code) in enumerate(failures):
                failed = root / f"scan-failed-{index}.pdf"
                result = apply_manual_actions_v1(str(source), str(failed), [action], expected_run_id="run", expected_document_sha256=document_sha256, expected_analysis_revision=1, raster_adapter=_RasterAdapter(defect), ocr_adapter=_OcrAdapter(True))
                self.assertEqual("failed", result["status"])
                self.assertEqual([reason_code], [item["reason_code"] for item in result["review_items"]])
                self.assertIsNone(result["output_file"])
                self.assertFalse(failed.exists())

            required_verdicts = (
                ("coverage_100", "scan_target_coverage_incomplete"),
                ("protected_ratio_ok", "scan_protected_roi_changed"),
                ("no_connected_diff", "scan_connected_diff_into_protected_glyph"),
            )
            for index, (field, reason_code) in enumerate(required_verdicts):
                for malformed in (None, "true"):
                    with self.subTest(field=field, malformed=malformed):
                        verdict = {"coverage_100": True, "protected_ratio_ok": True, "no_connected_diff": True}
                        expected_reason = reason_code
                        if malformed is None:
                            del verdict[field]
                        else:
                            verdict = malformed  # type: ignore[assignment]
                            expected_reason = "scan_raster_verification_malformed"
                        failed = root / f"scan-intrinsic-{index}-{malformed is not None}.pdf"
                        result = apply_manual_actions_v1(
                            str(source), str(failed), [action], expected_run_id="run",
                            expected_document_sha256=document_sha256, expected_analysis_revision=1,
                            raster_adapter=_RasterAdapter(verdict), ocr_adapter=_OcrAdapter(True),
                        )
                        self.assertEqual("failed", result["status"])
                        self.assertEqual([expected_reason], [item["reason_code"] for item in result["review_items"]])
                        self.assertIsNone(result["output_file"])
                        self.assertFalse(failed.exists())
            for index, field in enumerate(required_verdicts):
                for malformed in (1, "true", False):
                    with self.subTest(field=field[0], malformed=malformed):
                        verdict = {"coverage_100": True, "protected_ratio_ok": True, "no_connected_diff": True}
                        verdict[field[0]] = malformed
                        failed = root / f"scan-nonboolean-{index}-{type(malformed).__name__}.pdf"
                        result = apply_manual_actions_v1(
                            str(source), str(failed), [action], expected_run_id="run",
                            expected_document_sha256=document_sha256, expected_analysis_revision=1,
                            raster_adapter=_RasterAdapter(verdict), ocr_adapter=_OcrAdapter(True),
                        )
                        self.assertEqual("failed", result["status"])
                        self.assertIsNone(result["output_file"])
                        self.assertFalse(failed.exists())
            for index, (ocr, reason_code) in enumerate((
                (_OcrAdapter(False), "scan_target_ocr_residual"),
                (_FailingOcrAdapter(), "scan_ocr_verification_failed"),
            )):
                failed = root / f"scan-ocr-{index}.pdf"
                result = apply_manual_actions_v1(str(source), str(failed), [action], expected_run_id="run", expected_document_sha256=document_sha256, expected_analysis_revision=1, raster_adapter=_RasterAdapter({"coverage_100": True, "protected_ratio_ok": True, "no_connected_diff": True}), ocr_adapter=ocr)
                self.assertEqual("failed", result["status"])
                self.assertEqual([reason_code], [item["reason_code"] for item in result["review_items"]])
                self._assert_safe_result(result)
                self.assertIsNone(result["output_file"])
                self.assertFalse(failed.exists())
            for malformed in (1, "true", False, None):
                with self.subTest(ocr_evidence=malformed):
                    failed = root / f"scan-ocr-nonboolean-{type(malformed).__name__}.pdf"
                    result = apply_manual_actions_v1(
                        str(source), str(failed), [action], expected_run_id="run",
                        expected_document_sha256=document_sha256, expected_analysis_revision=1,
                        raster_adapter=_RasterAdapter({"coverage_100": True, "protected_ratio_ok": True, "no_connected_diff": True}),
                        ocr_adapter=_OcrAdapter(malformed),  # type: ignore[arg-type]
                    )
                    self.assertEqual("failed", result["status"])
                    self.assertIsNone(result["output_file"])
                    self.assertFalse(failed.exists())
            for field, value in (
                ("run_id", "other-run"),
                ("document_sha256", "0" * 64),
                ("analysis_revision", 2),
            ):
                with self.subTest(authority_field=field):
                    rejected = ManualActionV1(**{**action.__dict__, field: value})
                    raster = _RasterAdapter({"coverage_100": True, "protected_ratio_ok": True, "no_connected_diff": True})
                    ocr = _OcrAdapter(True)
                    failed = root / f"scan-stale-{field}.pdf"
                    result = apply_manual_actions_v1(
                        str(source), str(failed), [rejected], expected_run_id="run",
                        expected_document_sha256=document_sha256, expected_analysis_revision=1,
                        raster_adapter=raster, ocr_adapter=ocr,
                    )
                    self.assertEqual("blocked", result["status"])
                    self.assertEqual("stale_manual_action_identity", result["review_items"][0]["reason_code"])
                    self.assertEqual([], raster.render_calls)
                    self.assertEqual([], ocr.calls)
                    self.assertIsNone(result["output_file"])
                    self.assertFalse(failed.exists())

            for stage, reason_code in (
                ("before_render", "scan_before_render_failed"),
                ("after_render", "scan_after_render_failed"),
                ("verify", "scan_raster_verification_failed"),
            ):
                failed = root / f"scan-{stage}-failed.pdf"
                result = apply_manual_actions_v1(
                    str(source),
                    str(failed),
                    [action],
                    expected_run_id="run",
                    expected_document_sha256=document_sha256,
                    expected_analysis_revision=1,
                    raster_adapter=_RasterAdapter(
                        {"coverage_100": True, "protected_ratio_ok": True, "no_connected_diff": True},
                        stage,
                    ),
                    ocr_adapter=_OcrAdapter(True),
                )
                self.assertEqual("blocked" if stage == "before_render" else "failed", result["status"])
                self.assertEqual([reason_code], [item["reason_code"] for item in result["review_items"] if item["manual_action_id"] == action.manual_action_id])
                self._assert_safe_result(result)
                self.assertIsNone(result["output_file"])
                self.assertFalse(failed.exists())

            missing_path = root / "missing.pdf"
            missing = apply_manual_actions_v1(str(source), str(missing_path), [action], expected_run_id="run", expected_document_sha256=document_sha256, expected_analysis_revision=1)
            self.assertEqual("blocked", missing["status"])
            self.assertEqual(["scan_verification_adapter_unavailable"], [item["reason_code"] for item in missing["review_items"] if item["manual_action_id"] == action.manual_action_id])
            self.assertIsNone(missing["output_file"])
            self.assertFalse(missing_path.exists())


if __name__ == "__main__":
    unittest.main()
