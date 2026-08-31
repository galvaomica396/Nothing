from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import NotRequired, TypedDict

import fitz

import document_masker_ocr_gui as masker


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
    env = __import__("os").environ.copy()
    env["MASK_TOOL_ALLOWED_DIRS"] = str(input_pdf.parent)
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
        env=env,
    )
    return json.loads(completed.stdout)


def run_engine_manual_boxes(
    input_pdf: Path,
    original_pdf: Path,
    output_dir: Path,
    boxes: list[BoxPayload],
    display_mode: str = "black",
) -> dict[str, object]:
    env = __import__("os").environ.copy()
    env["MASK_TOOL_ALLOWED_DIRS"] = str(input_pdf.parent)
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
        env=env,
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
def assert_manual_output(path: Path, removed: str | None = None, preserved: str = "phone") -> str:
    doc = fitz.open(path)
    try:
        if len(doc) < 1:
            raise AssertionError("manual output has no pages")
    finally:
        doc.close()
    rendered = pdf_text(path)
    if removed is not None and removed in rendered:
        raise AssertionError(f"manual target remains visible: {removed}")
    normalized_for_control = rendered.replace("\xa0", " ")
    if preserved not in normalized_for_control:
        raise AssertionError(f"manual control text missing: {preserved}")
    return normalized_for_control




