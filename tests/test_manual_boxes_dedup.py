"""Equivalence tests for the unified manual-box core (G-3).

scripts/apply_manual_boxes.py owns the single manual-box implementation and
scripts/masking_engine_entry.run_manual_boxes delegates to it. Both CLI contracts
must stay byte-compatible with their historical shapes.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

import fitz

REPO_ROOT = Path(__file__).resolve().parents[1]
MANUAL_SCRIPT = REPO_ROOT / "scripts" / "apply_manual_boxes.py"
ENGINE_ENTRY = REPO_ROOT / "scripts" / "masking_engine_entry.py"

SHARED_KEYS = {
    "status", "output_file", "mask_count", "restore_count", "applied_count",
    "excluded_count", "mask_boxes_applied", "unmask_boxes_applied",
    "requires_revalidation", "skipped_boxes", "warnings", "display_mode",
}
ENGINE_EXTRA_KEYS = {"raw_value_saved", "engine_packaged"}


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
    paths = [Path(value) for index, value in enumerate(script_args) if index and script_args[index - 1] in {"--input", "--original", "--outdir"}]
    roots = [path if path.is_dir() else path.parent for path in paths]
    env["MASK_TOOL_ALLOWED_DIRS"] = str(Path(os.path.commonpath([str(root) for root in roots])))
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

    def test_engine_entry_delegates_to_shared_core_with_exact_arguments(self) -> None:
        spec = importlib.util.spec_from_file_location("masking_engine_entry_under_test", ENGINE_ENTRY)
        masking_engine_entry = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = masking_engine_entry
        spec.loader.exec_module(masking_engine_entry)

        core_result = {
            "status": "applied", "output_file": "/safe/output.pdf", "mask_count": 1,
            "restore_count": 1, "applied_count": 2, "excluded_count": 0,
            "mask_boxes_applied": 1, "unmask_boxes_applied": 1,
            "requires_revalidation": True, "skipped_boxes": 0, "warnings": [],
            "display_mode": "black",
        }
        shared_core = Mock(return_value=core_result.copy())
        args = argparse.Namespace(repo_root=str(REPO_ROOT), input="input.pdf", original="original.pdf", outdir="out", boxes='[{"page":0}]', display_mode="black")
        stream = io.StringIO()
        with patch.dict(sys.modules, {"apply_manual_boxes": SimpleNamespace(apply_manual_boxes=shared_core)}), contextlib.redirect_stdout(stream):
            self.assertEqual(0, masking_engine_entry.run_manual_boxes(args))

        shared_core.assert_called_once_with("input.pdf", "original.pdf", "out", [{"page": 0}], "black")
        engine_result = json.loads(stream.getvalue())
        self.assertEqual(SHARED_KEYS | ENGINE_EXTRA_KEYS, set(engine_result))
        self.assertTrue(engine_result["requires_revalidation"])
        self.assertFalse(engine_result["raw_value_saved"])
        self.assertFalse(engine_result["engine_packaged"])

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

            self.assertEqual(SHARED_KEYS, set(helper))
            self.assertEqual(SHARED_KEYS | ENGINE_EXTRA_KEYS, set(engine))
            for key in SHARED_KEYS - {"output_file"}:
                self.assertEqual(helper[key], engine[key], f"mismatch on {key}")
            self.assertFalse(ENGINE_EXTRA_KEYS & helper.keys())
            self.assertFalse(engine["requires_revalidation"])
            self.assertFalse(engine["raw_value_saved"])

    def test_restore_outcomes_have_parity_across_both_clis(self) -> None:
        boxes = [
            {"page": 0, "x0": 28, "y0": 38, "x1": 190, "y1": 62, "mode": "mask"},
            {"page": 0, "x0": 28, "y0": 78, "x1": 190, "y1": 104, "mode": "restore"},
        ]
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            pdf_a, pdf_b = Path(tmp_a) / "sample.pdf", Path(tmp_b) / "sample.pdf"
            write_pdf(pdf_a)
            write_pdf(pdf_b)
            helper = run_script([str(MANUAL_SCRIPT), "--input", str(pdf_a), "--original", str(pdf_a), "--outdir", tmp_a, "--boxes", json.dumps(boxes)])
            engine = run_script([str(ENGINE_ENTRY), "--manual-boxes", "--input", str(pdf_b), "--original", str(pdf_b), "--outdir", tmp_b, "--boxes", json.dumps(boxes)])

            self.assertEqual(SHARED_KEYS, set(helper))
            self.assertEqual(SHARED_KEYS | ENGINE_EXTRA_KEYS, set(engine))
            for key in SHARED_KEYS - {"output_file"}:
                self.assertEqual(helper[key], engine[key], f"restore mismatch on {key}")
            self.assertTrue(helper["requires_revalidation"])
            self.assertTrue(engine["requires_revalidation"])
            for result in (helper, engine):
                document = fitz.open(str(result["output_file"]))
                try:
                    self.assertNotIn("Hong Gil Dong", document[0].get_text())
                    self.assertIn("010-0000-0000", document[0].get_text())
                finally:
                    document.close()


if __name__ == "__main__":
    unittest.main()
