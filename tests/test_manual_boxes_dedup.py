"""Equivalence tests for the unified manual-box core (G-3).

scripts/apply_manual_boxes.py owns the single manual-box implementation and
scripts/masking_engine_entry.run_manual_boxes delegates to it. Both CLI contracts
must stay byte-compatible with their historical shapes.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import fitz

REPO_ROOT = Path(__file__).resolve().parents[1]
MANUAL_SCRIPT = REPO_ROOT / "scripts" / "apply_manual_boxes.py"
ENGINE_ENTRY = REPO_ROOT / "scripts" / "masking_engine_entry.py"

SHARED_KEYS = {
    "status", "mask_count", "restore_count", "applied_count", "excluded_count",
    "mask_boxes_applied", "unmask_boxes_applied", "skipped_boxes", "warnings",
    "display_mode",
}
ENGINE_EXTRA_KEYS = {"requires_revalidation", "raw_value_saved", "engine_packaged"}


def load_apply_module():
    spec = importlib.util.spec_from_file_location("apply_manual_boxes_under_test", MANUAL_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=240, height=180)
    page.insert_text((32, 52), "name Hong Gil Dong")
    page.insert_text((32, 92), "phone 010-0000-0000")
    doc.save(path)
    doc.close()


def run_script(script_args: list[str]) -> dict:
    env = os.environ.copy()
    env.pop("MASK_TOOL_ALLOWED_DIRS", None)
    proc = subprocess.run(
        [sys.executable, *script_args],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return json.loads(proc.stdout)


class ManualBoxesDedupTests(unittest.TestCase):
    def test_apply_module_exposes_core_callable(self) -> None:
        module = load_apply_module()
        self.assertTrue(callable(getattr(module, "apply_manual_boxes", None)))

    def test_engine_entry_imports_shared_core_not_duplicated_logic(self) -> None:
        engine_src = ENGINE_ENTRY.read_text(encoding="utf-8")
        # The delegator must call into the single source and keep its function name.
        self.assertIn("def run_manual_boxes", engine_src)
        self.assertIn("import apply_manual_boxes", engine_src)
        self.assertIn(".apply_manual_boxes(", engine_src)
        # The old duplicated helpers should no longer live in the entry point.
        self.assertNotIn("def safe_manual_output_path", engine_src)
        self.assertNotIn("def normalized_manual_rect", engine_src)

    def test_both_entry_points_agree_on_shared_result_fields(self) -> None:
        boxes = [{"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"}]
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            pdf_a = Path(tmp_a) / "sample.pdf"
            pdf_b = Path(tmp_b) / "sample.pdf"
            write_pdf(pdf_a)
            write_pdf(pdf_b)

            helper = run_script([
                str(MANUAL_SCRIPT),
                "--input", str(pdf_a), "--original", str(pdf_a),
                "--outdir", tmp_a, "--boxes", json.dumps(boxes),
            ])
            engine = run_script([
                str(ENGINE_ENTRY), "--manual-boxes",
                "--input", str(pdf_b), "--original", str(pdf_b),
                "--outdir", tmp_b, "--boxes", json.dumps(boxes),
            ])

            for key in SHARED_KEYS:
                self.assertEqual(helper[key], engine[key], f"mismatch on {key}")

            # Contract split: engine entry adds packaged fields; helper does not.
            self.assertTrue(ENGINE_EXTRA_KEYS.issubset(engine.keys()))
            self.assertFalse(ENGINE_EXTRA_KEYS & helper.keys())
            # Mask-only 보정은 노출을 줄이기만 하므로 재검증 불요(복원만 재검증 대상).
            self.assertFalse(engine["requires_revalidation"])
            self.assertFalse(engine["raw_value_saved"])

    def test_engine_entry_flags_revalidation_when_restore_applied(self) -> None:
        boxes = [
            {"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"},
            {"page": 0, "x0": 28, "y0": 78, "x1": 190, "y1": 104, "mode": "restore"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "sample.pdf"
            write_pdf(pdf)
            engine = run_script([
                str(ENGINE_ENTRY), "--manual-boxes",
                "--input", str(pdf), "--original", str(pdf),
                "--outdir", tmp, "--boxes", json.dumps(boxes),
            ])
            self.assertEqual(1, engine["unmask_boxes_applied"])
            self.assertTrue(engine["requires_revalidation"])


if __name__ == "__main__":
    unittest.main()