class ManualBoxesTests(unittest.TestCase):

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
            rendered = assert_manual_output(output_pdf, "Hong Gil Dong")
            self.assertIn("phone 010-0000-0000", rendered)

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
            self.assertNotIn("Hong Gil Dong", pdf_text(Path(str(mask_only["output_file"]))))
            self.assertIn("phone 010-0000-0000", pdf_text(Path(str(mask_only["output_file"]))))
            self.assertNotIn("Hong Gil Dong", pdf_text(Path(str(with_restore["output_file"]))))
            self.assertIn("phone 010-0000-0000", pdf_text(Path(str(with_restore["output_file"]))))

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
            rendered_text = assert_manual_output(output_pdf, "010-0000-0000", preserved="name Hong Gil Dong")
            self.assertEqual("applied", result["status"])
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
                rendered_text = assert_manual_output(output_pdf, "Hong Gil Dong", preserved="phone 010-0000-0000")
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
            rendered_text = assert_manual_output(output_pdf, "010-0000-0000", preserved="name Hong Gil Dong")
            self.assertEqual("applied", result["status"])
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
            self.assertNotIn("Hong Gil Dong", pdf_text(first_pdf))
            self.assertIn("Hong Gil Dong", pdf_text(second_pdf))
            third_text = assert_manual_output(third_pdf, "010-0000-0000", preserved="name Hong Gil Dong")
            self.assertIn("name Hong Gil Dong", third_text)

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
            restored_text = assert_manual_output(Path(str(restored["output_file"])), preserved="phone 010-0000-0000")
            self.assertIn("name Hong Gil Dong", restored_text)
            assert_manual_output(Path(str(masked["output_file"])), "Hong Gil Dong", preserved="phone 010-0000-0000")
            remasked = run_manual_boxes(
                original_pdf,
                original_pdf,
                work,
                [{**box, "mode": "mask"}, {**box, "mode": "restore"}, {**box, "mode": "mask"}],
            )
            self.assertEqual(2, remasked["mask_boxes_applied"])
            self.assertEqual(1, remasked["unmask_boxes_applied"])
            self.assertTrue(remasked["requires_revalidation"])
            assert_manual_output(
                Path(str(remasked["output_file"])),
                "Hong Gil Dong",
                preserved="phone 010-0000-0000",
            )

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
            rendered = assert_manual_output(Path(str(result["output_file"])), "Hong Gil Dong")
            self.assertIn("phone 010-0000-0000", rendered)

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
            unchanged_text = assert_manual_output(output_pdf, preserved="phone 010-0000-0000")
            self.assertIn("name Hong Gil Dong", unchanged_text)

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
            assert_manual_output(output_pdf, "Hong Gil Dong", preserved="phone 010-0000-0000")

    def test_gui_manual_corrections_skip_invalid_and_duplicate_boxes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)
            rect = (28, 38, 190, 62)
            result = masker.apply_manual_pdf_corrections(
                str(original_pdf),
                str(original_pdf),
                str(work / "manual_redacted.pdf"),
                [
                    masker.ManualCorrectionBox(page_index=99, rect=rect, action="mask"),
                    masker.ManualCorrectionBox(page_index=0, rect=(30, 40, 30, 80), action="unmask"),
                    masker.ManualCorrectionBox(page_index=0, rect=rect, action="mask"),
                    masker.ManualCorrectionBox(page_index=0, rect=rect, action="mask"),
                ],
            )
            self.assertEqual("applied", result["status"])
            self.assertEqual(1, result["mask_boxes_applied"])
            self.assertEqual(3, result["skipped_boxes"])
            self.assertIn("page out of range", " ".join(result["warnings"]))
            self.assertIn("invalid rectangle", " ".join(result["warnings"]))
            self.assertIn("duplicate manual box", " ".join(result["warnings"]))
            assert_manual_output(Path(result["output_file"]), "Hong Gil Dong")

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
                rendered_text = assert_manual_output(output_pdf, "Hong Gil Dong", preserved="phone 010-0000-0000")
                self.assertEqual("applied", result["status"], mode)
                # 마스킹 박스만 추가한 경우는 노출을 줄이기만 하므로 재검증이
                # 필요치 않다(복원만 위험을 늘려 재검증 대상).
                self.assertFalse(result["requires_revalidation"], mode)
                self.assertFalse(result["raw_value_saved"], mode)
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
            rendered_text = assert_manual_output(output_pdf, "010-0000-0000", preserved="name Hong Gil Dong")
            self.assertEqual("applied", result["status"])
            self.assertIn("[전화번호]", rendered_text)

    def test_gui_manual_corrections_create_fresh_files_after_invalid_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)
            target_pdf = work / "sample_manual_redacted.pdf"
            first = masker.apply_manual_pdf_corrections(
                str(original_pdf), str(original_pdf), str(target_pdf),
                [masker.ManualCorrectionBox(page_index=0, rect=(28, 38, 190, 62), action="mask")],
            )
            second = masker.apply_manual_pdf_corrections(
                str(first["output_file"]), str(original_pdf), str(target_pdf),
                [
                    masker.ManualCorrectionBox(page_index=99, rect=(28, 38, 190, 62), action="mask"),
                    masker.ManualCorrectionBox(page_index=0, rect=(28, 38, 190, 62), action="unmask"),
                ],
            )
            self.assertNotEqual(first["output_file"], second["output_file"])
            self.assertEqual(1, second["unmask_boxes_applied"])
            self.assertEqual(1, second["skipped_boxes"])
            self.assertIn("Hong Gil Dong", pdf_text(Path(second["output_file"])))
    def test_same_batch_gui_manual_corrections_preserve_action_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            original_pdf = work / "sample.pdf"
            write_pdf(original_pdf)
            rect = (28, 38, 190, 62)
            for actions, expected_masked in ((("mask", "unmask"), False), (("unmask", "mask"), True)):
                with self.subTest(actions=actions):
                    result = masker.apply_manual_pdf_corrections(
                        str(original_pdf), str(original_pdf), str(work / "manual_redacted.pdf"),
                        [
                            masker.ManualCorrectionBox(page_index=0, rect=rect, action=actions[0]),
                            masker.ManualCorrectionBox(page_index=0, rect=rect, action=actions[1]),
                        ],
                    )
                    self.assertEqual(expected_masked, "Hong Gil Dong" not in pdf_text(Path(result["output_file"])))
                    self.assertEqual(1, result["mask_boxes_applied"])
                    self.assertEqual(1, result["unmask_boxes_applied"])

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
                    "profile": "legal",
                    "region_scope": "national",
                    "display_mode": "black",
                    "name": True,
                    "custom_keywords": "Hong Gil Dong",
                },
            )
            runtime_manifest = masker.runtime_manifest_for_report(report)
            self.assertTrue(runtime_manifest["outputs"]["masked_pdf_file"])
            masked_pdf, = work.glob("sample.final_masked_black.*.pdf")

            self.assertIsNone(extracted_path)
            self.assertIsNone(masked_path)
            self.assertTrue(Path(str(report_path)).exists())
            self.assertTrue(masked_pdf.exists())
            self.assertFalse(list(work.glob("*.extracted.*.txt")))
            self.assertNotIn("Hong Gil Dong", pdf_text(masked_pdf))
            self.assertNotIn("010-0000-0000", pdf_text(masked_pdf))
            self.assertIn("name", pdf_text(masked_pdf))

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
            restored_text = pdf_text(restored_pdf)
            self.assertIn("010-0000-0000", restored_text)
            self.assertIn("name", restored_text)

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
            remasked_text = pdf_text(remasked_pdf)
            self.assertNotIn("Hong Gil Dong", remasked_text)
            self.assertIn("010-0000-0000", remasked_text)

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
            rendered = pdf_text(output_pdf)
            self.assertNotIn("page 1 name Hong Gil Dong", rendered)
            self.assertNotIn("page 2 phone 010-0000-0001", rendered)
            self.assertIn("page 1 phone 010-0000-0000", rendered)
            self.assertIn("page 2 name Hong Gil Dong", rendered)


if __name__ == "__main__":
    unittest.main()
