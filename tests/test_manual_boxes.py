from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import NotRequired, TypedDict
from unittest import mock

import fitz

import document_masker_ocr_gui as masker
import masking_redaction


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "apply_manual_boxes.py"
ENGINE_ENTRY_PATH = REPO_ROOT / "scripts" / "masking_engine_entry.py"


class BoxPayload(TypedDict):
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    mode: str
    tag: NotRequired[str]


def write_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=240, height=180)
    page.insert_text((32, 52), "name Hong Gil Dong")
    page.insert_text((32, 92), "phone 010-0000-0000")
    page.draw_rect(fitz.Rect(24, 24, 216, 132), color=(0, 0, 0), width=1)
    doc.save(path)
    doc.close()


def write_two_page_pdf(path: Path) -> None:
    doc = fitz.open()
    for page_num in range(2):
        page = doc.new_page(width=240, height=180)
        page.insert_text((32, 52), f"page {page_num + 1} name Hong Gil Dong")
        page.insert_text((32, 92), f"page {page_num + 1} phone 010-0000-000{page_num}")
        page.draw_rect(fitz.Rect(24, 24, 216, 132), color=(0, 0, 0), width=1)
    doc.save(path)
    doc.close()


def run_manual_boxes(
    input_pdf: Path,
    original_pdf: Path,
    output_dir: Path,
    boxes: list[BoxPayload],
    display_mode: str = "black",
) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--input",
            str(input_pdf),
            "--original",
            str(original_pdf),
            "--outdir",
            str(output_dir),
            "--boxes",
            json.dumps(boxes),
            "--display-mode",
            display_mode,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def run_engine_manual_boxes(
    input_pdf: Path,
    original_pdf: Path,
    output_dir: Path,
    boxes: list[BoxPayload],
    display_mode: str = "black",
) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            str(ENGINE_ENTRY_PATH),
            "--manual-boxes",
            "--input",
            str(input_pdf),
            "--original",
            str(original_pdf),
            "--outdir",
            str(output_dir),
            "--boxes",
            json.dumps(boxes),
            "--display-mode",
            display_mode,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def pdf_text(path: Path) -> str:
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def sampled_rgb(pdf_path: Path, page_index: int = 0, x: int = 170, y: int = 50) -> tuple[int, int, int]:
    doc = fitz.open(pdf_path)
    try:
        pix = doc[page_index].get_pixmap(alpha=False)
        offset = (y * pix.width + x) * pix.n
        return tuple(pix.samples[offset:offset + 3])
    finally:
        doc.close()


class ManualBoxesTests(unittest.TestCase):
    def test_restore_pipeline_removes_temporary_directory_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            output = root / "output.pdf"
            source.write_bytes(b"source")

            def write_auto(_source: str, temporary_output: str, *_args, **_kwargs):
                Path(temporary_output).write_bytes(b"redacted")
                return {"status": "applied", "output_file": temporary_output}

            with mock.patch.object(tempfile, "tempdir", str(root)), mock.patch.object(
                masking_redaction,
                "redact_pdf_native",
                side_effect=write_auto,
            ):
                result = masking_redaction.apply_manual_edits_with_restore(
                    str(source),
                    str(output),
                    [masker.RedactionMatch("NAME", "Alice Example")],
                    [],
                )

            self.assertEqual(b"redacted", output.read_bytes())
            self.assertEqual(str(output), result["output_file"])
            self.assertIsNone(result["auto_redaction"]["output_file"])
            self.assertEqual([], list(root.glob("masker_manual_restore_*")))

    def test_restore_pipeline_removes_temporary_directory_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.pdf"
            output = root / "output.pdf"
            source.write_bytes(b"source")

            with mock.patch.object(tempfile, "tempdir", str(root)), mock.patch.object(
                masking_redaction,
                "redact_pdf_native",
                side_effect=RuntimeError("synthetic failure"),
            ):
                with self.assertRaises(RuntimeError):
                    masking_redaction.apply_manual_edits_with_restore(
                        str(source),
                        str(output),
                        [masker.RedactionMatch("NAME", "Alice Example")],
                        [],
                    )

            self.assertEqual([], list(root.glob("masker_manual_restore_*")))

    def test_packaged_engine_entry_can_apply_manual_boxes_without_helper_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)

            result = run_engine_manual_boxes(
                original_pdf,
                original_pdf,
                work,
                [{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}],
            )

            output_pdf = Path(str(result["output_file"]))
            self.assertTrue(output_pdf.exists())
            self.assertEqual("applied", result["status"])
            self.assertEqual(1, result["mask_boxes_applied"])
            self.assertEqual(0, result["unmask_boxes_applied"])

    def test_engine_entry_requires_revalidation_only_when_restore_applied(self) -> None:
        # 저장 게이트 회귀(v4.0.0): 마스킹만 추가한 경우에도 requires_revalidation이
        # true로 오면 프론트가 안전 리포트를 무효화해 저장이 영구 차단됐다. 마스킹은
        # 노출을 줄이기만 하므로 재검증 불요, 복원(unmask)만 재검증 대상이어야 한다.
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)
            box = {"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62}

            mask_only = run_engine_manual_boxes(
                original_pdf, original_pdf, work, [{**box, "mode": "mask"}]
            )
            self.assertEqual(1, mask_only["mask_boxes_applied"])
            self.assertEqual(0, mask_only["unmask_boxes_applied"])
            self.assertFalse(mask_only["requires_revalidation"])

            with_restore = run_engine_manual_boxes(
                original_pdf,
                original_pdf,
                work,
                [{**box, "mode": "mask"}, {"page": 0, "x0": 28, "y0": 78, "x1": 190, "y1": 104, "mode": "restore"}],
            )
            self.assertEqual(1, with_restore["unmask_boxes_applied"])
            self.assertTrue(with_restore["requires_revalidation"])

    def test_manual_label_ko_uses_korean_mask_label(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)

            result = run_manual_boxes(
                original_pdf,
                original_pdf,
                work,
                [{"page": 0, "x0": 28, "y0": 78, "x1": 190, "y1": 102, "mode": "mask", "tag": "PHONE"}],
                display_mode="label_ko",
            )

            output_pdf = Path(str(result["output_file"]))
            rendered_text = pdf_text(output_pdf)
            self.assertEqual("applied", result["status"])
            self.assertNotIn("010-0000-0000", rendered_text)
            self.assertIn("[전화번호]", rendered_text)

    def test_packaged_engine_entry_manual_boxes_support_display_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)

            expected = {
                "black": "",
                "label_en": "[MASK]",
                "label_ko": "[마스킹]",
                "pseudonym": "",
            }
            for mode, label in expected.items():
                outdir = work / mode
                outdir.mkdir()
                result = run_engine_manual_boxes(
                    original_pdf,
                    original_pdf,
                    outdir,
                    [{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}],
                    display_mode=mode,
                )

                output_pdf = Path(str(result["output_file"]))
                self.assertEqual("applied", result["status"], mode)
                rendered_text = pdf_text(output_pdf)
                self.assertNotIn("Hong Gil Dong", rendered_text)
                if label:
                    self.assertIn(label, rendered_text)
                else:
                    self.assertNotIn("[MASK]", rendered_text)

    def test_packaged_engine_entry_manual_boxes_use_box_tag_for_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)

            result = run_engine_manual_boxes(
                original_pdf,
                original_pdf,
                work,
                [{"page": 0, "x0": 28, "y0": 78, "x1": 190, "y1": 102, "mode": "mask", "tag": "PHONE"}],
                display_mode="label_ko",
            )

            output_pdf = Path(str(result["output_file"]))
            rendered_text = pdf_text(output_pdf)
            self.assertEqual("applied", result["status"])
            self.assertNotIn("010-0000-0000", rendered_text)
            self.assertIn("[전화번호]", rendered_text)

    def test_repeated_manual_edits_create_fresh_preview_files_when_restoring_then_masking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)

            first = run_manual_boxes(
                original_pdf,
                original_pdf,
                work,
                [{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}],
            )
            first_pdf = Path(str(first["output_file"]))

            second = run_manual_boxes(
                first_pdf,
                original_pdf,
                work,
                [{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "restore"}],
            )
            second_pdf = Path(str(second["output_file"]))

            third = run_manual_boxes(
                second_pdf,
                original_pdf,
                work,
                [{"page": 0, "x0": 28, "y0": 78, "x1": 190, "y1": 104, "mode": "mask"}],
            )
            third_pdf = Path(str(third["output_file"]))

            self.assertTrue(first_pdf.exists())
            self.assertTrue(second_pdf.exists())
            self.assertTrue(third_pdf.exists())
            self.assertNotEqual(first_pdf, second_pdf)
            self.assertNotEqual(second_pdf, third_pdf)
            self.assertEqual(1, second["unmask_boxes_applied"])
            self.assertEqual(1, third["mask_boxes_applied"])

    def test_same_batch_manual_edits_follow_user_order_in_tauri_helper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)
            box = {"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62}

            restored = run_manual_boxes(
                original_pdf,
                original_pdf,
                work,
                [{**box, "mode": "mask"}, {**box, "mode": "restore"}],
            )
            restored_rgb = sampled_rgb(Path(str(restored["output_file"])))

            masked = run_manual_boxes(
                original_pdf,
                original_pdf,
                work,
                [{**box, "mode": "restore"}, {**box, "mode": "mask"}],
            )
            masked_rgb = sampled_rgb(Path(str(masked["output_file"])))

            self.assertGreater(sum(restored_rgb), 650)
            self.assertLess(sum(masked_rgb), 30)

    def test_mixed_valid_and_invalid_manual_boxes_apply_valid_boxes_with_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)

            result = run_manual_boxes(
                original_pdf,
                original_pdf,
                work,
                [
                    {"page": 99, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"},
                    {"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"},
                    {"page": 0, "x0": 28, "y0": 78, "x1": 190, "y1": 104, "mode": "restore"},
                ],
            )

            self.assertTrue(Path(str(result["output_file"])).exists())
            self.assertEqual(2, result["applied_count"])
            self.assertEqual(1, result["mask_boxes_applied"])
            self.assertEqual(1, result["unmask_boxes_applied"])
            self.assertEqual(1, result["skipped_boxes"])
            self.assertIn("page out of range", " ".join(result["warnings"]))

    def test_all_invalid_manual_boxes_return_warning_preview_without_script_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)

            result = run_manual_boxes(
                original_pdf,
                original_pdf,
                work,
                [
                    {"page": 99, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"},
                    {"page": 0, "x0": 30, "y0": 40, "x1": 30, "y1": 80, "mode": "restore"},
                ],
            )
            output_pdf = Path(str(result["output_file"]))

            self.assertTrue(output_pdf.exists())
            self.assertNotEqual(original_pdf, output_pdf)
            self.assertEqual("no_effect", result["status"])
            self.assertEqual(0, result["applied_count"])
            self.assertEqual(0, result["mask_boxes_applied"])
            self.assertEqual(0, result["unmask_boxes_applied"])
            self.assertEqual(2, result["skipped_boxes"])
            self.assertIn("no valid manual boxes", " ".join(result["warnings"]))
            self.assertGreater(sum(sampled_rgb(output_pdf, x=170, y=50)), 650)

    def test_duplicate_manual_masks_are_deduplicated_into_one_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)
            box = {"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}

            result = run_manual_boxes(original_pdf, original_pdf, work, [box, box])
            output_pdf = Path(str(result["output_file"]))

            self.assertTrue(output_pdf.exists())
            self.assertEqual(1, result["mask_boxes_applied"])
            self.assertEqual(1, result["skipped_boxes"])
            self.assertIn("duplicate manual box", " ".join(result["warnings"]))
            self.assertLess(sum(sampled_rgb(output_pdf)), 30)

    def test_gui_manual_corrections_return_no_effect_preview_for_all_invalid_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)
            target_pdf = work / "sample_manual_redacted.pdf"

            result = masker.apply_manual_pdf_corrections(
                str(original_pdf),
                str(original_pdf),
                str(target_pdf),
                [
                    masker.ManualCorrectionBox(page_index=99, rect=(28, 38, 190, 62), action="mask"),
                    masker.ManualCorrectionBox(page_index=0, rect=(30, 40, 30, 80), action="unmask"),
                ],
            )
            output_pdf = Path(result["output_file"])

            self.assertTrue(output_pdf.exists())
            self.assertEqual("no_effect", result["status"])
            self.assertEqual(0, result["mask_boxes_applied"])
            self.assertEqual(0, result["unmask_boxes_applied"])
            self.assertEqual(2, result["skipped_boxes"])
            self.assertIn("no valid manual boxes", " ".join(result["warnings"]))

    def test_gui_duplicate_manual_masks_are_deduplicated_into_one_operation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)
            target_pdf = work / "sample_manual_redacted.pdf"
            rect = (28, 38, 190, 62)

            result = masker.apply_manual_pdf_corrections(
                str(original_pdf),
                str(original_pdf),
                str(target_pdf),
                [
                    masker.ManualCorrectionBox(page_index=0, rect=rect, action="mask"),
                    masker.ManualCorrectionBox(page_index=0, rect=rect, action="mask"),
                ],
            )

            self.assertEqual(1, result["mask_boxes_applied"])
            self.assertEqual(1, result["skipped_boxes"])
            self.assertIn("duplicate manual box", " ".join(result["warnings"]))

    def test_gui_manual_corrections_support_display_modes_without_raw_value_leakage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)
            expected = {
                "black": "",
                "label_en": "[MASK]",
                "label_ko": "[마스킹]",
                "pseudonym": "",
            }

            for mode, label in expected.items():
                target_pdf = work / f"sample_{mode}.pdf"
                result = masker.apply_manual_pdf_corrections(
                    str(original_pdf),
                    str(original_pdf),
                    str(target_pdf),
                    [masker.ManualCorrectionBox(page_index=0, rect=(28, 38, 190, 62), action="mask")],
                    display_mode=mode,
                )

                output_pdf = Path(result["output_file"])
                rendered_text = pdf_text(output_pdf)
                self.assertEqual("applied", result["status"], mode)
                # 마스킹 박스만 추가한 경우는 노출을 줄이기만 하므로 재검증이
                # 필요치 않다(복원만 위험을 늘려 재검증 대상).
                self.assertFalse(result["requires_revalidation"], mode)
                self.assertFalse(result["raw_value_saved"], mode)
                self.assertNotIn("Hong Gil Dong", rendered_text)
                if label:
                    self.assertIn(label, rendered_text)
                else:
                    self.assertNotIn("[MASK]", rendered_text)

    def test_gui_manual_corrections_use_box_tag_for_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)

            result = masker.apply_manual_pdf_corrections(
                str(original_pdf),
                str(original_pdf),
                str(work / "sample_phone.pdf"),
                [masker.ManualCorrectionBox(page_index=0, rect=(28, 78, 190, 102), action="mask", tag="PHONE")],
                display_mode="label_ko",
            )

            output_pdf = Path(result["output_file"])
            rendered_text = pdf_text(output_pdf)
            self.assertEqual("applied", result["status"])
            self.assertNotIn("010-0000-0000", rendered_text)
            self.assertIn("[전화번호]", rendered_text)

    def test_gui_manual_corrections_create_fresh_files_and_report_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)
            target_pdf = work / "sample_manual_redacted.pdf"

            first = masker.apply_manual_pdf_corrections(
                str(original_pdf),
                str(original_pdf),
                str(target_pdf),
                [masker.ManualCorrectionBox(page_index=0, rect=(28, 38, 190, 62), action="mask")],
            )
            first_pdf = Path(first["output_file"])

            second = masker.apply_manual_pdf_corrections(
                str(first_pdf),
                str(original_pdf),
                str(target_pdf),
                [
                    masker.ManualCorrectionBox(page_index=99, rect=(28, 38, 190, 62), action="mask"),
                    masker.ManualCorrectionBox(page_index=0, rect=(28, 38, 190, 62), action="unmask"),
                ],
            )
            second_pdf = Path(second["output_file"])

            third = masker.apply_manual_pdf_corrections(
                str(second_pdf),
                str(original_pdf),
                str(target_pdf),
                [masker.ManualCorrectionBox(page_index=0, rect=(28, 78, 190, 104), action="mask")],
            )
            third_pdf = Path(third["output_file"])

            self.assertTrue(first_pdf.exists())
            self.assertTrue(second_pdf.exists())
            self.assertTrue(third_pdf.exists())
            self.assertNotEqual(first_pdf, second_pdf)
            self.assertNotEqual(second_pdf, third_pdf)
            self.assertEqual(1, second["unmask_boxes_applied"])
            self.assertEqual(1, second["skipped_boxes"])
            self.assertIn("page out of range", " ".join(second["warnings"]))
            self.assertEqual(1, third["mask_boxes_applied"])

    def test_same_batch_gui_manual_corrections_follow_user_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)
            target_pdf = work / "sample_manual_redacted.pdf"
            rect = (28, 38, 190, 62)

            restored = masker.apply_manual_pdf_corrections(
                str(original_pdf),
                str(original_pdf),
                str(target_pdf),
                [
                    masker.ManualCorrectionBox(page_index=0, rect=rect, action="mask"),
                    masker.ManualCorrectionBox(page_index=0, rect=rect, action="unmask"),
                ],
            )
            restored_rgb = sampled_rgb(Path(restored["output_file"]))

            masked = masker.apply_manual_pdf_corrections(
                str(original_pdf),
                str(original_pdf),
                str(target_pdf),
                [
                    masker.ManualCorrectionBox(page_index=0, rect=rect, action="unmask"),
                    masker.ManualCorrectionBox(page_index=0, rect=rect, action="mask"),
                ],
            )
            masked_rgb = sampled_rgb(Path(masked["output_file"]))

            self.assertGreater(sum(restored_rgb), 650)
            self.assertLess(sum(masked_rgb), 30)

    def test_pipeline_pdf_output_accepts_restore_then_additional_manual_masking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)

            extracted_path, masked_path, report_path, report = masker.process_file(
                str(original_pdf),
                outdir=str(work),
                opts={
                    "output_artifacts": "pdf_safe_report",
                    "pdf_redaction": True,
                    "extract_engine": "pypdf",
                    "profile": "official",
                    "region_scope": "national",
                    "display_mode": "black",
                },
            )
            masked_pdf = Path(masker.runtime_manifest_for_report(report)["outputs"]["masked_pdf_file"])

            self.assertIsNone(extracted_path)
            self.assertIsNone(masked_path)
            self.assertTrue(Path(str(report_path)).exists())
            self.assertTrue(masked_pdf.exists())
            self.assertFalse(list(work.glob("*.extracted.*.txt")))

            restored = run_manual_boxes(
                masked_pdf,
                original_pdf,
                work,
                [{"page": 0, "x0": 28, "y0": 78, "x1": 190, "y1": 104, "mode": "restore"}],
            )
            restored_pdf = Path(str(restored["output_file"]))
            self.assertTrue(restored_pdf.exists())
            self.assertEqual(1, restored["unmask_boxes_applied"])
            self.assertGreater(sum(sampled_rgb(restored_pdf, x=170, y=92)), 650)

            remasked = run_manual_boxes(
                restored_pdf,
                original_pdf,
                work,
                [{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}],
            )
            remasked_pdf = Path(str(remasked["output_file"]))
            self.assertTrue(remasked_pdf.exists())
            self.assertNotEqual(restored_pdf, remasked_pdf)
            self.assertEqual(1, remasked["mask_boxes_applied"])
            self.assertLess(sum(sampled_rgb(remasked_pdf)), 30)

    def test_multi_page_manual_edits_apply_to_their_own_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "multi.pdf"
            write_two_page_pdf(original_pdf)

            result = run_manual_boxes(
                original_pdf,
                original_pdf,
                work,
                [
                    {"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"},
                    {"page": 1, "x0": 28, "y0": 78, "x1": 190, "y1": 104, "mode": "mask"},
                ],
            )
            output_pdf = Path(str(result["output_file"]))

            self.assertTrue(output_pdf.exists())
            self.assertEqual(2, result["mask_boxes_applied"])
            self.assertLess(sum(sampled_rgb(output_pdf, page_index=0, x=100, y=50)), 30)
            self.assertGreater(sum(sampled_rgb(output_pdf, page_index=0, x=100, y=100)), 650)
            self.assertGreater(sum(sampled_rgb(output_pdf, page_index=1, x=180, y=60)), 650)
            self.assertLess(sum(sampled_rgb(output_pdf, page_index=1, x=100, y=90)), 30)


if __name__ == "__main__":
    unittest.main()
